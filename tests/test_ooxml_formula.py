import xml.etree.ElementTree as ET

import pytest
from openpyxl.formula.tokenizer import Tokenizer

from mobiliti_saas.quote_engine.ooxml_formula import (
    FormulaTranslationError,
    formula_tokens,
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


def calc_chain_effective_entries(payload):
    root = ET.fromstring(payload)
    effective_sheet_id = None
    entries = []
    for cell in root:
        if "i" in cell.attrib:
            effective_sheet_id = cell.attrib["i"]
        entries.append((cell.attrib["r"], effective_sheet_id, dict(cell.attrib)))
    return entries


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


@pytest.mark.parametrize(
    ("formula", "origin", "target", "expected"),
    [
        ("='Source Sheet'!A1:B2", "A1", "C4", "='Source Sheet'!C4:D5"),
        ("=SUM(1:3,A:C)", "A1", "C4", "=SUM(4:6,C:E)"),
        ("=SUM(A1:A3,B1:B3)", "A1", "C4", "=SUM(C4:C6,D4:D6)"),
        ("=SUM((A1:A3,B1:B3))", "A1", "C4", "=SUM((C4:C6,D4:D6))"),
    ],
)
def test_translate_formula_handles_supported_qualified_whole_and_union_ranges(
    formula, origin, target, expected
):
    assert translate_formula(formula, origin=origin, target=target) == expected


def test_translate_formula_fails_closed_for_an_unmappable_range_token():
    with pytest.raises(FormulaTranslationError, match="Mobiliti!H47"):
        translate_formula("=SUM(Table1[Amount])", origin="H47", target="H114", sheet="Mobiliti")


def test_translate_formula_fails_closed_for_a_defined_name_with_context():
    with pytest.raises(FormulaTranslationError, match="Mobiliti!H47"):
        translate_formula("=NamedRange+H14", origin="H47", target="H114", sheet="Mobiliti")


def test_translate_formula_applies_overrides_to_whole_tokens_only():
    translated = translate_formula(
        "=SUM(H14:H46,H14:H460)",
        origin="H47",
        target="H114",
        range_overrides={"H81:H113": "H14:H113"},
    )

    assert translated == "=SUM(H14:H113,H81:H527)"


def test_calc_chain_maps_moved_and_cloned_cells():
    result = translate_calc_chain(
        CALC_CHAIN_XML,
        sheet_id=2,
        coordinate_map={"W14": ["W14", "W47"]},
    )

    assert calc_chain_coordinates(result, sheet_id=2) == {"W14", "W47"}
    assert calc_chain_coordinates(result, sheet_id=1) == {"D7"}


def test_calc_chain_maps_entries_that_inherit_the_previous_sheet_id():
    payload = b'''<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <c r="W14" i="2" keep="first"/>
  <c r="W15" keep="inherited"/>
  <c r="D7" i="1" keep="other"/>
  <c r="D8" keep="other-inherited"/>
  <c r="W16" i="2" keep="last"/>
</calcChain>'''

    result = translate_calc_chain(
        payload,
        sheet_id=2,
        coordinate_map={"W15": ["W15", "W48"]},
    )

    assert calc_chain_effective_entries(result) == [
        ("W14", "2", {"r": "W14", "i": "2", "keep": "first"}),
        ("W15", "2", {"r": "W15", "keep": "inherited"}),
        ("W48", "2", {"r": "W48", "keep": "inherited"}),
        ("D7", "1", {"r": "D7", "i": "1", "keep": "other"}),
        ("D8", "1", {"r": "D8", "keep": "other-inherited"}),
        ("W16", "2", {"r": "W16", "i": "2", "keep": "last"}),
    ]


def test_calc_chain_deduplicates_a_clone_that_already_exists_and_preserves_order():
    payload = b'''<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <c r="W14" i="2" keep="mapped"/>
  <c r="W47" i="2" keep="existing"/>
  <c r="D7" i="1" keep="other-sheet"/>
</calcChain>'''

    result = translate_calc_chain(
        payload,
        sheet_id=2,
        coordinate_map={"W14": ["W14", "W47"]},
    )

    assert calc_chain_effective_entries(result) == [
        ("W14", "2", {"r": "W14", "i": "2", "keep": "mapped"}),
        ("W47", "2", {"r": "W47", "i": "2", "keep": "existing"}),
        ("D7", "1", {"r": "D7", "i": "1", "keep": "other-sheet"}),
    ]


def test_calc_chain_deduplicates_against_final_unmapped_entries_not_moved_sources():
    payload = b'''<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <c r="W14" i="2" keep="first"/>
  <c r="W47" keep="second"/>
  <c r="W47" i="1" keep="other-sheet"/>
</calcChain>'''

    result = translate_calc_chain(
        payload,
        sheet_id=2,
        coordinate_map={"W14": ["W14", "W47"], "W47": ["W48"]},
    )

    assert calc_chain_effective_entries(result) == [
        ("W14", "2", {"r": "W14", "i": "2", "keep": "first"}),
        ("W47", "2", {"r": "W47", "i": "2", "keep": "first"}),
        ("W48", "2", {"r": "W48", "keep": "second"}),
        ("W47", "1", {"r": "W47", "i": "1", "keep": "other-sheet"}),
    ]


def test_calc_chain_materializes_inherited_sheet_after_explicit_entry_is_elided():
    payload = b'''<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <c r="D7" i="1" keep="first-other"/>
  <c r="W14" i="2" keep="elided-target"/>
  <c r="W15" keep="inherits-target"/>
  <c r="E8" i="1" keep="second-other"/>
  <c r="E9" keep="inherits-other"/>
</calcChain>'''

    result = translate_calc_chain(
        payload,
        sheet_id=2,
        coordinate_map={"W14": ["W15"]},
    )

    assert calc_chain_effective_entries(result) == [
        ("D7", "1", {"r": "D7", "i": "1", "keep": "first-other"}),
        ("W15", "2", {"r": "W15", "i": "2", "keep": "inherits-target"}),
        ("E8", "1", {"r": "E8", "i": "1", "keep": "second-other"}),
        ("E9", "1", {"r": "E9", "keep": "inherits-other"}),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        b"<calcChain/>",
        b'<calcChain xmlns="urn:not-main"><c r="W14" i="2"/></calcChain>',
        b'<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><x r="W14" i="2"/></calcChain>',
    ],
)
def test_calc_chain_rejects_invalid_root_namespace_or_direct_child(payload):
    with pytest.raises(FormulaTranslationError):
        translate_calc_chain(payload, sheet_id=2, coordinate_map={})


