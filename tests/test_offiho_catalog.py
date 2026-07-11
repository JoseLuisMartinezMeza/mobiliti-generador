from decimal import Decimal
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit
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
    description: str = "",
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
        description=description,
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


def test_offiho_description_reaches_cart_and_quotation_workbook(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )

    description = "Estructura de acero tubular y asiento de polipropileno de alta resistencia."
    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": 2}],
        catalog=fake_runtime_catalog(
            available_quantity=5,
            unit_price=7999,
            description=description,
        ),
    )
    output = tmp_path / "offiho-description.xlsx"
    create_offiho_quotation_workbook(payload, output)

    assert payload["items"][0]["description"] == description
    workbook = load_workbook(output, read_only=True)
    assert description in workbook["Quotation"]["D9"].value
    workbook.close()

    from mobiliti_saas.worker.online_quote_generator import generate_online_quote

    final_output = tmp_path / "offiho-description-final.xlsx"
    generate_online_quote(output, final_output, {"tipo_cambio": "20"})
    final = load_workbook(final_output, read_only=True)
    assert any(
        description in str(cell.value or "")
        for row in final["Cotizacion"].iter_rows()
        for cell in row
    )
    final.close()


def test_exact_offiho_finish_image_reaches_cart_and_quotation_workbook(monkeypatch, tmp_path):
    from PIL import Image

    from mobiliti_saas.quote_engine import catalog_cart
    from mobiliti_saas.quote_engine.offiho_catalog import (
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
        load_offiho_catalog,
    )

    payload = build_offiho_cart_payload(
        [{"inventory_key": "OHS-86AL ROJA REVOLUTION", "quantity": 1}],
        catalog=load_offiho_catalog(),
    )
    expected_url = (
        "https://www.offiho.com/operativos/revolution/"
        "OHS-86al/colores/OHS-86alRojo.jpg"
    )
    captured = {}

    def fake_download(image_url, image_dir, code, source_type):
        captured.update(url=image_url, code=code, source_type=source_type)
        image_path = image_dir / "revolution-roja.png"
        Image.new("RGB", (40, 60), "red").save(image_path)
        return image_path

    monkeypatch.setattr(catalog_cart, "_download_catalog_image", fake_download)

    output = create_offiho_quotation_workbook(payload, tmp_path / "revolution-roja.xlsx")
    workbook = load_workbook(output)

    assert payload["items"][0]["image_url"] == expected_url
    assert captured == {
        "url": expected_url,
        "code": "OHS-86AL",
        "source_type": "offiho_cart",
    }
    assert len(workbook["Quotation"]._images) == 1
    workbook.close()


def test_offiho_cart_uses_inventory_key_when_variant_name_is_blank(tmp_path):
    from mobiliti_saas.quote_engine.offiho_catalog import (
        OffihoCatalogItem,
        build_offiho_cart_payload,
        create_offiho_quotation_workbook,
    )
    from mobiliti_saas.worker.online_quote_generator import generate_online_quote

    item = OffihoCatalogItem(
        inventory_key="VESPER/05 NEGRA",
        code="VESPER/05",
        name="",
        variant="NEGRA",
        unit="PZA",
        pieces_per_box=Decimal("1"),
        available_quantity=Decimal("312"),
        unit_price=Decimal("2799"),
        description="Estructura de acero tubular y asiento de polipropileno.",
    )
    catalog = {
        "source_hash": "hash",
        "items": [item],
        "by_inventory_key": {item.inventory_key: item},
    }
    payload = build_offiho_cart_payload(
        [{"inventory_key": item.inventory_key, "quantity": 1}],
        catalog=catalog,
    )
    source = create_offiho_quotation_workbook(payload, tmp_path / "vesper-source.xlsx")
    final_path = tmp_path / "vesper-final.xlsx"
    generate_online_quote(source, final_path, {"tipo_cambio": "20"})

    final = load_workbook(final_path, read_only=True)
    assert final["Quotation"]["B9"].value == "VESPER NEGRA"
    assert "modelo VESPER NEGRA" in final["Cotizacion"]["C17"].value
    final.close()
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

    assert catalog["source_row_count"] == 1287
    assert catalog["duplicate_row_count"] == 80
    assert catalog["unique_item_count"] == 1207


