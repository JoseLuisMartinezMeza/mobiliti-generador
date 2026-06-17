from pathlib import Path
import sys

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.engine import (  # noqa: E402
    MAX_PROD_PER_SECTION,
    SECTION_CATS,
    SECTION_PROD_STARTS,
    _write_mobiliti,
)
from mobiliti_saas.quote_engine.parser import QuoteItem  # noqa: E402


TEMPLATE = ROOT / "mobiliti_saas" / "worker" / "templates" / "Formato Cotizacion 2026 GDL.xlsx"


def _many_products(count: int) -> list[QuoteItem]:
    items = [QuoteItem(tipo="categoria", row=8, nombre="OPERATIVOS")]
    for index in range(count):
        items.append(
            QuoteItem(
                tipo="producto",
                row=9 + index,
                nombre=f"CLG{index:03d} Task Chair",
                descripcion="Task chair",
                dimension="600*600*900 mm",
                cantidad=1,
                precio=100,
            )
        )
    return items


def test_mobiliti_capacity_constants_are_expanded():
    assert len(SECTION_CATS) == 32
    assert MAX_PROD_PER_SECTION == 64
    assert SECTION_PROD_STARTS[0] == SECTION_CATS[0] + 1
    assert SECTION_CATS[1] - SECTION_CATS[0] >= MAX_PROD_PER_SECTION + 2


def test_mobiliti_writes_all_products_through_expanded_sections():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        row_map, _ = _write_mobiliti(
            ws,
            _many_products(70),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert len(row_map) == 70
        assert row_map[9] == SECTION_PROD_STARTS[0]
        assert row_map[72] == SECTION_PROD_STARTS[0] + 63
        assert row_map[73] == SECTION_PROD_STARTS[1]
        assert ws.cell(row_map[73], 4).value == "=Quotation!B73"
    finally:
        wb.close()
