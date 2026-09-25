import os
import sys
import asyncio
import hashlib
import json
import threading
import urllib.error
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from quotation_import_fixtures import write_import_fixture

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vercel_deploy", "api"))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import index
import mobiliti_saas.api.index as primary_index
from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem


JOB_MIXED_UUID = "11111111-1111-4111-8111-111111111111"
JOB_A_UUID = "22222222-2222-4222-8222-222222222222"
JOB_B_UUID = "33333333-3333-4333-8333-333333333333"


def dev_state_with_draft_job(job_id, user_id=7):
    return {
        "quote_jobs": [{"id": job_id, "usuario_id": user_id, "status": "draft"}],
        "tarkett_reservations": [],
        "offiho_reservations": [],
        "catalog_reservations": [],
    }


def dev_state_with_two_draft_jobs(first, second, user_id=7):
    state = dev_state_with_draft_job(first, user_id)
    state["quote_jobs"].append({"id": second, "usuario_id": user_id, "status": "draft"})
    return state


def configure_thread_safe_dev_store(monkeypatch, state):
    store_lock = threading.Lock()

    def load():
        with store_lock:
            return json.loads(json.dumps(state))

    def save(data):
        with store_lock:
            state.clear()
            state.update(json.loads(json.dumps(data)))

    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", load)
    monkeypatch.setattr(index, "_dev_save", save)


def configure_barrier_dev_store(monkeypatch, state):
    first_load_entered = threading.Event()
    allow_first_load = threading.Event()
    overlapping_load = threading.Event()
    load_threads = []

    def load():
        load_threads.append(threading.get_ident())
        if len(load_threads) == 1:
            first_load_entered.set()
            if not allow_first_load.wait(5):
                raise RuntimeError("timeout esperando barrera DEV")
        elif not allow_first_load.is_set():
            overlapping_load.set()
        return json.loads(json.dumps(state))

    def save(data):
        state.clear()
        state.update(json.loads(json.dumps(data)))

    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", load)
    monkeypatch.setattr(index, "_dev_save", save)
    return first_load_entered, allow_first_load, overlapping_load, load_threads


def submit_after_first_load(pool, first_load_entered, operation):
    assert first_load_entered.wait(5)
    second_started = threading.Event()

    def run():
        second_started.set()
        return operation()

    future = pool.submit(run)
    assert second_started.wait(5)
    return future


def _client():
    return TestClient(index.app)


def test_dev_save_never_exposes_a_truncated_json_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(index, "DEV_STORE_DIR", tmp_path)
    db_path = tmp_path / "db.json"
    initial = {"quote_jobs": [{"id": "before"}]}
    updated = {"quote_jobs": [{"id": "after"}], "payload": "x" * 1000}
    db_path.write_text(json.dumps(initial), encoding="utf-8")
    target_was_truncated = threading.Event()
    allow_direct_write_to_finish = threading.Event()
    original_write_text = Path.write_text

    def delayed_direct_write(path, content, *args, **kwargs):
        if path == db_path:
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
    writer = threading.Thread(target=index._dev_save, args=(updated,))
    writer.start()
    try:
        if target_was_truncated.wait(0.5):
            snapshot = index._dev_load()
        else:
            writer.join(5)
            snapshot = index._dev_load()
    finally:
        allow_direct_write_to_finish.set()
        writer.join(5)

    assert not writer.is_alive()
    assert snapshot in (initial, updated)
    assert json.loads(db_path.read_text(encoding="utf-8")) == updated


def _token(user_id=7, email="cliente@example.com"):
    return index.create_access_token({"sub": str(user_id), "email": email})


def _auth_headers(user_id=7, email="cliente@example.com"):
    return {"Authorization": f"Bearer {_token(user_id, email)}"}


def _mock_user(monkeypatch, user_id=7, active=True, email="cliente@example.com", admin=False):
    monkeypatch.setattr(
        index,
        "db_get_usuario_by_id",
        lambda requested_id: {
            "id": requested_id,
            "email": email,
            "nombre": "Cliente",
            "empresa": "Mobiliti",
            "es_admin": admin,
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
    monkeypatch.setattr(index, "_enforce_active_quote_limit", lambda *_args, **_kwargs: None)


def uploaded_draft_quote(monkeypatch, tmp_path, fixture="quotation-import.xlsx", user_id=7):
    _mock_user(monkeypatch, user_id=user_id)
    job_id = JOB_MIXED_UUID
    source = write_import_fixture(tmp_path / fixture)
    state = {
        "job": {
            "id": job_id,
            "usuario_id": user_id,
            "status": "draft",
            "input_path": f"users/{user_id}/jobs/{job_id}/input.xlsx",
            "metadata": {"original_filename": source.name},
        },
        "uploads": [],
    }

    monkeypatch.setattr(index, "db_get_quote_job", lambda requested_id: state["job"] if requested_id == job_id else None)
    monkeypatch.setattr(index, "_storage_download_bytes", lambda path: source.read_bytes())
    monkeypatch.setattr(
        index,
        "_storage_upload_bytes",
        lambda path, content, content_type="application/octet-stream": state["uploads"].append(
            (path, content, content_type)
        ),
    )
    monkeypatch.setattr(index, "_create_signed_download", lambda path: f"https://storage.example/{path}")

    def update_job(requested_id, updates, *, expected_status=None):
        if requested_id != job_id or (expected_status and state["job"]["status"] != expected_status):
            return {}
        state["job"].update(updates)
        return state["job"]

    monkeypatch.setattr(index, "db_update_quote_job", update_job)
    return _client(), _token(user_id), job_id, state


def test_import_preview_returns_manifest_and_signed_images(monkeypatch, tmp_path):
    client, token, job_id, state = uploaded_draft_quote(monkeypatch, tmp_path)

    response = client.post(
        f"/cotizaciones/{job_id}/import-preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["import_id"] == job_id
    assert body["currency_status"] == "required"
    assert len(body["items"]) == 7
    assert body["items"][0]["image_url"].startswith("http")
    assert state["job"]["metadata"]["import_item_count"] == 7
    uploads = {path: (content, content_type) for path, content, content_type in state["uploads"]}
    manifest_path = state["job"]["metadata"]["import_manifest_path"]
    manifest_bytes, manifest_type = uploads[manifest_path]
    stored_manifest = json.loads(manifest_bytes)
    assert manifest_type == "application/json"
    assert stored_manifest["import_id"] == job_id
    assert stored_manifest["preview_image_paths"] == state["job"]["metadata"]["import_preview_paths"]
    for row, image_path in stored_manifest["preview_image_paths"].items():
        _content, content_type = uploads[image_path]
        assert row.isdigit()
        assert content_type in {"image/png", "image/jpeg", "image/webp"}


def test_import_preview_omits_disallowed_or_signature_mismatched_images(monkeypatch, tmp_path):
    client, token, job_id, state = uploaded_draft_quote(monkeypatch, tmp_path)
    png_buffer = BytesIO()
    Image.new("RGBA", (900, 450), (10, 20, 30, 180)).save(png_buffer, format="PNG")
    png_bytes = png_buffer.getvalue()
    jpeg_buffer = BytesIO()
    Image.new("RGB", (320, 160), (40, 50, 60)).save(jpeg_buffer, format="JPEG")
    jpeg_bytes = jpeg_buffer.getvalue()
    gif_bytes = b"GIF89a-not-a-png"
    manifest = {
        "import_id": job_id,
        "source_hash": "a" * 64,
        "original_filename": "quotation-import.xlsx",
        "provider": "",
        "source_currency": None,
        "currency_status": "required",
        "sections": [{"id": "import-section-1", "title": "Productos", "item_keys": []}],
        "items": [
            {"key": f"import:{job_id}:9", "source_row": 9},
            {"key": f"import:{job_id}:10", "source_row": 10},
            {"key": f"import:{job_id}:11", "source_row": 11},
            {"key": f"import:{job_id}:12", "source_row": 12},
        ],
    }
    image_map = {
        9: (png_bytes, "image/png"),
        10: (gif_bytes, "image/png"),
        11: (gif_bytes, "image/gif"),
        12: (jpeg_bytes, ".jpeg"),
    }
    monkeypatch.setattr(index, "build_import_manifest", lambda *_args: (manifest, image_map))

    response = client.post(
        f"/cotizaciones/{job_id}/import-preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["image_url"] for item in body["items"]] == [
        f"https://storage.example/users/7/jobs/{job_id}/preview/{manifest['source_hash'][:16]}/row-9.png",
        "",
        "",
        f"https://storage.example/users/7/jobs/{job_id}/preview/{manifest['source_hash'][:16]}/row-12.png",
    ]
    uploads = {path: (content, content_type) for path, content, content_type in state["uploads"]}
    preview_prefix = f"users/7/jobs/{job_id}/preview/{manifest['source_hash'][:16]}"
    for row in (9, 12):
        normalized, content_type = uploads[f"{preview_prefix}/row-{row}.png"]
        assert content_type == "image/png"
        assert len(normalized) <= index.IMPORT_PREVIEW_IMAGE_MAX_BYTES
        with Image.open(BytesIO(normalized)) as preview_image:
            assert preview_image.format == "PNG"
            assert preview_image.width <= index.IMPORT_PREVIEW_THUMBNAIL_MAX_SIDE
            assert preview_image.height <= index.IMPORT_PREVIEW_THUMBNAIL_MAX_SIDE
    assert all(content != gif_bytes for path, (content, _type) in uploads.items() if path != f"{preview_prefix}/manifest.json")
    manifest_bytes, manifest_type = uploads[f"{preview_prefix}/manifest.json"]
    assert manifest_type == "application/json"
    assert json.loads(manifest_bytes)["preview_image_paths"] == {
        "9": f"{preview_prefix}/row-9.png",
        "12": f"{preview_prefix}/row-12.png",
    }
    assert state["job"]["metadata"]["import_preview_paths"] == {
        "9": f"{preview_prefix}/row-9.png",
        "12": f"{preview_prefix}/row-12.png",
    }


def test_import_preview_image_normalization_rejects_size_pixels_bomb_and_mime_mismatch():
    safe_buffer = BytesIO()
    Image.new("RGB", (1200, 600), "white").save(safe_buffer, format="JPEG", quality=90)
    normalized = index._normalize_import_preview_image(safe_buffer.getvalue(), "image/jpeg")
    assert normalized is not None
    content, suffix, content_type = normalized
    assert suffix == ".png"
    assert content_type == "image/png"
    assert len(content) <= index.IMPORT_PREVIEW_IMAGE_MAX_BYTES
    with Image.open(BytesIO(content)) as image:
        assert image.format == "PNG"
        assert image.size == (index.IMPORT_PREVIEW_THUMBNAIL_MAX_SIDE, 320)

    assert index._normalize_import_preview_image(
        b"\x89PNG\r\n\x1a\n" + b"0" * index.IMPORT_PREVIEW_IMAGE_MAX_BYTES,
        "image/png",
    ) is None

    too_many_pixels = BytesIO()
    Image.new("1", (5001, 5000), 1).save(too_many_pixels, format="PNG")
    assert index._normalize_import_preview_image(too_many_pixels.getvalue(), "image/png") is None
    assert index._normalize_import_preview_image(safe_buffer.getvalue(), "image/png") is None


@pytest.mark.parametrize(
    "case,expected_status",
    [
        ("other-user", 403),
        ("queued", 409),
        ("pdf", 409),
        ("missing-input", 400),
        ("too-many-products", 400),
    ],
)
def test_import_preview_rejects_invalid_source_cases(monkeypatch, tmp_path, case, expected_status):
    client, token, job_id, state = uploaded_draft_quote(monkeypatch, tmp_path)
    if case == "other-user":
        token = _token(8, "other@example.com")
    elif case == "queued":
        state["job"]["status"] = "queued"
    elif case == "pdf":
        state["job"]["input_path"] = state["job"]["input_path"].replace(".xlsx", ".pdf")
    elif case == "missing-input":
        state["job"]["input_path"] = None
    else:
        monkeypatch.setattr(
            index,
            "build_import_manifest",
            lambda *_args: (_ for _ in ()).throw(
                ValueError("La cotizacion requiere una fila fuera del limite XLSX")
            ),
        )

    response = client.post(
        f"/cotizaciones/{job_id}/import-preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == expected_status
    assert "import_source_hash" not in state["job"]["metadata"]


def test_import_preview_is_repeatable_without_reuploading_the_source(monkeypatch, tmp_path):
    client, token, job_id, state = uploaded_draft_quote(monkeypatch, tmp_path)
    downloads = []
    original_download = index._storage_download_bytes
    monkeypatch.setattr(
        index,
        "_storage_download_bytes",
        lambda path: downloads.append(path) or original_download(path),
    )

    first = client.post(f"/cotizaciones/{job_id}/import-preview", headers={"Authorization": f"Bearer {token}"})
    second = client.post(f"/cotizaciones/{job_id}/import-preview", headers={"Authorization": f"Bearer {token}"})

    assert first.status_code == second.status_code == 200
    assert first.json()["source_hash"] == second.json()["source_hash"]
    assert downloads == [state["job"]["input_path"], state["job"]["input_path"]]
    assert all("/input.xlsx" not in path for path, _content, _type in state["uploads"])


def test_import_preview_does_not_persist_metadata_when_draft_cas_loses_race(monkeypatch, tmp_path):
    client, token, job_id, state = uploaded_draft_quote(monkeypatch, tmp_path)
    deleted = []
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: deleted.extend(paths))
    monkeypatch.setattr(index, "db_update_quote_job", lambda *_args, **_kwargs: {})

    response = client.post(
        f"/cotizaciones/{job_id}/import-preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert "import_manifest_path" not in state["job"]["metadata"]
    assert "import_source_hash" not in state["job"]["metadata"]
    assert deleted
    assert set(deleted) == {path for path, _content, _type in state["uploads"]}


def test_import_preview_replacement_cleans_stale_manifest_and_removed_rows(monkeypatch, tmp_path):
    client, token, job_id, state = uploaded_draft_quote(monkeypatch, tmp_path)
    old_prefix = f"users/7/jobs/{job_id}/preview/{'b' * 16}"
    state["job"]["metadata"].update({
        "import_manifest_path": f"{old_prefix}/manifest.json",
        "import_preview_paths": {
            "9": f"{old_prefix}/row-9.png",
            "10": f"{old_prefix}/row-10.png",
        },
    })
    deleted = []
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: deleted.extend(paths))

    response = client.post(
        f"/cotizaciones/{job_id}/import-preview",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert set(deleted) == {
        f"{old_prefix}/manifest.json",
        f"{old_prefix}/row-9.png",
        f"{old_prefix}/row-10.png",
    }
    assert state["job"]["metadata"]["import_manifest_path"] not in deleted


def test_quote_storage_paths_include_only_owned_import_preview_objects():
    job = {
        "id": JOB_MIXED_UUID,
        "usuario_id": 7,
        "input_path": f"users/7/jobs/{JOB_MIXED_UUID}/input.xlsx",
        "output_path": f"users/7/jobs/{JOB_MIXED_UUID}/output.xlsx",
        "metadata": {
            "import_source_path": f"users/7/jobs/{JOB_MIXED_UUID}/import-source.xlsx",
            "import_manifest_path": f"users/7/jobs/{JOB_MIXED_UUID}/preview/manifest.json",
            "import_preview_paths": {
                "9": f"users/7/jobs/{JOB_MIXED_UUID}/preview/row-9.png",
                "10": "users/8/jobs/not-owned/preview/row-10.png",
            },
        },
    }

    assert index._quote_storage_paths(job) == [
        f"users/7/jobs/{JOB_MIXED_UUID}/input.xlsx",
        f"users/7/jobs/{JOB_MIXED_UUID}/output.xlsx",
        f"users/7/jobs/{JOB_MIXED_UUID}/import-source.xlsx",
        f"users/7/jobs/{JOB_MIXED_UUID}/preview/manifest.json",
        f"users/7/jobs/{JOB_MIXED_UUID}/preview/row-9.png",
    ]


@pytest.mark.parametrize(
    "import_source_path",
    (
        f"users/8/jobs/{JOB_MIXED_UUID}/import-source.xlsx",
        f"users/7/jobs/{JOB_MIXED_UUID}-shadow/import-source.xlsx",
        f"users/7/jobs/{JOB_A_UUID}/import-source.xlsx",
    ),
)
def test_quote_storage_paths_reject_import_source_outside_exact_job_prefix(
    import_source_path,
):
    job = {
        "id": JOB_MIXED_UUID,
        "usuario_id": 7,
        "input_path": None,
        "output_path": None,
        "metadata": {"import_source_path": import_source_path},
    }

    assert index._quote_storage_paths(job) == []


def test_quote_job_database_error_does_not_leak_connection_secret(monkeypatch, capsys):
    secret = "postgresql://admin:simulated-token@internal.example/private"
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(index.HTTPException) as error:
        index._quote_job_for_user(JOB_MIXED_UUID, 7)

    captured = capsys.readouterr()
    assert error.value.status_code == 503
    assert error.value.detail == "Servicio de cotizaciones no disponible"
    assert secret not in f"{captured.out}{captured.err}"


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
    created = {}
    monkeypatch.setattr(index, "_create_signed_upload", lambda path: {"token": "upload-token"})

    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        created.update({"metadata": metadata})
        return {
            "id": job_id or "job-1",
            "usuario_id": usuario_id,
            "template": template,
            "metadata": metadata,
            "input_path": input_path,
        }

    monkeypatch.setattr(index, "db_create_quote_job", fake_create)

    resp = _client().post(
        "/cotizaciones/init-upload",
        headers=_auth_headers(),
        json={"filename": "quotation.xlsx", "size": 1024, "template": "official_2026_gdl"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["bucket"] == "quote-files"
    assert data["token"] == "upload-token"
    assert data["path"] == f"users/7/jobs/{data['job_id']}/input.xlsx"
    assert created["metadata"]["storage_provider"] in {"supabase", "r2"}
    assert created["metadata"]["input_storage_provider"] == created["metadata"]["storage_provider"]


def test_init_upload_accepts_r2_signed_upload_without_supabase_token(monkeypatch):
    _mock_user(monkeypatch)
    created = {}
    monkeypatch.setattr(index, "_use_r2_storage", lambda: True, raising=False)
    monkeypatch.setattr(index, "_storage_bucket_name", lambda: "mobiliti-quotes", raising=False)
    monkeypatch.setattr(
        index,
        "_create_signed_upload",
        lambda path: {"provider": "r2", "signed_upload_url": "https://r2.example/upload"},
    )
    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        created.update({"metadata": metadata})
        return {
            "id": job_id or "job-1",
            "usuario_id": usuario_id,
            "template": template,
            "metadata": metadata,
            "input_path": input_path,
        }

    monkeypatch.setattr(index, "db_create_quote_job", fake_create)

    resp = _client().post(
        "/cotizaciones/init-upload",
        headers=_auth_headers(),
        json={"filename": "quotation.xlsx", "size": 1024, "template": "official_2026_gdl"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["bucket"] == "mobiliti-quotes"
    assert data["storage_provider"] == "r2"
    assert data["token"] is None
    assert data["signed_upload_url"] == "https://r2.example/upload"
    assert created["metadata"]["storage_provider"] == "r2"
    assert created["metadata"]["input_storage_provider"] == "r2"


def test_init_upload_accepts_pdf(monkeypatch):
    _mock_user(monkeypatch)
    created = {}

    monkeypatch.setattr(index, "_create_signed_upload", lambda path: {"token": "upload-token"})

    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        created.update({"metadata": metadata, "input_path": input_path})
        return {
            "id": job_id or "job-1",
            "usuario_id": usuario_id,
            "template": template,
            "metadata": metadata,
            "input_path": input_path,
        }

    monkeypatch.setattr(index, "db_create_quote_job", fake_create)

    resp = _client().post(
        "/cotizaciones/init-upload",
        headers=_auth_headers(),
        json={"filename": "supplier-quotation.pdf", "size": 2048, "template": "official_2026_gdl"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == f"users/7/jobs/{data['job_id']}/input.pdf"
    assert created["input_path"] == data["path"]
    assert created["metadata"]["input_extension"] == ".pdf"
    assert created["metadata"]["original_filename"] == "supplier-quotation.pdf"
    assert created["metadata"]["input_storage_provider"] == created["metadata"]["storage_provider"]


def _mock_tarkett_catalog():
    item = TarkettCatalogItem(
        code="25731726",
        name="Aurea Tech Cadiz 6.0mm",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("970.200"),
        product_url="https://tarkett.com.mx/producto/cadiz/",
        image_url="https://tarkett.com.mx/wp-content/uploads/2026/05/Aurea-Tech-Cadiz-scaled.jpg",
        match_status="name_match",
        unit_price=Decimal("472.63"),
        price_source="tarkettnet_code_match",
        stock_source="tarkettnet_code_match",
    )
    return {
        "source_hash": "catalog-hash",
        "generated_at": "2026-07-08T00:00:00+00:00",
        "items": [item],
        "by_code": {item.code: item},
    }


def _valid_tarkett_body(quantity=1):
    return {
        "proyecto": "Proyecto Tarkett",
        "cliente": "Cliente",
        "correo": "cliente@example.com",
        "telefono": "5551234567",
        "direccion": "Guadalajara",
        "razon_social": "Cliente SA de CV",
        "descuento": 40,
        "items": [{"code": "25731726", "quantity": quantity}],
    }


def _valid_mixed_body(items=None):
    return {
        "proyecto": "Proyecto mixto",
        "cliente": "Cliente",
        "correo": "cliente@example.com",
        "telefono": "5551234567",
        "direccion": "Guadalajara",
        "razon_social": "Cliente SA de CV",
        "descuento": 40,
        "quote_currency": "MXN",
        "items": items or [
            {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
        ],
    }


def _valid_submit_body():
    return {
        "cotizacion": "COT-001",
        "proyecto": "Proyecto",
        "cliente": "Cliente",
        "correo": "cliente@example.com",
        "telefono": "555",
        "direccion": "Direccion",
        "razon_social": "Empresa SA",
        "template": "official_2026_gdl",
    }


def _mock_mixed_quote_dependencies(monkeypatch):
    _mock_user(monkeypatch)
    state = {
        "jobs": [], "uploads": [], "events": [], "requested_catalogs": [],
        "loaded_catalogs": [], "rate_calls": 0, "released": [],
        "deleted_jobs": [], "deleted_inputs": [], "reserved_groups": [],
    }
    def tarkett_catalog():
        state["loaded_catalogs"].append("tarkett")
        return {**_mock_tarkett_catalog(), "source_hash": "a" * 64}

    def offiho_catalog():
        state["loaded_catalogs"].append("offiho")
        return {**_mock_offiho_catalog(available_quantity=8), "source_hash": "b" * 64}

    monkeypatch.setattr(index, "_load_tarkett_catalog_cached", tarkett_catalog)
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", offiho_catalog)

    def supplier_catalog(supplier):
        state["requested_catalogs"].append(supplier)
        state["loaded_catalogs"].append(supplier)
        base_currency = "USD" if supplier in {"sunon", "alma"} else "MXN"
        item = {
            "internal_id": f"{supplier}:desk-1", "supplier": supplier,
            "product_key": "desk-1", "sku": f"{supplier.upper()}-1",
            "code_status": "verified", "brand": supplier, "collection": "Work",
            "name": f"Escritorio {supplier}", "description": "Escritorio operativo",
            "unit": "pieza", "availability_type": "stocked", "stock": "5.000000",
            "lead_time": "Entrega inmediata", "base_price_options": [],
            "add_on_options": [], "base_currency": base_currency,
            "price_net": "100.000000", "tax_rate": "0.160000", "attributes": {},
            "image_url": "", "image_kind": "placeholder", "product_url": "",
            "warnings": [], "source_reference": f"{supplier}:source",
        }
        return {
            "supplier": supplier,
            "source_hash": hashlib.sha256(supplier.encode("utf-8")).hexdigest(),
            "generated_at": "2026-07-19T00:00:00+00:00", "items": [item],
        }

    monkeypatch.setattr(index, "_require_enabled_catalog_supplier", lambda supplier: supplier)
    monkeypatch.setattr(index, "_load_supplier_catalog_cached", supplier_catalog)

    def rates():
        state["rate_calls"] += 1
        return _supplier_rate_rows()

    monkeypatch.setattr(index, "db_list_exchange_rates", rates)
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)

    def create_job(usuario_id, template, metadata, input_path, job_id=None):
        state["events"].append("create_job")
        job = {
            "id": job_id, "usuario_id": usuario_id, "status": "draft",
            "template": template, "metadata": metadata, "input_path": input_path,
        }
        state["jobs"].append(job)
        return job

    def reserve(usuario_id, job_id, groups):
        state["events"].append("reserve_mixed")
        state["reserved_groups"].append(deepcopy(groups))
        return [
            {
                "catalog": group["catalog"], "identity": item["identity"],
                "reserved_before": "0.000000", "available_before": item["stock"],
                "insufficient": False, "reserved_by_others": False,
            }
            for group in groups for item in group["items"]
        ]

    def upload(path, content, content_type="application/octet-stream"):
        state["events"].append("upload")
        state["uploads"].append({"path": path, "content": content, "content_type": content_type})

    def queue(job_id, metadata):
        state["events"].append("queue")
        return {"id": job_id, "status": "queued", "metadata": metadata}

    monkeypatch.setattr(index, "db_create_quote_job", create_job)
    monkeypatch.setattr(index, "db_reserve_mixed_cart", reserve)
    monkeypatch.setattr(index, "db_queue_mixed_quote_job", queue)
    monkeypatch.setattr(index, "db_release_mixed_cart", lambda job_id: state["released"].append(job_id))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: state["deleted_jobs"].append(job_id))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: state["deleted_inputs"].extend(paths))
    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    monkeypatch.setattr(index, "_wake_worker", lambda: state["events"].append("wake"))
    return state


def _imported_mixed_quote_case(monkeypatch, tmp_path, *, explicit_currency=None):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    source = write_import_fixture(
        tmp_path / "quotation-import.xlsx", currency=explicit_currency
    )
    source_bytes = source.read_bytes()
    manifest, _images = index.build_import_manifest(
        source_bytes, JOB_A_UUID, source.name
    )
    manifest_path = f"users/7/jobs/{JOB_A_UUID}/preview/{manifest['source_hash'][:16]}/manifest.json"
    source_path = f"users/7/jobs/{JOB_A_UUID}/input.xlsx"
    import_job = {
        "id": JOB_A_UUID,
        "usuario_id": 7,
        "status": "draft",
        "input_path": source_path,
        "metadata": {
            "original_filename": source.name,
            "import_manifest_path": manifest_path,
            "import_preview_paths": {},
            "import_source_hash": manifest["source_hash"],
            "import_item_count": len(manifest["items"]),
        },
    }
    stored_manifest = {**manifest, "preview_image_paths": {}}
    objects = {
        source_path: source_bytes,
        manifest_path: json.dumps(
            stored_manifest, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"),
    }

    def get_job(requested_id):
        if requested_id == JOB_A_UUID:
            return import_job
        return next((job for job in state["jobs"] if job["id"] == requested_id), None)

    def update_job(requested_id, updates, *, expected_status=None):
        job = get_job(requested_id)
        if not job or (expected_status is not None and job.get("status") != expected_status):
            return {}
        job.update(deepcopy(updates))
        return job

    monkeypatch.setattr(index, "db_get_quote_job", get_job)
    monkeypatch.setattr(index, "db_update_quote_job", update_job)

    def upload(path, content, content_type="application/octet-stream"):
        state["events"].append("upload")
        state["uploads"].append({
            "path": path, "content": content, "content_type": content_type,
        })
        objects[path] = content

    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    monkeypatch.setattr(index, "_storage_download_bytes", lambda path: objects[path])
    item = {
        "kind": "imported",
        "import_id": JOB_A_UUID,
        "source_row": 11,
        "source_currency": explicit_currency or "USD",
        "quantity": "2",
        "overrides": {
            "name": "Alien Task Chair revisada",
            "description": "Silla operativa revisada",
            "dimension": "630 x 565 x 1000 mm",
            "unit_price": "82.00",
            "provider": "Sunon",
        },
    }
    return state, import_job, manifest, source_bytes, objects, item


def test_mixed_quote_validates_and_copies_imported_source_without_reserving_it(
    monkeypatch, tmp_path
):
    state, import_job, manifest, source_bytes, _objects, imported = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    catalog = {"catalog": "tarkett", "code": "25731726", "quantity": "1"}
    body = _valid_mixed_body([catalog, imported])
    body["sections"] = [{
        "id": "section-1",
        "title": "Recepcion",
        "item_keys": ["tarkett:25731726", f"import:{JOB_A_UUID}:11"],
    }]

    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=body
    )

    assert response.status_code == 200, response.json()
    final_job_id = state["jobs"][0]["id"]
    source_copy_path = f"users/7/jobs/{final_job_id}/import-source.xlsx"
    uploads = {upload["path"]: upload for upload in state["uploads"]}
    assert uploads[source_copy_path]["content"] == source_bytes
    payload_upload = uploads[f"users/7/jobs/{final_job_id}/input.json"]
    payload = json.loads(payload_upload["content"])
    assert payload["groups"][0]["catalog"] == "tarkett"
    assert payload["imported_source"]["source_path"] == source_copy_path
    assert payload["imported_source"]["source_hash"] == manifest["source_hash"]
    assert payload["imported_source"]["items"][0]["canonical_key"] == f"import:{JOB_A_UUID}:11"
    assert payload["sections"] == [{
        "id": "section-1",
        "title": "Recepcion",
        "line_ids": ["legacy-1", "legacy-import-1"],
    }]
    metadata = state["jobs"][0]["metadata"]
    assert metadata["import_source_path"] == source_copy_path
    assert metadata["import_item_count"] == 1
    assert metadata["import_source_currencies"] == ["USD"]
    assert "preview_image_paths" not in metadata
    assert all("import" not in group["catalog"] for group in payload["groups"])
    assert state["reserved_groups"] == [[{
        "catalog": "tarkett",
        "items": [{
            "identity": "25731726", "sku": "25731726",
            "quantity": "1.000000", "stock": "970.200000",
        }],
    }]]
    assert import_job["status"] == "failed"
    assert import_job["metadata"]["import_consumed_by_job_id"] == final_job_id


def test_failed_mixed_job_releases_consumed_import_for_retry(monkeypatch, tmp_path):
    state, import_job, _manifest, _source, _objects, imported = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    body = _valid_mixed_body([imported])

    first = _client().post("/catalogs/mixed-quote", headers=_auth_headers(), json=body)
    assert first.status_code == 200, first.json()
    first_job_id = first.json()["job"]["id"]
    assert import_job["metadata"]["import_consumed_by_job_id"] == first_job_id
    next(job for job in state["jobs"] if job["id"] == first_job_id)["status"] = "failed"

    second = _client().post("/catalogs/mixed-quote", headers=_auth_headers(), json=body)
    assert second.status_code == 200, second.json()
    second_job_id = second.json()["job"]["id"]
    assert second_job_id != first_job_id
    assert import_job["status"] == "failed"
    assert import_job["metadata"]["import_consumed_by_job_id"] == second_job_id


def test_active_quote_limit_ignores_consumed_import_sources(monkeypatch):
    monkeypatch.setattr(index, "MAX_ACTIVE_QUOTE_JOBS_PER_USER", 3)
    monkeypatch.setattr(index, "db_list_quote_jobs", lambda _user_id: [
        {"id": "source-a", "status": "failed", "metadata": {"import_consumed_by_job_id": "final-a"}},
        {"id": "source-b", "status": "failed", "metadata": {"import_consumed_by_job_id": "final-b"}},
        {"id": "source-c", "status": "failed", "metadata": {"import_consumed_by_job_id": "final-c"}},
    ])

    index._enforce_active_quote_limit(7)


def test_mixed_quote_allows_imported_only_and_omitted_manifest_rows(
    monkeypatch, tmp_path
):
    state, _job, _manifest, _source, _objects, imported = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )

    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(),
        json=_valid_mixed_body([imported]),
    )

    assert response.status_code == 200, response.json()
    payload = json.loads(next(
        upload["content"] for upload in state["uploads"]
        if upload["path"].endswith("/input.json")
    ))
    assert payload["groups"] == []
    assert payload["item_count"] == 1
    assert len(payload["imported_source"]["items"]) == 1
    assert payload["sections"] == [{
        "id": "section-1", "title": "Recepción",
        "line_ids": ["legacy-import-1"],
    }]
    assert state["reserved_groups"] == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("other-user", 403),
        ("not-draft", 409),
        ("missing-row", 400),
        ("duplicate-row", 400),
        ("unknown-field", 400),
        ("negative-price", 400),
        ("nonfinite-price", 400),
        ("too-precise-price", 400),
        ("missing-currency", 400),
        ("second-import-id", 400),
        ("changed-source", 409),
        ("changed-hash", 409),
        ("changed-manifest", 409),
        ("changed-preview-metadata", 409),
        ("changed-source-path", 409),
    ),
)
def test_mixed_quote_rejects_invalid_imported_items_without_creating_job(
    monkeypatch, tmp_path, mutation, expected
):
    state, import_job, manifest, source_bytes, objects, item = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    items = [item]
    if mutation == "other-user":
        import_job["usuario_id"] = 8
    elif mutation == "not-draft":
        import_job["status"] = "queued"
    elif mutation == "missing-row":
        item["source_row"] = 999
    elif mutation == "duplicate-row":
        items.append(deepcopy(item))
    elif mutation == "unknown-field":
        item["source_path"] = import_job["input_path"]
    elif mutation == "negative-price":
        item["overrides"]["unit_price"] = "-1"
    elif mutation == "nonfinite-price":
        item["overrides"]["unit_price"] = "NaN"
    elif mutation == "too-precise-price":
        item["overrides"]["unit_price"] = "1.0000001"
    elif mutation == "missing-currency":
        item["source_currency"] = None
    elif mutation == "second-import-id":
        second = deepcopy(item)
        second["import_id"] = JOB_B_UUID
        second["source_row"] = 12
        items.append(second)
    elif mutation == "changed-source":
        objects[import_job["input_path"]] = source_bytes + b"changed"
    elif mutation == "changed-hash":
        import_job["metadata"]["import_source_hash"] = "0" * 64
    elif mutation == "changed-preview-metadata":
        import_job["metadata"]["import_preview_paths"] = {
            "11": "users/8/jobs/not-owned/preview/row-11.png",
        }
    elif mutation == "changed-source-path":
        import_job["input_path"] = f"users/7/jobs/{JOB_A_UUID}/other.xlsx"
    else:
        stored = json.loads(objects[import_job["metadata"]["import_manifest_path"]])
        stored["items"][0]["name"] = "Manifest changed"
        objects[import_job["metadata"]["import_manifest_path"]] = json.dumps(stored).encode()

    response = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body(items),
    )

    assert response.status_code == expected
    assert state["jobs"] == []
    assert state["uploads"] == []
    detail = response.json()["detail"]
    assert import_job["input_path"] not in detail
    assert manifest["source_hash"] not in detail


def test_mixed_quote_rejects_overridden_explicit_currency_without_creating_job(
    monkeypatch, tmp_path
):
    state, _job, _manifest, _source, _objects, item = (
        _imported_mixed_quote_case(monkeypatch, tmp_path, explicit_currency="USD")
    )
    item["source_currency"] = "EUR"

    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(),
        json=_valid_mixed_body([item]),
    )

    assert response.status_code == 400
    assert state["jobs"] == []


@pytest.mark.parametrize("stage", ("copy", "verify", "create", "queue"))
def test_mixed_quote_import_failures_do_not_queue_or_wake(
    monkeypatch, tmp_path, stage
):
    state, _job, _manifest, _source, objects, item = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    original_upload = index._storage_upload_bytes
    original_download = index._storage_download_bytes
    if stage == "copy":
        monkeypatch.setattr(
            index, "_storage_upload_bytes",
            lambda path, *_args: (_ for _ in ()).throw(RuntimeError("copy failed"))
            if path.endswith("/import-source.xlsx") else original_upload(path, *_args),
        )
    elif stage == "verify":
        monkeypatch.setattr(
            index, "_storage_download_bytes",
            lambda path: b"corrupt" if path.endswith("/import-source.xlsx") else original_download(path),
        )
    elif stage == "create":
        monkeypatch.setattr(
            index, "db_create_quote_job",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("create failed")),
        )
    else:
        monkeypatch.setattr(
            index, "db_queue_mixed_quote_job",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("queue failed")),
        )

    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(),
        json=_valid_mixed_body([item]),
    )

    assert response.status_code == 503
    assert "wake" not in state["events"]
    assert state["released"] == []
    assert state["deleted_jobs"] == ([] if stage == "create" else [state["jobs"][0]["id"]])


