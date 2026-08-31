from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import posixpath
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest
from openpyxl.formula.tokenizer import Tokenizer

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed, plan_mobiliti_layout
from mobiliti_saas.quote_engine.mobiliti_pricing import (
    PricingRowBinding,
    build_mobiliti_pricing_writes,
    lumbro_frozen_cost,
    write_official_currency_selector,
)
from mobiliti_saas.quote_engine.ooxml_worksheet import (
    MobilitiCellWrite,
    WorksheetEditor,
    build_mobiliti_sheet,
)
from mobiliti_saas.quote_engine.quotation_sheets import QuotationDataRow


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _official_part(path: Path, sheet_name: str) -> str:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(f"{{{PACKAGE_REL}}}Relationship")
    }
    sheet = next(
        node
        for node in workbook.findall(f".//{{{MAIN}}}sheet")
        if node.attrib["name"] == sheet_name
    )
    return posixpath.normpath(
        posixpath.join("xl", targets[sheet.attrib[f"{{{OFFICE_REL}}}id"]].lstrip("/"))
    )


def _official_xml() -> bytes:
    part = _official_part(OFFICIAL_TEMPLATE, "Mobiliti")
    with ZipFile(OFFICIAL_TEMPLATE) as archive:
        return archive.read(part)


def _cell(root: ET.Element, coordinate: str) -> ET.Element:
    cell = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']")
    assert cell is not None
    return cell


def _formula_signature(cell: ET.Element) -> tuple[tuple[str, str, str], ...]:
    formula = cell.find(f"{{{MAIN}}}f")
    assert formula is not None and formula.text
    return tuple(
        (token.type, token.subtype, token.value)
        for token in Tokenizer("=" + formula.text).items
    )


def _row(
    key: str,
    *,
    section_id: str = "section-1",
    position: int = 1,
    original: object = Decimal("100.000000"),
    rate: object = Decimal("18.500000"),
    converted: object = Decimal("1850.00"),
) -> QuotationDataRow:
    return QuotationDataRow(
        item_key=key,
        section_id=section_id,
        section_title="SILLAS",
        position=position,
        origin="catalog",
        source_row=None,
        original_currency="USD",
        original_cost=original,
        frozen_rate=rate,
        converted_cost=converted,
        quantity=Decimal("1"),
        provider="Proveedor",
        region="MX",
        source_hash="a" * 64,
        upstream_row_hash="",
        row_hash="b" * 64,
    )


def _binding(
    key: str,
    target_row: int,
    *,
    section_id: str = "section-1",
    position: int = 1,
    quotation_row: int | None = None,
    quotation_rate: Decimal = Decimal("1"),
) -> PricingRowBinding:
    return PricingRowBinding(
        item_key=key,
        section_id=section_id,
        position=position,
        target_row=target_row,
        quotation_row=quotation_row,
        quotation_rate=quotation_rate,
    )


def test_import_freight_exception_keeps_taxes_margins_and_manual_factor():
    needs = [SectionNeed("first", "SILLAS", 2), SectionNeed("second", "OTRAS", 1)]
    mutation = build_mobiliti_sheet(
        _official_xml(),
        needs,
        (
            MobilitiCellWrite("F14", "text", "Alma - Exterior"),
            MobilitiCellWrite("P14", "number", Decimal("0")),
            MobilitiCellWrite("F15", "text", "Sunon Inc"),
            MobilitiCellWrite("P15", "number", Decimal("0.42")),
            MobilitiCellWrite("F49", "text", "Offiho"),
            MobilitiCellWrite("P49", "number", Decimal("0")),
        ),
    )
    output = ET.fromstring(mutation.xml)
    official = ET.fromstring(_official_xml())

    for row in (14, 15, 49):
        formula = _cell(output, f"L{row}").find(f"{{{MAIN}}}f")
        assert formula is not None
        # También funciona si otros productos sí aportan m3 al mismo proyecto.
        assert formula.text == (
            f'IF(K{row}="Importado",IF(OR(P{row}>0,Fletes!$E$60="MANUAL"),'
            'Fletes!$B$66,IF(Fletes!$B$61=0,0,'
            'Fletes!$B$65+Fletes!$B$78/Fletes!$B$61)),0%)'
        )
        for column in ("M", "N", "O", "Y", "Z"):
            assert _formula_signature(_cell(output, f"{column}{row}")) == (
                _formula_signature(_cell(official, f"{column}{row}"))
            )


