import copy
import importlib.util
from pathlib import Path

import pytest

import mobiliti_saas.api.index as api


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, *, Bucket, Key):
        try:
            object_data = self.objects[(Bucket, Key)]
        except KeyError:
            raise FakeS3Error(404, "NoSuchKey") from None
        return {**object_data, "Body": Body(object_data["Body"])}

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise FakeS3Error(412, "PreconditionFailed")
        self.objects[key] = {
            "Body": kwargs["Body"], "ContentType": kwargs["ContentType"],
            "ContentEncoding": kwargs["ContentEncoding"], "CacheControl": kwargs["CacheControl"],
            "Metadata": copy.deepcopy(kwargs["Metadata"]),
        }


class FakeS3Error(Exception):
    def __init__(self, status, code):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}


class Body:
    def __init__(self, value):
        self.value = value
        self.position = 0

    def read(self, amount=-1):
        if amount < 0:
            amount = len(self.value) - self.position
        result = self.value[self.position:self.position + amount]
        self.position += len(result)
        return result

    def close(self):
        pass


def _enable_private_cache(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(api, "CATALOG_SNAPSHOT_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(api, "R2_BUCKET", "private-quote-files")
    monkeypatch.setattr(api, "SUPABASE_URL", "https://example.supabase.co/")
    monkeypatch.setattr(api, "DATABASE_URL", None)
    monkeypatch.setattr(api, "_r2_configured", lambda: True)
    monkeypatch.setattr(api, "_r2_client", lambda: fake)
    monkeypatch.setattr(api, "_CATALOG_SNAPSHOT_CACHE", api.SnapshotCache(), raising=False)
    return fake


def test_modern_snapshot_checks_metadata_without_payload_before_private_cache_reuse(monkeypatch):
    _enable_private_cache(monkeypatch)
    metadata_calls = []
    payload_reads = []
    metadata = {"id": "version-1", "supplier": "alma", "source_hash": "a" * 64,
                "generated_at": "2026-09-02T00:00:00Z", "status": "published"}
    row = {**metadata, "payload": {"supplier": "alma", "source_hash": "a" * 64,
                                     "generated_at": "2026-09-02T00:00:00Z", "items": []}}

    def get_metadata(supplier):
        metadata_calls.append(supplier)
        return metadata

    monkeypatch.setattr(api, "db_get_published_catalog_metadata", get_metadata, raising=False)
    monkeypatch.setattr(api, "db_get_published_catalog_snapshot", lambda supplier, version: payload_reads.append((supplier, version)) or row)
    monkeypatch.setattr(api, "_catalog_asset_storage_fingerprint", lambda: ("supabase", ""))
    monkeypatch.setattr(api, "_hydrate_catalog_asset_urls", lambda payload: payload)
    monkeypatch.setattr(api, "load_supplier_catalog_data", lambda payload, expected_supplier: payload)

    first = api._load_supplier_catalog_cached("alma")
    second = api._load_supplier_catalog_cached("alma")

    assert first["source_hash"] == second["source_hash"] == "a" * 64
    assert payload_reads == [("alma", "version-1")]
    assert metadata_calls == ["alma", "alma"]


def test_modern_snapshot_never_returns_resident_copy_after_depublication(monkeypatch):
    _enable_private_cache(monkeypatch)
    metadata = {"id": "version-1", "supplier": "alma", "source_hash": "a" * 64,
                "generated_at": "2026-09-02T00:00:00Z", "status": "published"}
    row = {**metadata, "payload": {"supplier": "alma", "source_hash": "a" * 64,
                                     "generated_at": "2026-09-02T00:00:00Z", "items": []}}
    monkeypatch.setattr(api, "db_get_published_catalog_metadata", lambda supplier: metadata)
    monkeypatch.setattr(api, "db_get_published_catalog_snapshot", lambda supplier, version: row)
    monkeypatch.setattr(api, "_catalog_asset_storage_fingerprint", lambda: ("supabase", ""))
    monkeypatch.setattr(api, "_hydrate_catalog_asset_urls", lambda payload: payload)
    monkeypatch.setattr(api, "load_supplier_catalog_data", lambda payload, expected_supplier: payload)

    api._load_supplier_catalog_cached("alma")
    monkeypatch.setattr(api, "db_get_published_catalog_metadata", lambda supplier: None)

    with pytest.raises(RuntimeError, match="publicado no disponible"):
        api._load_supplier_catalog_cached("alma")


def test_legacy_snapshot_revision_includes_updated_at_and_metadata_query_has_no_payload(monkeypatch):
    _enable_private_cache(monkeypatch)
    requests = []
    rows = [{"supplier": "offiho", "source_hash": "a" * 64,
             "generated_at": "2026-09-02T00:00:00Z", "updated_at": "2026-09-02T00:01:00Z"}]

    def supabase(method, path, *, params=None, **kwargs):
        requests.append((method, path, params, kwargs))
        return rows

    monkeypatch.setattr(api, "DEV_MODE", False)
    monkeypatch.setattr(api, "DATABASE_URL", None)
    monkeypatch.setattr(api, "_supabase_req", supabase)
    assert api.db_get_supplier_catalog_snapshot_metadata("offiho") == rows[0]
    assert requests[0][2]["select"] == "supplier,source_hash,generated_at,updated_at"
    assert "payload" not in requests[0][2]["select"]


def test_legacy_private_cache_reloads_when_updated_at_changes(monkeypatch):
    _enable_private_cache(monkeypatch)
    reads = []
    metadata = {"supplier": "offiho", "source_hash": "a" * 64,
                "generated_at": "2026-09-02T00:00:00Z", "updated_at": "2026-09-02T00:01:00Z"}

    monkeypatch.setattr(api, "db_get_supplier_catalog_snapshot_metadata", lambda supplier: dict(metadata), raising=False)
    monkeypatch.setattr(
        api, "db_get_supplier_catalog_snapshot",
        lambda supplier: reads.append((supplier, metadata["updated_at"])) or {**metadata, "payload": {"items": []}},
    )

    first = api._load_legacy_snapshot_cached("offiho")
    second = api._load_legacy_snapshot_cached("offiho")
    metadata["updated_at"] = "2026-09-02T00:02:00Z"
    third = api._load_legacy_snapshot_cached("offiho")

    assert first == second
    assert third["updated_at"] == "2026-09-02T00:02:00Z"
    assert reads == [("offiho", "2026-09-02T00:01:00Z"), ("offiho", "2026-09-02T00:02:00Z")]


def test_supplier_snapshot_upsert_does_not_download_or_return_redundant_payload(monkeypatch):
    payload = {"supplier": "tarkett", "source_hash": "a" * 64,
               "generated_at": "2026-09-02T00:00:00Z", "items": []}
    calls = []
    monkeypatch.setattr(api, "DEV_MODE", False)
    monkeypatch.setattr(api, "DATABASE_URL", None)
    monkeypatch.setattr(api, "db_get_supplier_catalog_snapshot_metadata", lambda supplier: {"supplier": supplier})
    monkeypatch.setattr(api, "load_tarkett_catalog_data", lambda value: value)

    def request(method, path, *, params=None, json_data=None, **kwargs):
        calls.append((method, path, params, json_data))
        return [{"supplier": "tarkett", "source_hash": "a" * 64,
                 "generated_at": "2026-09-02T00:00:00Z", "updated_at": "now"}]

    monkeypatch.setattr(api, "_supabase_req", request)
    result = api.db_upsert_supplier_catalog_snapshot("tarkett", payload)

    assert result["payload"] == payload
    assert calls[0][0] == "PATCH"
    assert calls[0][2]["select"] == "supplier,source_hash,generated_at,updated_at"


def test_web_packaged_runtime_contains_the_identical_snapshot_cache_module():
    source = Path("mobiliti_saas/quote_engine/snapshot_cache.py")
    packaged = Path("mobiliti_saas/web/mobiliti_saas/quote_engine/snapshot_cache.py")
    spec = importlib.util.spec_from_file_location("packaged_snapshot_cache", packaged)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert packaged.read_bytes() == source.read_bytes()
    assert module.SnapshotCache.MAX_ENTRIES == 32


def test_private_snapshot_cache_rejects_the_public_catalog_asset_bucket(monkeypatch):
    monkeypatch.setattr(api, "CATALOG_SNAPSHOT_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(api, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(api, "R2_BUCKET", api.CATALOG_ASSET_BUCKET)
    monkeypatch.setattr(api, "_r2_configured", lambda: True)

    assert api._private_snapshot_cache_available() is False