@pytest.mark.parametrize("restore_failure", ("false", "raise"))
def test_import_checkout_rollback_preserves_consumer_when_source_restore_fails_and_allows_retry(
    monkeypatch, tmp_path, restore_failure
):
    state, import_job, _manifest, _source, _objects, item = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    original_queue = index.db_queue_mixed_quote_job
    original_restore = index._restore_consumed_import_draft
    monkeypatch.setattr(
        index,
        "db_queue_mixed_quote_job",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("queue failed")),
    )
    if restore_failure == "false":
        monkeypatch.setattr(index, "_restore_consumed_import_draft", lambda *_args: False)
    else:
        monkeypatch.setattr(
            index,
            "_restore_consumed_import_draft",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("restore failed")),
        )

    failed = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body([item]),
    )

    assert failed.status_code == 503
    first_consumer = state["jobs"][0]
    first_consumer_id = first_consumer["id"]
    assert first_consumer_id not in state["deleted_jobs"]
    assert first_consumer["status"] == "failed"
    assert import_job["status"] == "failed"
    assert import_job["metadata"]["import_consumed_by_job_id"] == first_consumer_id
    assert any(job["id"] == first_consumer_id for job in state["jobs"])

    monkeypatch.setattr(index, "db_queue_mixed_quote_job", original_queue)
    monkeypatch.setattr(index, "_restore_consumed_import_draft", original_restore)
    retried = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body([item]),
    )

    assert retried.status_code == 200, retried.json()
    assert retried.json()["job"]["id"] != first_consumer_id
    assert import_job["metadata"]["import_consumed_by_job_id"] == retried.json()["job"]["id"]


@pytest.mark.parametrize("stage", ("readback", "reserve", "input-upload", "cas"))
def test_mixed_quote_post_copy_failures_leave_no_final_job_storage_orphans(
    monkeypatch, tmp_path, stage
):
    state, _job, _manifest, _source, objects, item = _imported_mixed_quote_case(
        monkeypatch, tmp_path
    )
    original_upload = index._storage_upload_bytes
    original_download = index._storage_download_bytes

    def delete_storage(paths):
        state["deleted_inputs"].extend(paths)
        for path in paths:
            objects.pop(path, None)

    monkeypatch.setattr(index, "_delete_storage_paths", delete_storage)
    if stage == "readback":
        monkeypatch.setattr(
            index,
            "_storage_download_bytes",
            lambda path: (_ for _ in ()).throw(RuntimeError("readback failed"))
            if path.endswith("/import-source.xlsx")
            else original_download(path),
        )
    elif stage == "reserve":
        monkeypatch.setattr(
            index,
            "db_reserve_mixed_cart",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("reserve failed")),
        )
    elif stage == "input-upload":
        def upload_then_fail(path, content, content_type="application/octet-stream"):
            original_upload(path, content, content_type)
            if path.endswith("/input.json"):
                raise RuntimeError("input upload failed")

        monkeypatch.setattr(index, "_storage_upload_bytes", upload_then_fail)
    else:
        monkeypatch.setattr(
            index,
            "db_queue_mixed_quote_job",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("CAS failed")),
        )

    request_items = [item]
    if stage == "reserve":
        request_items.insert(
            0,
            {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
        )

    response = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body(request_items),
    )

    assert response.status_code == 503
    assert len(state["jobs"]) == 1
    final_job_id = state["jobs"][0]["id"]
    final_prefix = f"users/7/jobs/{final_job_id}/"
    input_path = f"{final_prefix}input.json"
    source_copy_path = f"{final_prefix}import-source.xlsx"
    assert state["deleted_inputs"] == [input_path, source_copy_path]
    assert not any(path.startswith(final_prefix) for path in objects)
    assert state["released"] == ([final_job_id] if stage == "reserve" else [])
    assert state["deleted_jobs"] == [final_job_id]
    assert "wake" not in state["events"]


def test_mixed_quote_route_is_registered_before_supplier_quote_route():
    post_paths = [
        route.path for route in index.app.routes
        if "POST" in getattr(route, "methods", set())
    ]
    assert "/catalogs/mixed-quote" in post_paths
    assert post_paths.index("/catalogs/mixed-quote") < post_paths.index("/catalogs/{supplier}/quote")


def test_mixed_quote_requires_authentication_before_catalog_loading(monkeypatch):
    loaded = []
    monkeypatch.setattr(index, "_load_tarkett_catalog_cached", lambda: loaded.append("tarkett"))
    response = _client().post("/catalogs/mixed-quote", json=_valid_mixed_body())
    assert response.status_code == 401
    assert loaded == []


@pytest.mark.parametrize(
    "field",
    ("unit_price", "base_currency", "exchange_rate", "stock", "image_url", "product_url"),
)
def test_mixed_quote_rejects_unexpected_browser_fields_before_job_creation(monkeypatch, field):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    body = _valid_mixed_body()
    body["items"][0][field] = "tampered"
    response = _client().post("/catalogs/mixed-quote", headers=_auth_headers(), json=body)
    assert response.status_code == 400
    assert "Campo mixto no permitido" in response.json()["detail"]
    assert state["jobs"] == []
    assert state["uploads"] == []


def test_mixed_quote_checks_subscription_with_integer_user_id_before_catalog_loading(monkeypatch):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(index, "_require_active_subscription", lambda user_id: calls.append(user_id))
    monkeypatch.setattr(
        index, "_load_tarkett_catalog_cached",
        lambda: (_ for _ in ()).throw(RuntimeError("stop after subscription")),
    )
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert calls == [7]
    assert type(calls[0]) is int


@pytest.mark.parametrize(
    "body",
    ([], {"items": []}),
)
def test_mixed_quote_rejects_invalid_container_or_line_count_before_dependencies(monkeypatch, body):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    response = _client().post("/catalogs/mixed-quote", headers=_auth_headers(), json=body)
    assert response.status_code == 400
    assert state["requested_catalogs"] == []
    assert state["rate_calls"] == 0
    assert state["jobs"] == []
    assert state["uploads"] == []


@pytest.mark.parametrize(
    "raw",
    (
        b'{"items":[],"items":[]}',
        b'{"items":[{"catalog":"tarkett","code":"25731726","quantity":NaN}]}',
        b'{"items":[]} trailing-bytes',
    ),
)
def test_mixed_quote_rejects_noncanonical_json_before_catalog_loading(monkeypatch, raw):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    response = _client().post(
        "/catalogs/mixed-quote",
        headers={**_auth_headers(), "content-type": "application/json"},
        content=raw,
    )
    assert response.status_code == 400
    assert state["requested_catalogs"] == []
    assert state["rate_calls"] == 0
    assert state["jobs"] == []


@pytest.mark.parametrize(
    "field",
    ("unit_price", "supplier", "metadata", "status", "usuario_id", "source_type"),
)
def test_mixed_quote_rejects_every_unexpected_top_level_field_before_dependencies(
    monkeypatch, field
):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    body = _valid_mixed_body()
    body[field] = "tampered"
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=body
    )
    assert response.status_code == 400
    assert "Campo de cotizacion no permitido" in response.json()["detail"]
    assert state["loaded_catalogs"] == []
    assert state["rate_calls"] == 0
    assert state["jobs"] == []
    assert state["uploads"] == []


