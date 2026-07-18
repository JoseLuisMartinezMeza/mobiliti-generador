from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
import io
from pathlib import Path

import pytest
from openpyxl import load_workbook
from PIL import Image

from mobiliti_saas.quote_engine import catalog_cart
from mobiliti_saas.quote_engine import supplier_catalog as supplier_module
from mobiliti_saas.quote_engine.supplier_catalog import (
    build_supplier_cart_payload,
    create_supplier_quotation_workbook,
    load_supplier_catalog_data,
    resolve_conversion_rate,
    safe_excel_text,
)


PUBLIC_FIELDS = {
    "internal_id", "supplier", "product_key", "sku", "code_status",
    "brand", "collection", "name", "description", "unit",
    "availability_type", "stock", "lead_time", "base_price_options",
    "add_on_options", "base_currency", "price_net", "tax_rate",
    "attributes", "image_url", "image_kind", "product_url", "warnings",
    "source_reference",
}


def catalog_payload(*, supplier="alma", items=None):
    item = {
        "internal_id": "alma:kun:kc8611n01rop",
        "supplier": supplier,
        "product_key": "kun-kc8611n01rop",
        "sku": "KC8611N01ROP",
        "code_status": "verified",
        "brand": "KUN",
        "collection": "KUN",
        "name": "Silla KUN",
        "description": "Silla configurable",
        "unit": "pieza",
        "availability_type": "made_to_order",
        "stock": None,
        "lead_time": "Sobre pedido",
        "base_price_options": [
            {"id": "powder-coated-aluminium", "name": "Powder coated aluminium", "price_net": "250.000000", "available": True},
            {"id": "solid-wood", "name": "Solid wood", "price_net": "300.000000", "available": True},
        ],
        "add_on_options": [
            {"id": "cushion:a-plus", "name": "Cushion A+", "family": "cushion", "price_net": "35.100000", "available": True},
            {"id": "cushion:a-plus-plus", "name": "Cushion A++", "family": "cushion", "price_net": "50.000000", "available": True},
            {"id": "ceramic:a", "name": "Ceramic A", "family": "ceramic", "price_net": "22.500000", "available": True, "compatible_base_option_ids": ["powder-coated-aluminium"]},
            {"id": "ceramic:b", "name": "Ceramic B", "family": "ceramic", "price_net": "30.000000", "available": False},
        ],
        "base_currency": "USD",
        "price_net": "199.990000",
        "tax_rate": "0.160000",
        "attributes": {"color": "Black", "dimensions": "65 x 45 x 83 cm"},
        "image_url": "https://example.test/kun.webp",
        "image_kind": "official",
        "product_url": "https://example.test/kun",
        "warnings": ["Imagen ilustrativa"],
        "source_reference": "SPEC Guide-Alma-KUN.xlsx:E9:I9",
    }
    return {
        "supplier": supplier,
        "source_hash": "a" * 64,
        "generated_at": "2026-07-15T00:00:00Z",
        "items": items or [item],
    }


def rate_rows(*, effective_date=None):
    day = effective_date or (date.today() - timedelta(days=1)).isoformat()
    return [
        {"currency": "USD", "effective_date": day, "mxn_per_unit": "18.500000", "retrieved_at": f"{day}T23:00:00Z"},
        {"currency": "EUR", "effective_date": day, "mxn_per_unit": "21.000000", "retrieved_at": f"{day}T23:05:00Z"},
    ]


def test_load_catalog_validates_and_preserves_public_decimal_strings():
    loaded = load_supplier_catalog_data(catalog_payload(), expected_supplier="alma")
    item = loaded["items"][0]

    assert set(item) == PUBLIC_FIELDS
    assert item["price_net"] == "199.990000"
    assert item["tax_rate"] == "0.160000"
    assert item["base_price_options"][0]["price_net"] == "250.000000"
    assert loaded["by_internal_id"][item["internal_id"]] is item


def test_unknown_base_currency_is_allowed_only_for_blocked_zero_prices():
    payload = catalog_payload()
    item = payload["items"][0]
    item["base_currency"] = "XXX"
    item["price_net"] = "0.000000"
    item["base_price_options"] = []
    item["add_on_options"] = []

    loaded = load_supplier_catalog_data(payload)

    assert loaded["items"][0]["base_currency"] == "XXX"

    item["price_net"] = "1.000000"
    with pytest.raises(ValueError, match="moneda base.*verificar"):
        load_supplier_catalog_data(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(items=[]), "vacio"),
        (lambda payload: payload["items"][0].pop("name"), "name"),
        (lambda payload: payload["items"][0].update(supplier="sunon"), "supplier"),
        (lambda payload: payload["items"][0].update(price_net=0.1), "price_net"),
        (lambda payload: payload["items"][0].update(price_net="NaN"), "price_net"),
        (lambda payload: payload["items"][0].update(sku="", code_status="verified"), "sku"),
        (lambda payload: payload["items"][0].update(sku="INVENTED", code_status="needs_review"), "sku"),
        (lambda payload: payload.update(source_hash="not-a-hash"), "source_hash"),
        (lambda payload: payload.update(generated_at="not-a-date"), "generated_at"),
    ],
)
def test_load_catalog_rejects_malformed_or_mismatched_data(mutation, message):
    payload = catalog_payload()
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        load_supplier_catalog_data(payload, expected_supplier="alma")


