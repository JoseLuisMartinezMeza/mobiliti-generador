"""Mutación OOXML de la hoja oficial ``Mobiliti`` sin guardar el libro.

El editor conserva el árbol original y reemplaza únicamente las filas de la
tabla dinámica. Las fórmulas, estilos, propiedades de fila, combinaciones y
reglas se clonan del XML oficial.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Literal, Sequence
import xml.etree.ElementTree as ET

from openpyxl.formula.tokenizer import Tokenizer
from openpyxl.utils.cell import column_index_from_string, get_column_letter

from .mobiliti_layout import MobilitiRowMap, SectionNeed, plan_mobiliti_layout
from .ooxml_formula import translate_formula


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
XM = "http://schemas.microsoft.com/office/excel/2006/main"
TABLE_LAST_COLUMN = 34  # AH
CANONICAL_TOTAL_ROW = 573
CANONICAL_AUXILIARY_START = 574
CANONICAL_AUXILIARY_END = 610
INPUT_COLUMNS = frozenset((4, 5, 6, 8, 10, 11, 16))
CANONICAL_SUBTOTAL_ROWS = tuple(range(47, 573, 35))
_CELL_REFERENCE = re.compile(r"(?P<column>\$?[A-Z]{1,3})(?P<row_abs>\$?)(?P<row>[1-9][0-9]*)$")
_RANGE_REFERENCE = re.compile(
    r"(?:(?P<sheet>'(?:[^']|'')+'|[^'!]+)!)?"
    r"(?P<first>\$?[A-Z]{1,3}\$?[1-9][0-9]*)"
    r"(?::(?P<last>\$?[A-Z]{1,3}\$?[1-9][0-9]*))?$"
)

for prefix, namespace in (
    ("", MAIN),
    ("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
    ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
    ("x14ac", "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"),
    ("xr", "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"),
    ("xr2", "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"),
    ("xr3", "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"),
    ("x14", X14),
    ("xm", XM),
):
    ET.register_namespace(prefix, namespace)


@dataclass(frozen=True)
class MobilitiCellWrite:
    coordinate: str
    kind: Literal["number", "text", "boolean"]
    value: Decimal | str | bool


@dataclass(frozen=True)
class MobilitiSheetMutation:
    xml: bytes
    row_map: MobilitiRowMap


@dataclass(frozen=True)
class OfficialMobilitiBlock:
    first_section_header: ET.Element
    first_product_row: ET.Element
    first_subtotal_row: ET.Element
    section_header: ET.Element
    product_row: ET.Element
    subtotal_row: ET.Element
    total_row: ET.Element
    auxiliary_rows: tuple[ET.Element, ...]
    merges: tuple[ET.Element, ...]


class WorksheetEditor:
    """Editor estrecho sobre un ``worksheet`` SpreadsheetML existente."""

    def __init__(self, root: ET.Element):
        self.root = root
        self.row_map: MobilitiRowMap | None = None
        self.sheet_data = root.find(f"{{{MAIN}}}sheetData")
        if self.sheet_data is None:
            raise ValueError("Mobiliti no contiene sheetData")

    @classmethod
    def from_xml(cls, payload: bytes) -> "WorksheetEditor":
        try:
            root = ET.fromstring(payload)
        except (ET.ParseError, TypeError) as error:
            raise ValueError("El XML de Mobiliti no es válido") from error
        if root.tag != f"{{{MAIN}}}worksheet":
            raise ValueError("La parte indicada no es un worksheet SpreadsheetML")
        return cls(root)

    def to_xml(self) -> bytes:
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)

    def row(self, number: int) -> ET.Element | None:
        return self.sheet_data.find(f"{{{MAIN}}}row[@r='{number}']")

    def require_row(self, number: int) -> ET.Element:
        row = self.row(number)
        if row is None:
            raise ValueError(f"La plantilla oficial no contiene la fila Mobiliti {number}")
        return row

    def replace_table_row(
        self,
        target_row: int,
        source: ET.Element,
        source_row: int,
        row_map: MobilitiRowMap,
        *,
        last_column: int = TABLE_LAST_COLUMN,
        product_range: tuple[int, int] | None = None,
        clear_input_formulas: bool = False,
    ) -> ET.Element:
        existing = self.row(target_row)
        sidecar = []
        if existing is not None:
            sidecar = [
                deepcopy(cell)
                for cell in existing.findall(f"{{{MAIN}}}c")
                if _cell_column(cell) > last_column
            ]
        clone = deepcopy(source)
        clone.set("r", str(target_row))
        for cell in list(clone.findall(f"{{{MAIN}}}c")):
            if _cell_column(cell) > last_column:
                clone.remove(cell)
                continue
            _move_cell(
                cell,
                source_row=source_row,
                target_row=target_row,
                row_map=row_map,
                product_range=product_range,
                clear_input_formulas=clear_input_formulas,
            )
        for cell in sidecar:
            clone.append(cell)
        if existing is not None:
            self.sheet_data.remove(existing)
        self.sheet_data.append(clone)
        return clone

    def replace_whole_row(
        self,
        target_row: int,
        source: ET.Element,
        source_row: int,
        row_map: MobilitiRowMap,
    ) -> ET.Element:
        existing = self.row(target_row)
        clone = deepcopy(source)
        clone.set("r", str(target_row))
        for cell in clone.findall(f"{{{MAIN}}}c"):
            _move_cell(cell, source_row=source_row, target_row=target_row, row_map=row_map)
        if existing is not None:
            self.sheet_data.remove(existing)
        self.sheet_data.append(clone)
        return clone

    def sort_rows(self) -> None:
        children = list(self.sheet_data)
        children.sort(key=lambda row: int(row.attrib["r"]))
        self.sheet_data[:] = children


def capture_official_mobiliti_block(
    editor: WorksheetEditor,
    first_section_row: int = 13,
    second_section_row: int = 48,
    total_row: int = CANONICAL_TOTAL_ROW,
) -> OfficialMobilitiBlock:
    """Captura las siete filas canónicas y el bloque auxiliar oficial."""

    merge_cells = editor.root.find(f"{{{MAIN}}}mergeCells")
    merges = tuple(deepcopy(node) for node in (() if merge_cells is None else merge_cells))
    return OfficialMobilitiBlock(
        first_section_header=deepcopy(editor.require_row(first_section_row)),
        first_product_row=deepcopy(editor.require_row(first_section_row + 1)),
        first_subtotal_row=deepcopy(editor.require_row(second_section_row - 1)),
        section_header=deepcopy(editor.require_row(second_section_row)),
        product_row=deepcopy(editor.require_row(second_section_row + 1)),
        subtotal_row=deepcopy(editor.require_row(second_section_row + 34)),
        total_row=deepcopy(editor.require_row(total_row)),
        auxiliary_rows=tuple(
            deepcopy(editor.require_row(row))
            for row in range(total_row + 1, CANONICAL_AUXILIARY_END + 1)
        ),
        merges=merges,
    )


def apply_mobiliti_layout(editor: WorksheetEditor, row_map: MobilitiRowMap) -> None:
    """Limpia solo la tabla oficial que será reconstruida desde sus clones."""

    editor.row_map = row_map
    target_rows = {
        row
        for section in row_map.sections
        for row in range(section.section_row, section.subtotal_row + 1)
    } | {row_map.total_row}
    for row in list(editor.sheet_data):
        number = int(row.attrib["r"])
        if 13 <= number <= CANONICAL_TOTAL_ROW and number not in target_rows:
            for cell in list(row.findall(f"{{{MAIN}}}c")):
                if _cell_column(cell) <= TABLE_LAST_COLUMN:
                    row.remove(cell)
        if CANONICAL_AUXILIARY_START <= number <= CANONICAL_AUXILIARY_END:
            editor.sheet_data.remove(row)


def _translate_static_structural_formulas(
    editor: WorksheetEditor, row_map: MobilitiRowMap
) -> None:
    """Actualiza referencias al total en cabecera y panel lateral preservados."""

    for row in editor.sheet_data:
        row_number = int(row.attrib["r"])
        if row_number > CANONICAL_TOTAL_ROW:
            continue
        for cell in row.findall(f"{{{MAIN}}}c"):
            if row_number >= 13 and _cell_column(cell) <= TABLE_LAST_COLUMN:
                continue
            formula = cell.find(f"{{{MAIN}}}f")
            if formula is None or not formula.text:
                continue
            coordinate = cell.attrib["r"]
            formula.text = _translate_official_formula(
                "=" + formula.text,
                origin=coordinate,
                target=coordinate,
                row_map=row_map,
            )[1:]


def relocate_official_auxiliary_rows(
    editor: WorksheetEditor,
    row_map: MobilitiRowMap,
    canonical: OfficialMobilitiBlock,
) -> None:
    """Mueve las filas oficiales 574:610 inmediatamente después del total."""

    for offset, source in enumerate(canonical.auxiliary_rows, start=1):
        editor.replace_whole_row(
            row_map.total_row + offset,
            source,
            CANONICAL_TOTAL_ROW + offset,
            row_map,
        )
    _update_dimension(editor, row_map.total_row + len(canonical.auxiliary_rows))


def clone_section_header(
    editor: WorksheetEditor,
    canonical_row: ET.Element,
    target_row: int,
    title: str,
    row_map: MobilitiRowMap | None = None,
    *,
    source_row: int = 48,
) -> None:
    effective_row_map = row_map or editor.row_map
    if effective_row_map is None:
        raise ValueError("apply_mobiliti_layout debe ejecutarse antes de clonar secciones")
    clone = editor.replace_table_row(
        target_row, canonical_row, source_row, effective_row_map
    )
    title_column = 4 if source_row == 13 else 1
    _set_cell_value(clone, f"{get_column_letter(title_column)}{target_row}", "text", title)


def clone_formula_row(
    editor: WorksheetEditor,
    canonical_row: ET.Element,
    target_row: int,
    row_map: MobilitiRowMap,
    *,
    source_row: int = 49,
) -> None:
    editor.replace_table_row(
        target_row,
        canonical_row,
        source_row,
        row_map,
        clear_input_formulas=True,
    )


def clone_subtotal_row(
    editor: WorksheetEditor,
    canonical_row: ET.Element,
    section,
    row_map: MobilitiRowMap,
    *,
    source_row: int = 82,
) -> None:
    last_used = section.product_start + max(section.item_count, 1) - 1
    clone = editor.replace_table_row(
        section.subtotal_row,
        canonical_row,
        source_row,
        row_map,
        product_range=(section.product_start, last_used),
    )
    section_number = row_map.sections.index(section) + 1
    _set_cell_value(
        clone,
        f"A{section.subtotal_row}",
        "text",
        f"Subtotales Sección {section_number}",
    )


def clone_total_row(
    editor: WorksheetEditor,
    canonical_row: ET.Element,
    target_row: int,
    row_map: MobilitiRowMap,
) -> None:
    clone = editor.replace_table_row(
        target_row,
        canonical_row,
        CANONICAL_TOTAL_ROW,
        row_map,
        last_column=36,
    )
    for cell in clone.findall(f"{{{MAIN}}}c"):
        formula = cell.find(f"{{{MAIN}}}f")
        if formula is None or not formula.text:
            continue
        # replace_table_row ya tradujo la fórmula; se reconstruye de nuevo desde
        # el original para incluir cualquier sección adicional a las 16 oficiales.
        source_cell = _find_cell(canonical_row, _cell_column(cell))
        source_formula = None if source_cell is None else source_cell.find(f"{{{MAIN}}}f")
        if source_formula is not None and source_formula.text:
            formula.text = _translate_total_formula(
                "=" + source_formula.text,
                origin=source_cell.attrib["r"],
                target=cell.attrib["r"],
                subtotal_rows=row_map.subtotal_rows,
            )[1:]


def clear_mobiliti_input_cells(editor: WorksheetEditor, row_map: MobilitiRowMap) -> None:
    """Elimina valores contaminados exclusivamente de las columnas de entrada."""

    for section in row_map.sections:
        for row_number in range(section.product_start, section.product_start + section.capacity):
            row = editor.require_row(row_number)
            for column in INPUT_COLUMNS:
                cell = _find_cell(row, column)
                if cell is not None:
                    _clear_cell(cell)


def apply_mobiliti_cell_writes(
    editor: WorksheetEditor,
    cell_writes: Sequence[MobilitiCellWrite],
    row_map: MobilitiRowMap,
) -> None:
    product_rows = {
        row
        for section in row_map.sections
        for row in range(section.product_start, section.product_start + section.capacity)
    }
    for write in cell_writes:
        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", write.coordinate.upper())
        if match is None:
            raise ValueError(f"Coordenada Mobiliti inválida: {write.coordinate!r}")
        column = column_index_from_string(match.group(1))
        row_number = int(match.group(2))
        if row_number not in product_rows or column not in INPUT_COLUMNS:
            raise ValueError(f"Escritura fuera de inputs Mobiliti: {write.coordinate}")
        row = editor.require_row(row_number)
        _set_cell_value(row, write.coordinate.upper(), write.kind, write.value)


def translate_mobiliti_validations(editor: WorksheetEditor, row_map: MobilitiRowMap) -> None:
    """Traduce validaciones clásicas y x14, conservando sus extensiones."""

    validations = editor.root.find(f"{{{MAIN}}}dataValidations")
    if validations is not None:
        for validation in validations:
            if "sqref" in validation.attrib:
                validation.set("sqref", _translate_sqref(validation.attrib["sqref"], row_map))
    for sqref in editor.root.findall(f".//{{{XM}}}sqref"):
        if sqref.text:
            sqref.text = _translate_sqref(sqref.text, row_map)


def translate_mobiliti_conditional_formatting(
    editor: WorksheetEditor, row_map: MobilitiRowMap
) -> None:
    """Mueve y amplía reglas oficiales de formato condicional por sección."""

    original = [
        node
        for node in list(editor.root)
        if node.tag == f"{{{MAIN}}}conditionalFormatting"
    ]
    dynamic: list[tuple[int, ET.Element]] = []
    fixed: list[ET.Element] = []
    for node in original:
        section_index = _sqref_section_index(node.attrib.get("sqref", ""))
        if section_index is None:
            fixed.append(node)
        else:
            dynamic.append((section_index, node))
        editor.root.remove(node)

    translated: list[ET.Element] = []
    for section_index, node in dynamic:
        if section_index < len(row_map.sections):
            translated.append(_translate_cf_node(node, section_index, row_map))
    second_templates = [node for index, node in dynamic if index == 1]
    for section_index in range(len(CANONICAL_SUBTOTAL_ROWS), len(row_map.sections)):
        translated.extend(
            _translate_cf_node(node, section_index, row_map, source_section_index=1)
            for node in second_templates
        )

    insertion_index = next(
        (
            index
            for index, node in enumerate(editor.root)
            if node.tag in {f"{{{MAIN}}}dataValidations", f"{{{MAIN}}}pageMargins"}
        ),
        len(editor.root),
    )
    for node in [*fixed, *translated]:
        editor.root.insert(insertion_index, node)
        insertion_index += 1


def _translate_cf_node(
    source: ET.Element,
    target_section_index: int,
    row_map: MobilitiRowMap,
    *,
    source_section_index: int | None = None,
) -> ET.Element:
    clone = deepcopy(source)
    source_index = (
        _sqref_section_index(source.attrib.get("sqref", ""))
        if source_section_index is None
        else source_section_index
    )
    if source_index is None:
        return clone
    source_start = 14 + source_index * 35
    target_start = row_map.sections[target_section_index].product_start
    clone.set(
        "sqref",
        _translate_sqref_token(
            source.attrib["sqref"], source_start, target_start, row_map.sections[target_section_index].capacity
        ),
    )
    for formula in clone.findall(f".//{{{MAIN}}}formula"):
        if formula.text:
            formula.text = _translate_official_formula(
                "=" + formula.text,
                origin=f"A{source_start}",
                target=f"A{target_start}",
                row_map=row_map,
            )[1:]
    return clone


def _cell_column(cell: ET.Element) -> int:
    match = re.match(r"([A-Z]{1,3})", cell.attrib.get("r", ""))
    if match is None:
        raise ValueError("Celda oficial sin coordenada válida")
    return column_index_from_string(match.group(1))


def _find_cell(row: ET.Element, column: int) -> ET.Element | None:
    for cell in row.findall(f"{{{MAIN}}}c"):
        if _cell_column(cell) == column:
            return cell
    return None


def _move_cell(
    cell: ET.Element,
    *,
    source_row: int,
    target_row: int,
    row_map: MobilitiRowMap,
    product_range: tuple[int, int] | None = None,
    clear_input_formulas: bool = False,
) -> None:
    column = get_column_letter(_cell_column(cell))
    origin = cell.attrib.get("r", f"{column}{source_row}")
    target = f"{column}{target_row}"
    cell.set("r", target)
    formula = cell.find(f"{{{MAIN}}}f")
    if formula is None:
        return
    if clear_input_formulas and _cell_column(cell) in INPUT_COLUMNS:
        _clear_cell(cell)
        return
    if not formula.text:
        raise ValueError(f"La fila canónica contiene una fórmula compartida vacía: {origin}")
    formula.text = _translate_official_formula(
        "=" + formula.text,
        origin=origin,
        target=target,
        row_map=row_map,
        product_range=product_range,
    )[1:]
    for shared_attribute in ("t", "ref", "si"):
        formula.attrib.pop(shared_attribute, None)
    value = cell.find(f"{{{MAIN}}}v")
    if value is not None:
        cell.remove(value)


def _translate_official_formula(
    formula: str,
    *,
    origin: str,
    target: str,
    row_map: MobilitiRowMap,
    product_range: tuple[int, int] | None = None,
) -> str:
    result = ["="]
    for token in Tokenizer(formula).items:
        value = token.value
        if token.type == "OPERAND" and token.subtype == "RANGE" and _RANGE_REFERENCE.fullmatch(value):
            # translate_formula sigue siendo la única autoridad de traslación;
            # los nombres definidos oficiales se dejan intactos por no ser rangos.
            translated = translate_formula(
                "=" + value,
                origin=origin,
                target=target,
                sheet="Mobiliti",
            )[1:]
            value = _structural_reference(
                value,
                translated,
                row_map,
                product_range=product_range,
            )
        result.append(value)
    return "".join(result)


def _structural_reference(
    original: str,
    translated: str,
    row_map: MobilitiRowMap,
    *,
    product_range: tuple[int, int] | None,
) -> str:
    match = _RANGE_REFERENCE.fullmatch(original)
    if match is None:
        return translated
    endpoints = [match.group("first")]
    if match.group("last"):
        endpoints.append(match.group("last"))
    parsed = [_CELL_REFERENCE.fullmatch(endpoint) for endpoint in endpoints]
    if any(item is None for item in parsed):
        return translated
    rows = [int(item.group("row")) for item in parsed if item is not None]
    if product_range is not None and (
        rows == [14, 46] or rows == [49, 81]
    ):
        first, last = product_range
        return _reference_with_rows(original, (first, last))
    if all(CANONICAL_TOTAL_ROW <= row <= CANONICAL_AUXILIARY_END for row in rows):
        return _reference_with_rows(
            original,
            tuple(row_map.total_row + row - CANONICAL_TOTAL_ROW for row in rows),
        )
    return translated


def _reference_with_rows(reference: str, rows: tuple[int, ...]) -> str:
    match = _RANGE_REFERENCE.fullmatch(reference)
    if match is None:
        return reference
    endpoints = [match.group("first")]
    if match.group("last"):
        endpoints.append(match.group("last"))
    rewritten = []
    for endpoint, row in zip(endpoints, rows, strict=True):
        cell = _CELL_REFERENCE.fullmatch(endpoint)
        if cell is None:
            return reference
        rewritten.append(f"{cell.group('column')}{cell.group('row_abs')}{row}")
    prefix = f"{match.group('sheet')}!" if match.group("sheet") else ""
    return prefix + ":".join(rewritten)


def _translate_total_formula(
    formula: str,
    *,
    origin: str,
    target: str,
    subtotal_rows: Sequence[int],
) -> str:
    tokens = Tokenizer(formula).items
    reference_indices = []
    source_rows = []
    source_columns = []
    for index, token in enumerate(tokens):
        if token.type != "OPERAND" or token.subtype != "RANGE":
            continue
        match = _RANGE_REFERENCE.fullmatch(token.value)
        cell = None if match is None else _CELL_REFERENCE.fullmatch(match.group("first"))
        if cell is None or int(cell.group("row")) not in CANONICAL_SUBTOTAL_ROWS:
            continue
        # Ejecuta la traducción oficial antes de sustituir el conjunto estructural.
        translate_formula("=" + token.value, origin=origin, target=target, sheet="Mobiliti")
        reference_indices.append(index)
        source_rows.append(int(cell.group("row")))
        source_columns.append(cell.group("column") + cell.group("row_abs"))
    if len(reference_indices) != len(CANONICAL_SUBTOTAL_ROWS):
        raise ValueError(f"El total oficial no contiene 16 subtotales: {origin}")
    first, second, last = reference_indices[0], reference_indices[1], reference_indices[-1]
    separator = "".join(token.value for token in tokens[first + 1 : second])
    descending = source_rows[0] > source_rows[-1]
    rows = list(reversed(subtotal_rows)) if descending else list(subtotal_rows)
    column = source_columns[0]
    references = separator.join(f"{column}{row}" for row in rows)
    prefix = "".join(token.value for token in tokens[:first])
    suffix = "".join(token.value for token in tokens[last + 1 :])
    return "=" + prefix + references + suffix


def _clear_cell(cell: ET.Element) -> None:
    for child in list(cell):
        if child.tag in {
            f"{{{MAIN}}}f",
            f"{{{MAIN}}}v",
            f"{{{MAIN}}}is",
        }:
            cell.remove(child)
    cell.attrib.pop("t", None)


def _set_cell_value(
    row: ET.Element,
    coordinate: str,
    kind: Literal["number", "text", "boolean"],
    value: Decimal | str | bool,
) -> None:
    column = column_index_from_string(re.match(r"[A-Z]{1,3}", coordinate).group())
    cell = _find_cell(row, column)
    if cell is None:
        cell = ET.Element(f"{{{MAIN}}}c", {"r": coordinate})
        row.append(cell)
    _clear_cell(cell)
    cell.set("r", coordinate)
    if kind == "text":
        if not isinstance(value, str):
            raise TypeError("Una escritura text requiere str")
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, f"{{{MAIN}}}is")
        text = ET.SubElement(inline, f"{{{MAIN}}}t")
        text.text = value
    elif kind == "boolean":
        if type(value) is not bool:
            raise TypeError("Una escritura boolean requiere bool")
        cell.set("t", "b")
        ET.SubElement(cell, f"{{{MAIN}}}v").text = "1" if value else "0"
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, Decimal):
            raise TypeError("Una escritura number requiere Decimal")
        ET.SubElement(cell, f"{{{MAIN}}}v").text = format(value, "f")
    else:
        raise ValueError(f"Tipo de escritura Mobiliti inválido: {kind!r}")
    _sort_cells(row)


def _sort_cells(row: ET.Element) -> None:
    cells = list(row.findall(f"{{{MAIN}}}c"))
    others = [node for node in row if node.tag != f"{{{MAIN}}}c"]
    cells.sort(key=_cell_column)
    row[:] = [*cells, *others]


def _translate_sqref(value: str, row_map: MobilitiRowMap) -> str:
    result = []
    seen = set()
    canonical_columns: list[tuple[str, str]] = []
    for token in value.split():
        match = _RANGE_REFERENCE.fullmatch(token)
        section_index = _sqref_token_section_index(token)
        if match is None or section_index is None:
            translated = token
        else:
            section = row_map.sections[section_index]
            translated = _translate_sqref_token(
                token, 14 + section_index * 35, section.product_start, section.capacity
            )
            if section_index == 1:
                first = _CELL_REFERENCE.fullmatch(match.group("first"))
                last = _CELL_REFERENCE.fullmatch(match.group("last"))
                canonical_columns.append((first.group("column"), last.group("column")))
        if translated not in seen:
            result.append(translated)
            seen.add(translated)
    for section in row_map.sections[len(CANONICAL_SUBTOTAL_ROWS) :]:
        for first_column, last_column in canonical_columns:
            token = (
                f"{first_column}{section.product_start}:"
                f"{last_column}{section.product_start + section.capacity - 1}"
            )
            if token not in seen:
                result.append(token)
                seen.add(token)
    return " ".join(result)


def _translate_sqref_token(
    token: str, source_start: int, target_start: int, capacity: int
) -> str:
    match = _RANGE_REFERENCE.fullmatch(token)
    if match is None:
        return token
    first = _CELL_REFERENCE.fullmatch(match.group("first"))
    last = _CELL_REFERENCE.fullmatch(match.group("last"))
    if first is None or last is None:
        return token
    prefix = f"{match.group('sheet')}!" if match.group("sheet") else ""
    first_delta = int(first.group("row")) - source_start
    last_delta = int(last.group("row")) - (source_start + 32)
    return (
        f"{prefix}{first.group('column')}{first.group('row_abs')}{target_start + first_delta}:"
        f"{last.group('column')}{last.group('row_abs')}"
        f"{target_start + capacity - 1 + last_delta}"
    )


def _sqref_token_section_index(token: str) -> int | None:
    match = _RANGE_REFERENCE.fullmatch(token)
    if match is None or match.group("last") is None:
        return None
    first = _CELL_REFERENCE.fullmatch(match.group("first"))
    last = _CELL_REFERENCE.fullmatch(match.group("last"))
    if first is None or last is None:
        return None
    first_row, last_row = int(first.group("row")), int(last.group("row"))
    if first_row < 14 or (first_row - 14) % 35 or last_row != first_row + 32:
        return None
    index = (first_row - 14) // 35
    return index if index < len(CANONICAL_SUBTOTAL_ROWS) else None


def _sqref_section_index(value: str) -> int | None:
    indices = {_sqref_token_section_index(token) for token in value.split()}
    indices.discard(None)
    return next(iter(indices)) if len(indices) == 1 else None


def _update_dimension(editor: WorksheetEditor, last_row: int) -> None:
    dimension = editor.root.find(f"{{{MAIN}}}dimension")
    if dimension is None:
        return
    reference = dimension.attrib.get("ref", "A1:AV610")
    if ":" not in reference:
        return
    start, end = reference.split(":", 1)
    column = re.match(r"\$?[A-Z]{1,3}", end)
    if column:
        dimension.set("ref", f"{start}:{column.group()}{last_row}")


def _replace_merges(
    editor: WorksheetEditor,
    canonical: OfficialMobilitiBlock,
    row_map: MobilitiRowMap,
) -> None:
    container = editor.root.find(f"{{{MAIN}}}mergeCells")
    if container is None:
        container = ET.Element(f"{{{MAIN}}}mergeCells")
        sheet_data_index = list(editor.root).index(editor.sheet_data)
        editor.root.insert(sheet_data_index + 1, container)
    preserved = []
    for merge in canonical.merges:
        bounds = _merge_bounds(merge.attrib["ref"])
        if bounds is None:
            preserved.append(deepcopy(merge))
            continue
        min_col, min_row, max_col, max_row = bounds
        dynamic_table = min_row >= 13 and max_row <= CANONICAL_TOTAL_ROW and min_col <= TABLE_LAST_COLUMN
        auxiliary = min_row >= CANONICAL_AUXILIARY_START and max_row <= CANONICAL_AUXILIARY_END
        if not dynamic_table and not auxiliary:
            preserved.append(deepcopy(merge))
    generated = []
    for index, section in enumerate(row_map.sections):
        templates = (
            ((13, section.section_row), (47, section.subtotal_row))
            if index == 0
            else ((48, section.section_row), (82, section.subtotal_row))
        )
        for source_row, target_row in templates:
            generated.extend(_row_merges(canonical.merges, source_row, target_row, TABLE_LAST_COLUMN))
    for merge in canonical.merges:
        bounds = _merge_bounds(merge.attrib["ref"])
        if bounds and bounds[1] >= CANONICAL_AUXILIARY_START and bounds[3] <= CANONICAL_AUXILIARY_END:
            generated.append(_shift_merge(merge, row_map.total_row - CANONICAL_TOTAL_ROW))
    container[:] = [*preserved, *generated]
    container.set("count", str(len(container)))


def _row_merges(
    merges: Sequence[ET.Element], source_row: int, target_row: int, max_column: int
) -> list[ET.Element]:
    result = []
    for merge in merges:
        bounds = _merge_bounds(merge.attrib["ref"])
        if bounds and bounds[1] == bounds[3] == source_row and bounds[2] <= max_column:
            result.append(_shift_merge(merge, target_row - source_row))
    return result


def _merge_bounds(reference: str) -> tuple[int, int, int, int] | None:
    match = _RANGE_REFERENCE.fullmatch(reference)
    if match is None or match.group("last") is None:
        return None
    first = _CELL_REFERENCE.fullmatch(match.group("first"))
    last = _CELL_REFERENCE.fullmatch(match.group("last"))
    if first is None or last is None:
        return None
    return (
        column_index_from_string(first.group("column").replace("$", "")),
        int(first.group("row")),
        column_index_from_string(last.group("column").replace("$", "")),
        int(last.group("row")),
    )


def _shift_merge(merge: ET.Element, row_delta: int) -> ET.Element:
    clone = deepcopy(merge)
    match = _RANGE_REFERENCE.fullmatch(clone.attrib["ref"])
    rows = []
    for endpoint in (match.group("first"), match.group("last")):
        cell = _CELL_REFERENCE.fullmatch(endpoint)
        rows.append(int(cell.group("row")) + row_delta)
    clone.set("ref", _reference_with_rows(clone.attrib["ref"], tuple(rows)))
    return clone


def build_mobiliti_sheet(
    official_sheet_xml: bytes,
    needs: list[SectionNeed],
    cell_writes: Sequence[MobilitiCellWrite],
) -> MobilitiSheetMutation:
    """Construye la mutación de ``Mobiliti`` a partir del XML oficial."""

    row_map = plan_mobiliti_layout(needs)
    editor = WorksheetEditor.from_xml(official_sheet_xml)
    canonical = capture_official_mobiliti_block(editor)
    _translate_static_structural_formulas(editor, row_map)
    apply_mobiliti_layout(editor, row_map)

    for index, section in enumerate(row_map.sections):
        first = index == 0
        header = canonical.first_section_header if first else canonical.section_header
        product = canonical.first_product_row if first else canonical.product_row
        subtotal = canonical.first_subtotal_row if first else canonical.subtotal_row
        header_source_row = 13 if first else 48
        product_source_row = 14 if first else 49
        subtotal_source_row = 47 if first else 82
        clone_section_header(
            editor,
            header,
            section.section_row,
            section.title,
            row_map,
            source_row=header_source_row,
        )
        for target_row in range(section.product_start, section.product_start + section.capacity):
            clone_formula_row(
                editor,
                product,
                target_row,
                row_map,
                source_row=product_source_row,
            )
        clone_subtotal_row(
            editor,
            subtotal,
            section,
            row_map,
            source_row=subtotal_source_row,
        )

    clone_total_row(editor, canonical.total_row, row_map.total_row, row_map)
    relocate_official_auxiliary_rows(editor, row_map, canonical)
    clear_mobiliti_input_cells(editor, row_map)
    apply_mobiliti_cell_writes(editor, cell_writes, row_map)
    _replace_merges(editor, canonical, row_map)
    translate_mobiliti_validations(editor, row_map)
    translate_mobiliti_conditional_formatting(editor, row_map)
    editor.sort_rows()
    return MobilitiSheetMutation(xml=editor.to_xml(), row_map=row_map)