def test_mixed_quote_rejects_request_byte_limit_with_explicit_reason(monkeypatch):
    assert primary_index.MAX_QUOTE_REQUEST_BYTES == 25 * 1024 * 1024
    primary_index.app.dependency_overrides[primary_index.get_current_user] = (
        lambda: {"id": 7}
    )
    monkeypatch.setattr(
        primary_index, "_require_active_subscription", lambda _user_id: None
    )
    try:
        response = TestClient(primary_index.app).post(
            "/catalogs/mixed-quote",
            headers={
                "content-length": str(primary_index.MAX_QUOTE_REQUEST_BYTES + 1)
            },
            content=b"{}",
        )
    finally:
        primary_index.app.dependency_overrides.clear()
    assert response.status_code == 413
    assert "bytes" in response.json()["detail"].lower()

    class StreamingRequest:
        headers = {}

        async def stream(self):
            yield b"x" * (primary_index.MAX_QUOTE_REQUEST_BYTES // 2)
            yield b"x" * (primary_index.MAX_QUOTE_REQUEST_BYTES // 2 + 1)

    with pytest.raises(primary_index.HTTPException) as exc:
        asyncio.run(primary_index._read_mixed_quote_body(StreamingRequest()))
    assert exc.value.status_code == 413
    assert "bytes" in exc.value.detail.lower()


def test_mixed_reservation_normalizer_accepts_700_lines_above_old_limit():
    normalized = primary_index._normalize_mixed_reservation_groups(
        [{
            "catalog": "alma",
            "items": [
                {
                    "identity": f"alma:large-{position}",
                    "sku": f"ALMA-LARGE-{position}",
                    "quantity": "1.000000",
                    "stock": "5.000000",
                }
                for position in range(700)
            ],
        }]
    )

    assert len(normalized[0]["items"]) == 700


@pytest.mark.parametrize(
    "row",
    (
        {"catalog": "tarkett", "code": "x" * 1001, "quantity": "1"},
        {"catalog": "tarkett", "code": "bad\u0000code", "quantity": "1"},
        {
            "catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1",
            "add_on_option_ids": [f"option-{number}" for number in range(201)],
        },
        {"catalog": "tarkett", "code": "25731726", "quantity": "1" * 65},
    ),
)
def test_mixed_quote_rejects_bounded_identity_options_and_quantity_before_dependencies(monkeypatch, row):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body([row])
    )
    assert response.status_code == 400
    assert state["requested_catalogs"] == []
    assert state["rate_calls"] == 0
    assert state["jobs"] == []
    assert state["uploads"] == []


def test_mixed_quote_creates_one_authoritative_job_upload_queue_and_wake(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    body = _valid_mixed_body([
        {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
        {"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "2"},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "3"},
    ])
    body["sections"] = [
        {
            "id": "section-1",
            "title": "Recepción",
            "item_keys": [
                "tarkett:25731726",
                'sonara:["sonara:desk-1","",[]]',
            ],
        },
        {
            "id": "section-2",
            "title": "Privados",
            "item_keys": ['alma:["alma:desk-1","",[]]'],
        },
    ]

    response = _client().post("/catalogs/mixed-quote", headers=_auth_headers(), json=body)

    assert response.status_code == 200, response.json()
    assert set(response.json()) == {"mensaje", "job"}
    assert state["events"] == ["create_job", "reserve_mixed", "upload", "queue", "wake"]
    assert len(state["jobs"]) == 1
    assert len(state["uploads"]) == 1
    payload = json.loads(state["uploads"][0]["content"])
    assert payload["source_type"] == "mixed_catalog_cart"
    assert payload["item_count"] == 3
    assert payload["sections"] == [
        {
            "id": "section-1",
            "title": "Recepción",
            "line_ids": ["legacy-1", "legacy-2"],
        },
        {
            "id": "section-2",
            "title": "Privados",
            "line_ids": ["legacy-3"],
        },
    ]
    assert {group["catalog"] for group in payload["groups"]} == {"tarkett", "sonara", "alma"}
    assert payload["groups"][0]["items"][0]["unit_price"] == "472.63"
    assert response.json()["job"]["id"] == state["jobs"][0]["id"]
    metadata = state["jobs"][0]["metadata"]
    assert metadata["source_type"] == "mixed_catalog_cart"
    assert metadata["mixed_item_count"] == 3
    assert metadata["mixed_section_count"] == 2
    assert metadata["catalog_item_counts"] == {"tarkett": 1, "sonara": 1, "alma": 1}
    assert metadata["catalog_source_hashes"] == {
        group["catalog"]: group["catalog_source_hash"] for group in payload["groups"]
    }
    assert metadata["quote_currency"] == "MXN"
    assert metadata["rate_summary"] == payload["rate_summary"]
    assert metadata["auto_electrification_rate"] == payload["auto_electrification_rate"]


def test_mixed_quote_loads_exactly_the_requested_catalogs(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    response = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body([
            {
                "catalog": "offiho", "inventory_key": "OHE-405 NEGRO ALUFSEN",
                "quantity": "1",
            },
            {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1"},
        ]),
    )
    assert response.status_code == 200
    assert state["loaded_catalogs"] == ["offiho", "alma"]
    assert len(state["jobs"]) == 1
    assert len(state["uploads"]) == 1


def test_mixed_quote_skips_reservation_rpc_for_made_to_order_project(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    original_loader = index._load_supplier_catalog_cached

    def made_to_order_catalog(supplier):
        catalog = deepcopy(original_loader(supplier))
        catalog["items"][0]["availability_type"] = "made_to_order"
        catalog["items"][0]["stock"] = None
        return catalog

    monkeypatch.setattr(index, "_load_supplier_catalog_cached", made_to_order_catalog)
    monkeypatch.setattr(
        index,
        "db_reserve_mixed_cart",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("No debe reservar un proyecto completamente sobre pedido")
        ),
    )

    response = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body([{
            "catalog": "alma",
            "internal_id": "alma:desk-1",
            "quantity": "1",
        }]),
    )

    assert response.status_code == 200, response.json()
    assert state["reserved_groups"] == []
    assert state["events"] == ["create_job", "upload", "queue", "wake"]


def test_mixed_quote_uses_the_shared_enqueue_transaction_once(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    original = index._enqueue_mixed_payload
    calls = []

    def tracked(**kwargs):
        calls.append(kwargs["cart_payload"]["source_type"])
        return original(**kwargs)

    monkeypatch.setattr(index, "_enqueue_mixed_payload", tracked)

    response = _client().post(
        "/catalogs/mixed-quote",
        headers=_auth_headers(),
        json=_valid_mixed_body(),
    )

    assert response.status_code == 200, response.json()
    assert calls == ["mixed_catalog_cart"]
    assert state["events"] == [
        "create_job", "reserve_mixed", "upload", "queue", "wake",
    ]


def _mixed_snapshot_payload(*, catalog="alma", quantity="1.000000", stock="5.000000", warning=None):
    line = {
        "catalog": catalog,
        "quantity": quantity,
        "warnings": [] if warning is None else [warning],
        "reservation": {
            "identity": f"{catalog}:desk-1", "sku": "DESK-1",
            "quantity": quantity, "stock": stock,
        },
    }
    return {"groups": [{"catalog": catalog, "items": [line]}]}


def test_apply_mixed_snapshot_validates_completeness_before_mutating_any_line():
    payload = _mixed_snapshot_payload()
    with pytest.raises(ValueError, match="Snapshot de reserva mixta invalido"):
        index._apply_mixed_reservation_snapshot(payload, [])
    line = payload["groups"][0]["items"][0]
    assert "reserved_quantity" not in line
    assert "available_after_reservations" not in line


def test_apply_mixed_snapshot_maps_one_aggregated_identity_to_all_configurations():
    payload = _mixed_snapshot_payload(quantity="1.000000")
    second = json.loads(json.dumps(payload["groups"][0]["items"][0]))
    second["quantity"] = "2.000000"
    second["reservation"]["quantity"] = "2.000000"
    payload["groups"][0]["items"].append(second)
    index._apply_mixed_reservation_snapshot(payload, [{
        "catalog": "alma", "identity": "alma:desk-1",
        "reserved_before": "1.000000", "available_before": "4.000000",
        "insufficient": False, "reserved_by_others": True,
    }])
    assert [line["reserved_quantity"] for line in payload["groups"][0]["items"]] == [
        "1.000000", "1.000000"
    ]
    assert all(line["reserved_by_others"] is True for line in payload["groups"][0]["items"])


def test_apply_mixed_snapshot_accepts_empty_snapshot_for_made_to_order_lines():
    payload = _mixed_snapshot_payload()
    payload["groups"][0]["items"][0]["reservation"] = None
    index._apply_mixed_reservation_snapshot(payload, [])
    assert "reserved_quantity" not in payload["groups"][0]["items"][0]


def test_apply_mixed_snapshot_tarkett_insufficient_fails_closed_before_mutation():
    payload = _mixed_snapshot_payload(catalog="tarkett", quantity="2.000000", stock="5.000000")
    with pytest.raises(ValueError, match="tarkett:tarkett:desk-1 sin existencia suficiente"):
        index._apply_mixed_reservation_snapshot(payload, [{
            "catalog": "tarkett", "identity": "tarkett:desk-1",
            "reserved_before": "5.000000", "available_before": "0.000000",
            "insufficient": True, "reserved_by_others": True,
        }])
    assert "reserved_quantity" not in payload["groups"][0]["items"][0]


def test_apply_mixed_snapshot_keeps_normalized_insufficient_warning_exactly_once():
    payload = _mixed_snapshot_payload(
        catalog="offiho", quantity="2.000000", stock="5.000000",
        warning="  EXISTÉNCIA insuficiente;   verificar disponibilidad. ",
    )
    index._apply_mixed_reservation_snapshot(payload, [{
        "catalog": "offiho", "identity": "offiho:desk-1",
        "reserved_before": "5.000000", "available_before": "0.000000",
        "insufficient": True, "reserved_by_others": True,
    }])
    warnings = payload["groups"][0]["items"][0]["warnings"]
    normalized = [" ".join("".join(
        character for character in index.unicodedata.normalize("NFKD", warning.casefold())
        if not index.unicodedata.combining(character)
    ).split()) for warning in warnings]
    assert normalized.count("existencia insuficiente; verificar disponibilidad.") == 1


@pytest.mark.parametrize(
    "case",
    ("extra_key", "duplicate_key", "nonfinite_decimal", "non_boolean", "bad_available", "bad_insufficient"),
)
def test_apply_mixed_snapshot_rejects_contract_violations_without_partial_mutation(case):
    payload = _mixed_snapshot_payload()
    second = json.loads(json.dumps(payload["groups"][0]["items"][0]))
    second["reservation"]["identity"] = "alma:desk-2"
    second["reservation"]["sku"] = "DESK-2"
    payload["groups"][0]["items"].append(second)
    snapshot = [
        {
            "catalog": "alma", "identity": "alma:desk-1",
            "reserved_before": "0.000000", "available_before": "5.000000",
            "insufficient": False, "reserved_by_others": False,
        },
        {
            "catalog": "alma", "identity": "alma:desk-2",
            "reserved_before": "0.000000", "available_before": "5.000000",
            "insufficient": False, "reserved_by_others": False,
        },
    ]
    if case == "extra_key":
        snapshot[1]["unexpected"] = True
    elif case == "duplicate_key":
        snapshot[1] = dict(snapshot[0])
    elif case == "nonfinite_decimal":
        snapshot[1]["reserved_before"] = "NaN"
    elif case == "non_boolean":
        snapshot[1]["reserved_by_others"] = 0
    elif case == "bad_available":
        snapshot[1]["available_before"] = "4.000000"
    else:
        snapshot[1]["insufficient"] = True

    with pytest.raises(ValueError, match="Snapshot de reserva mixta invalido"):
        index._apply_mixed_reservation_snapshot(payload, snapshot)

    for line in payload["groups"][0]["items"]:
        assert not {
            "reserved_quantity", "available_after_reservations", "reserved_by_others",
        } & set(line)


@pytest.mark.parametrize(
    ("stage", "expected"),
    (
        ("reserve", ["create_job", "reserve", "release", "delete_input", "delete_job"]),
        ("upload", ["create_job", "reserve", "upload", "release", "delete_input", "delete_job"]),
        ("queue", ["create_job", "reserve", "upload", "queue", "release", "delete_input", "delete_job"]),
    ),
)
def test_mixed_quote_failure_compensates_all_families(monkeypatch, stage, expected):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    state["events"].clear()

    def reserve(_user_id, _job_id, groups):
        state["events"].append("reserve")
        if stage == "reserve":
            raise RuntimeError("reserve failed")
        return [{
            "catalog": group["catalog"], "identity": item["identity"],
            "reserved_before": "0.000000", "available_before": item["stock"],
            "insufficient": False, "reserved_by_others": False,
        } for group in groups for item in group["items"]]

    def upload(*_args):
        state["events"].append("upload")
        if stage == "upload":
            raise RuntimeError("upload failed")

    def queue(*_args):
        state["events"].append("queue")
        if stage == "queue":
            raise RuntimeError("queue failed")
        return {"id": JOB_MIXED_UUID, "status": "queued"}

    monkeypatch.setattr(index, "db_reserve_mixed_cart", reserve)
    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    monkeypatch.setattr(index, "db_queue_mixed_quote_job", queue)
    monkeypatch.setattr(index, "db_release_mixed_cart", lambda job_id: state["events"].append("release"))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: state["events"].append("delete_job"))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: state["events"].append("delete_input"))

    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert state["events"] == expected


def test_mixed_quote_tarkett_concurrent_shortage_compensates_before_upload(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    stock = "970.200000"
    monkeypatch.setattr(index, "db_reserve_mixed_cart", lambda *_args: [{
        "catalog": "tarkett", "identity": "25731726",
        "reserved_before": stock, "available_before": "0.000000",
        "insufficient": True, "reserved_by_others": True,
    }])
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert len(state["released"]) == 1
    assert len(state["deleted_jobs"]) == 1
    assert state["uploads"] == []
    assert "queue" not in state["events"]
    assert "wake" not in state["events"]


def test_mixed_quote_real_dev_release_wins_before_real_queue_cas(monkeypatch):
    real_create = index.db_create_quote_job
    real_reserve = index.db_reserve_mixed_cart
    real_release = index.db_release_mixed_cart
    real_queue = index.db_queue_mixed_quote_job
    real_delete = index.db_delete_quote_job
    route_state = _mock_mixed_quote_dependencies(monkeypatch)
    dev_state = {
        "quote_jobs": [], "tarkett_reservations": [],
        "offiho_reservations": [], "catalog_reservations": [],
    }
    configure_thread_safe_dev_store(monkeypatch, dev_state)
    releases = []
    wakes = []

    def tracked_release(job_id):
        result = real_release(job_id)
        releases.append(result)
        return result

    def upload(path, content, content_type):
        route_state["uploads"].append({
            "path": path, "content": content, "content_type": content_type,
        })
        job_id = path.split("/jobs/", 1)[1].split("/", 1)[0]
        index.db_release_mixed_cart(job_id)

    monkeypatch.setattr(index, "db_create_quote_job", real_create)
    monkeypatch.setattr(index, "db_reserve_mixed_cart", real_reserve)
    monkeypatch.setattr(index, "db_release_mixed_cart", tracked_release)
    monkeypatch.setattr(index, "db_queue_mixed_quote_job", real_queue)
    monkeypatch.setattr(index, "db_delete_quote_job", real_delete)
    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    monkeypatch.setattr(index, "_delete_storage_paths", lambda _paths: None)
    monkeypatch.setattr(index, "_wake_worker", lambda: wakes.append("wake"))
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert wakes == []
    assert all(job["status"] != "queued" for job in dev_state["quote_jobs"])
    assert not any(
        row["status"] == "active"
        for table in ("tarkett_reservations", "offiho_reservations", "catalog_reservations")
        for row in dev_state[table]
    )
    assert releases == [
        {"tarkett": 1, "offiho": 0, "supplier": 0},
        {"tarkett": 0, "offiho": 0, "supplier": 0},
    ]


def test_mixed_quote_offiho_shortage_uploads_one_normalized_warning(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    original_builder = index.build_mixed_catalog_cart_payload

    def builder(*args, **kwargs):
        payload = original_builder(*args, **kwargs)
        payload["groups"][0]["items"][0]["warnings"].append(
            "  EXISTÉNCIA insuficiente;   verificar disponibilidad. "
        )
        return payload

    monkeypatch.setattr(index, "build_mixed_catalog_cart_payload", builder)
    monkeypatch.setattr(index, "db_reserve_mixed_cart", lambda *_args: [{
        "catalog": "offiho", "identity": "OHE-405 NEGRO ALUFSEN",
        "reserved_before": "8.000000", "available_before": "0.000000",
        "insufficient": True, "reserved_by_others": True,
    }])
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(),
        json=_valid_mixed_body([{
            "catalog": "offiho", "inventory_key": "OHE-405 NEGRO ALUFSEN",
            "quantity": "2",
        }]),
    )
    assert response.status_code == 200, {"body": response.json(), "events": state["events"]}
    payload = json.loads(state["uploads"][0]["content"])
    warnings = payload["groups"][0]["items"][0]["warnings"]
    normalized = [" ".join("".join(
        character for character in index.unicodedata.normalize("NFKD", warning.casefold())
        if not index.unicodedata.combining(character)
    ).split()) for warning in warnings]
    assert normalized.count("existencia insuficiente; verificar disponibilidad.") == 1


def test_mixed_quote_marks_cleanup_pending_when_release_fails(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    marked = []
    monkeypatch.setattr(
        index, "db_queue_mixed_quote_job",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("queue failed")),
    )
    monkeypatch.setattr(
        index, "db_release_mixed_cart",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("release failed")),
    )
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: marked.append(updates))
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert marked == [{
        "status": "failed",
        "error_message": (
            "cleanup_pending:release_reservations|enqueue:queue:RuntimeError"
        ),
    }]
    assert state["deleted_jobs"] == []
    assert state["deleted_inputs"] == []
    assert "wake" not in state["events"]


def test_mixed_quote_retains_failed_job_when_input_delete_fails(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    marked = []
    monkeypatch.setattr(
        index, "db_queue_mixed_quote_job",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("queue failed")),
    )
    monkeypatch.setattr(
        index, "_delete_storage_paths",
        lambda _paths: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
    )

    def mark_failed(job_id, updates):
        job = next(job for job in state["jobs"] if job["id"] == job_id)
        job.update(updates)
        marked.append(updates)
        return job

    monkeypatch.setattr(index, "db_update_quote_job", mark_failed)
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert len(state["released"]) == 1
    expected_error = "cleanup_pending:delete_input|enqueue:queue:RuntimeError"
    assert marked == [{"status": "failed", "error_message": expected_error}]
    assert state["deleted_jobs"] == []
    retained = state["jobs"][0]
    assert retained["status"] == "failed"
    assert retained["error_message"] == expected_error
    assert retained["input_path"].endswith("/input.json")
    assert retained["metadata"]["source_type"] == "mixed_catalog_cart"
    assert "wake" not in state["events"]


def test_mixed_quote_marks_cleanup_pending_when_job_delete_fails(monkeypatch):
    state = _mock_mixed_quote_dependencies(monkeypatch)
    marked = []
    monkeypatch.setattr(
        index, "db_queue_mixed_quote_job",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("queue failed")),
    )
    monkeypatch.setattr(
        index, "db_delete_quote_job",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("delete failed")),
    )
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: marked.append(updates))
    response = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body()
    )
    assert response.status_code == 503
    assert len(state["released"]) == 1
    assert marked == [{
        "status": "failed",
        "error_message": "cleanup_pending:delete_job|enqueue:queue:RuntimeError",
    }]
    assert len(state["deleted_inputs"]) == 1
    assert "wake" not in state["events"]


@pytest.mark.parametrize(
    "cleanup_error",
    (
        "cleanup_pending:release_reservations",
        "cleanup_pending:delete_input",
        "cleanup_pending:delete_job",
    ),
)
def test_retry_rejects_cleanup_pending_jobs_without_update_or_wake(monkeypatch, cleanup_error):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: {
        "id": job_id, "usuario_id": 7, "status": "failed",
        "input_path": "users/7/jobs/job-1/input.json", "error_message": cleanup_error,
    })
    monkeypatch.setattr(index, "db_update_quote_job", lambda *_args: calls.append("update"))
    monkeypatch.setattr(index, "_wake_worker", lambda: calls.append("wake"))
    response = _client().post("/cotizaciones/job-1/retry", headers=_auth_headers())
    assert response.status_code == 409
    assert calls == []


@pytest.mark.parametrize("path", ["/tarkett/catalog", "/offiho/catalog"])
def test_catalog_routes_require_token(path):
    resp = _client().get(path)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token no proporcionado"