def test_load_catalog_rejects_incompatible_duplicate_ids_and_skus():
    duplicate = deepcopy(catalog_payload()["items"][0])
    duplicate["name"] = "Conflicting name"
    with pytest.raises(ValueError, match="internal_id duplicado"):
        load_supplier_catalog_data(catalog_payload(items=[catalog_payload()["items"][0], duplicate]))


@pytest.mark.parametrize("unknown_fields", [{"unexpected": 1}, {"unexpected": 1, "other": 2}])
def test_load_catalog_rejects_one_or_multiple_unknown_item_fields(unknown_fields):
    payload = catalog_payload()
    payload["items"][0].update(unknown_fields)

    with pytest.raises(ValueError, match="campos inesperados"):
        load_supplier_catalog_data(payload)

    duplicate = deepcopy(catalog_payload()["items"][0])
    duplicate["internal_id"] = "alma:kun:another"
    with pytest.raises(ValueError, match="sku duplicado"):
        load_supplier_catalog_data(catalog_payload(items=[catalog_payload()["items"][0], duplicate]))


def test_build_cart_uses_only_catalog_values_and_central_rounding_example():
    catalog = load_supplier_catalog_data(catalog_payload())
    cart = build_supplier_cart_payload(
        [{
            "internal_id": "alma:kun:kc8611n01rop",
            "quantity": "2",
            "base_option_id": "powder-coated-aluminium",
            "add_on_option_ids": ["cushion:a-plus", "ceramic:a"],
        }],
        catalog=catalog,
        quote_currency="EUR",
        rate_rows=rate_rows(),
    )

    line = cart["items"][0]
    assert cart["source_type"] == "supplier_cart"
    assert cart["supplier"] == "alma"
    assert cart["catalog_source_hash"] == "a" * 64
    assert cart["exchange_rate"] == "0.880952"
    assert line["name"] == "Silla KUN"
    assert line["sku"] == "KC8611N01ROP"
    assert line["image_url"] == "https://example.test/kun.webp"
    assert line["unit_price_base"] == "307.600000"
    assert line["unit_price"] == "270.98"
    assert line["line_total"] == "541.96"
    assert line["quantity"] == "2"
    assert line["configuration"] == "Powder coated aluminium; Cushion A+; Ceramic A"
    assert all(not isinstance(value, float) for value in (cart["exchange_rate"], line["unit_price_base"], line["unit_price"], line["line_total"]))


