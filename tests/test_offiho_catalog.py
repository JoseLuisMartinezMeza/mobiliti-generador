from decimal import Decimal
from email.message import Message

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


class _CorruptNumericSheet(_Sheet):
    def __init__(self, column, value):
        self.column = column
        self.value = value

    def cell_value(self, row, column):
        if row == 5 and column == self.column:
            return self.value
        return super().cell_value(row, column)


class _CorruptNumericWorkbook:
    def __init__(self, column, value):
        self.column = column
        self.value = value

    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(self.column, self.value)


class _SpecialOrderWorkbook:
    def __init__(self, status):
        self.status = status

    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(2, self.status)


class _SpecialOrderPiecesWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(3, "Sobre Pedido")


class _BlankStockWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _CorruptNumericSheet(2, "")


class _RepeatedHeaderSheet(_Sheet):
    nrows = 8

    def cell_value(self, row, column):
        if row == 7:
            return {
                1: "CODIGO",
                2: "EXISTENCIA",
                3: "Piezas por Caja",
                4: "Precio",
            }.get(column, "")
        return super().cell_value(row, column)


class _RepeatedHeaderWorkbook:
    def sheet_by_name(self, name):
        assert name == "Publicaci\u00f3n"
        return _RepeatedHeaderSheet()


@pytest.fixture
def html_inventory_xls(tmp_path):
    path = tmp_path / "offiho-html.xls"
    path.write_text(
        """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body>
<table>
  <tr><th>CODIGO</th><th>Existencia</th><th>Piezas por Caja</th><th>Precio Lista 1</th></tr>
  <tr><td>OHE-405 NEGRO ALUFSEN</td><td>252</td><td>1</td><td>7,999</td></tr>
  <tr><td>OHE-406 GRIS ALUFSEN</td><td>0</td><td>2</td><td></td></tr>
</table>
</body></html>""",
        encoding="utf-8",
    )
    return path


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
    monkeypatch.setattr(build, "build_site_product_index", lambda cache, **kwargs: {})
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


def test_parse_inventory_supports_html_disguised_as_xls(html_inventory_xls):
    rows = parse_inventory_xls(html_inventory_xls)

    assert len(rows) == 2
    assert rows[0]["inventory_key"] == "OHE-405 NEGRO ALUFSEN"
    assert rows[0]["available_quantity"] == 252
    assert rows[0]["unit_price"] == 7999
    assert rows[1]["available_quantity"] == 0
    assert rows[1]["price_source"] == "missing"


@pytest.mark.parametrize(
    ("column", "column_name", "field"),
    [
        (2, "C", "Existencia"),
        (3, "D", "Piezas por Caja"),
        (4, "E", "Precio Lista 1"),
    ],
)
def test_parse_inventory_rejects_corrupt_numeric_values_with_location(
    monkeypatch, tmp_path, column, column_name, field
):
    monkeypatch.setattr(
        build.xlrd,
        "open_workbook",
        lambda path: _CorruptNumericWorkbook(column, "corrupto"),
    )

    with pytest.raises(RuntimeError, match=rf"Fila 6.*columna {column_name}.*{field}"):
        parse_inventory_xls(tmp_path / "offiho-corrupt.xls")


@pytest.mark.parametrize("status", ["SOBRE PEDIDO", "Consultar Existencias"])
def test_parse_inventory_audits_recognized_nonquantitative_stock(monkeypatch, tmp_path, status):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _SpecialOrderWorkbook(status))

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-special-order.xls")

    assert [row["inventory_key"] for row in rows] == ["OHE-406 GRIS ALUFSEN"]
    assert audit["excluded_stock_status_count"] == 1


def test_parse_inventory_audits_special_order_pieces_default(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _SpecialOrderPiecesWorkbook())

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-special-order-pieces.xls")

    assert rows[0]["pieces_per_box"] == 1
    assert audit["defaulted_pieces_status_count"] == 1


def test_parse_inventory_audits_blank_stock_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _BlankStockWorkbook())

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-blank-stock.xls")

    assert [row["inventory_key"] for row in rows] == ["OHE-406 GRIS ALUFSEN"]
    assert audit["excluded_blank_stock_count"] == 1


def test_parse_inventory_audits_repeated_header_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(build.xlrd, "open_workbook", lambda path: _RepeatedHeaderWorkbook())

    rows, audit = build._parse_inventory_xls(tmp_path / "offiho-repeated-header.xls")

    assert len(rows) == 2
    assert audit["excluded_header_row_count"] == 1


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


def test_image_extraction_never_uses_page_url_for_empty_candidate():
    page_url = "https://www.offiho.com/directivos/alufsen"
    parser = build._PageParser()

    image_url = build._extract_official_image_url(page_url, parser)

    assert image_url == ""
    assert image_url != page_url


def test_image_extraction_accepts_realistic_relative_og_image():
    page_url = "https://www.offiho.com/directivos/alufsen"
    parser = build._PageParser()
    parser.feed(
        '<meta property="og:image" '
        'content="/wp-content/uploads/2026/07/alufsen-negra.jpg?size=large">'
    )

    image_url = build._extract_official_image_url(page_url, parser)

    assert image_url == "https://www.offiho.com/wp-content/uploads/2026/07/alufsen-negra.jpg?size=large"


def test_redirect_handler_blocks_external_location_before_request():
    contacted = []
    handler = build._OfficialRedirectHandler()

    class _Parent:
        def open(self, request, timeout=None):
            contacted.append(request.full_url)
            raise AssertionError("external redirect target must not be contacted")

    handler.parent = _Parent()
    headers = Message()
    headers["Location"] = "https://attacker.example/collect"
    request = build.urllib.request.Request("https://www.offiho.com/start")

    with pytest.raises(ValueError, match="redireccion.*host oficial"):
        handler.http_error_302(request, object(), 302, "Found", headers)

    assert contacted == []


