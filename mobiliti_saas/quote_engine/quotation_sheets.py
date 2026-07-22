"""Filas canónicas y XML seguro para hojas auxiliares de cotización."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
from typing import Mapping, Sequence
import xml.etree.ElementTree as ET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
XLSX_MAX_ROWS = 1_048_576
_TWO_PLACES = Decimal("0.01")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UNSAFE_TEXT_PREFIXES = ("=", "+", "-", "@")

ET.register_namespace("", MAIN)


@dataclass(frozen=True)
class SheetAddition:
    """Una hoja nueva, sin relaciones ni partes auxiliares implícitas."""

    name: str
    state: str
    xml: bytes
    parts: Mapping[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class QuotationDataRow:
    """Registro de auditoría independiente de las hojas de presentación."""

    item_key: str
    section_id: str
    section_title: str
    position: int
    origin: str
    source_row: int | None
    original_currency: str
    original_cost: Decimal
    frozen_rate: Decimal
    converted_cost: Decimal
    quantity: Decimal
    provider: str
    region: str
    source_hash: str
    row_hash: str


QUOTATION_DATA_HEADERS = tuple(item.name for item in fields(QuotationDataRow))


def quotation_data_rows(payload: object) -> tuple[QuotationDataRow, ...]:
    """Materializa las líneas validadas en el orden declarado por secciones."""

    from .mixed_catalog import validate_mixed_catalog_payload

    checked = validate_mixed_catalog_payload(payload)
    items = _items_by_key(checked)
    ordered: list[QuotationDataRow] = []
    expected_keys: list[str] = []

    for section in checked["sections"]:
        section_id = section["id"]
        section_title = section["title"]
        _validate_safe_text(section_id)
        _validate_safe_text(section_title)
        for item_key in section["item_keys"]:
            if item_key in expected_keys or item_key not in items:
                raise ValueError("Orden de Quotation_Data inconsistente")
            expected_keys.append(item_key)
            line, source_hash, origin, source_row, upstream_row_hash = items[item_key]
            row = QuotationDataRow(
                item_key=item_key,
                section_id=section_id,
                section_title=section_title,
                position=len(ordered) + 1,
                origin=origin,
                source_row=source_row,
                original_currency=line["original_currency"],
                original_cost=_decimal(line["original_unit_price"], "original_cost"),
                frozen_rate=_decimal(line["frozen_exchange_rate"], "frozen_rate"),
                converted_cost=_decimal(line["unit_price"], "converted_cost"),
                quantity=_decimal(line["quantity"], "quantity"),
                provider=line["provider"] if origin == "imported" else line["supplier"],
                region=_region(line, origin),
                source_hash=source_hash,
                row_hash="",
            )
            _validate_row_values(row, upstream_row_hash=upstream_row_hash)
            ordered.append(_with_canonical_hash(row))

    if (
        len(ordered) != checked["item_count"]
        or expected_keys != [key for section in checked["sections"] for key in section["item_keys"]]
        or set(expected_keys) != set(items)
    ):
        raise ValueError("Orden de Quotation_Data inconsistente")
    return tuple(ordered)


def build_quotation_data_sheet(rows: Sequence[QuotationDataRow]) -> SheetAddition:
    """Construye una hoja muy oculta con texto inline y números Decimal."""

    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TypeError("Las filas de Quotation_Data deben ser una secuencia")
    materialized = tuple(rows)
    if len(materialized) + 1 > XLSX_MAX_ROWS:
        raise ValueError("Quotation_Data excede el límite físico de filas XLSX")
    _validate_rows(materialized)
    table = [
        QUOTATION_DATA_HEADERS,
        *(tuple(getattr(row, name) for name in QUOTATION_DATA_HEADERS) for row in materialized),
    ]
    return SheetAddition(
        name="Quotation_Data",
        state="veryHidden",
        xml=inline_worksheet_xml(table),
    )


def inline_worksheet_xml(rows: Sequence[Sequence[object]]) -> bytes:
    """Serializa una tabla limitada a SpreadsheetML seguro, sin fórmulas."""

    if not rows or len(rows) > XLSX_MAX_ROWS:
        raise ValueError("Filas de Quotation_Data inválidas")
    width = len(rows[0])
    if not 1 <= width <= 16_384 or any(len(row) != width for row in rows):
        raise ValueError("Tabla de Quotation_Data inconsistente")

    root = ET.Element(f"{{{MAIN}}}worksheet")
    ET.SubElement(root, f"{{{MAIN}}}dimension", {"ref": f"A1:{_column_name(width)}{len(rows)}"})
    sheet_data = ET.SubElement(root, f"{{{MAIN}}}sheetData")
    for row_index, values in enumerate(rows, start=1):
        row = ET.SubElement(sheet_data, f"{{{MAIN}}}row", {"r": str(row_index)})
        for column_index, value in enumerate(values, start=1):
            _append_cell(row, f"{_column_name(column_index)}{row_index}", value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)


def _items_by_key(payload: dict) -> dict[str, tuple[dict, str, str, int | None, str | None]]:
    result: dict[str, tuple[dict, str, str, int | None, str | None]] = {}
    for group in payload["groups"]:
        source_hash = group["catalog_source_hash"]
        for line in group["items"]:
            _add_item(result, line["canonical_key"], (line, source_hash, line["catalog"], None, None))
    imported = payload["imported_source"]
    if imported is not None:
        for line in imported["items"]:
            _add_item(
                result,
                line["canonical_key"],
                (line, line["source_hash"], "imported", line["source_row"], line["row_hash"]),
            )
    return result


def _add_item(result: dict, key: object, value: tuple[dict, str, str, int | None, str | None]) -> None:
    if not isinstance(key, str) or key in result:
        raise ValueError("Claves de Quotation_Data duplicadas")
    result[key] = value


def _region(line: dict, origin: str) -> str:
    value = line.get("catalog") if origin != "imported" else "imported"
    if not isinstance(value, str):
        raise ValueError("Región de Quotation_Data inválida")
    return value


def _with_canonical_hash(row: QuotationDataRow) -> QuotationDataRow:
    payload = {
        name: _hash_value(getattr(row, name))
        for name in QUOTATION_DATA_HEADERS
        if name != "row_hash"
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return QuotationDataRow(**{**row.__dict__, "row_hash": digest})


def _hash_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _validate_rows(rows: tuple[QuotationDataRow, ...]) -> None:
    seen_keys: set[str] = set()
    for position, row in enumerate(rows, start=1):
        _validate_row_values(row, upstream_row_hash=None)
        if row.position != position or row.item_key in seen_keys:
            raise ValueError("Orden de Quotation_Data inconsistente")
        seen_keys.add(row.item_key)
        if row.row_hash != _with_canonical_hash(row).row_hash:
            raise ValueError("row_hash de Quotation_Data inválido")


def _validate_row_values(row: QuotationDataRow, *, upstream_row_hash: str | None) -> None:
    if not isinstance(row, QuotationDataRow):
        raise TypeError("Fila de Quotation_Data inválida")
    if type(row.position) is not int or row.position < 1:
        raise ValueError("Posición de Quotation_Data inválida")
    if row.source_row is not None and (type(row.source_row) is not int or row.source_row <= 7):
        raise ValueError("source_row de Quotation_Data inválido")
    if row.origin not in {"imported", "tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro"}:
        raise ValueError("Origen de Quotation_Data inválido")
    if (row.origin == "imported") != (row.source_row is not None):
        raise ValueError("source_row de Quotation_Data inconsistente")
    for text in (row.item_key, row.section_id, row.section_title, row.origin, row.original_currency, row.provider, row.region):
        _validate_safe_text(text)
    for digest, field_name in ((row.source_hash, "source_hash"), (upstream_row_hash, "row_hash")):
        if digest is not None and (not isinstance(digest, str) or _SHA256.fullmatch(digest) is None):
            raise ValueError(f"{field_name} de Quotation_Data inválido")
    for value, field_name, positive in (
        (row.original_cost, "original_cost", False),
        (row.frozen_rate, "frozen_rate", True),
        (row.converted_cost, "converted_cost", False),
        (row.quantity, "quantity", True),
    ):
        if type(value) is not Decimal or not value.is_finite() or (positive and value <= 0) or (not positive and value < 0):
            raise ValueError(f"{field_name} de Quotation_Data inválido")
    if row.converted_cost != (row.original_cost * row.frozen_rate).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP):
        raise ValueError("Costo convertido de Quotation_Data inconsistente")


def _decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} de Quotation_Data inválido")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} de Quotation_Data inválido") from error
    if not number.is_finite():
        raise ValueError(f"{field_name} de Quotation_Data inválido")
    return number


def _validate_safe_text(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("Texto de Quotation_Data inseguro")
    folded = value.casefold()
    if (
        value.lstrip().startswith(_UNSAFE_TEXT_PREFIXES)
        or "http://" in folded
        or "https://" in folded
        or "blob:" in folded
        or "data:" in folded
        or "base64" in folded
        or "x-amz-signature" in folded
        or "signature=" in folded
        or "?sig=" in folded
        or "\\" in value
        or re.search(r"(?:^|/)\.(?:temp|tmp)(?:/|$)", folded) is not None
        or re.search(r"(?:^|/)(?:tmp|temp|temporary)(?:/|$)", folded) is not None
    ):
        raise ValueError("Texto de Quotation_Data inseguro")
    if any(ord(char) < 0x20 and char not in "\t\n\r" for char in value):
        raise ValueError("Texto de Quotation_Data inseguro")


def _append_cell(row: ET.Element, coordinate: str, value: object) -> None:
    cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": coordinate})
    if isinstance(value, str):
        _validate_safe_text(value)
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, f"{{{MAIN}}}is")
        text = ET.SubElement(inline, f"{{{MAIN}}}t")
        if _has_significant_whitespace(value):
            text.set(f"{{{XML}}}space", "preserve")
        text.text = value
    elif type(value) is int:
        ET.SubElement(cell, f"{{{MAIN}}}v").text = str(value)
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Número de Quotation_Data inválido")
        ET.SubElement(cell, f"{{{MAIN}}}v").text = format(value, "f")
    elif value is None:
        return
    else:
        raise TypeError("Tipo de celda Quotation_Data inválido")


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _has_significant_whitespace(value: str) -> bool:
    return value != value.strip() or any(character in value for character in "\t\r\n") or "  " in value
