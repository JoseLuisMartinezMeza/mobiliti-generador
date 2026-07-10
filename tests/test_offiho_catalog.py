from decimal import Decimal
from datetime import datetime
from email.message import Message
from pathlib import Path
import json

import pytest
from openpyxl import load_workbook

import scripts.build_offiho_catalog as build
from scripts.build_offiho_catalog import extract_offiho_identity
from scripts.build_offiho_catalog import match_official_product
from scripts.build_offiho_catalog import parse_inventory_xls
from scripts.build_offiho_catalog import parse_pdf_price_index


def fake_runtime_catalog(
    *,
    available_quantity: int,
    unit_price: int,
    image_url: str = "",
    price_source: str = "inventory",
):
    from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem

    item = OffihoCatalogItem(
        inventory_key="OHE-405 NEGRO ALUFSEN",
        code="OHE-405",
        name="ALUFSEN",
        variant="NEGRO",
        unit="PZA",
        pieces_per_box=Decimal("1"),
        available_quantity=Decimal(str(available_quantity)),
        unit_price=Decimal(str(unit_price)),
        price_source=price_source,
        product_url="https://www.offiho.com/productos/alufsen",
        image_url=image_url,
    )
    return {"source_hash": "hash", "items": [item], "by_inventory_key": {item.inventory_key: item}}


def test_offiho_cart_accepts_exhausted_and_overstock_lines():
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload

    exhausted = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 3}],
        catalog=fake_runtime_catalog(available_quantity=0, unit_price=7999),
    )
    insufficient = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 3}],
        catalog=fake_runtime_catalog(available_quantity=2, unit_price=7999),
    )

    assert exhausted["items"][0]["stock_status"] == "out_of_stock"
    assert insufficient["items"][0]["stock_status"] == "insufficient_stock"
    assert insufficient["items"][0]["unit_price"] == 7999


def test_offiho_cart_uses_catalog_owned_values_and_rejects_non_positive_quantity():
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload

    payload = build_offiho_cart_payload(
        [
            {
                "inventory_key": "OHE-405 NEGRO ALUFSEN",
                "quantity": "2",
                "unit_price": 1,
                "available_quantity": 999,
                "product_url": "https://example.test/untrusted",
                "image_url": "https://example.test/untrusted.jpg",
            }
        ],
        catalog=fake_runtime_catalog(
            available_quantity=2,
            unit_price=7999,
            image_url="https://www.offiho.com/uploads/alufsen.jpg",
        ),
    )

    assert payload["items"][0]["stock_status"] == "available"
    assert payload["items"][0]["unit_price"] == 7999
    assert payload["items"][0]["available_quantity"] == 2
    assert payload["items"][0]["product_url"] == "https://www.offiho.com/productos/alufsen"
    assert payload["items"][0]["image_url"] == "https://www.offiho.com/uploads/alufsen.jpg"
    with pytest.raises(ValueError, match="Cantidad invalida"):
        build_offiho_cart_payload(
            [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 0}],
            catalog=fake_runtime_catalog(available_quantity=252, unit_price=7999),
        )
    with pytest.raises(ValueError, match="Cantidad invalida"):
        build_offiho_cart_payload(
            [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": "NaN"}],
            catalog=fake_runtime_catalog(available_quantity=252, unit_price=7999),
        )


@pytest.mark.parametrize("quantity", ["1e5000", "0.0001", "1000000.001"])
def test_offiho_cart_rejects_extreme_or_overprecise_quantity(quantity):
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload

    with pytest.raises(ValueError, match="Cantidad invalida"):
        build_offiho_cart_payload(
            [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": quantity}],
            catalog=fake_runtime_catalog(available_quantity=0, unit_price=7999),
        )


def test_offiho_cart_accepts_commercial_quantity_limit_and_three_decimals():
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": "1000000.000"}],
        catalog=fake_runtime_catalog(available_quantity=0, unit_price=7999),
    )

    assert payload["items"][0]["quantity"] == 1000000


def test_offiho_cart_accepts_200_unique_lines_and_rejects_201():
    from mobiliti_saas.quote_engine.offiho_catalog import MAX_CART_LINES, OffihoCatalogItem, build_offiho_cart_payload

    items = [
        OffihoCatalogItem(
            inventory_key=f"OHE-{index:03d} NEGRO MODELO {index}",
            code=f"OHE-{index:03d}",
            name=f"MODELO {index}",
            variant="NEGRO",
            unit="PZA",
            pieces_per_box=Decimal("1"),
            available_quantity=Decimal("0"),
            unit_price=Decimal("1"),
        )
        for index in range(MAX_CART_LINES + 1)
    ]
    catalog = {
        "source_hash": "hash",
        "items": items,
        "by_inventory_key": {item.inventory_key: item for item in items},
    }
    raw_items = [{"inventory_key": item.inventory_key, "quantity": 1} for item in items]

    assert len(build_offiho_cart_payload(raw_items[:MAX_CART_LINES], catalog=catalog)["items"]) == 200
    with pytest.raises(ValueError, match="200"):
        build_offiho_cart_payload(raw_items, catalog=catalog)


def test_offiho_cart_rejects_duplicate_inventory_key():
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload

    line = {"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 1}
    with pytest.raises(ValueError, match="duplicada"):
        build_offiho_cart_payload([line, dict(line)], catalog=fake_runtime_catalog(available_quantity=1, unit_price=7999))


def _runtime_catalog_raw():
    path = Path(__file__).resolve().parents[1] / "mobiliti_saas" / "quote_engine" / "data" / "offiho_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_load_offiho_catalog_propagates_inventory_audit():
    from mobiliti_saas.quote_engine.offiho_catalog import load_offiho_catalog

    catalog = load_offiho_catalog()

    assert catalog["source_row_count"] == 1286
    assert catalog["duplicate_row_count"] == 80
    assert catalog["unique_item_count"] == 1206


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("available_quantity", -1),
        ("available_quantity", "invalid"),
        ("available_quantity", "1000000000.000001"),
        ("unit_price", -1),
        ("unit_price", "NaN"),
        ("unit_price", "1e5000"),
        ("unit_price", "0.0000001"),
        ("pieces_per_box", 0),
        ("pieces_per_box", "invalid"),
        ("pieces_per_box", "1000000.000001"),
    ],
)
def test_load_offiho_catalog_rejects_corrupt_numeric_item_in_1206_index(tmp_path, field, value):
    from mobiliti_saas.quote_engine.offiho_catalog import load_offiho_catalog

    raw = _runtime_catalog_raw()
    raw["items"][0][field] = value
    path = tmp_path / "offiho-corrupt.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        load_offiho_catalog(path)


