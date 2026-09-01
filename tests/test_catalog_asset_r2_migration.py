import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import migrate_catalog_assets_to_r2 as migration


PNG = b"\x89PNG\r\n\x1a\nfixture"
JPEG = b"\xff\xd8\xff\xe0fixture\xff\xd9"
WEBP = b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 "
CACHE_CONTROL = "public, max-age=31536000, immutable"


def _entry(body, extension, mime_type):
    digest = hashlib.sha256(body).hexdigest()
    return {
        "object_name": f"{digest}.{extension}",
        "sha256": digest,
        "byte_size": len(body),
        "mime_type": mime_type,
    }


def _manifest(entries, *, overrides=None):
    ordered = sorted(entries, key=lambda item: item["object_name"])
    keyset = hashlib.sha256(
        "\n".join(item["object_name"] for item in ordered).encode()
    ).hexdigest()
    rows = "\n".join(
        f'{item["object_name"]}|{item["sha256"]}|{item["byte_size"]}|{item["mime_type"]}'
        for item in ordered
    )
    mime_counts = {"image/png": 0, "image/webp": 0, "image/jpeg": 0}
    for item in ordered:
        mime_counts[item["mime_type"]] += 1
    value = {
        "schema_version": 1,
        "logical_bucket": "catalog-assets",
        "entry_count": len(ordered),
        "total_bytes": sum(item["byte_size"] for item in ordered),
        "mime_counts": mime_counts,
        "keyset_digest": keyset,
        "manifest_digest": hashlib.sha256(rows.encode()).hexdigest(),
        "entries": ordered,
    }
    value.update(overrides or {})
    return value


def _contract(entries):
    counts = {"image/png": 0, "image/webp": 0, "image/jpeg": 0}
    for item in entries:
        counts[item["mime_type"]] += 1
    return migration.ManifestContract(
        entry_count=len(entries),
        total_bytes=sum(item["byte_size"] for item in entries),
        mime_counts=counts,
    )


def _write_fixture(tmp_path, entries_and_bodies, *, extras=None, manifest_overrides=None):
    source = tmp_path / "repo" / ".mobiliti_dev_store" / "catalog-assets"
    source.mkdir(parents=True)
    entries = []
    for entry, body in entries_and_bodies:
        (source / entry["object_name"]).write_bytes(body)
        entries.append(entry)
    for name, body in extras or []:
        (source / name).write_bytes(body)
    manifest_path = tmp_path / "manifest.json"
    manifest_bytes = json.dumps(
        _manifest(entries, overrides=manifest_overrides),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_path.write_bytes(manifest_bytes)
    return source, manifest_path, hashlib.sha256(manifest_bytes).hexdigest(), entries


class R2Error(Exception):
    def __init__(self, status, code=""):
        super().__init__("sensitive endpoint and credentials must not escape")
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code or str(status)},
        }


def _head(entry):
    return {
        "ContentLength": entry["byte_size"],
        "ContentType": entry["mime_type"],
        "Metadata": {"sha256": entry["sha256"]},
        "CacheControl": CACHE_CONTROL,
    }


class StreamingBody:
    def __init__(self, body, *, fail=False):
        self._body = body
        self._offset = 0
        self.closed = False
        self.fail = fail

    def read(self, amount=-1):
        if self.fail:
            raise OSError("secret raw stream failure")
        if self._offset >= len(self._body):
            return b""
        if amount < 0:
            amount = len(self._body)
        result = self._body[self._offset : self._offset + amount]
        self._offset += len(result)
        return result

    def close(self):
        self.closed = True


def test_production_contract_is_the_frozen_authoritative_inventory():
    assert migration.PRODUCTION_CONTRACT == migration.ManifestContract(
        entry_count=2214,
        total_bytes=678_858_152,
        mime_counts={"image/png": 1568, "image/webp": 556, "image/jpeg": 90},
        keyset_digest="93e30738942bc0c4b85d85d63239c82588ec1d163c5c3820ef2de01dc07caeb7",
        manifest_digest="72ecc6b84bfec9ba012a24dea9c5bcdf6d1beaad8d81c68eb4697f8e83e188ff",
    )