@pytest.mark.parametrize(
    ("original", "rate", "expected"),
    [
        ("100.000000", "18.500000", Decimal("1850.00")),
        ("100.000000", "0.054054", Decimal("5.41")),
        ("100.000000", "1.000000", Decimal("100.00")),
    ],
)
def test_frozen_cost_keeps_raw_price_and_applies_uniform_sale_price(
    original, rate, expected
):
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    rows = [_row("item-1", original=Decimal(original), rate=Decimal(rate), converted=expected)]
    writes = build_mobiliti_pricing_writes(
        rows,
        row_map,
        bindings=(_binding("item-1", 14),),
    )

    assert writes == (MobilitiCellWrite("J14", "number", expected),)
    mutation = build_mobiliti_sheet(
        _official_xml(),
        [SectionNeed("section-1", "SILLAS", 1)],
        writes,
    )
    official = ET.fromstring(_official_xml())
    output = ET.fromstring(mutation.xml)

    j14 = _cell(output, "J14")
    assert j14.attrib.get("t") is None
    assert j14.find(f"{{{MAIN}}}f") is None
    assert Decimal(j14.findtext(f"{{{MAIN}}}v")) == expected
    assert _formula_signature(_cell(output, "Z14")) == _formula_signature(
        _cell(official, "Z14")
    )
    assert "_xlfn.MAXIFS(" in _cell(output, "AA14").findtext(f"{{{MAIN}}}f")
    assert _cell(output, "AB14").findtext(f"{{{MAIN}}}f") == (
        "IFERROR(AA14*H14,0)"
    )
    assert ET.tostring(_cell(output, "P6")) == ET.tostring(_cell(official, "P6"))

    # La fila amarilla sin producto conserva las fórmulas oficiales y no recibe costo.
    assert _cell(output, "J15").find(f"{{{MAIN}}}v") is None
    assert _cell(output, "Z15").find(f"{{{MAIN}}}f") is not None
    assert _cell(output, "AA15").find(f"{{{MAIN}}}f") is not None


def test_same_name_uses_price_from_largest_quantity_and_totals_that_price():
    needs = [
        SectionNeed("large", "SILLAS 60", 1),
        SectionNeed("small", "SILLAS 12", 1),
    ]
    row_map = plan_mobiliti_layout(needs)
    large_row, small_row = row_map.item_rows
    writes = [
        MobilitiCellWrite(f"D{large_row}", "text", "CHT85SW H2 Task Chair"),
        MobilitiCellWrite(f"H{large_row}", "number", Decimal("60")),
        MobilitiCellWrite(f"D{small_row}", "text", "CHT85SW H2 Task Chair"),
        MobilitiCellWrite(f"H{small_row}", "number", Decimal("12")),
    ]

    mutation = build_mobiliti_sheet(_official_xml(), needs, writes)
    output = ET.fromstring(mutation.xml)
    last_row = row_map.last_product_row

    for row in (large_row, small_row):
        uniform_formula = _cell(output, f"AA{row}").findtext(f"{{{MAIN}}}f")
        assert uniform_formula == (
            f"IF(Z{row}>=Y{row},"
            f"_xlfn.MINIFS($Z$14:$Z${last_row},"
            f"$D$14:$D${last_row},D{row},"
            f"$H$14:$H${last_row},"
            f"_xlfn.MAXIFS($H$14:$H${last_row},"
            f"$D$14:$D${last_row},D{row})),"
            '"NO SE ESTA RESPETANDO EL MARGEN")'
        )
        assert _cell(output, f"AB{row}").findtext(f"{{{MAIN}}}f") == (
            f"IFERROR(AA{row}*H{row},0)"
        )
        assert _cell(output, f"AE{row}").findtext(f"{{{MAIN}}}f") == (
            f"IFERROR(AA{row}*AD{row},0)"
        )
        assert _cell(output, f"AF{row}").findtext(f"{{{MAIN}}}f") == (
            f'IF($E$5>$E$6,"ERROR",AA{row}-AE{row})'
        )
        assert _cell(output, f"AG{row}").findtext(f"{{{MAIN}}}f") == (
            f"AF{row}*H{row}"
        )
        assert _cell(output, f"AH{row}").findtext(f"{{{MAIN}}}f") == (
            f'IF(A{row + 1}=TRUE,MAX(0,1-(AI{row}/Z{row})),"NA")'
        )