def test_checked_in_offiho_catalog_keeps_official_media_coverage():
    raw = _runtime_catalog_raw()
    items = raw["items"]
    linked = [item for item in items if item.get("product_url")]
    imaged = [item for item in items if item.get("image_url")]
    official_hosts = {
        "offiho.com",
        "www.offiho.com",
        "offihoblack.com",
        "www.offihoblack.com",
        "web-lemon-one-45.vercel.app",
    }

    assert len(items) == 1207
    assert len(linked) >= 850
    assert len(imaged) >= 750
    assert all(item.get("product_url") for item in imaged)
    for item in linked:
        fields = ("product_url", "image_url") if item.get("image_url") else ("product_url",)
        for field in fields:
            parsed = urlsplit(item[field])
            assert parsed.scheme == "https"
            assert parsed.hostname in official_hosts
            if parsed.hostname == "web-lemon-one-45.vercel.app":
                assert parsed.path.startswith("/catalog-assets/offiho/")
        if item.get("image_url"):
            assert item["product_url"] != item["image_url"]


def test_checked_in_revolution_variants_use_their_exact_official_color_image():
    items = [
        item
        for item in _runtime_catalog_raw()["items"]
        if item.get("code") == "OHS-86AL"
    ]
    expected_suffixes = {
        "BLANCO": "OHS-86alBlanco.jpg",
        "ARENA": "OHS-86alArena.jpg",
        "GRIS": "OHS-86alGris.jpg",
        "NEGRO": "OHS-86alNegro.jpg",
        "VERDE": "OHS-86alVerde.jpg",
        "AZUL MARINO": "OHS-86alMarino.jpg",
        "ROJA": "OHS-86alRojo.jpg",
        "NARANJA": "OHS-86alNaranja.jpg",
    }

    assert {item["variant"] for item in items} == set(expected_suffixes)
    for item in items:
        assert item["image_url"].endswith(expected_suffixes[item["variant"]])
    assert len({item["image_url"] for item in items}) == len(expected_suffixes)


