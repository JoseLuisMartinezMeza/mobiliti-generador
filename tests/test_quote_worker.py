import os
import subprocess
import sys
import json
import threading
import time
from datetime import datetime
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
        if method == "PATCH" and "status=eq.processing" in path:
            return [{
                "id": "job-1", "usuario_id": 7, "input_path": self.claim_input_path,
                "attempt_token": path.split("attempt_token=eq.", 1)[1].split("&", 1)[0],
                **(data or {}),
            }]
        if method == "PATCH" and "status=eq.completed" in path:
            return [{"id": "job-1", "status": "completed", **(data or {})}]
        return []

    def storage_download(self, object_path, dest):
        Path(dest).write_bytes(self.input_content)
        self.calls.append(("DOWNLOAD", object_path, None))

    def storage_upload(self, object_path, source):
        assert Path(source).exists()
        self.calls.append(("UPLOAD", object_path, None))

    def storage_delete(self, object_path):
        self.calls.append(("DELETE", object_path, None))


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
    assert any(method == "UPLOAD" and "/attempts/" in path for method, path, _data in client.calls)
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
    assert any(method == "UPLOAD" and "/attempts/" in path for method, path, _data in client.calls)


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
    assert any(method == "UPLOAD" and "/attempts/" in path for method, path, _data in client.calls)


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


def _valid_mixed_worker_payload():
    rate = {
        "catalog": "tarkett",
        "base_currency": "MXN",
        "quote_currency": "EUR",
        "exchange_rate": "0.048780",
        "rate_source": "saas_exchange_rates",
        "rate_effective_date": "2026-07-19",
        "rate_retrieved_at": "2026-07-19T20:00:00Z",
    }
    line = {
        "canonical_key": "tarkett:T-1",
        "catalog": "tarkett",
        "supplier": "Tarkett",
        "code": "T-1",
        "name": "Piso Tarkett",
        "description": "Piso de prueba",
        "unit": "M2",
        "quantity": "1.000000",
        "unit_price": "4.88",
        "discount_percent": "40.000000",
        "original_currency": "MXN",
        "original_unit_price": "100.000000",
        "frozen_exchange_rate": "0.048780",
        "source_reference": "tarkett:test:T-1",
        "price_mode": "list",
        "auto_electrification": True,
        "tax_rate": "0.160000",
        "image_url": "",
        "product_url": "",
        "warnings": [],
        "code_status": "verified",
        "configuration": "",
        "attributes": {},
        "variant": "",
        "availability_type": "stocked",
        "available_quantity": "10.000000",
        "stock": "10.000000",
        "lead_time": "",
        "price_source": "catalog",
        "stock_status": "available",
        "image_kind": "placeholder",
        "reservation": {
            "identity": "T-1",
            "sku": "T-1",
            "quantity": "1.000000",
            "stock": "10.000000",
        },
    }
    return {
        "source_type": "mixed_catalog_cart",
        "quote_currency": "EUR",
        "created_at": "2026-07-19T20:00:00+00:00",
        "item_count": 1,
        "groups": [
            {
                "catalog": "tarkett",
                "catalog_source_hash": "a" * 64,
                "base_currency": "MXN",
                "quote_currency": "EUR",
                "exchange_rate": "0.048780",
                "rate_source": "saas_exchange_rates",
                "rate_effective_date": "2026-07-19",
                "rate_retrieved_at": "2026-07-19T20:00:00Z",
                "items": [line],
            }
        ],
        "rate_summary": [rate],
        "auto_electrification_rate": {
            "base_currency": "MXN",
            "quote_currency": "EUR",
            "exchange_rate": "0.048780",
            "rate_source": "saas_exchange_rates",
            "rate_effective_date": "2026-07-19",
            "rate_retrieved_at": "2026-07-19T20:00:00Z",
        },
    }


