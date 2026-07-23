import os
import subprocess
import sys
import hashlib
import json
import threading
import time
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "worker"))

import quote_worker
import online_quote_generator
import render_web_worker
from mobiliti_saas.quote_engine.mixed_catalog import build_mixed_catalog_cart_payload
from mobiliti_saas.quote_engine.project_quote import project_context
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem
from project_fixtures import valid_project_payload
from quotation_import_fixtures import write_import_fixture


PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _write_minimal_parser_xlsx(path):
    workbook = Workbook()
    workbook.active.title = "Quotation"
    workbook.save(path)
    workbook.close()


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
        _write_minimal_parser_xlsx(output_xlsx)

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
        _write_minimal_parser_xlsx(output_xlsx)

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
        "line_id": "legacy-1",
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
        "imported_source": None,
        "sections": [
            {
                "id": "section-1",
                "title": "Recepción",
                "line_ids": ["legacy-1"],
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
        "project_context": None,
    }


def _project_mixed_worker_payload(*, include_complement=False):
    payload = _valid_mixed_worker_payload()
    project = valid_project_payload()
    if not include_complement:
        project["lines"] = project["lines"][:1]
    principal_id = project["lines"][0]["line_id"]
    payload["groups"][0]["items"][0]["line_id"] = principal_id
    payload["sections"] = [{
        "id": "section-1",
        "title": "Recepción",
        "line_ids": [principal_id],
    }]
    payload["project_context"] = project_context(project, PROJECT_ID, 3)
    return payload


def test_worker_passes_validated_project_context_to_official_engine(monkeypatch):
    client = FakeClient()
    client.claim_input_path = "users/7/jobs/job-1/input.json"
    payload = _project_mixed_worker_payload()
    client.input_content = json.dumps(payload).encode("utf-8")
    seen = {}

    def fake_convert(_source, output, converted_payload):
        converted_payload["project_context"]["compositions"].clear()
        _write_minimal_parser_xlsx(output)

    def fake_generator(job, _input_path, output_path):
        seen["metadata"] = deepcopy(job["metadata"])
        output_path.write_bytes(b"output")

    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        fake_convert,
    )
    monkeypatch.setattr(quote_worker, "_run_generator", fake_generator)

    quote_worker.process_job(client, {
        "id": "job-1",
        "usuario_id": 7,
        "input_path": client.claim_input_path,
        "metadata": {
            "source_type": "mixed_catalog_cart",
            "input_extension": ".json",
            "project_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "project_revision": 99,
            "project_payload_hash": "0" * 64,
        },
    })

    expected = payload["project_context"]
    assert seen["metadata"]["project_context"] == expected
    assert seen["metadata"]["project_context"] is not expected
    assert seen["metadata"]["project_id"] == expected["project_id"]
    assert seen["metadata"]["project_revision"] == expected["project_revision"]
    assert seen["metadata"]["project_payload_hash"] == expected["project_payload_hash"]


def test_worker_rejects_project_component_absent_from_resolved_mixed_payload(
    monkeypatch,
    tmp_path,
):
    payload = _project_mixed_worker_payload(include_complement=True)
    source = tmp_path / "project-missing-component.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        lambda *_args: pytest.fail("converter should not run"),
    )

    with pytest.raises(
        RuntimeError,
        match="Contexto de Proyecto invalido",
    ):
        quote_worker._prepare_generator_input(
            {
                "id": "job-project",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-project/input.json",
                "metadata": {
                    "source_type": "mixed_catalog_cart",
                    "input_extension": ".json",
                    "project_id": PROJECT_ID,
                    "project_revision": 3,
                },
            },
            source,
            tmp_path,
        )