def test_offiho_json_number_rejects_extreme_decimal_before_int_conversion():
    from mobiliti_saas.quote_engine.offiho_catalog import _json_number

    with pytest.raises(ValueError, match="fuera de rango"):
        _json_number(Decimal("1e5000"))


@pytest.mark.parametrize("field", ["inventory_key", "code", "unit", "price_source", "match_status"])
def test_offiho_catalog_item_rejects_blank_required_field(field):
    from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem

    raw = _runtime_catalog_raw()["items"][0]
    raw[field] = " "

    with pytest.raises(ValueError, match=field):
        OffihoCatalogItem.from_dict(raw)


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("product_url", "http://www.offiho.com/productos/alufsen"),
        ("product_url", "https://example.com/productos/alufsen"),
        ("image_url", "https://econosillas.com/alufsen.jpg"),
    ],
)
def test_offiho_catalog_item_rejects_non_official_https_url(field, url):
    from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem

    raw = _runtime_catalog_raw()["items"][0]
    raw[field] = url

    with pytest.raises(ValueError, match=field):
        OffihoCatalogItem.from_dict(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_hash", ""),
        ("source_hash", None),
        ("generated_at", " "),
        ("generated_at", None),
    ],
)
def test_load_offiho_catalog_rejects_blank_root_metadata(tmp_path, field, value):
    from mobiliti_saas.quote_engine.offiho_catalog import load_offiho_catalog

    raw = _runtime_catalog_raw()
    raw[field] = value
    path = tmp_path / "offiho-blank-root.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        load_offiho_catalog(path)


class _FakePeerSocket:
    def __init__(self, address):
        self.address = address
        self.calls = 0

    def getpeername(self):
        self.calls += 1
        return (self.address, 443)


class _FakeImageResponse:
    def __init__(self, *, peer_address="93.184.216.34", include_peer=True, payload=b"image-bytes"):
        self.headers = {"content-type": "image/png", "content-length": str(len(payload))}
        self.payload = payload
        self.socket = _FakePeerSocket(peer_address)
        if include_peer:
            raw = type("Raw", (), {"_sock": self.socket})()
            self.fp = type("Fp", (), {"raw": raw})()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size):
        return self.payload[:size]


def _install_fake_image_opener(monkeypatch, response):
    import mobiliti_saas.quote_engine.catalog_cart as catalog_cart

    class _FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "https://www.offiho.com/uploads/alufsen.png"
            assert timeout == 18
            return response

    monkeypatch.setattr(catalog_cart, "_resolve_public_host", lambda host: None)
    monkeypatch.setattr(catalog_cart.urllib.request, "build_opener", lambda *handlers: _FakeOpener())
    return catalog_cart


def test_catalog_image_write_error_is_omitted(monkeypatch, tmp_path):
    response = _FakeImageResponse()
    catalog_cart = _install_fake_image_opener(monkeypatch, response)
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: (_ for _ in ()).throw(OSError("disk full")))

    result = catalog_cart._download_catalog_image(
        "https://www.offiho.com/uploads/alufsen.png",
        tmp_path,
        "OHE-405",
        "offiho_cart",
    )

    assert result is None
    assert response.socket.calls == 1


def test_catalog_image_rejects_private_connected_peer(monkeypatch, tmp_path):
    response = _FakeImageResponse(peer_address="127.0.0.1")
    catalog_cart = _install_fake_image_opener(monkeypatch, response)

    result = catalog_cart._download_catalog_image(
        "https://www.offiho.com/uploads/alufsen.png",
        tmp_path,
        "OHE-405",
        "offiho_cart",
    )

    assert result is None
    assert response.socket.calls == 1
    assert not list(tmp_path.iterdir())


def test_catalog_image_accepts_public_connected_peer(monkeypatch, tmp_path):
    response = _FakeImageResponse(peer_address="93.184.216.34")
    catalog_cart = _install_fake_image_opener(monkeypatch, response)

    result = catalog_cart._download_catalog_image(
        "https://www.offiho.com/uploads/alufsen.png",
        tmp_path,
        "OHE-405",
        "offiho_cart",
    )

    assert response.socket.calls == 1
    assert result is not None
    assert result.read_bytes() == b"image-bytes"


def test_catalog_image_rejects_uninspectable_connected_peer(monkeypatch, tmp_path):
    response = _FakeImageResponse(include_peer=False)
    catalog_cart = _install_fake_image_opener(monkeypatch, response)

    result = catalog_cart._download_catalog_image(
        "https://www.offiho.com/uploads/alufsen.png",
        tmp_path,
        "OHE-405",
        "offiho_cart",
    )

    assert result is None
    assert not list(tmp_path.iterdir())


