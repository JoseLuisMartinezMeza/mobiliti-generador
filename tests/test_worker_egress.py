import io
import json
import os
import sys
from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "worker"))

import quote_worker
from mobiliti_saas.quote_engine.snapshot_cache import SnapshotCache


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class _MemoryS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, *, Bucket, Key):
        try:
            return deepcopy(self.objects[(Bucket, Key)])
        except KeyError as exc:
            error = RuntimeError("NoSuchKey")
            error.response = {
                "ResponseMetadata": {"HTTPStatusCode": 404},
                "Error": {"Code": "NoSuchKey"},
            }
            raise error from exc

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "Body": io.BytesIO(kwargs["Body"]),
            "ContentType": kwargs["ContentType"],
            "ContentEncoding": kwargs["ContentEncoding"],
            "CacheControl": kwargs["CacheControl"],
            "Metadata": dict(kwargs["Metadata"]),
        }


def _snapshot(source_hash, stock):
    return {
        "supplier": "offiho",
        "source_hash": source_hash,
        "generated_at": "2026-09-02T00:00:00+00:00",
        "updated_at": f"2026-09-02T00:00:0{stock}+00:00",
        "payload": {"source_hash": source_hash, "generated_at": "2026-09-02T00:00:00+00:00", "items": [{"available_quantity": stock}]},
    }