def _valid_imported_worker_payload(tmp_path):
    source = write_import_fixture(tmp_path / "worker-import.xlsx")
    manifest, _images = build_import_manifest(
        source.read_bytes(),
        import_id="7b1d6d42-236a-4bc1-9aa8-8d9db793c30b",
        original_filename=source.name,
    )
    imported_key = f"import:{manifest['import_id']}:11"
    payload = build_mixed_catalog_cart_payload(
        [],
        catalogs={},
        rate_rows=[{
            "currency": "USD",
            "effective_date": "2026-07-21",
            "mxn_per_unit": "18.500000",
            "retrieved_at": "2026-07-21T00:00:00Z",
        }],
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": "section-1",
            "title": "Recepción",
            "item_keys": [imported_key],
        }],
        imported_source={
            "manifest": manifest,
            "items": [{
                "kind": "imported",
                "import_id": manifest["import_id"],
                "source_row": 11,
                "source_currency": "USD",
                "quantity": "2",
                "overrides": {
                    "name": "Alien Task Chair revisada",
                    "description": "Silla operativa revisada",
                    "dimension": "630 x 565 x 1000 mm",
                    "unit_price": "82.00",
                    "provider": "Sunon",
                },
            }],
            "source_currency": "USD",
        },
    )
    payload["imported_source"].update(
        storage_path=(
            "users/7/jobs/11111111-1111-4111-8111-111111111111/"
            "import-source.xlsx"
        ),
        storage_provider="supabase",
    )
    return payload, source


def _valid_mixed_imported_worker_payload(tmp_path):
    source = write_import_fixture(tmp_path / "worker-mixed-import.xlsx")
    manifest, _images = build_import_manifest(
        source.read_bytes(),
        import_id="7b1d6d42-236a-4bc1-9aa8-8d9db793c30b",
        original_filename=source.name,
    )
    imported_key = f"import:{manifest['import_id']}:11"
    tarkett = TarkettCatalogItem(
        code="T-1",
        name="Piso Tarkett",
        unit="M2",
        available_quantity=Decimal("10"),
        unit_price=Decimal("100"),
        price_source="catalog",
    )
    payload = build_mixed_catalog_cart_payload(
        [{"catalog": "tarkett", "code": "T-1", "quantity": "1"}],
        catalogs={
            "tarkett": {
                "source_hash": "a" * 64,
                "items": [tarkett],
                "by_code": {tarkett.code: tarkett},
            }
        },
        rate_rows=[{
            "currency": "USD",
            "effective_date": date.today().isoformat(),
            "mxn_per_unit": "18.500000",
            "retrieved_at": f"{date.today().isoformat()}T00:00:00Z",
        }],
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": "section-1",
            "title": "Recepción",
            "item_keys": ["tarkett:T-1", imported_key],
        }],
        imported_source={
            "manifest": manifest,
            "items": [{
                "kind": "imported",
                "import_id": manifest["import_id"],
                "source_row": 11,
                "source_currency": "USD",
                "quantity": "2",
                "overrides": {
                    "name": "Alien Task Chair revisada",
                    "description": "Silla operativa revisada",
                    "dimension": "630 x 565 x 1000 mm",
                    "unit_price": "82.00",
                    "provider": "Sunon",
                },
            }],
            "source_currency": "USD",
        },
        today=date.today(),
    )
    storage_path = (
        "users/7/jobs/11111111-1111-4111-8111-111111111111/"
        "import-source.xlsx"
    )
    payload["imported_source"].update(
        storage_path=storage_path,
        storage_provider="supabase",
    )
    return payload, source


def _imported_worker_job(payload=None):
    metadata = {
        "source_type": "mixed_catalog_cart",
        "input_extension": ".json",
        "storage_provider": "supabase",
    }
    if payload is not None:
        imported = payload["imported_source"]
        metadata.update(
            mixed_item_count=payload["item_count"],
            mixed_section_count=len(payload["sections"]),
            catalog_item_counts={
                group["catalog"]: len(group["items"])
                for group in payload["groups"]
            },
            catalog_source_hashes={
                group["catalog"]: group["catalog_source_hash"]
                for group in payload["groups"]
            },
            quote_currency=payload["quote_currency"],
            import_source={
                "import_id": imported["import_id"],
                "original_filename": imported["original_filename"],
                "source_hash": imported["source_hash"],
            },
            import_item_count=len(imported["items"]),
            import_source_path=imported.get(
                "storage_path", imported.get("source_path")
            ),
        )
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "usuario_id": 7,
        "metadata": metadata,
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
        _write_minimal_parser_xlsx(output_xlsx)

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
                "descuento": 40,
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
    assert seen["metadata"]["descuento"] == 40
    assert seen["metadata"]["rate_summary"] == payload["rate_summary"]
    assert seen["metadata"]["auto_electrification_rate"] == payload["auto_electrification_rate"]


