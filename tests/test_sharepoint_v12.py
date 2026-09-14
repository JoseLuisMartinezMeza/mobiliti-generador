"""Las reglas del V12 deben llegar a ambos formatos y a todas las secciones."""
from xml.etree import ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage
from mobiliti_saas.quote_engine.ooxml_worksheet import build_mobiliti_sheet
from mobiliti_saas.quote_engine.template_profiles import resolve_template_profile
from mobiliti_saas.quote_engine.quotation_sheets import official_provider_name

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@pytest.mark.parametrize("profile_id", ["official_2026_gdl", "sunon_cdmx_v1c"])
def test_v12_santeco_and_unit_price_reach_both_templates(profile_id):
    assert official_provider_name("Santeco") == "Santeco"
    assert official_provider_name("Tarkett") == "Santeco"
    assert official_provider_name("Tarkett MX") == "Santeco"
    package = XlsxPackage.read(resolve_template_profile(profile_id).template_path)
    cotizacion = ET.fromstring(package.parts[package.sheet_part("Cotizacion")])
    assert cotizacion.findtext(f".//{{{MAIN}}}c[@r='F17']/{{{MAIN}}}f") == "Mobiliti!AA15"
    mutation = build_mobiliti_sheet(
        package.parts[package.sheet_part("Mobiliti")],
        [SectionNeed("uno", "Primera", 34), SectionNeed("dos", "Segunda", 2)],
        (),
    )
    root = ET.fromstring(mutation.xml)
    for row in (mutation.row_map.item_rows[0], mutation.row_map.item_rows[-1]):
        formula = root.findtext(f".//{{{MAIN}}}c[@r='Y{row}']/{{{MAIN}}}f")
        assert f'F{row}="Santeco"' in formula
        assert f'),J{row},' in formula
