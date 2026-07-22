from copy import deepcopy
from collections.abc import Sequence
from decimal import Decimal, ROUND_HALF_UP
import builtins
import xml.etree.ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.mixed_catalog import validate_mixed_catalog_payload
from mobiliti_saas.quote_engine.mixed_catalog import build_mixed_catalog_cart_payload
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from mobiliti_saas.quote_engine.quotation_sheets import (
    QUOTATION_DATA_HEADERS,
    QuotationDataRow,
    SheetAddition,
    build_quotation_data_sheet,
    quotation_data_rows,
)
import mobiliti_saas.quote_engine.quotation_sheets as quotation_sheets
from test_mixed_catalog_workbook import frozen_payload
from test_mixed_catalog_cart import IMPORT_ID, mixed_catalogs, rate_rows
from quotation_import_fixtures import write_import_fixture


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"main": MAIN}


@pytest.fixture
def mixed_payload():
    return frozen_payload()


def _lines_by_key(payload):
    return {
        line["canonical_key"]: line
        for group in payload["groups"]
        for line in group["items"]
    }


def test_quotation_data_contains_all_lines_in_user_order(mixed_payload):
    rows = quotation_data_rows(mixed_payload)
    expected_keys = [
        item_key
        for section in mixed_payload["sections"]
        for item_key in section["item_keys"]
    ]
    lines = _lines_by_key(mixed_payload)

    assert [row.item_key for row in rows] == expected_keys
    assert [row.position for row in rows] == list(range(1, len(rows) + 1))
    assert all(row.converted_cost == Decimal(lines[row.item_key]["unit_price"]) for row in rows)
    assert all(
        row.converted_cost
        == (row.original_cost * row.frozen_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        for row in rows
    )
    assert all(len(row.row_hash) == 64 for row in rows)


def test_quotation_data_is_very_hidden_with_only_inline_strings(mixed_payload):
    addition = build_quotation_data_sheet(quotation_data_rows(mixed_payload))
    root = ET.fromstring(addition.xml)
    values = [
        text.text or ""
        for text in root.findall(".//main:t", NS)
    ]

    assert addition.name == "Quotation_Data"
    assert addition.state == "veryHidden"
    assert addition.parts == {}
    assert root.find("main:dimension", NS).attrib["ref"] == f"A1:P{mixed_payload['item_count'] + 1}"
    assert len(root.findall("main:sheetData/main:row", NS)) == mixed_payload["item_count"] + 1
    assert values[: len(QUOTATION_DATA_HEADERS)] == list(QUOTATION_DATA_HEADERS)
    assert not root.findall(".//main:f", NS)
    assert not any("http://" in value.casefold() or "https://" in value.casefold() for value in values)


def test_quotation_data_rejects_formula_like_section_titles_before_xml(mixed_payload):
    mixed_payload["sections"][0]["title"] = "=HYPERLINK(\"https://invalid.example\")"
    assert validate_mixed_catalog_payload(mixed_payload) is mixed_payload

    with pytest.raises(ValueError, match="Texto de Quotation_Data inseguro"):
        quotation_data_rows(mixed_payload)


def test_quotation_data_keeps_imported_source_rows_verifiable(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")
    manifest, _images = build_import_manifest(source.read_bytes(), IMPORT_ID, source.name)
    item_key = f"import:{IMPORT_ID}:11"
    payload = build_mixed_catalog_cart_payload(
        [],
        catalogs=mixed_catalogs.__wrapped__(),
        rate_rows=rate_rows.__wrapped__(),
        quote_currency="MXN",
        commercial_discount_percent="40",
        presentation_sections=[{
            "id": "section-1", "title": "Recepcion", "item_keys": [item_key],
        }],
        imported_source={
            "manifest": manifest,
            "source_currency": "USD",
            "items": [{
                "kind": "imported", "import_id": IMPORT_ID, "source_row": 11,
                "source_currency": "USD", "quantity": "2",
                "overrides": {
                    "name": "Alien Task Chair", "description": "Silla operativa",
                    "dimension": "630 x 565 x 1000 mm", "unit_price": "82.00",
                    "provider": "Sunon",
                },
            }],
        },
    )

    rows = quotation_data_rows(payload)

    assert rows[0].source_row == 11
    assert rows[0].origin == "imported"
    assert rows[0].source_hash == manifest["source_hash"]
    assert rows[0].upstream_row_hash == payload["imported_source"]["items"][0]["row_hash"]
    assert build_quotation_data_sheet(rows).name == "Quotation_Data"


def test_quotation_data_local_preflight_has_no_500_line_cap(mixed_payload):
    payload = deepcopy(mixed_payload)
    line = payload["groups"][0]["items"][0]
    payload["groups"] = [payload["groups"][0]]
    payload["groups"][0]["items"] = [
        {**line, "canonical_key": f"tarkett:{index}"}
        for index in range(501)
    ]
    payload["sections"] = [{
        "id": "section-1", "title": "Recepcion",
        "item_keys": [line["canonical_key"] for line in payload["groups"][0]["items"]],
    }]
    payload["item_count"] = 501

    assert len(quotation_data_rows(payload)) == 501


def test_quotation_data_canonicalizes_decimal_values_and_hashes(mixed_payload):
    payload = deepcopy(mixed_payload)
    line = payload["groups"][0]["items"][0]
    line.update(
        original_unit_price="1.00", frozen_exchange_rate="1.0",
        unit_price="1.00", quantity="1.000000",
    )

    first = quotation_data_rows(payload)[0]
    payload["groups"][0]["items"][0].update(
        original_unit_price="1", frozen_exchange_rate="1.000000",
        unit_price="1", quantity="1",
    )
    second = quotation_data_rows(payload)[0]

    assert first.original_cost == second.original_cost == Decimal("1")
    assert first.row_hash == second.row_hash
    assert ">1<" in build_quotation_data_sheet((first,)).xml.decode("utf-8")


def test_quotation_data_hash_binds_imported_upstream_row_hash(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")
    manifest, _images = build_import_manifest(source.read_bytes(), IMPORT_ID, source.name)
    item_key = f"import:{IMPORT_ID}:11"
    source_payload = {
        "manifest": manifest, "source_currency": "USD",
        "items": [{
            "kind": "imported", "import_id": IMPORT_ID, "source_row": 11,
            "source_currency": "USD", "quantity": "2",
            "overrides": {"name": "Alien Task Chair", "description": "Silla operativa", "dimension": "630 x 565 x 1000 mm", "unit_price": "82.00", "provider": "Sunon"},
        }],
    }
    payload = build_mixed_catalog_cart_payload(
        [], catalogs=mixed_catalogs.__wrapped__(), rate_rows=rate_rows.__wrapped__(),
        quote_currency="MXN", commercial_discount_percent="40",
        presentation_sections=[{"id": "section-1", "title": "Recepcion", "item_keys": [item_key]}],
        imported_source=source_payload,
    )
    first = quotation_data_rows(payload)[0]
    payload["imported_source"]["items"][0]["row_hash"] = "f" * 64
    second = quotation_data_rows(payload)[0]

    assert first.upstream_row_hash != second.upstream_row_hash
    assert first.row_hash != second.row_hash


@pytest.mark.parametrize("title", ("- Recepción", "+ IVA", "@Proveedor"))
def test_quotation_data_keeps_legitimate_inline_text(title, mixed_payload):
    mixed_payload["sections"][0]["title"] = title

    assert quotation_data_rows(mixed_payload)[0].section_title == title


@pytest.mark.parametrize("title", (
    "%68%74%74%70%73%3A%2F%2Finvalid.example", "file:///C:/temp/a.xlsx",
    "\\\\server\\share\\temp\\a.xlsx", "data:image/png;base64,AAAA", "blob:unsafe",
    "\ufeffhttps://invalid.example", "texto\u200bhttps://invalid.example", "\ufffe",
))
def test_quotation_data_rejects_encoded_or_invisible_unsafe_text(title, mixed_payload):
    mixed_payload["sections"][0]["title"] = title

    with pytest.raises(ValueError, match="Texto de Quotation_Data inseguro"):
        quotation_data_rows(mixed_payload)


def test_quotation_data_sheet_checks_physical_limit_before_iteration():
    class OversizeRows(Sequence):
        def __len__(self):
            return 1_048_576

        def __getitem__(self, index):
            raise AssertionError("No debe indexar")

        def __iter__(self):
            raise AssertionError("No debe iterar")

    with pytest.raises(ValueError, match="límite físico"):
        build_quotation_data_sheet(OversizeRows())


def test_sheet_addition_parts_are_deeply_immutable():
    source = {"xl/worksheets/sheet99.xml": b"original"}
    addition = SheetAddition("Quotation_Data", "veryHidden", b"<worksheet/>", source)
    source["xl/worksheets/sheet99.xml"] = b"changed"

    assert addition.parts["xl/worksheets/sheet99.xml"] == b"original"
    with pytest.raises(TypeError):
        addition.parts["new"] = b"nope"


def test_quotation_data_row_rejects_boolean_decimal_and_source_row():
    row = QuotationDataRow(
        item_key="tarkett:1", section_id="section-1", section_title="Recepcion",
        position=1, origin="imported", source_row=True, original_currency="MXN",
        original_cost=Decimal("1"), frozen_rate=Decimal("1"), converted_cost=Decimal("1"),
        quantity=Decimal("1"), provider="Tarkett", region="imported",
        source_hash="a" * 64, upstream_row_hash="b" * 64, row_hash="c" * 64,
    )

    with pytest.raises(ValueError, match="source_row"):
        build_quotation_data_sheet((row,))


@pytest.mark.parametrize("mutate", (
    lambda payload: payload.update(item_count=payload["item_count"] + 1),
    lambda payload: payload["sections"][0]["item_keys"].append(payload["sections"][0]["item_keys"][0]),
    lambda payload: payload["sections"][0]["item_keys"].pop(),
    lambda payload: payload["groups"][0]["items"][0].update(catalog="spoof"),
))
def test_quotation_data_rejects_count_order_and_metadata_tampering(mixed_payload, mutate):
    mutate(mixed_payload)

    with pytest.raises(ValueError):
        quotation_data_rows(mixed_payload)


@pytest.mark.parametrize("value", ("1E+999999", "0.0000001", "1234567890123"))
def test_quotation_data_rejects_decimal_exponents_scale_and_magnitude(mixed_payload, value):
    mixed_payload["groups"][0]["items"][0]["original_unit_price"] = value

    with pytest.raises(ValueError, match="original_cost"):
        quotation_data_rows(mixed_payload)


def test_quotation_data_normalizes_negative_zero_and_accepts_source_row_one(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")
    manifest, _images = build_import_manifest(source.read_bytes(), IMPORT_ID, source.name)
    item_key = f"import:{IMPORT_ID}:11"
    payload = build_mixed_catalog_cart_payload(
        [], catalogs=mixed_catalogs.__wrapped__(), rate_rows=rate_rows.__wrapped__(),
        quote_currency="MXN", commercial_discount_percent="40",
        presentation_sections=[{"id": "section-1", "title": "Recepcion", "item_keys": [item_key]}],
        imported_source={"manifest": manifest, "source_currency": "USD", "items": [{
            "kind": "imported", "import_id": IMPORT_ID, "source_row": 11,
            "source_currency": "USD", "quantity": "1",
            "overrides": {"name": "Alien Task Chair", "description": "Silla operativa", "dimension": "630 x 565 x 1000 mm", "unit_price": "0", "provider": "Sunon"},
        }]},
    )
    line = payload["imported_source"]["items"][0]
    source_row_one_key = f"import:{IMPORT_ID}:1"
    line.update(
        source_row=1, canonical_key=source_row_one_key,
        original_unit_price="-0", unit_price="-0",
    )
    payload["sections"][0]["item_keys"] = [source_row_one_key]

    row = quotation_data_rows(payload)[0]

    assert row.source_row == 1
    assert row.original_cost == row.converted_cost == Decimal("0")
    assert "-0" not in build_quotation_data_sheet((row,)).xml.decode("utf-8")


def test_quotation_data_physical_limit_accepts_exact_count(monkeypatch, mixed_payload):
    row = quotation_data_rows(mixed_payload)[0]
    monkeypatch.setattr(quotation_sheets, "XLSX_MAX_ROWS", 2)

    addition = build_quotation_data_sheet((row,))

    assert b'<dimension ref="A1:P2"' in addition.xml


def test_quotation_data_rejects_imported_source_hash_and_key_mismatches(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")
    manifest, _images = build_import_manifest(source.read_bytes(), IMPORT_ID, source.name)
    item_key = f"import:{IMPORT_ID}:11"
    payload = build_mixed_catalog_cart_payload(
        [], catalogs=mixed_catalogs.__wrapped__(), rate_rows=rate_rows.__wrapped__(),
        quote_currency="MXN", commercial_discount_percent="40",
        presentation_sections=[{"id": "section-1", "title": "Recepcion", "item_keys": [item_key]}],
        imported_source={"manifest": manifest, "source_currency": "USD", "items": [{
            "kind": "imported", "import_id": IMPORT_ID, "source_row": 11,
            "source_currency": "USD", "quantity": "1",
            "overrides": {"name": "Alien Task Chair", "description": "Silla operativa", "dimension": "630 x 565 x 1000 mm", "unit_price": "82", "provider": "Sunon"},
        }]},
    )
    payload["imported_source"]["source_hash"] = "f" * 64

    with pytest.raises(ValueError, match="source_hash"):
        quotation_data_rows(payload)

    payload["imported_source"]["source_hash"] = manifest["source_hash"]
    payload["imported_source"]["items"][0]["canonical_key"] = "import:spoof:11"
    payload["sections"][0]["item_keys"] = ["import:spoof:11"]
    with pytest.raises(ValueError, match="canonical_key"):
        quotation_data_rows(payload)


@pytest.mark.parametrize("title", (
    "%25252568%25252574%25252574%25252570%25252573%2525253A%2525252F%2525252Finvalid.example",
    "%25EF%25BB%25BFhttps%253A%252F%252Finvalid.example",
    "   C:\\catalogo\\archivo.xlsx",
))
def test_quotation_data_rejects_nested_encoded_and_whitespace_obscured_text(title, mixed_payload):
    mixed_payload["sections"][0]["title"] = title

    with pytest.raises(ValueError, match="Texto de Quotation_Data inseguro"):
        quotation_data_rows(mixed_payload)


def test_quotation_data_sheet_uses_indexed_sequence_not_its_iterator(mixed_payload):
    row = quotation_data_rows(mixed_payload)[0]

    class OneRowSequence(Sequence):
        def __len__(self):
            return 1

        def __getitem__(self, index):
            if index == 0:
                return row
            raise IndexError(index)

        def __iter__(self):
            raise AssertionError("No debe usar el iterador libre")

    assert b'<dimension ref="A1:P2"' in build_quotation_data_sheet(OneRowSequence()).xml


def test_quotation_data_preflight_checks_declared_sequences_before_iteration(monkeypatch, mixed_payload):
    class TooMany(Sequence):
        def __len__(self):
            return 3

        def __getitem__(self, index):
            raise AssertionError("No debe materializar")

        def __iter__(self):
            raise AssertionError("No debe iterar")

    monkeypatch.setattr(quotation_sheets, "XLSX_MAX_ROWS", 2)
    mixed_payload["groups"][0]["items"] = TooMany()
    mixed_payload["item_count"] = 1

    with pytest.raises(ValueError, match="límite físico"):
        quotation_data_rows(mixed_payload)


def test_quotation_data_does_not_import_mixed_catalog_at_use_time(monkeypatch, mixed_payload):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.endswith("mixed_catalog"):
            raise AssertionError("quotation_sheets no debe importar mixed_catalog")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    assert quotation_data_rows(mixed_payload)[0].item_key
