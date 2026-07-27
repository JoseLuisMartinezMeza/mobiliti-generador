import json
import re
import socket
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from quotation_import_fixtures import write_import_fixture


IMPORT_JOB_ID = "77777777-7777-4777-8777-777777777777"
PROJECT_ID = "99999999-9999-4999-8999-999999999999"
IMPORT_PREVIEW_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

TARKETT_CATALOG = {
    "source_hash": "tarkett-e2e",
    "generated_at": "2026-07-19T20:00:00Z",
    "total": 1,
    "items": [{
        "code": "25731726",
        "name": "Piso Tarkett",
        "unit": "M2",
        "unit_price": "650.00",
        "price_source": "catalog",
        "available_quantity": "10",
        "reserved_quantity": "0",
        "reserved_by_others": False,
        "image_url": "",
        "product_url": "",
    }],
}

OFFIHO_CATALOG = {
    "source_hash": "offiho-e2e",
    "generated_at": "2026-07-19T20:00:00Z",
    "total": 2,
    "items": [{
        "inventory_key": "OFF-1",
        "code": "OFF-1",
        "name": "Silla Offiho",
        "variant": "Negro",
        "unit": "PZA",
        "pieces_per_box": "1",
        "available_quantity": "1",
        "reserved_quantity": "0",
        "reserved_by_others": False,
        "is_out_of_stock": False,
        "unit_price": "0",
        "price_source": "missing",
        "product_url": "",
        "image_url": "",
        "description": "Silla operativa",
        "description_source": "inventory_label",
        "match_status": "unmatched",
        "source_updated_at": "2026-07-19T20:00:00Z",
    }, {
        "inventory_key": "OHE-405 NEGRO ALUFSEN",
        "code": "OHE-405",
        "name": "ALUFSEN",
        "variant": "NEGRO",
        "unit": "PZA",
        "pieces_per_box": "1",
        "available_quantity": "8",
        "reserved_quantity": "0",
        "reserved_by_others": False,
        "is_out_of_stock": False,
        "unit_price": "7999.00",
        "price_source": "catalog",
        "product_url": "",
        "image_url": "",
        "description": "Silla operativa ALUFSEN",
        "description_source": "inventory_label",
        "match_status": "verified",
        "source_updated_at": "2026-07-19T20:00:00Z",
    }],
}

SONARA_CATALOG = {
    "supplier": "sonara",
    "source_hash": "sonara-e2e",
    "generated_at": "2026-07-19T20:00:00Z",
    "total": 1,
    "items": [{
        "internal_id": "sonara:review-panel",
        "supplier": "sonara",
        "product_key": "panel",
        "sku": "",
        "code_status": "needs_review",
        "brand": "Sonara",
        "collection": "Paneles",
        "name": "Panel Sonara",
        "description": "Panel liso",
        "unit": "PZA",
        "availability_type": "unknown",
        "stock": None,
        "reserved_quantity": "0",
        "reserved_by_others": False,
        "is_out_of_stock": False,
        "lead_time": "Por confirmar",
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": "MXN",
        "price_net": "77.00",
        "tax_rate": "0.160000",
        "attributes": {},
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": "",
        "warnings": ["Codigo por verificar"],
        "source_reference": "sonara:e2e:1",
    }],
}

ALMA_CATALOG = {
    "supplier": "alma",
    "source_hash": "alma-e2e",
    "generated_at": "2026-07-19T20:00:00Z",
    "total": 1,
    "items": [{
        "internal_id": "alma:desk",
        "supplier": "alma",
        "product_key": "desk",
        "sku": "AL-1",
        "code_status": "verified",
        "brand": "ALMA",
        "collection": "Workstations",
        "name": "Escritorio ALMA",
        "description": "Escritorio configurable",
        "unit": "PZA",
        "availability_type": "made_to_order",
        "stock": None,
        "reserved_quantity": "0",
        "reserved_by_others": False,
        "is_out_of_stock": False,
        "lead_time": "6 semanas",
        "base_price_options": [{
            "id": "base-a", "name": "Base A", "price_net": "100.00", "available": True,
        }],
        "add_on_options": [{
            "id": "addon-a",
            "name": "Electrificacion A",
            "family": "electrificacion",
            "price_net": "25.00",
            "available": True,
        }],
        "base_currency": "USD",
        "price_net": "0",
        "tax_rate": "0.160000",
        "attributes": {},
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": "",
        "warnings": [],
        "source_reference": "alma:e2e:1",
    }],
}

CATALOG_REGISTRY = {
    "suppliers": [
        {"supplier": "sonara", "label": "Sonara", "enabled": True},
        {"supplier": "alma", "label": "ALMA", "enabled": True},
    ],
}

SUCCESS_JOB = {
    "mensaje": "Cotizacion mixta en cola",
    "job": {
        "id": "job-mixed-1",
        "status": "queued",
        "metadata": {"source_type": "mixed_catalog_cart"},
    },
}

