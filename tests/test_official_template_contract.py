from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.official_template import (
    load_template_contract,
    verify_official_template,
)
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "formato-cotizacion-2026-oficial.contract.json"
)
TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
OFFICIAL_SHA256 = "fc87b105b2809fbb892986e084bf1aaeffc77ff7d2b7e4b5da7ef6d8c4d028f5"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _formula(root: ET.Element, coordinate: str) -> str:
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    assert cell is not None
    formula = cell.find(f"{{{MAIN}}}f")
    assert formula is not None and formula.text
    return formula.text


def test_promoted_template_matches_official_contract():
    contract = load_template_contract(CONTRACT)

    result = verify_official_template(TEMPLATE, contract)

    assert result.sha256 == OFFICIAL_SHA256
    assert result.sheet_states == {
        "Cotizacion": "visible",
        "sheep": "visible",
        "Mobiliti": "visible",
        "Estrategia Comercial ": "visible",
        "Fletes": "hidden",
        "Proveedores": "hidden",
        "SPEC LAMINADO JOME": "hidden",
        "SPEC-GUIDE-LUMBRO": "hidden",
        "SPEC-GUIDE ESTRUCTURAS": "hidden",
        "Spec Guide Estructura ": "hidden",
        "SPEC-GUIDE-CR GLOBAL": "hidden",
        "Meses Sin Intereses Tarjetas": "hidden",
    }
    assert result.defined_name_count == 31
    assert result.external_link_parts == 12
    assert result.spec_formula_count == 1314


def test_official_template_uses_sharepoint_gdl_price_columns() -> None:
    package = XlsxPackage.read(TEMPLATE)
    mobiliti = ET.fromstring(package.parts[package.sheet_part("Mobiliti")])

    assert mobiliti.find(f"{{{MAIN}}}dimension").attrib["ref"] == "A1:AV610"
    assert _formula(mobiliti, "W14") == (
        'IF(F14="Offiho",J14,IF(_xlfn.XLOOKUP('
        'F14,Proveedores!A$2:A$50,Proveedores!E$2:E$50,"")="Nacional",'
        '(J14/0.5)+(V14*H14),((J14/0.3/0.5)+(V14*H14))))'
    )
    assert _formula(mobiliti, "X14") == "(W14*H14)"
    assert "MINIFS" not in _formula(mobiliti, "X14")
    assert _formula(mobiliti, "Y14").startswith('IF(F14="Sunon Inc",$AJ$577')


def test_modified_template_fails_before_output(tmp_path):
    changed = tmp_path / "changed.xlsx"
    changed.write_bytes(TEMPLATE.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="Plantilla oficial incompatible"):
        verify_official_template(changed, load_template_contract(CONTRACT))


def test_worker_default_template_is_the_promoted_official_copy():
    from mobiliti_saas.worker import quote_worker

    assert quote_worker._default_template() == TEMPLATE