def test_project_cost_references_the_final_quotation_row_instead_of_freezing_a_number():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    writes = build_mobiliti_pricing_writes(
        [_row("item-1")],
        row_map,
        bindings=(_binding("item-1", 14, quotation_row=27),),
    )

    assert writes == (MobilitiCellWrite("J14", "formula", "=Quotation!K27"),)


def test_imported_cost_references_quotation_and_converts_exactly_once():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    writes = build_mobiliti_pricing_writes(
        [_row("item-1")],
        row_map,
        bindings=(
            _binding(
                "item-1",
                14,
                quotation_row=11,
                quotation_rate=Decimal("18.500000"),
            ),
        ),
    )

    assert writes == (
        MobilitiCellWrite(
            "J14",
            "formula",
            "=ROUND(Quotation!K11*18.500000,2)",
        ),
    )


def test_cost_writes_follow_every_item_row_in_exact_order_without_duplicates():
    needs = [SectionNeed("first", "PRIMERA", 2), SectionNeed("second", "SEGUNDA", 1)]
    row_map = plan_mobiliti_layout(needs)
    item_rows = row_map.item_rows
    rows = [
        _row(
            "a",
            section_id="first",
            position=1,
            original=Decimal("1"),
            rate=Decimal("1"),
            converted=Decimal("1.00"),
        ),
        _row(
            "b",
            section_id="first",
            position=2,
            original=Decimal("2"),
            rate=Decimal("1"),
            converted=Decimal("2.00"),
        ),
        _row(
            "c",
            section_id="second",
            position=3,
            original=Decimal("3"),
            rate=Decimal("1"),
            converted=Decimal("3.00"),
        ),
    ]
    bindings = (
        _binding("a", item_rows[0], section_id="first", position=1),
        _binding("b", item_rows[1], section_id="first", position=2),
        _binding("c", item_rows[2], section_id="second", position=3),
    )

    writes = build_mobiliti_pricing_writes(rows, row_map, bindings=bindings)

    assert tuple(write.coordinate for write in writes) == tuple(
        f"J{row}" for row in row_map.item_rows
    )
    assert tuple(write.value for write in writes) == (
        Decimal("1.00"),
        Decimal("2.00"),
        Decimal("3.00"),
    )
    assert all(write.kind == "number" for write in writes)


@pytest.mark.parametrize("delta", [-1, 1])
def test_cost_writes_reject_row_count_mismatch_instead_of_truncating(delta):
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 2)])
    rows = [
        _row(
            f"item-{index}",
            position=index + 1,
            original=Decimal("1"),
            rate=Decimal("1"),
            converted=Decimal("1.00"),
        )
        for index in range(2 + delta)
    ]
    bindings = tuple(
        _binding(f"item-{index}", target_row, position=index + 1)
        for index, target_row in enumerate(row_map.item_rows)
    )

    with pytest.raises(ValueError, match="cantidad.*filas Mobiliti"):
        build_mobiliti_pricing_writes(rows, row_map, bindings=bindings)