def test_prepared_generator_input_preserves_plain_provider_source(tmp_path):
    source = write_import_fixture(tmp_path / "provider-quotation.xlsx")
    prepared = quote_worker._prepare_generator_input(
        {
            "id": "job-provider",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-provider/input.xlsx",
            "metadata": {
                "input_extension": ".xlsx",
                "file_size": source.stat().st_size,
            },
        },
        source,
        tmp_path,
    )

    assert isinstance(prepared, quote_worker.PreparedGeneratorInput)
    assert prepared.parser_source == source
    assert prepared.original_quotation == source
    assert prepared.quotation_data == ()


def test_prepared_generator_input_catalog_only_uses_none_and_all_canonical_rows(
    monkeypatch,
    tmp_path,
):
    payload = _valid_mixed_worker_payload()
    source = tmp_path / "catalog-only.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        lambda _source, output, _payload: _write_minimal_parser_xlsx(output),
    )

    prepared = quote_worker._prepare_generator_input(
        {
            "id": "job-catalog-only",
            "usuario_id": 7,
            "input_path": "users/7/jobs/job-catalog-only/input.json",
            "metadata": {
                "source_type": "mixed_catalog_cart",
                "input_extension": ".json",
                "mixed_item_count": 1,
                "mixed_section_count": 1,
                "catalog_item_counts": {"tarkett": 1},
                "catalog_source_hashes": {"tarkett": "a" * 64},
                "quote_currency": "EUR",
            },
        },
        source,
        tmp_path,
    )

    assert isinstance(prepared, quote_worker.PreparedGeneratorInput)
    assert prepared.parser_source.name == "quotation_from_mixed_catalog.xlsx"
    assert prepared.original_quotation is None
    assert len(prepared.quotation_data) == payload["item_count"] == 1
    row = prepared.quotation_data[0]
    assert (row.item_key, row.section_id, row.section_title, row.position) == (
        "legacy-1",
        "section-1",
        "Recepción",
        1,
    )
    assert row.source_hash == "a" * 64
    assert len(row.row_hash) == 64


def test_prepared_generator_input_rejects_non_xlsx_converter_output(
    monkeypatch,
    tmp_path,
):
    payload = _valid_mixed_worker_payload()
    source = tmp_path / "catalog-only.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    converter_calls = []

    def fake_convert(_source, output, _payload):
        converter_calls.append(output)
        output.write_bytes(b"not-an-xlsx")

    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        fake_convert,
    )

    with pytest.raises(RuntimeError, match="XLSX"):
        quote_worker._prepare_generator_input(
            {
                "id": "job-invalid-synthetic",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-invalid-synthetic/input.json",
                "metadata": {
                    "source_type": "mixed_catalog_cart",
                    "input_extension": ".json",
                    "mixed_item_count": 1,
                    "mixed_section_count": 1,
                    "catalog_item_counts": {"tarkett": 1},
                    "catalog_source_hashes": {"tarkett": "a" * 64},
                    "quote_currency": "EUR",
                },
            },
            source,
            tmp_path,
        )

    assert len(converter_calls) == 1


