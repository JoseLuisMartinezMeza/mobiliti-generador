from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from mobiliti_saas.quote_engine import generate_quote
from mobiliti_saas.quote_engine import engine
from mobiliti_saas.quote_engine.parser import read_items


MIXED_HEADERS = {
    1: "No.",
    2: "Item",
    4: "Description",
    5: "Dimension",
    7: "Qty",
    10: "List Price",
    11: "URL",
    12: "Supplier",
    13: "Discount Percent",
    14: "Original Currency",
    15: "Original Unit Price",
    16: "Frozen Exchange Rate",
    17: "Source Reference",
    18: "Price Mode",
    19: "Auto Electrification",
}
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "mobiliti_saas" / "worker" / "templates" / "Formato Cotizacion 2026 GDL.xlsx"


def _converted_price(original, rate):
    return float(
        (Decimal(str(original)) * Decimal(str(rate))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def _mixed_line(
    *,
    name: str,
    provider: str,
    discount: float,
    mode: str,
    auto: bool,
    price: float,
    original_currency: str,
    original_price: float,
    frozen_rate: float,
    quantity: float = 1,
):
    return {
        "name": name,
        "provider": provider,
        "discount": discount,
        "mode": mode,
        "auto": auto,
        "price": price,
        "original_currency": original_currency,
        "original_price": original_price,
        "frozen_rate": frozen_rate,
        "quantity": quantity,
    }


def _write_mixed_source(path: Path, lines: list[dict]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    for column, value in MIXED_HEADERS.items():
        ws.cell(7, column).value = value
    ws["A8"] = "- Catalogos mixtos"
    for index, line in enumerate(lines, start=1):
        row = index + 8
        ws.cell(row, 1).value = index
        ws.cell(row, 2).value = line["name"]
        ws.cell(row, 4).value = f"Descripcion {line['name']}"
        ws.cell(row, 5).value = "pieza"
        ws.cell(row, 7).value = line["quantity"]
        ws.cell(row, 10).value = line["price"]
        ws.cell(row, 12).value = line["provider"]
        ws.cell(row, 13).value = line["discount"]
        ws.cell(row, 14).value = line["original_currency"]
        ws.cell(row, 15).value = line["original_price"]
        ws.cell(row, 16).value = line["frozen_rate"]
        ws.cell(row, 17).value = f"source:{line['provider'].lower()}:{index}"
        ws.cell(row, 18).value = line["mode"]
        ws.cell(row, 19).value = line["auto"]
    wb.save(path)
    wb.close()
    return path


def _rate_summary(quote_currency: str, mxn_rate: str, usd_rate: str):
    def rate(catalog, base_currency, exchange_rate):
        identity = base_currency == quote_currency
        return {
            "catalog": catalog,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "exchange_rate": exchange_rate,
            "rate_source": "identity" if identity else "saas_exchange_rates",
            "rate_effective_date": "2026-07-15",
            "rate_retrieved_at": "" if identity else "2026-07-15T23:00:00Z",
        }
    return [
        rate("tarkett", "MXN", mxn_rate),
        rate("alma", "USD", usd_rate),
    ]


def _mixed_metadata(
    quote_currency: str = "MXN",
    *,
    mxn_rate: str = "1.000000",
    usd_rate: str = "18.500000",
    auto_rate: bool = False,
):
    summary = _rate_summary(quote_currency, mxn_rate, usd_rate)
    return {
        "catalog_price_mode": "mixed_catalog_converted",
        "quote_currency": quote_currency,
        "rate_summary": summary,
        "auto_electrification_rate": (
            {key: summary[0][key] for key in (
                "base_currency",
                "quote_currency",
                "exchange_rate",
                "rate_source",
                "rate_effective_date",
                "rate_retrieved_at",
            )}
            if auto_rate
            else None
        ),
        "cotizacion": "MIXTA-001",
        "proyecto": "Proyecto mixto",
        "cliente": "Cliente",
    }


def _row_for_formula(ws, column: int, formula: str) -> int:
    return next(row for row in range(1, ws.max_row + 1) if ws.cell(row, column).value == formula)


@pytest.fixture(autouse=True)
def _mixed_tests_never_resolve_a_live_legacy_rate(monkeypatch):
    def fail_if_called(_metadata):
        raise AssertionError("mixed mode must not resolve the legacy exchange rate")

    monkeypatch.setattr(engine, "_exchange_rate", fail_if_called)


def _write_quotation(path: Path, *, mixed: bool) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    headers = MIXED_HEADERS if mixed else {key: MIXED_HEADERS[key] for key in (1, 2, 4, 5, 7, 10, 11)}
    for column, value in headers.items():
        ws.cell(7, column).value = value
    ws["A8"] = "- ALMA"
    ws["A9"] = 1
    ws["B9"] = "Producto ALMA"
    ws["D9"] = "Descripcion"
    ws["E9"] = "pieza"
    ws["G9"] = 2
    ws["J9"] = 1850
    if mixed:
        ws["L9"] = "ALMA"
        ws["M9"] = 0
        ws["N9"] = "USD"
        ws["O9"] = 100
        ws["P9"] = 18.5
        ws["Q9"] = "source:alma:1"
        ws["R9"] = "net"
        ws["S9"] = False
    wb.save(path)
    wb.close()
    return path


def test_parser_reads_mixed_audit_columns_by_header(tmp_path):
    source = _write_quotation(tmp_path / "mixed.xlsx", mixed=True)

    items, columns = read_items(source)

    product = next(item for item in items if item.tipo == "producto")
    assert columns["proveedor"] == "L"
    assert columns["descuento"] == "M"
    assert columns["electrificacion_automatica"] == "S"
    assert product.proveedor == "ALMA"
    assert product.descuento == 0
    assert product.moneda_original == "USD"
    assert product.precio_original == 100
    assert product.tipo_cambio_congelado == 18.5
    assert product.referencia_fuente == "source:alma:1"
    assert product.modo_precio == "net"
    assert product.electrificacion_automatica is False


def test_parser_keeps_legacy_defaults_when_mixed_headers_are_absent(tmp_path):
    source = _write_quotation(tmp_path / "legacy.xlsx", mixed=False)

    product = next(item for item in read_items(source)[0] if item.tipo == "producto")

    assert product.proveedor == ""
    assert product.descuento is None
    assert product.moneda_original == ""
    assert product.precio_original is None
    assert product.tipo_cambio_congelado is None
    assert product.referencia_fuente == ""
    assert product.modo_precio == ""
    assert product.electrificacion_automatica is None


@pytest.mark.parametrize(
    ("currency", "mxn_rate", "usd_rate", "money_literal"),
    [
        ("MXN", "1.000000", "18.500000", '"MXN" $'),
        ("USD", "0.054054", "1.000000", '"USD" $'),
        ("EUR", "0.048780", "0.902430", '"EUR" €'),
    ],
)
def test_mixed_engine_uses_per_line_provider_discount_and_converted_prices(
    tmp_path, currency, mxn_rate, usd_rate, money_literal
):
    source = _write_mixed_source(
        tmp_path / f"mixed-{currency}.xlsx",
        [
            _mixed_line(
                name="Piso Tarkett",
                provider="Tarkett",
                discount=40,
                mode="list",
                auto=False,
                price=_converted_price(123.456, mxn_rate),
                original_currency="MXN",
                original_price=123.456,
                frozen_rate=float(mxn_rate),
                quantity=2,
            ),
            _mixed_line(
                name="Silla ALMA",
                provider="ALMA",
                discount=0,
                mode="net",
                auto=False,
                price=_converted_price(100, usd_rate),
                original_currency="USD",
                original_price=100,
                frozen_rate=float(usd_rate),
                quantity=3,
            ),
        ],
    )
    output = tmp_path / f"final-{currency}.xlsx"

    generate_quote(
        source,
        output,
        _mixed_metadata(currency, mxn_rate=mxn_rate, usd_rate=usd_rate),
        TEMPLATE,
    )

    wb = load_workbook(output, data_only=False)
    try:
        cot = wb["Cotizacion"]
        mobiliti = wb["Mobiliti"]
        tarkett_mob = _row_for_formula(mobiliti, 4, "=Quotation!B9")
        alma_mob = _row_for_formula(mobiliti, 4, "=Quotation!B10")
        tarkett_cot = _row_for_formula(cot, 1, "=Quotation!B9")
        alma_cot = _row_for_formula(cot, 1, "=Quotation!B10")
        assert mobiliti.cell(tarkett_mob, 6).value == "Tarkett"
        assert mobiliti.cell(alma_mob, 6).value == "ALMA"
        assert cot.cell(tarkett_cot, 7).value == 0.4
        assert cot.cell(alma_cot, 7).value == 0
        assert cot.cell(tarkett_cot, 6).value == f"=ROUND(Mobiliti!X{tarkett_mob},2)"
        assert cot.cell(alma_cot, 6).value == f"=ROUND(Mobiliti!X{alma_mob},2)"
        assert mobiliti["J6"].value == f"{currency}/{currency}"
        assert mobiliti["K6"].value == 1
        for row in (tarkett_cot, alma_cot):
            assert cot.cell(row, 8).value == f"=ROUND(F{row}*G{row},2)"
            assert cot.cell(row, 9).value == f"=ROUND(F{row}-H{row},2)"
            assert cot.cell(row, 10).value == f"=ROUND(E{row}*I{row},2)"
            for column in (6, 8, 9, 10):
                assert money_literal in cot.cell(row, column).number_format
        for row in (tarkett_mob, alma_mob):
            for column in (10, 13, 14, 18, 20, 23, 24, 28, 29, 30, 32, 33):
                assert money_literal in mobiliti.cell(row, column).number_format
        if currency == "EUR":
            assert all(
                engine.MONEY_FORMAT != mobiliti.cell(row, column).number_format
                for row in (tarkett_mob, alma_mob)
                for column in (13, 18, 20)
            )
    finally:
        wb.close()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "unknown", "Modo de precio mixto invalido"),
        ("discount", 10, "Precio neto mixto no admite descuento"),
        ("provider", "ALMA", "Precio de lista mixto solo admite Tarkett u Offiho"),
        ("auto", True, "Electrificacion automatica mixta solo admite Tarkett u Offiho"),
    ],
)
def test_mixed_engine_rejects_invalid_per_line_policy_before_saving(
    tmp_path, field, value, message
):
    line = _mixed_line(
        name="Silla ALMA",
        provider="ALMA",
        discount=0,
        mode="net",
        auto=False,
        price=100,
        original_currency="USD",
        original_price=100,
        frozen_rate=18.5,
    )
    line[field] = value
    if field == "provider":
        line["mode"] = "list"
    source = _write_mixed_source(tmp_path / f"invalid-{field}.xlsx", [line])
    output = tmp_path / f"invalid-{field}-final.xlsx"

    with pytest.raises(ValueError, match=message):
        generate_quote(source, output, _mixed_metadata(), TEMPLATE)

    assert not output.exists()


