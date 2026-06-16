import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "worker"))

import quote_worker


def test_default_template_resolves_existing_template():
    template = quote_worker._default_template()

    assert template.exists()
    assert template.name.startswith("Formato")


class FakeClient:
    def __init__(self):
        self.calls = []

    def rest(self, method, path, params=None, data=None):
        self.calls.append((method, path, data))
        if method == "PATCH" and "status=eq.queued" in path and data and data.get("status") == "processing":
            return [{"id": "job-1", "usuario_id": 7, "input_path": "users/7/jobs/job-1/input.xlsx", **data}]
        if method == "PATCH" and data and data.get("status") == "completed":
            return [{"id": "job-1", **data}]
        return []

    def storage_download(self, object_path, dest):
        Path(dest).write_bytes(b"input")
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
