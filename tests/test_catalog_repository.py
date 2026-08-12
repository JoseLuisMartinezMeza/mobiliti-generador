import hashlib
import io
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request
from uuid import UUID

import pytest

from mobiliti_saas.worker.catalog_sync.graph import DownloadedFile, GraphItem
from mobiliti_saas.worker.catalog_sync.rates import ExchangeRate
from mobiliti_saas.worker.catalog_sync import repository as repository_module
from mobiliti_saas.worker.catalog_sync.repository import (
    CatalogRepository,
    CatalogRepositoryError,
    SyncClaim,
    RunRecord,
    SnapshotRecord,
    SourceFileRecord,
    SourceRecord,
)


BASE_URL = "https://abcdefghijklmnopqrst.supabase.co"
KEY = "service-secret-value"
SOURCE_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
FILE_ID = "33333333-3333-3333-3333-333333333333"
SNAPSHOT_ID = "44444444-4444-4444-4444-444444444444"


class Response(io.BytesIO):
    def __init__(self, status, payload=b"", headers=None):
        super().__init__(payload)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected socket request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status, value, headers=None):
    response_headers = {"Content-Type": "application/json"}
    response_headers.update(headers or {})
    return Response(status, json.dumps(value).encode(), response_headers)


def source_row(**changes):
    row = {
        "id": SOURCE_ID,
        "supplier": "sunon",
        "label": "Sunon",
        "adapter": "sunon",
        "graph_drive_id": "drive",
        "graph_root_item_id": "root",
        "delta_link": None,
        "enabled": True,
        "published_version_id": SNAPSHOT_ID,
    }
    row.update(changes)
    return row


def run_row(**changes):
    row = {
        "id": RUN_ID,
        "source_id": SOURCE_ID,
        "trigger_type": "manual",
        "status": "requested",
        "requested_by": 7,
        "candidate_version_id": None,
        "metrics": {},
        "error_summary": None,
    }
    row.update(changes)
    return row


def file_row(**changes):
    row = {
        "id": FILE_ID,
        "source_id": SOURCE_ID,
        "drive_item_id": "item-1",
        "path": "SUNON MTY/prices.xlsx",
        "e_tag": '"etag-1"',
        "c_tag": None,
        "size_bytes": 3,
        "sha256": hashlib.sha256(b"abc").hexdigest(),
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "private_object_path": f"catalog-sources/{hashlib.sha256(b'abc').hexdigest()}.xlsx",
        "validation_status": "valid",
        "validation_summary": {},
        "last_sync_run_id": RUN_ID,
        "is_deleted": False,
        "deleted_at": None,
        "deleted_sync_run_id": None,
        "discovered_at": "2026-07-16T12:00:00Z",
        "validated_at": "2026-07-16T12:00:00Z",
    }
    row.update(changes)
    return row


def repository(responses):
    opener = Opener(responses)
    return CatalogRepository(BASE_URL, KEY, opener=opener), opener


def request_parts(opener, index=0):
    request, timeout = opener.requests[index]
    parsed = urlsplit(request.full_url)
    return request, parsed, parse_qs(parsed.query), timeout