def _valid_mixed_supplier_worker_payload():
    payload = _valid_mixed_worker_payload()
    payload["groups"][0].update(
        {
            "catalog": "sonara",
            "catalog_source_hash": "b" * 64,
        }
    )
    line = payload["groups"][0]["items"][0]
    line.update(
        {
            "canonical_key": 'sonara:["sonara:item-1"]',
            "catalog": "sonara",
            "supplier": "Sonara",
            "code": "SON-1",
            "name": "Producto Sonara",
            "description": "Producto generico de prueba",
            "source_reference": "sonara:test:SON-1",
            "discount_percent": "0.000000",
            "price_mode": "net",
            "auto_electrification": False,
            "reservation": {
                "identity": "sonara:item-1",
                "sku": "SON-1",
                "quantity": "1.000000",
                "stock": "10.000000",
            },
        }
    )
    payload["rate_summary"][0]["catalog"] = "sonara"
    payload["auto_electrification_rate"] = None
    return payload


def test_process_job_converts_mixed_cart_once_and_sets_identity_exchange(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    payload = _valid_mixed_worker_payload()
    client.input_content = json.dumps(payload).encode("utf-8")
    seen = {"converter_calls": 0, "generator_calls": 0}

    def fake_convert(source_json, output_xlsx, cart_payload):
        seen["converter_calls"] += 1
        seen["payload"] = cart_payload
        seen["output_name"] = output_xlsx.name
        output_xlsx.write_bytes(b"converted")

    def fake_generator(job, input_path, output_path):
        seen["generator_calls"] += 1
        seen["generator_input"] = input_path.name
        seen["metadata"] = job["metadata"]
        seen["payload"]["rate_summary"][0]["exchange_rate"] = "9.999999"
        seen["payload"]["auto_electrification_rate"]["exchange_rate"] = "8.888888"
        output_path.write_bytes(b"output")

    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        fake_convert,
        raising=False,
    )
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(
        client,
        {
            "id": "job-1",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-1/input.json",
            "metadata": {
                "source_type": "mixed_catalog_cart",
                "input_extension": ".json",
            },
        },
    )

    assert seen["converter_calls"] == 1
    assert seen["generator_calls"] == 1
    assert seen["output_name"] == "quotation_from_mixed_catalog.xlsx"
    assert seen["generator_input"] == "quotation_from_mixed_catalog.xlsx"
    assert seen["metadata"]["mixed_catalog_converted"] is True
    assert seen["metadata"]["catalog_price_mode"] == "mixed_catalog_converted"
    assert seen["metadata"]["base_currency"] == "EUR"
    assert seen["metadata"]["quote_currency"] == "EUR"
    assert seen["metadata"]["exchange_rate"] == "1.000000"
    assert seen["metadata"]["descuento"] == 0
    assert seen["metadata"]["rate_summary"] == payload["rate_summary"]
    assert seen["metadata"]["auto_electrification_rate"] == payload["auto_electrification_rate"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda payload: payload.update(groups=[]), "Grupos mixtos invalidos"),
        (lambda payload: payload.update(item_count=2), "Conteo mixto inconsistente"),
        (
            lambda payload: payload.update(rate_summary=[]),
            "Resumen de tasas mixtas inconsistente",
        ),
        (
            lambda payload: payload.update(auto_electrification_rate=None),
            "Tasa de electrificacion mixta invalida",
        ),
    ),
)
def test_mixed_payload_is_validated_before_converter(
    monkeypatch,
    mutation,
    message,
):
    payload = _valid_mixed_worker_payload()
    mutation(payload)
    _assert_mixed_worker_preflight_rejects(monkeypatch, payload, message)


def _assert_mixed_worker_preflight_rejects(monkeypatch, payload, message):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    client.input_content = json.dumps(payload).encode("utf-8")
    called = []
    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        lambda *_args: called.append("convert"),
    )
    monkeypatch.setattr(
        quote_worker,
        "_run_generator",
        lambda *_args: called.append("generate"),
    )

    with pytest.raises(RuntimeError, match=message):
        quote_worker.process_job(
            client,
            {
                "id": "job-1",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-1/input.json",
                "metadata": {
                    "source_type": "mixed_catalog_cart",
                    "input_extension": ".json",
                },
            },
        )

    assert called == []


