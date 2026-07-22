"""Traducción segura de fórmulas y ``calcChain`` oficiales sin guardar libros."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Mapping, Sequence
import xml.etree.ElementTree as ET

from openpyxl.formula.tokenizer import Tokenizer
from openpyxl.formula.translate import Translator


_COLUMN = r"\$?[A-Z]{1,3}"
_ROW = r"\$?[1-9][0-9]*"
_CELL = rf"{_COLUMN}{_ROW}"
_SHEET = r"(?:'(?:[^']|'')+'|[^'!]+)!"
_RANGE_BODY = rf"(?:{_CELL}(?::{_CELL})?|{_COLUMN}:{_COLUMN}|{_ROW}:{_ROW})"
_RANGE_REFERENCE = re.compile(
    rf"^(?:{_SHEET})?{_RANGE_BODY}$", re.IGNORECASE
)
_CELL_REFERENCE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*$", re.IGNORECASE)
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_CALC_CHAIN_TAG = f"{{{_MAIN_NS}}}calcChain"
_CALC_CHAIN_CELL_TAG = f"{{{_MAIN_NS}}}c"


class FormulaTranslationError(ValueError):
    """Una fórmula oficial contiene una referencia que no se puede mover segura."""


def _context(sheet: str | None, origin: str, formula: str) -> str:
    address = f"{sheet}!{origin}" if sheet else origin
    return f"{address}; fórmula: {formula}"


def _validate_range_token(value: str, *, context: str) -> None:
    if not _RANGE_REFERENCE.fullmatch(value):
        raise FormulaTranslationError(
            f"No se puede traducir el rango {value!r} de forma segura ({context})"
        )


def _translate_range_token(value: str, *, origin: str, target: str, context: str) -> str:
    _validate_range_token(value, context=context)
    try:
        translated = Translator(f"={value}", origin=origin).translate_formula(target)
    except Exception as error:  # Translator expone varios ValueError internos.
        raise FormulaTranslationError(
            f"No se pudo mover el rango {value!r} ({context})"
        ) from error

    result = translated[1:]
    _validate_range_token(result, context=context)
    return result


def translate_formula(
    formula: str,
    *,
    origin: str,
    target: str,
    range_overrides: Mapping[str, str] | None = None,
    sheet: str | None = None,
) -> str:
    """Traslada solo tokens ``OPERAND/RANGE`` y deja intacto el resto.

    Los overrides se comparan contra la referencia ya trasladada; esto permite
    ampliar rangos modelo sin modificar literales de texto u otros tokens.
    """

    context = _context(sheet, origin, formula)
    if not isinstance(formula, str) or not formula.startswith("="):
        raise FormulaTranslationError(f"Se esperaba una fórmula Excel ({context})")

    overrides = dict(range_overrides or {})
    for source, replacement in overrides.items():
        _validate_range_token(source, context=context)
        _validate_range_token(replacement, context=context)

    try:
        tokens = Tokenizer(formula).items
    except Exception as error:
        raise FormulaTranslationError(f"No se pudo tokenizar la fórmula ({context})") from error

    result: list[str] = ["="]
    for token in tokens:
        value = token.value
        if token.type == "OPERAND" and token.subtype == "RANGE":
            translated = _translate_range_token(
                value, origin=origin, target=target, context=context
            )
            value = overrides.get(translated, translated)
        result.append(value)
    return "".join(result)


def _validate_coordinate(value: str) -> None:
    if not _CELL_REFERENCE.fullmatch(value):
        raise FormulaTranslationError(
            f"La coordenada de calcChain no es una celda XLSX válida: {value!r}"
        )


def translate_calc_chain(
    calc_chain_xml: bytes | str,
    *,
    sheet_id: int,
    coordinate_map: Mapping[str, Sequence[str]],
) -> bytes | str:
    """Mapea o clona entradas de un único sheet dentro de ``xl/calcChain.xml``."""

    input_is_text = isinstance(calc_chain_xml, str)
    payload = calc_chain_xml.encode("utf-8") if input_is_text else calc_chain_xml
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, TypeError) as error:
        raise FormulaTranslationError("calcChain.xml no es XML válido") from error

    if root.tag != _CALC_CHAIN_TAG:
        raise FormulaTranslationError("calcChain.xml no tiene el QName calcChain esperado")

    for source, destinations in coordinate_map.items():
        _validate_coordinate(source)
        if not destinations:
            raise FormulaTranslationError(
                f"La coordenada {source!r} no tiene destinos en calcChain"
            )
        for destination in destinations:
            _validate_coordinate(destination)

    ET.register_namespace("", _MAIN_NS)
    target_sheet_id = str(sheet_id)
    cells = list(root)
    effective_sheet_id: str | None = None
    cell_sheets: list[tuple[ET.Element, str | None]] = []
    for cell in cells:
        if cell.tag != _CALC_CHAIN_CELL_TAG:
            raise FormulaTranslationError("calcChain.xml contiene un hijo directo inválido")
        coordinate = cell.attrib.get("r")
        if coordinate is None:
            raise FormulaTranslationError("calcChain.xml contiene una entrada sin coordenada")
        _validate_coordinate(coordinate)
        if "i" in cell.attrib:
            effective_sheet_id = cell.attrib["i"]
        cell_sheets.append((cell, effective_sheet_id))

    output: list[ET.Element] = []
    existing_target_coordinates = {
        cell.attrib["r"]
        for cell, effective_id in cell_sheets
        if effective_id == target_sheet_id
    }

    for cell, effective_id in cell_sheets:
        source = cell.attrib["r"]
        if effective_id != target_sheet_id or source not in coordinate_map:
            output.append(cell)
            continue

        for destination in coordinate_map[source]:
            if destination != source and destination in existing_target_coordinates:
                continue
            clone = deepcopy(cell)
            clone.set("r", destination)
            output.append(clone)
            existing_target_coordinates.add(destination)

    root[:] = output
    translated = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return translated.decode("utf-8") if input_is_text else translated