@pytest.mark.parametrize("path", ["/tarkett/catalog", "/offiho/catalog"])
def test_catalog_routes_reject_expired_subscription(monkeypatch, path):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_suscripcion_by_usuario",
        lambda usuario_id: {
            "id": 1,
            "usuario_id": usuario_id,
            "estado": "activa",
            "plan": "mensual",
            "fecha_fin": "2020-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(index, "_enforce_active_quote_limit", lambda *_args, **_kwargs: None)
    resp = _client().get(path, headers=_auth_headers())

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Suscripcion no activa"


def test_active_quote_limit_uses_persisted_jobs(monkeypatch):
    monkeypatch.setattr(index, "MAX_ACTIVE_QUOTE_JOBS_PER_USER", 3)
    monkeypatch.setattr(
        index,
        "db_list_quote_jobs",
        lambda _usuario_id: [
            {"id": "draft-1", "status": "draft"},
            {"id": "queued-1", "status": "queued"},
            {"id": "processing-1", "status": "processing"},
            {"id": "completed-1", "status": "completed"},
        ],
    )

    with pytest.raises(index.HTTPException) as exc:
        index._enforce_active_quote_limit(7)

    assert exc.value.status_code == 429


def test_active_quote_limit_excludes_current_job(monkeypatch):
    monkeypatch.setattr(index, "MAX_ACTIVE_QUOTE_JOBS_PER_USER", 3)
    monkeypatch.setattr(
        index,
        "db_list_quote_jobs",
        lambda _usuario_id: [
            {"id": "job-1", "status": "draft"},
            {"id": "queued-1", "status": "queued"},
            {"id": "processing-1", "status": "processing"},
        ],
    )

    index._enforce_active_quote_limit(7, exclude_job_id="job-1")


@pytest.mark.parametrize("supplier", ["tarkett", "offiho"])
def test_catalog_quote_routes_require_token_before_upload(monkeypatch, supplier):
    monkeypatch.setattr(
        index,
        "_storage_upload_bytes",
        lambda *args: (_ for _ in ()).throw(AssertionError("unauthenticated quote must not upload")),
    )
    body = _valid_tarkett_body() if supplier == "tarkett" else _valid_offiho_body()

    resp = _client().post(f"/{supplier}/quote", json=body)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token no proporcionado"


@pytest.mark.parametrize("supplier", ["tarkett", "offiho"])
def test_catalog_quote_routes_reject_expired_subscription_before_upload(monkeypatch, supplier):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_suscripcion_by_usuario",
        lambda usuario_id: {
            "id": 1,
            "usuario_id": usuario_id,
            "estado": "activa",
            "plan": "mensual",
            "fecha_fin": "2020-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        index,
        "_storage_upload_bytes",
        lambda *args: (_ for _ in ()).throw(AssertionError("expired subscription must not upload")),
    )
    body = _valid_tarkett_body() if supplier == "tarkett" else _valid_offiho_body()

    resp = _client().post(f"/{supplier}/quote", headers=_auth_headers(), json=body)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Suscripcion no activa"


def test_tarkett_catalog_returns_base_stock_and_other_user_reservations(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_tarkett_catalog_cached", _mock_tarkett_catalog)
    monkeypatch.setattr(
        index,
        "db_list_tarkett_reservations",
        lambda status="active": [
            {"usuario_id": 7, "product_code": "25731726", "quantity": 1, "status": "active"},
            {"usuario_id": 8, "product_code": "25731726", "quantity": "2.5", "status": "active"},
        ],
    )

    resp = _client().get("/tarkett/catalog", headers=_auth_headers())

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["available_quantity"] == 970.2
    assert item["reserved_quantity"] == 3.5
    assert item["reserved_by_others"] is True
    assert item["product_url"] == "https://tarkett.com.mx/producto/cadiz/"
    assert item["collection"] == "Aurea Tech"


def test_tarkett_catalog_cache_reloads_default_file_mtime(monkeypatch, tmp_path):
    catalog_path = tmp_path / "tarkett_catalog.json"

    def write_catalog(source_hash, name, mtime):
        catalog_path.write_text(
            index.json.dumps(
                {
                    "source_hash": source_hash,
                    "generated_at": "2026-07-08T00:00:00+00:00",
                    "items": [
                        {
                            "code": "25731726",
                            "name": name,
                            "unit": "MTK - metro cuadrado",
                            "available_quantity": 1,
                            "product_url": "",
                            "image_url": "",
                            "match_status": "unmatched",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        os.utime(catalog_path, (mtime, mtime))

    write_catalog("hash-1", "Catalogo inicial", 1_800_000_001)
    monkeypatch.setattr(index, "TARKETT_CATALOG_PATH", "", raising=False)
    monkeypatch.setattr(index, "CATALOG_PATH", catalog_path, raising=False)
    monkeypatch.setattr(index, "_TARKETT_CATALOG_CACHE", {"path": None, "mtime": None, "catalog": None})

    first = index._load_tarkett_catalog_cached()
    write_catalog("hash-2", "Catalogo actualizado", 1_800_000_002)
    second = index._load_tarkett_catalog_cached()

    assert first["source_hash"] == "hash-1"
    assert second["source_hash"] == "hash-2"
    assert second["items"][0].name == "Catalogo actualizado"


def test_tarkett_quote_creates_json_job_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    uploaded = {}
    created = {}
    reservations = {}
    monkeypatch.setattr(index, "_load_tarkett_catalog_cached", _mock_tarkett_catalog)
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)
    monkeypatch.setattr(index, "_wake_worker", lambda: None)
    monkeypatch.setattr(index, "_enforce_quote_history_limit", lambda usuario_id: [])

    def fake_upload(path, content, content_type="application/octet-stream"):
        uploaded.update({"path": path, "content": content, "content_type": content_type})

    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        created.update({"usuario_id": usuario_id, "template": template, "metadata": metadata, "input_path": input_path, "job_id": job_id})
        return {"id": job_id, "usuario_id": usuario_id, "status": "draft", "metadata": metadata, "input_path": input_path}

    def fake_reserve(usuario_id, quote_job_id, lines):
        reservations.update({"usuario_id": usuario_id, "quote_job_id": quote_job_id, "lines": lines})
        return []

    monkeypatch.setattr(index, "_storage_upload_bytes", fake_upload)
    monkeypatch.setattr(index, "db_create_quote_job", fake_create)
    monkeypatch.setattr(index, "db_create_tarkett_reservations", fake_reserve)
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: {"id": job_id, **updates})

    resp = _client().post(
        "/tarkett/quote",
        headers=_auth_headers(),
        json={
            "proyecto": "Proyecto Tarkett",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "descuento": 40,
            "items": [{"code": "25731726", "quantity": "2.5"}],
        },
    )

    assert resp.status_code == 200
    assert uploaded["path"].endswith("/input.json")
    assert uploaded["content_type"] == "application/json"
    payload = index.json.loads(uploaded["content"].decode("utf-8"))
    assert payload["source_type"] == "tarkett_cart"
    assert payload["items"][0]["unit_price"] == 472.63
    assert payload["items"][0]["price_source"] == "tarkettnet_code_match"
    assert created["metadata"]["source_type"] == "tarkett_cart"
    assert created["metadata"]["input_extension"] == ".json"
    assert created["metadata"]["storage_provider"] in {"supabase", "r2"}
    assert created["metadata"]["input_storage_provider"] == created["metadata"]["storage_provider"]
    assert created["metadata"]["image_provider"] == "pillow"
    assert reservations["quote_job_id"] == created["job_id"]
    assert reservations["lines"][0]["code"] == "25731726"


def test_tarkett_quote_rejects_unknown_code(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_tarkett_catalog_cached", _mock_tarkett_catalog)

    resp = _client().post(
        "/tarkett/quote",
        headers=_auth_headers(),
        json={
            "proyecto": "Proyecto Tarkett",
            "cliente": "Cliente",
            "correo": "cliente@example.com",
            "telefono": "555",
            "direccion": "Direccion",
            "razon_social": "Empresa SA",
            "items": [{"code": "missing", "quantity": 1}],
        },
    )

    assert resp.status_code == 400
    assert "no encontrado" in resp.json()["detail"]


def _mock_offiho_catalog(available_quantity=0, unit_price=7999):
    item = OffihoCatalogItem(
        inventory_key="OHE-405 NEGRO ALUFSEN",
        code="OHE-405",
        name="ALUFSEN",
        variant="NEGRO",
        unit="PZA",
        pieces_per_box=Decimal("1"),
        available_quantity=Decimal(str(available_quantity)),
        unit_price=Decimal(str(unit_price)),
    )
    return {
        "source_hash": "offiho-catalog-hash",
        "generated_at": "2026-07-09T18:00:00+00:00",
        "source_row_count": 1287,
        "duplicate_row_count": 80,
        "unique_item_count": 1207,
        "items": [item],
        "by_inventory_key": {item.inventory_key: item},
    }


def _valid_offiho_body(quantity=1):
    return {
        "proyecto": "Proyecto Offiho",
        "cliente": "Cliente",
        "correo": "cliente@example.com",
        "telefono": "5551234567",
        "direccion": "Guadalajara",
        "razon_social": "Cliente SA de CV",
        "descuento": 40,
        "items": [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": quantity}],
    }


def test_offiho_catalog_returns_1288_items_with_catalog_prices_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "db_list_offiho_reservations", lambda status="active": [])

    resp = _client().get("/offiho/catalog", headers=_auth_headers())

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1288
    assert len(payload["items"]) == 1288
    assert payload["source_row_count"] == 1368
    assert payload["duplicate_row_count"] == 80
    assert payload["unique_item_count"] == 1288
    item = payload["items"][0]
    assert {"unit_price", "available_quantity", "product_url", "image_url", "is_out_of_stock", "reserved_quantity", "reserved_by_others"} <= set(item)


def test_offiho_catalog_returns_exhausted_item_and_other_user_reservation(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", _mock_offiho_catalog)
    monkeypatch.setattr(
        index,
        "db_list_offiho_reservations",
        lambda status="active": [
            {"usuario_id": 7, "product_code": "OHE-405 NEGRO ALUFSEN", "quantity": 1, "status": "active"},
            {"usuario_id": 8, "product_code": "OHE-405 NEGRO ALUFSEN", "quantity": 2, "status": "active"},
        ],
    )

    resp = _client().get("/offiho/catalog", headers=_auth_headers())

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["unit_price"] == 7999
    assert item["available_quantity"] == 0
    assert item["is_out_of_stock"] is True
    assert item["reserved_quantity"] == 3
    assert item["reserved_by_others"] is True


@pytest.mark.parametrize(
    ("supplier", "path_attr", "cache_attr", "loader_attr"),
    [
        ("offiho", "OFFIHO_CATALOG_PATH", "_OFFIHO_CATALOG_CACHE", "load_offiho_catalog"),
        ("tarkett", "TARKETT_CATALOG_PATH", "_TARKETT_CATALOG_CACHE", "load_tarkett_catalog"),
    ],
)
def test_catalog_cache_reloads_when_content_hash_changes_with_same_mtime(
    monkeypatch, tmp_path, supplier, path_attr, cache_attr, loader_attr
):
    catalog_path = tmp_path / f"{supplier}_catalog.json"
    catalog_path.write_text("first", encoding="utf-8")
    loaded = []

    def fake_load(path):
        content = Path(path).read_text(encoding="utf-8")
        loaded.append(content)
        if supplier == "offiho":
            catalog = _mock_offiho_catalog()
        else:
            catalog = _mock_tarkett_catalog()
        return {**catalog, "source_hash": f"hash-{content}"}

    monkeypatch.setattr(index, path_attr, str(catalog_path), raising=False)
    monkeypatch.setattr(index, loader_attr, fake_load)
    monkeypatch.setattr(index, cache_attr, {"path": None, "fingerprint": None, "catalog": None})
    fixed_mtime = 1_800_000_001
    os.utime(catalog_path, (fixed_mtime, fixed_mtime))
    load_cached = getattr(index, f"_load_{supplier}_catalog_cached")
    first = load_cached()
    catalog_path.write_text("second", encoding="utf-8")
    os.utime(catalog_path, (fixed_mtime, fixed_mtime))
    second = load_cached()

    assert first["source_hash"] == "hash-first"
    assert second["source_hash"] == "hash-second"
    assert loaded == ["first", "second"]
    assert getattr(index, cache_attr)["fingerprint"]["sha256"] == hashlib.sha256(b"second").hexdigest()


@pytest.mark.parametrize(
    ("supplier", "path_attr", "cache_attr", "loader_attr"),
    [
        ("offiho", "OFFIHO_CATALOG_PATH", "_OFFIHO_CATALOG_CACHE", "load_offiho_catalog"),
        ("tarkett", "TARKETT_CATALOG_PATH", "_TARKETT_CATALOG_CACHE", "load_tarkett_catalog"),
    ],
)
def test_catalog_cache_keeps_last_valid_catalog_after_corrupt_reload(
    monkeypatch, tmp_path, supplier, path_attr, cache_attr, loader_attr
):
    catalog_path = tmp_path / f"{supplier}_catalog.json"
    catalog_path.write_text("valid", encoding="utf-8")

    def fake_load(path):
        if Path(path).read_text(encoding="utf-8") == "corrupt":
            raise ValueError("corrupt catalog payload")
        return _mock_offiho_catalog() if supplier == "offiho" else _mock_tarkett_catalog()

    monkeypatch.setattr(index, path_attr, str(catalog_path), raising=False)
    monkeypatch.setattr(index, loader_attr, fake_load)
    monkeypatch.setattr(index, cache_attr, {"path": None, "fingerprint": None, "catalog": None})
    load_cached = getattr(index, f"_load_{supplier}_catalog_cached")

    valid = load_cached()
    catalog_path.write_text("corrupt", encoding="utf-8")
    retained = load_cached()

    assert retained is valid


@pytest.mark.parametrize(
    ("supplier", "path_attr", "cache_attr", "loader_attr"),
    [
        ("offiho", "OFFIHO_CATALOG_PATH", "_OFFIHO_CATALOG_CACHE", "load_offiho_catalog"),
        ("tarkett", "TARKETT_CATALOG_PATH", "_TARKETT_CATALOG_CACHE", "load_tarkett_catalog"),
    ],
)
def test_catalog_cache_without_valid_catalog_rejects_corrupt_file(
    monkeypatch, tmp_path, supplier, path_attr, cache_attr, loader_attr
):
    catalog_path = tmp_path / f"{supplier}_catalog.json"
    catalog_path.write_text("corrupt", encoding="utf-8")
    monkeypatch.setattr(index, path_attr, str(catalog_path), raising=False)
    monkeypatch.setattr(index, loader_attr, lambda path: (_ for _ in ()).throw(ValueError("corrupt")))
    monkeypatch.setattr(index, cache_attr, {"path": None, "fingerprint": None, "catalog": None})

    with pytest.raises(RuntimeError, match="Catalogo"):
        getattr(index, f"_load_{supplier}_catalog_cached")()


def test_tarkett_catalog_prefers_valid_supabase_snapshot(monkeypatch):
    snapshot_payload = {
        "source_file": "Inventario Tarkett.xls",
        "source_hash": "live-tarkettnet-hash",
        "generated_at": "2026-07-14T12:00:00+00:00",
        "total": 1,
        "items": [
            {
                "code": "25731726",
                "name": "Aurea Tech Cadiz 6.0mm",
                "unit": "MTK - metro cuadrado",
                "available_quantity": 12.5,
                "unit_price": 472.63,
                "price_source": "tarkettnet_code_match",
                "stock_source": "tarkettnet_code_match",
                "product_url": "https://www.tarkettnet.com.mx/vendas/25731726-aurea-tech-cadiz/0.htm",
                "image_url": "https://www.tarkettnet.com.mx/imagens/produtos/productos_tarkettnet/25731726_normal.jpg",
                "match_status": "tarkettnet_code_match",
            }
        ],
    }
    monkeypatch.setattr(index, "TARKETT_CATALOG_DB_ENABLED", True)
    monkeypatch.setattr(index, "TARKETT_CATALOG_DB_TTL_SECONDS", 300)
    monkeypatch.setattr(
        index,
        "_TARKETT_CATALOG_CACHE",
        {"path": None, "fingerprint": None, "source_hash": None, "catalog": None, "db_checked_at": 0.0},
    )
    monkeypatch.setattr(
        index,
        "db_get_supplier_catalog_snapshot",
        lambda supplier: {"supplier": supplier, "payload": snapshot_payload},
    )

    catalog = index._load_tarkett_catalog_cached()

    assert catalog["source_hash"] == "live-tarkettnet-hash"
    assert catalog["by_code"]["25731726"].unit_price == Decimal("472.63")
    assert index._TARKETT_CATALOG_CACHE["path"].startswith("supabase:")


def test_internal_tarkett_catalog_requires_worker_secret(monkeypatch):
    monkeypatch.setattr(index, "MOBILITI_REST_SECRET", "worker-secret")

    response = _client().get("/internal/catalogs/tarkett")

    assert response.status_code == 403


def test_internal_tarkett_catalog_reads_and_updates_snapshot(monkeypatch):
    payload = {
        "source_hash": "snapshot-hash",
        "generated_at": "2026-07-15T00:00:00+00:00",
        "items": [
            {
                "code": "25731726",
                "name": "Cadiz",
                "unit": "MTK - metro cuadrado",
                "available_quantity": 10,
                "unit_price": 472.63,
                "price_source": "tarkettnet_code_match",
                "stock_source": "tarkettnet_code_match",
            }
        ],
    }
    saved = []
    monkeypatch.setattr(index, "MOBILITI_REST_SECRET", "worker-secret")
    monkeypatch.setattr(
        index,
        "db_get_supplier_catalog_snapshot",
        lambda supplier: {"supplier": supplier, "source_hash": payload["source_hash"], "payload": payload},
    )
    monkeypatch.setattr(
        index,
        "db_upsert_supplier_catalog_snapshot",
        lambda supplier, current: saved.append((supplier, current)) or {"supplier": supplier, "payload": current},
    )
    headers = {"x-mobiliti-rest-secret": "worker-secret"}

    get_response = _client().get("/internal/catalogs/tarkett", headers=headers)
    put_response = _client().put("/internal/catalogs/tarkett", headers=headers, json={"payload": payload})

    assert get_response.status_code == 200
    assert get_response.json()["source_hash"] == "snapshot-hash"
    assert put_response.status_code == 200
    assert saved == [("tarkett", payload)]


def _dynamic_offiho_snapshot_payload():
    return {
        "source_hash": "d" * 64,
        "generated_at": "2026-08-11T20:00:00+00:00",
        "catalog_built_at": "2026-08-11T20:00:00+00:00",
        "inventory_last_modified": "2026-08-11T14:46:00+00:00",
        "source_row_count": 1,
        "duplicate_row_count": 0,
        "unique_item_count": 1,
        "total": 1,
        "items": [{
            "inventory_key": "OHE-1 NEGRO MODELO",
            "code": "OHE-1",
            "name": "MODELO",
            "variant": "NEGRO",
            "unit": "PZA",
            "pieces_per_box": 1,
            "available_quantity": 42,
            "unit_price": 100,
            "price_source": "inventory",
            "product_url": "",
            "image_url": "",
            "description": "Producto Offiho MODELO.",
            "description_source": "inventory_label",
            "match_status": "unmatched",
            "source_updated_at": "",
        }],
    }


def test_internal_offiho_catalog_reads_and_updates_snapshot(monkeypatch):
    payload = _dynamic_offiho_snapshot_payload()
    saved = []
    monkeypatch.setattr(index, "MOBILITI_REST_SECRET", "worker-secret")
    monkeypatch.setattr(
        index,
        "db_get_supplier_catalog_snapshot",
        lambda supplier: {"supplier": supplier, "source_hash": payload["source_hash"], "payload": payload},
    )
    monkeypatch.setattr(
        index,
        "db_upsert_supplier_catalog_snapshot",
        lambda supplier, current: saved.append((supplier, current)) or {"supplier": supplier, "payload": current},
    )
    headers = {"x-mobiliti-rest-secret": "worker-secret"}

    get_response = _client().get("/internal/catalogs/offiho", headers=headers)
    put_response = _client().put(
        "/internal/catalogs/offiho", headers=headers, json={"payload": payload}
    )

    assert get_response.status_code == 200
    assert get_response.json()["source_hash"] == "d" * 64
    assert put_response.status_code == 200
    assert saved == [("offiho", payload)]


def test_offiho_catalog_prefers_dynamic_database_snapshot(monkeypatch):
    payload = _dynamic_offiho_snapshot_payload()
    monkeypatch.setattr(index, "OFFIHO_CATALOG_DB_ENABLED", True, raising=False)
    monkeypatch.setattr(index, "OFFIHO_CATALOG_DB_TTL_SECONDS", 300, raising=False)
    monkeypatch.setattr(
        index,
        "_OFFIHO_CATALOG_CACHE",
        {"path": None, "fingerprint": None, "source_hash": None, "catalog": None, "db_checked_at": 0.0},
    )
    monkeypatch.setattr(
        index,
        "db_get_supplier_catalog_snapshot",
        lambda supplier: {"supplier": supplier, "source_hash": payload["source_hash"], "payload": payload},
    )

    catalog = index._load_offiho_catalog_cached()

    assert catalog["source_hash"] == "d" * 64
    assert catalog["unique_item_count"] == 1
    assert catalog["by_inventory_key"]["OHE-1 NEGRO MODELO"].available_quantity == Decimal("42")
    assert index._OFFIHO_CATALOG_CACHE["path"].startswith("supabase:")


def test_offiho_catalog_rejects_invalid_database_snapshot_and_uses_static_fallback(monkeypatch):
    invalid_payload = _dynamic_offiho_snapshot_payload()
    invalid_payload["unique_item_count"] = 2
    monkeypatch.setattr(index, "OFFIHO_CATALOG_DB_ENABLED", True, raising=False)
    monkeypatch.setattr(index, "OFFIHO_CATALOG_DB_TTL_SECONDS", 300, raising=False)
    monkeypatch.setattr(
        index,
        "_OFFIHO_CATALOG_CACHE",
        {"path": None, "fingerprint": None, "source_hash": None, "catalog": None, "db_checked_at": 0.0},
    )
    monkeypatch.setattr(
        index,
        "db_get_supplier_catalog_snapshot",
        lambda supplier: {"supplier": supplier, "payload": invalid_payload},
    )

    catalog = index._load_offiho_catalog_cached()

    assert catalog["unique_item_count"] == 1288
    assert not index._OFFIHO_CATALOG_CACHE["path"].startswith("supabase:")


def test_offiho_catalog_fresh_query_bypasses_server_cache(monkeypatch):
    _mock_user(monkeypatch)
    seen = []

    def response_for_user(user_id, *, force_refresh=False):
        seen.append((user_id, force_refresh))
        return {
            "source_hash": "f" * 64,
            "generated_at": "2026-08-11T20:00:00Z",
            "catalog_built_at": "2026-08-11T20:00:00Z",
            "inventory_last_modified": "2026-08-11T14:46:00Z",
            "source_row_count": 0,
            "duplicate_row_count": 0,
            "unique_item_count": 0,
            "total": 0,
            "items": [],
        }

    monkeypatch.setattr(index, "_offiho_catalog_response", response_for_user)

    response = _client().get("/offiho/catalog?fresh=1", headers=_auth_headers())

    assert response.status_code == 200
    assert seen == [(7, True)]
    assert response.headers["cache-control"] == "private, no-store"


def test_offiho_quote_creates_json_job_with_catalog_owned_values_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    uploaded = {}
    created = {}
    reservations = {}
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", _mock_offiho_catalog)
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)
    monkeypatch.setattr(index, "_wake_worker", lambda: None)
    monkeypatch.setattr(index, "_enforce_quote_history_limit", lambda usuario_id: [])
    monkeypatch.setattr(index, "_storage_upload_bytes", lambda path, content, content_type="application/octet-stream": uploaded.update({"path": path, "content": content, "content_type": content_type}))

    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        created.update({"usuario_id": usuario_id, "template": template, "metadata": metadata, "input_path": input_path, "job_id": job_id})
        return {"id": job_id, "usuario_id": usuario_id, "status": "draft", "metadata": metadata, "input_path": input_path}

    def fake_reserve(usuario_id, quote_job_id, lines):
        reservations.update({"usuario_id": usuario_id, "quote_job_id": quote_job_id, "lines": lines})
        return []

    monkeypatch.setattr(index, "db_create_quote_job", fake_create)
    monkeypatch.setattr(index, "db_create_offiho_reservations", fake_reserve)
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: {"id": job_id, **updates})
    body = _valid_offiho_body()
    body["items"][0].update({"unit_price": 1, "available_quantity": 999999, "product_url": "https://attacker.invalid", "image_url": "https://attacker.invalid/image.png"})

    resp = _client().post("/offiho/quote", headers=_auth_headers(), json=body)

    assert resp.status_code == 200
    payload = index.json.loads(uploaded["content"].decode("utf-8"))
    assert uploaded["path"].endswith("/input.json")
    assert uploaded["content_type"] == "application/json"
    assert payload["source_type"] == "offiho_cart"
    assert payload["items"][0]["unit_price"] == 7999
    assert payload["items"][0]["available_quantity"] == 0
    assert payload["items"][0]["product_url"] == ""
    assert payload["items"][0]["image_url"] == ""
    assert payload["items"][0]["stock_status"] == "out_of_stock"
    assert created["metadata"]["source_type"] == "offiho_cart"
    assert created["metadata"]["original_filename"] == "offiho-cart.json"
    assert created["metadata"]["input_extension"] == ".json"
    assert created["metadata"]["catalog_source_hash"] == "offiho-catalog-hash"
    assert created["metadata"]["offiho_item_count"] == 1
    assert created["metadata"]["estimated_duration_seconds"] == 120
    assert reservations["quote_job_id"] == created["job_id"]
    assert reservations["lines"][0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"


def test_offiho_quote_accepts_insufficient_stock(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", lambda: _mock_offiho_catalog(available_quantity=1))
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)
    monkeypatch.setattr(index, "_wake_worker", lambda: None)
    monkeypatch.setattr(index, "_enforce_quote_history_limit", lambda usuario_id: [])
    uploaded = {}
    monkeypatch.setattr(index, "_storage_upload_bytes", lambda path, content, content_type="application/octet-stream": uploaded.update({"content": content}))
    monkeypatch.setattr(index, "db_create_quote_job", lambda usuario_id, template, metadata, input_path, job_id=None: {"id": job_id, "usuario_id": usuario_id, "metadata": metadata})
    monkeypatch.setattr(index, "db_create_offiho_reservations", lambda usuario_id, quote_job_id, lines: [])
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: {"id": job_id, **updates})

    resp = _client().post("/offiho/quote", headers=_auth_headers(), json=_valid_offiho_body(quantity=2))

    assert resp.status_code == 200
    assert index.json.loads(uploaded["content"].decode("utf-8"))["items"][0]["stock_status"] == "insufficient_stock"


def test_offiho_quote_rejects_empty_unknown_or_invalid_items(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", _mock_offiho_catalog)

    for items in ([], [{"inventory_key": "unknown", "quantity": 1}], [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 0}], [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": "9" * 100}]):
        body = _valid_offiho_body()
        body["items"] = items
        resp = _client().post("/offiho/quote", headers=_auth_headers(), json=body)
        assert resp.status_code == 400


def test_offiho_quote_rejects_duplicate_inventory_key_before_upload(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", _mock_offiho_catalog)
    monkeypatch.setattr(
        index,
        "_storage_upload_bytes",
        lambda *args: (_ for _ in ()).throw(AssertionError("duplicate cart must not upload")),
    )
    body = _valid_offiho_body()
    body["items"] = body["items"] * 2

    resp = _client().post("/offiho/quote", headers=_auth_headers(), json=body)

    assert resp.status_code == 400
    assert "duplicada" in resp.json()["detail"].lower()


def test_offiho_quote_rejects_inactive_user(monkeypatch):
    _mock_user(monkeypatch, active=False)

    resp = _client().post("/offiho/quote", headers=_auth_headers(), json=_valid_offiho_body())

    assert resp.status_code == 403


def test_offiho_quote_releases_reservations_and_partial_job_after_failure(monkeypatch):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(index, "_load_offiho_catalog_cached", _mock_offiho_catalog)
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)
    monkeypatch.setattr(index, "_storage_upload_bytes", lambda *args: calls.append("upload"))
    monkeypatch.setattr(index, "db_create_quote_job", lambda usuario_id, template, metadata, input_path, job_id=None: calls.append("job") or {"id": job_id})
    monkeypatch.setattr(index, "db_create_offiho_reservations", lambda usuario_id, quote_job_id, lines: calls.append("reserve") or [])
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: calls.append("release"))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append("delete"))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: calls.append("storage-delete"))

    resp = _client().post("/offiho/quote", headers=_auth_headers(), json=_valid_offiho_body())

    assert resp.status_code == 503
    assert calls == ["upload", "job", "reserve", "release", "storage-delete", "delete"]


def _install_catalog_quote_failure_mocks(monkeypatch, supplier, calls, failure_stage):
    _mock_user(monkeypatch)
    body = _valid_offiho_body() if supplier == "offiho" else _valid_tarkett_body()
    monkeypatch.setattr(
        index,
        f"_load_{supplier}_catalog_cached",
        _mock_offiho_catalog if supplier == "offiho" else _mock_tarkett_catalog,
    )
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)
    monkeypatch.setattr(index, "_wake_worker", lambda: None)
    monkeypatch.setattr(index, "_enforce_quote_history_limit", lambda usuario_id: [])

    def fake_upload(*args):
        calls.append("upload")
        if failure_stage == "upload_timeout":
            raise TimeoutError("upload timed out after remote commit")

    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        calls.append("job")
        if failure_stage == "job_timeout":
            raise TimeoutError("job insert timed out after remote commit")
        return {"id": job_id, "usuario_id": usuario_id, "status": "draft"}

    def fake_reserve(usuario_id, job_id, lines):
        calls.append("reserve")
        return []

    def fake_update(job_id, updates):
        calls.append("update")
        return {} if failure_stage == "empty_update" else {"id": job_id, **updates}

    monkeypatch.setattr(index, "_storage_upload_bytes", fake_upload)
    monkeypatch.setattr(index, "db_create_quote_job", fake_create)
    monkeypatch.setattr(index, f"db_create_{supplier}_reservations", fake_reserve)
    monkeypatch.setattr(index, "db_update_quote_job", fake_update)
    monkeypatch.setattr(index, f"db_release_{supplier}_reservations", lambda job_id: calls.append("release"))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append("delete"))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: calls.append("storage-delete"))
    return body


@pytest.mark.parametrize("supplier", ["tarkett", "offiho"])
@pytest.mark.parametrize("failure_stage", ["empty_update", "upload_timeout", "job_timeout"])
def test_catalog_quote_requires_queued_update_and_always_cleans_known_job_and_input(
    monkeypatch, supplier, failure_stage
):
    calls = []
    body = _install_catalog_quote_failure_mocks(monkeypatch, supplier, calls, failure_stage)

    resp = _client().post(f"/{supplier}/quote", headers=_auth_headers(), json=body)

    assert resp.status_code == 503
    prefix = {
        "empty_update": ["upload", "job", "reserve", "update"],
        "upload_timeout": ["upload"],
        "job_timeout": ["upload", "job"],
    }[failure_stage]
    assert calls == prefix + ["release", "storage-delete", "delete"]


@pytest.mark.parametrize("supplier", ["tarkett", "offiho"])
def test_catalog_quote_cleanup_preserves_job_when_release_fails(monkeypatch, supplier):
    calls = []
    body = _install_catalog_quote_failure_mocks(monkeypatch, supplier, calls, "empty_update")
    updates = []
    original_update = index.db_update_quote_job

    def track_update(job_id, payload):
        updates.append(payload)
        return original_update(job_id, payload)

    def fail_release(job_id):
        calls.append("release")
        raise RuntimeError("cleanup release failed")

    monkeypatch.setattr(index, "db_update_quote_job", track_update)
    monkeypatch.setattr(index, f"db_release_{supplier}_reservations", fail_release)

    resp = _client().post(f"/{supplier}/quote", headers=_auth_headers(), json=body)

    assert resp.status_code == 503
    assert calls == ["upload", "job", "reserve", "update", "release", "update"]
    assert updates[-1] == {
        "status": "failed",
        "error_message": "cleanup_pending:release_reservations",
    }
    assert "cleanup release failed" not in resp.json()["detail"]


def test_offiho_reservations_work_in_dev_mode_without_existing_data(monkeypatch):
    store = dev_state_with_draft_job(JOB_MIXED_UUID)
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: store)
    monkeypatch.setattr(index, "_dev_save", lambda data: None)

    created = index.db_create_offiho_reservations(7, JOB_MIXED_UUID, [{
        "inventory_key": "OHE-405 NEGRO ALUFSEN",
        "quantity": 2,
        "available_quantity": Decimal("5"),
    }])
    released = index.db_release_offiho_reservations(JOB_MIXED_UUID)

    assert len(created) == 1
    assert store["tarkett_reservations"] == []
    assert store["offiho_reservations"][0]["status"] == "released"
    assert released[0]["product_code"] == "OHE-405 NEGRO ALUFSEN"


def test_delete_quote_and_retention_release_tarkett_and_offiho_reservations(monkeypatch):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: {"id": job_id, "usuario_id": 7, "status": "completed", "input_path": None, "output_path": None})
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: calls.append(("storage", job["id"])))
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: calls.append(("tarkett", job_id)))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: calls.append(("offiho", job_id)))
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: calls.append(("generic", job_id)))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("delete", job_id)))

    response = _client().delete("/cotizaciones/job-1", headers=_auth_headers())
    assert response.status_code == 200
    assert calls == [("tarkett", "job-1"), ("offiho", "job-1"), ("generic", "job-1"), ("delete", "job-1"), ("storage", "job-1")]

    calls.clear()
    monkeypatch.setattr(index, "MAX_QUOTE_HISTORY_PER_USER", 0)
    result = index._run_quote_retention(7, jobs=[{"id": "job-2", "status": "completed", "input_path": None, "output_path": None, "created_at": "2020-01-01T00:00:00+00:00"}])
    assert result["jobs_deleted"] == 1
    assert calls == [("tarkett", "job-2"), ("offiho", "job-2"), ("generic", "job-2"), ("delete", "job-2"), ("storage", "job-2")]


def test_delete_quote_keeps_storage_when_reservation_release_fails(monkeypatch):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "completed",
            "input_path": "users/7/jobs/job-1/input.json",
            "output_path": "users/7/jobs/job-1/output.xlsx",
        },
    )
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: calls.append(("tarkett", job_id)))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: calls.append(("offiho", job_id)))

    def fail_release(job_id):
        calls.append(("generic", job_id))
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(index, "db_release_catalog_reservations", fail_release)
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("delete", job_id)))
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: calls.append(("storage", job["id"])))

    response = _client().delete("/cotizaciones/job-1", headers=_auth_headers())

    assert response.status_code == 503
    assert calls == [("tarkett", "job-1"), ("offiho", "job-1"), ("generic", "job-1")]


_CATALOG_RUN_ID = "11111111-1111-1111-1111-111111111111"
_CATALOG_CANDIDATE_ID = "22222222-2222-2222-2222-222222222222"
_CATALOG_NEW_CANDIDATE_ID = "33333333-3333-3333-3333-333333333333"


def _image_bytes(format_name="PNG", *, size=(2, 2), metadata=False, animated=False):
    output = BytesIO()
    image = Image.new("RGB", size, (20, 120, 220))
    save_options = {}
    if metadata:
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Comment", "must be removed")
        save_options["pnginfo"] = png_info
    if animated:
        save_options.update(
            save_all=True,
            append_images=[Image.new("RGB", size, (220, 80, 20))],
            duration=100,
            loop=0,
        )
    image.save(output, format=format_name, **save_options)
    return output.getvalue()


