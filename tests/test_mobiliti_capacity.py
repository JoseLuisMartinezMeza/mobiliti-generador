from pathlib import Path
import sys

from openpyxl.cell.cell import MergedCell
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


def _row_merges(ws, row: int) -> list[tuple[int, int]]:
    return sorted(
        (merged.min_col, merged.max_col)
        for merged in ws.merged_cells.ranges
        if merged.min_row == row and merged.max_row == row and merged.min_col <= 33
    )


def _visual_signature(ws, row: int, col: int) -> tuple:
    cell = ws.cell(row, col)
    if isinstance(cell, MergedCell):
        return ("merged",)
    return (
        cell.number_format,
        cell.alignment.horizontal,
        cell.alignment.vertical,
        cell.alignment.wrap_text,
        cell.font.name,
        cell.font.sz,
        cell.font.bold,
        cell.fill.fill_type,
        cell.fill.fgColor.type,
        cell.fill.fgColor.rgb if cell.fill.fgColor.type == "rgb" else None,
        cell.border.left.style,
        cell.border.right.style,
        cell.border.top.style,
        cell.border.bottom.style,
    )


def test_mobiliti_capacity_constants_are_expanded():
    assert len(SECTION_CATS) == 32
    assert MAX_PROD_PER_SECTION == 64
    assert SECTION_PROD_STARTS[0] == SECTION_CATS[0] + 1
    assert SECTION_CATS[1] - SECTION_CATS[0] == 35


def test_mobiliti_preserves_template_spacing_when_section_fits_base_capacity():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _many_products(3),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert str(ws.cell(47, 1).value).startswith("Subtotales Secci")
        assert "Secci" in str(ws.cell(48, 1).value)
        assert ws.row_dimensions[47].height == 24
        assert ws.row_dimensions[48].height == 26
    finally:
        wb.close()


def test_mobiliti_appended_sections_keep_template_visual_skeleton():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _many_products(3),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        checks = [
            (48, SECTION_CATS[16]),
            (49, SECTION_CATS[16] + 1),
            (82, SECTION_CATS[16] + 34),
        ]
        for source_row, target_row in checks:
            assert ws.row_dimensions[source_row].height == ws.row_dimensions[target_row].height
            assert _row_merges(ws, source_row) == _row_merges(ws, target_row)
            for col in range(1, 34):
                assert _visual_signature(ws, source_row, col) == _visual_signature(ws, target_row, col)
    finally:
        wb.close()


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
        assert row_map[40] == SECTION_PROD_STARTS[0] + 31
        assert row_map[41] == SECTION_PROD_STARTS[0] + 32
        assert row_map[72] == SECTION_PROD_STARTS[0] + 63
        assert str(ws.cell(79, 1).value).startswith("Subtotales Secci")
        assert row_map[73] == 81
        assert ws.cell(row_map[73], 4).value == "=Quotation!B73"
    finally:
        wb.close()
