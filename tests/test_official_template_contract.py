from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.official_template import (
    load_template_contract,
    verify_official_template,
)


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
OFFICIAL_SHA256 = "e8bd97286aaa8af5dcf6d08b715231b9edcbe28b84da3db2523dfbb43f2c3989"


def test_promoted_template_matches_official_contract():
    contract = load_template_contract(CONTRACT)

    result = verify_official_template(TEMPLATE, contract)

    assert result.sha256 == OFFICIAL_SHA256
    assert result.sheet_states == {
        "Cotizacion": "visible",
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
    assert result.defined_name_count == 29
    assert result.external_link_parts == 12
    assert result.spec_formula_count == 1314


def test_modified_template_fails_before_output(tmp_path):
    changed = tmp_path / "changed.xlsx"
    changed.write_bytes(TEMPLATE.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="Plantilla oficial incompatible"):
        verify_official_template(changed, load_template_contract(CONTRACT))


def test_worker_default_template_is_the_promoted_official_copy():
    from mobiliti_saas.worker import quote_worker

    assert quote_worker._default_template() == TEMPLATE