def test_worker_passes_original_import_and_all_canonical_rows_to_generator(
    monkeypatch,
    tmp_path,
):
    payload, imported_source = _valid_mixed_imported_worker_payload(tmp_path)
    job = _imported_worker_job(payload)
    job.update(
        status="queued",
        input_path=(
            "users/7/jobs/11111111-1111-4111-8111-111111111111/input.json"
        ),
        output_path=None,
        attempt_token=None,
        lease_expires_at=None,
        updated_at="2026-07-22T00:00:00Z",
    )
    client = FencedWorkerClient()
    client.job = dict(job)
    client.objects = {
        job["input_path"]: json.dumps(payload).encode("utf-8"),
        payload["imported_source"]["storage_path"]: imported_source.read_bytes(),
    }
    downloads = []

    def storage_download_from_provider(object_path, destination, provider):
        downloads.append((object_path, provider))
        Path(destination).write_bytes(client.objects[object_path])

    def storage_delete_from_provider(object_path, provider):
        client.deletes.append(object_path)
        client.objects.pop(object_path, None)

    client.storage_download_from_provider = storage_download_from_provider
    client.storage_delete_from_provider = storage_delete_from_provider
    captured = {}

    def capture_generator(current_job, prepared, output_path):
        captured["prepared_type"] = type(prepared)
        captured["parser_name"] = prepared.parser_source.name
        captured["original_name"] = prepared.original_quotation.name
        captured["original_hash"] = hashlib.sha256(
            prepared.original_quotation.read_bytes()
        ).hexdigest()
        captured["rows"] = prepared.quotation_data
        captured["metadata"] = dict(current_job["metadata"])
        output_path.write_bytes(b"output")

    monkeypatch.setattr(quote_worker, "_run_generator", capture_generator)
    monkeypatch.setattr(quote_worker, "WORKER_HEARTBEAT_SECONDS", 3600)

    quote_worker.process_job(client, dict(job))

    assert captured["prepared_type"] is quote_worker.PreparedGeneratorInput
    assert captured["parser_name"] == "quotation_from_mixed_catalog.xlsx"
    assert captured["original_name"] == "import-source.xlsx"
    assert captured["original_hash"] == payload["imported_source"]["source_hash"]
    rows = captured["rows"]
    assert len(rows) == payload["item_count"] == 2
    assert [row.item_key for row in rows] == payload["sections"][0]["line_ids"]
    assert [(row.section_id, row.section_title, row.position) for row in rows] == [
        ("section-1", "Recepción", 1),
        ("section-1", "Recepción", 2),
    ]
    assert [row.source_hash for row in rows] == [
        "a" * 64,
        payload["imported_source"]["source_hash"],
    ]
    assert all(len(row.row_hash) == 64 for row in rows)
    import_path = payload["imported_source"]["storage_path"]
    assert downloads.count((import_path, "supabase")) == 1


