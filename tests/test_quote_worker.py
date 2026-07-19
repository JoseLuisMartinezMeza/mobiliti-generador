import os
import subprocess
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "worker"))

import quote_worker
import render_web_worker


def test_tarkett_catalog_sync_publishes_changed_snapshot_and_respects_interval(monkeypatch):
    base = {
        "source_file": "Inventario Tarkett.xls",
        "source_hash": "old-hash",
        "generated_at": "2026-07-08T00:00:00+00:00",
        "total": 1,
        "items": [
            {
                "code": "25731726",
                "name": "Cadiz",
                "unit": "MTK - metro cuadrado",
                "available_quantity": 10,
                "unit_price": 0,
                "price_source": "missing",
                "stock_source": "inventory_file",
                "product_url": "",
                "image_url": "",
                "match_status": "unmatched",
            }
        ],
    }
    enriched = {**base, "source_hash": "new-hash", "tarkettnet_matches": 1}

    class CatalogClient:
        def __init__(self):
            self.upserts = []

        def catalog_snapshot_get(self, supplier):
            assert supplier == "tarkett"
            return {"supplier": supplier, "source_hash": "old-hash", "payload": base}

        def catalog_snapshot_upsert(self, supplier, payload):
            self.upserts.append((supplier, payload))
            return {"supplier": supplier, "source_hash": payload["source_hash"]}

    client = CatalogClient()
    monkeypatch.setattr(quote_worker, "TARKETT_SYNC_ENABLED", True)
    monkeypatch.setattr(quote_worker, "TARKETTNET_EMAIL", "sync@example.com")
    monkeypatch.setattr(quote_worker, "TARKETTNET_PASSWORD", "test-password")
    monkeypatch.setattr(quote_worker, "TARKETT_SYNC_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(quote_worker, "_TARKETT_LAST_SYNC_ATTEMPT", 0.0)
    monkeypatch.setattr(
        quote_worker,
        "sync_catalog_from_tarkettnet",
        lambda payload, **kwargs: enriched,
    )

    assert quote_worker.sync_tarkett_catalog_if_due(client, force=True) is True
    assert client.upserts == [("tarkett", enriched)]
    assert quote_worker.sync_tarkett_catalog_if_due(client) is False
    assert client.upserts == [("tarkett", enriched)]


def test_tarkett_snapshot_uses_internal_api_without_service_key(monkeypatch):
    response_payload = {
        "supplier": "tarkett",
        "source_hash": "hash-1",
        "payload": {"source_hash": "hash-1", "generated_at": "2026-07-15T00:00:00+00:00", "items": []},
    }
    seen = {}

    class FakeResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(response_payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        seen.update(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "secret": request.get_header("X-mobiliti-rest-secret"),
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(quote_worker, "MOBILITI_REST_SECRET", "worker-secret")
    monkeypatch.setattr(quote_worker, "MOBILITI_API_URL", "https://web-lemon-one-45.vercel.app")
    monkeypatch.setattr(quote_worker.urllib.request, "urlopen", fake_urlopen)
    client = quote_worker.SupabaseClient.__new__(quote_worker.SupabaseClient)

    result = client.catalog_snapshot_get("tarkett")

    assert result == response_payload
    assert seen == {
        "url": "https://web-lemon-one-45.vercel.app/internal/catalogs/tarkett",
        "method": "GET",
        "secret": "worker-secret",
        "timeout": 60,
    }


@pytest.mark.parametrize("tarkett_did_work", [True, False])
def test_isolated_worker_runs_at_most_one_catalog_sync_during_idle_poll(
    monkeypatch, tarkett_did_work,
):
    client = object()
    synced = []
    monkeypatch.setattr(render_web_worker, "_has_pending_job", lambda: False)
    monkeypatch.setattr(render_web_worker, "_build_client", lambda: client)
    monkeypatch.setattr(
        render_web_worker.quote_worker,
        "sync_tarkett_catalog_if_due",
        lambda current: (synced.append("tarkett"), tarkett_did_work)[1],
    )
    monkeypatch.setattr(
        render_web_worker,
        "_run_rate_sync_isolated",
        lambda: (synced.append("rates"), False)[1],
    )
    monkeypatch.setattr(
        render_web_worker,
        "_run_catalog_sync_isolated",
        lambda: (synced.append("catalog"), False)[1],
    )

    assert render_web_worker._run_once_isolated() is tarkett_did_work
    assert synced == (["tarkett"] if tarkett_did_work else ["tarkett", "rates", "catalog"])


def test_isolated_worker_prioritizes_quote_and_skips_all_catalog_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(render_web_worker, "_has_pending_job", lambda: True)
    monkeypatch.setattr(
        render_web_worker.subprocess,
        "run",
        lambda cmd, **kwargs: (calls.append((cmd, kwargs)), type("Result", (), {"returncode": 0})())[1],
    )
    monkeypatch.setattr(
        render_web_worker,
        "_run_rate_sync_isolated",
        lambda: pytest.fail("rate sync must wait"),
    )
    monkeypatch.setattr(
        render_web_worker,
        "_run_catalog_sync_isolated",
        lambda: pytest.fail("catalog sync must wait"),
    )
    monkeypatch.setattr(
        render_web_worker.quote_worker,
        "sync_tarkett_catalog_if_due",
        lambda _client: pytest.fail("Tarkett sync must wait"),
    )

    assert render_web_worker._run_once_isolated() is True
    assert calls[0][0] == [sys.executable, str(render_web_worker.WORKER_SCRIPT), "--once"]


def test_rate_sync_is_isolated_throttled_and_visible_in_health(monkeypatch):
    calls = []
    clock = iter((100.0, 100.0, 101.0, 101.0))
    monkeypatch.setattr(render_web_worker, "CATALOG_SYNC_ENABLED", True, raising=False)
    monkeypatch.setattr(render_web_worker, "_RATE_LAST_SYNC_ATTEMPT", 0.0, raising=False)
    monkeypatch.setattr(render_web_worker.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        render_web_worker.subprocess,
        "run",
        lambda cmd, **kwargs: (
            calls.append((cmd, kwargs)), type("Result", (), {"returncode": 0})()
        )[1],
    )

    assert render_web_worker._run_rate_sync_isolated() is True
    assert render_web_worker._run_rate_sync_isolated() is False
    assert calls == [(
        [sys.executable, "-m", "mobiliti_saas.worker.catalog_sync.rate_service"],
        {
            "cwd": str(render_web_worker.PROJECT_ROOT),
            "check": False,
            "timeout": 30,
            "stdout": render_web_worker.subprocess.DEVNULL,
            "stderr": render_web_worker.subprocess.DEVNULL,
        },
    )]
    payload = render_web_worker._health_payload()
    assert payload["last_rate_sync_status"] == "succeeded"
    assert payload["last_rate_sync_at"]


@pytest.mark.parametrize(("failure", "expected"), [
    (type("Result", (), {"returncode": 3})(), "misconfigured"),
    (type("Result", (), {"returncode": 1})(), "failed"),
    (subprocess.TimeoutExpired(["rates"], 30, output=b"secret"), "timeout"),
])
def test_rate_sync_failure_is_redacted_and_does_not_block_catalog(monkeypatch, failure, expected):
    monkeypatch.setattr(render_web_worker, "CATALOG_SYNC_ENABLED", True, raising=False)
    monkeypatch.setattr(render_web_worker, "_RATE_LAST_SYNC_ATTEMPT", 0.0, raising=False)

    def fake_run(*_args, **_kwargs):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(render_web_worker.subprocess, "run", fake_run)

    assert render_web_worker._run_rate_sync_isolated() is False
    payload = render_web_worker._health_payload()
    assert payload["last_rate_sync_status"] == expected
    assert "secret" not in json.dumps(payload)


def test_catalog_sync_subprocess_uses_exact_command_timeout_and_safe_health(monkeypatch):
    calls = []
    monkeypatch.setattr(render_web_worker, "CATALOG_SYNC_ENABLED", True, raising=False)
    monkeypatch.setattr(render_web_worker, "CATALOG_SYNC_TIMEOUT_SECONDS", 1800, raising=False)
    monkeypatch.setattr(
        render_web_worker.subprocess,
        "run",
        lambda cmd, **kwargs: (calls.append((cmd, kwargs)), type("Result", (), {"returncode": 0})())[1],
    )

    assert render_web_worker._run_catalog_sync_isolated() is True
    assert calls == [(
        [sys.executable, "-m", "mobiliti_saas.worker.catalog_sync.service", "--due"],
        {
            "cwd": str(render_web_worker.PROJECT_ROOT),
            "check": False,
            "timeout": 1800,
            "stdout": render_web_worker.subprocess.DEVNULL,
            "stderr": render_web_worker.subprocess.DEVNULL,
        },
    )]
    payload = render_web_worker._health_payload()
    assert payload["last_catalog_sync_status"] == "succeeded"
    assert payload["last_catalog_sync_at"]
    assert "stdout" not in payload and "stderr" not in payload


@pytest.mark.parametrize(
    ("returncode", "expected_status"),
    [
        (render_web_worker.CATALOG_EXIT_NO_WORK, "no_work"),
        (render_web_worker.CATALOG_EXIT_DISABLED, "misconfigured"),
    ],
)
def test_catalog_sync_non_work_exit_does_not_replace_previous_failure(
    monkeypatch, returncode, expected_status,
):
    monkeypatch.setattr(render_web_worker, "CATALOG_SYNC_ENABLED", True, raising=False)
    monkeypatch.setattr(
        render_web_worker.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": returncode})(),
    )
    render_web_worker._set_state(
        status="degraded",
        last_error="catalog_sync_failed",
        last_catalog_sync_at="2026-07-16T00:00:00+00:00",
        last_catalog_sync_status="timeout",
    )

    assert render_web_worker._run_catalog_sync_isolated() is False
    payload = render_web_worker._health_payload()
    assert payload["last_catalog_sync_at"] == "2026-07-16T00:00:00+00:00"
    assert payload["last_catalog_sync_status"] == "timeout"
    assert payload["status"] == "degraded"
    assert payload["last_error"] == "catalog_sync_failed"

    render_web_worker._set_state(
        status="running", last_error=None, last_catalog_sync_at=None,
        last_catalog_sync_status="never",
    )
    assert render_web_worker._run_catalog_sync_isolated() is False
    payload = render_web_worker._health_payload()
    assert payload["last_catalog_sync_at"] is None
    assert payload["last_catalog_sync_status"] == expected_status


def test_catalog_sync_timeout_is_bounded_and_invalid_values_do_not_break_import():
    assert 0 < render_web_worker.CATALOG_SYNC_TIMEOUT_SECONDS < render_web_worker.CATALOG_SYNC_LEASE_SECONDS
    assert render_web_worker._catalog_sync_timeout("invalid") == 1800
    assert render_web_worker._catalog_sync_timeout("0") == 1800
    assert render_web_worker._catalog_sync_timeout("999999") < render_web_worker.CATALOG_SYNC_LEASE_SECONDS


def test_worker_loop_does_not_clear_catalog_failure_after_no_work(monkeypatch):
    class SingleCycleEvent:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, _seconds):
            self.stopped = True

    def failed_catalog_cycle():
        render_web_worker._set_state(
            status="degraded", last_error="catalog_sync_failed",
            last_catalog_sync_status="timeout",
        )
        return False

    monkeypatch.setattr(render_web_worker, "stop_event", SingleCycleEvent())
    monkeypatch.setattr(render_web_worker, "_run_once_isolated", failed_catalog_cycle)
    monkeypatch.setattr(render_web_worker, "ISOLATE_JOBS", True)

    render_web_worker.worker_loop()

    payload = render_web_worker._health_payload()
    assert payload["last_error"] == "catalog_sync_failed"
    assert payload["last_catalog_sync_status"] == "timeout"


@pytest.mark.parametrize(("failure", "expected"), [
    (type("Result", (), {"returncode": 9})(), "failed"),
    (subprocess.TimeoutExpired(["catalog"], 1800, output=b"secret", stderr=b"private"), "timeout"),
    (OSError("private executable path"), "failed"),
])
def test_catalog_sync_failure_is_degraded_but_does_not_raise_or_leak(monkeypatch, failure, expected):
    monkeypatch.setattr(render_web_worker, "CATALOG_SYNC_ENABLED", True, raising=False)

    def fake_run(*_args, **_kwargs):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(render_web_worker.subprocess, "run", fake_run)

    assert render_web_worker._run_catalog_sync_isolated() is False
    payload = render_web_worker._health_payload()
    assert payload["ok"] is True
    assert payload["status"] == "degraded"
    assert payload["last_catalog_sync_status"] == expected
    assert "secret" not in json.dumps(payload)
    assert "private" not in json.dumps(payload)


def test_default_template_resolves_existing_template():
    template = quote_worker._default_template()

    assert template.exists()
    assert template.name.startswith("Formato")


class FakeClient:
    def __init__(self):
        self.calls = []
        self.claim_input_path = "users/7/jobs/job-1/input.xlsx"
        self.input_content = b"input"

    def rest(self, method, path, params=None, data=None):
        self.calls.append((method, path, data))
        if method == "PATCH" and "status=eq.queued" in path and data and data.get("status") == "processing":
            return [{"id": "job-1", "usuario_id": 7, "input_path": self.claim_input_path, **data}]
        if method == "PATCH" and data and data.get("status") == "completed":
            return [{"id": "job-1", **data}]
        return []

    def storage_download(self, object_path, dest):
        Path(dest).write_bytes(self.input_content)
        self.calls.append(("DOWNLOAD", object_path, None))

    def storage_upload(self, object_path, source):
        assert Path(source).exists()
        self.calls.append(("UPLOAD", object_path, None))


def test_process_job_marks_completed(monkeypatch):
    client = FakeClient()

    def fake_generator(job, input_path, output_path):
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.xlsx",
            "metadata": {"cotizacion": "COT-001"},
        },
    )

    statuses = [data["status"] for _, _, data in client.calls if isinstance(data, dict) and "status" in data]
    assert statuses == ["processing", "completed"]
    assert ("UPLOAD", "users/7/jobs/job-1/output.xlsx", None) in client.calls
    completed_payload = next(data for _, _, data in client.calls if isinstance(data, dict) and data.get("status") == "completed")
    assert completed_payload["metadata"]["generation_seconds"] >= 0


