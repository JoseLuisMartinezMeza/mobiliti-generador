from decimal import Decimal

import pytest

import scripts.build_offiho_catalog as build
from scripts.build_offiho_catalog import extract_offiho_identity
from scripts.build_offiho_catalog import match_official_product
from scripts.build_offiho_catalog import parse_inventory_xls
from scripts.build_offiho_catalog import parse_pdf_price_index


class _Sheet:
    nrows = 7

    def cell_value(self, row, column):
        values = {
            (5, 1): "OHE-405 NEGRO ALUFSEN",
            (5, 2): 252,
            (5, 3): 1,
            (5, 4): 7999,
            (6, 1): "OHE-406 GRIS ALUFSEN",
            (6, 2): 0,
            (6, 3): 1,
            (6, 4): 8100,
        }
        return values.get((row, column), "")


class _Workbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _Sheet()


class _DuplicateSheet(_Sheet):
    nrows = 8

    def cell_value(self, row, column):
        if row == 7:
            row = 5
        return super().cell_value(row, column)


class _DuplicateWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _DuplicateSheet()


class _ConflictingSheet(_DuplicateSheet):
    def cell_value(self, row, column):
        if row == 7 and column == 2:
            return 251
        return super().cell_value(row, column)


class _ConflictingWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _ConflictingSheet()


def test_parse_inventory_keeps_available_and_exhausted_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _Workbook())

    rows = parse_inventory_xls(tmp_path / "offiho-small.xls")

    assert rows[0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"
    assert rows[0]["available_quantity"] == Decimal("252")
    assert rows[0]["unit_price"] == Decimal("7999")
    assert any(row["available_quantity"] == 0 for row in rows)


def test_parse_inventory_removes_materially_identical_duplicate_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _DuplicateWorkbook())

    rows = parse_inventory_xls(tmp_path / "offiho-duplicates.xls")

    assert len(rows) == 2
    assert [row["inventory_key"] for row in rows] == [
        "OHE-405 NEGRO ALUFSEN",
        "OHE-406 GRIS ALUFSEN",
    ]


def test_parse_inventory_rejects_duplicate_key_with_different_data(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _ConflictingWorkbook())

    with pytest.raises(RuntimeError, match="OHE-405 NEGRO ALUFSEN.*datos distintos"):
        parse_inventory_xls(tmp_path / "offiho-conflict.xls")


def test_build_catalog_reports_deduplicated_inventory_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _DuplicateWorkbook())
    monkeypatch.setattr(build, "parse_pdf_price_index", lambda paths: {})
    monkeypatch.setattr(build, "build_site_product_index", lambda cache: {})
    inventory = tmp_path / "offiho-duplicates.xls"
    inventory.write_bytes(b"xls")

    catalog = build.build_catalog(
        inventory,
        [],
        tmp_path / "cache.json",
        tmp_path / "catalog.json",
    )

    assert catalog["source_row_count"] == 3
    assert catalog["duplicate_row_count"] == 1
    assert catalog["unique_item_count"] == 2
    assert catalog["total"] == 2
    assert catalog["out_of_stock"] == 1
    assert catalog["inventory_prices"] == 2


def test_extract_identity_separates_model_name_and_variant():
    identity = extract_offiho_identity("OHE-405 NEGRO ALUFSEN")

    assert identity.code == "OHE-405"
    assert identity.name == "ALUFSEN"
    assert identity.variant == "NEGRO"


def test_pdf_price_index_normalizes_compact_variant(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "extract_pdf_pages", lambda paths: ["ALUFSEN OHE-405 negro $ 7,999"])

    prices = parse_pdf_price_index([tmp_path / "prices.pdf"])

    assert prices["OHE-405 NEGRO"] == Decimal("7999")


def test_site_match_requires_expected_model_code():
    product = match_official_product(
        extract_offiho_identity("OHE-405 NEGRO ALUFSEN"),
        [
            {
                "codes": ["OHE-405"],
                "url": "https://www.offiho.com/directivos/alufsen",
                "image_url": "https://www.offiho.com/alufsen.jpg",
            }
        ],
    )

    assert product["url"].endswith("/directivos/alufsen")
