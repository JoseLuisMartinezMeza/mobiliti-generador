import json
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest


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
    "total": 1,
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


class ApiStub:
    def __init__(self, mixed_responses):
        self.mixed_responses = list(mixed_responses)
        self.mixed_post_bodies = []
        self.unexpected_requests = []

    def install(self, page):
        def block_external_network(route):
            parsed = urlparse(route.request.url)
            if parsed.hostname == "127.0.0.1":
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
                fulfill_json(route, {}, status=204)
                return
            if request.method == "GET" and path == "/cotizaciones":
                fulfill_json(route, {"cotizaciones": []})
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
            if request.method == "POST" and path == "/catalogs/mixed-quote":
                self.mixed_post_bodies.append(request.post_data_json)
                if not self.mixed_responses:
                    self.unexpected_requests.append(f"POST {path} (respuesta agotada)")
                    fulfill_json(route, {"detail": "stub faltante"}, status=500)
                    return
                status, body = self.mixed_responses.pop(0)
                fulfill_json(route, body, status=status)
                return
            self.unexpected_requests.append(f"{request.method} {path}")
            fulfill_json(route, {"detail": "stub faltante"}, status=500)

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


def new_page(browser, viewport, api_stub):
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.set_default_timeout(12_000)
    page.add_init_script(
        "localStorage.setItem('mobiliti_session', JSON.stringify(%s))"
        % json.dumps(SESSION)
    )
    api_stub.install(page)
    return context, page


def assert_no_browser_failures(page, console_errors, page_errors):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    assert page_errors == []
    assert [message for message in console_errors if "favicon" not in message.lower()] == []


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
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub)
    console_errors = []
    page_errors = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
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
        fill_required_fields(dialog)
        assert dialog.locator("label", has_text="Moneda de cotizacion").locator("select").input_value() == "MXN"
        assert dialog.locator(
            "label", has_text="Descuento Tarkett y Offiho (%)"
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
        assert request_body["items"] == EXPECTED_ITEMS
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
        assert len(console_errors) == 1
        assert "422" in console_errors[0]
        assert "Failed to load resource" in console_errors[0]
        console_errors.clear()

        accept_confirmation(page, confirmations)
        dialog.get_by_role("button", name="Cotizar todos los catalogos", exact=True).click()
        page.get_by_role("status").filter(
            has_text="Cotizacion mixta en cola. Revisa el avance en Cotizaciones."
        ).wait_for()
        dialog.wait_for(state="hidden")
        assert page.get_by_role("button", name="Carrito (0)").first.is_visible()
        assert confirmations == [CONFIRMATION, CONFIRMATION]
        assert len(stub.mixed_post_bodies) == 2
        assert [body["items"] for body in stub.mixed_post_bodies] == [EXPECTED_ITEMS, EXPECTED_ITEMS]
        assert stub.unexpected_requests == []
        assert_no_browser_failures(page, console_errors, page_errors)
    finally:
        context.close()


def test_synchronous_double_submit_creates_one_mixed_job(vite_url, browser):
    stub = ApiStub([(200, SUCCESS_JOB)])
    context, page = new_page(browser, {"width": 1440, "height": 1000}, stub)
    console_errors = []
    page_errors = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
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


def test_mobile_drawer_traps_focus_closes_on_escape_and_never_overflows(vite_url, browser):
    stub = ApiStub([])
    context, page = new_page(browser, {"width": 390, "height": 844}, stub)
    console_errors = []
    page_errors = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
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