def test_supabase_client_storage_methods_route_to_r2(monkeypatch, tmp_path):
    calls = []

    class Body:
        def read(self):
            return b"r2-input"

    class FakeR2Client:
        def get_object(self, **kwargs):
            calls.append(("GET", kwargs))
            return {"Body": Body()}

        def put_object(self, **kwargs):
            calls.append(("PUT", {**kwargs, "Body": b"<body>"}))

        def delete_object(self, **kwargs):
            calls.append(("DELETE", kwargs))

    monkeypatch.setattr(quote_worker, "STORAGE_PROVIDER", "r2")
    monkeypatch.setattr(quote_worker, "R2_BUCKET", "mobiliti-quotes")
    monkeypatch.setattr(quote_worker, "_r2_client", lambda: FakeR2Client())

    client = quote_worker.SupabaseClient.__new__(quote_worker.SupabaseClient)
    downloaded = tmp_path / "input.xlsx"
    upload_source = tmp_path / "output.xlsx"
    upload_source.write_bytes(b"generated")

    client.storage_download("users/7/jobs/job-1/input.xlsx", downloaded)
    client.storage_upload("users/7/jobs/job-1/output.xlsx", upload_source)
    client.storage_delete("users/7/jobs/job-1/input.xlsx")

    assert downloaded.read_bytes() == b"r2-input"
    assert calls[0] == ("GET", {"Bucket": "mobiliti-quotes", "Key": "users/7/jobs/job-1/input.xlsx"})
    assert calls[1][0] == "PUT"
    assert calls[1][1]["Bucket"] == "mobiliti-quotes"
    assert calls[1][1]["Key"] == "users/7/jobs/job-1/output.xlsx"
    assert calls[1][1]["ContentType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert calls[2] == ("DELETE", {"Bucket": "mobiliti-quotes", "Key": "users/7/jobs/job-1/input.xlsx"})


def test_process_job_converts_pdf_before_generator(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.pdf"
    seen = {}

    def fake_convert(source_pdf, output_xlsx, reference_xlsx):
        seen["source_pdf"] = source_pdf.name
        seen["reference_xlsx"] = str(reference_xlsx)
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_input"] = input_path.name
        seen["metadata"] = dict(job.get("metadata") or {})
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_convert_pdf_to_quotation", fake_convert)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.pdf",
            "metadata": {"original_filename": "supplier.pdf"},
        },
    )

    assert seen["source_pdf"] == "input.pdf"
    assert seen["generator_input"] == "quotation_from_pdf.xlsx"
    assert seen["metadata"]["input_extension"] == ".pdf"
    assert seen["metadata"]["pdf_converted"] is True
    assert ("UPLOAD", "users/7/jobs/job-1/output.xlsx", None) in client.calls


