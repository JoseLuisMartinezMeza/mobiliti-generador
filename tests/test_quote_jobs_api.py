import os
import sys
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vercel_deploy", "api"))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import index
from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem


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
def test_catalog_quote_cleanup_continues_when_release_fails(monkeypatch, supplier):
    calls = []
    body = _install_catalog_quote_failure_mocks(monkeypatch, supplier, calls, "empty_update")

    def fail_release(job_id):
        calls.append("release")
        raise RuntimeError("cleanup release failed")

    monkeypatch.setattr(index, f"db_release_{supplier}_reservations", fail_release)

    resp = _client().post(f"/{supplier}/quote", headers=_auth_headers(), json=body)

    assert resp.status_code == 503
    assert calls == ["upload", "job", "reserve", "update", "release", "delete", "storage-delete"]
    assert "cleanup release failed" not in resp.json()["detail"]


def test_offiho_reservations_work_in_dev_mode_without_existing_data(monkeypatch):
    store = {"quote_jobs": [], "tarkett_reservations": []}
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: store)
    monkeypatch.setattr(index, "_dev_save", lambda data: None)

    created = index.db_create_offiho_reservations(7, "job-1", [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 2}])
    released = index.db_release_offiho_reservations("job-1")

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
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("delete", job_id)))

    response = _client().delete("/cotizaciones/job-1", headers=_auth_headers())
    assert response.status_code == 200
    assert calls == [("storage", "job-1"), ("tarkett", "job-1"), ("offiho", "job-1"), ("delete", "job-1")]

    calls.clear()
    monkeypatch.setattr(index, "MAX_QUOTE_HISTORY_PER_USER", 0)
    result = index._run_quote_retention(7, jobs=[{"id": "job-2", "status": "completed", "input_path": None, "output_path": None, "created_at": "2020-01-01T00:00:00+00:00"}])
    assert result["jobs_deleted"] == 1
    assert calls == [("storage", "job-2"), ("tarkett", "job-2"), ("offiho", "job-2"), ("delete", "job-2")]


def test_deployable_api_copies_have_identical_sha256():
    paths = [
        Path("mobiliti_saas/web/api/index.py"),
        Path("mobiliti_saas/api/index.py"),
        Path("vercel_deploy/api/index.py"),
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
    monkeypatch.setattr(index, "db_delete_quote_job", lambda job_id: calls.append(("delete", job_id)))

    resp = _client().delete("/cotizaciones/job-1", headers=_auth_headers())

    assert resp.status_code == 200
    assert calls == [("storage", "job-1"), ("release", "job-1"), ("offiho-release", "job-1"), ("delete", "job-1")]


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