_PNG_BYTES = _image_bytes()


def _enable_generic_catalogs(monkeypatch, *suppliers):
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", ",".join(suppliers))
    monkeypatch.setattr(index, "CATALOG_ENABLED_SUPPLIERS", tuple(suppliers), raising=False)


def _mock_supplier_catalog(*, availability_type="stocked", code_status="verified"):
    return {
        "supplier": "cr-global",
        "source_hash": "a" * 64,
        "generated_at": "2026-07-15T00:00:00Z",
        "items": [
            {
                "internal_id": "cr-global:chair-1",
                "supplier": "cr-global",
                "product_key": "chair-1",
                "sku": "CRG-001" if code_status == "verified" else "",
                "code_status": code_status,
                "brand": "CR Global",
                "collection": "Work",
                "name": "Silla operativa",
                "description": "Silla con respaldo de malla",
                "unit": "pieza",
                "availability_type": availability_type,
                "stock": "5.000000" if availability_type == "stocked" else None,
                "lead_time": "Entrega inmediata" if availability_type == "stocked" else "Sobre pedido",
                "base_price_options": [],
                "add_on_options": [],
                "base_currency": "MXN",
                "price_net": "100.000000",
                "tax_rate": "0.160000",
                "attributes": {"color": "Negro"},
                "image_url": "https://example.test/chair.png",
                "image_kind": "official",
                "product_url": "https://example.test/chair",
                "warnings": [],
                "source_reference": "CRG_LP_General_Dist_2026-04.pdf:12",
            }
        ],
    }


def _mock_lumbro_catalog():
    items = []
    for number in range(1, 6):
        verified = number <= 4
        items.append(
            {
                "internal_id": f"lumbro:contact-{number}",
                "supplier": "lumbro",
                "product_key": f"contact-{number}",
                "sku": f"LUM-{number:03d}" if verified else "",
                "code_status": "verified" if verified else "needs_review",
                "brand": "Lumbro",
                "collection": "Interconexion",
                "name": f"Multicontacto Lumbro {number}",
                "description": "Multicontacto para mobiliario de oficina",
                "unit": "PZA",
                "availability_type": "unknown",
                "stock": None,
                "lead_time": "",
                "base_price_options": [],
                "add_on_options": [],
                "base_currency": "MXN",
                "price_net": f"{number * 1000:.6f}",
                "tax_rate": "0.160000",
                "attributes": {"color": "Negro"},
                "image_url": f"https://lumbro.com.mx/assets/contact-{number}.png",
                "image_kind": "official",
                "product_url": "https://lumbro.com.mx/productos",
                "warnings": [] if verified else ["Codigo por verificar"],
                "source_reference": f"LISTA DE PRECIOS MULTICONTACTOS 2026.pdf:{number}",
            }
        )
    return {
        "supplier": "lumbro",
        "source_hash": "b" * 64,
        "generated_at": "2026-07-18T00:00:00Z",
        "items": items,
        "metadata": {
            "coverage": {"verified_items": 4, "needs_review_items": 1},
            "source_files": ["internal-audit-only"],
        },
    }


def _supplier_rate_rows():
    effective_date = (date.today() - timedelta(days=1)).isoformat()
    return [
        {
            "currency": "USD",
            "effective_date": effective_date,
            "mxn_per_unit": "18.500000",
            "retrieved_at": f"{effective_date}T20:00:00Z",
        },
        {
            "currency": "EUR",
            "effective_date": effective_date,
            "mxn_per_unit": "21.000000",
            "retrieved_at": f"{effective_date}T20:05:00Z",
        },
    ]


def test_dev_exchange_rates_replace_stale_usd_with_latest_market_reference(
    monkeypatch,
):
    latest = {
        "currency": "USD",
        "effective_date": date.today().isoformat(),
        "mxn_per_unit": "17.480400",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(
        index,
        "_dev_load",
        lambda: {
            "exchange_rates": [
                {
                    "currency": "USD",
                    "effective_date": "2026-07-20",
                    "mxn_per_unit": "18.500000",
                    "retrieved_at": "2026-07-20T15:54:36+00:00",
                }
            ]
        },
    )
    monkeypatch.setattr(
        index,
        "_fetch_latest_usd_mxn_row",
        lambda: latest,
        raising=False,
    )

    rows = index.db_list_exchange_rates()

    assert rows[0] == latest
    assert not any(
        row["currency"] == "USD" and row["mxn_per_unit"] == "18.500000"
        for row in rows
    )


def test_dev_exchange_rates_keep_stored_usd_when_market_date_is_future_locally(
    monkeypatch,
):
    from mobiliti_saas.quote_engine import engine

    stored = {
        "currency": "USD",
        "effective_date": (date.today() - timedelta(days=4)).isoformat(),
        "mxn_per_unit": "18.500000",
        "retrieved_at": (
            datetime.now(timezone.utc) - timedelta(days=4)
        ).isoformat(),
    }
    future_payload = json.dumps({
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "base": "USD",
        "quote": "MXN",
        "rate": 17.4782,
    }).encode("utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return future_payload

    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(
        index,
        "_dev_load",
        lambda: {"exchange_rates": [stored]},
    )
    monkeypatch.setattr(engine, "urlopen", lambda *_args, **_kwargs: Response())

    rows = index.db_list_exchange_rates()

    assert rows == [stored]


def test_dev_exchange_rates_request_banxico_not_later_than_local_day(monkeypatch):
    from mobiliti_saas.quote_engine import engine

    stored = {
        "currency": "USD",
        "effective_date": (date.today() - timedelta(days=20)).isoformat(),
        "mxn_per_unit": "18.500000",
        "retrieved_at": (
            datetime.now(timezone.utc) - timedelta(days=20)
        ).isoformat(),
    }
    future_payload = json.dumps({
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "base": "USD",
        "quote": "MXN",
        "rate": 17.4782,
    }).encode("utf-8")
    current_payload = json.dumps({
        "date": (date.today() - timedelta(days=2)).isoformat(),
        "base": "USD",
        "quote": "MXN",
        "rate": 17.0218,
    }).encode("utf-8")

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.payload

    def fake_urlopen(request, **_kwargs):
        local_day = f"date={date.today().isoformat()}"
        is_banxico_local_day = (
            local_day in request.full_url
            and "providers=BANXICO" in request.full_url
        )
        return Response(current_payload if is_banxico_local_day else future_payload)

    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(
        index,
        "_dev_load",
        lambda: {"exchange_rates": [stored]},
    )
    monkeypatch.setattr(engine, "urlopen", fake_urlopen)

    rows = index.db_list_exchange_rates()

    assert rows[0]["effective_date"] == (date.today() - timedelta(days=2)).isoformat()
    assert rows[0]["mxn_per_unit"] == "17.021800"


def _valid_supplier_line():
    return {
        "internal_id": "cr-global:chair-1",
        "quantity": "2",
        "add_on_option_ids": [],
    }


def _valid_supplier_body(*, items=None, quote_currency="MXN"):
    return {
        "proyecto": "Proyecto CR Global",
        "cliente": "Cliente",
        "correo": "cliente@example.com",
        "telefono": "5551234567",
        "direccion": "Guadalajara",
        "razon_social": "Cliente SA de CV",
        "descuento": 40,
        "quote_currency": quote_currency,
        "items": [_valid_supplier_line()] if items is None else items,
    }


def _install_supplier_quote_mocks(monkeypatch, catalog):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, catalog["supplier"])
    state = {
        "uploaded": {},
        "created": {},
        "reservations": [],
        "reservation_totals": {},
        "reserved_by_others": set(),
        "events": [],
        "wake_count": 0,
    }
    monkeypatch.setattr(index, "_load_supplier_catalog_cached", lambda supplier: catalog, raising=False)
    monkeypatch.setattr(index, "db_list_exchange_rates", lambda: _supplier_rate_rows(), raising=False)
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda user: None)
    monkeypatch.setattr(index, "_enforce_quote_history_limit", lambda usuario_id: [])

    def fake_upload(path, content, content_type="application/octet-stream"):
        state["events"].append("upload")
        state["uploaded"].update({"path": path, "content": content, "content_type": content_type})

    def fake_create(usuario_id, template, metadata, input_path, job_id=None):
        state["events"].append("job")
        state["created"].update(
            {
                "usuario_id": usuario_id,
                "template": template,
                "metadata": metadata,
                "input_path": input_path,
                "job_id": job_id,
            }
        )
        return {"id": job_id, "usuario_id": usuario_id, "status": "draft", "metadata": metadata}

    def fake_reserve(usuario_id, quote_job_id, supplier, lines):
        state["events"].append("reserve")
        state["reservations"].append(
            {
                "usuario_id": usuario_id,
                "quote_job_id": quote_job_id,
                "supplier": supplier,
                "lines": lines,
            }
        )
        snapshot = []
        for line in lines:
            internal_id = line["internal_id"]
            reserved = Decimal(str(state["reservation_totals"].get(internal_id, "0")))
            quantity = Decimal(str(line["quantity"]))
            stock = Decimal(str(line["stock"]))
            available = max(stock - reserved, Decimal(0))
            snapshot.append(
                {
                    "internal_id": internal_id,
                    "reserved_before": f"{reserved:.6f}",
                    "available_before": f"{available:.6f}",
                    "insufficient": quantity > available,
                    "reserved_by_others": internal_id in state["reserved_by_others"],
                }
            )
            state["reservation_totals"][internal_id] = reserved + quantity
        return snapshot

    def fake_wake():
        state["wake_count"] += 1

    monkeypatch.setattr(index, "_storage_upload_bytes", fake_upload)
    monkeypatch.setattr(index, "db_create_quote_job", fake_create)
    monkeypatch.setattr(index, "db_reserve_catalog_items", fake_reserve, raising=False)
    monkeypatch.setattr(
        index,
        "db_update_quote_job",
        lambda job_id, updates: state["events"].append("queue") or {"id": job_id, **updates},
    )
    monkeypatch.setattr(index, "_wake_worker", fake_wake)
    return state


def test_generic_catalog_registry_respects_feature_flags_and_canonical_order(monkeypatch):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, "alma", "cr-global")

    response = _client().get("/catalogs", headers=_auth_headers())

    assert response.status_code == 200
    assert [
        (row["supplier"], row["label"])
        for row in response.json()["suppliers"]
    ] == [("cr-global", "CR Global"), ("alma", "ALMA")]


def test_generic_catalog_supplier_registry_includes_lumbro_in_canonical_order(monkeypatch):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, "lumbro", "alma", "sunon", "sonara", "cr-global")

    response = _client().get("/catalogs", headers=_auth_headers())

    assert response.status_code == 200
    assert [
        (row["supplier"], row["label"])
        for row in response.json()["suppliers"]
    ] == [
        ("cr-global", "CR Global"),
        ("sonara", "Sonara"),
        ("sunon", "Sunon"),
        ("alma", "ALMA"),
        ("lumbro", "Lumbro"),
    ]


def test_lumbro_catalog_supplier_is_disabled_by_default(monkeypatch):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch)
    monkeypatch.setattr(
        index,
        "_load_supplier_catalog_cached",
        lambda supplier: (_ for _ in ()).throw(AssertionError("disabled catalog must not load")),
        raising=False,
    )

    registry = _client().get("/catalogs", headers=_auth_headers())
    detail = _client().get("/catalogs/lumbro", headers=_auth_headers())
    quote = _client().post(
        "/catalogs/lumbro/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(items=[]),
    )

    assert registry.status_code == 200
    assert registry.json()["suppliers"] == []
    assert detail.status_code == 404
    assert quote.status_code == 404


@pytest.mark.parametrize(
    "configured",
    [
        ("lumbro", "lumbro"),
        ("lumbro", "unknown"),
        (" lumbro",),
        ("lumbro ",),
        ("cr-global", "lumbro", "unknown"),
    ],
)
def test_catalog_supplier_feature_flag_fails_closed_for_invalid_duplicate_or_spaced_values(
    monkeypatch,
    configured,
):
    monkeypatch.setattr(index, "CATALOG_ENABLED_SUPPLIERS", configured, raising=False)

    assert index._enabled_catalog_suppliers() == ()


def test_exchange_rates_route_precedes_dynamic_supplier_route():
    get_paths = [
        route.path
        for route in index.app.routes
        if "GET" in getattr(route, "methods", set())
    ]

    assert "/catalogs/exchange-rates" in get_paths
    assert "/catalogs/{supplier}" in get_paths
    assert get_paths.index("/catalogs/exchange-rates") < get_paths.index("/catalogs/{supplier}")


@pytest.mark.parametrize("supplier", ["alma", "unknown"])
def test_supplier_catalog_rejects_disabled_or_unknown_supplier(monkeypatch, supplier):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, "cr-global")
    assert any(
        route.path == "/catalogs/{supplier}" and "GET" in getattr(route, "methods", set())
        for route in index.app.routes
    )

    response = _client().get(f"/catalogs/{supplier}", headers=_auth_headers())

    assert response.status_code == 404


def test_supplier_catalog_returns_published_stock_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, "cr-global")
    monkeypatch.setattr(
        index,
        "_load_supplier_catalog_cached",
        lambda supplier: _mock_supplier_catalog(),
        raising=False,
    )
    monkeypatch.setattr(
        index,
        "db_catalog_reservation_summary",
        lambda supplier, usuario_id: [
            {
                "internal_id": "cr-global:chair-1",
                "reserved_quantity": "3.000000",
                "reserved_by_others": True,
            },
        ],
        raising=False,
    )

    response = _client().get("/catalogs/cr-global", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_hash"] == "a" * 64
    item = payload["items"][0]
    assert item["stock"] == "5.000000"
    assert item["reserved_quantity"] == 3
    assert item["reserved_by_others"] is True
    assert item["is_out_of_stock"] is False
    assert item["image_url"] == "https://example.test/chair.png"


def test_lumbro_catalog_supplier_returns_public_items_without_snapshot_metadata(monkeypatch):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, "lumbro")
    monkeypatch.setattr(index, "_load_supplier_catalog_cached", lambda supplier: _mock_lumbro_catalog())
    monkeypatch.setattr(index, "db_catalog_reservation_summary", lambda supplier, usuario_id: [])

    response = _client().get("/catalogs/lumbro", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["supplier"] == "lumbro"
    assert payload["total"] == 5
    assert [item["code_status"] for item in payload["items"]] == [
        "verified",
        "verified",
        "verified",
        "verified",
        "needs_review",
    ]
    assert "metadata" not in payload
    assert "source_files" not in payload


def test_lumbro_catalog_supplier_quote_accepts_four_verified_items(monkeypatch):
    catalog = _mock_lumbro_catalog()
    state = _install_supplier_quote_mocks(monkeypatch, catalog)
    items = [
        {"internal_id": item["internal_id"], "quantity": "1", "add_on_option_ids": []}
        for item in catalog["items"]
        if item["code_status"] == "verified"
    ]

    response = _client().post(
        "/catalogs/lumbro/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(items=items),
    )

    assert response.status_code == 200
    payload = index.json.loads(state["uploaded"]["content"].decode("utf-8"))
    assert payload["supplier"] == "lumbro"
    assert len(payload["items"]) == 4
    assert payload["exchange_rate"] == "1.000000"
    assert state["created"]["metadata"]["catalog_source_hash"] == "b" * 64
    assert state["reservations"] == []


def test_lumbro_catalog_supplier_quote_accepts_needs_review_item_with_warning(monkeypatch):
    catalog = _mock_lumbro_catalog()
    state = _install_supplier_quote_mocks(monkeypatch, catalog)
    review_item = next(item for item in catalog["items"] if item["code_status"] == "needs_review")

    response = _client().post(
        "/catalogs/lumbro/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(
            items=[
                {
                    "internal_id": review_item["internal_id"],
                    "quantity": "1",
                    "add_on_option_ids": [],
                }
            ]
        ),
    )

    assert response.status_code == 200
    payload = index.json.loads(state["uploaded"]["content"].decode("utf-8"))
    line = payload["items"][0]
    assert line["code_status"] == "needs_review"
    assert line["sku"] == ""
    assert "Codigo por verificar" in line["warnings"]
    assert state["created"]["metadata"]["catalog_source_hash"] == "b" * 64
    assert state["reservations"] == []


def test_sonara_catalog_supplier_quote_freezes_needs_review_item(monkeypatch):
    catalog = _mock_lumbro_catalog()
    catalog["supplier"] = "sonara"
    for item in catalog["items"]:
        item["supplier"] = "sonara"
        item["internal_id"] = item["internal_id"].replace("lumbro:", "sonara:")
    state = _install_supplier_quote_mocks(monkeypatch, catalog)
    review_item = next(item for item in catalog["items"] if item["code_status"] == "needs_review")
    review_item["price_net"] = "77.000000"

    response = _client().post(
        "/catalogs/sonara/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(
            items=[
                {
                    "internal_id": review_item["internal_id"],
                    "quantity": "1",
                    "add_on_option_ids": [],
                }
            ]
        ),
    )

    assert response.status_code == 200
    uploaded_payload = index.json.loads(state["uploaded"]["content"].decode("utf-8"))
    line = uploaded_payload["items"][0]
    assert line["code_status"] == "needs_review"
    assert line["sku"] == ""
    assert line["base_currency"] == "MXN"
    assert line["unit_price"] == "77.00"
    assert line["tax_rate"] == "0.160000"
    assert "Codigo por verificar" in line["warnings"]


def test_supplier_quote_freezes_fx_and_creates_stock_reservations(monkeypatch):
    state = _install_supplier_quote_mocks(monkeypatch, _mock_supplier_catalog())

    response = _client().post(
        "/catalogs/cr-global/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(),
    )

    assert response.status_code == 200
    payload = index.json.loads(state["uploaded"]["content"].decode("utf-8"))
    frozen = {
        "quote_currency": "MXN",
        "exchange_rate": "1.000000",
        "rate_source": "identity",
        "rate_effective_date": date.today().isoformat(),
        "rate_retrieved_at": "",
    }
    assert state["uploaded"]["path"].endswith("/input.json")
    assert payload["source_type"] == "supplier_cart"
    assert {key: payload[key] for key in frozen} == frozen
    assert {key: state["created"]["metadata"][key] for key in frozen} == frozen
    assert state["created"]["metadata"]["catalog_source_hash"] == "a" * 64
    assert state["reservations"][0]["supplier"] == "cr-global"
    assert state["reservations"][0]["lines"][0]["internal_id"] == "cr-global:chair-1"
    assert state["reservations"][0]["lines"][0]["stock"] == "5.000000"
    assert state["events"][:4] == ["job", "reserve", "upload", "queue"]
    assert state["wake_count"] == 1


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "unknown-id",
        "client-sku",
        "invalid-option",
        "invalid-quantity",
        "fractional-piece-quantity",
    ],
)
def test_supplier_quote_rejects_invalid_cart_before_upload(monkeypatch, case):
    catalog = _mock_supplier_catalog()
    state = _install_supplier_quote_mocks(monkeypatch, catalog)
    line = _valid_supplier_line()
    if case == "empty":
        items = []
    elif case == "unknown-id":
        items = [{**line, "internal_id": "cr-global:missing"}]
    elif case == "client-sku":
        items = [{**line, "sku": "SPOOFED"}]
    elif case == "invalid-option":
        items = [{**line, "base_option_id": "missing"}]
    elif case == "invalid-quantity":
        items = [{**line, "quantity": "0"}]
    elif case == "fractional-piece-quantity":
        items = [{**line, "quantity": "1.5"}]
    else:
        items = [line]

    response = _client().post(
        "/catalogs/cr-global/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(items=items),
    )

    assert response.status_code == 400
    assert state["uploaded"] == {}
    assert state["created"] == {}
    assert state["reservations"] == []


def test_made_to_order_supplier_quote_does_not_create_stock_reservation(monkeypatch):
    state = _install_supplier_quote_mocks(
        monkeypatch,
        _mock_supplier_catalog(availability_type="made_to_order"),
    )

    response = _client().post(
        "/catalogs/cr-global/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(quote_currency="USD"),
    )

    assert response.status_code == 200
    line = index.json.loads(state["uploaded"]["content"].decode("utf-8"))["items"][0]
    assert line["availability_type"] == "made_to_order"
    assert line["stock"] is None
    assert line.get("stock_status") != "out_of_stock"
    assert state["reservations"] == []


@pytest.mark.parametrize("path", ["/catalogs", "/catalogs/exchange-rates", "/catalogs/cr-global"])
def test_generic_catalog_routes_require_token(monkeypatch, path):
    _enable_generic_catalogs(monkeypatch, "cr-global")

    response = _client().get(path)

    assert response.status_code == 401


def test_generic_catalog_rejects_expired_subscription_before_loading(monkeypatch):
    _mock_user(monkeypatch)
    _enable_generic_catalogs(monkeypatch, "cr-global")
    monkeypatch.setattr(
        index,
        "db_get_suscripcion_by_usuario",
        lambda usuario_id: {
            "id": 1,
            "usuario_id": usuario_id,
            "estado": "activa",
            "plan": "mensual",
            "fecha_fin": "2020-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        index,
        "_load_supplier_catalog_cached",
        lambda supplier: (_ for _ in ()).throw(AssertionError("expired user must not load catalog")),
    )

    response = _client().get("/catalogs/cr-global", headers=_auth_headers())

    assert response.status_code == 403


def test_supplier_quote_allows_insufficient_stock_with_frozen_warning(monkeypatch):
    state = _install_supplier_quote_mocks(monkeypatch, _mock_supplier_catalog())
    line = {**_valid_supplier_line(), "quantity": "6"}

    response = _client().post(
        "/catalogs/cr-global/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(items=[line]),
    )

    assert response.status_code == 200
    saved_line = index.json.loads(state["uploaded"]["content"].decode("utf-8"))["items"][0]
    assert saved_line["stock_status"] == "insufficient"
    assert "Cantidad solicitada supera la existencia; verificar disponibilidad." in saved_line["warnings"]
    assert state["reservations"][0]["lines"][0]["quantity"] == "6"


def test_supplier_quote_warning_accounts_for_existing_active_reservations(monkeypatch):
    state = _install_supplier_quote_mocks(monkeypatch, _mock_supplier_catalog())
    state["reservation_totals"]["cr-global:chair-1"] = Decimal("4")
    state["reserved_by_others"].add("cr-global:chair-1")

    response = _client().post(
        "/catalogs/cr-global/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(items=[{**_valid_supplier_line(), "quantity": "2"}]),
    )

    assert response.status_code == 200
    saved_line = index.json.loads(state["uploaded"]["content"].decode("utf-8"))["items"][0]
    assert saved_line["stock_status"] == "insufficient"
    assert saved_line["reserved_quantity"] == "4.000000"
    assert saved_line["available_after_reservations"] == "1.000000"
    assert "Cantidad solicitada supera la existencia disponible; verificar disponibilidad." in saved_line["warnings"]


def test_catalog_atomic_reservation_serializes_availability_in_dev(monkeypatch, tmp_path):
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "DEV_STORE_DIR", tmp_path)
    first_job = "11111111-1111-1111-1111-111111111111"
    second_job = "22222222-2222-2222-2222-222222222222"
    index.db_create_quote_job(1, "template.xlsx", {}, "first.json", job_id=first_job)
    index.db_create_quote_job(2, "template.xlsx", {}, "second.json", job_id=second_job)

    first = index.db_reserve_catalog_items(
        1,
        first_job,
        "cr-global",
        [{"internal_id": "chair-1", "sku": "CHAIR-1", "quantity": "4", "stock": "5"}],
    )
    second = index.db_reserve_catalog_items(
        2,
        second_job,
        "cr-global",
        [{"internal_id": "chair-1", "sku": "CHAIR-1", "quantity": "2", "stock": "5"}],
    )

    assert first == [
        {
            "internal_id": "chair-1",
            "reserved_before": "0.000000",
            "available_before": "5.000000",
            "insufficient": False,
            "reserved_by_others": False,
        }
    ]
    assert second == [
        {
            "internal_id": "chair-1",
            "reserved_before": "4.000000",
            "available_before": "1.000000",
            "insufficient": True,
            "reserved_by_others": True,
        }
    ]


def test_supplier_quote_failed_queue_update_runs_compensating_cleanup(monkeypatch):
    state = _install_supplier_quote_mocks(monkeypatch, _mock_supplier_catalog())
    calls = []
    monkeypatch.setattr(index, "db_update_quote_job", lambda job_id, updates: {"id": job_id, "status": "draft"})
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: calls.append(("release", job_id)))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("job", job_id)))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: calls.append(("storage", paths[0])))

    response = _client().post(
        "/catalogs/cr-global/quote",
        headers=_auth_headers(),
        json=_valid_supplier_body(),
    )

    assert response.status_code == 503
    job_id = state["created"]["job_id"]
    assert calls == [
        ("release", job_id),
        ("storage", f"users/7/jobs/{job_id}/input.json"),
        ("job", job_id),
    ]


def test_generic_catalog_backend_never_falls_back_to_anon_key(monkeypatch):
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", None)
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", None)

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
        index.db_list_exchange_rates()


