import os
import sys
import hashlib
from decimal import Decimal
from pathlib import Path

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
    )
    return {
        "source_hash": "catalog-hash",
        "generated_at": "2026-07-08T00:00:00+00:00",
        "items": [item],
        "by_code": {item.code: item},
    }


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
    assert payload["items"][0]["unit_price"] == 0
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


def test_offiho_catalog_returns_1206_items_with_catalog_prices_and_reservations(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(index, "db_list_offiho_reservations", lambda status="active": [])

    resp = _client().get("/offiho/catalog", headers=_auth_headers())

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1206
    assert len(payload["items"]) == 1206
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


def test_offiho_catalog_cache_reloads_when_mtime_changes(monkeypatch, tmp_path):
    catalog_path = tmp_path / "offiho_catalog.json"
    catalog_path.write_text("{}", encoding="utf-8")
    loaded = []

    def fake_load(path):
        loaded.append(Path(path).stat().st_mtime)
        return {"source_hash": f"hash-{len(loaded)}", "items": [], "by_inventory_key": {}}

    monkeypatch.setattr(index, "OFFIHO_CATALOG_PATH", str(catalog_path), raising=False)
    monkeypatch.setattr(index, "load_offiho_catalog", fake_load)
    monkeypatch.setattr(index, "_OFFIHO_CATALOG_CACHE", {"path": None, "mtime": None, "source_hash": None, "catalog": None})
    os.utime(catalog_path, (1_800_000_001, 1_800_000_001))
    first = index._load_offiho_catalog_cached()
    os.utime(catalog_path, (1_800_000_002, 1_800_000_002))
    second = index._load_offiho_catalog_cached()

    assert first["source_hash"] == "hash-1"
    assert second["source_hash"] == "hash-2"
    assert len(loaded) == 2
    assert index._OFFIHO_CATALOG_CACHE["source_hash"] == "hash-2"


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
