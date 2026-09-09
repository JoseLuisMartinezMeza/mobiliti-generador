"""Regresión del formato SharePoint recibido el 8 de septiembre de 2026."""

from pathlib import Path
from xml.etree import ElementTree as ET

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage
from mobiliti_saas.quote_engine.ooxml_worksheet import WorksheetEditor, build_mobiliti_sheet


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def test_v11_expansion_preserves_provider_rules_and_automatic_project_type():
    package = XlsxPackage.read(TEMPLATE)
    xml = package.parts[package.sheet_part("Mobiliti")]
    assert WorksheetEditor.from_xml(xml).layout.id == "v11"
    mutation = build_mobiliti_sheet(
        xml, [SectionNeed(str(i), f"Sección {i}", 34) for i in range(17)], ()
    )
    root = ET.fromstring(mutation.xml)

    def formula(coordinate):
        return root.findtext(f".//{{{MAIN}}}c[@r='{coordinate}']/{{{MAIN}}}f")

    for row in (mutation.row_map.item_rows[0], mutation.row_map.item_rows[-1]):
        assert formula(f"AD{row}") == f"MIN($E$5,AL{row})"
        assert formula(f"AM{row}") == f"_xlfn.XLOOKUP(F{row},Proveedores!A:A,Proveedores!G:G)"
        assert formula(f"AN{row}") == f'IF(AK{row}<AM{row},"ERROR","OK")'
        assert formula(f"L{row}") == f'IF(K{row}="Importado",Fletes!$B$66,0%)'
        assert "Proveedores!F:F" in formula(f"Q{row}")
    assert '"MIXTO"' in formula("P10")
    assert f"$K$15:$K${mutation.row_map.last_product_row}" in formula("P10")
    assert f"$A$15:$A${mutation.row_map.last_product_row}" in formula("E9")
    assert root.find(f".//{{{MAIN}}}c[@r='E9']/{{{MAIN}}}v") is None
