from decimal import Decimal, ROUND_HALF_UP
import xml.etree.ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.mixed_catalog import validate_mixed_catalog_payload
from mobiliti_saas.quote_engine.mixed_catalog import build_mixed_catalog_cart_payload
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from mobiliti_saas.quote_engine.quotation_sheets import (
    QUOTATION_DATA_HEADERS,
    build_quotation_data_sheet,
    quotation_data_rows,
)
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
    assert root.find("main:dimension", NS).attrib["ref"] == f"A1:O{mixed_payload['item_count'] + 1}"
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
    assert build_quotation_data_sheet(rows).name == "Quotation_Data"