def test_process_job_converts_tarkett_json_before_generator(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    client.input_content = json.dumps({"source_type": "tarkett_cart", "items": []}).encode("utf-8")
    seen = {}

    def fake_convert(source_json, output_xlsx, payload):
        seen["source_json"] = source_json.name
        seen["source_type"] = payload["source_type"]
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_input"] = input_path.name
        seen["metadata"] = dict(job.get("metadata") or {})
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_convert_tarkett_cart_to_quotation", fake_convert)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.json",
            "metadata": {"source_type": "tarkett_cart", "input_extension": ".json", "original_filename": "tarkett-cart.json"},
        },
    )

    assert seen["source_json"] == "input.json"
    assert seen["source_type"] == "tarkett_cart"
    assert seen["generator_input"] == "quotation_from_tarkett.xlsx"
    assert seen["metadata"]["input_extension"] == ".json"
    assert seen["metadata"]["tarkett_converted"] is True
    assert ("UPLOAD", "users/7/jobs/job-1/output.xlsx", None) in client.calls


def test_process_job_converts_offiho_json_before_generator(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    client.input_content = json.dumps({"source_type": "offiho_cart", "items": []}).encode("utf-8")
    seen = {}

    def fake_convert(source_json, output_xlsx, payload):
        seen["converted_input"] = source_json.name
        seen["source_type"] = payload["source_type"]
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_input"] = input_path.name
        seen["metadata"] = dict(job["metadata"])
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_convert_offiho_cart_to_quotation", fake_convert)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.json",
            "metadata": {"source_type": "offiho_cart", "input_extension": ".json"},
        },
    )

    assert seen["converted_input"] == "input.json"
    assert seen["source_type"] == "offiho_cart"
    assert seen["generator_input"] == "quotation_from_offiho.xlsx"
    assert seen["metadata"]["offiho_converted"] is True