def _mutate_canonical_base_currency(payload):
    payload["groups"][0]["base_currency"] = "USD"
    payload["groups"][0]["items"][0]["original_currency"] = "USD"
    payload["rate_summary"][0]["base_currency"] = "USD"
    payload["auto_electrification_rate"]["base_currency"] = "USD"


def _mutate_line_original_currency(payload):
    payload["groups"][0]["items"][0]["original_currency"] = "USD"


def _mutate_line_frozen_rate(payload):
    payload["groups"][0]["items"][0]["frozen_exchange_rate"] = "0.050000"


def _mutate_group_frozen_rate(payload):
    payload["groups"][0]["exchange_rate"] = "0.050000"
    payload["rate_summary"][0]["exchange_rate"] = "0.050000"
    payload["auto_electrification_rate"]["exchange_rate"] = "0.050000"


def _mutate_converted_unit_price(payload):
    payload["groups"][0]["items"][0]["unit_price"] = "4.89"


def _mutate_supplier_label(payload):
    payload["groups"][0]["items"][0]["supplier"] = "Proveedor falso"


def _mutate_generic_price_mode(payload):
    payload["groups"][0]["items"][0]["price_mode"] = "list"


def _mutate_generic_auto_electrification(payload):
    payload["groups"][0]["items"][0]["auto_electrification"] = True


def _mutate_reservation_stock(payload):
    payload["groups"][0]["items"][0]["reservation"]["stock"] = "9.000000"


def _mutate_oversized_attributes(payload):
    payload["groups"][0]["items"][0]["attributes"] = {"detail": "x" * 33_000}


def _mutate_deep_attributes(payload):
    nested = "leaf"
    for _ in range(10):
        nested = {"nested": nested}
    payload["groups"][0]["items"][0]["attributes"] = nested


@pytest.mark.parametrize(
    ("payload_factory", "mutation"),
    (
        (_valid_mixed_worker_payload, _mutate_canonical_base_currency),
        (_valid_mixed_worker_payload, _mutate_line_original_currency),
        (_valid_mixed_worker_payload, _mutate_line_frozen_rate),
        (_valid_mixed_worker_payload, _mutate_group_frozen_rate),
        (_valid_mixed_worker_payload, _mutate_converted_unit_price),
        (_valid_mixed_worker_payload, _mutate_supplier_label),
        (_valid_mixed_supplier_worker_payload, _mutate_generic_price_mode),
        (_valid_mixed_supplier_worker_payload, _mutate_generic_auto_electrification),
        (_valid_mixed_worker_payload, _mutate_reservation_stock),
        (_valid_mixed_worker_payload, _mutate_oversized_attributes),
        (_valid_mixed_worker_payload, _mutate_deep_attributes),
    ),
)
def test_mixed_payload_invariants_fail_before_converter_and_generator(
    monkeypatch,
    payload_factory,
    mutation,
):
    payload = payload_factory()
    mutation(payload)
    _assert_mixed_worker_preflight_rejects(
        monkeypatch,
        payload,
        "Grupos mixtos invalidos",
    )