def test_download_inventory_uses_validated_mocked_response(monkeypatch, tmp_path):
    payload = b"mock-biff-inventory"
    seen = []

    class _Response:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "application/vnd.ms-excel"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def geturl(self):
            return "https://www.offiho.com/existencias.xls"

        def read(self, size):
            return payload

    def fake_open(request, *, timeout):
        seen.append((request.full_url, timeout))
        return _Response()

    monkeypatch.setattr(build, "_open_official", fake_open)
    output = tmp_path / "inventory.xls"

    result = build.download_inventory("https://www.offiho.com/existencias.xls", output)

    assert result == output
    assert output.read_bytes() == payload
    assert seen == [("https://www.offiho.com/existencias.xls", 30)]


def _build_catalog_with_sources(monkeypatch, tmp_path, label, *, pdf_payload, site_index):
    inventory = tmp_path / f"inventory-{label}.xls"
    pdf = tmp_path / f"prices-{label}.pdf"
    inventory.write_bytes(b"same inventory bytes")
    pdf.write_bytes(pdf_payload)
    item = {
        "inventory_key": "OHE-405 NEGRO ALUFSEN",
        "code": "OHE-405",
        "name": "ALUFSEN",
        "variant": "NEGRO",
        "unit": "PZA",
        "pieces_per_box": 1,
        "available_quantity": 252,
        "unit_price": 7999,
        "price_source": "inventory",
    }
    monkeypatch.setattr(
        build,
        "_parse_inventory_xls",
        lambda path: ([dict(item)], {"source_row_count": 1, "duplicate_row_count": 0, "unique_item_count": 1}),
    )
    monkeypatch.setattr(build, "parse_pdf_price_index", lambda paths: {})
    monkeypatch.setattr(build, "build_site_product_index", lambda cache, **kwargs: site_index)
    return build.build_catalog(
        inventory,
        [pdf],
        tmp_path / f"cache-{label}.json",
        tmp_path / f"catalog-{label}.json",
    )


def test_source_hash_covers_pdf_and_site_snapshot_with_provenance(monkeypatch, tmp_path):
    site_a = {
        "OHE-405": {
            "url": "https://www.offiho.com/directivos/alufsen",
            "image_url": "https://www.offiho.com/uploads/alufsen.jpg",
            "source_updated_at": "Wed, 08 Jul 2026 12:00:00 GMT",
        }
    }
    site_b = {
        "OHE-405": {
            **site_a["OHE-405"],
            "image_url": "https://www.offiho.com/uploads/alufsen-v2.jpg",
        }
    }

    first = _build_catalog_with_sources(monkeypatch, tmp_path, "first", pdf_payload=b"pdf-a", site_index=site_a)
    same = _build_catalog_with_sources(monkeypatch, tmp_path, "same", pdf_payload=b"pdf-a", site_index=site_a)
    changed_pdf = _build_catalog_with_sources(
        monkeypatch, tmp_path, "changed-pdf", pdf_payload=b"pdf-b", site_index=site_a
    )
    changed_site = _build_catalog_with_sources(
        monkeypatch, tmp_path, "changed-site", pdf_payload=b"pdf-a", site_index=site_b
    )

    assert first["source_hash"] == same["source_hash"]
    assert first["source_hash"] != changed_pdf["source_hash"]
    assert first["source_hash"] != changed_site["source_hash"]
    assert first["sources"]["inventory"]["sha256"]
    assert first["sources"]["pdfs"][0]["sha256"]
    assert first["sources"]["site_index"]["sha256"]
    assert first["sources"]["site_index"]["cache_version"] == build.CACHE_VERSION


def test_no_network_uses_compatible_cache_without_refresh(monkeypatch):
    product = {
        "url": "https://www.offiho.com/directivos/alufsen",
        "image_url": "https://www.offiho.com/uploads/alufsen.jpg",
        "source_updated_at": "Wed, 08 Jul 2026 12:00:00 GMT",
    }
    cache = {
        "cache_version": build.CACHE_VERSION,
        "site_index": {"OHE-405": product},
        "site_index_expires_at": "2000-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(
        build,
        "_fetch_official_page",
        lambda url: (_ for _ in ()).throw(AssertionError("network must remain disabled")),
    )

    index = build.build_site_product_index(cache, no_network=True)

    assert index["OHE-405"] == product


def test_no_network_deterministically_migrates_legacy_snapshot(monkeypatch):
    page_url = "https://www.offiho.com/directivos/alufsen"
    cache = {
        "site_index": {
            "OHE-405": {
                "url": page_url,
                "image_url": page_url,
                "source_updated_at": "",
            }
        }
    }
    monkeypatch.setattr(
        build,
        "_fetch_official_page",
        lambda url: (_ for _ in ()).throw(AssertionError("network must remain disabled")),
    )

    index = build.build_site_product_index(cache, no_network=True)

    assert cache["cache_version"] == build.CACHE_VERSION
    assert cache["site_index_created_at"] == build.LEGACY_CACHE_TIMESTAMP
    assert index["OHE-405"]["image_url"] == ""


def test_no_network_rejects_incompatible_cache_version():
    cache = {"cache_version": -1, "site_index": {}}

    with pytest.raises(RuntimeError, match="version.*cache"):
        build.build_site_product_index(cache, no_network=True)
