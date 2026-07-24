from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import inspect
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from mobiliti_saas.quote_engine import engine, generate_quote
from mobiliti_saas.quote_engine.mixed_catalog import (
    build_mixed_catalog_cart_payload,
    create_mixed_catalog_quotation_workbook,
)
from mobiliti_saas.quote_engine.ooxml_package import XlsxPackage
from mobiliti_saas.quote_engine.ooxml_package import assert_package_preserved
from mobiliti_saas.quote_engine.project_quote import project_context
from mobiliti_saas.quote_engine.quotation_import import (
    MOBILITI_RESERVED_ROWS_AFTER_TOTAL,
    XLSX_MAX_ROWS,
    required_mobiliti_rows,
    validate_quote_size,
)
from mobiliti_saas.quote_engine.quotation_sheets import quotation_data_rows
from mobiliti_saas.web.api import index as web_api
from quotation_import_fixtures import build_rich_quotation_fixture
from test_official_quote_stress import (
    OFFICIAL_ALLOWED_PARTS,
    SEVEN_CATALOGS,
    QuoteShape,
    _cell_map,
    _cell_text,
    _formula,
    as_persistent_project_request,
    run_local_worker_job,
    synthetic_mixed_request,
)
from test_official_template_contract import assert_official_template_contract
from test_project_quote_engine import (
    FIXED_ID,
    PER_UNIT_ID,
    PRINCIPAL_ID,
    _project_payload,
    _supplier_item,
)
from test_quotation_sheet_transplant import quotation_semantic_signature
from test_quotation_sheet_transplant import _canonical


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TEMPLATE = engine.OFFICIAL_TEMPLATE_PATH
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _formula_semantic_xml(content: bytes) -> tuple:
    root = ET.fromstring(content)
    for cell in root.findall(f".//{{{MAIN}}}c"):
        if cell.find(f"{{{MAIN}}}f") is None:
            continue
        cached = cell.find(f"{{{MAIN}}}v")
        if cached is not None:
            cell.remove(cached)
    return _canonical(root)


def _project_quote_input(
    tmp_path: Path,
    quote_currency: str,
) -> tuple[Path, dict, dict]:
    catalog = {
        "supplier": "sunon",
        "source_hash": "c" * 64,
        "generated_at": "2026-07-23T00:00:00+00:00",
        "items": [
            _supplier_item("sunon:main-1", "MAIN-1", "Principal"),
            _supplier_item("sunon:per-1", "PER-1", "Complemento por unidad"),
            _supplier_item("sunon:fixed-1", "FIXED-1", "Complemento fijo"),
        ],
    }
    project = _project_payload()
    project["quote_fields"]["quote_currency"] = quote_currency
    context = project_context(
        project,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        3,
    )
    payload = build_mixed_catalog_cart_payload(
        [
            {
                "line_id": PRINCIPAL_ID,
                "catalog": "sunon",
                "internal_id": "sunon:main-1",
                "quantity": "10",
            },
            {
                "line_id": PER_UNIT_ID,
                "catalog": "sunon",
                "internal_id": "sunon:per-1",
                "quantity": "20",
            },
            {
                "line_id": FIXED_ID,
                "catalog": "sunon",
                "internal_id": "sunon:fixed-1",
                "quantity": "3",
            },
        ],
        catalogs={"sunon": catalog},
        rate_rows=[
            {
                "currency": "USD",
                "effective_date": "2026-07-23",
                "mxn_per_unit": "18.500000",
                "retrieved_at": "2026-07-23T00:00:00+00:00",
            }
        ],
        quote_currency=quote_currency,
        commercial_discount_percent="40",
        presentation_sections=[
            {
                "id": "section-1",
                "title": "Recepción",
                "line_ids": [PRINCIPAL_ID, PER_UNIT_ID, FIXED_ID],
            }
        ],
        project_context=context,
        today=date(2026, 7, 23),
    )
    source = create_mixed_catalog_quotation_workbook(
        payload,
        tmp_path / f"project-{quote_currency}.xlsx",
        image_dir=tmp_path / "images",
    )
    metadata = {
        **project["quote_fields"],
        "cotizacion": f"PROJECT-{quote_currency}",
        "catalog_price_mode": "mixed_catalog_converted",
        "base_currency": quote_currency,
        "quote_currency": quote_currency,
        "exchange_rate": "1.000000",
        "rate_summary": deepcopy(payload["rate_summary"]),
        "auto_electrification_rate": None,
        "catalog_source_hashes": {"sunon": "c" * 64},
        "project_context": deepcopy(payload["project_context"]),
    }
    return source, payload, metadata


def _generate_project_quote(
    tmp_path: Path,
    quote_currency: str,
    *,
    original_quotation_path: Path | None = None,
) -> tuple[Path, dict]:
    source, payload, metadata = _project_quote_input(tmp_path, quote_currency)
    output = tmp_path / f"project-output-{quote_currency}.xlsx"
    generate_quote(
        source,
        output,
        metadata,
        OFFICIAL_TEMPLATE,
        original_quotation_path=original_quotation_path,
        quotation_data_rows=quotation_data_rows(payload),
    )
    assert output.is_file()
    assert ZipFile(output).testzip() is None
    XlsxPackage.read(output)
    return output, payload


