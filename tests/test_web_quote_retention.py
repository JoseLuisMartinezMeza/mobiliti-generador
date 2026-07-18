import importlib.util
import os
from pathlib import Path
from datetime import datetime, timezone

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


def _mock_user(monkeypatch, user_id=7, active=True, email="cliente@example.com", es_admin=False):
    monkeypatch.setattr(
        index,
        "db_get_usuario_by_id",
        lambda requested_id: {
            "id": requested_id,
            "email": email,
            "nombre": "Cliente",
            "empresa": "Mobiliti",
            "es_admin": es_admin,
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


def test_health_reports_safe_runtime_backend():
    resp = _client().get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_backend"] in {"postgres", "supabase_rest"}
    assert isinstance(body["storage_configured"], bool)


def test_postgres_connect_kwargs_are_pooler_safe():
    sentinel_row_factory = object()

    kwargs = index._pg_connect_kwargs(sentinel_row_factory)

    assert kwargs["row_factory"] is sentinel_row_factory
    assert kwargs["connect_timeout"] == 10
    assert kwargs["prepare_threshold"] is None


def test_postgres_runtime_error_does_not_echo_driver_detail():
    class OperationalError(Exception):
        pass

    try:
        index._raise_pg_runtime_error(OperationalError("host=secret password=secret"))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert message == "Postgres connection/query error: OperationalError"
    assert "secret" not in message


def test_delete_quote_removes_only_owned_job_and_storage(monkeypatch):
    _mock_user(monkeypatch)
    deleted_jobs = []
    deleted_storage = []
    released = []

    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: _job(job_id))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: deleted_jobs.append(job_id) or _job(job_id), raising=False)
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: deleted_storage.extend([job["input_path"], job["output_path"]]), raising=False)
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: released.append(("tarkett", job_id)), raising=False)
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: released.append(("offiho", job_id)), raising=False)
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: released.append(("supplier", job_id)), raising=False)

    resp = _client().delete("/cotizaciones/job-1", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["deleted_id"] == "job-1"
    assert deleted_jobs == ["job-1"]
    assert released == [("tarkett", "job-1"), ("offiho", "job-1"), ("supplier", "job-1")]
    assert deleted_storage == ["users/7/jobs/job-1/input.xlsx", "users/7/jobs/job-1/output.xlsx"]


def test_delete_quote_rejects_other_user_job(monkeypatch):
    _mock_user(monkeypatch, user_id=7)
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: _job(job_id, usuario_id=99))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: (_ for _ in ()).throw(AssertionError("must not delete")), raising=False)

    resp = _client().delete("/cotizaciones/job-1", headers=_auth_headers(7))

    assert resp.status_code == 403


def test_delete_supplier_quote_releases_catalog_reservations(monkeypatch):
    _mock_user(monkeypatch)
    job = _job("supplier-job")
    job["metadata"]["source_type"] = "supplier_cart"
    released = []

    monkeypatch.setattr(index, "db_get_quote_job", lambda _job_id: job)
    monkeypatch.setattr(index, "db_delete_quote_job", lambda _job_id: job)
    monkeypatch.setattr(index, "_delete_quote_storage", lambda _job: None)
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: released.append(("tarkett", job_id)))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: released.append(("offiho", job_id)))
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: released.append(("supplier", job_id)))

    resp = _client().delete("/cotizaciones/supplier-job", headers=_auth_headers())

    assert resp.status_code == 200
    assert released == [
        ("tarkett", "supplier-job"),
        ("offiho", "supplier-job"),
        ("supplier", "supplier-job"),
    ]


def test_list_enforces_3_completed_quote_limit_and_purges_oldest_storage(monkeypatch):
    _mock_user(monkeypatch)
    jobs = [_job(f"job-{i}", created_suffix=i) for i in range(4, 0, -1)]
    deleted_jobs = []
    deleted_storage = []
    deleted_input_paths = []
    released = []

    def fake_list(usuario_id):
        return [job for job in jobs if job["id"] not in deleted_jobs]

    monkeypatch.setattr(index, "db_list_quote_jobs", fake_list)
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: deleted_jobs.append(job_id) or _job(job_id), raising=False)
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: deleted_storage.extend([job["input_path"], job["output_path"]]), raising=False)
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: deleted_input_paths.extend(paths), raising=False)
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: {"id": job_id, **updates}, raising=False)
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: released.append(("tarkett", job_id)), raising=False)
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: released.append(("offiho", job_id)), raising=False)
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: released.append(("supplier", job_id)), raising=False)

    resp = _client().get("/cotizaciones", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["cotizaciones"]) == 3
    assert deleted_jobs == ["job-1"]
    assert released == [("tarkett", "job-1"), ("offiho", "job-1"), ("supplier", "job-1")]
    assert "users/7/jobs/job-1/input.xlsx" in deleted_storage
    assert "users/7/jobs/job-1/output.xlsx" in deleted_storage
    assert "users/7/jobs/job-4/input.xlsx" in deleted_input_paths
    assert "users/7/jobs/job-2/input.xlsx" in deleted_input_paths


