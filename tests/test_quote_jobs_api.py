import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vercel_deploy", "api"))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import index


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
    monkeypatch.setattr(
        index,
        "db_get_suscripcion_by_usuario",
        lambda requested_id: {
            "id": 1,
            "usuario_id": requested_id,
            "estado": "activa",
            "plan": "mensual",
            "fecha_fin": "2099-01-01T00:00:00+00:00",
        },
    )


def test_init_upload_requires_token():
    resp = _client().post("/cotizaciones/init-upload", json={"filename": "q.xlsx", "size": 100})
    assert resp.status_code == 401


def test_cors_wildcard_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.delenv("ALLOW_WILDCARD_CORS", raising=False)

    origins = index._origins()

    assert "*" not in origins
    assert "https://web-lemon-one-45.vercel.app" in origins


def test_init_upload_creates_signed_upload(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_create_signed_upload", lambda path: {"token": "upload-token"})
    monkeypatch.setattr(
        index,
        "db_create_quote_job",
        lambda usuario_id, template, metadata, input_path, job_id=None: {
            "id": job_id or "job-1",
            "usuario_id": usuario_id,
            "template": template,
            "metadata": metadata,
            "input_path": input_path,
        },
    )

    resp = _client().post(
        "/cotizaciones/init-upload",
        headers=_auth_headers(),
        json={"filename": "quotation.xlsx", "size": 1024, "template": "Template.xlsx"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["bucket"] == "quote-files"
    assert data["token"] == "upload-token"
    assert data["path"] == f"users/7/jobs/{data['job_id']}/input.xlsx"


def test_submit_rejects_job_from_other_user(monkeypatch):
    _mock_user(monkeypatch, user_id=7)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 99,
            "status": "draft",
            "metadata": {},
            "template": "Template.xlsx",
        },
    )

    resp = _client().post("/cotizaciones/job-1/submit", headers=_auth_headers(7), json={})
    assert resp.status_code == 403


def test_submit_moves_job_to_queued(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "Template.xlsx",
        },
    )

    def fake_update(job_id, updates):
        return {"id": job_id, **updates}

    monkeypatch.setattr(index, "db_update_quote_job", fake_update)

    resp = _client().post(
        "/cotizaciones/job-1/submit",
        headers=_auth_headers(),
        json={
            "cotizacion": "COT-001",
            "proyecto": "Proyecto",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "image_provider": "dezgo",
            "image_prompt": "Prompt personalizado para mobiliario en fondo blanco",
            "template": "Template.xlsx",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["job"]["status"] == "queued"
    assert resp.json()["job"]["metadata"]["cotizacion"] == "COT-001"
    assert resp.json()["job"]["metadata"]["descuento"] == 40
    assert resp.json()["job"]["metadata"]["image_provider"] == "dezgo"
    assert resp.json()["job"]["metadata"]["image_prompt"] == "Prompt personalizado para mobiliario en fondo blanco"
    assert resp.json()["job"]["metadata"]["estimated_duration_seconds"] == 360


def test_submit_assigns_locked_quote_number_for_known_user(monkeypatch):
    _mock_user(monkeypatch, email="joel.meza@mobiliti.mx")
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "Template.xlsx",
        },
    )
    monkeypatch.setattr(
        index,
        "db_list_quote_jobs",
        lambda usuario_id: [
            {"metadata": {"cotizacion": "100-00000"}},
            {"metadata": {"cotizacion": "100-00004"}},
            {"metadata": {"cotizacion": "200-00099"}},
            {"metadata": {"cotizacion": "sin-folio"}},
        ],
    )

    def fake_update(job_id, updates):
        return {"id": job_id, **updates}

    monkeypatch.setattr(index, "db_update_quote_job", fake_update)

    resp = _client().post(
        "/cotizaciones/job-1/submit",
        headers=_auth_headers(email="joel.meza@mobiliti.mx"),
        json={
            "cotizacion": "HACK-123",
            "proyecto": "Proyecto",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "template": "Template.xlsx",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["job"]["metadata"]["cotizacion"] == "100-00005"


def test_submit_defaults_to_dezgo_for_missing_product_images(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "Template.xlsx",
        },
    )

    def fake_update(job_id, updates):
        return {"id": job_id, **updates}

    monkeypatch.setattr(index, "db_update_quote_job", fake_update)

    resp = _client().post(
        "/cotizaciones/job-1/submit",
        headers=_auth_headers(),
        json={
            "cotizacion": "COT-001",
            "proyecto": "Proyecto",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["job"]["metadata"]["image_provider"] == "dezgo"
    assert resp.json()["job"]["metadata"]["image_prompt"] == "Mejora la calidad de imagen y que este en fondo blanco"
    assert resp.json()["job"]["metadata"]["estimated_duration_seconds"] == 360


def test_submit_rejects_invalid_image_provider(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "Template.xlsx",
        },
    )

    resp = _client().post(
        "/cotizaciones/job-1/submit",
        headers=_auth_headers(),
        json={
            "cotizacion": "COT-001",
            "proyecto": "Proyecto",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "image_provider": "otro",
        },
    )

    assert resp.status_code == 400


def test_submit_rejects_discount_over_100(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "Template.xlsx",
        },
    )

    resp = _client().post(
        "/cotizaciones/job-1/submit",
        headers=_auth_headers(),
        json={
            "cotizacion": "COT-001",
            "proyecto": "Proyecto",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "descuento": 101,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Descuento debe estar entre 0 y 100"


def test_download_requires_completed_job(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {"id": job_id, "usuario_id": 7, "status": "processing", "output_path": None},
    )

    resp = _client().get("/cotizaciones/job-1/download", headers=_auth_headers())
    assert resp.status_code == 409


def test_retry_requeues_failed_job(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "failed",
            "input_path": "users/7/jobs/job-1/input.xlsx",
            "error_message": "boom",
        },
    )

    def fake_update(job_id, updates):
        return {"id": job_id, **updates}

    monkeypatch.setattr(index, "db_update_quote_job", fake_update)

    resp = _client().post("/cotizaciones/job-1/retry", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["job"]["status"] == "queued"
    assert resp.json()["job"]["error_message"] is None


def test_retry_rejects_non_failed_job(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "completed",
            "input_path": "users/7/jobs/job-1/input.xlsx",
        },
    )

    resp = _client().post("/cotizaciones/job-1/retry", headers=_auth_headers())

    assert resp.status_code == 409


def test_download_returns_signed_url(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {"id": job_id, "usuario_id": 7, "status": "completed", "output_path": "users/7/jobs/job-1/output.xlsx"},
    )
    monkeypatch.setattr(index, "_create_signed_download", lambda path: f"https://example.test/{path}")

    resp = _client().get("/cotizaciones/job-1/download", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["download_url"].endswith("output.xlsx")


def test_file_download_returns_xlsx_attachment(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "completed",
            "output_path": "users/7/jobs/job-1/output.xlsx",
            "metadata": {"proyecto": "Proyecto Alpha", "cotizacion": "COT-001"},
        },
    )
    monkeypatch.setattr(index, "_storage_download_bytes", lambda path: b"PK\x03\x04xlsx")

    resp = _client().get("/cotizaciones/job-1/file", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.content.startswith(b"PK\x03\x04")
    assert resp.headers["content-disposition"] == 'attachment; filename="Cotizacion_Proyecto_Alpha_COT-001.xlsx"'


def test_safe_quote_filename_uses_project_and_quote_number():
    filename = index._safe_quote_filename(
        {"id": "job-1", "metadata": {"proyecto": "IZA Reforma / Piso 3", "cotizacion": "300-00010"}}
    )

    assert filename == "Cotizacion_IZA_Reforma_Piso_3_300-00010.xlsx"