@pytest.mark.parametrize("quote_currency", ("MXN", "USD"))
def test_project_quote_opens_without_repair_and_totals_equal_components(
    tmp_path: Path,
    quote_currency: str,
) -> None:
    output, payload = _generate_project_quote(tmp_path, quote_currency)
    expected_unit_cost = Decimal("1850") if quote_currency == "MXN" else Decimal("100")
    physical_quantities = (Decimal("10"), Decimal("20"), Decimal("3"))
    component_totals = tuple(
        expected_unit_cost * quantity for quantity in physical_quantities
    )
    visible_principal_total = (
        expected_unit_cost
        + expected_unit_cost * Decimal("2")
        + expected_unit_cost * Decimal("3") / Decimal("10")
    ) * Decimal("10")

    assert visible_principal_total == sum(component_totals)
    assert [Decimal(item["unit_price"]) for item in payload["groups"][0]["items"]] == [
        expected_unit_cost,
        expected_unit_cost,
        expected_unit_cost,
    ]

    workbook = load_workbook(output, data_only=False, read_only=False)
    try:
        mobiliti = workbook["Mobiliti"]
        cotizacion = workbook["Cotizacion"]
        assert [Decimal(str(mobiliti.cell(row, 10).value)) for row in (14, 15, 16)] == [
            expected_unit_cost,
            expected_unit_cost,
            expected_unit_cost,
        ]
        assert [Decimal(str(mobiliti.cell(row, 8).value)) for row in (14, 15, 16)] == list(
            physical_quantities
        )
        assert cotizacion["F17"].value == (
            "=Mobiliti!X14+Mobiliti!X15*2+Mobiliti!X16*3/10"
        )
        assert cotizacion["E17"].value == 10
        assert cotizacion["C17"].value.count("\n+ ") == 2
    finally:
        workbook.close()


def test_project_quote_preserves_original_quotation_and_template_contract(
    tmp_path: Path,
) -> None:
    imported_source = build_rich_quotation_fixture(
        tmp_path / "checked-import-fixture.xlsx",
        formulas={"N9": "=G9*J9"},
        merges=["A1:N1", "B9:C9"],
        image_anchor="B9",
        print_area="A1:N40",
        hidden_rows=[12],
        state="hidden",
    )
    source_sha = hashlib.sha256(imported_source.read_bytes()).hexdigest()
    template_sha = hashlib.sha256(OFFICIAL_TEMPLATE.read_bytes()).hexdigest()

    output, _payload = _generate_project_quote(
        tmp_path,
        "MXN",
        original_quotation_path=imported_source,
    )

    assert quotation_semantic_signature(output) == quotation_semantic_signature(
        imported_source
    )
    assert hashlib.sha256(imported_source.read_bytes()).hexdigest() == source_sha
    assert hashlib.sha256(OFFICIAL_TEMPLATE.read_bytes()).hexdigest() == template_sha
    assert_official_template_contract()
    template_parts = set(XlsxPackage.read(OFFICIAL_TEMPLATE).parts)
    output_package = XlsxPackage.read(output)
    output_parts = set(output_package.parts)
    template_package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    fletes_part = template_package.sheet_part("Fletes")
    assert _formula_semantic_xml(
        output_package.parts[fletes_part]
    ) == _formula_semantic_xml(template_package.parts[fletes_part])
    assert_package_preserved(
        OFFICIAL_TEMPLATE,
        output,
        allowed_parts=(
            set(OFFICIAL_ALLOWED_PARTS)
            | (output_parts - template_parts)
            | {fletes_part}
        ),
    )
    XlsxPackage.read(output)
    assert ZipFile(output).testzip() is None


def test_project_quote_expands_past_16_sections_and_33_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shape = QuoteShape([35] * 20)
    base = synthetic_mixed_request(
        tmp_path,
        shape,
        include_imported=True,
        catalogs=SEVEN_CATALOGS,
    )
    request = as_persistent_project_request(base)
    output = run_local_worker_job(tmp_path, request, monkeypatch)
    package = XlsxPackage.read(output)

    assert ZipFile(output).testzip() is None
    assert len(request.quotation_data) == 700
    assert len({row.section_id for row in request.quotation_data}) == 20
    assert request.quotation_data[0].origin == "imported"
    identities = [
        line["identity"]["internal_id"]
        for line in request.metadata["project_context"][
            "normalized_project_payload"
        ]["lines"]
        if "identity" in line
    ]
    assert identities.count("sunon:duplicate-principal") >= 2

    quotation_data = _cell_map(package, "Quotation_Data")
    assert sum(
        1
        for coordinate, cell in quotation_data.items()
        if coordinate.startswith("A")
        and coordinate != "A1"
        and _cell_text(cell)
    ) == 700
    cotizacion = _cell_map(package, "Cotizacion")
    visible_names = [
        _cell_text(cell)
        for coordinate, cell in cotizacion.items()
        if coordinate.startswith("A") and _cell_text(cell) in set(request.names)
    ]
    assert len(visible_names) == 698
    first_visible_row = next(
        int(coordinate[1:])
        for coordinate, cell in cotizacion.items()
        if coordinate.startswith("A") and _cell_text(cell) == request.names[0]
    )
    assert _formula(cotizacion[f"F{first_visible_row}"]) == (
        "Mobiliti!X14+Mobiliti!X15+Mobiliti!X16"
    )


def test_project_quote_rejects_only_after_physical_xlsx_limit() -> None:
    fixed_rows = required_mobiliti_rows([34]) - 34
    maximum_components = (
        XLSX_MAX_ROWS
        - MOBILITI_RESERVED_ROWS_AFTER_TOTAL
        - fixed_rows
    )
    assert required_mobiliti_rows([maximum_components]) + (
        MOBILITI_RESERVED_ROWS_AFTER_TOTAL
    ) == XLSX_MAX_ROWS

    validate_quote_size(
        section_counts=[maximum_components],
        encoded_bytes=0,
    )
    with pytest.raises(ValueError, match="XLSX permite hasta"):
        validate_quote_size(
            section_counts=[maximum_components + 1],
            encoded_bytes=0,
        )

    source = inspect.getsource(web_api.projects_quote)
    assert source.index("validate_quote_size(") < source.index(
        "_enqueue_mixed_payload("
    )
