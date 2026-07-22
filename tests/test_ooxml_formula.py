import xml.etree.ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.ooxml_formula import (
    FormulaTranslationError,
    translate_calc_chain,
    translate_formula,
)


CALC_CHAIN_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <c r="W14" i="2"/>
  <c r="D7" i="1"/>
</calcChain>'''


def calc_chain_coordinates(payload, sheet_id):
    root = ET.fromstring(payload)
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return {
        cell.attrib["r"]
        for cell in root.findall("main:c", namespace)
        if cell.attrib.get("i") == str(sheet_id)
    }


def test_translate_formula_preserves_absolute_and_expands_section_range():
    formula = "=SUM(IFERROR(H14:H46,0))+$K$6+Mobiliti!W14"

    translated = translate_formula(
        formula,
        origin="H47",
        target="H114",
        range_overrides={"H81:H113": "H14:H113"},
    )

    assert translated == "=SUM(IFERROR(H14:H113,0))+$K$6+Mobiliti!W81"


def test_translate_formula_leaves_quoted_text_and_mixed_references_intact_when_required():
    formula = '=IF(A14="H14:H46",\'Source Sheet\'!$B14+C$4+$D$5,0)'

    translated = translate_formula(formula, origin="H14", target="H47")

    assert translated == '=IF(A47="H14:H46",\'Source Sheet\'!$B47+C$4+$D$5,0)'


def test_translate_formula_fails_closed_for_an_unmappable_range_token():
    with pytest.raises(FormulaTranslationError, match="Mobiliti!H47"):
        translate_formula("=SUM(Table1[Amount])", origin="H47", target="H114", sheet="Mobiliti")


def test_calc_chain_maps_moved_and_cloned_cells():
    result = translate_calc_chain(
        CALC_CHAIN_XML,
        sheet_id=2,
        coordinate_map={"W14": ["W14", "W47"]},
    )

    assert calc_chain_coordinates(result, sheet_id=2) == {"W14", "W47"}
    assert calc_chain_coordinates(result, sheet_id=1) == {"D7"}
