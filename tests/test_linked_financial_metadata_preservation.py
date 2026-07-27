"""Regresión del vínculo financiero USD/MXN de la plantilla oficial."""

from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage
from mobiliti_saas.quote_engine.ooxml_worksheet import (
    MobilitiCellWrite,
    build_mobiliti_sheet,
)


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _cell(root: ET.Element, coordinate: str) -> ET.Element:
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    assert cell is not None, f"La celda Mobiliti!{coordinate} no existe"
    return cell


def _formula_payload(cell: ET.Element) -> tuple[dict[str, str], str]:
    formula = cell.find(f"{{{MAIN}}}f")
    assert formula is not None and formula.text, "La fórmula oficial fue eliminada"
    return dict(formula.attrib), formula.text


def test_minimal_composition_preserves_linked_usd_mxn_metadata_and_pricing_formulas():
    """Evita que la composición reemplace el dato financiero oficial por una tasa fija."""

    package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    official = ET.fromstring(package.parts[package.sheet_part("Mobiliti")])
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed("sillas", "SILLAS", 1)],
        (
            MobilitiCellWrite("D14", "text", "Silla de prueba"),
            MobilitiCellWrite("J14", "number", Decimal("100.00")),
        ),
    )
    composed = ET.fromstring(mutation.xml)

    # J6 es un rich-value vinculado: ``vm`` lo conecta con xl/metadata.xml.
    assert _cell(composed, "J6").attrib.get("vm") == _cell(official, "J6").attrib["vm"]
    assert ET.tostring(_cell(composed, "J6")) == ET.tostring(_cell(official, "J6"))

    assert _formula_payload(_cell(composed, "K6")) == _formula_payload(
        _cell(official, "K6")
    )
    assert _formula_payload(_cell(composed, "K6"))[1] == '_FV(J6,"High")'

    for coordinate in ("W14", "X14"):
        assert _formula_payload(_cell(composed, coordinate)) == _formula_payload(
            _cell(official, coordinate)
        )
