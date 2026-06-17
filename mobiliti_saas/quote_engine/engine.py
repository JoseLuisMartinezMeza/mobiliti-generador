from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass
from io import BytesIO
import hashlib
import math
import os
import posixpath
from pathlib import Path
import re
import shutil
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.image import Image as XlsxImage
from openpyxl.formula.translate import Translator
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.datavalidation import DataValidation
from PIL import Image as PILImage

from .classification import classify_product_name, load_category_dictionary
from .descriptions import build_product_description, normalize_description_language
from .ai_image_provider import dezgo_config_from_env, generate_with_dezgo, normalize_image_provider
from .image_processing import improve_image_map
from .images import center_image_in_cell, extract_images, fit_image_to_cell, image_scale_for_category
from .parser import QuoteItem, col_index, read_items


MONEY_FORMAT = '$#,##0.00;[Red]-$#,##0.00;"-"'
PERCENT_FORMAT = "0%"
MOBILITI_SECTION_COUNT = 32
BASE_PROD_PER_SECTION = 32
MAX_PROD_PER_SECTION = 64
MOBILITI_FIRST_SECTION_ROW = 13
MOBILITI_SECTION_BLOCK_HEIGHT = BASE_PROD_PER_SECTION + 3
SECTION_CATS = [
    MOBILITI_FIRST_SECTION_ROW + index * MOBILITI_SECTION_BLOCK_HEIGHT
    for index in range(MOBILITI_SECTION_COUNT)
]
SECTION_PROD_STARTS = [row + 1 for row in SECTION_CATS]
SECTION_SUBTOTAL_ROWS = [row + BASE_PROD_PER_SECTION + 2 for row in SECTION_CATS]
MOBILITI_TOTAL_ROW = SECTION_SUBTOTAL_ROWS[-1] + 1
DEFAULT_EXCHANGE_RATE = 20.0
DEFAULT_DELIVERY_PLACE = "Guadalajara"
DEFAULT_DISCOUNT_PERCENT = 40.0
DEFAULT_MOBILITI_REGION = "Centro"
MOBILITI_REGION_COL = 16
MOBILITI_MAX_DISCOUNT_COL = 25
MOBILITI_COVER_DISCOUNT_COL = 26
MOBILITI_DISCOUNT_AMOUNT_COL = 27
MOBILITI_FINAL_PRICE_COL = 28
MOBILITI_COMMERCIAL_TOTAL_COL = 29
MOBILITI_CLEAR_COLS = tuple(range(4, 33))
FLETE_ROUTES = {
    5: ("Guadalajara", "Monterrey"),
    7: ("Guadalajara", "Mexico City"),
    9: ("Guadalajara", "Tijuana"),
    11: ("Guadalajara", "Mérida, Yucatán"),
    13: ("Guadalajara", "Querétaro"),
    15: ("Guadalajara", "State of Mexico"),
    17: ("Guadalajara", "Guadalajara"),
}
LUMBRO_PRICE_ROWS = {
    "MULT-LIDO-INT": 348,
    "LIDO.OP-INT": 380,
    "JUMP-1.5M": 396,
    "CAJA-FUS": 406,
}
LUMBRO_CATEGORY = "Multicontactos"
LUMBRO_PROVIDER = "Lumbro"
LUMBRO_ACCESSORY_IMAGE = Path(__file__).resolve().parent / "assets" / "lumbro_multicontacto_blanco.png"
LUMBRO_WORKSTATION_IMAGE = Path(__file__).resolve().parent / "assets" / "lumbro_workstation_multiusuario.png"


@dataclass(frozen=True)
class MobilitiSectionLayout:
    section_row: int
    product_start: int
    capacity: int
    subtotal_row: int


def _sheet_name(name: str) -> str:
    return "'{}'".format(name.replace("'", "''")) if any(ch in name for ch in " :!'-*[]?/\\") else name


def _formula(sheet: str, cell: str) -> str:
    return f"={_sheet_name(sheet)}!{cell}"


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return default


def _discount_rate(metadata: dict[str, Any]) -> float:
    raw = metadata.get("descuento", metadata.get("discount_percent", DEFAULT_DISCOUNT_PERCENT))
    value = _num(raw, DEFAULT_DISCOUNT_PERCENT)
    if 0 <= value <= 1:
        return value
    return max(0.0, min(value, 100.0)) / 100.0