def test_checked_in_kyos_and_sling_rows_keep_exact_official_model_images():
    catalog = json.loads(build.DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    items = catalog["items"]

    expected_model_paths = {
        "OHV-331": "/galeria/OHV-331.jpg",
        "OHV-335CR": "/galeria/OHV-335cr.jpg",
        "OHV-337": "/galeria/OHT-337.jpg",
    }
    for code, expected_path in expected_model_paths.items():
        rows = [item for item in items if item["code"] == code]
        assert rows
        assert all(item["image_url"].endswith(expected_path) for item in rows)

    sling = {
        (item["code"], item["variant"]): item
        for item in items
        if item["code"] in {"OHE-94", "OHV-94"}
    }
    assert sling[("OHE-94", "PLUS GRIS")]["image_url"].endswith("/OHE-94plusGris.jpg")
    assert sling[("OHE-94", "PLUS NEGRO")]["image_url"].endswith("/OHE-94plusnegro.jpg")
    assert sling[("OHV-94", "PLUS GRIS")]["image_url"].endswith("/OHV-94plusGris.jpg")
    assert sling[("OHV-94", "PLUS NEGRO")]["image_url"].endswith("/OHV-94plus.jpg")


def test_checked_in_official_images_never_belong_to_another_structured_code():
    official_hosts = {
        "offiho.com",
        "www.offiho.com",
        "offihoblack.com",
        "www.offihoblack.com",
    }
    mismatches = []
    for item in _runtime_catalog_raw()["items"]:
        image_url = item.get("image_url", "")
        if urlsplit(image_url).hostname not in official_hosts:
            continue
        if build.CODE_RE.fullmatch(item.get("code", "")) is None:
            continue
        identity = extract_offiho_identity(item["inventory_key"])
        product_url = item.get("product_url", "")
        curated_name_match = (
            item.get("match_status") == "official_name_match"
            and identity.code in build.OFFICIAL_NAME_ALIASES
        )
        if not (
            build._image_targets_identity(image_url, identity)
            or build._image_targets_identity(product_url, identity)
            or curated_name_match
        ):
            mismatches.append((item["inventory_key"], image_url))

    assert mismatches == []


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


def test_offiho_catalog_accepts_only_production_pdf_asset_prefix():
    from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem

    raw = _runtime_catalog_raw()["items"][0]
    raw["product_url"] = (
        "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
        "lp-black-colos-jul2026.pdf#page=15"
    )
    raw["image_url"] = (
        "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/images/vesper-1-15.jpg"
    )
    assert OffihoCatalogItem.from_dict(raw).image_url.endswith("vesper-1-15.jpg")

    raw["image_url"] = "https://web-lemon-one-45.vercel.app/arbitrary/user-content.jpg"
    with pytest.raises(ValueError, match="image_url"):
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
    monkeypatch.setattr(build, "parse_pdf_product_index", lambda paths, items, assets, base_url: {})
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


def test_page_description_extracts_only_official_product_description():
    page_text = """
    FENIX OHE-165 NEGRO
    DESCRIPCIÓN
    base: de nylon con rodajas.
    elevación: pistón neumático.
    mecanismo: reclinable.
    Accesorios opcionales
    Cabecera
    Modelados 3D
    Descargar
    """

    description = build._page_description(page_text)

    assert description == "base: de nylon con rodajas. elevación: pistón neumático. mecanismo: reclinable."


def test_real_black_pdf_index_maps_vesper_family_to_pdf_asset(tmp_path):
    project = Path(__file__).resolve().parents[1]
    pdf_path = project / "LP BLACK®️ & COLOS®️ JUL2026.pdf"
    if not pdf_path.exists():
        pytest.skip("El PDF BLACK/COLOS no esta disponible en este checkout")
    items = [
        {
            "inventory_key": "VESPER 1",
            "code": "VESPER",
            "name": "1",
            "variant": "",
        },
        {
            "inventory_key": "VESPER/05 NEGRA",
            "code": "VESPER/05",
            "name": "VESPER/05 NEGRA",
            "variant": "NEGRA",
        },
    ]

    index = build.parse_pdf_product_index(
        [pdf_path],
        items,
        tmp_path / "assets",
        "https://web-lemon-one-45.vercel.app/catalog-assets/offiho",
    )

    product = index["VESPER/05 NEGRA"]
    assert product["unit_price"] == Decimal("2799")
    assert "acero tubular" in product["description"].casefold()
    assert product["image_url"].startswith(
        "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/images/"
    )
    assert product["product_url"].endswith("lp-black-colos-jul2026.pdf#page=15")
    assert (tmp_path / "assets" / "lp-black-colos-jul2026.pdf").exists()
    assert list((tmp_path / "assets" / "images").glob("vesper-1*"))


def test_pdf_title_match_uses_longest_family_prefix_for_color_variants():
    records = [
        {"title": "PIAZZA 1", "image_name": "piazza-1"},
        {"title": "PIAZZA 3A", "image_name": "piazza-3a"},
        {"title": "VILLA 3", "image_name": "villa-3"},
    ]

    product = build._title_record_for_inventory("PIAZZA 3A N NEGRO", records)

    assert product is not None
    assert product["image_name"] == "piazza-3a"


def test_pdf_title_match_does_not_cross_product_families():
    records = [{"title": "PIAZZA 1", "image_name": "piazza-1"}]

    assert build._title_record_for_inventory("VILLA 1 N NEGRO", records) is None


@pytest.mark.parametrize(
    ("inventory_key", "asset_name", "page"),
    [
        ("ECOGERENCIAL NEGRO", "econosillas-ecogerencial.jpg", 6),
        ("ECONOMALLA BLANCO", "econosillas-economalla.jpg", 11),
        ("ECOVISITA VISITA", "econosillas-ecovisita.jpg", 16),
        ("ISO SIN BRAZOS BRAZOS", "econosillas-iso-sin-brazos.jpg", 17),
        ("ISO CON BRAZOS BRAZOS", "econosillas-iso-con-brazos.jpg", 18),
        ("NOVAISO SIN BRAZOS NEGRO", "econosillas-novaiso-sin-brazos.jpg", 19),
        ("NOVAISO CON BRAZOS AZUL", "econosillas-novaiso-con-brazos.jpg", 20),
        ("OHV-64 BLANCO SAND", "econosillas-sand.jpg", 26),
    ],
)
def test_official_brochure_fallback_maps_verified_product_family(
    tmp_path, inventory_key, asset_name, page
):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / asset_name).write_bytes(b"verified brochure image")

    product = build.match_official_brochure_product(
        {"inventory_key": inventory_key},
        tmp_path,
        "https://web-lemon-one-45.vercel.app/catalog-assets/offiho",
    )

    assert product["match_status"] == "official_brochure_match"
    assert product["image_url"].endswith(f"/images/{asset_name}")
    assert product["product_url"] == f"https://www.offiho.com/folletoeconosillas.pdf#page={page}"


def test_official_brochure_fallback_rejects_unmapped_or_missing_asset(tmp_path):
    assert build.match_official_brochure_product(
        {"inventory_key": "PRODUCTO SIN MAPA"}, tmp_path, "https://example.test/assets"
    ) == {}
    assert build.match_official_brochure_product(
        {"inventory_key": "OHV-64 BLANCO SAND"}, tmp_path, "https://example.test/assets"
    ) == {}


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