def test_repeated_kun_source_code_survives_cart_and_embeds_official_image_in_xlsx(
    monkeypatch, tmp_path
):
    first = deepcopy(catalog_payload()["items"][0])
    second = deepcopy(first)
    first["attributes"]["source_code"] = "KUN-REPETIDO"
    second.update(
        internal_id="alma:kun:variant:second",
        product_key="kun-second",
        sku="kun-internal-second",
        name="Silla KUN segunda",
        description="Descripcion oficial segunda",
        product_url="https://www.kundesign.com/s/1/product.html",
        image_url="https://project-ref.supabase.co/storage/v1/object/public/catalog-assets/"
        + "b" * 64
        + ".png",
    )
    second["attributes"]["source_code"] = "KUN-REPETIDO"
    catalog = load_supplier_catalog_data(catalog_payload(items=[first, second]))
    cart = build_supplier_cart_payload(
        [
            {
                "internal_id": second["internal_id"],
                "quantity": "2",
                "base_option_id": "powder-coated-aluminium",
                "add_on_option_ids": ["cushion:a-plus"],
            }
        ],
        catalog,
        "USD",
        [],
    )
    line = cart["items"][0]
    assert line["sku"] == "KUN-REPETIDO"
    assert line["description"] == second["description"]
    assert line["product_url"] == second["product_url"]
    assert line["image_url"] == second["image_url"]
    assert line["unit_price"] == "285.10"

    encoded = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(encoded, format="PNG")
    image_bytes = encoded.getvalue()

    class PeerSocket:
        def getpeername(self):
            return ("93.184.216.34", 443)

    class Response:
        headers = {
            "content-type": "image/png",
            "content-length": str(len(image_bytes)),
        }

        def __init__(self):
            self.fp = type("Fp", (), {"raw": type("Raw", (), {"_sock": PeerSocket()})()})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return image_bytes[:size]

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == second["image_url"]
            assert timeout == 18
            return Response()

    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.delenv("CATALOG_ASSET_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(catalog_cart, "_resolve_public_host", lambda host: None)
    monkeypatch.setattr(catalog_cart.urllib.request, "build_opener", lambda *handlers: Opener())
    output = create_supplier_quotation_workbook(cart, tmp_path / "kun-cart.xlsx")
    workbook = load_workbook(output)
    sheet = workbook["Quotation"]
    assert sheet["B9"].value == second["name"]
    assert "Descripcion oficial segunda" in sheet["D9"].value
    assert "KUN-REPETIDO" in sheet["D9"].value
    assert "Powder coated aluminium; Cushion A+" in sheet["D9"].value
    assert sheet["E9"].value == "65 x 45 x 83 cm"
    assert sheet["G9"].value == 2
    assert isinstance(sheet["G9"].value, int)
    assert sheet["J9"].value == 285.1
    assert sheet["K9"].value == second["product_url"]
    assert len(sheet._images) == 1
    workbook.close()


def test_supplier_xlsx_description_keeps_exact_link_color_and_availability_buckets(tmp_path):
    payload = catalog_payload(supplier="sunon")
    item = payload["items"][0]
    item.update(
        internal_id="sunon:erp:9001",
        product_key="fast10",
        sku="9001",
        brand="Sunon",
        collection="",
        name="Fast Chair",
        description="Silla operativa",
        unit="PZA",
        availability_type="stocked",
        stock="19",
        lead_time="1-2 semanas",
        base_price_options=[],
        add_on_options=[],
        price_net="200.000000",
        product_url="https://www.sunonglobal.com/product/fast-chair/",
        image_url="",
        image_kind="placeholder",
    )
    item["attributes"] = {
        "source_erp_code": "9001",
        "source_model_code": "FAST10",
        "dimensions": "700 x 700 mm",
        "color": "Black frame",
        "availability_buckets": [
            {"lead_time": "1-2 semanas", "quantity": "12", "source_refs": []},
            {"lead_time": "1-2 semanas", "quantity": "3", "source_refs": []},
            {"lead_time": "4-6 semanas", "quantity": "7", "source_refs": []},
        ],
        "product_url_match": {
            "status": "exact_code",
            "matched_code": "FAST10",
            "lookup_code": "FAST10",
        },
    }
    catalog = load_supplier_catalog_data(payload, expected_supplier="sunon")
    cart = build_supplier_cart_payload(
        [{"internal_id": item["internal_id"], "quantity": "2"}],
        catalog,
        "USD",
        [],
    )

    output = create_supplier_quotation_workbook(cart, tmp_path / "sunon-cart.xlsx")
    workbook = load_workbook(output)
    sheet = workbook["Quotation"]
    description = sheet["D9"].value

    assert "Silla operativa" in description
    assert "SKU: 9001" in description
    assert "Color: Black frame" in description
    assert "Entrega: 1-2 semanas" in description
    assert "Disponibilidad: 15 PZA (1-2 semanas); 7 PZA (4-6 semanas)" in description
    assert "URL: https://www.sunonglobal.com/product/fast-chair/" in description
    assert sheet["E9"].value == "700 x 700 mm"
    assert sheet["G9"].value == 2
    assert isinstance(sheet["G9"].value, int)
    assert sheet["K9"].value == "https://www.sunonglobal.com/product/fast-chair/"
    workbook.close()


def test_supplier_xlsx_discloses_unknown_availability():
    description, warning = catalog_cart._description_for_item(
        {
            "description": "Producto sin existencia publicada",
            "availability_type": "unknown",
            "warnings": [],
        },
        "",
        "",
        Decimal("1"),
    )

    assert "Disponibilidad: por confirmar" in description
    assert warning == ""


def test_supplier_xlsx_preserves_warranty_and_product_notes():
    description, warning = catalog_cart._description_for_item(
        {
            "description": "Producto CR Global",
            "availability_type": "unknown",
            "attributes": {
                "warranty": "GARANTIA DE 5 ANOS",
                "product_notes": ["NO INCLUYE CUBIERTA"],
            },
            "warnings": [],
        },
        "CR-1",
        "",
        Decimal("1"),
    )

    assert "Garantia: GARANTIA DE 5 ANOS" in description
    assert "Notas: NO INCLUYE CUBIERTA" in description
    assert warning == ""


def test_supplier_cart_rejects_verified_item_with_zero_price():
    payload = catalog_payload()
    payload["items"][0].update(
        code_status="verified",
        sku="ALMA-ZERO",
        base_currency="MXN",
        price_net="0.000000",
        base_price_options=[],
        add_on_options=[],
    )

    with pytest.raises(ValueError, match="precio por confirmar"):
        build_supplier_cart_payload(
            [{"internal_id": payload["items"][0]["internal_id"], "quantity": "1"}],
            payload,
            "MXN",
            [],
        )


@pytest.mark.parametrize(
    "host",
    ("kundesign.com", "www.kundesign.com", "assets.kundesign.com"),
)
def test_supplier_cart_never_allows_kundesign_as_image_storage(monkeypatch, tmp_path, host):
    monkeypatch.setenv("CATALOG_ASSET_PUBLIC_BASE_URL", f"https://{host}/assets")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(
        catalog_cart.urllib.request,
        "build_opener",
        lambda *handlers: pytest.fail("Kundesign no debe alcanzar el transporte"),
    )

    assert catalog_cart._allowed_image_hosts("supplier_cart") == frozenset()
    assert catalog_cart._download_catalog_image(
        f"https://{host}/image.png", tmp_path, "KUN", "supplier_cart"
    ) is None


def test_build_cart_rejects_client_catalog_fields():
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": "1",
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [],
        "unit_price": "0.01",
    }
    with pytest.raises(ValueError, match="no permitido"):
        build_supplier_cart_payload([raw], load_supplier_catalog_data(catalog_payload()), "USD", [])