def test_catalog_image_redirect_validates_private_and_public_peers(monkeypatch):
    import mobiliti_saas.quote_engine.catalog_cart as catalog_cart

    monkeypatch.setattr(catalog_cart, "_resolve_public_host", lambda host: None)
    handler = catalog_cart._OfficialRedirectHandler(catalog_cart.OFFICIAL_IMAGE_HOSTS["offiho_cart"])
    request = catalog_cart.urllib.request.Request("https://www.offiho.com/start")
    private_response = _FakeImageResponse(peer_address="10.0.0.8")

    with pytest.raises(ValueError, match="publica"):
        handler.redirect_request(
            request,
            private_response,
            302,
            "Found",
            {},
            "https://www.offiho.com/uploads/alufsen.png",
        )

    public_response = _FakeImageResponse(peer_address="93.184.216.34")
    redirected = handler.redirect_request(
        request,
        public_response,
        302,
        "Found",
        {},
        "https://www.offiho.com/uploads/alufsen.png",
    )
    assert redirected.full_url == "https://www.offiho.com/uploads/alufsen.png"
    assert private_response.socket.calls == 1
    assert public_response.socket.calls == 1


def test_catalog_image_disables_environment_proxies(monkeypatch, tmp_path):
    import mobiliti_saas.quote_engine.catalog_cart as catalog_cart

    response = _FakeImageResponse(peer_address="93.184.216.34")
    captured_handlers = []

    class _FakeOpener:
        def open(self, request, timeout):
            return response

    def fake_build_opener(*handlers):
        captured_handlers.extend(handlers)
        return _FakeOpener()

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(catalog_cart, "_resolve_public_host", lambda host: None)
    monkeypatch.setattr(catalog_cart.urllib.request, "build_opener", fake_build_opener)

    result = catalog_cart._download_catalog_image(
        "https://www.offiho.com/uploads/alufsen.png",
        tmp_path,
        "OHE-405",
        "offiho_cart",
    )

    proxy_handlers = [
        handler
        for handler in captured_handlers
        if isinstance(handler, catalog_cart.urllib.request.ProxyHandler)
    ]
    assert result is not None
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}


def test_offiho_workbook_writes_price_and_warning(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 1}],
        catalog=fake_runtime_catalog(available_quantity=0, unit_price=7999),
    )
    output = create_offiho_quotation_workbook(payload, tmp_path / "offiho.xlsx")
    wb = load_workbook(output)
    ws = wb["Quotation"]

    assert ws["J9"].value == 7999
    assert "ADVERTENCIA: PRODUCTO AGOTADO" in ws["D9"].value
    assert ws["D9"].fill.fgColor.rgb.endswith("FFF2CC")
    wb.close()


def test_offiho_missing_price_warns_in_temporary_and_final_workbooks(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )
    from mobiliti_saas.worker.online_quote_generator import generate_online_quote

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 1}],
        catalog=fake_runtime_catalog(
            available_quantity=2,
            unit_price=0,
            price_source="missing",
        ),
    )
    assert payload["items"][0]["price_source"] == "missing"
    source = create_offiho_quotation_workbook(payload, tmp_path / "offiho-price-pending.xlsx")
    temporary = load_workbook(source)
    assert temporary["Quotation"]["J9"].value == 0
    assert "ADVERTENCIA: PRECIO POR CONFIRMAR" in temporary["Quotation"]["D9"].value
    assert temporary["Quotation"]["D9"].fill.fgColor.rgb.endswith("FFF2CC")
    temporary.close()

    output = tmp_path / "cotizacion-price-pending.xlsx"
    generate_online_quote(source, output, {"tipo_cambio": "20"})
    final = load_workbook(output, data_only=False)
    assert any(
        "ADVERTENCIA: PRECIO POR CONFIRMAR" in str(cell.value or "")
        for row in final["Cotizacion"].iter_rows()
        for cell in row
    )
    assert any(
        cell.value == 0
        for row in final["Quotation"].iter_rows()
        for cell in row
    )
    final.close()


def test_offiho_warning_survives_online_quote_generation(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )
    from mobiliti_saas.worker.online_quote_generator import generate_online_quote

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 1}],
        catalog=fake_runtime_catalog(available_quantity=0, unit_price=7999),
    )
    source = create_offiho_quotation_workbook(payload, tmp_path / "offiho.xlsx")
    output = tmp_path / "cotizacion.xlsx"

    generate_online_quote(source, output, {"tipo_cambio": "20"})

    wb = load_workbook(output, data_only=False)
    assert {"Cotizacion", "Mobiliti", "Quotation"}.issubset(wb.sheetnames)
    assert any(
        "ADVERTENCIA: PRODUCTO AGOTADO" in str(cell.value or "")
        for row in wb["Cotizacion"].iter_rows()
        for cell in row
    )
    wb.close()


def test_offiho_insufficient_warning_preserves_available_quantity_in_final_quote(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )
    from mobiliti_saas.worker.online_quote_generator import generate_online_quote

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 3}],
        catalog=fake_runtime_catalog(available_quantity=2, unit_price=7999),
    )
    source = create_offiho_quotation_workbook(payload, tmp_path / "offiho-insufficient.xlsx")
    output = tmp_path / "cotizacion-insufficient.xlsx"

    generate_online_quote(source, output, {"tipo_cambio": "20"})

    wb = load_workbook(output, data_only=False)
    warning_cells = [
        str(cell.value or "")
        for row in wb["Cotizacion"].iter_rows()
        for cell in row
        if "ADVERTENCIA: EXISTENCIA INSUFICIENTE" in str(cell.value or "").upper()
    ]
    assert warning_cells
    assert "DISPONIBLE 2" in warning_cells[0].upper()
    wb.close()