def _excel_decimal(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _normalized_text(value: Any) -> str:
    text = "" if value is None else str(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _detect_user_count(item: QuoteItem) -> int | None:
    text = _normalized_text(
        f"{item.nombre or ''} {item.descripcion or ''} {item.dimension or ''} {item.categoria or ''}"
    )
    patterns = [
        r"(\d+)\s*pax\b",
        r"(\d+)\s*(?:usuarios?|personas?|users?)\b",
        r"(?:pax|capacidad|usuarios?|personas?|users?)\s*(?:de|para)?\s*(\d+)\b",
    ]
    matches: list[int] = []
    for pattern in patterns:
        matches.extend(int(match) for match in re.findall(pattern, text))
    return max(matches) if matches else None


def _item_quantity(item: QuoteItem) -> int:
    return max(1, int(math.ceil(_num(item.cantidad, 1))))


def _lumbro_accessories_for_item(item: QuoteItem, category: str) -> list[tuple[str, int]]:
    quantity = _item_quantity(item)
    users_per_item = _detect_user_count(item)

    if category == "Escritorios-WorkStation":
        if users_per_item:
            total_users = users_per_item * quantity
            fuse_count = math.ceil(total_users / 8) * 2
            return [
                ("LIDO.OP-INT", total_users),
                ("JUMP-1.5M", total_users),
                ("CAJA-FUS", fuse_count),
            ]
        return [("MULT-LIDO-INT", 1)]

    if category == "Mesas de Juntas":
        total_users = (users_per_item or 4) * quantity
        multicontacts = max(1, math.ceil(total_users / 4))
        return [
            ("MULT-LIDO-INT", multicontacts),
            ("JUMP-1.5M", multicontacts + 1),
        ]

    return []


def _copy_cell_style(src, dst) -> None:
    if src.has_style:
        dst._style = copy(src._style)
    if src.number_format:
        dst.number_format = src.number_format
    if src.alignment:
        dst.alignment = copy(src.alignment)


def _copy_row_style(ws, source_row: int, target_row: int, max_col: int = 10) -> None:
    ws.row_dimensions[target_row].height = ws.row_dimensions[source_row].height
    for col in range(1, max_col + 1):
        _copy_cell_style(ws.cell(source_row, col), ws.cell(target_row, col))


def _find_mobiliti_total_row(ws) -> int | None:
    for row in range(1, ws.max_row + 1):
        if str(ws.cell(row, 6).value or "").strip().upper() == "TOTAL PIEZAS":
            return row
    return None


def _find_provider_discount_start(ws) -> int | None:
    for row in range(1, ws.max_row + 1):
        if str(ws.cell(row, 35).value or "").strip() == "Sunon Inc":
            return row
    return None


def _snapshot_mobiliti_row(ws, row: int, max_col: int) -> dict[str, Any]:
    return {
        "height": ws.row_dimensions[row].height,
        "cells": [
            {
                "value": ws.cell(row, col).value,
                "style": copy(ws.cell(row, col)._style),
                "number_format": ws.cell(row, col).number_format,
                "alignment": copy(ws.cell(row, col).alignment),
            }
            for col in range(1, max_col + 1)
        ],
        "merges": [
            str(merged)
            for merged in ws.merged_cells.ranges
            if merged.min_row == row and merged.max_row == row
        ],
    }


def _translate_formula_value(value: Any, origin: str, target: str) -> Any:
    if isinstance(value, str) and value.startswith("="):
        try:
            return Translator(value, origin=origin).translate_formula(target)
        except Exception:
            return value
    return value


def _copy_mobiliti_row_from_snapshot(
    ws,
    snapshot: dict[str, Any],
    target_row: int,
    *,
    source_row: int,
    max_col: int,
    translate_formulas: bool = True,
) -> None:
    _unmerge_row(ws, target_row)
    ws.row_dimensions[target_row].height = snapshot["height"]
    for col, data in enumerate(snapshot["cells"], start=1):
        cell = ws.cell(target_row, col)
        cell.value = None
        cell._style = copy(data["style"])
        cell.number_format = data["number_format"]
        cell.alignment = copy(data["alignment"])
        value = data["value"]
        if translate_formulas:
            value = _translate_formula_value(
                value,
                f"{get_column_letter(col)}{source_row}",
                f"{get_column_letter(col)}{target_row}",
            )
        cell.value = value

    row_offset = target_row - source_row
    for merge in snapshot["merges"]:
        shifted = CellRange(merge)
        shifted.shift(row_shift=row_offset, col_shift=0)
        try:
            ws.merge_cells(str(shifted))
        except ValueError:
            pass


def _normalize_mobiliti_row_formulas(ws, row: int, total_row: int, discount_start: int | None) -> None:
    for col in range(1, min(ws.max_column, 48) + 1):
        cell = ws.cell(row, col)
        value = cell.value
        if not isinstance(value, str) or not value.startswith("="):
            continue
        value = value.replace("$H$573", f"$H${total_row}")
        value = re.sub(r"K\d+(?=\s*<=\s*\$AO\$)", f"K{total_row}", value)
        value = re.sub(r"K\d+(?=\s*>\s*\$AN\$)", f"K{total_row}", value)
        if discount_start:
            offset = discount_start - 577
            value = value.replace("$AO$578", f"$AO${578 + offset}")
            value = value.replace("$AN$579", f"$AN${579 + offset}")
            value = value.replace("$AN$580", f"$AN${580 + offset}")
        cell.value = value

    if discount_start:
        discount_end = discount_start + 30
        ws.cell(row, MOBILITI_MAX_DISCOUNT_COL).value = (
            f'=IFERROR(VLOOKUP(F{row},$AI${discount_start}:$AJ${discount_end},2,FALSE),0.5)'
        )
        ws.cell(row, MOBILITI_MAX_DISCOUNT_COL).number_format = PERCENT_FORMAT


def _set_mobiliti_subtotal_formulas(
    ws,
    row: int,
    section_number: int,
    product_start: int,
    capacity: int = BASE_PROD_PER_SECTION,
) -> None:
    product_end = product_start + capacity - 1
    ws.cell(row, 1).value = f"Subtotales Sección {section_number}"
    for col in (8, 12, 14, 24, 29, 32):
        letter = get_column_letter(col)
        ws.cell(row, col).value = f"=SUM({letter}{product_start}:{letter}{product_end})"
    ws.cell(row, 33).value = f"=IFERROR(1-(N{row}/AC{row}),0)"


def _set_mobiliti_total_formulas(
    ws,
    row: int,
    subtotal_rows: list[int] | None = None,
) -> None:
    subtotal_rows = subtotal_rows or SECTION_SUBTOTAL_ROWS
    ws.cell(row, 8).value = "=" + "+".join(f"H{subtotal}" for subtotal in subtotal_rows)
    ws.cell(row, 11).value = "=" + "+".join(f"L{subtotal}" for subtotal in subtotal_rows)
    ws.cell(row, 13).value = "=" + "+".join(f"N{subtotal}" for subtotal in subtotal_rows)
    ws.cell(row, 24).value = "=" + "+".join(f"X{subtotal}" for subtotal in subtotal_rows)
    ws.cell(row, 29).value = "=" + "+".join(f"AC{subtotal}" for subtotal in subtotal_rows)
    ws.cell(row, 32).value = "=" + "+".join(f"AF{subtotal}" for subtotal in subtotal_rows)
    ws.cell(row, 33).value = f"=AVERAGE({','.join(f'AG{subtotal}' for subtotal in subtotal_rows)})"


def _normalize_mobiliti_section_capacities(capacities: list[int]) -> list[int]:
    normalized = [
        MAX_PROD_PER_SECTION if capacity > BASE_PROD_PER_SECTION else BASE_PROD_PER_SECTION
        for capacity in capacities[:MOBILITI_SECTION_COUNT]
    ]
    while len(normalized) < MOBILITI_SECTION_COUNT:
        normalized.append(BASE_PROD_PER_SECTION)
    return normalized


def _mobiliti_section_capacities(
    items: list[QuoteItem],
    category_dictionary: dict[str, str],
) -> list[int]:
    needs: list[int] = []

    for item in items:
        if item.tipo == "categoria":
            if not needs or needs[-1] > 0:
                needs.append(0)
            continue
        if item.tipo != "producto":
            continue

        if not needs:
            needs.append(0)

        category = classify_product_name(str(item.nombre or ""), category_dictionary)
        rows_needed = 1 + len(_lumbro_accessories_for_item(item, category))
        while rows_needed > 0:
            remaining = MAX_PROD_PER_SECTION - needs[-1]
            if remaining <= 0:
                needs.append(0)
                remaining = MAX_PROD_PER_SECTION
            take = min(rows_needed, remaining)
            needs[-1] += take
            rows_needed -= take

    return _normalize_mobiliti_section_capacities(needs)


def _mobiliti_product_ranges(ws) -> list[tuple[int, int]]:
    ranges = getattr(ws, "_mobiliti_product_ranges", None)
    if ranges:
        return list(ranges)
    return [(start_row, BASE_PROD_PER_SECTION) for start_row in SECTION_PROD_STARTS]


def _write_mobiliti_section_title(
    ws,
    layout: MobilitiSectionLayout,
    section_number: int,
    title: str,
) -> None:
    anchor_col = _merged_anchor_column(ws, layout.section_row, 4)
    ws.cell(layout.section_row, anchor_col).value = f"Secci\u00f3n {section_number} - {title}"


def _ensure_mobiliti_capacity_legacy(ws) -> None:
    if ws.max_row >= MOBILITI_TOTAL_ROW and str(ws.cell(MOBILITI_TOTAL_ROW, 6).value or "") == "TOTAL PIEZAS":
        return

    max_col = max(ws.max_column, 48)
    original_total_row = _find_mobiliti_total_row(ws)
    if not original_total_row:
        return

    first_section = _snapshot_mobiliti_row(ws, 13, max_col)
    section = _snapshot_mobiliti_row(ws, 48, max_col)
    product = _snapshot_mobiliti_row(ws, 14, max_col)
    blank = _snapshot_mobiliti_row(ws, 46, max_col)
    subtotal = _snapshot_mobiliti_row(ws, 47, max_col)

    rows_to_insert = MOBILITI_TOTAL_ROW - original_total_row
    if rows_to_insert > 0:
        ws.insert_rows(original_total_row, rows_to_insert)

    discount_start = _find_provider_discount_start(ws)

    for index, section_row in enumerate(SECTION_CATS, start=1):
        section_snapshot = first_section if index == 1 else section
        section_source_row = 13 if index == 1 else 48
        _copy_mobiliti_row_from_snapshot(
            ws,
            section_snapshot,
            section_row,
            source_row=section_source_row,
            max_col=max_col,
            translate_formulas=False,
        )
        ws.cell(section_row, _merged_anchor_column(ws, section_row, 4)).value = f"Sección {index} - NOMBRE"

        product_start = section_row + 1
        for row in range(product_start, product_start + MAX_PROD_PER_SECTION):
            _copy_mobiliti_row_from_snapshot(
                ws,
                product,
                row,
                source_row=14,
                max_col=max_col,
            )
            _normalize_mobiliti_row_formulas(ws, row, MOBILITI_TOTAL_ROW, discount_start)

        blank_row = product_start + MAX_PROD_PER_SECTION
        _copy_mobiliti_row_from_snapshot(
            ws,
            blank,
            blank_row,
            source_row=46,
            max_col=max_col,
        )

        subtotal_row = SECTION_SUBTOTAL_ROWS[index - 1]
        _copy_mobiliti_row_from_snapshot(
            ws,
            subtotal,
            subtotal_row,
            source_row=47,
            max_col=max_col,
        )
        _set_mobiliti_subtotal_formulas(ws, subtotal_row, index, product_start)

    _set_mobiliti_total_formulas(ws, MOBILITI_TOTAL_ROW)
    ws["E4"] = f"=AC{MOBILITI_TOTAL_ROW}"
    ws["E6"] = f"=E4*E5"
    ws["E8"] = f"=(AC{MOBILITI_TOTAL_ROW}-M{MOBILITI_TOTAL_ROW})/AC{MOBILITI_TOTAL_ROW}"


def _ensure_mobiliti_capacity(ws, capacities: list[int]) -> list[MobilitiSectionLayout]:
    capacities = _normalize_mobiliti_section_capacities(capacities)
    max_col = max(ws.max_column, 48)
    original_total_row = _find_mobiliti_total_row(ws)
    if not original_total_row:
        layouts = [
            MobilitiSectionLayout(row, row + 1, capacity, row + capacity + 2)
            for row, capacity in zip(SECTION_CATS, capacities, strict=False)
        ]
        ws._mobiliti_product_ranges = [(layout.product_start, layout.capacity) for layout in layouts]
        return layouts

    section = _snapshot_mobiliti_row(ws, 48, max_col)
    first_product = _snapshot_mobiliti_row(ws, 14, max_col)
    section_product = _snapshot_mobiliti_row(ws, 49, max_col)
    section_blank = _snapshot_mobiliti_row(ws, 81, max_col)
    section_subtotal = _snapshot_mobiliti_row(ws, 82, max_col)

    rows_to_insert = MOBILITI_TOTAL_ROW - original_total_row
    if rows_to_insert > 0:
        ws.insert_rows(original_total_row, rows_to_insert)

    for index in range(16, MOBILITI_SECTION_COUNT):
        section_row = SECTION_CATS[index]
        _copy_mobiliti_row_from_snapshot(
            ws,
            section,
            section_row,
            source_row=48,
            max_col=max_col,
            translate_formulas=False,
        )
        ws.cell(section_row, _merged_anchor_column(ws, section_row, 4)).value = f"Secci\u00f3n {index + 1} - NOMBRE"

        product_start = section_row + 1
        for row in range(product_start, product_start + BASE_PROD_PER_SECTION):
            _copy_mobiliti_row_from_snapshot(
                ws,
                section_product,
                row,
                source_row=49,
                max_col=max_col,
            )

        blank_row = product_start + BASE_PROD_PER_SECTION
        _copy_mobiliti_row_from_snapshot(ws, section_blank, blank_row, source_row=81, max_col=max_col)

        subtotal_row = blank_row + 1
        _copy_mobiliti_row_from_snapshot(ws, section_subtotal, subtotal_row, source_row=82, max_col=max_col)

    layouts: list[MobilitiSectionLayout] = []
    inserted_rows = 0
    for index, capacity in enumerate(capacities):
        section_row = SECTION_CATS[index] + inserted_rows
        product_start = section_row + 1
        extra_rows = capacity - BASE_PROD_PER_SECTION
        if extra_rows > 0:
            insert_at = product_start + BASE_PROD_PER_SECTION
            ws.insert_rows(insert_at, extra_rows)
            product_snapshot = first_product if index == 0 else section_product
            product_source_row = 14 if index == 0 else 49
            for row in range(insert_at, insert_at + extra_rows):
                _copy_mobiliti_row_from_snapshot(
                    ws,
                    product_snapshot,
                    row,
                    source_row=product_source_row,
                    max_col=max_col,
                )
            inserted_rows += extra_rows

        subtotal_row = product_start + capacity + 1
        layouts.append(MobilitiSectionLayout(section_row, product_start, capacity, subtotal_row))

    total_row = MOBILITI_TOTAL_ROW + inserted_rows
    discount_start = _find_provider_discount_start(ws)
    subtotal_rows = [layout.subtotal_row for layout in layouts]

    for index, layout in enumerate(layouts, start=1):
        _write_mobiliti_section_title(ws, layout, index, "NOMBRE")
        for row in range(layout.product_start, layout.product_start + layout.capacity):
            _normalize_mobiliti_row_formulas(ws, row, total_row, discount_start)
        _set_mobiliti_subtotal_formulas(
            ws,
            layout.subtotal_row,
            index,
            layout.product_start,
            layout.capacity,
        )

    _set_mobiliti_total_formulas(ws, total_row, subtotal_rows)
    ws["E4"] = f"=AC{total_row}"
    ws["E6"] = f"=E4*E5"
    ws["E8"] = f"=(AC{total_row}-M{total_row})/AC{total_row}"
    ws._mobiliti_product_ranges = [(layout.product_start, layout.capacity) for layout in layouts]
    return layouts


def _unmerge_row(ws, row: int) -> None:
    for merged in list(ws.merged_cells.ranges):
        if merged.min_row <= row <= merged.max_row:
            try:
                ws.unmerge_cells(str(merged))
            except KeyError:
                try:
                    ws.merged_cells.ranges.remove(merged)
                except KeyError:
                    pass


def _clear_row(ws, row: int, max_col: int = 10) -> None:
    _unmerge_row(ws, row)
    for col in range(1, max_col + 1):
        ws.cell(row, col).value = None


def _merged_anchor_column(ws, row: int, default_col: int) -> int:
    for merged in ws.merged_cells.ranges:
        if (
            merged.min_row == row
            and merged.max_row == row
            and merged.min_col <= default_col <= merged.max_col
        ):
            return merged.min_col
    if isinstance(ws.cell(row, default_col), MergedCell):
        for col in range(default_col, 0, -1):
            if not isinstance(ws.cell(row, col), MergedCell):
                return col
    return default_col


def _find_terms_row(ws) -> int:
    for row in range(16, min(ws.max_row, 140) + 1):
        value = ws.cell(row, 1).value
        if isinstance(value, str) and "CONDICIONES" in value.upper():
            return row
    return 32


def _find_totals_row(ws, before_row: int) -> int:
    for row in range(16, max(16, before_row - 4) + 1):
        first_label = str(ws.cell(row, 4).value or "").strip().upper()
        second_label = str(ws.cell(row + 1, 4).value or "").strip().upper()
        total_label = str(ws.cell(row + 4, 4).value or "").strip().upper()
        if first_label == "SUBTOTAL:" and "FLETE" in second_label and total_label == "TOTAL:":
            return row
    return 21


def _snapshot_rows(ws, start_row: int, end_row: int, max_col: int = 10) -> dict[str, Any]:
    rows = []
    for row in range(start_row, end_row + 1):
        cells = []
        for col in range(1, max_col + 1):
            cell = ws.cell(row, col)
            cells.append(
                {
                    "value": cell.value,
                    "style": copy(cell._style),
                    "number_format": cell.number_format,
                    "alignment": copy(cell.alignment),
                }
            )
        rows.append(cells)
    merges = [
        str(merged)
        for merged in ws.merged_cells.ranges
        if start_row <= merged.min_row and merged.max_row <= end_row
    ]
    heights = {row: ws.row_dimensions[row].height for row in range(start_row, end_row + 1)}
    return {"rows": rows, "merges": merges, "heights": heights}


def _restore_rows(ws, start_row: int, snapshot: dict[str, Any]) -> int:
    row = start_row
    source_start = min(snapshot["heights"].keys()) if snapshot["heights"] else start_row
    row_offset = start_row - source_start
    for cells in snapshot["rows"]:
        _clear_row(ws, row)
        for col, data in enumerate(cells, start=1):
            cell = ws.cell(row, col)
            cell.value = data["value"]
            cell._style = copy(data["style"])
            cell.number_format = data["number_format"]
            cell.alignment = copy(data["alignment"])
        source_row = row - row_offset
        ws.row_dimensions[row].height = snapshot["heights"].get(source_row)
        row += 1
    for merged in snapshot["merges"]:
        shifted = CellRange(merged)
        shifted.shift(row_shift=row_offset, col_shift=0)
        try:
            ws.merge_cells(str(shifted))
        except ValueError:
            pass
    return row - 1


def _default_template() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Cotizacion"
    wb.create_sheet("Mobiliti")
    widths = [78, 156, 42, 22, 12, 14, 12, 14, 14, 16]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.merge_cells("A1:J1")
    ws["A1"] = "MOBILITI - COTIZACION"
    ws["A1"].fill = PatternFill("solid", fgColor="12332F")
    ws["A1"].font = Font(color="FFFFFF", bold=True, size=16)
    ws["A1"].alignment = Alignment(horizontal="center")
    for row in [16, 18, 21, 22, 23, 24, 25]:
        for col in range(1, 11):
            ws.cell(row, col).alignment = Alignment(vertical="top", wrap_text=True)
    return wb


def _set_cotizacion_image_column_px(
    ws: Worksheet, pixel_width: float = 1100, column: str = "B"
) -> None:
    ws.column_dimensions[column].width = max(1.0, (float(pixel_width) - 5) / 7)


def _load_template(template_path: str | Path | None) -> Workbook:
    if template_path:
        path = Path(template_path)
        if not path.exists():
            raise FileNotFoundError(f"Plantilla no encontrada: {path}")
        wb = load_workbook(path, keep_links=False)
        _sanitize_template_workbook(wb)
        return wb
    return _default_template()


def _load_lumbro_prices(template_path: str | Path | None) -> dict[str, float]:
    if not template_path:
        return {}
    path = Path(template_path)
    if not path.exists():
        return {}

    wb = load_workbook(path, data_only=True, keep_links=False)
    try:
        if "SPEC-GUIDE-LUMBRO" not in wb.sheetnames:
            return {}
        ws = wb["SPEC-GUIDE-LUMBRO"]
        prices: dict[str, float] = {}
        for code, row in LUMBRO_PRICE_ROWS.items():
            prices[code] = _num(ws.cell(row, 5).value, 0)
        return prices
    finally:
        wb.close()


def _sanitize_template_workbook(wb: Workbook) -> None:
    for name in list(wb.defined_names.keys()):
        defined_name = wb.defined_names[name]
        text = str(getattr(defined_name, "attr_text", "") or "")
        if "#REF!" in text or "[" in text or name.startswith("LOCAL_") or name == "Hon":
            del wb.defined_names[name]

    for ws in wb.worksheets:
        if not (ws.title.startswith("SPEC") or ws.title.startswith("Spec")):
            continue
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.value = None


def _quotation_image_sort_key(img: XlsxImage) -> tuple[int, int]:
    marker = getattr(getattr(img, "anchor", None), "_from", None)
    return (
        int(getattr(marker, "row", 0) or 0),
        int(getattr(marker, "col", 0) or 0),
    )


def _quotation_used_bounds(ws: Any) -> tuple[int, int]:
    max_row = 1
    max_col = 1
    for row in ws.iter_rows():
        for cell in row:
            if cell.value not in (None, ""):
                max_row = max(max_row, cell.row)
                max_col = max(max_col, cell.column)
    for merged in ws.merged_cells.ranges:
        max_row = max(max_row, merged.max_row)
        max_col = max(max_col, merged.max_col)
    for img in getattr(ws, "_images", []):
        row, col = _quotation_image_sort_key(img)
        max_row = max(max_row, row + 1)
        max_col = max(max_col, col + 1)
    return max_row, max(max_col, min(ws.max_column, 32))


def _apply_quotation_borders(ws: Any, max_row: int, max_col: int) -> None:
    side = Side(style="thin", color="000000")
    border = Border(left=side, right=side, top=side, bottom=side)
    for row in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
        for cell in row:
            cell.border = border


def _apply_quotation_item_name_font(ws: Any, max_row: int) -> None:
    for row in range(1, max_row + 1):
        for column in (2, 4):
            cell = ws.cell(row, column)
            font = copy(cell.font)
            font.color = "000000"
            cell.font = font


_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip_resolve_part(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _zip_rel_target(from_part: str, to_part: str) -> str:
    return posixpath.relpath(to_part, posixpath.dirname(from_part))


def _worksheet_part_for_name(zf: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rel_id = None
    for sheet in workbook.find(f"{{{_SHEET_NS}}}sheets"):
        if sheet.attrib.get("name") == sheet_name:
            rel_id = sheet.attrib[f"{{{_OFFICE_REL_NS}}}id"]
            break
    if rel_id is None:
        raise ValueError(f"No se encontro la hoja {sheet_name!r}")

    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.attrib.get("Id") == rel_id:
            return _zip_resolve_part("xl/workbook.xml", rel.attrib["Target"])
    raise ValueError(f"No se encontro la relacion de la hoja {sheet_name!r}")


def _drawing_part_for_sheet(zf: zipfile.ZipFile, sheet_part: str) -> tuple[str, str]:
    sheet = ET.fromstring(zf.read(sheet_part))
    drawing = sheet.find(f"{{{_SHEET_NS}}}drawing")
    if drawing is None:
        raise ValueError("La hoja Quotation no tiene drawing de imagenes")
    rel_id = drawing.attrib[f"{{{_OFFICE_REL_NS}}}id"]
    sheet_rels_path = posixpath.join(
        posixpath.dirname(sheet_part),
        "_rels",
        f"{posixpath.basename(sheet_part)}.rels",
    )
    sheet_rels = ET.fromstring(zf.read(sheet_rels_path))
    for rel in sheet_rels:
        if rel.attrib.get("Id") == rel_id:
            return _zip_resolve_part(sheet_part, rel.attrib["Target"]), sheet_rels_path
    raise ValueError("No se encontro la relacion del drawing de Quotation")


def _image_parts_from_rels(zf: zipfile.ZipFile, drawing_part: str, rels_path: str) -> set[str]:
    image_parts: set[str] = set()
    rels = ET.fromstring(zf.read(rels_path))
    for rel in rels:
        if str(rel.attrib.get("Type", "")).endswith("/image"):
            image_parts.add(_zip_resolve_part(drawing_part, rel.attrib["Target"]))
    return image_parts


def _other_drawing_image_parts(zf: zipfile.ZipFile, excluded_rels_path: str) -> set[str]:
    image_parts: set[str] = set()
    for name in zf.namelist():
        if not (name.startswith("xl/drawings/_rels/") and name.endswith(".rels")):
            continue
        if name == excluded_rels_path:
            continue
        drawing_part = posixpath.join(
            "xl/drawings",
            posixpath.basename(name).removesuffix(".rels"),
        )
        if drawing_part in zf.namelist():
            image_parts.update(_image_parts_from_rels(zf, drawing_part, name))
    return image_parts


def _drawing_embed_rel_ids(drawing_xml: bytes) -> set[str]:
    drawing = ET.fromstring(drawing_xml)
    ids: set[str] = set()
    for element in drawing.iter():
        rel_id = element.attrib.get(f"{{{_OFFICE_REL_NS}}}embed")
        if rel_id:
            ids.add(rel_id)
    return ids


def _patch_quotation_drawing_from_source(source_path: str | Path, output_path: str | Path) -> None:
    ET.register_namespace("", _PKG_REL_NS)
    source_path = Path(source_path)
    output_path = Path(output_path)
    tmp_path = output_path.with_name(f"{output_path.stem}.quotation_media_tmp{output_path.suffix}")

    with zipfile.ZipFile(source_path) as src_zip, zipfile.ZipFile(output_path) as out_zip:
        src_sheet_part = _worksheet_part_for_name(src_zip, "Quotation")
        try:
            src_drawing_part, _ = _drawing_part_for_sheet(src_zip, src_sheet_part)
        except ValueError as exc:
            if "no tiene drawing" in str(exc):
                return
            raise
        src_drawing_rels_path = posixpath.join(
            posixpath.dirname(src_drawing_part),
            "_rels",
            f"{posixpath.basename(src_drawing_part)}.rels",
        )

        out_sheet_part = _worksheet_part_for_name(out_zip, "Quotation")
        out_drawing_part, _ = _drawing_part_for_sheet(out_zip, out_sheet_part)
        out_drawing_rels_path = posixpath.join(
            posixpath.dirname(out_drawing_part),
            "_rels",
            f"{posixpath.basename(out_drawing_part)}.rels",
        )

        old_quotation_media = _image_parts_from_rels(out_zip, out_drawing_part, out_drawing_rels_path)
        media_used_elsewhere = _other_drawing_image_parts(out_zip, out_drawing_rels_path)
        old_media_to_skip = old_quotation_media - media_used_elsewhere

        src_drawing_xml = src_zip.read(src_drawing_part)
        used_rel_ids = _drawing_embed_rel_ids(src_drawing_xml)
        src_rels = ET.fromstring(src_zip.read(src_drawing_rels_path))
        copied_media: dict[str, bytes] = {}
        for index, rel in enumerate(list(src_rels), start=1):
            if rel.attrib.get("Id") not in used_rel_ids:
                src_rels.remove(rel)
                continue
            if not str(rel.attrib.get("Type", "")).endswith("/image"):
                continue
            if rel.attrib.get("TargetMode") == "External":
                continue
            src_media_part = _zip_resolve_part(src_drawing_part, rel.attrib["Target"])
            if src_media_part not in src_zip.namelist():
                continue
            data = src_zip.read(src_media_part)
            suffix = Path(src_media_part).suffix or ".png"
            media_part = f"xl/media/quotation_original_{index:03d}{suffix}"
            copied_media[media_part] = data
            rel.attrib["Target"] = _zip_rel_target(out_drawing_part, media_part)

        patched_rels_xml = ET.tostring(src_rels, encoding="utf-8", xml_declaration=True)

        with zipfile.ZipFile(
            tmp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as patched_zip:
            for info in out_zip.infolist():
                if info.filename in old_media_to_skip:
                    continue
                data = out_zip.read(info.filename)
                if info.filename == out_drawing_part:
                    data = src_drawing_xml
                elif info.filename == out_drawing_rels_path:
                    data = patched_rels_xml
                patched_zip.writestr(info, data)
            for media_part, data in copied_media.items():
                patched_zip.writestr(media_part, data)

    tmp_path.replace(output_path)


def _copy_source_sheet(source_path: str | Path, wb_out: Workbook) -> None:
    src = load_workbook(source_path, data_only=False)
    if "Quotation" not in src.sheetnames:
        src.close()
        return
    if "Quotation" in wb_out.sheetnames:
        del wb_out["Quotation"]
    src_ws = src["Quotation"]
    out_ws = wb_out.create_sheet("Quotation")
    for row in src_ws.iter_rows():
        for cell in row:
            dst = out_ws.cell(cell.row, cell.column, cell.value)
            _copy_cell_style(cell, dst)
    for key, dim in src_ws.column_dimensions.items():
        out_ws.column_dimensions[key].width = dim.width
        out_ws.column_dimensions[key].hidden = dim.hidden
        out_ws.column_dimensions[key].outlineLevel = dim.outlineLevel
    for row, dim in src_ws.row_dimensions.items():
        out_ws.row_dimensions[row].height = dim.height
        out_ws.row_dimensions[row].hidden = dim.hidden
        out_ws.row_dimensions[row].outlineLevel = dim.outlineLevel
    for merged in src_ws.merged_cells.ranges:
        out_ws.merge_cells(str(merged))

    max_row, max_col = _quotation_used_bounds(src_ws)
    _apply_quotation_borders(out_ws, max_row, max_col)
    _apply_quotation_item_name_font(out_ws, max_row)

    for src_img in sorted(src_ws._images, key=_quotation_image_sort_key):
        stream = BytesIO(src_img._data())
        img = XlsxImage(stream)
        img.width = src_img.width
        img.height = src_img.height
        img.anchor = deepcopy(src_img.anchor)
        img._mobiliti_stream = stream
        out_ws.add_image(img)

    out_ws.sheet_properties = deepcopy(src_ws.sheet_properties)
    out_ws.sheet_format = copy(src_ws.sheet_format)
    out_ws.page_setup = copy(src_ws.page_setup)
    out_ws.page_margins = copy(src_ws.page_margins)
    out_ws.print_options = copy(src_ws.print_options)
    out_ws.sheet_view.showGridLines = src_ws.sheet_view.showGridLines
    out_ws.sheet_view.zoomScale = src_ws.sheet_view.zoomScale
    out_ws.sheet_view.zoomScaleNormal = src_ws.sheet_view.zoomScaleNormal
    out_ws.sheet_view.view = src_ws.sheet_view.view
    out_ws.sheet_view.topLeftCell = src_ws.sheet_view.topLeftCell
    out_ws.sheet_view.selection = deepcopy(src_ws.sheet_view.selection)
    out_ws.sheet_state = src_ws.sheet_state
    out_ws.protection = copy(src_ws.protection)
    out_ws.auto_filter.ref = src_ws.auto_filter.ref
    out_ws.data_validations = deepcopy(src_ws.data_validations)
    out_ws.conditional_formatting = deepcopy(src_ws.conditional_formatting)
    out_ws.row_breaks = deepcopy(src_ws.row_breaks)
    out_ws.col_breaks = deepcopy(src_ws.col_breaks)
    out_ws.freeze_panes = None
    out_ws.print_area = src_ws.print_area
    out_ws.print_title_rows = src_ws.print_title_rows
    out_ws.print_title_cols = src_ws.print_title_cols
    src.close()


def _first_product_row(items: list[QuoteItem]) -> int:
    return next((item.row for item in items if item.tipo == "producto"), 9)


def _write_header(ws, metadata: dict[str, Any]) -> None:
    for row in range(3, 13):
        _unmerge_row(ws, row)
    ws["B3"] = metadata.get("cotizacion", "")
    ws["B4"] = None
    ws["B7"] = metadata.get("proyecto", "")
    ws["B8"] = metadata.get("cliente", "")
    ws["B9"] = metadata.get("correo", "")
    ws["B10"] = metadata.get("telefono", "")
    ws["B11"] = metadata.get("direccion", "")
    ws["B12"] = metadata.get("razon_social", "")


def _write_mobiliti(
    ws,
    items: list[QuoteItem],
    column_map: dict[str, str],
    lumbro_prices: dict[str, float] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[int, int], dict[int, list[int]]]:
    q_sheet = "Quotation"
    row_map: dict[int, int] = {}
    lumbro_row_map: dict[int, list[int]] = {}
    lumbro_prices = lumbro_prices or {}
    discount_rate = _discount_rate(metadata or {})
    written_rows: set[int] = set()
    category_dictionary = load_category_dictionary(
        [str(item.nombre or "") for item in items if item.tipo == "producto"]
    )
    section_layouts = _ensure_mobiliti_capacity(
        ws,
        _mobiliti_section_capacities(items, category_dictionary),
    )
    m3_col = column_map.get("m3", column_map.get("dimension", "E"))
    qty_col = column_map.get("cantidad", "G")
    price_col = column_map.get("unit_price", column_map.get("list_price", "J"))
    first_row = _first_product_row(items)
    ws["K14"] = _formula(q_sheet, f"{m3_col}{first_row}")

    section_idx = 0
    prod_in_section = 0
    active_section_name = "NOMBRE"

    def next_product_row() -> int | None:
        nonlocal section_idx, prod_in_section
        if prod_in_section >= section_layouts[section_idx].capacity:
            section_idx += 1
            prod_in_section = 0
            if section_idx < len(section_layouts):
                _write_mobiliti_section_title(
                    ws,
                    section_layouts[section_idx],
                    section_idx + 1,
                    active_section_name,
                )
        if section_idx >= len(section_layouts):
            return None
        row_number = section_layouts[section_idx].product_start + prod_in_section
        prod_in_section += 1
        return row_number

    def mark_written_row(row_number: int, region: str = DEFAULT_MOBILITI_REGION) -> None:
        ws.cell(row_number, MOBILITI_REGION_COL).value = region
        ws.cell(row_number, MOBILITI_COVER_DISCOUNT_COL).value = (
            f"=MIN({_excel_decimal(discount_rate)},"
            f"{get_column_letter(MOBILITI_MAX_DISCOUNT_COL)}{row_number})"
        )
        ws.cell(row_number, MOBILITI_DISCOUNT_AMOUNT_COL).value = f"=W{row_number}*Z{row_number}"
        ws.cell(row_number, MOBILITI_FINAL_PRICE_COL).value = f"=W{row_number}*(1-Z{row_number})"
        ws.cell(row_number, MOBILITI_COMMERCIAL_TOTAL_COL).value = f"=AB{row_number}*H{row_number}"
        written_rows.add(row_number)

    def write_lumbro_row(row_number: int, code: str, quantity: int, region: str = DEFAULT_MOBILITI_REGION) -> None:
        price_mxn = lumbro_prices.get(code, 0)
        ws.cell(row_number, 4).value = code
        ws.cell(row_number, 5).value = LUMBRO_CATEGORY
        ws.cell(row_number, 6).value = LUMBRO_PROVIDER
        ws.cell(row_number, 8).value = quantity
        ws.cell(row_number, 10).value = f"={price_mxn}/$K$6"
        ws.cell(row_number, 11).value = 0
        mark_written_row(row_number, region)

    for item in items:
        if item.tipo == "categoria":
            active_section_name = str(item.nombre or "NOMBRE")
            if prod_in_section > 0:
                section_idx += 1
                prod_in_section = 0
            if section_idx >= len(section_layouts):
                break
            section_row = section_layouts[section_idx].section_row
            anchor_col = _merged_anchor_column(ws, section_row, 4)
            ws.cell(section_row, anchor_col).value = f"Sección {section_idx + 1} - {item.nombre}"
            continue

        row = next_product_row()
        if row is None:
            break

        ws.cell(row, 4).value = _formula(q_sheet, f"B{item.row}")
        category = classify_product_name(str(item.nombre or ""), category_dictionary)
        ws.cell(row, 5).value = category
        ws.cell(row, 6).value = "Sunon Inc"
        ws.cell(row, 8).value = _formula(q_sheet, f"{qty_col}{item.row}")
        ws.cell(row, 10).value = _formula(q_sheet, f"{price_col}{item.row}")
        ws.cell(row, 11).value = _formula(q_sheet, f"{m3_col}{item.row}")
        mark_written_row(row)
        row_map[item.row] = row

        lumbro_rows: list[int] = []
        for code, quantity in _lumbro_accessories_for_item(item, category):
            accessory_row = next_product_row()
            if accessory_row is None:
                break
            write_lumbro_row(accessory_row, code, quantity)
            lumbro_rows.append(accessory_row)
        if lumbro_rows:
            lumbro_row_map[item.row] = lumbro_rows
    _clear_unused_mobiliti_product_rows(ws, written_rows)
    return row_map, lumbro_row_map


def _clear_unused_mobiliti_product_rows(ws, written_rows: set[int]) -> None:
    for start_row, capacity in _mobiliti_product_ranges(ws):
        for row in range(start_row, start_row + capacity):
            if row in written_rows:
                continue
            for col in MOBILITI_CLEAR_COLS:
                cell = ws.cell(row, col)
                if isinstance(cell, MergedCell):
                    continue
                cell.value = None


def _apply_mobiliti_provider_validation(ws) -> None:
    if "Proveedores" not in ws.parent.sheetnames:
        return

    provider_ws = ws.parent["Proveedores"]
    last_provider_row = 1
    for row in range(provider_ws.max_row, 1, -1):
        if provider_ws.cell(row, 1).value:
            last_provider_row = row
            break
    if last_provider_row < 2:
        return

    formula = f"={_sheet_name(provider_ws.title)}!$A$2:$A${last_provider_row}"
    validation = DataValidation(type="list", formula1=formula, allow_blank=True)
    ws.add_data_validation(validation)
    for start_row, capacity in _mobiliti_product_ranges(ws):
        validation.add(f"F{start_row}:F{start_row + capacity - 1}")


def _apply_mobiliti_region_validation(ws) -> None:
    validation = DataValidation(type="list", formula1="Taba_Region", allow_blank=True)
    ws.add_data_validation(validation)
    for start_row, capacity in _mobiliti_product_ranges(ws):
        validation.add(f"P{start_row}:P{start_row + capacity - 1}")


def _write_fletes(ws) -> None:
    for row, (origin, destination) in FLETE_ROUTES.items():
        ws.cell(row, 1).value = origin
        ws.cell(row, 3).value = destination
    ws["I8"] = "Escritorios-WorkStation"
    ws["M8"] = "Escritorios-WorkStation"


def _write_mobiliti_settings(ws, metadata: dict[str, Any]) -> None:
    exchange_rate = _num(
        metadata.get("tipo_cambio", metadata.get("exchange_rate")),
        DEFAULT_EXCHANGE_RATE,
    )
    delivery_place = (
        metadata.get("lugar_entrega")
        or metadata.get("delivery_place")
        or DEFAULT_DELIVERY_PLACE
    )
    ws["J6"] = "USD/MXN"
    ws["K6"] = exchange_rate
    ws["K8"] = delivery_place


def _write_estrategia_comercial(ws, total_row: int) -> None:
    ws["D59"] = f"=Cotizacion!H{total_row}"


def _format_product_row_text(ws, row: int) -> None:
    for col in range(1, 11):
        cell = ws.cell(row, col)
        font = copy(cell.font)
        font.sz = 18
        cell.font = font
        alignment = copy(cell.alignment)
        cell.alignment = Alignment(
            horizontal=alignment.horizontal,
            vertical="center",
            text_rotation=alignment.text_rotation,
            wrap_text=False if col == 2 else True,
            shrink_to_fit=alignment.shrink_to_fit,
            indent=alignment.indent,
        )


def _align_description_top_for_category(ws, row: int, category: str) -> None:
    if category not in {"Escritorios-WorkStation", "Mesas de Juntas"}:
        return
    cell = ws.cell(row, 3)
    alignment = copy(cell.alignment)
    cell.alignment = Alignment(
        horizontal=alignment.horizontal,
        vertical="top",
        text_rotation=alignment.text_rotation,
        wrap_text=alignment.wrap_text,
        shrink_to_fit=alignment.shrink_to_fit,
        indent=alignment.indent,
    )


def _anchor_position(img: XlsxImage) -> tuple[int, int]:
    anchor = getattr(img, "anchor", None)
    marker = getattr(anchor, "_from", None)
    return (
        int(getattr(marker, "row", 0) or 0) + 1,
        int(getattr(marker, "col", 0) or 0) + 1,
    )


def _transparent_white_logo_png(data: bytes) -> BytesIO:
    output = BytesIO()
    with PILImage.open(BytesIO(data)) as src:
        rgba = src.convert("RGBA")
        pixels = rgba.load()
        for y in range(rgba.height):
            for x in range(rgba.width):
                r, g, b, a = pixels[x, y]
                if a == 0:
                    continue
                whiteness = min(r, g, b)
                if whiteness >= 246:
                    pixels[x, y] = (r, g, b, 0)
                elif whiteness >= 224 and max(r, g, b) - min(r, g, b) <= 18:
                    alpha = int(a * (246 - whiteness) / 22)
                    pixels[x, y] = (r, g, b, alpha)
        rgba.save(output, format="PNG")
    output.seek(0)
    return output


def _normalize_cotizacion_header_logo(ws) -> None:
    replacements: list[tuple[XlsxImage, XlsxImage]] = []
    for img in list(getattr(ws, "_images", [])):
        row, col = _anchor_position(img)
        if row > 15 or col < 6:
            continue
        stream = _transparent_white_logo_png(img._data())
        replacement = XlsxImage(stream)
        replacement.width = img.width
        replacement.height = img.height
        replacement.anchor = deepcopy(img.anchor)
        replacement._mobiliti_stream = stream
        replacements.append((img, replacement))

    for old_img, new_img in replacements:
        ws._images.remove(old_img)
        ws.add_image(new_img)


def _find_authorization_row(ws, fallback_row: int) -> int:
    targets = (
        "NOMBRE, FIRMA Y FECHA DE AUTORIZACIÓN DEL CLIENTE",
        "NOMBRE, FIRMA Y FECHA DE AUTORIZACION DEL CLIENTE",
    )
    for row in range(1, ws.max_row + 1):
        for col in range(1, 11):
            value = str(ws.cell(row, col).value or "").upper()
            if any(target in value for target in targets):
                return row
    return fallback_row


def _lumbro_accessory_image_path(
    metadata: dict[str, Any],
    category: str | None = None,
    user_count: int | None = None,
) -> Path | None:
    raw_path = metadata.get("lumbro_image_path", metadata.get("imagen_lumbro"))
    if raw_path:
        path = Path(raw_path)
    elif category == "Escritorios-WorkStation" and (user_count or 0) > 1:
        path = LUMBRO_WORKSTATION_IMAGE
    else:
        path = LUMBRO_ACCESSORY_IMAGE
    return path if path.exists() else None


def _bottom_center_image_in_cell(
    img: XlsxImage,
    *,
    row: int,
    column: int,
    cell_width: float,
    cell_height: float,
    bottom_padding: float = 18,
) -> XlsxImage:
    col_offset = max(0, (cell_width - img.width) / 2)
    row_offset = max(0, cell_height - img.height - bottom_padding)
    img.anchor = OneCellAnchor(
        _from=AnchorMarker(
            col=column - 1,
            row=row - 1,
            colOff=pixels_to_EMU(col_offset),
            rowOff=pixels_to_EMU(row_offset),
        ),
        ext=XDRPositiveSize2D(pixels_to_EMU(img.width), pixels_to_EMU(img.height)),
    )
    return img


def _write_cotizacion(
    ws,
    items: list[QuoteItem],
    row_map: dict[int, int],
    lumbro_row_map: dict[int, list[int]],
    image_map: dict[int, str],
    metadata: dict[str, Any],
) -> int:
    description_language = normalize_description_language(
        metadata.get("description_language", metadata.get("idioma_descripcion", "es"))
    )
    category_dictionary = load_category_dictionary(
        [str(item.nombre or "") for item in items if item.tipo == "producto"]
    )
    terms_start = _find_terms_row(ws)
    totals_start = _find_totals_row(ws, terms_start)
    totals_snapshot = _snapshot_rows(ws, totals_start, totals_start + 4)
    terms_end = min(ws.max_row, max(terms_start, terms_start + 136))
    terms_snapshot = _snapshot_rows(ws, terms_start, terms_end)

    for row in range(16, terms_end + 1):
        _clear_row(ws, row)

    current_row = 16
    first_product = None
    last_product = None
    discount_row = None
    discount_rate = _discount_rate(metadata)
    quote_to_cot: dict[int, int] = {}
    category_by_quote_row: dict[int, str] = {}
    user_count_by_quote_row: dict[int, int | None] = {}

    for item in items:
        if item.tipo == "categoria":
            _copy_row_style(ws, 16, current_row)
            _clear_row(ws, current_row)
            ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=10)
            ws.cell(current_row, 1).value = _formula("Quotation", f"A{item.row}")
            current_row += 1
            continue

        if first_product is None:
            first_product = current_row
            discount_row = current_row
        last_product = current_row
        quote_to_cot[item.row] = current_row
        mob_row = row_map.get(item.row)

        _copy_row_style(ws, 17, current_row)
        _clear_row(ws, current_row)
        category = classify_product_name(str(item.nombre or ""), category_dictionary)
        category_by_quote_row[item.row] = category
        user_count_by_quote_row[item.row] = _detect_user_count(item)
        ws.cell(current_row, 1).value = _formula("Quotation", f"B{item.row}")
        code_alignment = copy(ws.cell(current_row, 1).alignment)
        ws.cell(current_row, 1).alignment = Alignment(
            horizontal="center",
            vertical="center",
            text_rotation=code_alignment.text_rotation,
            wrap_text=code_alignment.wrap_text,
            shrink_to_fit=code_alignment.shrink_to_fit,
            indent=code_alignment.indent,
        )
        ws.cell(current_row, 3).value = build_product_description(
            item.nombre,
            item.descripcion,
            category,
            description_language,
        )
        ws.cell(current_row, 4).value = _formula("Quotation", f"E{item.row}")
        if mob_row:
            ws.cell(current_row, 5).value = f"=Mobiliti!H{mob_row}"
            price_rows = [mob_row, *lumbro_row_map.get(item.row, [])]
            ws.cell(current_row, 6).value = "=" + "+".join(f"Mobiliti!W{row}" for row in price_rows)
        else:
            ws.cell(current_row, 5).value = item.cantidad
            ws.cell(current_row, 6).value = item.precio
        ws.cell(current_row, 7).value = discount_rate if current_row == discount_row else f"=G${discount_row}"
        ws.cell(current_row, 8).value = f"=F{current_row}*G{current_row}"
        ws.cell(current_row, 9).value = f"=F{current_row}-H{current_row}"
        ws.cell(current_row, 10).value = f"=I{current_row}*E{current_row}"
        ws.cell(current_row, 7).number_format = PERCENT_FORMAT
        for col in [6, 8, 9, 10]:
            ws.cell(current_row, col).number_format = MONEY_FORMAT
        _format_product_row_text(ws, current_row)
        _align_description_top_for_category(ws, current_row, category)
        current_row += 1

    if first_product is None or last_product is None:
        raise ValueError("No se encontraron productos en Quotation")

    total_labels = ["SUBTOTAL:", "COSTO DE FLETE:", "SUBTOTAL:", "IVA:", "TOTAL:"]
    total_formulas = [
        f"=SUM(J{first_product}:J{last_product})",
        f"=H{current_row}*12%",
        f"=H{current_row}+H{current_row + 1}",
        f"=H{current_row + 2}*16%",
        f"=H{current_row + 2}+H{current_row + 3}",
    ]
    total_row = current_row + len(total_labels) - 1
    _restore_rows(ws, current_row, totals_snapshot)
    for offset, (label, formula) in enumerate(zip(total_labels, total_formulas)):
        row = current_row + offset
        ws.cell(row, 4).value = label
        ws.cell(row, 8).value = formula
        ws.cell(row, 8).number_format = MONEY_FORMAT
        value_alignment = copy(ws.cell(row, 8).alignment)
        ws.cell(row, 8).alignment = Alignment(
            horizontal="right",
            vertical=value_alignment.vertical,
            text_rotation=value_alignment.text_rotation,
            wrap_text=value_alignment.wrap_text,
            shrink_to_fit=value_alignment.shrink_to_fit,
            indent=value_alignment.indent,
        )
    for row in range(current_row + len(total_labels), current_row + 7):
        ws.row_dimensions[row].height = 8
    current_row += 7
    last_terms = _restore_rows(ws, current_row, terms_snapshot)

    for q_row, image_path in image_map.items():
        cot_row = quote_to_cot.get(q_row)
        if not cot_row:
            continue
        cell = ws.cell(cot_row, 2)
        width_px = (ws.column_dimensions[get_column_letter(cell.column)].width or 18) * 7
        height_px = (ws.row_dimensions[cot_row].height or 90) * 1.33
        scale = image_scale_for_category(category_by_quote_row.get(q_row))
        img = fit_image_to_cell(image_path, width_px, height_px, scale=scale)
        center_image_in_cell(img, row=cot_row, column=cell.column, cell_width=width_px, cell_height=height_px)
        ws.add_image(img)

    for q_row in lumbro_row_map:
        lumbro_image_path = _lumbro_accessory_image_path(
            metadata,
            category_by_quote_row.get(q_row),
            user_count_by_quote_row.get(q_row),
        )
        if lumbro_image_path:
            cot_row = quote_to_cot.get(q_row)
            if not cot_row:
                continue
            cell = ws.cell(cot_row, 3)
            width_px = (ws.column_dimensions[get_column_letter(cell.column)].width or 42) * 7
            height_px = (ws.row_dimensions[cot_row].height or 90) * 1.33
            img = fit_image_to_cell(str(lumbro_image_path), width_px, height_px, scale=0.38)
            _bottom_center_image_in_cell(
                img,
                row=cot_row,
                column=cell.column,
                cell_width=width_px,
                cell_height=height_px,
            )
            ws.add_image(img)

    print_end_row = _find_authorization_row(ws, last_terms)
    ws.print_area = f"A1:J{print_end_row}"
    return total_row


def _align_image_map_to_product_rows(
    image_map: dict[int, str],
    items: list[QuoteItem],
    max_distance: int = 3,
) -> dict[int, str]:
    product_rows = [item.row for item in items if item.tipo == "producto"]
    if not product_rows:
        return image_map

    product_row_set = set(product_rows)
    aligned: dict[int, str] = {}
    for image_row, image_path in sorted(image_map.items()):
        if image_row in product_row_set:
            target_row = image_row
        else:
            nearby = [row for row in product_rows if abs(row - image_row) <= max_distance]
            if not nearby:
                aligned[image_row] = image_path
                continue
            # Excel often anchors floating images one row above the product row.
            target_row = min(nearby, key=lambda row: (abs(row - image_row), 0 if row >= image_row else 1))

        if target_row not in aligned or target_row == image_row:
            aligned[target_row] = image_path
    return aligned


def _generate_missing_dezgo_images(
    image_map: dict[int, str],
    items: list[QuoteItem],
    temp_dir: str | Path,
    metadata: dict[str, Any],
    stats: dict[str, Any] | None = None,
) -> dict[int, str]:
    requested_provider = metadata.get("image_provider", metadata.get("proveedor_imagen"))
    provider = normalize_image_provider(requested_provider or os.environ.get("IMAGE_PROVIDER"))
    if provider != "dezgo":
        return image_map

    config = dezgo_config_from_env()
    style_prompt = str(metadata.get("image_prompt", metadata.get("prompt_imagen", ""))).strip() or config.prompt
    generated_dir = Path(temp_dir) / "generated"
    result = dict(image_map)
    for item in items:
        if item.tipo != "producto" or item.row in result:
            continue
        _bump_image_stat(stats, "image_ai_missing_attempted_count")
        prompt = _dezgo_missing_image_prompt(item, style_prompt)
        output = generated_dir / f"missing_{item.row}_{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]}.png"
        if not output.exists():
            try:
                generate_with_dezgo(prompt, output, config)
            except Exception:
                _bump_image_stat(stats, "image_ai_missing_failed_count")
                if normalize_image_provider(requested_provider) == "dezgo":
                    raise
                continue
        _bump_image_stat(stats, "image_ai_generated_count")
        result[item.row] = str(output)
    return result


def _bump_image_stat(stats: dict[str, Any] | None, key: str, amount: int = 1) -> None:
    if stats is not None:
        stats[key] = int(stats.get(key, 0) or 0) + amount


def _estimate_generation_seconds(metadata: dict[str, Any], image_stats: dict[str, Any], item_count: int) -> int:
    provider = normalize_image_provider(metadata.get("image_provider", metadata.get("proveedor_imagen")))
    products = max(1, item_count)
    if provider == "dezgo":
        source_images = int(image_stats.get("image_source_count", 0) or 0)
        missing_attempts = int(image_stats.get("image_ai_missing_attempted_count", 0) or 0)
        return min(1800, max(240, 90 + products * 4 + source_images * 22 + missing_attempts * 35))
    return min(420, max(75, 45 + products * 2))


def _dezgo_missing_image_prompt(item: QuoteItem, style_prompt: str) -> str:
    details = " ".join(
        str(part or "").replace("\n", " ")
        for part in [item.nombre, item.dimension, item.descripcion]
        if part
    )
    details = " ".join(details.split())[:560]
    category = f"Category: {item.categoria}. " if item.categoria else ""
    return (
        f"{style_prompt}. Generate a catalog image for this quoted office furniture product. "
        f"{category}Product: {details}. "
        "Single item only, centered, full product visible, realistic scale, commercial furniture render, "
        "no text, no SKU labels, no watermark, no people."
    )


def _set_calc_mode(wb: Workbook) -> None:
    try:
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
    except Exception:
        pass


def generate_quote(
    source_path: str | Path,
    output_path: str | Path,
    metadata: dict[str, Any] | None = None,
    template_path: str | Path | None = None,
) -> Path:
    metadata = metadata or {}
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    items, column_map = read_items(source_path)
    lumbro_prices = _load_lumbro_prices(template_path)
    wb = _load_template(template_path)
    if "Cotizacion" not in wb.sheetnames:
        wb.create_sheet("Cotizacion", 0)
    if "Mobiliti" not in wb.sheetnames:
        wb.create_sheet("Mobiliti")
    ws_cot = wb["Cotizacion"]
    _set_cotizacion_image_column_px(ws_cot, 1100)
    _set_cotizacion_image_column_px(ws_cot, 550, "A")
    _normalize_cotizacion_header_logo(ws_cot)
    ws_mob = wb["Mobiliti"]

    _copy_source_sheet(source_path, wb)
    _write_header(ws_cot, metadata)
    row_map, lumbro_row_map = _write_mobiliti(ws_mob, items, column_map, lumbro_prices, metadata)
    _apply_mobiliti_provider_validation(ws_mob)
    _apply_mobiliti_region_validation(ws_mob)
    _write_mobiliti_settings(ws_mob, metadata)
    if "Fletes" in wb.sheetnames:
        _write_fletes(wb["Fletes"])

    image_map, temp_dir = extract_images(source_path)
    image_stats: dict[str, Any] = {}
    try:
        image_map = improve_image_map(
            image_map,
            temp_dir,
            background=metadata.get("image_background", metadata.get("fondo_imagen", "transparent")),
            min_size=int(_num(metadata.get("image_min_size", metadata.get("imagen_min_size")), 900)),
            cleanup_strength=metadata.get(
                "image_cleanup_strength",
                metadata.get("limpieza_imagen", "normal"),
            ),
            image_provider=metadata.get("image_provider", metadata.get("proveedor_imagen")),
            image_prompt=metadata.get("image_prompt", metadata.get("prompt_imagen")),
            stats=image_stats,
        )
        image_map = _align_image_map_to_product_rows(image_map, items)
        image_map = _generate_missing_dezgo_images(image_map, items, temp_dir, metadata, stats=image_stats)
        metadata.update(image_stats)
        metadata["product_count"] = len([item for item in items if item.tipo == "producto"])
        metadata["estimated_duration_seconds"] = _estimate_generation_seconds(metadata, image_stats, len(items))
        total_row = _write_cotizacion(ws_cot, items, row_map, lumbro_row_map, image_map, metadata)
        if "Estrategia Comercial " in wb.sheetnames:
            _write_estrategia_comercial(wb["Estrategia Comercial "], total_row)
        _set_calc_mode(wb)
        wb.save(output_path)
        _patch_quotation_drawing_from_source(source_path, output_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        wb.close()
    return output_path