def test_process_job_converts_supplier_cart_and_sets_frozen_catalog_metadata(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    payload = {
        "source_type": "supplier_cart",
        "supplier": "alma",
        "catalog_source_hash": "a" * 64,
        "base_currency": "USD",
        "quote_currency": "MXN",
        "exchange_rate": "18.500000",
        "rate_source": "saas_exchange_rates",
        "rate_effective_date": "2026-07-15",
        "rate_retrieved_at": "2026-07-15T23:00:00Z",
        "items": [],
    }
    client.input_content = json.dumps(payload).encode("utf-8")
    seen = {}

    def fake_convert(source_json, output_xlsx, cart_payload):
        seen["converted_input"] = source_json.name
        seen["payload"] = cart_payload
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_input"] = input_path.name
        seen["metadata"] = dict(job["metadata"])
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_convert_supplier_cart_to_quotation", fake_convert, raising=False)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.json",
            "metadata": {"source_type": "supplier_cart", "input_extension": ".json"},
        },
    )

    assert seen["converted_input"] == "input.json"
    assert seen["payload"] == payload
    assert seen["generator_input"] == "quotation_from_supplier.xlsx"
    expected_metadata = {
        "source_type": "supplier_cart",
        "input_extension": ".json",
        "supplier_converted": True,
        "catalog_supplier": "alma",
        "catalog_supplier_label": "ALMA",
        "catalog_price_mode": "list_price_net",
        "base_currency": "USD",
        "quote_currency": "MXN",
        "exchange_rate": "18.500000",
        "rate_source": "saas_exchange_rates",
        "rate_effective_date": "2026-07-15",
        "rate_retrieved_at": "2026-07-15T23:00:00Z",
    }
    assert {key: seen["metadata"][key] for key in expected_metadata} == expected_metadata