SESSION = {
    "access_token": "mixed-e2e-token",
    "usuario": {
        "id": 101,
        "email": "mixed-e2e@example.test",
        "nombre": "Mixed E2E",
        "empresa": "Mobiliti E2E",
        "es_admin": False,
    },
    "suscripcion": {
        "estado": "activa",
        "plan": "E2E",
        "fecha_fin": "2099-01-01T00:00:00Z",
    },
}

REQUIRED_FIELDS = (
    ("Proyecto *", "Proyecto E2E"),
    ("Cliente *", "Cliente E2E"),
    ("Correo *", "cliente@example.test"),
    ("Telefono *", "3330000000"),
    ("Direccion *", "Guadalajara"),
    ("Razon social *", "Cliente E2E SA de CV"),
)

EXPECTED_ITEMS = [
    {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
    {"catalog": "offiho", "inventory_key": "OFF-1", "quantity": "1.25"},
    {
        "catalog": "sonara",
        "internal_id": "sonara:review-panel",
        "quantity": "1",
        "add_on_option_ids": [],
    },
    {
        "catalog": "alma",
        "internal_id": "alma:desk",
        "quantity": "1",
        "base_option_id": "base-a",
        "add_on_option_ids": ["addon-a"],
    },
]

CONFIRMATION = (
    "Hay 1 producto(s) agotado(s) o con existencia insuficiente y 1 producto(s) "
    "con precio por confirmar. ¿Deseas continuar?"
)

KNOWN_API_REQUESTS = frozenset({
    ("GET", "/cotizaciones"),
    ("GET", "/cotizaciones/job-mixed-1"),
    ("GET", "/tarkett/catalog"),
    ("GET", "/offiho/catalog"),
    ("GET", "/catalogs"),
    ("GET", "/catalogs/sonara"),
    ("GET", "/catalogs/alma"),
    ("POST", "/catalogs/mixed-quote"),
    ("POST", "/cotizaciones/init-upload"),
    ("POST", f"/cotizaciones/{IMPORT_JOB_ID}/dev-upload"),
    ("POST", f"/cotizaciones/{IMPORT_JOB_ID}/import-preview"),
    ("GET", "/projects"),
    ("POST", "/projects"),
    ("GET", f"/projects/{PROJECT_ID}"),
    ("PATCH", f"/projects/{PROJECT_ID}"),
    ("POST", f"/projects/{PROJECT_ID}/quote"),
    ("POST", f"/projects/{PROJECT_ID}/imports/{IMPORT_JOB_ID}"),
    ("GET", "/catalogs/search"),
})


def is_exact_origin(request_url, controlled_origin):
    request = urlparse(request_url)
    controlled = urlparse(controlled_origin)
    return (
        request.scheme.lower(), request.hostname, request.port
    ) == (
        controlled.scheme.lower(), controlled.hostname, controlled.port
    )


def is_known_api_request(method, path, preflight_method=""):
    effective_method = preflight_method if method == "OPTIONS" else method
    return (effective_method.upper(), path) in KNOWN_API_REQUESTS


def test_network_guard_contract_rejects_other_local_origins_and_unknown_preflights():
    vite_origin = "http://127.0.0.1:5173"

    assert is_exact_origin("http://127.0.0.1:5173/src/main.jsx", vite_origin)
    assert not is_exact_origin("http://127.0.0.1:5174/src/main.jsx", vite_origin)
    assert not is_exact_origin("http://localhost:5173/src/main.jsx", vite_origin)
    assert not is_exact_origin("http://127.0.0.1:8000/catalogs", vite_origin)
    assert is_known_api_request("OPTIONS", "/catalogs/mixed-quote", "POST")
    assert not is_known_api_request("OPTIONS", "/catalogs/not-stubbed", "POST")
    assert not is_known_api_request("OPTIONS", "/catalogs/mixed-quote", "DELETE")


def test_project_survives_reload_and_supports_replacements_and_complements(
    vite_url, browser
):
    stub = ApiStub([])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(
        browser, {"width": 1440, "height": 1000}, stub, vite_url
    )
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def add_product(code):
        page.get_by_role("button", name="Agregar producto", exact=True).click()
        picker = page.get_by_role("dialog", name="Seleccionar producto")
        picker.get_by_label("Buscar producto", exact=True).fill(code)
        picker.get_by_role("button", name=re.compile(code, re.IGNORECASE)).click()
        picker.get_by_role("button", name="Agregar al Proyecto", exact=True).click()

    try:
        page.goto(vite_url)
        page.get_by_role("button", name="Proyectos", exact=True).click()
        page.get_by_role("button", name="Nuevo Proyecto", exact=True).click()
        page.get_by_label("Nombre del Proyecto", exact=True).fill(
            "QA Proyecto persistente"
        )

        add_product("OLIVE-II")
        add_product("OLIVE-II")
        assert page.get_by_text("OLIVE-II", exact=True).count() == 2

        page.get_by_role("button", name="Agregar complemento", exact=True).first.click()
        picker = page.get_by_role("dialog", name="Seleccionar producto")
        picker.get_by_label("Buscar producto", exact=True).fill("HEAD-1")
        picker.get_by_role(
            "button", name=re.compile("HEAD-1", re.IGNORECASE)
        ).click()
        picker.get_by_role(
            "button", name="Agregar complemento", exact=True
        ).click()

        config = page.get_by_role("dialog", name="Configurar HEAD-1")
        config.wait_for(state="visible")
        assert page.get_by_text("+ HEAD-1", exact=True).count() == 0
        config.get_by_role("combobox").select_option("fixed_project")
        config.get_by_role("textbox").fill("2")
        config.get_by_role(
            "button", name="Confirmar complemento", exact=True
        ).click()
        complement = page.get_by_text("+ HEAD-1", exact=True)
        complement.wait_for(state="visible")
        assert complement.count() == 1

        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        assert stub.project_revision > 0
        persisted = deepcopy(stub.saved_project)
        assert len(persisted["payload"]["lines"]) == 3

        page.reload()
        page.get_by_role("button", name="Proyectos", exact=True).click()
        project_card = page.locator(
            ".project-card", has_text="QA Proyecto persistente"
        )
        project_card.get_by_role("button", name="Abrir", exact=True).click()
        assert page.get_by_text("OLIVE-II", exact=True).count() == 2
        assert page.get_by_text("+ HEAD-1", exact=True).count() == 1
        assert stub.saved_project == persisted

        page.set_viewport_size({"width": 390, "height": 844})
        editor_box = page.locator(".project-editor").bounding_box()
        assert editor_box is not None
        assert editor_box["x"] == pytest.approx(0, abs=1)
        assert editor_box["y"] == pytest.approx(0, abs=1)
        assert editor_box["width"] == pytest.approx(390, abs=1)
        assert editor_box["height"] == pytest.approx(844, abs=1)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


class ApiStub:
    def __init__(self, mixed_responses, import_preview=None):
        self.mixed_responses = list(mixed_responses)
        self.import_preview = import_preview
        self.mixed_post_bodies = []
        self.upload_count = 0
        self.unexpected_requests = []

    def enable_project_routes(self, *, project_id):
        self.project_id = project_id
        self.saved_project = None
        self.project_revision = 0

    def install(self, page, vite_origin):
        def block_external_network(route):
            parsed = urlparse(route.request.url)
            if is_exact_origin(route.request.url, vite_origin):
                route.continue_()
                return
            self.unexpected_requests.append(
                f"EXTERNAL {route.request.method} {parsed.scheme}://{parsed.netloc}{parsed.path}"
            )
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"detail": "red externa bloqueada"}),
            )

        def fulfill_json(route, body, status=200):
            route.fulfill(
                status=status,
                content_type="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
                },
                body=json.dumps(body),
            )

        def project_response(name, payload):
            lines = payload.get("lines", [])
            return {
                "id": self.project_id,
                "usuario_id": SESSION["usuario"]["id"],
                "name": name,
                "status": "active",
                "revision": self.project_revision,
                "schema_version": deepcopy(payload["schema_version"]),
                "payload": deepcopy(payload),
                "last_operation_id": None,
                "created_at": "2026-07-23T12:00:00Z",
                "updated_at": f"2026-07-23T12:00:{self.project_revision:02d}Z",
                "archived_at": None,
                "summary": {
                    "principals": sum(
                        line.get("role") == "principal" for line in lines
                    ),
                    "complements": sum(
                        line.get("role") == "complement" for line in lines
                    ),
                },
            }

        def project_quote_snapshot():
            payload = deepcopy(self.saved_project["payload"])
            items = []
            for line in payload["lines"]:
                if line["source"] == "catalog":
                    item = {
                        "catalog": line["catalog"],
                        "quantity": line["quantity"],
                        **deepcopy(line["identity"]),
                    }
                else:
                    item = {
                        "kind": "imported",
                        "import_id": line["import_id"],
                        "source_row": line["source_row"],
                        "source_currency": line["source_currency"],
                        "quantity": line["quantity"],
                        "overrides": {
                            "name": line["name"],
                            "description": line["description"],
                            "dimension": line["dimension"],
                            "unit_price": line["unit_price"],
                            "provider": line["provider"],
                        },
                    }
                items.append(item)
            occupied_sections = {
                line["section_id"] for line in payload["lines"]
                if line["role"] == "principal"
            }
            sections = [
                section for section in payload["sections"]
                if section["section_id"] in occupied_sections
            ]
            return {
                **deepcopy(payload["quote_fields"]),
                "items": items,
                "sections": sections,
            }

        def dispatch(route):
            request = route.request
            parsed = urlparse(request.url)
            path = parsed.path
            if request.method == "OPTIONS":
                requested_method = request.headers.get("access-control-request-method", "")
                if is_known_api_request(request.method, path, requested_method):
                    fulfill_json(route, {}, status=204)
                else:
                    self.unexpected_requests.append(
                        f"OPTIONS {path} ({requested_method or 'metodo ausente'})"
                    )
                    fulfill_json(route, {"detail": "stub faltante"}, status=500)
                return
            if not is_known_api_request(request.method, path):
                self.unexpected_requests.append(f"{request.method} {path}")
                fulfill_json(route, {"detail": "stub faltante"}, status=500)
                return
            if request.method == "GET" and path == "/cotizaciones":
                fulfill_json(route, {"cotizaciones": []})
                return
            if request.method == "GET" and path == "/cotizaciones/job-mixed-1":
                fulfill_json(route, {"job": {**SUCCESS_JOB["job"], "status": "completed"}})
                return
            if request.method == "GET" and path == "/tarkett/catalog":
                fulfill_json(route, TARKETT_CATALOG)
                return
            if request.method == "GET" and path == "/offiho/catalog":
                fulfill_json(route, OFFIHO_CATALOG)
                return
            if request.method == "GET" and path == "/catalogs":
                fulfill_json(route, CATALOG_REGISTRY)
                return
            if request.method == "GET" and path == "/catalogs/sonara":
                fulfill_json(route, SONARA_CATALOG)
                return
            if request.method == "GET" and path == "/catalogs/alma":
                fulfill_json(route, ALMA_CATALOG)
                return
            if (
                hasattr(self, "project_id")
                and request.method == "GET"
                and path == "/projects"
            ):
                status = parse_qs(parsed.query).get("status", ["active"])[0]
                projects = (
                    [deepcopy(self.saved_project)]
                    if self.saved_project is not None
                    and self.saved_project["status"] == status
                    else []
                )
                fulfill_json(route, {"projects": projects})
                return
            if (
                hasattr(self, "project_id")
                and request.method == "POST"
                and path == "/projects"
            ):
                body = deepcopy(request.post_data_json)
                assert set(body) == {"name", "payload"}
                self.saved_project = project_response(body["name"], body["payload"])
                fulfill_json(
                    route, {"project": deepcopy(self.saved_project)}, status=201
                )
                return
            if (
                hasattr(self, "project_id")
                and request.method == "GET"
                and path == f"/projects/{self.project_id}"
            ):
                assert self.saved_project is not None
                fulfill_json(route, {"project": deepcopy(self.saved_project)})
                return
            if (
                hasattr(self, "project_id")
                and request.method == "PATCH"
                and path == f"/projects/{self.project_id}"
            ):
                assert self.saved_project is not None
                body = deepcopy(request.post_data_json)
                assert set(body) == {
                    "name", "payload", "expected_revision", "operation_id"
                }
                assert body["expected_revision"] == self.project_revision
                self.project_revision += 1
                self.saved_project = project_response(body["name"], body["payload"])
                self.saved_project["last_operation_id"] = body["operation_id"]
                fulfill_json(route, {"project": deepcopy(self.saved_project)})
                return
            if (
                hasattr(self, "project_id")
                and request.method == "POST"
                and path == f"/projects/{self.project_id}/imports/{IMPORT_JOB_ID}"
            ):
                assert self.import_preview is not None
                manifest = deepcopy(self.import_preview)
                manifest["items"] = [
                    {
                        key: deepcopy(value)
                        for key, value in item.items()
                        if key != "image_url"
                    }
                    for item in manifest["items"]
                ]
                prefix = (
                    f"projects/{SESSION['usuario']['id']}/{self.project_id}"
                )
                source_hash = manifest["source_hash"]
                fulfill_json(route, {
                    "source_asset_key": (
                        f"{prefix}/sources/{source_hash}.xlsx"
                    ),
                    "image_asset_keys": {
                        str(item["source_row"]): (
                            f"{prefix}/images/{source_hash[:16]}"
                            f"-row-{item['source_row']}.png"
                        )
                        for item in self.import_preview["items"]
                        if item.get("image_url")
                    },
                    "manifest": manifest,
                })
                return
            if (
                hasattr(self, "project_id")
                and request.method == "POST"
                and path == f"/projects/{self.project_id}/quote"
            ):
                assert self.saved_project is not None
                body = deepcopy(request.post_data_json)
                assert body == {"expected_revision": self.project_revision}
                self.mixed_post_bodies.append(project_quote_snapshot())
                if not self.mixed_responses:
                    self.unexpected_requests.append(
                        f"POST {path} (respuesta agotada)"
                    )
                    fulfill_json(route, {"detail": "stub faltante"}, status=500)
                    return
                status, response_body = self.mixed_responses.pop(0)
                fulfill_json(route, response_body, status=status)
                return
            if (
                hasattr(self, "project_id")
                and request.method == "GET"
                and path == "/catalogs/search"
            ):
                products = [{
                    "catalog": "sunon",
                    "official_code": "OLIVE-II",
                    "identity": {
                        "internal_id": "sunon:olive-ii",
                        "base_option_id": "",
                        "add_on_option_ids": [],
                    },
                    "snapshot": {
                        "name": "OLIVE-II",
                        "code": "OLIVE-II",
                        "image_url": IMPORT_PREVIEW_IMAGE,
                        "availability": "Disponible",
                        "configuration": "",
                        "warnings": [],
                    },
                }, {
                    "catalog": "alma",
                    "official_code": "HEAD-1",
                    "identity": {
                        "internal_id": "alma:head-1",
                        "base_option_id": "",
                        "add_on_option_ids": [],
                    },
                    "snapshot": {
                        "name": "HEAD-1",
                        "code": "HEAD-1",
                        "image_url": IMPORT_PREVIEW_IMAGE,
                        "availability": "Disponible",
                        "configuration": "",
                        "warnings": [],
                    },
                }]
                fulfill_json(route, {
                    "items": deepcopy(products),
                    "total": len(products),
                    "next_offset": None,
                })
                return
            if request.method == "POST" and path == "/cotizaciones/init-upload":
                fulfill_json(route, {
                    "job_id": IMPORT_JOB_ID,
                    "upload_url": f"/cotizaciones/{IMPORT_JOB_ID}/dev-upload",
                    "signed_upload_url": None,
                })
                return
            if request.method == "POST" and path == f"/cotizaciones/{IMPORT_JOB_ID}/dev-upload":
                self.upload_count += 1
                fulfill_json(route, {"mensaje": "Archivo cargado"})
                return
            if request.method == "POST" and path == f"/cotizaciones/{IMPORT_JOB_ID}/import-preview":
                if self.import_preview is None:
                    self.unexpected_requests.append(f"POST {path} (preview ausente)")
                    fulfill_json(route, {"detail": "stub faltante"}, status=500)
                    return
                fulfill_json(route, self.import_preview)
                return
            if request.method == "POST" and path == "/catalogs/mixed-quote":
                self.mixed_post_bodies.append(request.post_data_json)
                if not self.mixed_responses:
                    self.unexpected_requests.append(f"POST {path} (respuesta agotada)")
                    fulfill_json(route, {"detail": "stub faltante"}, status=500)
                    return
                status, body = self.mixed_responses.pop(0)
                fulfill_json(route, body, status=status)
                return
            raise AssertionError(f"Known API route lacks a deterministic stub: {request.method} {path}")

        page.route("**/*", block_external_network)
        page.route("http://127.0.0.1:8000/**", dispatch)


