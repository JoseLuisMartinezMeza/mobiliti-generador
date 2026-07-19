import os
import sys
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vercel_deploy", "api"))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import index
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


def _client():
    return TestClient(index.app)


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
        json={"filename": "quotation.xlsx", "size": 1024, "template": "Template.xlsx"},
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
        json={"filename": "quotation.xlsx", "size": 1024, "template": "Template.xlsx"},
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
        json={"filename": "supplier-quotation.pdf", "size": 2048, "template": "Template.xlsx"},
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


def test_offiho_catalog_returns_1207_items_with_catalog_prices_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "db_list_offiho_reservations", lambda status="active": [])

    resp = _client().get("/offiho/catalog", headers=_auth_headers())

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1207
    assert len(payload["items"]) == 1207
    assert payload["source_row_count"] == 1287
    assert payload["duplicate_row_count"] == 80
    assert payload["unique_item_count"] == 1207
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
    assert calls == ["upload", "job", "reserve", "release", "delete", "storage-delete"]


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
    assert calls == prefix + ["release", "delete", "storage-delete"]


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
        "unverified",
    ],
)
def test_supplier_quote_rejects_invalid_or_unverified_cart_before_upload(monkeypatch, case):
    catalog = _mock_supplier_catalog(code_status="needs_review" if case == "unverified" else "verified")
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
        ("job", job_id),
        ("storage", f"users/7/jobs/{job_id}/input.json"),
    ]


def test_generic_catalog_backend_never_falls_back_to_anon_key(monkeypatch):
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "DATABASE_URL", None)
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", None)

    with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_KEY"):
        index.db_list_exchange_rates()


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
        lambda supplier: {"id": "snapshot-1", "supplier": supplier, "payload": payload},
    )
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
        lambda supplier: {"id": "snapshot-legacy", "supplier": supplier, "payload": payload},
    )
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
        (
            "mark_failed",
            "job-1",
            {"status": "failed", "error_message": "cleanup_pending:delete_job"},
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


def test_generic_internal_catalog_put_route_is_absent_and_tarkett_remains():
    put_paths = [
        route.path
        for route in index.app.routes
        if "PUT" in getattr(route, "methods", set())
        and route.path.startswith("/internal/catalogs/")
    ]

    assert "/internal/catalogs/tarkett" in put_paths
    assert all("{" not in path for path in put_paths)


def test_deployable_api_copies_have_identical_sha256():
    paths = [
        Path("mobiliti_saas/web/api/index.py"),
        Path("mobiliti_saas/api/index.py"),
        Path("vercel_deploy/api/index.py"),
    ]
    hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    assert len(hashes) == 1


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

    rest_calls = []
    monkeypatch.setattr(index, "_use_postgres", lambda: False)
    monkeypatch.setattr(index, "_supabase_req", lambda method, path, params=None, json_data=None: rest_calls.append((method, path, params, json_data)) or [{"status": "queued"}])
    index.db_queue_mixed_quote_job(JOB_MIXED_UUID, metadata)
    assert rest_calls[0][0:3] == ("PATCH", "/saas_quote_jobs", {"id": f"eq.{JOB_MIXED_UUID}", "status": "eq.draft"})


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