def test_worker_uses_lumbro_label_through_generic_supplier_registry():
    assert quote_worker.SUPPLIER_LABELS["lumbro"] == "Lumbro"


def test_process_job_uses_source_type_when_input_name_is_not_json(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/upload.bin"
    client.input_content = json.dumps({"source_type": "offiho_cart", "items": []}).encode("utf-8")
    seen = {}

    def fake_convert(source_json, output_xlsx, payload):
        seen["converted_input"] = source_json.name
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_input"] = input_path.name
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_convert_offiho_cart_to_quotation", fake_convert)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/upload.bin",
            "metadata": {"source_type": "offiho_cart"},
        },
    )

    assert seen["converted_input"] == "input.json"
    assert seen["generator_input"] == "quotation_from_offiho.xlsx"


@pytest.mark.parametrize("source_type", ["tarkett_cart", "offiho_cart"])
def test_input_extension_uses_cart_source_type_without_json_filename(source_type):
    job = {
        "input_path": "users/7/jobs/job-1/upload.bin",
        "metadata": {"source_type": source_type},
    }

    assert quote_worker._input_extension_for_job(job) == ".json"


@pytest.mark.parametrize(
    ("payload", "metadata_source_type", "error"),
    [
        ({"source_type": "unknown_cart", "items": []}, "unknown_cart", "Tipo de fuente JSON no soportado"),
        ({"items": []}, "tarkett_cart", "JSON de entrada sin source_type"),
        ({"source_type": "offiho_cart", "items": []}, "tarkett_cart", "source_type de metadata no coincide"),
        ({"source_type": "offiho_cart", "items": []}, None, "source_type de metadata ausente"),
        (["offiho_cart"], "offiho_cart", "JSON de entrada debe ser un objeto"),
    ],
)
def test_process_job_rejects_invalid_json_cart_before_generator(monkeypatch, payload, metadata_source_type, error):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    client.input_content = json.dumps(payload).encode("utf-8")
    generator_called = False

    def fake_generator(job, input_path, output_path):
        nonlocal generator_called
        generator_called = True

    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)
    metadata = {"input_extension": ".json"}
    if metadata_source_type is not None:
        metadata["source_type"] = metadata_source_type

    with pytest.raises(RuntimeError, match=error):
        quote_worker.process_job(
            client,
            {
                "id": "job-1",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-1/input.json",
                "metadata": metadata,
            },
        )

    assert generator_called is False