def test_mixed_auto_electrification_rate_mismatch_fails_before_converter(
    monkeypatch,
):
    payload = _valid_mixed_worker_payload()
    payload["auto_electrification_rate"]["exchange_rate"] = "0.050000"
    _assert_mixed_worker_preflight_rejects(
        monkeypatch,
        payload,
        "Tasa de electrificacion mixta invalida",
    )


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
        (_valid_mixed_worker_payload(), "tarkett_cart", "source_type de metadata no coincide"),
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
    calls = []

    def fake_rest(method, path, params=None, data=None):
        calls.append((method, path, params, data))
        if method == "GET":
            assert path == "/saas_quote_jobs"
            assert params["status"] == "eq.processing"
            return [{
                "id": "job-1", "status": "processing", "attempt_token": "attempt-1",
                "lease_expires_at": "2000-01-01T00:00:00Z",
                "updated_at": "2000-01-01T00:00:00Z",
            }]
        assert method == "PATCH"
        assert "id=eq.job-1&status=eq.processing&attempt_token=eq.attempt-1" in path
        assert "lease_expires_at=lt." in path
        assert data["status"] == "queued"
        return [{"id": "job-1", **data}]

    monkeypatch.setattr(client, "rest", fake_rest)
    monkeypatch.setattr(quote_worker, "STALE_MINUTES", 30)

    assert quote_worker.recover_stale_jobs(client) == 1
    assert [method for method, *_rest in calls] == ["GET", "PATCH"]


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

    assert not any(method == "UPLOAD" for method, _path, _data in client.calls)
    failed_payload = next(data for _, _, data in client.calls if isinstance(data, dict) and data.get("status") == "failed")
    assert "supera el limite de Storage" in failed_payload["error_message"]


class FencedWorkerClient:
    def __init__(self, *, completion_mode="ok"):
        self.lock = threading.RLock()
        self.job = {
            "id": "job-fenced",
            "usuario_id": 7,
            "status": "queued",
            "input_path": "users/7/jobs/job-fenced/input.xlsx",
            "output_path": None,
            "metadata": {},
            "attempt_token": None,
            "lease_expires_at": None,
            "updated_at": "2026-07-19T00:00:00Z",
        }
        self.objects = {self.job["input_path"]: b"input"}
        self.uploads = []
        self.deletes = []
        self.completion_mode = completion_mode
        self.completion_attempts = 0
        self.heartbeat_count = 0

    @staticmethod
    def _filters(path):
        query = path.split("?", 1)[1] if "?" in path else ""
        filters = []
        for part in query.split("&"):
            if "=eq." in part:
                key, value = part.split("=eq.", 1)
                filters.append((key, "eq", value.replace("%2B", "+")))
            elif "=lt." in part:
                key, value = part.split("=lt.", 1)
                filters.append((key, "lt", value.replace("%2B", "+")))
            elif part.endswith("=is.null"):
                filters.append((part[:-8], "null", None))
        return filters

    def _matches(self, filters):
        for key, operator, value in filters:
            current = self.job.get(key)
            if operator == "eq" and str(current) != value:
                return False
            if operator == "null" and current is not None:
                return False
            if operator == "lt":
                if current is None or datetime.fromisoformat(str(current).replace("Z", "+00:00")) >= datetime.fromisoformat(value.replace("Z", "+00:00")):
                    return False
        return True

    def rest(self, method, path, params=None, data=None):
        with self.lock:
            if method == "GET" and path == "/saas_quote_jobs":
                status_filter = (params or {}).get("status")
                id_filter = (params or {}).get("id")
                if status_filter and self.job["status"] != str(status_filter).split(".", 1)[1]:
                    return []
                if id_filter and self.job["id"] != str(id_filter).split(".", 1)[1]:
                    return []
                return [dict(self.job)]
            assert method == "PATCH"
            assert path.startswith("/saas_quote_jobs?")
            if not self._matches(self._filters(path)):
                return []
            if (data or {}).get("status") == "completed":
                self.completion_attempts += 1
                if self.completion_mode == "exception_after_commit":
                    self.completion_mode = "ok"
                    self.job.update(data or {})
                    raise RuntimeError("completion response lost")
                if self.completion_mode == "exception":
                    self.completion_mode = "ok"
                    raise RuntimeError("completion unavailable")
                if self.completion_mode == "empty":
                    self.completion_mode = "ok"
                    return []
            if set(data or {}) == {"lease_expires_at", "updated_at"}:
                self.heartbeat_count += 1
            self.job.update(data or {})
            return [dict(self.job)]

    def storage_download(self, object_path, destination):
        Path(destination).write_bytes(self.objects[object_path])

    def storage_upload(self, object_path, source):
        self.uploads.append(object_path)
        self.objects[object_path] = Path(source).read_bytes()

    def storage_delete(self, object_path):
        self.deletes.append(object_path)
        self.objects.pop(object_path, None)


