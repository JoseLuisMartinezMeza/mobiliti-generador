import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from mobiliti_saas.worker.catalog_sync.importers import parse_lumbro_pdf_prices


FIXTURES = Path("tests/fixtures/catalog_graph/lumbro")
GENERAL_PATH = "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf"
NEW_PATH = "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf"


@dataclass(frozen=True)
class AdapterFile:
    path: str
    kind: str
    brand: str | None
    sha256: str
    mime_type: str
    local_path: Path | None


def _write_pdf(path: Path, fixture_name: str) -> None:
    pages = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    document = fitz.open()
    for expected_page, fixture in enumerate(pages, 1):
        assert fixture["page"] == expected_page
        page = document.new_page(width=612, height=792)
        y = 45
        for line in fixture["text"].splitlines():
            page.insert_text((45, y), line, fontsize=9)
            y += 15
    document.save(path)
    document.close()


def _adapter_file(logical_path: str, local_path: Path) -> AdapterFile:
    return AdapterFile(
        path=logical_path,
        kind="price_list",
        brand=None,
        sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
        mime_type="application/pdf",
        local_path=local_path,
    )


@pytest.fixture
def pdf_sources(tmp_path):
    general = tmp_path / "LISTA DE PRECIOS MULTICONTACTOS 2026.pdf"
    new = tmp_path / "LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf"
    _write_pdf(general, "price_general_pages.json")
    _write_pdf(new, "price_new_pages.json")
    return (
        _adapter_file(GENERAL_PATH, general),
        _adapter_file(NEW_PATH, new),
    )


def test_general_pdf_keeps_published_net_price(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    barcelona = next(row for row in rows if row.identity == "barcelona")

    assert barcelona.model == "Barcelona"
    assert barcelona.configuration == ""
    assert barcelona.net_price == Decimal("2824")
    assert barcelona.currency == "MXN"
    assert barcelona.tax_rate == Decimal("0.16")
    assert barcelona.authority_rank == 3
    assert barcelona.parse_status == "parsed"
    assert barcelona.source.path == GENERAL_PATH
    assert barcelona.source.file_id == pdf_sources[0].sha256
    assert barcelona.source.page > 0
    assert not isinstance(barcelona.net_price, float)


def test_general_pdf_parses_explicit_ibiza_carga_a_c_configuration(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    ibiza = next(row for row in rows if row.identity == "ibiza carga a c")

    assert ibiza.model == "Ibiza"
    assert ibiza.configuration == "Carga A+C"
    assert ibiza.net_price == Decimal("824")
    assert ibiza.parse_status == "parsed"


def test_general_pdf_does_not_swallow_currency_separator_before_wrapped_model(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    box = next(row for row in rows if row.identity == "barcelona box hdmi inalambrico")

    assert box.model == "Barcelona"
    assert box.configuration == "Box/HDMI Inalámbrico"
    assert box.net_price == Decimal("2824")


def test_new_pdf_parses_venecia_inalambrico_with_higher_authority(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    venecia = [
        row
        for row in rows
        if row.identity == "venecia inalambrico" and row.source.path == NEW_PATH
    ]

    assert len(venecia) == 1
    assert venecia[0].model == "Venecia"
    assert venecia[0].configuration == "Inalámbrico"
    assert venecia[0].net_price == Decimal("1490")
    assert venecia[0].authority_rank == 2
    assert venecia[0].parse_status == "parsed"


def test_pdf_repeated_headings_are_not_product_rows(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)

    assert all(row.identity not in {"modelo", "precio"} for row in rows)
    assert sum(row.identity == "torre octa" for row in rows) == 2


def test_general_pdf_malformed_currency_is_retained_for_review(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    hamburgo = next(row for row in rows if row.identity == "hamburgo")

    assert hamburgo.net_price is None
    assert hamburgo.parse_status == "needs_review"
    assert "malformed_currency" in hamburgo.warnings
    assert hamburgo.source.path == GENERAL_PATH


def test_new_pdf_conflicting_duplicate_prices_remain_as_review_evidence(pdf_sources):
    rows = parse_lumbro_pdf_prices(pdf_sources)
    duplicates = [row for row in rows if row.identity == "torre octa"]

    assert [row.net_price for row in duplicates] == [Decimal("5480"), Decimal("5580")]
    assert all(row.parse_status == "needs_review" for row in duplicates)
    assert all("conflicting_price" in row.warnings for row in duplicates)
    assert [row.source.page for row in duplicates] == [1, 2]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows: rows[:1],
        lambda rows: (rows[0], rows[0]),
        lambda rows: (replace(rows[0], path="LUMBRO/LP/otro.pdf"), rows[1]),
        lambda rows: (replace(rows[0], kind="catalog"), rows[1]),
        lambda rows: (replace(rows[0], mime_type="application/octet-stream"), rows[1]),
        lambda rows: (replace(rows[0], sha256="0" * 64), rows[1]),
    ],
)
def test_pdf_source_set_is_strictly_validated(pdf_sources, mutation):
    with pytest.raises(ValueError, match="LUMBRO_PDF_(?:BUNDLE|HASH)"):
        parse_lumbro_pdf_prices(mutation(pdf_sources))