def test_online_wrapper_forwards_explicit_original_and_canonical_rows(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "parser.xlsx"
    original = tmp_path / "original.xlsx"
    output = tmp_path / "output.xlsx"
    rows = (object(), object())
    captured = {}

    def fake_generate_quote(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return output

    monkeypatch.setattr(online_quote_generator, "generate_quote", fake_generate_quote)

    result = online_quote_generator.generate_online_quote(
        source,
        output,
        {"proyecto": "Handoff"},
        tmp_path / "template.xlsx",
        original_quotation_path=original,
        quotation_data_rows=rows,
    )

    assert result == output
    assert captured["kwargs"]["original_quotation_path"] == original
    assert captured["kwargs"]["quotation_data_rows"] is rows


@pytest.mark.parametrize("storage_contract", ("canonical", "legacy"))
def test_worker_downloads_validated_import_source_and_passes_verified_path_to_builder(
    monkeypatch, tmp_path, storage_contract
):
    payload, source = _valid_imported_worker_payload(tmp_path)
    expected_storage_path = payload["imported_source"]["storage_path"]
    if storage_contract == "legacy":
        payload["imported_source"]["source_path"] = payload["imported_source"].pop(
            "storage_path"
        )
        payload["imported_source"].pop("storage_provider")
    local_input = tmp_path / "input.json"
    local_input.write_text(json.dumps(payload), encoding="utf-8")
    seen = {"downloads": [], "conversions": []}

    class Client:
        def storage_download_from_provider(self, storage_path, destination, provider):
            seen["downloads"].append((storage_path, provider))
            destination.write_bytes(source.read_bytes())

    def fake_convert(
        source_json,
        output_xlsx,
        cart_payload,
        *,
        imported_source_path,
    ):
        seen["conversions"].append(
            (
                source_json.name,
                imported_source_path.name,
                hashlib.sha256(imported_source_path.read_bytes()).hexdigest(),
            )
        )
        _write_minimal_parser_xlsx(output_xlsx)

    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        fake_convert,
    )
    output = quote_worker._prepare_generator_input(
        _imported_worker_job(),
        local_input,
        tmp_path,
        client=Client(),
    )

    assert output.name == "quotation_from_mixed_catalog.xlsx"
    assert seen["downloads"] == [(
        expected_storage_path,
        "supabase",
    )]
    assert seen["conversions"] == [(
        "input.json",
        "import-source.xlsx",
        payload["imported_source"]["source_hash"],
    )]


def test_completed_mixed_job_cleans_consumed_import_source_only_after_success():
    final_job_id = "11111111-1111-4111-8111-111111111111"
    import_id = "22222222-2222-4222-8222-222222222222"
    prefix = f"users/7/jobs/{import_id}/"
    source = {
        "id": import_id,
        "usuario_id": 7,
        "status": "failed",
        "input_path": f"{prefix}input.xlsx",
        "metadata": {
            "input_storage_provider": "supabase",
            "import_consumed_by_job_id": final_job_id,
            "import_manifest_path": f"{prefix}preview/hash/manifest.json",
            "import_preview_paths": {"9": f"{prefix}preview/hash/row-9.png"},
        },
    }
    deleted = []
    patched = []

    class Client:
        def rest(self, method, path, params=None, data=None):
            if method == "GET":
                assert params == {"id": f"eq.{import_id}", "select": "*", "limit": "2"}
                return [source]
            patched.append((path, data))
            return [{**source, **(data or {})}]

        def storage_delete_from_provider(self, path, provider):
            deleted.append((path, provider))

    cleaned = quote_worker._cleanup_completed_import_source(
        Client(),
        {
            "id": final_job_id,
            "usuario_id": 7,
            "metadata": {"import_source": {"import_id": import_id}},
        },
    )

    assert cleaned is True
    assert deleted == [
        (f"{prefix}input.xlsx", "supabase"),
        (f"{prefix}preview/hash/manifest.json", "supabase"),
        (f"{prefix}preview/hash/row-9.png", "supabase"),
    ]
    assert len(patched) == 1
    patch_path, updates = patched[0]
    assert f"id=eq.{import_id}" in patch_path and "status=eq.failed" in patch_path
    assert updates["input_path"] is None
    assert "import_manifest_path" not in updates["metadata"]
    assert "import_preview_paths" not in updates["metadata"]
    assert updates["metadata"]["import_consumed_by_job_id"] == final_job_id


def test_worker_converter_rejects_imported_row_remap_without_leaving_output(tmp_path):
    payload, source = _valid_imported_worker_payload(tmp_path)
    manifest, _images = build_import_manifest(
        source.read_bytes(),
        import_id=payload["imported_source"]["import_id"],
        original_filename=payload["imported_source"]["original_filename"],
    )
    authoritative_row_9 = next(
        item for item in manifest["items"] if item["source_row"] == 9
    )
    remapped_key = f"import:{manifest['import_id']}:9"
    imported_line = payload["imported_source"]["items"][0]
    imported_line.update(
        source_row=9,
        canonical_key=remapped_key,
        source_reference=(
            f"{payload['imported_source']['original_filename']}#Quotation!9"
        ),
        row_hash=authoritative_row_9["row_hash"],
    )
    payload["sections"][0]["line_ids"] = [imported_line["line_id"]]
    local_input = tmp_path / "input.json"
    local_input.write_text(json.dumps(payload), encoding="utf-8")

    class Client:
        def storage_download_from_provider(self, storage_path, destination, provider):
            destination.write_bytes(source.read_bytes())

    with pytest.raises(ValueError, match="fila importada"):
        quote_worker._prepare_generator_input(
            _imported_worker_job(),
            local_input,
            tmp_path,
            client=Client(),
        )

    assert not (tmp_path / "quotation_from_mixed_catalog.xlsx").exists()


@pytest.mark.parametrize("failure", ("changed_hash", "download_failure", "oversized"))
def test_worker_does_not_build_output_when_import_source_cannot_be_verified(
    monkeypatch, tmp_path, failure
):
    payload, source = _valid_imported_worker_payload(tmp_path)
    local_input = tmp_path / "input.json"
    local_input.write_text(json.dumps(payload), encoding="utf-8")
    conversions = []

    class Client:
        def storage_download_from_provider(self, storage_path, destination, provider):
            if failure == "download_failure":
                raise RuntimeError("storage unavailable")
            data = source.read_bytes()
            destination.write_bytes(data + (b"changed" if failure == "changed_hash" else b""))

    if failure == "oversized":
        monkeypatch.setattr(quote_worker, "MAX_IMPORTED_SOURCE_BYTES", 1)
    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        lambda *_args, **_kwargs: conversions.append("convert"),
    )

    with pytest.raises(RuntimeError):
        quote_worker._prepare_generator_input(
            _imported_worker_job(),
            local_input,
            tmp_path,
            client=Client(),
        )

    assert conversions == []
    assert not (tmp_path / "quotation_from_mixed_catalog.xlsx").exists()


