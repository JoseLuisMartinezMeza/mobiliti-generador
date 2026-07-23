import json
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest

from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from quotation_import_fixtures import write_import_fixture


IMPORT_JOB_ID = "77777777-7777-4777-8777-777777777777"
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


class ApiStub:
    def __init__(self, mixed_responses, import_preview=None):
        self.mixed_responses = list(mixed_responses)
        self.import_preview = import_preview
        self.mixed_post_bodies = []
        self.upload_count = 0
        self.unexpected_requests = []

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
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                },
                body=json.dumps(body),
            )

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
        and urlparse(message["url"]).path == "/catalogs/mixed-quote"
    )


def assert_no_browser_failures(page, console_errors, page_errors):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert page_errors == []
    assert console_errors == []


def close_auto_opened_drawer(page, expected_count):
    dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
    dialog.wait_for(state="visible")
    assert dialog.locator(".mixed-cart-line").count() == expected_count
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert page.get_by_role("button", name=f"Carrito ({expected_count})").first.is_visible()


def fill_required_fields(dialog):
    for label, value in REQUIRED_FIELDS:
        dialog.get_by_label(label, exact=True).fill(value)


def large_import_preview(total=700, section_count=20):
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
                "name": f"Producto grande {source_row}",
                "description": "",
                "dimension": "",
                "quantity": "1",
                "unit_price": "10.00",
                "source_currency": "MXN",
                "image_url": "",
            })
            source_row += 1
        sections.append({
            "id": f"source-section-{section_index}",
            "title": f"Espacio {section_index}",
            "item_keys": item_keys,
        })
    return {
        "import_id": IMPORT_JOB_ID,
        "original_filename": "large-import.xlsx",
        "provider": "Proveedor grande",
        "source_currency": "MXN",
        "currency_status": "detected",
        "sections": sections,
        "items": items,
    }


def accept_confirmation(page, confirmation_messages):
    def accept(prompt):
        confirmation_messages.append(prompt.message)
        prompt.accept()

    page.once("dialog", accept)