def test_site_match_uses_exact_name_link_without_borrowing_an_unverified_model_image():
    product = match_official_product(
        extract_offiho_identity("OHE-405 NEGRO ALUFSEN"),
        [
            {
                "codes": ["OHE-999"],
                "names": ["Alufsen"],
                "url": "https://www.offiho.com/directivos/alufsen/",
                "image_url": "https://www.offiho.com/images/alufsen-frente.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
                "description": "Respaldo de malla y asiento tapizado.",
            }
        ],
    )

    assert product["url"] == "https://www.offiho.com/directivos/alufsen/"
    assert product["image_url"] == ""
    assert product["match_status"] == "official_name_match"
    assert product["description"] == "Respaldo de malla y asiento tapizado."


def test_site_match_prefers_exact_code_over_name_fallback():
    product = match_official_product(
        extract_offiho_identity("OHE-405 NEGRO ALUFSEN"),
        [
            {
                "codes": ["OHE-999"],
                "names": ["ALUFSEN"],
                "url": "https://www.offiho.com/directivos/alufsen/",
            },
            {
                "codes": ["OHE-405"],
                "names": ["ALUFSEN"],
                "url": "https://www.offiho.com/directivos/alufsen/directivos-alufsen-modelo-OHE-405",
            },
        ],
    )

    assert product["url"].endswith("modelo-OHE-405")
    assert product["match_status"] == "official_code_match"