@pytest.fixture(scope="module")
def vite_url():
    web_root = Path("mobiliti_saas/web").resolve()
    node = web_root / "node_modules" / "vite" / "bin" / "vite.js"
    if not node.is_file():
        pytest.skip("The checked-in Vite runtime is required for browser acceptance")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        ["node", str(node), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=web_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while True:
            if process.poll() is not None:
                pytest.fail(f"Vite exited early with status {process.returncode}")
            try:
                with urlopen(f"http://127.0.0.1:{port}", timeout=1):
                    break
            except OSError:
                if time.time() >= deadline:
                    pytest.fail("Vite did not start for mixed-cart browser acceptance")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@pytest.fixture(scope="module")
def import_fixture(tmp_path_factory):
    source = write_import_fixture(
        tmp_path_factory.mktemp("mixed-browser-import") / "quotation-import.xlsx"
    )
    manifest, images = build_import_manifest(
        source.read_bytes(), IMPORT_JOB_ID, source.name
    )
    assert len(manifest["sections"]) == 3
    assert len(manifest["items"]) == 7
    assert len(images) == 7
    preview = {
        **manifest,
        "items": [
            {**item, "image_url": IMPORT_PREVIEW_IMAGE}
            for item in manifest["items"]
        ],
    }
    return source, preview


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.is_file():
        pytest.skip("System Chrome is required for mixed-cart browser acceptance")
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch(executable_path=str(chrome), headless=True)
        try:
            yield instance
        finally:
            instance.close()


def new_page(browser, viewport, api_stub, vite_url):
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.set_default_timeout(12_000)
    page.add_init_script(
        "localStorage.setItem('mobiliti_session', JSON.stringify(%s))"
        % json.dumps(SESSION)
    )
    api_stub.install(page, vite_url)
    return context, page


def capture_console_errors(page):
    errors = []

    def capture(message):
        if message.type != "error":
            return
        errors.append({
            "text": message.text,
            "url": message.location.get("url", ""),
        })

    page.on("console", capture)
    return errors


def is_intentional_mixed_422_console_error(message):
    return (
        "422" in message["text"]
        and urlparse(message["url"]).path in {
            "/catalogs/mixed-quote",
            f"/projects/{PROJECT_ID}/quote",
        }
    )


def assert_no_browser_failures(page, console_errors, page_errors):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert page_errors == []
    assert console_errors == []


def close_auto_opened_drawer(page, expected_count):
    dialog = page.get_by_role("dialog", name="Proyecto activo")
    dialog.wait_for(state="visible")
    assert dialog.locator(".mixed-cart-title span").inner_text() == str(expected_count)
    dialog.get_by_role("status").filter(has_text="Guardado").wait_for(
        state="visible"
    )
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert page.get_by_role(
        "button", name=f"Proyecto ({expected_count})"
    ).first.is_visible()


def create_active_project(page, name="Proyecto E2E"):
    page.get_by_role("button", name="Proyectos", exact=True).click()
    page.get_by_role("button", name="Nuevo Proyecto", exact=True).click()
    project_name = page.get_by_label("Nombre del Proyecto", exact=True)
    project_name.wait_for(state="visible")
    project_name.fill(name)
    page.locator(".project-autosave-status.saved").wait_for(state="visible")


def open_project_editor_from_quick_panel(page):
    panel = page.get_by_role("dialog", name="Proyecto activo")
    panel.wait_for(state="visible")
    panel.get_by_role("button", name="Editar Proyecto", exact=True).click()
    page.locator(".project-editor").wait_for(state="visible")
    return page.locator(".project-editor")


def fill_required_fields(editor):
    editor.get_by_role("button", name=re.compile("Datos de cotizaci")).click()
    form = editor.locator(".project-quote-fields")
    values = dict(REQUIRED_FIELDS)
    for field, value in (
        ("proyecto", values["Proyecto *"]),
        ("cliente", values["Cliente *"]),
        ("correo", values["Correo *"]),
        ("telefono", values["Telefono *"]),
        ("direccion", values["Direccion *"]),
        ("razon_social", values["Razon social *"]),
    ):
        form.locator(f'input[name="{field}"]').fill(value)
    return form


def large_import_preview(base_preview, total=700, section_count=20):
    assert total == 700 and section_count == 20
    counts = [60] * 10 + [10] * 10
    items = []
    sections = []
    source_row = 9
    for section_index, count in enumerate(counts, start=1):
        item_keys = []
        for _ in range(count):
            key = f"import:{IMPORT_JOB_ID}:{source_row}"
            item_keys.append(key)
            items.append({
                "key": key,
                "source_row": source_row,
                "category": "",
                "name": f"Producto grande {source_row}",
                "description": "",
                "dimension": "",
                "provider": "Proveedor grande",
                "official_code": f"PG-{source_row}",
                "quantity": "1",
                "unit_price": "10.00",
                "source_currency": "MXN",
                "image_url": "",
                "row_hash": f"{source_row:064x}",
                "source_reference": (
                    f"{base_preview['original_filename']}"
                    f"#Quotation!{source_row}"
                ),
            })
            source_row += 1
        sections.append({
            "id": f"import-section-{section_index}",
            "title": f"Espacio {section_index}",
            "item_keys": item_keys,
        })
    return {
        "schema_version": base_preview["schema_version"],
        "import_id": IMPORT_JOB_ID,
        "source_hash": base_preview["source_hash"],
        "original_filename": base_preview["original_filename"],
        "provider": "Proveedor grande",
        "source_currency": "MXN",
        "currency_status": "detected",
        "columns": deepcopy(base_preview["columns"]),
        "sections": sections,
        "items": items,
    }


def accept_confirmation(page, confirmation_messages):
    def accept(prompt):
        confirmation_messages.append(prompt.message)
        prompt.accept()

    page.once("dialog", accept)


def add_catalog_product(
    page,
    catalog_name,
    product_name,
    *,
    configure=None,
    close_panel=True,
):
    page.get_by_role("button", name=re.compile(rf"^{re.escape(catalog_name)}")).click()
    page.get_by_text(product_name, exact=True).wait_for()
    if configure is not None:
        configure(page)
    if close_panel:
        with page.expect_response(
            lambda response: (
                response.request.method == "PATCH"
                and urlparse(response.url).path
                == f"/projects/{PROJECT_ID}"
            )
        ) as saved:
            page.get_by_role("button", name="Agregar", exact=True).first.click()
        assert saved.value.ok
        count = int(
            page.get_by_role("dialog", name="Proyecto activo")
            .locator(".mixed-cart-title span")
            .inner_text()
        )
        close_auto_opened_drawer(page, count)
    else:
        page.get_by_role("button", name="Agregar", exact=True).first.click()


def import_preview_into_active_project(
    page,
    source,
    *,
    source_currency=None,
):
    page.get_by_role("button", name="Nueva", exact=True).click()
    page.locator('input[type="file"][accept=".xlsx,.pdf"]').set_input_files(source)
    page.get_by_role(
        "button", name="Previsualizar e importar al proyecto", exact=True
    ).click()
    preview_panel = page.get_by_role(
        "region", name="Previsualizacion de importacion"
    )
    preview_panel.wait_for(state="visible")
    if source_currency is not None:
        preview_panel.get_by_label(re.compile(r"^Moneda de origen")).select_option(
            source_currency
        )
    preview_panel.get_by_role(
        "button", name="Confirmar importacion al proyecto", exact=True
    ).click()
    panel = page.get_by_role("dialog", name="Proyecto activo")
    visible_error = page.locator('[role="alert"]:visible')
    page.locator(
        '[role="dialog"][aria-label="Proyecto activo"]:visible, '
        '[role="alert"]:visible'
    ).first.wait_for(state="visible")
    if visible_error.count():
        raise AssertionError(
            "Importacion al proyecto rechazada: "
            + " | ".join(visible_error.all_inner_texts())
        )
    return panel


def test_four_catalog_checkout_retains_422_state_then_retries_once(vite_url, browser):
    stub = ApiStub([
        (422, {"detail": "sonara:sonara:review-panel requiere revision"}),
        (200, SUCCESS_JOB),
    ])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto checkout E2E")
        add_catalog_product(page, "Tarkett", "Piso Tarkett")
        add_catalog_product(page, "Offiho", "Silla Offiho")
        add_catalog_product(page, "Sonara", "Panel Sonara")
        add_catalog_product(
            page,
            "ALMA",
            "Escritorio ALMA",
            configure=lambda current: (
                current.get_by_role("button", name=re.compile(r"^Base A")).click(),
                current.get_by_role(
                    "button", name=re.compile(r"^Electrificacion A")
                ).click(),
            ),
            close_panel=False,
        )
        editor = open_project_editor_from_quick_panel(page)
        assert editor.locator(".project-principal").count() == 4
        fill_required_fields(editor)
        page.locator(".project-autosave-status.saved").wait_for(state="visible")

        confirmations = []
        accept_confirmation(page, confirmations)
        editor.get_by_role("button", name=re.compile("^Generar cotizaci")).click()
        editor.get_by_role("alert").filter(
            has_text="sonara:sonara:review-panel requiere revision"
        ).wait_for()
        assert len(stub.mixed_post_bodies) == 1
        assert len(stub.mixed_post_bodies[0]["items"]) == 4
        intentional = [
            message for message in console_errors
            if is_intentional_mixed_422_console_error(message)
        ]
        assert intentional
        console_errors[:] = [
            message for message in console_errors if message not in intentional
        ]

        accept_confirmation(page, confirmations)
        editor.get_by_role("button", name=re.compile("^Generar cotizaci")).click()
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola"
        ).wait_for()
        assert len(stub.mixed_post_bodies) == 2
        assert stub.mixed_post_bodies[0]["items"] == stub.mixed_post_bodies[1]["items"]
        assert len(stub.saved_project["payload"]["lines"]) == 4
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_synchronous_double_submit_creates_one_mixed_job(vite_url, browser):
    stub = ApiStub([(200, SUCCESS_JOB)])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto doble submit")
        add_catalog_product(
            page, "Tarkett", "Piso Tarkett", close_panel=False
        )
        editor = open_project_editor_from_quick_panel(page)
        fill_required_fields(editor)
        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        generate = editor.get_by_role(
            "button", name=re.compile("^Generar cotizaci")
        )
        generate.evaluate("button => { button.click(); button.click(); }")
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola"
        ).wait_for()
        assert len(stub.mixed_post_bodies) == 1
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_tarkett_card_rejects_invalid_draft_without_adding_line(vite_url, browser):
    stub = ApiStub([])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto cantidad invalida")
        page.get_by_role("button", name=re.compile(r"^Tarkett")).click()
        card = page.locator("article.tarkett-product", has_text="Piso Tarkett")
        card.wait_for()
        card.locator('input[type="number"]').fill("")
        card.get_by_role("button", name="Agregar", exact=True).click()
        assert page.get_by_role("alert").filter(has_text="Cantidad invalida").is_visible()
        assert stub.saved_project["payload"]["lines"] == []
        assert page.locator(".mixed-cart-overlay").count() == 0
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_mobile_drawer_traps_focus_closes_on_escape_and_never_overflows(
    vite_url, browser
):
    stub = ApiStub([])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto panel movil")
        add_catalog_product(
            page, "Sonara", "Panel Sonara", close_panel=False
        )
        dialog = page.get_by_role("dialog", name="Proyecto activo")
        dialog.wait_for(state="visible")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        close_button = dialog.get_by_role("button", name="Cerrar proyecto")
        close_button.focus()
        page.keyboard.press("Shift+Tab")
        assert dialog.evaluate("element => element.contains(document.activeElement)")
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        page.get_by_role("button", name="Proyecto (1)").first.click()
        dialog.wait_for(state="visible")
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_browser_imports_uploaded_quotation_into_global_cart_and_quotes(
    vite_url, browser, import_fixture
):
    source, preview = import_fixture
    stub = ApiStub([(200, SUCCESS_JOB)], import_preview=preview)
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto importado")
        panel = import_preview_into_active_project(
            page,
            source,
            source_currency="USD",
        )
        panel.wait_for(state="visible")
        assert panel.locator(".mixed-cart-title span").inner_text() == "7"
        editor = open_project_editor_from_quick_panel(page)
        assert editor.locator(".project-principal").count() == 7
        imported_line = editor.locator(
            "article.project-principal", has_text="CAI63SW Alien Task Chair"
        )
        imported_line.get_by_text("Editar datos importados", exact=True).click()
        imported_line.locator('input[name="unitPrice"]').fill("82.00")
        imported_line.locator('input[name="unitPrice"]').blur()
        imported_line.locator('textarea[name="description"]').fill(
            "Descripcion revisada en navegador"
        )
        imported_line.locator('textarea[name="description"]').blur()
        imported_line.locator(".project-line-quantity input").fill("2")
        imported_line.locator(".project-line-quantity input").blur()
        fill_required_fields(editor)
        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        editor.get_by_role("button", name=re.compile("^Generar cotizaci")).click()
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola"
        ).wait_for()
        assert len(stub.mixed_post_bodies) == 1
        assert stub.upload_count == 1
        assert len(stub.mixed_post_bodies[0]["items"]) == 7
        imported = next(
            item for item in stub.mixed_post_bodies[0]["items"]
            if item.get("source_row") == 11
        )
        assert imported["quantity"] == "2"
        assert imported["overrides"]["unit_price"] == "82.00"
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_browser_submits_700_lines_once_from_compact_collapsed_cart(
    vite_url, browser, import_fixture
):
    source, base_preview = import_fixture
    preview = large_import_preview(base_preview)
    stub = ApiStub([(200, SUCCESS_JOB)], import_preview=preview)
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    page.set_default_timeout(45_000)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto 700 lineas")
        panel = import_preview_into_active_project(page, source)
        panel.wait_for(state="visible")
        assert panel.locator(".mixed-cart-title span").inner_text() == "700"
        panel.get_by_role("status").filter(
            has_text=re.compile("Cambios pendientes|Guardando")
        ).wait_for(state="visible")
        panel.get_by_role("status").filter(has_text="Guardado").wait_for(
            state="visible"
        )
        assert len(stub.saved_project["payload"]["lines"]) == 700
        editor = open_project_editor_from_quick_panel(page)
        fill_required_fields(editor)
        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        editor.get_by_role("button", name=re.compile("^Generar cotizaci")).click()
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola"
        ).wait_for()
        assert len(stub.mixed_post_bodies) == 1
        assert len(stub.mixed_post_bodies[0]["items"]) == 700
        assert len(stub.mixed_post_bodies[0]["sections"]) == 20
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_collapsed_imported_editor_preserves_invalid_text_and_error(
    vite_url, browser, import_fixture
):
    source, preview = import_fixture
    stub = ApiStub([], import_preview=preview)
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto conserva borrador")
        import_preview_into_active_project(
            page,
            source,
            source_currency="USD",
        )
        editor = open_project_editor_from_quick_panel(page)
        line = editor.locator(
            "article.project-principal", has_text="CAI63SW Alien Task Chair"
        )
        line.get_by_text("Editar datos importados", exact=True).click()
        unit_price = line.locator('input[name="unitPrice"]')
        unit_price.fill("")
        unit_price.blur()
        error = line.locator('small[id$="-unit-price-error"]').filter(
            has_text="Precio importado invalido"
        )
        error.wait_for(state="visible")
        page.get_by_label("Nombre del Proyecto", exact=True).fill(
            "Proyecto conserva borrador actualizado"
        )
        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        assert unit_price.input_value() == ""
        assert error.is_visible()
        unit_price.fill("12.00")
        unit_price.blur()
        error.wait_for(state="detached")
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_hidden_invalid_quantity_expands_announces_and_focuses_before_submit(
    vite_url, browser
):
    stub = ApiStub([])
    stub.enable_project_routes(project_id=PROJECT_ID)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        create_active_project(page, "Proyecto cantidades locales")
        add_catalog_product(
            page, "Tarkett", "Piso Tarkett", close_panel=False
        )
        editor = open_project_editor_from_quick_panel(page)
        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        quantity = editor.locator(".project-line-quantity input")
        quantity.fill("")
        quantity.blur()
        alert = editor.locator(".project-line-quantity").get_by_role("alert")
        alert.wait_for(state="visible")
        assert quantity.get_attribute("aria-invalid") == "true"
        assert stub.saved_project["payload"]["lines"][0]["quantity"] == "1"
        quantity.fill("2")
        quantity.blur()
        alert.wait_for(state="detached")
        page.locator(".project-autosave-status.saved").wait_for(state="visible")
        assert stub.saved_project["payload"]["lines"][0]["quantity"] == "2"
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()
