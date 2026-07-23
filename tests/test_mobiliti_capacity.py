from pathlib import Path
from xml.etree import ElementTree as ET

from openpyxl.utils.cell import column_index_from_string

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed, plan_mobiliti_layout
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


def _formula_columns(root: ET.Element, row: int) -> set[str]:
    return {
        cell.attrib["r"].rstrip("0123456789")
        for cell in root.findall(f".//{{{MAIN}}}row[@r='{row}']/{{{MAIN}}}c")
        if cell.find(f"{{{MAIN}}}f") is not None
        and column_index_from_string(cell.attrib["r"].rstrip("0123456789")) <= 34
    }


def test_layout_expands_one_section_to_one_hundred_products_without_truncation():
    layout = plan_mobiliti_layout([SectionNeed("large", "Large", 100)])

    assert layout.sections[0].capacity == 100
    assert layout.sections[0].item_count == 100
    assert len(layout.item_rows) == 100
    assert layout.total_row < 1_048_576


def test_official_composer_clones_row_14_formula_surface_into_twenty_sections():
    package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed(f"s-{index}", f"Section {index}", 1) for index in range(20)],
        (),
    )
    root = ET.fromstring(mutation.xml)
    canonical_columns = _formula_columns(root, 14)

    assert {"L", "N", "O"}.issubset(canonical_columns)
    assert len(mutation.row_map.sections) == 20
    assert all(
        _formula_columns(root, section.product_start) == canonical_columns
        for section in mutation.row_map.sections
    )