def test_build_cart_rejects_review_codes():
    review_payload = catalog_payload()
    review_item = review_payload["items"][0]
    review_item.update({"code_status": "needs_review", "sku": ""})
    raw = {
        "internal_id": review_item["internal_id"],
        "quantity": "1",
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [],
    }
    with pytest.raises(ValueError, match="verificar"):
        build_supplier_cart_payload([raw], review_payload, "USD", [])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("internal_id", 1), ("internal_id", True), ("internal_id", 1.5),
        ("internal_id", []), ("internal_id", {}),
        ("base_option_id", 1), ("base_option_id", True), ("base_option_id", 1.5),
        ("base_option_id", []), ("base_option_id", {}),
    ],
)
def test_build_cart_rejects_non_string_browser_ids(field, value):
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": "1",
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [],
    }
    raw[field] = value

    with pytest.raises(ValueError, match=field):
        build_supplier_cart_payload([raw], load_supplier_catalog_data(catalog_payload()), "USD", [])


@pytest.mark.parametrize("value", [1, True, 1.5, [], {}])
def test_build_cart_rejects_non_string_add_on_ids(value):
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": "1",
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [value],
    }
    with pytest.raises(ValueError, match="add_on_option_id"):
        build_supplier_cart_payload([raw], load_supplier_catalog_data(catalog_payload()), "USD", [])


@pytest.mark.parametrize("quantity", [1, True, 1.5])
def test_build_cart_rejects_non_decimal_string_browser_quantities(quantity):
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": quantity,
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [],
    }
    with pytest.raises(ValueError, match="cantidad"):
        build_supplier_cart_payload([raw], load_supplier_catalog_data(catalog_payload()), "USD", [])


@pytest.mark.parametrize("quantity", ["0.5", "1.25", Decimal("2.5")])
def test_build_cart_rejects_fractional_piece_quantities(quantity):
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": quantity,
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [],
    }
    with pytest.raises(ValueError, match="entera"):
        build_supplier_cart_payload(
            [raw], load_supplier_catalog_data(catalog_payload()), "USD", []
        )


@pytest.mark.parametrize("unit", ["M2", "m²"])
def test_build_cart_accepts_fractional_square_meter_quantities(unit):
    payload = catalog_payload()
    payload["items"][0]["unit"] = unit
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": "1.25",
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": [],
    }
    cart = build_supplier_cart_payload([raw], payload, "USD", [])
    assert cart["items"][0]["quantity"] == "1.25"


def test_decimal_results_do_not_depend_on_ambient_context_precision():
    raw = {
        "internal_id": "alma:kun:kc8611n01rop",
        "quantity": "2",
        "base_option_id": "powder-coated-aluminium",
        "add_on_option_ids": ["cushion:a-plus", "ceramic:a"],
    }
    expected = build_supplier_cart_payload([raw], catalog_payload(), "EUR", rate_rows())

    with localcontext() as context:
        context.prec = 2
        actual = build_supplier_cart_payload([raw], catalog_payload(), "EUR", rate_rows())

    assert actual["exchange_rate"] == expected["exchange_rate"] == "0.880952"
    assert actual["items"][0]["unit_price_base"] == expected["items"][0]["unit_price_base"] == "307.600000"
    assert actual["items"][0]["unit_price"] == expected["items"][0]["unit_price"] == "270.98"
    assert actual["items"][0]["line_total"] == expected["items"][0]["line_total"] == "541.96"


