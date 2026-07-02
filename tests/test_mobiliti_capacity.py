from pathlib import Path
import sys

from openpyxl.cell.cell import MergedCell
from openpyxl import load_workbook
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.formula import ArrayFormula

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.engine import (  # noqa: E402
    MAX_PROD_PER_SECTION,
    MOBILITI_DISCOUNT_AMOUNT_COL,
    MOBILITI_FINAL_PRICE_COL,
    MOBILITI_GP_COL,
    MOBILITI_LIST_TOTAL_COL,
    MOBILITI_MIN_UNIT_PRICE_COL,
    MOBILITI_PROVIDER_LIST_NAME,
    MOBILITI_STATUS_COL,
    MOBILITI_TOTAL_ROW,
    MOBILITI_UNIT_PRICE_COL,
    SECTION_CATS,
    SECTION_PROD_STARTS,
    _apply_mobiliti_provider_validation,
    _find_mobiliti_total_row,
    _lumbro_accessories_for_item,
    _write_fletes,
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


def _one_product_per_section(count: int) -> list[QuoteItem]:
    items: list[QuoteItem] = []
    for index in range(count):
        source_row = 9 + index
        items.append(QuoteItem(tipo="categoria", row=source_row, nombre=f"SECCION {index + 1}"))
        items.append(
            QuoteItem(
                tipo="producto",
                row=source_row,
                nombre=f"CLG{index:03d} Task Chair",
                descripcion="Task chair",
                dimension="600*600*900 mm",
                cantidad=1,
                precio=100,
            )
        )
    return items


def _first_section_with_exact_count_then_second(first_count: int) -> list[QuoteItem]:
    items = [QuoteItem(tipo="categoria", row=8, nombre="OPERATIVOS")]
    for index in range(first_count):
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
    items.append(QuoteItem(tipo="categoria", row=200, nombre="PRIVADOS"))
    items.append(
        QuoteItem(
            tipo="producto",
            row=201,
            nombre="DG64 Storage Cabinets",
            descripcion="Cabinet",
            dimension="1200*450*900 mm",
            cantidad=1,
            precio=100,
        )
    )
    return items


def _first_section_needing_65_rows_then_second() -> list[QuoteItem]:
    items = [QuoteItem(tipo="categoria", row=8, nombre="OPERATIVOS")]
    for index in range(63):
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
    items.append(
        QuoteItem(
            tipo="producto",
            row=72,
            nombre="DU54 High single desk for two people",
            descripcion="Desk for two people",
            dimension="1200*4800*750 mm",
            cantidad=1,
            precio=100,
        )
    )
    items.append(QuoteItem(tipo="categoria", row=80, nombre="PRIVADOS"))
    items.append(
        QuoteItem(
            tipo="producto",
            row=81,
            nombre="DG64 Storage Cabinets",
            descripcion="Cabinet",
            dimension="1200*450*900 mm",
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


def _fill_signature(ws, row: int, col: int) -> tuple:
    fill = ws.cell(row, col).fill
    return (
        fill.fill_type,
        fill.fgColor.type,
        fill.fgColor.rgb if fill.fgColor.type == "rgb" else fill.fgColor.indexed,
    )


def _fill_border_signature(ws, row: int, col: int) -> tuple:
    cell = ws.cell(row, col)
    border = cell.border
    return (
        _fill_signature(ws, row, col),
        border.left.style,
        border.right.style,
        border.top.style,
        border.bottom.style,
    )


def _cell_has_conditional_format(ws, coordinate: str) -> bool:
    for cf in getattr(ws.conditional_formatting, "_cf_rules", {}):
        for cell_range in str(cf.sqref).split():
            if coordinate in CellRange(cell_range):
                return True
    return False


def _count_cells_containing(ws, text: str) -> int:
    needle = text.upper()
    total = 0
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and needle in cell.value.upper():
                total += 1
    return total


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
            for col in range(1, MOBILITI_STATUS_COL):
                assert _visual_signature(ws, source_row, col) == _visual_signature(ws, target_row, col)
    finally:
        wb.close()


def test_mobiliti_appended_provider_column_keeps_product_yellow_fill_and_borders():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _one_product_per_section(17),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        first_appended_product_row = SECTION_PROD_STARTS[16]
        assert _fill_border_signature(ws, first_appended_product_row, 6) == _fill_border_signature(
            ws,
            first_appended_product_row,
            5,
        )
        assert _fill_border_signature(ws, first_appended_product_row + 1, 6) == _fill_border_signature(
            ws,
            first_appended_product_row,
            5,
        )
        first_appended_blank_row = SECTION_CATS[16] + 33
        assert _fill_border_signature(ws, first_appended_blank_row, 6) == _fill_border_signature(
            ws,
            first_appended_blank_row,
            5,
        )
    finally:
        wb.close()


def test_lumbro_electrification_treats_px_as_pax_synonym():
    workstation = QuoteItem(
        tipo="producto",
        row=9,
        nombre="Estacion Lido 8 px",
        descripcion="Workstation para equipo operativo",
        dimension="1200*4800*750 mm",
        cantidad=1,
        precio=1000,
    )
    meeting_table = QuoteItem(
        tipo="producto",
        row=10,
        nombre="Sala de juntas 8 px",
        descripcion="Mesa para equipo directivo",
        dimension="6000*1800*750 mm",
        cantidad=1,
        precio=1222,
    )

    assert _lumbro_accessories_for_item(workstation, "Escritorios-WorkStation") == [
        ("LIDO.OP-INT", 8),
        ("JUMP-1.5M", 8),
        ("CAJA-FUS", 2),
    ]
    assert _lumbro_accessories_for_item(meeting_table, "Mesas de Juntas") == [
        ("MULT-LIDO-INT", 2),
        ("JUMP-1.5M", 3),
    ]


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
        assert row_map[73] == SECTION_PROD_STARTS[0] + 64
        assert str(ws.cell(85, 1).value).startswith("Subtotales Secci")
        assert ws.cell(row_map[73], 4).value == "=Quotation!B73"
    finally:
        wb.close()


def test_mobiliti_does_not_split_source_category_that_needs_more_than_64_rows():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        row_map, lumbro_row_map = _write_mobiliti(
            ws,
            _first_section_needing_65_rows_then_second(),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert row_map[72] == 77
        assert lumbro_row_map[72] == [78]
        assert ws.cell(80, 1).value == "Subtotales Sección 1"
        assert ws.cell(81, 1).value == "Sección 2 - PRIVADOS"
        assert row_map[81] == 82
        assert ws.cell(82, 4).value == "=Quotation!B81"
    finally:
        wb.close()


def test_mobiliti_overflow_section_keeps_product_row_format_and_formulas():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        expected_subtotal_height = ws.row_dimensions[47].height
        expected_section_height = ws.row_dimensions[48].height
        expected_product_height = ws.row_dimensions[49].height
        expected_subtotal_merges = _row_merges(ws, 47)
        expected_section_merges = _row_merges(ws, 48)
        expected_product_merges = _row_merges(ws, 49)
        row_map, _ = _write_mobiliti(
            ws,
            _many_products(70),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        first_overflow_row = row_map[41]
        assert first_overflow_row == 46
        section1_subtotal_row = 85
        section2_title_row = 86
        assert ws.row_dimensions[section1_subtotal_row].height == expected_subtotal_height
        assert ws.row_dimensions[section2_title_row].height == expected_section_height
        assert _row_merges(ws, section1_subtotal_row) == expected_subtotal_merges
        assert _row_merges(ws, section2_title_row) == expected_section_merges
        assert ws.row_dimensions[first_overflow_row].height == expected_product_height
        assert ws.row_dimensions[first_overflow_row + 1].height == expected_product_height
        assert _row_merges(ws, first_overflow_row) == expected_product_merges
        assert _row_merges(ws, first_overflow_row + 1) == expected_product_merges
        assert "F49" not in str(ws.cell(first_overflow_row, 13).value)
        assert "J49" not in str(ws.cell(first_overflow_row, 13).value)
        assert f"F{first_overflow_row}" in str(ws.cell(first_overflow_row, 13).value)
        assert f"J{first_overflow_row}" in str(ws.cell(first_overflow_row, 13).value)
        assert ws.cell(first_overflow_row, MOBILITI_GP_COL).value == f"=(AD{first_overflow_row}-N{first_overflow_row})/AD{first_overflow_row}"
    finally:
        wb.close()


def test_mobiliti_overflow_intersection_rows_use_explicit_original_visual_colors():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _first_section_with_exact_count_then_second(MAX_PROD_PER_SECTION),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        for col in range(1, MOBILITI_STATUS_COL):
            if not isinstance(ws.cell(79, col), MergedCell):
                assert _fill_signature(ws, 79, col) == ("solid", "rgb", "FF404040")
        for col in range(1, 11):
            if not isinstance(ws.cell(80, col), MergedCell):
                assert _fill_signature(ws, 80, col) == ("solid", "rgb", "FF3E2500")
        for col in range(11, MOBILITI_STATUS_COL):
            assert _fill_signature(ws, 80, col) == ("solid", "rgb", "FF262626")
    finally:
        wb.close()


def test_mobiliti_commission_table_is_not_duplicated_by_product_row_copying():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        expected_count = _count_cells_containing(ws, "TABLA DE ESQUEMA COMISION")
        _write_mobiliti(
            ws,
            _first_section_with_exact_count_then_second(MAX_PROD_PER_SECTION),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert _count_cells_containing(ws, "TABLA DE ESQUEMA COMISION") == expected_count
    finally:
        wb.close()


def test_mobiliti_overflow_subtotals_do_not_keep_stale_array_formula_refs():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _first_section_with_exact_count_then_second(MAX_PROD_PER_SECTION),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert not isinstance(ws["AE79"].value, ArrayFormula)
        assert ws["AE79"].value == "=IFERROR(AVERAGE(AE14:AE77),0)"
        assert "AE79" not in ws.array_formulae
    finally:
        wb.close()


def test_mobiliti_appended_sections_do_not_keep_stale_blank_row_formulas():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _one_product_per_section(17),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        first_appended_blank_row = SECTION_CATS[16] + 33
        for col in range(4, MOBILITI_STATUS_COL):
            assert ws.cell(first_appended_blank_row, col).value is None
        assert ws.cell(first_appended_blank_row, MOBILITI_STATUS_COL).value is None
    finally:
        wb.close()


def test_fletes_region_helper_formulas_are_div_zero_safe():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Fletes"]
    try:
        _write_fletes(ws)

        assert ws["D19"].value == f"=Mobiliti!H{MOBILITI_TOTAL_ROW}"
        assert ws["F20"].value == "=IF($D$19=0,0,D11/$D$19)"
        for row in range(6, 19):
            assert str(ws.cell(row, 11).value).startswith("=IFERROR(")
            assert str(ws.cell(row, 14).value).startswith("=IFERROR(")
        for row in range(27, 31):
            assert ws.cell(row, 2).value == (
                f"=IF($D$19=0,0,_xlfn.XLOOKUP(A{row},Taba_Region,Fletes!$D$5:$D$18)/$D$19)"
            )
            assert ws.cell(row, 3).value == 0
            assert ws.cell(row, 4).value == 0
            assert "#DIV/0!" not in {
                str(ws.cell(row, col).value)
                for col in range(2, 5)
            }
    finally:
        wb.close()


def test_fletes_total_muebles_references_dynamic_mobiliti_total_row():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws_mob = wb["Mobiliti"]
    ws_fletes = wb["Fletes"]
    try:
        _write_mobiliti(
            ws_mob,
            _one_product_per_section(17),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        total_row = _find_mobiliti_total_row(ws_mob)
        _write_fletes(ws_fletes, total_row)

        assert total_row is not None
        assert total_row != 573
        assert ws_fletes["D19"].value == f"=Mobiliti!H{total_row}"
    finally:
        wb.close()


def test_mobiliti_p9_references_dynamic_total_row():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _one_product_per_section(17),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert ws["P9"].value == f"=P8/H{MOBILITI_TOTAL_ROW}"
        assert ws["P9"].value == "=P8/H1133"
    finally:
        wb.close()


def test_mobiliti_appended_ok_status_keeps_conditional_formatting():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _one_product_per_section(17),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        first_appended_product_row = SECTION_PROD_STARTS[16]
        first_appended_blank_row = SECTION_CATS[16] + 33
        assert ws.cell(first_appended_product_row, MOBILITI_STATUS_COL).value == (
            f'=IF(AH{first_appended_product_row}<30%,"ERROR","OK")'
        )
        assert _cell_has_conditional_format(ws, f"AI{first_appended_product_row}")
        assert ws.cell(first_appended_blank_row, MOBILITI_STATUS_COL).value is None
    finally:
        wb.close()


def test_mobiliti_provider_validation_uses_named_single_column_range_for_excel():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _first_section_with_exact_count_then_second(MAX_PROD_PER_SECTION),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )
        _apply_mobiliti_provider_validation(ws)

        provider_validations = [
            validation
            for validation in ws.data_validations.dataValidation
            if str(validation.sqref).startswith("F14:F77")
        ]
        assert len(provider_validations) == 1
        assert provider_validations[0].formula1 == MOBILITI_PROVIDER_LIST_NAME
        assert "!" not in str(provider_validations[0].formula1)
        assert MOBILITI_PROVIDER_LIST_NAME in wb.defined_names
        assert wb.defined_names[MOBILITI_PROVIDER_LIST_NAME].attr_text == "Proveedores!$A$2:$A$32"
        assert "F81:F112" in str(provider_validations[0].sqref)
    finally:
        wb.close()


def test_mobiliti_uses_visible_new_caratula_price_for_totals():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _many_products(1),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        assert ws.column_dimensions["W"].hidden is True
        assert ws.cell(12, MOBILITI_UNIT_PRICE_COL).value == "Precio Unitario Base (Aux)"
        assert ws.cell(12, MOBILITI_MIN_UNIT_PRICE_COL).value == "Precio Unitario de Lista (Carátula)"
        assert ws.cell(14, MOBILITI_LIST_TOTAL_COL).value == "=X14*H14"
        assert ws.cell(14, MOBILITI_DISCOUNT_AMOUNT_COL).value == "=X14*AA14"
        assert ws.cell(14, MOBILITI_FINAL_PRICE_COL).value == '=IF(AA14>Z14,"ERROR",(X14-AB14))'
        assert ws["AE14"].value == '=IF(A15=TRUE,MAX(0,1-(AF14/X14)),"NA")'
    finally:
        wb.close()


def test_mobiliti_status_conditional_formatting_covers_inner_overflow_gaps():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        _write_mobiliti(
            ws,
            _many_products(70),
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        for row in (47, 48):
            assert ws.cell(row, MOBILITI_STATUS_COL).value == f'=IF(AH{row}<30%,"ERROR","OK")'
            assert _cell_has_conditional_format(ws, f"AI{row}")
    finally:
        wb.close()


def test_mobiliti_appended_section_formulas_use_dynamic_total_and_zero_guards():
    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    items = _one_product_per_section(17)
    items.append(
        QuoteItem(
            tipo="producto",
            row=90,
            nombre="CLG extra Task Chair",
            descripcion="Task chair",
            dimension="600*600*900 mm",
            cantidad=1,
            precio=100,
        )
    )
    try:
        _write_mobiliti(
            ws,
            items,
            {"m3": "H", "cantidad": "G", "unit_price": "J"},
        )

        first_appended_product_row = SECTION_PROD_STARTS[16]
        second_appended_product_row = first_appended_product_row + 1
        landed_cost_formula = ws.cell(first_appended_product_row, 13).value
        second_landed_cost_formula = ws.cell(second_appended_product_row, 13).value
        freight_formula = ws.cell(first_appended_product_row, 22).value

        assert f"K{MOBILITI_TOTAL_ROW}<=$AP$" in landed_cost_formula
        assert f"K{MOBILITI_TOTAL_ROW}<=$AO$" in landed_cost_formula
        assert "K608<=$AO$" not in landed_cost_formula
        assert f"K{MOBILITI_TOTAL_ROW}<=$AO$" in second_landed_cost_formula
        assert "K1134<=$AO$" not in second_landed_cost_formula
        assert freight_formula == (
            f"=IFERROR(IF(OR(K{first_appended_product_row}=0,L{first_appended_product_row}=0),"
            f"U{first_appended_product_row}/$H${MOBILITI_TOTAL_ROW},"
            f"U{first_appended_product_row}*(K{first_appended_product_row}/L{first_appended_product_row}))"
            f"*H{first_appended_product_row},0)"
        )
    finally:
        wb.close()