def test_four_catalog_checkout_retains_422_state_then_retries_once(vite_url, browser):
    stub = ApiStub([
        (422, {"detail": "sonara:sonara:review-panel requiere revision"}),
        (200, SUCCESS_JOB),
    ])
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        drawer = page.locator(".mixed-cart-drawer")
        drawer.wait_for(state="hidden")
        assert page.locator(".mixed-cart-overlay").count() == 0

        page.get_by_role("button", name=re.compile(r"^Tarkett")).click()
        page.get_by_text("Piso Tarkett", exact=True).wait_for()
        page.get_by_role("button", name="Agregar", exact=True).first.click()
        close_auto_opened_drawer(page, 1)

        page.get_by_role("button", name=re.compile(r"^Offiho")).click()
        page.get_by_text("Silla Offiho", exact=True).wait_for()
        page.get_by_role("button", name="Agregar", exact=True).first.click()
        close_auto_opened_drawer(page, 2)

        page.get_by_role("button", name=re.compile(r"^Sonara")).click()
        page.get_by_text("Panel Sonara", exact=True).wait_for()
        page.get_by_role("button", name="Agregar", exact=True).first.click()
        close_auto_opened_drawer(page, 3)

        page.get_by_role("button", name=re.compile(r"^ALMA")).click()
        page.get_by_text("Escritorio ALMA", exact=True).wait_for()
        page.get_by_role("button", name=re.compile(r"^Base A")).click()
        page.get_by_role("button", name=re.compile(r"^Electrificacion A")).click()
        page.get_by_role("button", name="Agregar", exact=True).first.click()

        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        assert dialog.locator(".mixed-cart-line").count() == 4
        assert dialog.get_by_text("Codigo por verificar", exact=True).is_visible()
        assert dialog.get_by_text("Escritorio ALMA", exact=True).is_visible()
        assert dialog.get_by_text("Base A + Electrificacion A", exact=True).is_visible()
        first_concept = dialog.get_by_label("Concepto de la sección 1", exact=True)
        assert first_concept.input_value() == "Recepción"
        dialog.get_by_role("button", name="Cerrar sección y abrir otra", exact=True).click()
        second_concept = dialog.get_by_label("Concepto de la sección 2", exact=True)
        assert second_concept.input_value() == "Sala de estar"
        second_concept.fill("Privados")
        dialog.get_by_role("button", name="Subir Silla Offiho", exact=True).click()
        dialog.get_by_label(
            "Mover Escritorio ALMA a otra sección", exact=True
        ).select_option("section-2")
        fill_required_fields(dialog)
        assert dialog.locator("label", has_text="Moneda de cotizacion").locator("select").input_value() == "MXN"
        assert dialog.locator(
            "label", has_text="Descuento general (%)"
        ).locator("input").input_value() == "40"

        offiho_quantity = dialog.get_by_label("Cantidad para Silla Offiho", exact=True)
        offiho_quantity.fill("")
        dialog.get_by_role("button", name="Cotizar todos los catalogos", exact=True).click()
        assert stub.mixed_post_bodies == []
        assert offiho_quantity.evaluate("element => document.activeElement === element")
        offiho_quantity.fill("1.")
        assert offiho_quantity.input_value() == "1."
        offiho_quantity.fill("1.25")
        offiho_quantity.press("Tab")
        assert offiho_quantity.input_value() == "1.25"

        confirmations = []
        accept_confirmation(page, confirmations)
        dialog.get_by_role("button", name="Cotizar todos los catalogos", exact=True).click()
        dialog.get_by_role("alert").filter(
            has_text="sonara:sonara:review-panel requiere revision"
        ).wait_for()
        assert confirmations == [CONFIRMATION]
        assert len(stub.mixed_post_bodies) == 1
        request_body = stub.mixed_post_bodies[0]
        expected_manual_items = [
            EXPECTED_ITEMS[1], EXPECTED_ITEMS[0], EXPECTED_ITEMS[2], EXPECTED_ITEMS[3]
        ]
        assert request_body["items"] == expected_manual_items
        assert request_body["sections"] == [
            {
                "id": "section-1",
                "title": "Recepción",
                "item_keys": [
                    "offiho:OFF-1",
                    "tarkett:25731726",
                    'sonara:["sonara:review-panel","",[]]',
                ],
            },
            {
                "id": "section-2",
                "title": "Privados",
                "item_keys": ['alma:["alma:desk","base-a",["addon-a"]]'],
            },
        ]
        for forbidden in (
            "snapshot", "unit_price", "price_net", "base_currency", "exchange_rate",
            "stock", "image_url", "product_url", "warnings",
        ):
            assert all(forbidden not in item for item in request_body["items"])

        assert dialog.locator(".mixed-cart-line").count() == 4
        assert page.get_by_role("button", name="Carrito (4)").first.is_visible()
        assert offiho_quantity.input_value() == "1.25"
        for label, value in REQUIRED_FIELDS:
            assert dialog.get_by_label(label, exact=True).input_value() == value

        # Chrome reports the deliberately stubbed 422 as a network console error.
        # Account for that single expected diagnostic, then require a clean retry.
        intentional_422_errors = [
            message for message in console_errors
            if is_intentional_mixed_422_console_error(message)
        ]
        assert intentional_422_errors
        assert [
            message for message in console_errors
            if message not in intentional_422_errors
        ] == []
        console_errors[:] = [
            message for message in console_errors
            if message not in intentional_422_errors
        ]

        accept_confirmation(page, confirmations)
        dialog.get_by_role("button", name="Cotizar todos los catalogos", exact=True).click()
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola. Revisa el avance en Cotizaciones."
        ).wait_for()
        dialog.wait_for(state="hidden")
        empty_cart = page.get_by_role("button", name="Carrito (0)").first
        empty_cart.wait_for(state="visible")
        assert empty_cart.is_visible()
        assert confirmations == [CONFIRMATION, CONFIRMATION]
        assert len(stub.mixed_post_bodies) == 2
        assert [body["items"] for body in stub.mixed_post_bodies] == [
            expected_manual_items,
            expected_manual_items,
        ]
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_synchronous_double_submit_creates_one_mixed_job(vite_url, browser):
    stub = ApiStub([(200, SUCCESS_JOB)])
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        page.get_by_role("button", name=re.compile(r"^Tarkett")).click()
        page.get_by_text("Piso Tarkett", exact=True).wait_for()
        page.get_by_role("button", name="Agregar", exact=True).first.click()
        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        fill_required_fields(dialog)

        dialog.locator("form.mixed-quote-form").evaluate(
            """form => {
                const first = new Event('submit', {bubbles: true, cancelable: true});
                const second = new Event('submit', {bubbles: true, cancelable: true});
                form.dispatchEvent(first);
                form.dispatchEvent(second);
            }"""
        )
        page.get_by_role("status").filter(has_text="Cotizacion mixta en cola").wait_for()
        assert len(stub.mixed_post_bodies) == 1
        assert stub.mixed_post_bodies[0]["items"] == [EXPECTED_ITEMS[0]]
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_tarkett_card_rejects_invalid_draft_without_adding_line(vite_url, browser):
    stub = ApiStub([])
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        page.get_by_role("button", name=re.compile(r"^Tarkett")).click()
        card = page.locator("article.tarkett-product", has_text="Piso Tarkett")
        card.wait_for()
        quantity = card.locator('input[type="number"]')
        quantity.fill("")
        card.get_by_role("button", name="Agregar", exact=True).click()

        assert page.get_by_role("alert").filter(has_text="Cantidad invalida").is_visible()
        assert page.get_by_role("button", name="Carrito (0)").first.is_visible()
        assert page.locator(".mixed-cart-overlay").count() == 0
        assert stub.mixed_post_bodies == []
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_mobile_drawer_traps_focus_closes_on_escape_and_never_overflows(vite_url, browser):
    stub = ApiStub([])
    context, page = new_page(browser, {"width": 390, "height": 844}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        assert_no_browser_failures(page, console_errors, page_errors)
        page.get_by_role("button", name=re.compile(r"^Sonara")).click()
        page.get_by_text("Panel Sonara", exact=True).wait_for()
        page.get_by_role("button", name="Agregar", exact=True).first.click()

        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")

        page.get_by_role("button", name="Carrito (1)").first.click()
        dialog.wait_for(state="visible")
        close_button = dialog.get_by_role("button", name="Cerrar carrito")
        close_button.focus()
        assert close_button.evaluate("element => document.activeElement === element")
        page.keyboard.press("Shift+Tab")
        assert dialog.evaluate("element => element.contains(document.activeElement)")
        page.keyboard.press("Tab")
        assert close_button.evaluate("element => document.activeElement === element")

        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_browser_imports_uploaded_quotation_into_global_cart_and_quotes(
    vite_url, browser, import_fixture
):
    source, preview = import_fixture
    stub = ApiStub([(200, SUCCESS_JOB)], import_preview=preview)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        page.get_by_role("button", name="Nueva cotizacion", exact=True).click()
        page.locator('input[type="file"][accept=".xlsx,.pdf"]').set_input_files(source)
        page.get_by_role(
            "button", name="Previsualizar e importar al carrito", exact=True
        ).click()

        preview_panel = page.get_by_role(
            "region", name="Previsualizacion de importacion"
        )
        preview_panel.wait_for(state="visible")
        assert preview_panel.get_by_text(
            "7 producto(s) en 3 seccion(es).", exact=True
        ).is_visible()
        assert preview["currency_status"] == "required"
        currency = preview_panel.get_by_label(re.compile(r"^Moneda de origen"))
        confirm = preview_panel.get_by_role(
            "button", name="Confirmar importacion al carrito", exact=True
        )
        assert currency.input_value() == ""
        assert confirm.is_disabled()
        currency.select_option("USD")
        assert confirm.is_enabled()
        confirm.click()

        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        assert dialog.locator(".mixed-cart-line").count() == 7
        assert dialog.locator(".mixed-cart-line img").count() == 7

        imported_line = dialog.locator(
            "article.mixed-cart-line", has_text="CAI63SW Alien Task Chair"
        )
        imported_line.get_by_text("Editar datos importados", exact=True).click()
        imported_line.get_by_label("Precio unitario", exact=True).fill("82.00")
        imported_line.get_by_label("Precio unitario", exact=True).press("Tab")
        description = imported_line.locator('textarea[name="description"]')
        description.fill("Descripcion revisada en navegador")
        description.press("Tab")
        imported_line.get_by_label(
            "Cantidad para CAI63SW Alien Task Chair", exact=True
        ).fill("2")
        imported_line.get_by_label(
            "Cantidad para CAI63SW Alien Task Chair", exact=True
        ).press("Tab")

        page.keyboard.press("Escape")
        page.get_by_role("button", name=re.compile(r"^Offiho")).click()
        catalog_card = page.locator("article.offiho-product", has_text="ALUFSEN")
        catalog_card.wait_for(state="visible")
        catalog_card.get_by_role("button", name="Agregar", exact=True).click()

        dialog.wait_for(state="visible")
        assert dialog.locator(".mixed-cart-line").count() == 8
        catalog_line = dialog.locator("article.mixed-cart-line", has_text="ALUFSEN")
        catalog_line.locator(".mixed-cart-move-section select").select_option("section-2")
        catalog_line.get_by_role("button", name=re.compile(r"^Subir ")).click()

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.set_viewport_size({"width": 1440, "height": 1000})
        fill_required_fields(dialog)
        assert dialog.locator(
            "label", has_text="Moneda de cotizacion"
        ).locator("select").input_value() == "MXN"
        dialog.get_by_role(
            "button", name="Cotizar todos los catalogos", exact=True
        ).click()
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola. Revisa el avance en Cotizaciones."
        ).wait_for()

        assert len(stub.mixed_post_bodies) == 1
        assert stub.upload_count == 1
        body = stub.mixed_post_bodies[0]
        assert len(body["items"]) == 8
        imported = next(item for item in body["items"] if item.get("source_row") == 11)
        assert imported["source_currency"] == "USD"
        assert imported["quantity"] == "2"
        assert imported["overrides"]["unit_price"] == "82.00"
        assert imported["overrides"]["description"] == "Descripcion revisada en navegador"
        assert any(
            section["item_keys"][-2:] == [
                "offiho:OHE-405 NEGRO ALUFSEN",
                f"import:{IMPORT_JOB_ID}:15",
            ]
            for section in body["sections"]
        )
        assert body["quote_currency"] == "MXN"
        empty_cart = page.get_by_role("button", name="Carrito (0)").first
        empty_cart.wait_for(state="visible")
        assert empty_cart.is_visible()
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_browser_submits_700_lines_once_from_compact_collapsed_cart(
    vite_url, browser, import_fixture
):
    source, _preview = import_fixture
    preview = large_import_preview()
    stub = ApiStub([(200, SUCCESS_JOB)], import_preview=preview)
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    page.set_default_timeout(30_000)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        page.get_by_role("button", name="Nueva cotizacion", exact=True).click()
        page.locator('input[type="file"][accept=".xlsx,.pdf"]').set_input_files(source)
        page.get_by_role(
            "button", name="Previsualizar e importar al carrito", exact=True
        ).click()
        preview_panel = page.get_by_role(
            "region", name="Previsualizacion de importacion"
        )
        preview_panel.get_by_text("700 producto(s) en 20 seccion(es).", exact=True).wait_for()
        preview_panel.get_by_role(
            "button", name="Confirmar importacion al carrito", exact=True
        ).click()

        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        toggles = dialog.locator(".mixed-cart-section-toggle")
        assert toggles.count() == 20
        assert [toggles.nth(index).get_attribute("aria-expanded") for index in range(20)] == (
            ["false"] * 10 + ["true"] * 10
        )
        toggle_labels = [toggles.nth(index).get_attribute("aria-label") for index in range(20)]
        assert all(label and "seccion" in label.lower() for label in toggle_labels)
        assert len(set(toggle_labels)) == 20
        assert dialog.locator(".mixed-cart-line").count() == 100
        assert toggles.first.evaluate(
            "button => document.getElementById(button.getAttribute('aria-controls'))?.hidden === true"
        )

        fill_required_fields(dialog)
        dialog.get_by_role(
            "button", name="Cotizar todos los catalogos", exact=True
        ).click()
        page.get_by_role("status").filter(has_text="Cotizacion mixta en cola").wait_for()

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
    source, _preview = import_fixture
    stub = ApiStub([], import_preview=large_import_preview())
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    page.set_default_timeout(30_000)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        page.get_by_role("button", name="Nueva cotizacion", exact=True).click()
        page.locator('input[type="file"][accept=".xlsx,.pdf"]').set_input_files(source)
        page.get_by_role(
            "button", name="Previsualizar e importar al carrito", exact=True
        ).click()
        preview_panel = page.get_by_role(
            "region", name="Previsualizacion de importacion"
        )
        preview_panel.get_by_text("700 producto(s) en 20 seccion(es).", exact=True).wait_for()
        preview_panel.get_by_role(
            "button", name="Confirmar importacion al carrito", exact=True
        ).click()

        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        toggle = dialog.locator(".mixed-cart-section-toggle").first
        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.click()

        line = dialog.locator("article.mixed-cart-line", has_text="Producto grande 9")
        line.get_by_text("Editar datos importados", exact=True).click()
        unit_price = line.locator('input[name="unitPrice"]')
        unit_price.fill("")
        unit_price.blur()
        unit_price_error = line.locator('small[id$="-unit-price-error"]')
        unit_price_error.filter(has_text="Precio importado invalido").wait_for()
        assert unit_price.input_value() == ""

        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"

        restored_line = dialog.locator("article.mixed-cart-line", has_text="Producto grande 9")
        restored_line.get_by_text("Editar datos importados", exact=True).click()
        restored_price = restored_line.locator('input[name="unitPrice"]')
        assert restored_price.input_value() == ""
        assert restored_line.locator('small[id$="-unit-price-error"]').filter(
            has_text="Precio importado invalido"
        ).is_visible()

        restored_price.fill("12.00")
        restored_price.blur()
        assert restored_line.locator('small[id$="-unit-price-error"]').filter(
            has_text="Precio importado invalido"
        ).count() == 0
        assert dialog.get_by_role(
            "button", name="Cotizar todos los catalogos", exact=True
        ).is_enabled()
        assert stub.mixed_post_bodies == []
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_hidden_invalid_quantity_expands_announces_and_focuses_before_submit(
    vite_url, browser, import_fixture
):
    source, _preview = import_fixture
    stub = ApiStub([(200, SUCCESS_JOB)], import_preview=large_import_preview())
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub, vite_url)
    page.set_default_timeout(30_000)
    console_errors = capture_console_errors(page)
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        page.goto(vite_url)
        page.get_by_role("button", name="Nueva cotizacion", exact=True).click()
        page.locator('input[type="file"][accept=".xlsx,.pdf"]').set_input_files(source)
        page.get_by_role(
            "button", name="Previsualizar e importar al carrito", exact=True
        ).click()
        preview_panel = page.get_by_role(
            "region", name="Previsualizacion de importacion"
        )
        preview_panel.get_by_text("700 producto(s) en 20 seccion(es).", exact=True).wait_for()
        preview_panel.get_by_role(
            "button", name="Confirmar importacion al carrito", exact=True
        ).click()

        dialog = page.get_by_role("dialog", name="Carrito de todos los catalogos")
        dialog.wait_for(state="visible")
        toggle = dialog.locator(".mixed-cart-section-toggle").first
        toggle.click()
        quantity = dialog.get_by_label("Cantidad para Producto grande 9", exact=True)
        second_quantity = dialog.get_by_label("Cantidad para Producto grande 10", exact=True)
        quantity.fill("")
        second_quantity.fill("")
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "false"

        fill_required_fields(dialog)
        dialog.get_by_role(
            "button", name="Cotizar todos los catalogos", exact=True
        ).click()

        dialog.get_by_role("alert").filter(
            has_text="Corrige la cantidad marcada antes de cotizar."
        ).wait_for()
        assert toggle.get_attribute("aria-expanded") == "true"
        restored_quantity = dialog.get_by_label(
            "Cantidad para Producto grande 9", exact=True
        )
        restored_second_quantity = dialog.get_by_label(
            "Cantidad para Producto grande 10", exact=True
        )
        assert restored_quantity.input_value() == ""
        assert restored_quantity.get_attribute("aria-invalid") == "true"
        assert restored_second_quantity.input_value() == ""
        assert restored_second_quantity.get_attribute("aria-invalid") == "true"
        assert restored_quantity.evaluate("element => document.activeElement === element")

        imported_line = dialog.locator(
            "article.mixed-cart-line", has_text="Producto grande 9"
        )
        imported_line.get_by_text("Editar datos importados", exact=True).click()
        provider = imported_line.locator('input[name="provider"]')
        provider.fill("Proveedor actualizado")
        provider.blur()
        assert dialog.get_by_role("alert").filter(
            has_text="Corrige la cantidad marcada antes de cotizar."
        ).is_visible()

        restored_quantity.fill("2")
        restored_quantity.blur()
        assert restored_quantity.get_attribute("aria-invalid") == "false"
        assert restored_second_quantity.get_attribute("aria-invalid") == "true"
        assert dialog.get_by_role("alert").filter(
            has_text="Corrige la cantidad marcada antes de cotizar."
        ).is_visible()

        restored_second_quantity.fill("3")
        restored_second_quantity.blur()
        assert restored_second_quantity.get_attribute("aria-invalid") == "false"
        dialog.get_by_role("alert").filter(
            has_text="Corrige la cantidad marcada antes de cotizar."
        ).wait_for(state="detached")

        dialog.get_by_role(
            "button", name="Cotizar todos los catalogos", exact=True
        ).click()
        page.get_by_role("status").filter(has_text="Cotizacion mixta en cola").wait_for()
        assert len(stub.mixed_post_bodies) == 1
        assert stub.mixed_post_bodies[0]["items"][0]["quantity"] == "2"
        assert stub.mixed_post_bodies[0]["items"][1]["quantity"] == "3"
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()