def test_derived_line_boundary_is_accepted_under_low_ambient_precision():
    payload = catalog_payload()
    payload["items"][0]["base_price_options"] = []
    payload["items"][0]["add_on_options"] = []
    payload["items"][0]["price_net"] = "1000000000.000000"
    raw = {"internal_id": "alma:kun:kc8611n01rop", "quantity": "1000000", "add_on_option_ids": []}

    with localcontext() as context:
        context.prec = 2
        cart = build_supplier_cart_payload([raw], payload, "USD", [])

    assert cart["items"][0]["line_total"] == "1000000000000000.00"


def test_extreme_derived_ratio_and_line_total_are_rejected_cleanly():
    today = date(2026, 7, 15)
    extreme_rates = [
        {"currency": "USD", "effective_date": "2026-07-15", "mxn_per_unit": "1000000000.000000", "retrieved_at": "2026-07-15T20:00:00Z"},
        {"currency": "EUR", "effective_date": "2026-07-15", "mxn_per_unit": "0.000001", "retrieved_at": "2026-07-15T20:00:00Z"},
    ]
    with pytest.raises(ValueError, match="exchange_rate fuera de rango"):
        resolve_conversion_rate("USD", "EUR", extreme_rates, today)

    payload = catalog_payload()
    payload["items"][0]["base_price_options"] = [
        {"id": "expensive", "name": "Expensive", "price_net": "1000000000.000000", "available": True}
    ]
    payload["items"][0]["add_on_options"] = [
        {"id": "extra", "name": "Extra", "family": "extra", "price_net": "1000000000.000000", "available": True}
    ]
    raw = {"internal_id": "alma:kun:kc8611n01rop", "quantity": "1000000", "base_option_id": "expensive", "add_on_option_ids": ["extra"]}
    with pytest.raises(ValueError, match="line_total fuera de rango"):
        build_supplier_cart_payload([raw], payload, "USD", [])


def test_build_cart_uses_price_net_when_there_are_no_options():
    payload = catalog_payload()
    payload["items"][0]["base_price_options"] = []
    payload["items"][0]["add_on_options"] = []
    payload["items"][0]["unit"] = "M2"
    catalog = load_supplier_catalog_data(payload)

    cart = build_supplier_cart_payload(
        [{"internal_id": "alma:kun:kc8611n01rop", "quantity": "1.25", "add_on_option_ids": []}],
        catalog=catalog,
        quote_currency="USD",
        rate_rows=[],
    )

    assert cart["exchange_rate"] == "1.000000"
    assert cart["items"][0]["unit_price_base"] == "199.990000"
    assert cart["items"][0]["unit_price"] == "199.99"
    assert cart["items"][0]["line_total"] == "249.99"


def test_build_cart_line_total_uses_the_rounded_display_unit_price():
    payload = catalog_payload()
    payload["items"][0]["base_price_options"] = []
    payload["items"][0]["add_on_options"] = []
    payload["items"][0]["price_net"] = "0.335000"

    cart = build_supplier_cart_payload(
        [{"internal_id": "alma:kun:kc8611n01rop", "quantity": "3", "add_on_option_ids": []}],
        payload,
        "USD",
        [],
    )

    assert cart["items"][0]["unit_price"] == "0.34"
    assert cart["items"][0]["line_total"] == "1.02"


def test_build_cart_rejects_zero_product_price():
    payload = catalog_payload()
    payload["items"][0]["base_price_options"] = []
    payload["items"][0]["add_on_options"] = []
    payload["items"][0]["price_net"] = "0.000000"

    with pytest.raises(ValueError, match="precio por confirmar"):
        build_supplier_cart_payload(
            [{"internal_id": "alma:kun:kc8611n01rop", "quantity": "2", "add_on_option_ids": []}],
            payload,
            "USD",
            [],
        )


def test_build_cart_rejects_unknown_base_currency_until_verified():
    payload = catalog_payload()
    payload["items"][0]["base_price_options"] = []
    payload["items"][0]["add_on_options"] = []
    payload["items"][0]["base_currency"] = "XXX"
    payload["items"][0]["price_net"] = "0.000000"

    with pytest.raises(ValueError, match="moneda base.*verificar"):
        build_supplier_cart_payload(
            [{"internal_id": "alma:kun:kc8611n01rop", "quantity": "2", "add_on_option_ids": []}],
            payload,
            "USD",
            [],
        )