@pytest.mark.parametrize(
    ("currency", "mxn_rate", "usd_rate"),
    [
        ("MXN", "1.000000", "18.500000"),
        ("USD", "0.054054", "1.000000"),
        ("EUR", "0.048780", "0.902430"),
    ],
)
def test_mixed_lumbro_accessories_are_selective_and_use_frozen_rate(
    tmp_path, currency, mxn_rate, usd_rate
):
    source = _write_mixed_source(
        tmp_path / f"accessories-{currency}.xlsx",
        [
            _mixed_line(
                name="Estacion Lido 8PAX",
                provider="Tarkett",
                discount=40,
                mode="list",
                auto=True,
                price=_converted_price(1000, mxn_rate),
                original_currency="MXN",
                original_price=1000,
                frozen_rate=float(mxn_rate),
                quantity=1,
            ),
            _mixed_line(
                name="Estacion Lido 8PAX ALMA",
                provider="ALMA",
                discount=0,
                mode="net",
                auto=False,
                price=_converted_price(100, usd_rate),
                original_currency="USD",
                original_price=100,
                frozen_rate=float(usd_rate),
                quantity=1,
            ),
            _mixed_line(
                name="LIDO.OP-INT manual",
                provider="Lumbro",
                discount=0,
                mode="net",
                auto=False,
                price=_converted_price(120, mxn_rate),
                original_currency="MXN",
                original_price=120,
                frozen_rate=float(mxn_rate),
                quantity=1,
            ),
        ],
    )
    metadata = _mixed_metadata(
        currency,
        mxn_rate=mxn_rate,
        usd_rate=usd_rate,
        auto_rate=True,
    )
    metadata["rate_summary"].append(
        {
            "catalog": "lumbro",
            "base_currency": "MXN",
            "quote_currency": currency,
            "exchange_rate": mxn_rate,
            "rate_source": "identity" if currency == "MXN" else "saas_exchange_rates",
            "rate_effective_date": "2026-07-15",
            "rate_retrieved_at": "" if currency == "MXN" else "2026-07-15T23:00:00Z",
        }
    )
    output = tmp_path / f"accessories-final-{currency}.xlsx"

    generate_quote(source, output, metadata, TEMPLATE)

    wb = load_workbook(output, data_only=False)
    try:
        cot = wb["Cotizacion"]
        mobiliti = wb["Mobiliti"]
        parent_mob = _row_for_formula(mobiliti, 4, "=Quotation!B9")
        alma_mob = _row_for_formula(mobiliti, 4, "=Quotation!B10")
        manual_mob = _row_for_formula(mobiliti, 4, "=Quotation!B11")
        automatic_rows = [
            row
            for row in range(1, mobiliti.max_row + 1)
            if mobiliti.cell(row, 4).value in {"LIDO.OP-INT", "JUMP-1.5M", "CAJA-FUS"}
        ]
        assert len(automatic_rows) == 3
        assert parent_mob < automatic_rows[0] < alma_mob < manual_mob
        expected_rate = str(float(mxn_rate)).rstrip("0").rstrip(".")
        expected_rate = expected_rate or "0"
        for row in automatic_rows:
            assert mobiliti.cell(row, 10).value.startswith("=ROUND('SPEC-GUIDE-LUMBRO'!E")
            assert mobiliti.cell(row, 10).value.endswith(f"*{expected_rate},2)")
            assert mobiliti.cell(row, 27).value == f"=MIN(0.4,Z{row})"
        parent_cot = _row_for_formula(cot, 1, "=Quotation!B9")
        formula = cot.cell(parent_cot, 6).value
        assert formula.startswith("=ROUND(IFERROR((")
        assert formula.count(f"Mobiliti!X{parent_mob}*Mobiliti!H{parent_mob}") == 1
        for row in automatic_rows:
            assert formula.count(f"Mobiliti!X{row}*Mobiliti!H{row}") == 1
        assert cot.cell(parent_cot, 7).value == 0.4
        assert mobiliti.cell(manual_mob, 6).value == "Lumbro"
    finally:
        wb.close()


