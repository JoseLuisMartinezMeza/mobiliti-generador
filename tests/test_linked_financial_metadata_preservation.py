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
    first_product_row = 15
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed("sillas", "SILLAS", 1)],
        (
            MobilitiCellWrite(f"D{first_product_row}", "text", "Silla de prueba"),
            MobilitiCellWrite(f"J{first_product_row}", "number", Decimal("100.00")),
        ),
    )
    composed = ET.fromstring(mutation.xml)
    last_product_row = mutation.row_map.last_product_row

    # J6 es un rich-value vinculado: ``vm`` lo conecta con xl/metadata.xml.
    assert _cell(composed, "J6").attrib.get("vm") == _cell(official, "J6").attrib["vm"]
    assert ET.tostring(_cell(composed, "J6")) == ET.tostring(_cell(official, "J6"))

    assert _formula_payload(_cell(composed, "P6")) == _formula_payload(
        _cell(official, "P6")
    )
    assert _formula_payload(_cell(composed, "P6"))[1] == (
        'IF(P4=TRUE,_FV(J6,"Price"),0)'
    )

    assert _formula_payload(_cell(composed, f"Z{first_product_row}")) == _formula_payload(
        _cell(official, f"Z{first_product_row}")
    )
    assert _formula_payload(_cell(composed, f"AA{first_product_row}"))[1] == (
        f"IF(Z{first_product_row}>=Y{first_product_row},"
        f"_xlfn.MINIFS($Z${first_product_row}:$Z${last_product_row},"
        f"$D${first_product_row}:$D${last_product_row},D{first_product_row},"
        f"$H${first_product_row}:$H${last_product_row},"
        f"_xlfn.MAXIFS($H${first_product_row}:$H${last_product_row},"
        f"$D${first_product_row}:$D${last_product_row},D{first_product_row}))"
        ',"NO SE ESTA RESPETANDO EL MARGEN")'
    )
    assert _formula_payload(_cell(composed, f"AB{first_product_row}"))[1] == (
        f"IFERROR(AA{first_product_row}*H{first_product_row},0)"
    )