def test_catalog_search_rejects_missing_supplier_before_reading_payload(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "_catalog_search_snapshots",
        lambda *_args: pytest.fail("La búsqueda global no debe leer payloads"),
    )

    response = _client().get("/catalogs/search?q=silla", headers=_auth_headers())

    assert response.status_code == 400
    assert response.json()["detail"] == "proveedor requerido"


def test_supplier_catalog_cache_reuses_payload_when_published_version_is_unchanged(monkeypatch):
    payload_reads = []
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "db_get_published_catalog_version_id", lambda supplier: "version-1", raising=False)
    monkeypatch.setattr(
        index,
        "db_get_published_catalog_snapshot",
        lambda supplier, version_id=None: payload_reads.append((supplier, version_id)) or {
            "id": "version-1", "supplier": supplier, "payload": _mock_supplier_catalog(),
        },
    )
    index._SUPPLIER_CATALOG_CACHE.clear()

    index._load_supplier_catalog_cached("cr-global")
    index._load_supplier_catalog_cached("cr-global")

    assert payload_reads == [("cr-global", "version-1")]


def test_supplier_catalog_cache_reads_one_new_payload_after_published_version_changes(monkeypatch):
    version = {"id": "version-1"}
    payload_reads = []
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "db_get_published_catalog_version_id", lambda supplier: version["id"], raising=False)
    monkeypatch.setattr(
        index,
        "db_get_published_catalog_snapshot",
        lambda supplier, version_id=None: payload_reads.append((supplier, version_id)) or {
            "id": version_id, "supplier": supplier, "payload": _mock_supplier_catalog(),
        },
    )
    index._SUPPLIER_CATALOG_CACHE.clear()

    index._load_supplier_catalog_cached("cr-global")
    version["id"] = "version-2"
    index._load_supplier_catalog_cached("cr-global")

    assert payload_reads == [("cr-global", "version-1"), ("cr-global", "version-2")]


def test_authenticated_catalog_search_disables_shared_http_caching(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(
        index,
        "_catalog_search_snapshots",
        lambda _user_id, supplier: {supplier: {"items": []}},
    )

    response = _client().get(
        "/catalogs/search?supplier=cr-global&q=silla",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("image_kind", ["official", "generated_reference"])
def test_published_catalog_hydrates_approved_asset_without_changing_contract(monkeypatch, image_kind):
    payload = _mock_supplier_catalog()
    payload["items"][0]["image_url"] = ""
    payload["items"][0]["image_kind"] = image_kind
    payload["items"][0]["attributes"]["approved_asset"] = {
        "bucket": "catalog-assets",
        "path": f"{'b' * 64}.png",
        "label": "Imagen de referencia",
        "image_kind": image_kind,
        "approved": True,
    }
    payload["items"][0]["attributes"]["image_match"] = {
        "status": "exact_xlsx",
        "asset_sha256": "b" * 64,
        "source_references": [{"file_id": "secret-source"}],
    }
    payload["items"][0]["attributes"]["price_evidence"] = [{"kind": "base"}]
    monkeypatch.setattr(
        index,
        "db_get_published_catalog_snapshot",
        lambda supplier, version_id: {"id": version_id, "supplier": supplier, "payload": payload},
    )
    monkeypatch.setattr(index, "db_get_published_catalog_version_id", lambda supplier: "snapshot-1")
    monkeypatch.setattr(index, "CATALOG_ASSET_STORAGE_PROVIDER", "supabase")
    monkeypatch.setattr(index, "SUPABASE_URL", "https://project.supabase.co")
    index._SUPPLIER_CATALOG_CACHE.clear()

    catalog = index._load_supplier_catalog_cached("cr-global")

    assert catalog["items"][0]["image_url"] == (
        f"https://project.supabase.co/storage/v1/object/public/catalog-assets/{'b' * 64}.png"
    )
    assert catalog["items"][0]["image_kind"] == image_kind
    assert "image" not in catalog["items"][0]
    assert "approved_asset" not in catalog["items"][0]["attributes"]
    assert "image_match" not in catalog["items"][0]["attributes"]
    assert catalog["items"][0]["source_reference"] == "CRG_LP_General_Dist_2026-04.pdf:12"
    assert catalog["items"][0]["attributes"]["price_evidence"] == [{"kind": "base"}]


def test_dev_catalog_cache_invalidates_when_published_version_changes(monkeypatch):
    state = {"payload": _mock_supplier_catalog(), "version_id": "snapshot-stable"}
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(
        index,
        "db_get_published_catalog_snapshot",
        lambda supplier, version_id: {
            "id": version_id,
            "supplier": supplier,
            "source_hash": state["payload"]["source_hash"],
            "payload": deepcopy(state["payload"]),
        },
    )
    monkeypatch.setattr(index, "db_get_published_catalog_version_id", lambda supplier: state["version_id"])
    index._SUPPLIER_CATALOG_CACHE.clear()

    first = index._load_supplier_catalog_cached("cr-global")
    assert first["items"][0]["product_url"] == "https://example.test/chair"

    state["payload"]["items"][0]["product_url"] = "https://example.test/chair-curated"
    state["version_id"] = "snapshot-updated"
    second = index._load_supplier_catalog_cached("cr-global")

    assert second["items"][0]["product_url"] == "https://example.test/chair-curated"


def test_catalog_asset_public_url_uses_local_dev_endpoint(monkeypatch):
    object_name = f"{'d' * 64}.png"
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "DEV_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

    assert index._catalog_asset_public_url(object_name) == (
        f"http://127.0.0.1:8000/dev/catalog-assets/{object_name}"
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://assets.example.test",
        "https://user:pass@assets.example.test",
        "https://assets.example.test:8443",
        "https://assets.example.test/path",
        "https://assets.example.test?token=x",
        "https://assets.example.test#fragment",
        "https://catalog.r2.dev",
    ],
)
def test_catalog_r2_public_url_rejects_non_exact_or_r2_dev_origins(monkeypatch, base_url):
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "CATALOG_ASSET_STORAGE_PROVIDER", "r2", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_PUBLIC_BASE_URL", base_url, raising=False)

    with pytest.raises(RuntimeError, match="catalogo"):
        index._catalog_asset_public_url(f"{'d' * 64}.png")


def test_catalog_cache_fingerprint_changes_with_provider_and_public_origin(monkeypatch):
    payload = _mock_supplier_catalog()
    object_name = f"{'b' * 64}.png"
    payload["items"][0]["attributes"]["approved_asset"] = {
        "bucket": "catalog-assets",
        "path": object_name,
        "image_kind": "official",
        "approved": True,
    }
    monkeypatch.setattr(
        index,
        "db_get_published_catalog_snapshot",
        lambda supplier, version_id: {"id": version_id, "supplier": supplier, "payload": payload},
    )
    monkeypatch.setattr(index, "db_get_published_catalog_version_id", lambda supplier: "snapshot-1")
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "CATALOG_ASSET_STORAGE_PROVIDER", "supabase", raising=False)
    monkeypatch.setattr(index, "SUPABASE_URL", "https://project.supabase.co")
    index._SUPPLIER_CATALOG_CACHE.clear()

    first = index._load_supplier_catalog_cached("cr-global")

    monkeypatch.setattr(index, "CATALOG_ASSET_STORAGE_PROVIDER", "r2", raising=False)
    monkeypatch.setattr(
        index, "CATALOG_ASSET_PUBLIC_BASE_URL", "https://assets.example.test", raising=False
    )
    second = index._load_supplier_catalog_cached("cr-global")

    assert first["items"][0]["image_url"] == (
        f"https://project.supabase.co/storage/v1/object/public/catalog-assets/{object_name}"
    )
    assert second["items"][0]["image_url"] == f"https://assets.example.test/{object_name}"
    assert index._SUPPLIER_CATALOG_CACHE["cr-global"]["storage_fingerprint"] == (
        "r2",
        "https://assets.example.test",
    )


class _CatalogR2Error(Exception):
    def __init__(self, status, code):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class _CatalogR2Client:
    def __init__(self, *, put=None, head=None):
        self.put = list(put or [{}])
        self.head = list(head or [])
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        result = self.put.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        result = self.head.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _configure_catalog_r2(monkeypatch, client):
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "CATALOG_ASSET_STORAGE_PROVIDER", "r2", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_ACCOUNT_ID", "catalog-account", raising=False)
    monkeypatch.setattr(
        index,
        "CATALOG_ASSET_R2_ENDPOINT_URL",
        "https://catalog-account.r2.cloudflarestorage.com",
        raising=False,
    )
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_ACCESS_KEY_ID", "catalog-access", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_SECRET_ACCESS_KEY", "catalog-secret", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_SESSION_TOKEN", "", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_BUCKET", "catalog-assets", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_REGION", "auto", raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_PUBLIC_BASE_URL", "https://assets.example.test", raising=False)
    monkeypatch.setattr(index, "_CATALOG_ASSET_R2_CLIENT", client, raising=False)


def test_catalog_r2_upload_uses_exact_create_only_contract_before_registry(monkeypatch):
    content = b"\x89PNG\r\n\x1a\napi r2 image"
    digest = hashlib.sha256(content).hexdigest()
    object_name = digest + ".png"
    client = _CatalogR2Client()
    _configure_catalog_r2(monkeypatch, client)
    registered = []
    monkeypatch.setattr(index, "_register_catalog_asset", lambda *args: registered.append(args))

    index._upload_catalog_asset(object_name, content, "image/png")

    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "catalog-assets",
                "Key": object_name,
                "Body": content,
                "IfNoneMatch": "*",
                "ContentType": "image/png",
                "CacheControl": "public, max-age=31536000, immutable",
                "Metadata": {"sha256": digest},
            },
        )
    ]
    assert registered == [(object_name, len(content), "image/png")]


def test_catalog_r2_registry_uses_catalog_provider_and_logical_bucket(monkeypatch):
    client = _CatalogR2Client()
    _configure_catalog_r2(monkeypatch, client)
    captured = {}
    monkeypatch.setattr(index, "_use_postgres", lambda: True)
    monkeypatch.setattr(
        index,
        "_pg_write",
        lambda sql, params: captured.update(sql=sql, params=params)
        or {"value": f"{'a' * 64}.png"},
    )

    object_name = f"{'a' * 64}.png"
    index._register_catalog_asset(object_name, 123, "image/png")

    assert captured["params"] == (
        object_name,
        "r2",
        "catalog-assets",
        123,
        "image/png",
    )


def test_catalog_r2_precondition_requires_matching_head_before_registry(monkeypatch):
    content = b"\x89PNG\r\n\x1a\napi r2 retry"
    digest = hashlib.sha256(content).hexdigest()
    object_name = digest + ".png"
    matching = {
        "ContentLength": len(content),
        "ContentType": "image/png",
        "CacheControl": "public, max-age=31536000, immutable",
        "Metadata": {"sha256": digest},
    }
    client = _CatalogR2Client(
        put=[_CatalogR2Error(412, "PreconditionFailed")],
        head=[matching],
    )
    _configure_catalog_r2(monkeypatch, client)
    registered = []
    monkeypatch.setattr(index, "_register_catalog_asset", lambda *args: registered.append(args))

    index._upload_catalog_asset(object_name, content, "image/png")
    assert [name for name, _kwargs in client.calls] == ["put_object", "head_object"]
    assert registered == [(object_name, len(content), "image/png")]

    client = _CatalogR2Client(
        put=[_CatalogR2Error(412, "PreconditionFailed")],
        head=[matching | {"CacheControl": "no-cache"}],
    )
    _configure_catalog_r2(monkeypatch, client)
    registered.clear()
    with pytest.raises(RuntimeError, match="incompatible"):
        index._upload_catalog_asset(object_name, content, "image/png")
    assert registered == []


@pytest.mark.parametrize(
    ("cache_control", "expected"),
    [
        (None, False),
        ("no-cache", False),
        ("public, max-age=31536000, immutable", True),
    ],
)
def test_catalog_r2_head_requires_exact_immutable_cache_control(
    monkeypatch, cache_control, expected
):
    content = b"\x89PNG\r\n\x1a\napi r2 head"
    digest = hashlib.sha256(content).hexdigest()
    info = {
        "ContentLength": len(content),
        "ContentType": "image/png",
        "Metadata": {"sha256": digest},
    }
    if cache_control is not None:
        info["CacheControl"] = cache_control
    client = _CatalogR2Client(head=[info])
    _configure_catalog_r2(monkeypatch, client)

    assert index._catalog_asset_r2_matches(
        digest + ".png", digest, len(content), "image/png"
    ) is expected


@pytest.mark.parametrize(
    ("session_token", "expected_token"),
    [("catalog-session-token", "catalog-session-token"), ("", None)],
)
def test_catalog_r2_client_uses_only_optional_catalog_session_token(
    monkeypatch, session_token, expected_token
):
    captured = []
    quote_client = object()
    _configure_catalog_r2(monkeypatch, None)
    monkeypatch.setattr(index, "_CATALOG_ASSET_R2_CLIENT", None, raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_R2_SESSION_TOKEN", session_token, raising=False)
    monkeypatch.setattr(index, "_R2_CLIENT", quote_client)
    monkeypatch.setattr(index, "R2_SESSION_TOKEN", "quote-session-token", raising=False)
    monkeypatch.setenv("AWS_SESSION_TOKEN", "aws-chain-token")
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda service, **kwargs: captured.append((service, kwargs)) or object()),
    )

    catalog_client = index._catalog_asset_r2_client()

    assert catalog_client is not quote_client
    assert len(captured) == 1
    service, kwargs = captured[0]
    assert service == "s3"
    assert kwargs.get("aws_session_token") == expected_token
    assert ("aws_session_token" in kwargs) is (expected_token is not None)
    assert "quote-session-token" not in repr(kwargs)
    assert "aws-chain-token" not in repr(kwargs)


def test_health_reports_catalog_readiness_without_catalog_or_quote_secrets(monkeypatch):
    client = _CatalogR2Client()
    _configure_catalog_r2(monkeypatch, client)
    monkeypatch.setattr(index, "R2_ACCESS_KEY_ID", "quote-access")
    monkeypatch.setattr(index, "R2_SECRET_ACCESS_KEY", "quote-secret")
    monkeypatch.setattr(index, "R2_BUCKET", "quote-files")
    monkeypatch.setattr(
        index, "CATALOG_ASSET_R2_SESSION_TOKEN", "catalog-session-token", raising=False
    )

    payload = index.health()
    serialized = json.dumps(payload)

    assert payload["catalog_asset_storage_provider"] == "r2"
    assert payload["catalog_asset_storage_configured"] is True
    assert payload["catalog_asset_public_configured"] is True
    assert payload["catalog_asset_ready"] is True
    assert client.calls == []
    assert "catalog-access" not in serialized
    assert "catalog-secret" not in serialized
    assert "quote-access" not in serialized
    assert "quote-secret" not in serialized
    assert "catalog-account" not in serialized
    assert "catalog-session-token" not in serialized
    assert "catalog-assets" not in serialized


def test_catalog_and_quote_r2_clients_and_buckets_are_isolated(monkeypatch):
    catalog_client = _CatalogR2Client()
    quote_client = object()
    _configure_catalog_r2(monkeypatch, catalog_client)
    monkeypatch.setattr(index, "QUOTE_STORAGE_PROVIDER", "r2")
    monkeypatch.setattr(index, "R2_BUCKET", "quote-files")
    monkeypatch.setattr(index, "_R2_CLIENT", quote_client)

    assert index._catalog_asset_r2_client() is catalog_client
    assert index._r2_client() is quote_client
    assert index.CATALOG_ASSET_R2_BUCKET == "catalog-assets"
    assert index.R2_BUCKET == "quote-files"


def test_catalog_asset_upload_rejects_incompatible_conflict_before_registry(monkeypatch):
    content = b"\x89PNG\r\n\x1a\nincompatible"
    object_name = f"{hashlib.sha256(content).hexdigest()}.png"
    conflict = urllib.error.HTTPError("redacted", 409, "conflict", {}, BytesIO(b"exists"))
    registered = []

    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "SUPABASE_URL", "https://example.test")
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", "test-key")
    monkeypatch.setattr(index, "_register_catalog_asset", lambda *args: registered.append(args), raising=False)
    monkeypatch.setattr(index.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(conflict))

    with pytest.raises(RuntimeError, match="[Cc]onflicto incompatible"):
        index._upload_catalog_asset(object_name, content, "image/png")

    assert registered == []


def test_catalog_asset_upload_registers_after_verified_put(monkeypatch):
    content = b"\x89PNG\r\n\x1a\nregistered"
    object_name = f"{hashlib.sha256(content).hexdigest()}.png"
    registered = []

    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "SUPABASE_URL", "https://example.test")
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", "test-key")
    monkeypatch.setattr(index, "_register_catalog_asset", lambda *args: registered.append(args), raising=False)

    class Uploaded:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(index.urllib.request, "urlopen", lambda *_args, **_kwargs: Uploaded())

    index._upload_catalog_asset(object_name, content, "image/png")

    assert registered == [(object_name, len(content), "image/png")]


def test_dev_catalog_asset_download_serves_hash_named_png(monkeypatch, tmp_path):
    object_name = f"{'e' * 64}.png"
    content = b"\x89PNG\r\n\x1a\nlocal-catalog-image"
    destination = tmp_path / "catalog-assets" / object_name
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "DEV_STORE_DIR", tmp_path)

    response = _client().get(f"/dev/catalog-assets/{object_name}")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_published_catalog_hydrates_legacy_asset_with_safe_existing_kind(monkeypatch):
    payload = _mock_supplier_catalog()
    payload["items"][0]["image_kind"] = "official"
    payload["items"][0]["attributes"]["approved_asset"] = {
        "bucket": "catalog-assets",
        "path": f"{'c' * 64}.png",
        "approved": True,
    }
    monkeypatch.setattr(
        index,
        "db_get_published_catalog_snapshot",
        lambda supplier, version_id: {"id": version_id, "supplier": supplier, "payload": payload},
    )
    monkeypatch.setattr(index, "db_get_published_catalog_version_id", lambda supplier: "snapshot-legacy")
    monkeypatch.setattr(index, "SUPABASE_URL", "https://project.supabase.co")
    index._SUPPLIER_CATALOG_CACHE.clear()

    catalog = index._load_supplier_catalog_cached("cr-global")

    assert catalog["items"][0]["image_kind"] == "official"


def test_delete_quote_releases_generic_supplier_reservations(monkeypatch):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "completed",
            "input_path": None,
            "output_path": None,
        },
    )
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: calls.append(("storage", job["id"])))
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: calls.append(("tarkett", job_id)))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: calls.append(("offiho", job_id)))
    monkeypatch.setattr(
        index,
        "db_release_catalog_reservations",
        lambda job_id: calls.append(("generic", job_id)),
        raising=False,
    )
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("delete", job_id)))

    response = _client().delete("/cotizaciones/job-1", headers=_auth_headers())

    assert response.status_code == 200
    assert ("generic", "job-1") in calls
    assert calls.index(("generic", "job-1")) < calls.index(("delete", "job-1"))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/catalog-sync-runs"),
        ("post", "/admin/catalog-sync/cr-global"),
        ("get", f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}"),
        ("post", f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/approve"),
        ("post", f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/reject"),
        ("post", f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images"),
    ],
)
def test_catalog_admin_routes_require_admin(monkeypatch, method, path):
    _mock_user(monkeypatch)
    kwargs = {}
    if path.endswith("/images"):
        kwargs = {
            "data": {"item_index": "0"},
            "files": {"file": ("chair.png", _PNG_BYTES, "image/png")},
        }
    elif method == "post" and "/catalog-sync/" not in path:
        kwargs = {"json": {"review_note": "Reviewed"}}

    response = getattr(_client(), method)(path, headers=_auth_headers(), **kwargs)

    assert response.status_code == 403


def test_catalog_admin_sync_queues_manual_run_and_wakes_worker(monkeypatch):
    _mock_user(monkeypatch, admin=True)
    _enable_generic_catalogs(monkeypatch, "cr-global")
    calls = []

    def fake_create(supplier, requested_by, trigger_type="manual"):
        calls.append(("create", supplier, requested_by, trigger_type))
        return {
            "id": _CATALOG_RUN_ID,
            "supplier": supplier,
            "status": "requested",
            "trigger_type": trigger_type,
            "requested_by": requested_by,
        }

    monkeypatch.setattr(index, "db_create_catalog_sync_run", fake_create, raising=False)
    monkeypatch.setattr(index, "_wake_worker", lambda: calls.append(("wake",)))

    response = _client().post("/admin/catalog-sync/cr-global", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["run"]["status"] == "requested"
    assert calls == [("create", "cr-global", 7, "manual"), ("wake",)]


@pytest.mark.parametrize(
    ("action", "helper_name", "result_status"),
    [
        ("approve", "db_publish_catalog_snapshot", "published"),
        ("reject", "db_reject_catalog_snapshot", "rejected"),
    ],
)
def test_catalog_admin_approve_and_reject_use_atomic_candidate_rpc(
    monkeypatch, action, helper_name, result_status
):
    _mock_user(monkeypatch, admin=True)
    calls = []
    monkeypatch.setattr(
        index,
        "db_get_catalog_sync_run",
        lambda run_id: {
            "id": run_id,
            "status": "awaiting_approval",
            "candidate_version_id": _CATALOG_CANDIDATE_ID,
        },
        raising=False,
    )

    def fake_review(candidate_id, reviewed_by, review_note):
        calls.append((candidate_id, reviewed_by, review_note))
        return {"candidate_id": candidate_id, "status": result_status}

    monkeypatch.setattr(index, helper_name, fake_review, raising=False)

    response = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/{action}",
        headers=_auth_headers(),
        json={"review_note": "Reviewed atomically"},
    )

    assert response.status_code == 200
    assert calls == [(_CATALOG_CANDIDATE_ID, 7, "Reviewed atomically")]


@pytest.mark.parametrize(
    ("case", "filename", "content", "content_type", "expected_status"),
    [
        ("header_false", "chair.png", b"\x89PNG\r\n\x1a\nnot-an-image", "image/png", 400),
        ("truncated", "chair.png", _PNG_BYTES[:-8], "image/png", 400),
        ("polyglot", "chair.png", _PNG_BYTES + b"<script>alert(1)</script>", "image/png", 400),
        ("jpeg_polyglot", "chair.jpg", _image_bytes("JPEG") + b"payload\xff\xd9", "image/jpeg", 400),
        ("animated", "chair.png", _image_bytes(animated=True), "image/png", 400),
        ("mime", "chair.jpg", _PNG_BYTES, "image/jpeg", 400),
        ("size", "chair.png", _PNG_BYTES + b"0" * (8 * 1024 * 1024), "image/png", 413),
        ("dimensions", "chair.png", _image_bytes(size=(8193, 1)), "image/png", 400),
    ],
    ids=["header-false", "truncated", "polyglot", "jpeg-polyglot", "animated", "mime", "size", "dimensions"],
)
def test_catalog_admin_image_upload_rejects_unsafe_images(
    monkeypatch, case, filename, content, content_type, expected_status
):
    _mock_user(monkeypatch, admin=True)
    monkeypatch.setattr(
        index,
        "db_get_catalog_sync_run",
        lambda run_id: {"id": run_id, "candidate_version_id": _CATALOG_CANDIDATE_ID},
        raising=False,
    )
    monkeypatch.setattr(
        index,
        "_upload_catalog_asset",
        lambda *args: (_ for _ in ()).throw(AssertionError("invalid image must not upload")),
        raising=False,
    )
    monkeypatch.setattr(
        index,
        "db_clone_catalog_candidate_with_image_metadata",
        lambda *args: (_ for _ in ()).throw(AssertionError("invalid image must not clone")),
        raising=False,
    )

    response = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images",
        headers=_auth_headers(),
        data={"item_index": "0"},
        files={"file": (filename, content, content_type)},
    )

    assert response.status_code == expected_status, case