def test_final_cotizacion_fills_only_warning_descriptions_yellow(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        OffihoCatalogItem,
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )
    from mobiliti_saas.worker.online_quote_generator import generate_online_quote

    definitions = [
        ("OHE-501", "AGOTADO", 0, 7999, "inventory"),
        ("OHE-502", "PRECIO PENDIENTE", 2, 0, "missing"),
        ("OHE-503", "AGOTADO SIN PRECIO", 0, 0, "missing"),
        ("OHE-504", "NORMAL", 2, 7999, "inventory"),
    ]
    items = [
        OffihoCatalogItem(
            inventory_key=f"{code} NEGRO {name}",
            code=code,
            name=name,
            variant="NEGRO",
            unit="PZA",
            pieces_per_box=Decimal("1"),
            available_quantity=Decimal(str(stock)),
            unit_price=Decimal(str(price)),
            price_source=price_source,
        )
        for code, name, stock, price, price_source in definitions
    ]
    catalog = {
        "source_hash": "hash",
        "items": items,
        "by_inventory_key": {item.inventory_key: item for item in items},
    }
    payload = build_offiho_cart_payload(
        [{"inventory_key": item.inventory_key, "quantity": 1} for item in items],
        catalog=catalog,
    )
    source = create_offiho_quotation_workbook(payload, tmp_path / "offiho-all-warnings.xlsx")
    output = tmp_path / "cotizacion-all-warnings.xlsx"

    generate_online_quote(source, output, {"tipo_cambio": "20"})

    workbook = load_workbook(output, data_only=False)
    sheet = workbook["Cotizacion"]
    warning_cells = [cell for cell in sheet["C"] if "ADVERTENCIA:" in str(cell.value or "").upper()]
    assert len(warning_cells) == 3
    for code in ("OHE-501", "OHE-502", "OHE-503"):
        cell = next(cell for cell in warning_cells if code in str(cell.value or ""))
        assert cell.fill.fill_type == "solid"
        assert cell.fill.fgColor.rgb.endswith("FFF2CC")
    both = next(cell for cell in warning_cells if "OHE-503" in str(cell.value or ""))
    assert "ADVERTENCIA: PRODUCTO AGOTADO" in both.value
    assert "ADVERTENCIA: PRECIO POR CONFIRMAR" in both.value

    normal = next(cell for cell in sheet["C"] if "OHE-504" in str(cell.value or ""))
    assert not (
        normal.fill.fill_type == "solid"
        and str(normal.fill.fgColor.rgb or "").endswith("FFF2CC")
    )
    category = next(cell for cell in sheet["A"] if cell.value == "=Quotation!A8")
    assert not (
        category.fill.fill_type == "solid"
        and str(category.fill.fgColor.rgb or "").endswith("FFF2CC")
    )
    workbook.close()


def test_catalog_workbook_closes_when_save_fails(monkeypatch, tmp_path):
    import mobiliti_saas.quote_engine.catalog_cart as catalog_cart
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 1}],
        catalog=fake_runtime_catalog(available_quantity=2, unit_price=7999),
    )
    workbook = catalog_cart.Workbook()
    original_close = workbook.close
    close_calls = []

    def fail_save(path):
        raise OSError("save failed")

    def record_close():
        close_calls.append(True)
        original_close()

    monkeypatch.setattr(catalog_cart, "Workbook", lambda: workbook)
    monkeypatch.setattr(workbook, "save", fail_save)
    monkeypatch.setattr(workbook, "close", record_close)

    with pytest.raises(OSError, match="save failed"):
        catalog_cart.create_catalog_quotation_workbook(
            payload,
            tmp_path / "save-failure.xlsx",
            source_type="offiho_cart",
            category_label="Offiho",
            image_dir=tmp_path / "images",
        )

    assert close_calls == [True]


class _Sheet:
    nrows = 7

    def cell_value(self, row, column):
        values = {
            (5, 1): "OHE-405 NEGRO ALUFSEN",
            (5, 2): 252,
            (5, 3): 1,
            (5, 4): 7999,
            (6, 1): "OHE-406 GRIS ALUFSEN",
            (6, 2): 0,
            (6, 3): 1,
            (6, 4): 8100,
        }
        return values.get((row, column), "")


class _Workbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _Sheet()


class _DuplicateSheet(_Sheet):
    nrows = 8

    def cell_value(self, row, column):
        if row == 7:
            row = 5
        return super().cell_value(row, column)


class _DuplicateWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _DuplicateSheet()


class _ConflictingSheet(_DuplicateSheet):
    def cell_value(self, row, column):
        if row == 7 and column == 2:
            return 251
        return super().cell_value(row, column)


class _ConflictingWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _ConflictingSheet()


class _CorruptNumericSheet(_Sheet):
    def __init__(self, column, value):
        self.column = column
        self.value = value

    def cell_value(self, row, column):
        if row == 5 and column == self.column:
            return self.value
        return super().cell_value(row, column)


class _CorruptNumericWorkbook:
    def __init__(self, column, value):
        self.column = column
        self.value = value

    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(self.column, self.value)


class _SpecialOrderWorkbook:
    def __init__(self, status):
        self.status = status

    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(2, self.status)


class _SpecialOrderPiecesWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(3, "Sobre Pedido")


class _BlankStockWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(2, "")