def test_process_job_rejects_invalid_json_without_exposing_content(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    client.input_content = b'{"source_type": "offiho_cart", "secret": "do-not-expose"'
    monkeypatch.setattr(quote_worker, "_run_generator", lambda *args: pytest.fail("generator should not run"))

    with pytest.raises(RuntimeError, match="JSON de entrada invalido") as exc_info:
        quote_worker.process_job(
            client,
            {
                "id": "job-1",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-1/input.json",
                "metadata": {"source_type": "offiho_cart", "input_extension": ".json"},
            },
        )

    assert "do-not-expose" not in str(exc_info.value)


def test_prepare_generator_input_reads_cart_json_once(monkeypatch, tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"source_type":"offiho_cart","items":[]}', encoding="utf-8")
    reads = []

    original_read_text = Path.read_text

    def record_read_text(path, *args, **kwargs):
        if path == source:
            reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record_read_text)
    monkeypatch.setattr(
        quote_worker,
        "_convert_offiho_cart_to_quotation",
        lambda source_json, output_xlsx, payload: output_xlsx.write_bytes(b"converted"),
    )

    output = quote_worker._prepare_generator_input(
        {"metadata": {"source_type": "offiho_cart", "input_extension": ".json"}}, source, tmp_path
    )

    assert output.name == "quotation_from_offiho.xlsx"
    assert reads == [source]


def test_process_job_downloads_input_from_job_storage_provider(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    seen = {}

    def fake_download(client_arg, job, dest):
        assert client_arg is client
        seen["job_storage_provider"] = job["metadata"].get("storage_provider")
        seen["dest"] = dest.name
        dest.write_text('{"source_type":"tarkett_cart","items":[{"code":"25731726"}]}', encoding="utf-8")

    def fake_convert(source_json, output_xlsx, payload):
        seen["converted_input"] = source_json.name
        seen["source_type"] = payload["source_type"]
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_input"] = input_path.name
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_download_job_input", fake_download)
    monkeypatch.setattr(quote_worker, "_convert_tarkett_cart_to_quotation", fake_convert)
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.json",
            "metadata": {
                "source_type": "tarkett_cart",
                "input_extension": ".json",
                "original_filename": "tarkett-cart.json",
                "storage_provider": "supabase",
            },
        },
    )

    assert seen["job_storage_provider"] == "supabase"
    assert seen["dest"] == "input.json"
    assert seen["converted_input"] == "input.json"
    assert seen["source_type"] == "tarkett_cart"
    assert seen["generator_input"] == "quotation_from_tarkett.xlsx"


