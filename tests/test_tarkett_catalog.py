from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from mobiliti_saas.quote_engine.parser import read_items
from mobiliti_saas.quote_engine.tarkett_catalog import (
    TarkettCatalogItem,
    build_tarkett_cart_payload,
    create_tarkett_quotation_workbook,
    load_tarkett_catalog,
)
import scripts.build_tarkett_catalog as tarkett_builder
from scripts.build_tarkett_catalog import InventoryRow
from scripts.build_tarkett_catalog import parse_inventory_html
from scripts.build_tarkett_catalog import resolve_tarkett_product


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "Inventario Tarkett- 6 Julio .xls"


def _sample_catalog():
    item = TarkettCatalogItem(
        code="25731726",
        name="Aurea Tech Cadiz 6.0mm",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("970.200"),
        product_url="https://tarkett.com.mx/producto/cadiz/",
        image_url="",
        match_status="name_match",
    )
    return {
        "source_hash": "hash-1",
        "generated_at": "2026-07-08T00:00:00+00:00",
        "items": [item],
        "by_code": {item.code: item},
    }


def test_inventory_html_parser_reads_expected_tarkett_rows():
    rows = parse_inventory_html(INVENTORY)

    assert len(rows) == 125
    assert rows[0].code == "25731726"
    assert rows[0].name == "Aurea Tech Cadiz 6.0mm"
    assert rows[0].available_quantity == Decimal("970.200")
    assert all(row.available_quantity > 0 for row in rows)
    assert {row.unit for row in rows} == {
        "FOT - pie",
        "H87 - pieza",
        "KGM - kilogramo",
        "MTK - metro cuadrado",
    }


def test_generated_catalog_contains_cadiz_url_and_image():
    catalog = load_tarkett_catalog(ROOT / "mobiliti_saas" / "quote_engine" / "data" / "tarkett_catalog.json")
    first = catalog["items"][0]
    by_code = catalog["by_code"]

    assert len(catalog["items"]) == 125
    assert first.code == "25731726"
    assert first.product_url == "https://tarkett.com.mx/producto/cadiz/"
    assert "Aurea-Tech-Cadiz" in first.image_url
    assert by_code["24174124"].product_url == "https://tarkett.com.mx/producto/grafito-porcelain/"
    assert "Grafito-Porcelain-24174124-24175124" in by_code["24174124"].image_url
    assert by_code["24173722"].match_status == "media_sku_match"
    assert "24173722-Alicante" in by_code["24173722"].image_url
    assert by_code["711533007"].match_status == "professional_es_sku_match"
    assert by_code["711533007"].image_url == "https://media.tarkett-image.com/large/TH_EssenceStructure_9502.jpg"
    assert by_code["711793003"].match_status == "professional_es_sku_match"
    assert by_code["711793003"].image_url == "https://media.tarkett-image.com/large/TH_Grezzo_7844.jpg"
    assert by_code["25731101"].match_status == "tarkett_mx_line_name_match"
    assert "Mallorca" in by_code["25731101"].image_url
    assert by_code["7100910014"].match_status == "professional_es_collection_match"
    assert by_code["666214"].match_status == "tarkett_ar_accessory_sku_match"
    assert sum(1 for item in catalog["items"] if item.image_url) >= 122


def test_tarkett_cart_payload_validates_stock_and_sets_zero_price():
    payload = build_tarkett_cart_payload([{"code": "25731726", "quantity": "2.5"}], catalog=_sample_catalog())

    assert payload["source_type"] == "tarkett_cart"
    assert payload["catalog_source_hash"] == "hash-1"
    assert payload["items"][0]["unit_price"] == 0
    assert payload["items"][0]["quantity"] == 2.5
    assert payload["items"][0]["product_url"] == "https://tarkett.com.mx/producto/cadiz/"


def test_tarkett_cart_payload_rejects_unknown_and_excess_quantity():
    catalog = _sample_catalog()

    try:
        build_tarkett_cart_payload([{"code": "missing", "quantity": 1}], catalog=catalog)
    except ValueError as exc:
        assert "no encontrado" in str(exc)
    else:
        raise AssertionError("unknown product should fail")

    try:
        build_tarkett_cart_payload([{"code": "25731726", "quantity": 9999}], catalog=catalog)
    except ValueError as exc:
        assert "mayor a existencia" in str(exc)
    else:
        raise AssertionError("quantity over stock should fail")


@pytest.mark.parametrize("quantity", ["1e5000", "0.0001"])
def test_tarkett_cart_rejects_extreme_or_overprecise_quantity(quantity):
    with pytest.raises(ValueError, match="Cantidad invalida"):
        build_tarkett_cart_payload([{"code": "25731726", "quantity": quantity}], catalog=_sample_catalog())


def test_tarkett_cart_workbook_is_readable_by_quote_parser(tmp_path):
    payload = build_tarkett_cart_payload([{"code": "25731726", "quantity": "3.5"}], catalog=_sample_catalog())
    output = tmp_path / "tarkett.xlsx"

    create_tarkett_quotation_workbook(payload, output)
    items, column_map = read_items(output)
    products = [item for item in items if item.tipo == "producto"]

    assert "cantidad" in column_map
    assert products[0].nombre == "Aurea Tech Cadiz 6.0mm"
    assert products[0].cantidad == 3.5
    assert products[0].precio == 0
    assert products[0].categoria == "Tarkett"