def test_admin_storage_retention_defaults_to_dry_run(monkeypatch):
    _mock_user(monkeypatch, es_admin=True)
    jobs = [_job(f"job-{i}", created_suffix=i) for i in range(4, 0, -1)]
    monkeypatch.setattr(index, "db_list_usuarios", lambda: [{"id": 7}], raising=False)
    monkeypatch.setattr(index, "db_list_quote_jobs", lambda usuario_id: jobs, raising=False)
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: (_ for _ in ()).throw(AssertionError("dry-run must not delete jobs")), raising=False)
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: (_ for _ in ()).throw(AssertionError("dry-run must not delete storage")), raising=False)
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: (_ for _ in ()).throw(AssertionError("dry-run must not delete inputs")), raising=False)

    resp = _client().post("/admin/storage-retention", headers=_auth_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["policy"]["max_completed_outputs_per_user"] == 3
    assert body["totals"]["jobs_deleted"] == 1
    assert body["totals"]["completed_inputs_deleted"] == 3
    assert body["totals"]["storage_objects_deleted"] == 0


def test_emergency_storage_retention_requires_token():
    resp = _client().post("/admin/storage-retention-emergency", json={"dry_run": True})

    assert resp.status_code == 403


def test_emergency_storage_retention_dry_run_uses_storage_only(monkeypatch):
    monkeypatch.setattr(index, "QUOTE_RETENTION_TOKEN", "retention-test", raising=False)
    monkeypatch.setattr(
        index,
        "_storage_list_recursive",
        lambda bucket, prefix: [
            {
                "id": "1",
                "_full_name": "users/7/jobs/job-1/output.xlsx",
                "created_at": "2026-06-01T00:00:00+00:00",
                "metadata": {"size": 10 * 1024 * 1024},
            },
            {
                "id": "2",
                "_full_name": "users/7/jobs/job-1/input.xlsx",
                "created_at": "2026-06-01T00:00:00+00:00",
                "metadata": {"size": 2 * 1024 * 1024},
            },
        ],
        raising=False,
    )
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: (_ for _ in ()).throw(AssertionError("dry-run must not delete")), raising=False)

    resp = _client().post(
        "/admin/storage-retention-emergency",
        headers={"x-quote-retention-token": "retention-test"},
        json={"dry_run": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["completed_inputs_deleted"] == 1
    assert body["objects_planned"] == 1
    assert body["estimated_mb"] == 2.0


def test_emergency_storage_retention_explicit_paths_requires_safe_paths(monkeypatch):
    monkeypatch.setattr(index, "QUOTE_RETENTION_TOKEN", "retention-test", raising=False)

    resp = _client().post(
        "/admin/storage-retention-emergency",
        headers={"x-quote-retention-token": "retention-test"},
        json={
            "dry_run": True,
            "confirm": "delete-quote-storage-paths",
            "paths": ["avatars/private.png"],
        },
    )

    assert resp.status_code == 503
    assert "Ruta de borrado de storage invalida" in resp.json()["detail"]


def test_emergency_storage_retention_explicit_paths_deletes_only_after_confirm(monkeypatch):
    monkeypatch.setattr(index, "QUOTE_RETENTION_TOKEN", "retention-test", raising=False)
    deleted = []
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: deleted.extend(paths), raising=False)

    resp = _client().post(
        "/admin/storage-retention-emergency",
        headers={"x-quote-retention-token": "retention-test"},
        json={
            "dry_run": False,
            "confirm": "delete-quote-storage-paths",
            "paths": [
                "users/7/jobs/job-1/input.xlsx",
                "/users/7/jobs/job-1/input.xlsx",
                "users/7/jobs/job-1/output.xlsx",
            ],
        },
    )

    assert resp.status_code == 200
    assert resp.json()["objects_deleted"] == 2
    assert deleted == ["users/7/jobs/job-1/input.xlsx", "users/7/jobs/job-1/output.xlsx"]


def test_storage_retention_plan_skips_recent_storage_only_jobs():
    objects = [
        {
            "id": "1",
            "_full_name": "users/7/jobs/recent/output.xlsx",
            "created_at": "2026-06-26T00:00:00+00:00",
            "metadata": {"size": 10 * 1024 * 1024},
        },
        {
            "id": "2",
            "_full_name": "users/7/jobs/recent/input.xlsx",
            "created_at": "2026-06-26T00:00:00+00:00",
            "metadata": {"size": 2 * 1024 * 1024},
        },
    ]

    report = index._build_storage_retention_plan(
        objects,
        max_per_user=5,
        min_age_days=1,
        now=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert report["delete_paths"] == []
    assert report["summary"]["recent_inputs_skipped"] == 1
    assert report["summary"]["objects_planned"] == 0


def test_safe_http_error_does_not_echo_storage_paths():
    error = index._safe_http_error(
        "Supabase Storage",
        402,
        '{"message":"Service restricted exceed_storage_size_quota users/7/jobs/private/output.xlsx"}',
    )

    assert error == "Supabase Storage HTTP 402: exceed_storage_size_quota"
    assert "users/7/jobs" not in error


def test_retention_expires_downloaded_outputs_after_retention_window():
    old_job = _job("job-old")
    old_job["metadata"]["last_downloaded_at"] = "2026-01-01T00:00:00+00:00"

    report = index._run_quote_retention(7, [old_job], dry_run=True)

    assert report["jobs_deleted"] == 1
    assert report["deleted_reasons"] == {"downloaded_output_expired": 1}
    assert report["storage_objects_planned"] == 2
    assert report["remaining_jobs"] == []


def test_download_marks_job_as_downloaded(monkeypatch):
    _mock_user(monkeypatch)
    updates = []
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: _job(job_id), raising=False)
    monkeypatch.setattr(
        index,
        "_create_signed_download",
        lambda path, filename=None: "https://example.test/download.xlsx",
        raising=False,
    )
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, update: updates.append((job_id, update)) or {"id": job_id, **update}, raising=False)

    resp = _client().get("/cotizaciones/job-1/download", headers=_auth_headers())

    assert resp.status_code == 200
    assert updates[0][0] == "job-1"
    assert updates[0][1]["metadata"]["download_count"] == 1
    assert "last_downloaded_at" in updates[0][1]["metadata"]