def test_cost_writes_reject_duplicate_canonical_keys():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 2)])
    rows = [_row("duplicate"), replace(_row("duplicate"), position=2)]
    bindings = tuple(
        _binding("duplicate", target_row, position=index)
        for index, target_row in enumerate(row_map.item_rows, start=1)
    )

    with pytest.raises(ValueError, match="duplicad"):
        build_mobiliti_pricing_writes(rows, row_map, bindings=bindings)


def test_cost_writes_reject_swapped_items_even_when_positions_are_rewritten_validly():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 2)])
    rows = [
        _row(
            "item-b",
            position=1,
            original=Decimal("2"),
            rate=Decimal("1"),
            converted=Decimal("2.00"),
        ),
        _row(
            "item-a",
            position=2,
            original=Decimal("1"),
            rate=Decimal("1"),
            converted=Decimal("1.00"),
        ),
    ]
    bindings = (
        _binding("item-a", row_map.item_rows[0], position=1),
        _binding("item-b", row_map.item_rows[1], position=2),
    )

    with pytest.raises(ValueError, match="[Ii]dentidad.*item_key"):
        build_mobiliti_pricing_writes(rows, row_map, bindings=bindings)


def test_cost_writes_require_independent_bindings_and_validate_target_identity():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    rows = [_row("item-a")]

    with pytest.raises(TypeError, match="bindings"):
        build_mobiliti_pricing_writes(rows, row_map)
    binding = _binding("item-a", 14)
    tampered = (
        (replace(binding, item_key="item-b"), "item_key"),
        (replace(binding, section_id="other"), "section_id"),
        (replace(binding, position=2), "position"),
        (replace(binding, target_row=15), "target_row"),
    )
    for changed_binding, message in tampered:
        with pytest.raises(ValueError, match=message):
            build_mobiliti_pricing_writes(
                rows,
                row_map,
                bindings=(changed_binding,),
            )


def test_cost_writes_reject_converted_cost_mismatch_without_reconverting_it():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])

    with pytest.raises(ValueError, match="inconsistente"):
        build_mobiliti_pricing_writes(
            [_row("item-1", converted=Decimal("1849.99"))],
            row_map,
            bindings=(_binding("item-1", 14),),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("original_cost", Decimal("-0.01"), "original_cost"),
        ("converted_cost", Decimal("-0.01"), "converted_cost"),
        ("frozen_rate", Decimal("0"), "frozen_rate"),
        ("frozen_rate", Decimal("-1"), "frozen_rate"),
        ("original_cost", Decimal("NaN"), "original_cost"),
        ("converted_cost", Decimal("Infinity"), "converted_cost"),
        ("frozen_rate", Decimal("-Infinity"), "frozen_rate"),
        ("original_cost", True, "original_cost"),
        ("converted_cost", False, "converted_cost"),
        ("frozen_rate", True, "frozen_rate"),
        ("original_cost", Decimal("1000000000000"), "NUMERIC\\(18,6\\)"),
        ("frozen_rate", Decimal("1.0000001"), "NUMERIC\\(18,6\\)"),
    ],
)
def test_cost_writes_reject_invalid_numeric_contract(field, value, message):
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    row = replace(_row("item-1"), **{field: value})

    with pytest.raises((TypeError, ValueError), match=message):
        build_mobiliti_pricing_writes(
            [row],
            row_map,
            bindings=(_binding("item-1", 14),),
        )


def test_real_quotation_data_row_accepts_numeric_18_2_maximum_converted_cost():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    maximum = Decimal("9999999999999999.99")
    canonical = _row(
        "item-1",
        original=Decimal("999999999999.999999"),
        rate=Decimal("10000.000000"),
        converted=maximum,
    )

    assert isinstance(canonical, QuotationDataRow)
    assert build_mobiliti_pricing_writes(
        [canonical],
        row_map,
        bindings=(_binding("item-1", 14),),
    ) == (MobilitiCellWrite("J14", "number", maximum),)