def test_dry_run_needs_no_environment_and_never_constructs_clients(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    source, manifest, anchor, entries = _write_fixture(
        tmp_path, [(entry, PNG)], extras=[("unmanifested.bin", b"do-not-open")]
    )
    report = tmp_path / "report.json"
    monkeypatch.setattr(migration, "SOURCE_DIR", source)
    called = []

    result = migration.run(
        [
            "--manifest", str(manifest),
            "--expected-manifest-file-sha256", anchor,
            "--report", str(report),
        ],
        contract=_contract(entries),
        environ={},
        r2_factory=lambda _: called.append("r2"),
        rpc_factory=lambda _: called.append("rpc"),
    )

    payload = json.loads(report.read_text("utf-8"))
    assert result == 0
    assert called == []
    assert payload["mode"] == "dry-run"
    assert payload["certified"] is False
    assert payload["expected"]["count"] == payload["observed"]["count"] == 1
    assert payload["excluded_unmanifested"]["count"] == 1
    assert payload["excluded_unmanifested"]["bytes"] == len(b"do-not-open")
    assert payload["rpc"] == {"status": "not_started", "count": 0}


def test_external_file_sha_is_checked_before_json_or_local_audit(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(migration, "SOURCE_DIR", tmp_path / "missing")

    with pytest.raises(migration.MigrationError, match="manifest_anchor_mismatch"):
        migration.prepare(manifest, "0" * 64, migration.PRODUCTION_CONTRACT)


def test_injectable_contract_can_freeze_authoritative_digests(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    _, manifest, anchor, entries = _write_fixture(tmp_path, [(entry, PNG)])
    contract = _contract(entries)
    frozen = migration.ManifestContract(
        entry_count=contract.entry_count,
        total_bytes=contract.total_bytes,
        mime_counts=contract.mime_counts,
        keyset_digest="0" * 64,
        manifest_digest="1" * 64,
    )

    with pytest.raises(migration.MigrationError, match="manifest_keyset_digest_mismatch"):
        migration.load_manifest(manifest, anchor, frozen)


@pytest.mark.parametrize("nested", [False, True])
def test_duplicate_json_member_names_are_rejected_recursively(tmp_path, nested):
    entry = _entry(PNG, "png", "image/png")
    document = json.dumps(_manifest([entry]), sort_keys=True, separators=(",", ":"))
    if nested:
        needle = f'"byte_size":{len(PNG)}'
        document = document.replace(needle, f'{needle},"byte_size":{len(PNG)}', 1)
    else:
        document = document.replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1)
    raw = document.encode()
    path = tmp_path / "duplicate.json"
    path.write_bytes(raw)

    with pytest.raises(migration.MigrationError, match="manifest_json_duplicate"):
        migration.load_manifest(path, hashlib.sha256(raw).hexdigest(), _contract([entry]))


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda value: value.update(schema_version=2), "manifest_schema_invalid"),
        (lambda value: value.update(logical_bucket="quote-files"), "manifest_bucket_invalid"),
        (lambda value: value.update(entry_count=2), "manifest_count_mismatch"),
        (lambda value: value.update(total_bytes=999), "manifest_bytes_mismatch"),
        (lambda value: value.update(mime_counts={"image/png": 0, "image/webp": 1, "image/jpeg": 0}), "manifest_mime_mismatch"),
        (lambda value: value.update(keyset_digest="0" * 64), "manifest_keyset_digest_mismatch"),
        (lambda value: value.update(manifest_digest="0" * 64), "manifest_digest_mismatch"),
        (lambda value: value["entries"].append(dict(value["entries"][0])), "manifest_duplicate_name"),
        (lambda value: value["entries"][0].update(object_name="../" + value["entries"][0]["object_name"]), "manifest_name_invalid"),
        (lambda value: value["entries"][0].update(sha256="0" * 64), "manifest_sha_name_mismatch"),
        (lambda value: value["entries"][0].update(mime_type="image/webp"), "manifest_mime_name_mismatch"),
    ],
)
def test_manifest_rejects_contract_and_entry_mutations(tmp_path, mutate, code):
    entry = _entry(PNG, "png", "image/png")
    value = _manifest([entry])
    mutate(value)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)

    with pytest.raises(migration.MigrationError, match=code):
        migration.load_manifest(path, hashlib.sha256(raw).hexdigest(), _contract([entry]))


def test_manifest_accepts_png_jpg_jpeg_and_webp_with_fixed_mime_mapping(tmp_path):
    fixtures = [
        (_entry(PNG, "png", "image/png"), PNG),
        (_entry(JPEG[:-2] + b"j" + JPEG[-2:], "jpg", "image/jpeg"), JPEG[:-2] + b"j" + JPEG[-2:]),
        (_entry(JPEG[:-2] + b"e" + JPEG[-2:], "jpeg", "image/jpeg"), JPEG[:-2] + b"e" + JPEG[-2:]),
        (_entry(WEBP, "webp", "image/webp"), WEBP),
    ]
    source, manifest, anchor, entries = _write_fixture(tmp_path, fixtures)

    loaded = migration.load_manifest(manifest, anchor, _contract(entries))
    audit = migration.audit_local_source(source, loaded.entries)

    assert audit.count == 4
    assert audit.total_bytes == sum(len(body) for _, body in fixtures)


@pytest.mark.parametrize(
    "kind",
    ["missing", "size", "hash", "magic_png", "magic_jpeg", "magic_webp"],
)
def test_local_audit_fails_closed_before_network_for_every_body_problem(tmp_path, kind):
    body, extension, mime = {
        "magic_png": (b"bad png", "png", "image/png"),
        "magic_jpeg": (b"bad jpeg", "jpg", "image/jpeg"),
        "magic_webp": (b"bad webp body!!", "webp", "image/webp"),
    }.get(kind, (PNG, "png", "image/png"))
    valid_entry = _entry(body if not kind.startswith("magic") else PNG, extension, mime)
    source = tmp_path / "source"
    source.mkdir()
    if kind != "missing":
        (source / valid_entry["object_name"]).write_bytes(body)
    entry = dict(valid_entry)
    if kind == "size":
        entry["byte_size"] += 1
    if kind == "hash":
        entry["sha256"] = entry["object_name"].split(".")[0]
        (source / entry["object_name"]).write_bytes(PNG + b"changed")

    with pytest.raises(migration.MigrationError, match="local_asset_"):
        migration.audit_local_source(source, [entry])


