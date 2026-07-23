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
    expected_columns = {
        coordinate.rstrip("0123456789")
        for coordinate in _formula_coordinates(root, 14)
    }

    assert all(
        {
            coordinate.rstrip("0123456789")
            for coordinate in _formula_coordinates(root, row)
        }
        == expected_columns
        for row in range(14, 47)
    )


def test_official_mobiliti_preserves_k6_and_lumbro_spec_sheet():
    package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed("official", "Official", 1)],
        (),
    )
    root = ET.fromstring(mutation.xml)
    k6 = root.find(f".//{{{MAIN}}}c[@r='K6']/{{{MAIN}}}f")

    assert k6 is not None and k6.text == '_FV(J6,"High")'
    assert package.sheet_part("SPEC-GUIDE-LUMBRO")