def test_build_cart_rejects_zero_base_and_add_on_option_prices():
    payload = catalog_payload()
    payload["items"][0]["base_price_options"][0]["price_net"] = "0.000000"
    payload["items"][0]["add_on_options"][0]["price_net"] = "0.000000"

    with pytest.raises(ValueError, match="precio por confirmar"):
        build_supplier_cart_payload(
            [{
                "internal_id": "alma:kun:kc8611n01rop",
                "quantity": "2",
                "base_option_id": "powder-coated-aluminium",
                "add_on_option_ids": ["cushion:a-plus"],
            }],
            payload,
            "USD",
            [],
        )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"internal_id": "missing", "quantity": "1", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": []}, "no encontrado"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "0", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": []}, "cantidad"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1000000.000001", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": []}, "cantidad"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1.0000001", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": []}, "cantidad"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": None, "add_on_option_ids": []}, "base"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "unknown", "add_on_option_ids": []}, "base"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": ["ceramic:b"]}, "disponible"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "solid-wood", "add_on_option_ids": ["ceramic:a"]}, "incompatible"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": ["cushion:a-plus", "cushion:a-plus-plus"]}, "familia"),
        ({"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": ["ceramic:a", "ceramic:a"]}, "duplicado"),
    ],
)
def test_build_cart_rejects_invalid_client_configurations(raw, message):
    with pytest.raises(ValueError, match=message):
        build_supplier_cart_payload([raw], load_supplier_catalog_data(catalog_payload()), "USD", [])


def test_build_cart_rejects_duplicate_configuration_rows():
    raw = {"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": ["ceramic:a"]}
    with pytest.raises(ValueError, match="configuracion duplicada"):
        build_supplier_cart_payload([raw, deepcopy(raw)], load_supplier_catalog_data(catalog_payload()), "USD", [])


def test_resolve_conversion_rate_uses_common_date_and_decimal_cross_rate():
    rows = rate_rows(effective_date="2026-07-14") + [
        {"currency": "USD", "effective_date": "2026-07-15", "mxn_per_unit": "19.000000", "retrieved_at": "2026-07-15T20:00:00Z"}
    ]
    snapshot = resolve_conversion_rate("USD", "EUR", rows, date(2026, 7, 15))

    assert snapshot.exchange_rate == Decimal("0.880952")
    assert snapshot.rate_effective_date == date(2026, 7, 14)
    assert snapshot.rate_retrieved_at == "2026-07-14T23:05:00Z"


def test_resolve_conversion_rate_supports_mxn_and_rejects_stale_or_invalid_rows():
    today = date(2026, 7, 15)
    assert resolve_conversion_rate("USD", "MXN", rate_rows(effective_date="2026-07-15"), today).exchange_rate == Decimal("18.500000")
    assert resolve_conversion_rate("MXN", "USD", rate_rows(effective_date="2026-07-15"), today).exchange_rate == Decimal("0.054054")
    assert resolve_conversion_rate("USD", "USD", [], today).exchange_rate == Decimal("1.000000")

    with pytest.raises(ValueError, match="vencida"):
        resolve_conversion_rate("USD", "MXN", rate_rows(effective_date="2026-07-09"), today)
    bad_rows = rate_rows(effective_date="2026-07-15")
    bad_rows[0]["mxn_per_unit"] = "Infinity"
    with pytest.raises(ValueError, match="tasa"):
        resolve_conversion_rate("USD", "MXN", bad_rows, today)


def test_identity_rate_validates_supplied_rows_without_requiring_any():
    today = date(2026, 7, 15)
    assert resolve_conversion_rate("USD", "USD", [], today).exchange_rate == Decimal("1.000000")
    assert resolve_conversion_rate("USD", "USD", rate_rows(effective_date="2026-07-15"), today).exchange_rate == Decimal("1.000000")

    malformed = rate_rows(effective_date="2026-07-15")
    malformed[0]["mxn_per_unit"] = 18.5
    with pytest.raises(ValueError, match="tasa"):
        resolve_conversion_rate("USD", "USD", malformed, today)

    duplicates = rate_rows(effective_date="2026-07-15")
    duplicates.append({**duplicates[0], "mxn_per_unit": "19.000000"})
    with pytest.raises(ValueError, match="duplicada"):
        resolve_conversion_rate("USD", "USD", duplicates, today)

    future = rate_rows(effective_date="2026-07-16")
    with pytest.raises(ValueError, match="futura"):
        resolve_conversion_rate("USD", "USD", future, today)