@pytest.mark.parametrize(
    ("converted", "message"),
    [
        (Decimal("10000000000000000.00"), "NUMERIC\\(18,2\\)"),
        (Decimal("1.000"), "NUMERIC\\(18,2\\)"),
    ],
    ids=("seventeen-integral-digits", "scale-three"),
)
def test_converted_cost_rejects_numeric_18_2_magnitude_and_scale(
    converted, message
):
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    if converted == Decimal("1.000"):
        original = rate = Decimal("1.000000")
    else:
        original = Decimal("100000000000.000000")
        rate = Decimal("100000.000000")

    with pytest.raises(ValueError, match=message):
        build_mobiliti_pricing_writes(
            [_row("item-1", original=original, rate=rate, converted=converted)],
            row_map,
            bindings=(_binding("item-1", 14),),
        )


def test_pricing_detects_post_multiplication_numeric_18_2_overflow():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])

    with pytest.raises(ValueError, match="converted_cost.*NUMERIC\\(18,2\\)"):
        build_mobiliti_pricing_writes(
            [
                _row(
                    "item-1",
                    original=Decimal("100000000000.000000"),
                    rate=Decimal("100000.000000"),
                    converted=Decimal("0.00"),
                )
            ],
            row_map,
            bindings=(_binding("item-1", 14),),
        )


def test_lumbro_accessory_is_a_frozen_decimal_and_never_a_k6_formula():
    cost = lumbro_frozen_cost(Decimal("100.000000"), Decimal("0.054054"))

    assert cost == Decimal("5.41")
    assert isinstance(cost, Decimal)
    assert "$K$6" not in str(cost)
    assert MobilitiCellWrite("J14", "number", cost).kind == "number"


def test_lumbro_accepts_numeric_18_2_maximum_and_rejects_overflow():
    assert lumbro_frozen_cost(
        Decimal("999999999999.999999"),
        Decimal("10000.000000"),
    ) == Decimal("9999999999999999.99")

    with pytest.raises(ValueError, match="Lumbro.*NUMERIC\\(18,2\\)"):
        lumbro_frozen_cost(
            Decimal("100000000000.000000"),
            Decimal("100000.000000"),
        )


@pytest.mark.parametrize("ambiguous", [None, "100", 100, True])
def test_lumbro_missing_or_ambiguous_price_fails_closed(ambiguous):
    with pytest.raises((TypeError, ValueError), match="Lumbro"):
        lumbro_frozen_cost(ambiguous, Decimal("1"))


def test_lumbro_none_always_fails_and_decimal_zero_is_the_only_zero_contract():
    with pytest.raises((TypeError, ValueError), match="Lumbro"):
        lumbro_frozen_cost(None, Decimal("1"))

    assert lumbro_frozen_cost(Decimal("0"), Decimal("1")) == Decimal("0.00")

    with pytest.raises(TypeError, match="missing_price_is_zero"):
        lumbro_frozen_cost(
            None,
            Decimal("1"),
            missing_price_is_zero=True,
        )


@pytest.mark.parametrize("currency", ["", "mxn", "GBP", None, True])
def test_currency_selector_rejects_every_currency_outside_the_closed_set(currency):
    editor = WorksheetEditor.from_xml(_official_xml())

    with pytest.raises((TypeError, ValueError), match="Moneda"):
        write_official_currency_selector(editor, currency, "Guadalajara")


@pytest.mark.parametrize(
    "delivery_place",
    [
        pytest.param("control\x01", id="control"),
        pytest.param("surrogate\ud800", id="surrogate"),
        pytest.param("x" * 32_768, id="oversized"),
    ],
)
def test_currency_selector_rejects_unsafe_or_oversized_delivery_text(delivery_place):
    editor = WorksheetEditor.from_xml(_official_xml())

    with pytest.raises((TypeError, ValueError), match="Lugar de entrega"):
        write_official_currency_selector(editor, "MXN", delivery_place)