class _RepeatedHeaderSheet(_Sheet):
    nrows = 8

    def cell_value(self, row, column):
        if row == 7:
            return {
                1: "CODIGO",
                2: "EXISTENCIA",
                3: "Piezas por Caja",
                4: "Precio",
            }.get(column, "")
        return super().cell_value(row, column)


class _RepeatedHeaderWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _RepeatedHeaderSheet()


@pytest.fixture
def html_inventory_xls(tmp_path):
    path = tmp_path / "offiho-html.xls"
    path.write_text(
        """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<table>
  <tr><th>CODIGO</th><th>Existencia</th><th>Piezas por Caja</th><th>Precio Lista 1</th></tr>
  <tr><td>OHE-405 NEGRO ALUFSEN</td><td>252</td><td>1</td><td>7,999</td></tr>
  <tr><td>OHE-406 GRIS ALUFSEN</td><td>0</td><td>2</td><td></td></tr>
</table>
</body></html>""",
        encoding="utf-8",
    )
    return path


def test_parse_inventory_keeps_available_and_exhausted_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _Workbook())

    rows = parse_inventory_xls(tmp_path / "offiho-small.xls")

    assert rows[0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"
    assert rows[0]["available_quantity"] == Decimal("252")
    assert rows[0]["unit_price"] == Decimal("7999")
    assert any(row["available_quantity"] == 0 for row in rows)


def test_parse_inventory_removes_materially_identical_duplicate_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _DuplicateWorkbook())

    rows = parse_inventory_xls(tmp_path / "offiho-duplicates.xls")

    assert len(rows) == 2
    assert [row["inventory_key"] for row in rows] == [
        "OHE-405 NEGRO ALUFSEN",
        "OHE-406 GRIS ALUFSEN",
    ]


def test_parse_inventory_rejects_duplicate_key_with_different_data(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _ConflictingWorkbook())

    with pytest.raises(RuntimeError, match="OHE-405 NEGRO ALUFSEN.*datos distintos"):
        parse_inventory_xls(tmp_path / "offiho-conflict.xls")


def test_build_catalog_reports_deduplicated_inventory_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _DuplicateWorkbook())
    monkeypatch.setattr(build, "parse_pdf_price_index", lambda paths: {})
    monkeypatch.setattr(build, "build_site_product_index", lambda cache, **kwargs: {})
    inventory = tmp_path / "offiho-duplicates.xls"
    inventory.write_bytes(b"xls")

    catalog = build.build_catalog(
        inventory,
        [],
        tmp_path / "cache.json",
        tmp_path / "catalog.json",
    )

    assert catalog["source_row_count"] == 3
    assert catalog["duplicate_row_count"] == 1
    assert catalog["unique_item_count"] == 2
    assert catalog["total"] == 2
    assert catalog["out_of_stock"] == 1
    assert catalog["inventory_prices"] == 2


def test_parse_inventory_supports_html_disguised_as_xls(html_inventory_xls):
    rows = parse_inventory_xls(html_inventory_xls)

    assert len(rows) == 2
    assert rows[0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"
    assert rows[0]["available_quantity"] == 252
    assert rows[0]["unit_price"] == 7999
    assert rows[1]["available_quantity"] == 0
    assert rows[1]["price_source"] == "missing"


@pytest.mark.parametrize(
    ("column", "column_name", "field"),
    [
        (2, "C", "Existencia"),
        (3, "D", "Piezas por Caja"),
        (4, "E", "Precio Lista 1"),
    ],
)
def test_parse_inventory_rejects_corrupt_numeric_values_with_location(
    monkeypatch, tmp_path, column, column_name, field
):
    monkeypatch.setattr(
        build.xlrd,
        "open_workbook",
        lambda path: _CorruptNumericWorkbook(column, "corrupto"),
    )

    with pytest.raises(RuntimeError, match=rf"Fila 6.*columna {column_name}.*{field}"):
        parse_inventory_xls(tmp_path / "offiho-corrupt.xls")


@pytest.mark.parametrize("status", ["SOBRE PEDIDO", "Consultar Existencias"])
def test_parse_inventory_audits_recognized_nonquantitative_stock(monkeypatch, tmp_path, status):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _SpecialOrderWorkbook(status))

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-special-order.xls")

    assert [row["inventory_key"] for row in rows] == ["OHE-406 GRIS ALUFSEN"]
    assert audit["excluded_stock_status_count"] == 1


def test_parse_inventory_audits_special_order_pieces_default(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _SpecialOrderPiecesWorkbook())

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-special-order-pieces.xls")

    assert rows[0]["pieces_per_box"] == 1
    assert audit["defaulted_pieces_status_count"] == 1


def test_parse_inventory_audits_blank_stock_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _BlankStockWorkbook())

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-blank-stock.xls")

    assert [row["inventory_key"] for row in rows] == ["OHE-406 GRIS ALUFSEN"]
    assert audit["excluded_blank_stock_count"] == 1


def test_parse_inventory_audits_repeated_header_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _RepeatedHeaderWorkbook())

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-repeated-header.xls")

    assert len(rows) == 2
    assert audit["excluded_header_row_count"] == 1


def test_extract_identity_separates_model_name_and_variant():
    identity = extract_offiho_identity("OHE-405 NEGRO ALUFSEN")

    assert identity.code == "OHE-405"
    assert identity.name == "ALUFSEN"
    assert identity.variant == "NEGRO"


def test_pdf_price_index_normalizes_compact_variant(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "extract_pdf_pages", lambda paths: ["ALUFSEN OHE-405 negro $ 7,999"])

    prices = parse_pdf_price_index([tmp_path / "prices.pdf"])

    assert prices["OHE-405 NEGRO"] == Decimal("7999")