def test_resolve_conversion_rate_rejects_datetime_for_today():
    with pytest.raises(ValueError, match="Fecha de conversion invalida"):
        resolve_conversion_rate("USD", "USD", [], datetime(2026, 7, 15, 12, 0))


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)", "data:text/plain,bad", "file:///tmp/bad",
        "//cdn.example.test/image.webp", "https://user:pass@example.test/image.webp",
        "https://example.test/bad\nheader", "x" * 2049,
        "https://exa mple.com/x", "https://./x", "https://example..com/x",
        "https://-example.com/x", "https://example-.com/x",
        "https://exa%mple.com/x", "https://example.com\\evil/x",
        f"https://{'a' * 64}.com/x",
        f"https://{'.'.join(['a' * 63] * 4)}/x",
        "https://256.1.1.1/x", "https://999.999.999.999/x",
        "https://127.1/x", "https://xn--a.example/x",
    ],
)
def test_load_catalog_rejects_unsafe_or_unbounded_urls(url):
    payload = catalog_payload()
    payload["items"][0]["image_url"] = url
    with pytest.raises(ValueError, match="image_url"):
        load_supplier_catalog_data(payload)


def test_load_catalog_accepts_empty_and_absolute_http_storage_urls():
    payload = catalog_payload()
    payload["items"][0]["image_url"] = "http://assets.example.test/catalog/item.jpg"
    payload["items"][0]["product_url"] = "https://storage.example.test/catalog/item"
    loaded = load_supplier_catalog_data(payload)
    assert loaded["items"][0]["image_url"].startswith("http://")

    payload["items"][0]["image_url"] = ""
    payload["items"][0]["product_url"] = ""
    load_supplier_catalog_data(payload)


@pytest.mark.parametrize(
    "url",
    [
        "https://catalog.example.com/x",
        "https://192.0.2.10/x",
        "https://127.0.0.1/x",
        "https://[2001:db8::10]/x",
        "https://münich.example/x",
        "https://xn--mnich-kva.example/x",
    ],
)
def test_load_catalog_accepts_valid_dns_ip_and_idna_urls(url):
    payload = catalog_payload()
    payload["items"][0]["image_url"] = url
    assert load_supplier_catalog_data(payload)["items"][0]["image_url"] == url


def test_catalog_item_and_cart_row_limits_are_enforced_before_iteration():
    payload = catalog_payload()
    payload["items"] = [payload["items"][0]] * (supplier_module.MAX_CATALOG_ITEMS + 1)
    with pytest.raises(ValueError, match="limite de items"):
        load_supplier_catalog_data(payload)

    raw = {"internal_id": "alma:kun:kc8611n01rop", "quantity": "1", "base_option_id": "powder-coated-aluminium", "add_on_option_ids": []}
    with pytest.raises(ValueError, match="limite de filas"):
        build_supplier_cart_payload([raw] * (supplier_module.MAX_CART_ROWS + 1), catalog_payload(), "USD", [])


def test_options_warnings_and_compatibility_limits_are_enforced():
    payload = catalog_payload()
    option = payload["items"][0]["base_price_options"][0]
    payload["items"][0]["base_price_options"] = [{**option, "id": f"base-{index}"} for index in range(supplier_module.MAX_OPTIONS_PER_ITEM + 1)]
    with pytest.raises(ValueError, match="limite de options"):
        load_supplier_catalog_data(payload)

    payload = catalog_payload()
    payload["items"][0]["warnings"] = ["warning"] * (supplier_module.MAX_WARNINGS_PER_ITEM + 1)
    with pytest.raises(ValueError, match="limite de warnings"):
        load_supplier_catalog_data(payload)

    payload = catalog_payload()
    payload["items"][0]["add_on_options"][0]["compatible_base_option_ids"] = [
        "powder-coated-aluminium"
    ] * (supplier_module.MAX_COMPATIBLE_OPTION_IDS + 1)
    with pytest.raises(ValueError, match="limite de compatible"):
        load_supplier_catalog_data(payload)


@pytest.mark.parametrize(
    ("option_kind", "mutation", "message"),
    [
        ("base_price_options", lambda option: option.update(unexpected="value"), "campos inesperados"),
        ("base_price_options", lambda option: option.update(compatible_base_option_ids=[]), "campos inesperados"),
        ("add_on_options", lambda option: option.update(unexpected="value"), "campos inesperados"),
        ("base_price_options", lambda option: option.pop("name"), "campos faltantes"),
        ("add_on_options", lambda option: option.pop("family"), "campos faltantes"),
    ],
)
def test_options_require_strict_fields_by_option_kind(option_kind, mutation, message):
    payload = catalog_payload()
    mutation(payload["items"][0][option_kind][0])

    with pytest.raises(ValueError, match=message):
        load_supplier_catalog_data(payload)