def test_site_match_accepts_official_code_with_inventory_variant_suffix():
    product = match_official_product(
        extract_offiho_identity("OHE-165 NEGRO FENIX"),
        [
            {
                "codes": ["OHE-165NEGRO"],
                "names": ["FENIX"],
                "url": "https://www.offiho.com/directivos/fenix/directivos-fenix-modelo-OHE-165negro",
                "image_url": "https://www.offiho.com/directivos/fenix/OHE-165/OHE-165negroFrente.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["url"].endswith("modelo-OHE-165negro")
    assert product["image_url"].endswith("OHE-165negroFrente.jpg")
    assert product["match_status"] == "official_code_match"


def test_site_match_accepts_compound_variant_suffix_from_official_code():
    image_url = (
        "https://www.offiho.com/operativos/slingplus/"
        "OHE-94plusgris/colores/OHE-94plusGris.jpg"
    )
    metadata = {
        "image_url": image_url,
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 57907,
    }

    product = build.match_official_product(
        extract_offiho_identity("OHE-94 PLUS GRIS SLING"),
        [
            {
                "url": (
                    "https://www.offiho.com/operativos/slingplus/"
                    "operativos-sling-modelo-OHE-94plusgris"
                ),
                "codes": ["OHE-94PLUSGRIS"],
                "names": ["SLING"],
                "variant_images": {"GRIS": metadata},
                **metadata,
            }
        ],
    )

    assert product["match_status"] == "official_code_match"
    assert product["image_url"] == image_url


def test_site_match_uses_documented_official_alias_for_inventory_code():
    image_url = (
        "https://www.offiho.com/visitantes-interior/kyos-collection/"
        "kyos-tapizadas/galeria/OHT-337.jpg"
    )

    product = build.match_official_product(
        extract_offiho_identity("OHV-337 BLANCO KYOS"),
        [
            {
                "url": (
                    "https://www.offiho.com/visitantes-interior/kyos-collection/"
                    "kyos-tapizadas/visitantes-interior-kyoscollection-modelo-OHT-337"
                ),
                "codes": ["OHT-337"],
                "names": ["KYOS"],
                "image_url": image_url,
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["match_status"] == "official_code_match"
    assert product["image_url"] == image_url


@pytest.mark.parametrize(
    ("inventory_key", "official_code"),
    [
        ("OHV-338 NEGRO KYOS", "OHT-338"),
        ("OHV-339CR GRIS KYOS", "OHT-339CR"),
        ("OHV-340CR BLANCO KYOS", "OHT-340CR"),
        ("OHR-2800-3P CR CROMADA IVY", "OHR-2800-3PCR"),
        ("OHR-2800-4P CR CROMADA IVY", "OHR-2800-4PCR"),
    ],
)
def test_site_match_uses_documented_inventory_code_aliases(inventory_key, official_code):
    image_url = f"https://www.offiho.com/galeria/{official_code}.jpg"
    product = match_official_product(
        extract_offiho_identity(inventory_key),
        [
            {
                "url": f"https://www.offiho.com/modelo-{official_code}",
                "codes": [official_code],
                "names": ["KYOS", "IVY"],
                "image_url": image_url,
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["match_status"] == "official_code_match"
    assert product["image_url"] == image_url


@pytest.mark.parametrize(
    ("inventory_key", "official_name", "image_name"),
    [
        ("OHV-90 GRIS VIOLET", "VIOLET 90", "VioletGris.jpg"),
        ("GAMER-002 MESA DRAGON", "ESCRITORIO DRAGON GAMER002", "Gamer002.jpg"),
        ("SILLA ELEFANTE CAFÉ", "SILLA ELEFANTE", "Elefante.jpg"),
    ],
)
def test_site_match_uses_curated_official_name_aliases(inventory_key, official_name, image_name):
    image_url = f"https://www.offiho.com/galeria/{image_name}"
    product = match_official_product(
        extract_offiho_identity(inventory_key),
        [
            {
                "url": f"https://www.offiho.com/productos/{image_name.lower()}",
                "codes": [],
                "names": [official_name],
                "image_url": image_url,
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["match_status"] == "official_name_match"
    assert product["image_url"] == image_url


def test_site_match_falls_back_to_exact_name_when_false_code_match_has_no_image():
    product = match_official_product(
        extract_offiho_identity("OHV-90 GRIS VIOLET"),
        [
            {
                "url": "https://www.offihoblack.com/collections/sillas-interior",
                "codes": ["OHV-90"],
                "names": ["SILLAS"],
                "image_url": "https://www.offihoblack.com/images/OHV-127.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            },
            {
                "url": "https://www.offihoblack.com/products/violet-90",
                "codes": ["OHV-128"],
                "names": ["VIOLET 90"],
                "image_url": "https://www.offihoblack.com/images/VioletGris.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            },
        ],
    )

    assert product["match_status"] == "official_name_match"
    assert product["image_url"].endswith("VioletGris.jpg")


def test_site_match_never_borrows_a_variant_image_from_another_model():
    product = match_official_product(
        extract_offiho_identity("OHS-85AL NEGRO REVOLUTION"),
        [
            {
                "codes": ["OHS-85AL"],
                "url": (
                    "https://www.offiho.com/operativos/revolution/"
                    "operativos-revolution-modelo-OHS-85al"
                ),
                "image_url": (
                    "https://www.offiho.com/operativos/revolution/"
                    "OHS-85al/OHS-85alFrente.jpg"
                ),
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            },
            {
                "codes": ["OHS-85AL", "OHV-87CR"],
                "url": (
                    "https://www.offiho.com/visitantes-interior/revolution/"
                    "visitantes-interior-revolution-modelo-OHV-87cr"
                ),
                "variant_images": {
                    "NEGRO": {
                        "image_url": (
                            "https://www.offiho.com/visitantes-interior/revolution/"
                            "OHV-87cr/colores/OHV-87crNegroTurquesa.jpg"
                        ),
                        "image_verified": True,
                        "image_content_type": "image/jpeg",
                        "image_content_length": 2048,
                    }
                },
            },
        ],
    )

    assert product["image_url"].endswith("OHS-85al/OHS-85alFrente.jpg")


def test_site_match_uses_same_model_generic_image_when_exact_color_is_missing():
    product = match_official_product(
        extract_offiho_identity("OHE-805 BLANCO QUO"),
        [
            {
                "codes": ["OHE-805"],
                "url": "https://www.offiho.com/ejecutivos/quo/modelo-OHE-805",
                "image_url": "https://www.offiho.com/ejecutivos/quo/OHE-805Frente.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
                "variant_images": {
                    "NEGRO": {
                        "image_url": "https://www.offiho.com/ejecutivos/quo/OHE-805negro.jpg",
                        "image_verified": True,
                        "image_content_type": "image/jpeg",
                        "image_content_length": 2048,
                    }
                },
            }
        ],
    )

    assert product["image_url"].endswith("OHE-805Frente.jpg")
    assert product["has_variant_catalog"] is True


def test_site_match_rejects_generic_image_labeled_as_another_color():
    product = match_official_product(
        extract_offiho_identity("OHE-805 BLANCO QUO"),
        [
            {
                "codes": ["OHE-805"],
                "url": "https://www.offiho.com/ejecutivos/quo/modelo-OHE-805",
                "image_url": "https://www.offiho.com/ejecutivos/quo/OHE-805negroFrente.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["match_status"] == "official_code_match"
    assert product["image_url"] == ""


def test_site_match_accepts_verified_shopify_image_from_exact_code_product_page():
    product = match_official_product(
        extract_offiho_identity("OHE-75 NEGRO VANTO"),
        [
            {
                "codes": ["OHE-75"],
                "names": ["VANTO"],
                "url": "https://www.offihoblack.com/products/vanto-ohe-75",
                "image_url": "https://www.offihoblack.com/cdn/shop/products/VantoE_1400x.jpg?v=1",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["match_status"] == "official_code_match"
    assert product["image_url"].endswith("VantoE_1400x.jpg?v=1")


def test_site_match_prefers_variant_specific_page_generic_image():
    gray_image = "https://www.offiho.com/ejecutivos/aiko/OHE-705gris/OHE-705grisFrente.jpg"
    product = match_official_product(
        extract_offiho_identity("OHE-705 GRIS AIKO"),
        [
            {
                "codes": ["OHE-705"],
                "names": ["AIKO"],
                "url": "https://www.offiho.com/ejecutivos/aiko/modelo-OHE-705",
                "image_url": "https://www.offiho.com/ejecutivos/aiko/OHE-705/OHE-705negroFrente.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
                "variant_images": {
                    "NEGRO": {
                        "image_url": "https://www.offiho.com/ejecutivos/aiko/OHE-705/colores/OHE-705.jpg",
                        "image_verified": True,
                        "image_content_type": "image/jpeg",
                        "image_content_length": 2048,
                    }
                },
            },
            {
                "codes": ["OHE-705GRIS"],
                "names": ["AIKO"],
                "url": "https://www.offiho.com/ejecutivos/aiko/modelo-OHE-705gris",
                "image_url": gray_image,
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            },
        ],
    )

    assert product["image_url"] == gray_image


@pytest.mark.parametrize("inventory_key", ["RIMINI NEGRO", "FESTINA PERLA"])
def test_site_match_uses_unstructured_inventory_identifier_as_name(inventory_key):
    identity = extract_offiho_identity(inventory_key)
    product = match_official_product(
        identity,
        [
            {
                "codes": [],
                "names": [identity.code],
                "url": f"https://www.offiho.com/econosillas/{identity.code.casefold()}/",
                "image_url": f"https://www.offiho.com/images/{identity.code.casefold()}.jpg",
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
            }
        ],
    )

    assert product["url"].endswith(f"/{identity.code.casefold()}/")
    assert product["match_status"] == "official_name_match"


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


def test_canonical_product_url_preserves_offiho_directory_trailing_slash():
    category = "https://www.offiho.com/directivos/"

    assert build._canonical_product_url(category) == category


def test_fetch_page_resolves_relative_links_from_redirected_directory(monkeypatch):
    payload = b'<html><head><title>Directivos - Offiho</title></head><body><a href="alufsen/">ALUFSEN</a></body></html>'

    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "text/html; charset=utf-8"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://www.offiho.com/directivos/"

        def read(self, size):
            return payload[:size]

    monkeypatch.setattr(build, "_open_official", lambda request, timeout: _Response())

    record = build._fetch_official_page("https://www.offiho.com/directivos")

    assert record["url"] == "https://www.offiho.com/directivos/"
    assert "https://www.offiho.com/directivos/alufsen/" in record["links"]


def test_fetch_page_normalizes_mixed_case_product_codes(monkeypatch):
    payload = b"<html><body><h1>KYOS</h1><p>Modelo OHP-325cr</p></body></html>"

    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "text/html"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://www.offiho.com/escolar/kyos-collection/modelo-OHP-325cr"

        def read(self, size):
            return payload[:size]

    monkeypatch.setattr(build, "_open_official", lambda request, timeout: _Response())

    record = build._fetch_official_page("https://www.offiho.com/escolar/kyos-collection/modelo-OHP-325cr")

    assert record["codes"] == ["OHP-325CR"]


def test_fetch_product_page_ignores_related_model_codes_in_body(monkeypatch):
    payload = (
        b"<html><body><h1>OHS-86al</h1>"
        b"<a href='/modelo-OHV-87cr'>Relacionado OHV-87cr</a></body></html>"
    )

    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "text/html"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return (
                "https://www.offiho.com/operativos/revolution/"
                "operativos-revolution-modelo-OHS-86al"
            )

        def read(self, size):
            return payload[:size]

    monkeypatch.setattr(build, "_open_official", lambda request, timeout: _Response())

    record = build._fetch_official_page("https://www.offiho.com/modelo-OHS-86al")

    assert record["codes"] == ["OHS-86AL"]


def test_page_names_do_not_treat_category_card_headings_as_page_name():
    parser = build._PageParser()
    parser.feed("<html><head><title>Visitantes Interior - Offiho</title></head><body><h3>KYOS</h3></body></html>")

    assert "KYOS" not in build._page_names("https://www.offiho.com/visitantes-interior/", parser, [])


def test_page_names_normalize_collection_family_slug():
    parser = build._PageParser()

    assert build._page_names(
        "https://www.offiho.com/visitantes-interior/kyos-collection/", parser, []
    ) == ["KYOS"]


def test_site_index_keeps_name_only_official_product_pages(monkeypatch):
    page_url = "https://www.offiho.com/directivos/alufsen/"
    record = {
        "url": page_url,
        "links": [],
        "codes": [],
        "names": ["ALUFSEN"],
        "image_url": "https://www.offiho.com/images/alufsen-frente.jpg",
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 2048,
        "source_updated_at": "",
    }
    monkeypatch.setattr(build, "SITE_SEEDS", (page_url,))
    monkeypatch.setattr(build, "_cached_or_fetch_page", lambda url, pages: record)

    index = build.build_site_product_index({}, now=datetime(2026, 7, 10, tzinfo=timezone.utc))

    assert index["name:ALUFSEN"]["url"] == page_url
    assert index["name:ALUFSEN"]["names"] == ["ALUFSEN"]


def test_name_index_prefers_family_page_over_different_model_page():
    family = {
        "url": "https://www.offiho.com/directivos/alufsen/",
        "codes": ["OHE-405", "OHV-408"],
        "names": ["ALUFSEN"],
        "image_url": "https://www.offiho.com/images/alufsen.jpg",
    }
    model = {
        "url": "https://www.offiho.com/directivos/alufsen/directivos-alufsen-modelo-OHE-405",
        "codes": ["OHE-405"],
        "names": ["ALUFSEN"],
        "image_url": "https://www.offiho.com/images/ohe-405.jpg",
    }

    assert build._site_candidate_rank("name:ALUFSEN", family) > build._site_candidate_rank(
        "name:ALUFSEN", model
    )


def test_site_seeds_include_each_offiho_catalog_section():
    expected = {
        "directivos",
        "ejecutivos",
        "operativos",
        "industrial",
        "accesorios",
        "visitantes-interior",
        "visitantes-exterior",
        "mesas",
        "bancos",
        "confortables",
        "bancas",
        "escolar",
        "nuevos-productos",
    }
    paths = {urlsplit(url).path.strip("/") for url in build.SITE_SEEDS}

    assert expected <= paths


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


def test_collection_swatch_extraction_maps_large_images_by_exact_finish():
    page_url = (
        "https://www.offiho.com/operativos/revolution/"
        "operativos-revolution-modelo-OHS-86al"
    )
    parser = build._PageParser()
    parser.feed(
        """
        <img class="cloudzoom-gallery colecciones"
             src="OHS-86al/colores/gris.jpg"
             data-cloudzoom="useZoom: '.cloudzoom', image: 'OHS-86al/colores/OHS-86alGris.jpg', zoomImage: 'OHS-86al/zoom/OHS-86alGris.jpg'">
        <img class="cloudzoom-gallery colecciones"
             src="OHS-86al/colores/azul.jpg"
             data-cloudzoom="useZoom: '.cloudzoom', image: 'OHS-86al/colores/OHS-86alMarino.jpg', zoomImage: 'OHS-86al/zoom/OHS-86alMarino.jpg'">
        """
    )

    images = build._extract_variant_image_urls(page_url, parser, codes=["OHS-86AL"])

    assert images == {
        "AZUL": (
            "https://www.offiho.com/operativos/revolution/"
            "OHS-86al/colores/OHS-86alMarino.jpg"
        ),
        "GRIS": (
            "https://www.offiho.com/operativos/revolution/"
            "OHS-86al/colores/OHS-86alGris.jpg"
        ),
        "MARINO": (
            "https://www.offiho.com/operativos/revolution/"
            "OHS-86al/colores/OHS-86alMarino.jpg"
        ),
    }


def test_collection_gallery_extraction_maps_each_image_to_its_exact_code():
    page_url = (
        "https://www.offiho.com/visitantes-interior/kyos-collection/"
        "kyos-tapizadas/"
    )
    parser = build._PageParser()
    parser.feed(
        '<img src="galeria/OHV-331.jpg" alt="OHV-331">'
        '<img src="galeria/OHV-335cr.jpg" alt="OHV-335cr">'
        '<img src="galeria/OHT-337.jpg" alt="OHT-337">'
    )

    images = build._extract_code_image_urls(
        page_url,
        parser,
        codes=["OHV-331", "OHV-335CR", "OHT-337"],
    )

    assert images == {
        "OHT-337": f"{page_url}galeria/OHT-337.jpg",
        "OHV-331": f"{page_url}galeria/OHV-331.jpg",
        "OHV-335CR": f"{page_url}galeria/OHV-335cr.jpg",
    }


def test_site_index_uses_exact_collection_image_for_each_code(monkeypatch):
    page_url = (
        "https://www.offiho.com/visitantes-interior/kyos-collection/"
        "kyos-tapizadas/"
    )

    def metadata(code):
        return {
            "image_url": f"{page_url}galeria/{code}.jpg",
            "image_verified": True,
            "image_content_type": "image/jpeg",
            "image_content_length": 2048,
        }

    record = {
        "url": page_url,
        "links": [],
        "codes": ["OHV-331", "OHV-335CR"],
        "names": ["KYOS TAPIZADAS"],
        "image_url": f"{page_url}galeria/OHV-331.jpg",
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 2048,
        "code_images": {
            "OHV-331": metadata("OHV-331"),
            "OHV-335CR": metadata("OHV-335cr"),
        },
        "source_updated_at": "",
    }
    monkeypatch.setattr(build, "SITE_SEEDS", (page_url,))
    monkeypatch.setattr(build, "_cached_or_fetch_page", lambda url, pages: record)

    index = build.build_site_product_index({}, now=datetime(2026, 7, 10, tzinfo=timezone.utc))

    assert index["OHV-331"]["image_url"].endswith("/OHV-331.jpg")
    assert index["OHV-335CR"]["image_url"].endswith("/OHV-335cr.jpg")
    assert index["OHV-331"]["codes"] == ["OHV-331"]
    assert index["OHV-335CR"]["codes"] == ["OHV-335CR"]


@pytest.mark.parametrize(
    ("variant", "finish"),
    [("GRIS", "Gris"), ("AZUL MARINO", "Marino"), ("ROJA", "Rojo")],
)
def test_site_match_prefers_exact_finish_image_over_generic_gallery(variant, finish):
    image_url = (
        "https://www.offiho.com/operativos/revolution/"
        f"OHS-86al/colores/OHS-86al{finish}.jpg"
    )
    metadata = {
        "image_url": image_url,
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 2048,
    }
    product = match_official_product(
        extract_offiho_identity(f"OHS-86AL {variant} REVOLUTION"),
        [
            {
                "codes": ["OHS-86AL"],
                "names": ["REVOLUTION"],
                "url": (
                    "https://www.offiho.com/operativos/revolution/"
                    "operativos-revolution-modelo-OHS-86al"
                ),
                "image_url": (
                    "https://www.offiho.com/operativos/revolution/"
                    "galeria/OHV-85cr.jpg"
                ),
                "image_verified": True,
                "image_content_type": "image/jpeg",
                "image_content_length": 2048,
                "variant_images": {finish.upper(): metadata},
            }
        ],
    )

    assert product["image_url"] == image_url


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
    monkeypatch.setattr(build, "parse_pdf_product_index", lambda paths, items, assets, base_url: {})
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


def test_no_network_preserves_verified_description_and_finish_images():
    variant_image = {
        "image_url": "https://www.offiho.com/operativos/revolution/OHS-86alRojo.jpg",
        "image_verified": True,
        "image_content_type": "image/jpeg",
        "image_content_length": 2048,
    }
    cache = {
        "cache_version": build.CACHE_VERSION,
        "site_index": {
            "OHS-86AL": {
                "url": "https://www.offiho.com/operativos/revolution/modelo-OHS-86al",
                "description": "Asiento y respaldo de polipropileno de alta resistencia.",
                "variant_images": {"ROJO": variant_image},
                "source_updated_at": "",
            }
        },
        "site_index_expires_at": "2000-01-01T00:00:00+00:00",
    }

    index = build.build_site_product_index(cache, no_network=True)

    assert index["OHS-86AL"]["description"].startswith("Asiento y respaldo")
    assert index["OHS-86AL"]["variant_images"]["ROJO"] == variant_image


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