def test_site_match_requires_expected_model_code():
    product = match_official_product(
        extract_offiho_identity("OHE-405 NEGRO ALUFSEN"),
        [
            {
                "codes": ["OHE-405"],
                "url": "https://www.offiho.com/directivos/alufsen",
                "image_url": "https://www.offiho.com/alufsen.jpg",
            }
        ],
    )

    assert product["url"].endswith("/directivos/alufsen")
    assert product["image_url"] == ""


def test_image_extraction_never_uses_page_url_for_empty_candidate():
    page_url = "https://www.offiho.com/directivos/alufsen"
    parser = build._PageParser()

    image_url = build._extract_official_image_url(page_url, parser)

    assert image_url == ""
    assert image_url != page_url


def test_image_extraction_accepts_realistic_relative_og_image():
    page_url = "https://www.offiho.com/directivos/alufsen"
    parser = build._PageParser()
    parser.feed(
        '<meta property="og:image" '
        'content="/wp-content/uploads/2026/07/alufsen-negra.jpg?size=large">'
    )

    image_url = build._extract_official_image_url(page_url, parser)

    assert image_url == "https://www.offiho.com/wp-content/uploads/2026/07/alufsen-negra.jpg?size=large"


def test_image_extraction_prefers_ciao_product_front_over_branding_assets():
    page_url = "https://www.offiho.com/econosillas/ciao"
    parser = build._PageParser()
    parser.feed(
        '<img src="/images/logo-econosillas.png">'
        '<img src="/images/logociao.jpg">'
        '<img src="/uploads/precios-lista.jpg">'
        '<img src="/uploads/OHS-12CB/OHS-12B-Negro-frente.jpg">'
        '<img src="/uploads/OHS-12CB/OHS-12B-Negro-lateral.jpg">'
    )

    image_url = build._extract_official_image_url(page_url, parser, codes=["OHS-12B"])

    assert image_url == "https://www.offiho.com/uploads/OHS-12CB/OHS-12B-Negro-frente.jpg"


def test_image_extraction_rejects_logo_menu_social_and_accessory_candidates():
    page_url = "https://www.offiho.com/econosillas/ciao"
    parser = build._PageParser()
    parser.feed(
        '<img src="/images/logo.png">'
        '<img src="/images/menu-icon.png">'
        '<img src="/images/instagram-social.png">'
        '<img src="/images/garantia.jpg">'
        '<img src="/images/caja-accesorio.jpg">'
    )

    assert build._extract_official_image_url(page_url, parser, codes=["OHS-12B"]) == ""


def test_image_extraction_uses_shopify_product_srcset_over_header_logo():
    page_url = "https://www.offihoblack.com/products/vanto-ohe-75"
    parser = build._PageParser()
    parser.feed(
        '<img src="/cdn/shop/files/OffihoBlack_Logo.png">'
        '<source srcset="/cdn/shop/products/VantoEFrente_1800x1800.jpg?v=1 1800w">'
    )

    image_url = build._extract_official_image_url(page_url, parser, codes=["OHE-75"])

    assert image_url == "https://www.offihoblack.com/cdn/shop/products/VantoEFrente_1800x1800.jpg?v=1"


def test_discovery_prioritizes_canonical_shopify_product_pages():
    urls = build._prioritize_product_pages(
        {
            "https://www.offihoblack.com/collections/all",
            "https://www.offihoblack.com/collections/all/products/vanto-ohe-75?variant=123",
            "https://www.offihoblack.com/products/shine-ohv-80",
            "https://www.offiho.com/operativos/ciao/operativos-ciao-modelo-OHS-12CB",
        }
    )

    assert urls[:3] == [
        "https://www.offihoblack.com/products/shine-ohv-80",
        "https://www.offihoblack.com/products/vanto-ohe-75",
        "https://www.offiho.com/operativos/ciao/operativos-ciao-modelo-OHS-12CB",
    ]


def test_official_link_normalization_escapes_spaces_before_fetching():
    assert build._normalize_official_link("https://www.offiho.com/3d/WAY OHV-58_DWG.dwg") == (
        "https://www.offiho.com/3d/WAY%20OHV-58_DWG.dwg"
    )


def test_shopify_asset_path_is_not_canonicalized_or_crawled_as_product_page():
    asset = "https://www.offihoblack.com/cdn/shop/products/VantoEFrente_1800x1800.jpg"

    assert build._canonical_product_url(asset) == asset
    assert build._is_official_page_url(asset) is False


def test_image_extraction_ranks_shopify_gallery_link_as_product_image():
    page_url = "https://www.offihoblack.com/products/amelia-ohm-41001"
    parser = build._PageParser()
    parser.feed('<img src="/cdn/shop/files/OffihoBlack_Logo.png">')
    gallery_url = "https://www.offihoblack.com/cdn/shop/files/Amelia-Frente41001_1800x1800.jpg?v=1"

    image_url = build._extract_official_image_url(
        page_url,
        parser,
        codes=["OHM-41001"],
        extra_candidates=[gallery_url],
    )

    assert image_url == gallery_url


def test_image_extraction_rejects_shopify_width_template_before_real_photo():
    page_url = "https://www.offihoblack.com/products/amelia-ohm-41001"
    parser = build._PageParser()
    parser.feed(
        '<meta property="og:image" content="/cdn/shop/files/AMELIA_OHM-41001_{width}x.jpg">'
        '<img src="/cdn/shop/files/Amelia-Frente41001_1800x1800.jpg">'
    )

    image_url = build._extract_official_image_url(page_url, parser, codes=["OHM-41001"])

    assert image_url == "https://www.offihoblack.com/cdn/shop/files/Amelia-Frente41001_1800x1800.jpg"