def test_add_on_allows_only_documented_optional_compatibility_field():
    payload = catalog_payload()
    loaded = load_supplier_catalog_data(payload)

    add_ons = loaded["items"][0]["add_on_options"]
    assert add_ons[0].keys() == {"id", "name", "family", "price_net", "available"}
    assert add_ons[2]["compatible_base_option_ids"] == ["powder-coated-aluminium"]


@pytest.mark.parametrize("field", ["name", "description", "source_reference"])
def test_catalog_text_limits_are_enforced_without_truncation(field):
    payload = catalog_payload()
    limit = {
        "name": supplier_module.MAX_TEXT_LENGTH,
        "description": supplier_module.MAX_DESCRIPTION_LENGTH,
        "source_reference": supplier_module.MAX_SOURCE_REFERENCE_LENGTH,
    }[field]
    payload["items"][0][field] = "x" * (limit + 1)
    with pytest.raises(ValueError, match=field):
        load_supplier_catalog_data(payload)


@pytest.mark.parametrize("attributes", [{"bad": object()}, {"bad": {1, 2}}, {"bad": Decimal("1")}])
def test_attributes_reject_non_json_values(attributes):
    payload = catalog_payload()
    payload["items"][0]["attributes"] = attributes
    with pytest.raises(ValueError, match="attributes"):
        load_supplier_catalog_data(payload)


def test_attributes_reject_excessive_size_and_depth():
    payload = catalog_payload()
    payload["items"][0]["attributes"] = {"value": "x" * supplier_module.MAX_ATTRIBUTES_JSON_BYTES}
    with pytest.raises(ValueError, match="attributes"):
        load_supplier_catalog_data(payload)

    nested = "leaf"
    for _ in range(supplier_module.MAX_ATTRIBUTES_DEPTH + 1):
        nested = {"nested": nested}
    payload = catalog_payload()
    payload["items"][0]["attributes"] = nested
    with pytest.raises(ValueError, match="attributes"):
        load_supplier_catalog_data(payload)


def test_rate_row_limit_applies_to_identity_and_conversion():
    rows = rate_rows() * (supplier_module.MAX_RATE_ROWS // 2 + 1)
    with pytest.raises(ValueError, match="limite de tasas"):
        resolve_conversion_rate("USD", "USD", rows, date.today())
    with pytest.raises(ValueError, match="limite de tasas"):
        resolve_conversion_rate("USD", "EUR", rows, date.today())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ordinary text", "ordinary text"),
        ("  =1+1", "'  =1+1"),
        ("\t+SUM(A1:A2)", "'\t+SUM(A1:A2)"),
        ("-2", "'-2"),
        ("@command", "'@command"),
        (None, ""),
        ("line\r\ntext", "line\ntext"),
    ],
)
def test_safe_excel_text_neutralizes_formula_prefixes(value, expected):
    assert safe_excel_text(value) == expected


def test_workbook_boundary_rejects_invalid_supplier_payload(tmp_path):
    with pytest.raises(ValueError, match="Proveedor no soportado"):
        create_supplier_quotation_workbook({}, tmp_path / "supplier.xlsx")


def test_supplier_images_only_allow_configured_public_asset_hosts(monkeypatch):
    monkeypatch.delenv("CATALOG_ASSET_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert catalog_cart._allowed_image_hosts("supplier_cart") == frozenset()

    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setenv("CATALOG_ASSET_PUBLIC_BASE_URL", "https://catalog-assets.example.test/public")
    assert catalog_cart._allowed_image_hosts("supplier_cart") == frozenset(
        {"project-ref.supabase.co", "catalog-assets.example.test"}
    )
    assert "www.offiho.com" not in catalog_cart._allowed_image_hosts("supplier_cart")


def test_supplier_image_allowlist_ignores_insecure_or_credentialed_bases(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project-ref.supabase.co:8443")
    monkeypatch.setenv("CATALOG_ASSET_PUBLIC_BASE_URL", "https://user:pass@assets.example.test")
    assert catalog_cart._allowed_image_hosts("supplier_cart") == frozenset()


def test_supplier_module_copies_are_byte_identical():
    root = Path(__file__).resolve().parents[1]
    for module_name in ("supplier_catalog.py", "catalog_cart.py"):
        assert (root / "mobiliti_saas/quote_engine" / module_name).read_bytes() == (
            root / "mobiliti_saas/web/mobiliti_saas/quote_engine" / module_name
        ).read_bytes()