def test_catalog_admin_image_upload_rejects_pixel_bomb_and_oversized_output(monkeypatch):
    _mock_user(monkeypatch, admin=True)
    monkeypatch.setattr(index, "CATALOG_ASSET_MAX_PIXELS", 1, raising=False)
    monkeypatch.setattr(index, "_upload_catalog_asset", lambda *args: pytest.fail("unsafe image uploaded"))

    pixel_bomb = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images",
        headers=_auth_headers(),
        data={"item_index": "0"},
        files={"file": ("chair.png", _PNG_BYTES, "image/png")},
    )

    assert pixel_bomb.status_code == 400

    monkeypatch.setattr(index, "CATALOG_ASSET_MAX_PIXELS", 25_000_000, raising=False)
    monkeypatch.setattr(index, "CATALOG_ASSET_MAX_OUTPUT_BYTES", 1, raising=False)
    oversized_output = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images",
        headers=_auth_headers(),
        data={"item_index": "0"},
        files={"file": ("chair.png", _PNG_BYTES, "image/png")},
    )

    assert oversized_output.status_code == 413


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("chair.png", _image_bytes(metadata=True), "image/png"),
        ("chair.jpg", _image_bytes("JPEG"), "image/jpeg"),
        ("chair.webp", _image_bytes("WEBP"), "image/webp"),
    ],
)
def test_catalog_admin_image_upload_reencodes_canonical_png_and_hashes_output(
    monkeypatch, filename, content, content_type
):
    _mock_user(monkeypatch, admin=True)
    calls = []
    monkeypatch.setattr(
        index,
        "db_get_catalog_sync_run",
        lambda run_id: {
            "id": run_id,
            "status": "awaiting_approval",
            "candidate_version_id": _CATALOG_CANDIDATE_ID,
        },
        raising=False,
    )

    def fake_upload(object_name, content, content_type):
        calls.append(("upload", object_name, content, content_type))

    def fake_clone(candidate_id, reviewed_by, object_name, json_path, image_kind, image_label, image_references):
        calls.append(("clone", candidate_id, reviewed_by, object_name, json_path, image_kind, image_label, image_references))
        return _CATALOG_NEW_CANDIDATE_ID

    monkeypatch.setattr(index, "_upload_catalog_asset", fake_upload, raising=False)
    monkeypatch.setattr(
        index,
        "db_clone_catalog_candidate_with_image_metadata",
        fake_clone,
        raising=False,
    )

    response = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images",
        headers=_auth_headers(),
        data={"item_index": "0"},
        files={"file": (filename, content, content_type)},
    )

    assert response.status_code == 200
    uploaded = calls[0][2]
    object_name = f"{hashlib.sha256(uploaded).hexdigest()}.png"
    with Image.open(BytesIO(uploaded)) as normalized:
        normalized.load()
        assert normalized.format == "PNG"
        assert normalized.size == (2, 2)
        assert normalized.n_frames == 1
        assert "Comment" not in normalized.info
    assert uploaded != content
    assert calls == [
        ("upload", object_name, uploaded, "image/png"),
        ("clone", _CATALOG_CANDIDATE_ID, 7, object_name, ["items", "0"], "official", "", []),
    ]
    assert response.json()["object_name"] == object_name


def test_catalog_admin_generated_image_requires_traceable_metadata_and_uses_additive_rpc(monkeypatch):
    _mock_user(monkeypatch, admin=True)
    monkeypatch.setattr(
        index,
        "db_get_catalog_sync_run",
        lambda run_id: {
            "id": run_id,
            "status": "awaiting_approval",
            "candidate_version_id": _CATALOG_CANDIDATE_ID,
        },
        raising=False,
    )
    calls = []
    monkeypatch.setattr(index, "_upload_catalog_asset", lambda *args: calls.append(("upload", *args)), raising=False)
    monkeypatch.setattr(
        index,
        "db_clone_catalog_candidate_with_image_metadata",
        lambda *args: calls.append(("clone", *args)) or _CATALOG_NEW_CANDIDATE_ID,
        raising=False,
    )

    missing_metadata = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images",
        headers=_auth_headers(),
        data={"item_index": "0", "image_kind": "generated_reference"},
        files={"file": ("chair.png", _PNG_BYTES, "image/png")},
    )

    assert missing_metadata.status_code == 400
    assert calls == []

    response = _client().post(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}/images",
        headers=_auth_headers(),
        data={
            "item_index": "0",
            "image_kind": "generated_reference",
            "image_label": "Referencia creada por diseno",
            "image_references": '["https://source.example/product"]',
        },
        files={"file": ("chair.png", _PNG_BYTES, "image/png")},
    )

    assert response.status_code == 200
    uploaded = calls[0][2]
    object_name = f"{hashlib.sha256(uploaded).hexdigest()}.png"
    assert calls == [
        ("upload", object_name, uploaded, "image/png"),
        (
            "clone",
            _CATALOG_CANDIDATE_ID,
            7,
            object_name,
            ["items", "0"],
            "generated_reference",
            "Referencia creada por diseno",
            ["https://source.example/product"],
        ),
    ]


def test_catalog_asset_rpc_uses_text_array_with_direct_postgres(monkeypatch):
    captured = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(index, "_use_postgres", lambda: True)

    def fake_write(sql, params):
        captured.update({"sql": sql, "params": params})
        return {"value": _CATALOG_NEW_CANDIDATE_ID}

    monkeypatch.setattr(index, "_pg_write", fake_write)

    result = index.db_clone_catalog_candidate_with_asset(
        _CATALOG_CANDIDATE_ID,
        7,
        f"{'c' * 64}.png",
        ["items", "0"],
    )

    assert result == _CATALOG_NEW_CANDIDATE_ID
    assert "ARRAY[%s, %s]::TEXT[]" in captured["sql"]
    assert captured["params"] == (
        _CATALOG_CANDIDATE_ID,
        7,
        f"{'c' * 64}.png",
        "items",
        "0",
    )


def test_catalog_image_metadata_rpc_is_additive_and_keeps_traceability(monkeypatch):
    captured = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(index, "_use_postgres", lambda: True)
    monkeypatch.setattr(
        index,
        "_pg_write",
        lambda sql, params: captured.update({"sql": sql, "params": params}) or {"value": _CATALOG_NEW_CANDIDATE_ID},
    )

    result = index.db_clone_catalog_candidate_with_image_metadata(
        _CATALOG_CANDIDATE_ID,
        7,
        f"{'d' * 64}.png",
        ["items", "0"],
        "generated_reference",
        "Referencia creada por diseno",
        ["https://source.example/product"],
    )

    assert result == _CATALOG_NEW_CANDIDATE_ID
    assert "saas_clone_catalog_candidate_with_image_metadata" in captured["sql"]
    assert captured["sql"].count("::TEXT[]") == 2
    assert captured["params"] == (
        _CATALOG_CANDIDATE_ID,
        7,
        f"{'d' * 64}.png",
        "items",
        "0",
        "generated_reference",
        "Referencia creada por diseno",
        "https://source.example/product",
    )


def test_catalog_official_image_rpc_uses_empty_text_array_with_direct_postgres(monkeypatch):
    captured = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(index, "_use_postgres", lambda: True)
    monkeypatch.setattr(
        index,
        "_pg_write",
        lambda sql, params: captured.update({"sql": sql, "params": params}) or {"value": _CATALOG_NEW_CANDIDATE_ID},
    )

    result = index.db_clone_catalog_candidate_with_image_metadata(
        _CATALOG_CANDIDATE_ID,
        7,
        f"{'e' * 64}.png",
        ["items", "0"],
        "official",
        "",
        [],
    )

    assert result == _CATALOG_NEW_CANDIDATE_ID
    assert "ARRAY[]::TEXT[]" in captured["sql"]
    assert captured["params"] == (
        _CATALOG_CANDIDATE_ID,
        7,
        f"{'e' * 64}.png",
        "items",
        "0",
        "official",
        "",
    )


def test_catalog_admin_run_detail_returns_bounded_sanitized_real_diff(monkeypatch):
    _mock_user(monkeypatch, admin=True)
    base = _mock_supplier_catalog()
    candidate = _mock_supplier_catalog()
    base["items"][0]["price_net"] = "100.000000"
    candidate["items"][0]["price_net"] = "125.000000"
    candidate["items"][0]["source_reference"] = json.dumps([
        {
            "file_id": "f" * 64,
            "sheet_or_page": "Lista 2026",
            "cell_or_bbox": "E9",
            "private_object_path": "catalog-sources/private.xlsx",
        }
    ])
    run = {
        "id": _CATALOG_RUN_ID,
        "supplier": "cr-global",
        "status": "awaiting_approval",
        "candidate_version_id": _CATALOG_CANDIDATE_ID,
        "metrics": {"changed_items": 1},
    }
    versions = {
        _CATALOG_CANDIDATE_ID: {
            "id": _CATALOG_CANDIDATE_ID,
            "supplier": "cr-global",
            "status": "candidate",
            "payload": candidate,
            "base_published_version_id": "published-1",
            "previous_snapshot_id": None,
        },
        "published-1": {
            "id": "published-1",
            "supplier": "cr-global",
            "status": "published",
            "payload": base,
            "base_published_version_id": None,
            "previous_snapshot_id": None,
        },
    }
    monkeypatch.setattr(index, "db_get_catalog_sync_run", lambda run_id: run)
    monkeypatch.setattr(index, "db_get_catalog_snapshot_version", lambda version_id: versions.get(version_id), raising=False)

    response = _client().get(
        f"/admin/catalog-sync-runs/{_CATALOG_RUN_ID}", headers=_auth_headers()
    )

    assert response.status_code == 200
    detail = response.json()["run"]
    assert detail["metrics"] == {"changed_items": 1}
    assert detail["diff"]["truncated"] is False
    changed = detail["diff"]["items"]
    assert changed == [
        {
            "item_id": "cr-global:chair-1",
            "field": "price_net",
            "before": "100.000000",
            "after": "125.000000",
            "source_coordinate": "Lista 2026!E9",
            "material_type": "commercial",
        }
    ]
    serialized = json.dumps(detail["diff"])
    assert "file_id" not in serialized
    assert "private.xlsx" not in serialized


def test_failed_catalog_cleanup_preserves_job_when_reservation_release_fails(monkeypatch):
    calls = []

    def fail_release(job_id):
        calls.append(("release", job_id))
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("job", job_id)))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: calls.append(("storage", paths)))
    monkeypatch.setattr(
        index,
        "db_update_quote_job",
        lambda job_id, updates: calls.append(("mark_failed", job_id, updates)),
    )

    index._cleanup_failed_catalog_quote("job-1", "users/7/jobs/job-1/input.json", fail_release)

    assert calls == [
        ("release", "job-1"),
        (
            "mark_failed",
            "job-1",
            {
                "status": "failed",
                "error_message": "cleanup_pending:release_reservations",
            },
        ),
    ]


def test_failed_catalog_cleanup_marks_job_failed_when_delete_fails(monkeypatch):
    calls = []

    def fail_delete(job_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(index, "db_delete_quote_job", fail_delete)
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: calls.append(("storage", paths)))
    monkeypatch.setattr(
        index,
        "db_update_quote_job",
        lambda job_id, updates: calls.append(("mark_failed", job_id, updates)),
    )

    index._cleanup_failed_catalog_quote(
        "job-1",
        "users/7/jobs/job-1/input.json",
        lambda job_id: calls.append(("release", job_id)),
    )

    assert calls == [
        ("release", "job-1"),
        ("storage", ["users/7/jobs/job-1/input.json"]),
        (
            "mark_failed",
            "job-1",
            {"status": "failed", "error_message": "cleanup_pending:delete_job"},
        ),
    ]


def test_failed_catalog_cleanup_retains_job_when_input_delete_fails(monkeypatch):
    calls = []

    def fail_storage(paths):
        calls.append(("storage", paths))
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(index, "_delete_storage_paths", fail_storage)
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("job", job_id)))
    monkeypatch.setattr(
        index,
        "db_update_quote_job",
        lambda job_id, updates: calls.append(("mark_failed", job_id, updates)),
    )

    index._cleanup_failed_catalog_quote(
        "job-1",
        "users/7/jobs/job-1/input.json",
        lambda job_id: calls.append(("release", job_id)),
    )

    assert calls == [
        ("release", "job-1"),
        ("storage", ["users/7/jobs/job-1/input.json"]),
        (
            "mark_failed",
            "job-1",
            {"status": "failed", "error_message": "cleanup_pending:delete_input"},
        ),
    ]


def test_catalog_sync_postgrest_insert_uses_only_granted_columns(monkeypatch):
    captured = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", None)
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(index, "_use_postgres", lambda: False)
    monkeypatch.setattr(
        index,
        "db_get_catalog_source",
        lambda supplier: {"id": "source-1", "supplier": supplier, "label": "CR Global"},
    )

    def fake_request(method, path, params=None, json_data=None):
        captured.update({"method": method, "path": path, "payload": json_data})
        return [{"id": "run-1", "status": "requested", **json_data}]

    monkeypatch.setattr(index, "_supabase_req", fake_request)

    saved = index.db_create_catalog_sync_run("cr-global", 7)

    assert saved["status"] == "requested"
    assert captured == {
        "method": "POST",
        "path": "/saas_catalog_sync_runs",
        "payload": {
            "source_id": "source-1",
            "trigger_type": "manual",
            "requested_by": 7,
            "metrics": {},
        },
    }


def test_catalog_reservation_summary_uses_single_postgrest_rpc(monkeypatch):
    captured = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", None)
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(index, "_use_postgres", lambda: False)

    def fake_request(method, path, params=None, json_data=None):
        captured.update({"method": method, "path": path, "params": params, "json_data": json_data})
        return [
            {
                "internal_id": "cr-global:chair-1",
                "reserved_quantity": "1001.000000",
                "reserved_by_others": True,
            }
        ]

    monkeypatch.setattr(index, "_supabase_req", fake_request)

    rows = index.db_catalog_reservation_summary("cr-global", 7)

    assert rows[0]["reserved_quantity"] == "1001.000000"
    assert captured == {
        "method": "POST",
        "path": "/rpc/saas_catalog_reservation_summary",
        "params": None,
        "json_data": {"p_supplier": "cr-global", "p_usuario_id": 7},
    }


def test_catalog_atomic_reservation_uses_single_postgrest_rpc(monkeypatch):
    captured = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", None)
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(index, "_use_postgres", lambda: False)

    def fake_request(method, path, params=None, json_data=None):
        captured.update({"method": method, "path": path, "params": params, "json_data": json_data})
        return [
            {
                "internal_id": "chair-1",
                "reserved_before": "4.000000",
                "available_before": "1.000000",
                "insufficient": True,
                "reserved_by_others": True,
            }
        ]

    monkeypatch.setattr(index, "_supabase_req", fake_request)
    lines = [{"internal_id": "chair-1", "sku": "CHAIR-1", "quantity": "2", "stock": "5"}]

    rows = index.db_reserve_catalog_items(
        7,
        "11111111-1111-1111-1111-111111111111",
        "cr-global",
        lines,
    )

    assert rows[0]["insufficient"] is True
    assert captured == {
        "method": "POST",
        "path": "/rpc/saas_reserve_catalog_items",
        "params": None,
        "json_data": {
            "p_usuario_id": 7,
            "p_quote_job_id": "11111111-1111-1111-1111-111111111111",
            "p_supplier": "cr-global",
            "p_lines": lines,
        },
    }


def test_catalog_reservation_aggregation_keeps_decimal_quantity(monkeypatch):
    captured = []
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(index, "_use_postgres", lambda: True)

    def fake_write(sql, params):
        captured.append(params)
        return {"id": params[0], "quantity": params[4]}

    monkeypatch.setattr(index, "_pg_write", fake_write)

    rows = index.db_create_catalog_reservations(
        7,
        "11111111-1111-1111-1111-111111111111",
        "cr-global",
        [
            {"internal_id": "chair-1", "sku": "CHAIR-1", "quantity": "0.1"},
            {"internal_id": "chair-1", "sku": "CHAIR-1", "quantity": "0.2"},
        ],
    )

    assert len(rows) == 1
    assert captured[0][4] == "0.3"


def test_generic_internal_catalog_put_route_is_absent_and_explicit_routes_remain():
    put_paths = [
        route.path
        for route in index.app.routes
        if "PUT" in getattr(route, "methods", set())
        and route.path.startswith("/internal/catalogs/")
    ]

    assert "/internal/catalogs/tarkett" in put_paths
    assert "/internal/catalogs/offiho" in put_paths
    assert all("{" not in path for path in put_paths)


def test_deployable_api_copies_have_identical_sha256():
    paths = [
        Path("mobiliti_saas/web/api/index.py"),
        Path("mobiliti_saas/api/index.py"),
        Path("vercel_deploy/api/index.py"),
    ]
    hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    assert len(hashes) == 1


def test_dev_offiho_managed_image_uses_local_vite_without_changing_production(
    monkeypatch,
):
    managed = (
        "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
        "residual-visual-exact/example.jpg?v=1"
    )
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "DEV_WEB_BASE_URL", "http://127.0.0.1:5174")

    assert index._dev_offiho_image_url(managed) == (
        "http://127.0.0.1:5174/catalog-assets/offiho/"
        "residual-visual-exact/example.jpg?v=1"
    )
    assert index._dev_offiho_image_url("https://www.offiho.com/product.jpg") == (
        "https://www.offiho.com/product.jpg"
    )

    monkeypatch.setattr(index, "DEV_MODE", False)
    assert index._dev_offiho_image_url(managed) == managed


@pytest.mark.parametrize(
    "object_path",
    [
        "C:/Windows/win.ini",
        r"C:\Windows\win.ini",
        "C:relative.xlsx",
        r"\\server\share\quotation.xlsx",
        "/etc/passwd",
        r"\Windows\win.ini",
        "../escape.xlsx",
        "users/7/../escape.xlsx",
        "users//7/input.xlsx",
        "users/./7/input.xlsx",
        "users/7/input.xlsx/",
        "users/7/in\x00put.xlsx",
    ],
)
def test_dev_storage_path_rejects_absolute_traversal_and_anomalous_paths(
    monkeypatch, tmp_path, object_path
):
    monkeypatch.setattr(index, "DEV_STORE_DIR", tmp_path / "synthetic-dev-store")

    with pytest.raises(RuntimeError, match="^Ruta de storage invalida$"):
        index._dev_storage_file(object_path)


def test_dev_storage_path_resolves_valid_internal_separators_under_exact_root(
    monkeypatch, tmp_path
):
    store = tmp_path / "synthetic-dev-store"
    monkeypatch.setattr(index, "DEV_STORE_DIR", store)

    candidate = index._dev_storage_file(r"users\7\jobs\job-1\input.xlsx")
    root = (store / "storage" / index.QUOTE_STORAGE_BUCKET).resolve()

    assert candidate == root / "users" / "7" / "jobs" / "job-1" / "input.xlsx"
    assert candidate.relative_to(root).as_posix() == "users/7/jobs/job-1/input.xlsx"


@pytest.mark.parametrize("helper_name", ["_storage_download_bytes", "_storage_upload_bytes"])
def test_dev_storage_read_and_upload_helpers_preserve_path_rejection(
    monkeypatch, tmp_path, helper_name
):
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "DEV_STORE_DIR", tmp_path / "synthetic-dev-store")
    helper = getattr(index, helper_name)

    with pytest.raises(RuntimeError, match="^Ruta de storage invalida$"):
        if helper_name == "_storage_upload_bytes":
            helper("/absolute/input.xlsx", b"synthetic")
        else:
            helper("/absolute/input.xlsx")


def test_mixed_dev_reservation_saves_once_only_after_all_groups_validate(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID, user_id=7)
    saves = []
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(index, "_dev_save", lambda data: saves.append(data))
    groups = [
        {"catalog": "tarkett", "items": [{"identity": "T-1", "sku": "T-1", "quantity": "1", "stock": "5"}]},
        {"catalog": "alma", "items": [{"identity": "alma:desk", "sku": "AL-1", "quantity": "bad", "stock": "5"}]},
    ]
    with pytest.raises(RuntimeError, match="[Rr]eserva mixta"):
        index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, groups)
    assert saves == []


def test_mixed_dev_reservation_serializes_availability_under_one_lock(monkeypatch):
    state = dev_state_with_two_draft_jobs(JOB_A_UUID, JOB_B_UUID, user_id=7)
    configure_thread_safe_dev_store(monkeypatch, state)
    groups = [{
        "catalog": "offiho",
        "items": [{"identity": "OFF-1", "sku": "OFF-1", "quantity": "3", "stock": "5"}],
    }]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(index.db_reserve_mixed_cart, 7, JOB_A_UUID, groups)
        second = pool.submit(index.db_reserve_mixed_cart, 7, JOB_B_UUID, groups)
        snapshots = [first.result(), second.result()]
    assert sorted(row[0]["reserved_before"] for row in snapshots) == ["0.000000", "3.000000"]
    assert all(isinstance(row[0]["available_before"], str) for row in snapshots)


def test_mixed_empty_reservation_validates_job_loads_once_and_never_saves(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    loads = []
    saves = []
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: loads.append(True) or json.loads(json.dumps(state)))
    monkeypatch.setattr(index, "_dev_save", lambda data: saves.append(data))

    assert index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, []) == []
    assert loads == [True]
    assert saves == []
    with pytest.raises(RuntimeError, match="Cotizacion de reserva mixta invalida"):
        index.db_reserve_mixed_cart(8, JOB_MIXED_UUID, [])


def test_mixed_released_reservation_row_prevents_same_job_retry(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    state["tarkett_reservations"].append({
        "quote_job_id": JOB_MIXED_UUID, "product_code": "T-1", "quantity": "1.000000",
        "usuario_id": 7, "status": "released",
    })
    saves = []
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(index, "_dev_save", lambda data: saves.append(data))

    with pytest.raises(RuntimeError, match="ya tiene reservas mixtas"):
        index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, [{
            "catalog": "tarkett", "items": [
                {"identity": "T-1", "sku": "T-1", "quantity": "1", "stock": "5"}
            ],
        }])
    assert saves == []


def test_mixed_remote_reservation_uses_one_rpc_and_validates_snapshot(monkeypatch):
    captured = []
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_postgres", lambda: False)

    def request(method, path, params=None, json_data=None):
        captured.append({"method": method, "path": path, "json_data": json_data})
        return [{
            "catalog": "tarkett", "identity": "T-1", "reserved_before": "0.000000",
            "available_before": "5.000000", "insufficient": False,
            "reserved_by_others": False,
        }]

    monkeypatch.setattr(index, "_supabase_req", request)
    groups = [{"catalog": "tarkett", "items": [
        {"identity": "T-1", "sku": "T-1", "quantity": "1", "stock": "5"}
    ]}]
    result = index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, groups)
    normalized_groups = [{"catalog": "tarkett", "items": [
        {"identity": "T-1", "sku": "T-1", "quantity": "1.000000", "stock": "5.000000"}
    ]}]
    assert result[0]["reserved_before"] == "0.000000"
    assert captured == [{
        "method": "POST", "path": "/rpc/saas_reserve_mixed_cart",
        "json_data": {"p_usuario_id": 7, "p_quote_job_id": JOB_MIXED_UUID, "p_groups": normalized_groups},
    }]

    monkeypatch.setattr(index, "_supabase_req", lambda *_args, **_kwargs: [{
        "catalog": "tarkett", "identity": "UNKNOWN", "reserved_before": "0.000000",
        "available_before": "5.000000", "insufficient": False, "reserved_by_others": False,
    }])
    with pytest.raises(RuntimeError, match="Respuesta de reserva mixta invalida"):
        index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, groups)


def test_mixed_legacy_reservation_wrappers_project_six_place_decimals(monkeypatch):
    calls = []
    monkeypatch.setattr(index, "db_reserve_mixed_cart", lambda user, job, groups: calls.append(groups) or [{
        "catalog": groups[0]["catalog"], "identity": groups[0]["items"][0]["identity"],
        "reserved_before": "0.000000", "available_before": "5.000000",
        "insufficient": False, "reserved_by_others": False,
    }])

    tarkett = index.db_create_tarkett_reservations(7, JOB_A_UUID, [
        {"code": "T-1", "quantity": 1, "available_quantity": Decimal("5")}
    ])
    offiho = index.db_create_offiho_reservations(7, JOB_B_UUID, [
        {"inventory_key": "OFF-1", "sku": "OFF-1", "quantity": 2.5, "available_quantity": 7}
    ])

    assert [call[0]["catalog"] for call in calls] == ["tarkett", "offiho"]
    assert calls[0][0]["items"][0]["quantity"] == "1.000000"
    assert calls[1][0]["items"][0]["stock"] == "7.000000"
    assert tarkett[0]["product_code"] == "T-1"
    assert offiho[0]["product_code"] == "OFF-1"


def test_mixed_legacy_reservation_wrapper_matches_snapshot_by_identity(monkeypatch):
    monkeypatch.setattr(index, "db_reserve_mixed_cart", lambda *_args: [
        {
            "catalog": "tarkett", "identity": "A", "reserved_before": "1.000000",
            "available_before": "4.000000", "insufficient": False,
            "reserved_by_others": True,
        },
        {
            "catalog": "tarkett", "identity": "Z", "reserved_before": "2.000000",
            "available_before": "3.000000", "insufficient": False,
            "reserved_by_others": False,
        },
    ])
    rows = index.db_create_tarkett_reservations(7, JOB_A_UUID, [
        {"code": "Z", "quantity": 1, "available_quantity": 5},
        {"code": "A", "quantity": 1, "available_quantity": 5},
    ])
    assert [(row["product_code"], row["reserved_before"]) for row in rows] == [
        ("Z", "2.000000"), ("A", "1.000000")
    ]


def test_mixed_release_reservation_is_idempotent_and_blocks_queue(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    configure_thread_safe_dev_store(monkeypatch, state)
    index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, [{
        "catalog": "alma", "items": [
            {"identity": "alma:desk", "sku": "AL-1", "quantity": "1", "stock": "5"}
        ],
    }])

    assert index.db_release_mixed_cart(JOB_MIXED_UUID) == {"tarkett": 0, "offiho": 0, "supplier": 1}
    assert index.db_release_mixed_cart(JOB_MIXED_UUID) == {"tarkett": 0, "offiho": 0, "supplier": 0}
    with pytest.raises(RuntimeError, match="ya no esta en borrador"):
        index.db_queue_mixed_quote_job(JOB_MIXED_UUID, {"source_type": "mixed_catalog_cart"})
    assert state["quote_jobs"][0]["status"] == "failed"
    assert state["catalog_reservations"][0]["status"] == "released"