@pytest.mark.parametrize("completion_mode", ["exception", "empty"])
def test_completion_cas_failure_retains_input_and_allows_retry(monkeypatch, completion_mode):
    client = FencedWorkerClient(completion_mode=completion_mode)
    monkeypatch.setattr(
        quote_worker,
        "_run_generator",
        lambda _job, _input, output: output.write_bytes(b"output"),
    )
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 3600)

    with pytest.raises((RuntimeError, quote_worker.WorkerLeaseLost)):
        quote_worker.process_job(client, dict(client.job))

    assert client.job["status"] == "failed"
    assert client.job["input_path"] in client.objects
    first_output = client.uploads[0]
    assert "/attempts/" in first_output and first_output.endswith("/output.xlsx")
    assert client.deletes == [first_output]
    assert first_output not in client.objects

    client.job.update(
        status="queued", attempt_token=None, lease_expires_at=None,
        error_message=None, output_path=None, completed_at=None,
    )
    completed = quote_worker.process_job(client, dict(client.job))

    assert completed and completed[0]["status"] == "completed"
    assert client.job["input_path"] is None
    assert client.deletes == [first_output, "users/7/jobs/job-fenced/input.xlsx"]
    assert len(client.uploads) == 2
    assert client.uploads[0] != client.uploads[1]


def test_ambiguous_completion_retains_persisted_winning_output(monkeypatch):
    client = FencedWorkerClient(completion_mode="exception_after_commit")
    monkeypatch.setattr(
        quote_worker,
        "_run_generator",
        lambda _job, _input, output: output.write_bytes(b"winner"),
    )
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 3600)

    with pytest.raises(quote_worker.WorkerLeaseLost):
        quote_worker.process_job(client, dict(client.job))

    output_path = client.uploads[0]
    assert client.job["status"] == "completed"
    assert client.job["output_path"] == output_path
    assert output_path in client.objects
    assert output_path not in client.deletes
    assert client.job["input_path"] in client.objects


def test_lost_attempt_after_upload_deletes_only_its_orphan(monkeypatch):
    client = FencedWorkerClient()
    original_upload = client.storage_upload

    def lose_after_upload(object_path, source):
        original_upload(object_path, source)
        client.job.update(
            status="queued", attempt_token=None, lease_expires_at=None,
            output_path=None, metadata={"winner": "not-started"},
        )

    monkeypatch.setattr(client, "storage_upload", lose_after_upload)
    monkeypatch.setattr(
        quote_worker,
        "_run_generator",
        lambda _job, _input, output: output.write_bytes(b"orphan"),
    )
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 3600)

    with pytest.raises(quote_worker.WorkerLeaseLost):
        quote_worker.process_job(client, dict(client.job))

    orphan_path = client.uploads[0]
    assert client.job["status"] == "queued"
    assert client.job["output_path"] is None
    assert client.job["metadata"] == {"winner": "not-started"}
    assert client.deletes == [orphan_path]
    assert orphan_path not in client.objects
    assert "users/7/jobs/job-fenced/input.xlsx" in client.objects


def test_stale_worker_loses_lease_without_clobbering_recovery_attempt(monkeypatch):
    client = FencedWorkerClient()
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 3600)
    generator_calls = []

    def interleaved_generator(_job, _input, output):
        generator_calls.append(_job["attempt_token"])
        if len(generator_calls) == 1:
            client.job["lease_expires_at"] = "2000-01-01T00:00:00Z"
            assert quote_worker.recover_stale_jobs(client) == 1
            recovered = dict(client.job)
            assert recovered["status"] == "queued"
            quote_worker.process_job(client, recovered)
            output.write_bytes(b"stale-output")
            return
        output.write_bytes(b"winning-output")

    monkeypatch.setattr(quote_worker, "_run_generator", interleaved_generator)

    with pytest.raises(quote_worker.WorkerLeaseLost):
        quote_worker.process_job(client, dict(client.job))

    assert len(generator_calls) == 2
    assert generator_calls[0] != generator_calls[1]
    assert client.job["status"] == "completed"
    assert client.job["attempt_token"] == generator_calls[1]
    assert client.job["output_path"] == client.uploads[0]
    assert generator_calls[1] in client.job["output_path"]
    assert generator_calls[0] not in client.job["output_path"]
    assert client.deletes == ["users/7/jobs/job-fenced/input.xlsx"]