@pytest.mark.parametrize(
    "mutation",
    (
        lambda imported: imported.update(storage_provider="browser"),
        lambda imported: imported.update(storage_path="C:/client/source.xlsx"),
        lambda imported: imported.update(storage_path="users/7/jobs/not-a-uuid/import-source.xlsx"),
    ),
)
def test_worker_rejects_import_storage_reference_before_download(
    monkeypatch, tmp_path, mutation
):
    payload, _source = _valid_imported_worker_payload(tmp_path)
    mutation(payload["imported_source"])
    local_input = tmp_path / "input.json"
    local_input.write_text(json.dumps(payload), encoding="utf-8")
    downloads = []

    class Client:
        def storage_download_from_provider(self, *args):
            downloads.append(args)

    monkeypatch.setattr(
        quote_worker,
        "_convert_mixed_catalog_cart_to_quotation",
        lambda *_args, **_kwargs: pytest.fail("converter must not run"),
    )
    with pytest.raises(RuntimeError, match="Payload de cotizacion mixta invalido"):
        quote_worker._prepare_generator_input(
            _imported_worker_job(),
            local_input,
            tmp_path,
            client=Client(),
        )
    assert downloads == []


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
        _write_minimal_parser_xlsx(output_xlsx)

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
        _write_minimal_parser_xlsx(output_xlsx)

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
        lambda source_json, output_xlsx, payload: _write_minimal_parser_xlsx(output_xlsx),
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
        _write_minimal_parser_xlsx(output_xlsx)

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


def test_claimed_job_with_foreign_input_path_records_failure_and_releases_lease(
    monkeypatch,
):
    client = FakeClient()
    client.claim_input_path = "users/99/jobs/job-1/input.xlsx"
    generator_calls = []
    monkeypatch.setattr(
        quote_worker,
        "_run_generator",
        lambda *_args: generator_calls.append("generate"),
    )

    with pytest.raises(RuntimeError, match="Ruta de entrada no corresponde al job"):
        quote_worker.process_job(
            client,
            {
                "id": "job-1",
                "usuario_id": 7,
                "input_path": "users/7/jobs/job-1/input.xlsx",
                "metadata": {},
            },
        )

    statuses = [
        data["status"]
        for _method, _path, data in client.calls
        if isinstance(data, dict) and "status" in data
    ]
    assert statuses == ["processing", "failed"]
    failed_payload = next(
        data
        for _method, _path, data in client.calls
        if isinstance(data, dict) and data.get("status") == "failed"
    )
    assert failed_payload["lease_expires_at"] is None
    assert "Ruta de entrada no corresponde al job" in failed_payload["error_message"]
    assert generator_calls == []
    assert not any(method == "UPLOAD" for method, _path, _data in client.calls)


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
            "descuento": 40,
        },
    )
    client.objects = {client.job["input_path"]: json.dumps(payload).encode("utf-8")}
    real_run_generator = quote_worker._run_generator
    worker_template = (
        Path(
            "mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx"
        ).resolve()
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
        assert current["descuento"] == 40


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


def test_local_dev_client_save_never_exposes_a_truncated_json_snapshot(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(quote_worker, "DEV_STORE_DIR", tmp_path)
    client = quote_worker.LocalDevClient()
    client.db_path.parent.mkdir(parents=True, exist_ok=True)
    initial = {"quote_jobs": [{"id": "before"}]}
    updated = {"quote_jobs": [{"id": "after"}], "payload": "x" * 1000}
    client.db_path.write_text(json.dumps(initial), encoding="utf-8")
    target_was_truncated = threading.Event()
    allow_direct_write_to_finish = threading.Event()
    original_write_text = Path.write_text

    def delayed_direct_write(path, content, *args, **kwargs):
        if path == client.db_path:
            encoding = kwargs.get("encoding", "utf-8")
            with path.open("w", encoding=encoding) as stream:
                stream.write("")
                stream.flush()
                target_was_truncated.set()
                assert allow_direct_write_to_finish.wait(5)
                stream.write(content)
            return len(content)
        return original_write_text(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", delayed_direct_write)
    writer = threading.Thread(target=client._save, args=(updated,))
    writer.start()
    try:
        if target_was_truncated.wait(0.5):
            snapshot = client._load()
        else:
            writer.join(5)
            snapshot = client._load()
    finally:
        allow_direct_write_to_finish.set()
        writer.join(5)

    assert not writer.is_alive()
    assert snapshot in (initial, updated)
    assert json.loads(client.db_path.read_text(encoding="utf-8")) == updated


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
