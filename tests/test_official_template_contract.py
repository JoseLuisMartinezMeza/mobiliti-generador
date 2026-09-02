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
OFFICIAL_SHA256 = "39f5cebd3cbe3e7356f4d4174161e8599bf7158e7b495a789c9fc04850928ee4"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def assert_official_template_contract():
    """Audita hash, hojas, relaciones externas y fórmulas de la plantilla."""

    return verify_official_template(TEMPLATE, load_template_contract(CONTRACT))


def _formula(root: ET.Element, coordinate: str) -> str:
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    assert cell is not None
    formula = cell.find(f"{{{MAIN}}}f")
    assert formula is not None and formula.text
    return formula.text


def test_promoted_template_matches_official_contract():
    result = assert_official_template_contract()

    assert result.sha256 == OFFICIAL_SHA256
    assert result.sheet_states == {
        "Cotizacion": "visible",
        "Mobiliti": "visible",
        "Control Administrativo": "visible",
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


def test_official_template_uses_latest_sharepoint_price_columns() -> None:
    package = XlsxPackage.read(TEMPLATE)
    mobiliti = ET.fromstring(package.parts[package.sheet_part("Mobiliti")])
    cotizacion = ET.fromstring(package.parts[package.sheet_part("Cotizacion")])
    fletes = ET.fromstring(package.parts[package.sheet_part("Fletes")])

    assert mobiliti.find(f"{{{MAIN}}}dimension").attrib["ref"] == "A1:AZ614"
    assert _formula(mobiliti, "E9") == (
        "ROUND(IFERROR(1-SUMPRODUCT(($A$15:$A$572=TRUE)*($H$15:$H$572)*"
        "($AI$15:$AI$572))/SUMPRODUCT(($A$15:$A$572=TRUE)*($H$15:$H$572)*"
        "($Z$15:$Z$572)),0),2)"
    )
    assert _formula(mobiliti, "AD14") == "E6"
    assert _formula(mobiliti, "Y15").startswith("ROUNDUP(IF(OR(F15=\"Offiho\"")
    assert _formula(mobiliti, "Z15").startswith("ROUNDUP(IF(OR(F15=\"Offiho\"")
    assert _formula(mobiliti, "AA15") == (
        'IF(Z15>=Y15,_xlfn.MINIFS($Z$15:$Z$572,$D$15:$D$572,D15),'
        '"NO SE ESTA RESPETANDO EL MARGEN")'
    )
    assert _formula(mobiliti, "AD15") == "IF(H15>0,$E$5,0)"
    assert _formula(cotizacion, "F17") == "Mobiliti!AA15"
    assert _formula(cotizacion, "G17") == "ROUND(Mobiliti!$AD$14,2)"
    assert _formula(fletes, "B66") == (
        'MIN(110%, IF(E60="MANUAL", E63, IF(E60="PRORRATEADO", '
        '(B61*B65+E62+B78)/B61, (B61*B65+B64+B78)/B61)))'
    )


def test_modified_template_fails_before_output(tmp_path):
    changed = tmp_path / "changed.xlsx"
    changed.write_bytes(TEMPLATE.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="Plantilla oficial incompatible"):
        verify_official_template(changed, load_template_contract(CONTRACT))


def test_worker_default_template_is_the_promoted_official_copy():
    from mobiliti_saas.worker import quote_worker

    assert quote_worker._default_template() == TEMPLATE
