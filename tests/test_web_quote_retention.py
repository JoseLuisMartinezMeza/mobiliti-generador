import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient


os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")


def _load_api():
    module_path = Path(__file__).resolve().parents[1] / "mobiliti_saas" / "web" / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("mobiliti_web_api_index_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


index = _load_api()


def _client():
    return TestClient(index.app)


def _token(user_id=7, email="cliente@example.com"):
    return index.create_access_token({"sub": str(user_id), "email": email})


def _auth_headers(user_id=7, email="cliente@example.com"):
    return {"Authorization": f"Bearer {_token(user_id, email)}"}


def _mock_user(monkeypatch, user_id=7, active=True, email="cliente@example.com"):
    monkeypatch.setattr(
        index,
        "db_get_usuario_by_id",
        lambda requested_id: {
            "id": requested_id,
            "email": email,
            "nombre": "Cliente",
            "empresa": "Mobiliti",
            "es_admin": False,
            "activo": active,
        },
    )


def _job(job_id, usuario_id=7, created_suffix=1):
    return {
        "id": job_id,
        "usuario_id": usuario_id,
        "status": "completed",
        "input_path": f"users/{usuario_id}/jobs/{job_id}/input.xlsx",
        "output_path": f"users/{usuario_id}/jobs/{job_id}/output.xlsx",
        "metadata": {"cotizacion": f"100-{created_suffix:05d}", "proyecto": f"Proyecto {created_suffix}"},
        "created_at": f"2026-06-{created_suffix:02d}T00:00:00+00:00",
        "updated_at": f"2026-06-{created_suffix:02d}T00:00:00+00:00",
    }


def test_delete_quote_removes_only_owned_job_and_storage(monkeypatch):
    _mock_user(monkeypatch)
    deleted_jobs = []
    deleted_storage = []

    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: _job(job_id))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: deleted_jobs.append(job_id) or _job(job_id), raising=False)
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: deleted_storage.extend([job["input_path"], job["output_path"]]), raising=False)

    resp = _client().delete("/cotizaciones/job-1", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["deleted_id"] == "job-1"
    assert deleted_jobs == ["job-1"]
    assert deleted_storage == ["users/7/jobs/job-1/input.xlsx", "users/7/jobs/job-1/output.xlsx"]


def test_delete_quote_rejects_other_user_job(monkeypatch):
    _mock_user(monkeypatch, user_id=7)
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: _job(job_id, usuario_id=99))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: (_ for _ in ()).throw(AssertionError("must not delete")), raising=False)

    resp = _client().delete("/cotizaciones/job-1", headers=_auth_headers(7))

    assert resp.status_code == 403


def test_list_enforces_15_quote_limit_and_purges_oldest_storage(monkeypatch):
    _mock_user(monkeypatch)
    jobs = [_job(f"job-{i}", created_suffix=i) for i in range(16, 0, -1)]
    deleted_jobs = []
    deleted_storage = []

    def fake_list(usuario_id):
        return [job for job in jobs if job["id"] not in deleted_jobs]

    monkeypatch.setattr(index, "db_list_quote_jobs", fake_list)
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: deleted_jobs.append(job_id) or _job(job_id), raising=False)
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: deleted_storage.extend([job["input_path"], job["output_path"]]), raising=False)

    resp = _client().get("/cotizaciones", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["cotizaciones"]) == 15
    assert deleted_jobs == ["job-1"]
    assert "users/7/jobs/job-1/input.xlsx" in deleted_storage
    assert "users/7/jobs/job-1/output.xlsx" in deleted_storage