def _mock_image_response(url, content_type, content_length):
    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = content_type
            self.headers["Content-Length"] = str(content_length)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return url

    return _Response()


def test_image_verification_rejects_jpg_served_as_html(monkeypatch):
    image_url = "https://www.offiho.com/uploads/product.jpg"
    monkeypatch.setattr(
        build,
        "_open_official",
        lambda request, timeout: _mock_image_response(image_url, "text/html", 512),
    )

    verification = build._verify_official_image(image_url)

    assert verification["image_verified"] is False
    assert verification["image_url"] == ""


def test_image_verification_accepts_valid_image_content_type_and_size(monkeypatch):
    image_url = "https://www.offiho.com/uploads/product.jpg"
    seen = []

    def fake_open(request, *, timeout):
        seen.append((request.get_method(), request.full_url, timeout))
        return _mock_image_response(image_url, "image/jpeg", 2048)

    monkeypatch.setattr(build, "_open_official", fake_open)

    verification = build._verify_official_image(image_url)

    assert verification == {
        "image_url": image_url,
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 2048,
    }
    assert seen == [("HEAD", image_url, 10)]


def test_redirect_handler_blocks_external_location_before_request():
    contacted = []
    handler = build._OfficialRedirectHandler()

    class _Parent:
        def open(self, request, timeout=None):
            contacted.append(request.full_url)
            raise AssertionError("external redirect target must not be contacted")

    handler.parent = _Parent()
    headers = Message()
    headers["Location"] = "https://attacker.example/collect"
    request = build.urllib.request.Request("https://www.offiho.com/start")

    with pytest.raises(ValueError, match="redireccion.*host oficial"):
        handler.http_error_302(request, object(), 302, "Found", headers)

    assert contacted == []


def test_download_inventory_uses_validated_mocked_response(monkeypatch, tmp_path):
    payload = b"mock-biff-inventory"
    seen = []

    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "application/vnd.ms-excel"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://www.offiho.com/existencias.xls"

        def read(self, size):
            return payload

    def fake_open(request, *, timeout):
        seen.append((request.full_url, timeout))
        return _Response()

    monkeypatch.setattr(build, "_open_official", fake_open)
    output = tmp_path / "inventory.xls"

    result = build.download_inventory("https://www.offiho.com/existencias.xls", output)

    assert result == output
    assert output.read_bytes() == payload
    assert seen == [("https://www.offiho.com/existencias.xls", 30)]


def _mock_download_response(payload, content_type):
    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = content_type

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://www.offiho.com/existencias.xls"

        def read(self, size):
            return payload if len(payload) <= size else payload[:size]

    return _Response()


def test_download_inventory_accepts_valid_html_table_mime(monkeypatch, tmp_path):
    payload = b"""<!DOCTYPE html><html><body><table>
<tr><th>CODIGO</th><th>Existencia</th><th>Piezas por Caja</th><th>Precio Lista 1</th></tr>
<tr><td>OHE-405 NEGRO ALUFSEN</td><td>252</td><td>1</td><td>7999</td></tr>
</table></body></html>"""
    monkeypatch.setattr(
        build,
        "_open_official",
        lambda request, timeout: _mock_download_response(payload, "text/html; charset=utf-8"),
    )
    output = tmp_path / "inventory-html.xls"

    build.download_inventory("https://www.offiho.com/existencias.xls", output)

    assert output.read_bytes() == payload
    assert parse_inventory_xls(output)[0]["available_quantity"] == 252


def test_download_inventory_rejects_arbitrary_html_landing(monkeypatch, tmp_path):
    payload = b"<html><body><h1>Maintenance</h1></body></html>"
    monkeypatch.setattr(
        build,
        "_open_official",
        lambda request, timeout: _mock_download_response(payload, "text/html"),
    )
    output = tmp_path / "landing.xls"

    with pytest.raises(ValueError, match="HTML.*inventario"):
        build.download_inventory("https://www.offiho.com/existencias.xls", output)

    assert not output.exists()


def test_download_inventory_rejects_payload_over_limit(monkeypatch, tmp_path):
    payload = b"x" * (10 * 1024 * 1024 + 1)
    monkeypatch.setattr(
        build,
        "_open_official",
        lambda request, timeout: _mock_download_response(payload, "application/vnd.ms-excel"),
    )
    output = tmp_path / "oversized.xls"

    with pytest.raises(ValueError, match="excede el limite"):
        build.download_inventory("https://www.offiho.com/existencias.xls", output)

    assert not output.exists()


def _build_catalog_with_sources(monkeypatch, tmp_path, label, *, pdf_payload, site_index):
    inventory = tmp_path / f"inventory-{label}.xls"
    pdf = tmp_path / f"prices-{label}.pdf"
    inventory.write_bytes(b"same inventory bytes")
    pdf.write_bytes(pdf_payload)
    item = {
        "inventory_key": "OHE-405 NEGRO ALUFSEN",
        "code": "OHE-405",
        "name": "ALUFSEN",
        "variant": "NEGRO",
        "unit": "PZA",
        "pieces_per_box": 1,
        "available_quantity": 252,
        "unit_price": 7999,
        "price_source": "inventory",
    }
    monkeypatch.setattr(
        build,
        "_parse_inventory_xls",
        lambda path: ([dict(item)], {"source_row_count": 1, "duplicate_row_count": 0, "unique_item_count": 1}),
    )
    monkeypatch.setattr(build, "parse_pdf_price_index", lambda paths: {})
    monkeypatch.setattr(build, "build_site_product_index", lambda cache, **kwargs: site_index)
    return build.build_catalog(
        inventory,
        [pdf],
        tmp_path / f"cache-{label}.json",
        tmp_path / f"catalog-{label}.json",
    )


