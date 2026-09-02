from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import column_index_from_string

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage
from mobiliti_saas.quote_engine.ooxml_worksheet import build_mobiliti_sheet


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _formula_coordinates(root: ET.Element, row: int) -> set[str]:
    return {
        cell.attrib["r"]
        for cell in root.findall(f".//{{{MAIN}}}row[@r='{row}']/{{{MAIN}}}c")
        if cell.find(f"{{{MAIN}}}f") is not None
        and column_index_from_string(cell.attrib["r"].rstrip("0123456789")) <= 34
    }


def test_official_mobiliti_keeps_formula_surface_in_all_33_product_rows():
    package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed("official", "Official", 1)],
        (),
    )
    root = ET.fromstring(mutation.xml)
    first_product_row = mutation.row_map.sections[0].product_start
    expected_columns = {
        coordinate.rstrip("0123456789")
        for coordinate in _formula_coordinates(root, first_product_row)
    }

    assert all(
        {
            coordinate.rstrip("0123456789")
            for coordinate in _formula_coordinates(root, row)
        }
        == expected_columns
        for row in range(first_product_row, first_product_row + 33)
    )


def test_official_mobiliti_preserves_linked_currency_formula_and_lumbro_spec_sheet():
    package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed("official", "Official", 1)],
        (),
    )
    root = ET.fromstring(mutation.xml)
    p6 = root.find(f".//{{{MAIN}}}c[@r='P6']/{{{MAIN}}}f")

    assert p6 is not None and p6.text == 'IF(P4=TRUE,_FV(J6,"Price"),0)'
    assert package.sheet_part("SPEC-GUIDE-LUMBRO")