def test_mixed_job_without_eligible_auto_lines_needs_no_auto_rate(tmp_path):
    source = _write_mixed_source(
        tmp_path / "alma-only.xlsx",
        [
            _mixed_line(
                name="Estacion Lido 8PAX ALMA",
                provider="ALMA",
                discount=0,
                mode="net",
                auto=False,
                price=1850,
                original_currency="USD",
                original_price=100,
                frozen_rate=18.5,
            )
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = metadata["rate_summary"][1:]
    output = tmp_path / "alma-only-final.xlsx"

    generate_quote(source, output, metadata, TEMPLATE)

    wb = load_workbook(output, data_only=False)
    try:
        assert not any(
            wb["Mobiliti"].cell(row, 4).value in {"LIDO.OP-INT", "JUMP-1.5M", "CAJA-FUS"}
            for row in range(1, wb["Mobiliti"].max_row + 1)
        )
    finally:
        wb.close()


def test_mixed_headers_are_saved_as_safe_text(tmp_path):
    source = _write_mixed_source(
        tmp_path / "safe-header.xlsx",
        [
            _mixed_line(
                name="Silla ALMA",
                provider="ALMA",
                discount=0,
                mode="net",
                auto=False,
                price=1850,
                original_currency="USD",
                original_price=100,
                frozen_rate=18.5,
            )
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = metadata["rate_summary"][1:]
    fields = ("cotizacion", "proyecto", "cliente", "correo", "telefono", "direccion", "razon_social")
    dangerous_values = ("=1+1", "+SUM(A1:A2)", "-2+3", "@cmd", "=A1", "+1", "-9")
    metadata.update(dict(zip(fields, dangerous_values, strict=True)))
    output = tmp_path / "safe-headers.xlsx"

    generate_quote(source, output, metadata, TEMPLATE)

    wb = load_workbook(output, data_only=False)
    try:
        cot = wb["Cotizacion"]
        for address, dangerous in zip(
            ("B3", "B7", "B8", "B9", "B10", "B11", "B12"),
            dangerous_values,
            strict=True,
        ):
            assert cot[address].value == "'" + dangerous
            assert cot[address].data_type != "f"
    finally:
        wb.close()


def test_mixed_rate_legend_is_canonical_safe_and_compact(tmp_path):
    source = _write_mixed_source(
        tmp_path / "legend.xlsx",
        [
            _mixed_line(
                name="Piso Tarkett",
                provider="Tarkett",
                discount=40,
                mode="list",
                auto=False,
                price=100,
                original_currency="MXN",
                original_price=100,
                frozen_rate=1,
            ),
            _mixed_line(
                name="Silla ALMA",
                provider="ALMA",
                discount=0,
                mode="net",
                auto=False,
                price=1850,
                original_currency="USD",
                original_price=100,
                frozen_rate=18.5,
            ),
        ],
    )
    output = tmp_path / "legend-final.xlsx"

    generate_quote(source, output, _mixed_metadata(), TEMPLATE)

    wb = load_workbook(output, data_only=False)
    try:
        legend = wb["Cotizacion"]["B4"].value
        assert legend == (
            "MXN | precios mixtos mas IVA | Tarkett MXN/MXN 1.000000; "
            "ALMA USD/MXN 18.500000 Banco de Mexico / DOF 2026-07-15"
        )
        assert len(legend) <= 1000
        assert wb["Cotizacion"]["B4"].data_type != "f"
    finally:
        wb.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rate_effective_date", "2026-99-99"),
        ("rate_retrieved_at", "not-a-timestamp"),
    ],
)
def test_mixed_rate_summary_rejects_malformed_dates(field, value):
    metadata = deepcopy(_mixed_metadata())
    metadata["rate_summary"][1][field] = value

    with pytest.raises(ValueError, match="Resumen de tasas mixtas invalido"):
        engine._mixed_rate_summary(metadata)


def test_mixed_auto_line_requires_the_complete_frozen_rate_before_saving(tmp_path):
    source = _write_mixed_source(
        tmp_path / "missing-auto-rate.xlsx",
        [
            _mixed_line(
                name="Estacion Lido 8PAX",
                provider="Tarkett",
                discount=40,
                mode="list",
                auto=True,
                price=1000,
                original_currency="MXN",
                original_price=1000,
                frozen_rate=1,
            )
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = metadata["rate_summary"][:1]
    output = tmp_path / "missing-auto-rate-final.xlsx"

    with pytest.raises(ValueError, match="Tasa de electrificacion mixta incompleta"):
        generate_quote(source, output, metadata, TEMPLATE)

    assert not output.exists()


def test_mixed_auto_lines_require_one_identical_snapshot_for_every_eligible_provider(
    monkeypatch, tmp_path
):
    source = _write_mixed_source(
        tmp_path / "two-auto-providers.xlsx",
        [
            _mixed_line(
                name="Piso Tarkett",
                provider="Tarkett",
                discount=40,
                mode="list",
                auto=True,
                price=100,
                original_currency="MXN",
                original_price=100,
                frozen_rate=1,
            ),
            _mixed_line(
                name="Escritorio Offiho",
                provider="Offiho",
                discount=40,
                mode="list",
                auto=True,
                price=200,
                original_currency="MXN",
                original_price=200,
                frozen_rate=1,
            ),
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = [
        metadata["rate_summary"][0],
        {
            **metadata["rate_summary"][0],
            "catalog": "offiho",
            "rate_effective_date": "2026-07-14",
        },
    ]
    metadata["auto_electrification_rate"] = {
        key: metadata["rate_summary"][0][key]
        for key in (
            "base_currency",
            "quote_currency",
            "exchange_rate",
            "rate_source",
            "rate_effective_date",
            "rate_retrieved_at",
        )
    }
    output = tmp_path / "missing-parent" / "final.xlsx"

    monkeypatch.setattr(
        engine,
        "_load_template",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("mixed snapshot validation must run before template loading")
        ),
    )
    with pytest.raises(ValueError, match="Tasa de electrificacion mixta inconsistente"):
        generate_quote(source, output, metadata, TEMPLATE)

    assert not output.parent.exists()
    assert not output.exists()


def test_mixed_auto_lines_accept_one_matching_snapshot_for_all_eligible_providers(tmp_path):
    source = _write_mixed_source(
        tmp_path / "matching-auto-providers.xlsx",
        [
            _mixed_line(
                name="Piso Tarkett",
                provider="Tarkett",
                discount=40,
                mode="list",
                auto=True,
                price=100,
                original_currency="MXN",
                original_price=100,
                frozen_rate=1,
            ),
            _mixed_line(
                name="Escritorio Offiho",
                provider="Offiho",
                discount=40,
                mode="list",
                auto=True,
                price=200,
                original_currency="MXN",
                original_price=200,
                frozen_rate=1,
            ),
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = [
        metadata["rate_summary"][0],
        {**metadata["rate_summary"][0], "catalog": "offiho"},
    ]
    metadata["auto_electrification_rate"] = {
        key: metadata["rate_summary"][0][key]
        for key in (
            "base_currency",
            "quote_currency",
            "exchange_rate",
            "rate_source",
            "rate_effective_date",
            "rate_retrieved_at",
        )
    }

    items = read_items(source)[0]

    engine._validate_mixed_catalog_metadata(items, metadata)


def test_mixed_converted_price_audit_rejects_arbitrary_final_price_before_paths(
    monkeypatch, tmp_path
):
    source = _write_mixed_source(
        tmp_path / "tampered-converted-price.xlsx",
        [
            _mixed_line(
                name="Silla ALMA",
                provider="ALMA",
                discount=0,
                mode="net",
                auto=False,
                price=999999,
                original_currency="USD",
                original_price=100,
                frozen_rate=18.5,
            )
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = metadata["rate_summary"][1:]
    output = tmp_path / "no-output-dir" / "final.xlsx"

    monkeypatch.setattr(
        engine,
        "_load_template",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("converted price validation must run before template loading")
        ),
    )
    with pytest.raises(ValueError, match="Precio convertido mixto inconsistente"):
        generate_quote(source, output, metadata, TEMPLATE)

    assert not output.parent.exists()
    assert not output.exists()


def test_mixed_discount_precision_and_half_up_price_boundaries_reach_both_sheets(tmp_path):
    source = _write_mixed_source(
        tmp_path / "precision.xlsx",
        [
            _mixed_line(
                name="Piso Tarkett precision alta",
                provider="Tarkett",
                discount=12.345678,
                mode="list",
                auto=False,
                price=2.68,
                original_currency="MXN",
                original_price=2.675,
                frozen_rate=1,
            ),
            _mixed_line(
                name="Piso Tarkett precision minima",
                provider="Tarkett",
                discount=0.000001,
                mode="list",
                auto=False,
                price=0.01,
                original_currency="MXN",
                original_price=0.005,
                frozen_rate=1,
            ),
        ],
    )
    metadata = _mixed_metadata()
    metadata["rate_summary"] = metadata["rate_summary"][:1]
    output = tmp_path / "precision-final.xlsx"

    generate_quote(source, output, metadata, TEMPLATE)

    wb = load_workbook(output, data_only=False)
    try:
        cot = wb["Cotizacion"]
        mobiliti = wb["Mobiliti"]
        expectations = {
            9: (0.12345678, "0.12345678"),
            10: (0.00000001, "0.00000001"),
        }
        for source_row, (fraction, literal) in expectations.items():
            cot_row = _row_for_formula(cot, 1, f"=Quotation!B{source_row}")
            mobiliti_row = _row_for_formula(mobiliti, 4, f"=Quotation!B{source_row}")
            assert cot.cell(cot_row, 7).value == fraction
            assert mobiliti.cell(mobiliti_row, 27).value == (
                f"=MIN({literal},Z{mobiliti_row})"
            )
            assert mobiliti.cell(mobiliti_row, 23).value == f"=ROUND(J{mobiliti_row},2)"
            assert mobiliti.cell(mobiliti_row, 24).value == f"=ROUND(J{mobiliti_row},2)"
    finally:
        wb.close()