def test_service_snapshot_cache_shares_one_payload_download_and_refreshes_changed_stock(monkeypatch):
    """Mutación detectada: quitar validación de revisión o caché compartida entre clientes."""
    state = {"row": _snapshot("hash-1", 1), "payload_reads": 0, "metadata_reads": 0}
    memory_s3 = _MemoryS3()

    def fake_urlopen(request, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        select = query.get("select", [""])[0]
        if "payload" in select:
            state["payload_reads"] += 1
            return _Response([deepcopy(state["row"])])
        state["metadata_reads"] += 1
        row = state["row"]
        return _Response([{key: row[key] for key in ("supplier", "source_hash", "generated_at", "updated_at")}])

    monkeypatch.setenv("SUPABASE_URL", "https://catalog.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(quote_worker, "CATALOG_SNAPSHOT_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(quote_worker, "R2_BUCKET", "private-quotes")
    monkeypatch.setattr(quote_worker, "_r2_configured", lambda: True)
    monkeypatch.setattr(quote_worker, "_r2_client", lambda: memory_s3)
    monkeypatch.setattr(quote_worker.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(quote_worker, "_CATALOG_SNAPSHOT_CACHE", SnapshotCache(), raising=False)

    first = quote_worker.SupabaseClient()
    second = quote_worker.SupabaseClient()
    for index in range(24):
        current = (first if index % 2 else second).catalog_snapshot_get("offiho")
        assert current["payload"]["items"][0]["available_quantity"] == 1

    assert state["payload_reads"] == 1
    assert state["metadata_reads"] == 48

    state["row"] = _snapshot("hash-2", 2)
    changed = second.catalog_snapshot_get("offiho")
    assert changed["payload"]["items"][0]["available_quantity"] == 2
    assert state["payload_reads"] == 2


def test_service_snapshot_metadata_authorization_failure_never_returns_stale_memory(monkeypatch):
    """Mutación detectada: devolver la entrada residente cuando falla autorización."""
    monkeypatch.setenv("SUPABASE_URL", "https://catalog.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(quote_worker, "CATALOG_SNAPSHOT_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(quote_worker, "R2_BUCKET", "private-quotes")
    monkeypatch.setattr(quote_worker, "_r2_configured", lambda: True)
    monkeypatch.setattr(quote_worker, "_r2_client", lambda: _MemoryS3())
    monkeypatch.setattr(quote_worker, "_CATALOG_SNAPSHOT_CACHE", SnapshotCache(), raising=False)

    def denied(_request, timeout):
        raise quote_worker.urllib.error.HTTPError("https://catalog.example", 401, "denied", {}, io.BytesIO(b"denied"))

    monkeypatch.setattr(quote_worker.urllib.request, "urlopen", denied)
    with pytest.raises(RuntimeError, match="401"):
        quote_worker.SupabaseClient().catalog_snapshot_get("offiho")


def test_catalog_snapshot_upsert_returns_only_metadata(monkeypatch):
    """Mutación detectada: pedir/devolver la representación pesada del payload tras escribir."""
    payload = {"source_hash": "hash-1", "generated_at": "2026-09-02T00:00:00+00:00", "items": [{"padding": "x" * 1000}]}
    seen = {}

    def fake_urlopen(request, timeout):
        seen["query"] = parse_qs(urlparse(request.full_url).query)
        seen["prefer"] = request.get_header("Prefer")
        return _Response([{"supplier": "offiho", "source_hash": "hash-1", "generated_at": payload["generated_at"], "updated_at": "2026-09-02T00:00:01+00:00"}])

    monkeypatch.setenv("SUPABASE_URL", "https://catalog.example")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(quote_worker.urllib.request, "urlopen", fake_urlopen)

    result = quote_worker.SupabaseClient().catalog_snapshot_upsert("offiho", payload)

    assert seen["query"]["select"] == ["supplier,source_hash,generated_at,updated_at"]
    assert "return=representation" in seen["prefer"]
    assert result == {"supplier": "offiho", "source_hash": "hash-1", "generated_at": payload["generated_at"], "updated_at": "2026-09-02T00:00:01+00:00"}


def test_worker_job_queries_use_minimum_projections_and_claim_still_has_authoritative_fields():
    """Mutación detectada: ampliar las lecturas idle o dejar de devolver la fila completa al claim."""
    calls = []

    class Client:
        def rest(self, method, path, params=None, data=None):
            calls.append((method, path, params, data))
            if method == "GET" and params["status"] == "eq.queued":
                return [{"id": "job-1"}]
            if method == "GET":
                return [{"id": "stale-1", "status": "processing", "attempt_token": "attempt-1", "lease_expires_at": "2000-01-01T00:00:00+00:00", "updated_at": "2000-01-01T00:00:00+00:00"}]
            if path.endswith("status=eq.queued"):
                return [{"id": "job-1", "usuario_id": 7, "input_path": "users/7/jobs/job-1/input.xlsx", "metadata": {"complete": True}, "status": "processing", "attempt_token": data["attempt_token"], "output_path": None}]
            return [{"id": "stale-1", **data}]

    client = Client()
    queued = quote_worker.fetch_next_job(client)
    claimed = quote_worker.claim_job(client, queued)
    assert claimed["metadata"] == {"complete": True}
    assert claimed["usuario_id"] == 7
    assert quote_worker.recover_stale_jobs(client) == 1
    assert calls[0][2]["select"] == "id"
    assert calls[2][2]["select"] == "id,status,attempt_token,lease_expires_at,updated_at"


def test_postgres_job_reads_honor_safe_queued_and_processing_projections(monkeypatch):
    """Mutación detectada: ignorar select y volver a ejecutar SELECT * en DATABASE_URL."""
    client = quote_worker.PostgresClient.__new__(quote_worker.PostgresClient)
    statements = []
    monkeypatch.setattr(
        client,
        "_rows",
        lambda sql, params: statements.append((sql, params)) or [],
    )

    client.rest(
        "GET",
        "/saas_quote_jobs",
        params={"status": "eq.queued", "select": "id", "limit": "1"},
    )
    client.rest(
        "GET",
        "/saas_quote_jobs",
        params={
            "status": "eq.processing",
            "select": "id,status,attempt_token,lease_expires_at,updated_at",
            "limit": "100",
        },
    )

    assert statements[0][0].startswith("SELECT id FROM saas_quote_jobs")
    assert statements[0][1] == ("queued", 1)
    assert statements[1][0].startswith(
        "SELECT id,status,attempt_token,lease_expires_at,updated_at FROM saas_quote_jobs"
    )
    assert statements[1][1] == ("processing", 100)


def test_idle_cycles_keep_fast_queue_polling_but_bound_recovery(monkeypatch):
    """Mutación detectada: recuperar leases en cada ciclo o demorar el sondeo de queued."""
    class Clock:
        value = 0

    clock = Clock()
    queue_poll_times = []
    recovery_times = []
    client = object()
    monkeypatch.setattr(quote_worker, "DEV_MODE", True)
    monkeypatch.setattr(quote_worker, "LocalDevClient", lambda: client)
    monkeypatch.setattr(quote_worker.time, "monotonic", lambda: clock.value)
    monkeypatch.setattr(quote_worker, "_LAST_STALE_RECOVERY_AT", None, raising=False)
    monkeypatch.setattr(quote_worker, "recover_stale_jobs", lambda _client: recovery_times.append(clock.value))
    monkeypatch.setattr(quote_worker, "fetch_next_job", lambda _client: queue_poll_times.append(clock.value))
    monkeypatch.setattr(quote_worker, "sync_tarkett_catalog_if_due", lambda _client: False)
    monkeypatch.setattr(quote_worker, "sync_offiho_catalog_if_due", lambda _client: False)

    for second in (0, 10, 20, 30, 40, 50, 60):
        clock.value = second
        quote_worker.run_once()

    assert queue_poll_times == [0, 10, 20, 30, 40, 50, 60]
    assert recovery_times == [0, 60]
