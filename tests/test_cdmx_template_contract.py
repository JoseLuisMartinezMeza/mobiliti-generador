from __future__ import annotations

import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from mobiliti_saas.quote_engine.official_template import (
    inspect_template,
    load_template_contract,
    verify_official_template,
)
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "mobiliti_saas" / "worker" / "templates"
OFFICIAL = TEMPLATES / "Formato Cotizacion 2026 Oficial.xlsx"
CDMX = TEMPLATES / "Formato Cotizacion Sunon CDMX V1C.xlsx"
CONTRACT = TEMPLATES / "formato-cotizacion-sunon-cdmx-v1c.contract.json"
OFFICIAL_SHA256 = "25f79e3ae533aa8f560be3e80586c19993ea65c0a07c500eb458738f9915b251"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
FIXED_RATE = re.compile(r"/\s*(?:18(?:\.0+)?|18\.5(?:0+)?)\b")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sheet_formulas(package: XlsxPackage, sheet_name: str) -> list[str]:
    root = ET.fromstring(package.parts[package.sheet_part(sheet_name)])
    return [
        formula.text or ""
        for formula in root.findall(f".//{{{MAIN}}}f")
    ]


def _row_style_signature(package: XlsxPackage, sheet_name: str, row: int) -> tuple[str, ...]:
    root = ET.fromstring(package.parts[package.sheet_part(sheet_name)])
    cells = root.findall(f".//{{{MAIN}}}row[@r='{row}']/{{{MAIN}}}c")
    return tuple(cell.attrib.get("s", "0") for cell in cells)


def test_cdmx_asset_is_separate_and_matches_its_contract() -> None:
    assert CDMX.exists()
    assert CONTRACT.exists()
    assert CDMX.read_bytes() != OFFICIAL.read_bytes()

    contract = load_template_contract(CONTRACT)
    inspection = verify_official_template(CDMX, contract)

    assert inspection.sha256 == contract.sha256 == _sha256(CDMX)
    assert _sha256(OFFICIAL) == OFFICIAL_SHA256
    assert set(contract.mutable_sheets) == {
        "Mobiliti",
        "Cotizacion",
        "Cantidades Lumbro ",
    }


def test_cdmx_asset_keeps_official_technical_sheets_and_adds_only_quantities() -> None:
    official = inspect_template(OFFICIAL)
    cdmx = inspect_template(CDMX)

    assert set(official.sheet_states).issubset(cdmx.sheet_states)
    assert cdmx.sheet_states["Cotizacion"] == "visible"
    assert cdmx.sheet_states["Cantidades Lumbro "] == "visible"
    assert "COSTO LUMBRO " not in cdmx.sheet_states
    assert "Quotation" not in cdmx.sheet_states
    assert cdmx.external_link_parts == official.external_link_parts


def test_cdmx_lumbro_quantities_have_no_sample_or_fixed_exchange_rate_formulas() -> None:
    package = XlsxPackage.read(CDMX)

    formulas = _sheet_formulas(package, "Cantidades Lumbro ")
    assert not any(FIXED_RATE.search(formula) for formula in formulas)
    assert not any("Formato-Cotizacion-Unico" in formula for formula in formulas)
    assert not any("[" in formula or "]" in formula for formula in formulas)
    assert "COSTO LUMBRO " not in inspect_template(CDMX).sheet_states


def test_cdmx_asset_has_its_own_cotizacion_presentation_archetypes() -> None:
    official = XlsxPackage.read(OFFICIAL)
    cdmx = XlsxPackage.read(CDMX)

    assert _row_style_signature(cdmx, "Cotizacion", 15)
    assert _row_style_signature(cdmx, "Cotizacion", 16)
    assert _row_style_signature(cdmx, "Cotizacion", 17)
    assert (
        _row_style_signature(cdmx, "Cotizacion", 17)
        != _row_style_signature(official, "Cotizacion", 17)
    )
