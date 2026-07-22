"""Filas canónicas y XML seguro para hojas auxiliares de cotización."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from io import BytesIO
from types import MappingProxyType
import re
import struct
from typing import Mapping, Sequence
from urllib.parse import unquote
from xml.sax.saxutils import escape


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_MAX_ROWS = 1_048_576
_TWO_PLACES = Decimal("0.01")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_HASH_DOMAIN = b"mobiliti:quotation-data-row:v1\0"
# NUMERIC(18,6) is the narrowest existing persistent numeric contract.
_DECIMAL_RULES = {
    "original_cost": (6, 12),
    "frozen_rate": (6, 12),
    "converted_cost": (2, 16),
    "quantity": (6, 12),
}
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u2060"


@dataclass(frozen=True)
class SheetAddition:
    """Una hoja nueva, sin relaciones ni partes auxiliares implícitas."""

    name: str
    state: str
    xml: bytes
    parts: Mapping[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name != "Quotation_Data":
            raise ValueError("Nombre de hoja Quotation_Data inválido")
        if self.state != "veryHidden" or not isinstance(self.xml, bytes):
            raise ValueError("SheetAddition inválido")
        copied: dict[str, bytes] = {}
        for name, content in self.parts.items():
            if not isinstance(name, str) or not isinstance(content, bytes):
                raise TypeError("Partes de SheetAddition inválidas")
            copied[name] = bytes(content)
        object.__setattr__(self, "parts", MappingProxyType(copied))


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
    upstream_row_hash: str
    row_hash: str


QUOTATION_DATA_HEADERS = tuple(item.name for item in fields(QuotationDataRow))


def quotation_data_rows(payload: object) -> tuple[QuotationDataRow, ...]:
    """Materializa el payload congelado sin heredar límites de negocio ajenos."""

    checked = _preflight_payload(payload)
    ordered: list[QuotationDataRow] = []
    expected_keys: set[str] = set()

    for section in checked.sections:
        section_id = section["id"]
        section_title = section["title"]
        _validate_safe_text(section_id)
        _validate_safe_text(section_title)
        for item_key in section["item_keys"]:
            if item_key in expected_keys or item_key not in checked.items:
                raise ValueError("Orden de Quotation_Data inconsistente")
            expected_keys.add(item_key)
            line, source_hash, origin, source_row, upstream_row_hash = checked.items[item_key]
            row = QuotationDataRow(
                item_key=item_key,
                section_id=section_id,
                section_title=section_title,
                position=len(ordered) + 1,
                origin=origin,
                source_row=source_row,
                original_currency=line.get("original_currency"),
                original_cost=_decimal(line.get("original_unit_price"), "original_cost"),
                frozen_rate=_decimal(line.get("frozen_exchange_rate"), "frozen_rate"),
                converted_cost=_decimal(line.get("unit_price"), "converted_cost"),
                quantity=_decimal(line.get("quantity"), "quantity"),
                provider=line.get("provider") if origin == "imported" else line.get("supplier"),
                region=_region(line, origin),
                source_hash=source_hash,
                upstream_row_hash=upstream_row_hash,
                row_hash="",
            )
            _validate_row_values(row)
            ordered.append(_with_canonical_hash(row))

    if len(ordered) != checked.item_count or expected_keys != set(checked.items):
        raise ValueError("Orden de Quotation_Data inconsistente")
    return tuple(ordered)


def build_quotation_data_sheet(rows: Sequence[QuotationDataRow]) -> SheetAddition:
    """Construye una hoja muy oculta con texto inline y números Decimal."""

    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TypeError("Las filas de Quotation_Data deben ser una secuencia")
    row_count = len(rows)
    if row_count + 1 > XLSX_MAX_ROWS:
        raise ValueError("Quotation_Data excede el límite físico de filas XLSX")
    return SheetAddition(
        name="Quotation_Data",
        state="veryHidden",
        xml=_stream_worksheet_xml(rows, row_count),
    )


def _preflight_payload(payload: object) -> "_Payload":
    if not isinstance(payload, dict):
        raise ValueError("Payload de Quotation_Data inválido")
    item_count = payload.get("item_count")
    if type(item_count) is not int or item_count < 0:
        raise ValueError("Conteo de Quotation_Data inconsistente")
    if item_count + 1 > XLSX_MAX_ROWS:
        raise ValueError("Quotation_Data excede el límite físico de filas XLSX")
    groups = payload.get("groups")
    sections = payload.get("sections")
    if not _is_sequence(groups) or not _is_sequence(sections) or not sections:
        raise ValueError("Payload de Quotation_Data inválido")
    imported = payload.get("imported_source")
    imported_items: Sequence[object] = ()
    declared_items = 0
    declared_section_keys = 0
    for group in groups:
        if not isinstance(group, dict) or not _is_sequence(group.get("items")):
            raise ValueError("Grupo de Quotation_Data inválido")
        declared_items += len(group["items"])
    if imported is not None:
        if not isinstance(imported, dict) or not _is_sequence(imported.get("items")):
            raise ValueError("Fuente importada de Quotation_Data inválida")
        imported_items = imported["items"]
        declared_items += len(imported_items)
    for section in sections:
        if not isinstance(section, dict) or not _is_sequence(section.get("item_keys")):
            raise ValueError("Sección de Quotation_Data inválida")
        declared_section_keys += len(section["item_keys"])
    if declared_items + 1 > XLSX_MAX_ROWS or declared_section_keys + 1 > XLSX_MAX_ROWS:
        raise ValueError("Quotation_Data excede el límite físico de filas XLSX")
    if declared_items != item_count or declared_section_keys != item_count:
        raise ValueError("Orden de Quotation_Data inconsistente")
    result: dict[str, tuple[dict, str, str, int | None, str]] = {}
    for group in groups:
        source_hash = group.get("catalog_source_hash")
        _validate_hash(source_hash, "source_hash")
        catalog = group.get("catalog")
        _validate_safe_text(catalog)
        for line in group["items"]:
            if not isinstance(line, dict) or line.get("catalog") != catalog:
                raise ValueError("Línea de Quotation_Data inválida")
            _add_item(result, line.get("canonical_key"), (line, source_hash, catalog, None, ""))
    if imported is not None:
        source_hash = imported.get("source_hash")
        import_id = imported.get("import_id")
        _validate_hash(source_hash, "source_hash")
        _validate_import_id(import_id)
        for line in imported_items:
            if not isinstance(line, dict) or line.get("kind") != "imported":
                raise ValueError("Línea importada de Quotation_Data inválida")
            source_row = line.get("source_row")
            if type(source_row) is not int or source_row <= 0:
                raise ValueError("source_row de Quotation_Data inválido")
            if line.get("import_id") != import_id or line.get("source_hash") != source_hash:
                raise ValueError("source_hash importado de Quotation_Data inconsistente")
            _validate_hash(line.get("row_hash"), "upstream_row_hash")
            if line.get("canonical_key") != f"import:{import_id}:{source_row}":
                raise ValueError("canonical_key importado de Quotation_Data inválido")
            _add_item(
                result,
                line.get("canonical_key"),
                (line, source_hash, "imported", source_row, line.get("row_hash")),
            )
    normalized_sections: list[dict] = []
    seen_section_ids: set[str] = set()
    flattened: list[str] = []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"id", "title", "item_keys"}:
            raise ValueError("Sección de Quotation_Data inválida")
        section_id, title, keys = section["id"], section["title"], section["item_keys"]
        _validate_safe_text(section_id)
        _validate_safe_text(title)
        if section_id in seen_section_ids or not _is_sequence(keys) or not keys:
            raise ValueError("Sección de Quotation_Data inválida")
        seen_section_ids.add(section_id)
        for key in keys:
            _validate_safe_text(key)
            flattened.append(key)
        normalized_sections.append(section)
    if len(result) != item_count or len(flattened) != item_count or len(set(flattened)) != item_count or set(flattened) != set(result):
        raise ValueError("Orden de Quotation_Data inconsistente")
    return _Payload(item_count, result, tuple(normalized_sections))


@dataclass(frozen=True)
class _Payload:
    item_count: int
    items: Mapping[str, tuple[dict, str, str, int | None, str]]
    sections: tuple[dict, ...]


def _add_item(result: dict, key: object, value: tuple[dict, str, str, int | None, str]) -> None:
    if not isinstance(key, str) or key in result:
        raise ValueError("Claves de Quotation_Data duplicadas")
    _validate_safe_text(key)
    result[key] = value


def _region(line: dict, origin: str) -> str:
    value = line.get("catalog") if origin != "imported" else "imported"
    if not isinstance(value, str):
        raise ValueError("Región de Quotation_Data inválida")
    return value


def _with_canonical_hash(row: QuotationDataRow) -> QuotationDataRow:
    digest = _row_hash(row)
    return QuotationDataRow(**{**row.__dict__, "row_hash": digest})


def _row_hash(row: QuotationDataRow) -> str:
    digest = hashlib.sha256(_HASH_DOMAIN)
    for name in QUOTATION_DATA_HEADERS:
        if name == "row_hash":
            continue
        value = getattr(row, name)
        if isinstance(value, Decimal):
            kind, encoded = b"d", _decimal_text(value, name).encode("ascii")
        elif type(value) is int:
            kind, encoded = b"i", str(value).encode("ascii")
        elif value is None:
            kind, encoded = b"n", b""
        elif isinstance(value, str):
            kind, encoded = b"s", value.encode("utf-8")
        else:
            raise TypeError("Tipo de hash Quotation_Data inválido")
        encoded_name = name.encode("ascii")
        digest.update(struct.pack(">H", len(encoded_name)))
        digest.update(encoded_name)
        digest.update(kind)
        digest.update(struct.pack(">I", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


def _stream_worksheet_xml(rows: Sequence[QuotationDataRow], row_count: int) -> bytes:
    output = BytesIO()
    _write(output, f'<?xml version="1.0" encoding="utf-8"?><worksheet xmlns="{MAIN}"><dimension ref="A1:P{row_count + 1}"/><sheetData>')
    _write_xml_row(output, 1, QUOTATION_DATA_HEADERS)
    seen_keys: set[str] = set()
    for position in range(1, row_count + 1):
        try:
            row = rows[position - 1]
        except IndexError as error:
            raise ValueError("Secuencia de Quotation_Data inconsistente") from error
        _validate_row_values(row)
        if row.position != position or row.item_key in seen_keys:
            raise ValueError("Orden de Quotation_Data inconsistente")
        seen_keys.add(row.item_key)
        if row.row_hash != _row_hash(row):
            raise ValueError("row_hash de Quotation_Data inválido")
        _write_xml_row(output, position + 1, tuple(getattr(row, name) for name in QUOTATION_DATA_HEADERS))
    _write(output, "</sheetData></worksheet>")
    return output.getvalue()


def _validate_row_values(row: QuotationDataRow) -> None:
    if not isinstance(row, QuotationDataRow):
        raise TypeError("Fila de Quotation_Data inválida")
    if type(row.position) is not int or row.position < 1:
        raise ValueError("Posición de Quotation_Data inválida")
    if row.source_row is not None and (type(row.source_row) is not int or row.source_row <= 0):
        raise ValueError("source_row de Quotation_Data inválido")
    if not isinstance(row.origin, str) or not row.origin:
        raise ValueError("Origen de Quotation_Data inválido")
    if (row.origin == "imported") != (row.source_row is not None):
        raise ValueError("source_row de Quotation_Data inconsistente")
    for text in (row.item_key, row.section_id, row.section_title, row.origin, row.original_currency, row.provider, row.region):
        _validate_safe_text(text)
    _validate_hash(row.source_hash, "source_hash")
    if row.origin == "imported":
        _validate_hash(row.upstream_row_hash, "upstream_row_hash")
    elif row.upstream_row_hash != "":
        raise ValueError("upstream_row_hash de Quotation_Data inválido")
    for value, field_name, positive in (
        (row.original_cost, "original_cost", False),
        (row.frozen_rate, "frozen_rate", True),
        (row.converted_cost, "converted_cost", False),
        (row.quantity, "quantity", True),
    ):
        if type(value) is not Decimal or not value.is_finite() or (positive and value <= 0) or (not positive and value < 0):
            raise ValueError(f"{field_name} de Quotation_Data inválido")
        _decimal_text(value, field_name)
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
    _decimal_text(number, field_name)
    return Decimal(0) if number.is_zero() else number.normalize()


def _decimal_text(value: Decimal, field_name: str) -> str:
    scale, integral_digits = _DECIMAL_RULES[field_name]
    tuple_value = value.as_tuple()
    actual_scale = max(-tuple_value.exponent, 0)
    adjusted = value.adjusted() if not value.is_zero() else 0
    actual_integral = max(adjusted + 1, 1)
    if actual_scale > scale or actual_integral > integral_digits or len(tuple_value.digits) > scale + integral_digits:
        raise ValueError(f"{field_name} de Quotation_Data inválido")
    if value.is_zero():
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _validate_safe_text(value: object, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError("Texto de Quotation_Data inseguro")
    if len(value) > 10_000 or any(not _is_xml_10_character(ord(char)) for char in value):
        raise ValueError("Texto de Quotation_Data inseguro")
    inspected = _inspection_text(value)
    folded = inspected.casefold()
    if (
        any(char in value for char in _INVISIBLE)
        or re.search(r"(?:https?|file|data|blob):", folded) is not None
        or "base64" in folded
        or "x-amz-signature" in folded
        or "signature=" in folded
        or "?sig=" in folded
        or re.search(r"(?:^|[\\/])(?:tmp|temp|temporary)(?:[\\/]|$)", folded) is not None
        or re.search(r"(?:^|[\\/])\.(?:tmp|temp)(?:[\\/]|$)", folded) is not None
        or re.search(r"(?:^[a-z]:[\\/]|^[\\/]{2})", folded) is not None
    ):
        raise ValueError("Texto de Quotation_Data inseguro")


def _inspection_text(value: str) -> str:
    inspected = value
    for _ in range(8):
        if len(inspected) > 10_000 or any(char in inspected for char in _INVISIBLE):
            raise ValueError("Texto de Quotation_Data inseguro")
        decoded = unquote(inspected)
        if decoded == inspected:
            return "".join(inspected.split())
        inspected = decoded
    raise ValueError("Texto de Quotation_Data inseguro")


def _write_xml_row(output: BytesIO, row_number: int, values: Sequence[object]) -> None:
    _write(output, f'<row r="{row_number}">')
    for column, value in enumerate(values, start=1):
        coordinate = f"{_column_name(column)}{row_number}"
        _write_xml_cell(output, coordinate, value)
    _write(output, "</row>")


def _write_xml_cell(output: BytesIO, coordinate: str, value: object) -> None:
    if isinstance(value, str):
        _validate_safe_text(value, allow_empty=True)
        space = ' xml:space="preserve"' if _has_significant_whitespace(value) else ""
        _write(output, f'<c r="{coordinate}" t="inlineStr"><is><t{space}>{escape(value)}</t></is></c>')
    elif type(value) is int:
        _write(output, f'<c r="{coordinate}"><v>{value}</v></c>')
    elif isinstance(value, Decimal):
        _write(output, f'<c r="{coordinate}"><v>{_decimal_text(value, _decimal_field_for_coordinate(coordinate))}</v></c>')
    elif value is None:
        _write(output, f'<c r="{coordinate}"/>')
    else:
        raise TypeError("Tipo de celda Quotation_Data inválido")


def _decimal_field_for_coordinate(coordinate: str) -> str:
    return {"H": "original_cost", "I": "frozen_rate", "J": "converted_cost", "K": "quantity"}[re.match(r"[A-Z]+", coordinate).group()]


def _write(output: BytesIO, text: str) -> None:
    output.write(text.encode("utf-8"))


def _validate_hash(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} de Quotation_Data inválido")


def _validate_import_id(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value) is None:
        raise ValueError("import_id de Quotation_Data inválido")


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_xml_10_character(codepoint: int) -> bool:
    return codepoint in {0x9, 0xA, 0xD} or 0x20 <= codepoint <= 0xD7FF or 0xE000 <= codepoint <= 0xFFFD or 0x10000 <= codepoint <= 0x10FFFF


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _has_significant_whitespace(value: str) -> bool:
    return value != value.strip() or any(character in value for character in "\t\r\n") or "  " in value
