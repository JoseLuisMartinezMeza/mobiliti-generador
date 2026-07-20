from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import uuid

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image
import pytest

from mobiliti_saas.quote_engine import catalog_cart
from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem


ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "mobiliti_saas" / "worker"
WORKER_TEMPLATE = (
    WORKER_DIR / "templates" / "Formato Cotizacion 2026 GDL.xlsx"
)
MONEY = Decimal("0.01")
CATALOGS = (
    "tarkett",
    "offiho",
    "cr-global",
    "sonara",
    "sunon",
    "alma",
    "lumbro",
)
CATALOG_LABELS = {
    "tarkett": "Tarkett",
    "offiho": "Offiho",
    "cr-global": "CR Global",
    "sonara": "Sonara",
    "sunon": "Sunon",
    "alma": "ALMA",
    "lumbro": "Lumbro",
}
SOURCE_HASHES = {
    catalog: hashlib.sha256(f"mixed-e2e:{catalog}".encode("utf-8")).hexdigest()
    for catalog in CATALOGS
}


@pytest.fixture
def isolated_quote_runtime(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")
    monkeypatch.setenv(
        "CATALOG_ENABLED_SUPPLIERS", "cr-global,sonara,sunon,alma,lumbro"
    )
    monkeypatch.syspath_prepend(str(WORKER_DIR))
    modules_before = set(sys.modules)
    suffix = uuid.uuid4().hex

    def load_module(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    api_index = load_module(
        f"mixed_quote_e2e_api_{suffix}",
        ROOT / "mobiliti_saas" / "api" / "index.py",
    )
    quote_worker = load_module(
        f"mixed_quote_e2e_worker_{suffix}",
        WORKER_DIR / "quote_worker.py",
    )
    assert api_index._enabled_catalog_suppliers() == CATALOGS[2:]
    try:
        yield api_index, quote_worker
    finally:
        for name in set(sys.modules) - modules_before:
            module_path = getattr(sys.modules.get(name), "__file__", None)
            if name.endswith(suffix) or (
                module_path
                and Path(module_path).resolve().is_relative_to(WORKER_DIR.resolve())
            ):
                sys.modules.pop(name, None)


def _supplier_item(
    supplier: str,
    *,
    internal_id: str,
    name: str,
    price: str,
    currency: str,
    sku: str | None = None,
    code_status: str = "verified",
    image_url: str = "",
    image_kind: str = "placeholder",
    warnings: list[str] | None = None,
    base_price_options: list[dict] | None = None,
    add_on_options: list[dict] | None = None,
) -> dict:
    return {
        "internal_id": internal_id,
        "supplier": supplier,
        "product_key": internal_id,
        "sku": (sku or internal_id.upper()) if code_status == "verified" else "",
        "code_status": code_status,
        "brand": CATALOG_LABELS[supplier],
        "collection": "E2E",
        "name": name,
        "description": f"Descripcion {name}",
        "unit": "PZA",
        "availability_type": "stocked",
        "stock": "20.000000",
        "lead_time": "Entrega inmediata",
        "base_price_options": base_price_options or [],
        "add_on_options": add_on_options or [],
        "base_currency": currency,
        "price_net": price,
        "tax_rate": "0.160000",
        "attributes": {},
        "image_url": image_url,
        "image_kind": image_kind,
        "product_url": "",
        "warnings": warnings or [],
        "source_reference": f"{supplier}:e2e:{internal_id}",
    }


def authoritative_catalogs() -> dict[str, dict]:
    tarkett = TarkettCatalogItem(
        code="TARK-E2E",
        name="Estacion Lido 8PAX Tarkett",
        unit="PZA",
        available_quantity=Decimal("20"),
        image_url="https://media.tarkett-image.com/e2e-tarkett.png",
        unit_price=Decimal("1000"),
        price_source="catalog",
    )
    offiho = OffihoCatalogItem(
        inventory_key="OFF-E2E NEGRO",
        code="OFF-E2E",
        name="Silla Offiho",
        variant="Negro",
        unit="PZA",
        pieces_per_box=Decimal("1"),
        available_quantity=Decimal("20"),
        unit_price=Decimal("500"),
        image_url="https://offiho.com.mx/e2e-offiho.png",
        price_source="catalog",
    )
    alma_options = [
        {
            "id": "alma-base",
            "name": "Base operativa",
            "price_net": "100.000000",
            "available": True,
        }
    ]
    alma_add_ons = [
        {
            "id": "alma-electrificacion-a",
            "name": "Electrificacion A",
            "family": "electrificacion",
            "price_net": "10.000000",
            "available": True,
            "compatible_base_option_ids": ["alma-base"],
        },
        {
            "id": "alma-pasacables-b",
            "name": "Pasacables B",
            "family": "pasacables",
            "price_net": "20.000000",
            "available": True,
            "compatible_base_option_ids": ["alma-base"],
        },
    ]
    supplier_items = {
        "cr-global": _supplier_item(
            "cr-global", internal_id="cr:e2e", name="Silla CR Global",
            price="300.000000", currency="MXN", sku="CR-E2E",
        ),
        "sonara": _supplier_item(
            "sonara", internal_id="sonara:e2e-review",
            name="Panel Sonara por verificar", price="400.000000",
            currency="MXN", code_status="needs_review",
            warnings=["Revision documental local"],
        ),
        "sunon": _supplier_item(
            "sunon", internal_id="sunon:e2e", name="Silla Sunon",
            price="50.000000", currency="USD", sku="SUN-E2E",
        ),
        "alma": _supplier_item(
            "alma", internal_id="alma:e2e-configurable", name="Mesa ALMA",
            price="0.000000", currency="USD", sku="ALMA-E2E",
            image_url="https://alma.example.test/e2e-alma.png",
            image_kind="official", base_price_options=alma_options,
            add_on_options=alma_add_ons,
        ),
        "lumbro": _supplier_item(
            "lumbro", internal_id="lumbro:e2e-manual",
            name="LIDO.OP-INT manual", price="250.000000",
            currency="MXN", sku="LUMBRO-MANUAL",
        ),
    }
    catalogs = {
        "tarkett": {
            "source_hash": SOURCE_HASHES["tarkett"],
            "items": [tarkett],
            "by_code": {tarkett.code: tarkett},
        },
        "offiho": {
            "source_hash": SOURCE_HASHES["offiho"],
            "items": [offiho],
            "by_inventory_key": {offiho.inventory_key: offiho},
        },
    }
    for supplier, item in supplier_items.items():
        catalogs[supplier] = {
            "supplier": supplier,
            "source_hash": SOURCE_HASHES[supplier],
            "generated_at": "2026-07-19T00:00:00+00:00",
            "items": [item],
        }
    return catalogs


def browser_rows_for_all_catalogs_and_two_alma_configs() -> list[dict]:
    return [
        {"catalog": "tarkett", "code": "TARK-E2E", "quantity": "1"},
        {
            "catalog": "offiho",
            "inventory_key": "OFF-E2E NEGRO",
            "quantity": "2",
        },
        {"catalog": "cr-global", "internal_id": "cr:e2e", "quantity": "1"},
        {
            "catalog": "sonara",
            "internal_id": "sonara:e2e-review",
            "quantity": "1",
        },
        {"catalog": "sunon", "internal_id": "sunon:e2e", "quantity": "1"},
        {
            "catalog": "alma",
            "internal_id": "alma:e2e-configurable",
            "quantity": "1",
            "base_option_id": "alma-base",
            "add_on_option_ids": ["alma-electrificacion-a"],
        },
        {
            "catalog": "alma",
            "internal_id": "alma:e2e-configurable",
            "quantity": "1",
            "base_option_id": "alma-base",
            "add_on_option_ids": ["alma-pasacables-b"],
        },
        {
            "catalog": "lumbro",
            "internal_id": "lumbro:e2e-manual",
            "quantity": "1",
        },
    ]


def rate_rows() -> list[dict]:
    today = date.today().isoformat()
    return [
        {
            "currency": "USD",
            "effective_date": today,
            "mxn_per_unit": "18.500000",
            "retrieved_at": f"{today}T12:00:00+00:00",
        },
        {
            "currency": "EUR",
            "effective_date": today,
            "mxn_per_unit": "20.500000",
            "retrieved_at": f"{today}T12:00:00+00:00",
        },
    ]


def install_api_boundary(monkeypatch, api_index, catalogs: dict) -> dict:
    state = {
        "events": [],
        "jobs": [],
        "uploads": [],
        "released": [],
        "deleted_jobs": [],
        "deleted_inputs": [],
    }
    monkeypatch.setattr(
        api_index,
        "db_get_usuario_by_id",
        lambda user_id: {
            "id": user_id,
            "email": "cliente@example.test",
            "nombre": "Cliente",
            "empresa": "Mobiliti",
            "es_admin": False,
            "activo": True,
        },
    )
    monkeypatch.setattr(
        api_index,
        "db_get_suscripcion_by_usuario",
        lambda user_id: {
            "id": 1,
            "usuario_id": user_id,
            "estado": "activa",
            "plan": "mensual",
            "fecha_fin": "2099-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(api_index, "_enforce_active_quote_limit", lambda _user: None)
    monkeypatch.setattr(api_index, "_next_quote_number_for_user", lambda _user: None)
    monkeypatch.setattr(api_index, "_storage_provider_name", lambda: "supabase")
    monkeypatch.setattr(
        api_index, "_load_tarkett_catalog_cached", lambda: catalogs["tarkett"]
    )
    monkeypatch.setattr(
        api_index, "_load_offiho_catalog_cached", lambda: catalogs["offiho"]
    )
    monkeypatch.setattr(
        api_index,
        "_load_supplier_catalog_cached",
        lambda supplier: catalogs[supplier],
    )
    monkeypatch.setattr(api_index, "db_list_exchange_rates", rate_rows)

    def create_job(user_id, template, metadata, input_path, job_id=None):
        state["events"].append("create_job")
        job = {
            "id": job_id,
            "usuario_id": user_id,
            "status": "draft",
            "template": template,
            "metadata": deepcopy(metadata),
            "input_path": input_path,
        }
        state["jobs"].append(job)
        return deepcopy(job)

    def reserve_mixed(_user_id, _job_id, groups):
        state["events"].append("reserve_mixed")
        return [
            {
                "catalog": group["catalog"],
                "identity": item["identity"],
                "reserved_before": "0.000000",
                "available_before": item["stock"],
                "insufficient": False,
                "reserved_by_others": False,
            }
            for group in groups
            for item in group["items"]
        ]

    def upload(path, content, content_type="application/octet-stream"):
        state["events"].append("upload")
        state["uploads"].append((path, bytes(content), content_type))

    def queue(job_id, metadata):
        state["events"].append("queue")
        job = next(job for job in state["jobs"] if job["id"] == job_id)
        job.update(status="queued", metadata=deepcopy(metadata))
        return deepcopy(job)

    monkeypatch.setattr(api_index, "db_create_quote_job", create_job)
    monkeypatch.setattr(api_index, "db_reserve_mixed_cart", reserve_mixed)
    monkeypatch.setattr(api_index, "_storage_upload_bytes", upload)
    monkeypatch.setattr(api_index, "db_queue_mixed_quote_job", queue)
    monkeypatch.setattr(
        api_index,
        "db_release_mixed_cart",
        lambda job_id: state["released"].append(job_id),
    )
    monkeypatch.setattr(
        api_index,
        "db_delete_quote_job",
        lambda job_id: state["deleted_jobs"].append(job_id),
    )
    monkeypatch.setattr(
        api_index,
        "_delete_storage_paths",
        lambda paths: state["deleted_inputs"].extend(paths),
    )
    monkeypatch.setattr(api_index, "_wake_worker", lambda: state["events"].append("wake"))
    return state


class EndToEndWorkerClient:
    EXPECTED_EVENTS = (
        "claim",
        "progress:45",
        "download",
        "progress:55",
        "converter",
        "generator",
        "progress:90",
        "upload",
        "delete",
        "completed",
    )

    def __init__(self, job: dict, objects: dict[str, bytes]):
        self.job = deepcopy(job)
        self.objects = dict(objects)
        self.events: list[str] = []
        self.downloads: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.deleted_inputs: list[str] = []
        self.completed_updates: list[dict] = []
        self.failed_updates: list[dict] = []

    def record_event(self, event: str) -> None:
        assert len(self.events) < len(self.EXPECTED_EVENTS)
        assert event == self.EXPECTED_EVENTS[len(self.events)]
        self.events.append(event)

    def rest(self, method, path, params=None, data=None):
        assert method == "PATCH"
        assert params is None
        data = deepcopy(data or {})
        job_path = f"/saas_quote_jobs?id=eq.{self.job['id']}"
        if path == job_path + "&status=eq.queued":
            assert set(data) == {"status", "updated_at"}
            assert data["status"] == "processing"
            assert isinstance(data["updated_at"], str) and data["updated_at"]
            self.record_event("claim")
            claimed = {**self.job, **data}
            self.job = claimed
            return [deepcopy(claimed)]
        if path == job_path and set(data) == {"metadata", "updated_at"}:
            metadata = data["metadata"]
            assert isinstance(metadata, dict)
            progress = metadata.get("progress_percent")
            assert progress in {45, 55, 90}
            self.record_event(f"progress:{progress}")
            self.job = {**self.job, **data}
            return [deepcopy(self.job)]
        if path == job_path and data.get("status") == "completed":
            assert set(data) == {
                "status", "input_path", "output_path", "metadata",
                "error_message", "updated_at", "completed_at",
            }
            assert data["input_path"] is None
            assert data["output_path"] == (
                f"users/{self.job['usuario_id']}/jobs/{self.job['id']}/output.xlsx"
            )
            assert data["metadata"]["progress_percent"] == 100
            assert data["error_message"] is None
            self.record_event("completed")
            completed = {**self.job, **data}
            self.job = completed
            self.completed_updates.append(deepcopy(completed))
            return [deepcopy(completed)]
        if path == job_path and data.get("status") == "failed":
            assert set(data) == {
                "status", "metadata", "error_message", "updated_at"
            }
            failed = {**self.job, **data}
            self.job = failed
            self.failed_updates.append(deepcopy(failed))
            return [deepcopy(failed)]
        raise AssertionError(f"Contrato REST worker inesperado: {method} {path} {data}")

    def storage_download(self, object_path, destination):
        self.record_event("download")
        assert object_path == self.job["input_path"]
        assert object_path in self.objects
        self.downloads.append(object_path)
        Path(destination).write_bytes(self.objects[object_path])

    def storage_upload(self, object_path, source):
        self.record_event("upload")
        assert object_path == (
            f"users/{self.job['usuario_id']}/jobs/{self.job['id']}/output.xlsx"
        )
        assert Path(source).is_file()
        self.uploads.append((object_path, Path(source).read_bytes()))

    def storage_delete(self, object_path):
        self.record_event("delete")
        assert object_path == self.job["input_path"]
        assert object_path in self.objects
        self.deleted_inputs.append(object_path)
        self.objects.pop(object_path)


def auth_headers(api_index) -> dict[str, str]:
    token = api_index.create_access_token(
        {"sub": "7", "email": "cliente@example.test"}
    )
    return {"Authorization": f"Bearer {token}"}


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def expected_mixed_totals(
    payload: dict,
    accessories_by_parent: dict[str, list[tuple[Decimal, Decimal]]],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    net = Decimal("0")
    auto_rate = Decimal(payload["auto_electrification_rate"]["exchange_rate"])
    for group in payload["groups"]:
        for item in group["items"]:
            price = Decimal(item["unit_price"])
            discount = Decimal(item["discount_percent"]) / Decimal("100")
            quantity = Decimal(item["quantity"])
            accessory_total = sum(
                (
                    money(unit_price_mxn * auto_rate) * accessory_quantity
                    for unit_price_mxn, accessory_quantity
                    in accessories_by_parent.get(item["canonical_key"], [])
                ),
                Decimal("0"),
            )
            combined_unit = money(((price * quantity) + accessory_total) / quantity)
            discount_amount = money(combined_unit * discount)
            net_unit = money(combined_unit - discount_amount)
            net += money(net_unit * quantity)
    net = money(net)
    freight = money(net * Decimal("0.12"))
    before_tax = money(net + freight)
    tax = money(before_tax * Decimal("0.16"))
    return net, freight, before_tax, tax, money(before_tax + tax)


def _row_for_formula(ws, column: int, formula: str) -> int:
    return next(
        row
        for row in range(1, ws.max_row + 1)
        if ws.cell(row, column).value == formula
    )


@pytest.mark.parametrize(
    ("quote_currency", "automatic_rate"),
    (("MXN", "1.000000"), ("USD", "0.054054"), ("EUR", "0.048780")),
)
def test_mixed_api_worker_produces_one_auditable_workbook(
    quote_currency,
    automatic_rate,
    isolated_quote_runtime,
    monkeypatch,
    tmp_path,
):
    assert WORKER_TEMPLATE.is_file()
    api_index, quote_worker = isolated_quote_runtime
    api_state = install_api_boundary(monkeypatch, api_index, authoritative_catalogs())

    local_images = {
        ("tarkett_cart", "https://media.tarkett-image.com/e2e-tarkett.png"):
            _make_png(tmp_path / "tarkett.png", (20, 70, 120)),
        ("offiho_cart", "https://offiho.com.mx/e2e-offiho.png"):
            _make_png(tmp_path / "offiho.png", (120, 70, 20)),
        ("supplier_cart", "https://alma.example.test/e2e-alma.png"):
            _make_png(tmp_path / "alma.png", (40, 120, 70)),
    }
    image_calls = []

    def local_catalog_image(url, image_dir, code, source_type, destination_key=None):
        image_calls.append((source_type, url, destination_key))
        return local_images[(source_type, url)]

    monkeypatch.setattr(catalog_cart, "_download_catalog_image", local_catalog_image)

    body = {
        "items": browser_rows_for_all_catalogs_and_two_alma_configs(),
        "quote_currency": quote_currency,
        "descuento": "40",
        "proyecto": "Proyecto mixto",
        "cliente": "Cliente prueba",
        "correo": "cliente@example.test",
        "telefono": "3330000000",
        "direccion": "Guadalajara",
        "razon_social": "Cliente SA de CV",
        "image_provider": "pillow",
        "template": WORKER_TEMPLATE.name,
    }
    with TestClient(api_index.app) as api_client:
        response = api_client.post(
            "/catalogs/mixed-quote",
            headers=auth_headers(api_index),
            json=body,
        )
    assert response.status_code == 200, response.json()
    queued_job = response.json()["job"]
    assert api_state["events"] == [
        "create_job", "reserve_mixed", "upload", "queue", "wake"
    ]
    assert len(api_state["jobs"]) == 1
    assert api_state["released"] == []
    assert api_state["deleted_jobs"] == []
    assert api_state["deleted_inputs"] == []
    assert len(api_state["uploads"]) == 1
    input_path, input_bytes, content_type = api_state["uploads"][0]
    assert input_path == queued_job["input_path"]
    assert content_type == "application/json"
    payload = json.loads(input_bytes)
    frozen_payload = deepcopy(payload)
    assert payload["source_type"] == "mixed_catalog_cart"
    assert payload["item_count"] == 8
    assert [group["catalog"] for group in payload["groups"]] == list(CATALOGS)
    assert {
        group["catalog"]: group["catalog_source_hash"] for group in payload["groups"]
    } == SOURCE_HASHES
    alma_lines = next(group for group in payload["groups"] if group["catalog"] == "alma")["items"]
    assert len({line["canonical_key"] for line in alma_lines}) == 2
    assert {line["configuration"] for line in alma_lines} == {
        "Base operativa; Electrificacion A",
        "Base operativa; Pasacables B",
    }
    assert payload["auto_electrification_rate"]["exchange_rate"] == automatic_rate
    for line in (item for group in payload["groups"] for item in group["items"]):
        assert line["reservation"] is not None
        assert line["reserved_quantity"] == "0.000000"
        assert line["available_after_reservations"] == "20.000000"
        assert line["reserved_by_others"] is False

    worker_client = EndToEndWorkerClient(
        queued_job,
        {input_path: input_bytes},
    )
    real_run_generator = quote_worker._run_generator
    generator_calls = []

    def counted_run_generator(job, generator_input, local_output):
        assert generator_input.name == "quotation_from_mixed_catalog.xlsx"
        assert generator_input.is_file()
        converted = load_workbook(generator_input, data_only=False, read_only=True)
        try:
            assert converted.sheetnames == ["Quotation"]
        finally:
            converted.close()
        worker_client.record_event("converter")
        worker_client.record_event("generator")
        generator_calls.append(generator_input.name)
        return real_run_generator(job, generator_input, local_output)

    monkeypatch.setattr(quote_worker, "_run_generator", counted_run_generator)
    monkeypatch.setattr(quote_worker, "_template_path", lambda: str(WORKER_TEMPLATE))
    monkeypatch.setattr(quote_worker, "QUOTE_ENGINE", "python")

    completed = quote_worker.process_job(worker_client, queued_job)
    assert completed
    assert worker_client.events == [
        "claim", "progress:45", "download", "progress:55", "converter",
        "generator", "progress:90", "upload", "delete", "completed",
    ]
    assert generator_calls == ["quotation_from_mixed_catalog.xlsx"]
    assert worker_client.downloads == [input_path]
    assert len(worker_client.uploads) == 1
    uploaded_path, output_bytes = worker_client.uploads[0]
    assert uploaded_path == f"users/7/jobs/{queued_job['id']}/output.xlsx"
    assert output_bytes.startswith(b"PK")
    assert worker_client.deleted_inputs == [input_path]
    assert input_path not in worker_client.objects
    assert len(worker_client.completed_updates) == 1
    assert worker_client.failed_updates == []
    completed_job = worker_client.completed_updates[0]
    completed_metadata = completed_job["metadata"]
    assert completed_metadata["mixed_catalog_converted"] is True
    assert completed_metadata["catalog_price_mode"] == "mixed_catalog_converted"
    assert completed_metadata["base_currency"] == quote_currency
    assert completed_metadata["quote_currency"] == quote_currency
    assert completed_metadata["exchange_rate"] == "1.000000"
    assert completed_metadata["rate_summary"] == frozen_payload["rate_summary"]
    assert completed_metadata["auto_electrification_rate"] == frozen_payload[
        "auto_electrification_rate"
    ]
    assert completed_metadata["catalog_source_hashes"] == SOURCE_HASHES
    assert completed_metadata["descuento"] == 0
    assert completed_job["input_path"] is None
    assert completed_job["output_path"] == uploaded_path
    assert payload == frozen_payload

    assert [(source, url) for source, url, _key in image_calls] == list(
        local_images
    )
    image_keys = [key for _source, _url, key in image_calls]
    assert all(image_keys)
    assert len(set(image_keys)) == 3

    output_xlsx = tmp_path / f"cotizacion_mixta_final_{quote_currency}.xlsx"
    output_xlsx.write_bytes(output_bytes)
    assert output_xlsx.is_file()
    wb = load_workbook(output_xlsx, data_only=False)
    try:
        _assert_final_workbook(
            wb,
            payload,
            quote_currency=quote_currency,
            automatic_rate=automatic_rate,
        )
    finally:
        wb.close()


def _make_png(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (96, 72), color).save(path, format="PNG")
    return path


def _assert_final_workbook(
    wb,
    payload: dict,
    *,
    quote_currency: str,
    automatic_rate: str,
) -> None:
    assert wb.sheetnames.count("Cotizacion") == 1
    assert wb.sheetnames.count("Mobiliti") == 1
    assert wb.sheetnames.count("Quotation") == 1
    quotation = wb["Quotation"]
    mobiliti = wb["Mobiliti"]
    cot = wb["Cotizacion"]
    assert [
        quotation.cell(row, 1).value
        for row in range(8, quotation.max_row + 1)
        if isinstance(quotation.cell(row, 1).value, str)
    ] == [
        "- Tarkett", "- Offiho", "- CR Global", "- Sonara", "- Sunon",
        "- ALMA", "- Lumbro",
    ]
    source_rows = [
        row
        for row in range(8, quotation.max_row + 1)
        if isinstance(quotation.cell(row, 1).value, (int, float))
    ]
    assert [quotation.cell(row, 1).value for row in source_rows] == list(range(1, 9))
    assert [quotation.cell(row, 12).value for row in source_rows] == [
        "Tarkett", "Offiho", "CR Global", "Sonara", "Sunon", "ALMA",
        "ALMA", "Lumbro",
    ]
    assert [quotation.cell(row, 13).value for row in source_rows] == [
        40, 40, 0, 0, 0, 0, 0, 0,
    ]
    assert [quotation.cell(row, 19).value for row in source_rows] == [
        True, True, False, False, False, False, False, False,
    ]
    sonara_description = str(quotation.cell(source_rows[3], 4).value)
    assert "Revision documental local" in sonara_description
    assert "Codigo por verificar" in sonara_description
    alma_a_description = str(quotation.cell(source_rows[5], 4).value)
    alma_b_description = str(quotation.cell(source_rows[6], 4).value)
    assert "electrificacion a" in alma_a_description.casefold()
    assert "pasacables b" in alma_b_description.casefold()
    assert alma_a_description != alma_b_description
    assert "LIDO.OP-INT manual" in str(quotation.cell(source_rows[7], 2).value)
    assert len(quotation._images) >= 3
    assert len(cot._images) >= 3

    row_maps = []
    for source_row in source_rows:
        mobiliti_row = _row_for_formula(mobiliti, 4, f"=Quotation!B{source_row}")
        cot_row = _row_for_formula(cot, 1, f"=Quotation!B{source_row}")
        row_maps.append((source_row, mobiliti_row, cot_row))
    assert [
        (row, cot.cell(row, 1).value)
        for row in range(1, cot.max_row + 1)
        if str(cot.cell(row, 1).value or "").startswith("=Quotation!B")
    ] == [
        (cot_row, f"=Quotation!B{source_row}")
        for source_row, _mobiliti_row, cot_row in row_maps
    ]
    assert [mobiliti.cell(row, 6).value for _source, row, _cot in row_maps] == [
        "Tarkett", "Offiho", "CR Global", "Sonara", "Sunon", "ALMA",
        "ALMA", "Lumbro",
    ]
    assert [cot.cell(row, 7).value for _source, _mob, row in row_maps] == [
        0.4, 0.4, 0, 0, 0, 0, 0, 0,
    ]
    assert mobiliti["J6"].value == f"{quote_currency}/{quote_currency}"
    assert mobiliti["K6"].value == 1
    frozen_lines = [
        item for group in payload["groups"] for item in group["items"]
    ]
    assert [quotation.cell(row, 10).value for row in source_rows] == [
        float(Decimal(item["unit_price"])) for item in frozen_lines
    ]
    for index, (_source_row, mobiliti_row, cot_row) in enumerate(row_maps):
        assert "$K$6" not in str(mobiliti.cell(mobiliti_row, 10).value)
        assert "$K$6" not in str(cot.cell(cot_row, 6).value)
        if index:
            assert cot.cell(cot_row, 6).value == f"=ROUND(Mobiliti!X{mobiliti_row},2)"
        assert cot.cell(cot_row, 8).value == f"=ROUND(F{cot_row}*G{cot_row},2)"
        assert cot.cell(cot_row, 9).value == f"=ROUND(F{cot_row}-H{cot_row},2)"
        assert cot.cell(cot_row, 10).value == f"=ROUND(E{cot_row}*I{cot_row},2)"

    automatic_rows = [
        row
        for row in range(1, mobiliti.max_row + 1)
        if mobiliti.cell(row, 4).value in {"LIDO.OP-INT", "JUMP-1.5M", "CAJA-FUS"}
    ]
    assert len(automatic_rows) == 3
    automatic_by_code = {
        mobiliti.cell(row, 4).value: row for row in automatic_rows
    }
    assert list(automatic_by_code) == ["LIDO.OP-INT", "JUMP-1.5M", "CAJA-FUS"]
    expected_template_rows = {
        "LIDO.OP-INT": 380,
        "JUMP-1.5M": 396,
        "CAJA-FUS": 406,
    }
    assert {
        code: mobiliti.cell(row, 8).value
        for code, row in automatic_by_code.items()
    } == {"LIDO.OP-INT": 8, "JUMP-1.5M": 8, "CAJA-FUS": 2}
    expected_rate_literal = str(float(automatic_rate)).rstrip("0").rstrip(".") or "0"
    for code, template_row in expected_template_rows.items():
        row = automatic_by_code[code]
        assert mobiliti.cell(row, 10).value == (
            f"=ROUND('SPEC-GUIDE-LUMBRO'!E{template_row}*"
            f"{expected_rate_literal},2)"
        )
    parent_cot_row = row_maps[0][2]
    parent_mobiliti_row = row_maps[0][1]
    parent_formula = str(cot.cell(parent_cot_row, 6).value)
    expected_terms = [
        f"Mobiliti!X{parent_mobiliti_row}*Mobiliti!H{parent_mobiliti_row}",
        *(
            f"Mobiliti!X{automatic_by_code[code]}*"
            f"Mobiliti!H{automatic_by_code[code]}"
            for code in expected_template_rows
        ),
    ]
    assert parent_formula == (
        f"=ROUND(IFERROR(({'+'.join(expected_terms)})/"
        f"Mobiliti!H{parent_mobiliti_row},0),2)"
    )
    assert "Mobiliti!Y" not in parent_formula
    assert "$K$6" not in parent_formula
    for _source, _mob, cot_row in row_maps[1:]:
        formula = str(cot.cell(cot_row, 6).value)
        assert all(
            f"Mobiliti!X{row}*Mobiliti!H{row}" not in formula
            for row in automatic_rows
        )

    total_rows = [
        row
        for row in range(1, cot.max_row + 1)
        if cot.cell(row, 4).value
        in {"SUBTOTAL:", "COSTO DE FLETE:", "IVA:", "TOTAL:"}
    ]
    assert len(total_rows) == 5
    assert [cot.cell(row, 4).value for row in total_rows] == [
        "SUBTOTAL:", "COSTO DE FLETE:", "SUBTOTAL:", "IVA:", "TOTAL:",
    ]
    first_product = row_maps[0][2]
    last_product = row_maps[-1][2]
    subtotal, freight, before_tax, tax, total = total_rows
    assert cot.cell(subtotal, 8).value == f"=ROUND(SUM(J{first_product}:J{last_product}),2)"
    assert cot.cell(freight, 8).value == f"=ROUND(H{subtotal}*12%,2)"
    assert cot.cell(before_tax, 8).value == f"=ROUND(H{subtotal}+H{freight},2)"
    assert cot.cell(tax, 8).value == f"=ROUND(H{before_tax}*16%,2)"
    assert cot.cell(total, 8).value == f"=ROUND(H{before_tax}+H{tax},2)"

    template_values = load_workbook(WORKER_TEMPLATE, data_only=True, read_only=True)
    try:
        lumbro = template_values["SPEC-GUIDE-LUMBRO"]
        unit_prices = {
            code: Decimal(str(lumbro[f"E{template_row}"].value))
            for code, template_row in expected_template_rows.items()
        }
    finally:
        template_values.close()
    tarkett_key = payload["groups"][0]["items"][0]["canonical_key"]
    accessories = {
        tarkett_key: [
            (
                unit_prices[code],
                Decimal(str(mobiliti.cell(automatic_by_code[code], 8).value)),
            )
            for code in expected_template_rows
        ]
    }
    totals = expected_mixed_totals(payload, accessories)
    assert totals == {
        "MXN": (
            Decimal("20062.12"), Decimal("2407.45"), Decimal("22469.57"),
            Decimal("3595.13"), Decimal("26064.70"),
        ),
        "USD": (
            Decimal("1084.44"), Decimal("130.13"), Decimal("1214.57"),
            Decimal("194.33"), Decimal("1408.90"),
        ),
        "EUR": (
            Decimal("978.61"), Decimal("117.43"), Decimal("1096.04"),
            Decimal("175.37"), Decimal("1271.41"),
        ),
    }[quote_currency]
    assert totals[2] == totals[0] + totals[1]
    assert totals[4] == totals[2] + totals[3]