def test_tarkett_workbook_delegates_to_shared_catalog_adapter(monkeypatch, tmp_path):
    import mobiliti_saas.quote_engine.tarkett_catalog as tarkett_catalog

    payload = build_tarkett_cart_payload([{"code": "25731726", "quantity": "3.5"}], catalog=_sample_catalog())
    seen = {}

    def fake_adapter(cart_payload, output_path, *, source_type, category_label, image_dir=None):
        seen.update(
            {
                "payload": cart_payload,
                "output": output_path,
                "source_type": source_type,
                "category_label": category_label,
                "image_dir": image_dir,
            }
        )
        return Path(output_path)

    monkeypatch.setattr(tarkett_catalog, "create_catalog_quotation_workbook", fake_adapter)

    output = tarkett_catalog.create_tarkett_quotation_workbook(payload, tmp_path / "tarkett.xlsx")

    assert output == tmp_path / "tarkett.xlsx"
    assert seen["source_type"] == "tarkett_cart"
    assert seen["category_label"] == "Tarkett"
    assert seen["payload"]["items"][0]["unit_price"] == 0


def test_tarkett_workbook_forces_zero_price_from_untrusted_payload(tmp_path):
    payload = build_tarkett_cart_payload([{"code": "25731726", "quantity": "3.5"}], catalog=_sample_catalog())
    payload["items"][0]["unit_price"] = 999

    output = create_tarkett_quotation_workbook(payload, tmp_path / "tarkett-untrusted-price.xlsx")

    wb = load_workbook(output)
    assert wb["Quotation"]["J9"].value == 0
    wb.close()


def test_tarkett_scraper_resolves_typo_name_by_sku_index():
    row = InventoryRow(
        code="24174124",
        name="Piso Ambienta Stone Grafitto Porcelain 600x600mm",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("10.8"),
    )
    product_index = [
        {
            "slug": "grafito-porcelain",
            "link": "https://tarkett.com.mx/producto/grafito-porcelain/",
            "title": {"rendered": "Grafito Porcelain"},
            "content": {"rendered": ""},
            "excerpt": {"rendered": ""},
            "_embedded": {
                "wp:featuredmedia": [
                    {
                        "source_url": "https://tarkett.com.mx/wp-content/uploads/2025/06/ambienta-stone_0009_Grafito-Porcelain-24174124-24175124.jpg",
                        "title": {"rendered": "Grafito Porcelain"},
                    }
                ]
            },
        }
    ]

    match = resolve_tarkett_product(row, {}, no_network=True, product_index=product_index)

    assert match["match_status"] == "sku_match"
    assert match["product_url"] == "https://tarkett.com.mx/producto/grafito-porcelain/"
    assert "24174124-24175124" in match["image_url"]


def test_tarkett_scraper_uses_official_media_by_sku(monkeypatch):
    row = InventoryRow(
        code="24173722",
        name="Piso Aurea Tech Alicante",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("1298.26"),
    )

    def fake_fetch_media(term, cache, *, no_network=False):
        if term != "24173722":
            return []
        return [
            {
                "link": "https://tarkett.com.mx/24173722-alicante/",
                "source_url": "https://tarkett.com.mx/wp-content/uploads/2022/03/24173722-Alicante-scaled.jpg",
                "slug": "24173722-alicante",
                "title": {"rendered": "24173722 Alicante"},
                "mime_type": "image/jpeg",
            }
        ]

    monkeypatch.setattr(tarkett_builder, "_fetch_media", fake_fetch_media)

    match = resolve_tarkett_product(row, {}, no_network=False, product_index=[], sku_index={})

    assert match["match_status"] == "media_sku_match"
    assert match["product_url"] == "https://tarkett.com.mx/24173722-alicante/"
    assert match["image_url"].endswith("24173722-Alicante-scaled.jpg")


def test_tarkett_scraper_uses_professional_es_sitemap_sku_match(monkeypatch):
    row = InventoryRow(
        code="711533007",
        name="Desso Ess Strct AA92 9502 B1 100x25",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("12"),
    )
    page_url = "https://profesional.tarkett.es/es_ES/coleccion-C001042-essence-structure/essence-structure-aa92-9502"

    monkeypatch.setattr(tarkett_builder, "_fetch_professional_sitemap_urls", lambda cache, no_network=False: [page_url])
    monkeypatch.setattr(
        tarkett_builder,
        "_fetch_professional_page_snapshot",
        lambda url, cache, no_network=False: {
            "codes": ["711533007"],
            "image_url": "https://media.tarkett-image.com/large/TH_EssenceStructure_9502.jpg",
            "title": "Essence Structure AA92 9502",
            "text": "Ref. 711533007",
        },
    )

    match = resolve_tarkett_product(row, {}, no_network=False, product_index=[], sku_index={})

    assert match["match_status"] == "professional_es_sku_match"
    assert match["product_url"] == page_url
    assert match["image_url"].endswith("TH_EssenceStructure_9502.jpg")