def test_calc_chain_rejects_malformed_xml_with_context():
    with pytest.raises(FormulaTranslationError, match="calcChain.xml no es XML válido"):
        translate_calc_chain(b"<calcChain><c", sheet_id=2, coordinate_map={})


@pytest.mark.parametrize(
    "formula",
    [
        "=SUM(IFERROR(H14:H46,0))+$K$6+Mobiliti!W14",
        "=IF(A14=\"H14:H46\",'Source Sheet'!$B14+C$4+$D$5,0)",
        "=SUM((A1:A3,B1:B3))",
        "=NamedRange+H14",
    ],
)
def test_formula_tokens_memoizado_equivale_al_tokenizer(formula):
    esperado = [(t.value, t.type, t.subtype) for t in Tokenizer(formula).items]

    for _ in range(2):
        tokens = formula_tokens(formula)
        assert isinstance(tokens, tuple)
        assert [(t.value, t.type, t.subtype) for t in tokens] == esperado


def test_translate_formula_repite_el_mismo_resultado_con_cache():
    argumentos = dict(
        origin="H47",
        target="H114",
        range_overrides={"H81:H113": "H14:H113"},
    )
    formula = "=SUM(IFERROR(H14:H46,0))+$K$6+'Source Sheet'!W14&\"H14:H46\""

    primera = translate_formula(formula, **argumentos)
    segunda = translate_formula(formula, **argumentos)

    assert primera == segunda == (
        "=SUM(IFERROR(H14:H113,0))+$K$6+'Source Sheet'!W81&\"H14:H46\""
    )


def test_translate_formula_no_reutiliza_el_contexto_de_un_fallo_anterior():
    # A1 no puede subir dos filas: el mismo rango falla con dos contextos distintos.
    with pytest.raises(FormulaTranslationError) as primero:
        translate_formula("=A1+1", origin="A3", target="A1", sheet="Mobiliti")
    with pytest.raises(FormulaTranslationError) as segundo:
        translate_formula("=A1+2", origin="A3", target="A1", sheet="Cotizacion")

    assert str(primero.value) == (
        "No se pudo mover el rango 'A1' (Mobiliti!A3; fórmula: =A1+1)"
    )
    assert str(segundo.value) == (
        "No se pudo mover el rango 'A1' (Cotizacion!A3; fórmula: =A1+2)"
    )


def test_translate_formula_invalida_sigue_fallando_en_la_segunda_llamada():
    for _ in range(2):
        with pytest.raises(FormulaTranslationError, match="Mobiliti!H47"):
            translate_formula(
                "=SUM(Table1[Amount])", origin="H47", target="H114", sheet="Mobiliti"
            )
        with pytest.raises(FormulaTranslationError, match="No se pudo tokenizar"):
            translate_formula('="sin cerrar', origin="H47", target="H114")
