"""Descuentos válidos llegan al selector v17 sin residuos de división float."""

from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.engine import (
    _OfficialPresentationLine,
    _build_official_mobiliti,
    _official_canonical_rows,
)
from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@pytest.mark.parametrize(
    ("discount", "expected"),
    [
        (33.34, Decimal("0.3334")),
        (19.99, Decimal("0.1999")),
        (35.5, Decimal("0.355")),
        ("33.340000", Decimal("0.3334")),
        (Decimal("0.1999"), Decimal("0.1999")),
        ("12.345678", Decimal("0.12345678")),
    ],
)
def test_official_generation_accepts_decimal_discounts_without_float_residue(
    discount, expected
):
    line = _OfficialPresentationLine(
        item_key="qa-product",
        section_id="qa-section",
        section_title="SILLAS",
        item=None,
        name="Silla QA",
        description="Silla de prueba",
        dimensions="60 x 60 x 90 cm",
        m3=Decimal("0.12"),
        quantity=Decimal("1"),
        category="Silla",
        provider="Sunon Inc",
        region="Centro",
        original_currency="MXN",
        original_cost=Decimal("1000"),
        frozen_rate=Decimal("1"),
        converted_cost=Decimal("1000.00"),
        origin="imported",
        source_row=9,
        upstream_row_hash="",
    )
    result = _build_official_mobiliti(
        XlsxPackage.read(TEMPLATE),
        (line,),
        (SectionNeed("qa-section", "SILLAS", 1),),
        _official_canonical_rows((line,), b"quotation-fixture"),
        {"quote_currency": "MXN", "lugar_entrega": "CDMX", "descuento": discount},
        {"qa-product": 9},
        {},
    )

    root = ET.fromstring(result.xml)
    value = root.findtext(f".//{{{MAIN}}}c[@r='AD14']/{{{MAIN}}}v")
    assert value is not None
    assert Decimal(value) == expected
    assert root.findtext(f".//{{{MAIN}}}c[@r='P4']/{{{MAIN}}}v") == "0"