def test_long_generator_heartbeats_current_attempt(monkeypatch):
    client = FencedWorkerClient()
    entered = threading.Event()
    release = threading.Event()
    result = []

    def slow_generator(_job, _input, output):
        entered.set()
        assert release.wait(5)
        output.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_run_generator", slow_generator)
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 0.01)
    thread = threading.Thread(
        target=lambda: result.append(quote_worker.process_job(client, dict(client.job))),
        daemon=True,
    )
    thread.start()
    assert entered.wait(5)
    deadline = time.time() + 5
    while client.heartbeat_count < 1 and time.time() < deadline:
        time.sleep(0.01)
    release.set()
    thread.join(5)

    assert not thread.is_alive()
    assert client.heartbeat_count >= 1
    assert result and result[0][0]["status"] == "completed"


def test_real_mixed_conversion_and_generator_preserve_pricing_metadata_across_heartbeat(
    monkeypatch,
):
    client = FencedWorkerClient()
    payload = _valid_mixed_worker_payload()
    client.job.update(
        input_path="users/7/jobs/job-fenced/input.json",
        metadata={
            "source_type": "mixed_catalog_cart",
            "input_extension": ".json",
            "catalog_source_hashes": {"tarkett": "a" * 64},
            "cotizacion": "COT-HEARTBEAT",
            "proyecto": "Heartbeat mixto",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "image_provider": "pillow",
        },
    )
    client.objects = {client.job["input_path"]: json.dumps(payload).encode("utf-8")}
    real_run_generator = quote_worker._run_generator
    worker_template = (
        Path("mobiliti_saas/worker/templates/Formato Cotizacion 2026 GDL.xlsx").resolve()
    )
    seen = {}

    def slow_real_generator(job, input_path, output_path):
        heartbeat_before = client.heartbeat_count
        deadline = time.time() + 5
        while client.heartbeat_count <= heartbeat_before and time.time() < deadline:
            time.sleep(0.01)
        assert client.heartbeat_count > heartbeat_before
        seen.update(input_name=input_path.name, metadata=json.loads(json.dumps(job["metadata"])))
        return real_run_generator(job, input_path, output_path)

    monkeypatch.setattr(quote_worker, "_run_generator", slow_real_generator)
    monkeypatch.setattr(quote_worker, "_template_path", lambda: str(worker_template))
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(quote_worker, "QUOTE_ENGINE", "python")

    completed = quote_worker.process_job(client, dict(client.job))

    metadata = completed[0]["metadata"]
    assert seen["input_name"] == "quotation_from_mixed_catalog.xlsx"
    for current in (seen["metadata"], metadata):
        assert current["mixed_catalog_converted"] is True
        assert current["catalog_price_mode"] == "mixed_catalog_converted"
        assert current["rate_summary"] == payload["rate_summary"]
        assert current["auto_electrification_rate"] == payload["auto_electrification_rate"]
        assert current["catalog_source_hashes"] == {"tarkett": "a" * 64}
        assert current["quote_currency"] == "EUR"
        assert current["descuento"] == 0