def test_download_job_input_falls_back_for_legacy_jobs_without_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(quote_worker, "STORAGE_PROVIDER", "r2")

    class LegacyStorageClient:
        def __init__(self):
            self.download_calls = []
            self.delete_calls = []

        def storage_download_from_provider(self, object_path, dest, provider):
            self.download_calls.append((object_path, provider))
            if provider == "r2":
                raise RuntimeError("R2 download error: NoSuchKey")
            Path(dest).write_bytes(b"legacy input")

        def storage_delete_from_provider(self, object_path, provider):
            self.delete_calls.append((object_path, provider))

    client = LegacyStorageClient()
    job = {"id": "job-legacy", "input_path": "users/7/jobs/job-legacy/input.json", "metadata": {}}
    dest = tmp_path / "input.json"

    quote_worker._download_job_input(client, job, dest)
    quote_worker._delete_job_input(client, job)

    assert dest.read_bytes() == b"legacy input"
    assert client.download_calls == [
        ("users/7/jobs/job-legacy/input.json", "r2"),
        ("users/7/jobs/job-legacy/input.json", "supabase"),
    ]
    assert client.delete_calls == [("users/7/jobs/job-legacy/input.json", "supabase")]
    assert job["metadata"]["resolved_input_storage_provider"] == "supabase"


def test_process_job_skips_when_not_claimed(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(client, "rest", lambda method, path, params=None, data=None: [])

    called = False

    def fake_generator(job, input_path, output_path):
        nonlocal called
        called = True

    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    result = quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.xlsx",
            "metadata": {},
        },
    )

    assert result is None
    assert called is False


def test_recover_stale_jobs_requeues_processing(monkeypatch):
    client = FakeClient()

    def fake_rest(method, path, params=None, data=None):
        assert method == "PATCH"
        assert path == "/saas_quote_jobs"
        assert params["status"] == "eq.processing"
        assert params["updated_at"].startswith("lt.")
        assert data["status"] == "queued"
        return [{"id": "job-1", **data}]

    monkeypatch.setattr(client, "rest", fake_rest)
    monkeypatch.setattr(quote_worker, "STALE_MINUTES", 30)

    assert quote_worker.recover_stale_jobs(client) == 1


def test_recover_stale_jobs_can_be_disabled(monkeypatch):
    client = FakeClient()

    def fail_rest(method, path, params=None, data=None):
        raise AssertionError("rest should not be called")

    monkeypatch.setattr(client, "rest", fail_rest)
    monkeypatch.setattr(quote_worker, "STALE_MINUTES", 0)

    assert quote_worker.recover_stale_jobs(client) == 0


def test_process_job_marks_failed(monkeypatch):
    client = FakeClient()

    def failing_generator(job, input_path, output_path):
        raise RuntimeError("excel failed")

    monkeypatch.setattr(quote_worker, "_run_generator", failing_generator)

    try:
        quote_worker.process_job(
            client,
            {
                "id": "job-1",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-1/input.xlsx",
                "metadata": {},
            },
        )
    except RuntimeError:
        pass

    statuses = [data["status"] for _, _, data in client.calls if isinstance(data, dict) and "status" in data]
    assert statuses == ["processing", "failed"]
    failed_payload = next(data for _, _, data in client.calls if isinstance(data, dict) and data.get("status") == "failed")
    assert failed_payload["metadata"]["generation_seconds"] >= 0


def test_process_job_rejects_output_larger_than_storage_limit(monkeypatch):
    client = FakeClient()

    def fake_generator(job, input_path, output_path):
        output_path.write_bytes(b"x" * 11)

    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)
    monkeypatch.setattr(quote_worker, "MAX_QUOTE_OUTPUT_MB", 0)

    try:
        quote_worker.process_job(
            client,
            {
                "id": "job-1",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-1/input.xlsx",
                "metadata": {},
            },
        )
    except RuntimeError as exc:
        assert "supera el limite de Storage" in str(exc)
    else:
        raise AssertionError("oversized output should fail before storage upload")

    assert ("UPLOAD", "users/7/jobs/job-1/output.xlsx", None) not in client.calls
    failed_payload = next(data for _, _, data in client.calls if isinstance(data, dict) and data.get("status") == "failed")
    assert "supera el limite de Storage" in failed_payload["error_message"]