def test_mixed_release_first_rejects_waiting_reservation(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    configure_thread_safe_dev_store(monkeypatch, state)

    assert index.db_release_mixed_cart(JOB_MIXED_UUID) == {"tarkett": 0, "offiho": 0, "supplier": 0}
    with pytest.raises(RuntimeError, match="Cotizacion de reserva mixta invalida"):
        index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, [{
            "catalog": "offiho", "items": [
                {"identity": "OFF-1", "sku": "OFF-1", "quantity": "1", "stock": "5"}
            ],
        }])
    assert not state["offiho_reservations"]


def test_mixed_reservation_cleanup_routes_atomically_for_mixed_source(monkeypatch):
    calls = []
    monkeypatch.setattr(index, "db_release_mixed_cart", lambda job_id: calls.append(("mixed", job_id)))
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: calls.append(("tarkett", job_id)))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: calls.append(("offiho", job_id)))
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: calls.append(("supplier", job_id)))

    index._release_quote_reservations({
        "id": JOB_MIXED_UUID, "metadata": {"source_type": "mixed_catalog_cart"}
    })
    assert calls == [("mixed", JOB_MIXED_UUID)]


def test_mixed_queue_reservation_postgres_and_supabase_are_compare_and_set(monkeypatch):
    metadata = {"source_type": "mixed_catalog_cart"}
    pg_calls = []
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_postgres", lambda: True)
    monkeypatch.setattr(index, "_pg_write", lambda sql, params: pg_calls.append((sql, params)) or {"status": "queued"})
    index.db_queue_mixed_quote_job(JOB_MIXED_UUID, metadata)
    assert "WHERE id = %s AND status = 'draft'" in pg_calls[0][0]
    assert "attempt_token = NULL" in pg_calls[0][0]
    assert "lease_expires_at = NULL" in pg_calls[0][0]
    assert pg_calls[0][1][0] == metadata
    assert pg_calls[0][1][1] is not None

    rest_calls = []
    monkeypatch.setattr(index, "_use_postgres", lambda: False)
    monkeypatch.setattr(index, "_supabase_req", lambda method, path, params=None, json_data=None: rest_calls.append((method, path, params, json_data)) or [{"status": "queued"}])
    index.db_queue_mixed_quote_job(JOB_MIXED_UUID, metadata)
    assert rest_calls[0][0:3] == ("PATCH", "/saas_quote_jobs", {"id": f"eq.{JOB_MIXED_UUID}", "status": "eq.draft"})
    assert rest_calls[0][3]["attempt_token"] is None
    assert rest_calls[0][3]["lease_expires_at"] is None


def test_mixed_reservation_reserve_first_then_release_uses_real_lifecycle_lock(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    entered, allow, overlap, _loads = configure_barrier_dev_store(monkeypatch, state)
    groups = [{"catalog": "tarkett", "items": [
        {"identity": "T-1", "sku": "T-1", "quantity": "2", "stock": "5"}
    ]}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        reserve = pool.submit(index.db_reserve_mixed_cart, 7, JOB_MIXED_UUID, groups)
        release = submit_after_first_load(
            pool, entered, lambda: index.db_release_mixed_cart(JOB_MIXED_UUID)
        )
        try:
            assert not overlap.wait(0.2)
            assert not release.done()
        finally:
            allow.set()
        assert reserve.result()[0]["reserved_before"] == "0.000000"
        assert release.result() == {"tarkett": 1, "offiho": 0, "supplier": 0}

    assert state["quote_jobs"][0]["status"] == "failed"
    assert [row["status"] for row in state["tarkett_reservations"]] == ["released"]


def test_mixed_reservation_release_first_rejects_waiting_reserve_under_real_lock(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    entered, allow, overlap, _loads = configure_barrier_dev_store(monkeypatch, state)
    groups = [{"catalog": "offiho", "items": [
        {"identity": "OFF-1", "sku": "OFF-1", "quantity": "1", "stock": "5"}
    ]}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        release = pool.submit(index.db_release_mixed_cart, JOB_MIXED_UUID)
        reserve = submit_after_first_load(
            pool, entered,
            lambda: index.db_reserve_mixed_cart(7, JOB_MIXED_UUID, groups),
        )
        try:
            assert not overlap.wait(0.2)
            assert not reserve.done()
        finally:
            allow.set()
        assert release.result() == {"tarkett": 0, "offiho": 0, "supplier": 0}
        with pytest.raises(RuntimeError, match="Cotizacion de reserva mixta invalida"):
            reserve.result()

    assert state["quote_jobs"][0]["status"] == "failed"
    assert state["offiho_reservations"] == []


def test_mixed_reservation_legacy_tarkett_reserve_then_release_shares_real_lock(monkeypatch):
    state = dev_state_with_draft_job(JOB_MIXED_UUID)
    entered, allow, overlap, _loads = configure_barrier_dev_store(monkeypatch, state)
    lines = [{"code": "T-LEGACY", "quantity": 2, "available_quantity": 5}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        reserve = pool.submit(
            index.db_create_tarkett_reservations, 7, JOB_MIXED_UUID, lines
        )
        release = submit_after_first_load(
            pool, entered, lambda: index.db_release_mixed_cart(JOB_MIXED_UUID)
        )
        try:
            assert not overlap.wait(0.2)
            assert not release.done()
        finally:
            allow.set()
        assert reserve.result()[0]["reserved_before"] == "0.000000"
        assert release.result()["tarkett"] == 1

    assert state["tarkett_reservations"][0]["status"] == "released"


@pytest.mark.parametrize(
    ("catalog", "wrapper", "identity_field", "identity"),
    [
        ("tarkett", "db_create_tarkett_reservations", "code", "T-SHARED"),
        ("offiho", "db_create_offiho_reservations", "inventory_key", "OFF-SHARED"),
    ],
)
def test_mixed_reservation_legacy_family_and_mixed_snapshot_are_serialized_by_real_lock(
    monkeypatch, catalog, wrapper, identity_field, identity
):
    state = dev_state_with_two_draft_jobs(JOB_A_UUID, JOB_B_UUID)
    entered, allow, overlap, _loads = configure_barrier_dev_store(monkeypatch, state)
    line = {identity_field: identity, "quantity": 2, "available_quantity": 5}
    groups = [{"catalog": catalog, "items": [
        {"identity": identity, "sku": identity, "quantity": "1", "stock": "5"}
    ]}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        legacy = pool.submit(getattr(index, wrapper), 7, JOB_A_UUID, [line])
        mixed = submit_after_first_load(
            pool, entered, lambda: index.db_reserve_mixed_cart(7, JOB_B_UUID, groups)
        )
        try:
            assert not overlap.wait(0.2)
            assert not mixed.done()
        finally:
            allow.set()
        assert legacy.result()[0]["reserved_before"] == "0.000000"
        assert mixed.result()[0]["reserved_before"] == "2.000000"


def test_mixed_reservation_legacy_alma_and_mixed_snapshot_are_serialized_by_real_lock(monkeypatch):
    state = dev_state_with_two_draft_jobs(JOB_A_UUID, JOB_B_UUID)
    entered, allow, overlap, _loads = configure_barrier_dev_store(monkeypatch, state)
    legacy_lines = [
        {"internal_id": "alma:desk", "sku": "AL-1", "quantity": "2", "stock": "5"}
    ]
    groups = [{"catalog": "alma", "items": [
        {"identity": "alma:desk", "sku": "AL-1", "quantity": "1", "stock": "5"}
    ]}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        legacy = pool.submit(
            index.db_reserve_catalog_items, 7, JOB_A_UUID, "alma", legacy_lines
        )
        mixed = submit_after_first_load(
            pool, entered, lambda: index.db_reserve_mixed_cart(7, JOB_B_UUID, groups)
        )
        try:
            assert not overlap.wait(0.2)
            assert not mixed.done()
        finally:
            allow.set()
        assert legacy.result()[0]["reserved_before"] == "0.000000"
        assert mixed.result()[0]["reserved_before"] == "2.000000"


def test_mixed_reservation_legacy_release_and_mixed_reserve_preserve_both_updates(monkeypatch):
    state = dev_state_with_draft_job(JOB_B_UUID)
    state["catalog_reservations"].append({
        "id": "legacy-row", "supplier": "alma", "internal_id": "alma:old",
        "sku": "AL-OLD", "quantity": "1.000000", "usuario_id": 7,
        "quote_job_id": JOB_A_UUID, "status": "active",
    })
    entered, allow, overlap, _loads = configure_barrier_dev_store(monkeypatch, state)
    groups = [{"catalog": "alma", "items": [
        {"identity": "alma:new", "sku": "AL-NEW", "quantity": "1", "stock": "5"}
    ]}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        release = pool.submit(index.db_release_catalog_reservations, JOB_A_UUID)
        reserve = submit_after_first_load(
            pool, entered, lambda: index.db_reserve_mixed_cart(7, JOB_B_UUID, groups)
        )
        try:
            assert not overlap.wait(0.2)
            assert not reserve.done()
        finally:
            allow.set()
        assert release.result()[0]["status"] == "released"
        assert reserve.result()[0]["reserved_before"] == "0.000000"

    rows = {row["id"]: row for row in state["catalog_reservations"]}
    assert rows["legacy-row"]["status"] == "released"
    assert any(row["internal_id"] == "alma:new" and row["status"] == "active" for row in rows.values())


def test_supplier_catalog_module_copies_have_identical_sha256():
    paths = [
        Path("mobiliti_saas/quote_engine/supplier_catalog.py"),
        Path("mobiliti_saas/web/mobiliti_saas/quote_engine/supplier_catalog.py"),
    ]
    hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    assert len(hashes) == 1


def test_reservation_sql_enforces_server_only_rls_and_offiho_job_product_uniqueness():
    root = Path(__file__).resolve().parents[1]
    migration_paths = {
        "saas_tarkett_reservations": root / "mobiliti_saas" / "supabase_setup" / "2026_07_tarkett_reservations.sql",
        "saas_offiho_reservations": root / "mobiliti_saas" / "supabase_setup" / "2026_07_offiho_reservations.sql",
    }
    create_tables = (root / "mobiliti_saas" / "supabase_setup" / "create_tables.sql").read_text(encoding="utf-8").lower()

    for table, path in migration_paths.items():
        migration = path.read_text(encoding="utf-8").lower()
        for sql in (migration, create_tables):
            assert f"alter table {table} enable row level security" in sql
            assert f"revoke all on table {table} from anon, authenticated" in sql
            assert f"grant all on table {table} to service_role" in sql
    for table, path in migration_paths.items():
        for sql in (path.read_text(encoding="utf-8").lower(), create_tables):
            assert f"idx_{table.removeprefix('saas_')}_quote_job_product" in sql
            assert "unique" in sql
            assert "(quote_job_id, product_code)" in sql


def test_init_upload_rejects_unsupported_file(monkeypatch):
    _mock_user(monkeypatch)

    resp = _client().post(
        "/cotizaciones/init-upload",
        headers=_auth_headers(),
        json={"filename": "quotation.xls", "size": 1024},
    )

    assert resp.status_code == 400
    assert ".xlsx o .pdf" in resp.json()["detail"]


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
            "template": "official_2026_gdl",
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
            "template": "official_2026_gdl",
        },
    )

    def fake_update(job_id, updates, *, expected_status=None):
        assert expected_status == "draft"
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
            "template": "official_2026_gdl",
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
            "template": "official_2026_gdl",
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

    def fake_update(job_id, updates, *, expected_status=None):
        assert expected_status == "draft"
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
            "template": "official_2026_gdl",
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
            "template": "official_2026_gdl",
        },
    )

    def fake_update(job_id, updates, *, expected_status=None):
        assert expected_status == "draft"
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
    assert resp.json()["job"]["attempt_token"] is None
    assert resp.json()["job"]["lease_expires_at"] is None


@pytest.mark.parametrize(
    ("input_path", "error_message", "expected_status"),
    [
        (None, "boom", 400),
        ("users/7/jobs/job-1/input.json", "cleanup_pending:release_reservations", 409),
        ("users/7/jobs/job-1/input.json", "cleanup_pending:delete_input", 409),
        ("users/7/jobs/job-1/input.json", "cleanup_pending:delete_job", 409),
    ],
)
def test_submit_failed_job_reuses_retry_preconditions_without_queue_or_lease_reset(
    monkeypatch, input_path, error_message, expected_status,
):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: {
        "id": job_id, "usuario_id": 7, "status": "failed",
        "input_path": input_path, "error_message": error_message,
        "metadata": {"source_type": "mixed_catalog_cart"},
        "template": "official_2026_gdl", "attempt_token": "old-attempt",
        "lease_expires_at": "2026-07-19T00:00:00Z",
    })
    calls = []
    monkeypatch.setattr(index, "db_update_quote_job", lambda *_args, **_kwargs: calls.append("update"))
    monkeypatch.setattr(index, "_wake_worker", lambda: calls.append("wake"))

    response = _client().post(
        "/cotizaciones/job-1/submit", headers=_auth_headers(), json=_valid_submit_body()
    )

    assert response.status_code == expected_status
    assert calls == []


def test_submit_failed_job_queues_when_central_retry_preconditions_pass(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: {
        "id": job_id, "usuario_id": 7, "status": "failed",
        "input_path": "users/7/jobs/job-1/input.json", "error_message": "generator failed",
        "metadata": {"source_type": "mixed_catalog_cart"},
        "template": "official_2026_gdl", "attempt_token": "old-attempt",
        "lease_expires_at": "2026-07-19T00:00:00Z",
    })
    seen = {}

    def update(job_id, updates, *, expected_status=None):
        seen.update(job_id=job_id, updates=updates, expected_status=expected_status)
        return {"id": job_id, **updates}

    wakes = []
    monkeypatch.setattr(index, "db_update_quote_job", update)
    monkeypatch.setattr(index, "_wake_worker", lambda: wakes.append(True))

    response = _client().post(
        "/cotizaciones/job-1/submit", headers=_auth_headers(), json=_valid_submit_body()
    )

    assert response.status_code == 200
    assert seen["expected_status"] == "failed"
    assert seen["updates"]["attempt_token"] is None
    assert seen["updates"]["lease_expires_at"] is None
    assert wakes == [True]
    assert response.json()["job"]["attempt_token"] is None
    assert response.json()["job"]["lease_expires_at"] is None


def test_submit_accepts_sunon_web_image_provider(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "official_2026_gdl",
        },
    )

    def fake_update(job_id, updates, *, expected_status=None):
        assert expected_status == "draft"
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
            "image_provider": "sunon_web",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["job"]["metadata"]["image_provider"] == "sunon_web"
    assert resp.json()["job"]["metadata"]["estimated_duration_seconds"] == 180


def test_submit_accepts_sunon_catalog_image_provider(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "draft",
            "metadata": {"original_filename": "quotation.xlsx"},
            "template": "official_2026_gdl",
        },
    )

    def fake_update(job_id, updates, *, expected_status=None):
        assert expected_status == "draft"
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
            "image_provider": "catalogo_sunon",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["job"]["metadata"]["image_provider"] == "sunon_catalog"
    assert resp.json()["job"]["metadata"]["estimated_duration_seconds"] == 180


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
            "template": "official_2026_gdl",
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
            "template": "official_2026_gdl",
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

    def fake_update(job_id, updates, *, expected_status=None):
        assert expected_status == "failed"
        return {"id": job_id, **updates}

    monkeypatch.setattr(index, "db_update_quote_job", fake_update)

    resp = _client().post("/cotizaciones/job-1/retry", headers=_auth_headers())

    assert resp.status_code == 200
    assert resp.json()["job"]["status"] == "queued"
    assert resp.json()["job"]["error_message"] is None
    assert resp.json()["job"]["attempt_token"] is None
    assert resp.json()["job"]["lease_expires_at"] is None


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


def test_retry_compare_and_set_rejects_status_race_without_waking(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id, "usuario_id": 7, "status": "failed",
            "input_path": "users/7/jobs/job-1/input.xlsx", "error_message": "boom",
        },
    )
    seen = {}

    def raced_update(job_id, updates, *, expected_status=None):
        seen.update(job_id=job_id, updates=updates, expected_status=expected_status)
        return {}

    wakes = []
    monkeypatch.setattr(index, "db_update_quote_job", raced_update)
    monkeypatch.setattr(index, "_wake_worker", lambda: wakes.append(True))

    resp = _client().post("/cotizaciones/job-1/retry", headers=_auth_headers())

    assert resp.status_code == 409
    assert seen["expected_status"] == "failed"
    assert seen["updates"]["attempt_token"] is None
    assert seen["updates"]["lease_expires_at"] is None
    assert wakes == []


def test_delete_quote_releases_tarkett_reservations(monkeypatch):
    _mock_user(monkeypatch)
    calls = []
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "completed",
            "input_path": None,
            "output_path": "users/7/jobs/job-1/output.xlsx",
        },
    )
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: calls.append(("storage", job["id"])))
    monkeypatch.setattr(index, "db_release_tarkett_reservations", lambda job_id: calls.append(("release", job_id)))
    monkeypatch.setattr(index, "db_release_offiho_reservations", lambda job_id: calls.append(("offiho-release", job_id)))
    monkeypatch.setattr(index, "db_release_catalog_reservations", lambda job_id: calls.append(("generic-release", job_id)))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("delete", job_id)))

    resp = _client().delete("/cotizaciones/job-1", headers=_auth_headers())

    assert resp.status_code == 200
    assert calls == [
        ("release", "job-1"),
        ("offiho-release", "job-1"),
        ("generic-release", "job-1"),
        ("delete", "job-1"),
        ("storage", "job-1"),
    ]


@pytest.mark.parametrize("consumer_status", ("queued", "failed"))
def test_delete_import_consumer_restores_source_before_delete_and_source_can_retry(
    monkeypatch, tmp_path, consumer_status
):
    state, import_job, _manifest, _source, _objects, item = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    created = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body([item])
    )
    assert created.status_code == 200, created.json()
    consumer_id = created.json()["job"]["id"]
    consumer = next(job for job in state["jobs"] if job["id"] == consumer_id)
    consumer["status"] = consumer_status
    assert import_job["metadata"]["import_consumed_by_job_id"] == consumer_id

    deleted = _client().delete(f"/cotizaciones/{consumer_id}", headers=_auth_headers())

    assert deleted.status_code == 200, deleted.json()
    assert consumer_id in state["deleted_jobs"]
    assert import_job["status"] == "draft"
    assert "import_consumed_by_job_id" not in import_job["metadata"]

    retried = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body([item])
    )
    assert retried.status_code == 200, retried.json()
    assert retried.json()["job"]["id"] != consumer_id


@pytest.mark.parametrize("restore_failure", ("false", "raise"))
def test_delete_import_consumer_is_blocked_when_source_cannot_be_restored(
    monkeypatch, tmp_path, restore_failure
):
    state, import_job, _manifest, _source, _objects, item = (
        _imported_mixed_quote_case(monkeypatch, tmp_path)
    )
    created = _client().post(
        "/catalogs/mixed-quote", headers=_auth_headers(), json=_valid_mixed_body([item])
    )
    assert created.status_code == 200, created.json()
    consumer_id = created.json()["job"]["id"]
    consumer = next(job for job in state["jobs"] if job["id"] == consumer_id)
    consumer["status"] = "failed"
    if restore_failure == "false":
        monkeypatch.setattr(index, "_restore_consumed_import_draft", lambda *_args: False)
    else:
        monkeypatch.setattr(
            index,
            "_restore_consumed_import_draft",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("restore failed")),
        )

    deleted = _client().delete(f"/cotizaciones/{consumer_id}", headers=_auth_headers())

    assert deleted.status_code in {409, 503}
    assert consumer_id not in state["deleted_jobs"]
    assert consumer_id not in state["released"]
    assert import_job["status"] == "failed"
    assert import_job["metadata"]["import_consumed_by_job_id"] == consumer_id


def test_delete_completed_import_consumer_detaches_cleaned_source_without_restoring_it(
    monkeypatch,
):
    _mock_user(monkeypatch)
    source_id = JOB_A_UUID
    consumer_id = JOB_B_UUID
    source = {
        "id": source_id,
        "usuario_id": 7,
        "status": "failed",
        "input_path": None,
        "metadata": {
            "import_consumed_by_job_id": consumer_id,
            "import_consumed_at": "2026-07-21T12:00:00+00:00",
            "import_consumed_cleanup_at": "2026-07-21T12:01:00+00:00",
        },
    }
    consumer = {
        "id": consumer_id,
        "usuario_id": 7,
        "status": "completed",
        "input_path": None,
        "output_path": f"users/7/jobs/{consumer_id}/output.xlsx",
        "metadata": {"import_source": {"import_id": source_id}},
    }
    jobs = {source_id: source, consumer_id: consumer}
    calls = []

    def update_job(job_id, updates, *, expected_status=None):
        job = jobs.get(job_id)
        if not job or (expected_status is not None and job["status"] != expected_status):
            return {}
        job.update(deepcopy(updates))
        return job

    monkeypatch.setattr(index, "db_get_quote_job", jobs.get)
    monkeypatch.setattr(index, "db_update_quote_job", update_job)
    monkeypatch.setattr(index, "_release_quote_reservations", lambda job: calls.append("release"))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append("delete"))
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: calls.append("storage"))

    deleted = _client().delete(f"/cotizaciones/{consumer_id}", headers=_auth_headers())

    assert deleted.status_code == 200, deleted.json()
    assert source["status"] == "failed"
    assert source["input_path"] is None
    assert "import_consumed_by_job_id" not in source["metadata"]
    assert calls == ["release", "delete", "storage"]


def test_delete_completed_import_consumer_waits_for_source_cleanup_marker(monkeypatch):
    _mock_user(monkeypatch)
    source_id = JOB_A_UUID
    consumer_id = JOB_B_UUID
    source = {
        "id": source_id,
        "usuario_id": 7,
        "status": "failed",
        "input_path": f"users/7/jobs/{source_id}/input.xlsx",
        "metadata": {
            "import_consumed_by_job_id": consumer_id,
            "import_consumed_at": "2026-07-21T12:00:00+00:00",
        },
    }
    consumer = {
        "id": consumer_id,
        "usuario_id": 7,
        "status": "completed",
        "metadata": {"import_source": {"import_id": source_id}},
    }
    calls = []
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: source if job_id == source_id else consumer,
    )
    monkeypatch.setattr(index, "db_update_quote_job", lambda *_args, **_kwargs: calls.append("update"))
    monkeypatch.setattr(index, "_release_quote_reservations", lambda job: calls.append("release"))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append("delete"))
    monkeypatch.setattr(index, "_delete_quote_storage", lambda job: calls.append("storage"))

    deleted = _client().delete(f"/cotizaciones/{consumer_id}", headers=_auth_headers())

    assert deleted.status_code == 409
    assert source["metadata"]["import_consumed_by_job_id"] == consumer_id
    assert calls == []


def test_download_returns_signed_url(monkeypatch):
    _mock_user(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        index,
        "db_get_quote_job",
        lambda job_id: {
            "id": job_id,
            "usuario_id": 7,
            "status": "completed",
            "output_path": "users/7/jobs/job-1/output.xlsx",
            "metadata": {"proyecto": "IZA Reforma", "cotizacion": "300-00010"},
        },
    )

    def fake_signed_download(path, filename=None):
        captured["path"] = path
        captured["filename"] = filename
        return f"https://example.test/{path}?X-Amz-Signature=signed"

    monkeypatch.setattr(index, "_create_signed_download", fake_signed_download)

    resp = _client().get("/cotizaciones/job-1/download", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["download_url"].endswith("X-Amz-Signature=signed")
    assert resp.json()["filename"] == "Cotizacion_IZA_Reforma_300-00010.xlsx"
    assert captured == {
        "path": "users/7/jobs/job-1/output.xlsx",
        "filename": "Cotizacion_IZA_Reforma_300-00010.xlsx",
    }


def test_r2_download_filename_is_signed_in_presigned_params(monkeypatch):
    captured = {}

    class FakeR2Client:
        def generate_presigned_url(self, operation, Params, ExpiresIn):
            captured["operation"] = operation
            captured["params"] = Params
            captured["expires_in"] = ExpiresIn
            return "https://r2.example/output.xlsx?X-Amz-Signature=signed"

    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "QUOTE_STORAGE_PROVIDER", "r2")
    monkeypatch.setattr(index, "R2_BUCKET", "quote-files")
    monkeypatch.setattr(index, "_r2_client", lambda: FakeR2Client())

    signed = index._create_signed_download("users/7/jobs/job-1/output.xlsx", filename="Cotizacion_IZA_300-00010.xlsx")

    assert signed.endswith("X-Amz-Signature=signed")
    assert captured == {
        "operation": "get_object",
        "params": {
            "Bucket": "quote-files",
            "Key": "users/7/jobs/job-1/output.xlsx",
            "ResponseContentDisposition": 'attachment; filename="Cotizacion_IZA_300-00010.xlsx"',
        },
        "expires_in": index.SIGNED_DOWNLOAD_TTL_SECONDS,
    }


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