def test_manifested_symlink_or_reparse_point_is_rejected(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target.png"
    target.write_bytes(PNG)
    try:
        os.symlink(target, source / entry["object_name"])
    except OSError:
        pytest.skip("symlinks are not available in this Windows session")

    with pytest.raises(migration.MigrationError, match="local_asset_not_regular"):
        migration.audit_local_source(source, [entry])


def test_manifested_windows_reparse_attribute_is_rejected_without_open(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    path = tmp_path / entry["object_name"]
    path.write_bytes(PNG)
    actual = path.lstat()
    fields = {name: getattr(actual, name) for name in dir(actual) if name.startswith("st_")}
    fields["st_file_attributes"] = 0x400
    fake = SimpleNamespace(**fields)
    real_lstat = Path.lstat

    monkeypatch.setattr(Path, "lstat", lambda self: fake if self == path else real_lstat(self))
    monkeypatch.setattr(
        migration.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reparse body opened")),
    )

    with pytest.raises(migration.MigrationError, match="local_asset_not_regular"):
        migration._audit_manifested_file(path, entry)


def test_source_root_reparse_attribute_is_rejected_before_scandir(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    actual = source.lstat()
    fields = {name: getattr(actual, name) for name in dir(actual) if name.startswith("st_")}
    fields["st_file_attributes"] = 0x400
    fake = SimpleNamespace(**fields)
    real_lstat = Path.lstat

    monkeypatch.setattr(Path, "lstat", lambda self: fake if self == source else real_lstat(self))
    monkeypatch.setattr(
        migration.os,
        "scandir",
        lambda *_: (_ for _ in ()).throw(AssertionError("reparse root scanned")),
    )

    with pytest.raises(migration.MigrationError, match="local_source_not_directory"):
        migration.audit_local_source(source, [entry])


def test_extras_are_enumerated_but_their_bodies_are_never_opened(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(
        tmp_path, [(entry, PNG)], extras=[("extra.webp", b"not even a webp")]
    )
    real_open = os.open
    opened = []

    def recording_open(path, *args, **kwargs):
        opened.append(Path(path).name)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(migration.os, "open", recording_open)
    audit = migration.audit_local_source(source, [entry])

    assert entry["object_name"] in opened
    assert "extra.webp" not in opened
    assert audit.excluded_unmanifested.count == 1


def test_toctou_identity_change_between_lstat_and_fstat_is_rejected(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    real_fstat = migration.os.fstat

    def changed_fstat(fd):
        value = real_fstat(fd)
        fields = {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
        fields["st_ino"] = value.st_ino + 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(migration.os, "fstat", changed_fstat)
    with pytest.raises(migration.MigrationError, match="local_asset_changed"):
        migration.audit_local_source(source, [entry])


def test_existing_exact_head_does_not_put():
    entry = _entry(PNG, "png", "image/png")

    class Client:
        def head_object(self, **kwargs):
            assert kwargs == {"Bucket": "catalog-assets", "Key": entry["object_name"]}
            return _head(entry)

        def put_object(self, **kwargs):
            raise AssertionError("an exact existing object must not be overwritten")

    stats = migration.TransferStats()
    result = migration.ensure_r2_object(Client(), "catalog-assets", entry, Path("unused"), stats=stats)
    assert result == "existing"
    assert stats.existing == 1 and stats.created == 0


def test_404_puts_create_only_with_exact_headers_then_heads(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    path = tmp_path / entry["object_name"]
    path.write_bytes(PNG)

    class Client:
        def __init__(self):
            self.heads = 0

        def head_object(self, **kwargs):
            self.heads += 1
            if self.heads == 1:
                raise R2Error(404, "NoSuchKey")
            return _head(entry)

        def put_object(self, **kwargs):
            assert kwargs["Bucket"] == "catalog-assets"
            assert kwargs["Key"] == entry["object_name"]
            assert kwargs["IfNoneMatch"] == "*"
            assert kwargs["ContentType"] == "image/png"
            assert kwargs["CacheControl"] == CACHE_CONTROL
            assert kwargs["Metadata"] == {"sha256": entry["sha256"]}
            assert kwargs["Body"] == PNG
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    stats = migration.TransferStats()
    assert migration.ensure_r2_object(Client(), "catalog-assets", entry, path, stats=stats) == "created"
    assert stats.created == 1 and stats.head == 2 and stats.put == 1


def test_412_race_is_followed_by_exact_head(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    path = tmp_path / entry["object_name"]
    path.write_bytes(PNG)

    class Client:
        def __init__(self):
            self.heads = 0

        def head_object(self, **kwargs):
            self.heads += 1
            if self.heads == 1:
                raise R2Error(404)
            return _head(entry)

        def put_object(self, **kwargs):
            raise R2Error(412, "PreconditionFailed")

    stats = migration.TransferStats()
    assert migration.ensure_r2_object(Client(), "catalog-assets", entry, path, stats=stats) == "precondition_existing"
    assert stats.precondition_412 == 1


def test_upload_revalidates_path_after_audit_and_never_puts_changed_bytes(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    path = tmp_path / entry["object_name"]
    path.write_bytes(PNG)
    migration._audit_manifested_file(path, entry)
    path.write_bytes(PNG + b"changed-after-audit")
    puts = []

    class Client:
        def head_object(self, **kwargs):
            raise R2Error(404)

        def put_object(self, **kwargs):
            puts.append(kwargs)

    with pytest.raises(migration.MigrationError, match="local_asset_"):
        migration.ensure_r2_object(
            Client(), "catalog-assets", entry, path, stats=migration.TransferStats()
        )
    assert puts == []


def test_upload_passes_verified_immutable_bytes_and_never_reopens_path(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    path = tmp_path / entry["object_name"]
    path.write_bytes(PNG)

    class Client:
        def __init__(self):
            self.heads = 0

        def head_object(self, **kwargs):
            self.heads += 1
            if self.heads == 1:
                raise R2Error(404)
            return _head(entry)

        def put_object(self, **kwargs):
            path.write_bytes(b"corrupted-during-put")
            assert type(kwargs["Body"]) is bytes
            assert kwargs["Body"] == PNG
            return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    assert migration.ensure_r2_object(
        Client(), "catalog-assets", entry, path, stats=migration.TransferStats()
    ) == "created"


def test_head_mismatch_is_fatal_and_never_corrected():
    entry = _entry(PNG, "png", "image/png")

    class Client:
        def head_object(self, **kwargs):
            return _head(entry) | {"CacheControl": "no-cache"}

        def put_object(self, **kwargs):
            raise AssertionError("mismatch must not trigger a corrective PUT")

    with pytest.raises(migration.MigrationError, match="r2_head_mismatch"):
        migration.ensure_r2_object(Client(), "catalog-assets", entry, Path("unused"), stats=migration.TransferStats())


def test_403_is_a_fatal_circuit_breaker_without_retry():
    entry = _entry(PNG, "png", "image/png")
    attempts = []

    class Client:
        def head_object(self, **kwargs):
            attempts.append(1)
            raise R2Error(403, "AccessDenied")

    with pytest.raises(migration.MigrationError, match="r2_access_denied"):
        migration.ensure_r2_object(
            Client(), "catalog-assets", entry, Path("unused"), stats=migration.TransferStats(),
            retry=migration.RetryPolicy(max_attempts=5, sleep=lambda _: None, random_fn=lambda: 0),
        )
    assert len(attempts) == 1


def test_retryable_5xx_uses_bounded_injectable_backoff():
    entry = _entry(PNG, "png", "image/png")
    sleeps = []

    class Client:
        def __init__(self):
            self.attempts = 0

        def head_object(self, **kwargs):
            self.attempts += 1
            if self.attempts < 3:
                raise R2Error(503, "SlowDown")
            return _head(entry)

    client = Client()
    stats = migration.TransferStats()
    migration.ensure_r2_object(
        client, "catalog-assets", entry, Path("unused"), stats=stats,
        retry=migration.RetryPolicy(max_attempts=3, base_delay=1, sleep=sleeps.append, random_fn=lambda: 0),
    )
    assert client.attempts == 3
    assert sleeps == [1, 2]
    assert stats.retries == 2


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: pytest.importorskip("botocore.exceptions").EndpointConnectionError(
            endpoint_url="https://redacted.invalid"
        ),
        lambda: pytest.importorskip("botocore.exceptions").ConnectionClosedError(
            endpoint_url="https://redacted.invalid", request=None, response=None
        ),
        lambda: pytest.importorskip("botocore.exceptions").ConnectTimeoutError(
            endpoint_url="https://redacted.invalid"
        ),
        lambda: pytest.importorskip("botocore.exceptions").ReadTimeoutError(
            endpoint_url="https://redacted.invalid", request=None
        ),
    ],
)
def test_real_botocore_transport_errors_are_retried(error_factory):
    entry = _entry(PNG, "png", "image/png")
    sleeps = []

    class Client:
        def __init__(self):
            self.attempts = 0

        def head_object(self, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise error_factory()
            return _head(entry)

    client = Client()
    stats = migration.TransferStats()
    migration.ensure_r2_object(
        client, "catalog-assets", entry, Path("unused"), stats=stats,
        retry=migration.RetryPolicy(max_attempts=2, sleep=sleeps.append, random_fn=lambda: 0),
    )
    assert client.attempts == 2
    assert stats.attempts == 2 and stats.retries == 1
    assert sleeps == [0.25]


def test_full_get_streams_hashes_and_always_closes_body():
    entry = _entry(PNG, "png", "image/png")
    body = StreamingBody(PNG)

    class Client:
        def get_object(self, **kwargs):
            return _head(entry) | {"Body": body}

    stats = migration.TransferStats()
    migration.verify_r2_body(Client(), "catalog-assets", entry, stats=stats, chunk_size=3)
    assert body.closed is True
    assert stats.full_get == 1


def test_full_get_failure_closes_body_and_is_sanitized():
    entry = _entry(PNG, "png", "image/png")
    body = StreamingBody(PNG, fail=True)

    class Client:
        def get_object(self, **kwargs):
            return _head(entry) | {"Body": body}

    with pytest.raises(migration.MigrationError, match="r2_get_body_failed") as error:
        migration.verify_r2_body(Client(), "catalog-assets", entry, stats=migration.TransferStats())
    assert body.closed is True
    assert "secret" not in str(error.value)


def test_full_get_closes_body_even_when_headers_mismatch():
    entry = _entry(PNG, "png", "image/png")
    body = StreamingBody(PNG)

    class Client:
        def get_object(self, **kwargs):
            return _head(entry) | {"ContentType": "image/webp", "Body": body}

    with pytest.raises(migration.MigrationError, match="r2_get_header_mismatch"):
        migration.verify_r2_body(Client(), "catalog-assets", entry, stats=migration.TransferStats())
    assert body.closed is True


def test_no_rpc_occurs_before_the_last_full_get(tmp_path):
    first = _entry(PNG, "png", "image/png")
    second_body = JPEG + b"second"
    second = _entry(second_body, "jpg", "image/jpeg")
    entries = [first, second]
    source, _, _, _ = _write_fixture(tmp_path, [(first, PNG), (second, second_body)])
    calls = []

    class Client:
        def head_object(self, **kwargs):
            entry = next(item for item in entries if item["object_name"] == kwargs["Key"])
            return _head(entry)

        def get_object(self, **kwargs):
            calls.append(("get", kwargs["Key"]))
            if kwargs["Key"] == second["object_name"]:
                raise R2Error(500)
            return _head(first) | {"Body": StreamingBody(PNG)}

    class Rpc:
        def call(self, name, payload):
            calls.append(("rpc", name))

    prepared = SimpleNamespace(
        entries=entries, source_dir=source, manifest_file_sha256="a" * 64,
        manifest_digest="b" * 64, keyset_digest="c" * 64,
    )
    with pytest.raises(migration.MigrationError):
        migration.execute_migration(prepared, Client(), Rpc(), checkpoint_path=tmp_path / "cp.json")
    assert not any(call[0] == "rpc" for call in calls)


def test_rpc_order_payloads_and_intermediate_failure_never_finalize():
    entries = sorted(
        [_entry(PNG, "png", "image/png"), _entry(JPEG, "jpg", "image/jpeg")],
        key=lambda item: item["object_name"],
    )
    calls = []

    class Rpc:
        def call(self, name, payload):
            calls.append((name, payload))
            if name == "saas_register_catalog_asset" and payload["p_object_name"] == entries[1]["object_name"]:
                raise migration.MigrationError("rpc_register_failed")
            if name in {"saas_start_catalog_asset_cutover_batch", "saas_finalize_catalog_asset_cutover_batch"}:
                return payload["p_batch_id"]
            return payload["p_object_name"]

    batch_id = migration.deterministic_batch_id("a" * 64, "b" * 64, "c" * 64)
    with pytest.raises(migration.MigrationError, match="rpc_register_failed"):
        migration.run_registry_cutover(Rpc(), entries, batch_id, "b" * 64, "c" * 64)

    names = [name for name, _ in calls]
    assert names == [
        "saas_start_catalog_asset_cutover_batch",
        "saas_add_catalog_asset_cutover_entry",
        "saas_add_catalog_asset_cutover_entry",
        "saas_register_catalog_asset",
        "saas_register_catalog_asset",
    ]
    assert calls[0][1] == {
        "p_batch_id": batch_id,
        "p_expected_count": 2,
        "p_manifest_digest": "b" * 64,
        "p_keyset_digest": "c" * 64,
    }
    assert calls[1][1] == {"p_batch_id": batch_id, **{f"p_{key}": entries[0][key] for key in ("object_name", "sha256", "byte_size", "mime_type")}}
    assert calls[3][1] == {
        "p_object_name": entries[0]["object_name"],
        "p_storage_provider": "r2",
        "p_physical_bucket": "catalog-assets",
        "p_byte_size": entries[0]["byte_size"],
        "p_mime_type": entries[0]["mime_type"],
    }
    assert "saas_finalize_catalog_asset_cutover_batch" not in names


def test_successful_rpc_order_finishes_only_after_all_adds_and_registers():
    entry = _entry(PNG, "png", "image/png")
    calls = []

    class Rpc:
        def call(self, name, payload):
            calls.append(name)
            if name in {"saas_start_catalog_asset_cutover_batch", "saas_finalize_catalog_asset_cutover_batch"}:
                return payload["p_batch_id"]
            return payload["p_object_name"]

    migration.run_registry_cutover(Rpc(), [entry], "d" * 36, "b" * 64, "c" * 64)
    assert calls == [
        "saas_start_catalog_asset_cutover_batch",
        "saas_add_catalog_asset_cutover_entry",
        "saas_register_catalog_asset",
        "saas_finalize_catalog_asset_cutover_batch",
    ]


def _batch_row(prepared, batch_id, status="verified", **overrides):
    value = {
        "batch_id": batch_id,
        "manifest_digest": prepared.manifest_digest,
        "keyset_digest": prepared.keyset_digest,
        "expected_count": len(prepared.entries),
        "status": status,
        "verified_count": len(prepared.entries) if status == "verified" else 0,
        "missing_count": 0,
        "failed_count": 0,
    }
    value.update(overrides)
    return value


def _rpc_result(name, payload):
    if name in {"saas_start_catalog_asset_cutover_batch", "saas_finalize_catalog_asset_cutover_batch"}:
        return payload["p_batch_id"]
    return payload["p_object_name"]


def test_spoofed_finalized_checkpoint_is_not_db_authority_and_replays_full_rpc(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    prepared = SimpleNamespace(
        entries=[entry], source_dir=source, manifest_file_sha256="a" * 64,
        manifest_digest="b" * 64, keyset_digest="c" * 64,
    )
    checkpoint = tmp_path / "checkpoint.json"
    batch_id = migration.deterministic_batch_id("a" * 64, "b" * 64, "c" * 64)
    migration.write_checkpoint(
        checkpoint, migration.new_checkpoint(prepared, batch_id, prepared_names=[entry["object_name"]], rpc_status="finalized")
    )
    calls = []

    class Client:
        def head_object(self, **kwargs):
            raise AssertionError("resume may skip completed HEAD/PUT")

        def get_object(self, **kwargs):
            calls.append("get")
            return _head(entry) | {"Body": StreamingBody(PNG)}

    class Rpc:
        def __init__(self):
            self.row = None

        def get_cutover_batch(self, requested_batch_id):
            calls.append("db")
            assert requested_batch_id == batch_id
            return self.row

        def call(self, name, payload):
            calls.append(name)
            if name == "saas_finalize_catalog_asset_cutover_batch":
                self.row = _batch_row(prepared, batch_id)
            return _rpc_result(name, payload)

    outcome = migration.execute_migration(prepared, Client(), Rpc(), checkpoint_path=checkpoint)
    assert calls == [
        "get", "db", "saas_start_catalog_asset_cutover_batch",
        "saas_add_catalog_asset_cutover_entry", "saas_register_catalog_asset",
        "saas_finalize_catalog_asset_cutover_batch", "db",
    ]
    assert outcome.certified is True
    mismatched = SimpleNamespace(**(prepared.__dict__ | {"manifest_file_sha256": "f" * 64}))
    with pytest.raises(migration.MigrationError, match="checkpoint_binding_mismatch"):
        migration.load_checkpoint(checkpoint, mismatched, batch_id)


def test_exact_verified_db_row_still_requires_idempotent_finalize_before_certify(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    prepared = SimpleNamespace(
        entries=[entry], source_dir=source, manifest_file_sha256="a" * 64,
        manifest_digest="b" * 64, keyset_digest="c" * 64,
    )
    batch_id = migration.deterministic_batch_id("a" * 64, "b" * 64, "c" * 64)
    calls = []

    class Client:
        def head_object(self, **kwargs):
            return _head(entry)

        def get_object(self, **kwargs):
            return _head(entry) | {"Body": StreamingBody(PNG)}

    class Rpc:
        def get_cutover_batch(self, requested_batch_id):
            calls.append("db")
            return _batch_row(prepared, batch_id)

        def call(self, name, payload):
            calls.append(name)
            assert name == "saas_finalize_catalog_asset_cutover_batch"
            return batch_id

    outcome = migration.execute_migration(
        prepared, Client(), Rpc(), checkpoint_path=tmp_path / "checkpoint.json"
    )
    assert outcome.certified is True
    assert calls == ["db", "saas_finalize_catalog_asset_cutover_batch", "db"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"manifest_digest": "f" * 64},
        {"keyset_digest": "f" * 64},
        {"expected_count": 99},
        {"verified_count": 0},
        {"verified_count": True},
        {"missing_count": 1},
        {"missing_count": False},
        {"failed_count": 1},
        {"status": "failed"},
    ],
)
def test_verified_db_proof_mismatch_fails_without_finalize(tmp_path, overrides):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    prepared = SimpleNamespace(
        entries=[entry], source_dir=source, manifest_file_sha256="a" * 64,
        manifest_digest="b" * 64, keyset_digest="c" * 64,
    )
    batch_id = migration.deterministic_batch_id("a" * 64, "b" * 64, "c" * 64)

    class Client:
        def head_object(self, **kwargs):
            return _head(entry)

        def get_object(self, **kwargs):
            return _head(entry) | {"Body": StreamingBody(PNG)}

    class Rpc:
        def get_cutover_batch(self, requested_batch_id):
            return _batch_row(prepared, batch_id, **overrides)

        def call(self, name, payload):
            raise AssertionError("mismatching DB proof must not call RPC")

    with pytest.raises(migration.MigrationError, match="cutover_batch_db_mismatch"):
        migration.execute_migration(
            prepared, Client(), Rpc(), checkpoint_path=tmp_path / "checkpoint.json"
        )


def test_crash_after_finalize_response_loss_resumes_from_verified_db_not_checkpoint(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    prepared = SimpleNamespace(
        entries=[entry], source_dir=source, manifest_file_sha256="a" * 64,
        manifest_digest="b" * 64, keyset_digest="c" * 64,
    )
    batch_id = migration.deterministic_batch_id("a" * 64, "b" * 64, "c" * 64)
    checkpoint = tmp_path / "checkpoint.json"

    class Client:
        def head_object(self, **kwargs):
            return _head(entry)

        def get_object(self, **kwargs):
            return _head(entry) | {"Body": StreamingBody(PNG)}

    class Rpc:
        def __init__(self):
            self.row = None
            self.lose_once = True
            self.calls = []

        def get_cutover_batch(self, requested_batch_id):
            self.calls.append("db")
            return self.row

        def call(self, name, payload):
            self.calls.append(name)
            if name == "saas_finalize_catalog_asset_cutover_batch":
                self.row = _batch_row(prepared, batch_id)
                if self.lose_once:
                    self.lose_once = False
                    raise migration.MigrationError("rpc_failed")
            return _rpc_result(name, payload)

    rpc = Rpc()
    with pytest.raises(migration.MigrationError, match="rpc_failed"):
        migration.execute_migration(prepared, Client(), rpc, checkpoint_path=checkpoint)
    first_call_count = len(rpc.calls)
    outcome = migration.execute_migration(prepared, Client(), rpc, checkpoint_path=checkpoint)

    assert outcome.certified is True
    assert rpc.calls[first_call_count:] == ["db", "saas_finalize_catalog_asset_cutover_batch", "db"]
    assert outcome.stats.attempts == 1
    assert outcome.cumulative_stats.attempts == 3


def test_finalize_response_without_verified_db_proof_never_certifies(tmp_path):
    entry = _entry(PNG, "png", "image/png")
    source, _, _, _ = _write_fixture(tmp_path, [(entry, PNG)])
    prepared = SimpleNamespace(
        entries=[entry], source_dir=source, manifest_file_sha256="a" * 64,
        manifest_digest="b" * 64, keyset_digest="c" * 64,
    )

    class Client:
        def head_object(self, **kwargs):
            return _head(entry)

        def get_object(self, **kwargs):
            return _head(entry) | {"Body": StreamingBody(PNG)}

    class Rpc:
        def get_cutover_batch(self, requested_batch_id):
            return None

        def call(self, name, payload):
            return _rpc_result(name, payload)

    with pytest.raises(migration.MigrationError, match="cutover_batch_db_unverified"):
        migration.execute_migration(
            prepared, Client(), Rpc(), checkpoint_path=tmp_path / "checkpoint.json"
        )


def test_execute_config_reads_only_allowed_names_and_uses_explicit_credentials(monkeypatch):
    accessed = []

    class Env(dict):
        def get(self, key, default=None):
            accessed.append(key)
            return super().get(key, default)

    env = Env(
        CATALOG_ASSET_R2_ENDPOINT_URL="https://account.example.invalid",
        CATALOG_ASSET_R2_ACCESS_KEY_ID="access",
        CATALOG_ASSET_R2_SECRET_ACCESS_KEY="secret",
        CATALOG_ASSET_R2_REGION="auto",
        CATALOG_ASSET_R2_BUCKET="catalog-assets",
        SUPABASE_URL="https://project.example.invalid",
        SUPABASE_SERVICE_KEY="service",
        R2_BUCKET="quote-files",
        AWS_PROFILE="forbidden",
    )
    config = migration.load_execute_config(env)
    captured = {}

    class Boto:
        @staticmethod
        def client(service, **kwargs):
            captured.update(service=service, **kwargs)
            return object()

    migration.create_r2_client(config, boto3_module=Boto)
    assert set(accessed) == {
        "CATALOG_ASSET_R2_ENDPOINT_URL", "CATALOG_ASSET_R2_ACCESS_KEY_ID",
        "CATALOG_ASSET_R2_SECRET_ACCESS_KEY", "CATALOG_ASSET_R2_REGION",
        "CATALOG_ASSET_R2_BUCKET", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
    }
    assert captured == {
        "service": "s3", "endpoint_url": "https://account.example.invalid",
        "aws_access_key_id": "access", "aws_secret_access_key": "secret",
        "region_name": "auto",
    }


def test_private_batch_select_is_authenticated_exact_and_never_uses_storage_endpoint():
    batch_id = "76868e70-7ac9-5467-8f03-8379e277a6f7"
    row = {
        "batch_id": batch_id,
        "manifest_digest": "b" * 64,
        "keyset_digest": "c" * 64,
        "expected_count": 2214,
        "status": "verified",
        "verified_count": 2214,
        "missing_count": 0,
        "failed_count": 0,
    }
    requests = []

    class Response:
        status = 200

        def __init__(self):
            self._reads = [json.dumps([row]).encode(), b""]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _amount=-1):
            return self._reads.pop(0)

    def opener(request, timeout):
        requests.append(request)
        return Response()

    client = migration.SupabaseRpcClient(
        "https://project.example.invalid", "service-secret", opener=opener
    )
    assert client.get_cutover_batch(batch_id) == row
    request = requests[0]
    assert "/rest/v1/saas_catalog_asset_cutover_batches?" in request.full_url
    assert "storage/v1" not in request.full_url
    assert "select=batch_id%2Cmanifest_digest%2Ckeyset_digest%2Cexpected_count%2Cstatus%2Cverified_count%2Cmissing_count%2Cfailed_count" in request.full_url
    assert f"batch_id=eq.{batch_id}" in request.full_url
    assert request.get_header("Authorization") == "Bearer service-secret"
    assert request.get_header("Apikey") == "service-secret"


def test_report_and_checkpoint_are_sanitized_and_certification_is_gated(tmp_path):
    report = tmp_path / "report.json"
    checkpoint = tmp_path / "checkpoint.json"
    secret = "super-secret-service-key"
    migration.write_checkpoint(checkpoint, {"schema_version": 1, "status": "loading"})
    migration.write_report(
        report,
        {"mode": "execute", "certified": False, "failures": [{"code": "rpc_failed"}]},
    )
    combined = checkpoint.read_text("utf-8") + report.read_text("utf-8")
    assert secret not in combined
    assert "Authorization" not in combined
    assert "endpoint" not in combined.lower()
    assert json.loads(report.read_text("utf-8"))["certified"] is False


def test_atomic_outputs_defensively_redact_sensitive_keys_urls_and_exceptions(tmp_path):
    report = tmp_path / "report.json"
    secret = "super-secret-service-key"

    migration.write_report(
        report,
        {
            "status": "failed",
            "Authorization": f"Bearer {secret}",
            "endpoint_url": "https://private.example.invalid",
            "nested": {"raw_exception": RuntimeError(secret), "safe_code": "r2_failed"},
        },
    )

    raw = report.read_text("utf-8")
    payload = json.loads(raw)
    assert secret not in raw
    assert "private.example.invalid" not in raw
    assert "Authorization" not in raw
    assert "endpoint" not in raw.lower()
    assert payload["nested"]["safe_code"] == "r2_failed"


def test_failed_execute_report_keeps_partial_attempts_and_never_certifies(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    source, manifest, anchor, entries = _write_fixture(tmp_path, [(entry, PNG)])
    report = tmp_path / "report.json"
    monkeypatch.setattr(migration, "SOURCE_DIR", source)
    env = {
        "CATALOG_ASSET_R2_ENDPOINT_URL": "https://account.example.invalid",
        "CATALOG_ASSET_R2_ACCESS_KEY_ID": "access",
        "CATALOG_ASSET_R2_SECRET_ACCESS_KEY": "never-report-this-secret",
        "CATALOG_ASSET_R2_REGION": "auto",
        "CATALOG_ASSET_R2_BUCKET": "catalog-assets",
        "SUPABASE_URL": "https://project.example.invalid",
        "SUPABASE_SERVICE_KEY": "never-report-this-service-key",
    }

    class Client:
        def head_object(self, **kwargs):
            raise R2Error(403, "AccessDenied")

    result = migration.run(
        [
            "--manifest", str(manifest),
            "--expected-manifest-file-sha256", anchor,
            "--report", str(report),
            "--execute",
        ],
        contract=_contract(entries),
        environ=env,
        r2_factory=lambda _: Client(),
        rpc_factory=lambda _: SimpleNamespace(
            call=lambda *_: (_ for _ in ()).throw(AssertionError("RPC must not be called"))
        ),
    )

    raw = report.read_text("utf-8")
    payload = json.loads(raw)
    assert result == 2
    assert payload["certified"] is False
    assert payload["transfer"]["current"]["attempts"] == 1
    assert payload["transfer"]["cumulative"]["attempts"] == 1
    assert payload["failures"] == [{"code": "r2_access_denied"}]
    assert "never-report" not in raw


def test_cli_has_no_source_override_and_execute_checkpoint_defaults_next_to_report():
    parser = migration.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--source", "elsewhere"])
    args = parser.parse_args([
        "--manifest", "manifest.json", "--expected-manifest-file-sha256", "a" * 64,
        "--report", "run-report.json", "--execute",
    ])
    assert migration.resolve_checkpoint_path(args) == Path("run-report.checkpoint.json")


def test_output_paths_cannot_overwrite_manifest_source_or_each_other(tmp_path, monkeypatch):
    entry = _entry(PNG, "png", "image/png")
    source, manifest, anchor, entries = _write_fixture(tmp_path, [(entry, PNG)])
    original_manifest = manifest.read_bytes()
    monkeypatch.setattr(migration, "SOURCE_DIR", source)

    same_manifest = migration.run(
        [
            "--manifest", str(manifest),
            "--expected-manifest-file-sha256", anchor,
            "--report", str(manifest),
        ],
        contract=_contract(entries),
        environ={},
    )
    source_report = migration.run(
        [
            "--manifest", str(manifest),
            "--expected-manifest-file-sha256", anchor,
            "--report", str(source / "unsafe-report.json"),
        ],
        contract=_contract(entries),
        environ={},
    )
    shared = tmp_path / "shared.json"
    same_outputs = migration.run(
        [
            "--manifest", str(manifest),
            "--expected-manifest-file-sha256", anchor,
            "--report", str(shared),
            "--checkpoint", str(shared),
            "--execute",
        ],
        contract=_contract(entries),
        environ={},
    )

    assert (same_manifest, source_report, same_outputs) == (2, 2, 2)
    assert manifest.read_bytes() == original_manifest
    assert not (source / "unsafe-report.json").exists()
    assert not shared.exists()


def test_output_parent_creation_error_is_sanitized(tmp_path, monkeypatch):
    secret = "secret-path-detail"
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(secret)),
    )

    with pytest.raises(migration.MigrationError, match="atomic_output_failed") as error:
        migration.write_report(tmp_path / "missing" / "report.json", {"status": "failed"})
    assert secret not in str(error.value)