def test_from_environment_requires_valid_supabase_configuration(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(CatalogRepositoryError, match="configuration"):
        CatalogRepository.from_environment()

    monkeypatch.setenv("SUPABASE_URL", "http://evil.example/query?token=leak")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", KEY)
    with pytest.raises(CatalogRepositoryError) as caught:
        CatalogRepository.from_environment()
    assert KEY not in str(caught.value)
    assert "evil" not in str(caught.value)
    assert "token" not in str(caught.value)

    for invalid_host in (
        "https://abcdefghijklmnopqrs.supabase.co",
        "https://abcdefghijklmnopqrstu.supabase.co",
        "https://abcdefghijklmnopqrs1.supabase.co",
        "https://ABCDEFGHIJKLMNOPQRST.supabase.co",
    ):
        with pytest.raises(CatalogRepositoryError, match="configuration"):
            CatalogRepository(invalid_host, KEY)


def test_default_opener_disables_proxies_and_redirects_and_redacts_exceptions():
    repo = CatalogRepository(BASE_URL, KEY)
    assert not any(hasattr(handler, "proxy_open") for handler in repo._opener.handlers)
    redirect_handlers = [
        handler for handler in repo._opener.handlers if isinstance(handler, HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    request = Request(f"{BASE_URL}/rest/v1/test")
    assert redirect_handlers[0].redirect_request(request, None, 302, "found", {}, BASE_URL) is None

    class SecretError(Exception):
        pass

    failed, _ = repository([SecretError(f"{KEY} {BASE_URL}/?delta=secret")])
    with pytest.raises(CatalogRepositoryError) as caught:
        failed.get_source("sunon")
    assert KEY not in str(caught.value) and "delta" not in str(caught.value)

    class StopNow(BaseException):
        pass

    class StopOpener:
        def open(self, request, timeout):
            raise StopNow

    with pytest.raises(StopNow):
        CatalogRepository(BASE_URL, KEY, opener=StopOpener()).get_source("sunon")


def test_get_source_uses_exact_host_filters_headers_and_strict_record():
    repo, opener = repository([response(200, [source_row()])])
    record = repo.get_source("sunon")
    assert isinstance(record, SourceRecord)
    assert record.id == UUID(SOURCE_ID)
    request, parsed, query, timeout = request_parts(opener)
    assert parsed.hostname == "abcdefghijklmnopqrst.supabase.co"
    assert parsed.path == "/rest/v1/saas_catalog_sources"
    assert query["supplier"] == ["eq.sunon"]
    assert query["limit"] == ["2"]
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["Apikey"] == KEY
    assert 0 < timeout <= 30

    bad, _ = repository([response(200, [source_row(extra="field")])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        bad.get_source("sunon")


def test_json_response_is_bounded_and_errors_are_redacted():
    huge = Response(200, b"[" + b" " * (4 * 1024 * 1024) + b"]")
    repo, _ = repository([huge])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.get_source("sunon")

    body = b'{"message":"delta-token private/object.xlsx service-secret-value"}'
    failure = HTTPError(f"{BASE_URL}/rest/v1/table?secret=yes", 500, "bad", {}, io.BytesIO(body))
    repo, _ = repository([failure])
    with pytest.raises(CatalogRepositoryError) as caught:
        repo.get_source("sunon")
    message = str(caught.value)
    assert KEY not in message and "delta-token" not in message
    assert "private/object" not in message and "secret=yes" not in message

    wrong_type = Response(200, json.dumps([source_row()]).encode(), {"Content-Type": "text/html"})
    repo, _ = repository([wrong_type])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.get_source("sunon")


def test_create_and_conditionally_claim_run_including_lost_race():
    repo, opener = repository(
        [response(201, [run_row()]), response(200, [run_row(status="running")]), response(200, [])]
    )
    created = repo.create_run(UUID(SOURCE_ID), "manual", 7)
    assert isinstance(created, RunRecord)
    claimed = repo.claim_run(UUID(RUN_ID))
    assert claimed.status == "running"
    assert repo.claim_run(UUID(RUN_ID)) is None

    create_request, create_url, _, _ = request_parts(opener, 0)
    assert create_request.method == "POST"
    assert create_url.path == "/rest/v1/saas_catalog_sync_runs"
    assert json.loads(create_request.data) == {
        "source_id": SOURCE_ID,
        "trigger_type": "manual",
        "requested_by": 7,
        "metrics": {},
    }
    claim_request, _, claim_query, _ = request_parts(opener, 1)
    assert claim_request.method == "PATCH"
    assert claim_query["id"] == [f"eq.{RUN_ID}"]
    assert claim_query["status"] == ["eq.requested"]


def test_create_run_returns_none_only_for_http_conflict():
    conflict = HTTPError("redacted", 409, "conflict", {}, io.BytesIO(b"conflict"))
    repo, _ = repository([conflict])
    assert repo.create_run(UUID(SOURCE_ID), "manual", 7) is None

    failed = HTTPError("redacted", 500, "failed", {}, io.BytesIO(b"failed"))
    repo, _ = repository([failed])
    with pytest.raises(CatalogRepositoryError, match="request"):
        repo.create_run(UUID(SOURCE_ID), "manual", 7)


def test_start_run_retries_once_with_same_request_key_and_accepts_null_claim():
    repo, opener = repository([
        TimeoutError("commit response lost"),
        response(200, RUN_ID),
    ])
    assert repo.start_run(UUID(SOURCE_ID), "manual", 7) == UUID(RUN_ID)
    assert len(opener.requests) == 2
    payloads = [json.loads(request.data) for request, _ in opener.requests]
    assert payloads[0] == payloads[1]
    assert payloads[0]["p_source_id"] == SOURCE_ID
    assert payloads[0]["p_trigger_type"] == "manual"
    assert payloads[0]["p_requested_by"] == 7
    assert str(UUID(payloads[0]["p_request_key"])) == payloads[0]["p_request_key"]
    assert all(urlsplit(request.full_url).path == "/rest/v1/rpc/saas_start_catalog_sync"
               for request, _ in opener.requests)

    repo, opener = repository([response(200, None)])
    assert repo.start_run(UUID(SOURCE_ID), "manual", None) is None
    assert len(opener.requests) == 1

    repo, opener = repository([TimeoutError("first"), TimeoutError("second")])
    with pytest.raises(CatalogRepositoryError, match="request"):
        repo.start_run(UUID(SOURCE_ID), "manual", 7)
    assert len(opener.requests) == 2


def test_claim_next_sync_uses_atomic_rpc_and_validates_the_claim():
    claim = {
        "run_id": RUN_ID,
        "supplier": "sunon",
        "trigger_type": "manual",
        "requested_by": 7,
    }
    repo, opener = repository([response(200, [claim])])

    result = repo.claim_next_sync(("sunon", "alma"))

    assert result == SyncClaim(UUID(RUN_ID), "sunon", "manual", 7)
    request, parsed, _, _ = request_parts(opener)
    assert request.method == "POST"
    assert parsed.path == "/rest/v1/rpc/saas_claim_next_catalog_sync"
    assert json.loads(request.data) == {"p_enabled_suppliers": ["sunon", "alma"]}

    repo, _ = repository([response(200, [])])
    assert repo.claim_next_sync(("sunon",)) is None


def test_repository_accepts_lumbro_in_generic_sync_whitelist():
    claim = {
        "run_id": RUN_ID,
        "supplier": "lumbro",
        "trigger_type": "scheduled",
        "requested_by": None,
    }
    repo, opener = repository([response(200, [claim])])

    assert repo.claim_next_sync(("lumbro",)) == SyncClaim(
        UUID(RUN_ID), "lumbro", "scheduled", None
    )
    request, _, _, _ = request_parts(opener)
    assert json.loads(request.data) == {"p_enabled_suppliers": ["lumbro"]}


def test_recover_stale_syncs_uses_atomic_rpc_and_validates_count():
    repo, opener = repository([response(200, 2)])

    assert repo.recover_stale_syncs(("sunon", "alma")) == 2
    request, parsed, _, _ = request_parts(opener)
    assert request.method == "POST"
    assert parsed.path == "/rest/v1/rpc/saas_recover_stale_catalog_sync_runs"
    assert json.loads(request.data) == {"p_enabled_suppliers": ["sunon", "alma"]}

    repo, _ = repository([response(200, -1)])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.recover_stale_syncs(("sunon",))


@pytest.mark.parametrize(
    "suppliers",
    [(), ("sunon", "sunon"), ("unknown",), ["sunon"]],
)
def test_claim_next_sync_rejects_invalid_supplier_whitelists(suppliers):
    repo, opener = repository([])

    with pytest.raises(CatalogRepositoryError):
        repo.claim_next_sync(suppliers)

    assert opener.requests == []


def test_claim_next_sync_rejects_malformed_or_unrequested_claims():
    repo, _ = repository([response(200, [{
        "run_id": RUN_ID,
        "supplier": "cr-global",
        "trigger_type": "scheduled",
        "requested_by": None,
    }])])

    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.claim_next_sync(("sunon",))


def test_filtered_and_written_rows_must_match_request_context():
    other_id = "99999999-9999-9999-9999-999999999999"

    repo, _ = repository([response(200, [source_row(supplier="alma")])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.get_source("sunon")

    repo, _ = repository([response(201, [run_row(source_id=other_id)])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.create_run(UUID(SOURCE_ID), "manual", 7)

    repo, _ = repository([response(200, [run_row(id=other_id, status="running")])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.claim_run(UUID(RUN_ID))

    snapshot = {
        "id": other_id,
        "supplier": "sunon",
        "source_hash": "a" * 64,
        "generated_at": "2026-07-16T12:00:00Z",
        "status": "published",
        "payload": {"supplier": "sunon", "source_hash": "a" * 64,
                    "generated_at": "2026-07-16T12:00:00Z", "items": []},
    }
    repo, _ = repository([response(200, [snapshot])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.get_published_snapshot(SourceRecord.from_row(source_row()))

    repo, _ = repository([response(200, [file_row(e_tag='"other"')])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.find_file(UUID(SOURCE_ID), "item-1", '"etag-1"')

    repo, _ = repository([response(200, other_id)])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.auto_publish_candidate(UUID(SNAPSHOT_ID))


def test_published_snapshot_lookup_validates_pointer_and_row():
    snapshot = {
        "id": SNAPSHOT_ID,
        "supplier": "sunon",
        "source_hash": "a" * 64,
        "generated_at": "2026-07-16T12:00:00Z",
        "status": "published",
        "payload": {"supplier": "sunon", "source_hash": "a" * 64,
                    "generated_at": "2026-07-16T12:00:00Z", "items": []},
    }
    repo, opener = repository([response(200, [snapshot])])
    found = repo.get_published_snapshot(SourceRecord.from_row(source_row()))
    assert isinstance(found, SnapshotRecord)
    assert found.id == UUID(SNAPSHOT_ID)
    _, _, query, _ = request_parts(opener)
    assert query["id"] == [f"eq.{SNAPSHOT_ID}"]
    assert query["status"] == ["eq.published"]
    assert repo.get_published_snapshot(SourceRecord.from_row(source_row(published_version_id=None))) is None


def test_published_snapshot_response_can_reach_sql_payload_bound():
    snapshot = {
        "id": SNAPSHOT_ID,
        "supplier": "sunon",
        "source_hash": "a" * 64,
        "generated_at": "2026-07-16T12:00:00Z",
        "status": "published",
        "payload": {"supplier": "sunon", "source_hash": "a" * 64,
                    "generated_at": "2026-07-16T12:00:00Z", "items": [],
                    "padding": "x" * (4 * 1024 * 1024)},
    }
    repo, _ = repository([response(200, [snapshot])])
    assert repo.get_published_snapshot(SourceRecord.from_row(source_row())).id == UUID(SNAPSHOT_ID)


def test_snapshot_payload_and_envelopes_have_distinct_bounded_limits(monkeypatch):
    mib = 1024 * 1024
    assert repository_module._MAX_SNAPSHOT_PAYLOAD_BYTES == 100 * mib
    assert repository_module._SNAPSHOT_ENVELOPE_MARGIN_BYTES == 4 * mib
    assert repository_module._MAX_SNAPSHOT_REQUEST_BYTES == 104 * mib
    assert repository_module._MAX_SNAPSHOT_RESPONSE_BYTES == 104 * mib

    generated = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    generated_text = "2026-07-16T12:00:00Z"
    payload = {
        "supplier": "sunon",
        "source_hash": "a" * 64,
        "generated_at": generated_text,
        "items": [],
        "padding": "x" * 2048,
    }
    rpc_payload = {
        "p_run_id": RUN_ID,
        "p_source_hash": "a" * 64,
        "p_generated_at": generated_text,
        "p_payload": payload,
        "p_metrics": {"files": 1},
        "p_delta_link": "opaque-delta-token",
    }
    payload_size = len(json.dumps(payload, separators=(",", ":")).encode())
    request_size = len(json.dumps(rpc_payload, separators=(",", ":")).encode())
    assert request_size > payload_size

    monkeypatch.setattr(repository_module, "_MAX_SNAPSHOT_PAYLOAD_BYTES", payload_size)
    monkeypatch.setattr(repository_module, "_MAX_SNAPSHOT_REQUEST_BYTES", request_size)
    repo, opener = repository([response(200, SNAPSHOT_ID)])
    snapshot_input = dict(payload, generated_at=generated)
    assert repo.stage_candidate(
        UUID(RUN_ID), snapshot_input, {"files": 1}, "opaque-delta-token"
    ) == UUID(SNAPSHOT_ID)
    assert len(opener.requests[0][0].data) == request_size

    monkeypatch.setattr(repository_module, "_MAX_SNAPSHOT_REQUEST_BYTES", request_size - 1)
    repo, opener = repository([])
    with pytest.raises(CatalogRepositoryError, match="payload"):
        repo.stage_candidate(UUID(RUN_ID), snapshot_input, {"files": 1}, "opaque-delta-token")
    assert opener.requests == []

    row = {
        "id": SNAPSHOT_ID,
        "supplier": "sunon",
        "source_hash": "a" * 64,
        "generated_at": generated_text,
        "status": "published",
        "payload": payload,
    }
    response_size = len(json.dumps([row]).encode())
    assert response_size > payload_size
    monkeypatch.setattr(repository_module, "_MAX_SNAPSHOT_RESPONSE_BYTES", response_size)
    repo, _ = repository([response(200, [row])])
    assert repo.get_published_snapshot(SourceRecord.from_row(source_row())).id == UUID(SNAPSHOT_ID)

    monkeypatch.setattr(repository_module, "_MAX_SNAPSHOT_RESPONSE_BYTES", response_size - 1)
    repo, _ = repository([response(200, [row])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.get_published_snapshot(SourceRecord.from_row(source_row()))


def test_find_and_list_latest_files_are_deterministic_and_allowlisted():
    older = file_row(id="55555555-5555-5555-5555-555555555555", e_tag='"old"')
    deleted_latest = file_row(
        id="66666666-6666-6666-6666-666666666666",
        is_deleted=True,
        deleted_at="2026-07-16T13:00:00Z",
        deleted_sync_run_id=RUN_ID,
        discovered_at="2026-07-16T13:00:00Z",
    )
    active = file_row(
        id="77777777-7777-7777-7777-777777777777",
        drive_item_id="item-2",
        path="SUNON MTY/catalog.pdf",
        e_tag='"pdf"',
        mime_type="application/pdf",
        private_object_path="catalog-sources/" + "b" * 64 + ".pdf",
        sha256="b" * 64,
    )
    outside = file_row(drive_item_id="item-3", path="OTHER/file.xlsx")
    repo, opener = repository(
        [
            response(200, [file_row()]),
            response(200, [deleted_latest, older, active, outside], {"Content-Range": "0-3/4"}),
        ]
    )
    assert isinstance(repo.find_file(UUID(SOURCE_ID), "item-1", '"etag-1"'), SourceFileRecord)
    _, _, find_query, _ = request_parts(opener, 0)
    assert find_query["is_deleted"] == ["eq.false"]
    rows = repo.list_latest_files(
        UUID(SOURCE_ID), ("SUNON MTY/prices.xlsx", "SUNON MTY/catalog.pdf")
    )
    assert [row.drive_item_id for row in rows] == ["item-2"]
    _, _, query, _ = request_parts(opener, 1)
    assert query["source_id"] == [f"eq.{SOURCE_ID}"]
    assert query["order"] == ["drive_item_id.asc,discovered_at.desc,id.desc"]
    with pytest.raises(CatalogRepositoryError, match="allowlist"):
        repo.list_latest_files(UUID(SOURCE_ID), ("../escape.xlsx",))


def test_find_file_with_deleted_history_returns_only_active_same_etag():
    deleted = file_row(
        id="66666666-6666-6666-6666-666666666666",
        is_deleted=True,
        deleted_at="2026-07-16T13:00:00Z",
        deleted_sync_run_id=RUN_ID,
    )
    active = file_row(id="77777777-7777-7777-7777-777777777777")
    assert deleted["drive_item_id"] == active["drive_item_id"]
    assert deleted["e_tag"] == active["e_tag"]

    repo, opener = repository([response(200, [active])])
    found = repo.find_file(UUID(SOURCE_ID), "item-1", '"etag-1"')
    assert found.id == UUID(active["id"]) and not found.is_deleted
    _, _, query, _ = request_parts(opener)
    assert query["is_deleted"] == ["eq.false"]

    repo, _ = repository([response(200, [deleted])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.find_file(UUID(SOURCE_ID), "item-1", '"etag-1"')


def test_list_latest_files_paginates_with_exact_ranges_and_count():
    rows = [
        file_row(id=str(UUID(int=index + 100)), drive_item_id=f"item-{index:04d}")
        for index in range(1001)
    ]
    repo, opener = repository([
        response(200, rows[:1000], {"Content-Range": "0-999/1001"}),
        response(200, rows[1000:], {"Content-Range": "1000-1000/1001"}),
    ])
    found = repo.list_latest_files(UUID(SOURCE_ID), ("SUNON MTY/prices.xlsx",))
    assert len(found) == 1001
    first, _, query, _ = request_parts(opener, 0)
    second = request_parts(opener, 1)[0]
    assert query["order"] == ["drive_item_id.asc,discovered_at.desc,id.desc"]
    assert "limit" not in query
    assert first.headers["Range"] == "0-999"
    assert second.headers["Range"] == "1000-1999"
    assert first.headers["Range-unit"] == "items"
    assert first.headers["Prefer"] == "count=exact"


@pytest.mark.parametrize(
    "responses",
    [
        [response(200, [file_row()], {"Content-Range": "0-1/2"})],
        [response(200, [file_row()], {"Content-Range": "0-0/10001"})],
        [
            response(200, [file_row()], {"Content-Range": "0-0/2"}),
            response(200, [file_row()], {"Content-Range": "1-1/2"}),
        ],
        [response(200, [file_row()], {"Content-Range": "bad"})],
    ],
)
def test_list_latest_files_rejects_truncation_limits_cycles_and_bad_ranges(responses):
    repo, _ = repository(responses)
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.list_latest_files(UUID(SOURCE_ID), ("SUNON MTY/prices.xlsx",))


def test_mark_file_deleted_uses_only_atomic_task_2c_rpc():
    repo, opener = repository([response(200, FILE_ID)])
    repo.mark_file_deleted(UUID(SOURCE_ID), "item-1", UUID(RUN_ID))
    assert len(opener.requests) == 1
    request, parsed, query, _ = request_parts(opener)
    assert request.method == "POST" and query == {}
    assert parsed.path == "/rest/v1/rpc/saas_mark_catalog_source_file_deleted"
    assert json.loads(request.data) == {
        "p_source_id": SOURCE_ID,
        "p_drive_item_id": "item-1",
        "p_run_id": RUN_ID,
    }


@pytest.mark.parametrize(
    "extension,mime_type",
    [
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("xlsb", "application/vnd.ms-excel.sheet.binary.macroEnabled.12"),
        ("pdf", "application/pdf"),
    ],
)
def test_store_raw_is_content_addressed_and_never_upserts(tmp_path, extension, mime_type):
    content = b"catalog bytes"
    local = tmp_path / f"source.{extension}"
    local.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    repo, opener = repository([response(200, {"Key": "ignored"})])
    result = repo.store_raw_if_absent(local, digest, f".{extension}", mime_type)
    assert result == f"catalog-sources/{digest}.{extension}"
    request, parsed, _, _ = request_parts(opener)
    assert request.method == "POST"
    assert parsed.path == f"/storage/v1/object/catalog-sources/{digest}.{extension}"
    assert request.headers["X-upsert"] == "false"
    assert request.data == content


def test_source_file_record_accepts_the_validated_lauco_xlsb_object():
    digest = hashlib.sha256(b"abc").hexdigest()
    parsed = SourceFileRecord.from_row(
        file_row(
            path="SPEC GUIDES 2026/LAUCO/Spec Guide Lauco-2026.xlsb",
            mime_type="application/vnd.ms-excel.sheet.binary.macroEnabled.12",
            private_object_path=f"catalog-sources/{digest}.xlsb",
        )
    )

    assert parsed.private_object_path == f"catalog-sources/{digest}.xlsb"


def test_store_raw_conflict_requires_confirmed_existence(tmp_path):
    local = tmp_path / "source.pdf"
    local.write_bytes(b"pdf")
    digest = hashlib.sha256(b"pdf").hexdigest()
    conflict = HTTPError("redacted", 409, "conflict", {}, io.BytesIO(b"already exists"))
    repo, opener = repository([conflict, Response(200, b"")])
    assert repo.store_raw_if_absent(local, digest, "pdf", "application/pdf").endswith(".pdf")
    confirmation, _, _, _ = request_parts(opener, 1)
    assert confirmation.method in {"HEAD", "GET"}

    repo, _ = repository([conflict, Response(404, b"")])
    with pytest.raises(CatalogRepositoryError, match="storage"):
        repo.store_raw_if_absent(local, digest, "pdf", "application/pdf")


def test_store_raw_rejects_hash_size_mime_and_non_regular_file(tmp_path):
    local = tmp_path / "source.pdf"
    local.write_bytes(b"pdf")
    digest = hashlib.sha256(b"pdf").hexdigest()
    repo, opener = repository([])
    for sha, extension, mime in [
        (digest.upper(), "pdf", "application/pdf"),
        ("0" * 64, "pdf", "application/pdf"),
        (digest, "xlsx", "application/pdf"),
    ]:
        with pytest.raises(CatalogRepositoryError):
            repo.store_raw_if_absent(local, sha, extension, mime)
    with pytest.raises(CatalogRepositoryError):
        repo.store_raw_if_absent(tmp_path, digest, "pdf", "application/pdf")
    assert opener.requests == []


def test_store_catalog_asset_is_content_addressed_and_never_upserts():
    content = b"\x89PNG\r\n\x1a\nofficial image"
    digest = hashlib.sha256(content).hexdigest()
    object_name = f"{digest}.png"
    repo, opener = repository([response(200, {"Key": "ignored"})])

    assert repo.store_catalog_asset_if_absent(
        object_name, content, "image/png"
    ) == object_name

    request, parsed, query, _ = request_parts(opener)
    assert request.method == "POST" and query == {}
    assert parsed.path == f"/storage/v1/object/catalog-assets/{object_name}"
    assert request.headers["Content-type"] == "image/png"
    assert request.headers["X-upsert"] == "false"
    assert request.data == content


@pytest.mark.parametrize(
    "object_name,content,content_type",
    [
        ("A" * 64 + ".png", b"image", "image/png"),
        ("0" * 64 + ".PNG", b"image", "image/png"),
        ("../" + "0" * 64 + ".png", b"image", "image/png"),
        ("0" * 64 + ".png", b"image", "image/jpeg"),
        ("0" * 64 + ".png", bytearray(b"image"), "image/png"),
        (
            hashlib.sha256(b"image").hexdigest() + ".png",
            b"different image",
            "image/png",
        ),
    ],
)
def test_store_catalog_asset_rejects_invalid_name_mime_content_and_hash(
    object_name, content, content_type
):
    repo, opener = repository([])
    with pytest.raises(CatalogRepositoryError, match="asset"):
        repo.store_catalog_asset_if_absent(object_name, content, content_type)
    assert opener.requests == []


def test_store_catalog_asset_rejects_more_than_eight_mib_without_network():
    content = b"x" * (8 * 1024 * 1024 + 1)
    object_name = hashlib.sha256(content).hexdigest() + ".png"
    repo, opener = repository([])

    with pytest.raises(CatalogRepositoryError, match="asset"):
        repo.store_catalog_asset_if_absent(object_name, content, "image/png")

    assert opener.requests == []


def test_store_catalog_asset_conflict_accepts_only_same_authenticated_content():
    content = b"\x89PNG\r\n\x1a\nofficial image"
    object_name = hashlib.sha256(content).hexdigest() + ".png"
    conflict = HTTPError("redacted", 409, "conflict", {}, io.BytesIO(b"exists"))
    repo, opener = repository([
        conflict,
        Response(200, content, {"Content-Type": "image/png"}),
    ])

    assert repo.store_catalog_asset_if_absent(
        object_name, content, "image/png"
    ) == object_name

    confirmation, parsed, query, _ = request_parts(opener, 1)
    assert confirmation.method == "GET" and query == {}
    assert parsed.path == (
        f"/storage/v1/object/authenticated/catalog-assets/{object_name}"
    )
    assert confirmation.headers["Accept"] == "application/octet-stream"


def test_store_catalog_asset_conflict_rejects_collision_or_missing_object():
    content = b"\x89PNG\r\n\x1a\nofficial image"
    object_name = hashlib.sha256(content).hexdigest() + ".png"

    for existing in (
        Response(200, b"different", {"Content-Type": "image/png"}),
        HTTPError("redacted", 404, "missing", {}, io.BytesIO(b"missing")),
    ):
        conflict = HTTPError("redacted", 409, "conflict", {}, io.BytesIO(b"exists"))
        repo, opener = repository([conflict, existing])
        with pytest.raises(CatalogRepositoryError, match="storage"):
            repo.store_catalog_asset_if_absent(object_name, content, "image/png")
        assert len(opener.requests) == 2


def test_store_raw_reads_one_verified_descriptor_and_detects_identity_change(tmp_path, monkeypatch):
    local = tmp_path / "source.pdf"
    local.write_bytes(b"pdf")
    digest = hashlib.sha256(b"pdf").hexdigest()
    real_open = repository_module.os.open
    real_fstat = repository_module.os.fstat
    opened_flags = []

    def tracked_open(path, flags):
        opened_flags.append(flags)
        return real_open(path, flags)

    monkeypatch.setattr(repository_module.os, "open", tracked_open)
    repo, _ = repository([response(200, {})])
    assert repo.store_raw_if_absent(local, digest, "pdf", "application/pdf").endswith(".pdf")
    assert len(opened_flags) == 1
    if hasattr(os, "O_NOFOLLOW"):
        assert opened_flags[0] & os.O_NOFOLLOW

    calls = 0

    def changed_fstat(descriptor):
        nonlocal calls
        calls += 1
        details = real_fstat(descriptor)
        if calls == 2:
            return SimpleNamespace(
                st_mode=details.st_mode,
                st_dev=details.st_dev,
                st_ino=details.st_ino,
                st_size=details.st_size + 1,
                st_mtime_ns=details.st_mtime_ns,
            )
        return details

    monkeypatch.setattr(repository_module.os, "fstat", changed_fstat)
    repo, opener = repository([response(200, {})])
    with pytest.raises(CatalogRepositoryError, match="raw file"):
        repo.store_raw_if_absent(local, digest, "pdf", "application/pdf")
    assert opener.requests == []


def test_materialize_raw_gets_private_object_to_exclusive_verified_descriptor(tmp_path, monkeypatch):
    row = SourceFileRecord.from_row(file_row())
    destination = tmp_path / "materialized.xlsx"
    opened_flags = []
    real_open = repository_module.os.open

    def tracked_open(path, flags, mode=0o777):
        opened_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(repository_module.os, "open", tracked_open)
    repo, opener = repository([
        Response(200, b"abc", {"Content-Type": "application/octet-stream"})
    ])
    downloaded = repo.materialize_raw_if_present(row, destination)
    assert downloaded == DownloadedFile(destination, 3, hashlib.sha256(b"abc").hexdigest())
    assert destination.read_bytes() == b"abc"
    request, parsed, query, _ = request_parts(opener)
    assert request.method == "GET" and query == {}
    assert parsed.path == f"/storage/v1/object/authenticated/{row.private_object_path}"
    assert request.headers["Accept"] == "application/octet-stream"
    assert opened_flags[0] & os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        assert opened_flags[0] & os.O_NOFOLLOW


def test_materialize_raw_is_bounded_missing_safe_and_never_overwrites(tmp_path):
    row = SourceFileRecord.from_row(file_row())
    missing = HTTPError("redacted", 404, "missing", {}, io.BytesIO(b"missing"))
    destination = tmp_path / "missing.xlsx"
    repo, _ = repository([missing])
    assert repo.materialize_raw_if_present(row, destination) is None
    assert not destination.exists()

    destination.write_bytes(b"owner data")
    repo, opener = repository([])
    with pytest.raises(CatalogRepositoryError, match="destination"):
        repo.materialize_raw_if_present(row, destination)
    assert destination.read_bytes() == b"owner data" and opener.requests == []

    destination = tmp_path / "bad.xlsx"
    repo, _ = repository([
        Response(200, b"abd", {"Content-Type": "application/octet-stream"})
    ])
    with pytest.raises(CatalogRepositoryError, match="raw file"):
        repo.materialize_raw_if_present(row, destination)
    assert not destination.exists()

    oversized = SourceFileRecord.from_row(file_row(size_bytes=repository_module._MAX_RAW_BYTES + 1))
    repo, opener = repository([])
    with pytest.raises(CatalogRepositoryError, match="raw file"):
        repo.materialize_raw_if_present(oversized, tmp_path / "large.xlsx")
    assert opener.requests == []


def test_materialize_raw_rehashes_the_written_descriptor(tmp_path, monkeypatch):
    row = SourceFileRecord.from_row(file_row())
    real_write = repository_module.os.write

    def corrupt_write(descriptor, content):
        return real_write(descriptor, b"abd" if content == b"abc" else content)

    monkeypatch.setattr(repository_module.os, "write", corrupt_write)
    repo, _ = repository([
        Response(200, b"abc", {"Content-Type": "application/octet-stream"})
    ])
    with pytest.raises(CatalogRepositoryError, match="raw file"):
        repo.materialize_raw_if_present(row, tmp_path / "corrupt.xlsx")


def test_record_source_file_uses_graph_download_and_validation_contract():
    item = GraphItem("item-1", "prices.xlsx", "SUNON MTY/prices.xlsx", 3, '"etag-1"', None,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False, None)
    downloaded = DownloadedFile(Path("prices.xlsx"), 3, hashlib.sha256(b"abc").hexdigest())
    repo, opener = repository([response(201, [file_row()])])
    record = repo.record_source_file(
        UUID(SOURCE_ID), item, downloaded, file_row()["private_object_path"], UUID(RUN_ID),
        {"status": "valid", "summary": {}},
    )
    assert isinstance(record, SourceFileRecord)
    request, _, _, _ = request_parts(opener)
    payload = json.loads(request.data)
    assert payload["source_id"] == SOURCE_ID
    assert payload["last_sync_run_id"] == RUN_ID
    assert payload["validation_status"] == "valid"
    assert not {"is_deleted", "deleted_at", "deleted_sync_run_id"} & payload.keys()


def test_write_responses_must_match_file_run_and_source_context():
    item = GraphItem("item-1", "prices.xlsx", "SUNON MTY/prices.xlsx", 3, '"etag-1"', None,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False, None)
    downloaded = DownloadedFile(Path("prices.xlsx"), 3, hashlib.sha256(b"abc").hexdigest())
    repo, _ = repository([response(201, [file_row(drive_item_id="other-item")])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.record_source_file(
            UUID(SOURCE_ID), item, downloaded, file_row()["private_object_path"], UUID(RUN_ID),
            {"status": "valid", "summary": {}},
        )

    repo, _ = repository([response(200, {"not": "a uuid"})])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.mark_file_deleted(UUID(SOURCE_ID), "item-1", UUID(RUN_ID))

    repo, opener = repository([response(200, "99999999-9999-9999-9999-999999999999")])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.finish_no_changes(UUID(RUN_ID), {"files": 0}, "opaque-delta-token")
    assert len(opener.requests) == 1

    repo, _ = repository([response(200, [run_row(id="99999999-9999-9999-9999-999999999999",
                                                  status="failed", error_summary="parse_failed")])])
    with pytest.raises(CatalogRepositoryError, match="response"):
        repo.finish_failed(UUID(RUN_ID), "parse_failed", {"files": 1})


def test_atomic_candidate_and_publication_rpcs_use_exact_payloads():
    repo, opener = repository([
        response(200, SNAPSHOT_ID), response(200, SNAPSHOT_ID), response(200, SNAPSHOT_ID)
    ])
    generated = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    snapshot = {"supplier": "sunon", "source_hash": "a" * 64,
                "generated_at": generated, "items": []}
    assert repo.stage_candidate(
        UUID(RUN_ID), snapshot, {"files": 2}, "opaque-delta-token"
    ) == UUID(SNAPSHOT_ID)
    assert repo.auto_publish_candidate(UUID(SNAPSHOT_ID)) == UUID(SNAPSHOT_ID)
    assert repo.publish_candidate(UUID(SNAPSHOT_ID), 7, "approved") == UUID(SNAPSHOT_ID)

    stage, stage_url, _, _ = request_parts(opener, 0)
    assert stage_url.path == "/rest/v1/rpc/saas_stage_catalog_candidate"
    assert json.loads(stage.data) == {
        "p_run_id": RUN_ID,
        "p_source_hash": "a" * 64,
        "p_generated_at": "2026-07-16T12:00:00Z",
        "p_payload": {"supplier": "sunon", "source_hash": "a" * 64,
                      "generated_at": "2026-07-16T12:00:00Z", "items": []},
        "p_metrics": {"files": 2},
        "p_delta_link": "opaque-delta-token",
    }
    assert request_parts(opener, 1)[1].path.endswith("/saas_auto_publish_catalog_snapshot")
    manual = json.loads(request_parts(opener, 2)[0].data)
    assert manual == {"p_candidate_id": SNAPSHOT_ID, "p_reviewed_by": 7, "p_review_note": "approved"}


def test_stage_candidate_allows_an_absent_cursor_but_no_changes_does_not():
    generated = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    snapshot = {
        "supplier": "sunon", "source_hash": "a" * 64,
        "generated_at": generated, "items": [],
    }
    repo, opener = repository([response(200, SNAPSHOT_ID)])

    assert repo.stage_candidate(UUID(RUN_ID), snapshot, {"files": 2}, None) == UUID(SNAPSHOT_ID)
    assert json.loads(request_parts(opener, 0)[0].data)["p_delta_link"] is None

    with pytest.raises(CatalogRepositoryError, match="delta token"):
        repo.stage_candidate(UUID(RUN_ID), snapshot, {"files": 2}, "")
    with pytest.raises(CatalogRepositoryError, match="delta token"):
        repo.finish_no_changes(UUID(RUN_ID), {"files": 0}, None)


def test_no_changes_and_failed_are_conditional_and_never_write_published_pointer():
    repo, opener = repository([
        response(200, RUN_ID),
        response(200, [run_row(status="failed", error_summary="parse_failed", metrics={"files": 1})]),
    ])
    repo.finish_no_changes(UUID(RUN_ID), {"files": 0}, "opaque-delta-token")
    repo.finish_failed(UUID(RUN_ID), "parse_failed", {"files": 1})
    finish, finish_url, finish_query, _ = request_parts(opener, 0)
    assert finish.method == "POST" and finish_query == {}
    assert finish_url.path == "/rest/v1/rpc/saas_finish_catalog_sync_no_changes"
    assert json.loads(finish.data) == {
        "p_run_id": RUN_ID,
        "p_metrics": {"files": 0},
        "p_delta_link": "opaque-delta-token",
    }
    failed, _, failed_query, _ = request_parts(opener, 1)
    assert failed_query["status"] == ["eq.running"]
    assert "published_version_id" not in json.loads(failed.data)


def test_rates_use_decimal_strings_canonical_dates_and_bounded_rpc_batch():
    rate = ExchangeRate(
        "USD", date(2026, 7, 15), Decimal("18.125000"), "SF43718", "BANXICO_SIE",
        datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc), "a" * 64,
    )
    repo, opener = repository([response(200, 1)])
    assert repo.insert_rates_if_absent((rate,)) == 1
    request, parsed, _, _ = request_parts(opener)
    assert parsed.path == "/rest/v1/rpc/saas_insert_exchange_rates_if_absent"
    assert json.loads(request.data) == {"p_rates": [{
        "currency": "USD",
        "effective_date": "2026-07-15",
        "mxn_per_unit": "18.125000",
        "series_id": "SF43718",
        "source": "BANXICO_SIE",
        "retrieved_at": "2026-07-16T12:30:00Z",
        "raw_hash": "a" * 64,
    }]}
    with pytest.raises(CatalogRepositoryError, match="batch"):
        repo.insert_rates_if_absent((rate,) * 1001)


def test_transport_has_no_delete_and_default_opener_disables_proxies():
    assert not hasattr(CatalogRepository, "delete")
    assert not hasattr(CatalogRepository, "storage_upload")
    repo = CatalogRepository(BASE_URL, KEY)
    assert not any(hasattr(handler, "proxy_open") for handler in repo._opener.handlers)