@pytest.mark.parametrize(
    "invisible",
    [
        pytest.param("\ufeff=WEBSERVICE()", id="bom"),
        pytest.param("\u200b+1", id="zero-width-space"),
        pytest.param("\u2060@SUM", id="word-joiner"),
        pytest.param("\u2066=WEBSERVICE()", id="left-to-right-isolate"),
        pytest.param("\u2061+1", id="function-application"),
        pytest.param("\u200e@SUM", id="left-to-right-mark"),
        pytest.param("\u202e-1", id="right-to-left-override"),
        pytest.param("\u180e=1", id="mongolian-vowel-separator"),
    ],
)
def test_currency_selector_rejects_invisible_formula_prefixes(invisible):
    editor = WorksheetEditor.from_xml(_official_xml())
    before = editor.to_xml()

    with pytest.raises(ValueError, match="Lugar de entrega.*invisible"):
        write_official_currency_selector(editor, "MXN", invisible)

    assert editor.to_xml() == before


def test_currency_selector_rechecks_delivery_limit_after_formula_neutralization():
    editor = WorksheetEditor.from_xml(_official_xml())
    before = editor.to_xml()
    maximum_length_formula = "=" + ("x" * 32_766)

    with pytest.raises(ValueError, match="Lugar de entrega.*32767"):
        write_official_currency_selector(editor, "MXN", maximum_length_formula)

    assert editor.to_xml() == before


def test_currency_selector_is_atomic_when_p4_destination_is_absent():
    editor = WorksheetEditor.from_xml(_official_xml())
    row = editor.require_row(4)
    p4 = row.find(f"{{{MAIN}}}c[@r='P4']")
    assert p4 is not None
    row.remove(p4)
    before = editor.to_xml()

    with pytest.raises(ValueError, match="P4"):
        write_official_currency_selector(editor, "MXN", "Guadalajara")

    assert editor.to_xml() == before


def test_task7_sources_are_strict_utf8_without_mojibake():
    paths = (
        ROOT / "mobiliti_saas" / "quote_engine" / "mobiliti_pricing.py",
        ROOT / "mobiliti_saas" / "quote_engine" / "ooxml_worksheet.py",
        ROOT / "tests" / "test_official_mobiliti_pricing.py",
        ROOT / "tests" / "test_mobiliti_ooxml_expansion.py",
    )
    decoded = tuple(
        path.read_bytes().decode("utf-8", errors="strict") for path in paths
    )

    assert "num\u00e9ricas" in decoded[0]
    assert not any(
        marker in source
        for source in decoded
        for marker in ("\u00c3", "\u00c2", "\u00e2\u20ac")
    )


@pytest.mark.parametrize(
    ("currency", "expected_boolean"),
    [("MXN", "0"), ("USD", "1"), ("EUR", "1")],
)
def test_currency_selector_writes_only_p4_and_global_discount_idempotently(
    currency, expected_boolean
):
    editor = WorksheetEditor.from_xml(_official_xml())
    before = {
        cell.attrib["r"]: ET.tostring(cell)
        for cell in editor.root.findall(f".//{{{MAIN}}}c")
    }

    write_official_currency_selector(
        editor,
        currency,
        " =NORTE & SUR ",
        Decimal("0.4"),
    )
    once = editor.to_xml()
    write_official_currency_selector(
        editor,
        currency,
        " =NORTE & SUR ",
        Decimal("0.4"),
    )
    twice = editor.to_xml()
    output = ET.fromstring(twice)

    assert once == twice
    p4 = _cell(output, "P4")
    assert p4.attrib["t"] == "b"
    assert p4.findtext(f"{{{MAIN}}}v") == expected_boolean
    assert Decimal(_cell(output, "AD13").findtext(f"{{{MAIN}}}v")) == Decimal("0.4")
    assert _cell(output, "P6").find(f"{{{MAIN}}}f") is not None

    after = {
        cell.attrib["r"]: ET.tostring(cell)
        for cell in output.findall(f".//{{{MAIN}}}c")
    }
    changed = {
        coordinate for coordinate in before if before[coordinate] != after[coordinate]
    }
    assert changed <= {"P4", "AD13"}
    assert changed == ({"P4", "AD13"} if expected_boolean == "0" else {"AD13"})
    for forbidden in ("J6", "P6", "Z14", "AA14"):
        assert after[forbidden] == before[forbidden]