def test_postgres_client_threads_attempt_and_lease_filters_into_update(monkeypatch):
    client = quote_worker.PostgresClient.__new__(quote_worker.PostgresClient)
    seen = {}

    def update(data, where_sql, where_params):
        seen.update(data=data, where_sql=where_sql, where_params=where_params)
        return [{"id": "job-1", **data}]

    monkeypatch.setattr(client, "_update_jobs", update)
    rows = client.rest(
        "PATCH",
        "/saas_quote_jobs?id=eq.job-1&status=eq.processing"
        "&attempt_token=eq.11111111-1111-4111-8111-111111111111"
        "&lease_expires_at=lt.2026-07-19T00:00:00Z",
        data={"status": "queued"},
    )

    assert rows == [{"id": "job-1", "status": "queued"}]
    assert seen["where_sql"] == (
        "id = %s AND status = %s AND attempt_token = %s AND lease_expires_at < %s"
    )
    assert seen["where_params"] == (
        "job-1", "processing", "11111111-1111-4111-8111-111111111111",
        "2026-07-19T00:00:00Z",
    )


def test_local_dev_client_rejects_patch_from_lost_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(quote_worker, "DEV_STORE_DIR", tmp_path)
    client = quote_worker.LocalDevClient()
    client.db_path.parent.mkdir(parents=True, exist_ok=True)
    client.db_path.write_text(json.dumps({"quote_jobs": [{
        "id": "job-1", "status": "processing", "attempt_token": "current-attempt",
        "lease_expires_at": "2026-07-19T01:00:00Z", "updated_at": "2026-07-19T00:00:00Z",
    }]}), encoding="utf-8")

    lost = client.rest(
        "PATCH",
        "/saas_quote_jobs?id=eq.job-1&status=eq.processing&attempt_token=eq.stale-attempt",
        data={"status": "failed"},
    )
    current = client.rest(
        "PATCH",
        "/saas_quote_jobs?id=eq.job-1&status=eq.processing&attempt_token=eq.current-attempt",
        data={"status": "completed"},
    )

    assert lost == []
    assert current[0]["status"] == "completed"


def test_local_dev_client_serializes_heartbeat_and_progress_read_modify_write(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(quote_worker, "DEV_STORE_DIR", tmp_path)
    client = quote_worker.LocalDevClient()
    client.db_path.parent.mkdir(parents=True, exist_ok=True)
    client.db_path.write_text(json.dumps({"quote_jobs": [{
        "id": "job-1", "status": "processing", "attempt_token": "attempt-1",
        "metadata": {"source_type": "mixed_catalog_cart"},
        "lease_expires_at": "2026-07-19T01:00:00Z", "updated_at": "2026-07-19T00:00:00Z",
    }]}), encoding="utf-8")
    original_load = client._load
    original_save = client._save
    first_save_entered = threading.Event()
    allow_first_save = threading.Event()
    second_load_entered = threading.Event()
    load_count = 0
    save_count = 0

    def controlled_load():
        nonlocal load_count
        load_count += 1
        if load_count == 2:
            second_load_entered.set()
        return original_load()

    def controlled_save(data):
        nonlocal save_count
        save_count += 1
        if save_count == 1:
            first_save_entered.set()
            assert allow_first_save.wait(5)
        return original_save(data)

    monkeypatch.setattr(client, "_load", controlled_load)
    monkeypatch.setattr(client, "_save", controlled_save)
    path = (
        "/saas_quote_jobs?id=eq.job-1&status=eq.processing&attempt_token=eq.attempt-1"
    )
    progress = threading.Thread(
        target=lambda: client.rest("PATCH", path, data={"metadata": {
            "source_type": "mixed_catalog_cart", "progress_percent": 55,
        }}),
        daemon=True,
    )
    heartbeat = threading.Thread(
        target=lambda: client.rest("PATCH", path, data={
            "lease_expires_at": "2026-07-19T02:00:00Z",
        }),
        daemon=True,
    )
    progress.start()
    assert first_save_entered.wait(5)
    heartbeat.start()
    assert not second_load_entered.wait(0.2)
    allow_first_save.set()
    progress.join(5)
    heartbeat.join(5)

    assert not progress.is_alive() and not heartbeat.is_alive()
    stored = original_load()["quote_jobs"][0]
    assert stored["metadata"]["progress_percent"] == 55
    assert stored["lease_expires_at"] == "2026-07-19T02:00:00Z"