def test_source_hash_covers_pdf_and_site_snapshot_with_provenance(monkeypatch, tmp_path):
    site_a = {
        "OHE-405": {
            "url": "https://www.offiho.com/directivos/alufsen",
            "image_url": "https://www.offiho.com/uploads/alufsen.jpg",
            "source_updated_at": "Wed, 08 Jul 2026 12:00:00 GMT",
        }
    }
    site_b = {
        "OHE-405": {
            **site_a["OHE-405"],
            "image_url": "https://www.offiho.com/uploads/alufsen-v2.jpg",
        }
    }

    first = _build_catalog_with_sources(monkeypatch, tmp_path, "first", pdf_payload=b"pdf-a", site_index=site_a)
    same = _build_catalog_with_sources(monkeypatch, tmp_path, "same", pdf_payload=b"pdf-a", site_index=site_a)
    changed_pdf = _build_catalog_with_sources(
        monkeypatch, tmp_path, "changed-pdf", pdf_payload=b"pdf-b", site_index=site_a
    )
    changed_site = _build_catalog_with_sources(
        monkeypatch, tmp_path, "changed-site", pdf_payload=b"pdf-a", site_index=site_b
    )

    assert first["source_hash"] == same["source_hash"]
    assert first["source_hash"] != changed_pdf["source_hash"]
    assert first["source_hash"] != changed_site["source_hash"]
    assert first["sources"]["inventory"]["sha256"]
    assert first["sources"]["pdfs"][0]["sha256"]
    assert first["sources"]["site_index"]["sha256"]
    assert first["sources"]["site_index"]["cache_version"] == build.CACHE_VERSION


def test_build_catalog_is_byte_reproducible_for_identical_sources(monkeypatch, tmp_path):
    inventory = tmp_path / "inventory.xls"
    inventory.write_bytes(b"identical inventory")
    item = {
        "inventory_key": "OHE-405 NEGRO ALUFSEN",
        "code": "OHE-405",
        "name": "ALUFSEN",
        "variant": "NEGRO",
        "unit": "PZA",
        "pieces_per_box": 1,
        "available_quantity": 252,
        "unit_price": 7999,
        "price_source": "inventory",
    }
    audit = {"source_row_count": 1, "duplicate_row_count": 0, "unique_item_count": 1}
    monkeypatch.setattr(build, "_parse_inventory_xls", lambda path: ([dict(item)], dict(audit)))
    monkeypatch.setattr(build, "parse_pdf_price_index", lambda paths: {})
    monkeypatch.setattr(build, "build_site_product_index", lambda cache, **kwargs: {})
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"

    first = build.build_catalog(inventory, [], tmp_path / "cache.json", first_path)
    second = build.build_catalog(inventory, [], tmp_path / "cache.json", second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["generated_at"] == second["generated_at"]
    assert datetime.fromisoformat(first["generated_at"]).tzinfo is not None


def test_no_network_uses_compatible_cache_without_refresh(monkeypatch):
    product = {
        "url": "https://www.offiho.com/directivos/alufsen",
        "image_url": "https://www.offiho.com/uploads/alufsen.jpg",
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 2048,
        "source_updated_at": "Wed, 08 Jul 2026 12:00:00 GMT",
    }
    cache = {
        "cache_version": build.CACHE_VERSION,
        "site_index": {"OHE-405": product},
        "site_index_expires_at": "2000-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(
        build,
        "_fetch_official_page",
        lambda url: (_ for _ in ()).throw(AssertionError("network must remain disabled")),
    )

    index = build.build_site_product_index(cache, no_network=True)

    assert index["OHE-405"] == product


def test_no_network_discards_unverified_cache_image():
    cache = {
        "cache_version": build.CACHE_VERSION,
        "site_index": {
            "OHE-405": {
                "url": "https://www.offiho.com/directivos/alufsen",
                "image_url": "https://www.offiho.com/uploads/alufsen.jpg",
                "source_updated_at": "",
            }
        },
        "site_index_expires_at": "2000-01-01T00:00:00+00:00",
    }

    index = build.build_site_product_index(cache, no_network=True)

    assert index["OHE-405"]["image_url"] == ""
    assert index["OHE-405"]["image_verified"] is False
    assert cache["site_index"]["OHE-405"]["image_verified"] is False
    assert cache["site_index"]["OHE-405"]["image_content_type"] == ""


def test_no_network_deterministically_migrates_legacy_snapshot(monkeypatch):
    page_url = "https://www.offiho.com/directivos/alufsen"
    cache = {
        "site_index": {
            "OHE-405": {
                "url": page_url,
                "image_url": "https://www.offiho.com/uploads/unverified.jpg",
                "source_updated_at": "",
            }
        }
    }
    monkeypatch.setattr(
        build,
        "_fetch_official_page",
        lambda url: (_ for _ in ()).throw(AssertionError("network must remain disabled")),
    )

    index = build.build_site_product_index(cache, no_network=True)

    assert cache["cache_version"] == build.CACHE_VERSION
    assert cache["site_index_created_at"] == build.LEGACY_CACHE_TIMESTAMP
    assert index["OHE-405"]["image_url"] == ""


def test_no_network_rejects_incompatible_cache_version():
    cache = {"cache_version": -1, "site_index": {}}

    with pytest.raises(RuntimeError, match="version.*cache"):
        build.build_site_product_index(cache, no_network=True)