def test_tarkett_professional_es_fallback_rejects_page_without_sku(monkeypatch):
    row = InventoryRow(
        code="711533007",
        name="Desso Ess Strct AA92 9502 B1 100x25",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("12"),
    )
    page_url = "https://profesional.tarkett.es/es_ES/coleccion-C001042-essence-structure/essence-structure-aa92-9502"

    monkeypatch.setattr(tarkett_builder, "_fetch_professional_sitemap_urls", lambda cache, no_network=False: [page_url])
    monkeypatch.setattr(
        tarkett_builder,
        "_fetch_professional_page_snapshot",
        lambda url, cache, no_network=False: {
            "codes": ["711533008"],
            "image_url": "https://media.tarkett-image.com/large/TH_EssenceStructure_9502.jpg",
            "title": "Essence Structure AA92 9502",
            "text": "Ref. 711533008",
        },
    )

    match = resolve_tarkett_product(row, {}, no_network=False, product_index=[], sku_index={})

    assert match["match_status"] == "unmatched"
    assert match["image_url"] == ""


def test_tarkett_professional_es_collection_fallback_for_catalog_items(monkeypatch):
    row = InventoryRow(
        code="2102002000",
        name="CATALOGO ECLIPSE PREMIUM",
        unit="H87 - pieza",
        available_quantity=Decimal("10"),
    )
    page_url = "https://profesional.tarkett.es/es_ES/coleccion-C000043-eclipse-premium"

    monkeypatch.setattr(tarkett_builder, "_fetch_media", lambda term, cache, no_network=False: [])
    monkeypatch.setattr(tarkett_builder, "_fetch_professional_sitemap_urls", lambda cache, no_network=False: [page_url])
    monkeypatch.setattr(
        tarkett_builder,
        "_fetch_professional_page_snapshot",
        lambda url, cache, no_network=False: {
            "codes": ["000043"],
            "image_url": "https://media.tarkett-image.com/large/IN_HP_Eclipse_Premium.jpg",
            "title": "Eclipse Premium",
            "text": "Coleccion Eclipse Premium",
        },
    )

    match = resolve_tarkett_product(row, {}, no_network=False, product_index=[], sku_index={})

    assert match["match_status"] == "professional_es_collection_match"
    assert match["product_url"] == page_url
    assert match["image_url"].endswith("IN_HP_Eclipse_Premium.jpg")


def test_tarkett_secondary_official_fallback_matches_line_name_variant(monkeypatch):
    row = InventoryRow(
        code="25731101",
        name="Aurea Tech Maiorca 6.0mm",
        unit="MTK - metro cuadrado",
        available_quantity=Decimal("1"),
    )

    monkeypatch.setattr(tarkett_builder, "_fetch_media", lambda term, cache, no_network=False: [])
    monkeypatch.setattr(tarkett_builder, "_fetch_secondary_official_page_snapshot", lambda url, cache, no_network=False: {
        "image_url": "",
        "images": [
            "https://tarkett.com.mx/wp-content/uploads/elementor/thumbs/Aurea-Tech-Mallorca-thumb.jpg",
            "https://tarkett.com.mx/wp-content/uploads/2022/03/Aurea-Tech-Mallorca.jpg",
        ],
        "text": "Aurea Tech Mallorca",
        "windows_by_code": {},
    })

    match = resolve_tarkett_product(row, {}, no_network=False, product_index=[], sku_index={})

    assert match["match_status"] == "tarkett_mx_line_name_match"
    assert match["image_url"].endswith("Aurea-Tech-Mallorca.jpg")


def test_tarkett_secondary_official_fallback_matches_accessory_sku(monkeypatch):
    row = InventoryRow(
        code="666214",
        name="Ultrabond Eco 4 LVT bucket 14 kg",
        unit="KGM - kilogramo",
        available_quantity=Decimal("1"),
    )

    monkeypatch.setattr(tarkett_builder, "_fetch_media", lambda term, cache, no_network=False: [])
    monkeypatch.setattr(tarkett_builder, "_fetch_secondary_official_page_snapshot", lambda url, cache, no_network=False: {
        "image_url": "https://tarkett.com.ar/wp-content/uploads/2023/11/tarkett-logo-web.png",
        "images": ["https://tarkett.com.ar/prod/acc/ultrabond-4-lvt-14kg.jpg"],
        "text": "Ultrabond 4 LVT 14kg 666214",
        "windows_by_code": {
            "666214": '<img data-src="https://tarkett.com.ar/prod/acc/ultrabond-4-lvt-14kg.jpg" /><h6>666214</h6>'
        },
    })

    match = resolve_tarkett_product(row, {}, no_network=False, product_index=[], sku_index={})

    assert match["match_status"] == "tarkett_ar_accessory_sku_match"
    assert match["image_url"].endswith("ultrabond-4-lvt-14kg.jpg")
