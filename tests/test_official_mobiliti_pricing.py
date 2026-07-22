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
    original: object = Decimal("100.000000"),
    rate: object = Decimal("18.500000"),
    converted: object = Decimal("1850.00"),
) -> QuotationDataRow:
    return QuotationDataRow(
        item_key=key,
        section_id="section-1",
        section_title="SILLAS",
        position=1,
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


@pytest.mark.parametrize(
    ("original", "rate", "expected"),
    [
        ("100.000000", "18.500000", Decimal("1850.00")),
        ("100.000000", "0.054054", Decimal("5.41")),
        ("100.000000", "1.000000", Decimal("100.00")),
    ],
)
def test_frozen_cost_is_written_once_and_official_pricing_formulas_survive(
    original, rate, expected
):
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])
    rows = [_row("item-1", original=Decimal(original), rate=Decimal(rate), converted=expected)]
    writes = build_mobiliti_pricing_writes(rows, row_map)

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
    assert _formula_signature(_cell(output, "W14")) == _formula_signature(
        _cell(official, "W14")
    )
    assert _formula_signature(_cell(output, "X14")) == _formula_signature(
        _cell(official, "X14")
    )
    assert ET.tostring(_cell(output, "K6")) == ET.tostring(_cell(official, "K6"))

    # La fila amarilla sin producto conserva las fÃ³rmulas oficiales y no recibe costo.
    assert _cell(output, "J15").find(f"{{{MAIN}}}v") is None
    assert _cell(output, "W15").find(f"{{{MAIN}}}f") is not None
    assert _cell(output, "X15").find(f"{{{MAIN}}}f") is not None


def test_cost_writes_follow_every_item_row_in_exact_order_without_duplicates():
    needs = [SectionNeed("first", "PRIMERA", 2), SectionNeed("second", "SEGUNDA", 1)]
    row_map = plan_mobiliti_layout(needs)
    rows = [
        _row("a", original=Decimal("1"), rate=Decimal("1"), converted=Decimal("1.00")),
        replace(
            _row("b", original=Decimal("2"), rate=Decimal("1"), converted=Decimal("2.00")),
            position=2,
        ),
        replace(
            _row("c", original=Decimal("3"), rate=Decimal("1"), converted=Decimal("3.00")),
            position=3,
        ),
    ]

    writes = build_mobiliti_pricing_writes(rows, row_map)

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
        _row(f"item-{index}", original=Decimal("1"), rate=Decimal("1"), converted=Decimal("1.00"))
        for index in range(2 + delta)
    ]

    with pytest.raises(ValueError, match="cantidad.*filas Mobiliti"):
        build_mobiliti_pricing_writes(rows, row_map)


def test_cost_writes_reject_duplicate_canonical_keys():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 2)])
    rows = [_row("duplicate"), replace(_row("duplicate"), position=2)]

    with pytest.raises(ValueError, match="duplicad"):
        build_mobiliti_pricing_writes(rows, row_map)


def test_cost_writes_reject_converted_cost_mismatch_without_reconverting_it():
    row_map = plan_mobiliti_layout([SectionNeed("section-1", "SILLAS", 1)])

    with pytest.raises(ValueError, match="inconsistente"):
        build_mobiliti_pricing_writes(
            [_row("item-1", converted=Decimal("1849.99"))],
            row_map,
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
        build_mobiliti_pricing_writes([row], row_map)


def test_lumbro_accessory_is_a_frozen_decimal_and_never_a_k6_formula():
    cost = lumbro_frozen_cost(Decimal("100.000000"), Decimal("0.054054"))

    assert cost == Decimal("5.41")
    assert isinstance(cost, Decimal)
    assert "$K$6" not in str(cost)
    assert MobilitiCellWrite("J14", "number", cost).kind == "number"


@pytest.mark.parametrize("ambiguous", [None, "100", 100, True])
def test_lumbro_missing_or_ambiguous_price_fails_closed(ambiguous):
    with pytest.raises((TypeError, ValueError), match="Lumbro"):
        lumbro_frozen_cost(ambiguous, Decimal("1"))


def test_lumbro_missing_price_requires_explicit_zero_contract():
    assert lumbro_frozen_cost(
        None,
        Decimal("1"),
        missing_price_is_zero=True,
    ) == Decimal("0.00")


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
def test_currency_selector_rejects_unsafe_or_oversized_k8_text(delivery_place):
    editor = WorksheetEditor.from_xml(_official_xml())

    with pytest.raises((TypeError, ValueError), match="K8"):
        write_official_currency_selector(editor, "MXN", delivery_place)


@pytest.mark.parametrize(
    ("currency", "expected_boolean"),
    [("MXN", "0"), ("USD", "1"), ("EUR", "1")],
)
def test_currency_selector_writes_only_k4_and_safe_inline_k8_idempotently(
    currency, expected_boolean
):
    editor = WorksheetEditor.from_xml(_official_xml())
    before = {
        cell.attrib["r"]: ET.tostring(cell)
        for cell in editor.root.findall(f".//{{{MAIN}}}c")
    }

    write_official_currency_selector(editor, currency, " =NORTE & SUR ")
    once = editor.to_xml()
    write_official_currency_selector(editor, currency, " =NORTE & SUR ")
    twice = editor.to_xml()
    output = ET.fromstring(twice)

    assert once == twice
    k4 = _cell(output, "K4")
    assert k4.attrib["t"] == "b"
    assert k4.findtext(f"{{{MAIN}}}v") == expected_boolean
    k8 = _cell(output, "K8")
    assert k8.attrib["t"] == "inlineStr"
    assert k8.findtext(f"{{{MAIN}}}is/{{{MAIN}}}t") == "' =NORTE & SUR "
    assert _cell(output, "K6").find(f"{{{MAIN}}}f") is not None

    after = {
        cell.attrib["r"]: ET.tostring(cell)
        for cell in output.findall(f".//{{{MAIN}}}c")
    }
    changed = {
        coordinate for coordinate in before if before[coordinate] != after[coordinate]
    }
    assert changed <= {"K4", "K8"}
    assert "K8" in changed
    for forbidden in ("J6", "K6", "W14", "X14"):
        assert after[forbidden] == before[forbidden]
