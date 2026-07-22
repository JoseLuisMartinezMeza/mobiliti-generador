"""Filas canónicas y XML seguro para hojas auxiliares de cotización."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
import re
import struct
from typing import Mapping, Sequence
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .ooxml_package import (
    OFFICE_DOCUMENT_RELATIONSHIPS,
    PACKAGE_RELATIONSHIPS,
    XlsxPackage,
    rewrite_relationship_targets,
)


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
WORKSHEET_RELATIONSHIP = f"{OFFICE_DOCUMENT_RELATIONSHIPS}/worksheet"
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
_STYLE_SECTIONS = (
    "numFmts",
    "fonts",
    "fills",
    "borders",
    "cellStyleXfs",
    "cellXfs",
    "cellStyles",
    "dxfs",
    "tableStyles",
)
_STYLE_SECTION_CHILDREN = {
    "numFmts": "numFmt",
    "fonts": "font",
    "fills": "fill",
    "borders": "border",
    "cellStyleXfs": "xf",
    "cellXfs": "xf",
    "cellStyles": "cellStyle",
    "dxfs": "dxf",
    "tableStyles": "tableStyle",
}
_STYLE_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "numFmts",
            "fonts",
            "fills",
            "borders",
            "cellStyleXfs",
            "cellXfs",
            "cellStyles",
            "dxfs",
            "tableStyles",
            "colors",
            "extLst",
        )
    )
}
_INDEXED_STYLE_COMPONENTS = {
    "fontId": ("fonts", "font"),
    "fillId": ("fills", "fill"),
    "borderId": ("borders", "border"),
}
_SAFE_CLOSURE_RELATIONSHIPS = {
    "drawing",
    "image",
    "comments",
    "vmlDrawing",
    "table",
    "hyperlink",
    "printerSettings",
    "chart",
    "chartUserShapes",
}
_SAFE_CLOSURE_RELATIONSHIP_TYPES = {
    f"{OFFICE_DOCUMENT_RELATIONSHIPS}/{name}"
    for name in _SAFE_CLOSURE_RELATIONSHIPS
}
_ACTIVE_RELATIONSHIP_MARKERS = (
    "vbaproject",
    "oleobject",
    "activex",
    "/control",
    "attachedtoolbars",
    "externallink",
)
_ACTIVE_PART_MARKERS = (
    "xl/vbaproject",
    "xl/activex/",
    "xl/ctrlprops/",
    "xl/embeddings/",
    "xl/customui/",
    "xl/externallinks/",
    "_xmlsignatures/",
    "xl/connections.xml",
    "xl/attachedtoolbars",
)


@dataclass(frozen=True)
class LocalDefinedName:
    """Nombre definido local, desacoplado de su índice de hoja de origen."""

    name: str
    text: str
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Nombre definido local inválido")
        if not isinstance(self.text, str):
            raise TypeError("Texto de nombre definido local inválido")
        copied: dict[str, str] = {}
        for key, value in self.attributes.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or key in {"name", "localSheetId"}
            ):
                raise ValueError("Metadatos de nombre definido local inválidos")
            copied[key] = value
        object.__setattr__(self, "attributes", MappingProxyType(copied))

    def xml_for_sheet_index(self, sheet_index: int) -> bytes:
        """Serializa el nombre con el índice final asignado por el compositor."""

        if type(sheet_index) is not int or sheet_index < 0:
            raise ValueError("Índice de hoja para nombre definido inválido")
        attributes = {
            "name": self.name,
            **self.attributes,
            "localSheetId": str(sheet_index),
        }
        element = ET.Element(f"{{{MAIN}}}definedName", attributes)
        element.text = self.text
        return ET.tostring(element, encoding="utf-8")


@dataclass(frozen=True)
class SheetAddition:
    """Hoja nueva con adiciones, reemplazos y metadatos OPC explícitos."""

    name: str
    state: str
    xml: bytes
    parts: Mapping[str, bytes] = field(default_factory=dict)
    replacements: Mapping[str, bytes] = field(default_factory=dict)
    content_types: Mapping[str, str] = field(default_factory=dict)
    sheet_part: str | None = None
    relationship_type: str = WORKSHEET_RELATIONSHIP
    defined_names: tuple[LocalDefinedName, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in {
            "Quotation", "Quotation_Data"
        }:
            raise ValueError("Nombre de SheetAddition inválido")
        if self.state not in {"visible", "hidden", "veryHidden"} or not isinstance(
            self.xml, bytes
        ):
            raise ValueError("SheetAddition inválido")
        if self.name == "Quotation_Data" and self.state != "veryHidden":
            raise ValueError("Quotation_Data debe permanecer veryHidden")
        additions = _immutable_bytes_mapping(self.parts, "Partes")
        replacements = _immutable_bytes_mapping(self.replacements, "Reemplazos")
        if set(additions) & set(replacements):
            raise ValueError("Partes y reemplazos de SheetAddition duplicados")
        content_types: dict[str, str] = {}
        for name, content_type in self.content_types.items():
            if not isinstance(name, str) or not isinstance(content_type, str) or not content_type:
                raise TypeError("Content types de SheetAddition inválidos")
            content_types[name] = content_type
        if not isinstance(self.relationship_type, str) or not self.relationship_type.endswith(
            "/worksheet"
        ):
            raise ValueError("Tipo de relación de SheetAddition inválido")
        if self.sheet_part is not None:
            if self.sheet_part not in additions or additions[self.sheet_part] != self.xml:
                raise ValueError("Parte principal de SheetAddition inválida")
        elif self.name == "Quotation":
            raise ValueError("Quotation requiere parte principal asignada")
        if not isinstance(self.defined_names, tuple) or not all(
            isinstance(item, LocalDefinedName) for item in self.defined_names
        ):
            raise TypeError("Nombres definidos de SheetAddition inválidos")
        object.__setattr__(self, "parts", additions)
        object.__setattr__(self, "replacements", replacements)
        object.__setattr__(self, "content_types", MappingProxyType(content_types))


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


def inline_source_shared_strings(
    sheet_xml: bytes,
    shared_strings: Sequence[object],
) -> bytes:
    """Convierte celdas shared-string a inlineStr sin perder CT_Rst rico."""

    root = _parse_worksheet(sheet_xml)
    _validate_unique_cell_references(root)
    if isinstance(shared_strings, (str, bytes)) or not isinstance(
        shared_strings, Sequence
    ):
        raise TypeError("Shared strings inválidos")
    parsed = tuple(_shared_string_children(item) for item in shared_strings)
    for cell in root.findall(f".//{{{MAIN}}}c[@t='s']"):
        values = cell.findall(f"{{{MAIN}}}v")
        if len(values) != 1 or values[0].text is None:
            raise ValueError("Celda shared string sin índice válido")
        raw_index = values[0].text
        if re.fullmatch(r"0|[1-9][0-9]*", raw_index) is None:
            raise ValueError("Índice de shared string inválido")
        index = int(raw_index)
        if index >= len(parsed):
            raise ValueError("Índice de shared string fuera de rango")
        if cell.find(f"{{{MAIN}}}is") is not None:
            raise ValueError("Celda shared string ambigua")
        value_index = list(cell).index(values[0])
        cell.remove(values[0])
        inline = ET.Element(f"{{{MAIN}}}is")
        for child in parsed[index]:
            inline.append(deepcopy(child))
        cell.insert(value_index, inline)
        cell.attrib["t"] = "inlineStr"
    return _xml_bytes(root)


@dataclass
class StyleTableMerger:
    """Fusiona estilos por dependencia sin tocar índices oficiales existentes."""

    root: ET.Element
    component_maps: dict[str, dict[tuple, int]]
    dxf_map: dict[int, int] = field(default_factory=dict)
    table_style_name_map: dict[str, str] = field(default_factory=dict)
    _num_fmt_map: dict[int, int] = field(default_factory=dict, repr=False)
    _indexed_maps: dict[str, dict[int, int]] = field(default_factory=dict, repr=False)
    _style_xf_map: dict[int, int] = field(default_factory=dict, repr=False)
    _source: ET.Element | None = field(default=None, repr=False)

    @classmethod
    def from_xml(cls, target_styles: bytes) -> "StyleTableMerger":
        root = _parse_style_sheet(target_styles)
        maps: dict[str, dict[tuple, int]] = {}
        for section_name, child_name in (
            ("fonts", "font"),
            ("fills", "fill"),
            ("borders", "border"),
            ("cellStyleXfs", "xf"),
            ("cellXfs", "xf"),
            ("dxfs", "dxf"),
        ):
            maps[section_name] = {
                _element_key(element): index
                for index, element in enumerate(
                    _section_children(root, section_name, child_name)
                )
            }
        return cls(root=root, component_maps=maps)

    def merge_referenced_styles(
        self,
        source_styles: bytes,
        style_ids: set[int],
        *,
        dxf_ids: set[int] | None = None,
        table_style_names: set[str] | None = None,
    ) -> dict[int, int]:
        source = _parse_style_sheet(source_styles)
        self._source = source
        self._num_fmt_map = {}
        self._indexed_maps = {name: {} for name in ("fonts", "fills", "borders")}
        self._style_xf_map = {}
        self.dxf_map = {}
        self.table_style_name_map = {}

        mapping: dict[int, int] = {}
        for style_id in sorted(style_ids):
            if type(style_id) is not int or style_id < 0:
                raise ValueError("Referencia de estilo inválida")
            mapping[style_id] = self._merge_cell_xf(style_id)
        for dxf_id in sorted(dxf_ids or ()):
            self._merge_dxf(dxf_id)
        for style_name in sorted(table_style_names or ()):
            self._merge_table_style(style_name)
        return mapping

    def to_xml(self) -> bytes:
        for section_name in _STYLE_SECTIONS:
            section = self.root.find(f"{{{MAIN}}}{section_name}")
            if section is not None:
                section.attrib["count"] = str(len(section))
        return _xml_bytes(self.root)

    def _merge_cell_xf(self, style_id: int) -> int:
        assert self._source is not None
        source_xfs = _section_children(self._source, "cellXfs", "xf")
        if style_id >= len(source_xfs):
            raise ValueError(f"Referencia de estilo fuera de rango: {style_id}")
        clone = deepcopy(source_xfs[style_id])
        self._remap_xf(clone, include_xf_id=True)
        return self._find_or_append("cellXfs", "xf", clone)

    def _merge_style_xf(self, style_xf_id: int) -> int:
        assert self._source is not None
        if style_xf_id in self._style_xf_map:
            return self._style_xf_map[style_xf_id]
        source_xfs = _section_children(self._source, "cellStyleXfs", "xf")
        if style_xf_id < 0 or style_xf_id >= len(source_xfs):
            raise ValueError(f"Referencia xfId fuera de rango: {style_xf_id}")
        clone = deepcopy(source_xfs[style_xf_id])
        if "xfId" in clone.attrib:
            raise ValueError("cellStyleXf con dependencia circular")
        self._remap_xf(clone, include_xf_id=False)
        target_id = self._find_or_append("cellStyleXfs", "xf", clone)
        self._style_xf_map[style_xf_id] = target_id
        self._merge_cell_styles(style_xf_id, target_id)
        return target_id

    def _remap_xf(self, xf: ET.Element, *, include_xf_id: bool) -> None:
        for attribute, (section_name, child_name) in _INDEXED_STYLE_COMPONENTS.items():
            source_id = _integer_attribute(xf, attribute, default=0)
            xf.attrib[attribute] = str(
                self._merge_indexed_component(
                    section_name, child_name, source_id
                )
            )
        num_fmt_id = _integer_attribute(xf, "numFmtId", default=0)
        xf.attrib["numFmtId"] = str(self._merge_num_fmt(num_fmt_id))
        if include_xf_id:
            source_xf_id = _integer_attribute(xf, "xfId", default=0)
            xf.attrib["xfId"] = str(self._merge_style_xf(source_xf_id))

    def _merge_indexed_component(
        self,
        section_name: str,
        child_name: str,
        source_id: int,
    ) -> int:
        assert self._source is not None
        cache = self._indexed_maps[section_name]
        if source_id in cache:
            return cache[source_id]
        source_items = _section_children(self._source, section_name, child_name)
        if source_id < 0 or source_id >= len(source_items):
            raise ValueError(
                f"Referencia {section_name} fuera de rango: {source_id}"
            )
        target_id = self._find_or_append(
            section_name, child_name, deepcopy(source_items[source_id])
        )
        cache[source_id] = target_id
        return target_id

    def _merge_num_fmt(self, source_id: int) -> int:
        assert self._source is not None
        if source_id in self._num_fmt_map:
            return self._num_fmt_map[source_id]
        source_by_id = _num_fmts_by_id(self._source)
        source_element = source_by_id.get(source_id)
        if source_element is None:
            if source_id >= 164:
                raise ValueError(f"numFmt fuera de rango: {source_id}")
            self._num_fmt_map[source_id] = source_id
            return source_id
        target_by_id = _num_fmts_by_id(self.root)
        format_code = source_element.attrib["formatCode"]
        same_code = sorted(
            number
            for number, element in target_by_id.items()
            if element.attrib["formatCode"] == format_code
        )
        if same_code:
            target_id = same_code[0]
        else:
            used = set(target_by_id)
            target_id = source_id if source_id >= 164 and source_id not in used else 164
            while target_id in used:
                target_id += 1
            clone = deepcopy(source_element)
            clone.attrib["numFmtId"] = str(target_id)
            self._section("numFmts").append(clone)
        self._num_fmt_map[source_id] = target_id
        return target_id

    def _merge_dxf(self, dxf_id: int) -> int:
        assert self._source is not None
        if dxf_id in self.dxf_map:
            return self.dxf_map[dxf_id]
        source_dxfs = _section_children(self._source, "dxfs", "dxf")
        if type(dxf_id) is not int or dxf_id < 0 or dxf_id >= len(source_dxfs):
            raise ValueError(f"Referencia dxf fuera de rango: {dxf_id}")
        clone = deepcopy(source_dxfs[dxf_id])
        for num_fmt in clone.iter(f"{{{MAIN}}}numFmt"):
            old_id = _integer_attribute(num_fmt, "numFmtId", default=None)
            format_code = num_fmt.get("formatCode")
            if old_id is None or format_code is None:
                raise ValueError("numFmt de dxf inválido")
            target_id = self._merge_num_fmt_code(old_id, format_code)
            num_fmt.attrib["numFmtId"] = str(target_id)
        target_id = self._find_or_append("dxfs", "dxf", clone)
        self.dxf_map[dxf_id] = target_id
        return target_id

    def _merge_num_fmt_code(self, source_id: int, format_code: str) -> int:
        assert self._source is not None
        source_by_id = _num_fmts_by_id(self._source)
        existing = source_by_id.get(source_id)
        if existing is not None and existing.attrib["formatCode"] != format_code:
            raise ValueError("numFmt dxf inconsistente")
        if existing is None:
            synthetic = ET.Element(
                f"{{{MAIN}}}numFmt",
                {"numFmtId": str(source_id), "formatCode": format_code},
            )
            section = self._source.find(f"{{{MAIN}}}numFmts")
            if section is None:
                section = _insert_style_section(self._source, "numFmts")
            section.append(synthetic)
        return self._merge_num_fmt(source_id)

    def _merge_cell_styles(self, source_xf_id: int, target_xf_id: int) -> None:
        assert self._source is not None
        source_styles = _section_children(
            self._source, "cellStyles", "cellStyle", required=False
        )
        target_section = self._section("cellStyles")
        for source_style in source_styles:
            if _integer_attribute(source_style, "xfId", default=None) != source_xf_id:
                continue
            clone = deepcopy(source_style)
            clone.attrib["xfId"] = str(target_xf_id)
            key = _element_key(clone)
            if any(_element_key(item) == key for item in target_section):
                continue
            used_names = {item.get("name", "") for item in target_section}
            source_name = clone.get("name")
            if not source_name:
                raise ValueError("cellStyle sin nombre")
            if source_name in used_names:
                suffix = 1
                candidate = f"{source_name} Quotation {suffix}"
                while candidate in used_names:
                    suffix += 1
                    candidate = f"{source_name} Quotation {suffix}"
                clone.attrib["name"] = candidate
                clone.attrib.pop("builtinId", None)
            target_section.append(clone)

    def _merge_table_style(self, style_name: str) -> str:
        assert self._source is not None
        if not isinstance(style_name, str) or not style_name:
            raise ValueError("Nombre de tableStyle inválido")
        if style_name in self.table_style_name_map:
            return self.table_style_name_map[style_name]
        source_section = self._source.find(f"{{{MAIN}}}tableStyles")
        source_matches = [] if source_section is None else [
            item
            for item in source_section.findall(f"{{{MAIN}}}tableStyle")
            if item.get("name") == style_name
        ]
        if not source_matches:
            self.table_style_name_map[style_name] = style_name
            return style_name
        if len(source_matches) != 1:
            raise ValueError(f"tableStyle duplicado: {style_name}")
        clone = deepcopy(source_matches[0])
        for element in clone.findall(f"{{{MAIN}}}tableStyleElement"):
            dxf_id = _integer_attribute(element, "dxfId", default=None)
            if dxf_id is None:
                raise ValueError("tableStyleElement sin dxfId")
            element.attrib["dxfId"] = str(self._merge_dxf(dxf_id))
        target_section = self._section("tableStyles")
        existing = [
            item for item in target_section if item.get("name") == style_name
        ]
        if existing and _element_key(existing[0]) == _element_key(clone):
            target_name = style_name
        else:
            used_names = {item.get("name", "") for item in target_section}
            target_name = style_name
            suffix = 1
            while target_name in used_names:
                target_name = f"{style_name}_Quotation_{suffix}"
                suffix += 1
            clone.attrib["name"] = target_name
            target_section.append(clone)
        self.table_style_name_map[style_name] = target_name
        return target_name

    def _find_or_append(
        self,
        section_name: str,
        child_name: str,
        element: ET.Element,
    ) -> int:
        section = self._section(section_name)
        key = _element_key(element)
        component_map = self.component_maps.setdefault(section_name, {})
        if key in component_map:
            return component_map[key]
        target_id = len(section)
        section.append(element)
        component_map[key] = target_id
        return target_id

    def _section(self, name: str) -> ET.Element:
        section = self.root.find(f"{{{MAIN}}}{name}")
        if section is None:
            section = _insert_style_section(self.root, name)
        return section


def remap_source_styles(
    sheet_xml: bytes,
    source_styles: bytes,
    target_styles: bytes,
) -> tuple[bytes, bytes]:
    """Fusiona y remapea refs de celdas, filas, columnas y CF estándar."""

    remapped_sheet, merged_styles, _parts = _remap_source_styles_with_parts(
        sheet_xml,
        source_styles,
        target_styles,
        {},
    )
    return remapped_sheet, merged_styles


def transplant_quotation(
    source: Path | bytes | None,
    destination_package: XlsxPackage,
) -> SheetAddition | None:
    """Transplanta Quotation y toda su clausura pasiva a una nueva adición."""

    if source is None:
        return None
    if not isinstance(destination_package, XlsxPackage):
        raise TypeError("Paquete destino inválido")
    source_package = (
        XlsxPackage.from_bytes(source)
        if isinstance(source, bytes)
        else XlsxPackage.read(Path(source))
    )
    _reject_unsupported_active_content(source_package)
    try:
        source_sheet = source_package.sheet_part("Quotation")
    except KeyError:
        return None
    source_state = source_package.sheet_state("Quotation")
    closure = source_package.relationship_closure(source_sheet)
    _validate_passive_closure(closure)
    allocation = destination_package.allocate_closure(
        closure, prefix="quotation_original"
    )
    rewritten = rewrite_relationship_targets(closure, allocation)
    target_sheet = allocation[source_sheet]
    sheet_xml = inline_source_shared_strings(
        rewritten[target_sheet], source_package.shared_strings()
    )

    source_styles_part = source_package.workbook_related_part("styles")
    destination_styles_part = destination_package.workbook_related_part("styles")
    if source_styles_part is None or destination_styles_part is None:
        raise ValueError("Styles OOXML ausentes")
    sheet_xml, styles_xml, rewritten = _remap_source_styles_with_parts(
        sheet_xml,
        source_package.parts[source_styles_part],
        destination_package.parts[destination_styles_part],
        rewritten,
    )
    rewritten[target_sheet] = sheet_xml

    source_content_types = source_package.content_types_for(set(closure))
    allocated_content_types = {
        allocation[source_name]: content_type
        for source_name, content_type in source_content_types.items()
    }
    defined_names = _quotation_local_defined_names(source_package)
    return SheetAddition(
        name="Quotation",
        state=source_state,
        xml=sheet_xml,
        parts=rewritten,
        replacements={destination_styles_part: styles_xml},
        content_types=allocated_content_types,
        sheet_part=target_sheet,
        relationship_type=WORKSHEET_RELATIONSHIP,
        defined_names=defined_names,
    )


def _remap_source_styles_with_parts(
    sheet_xml: bytes,
    source_styles: bytes,
    target_styles: bytes,
    related_parts: Mapping[str, bytes],
) -> tuple[bytes, bytes, dict[str, bytes]]:
    root = _parse_worksheet(sheet_xml)
    _validate_unique_cell_references(root)
    # Las celdas sin `s` dependen del cellXf 0 del libro fuente.
    style_ids: set[int] = {0}
    for xpath, attribute in (
        (f".//{{{MAIN}}}c[@s]", "s"),
        (f".//{{{MAIN}}}row[@s]", "s"),
        (f".//{{{MAIN}}}col[@style]", "style"),
    ):
        for element in root.findall(xpath):
            value = _integer_attribute(element, attribute, default=None)
            if value is None:
                raise ValueError("Referencia de estilo inválida")
            style_ids.add(value)
    dxf_ids: set[int] = set()
    for element in root.iter():
        if "dxfId" in element.attrib:
            dxf_id = _integer_attribute(element, "dxfId", default=None)
            if dxf_id is None:
                raise ValueError("Referencia dxf inválida")
            dxf_ids.add(dxf_id)

    rewritten_parts = {name: bytes(content) for name, content in related_parts.items()}
    tables: dict[str, ET.Element] = {}
    table_style_names: set[str] = set()
    for name, content in rewritten_parts.items():
        if not name.startswith("xl/tables/") or name.endswith(".rels"):
            continue
        try:
            table = ET.fromstring(content)
        except ET.ParseError as error:
            raise ValueError(f"Tabla OOXML inválida: {name}") from error
        if table.tag != f"{{{MAIN}}}table":
            raise ValueError(f"Tabla OOXML inválida: {name}")
        style_infos = table.findall(f"{{{MAIN}}}tableStyleInfo")
        if len(style_infos) > 1:
            raise ValueError(f"tableStyleInfo duplicado: {name}")
        if style_infos:
            style_name = style_infos[0].get("name")
            if not style_name:
                raise ValueError(f"tableStyleInfo inválido: {name}")
            table_style_names.add(style_name)
        tables[name] = table

    merger = StyleTableMerger.from_xml(target_styles)
    style_map = merger.merge_referenced_styles(
        source_styles,
        style_ids,
        dxf_ids=dxf_ids,
        table_style_names=table_style_names,
    )
    styled_column_ranges: list[tuple[int, int]] = []
    for column in root.findall(f".//{{{MAIN}}}col[@style]"):
        minimum = _integer_attribute(column, "min", default=None)
        maximum = _integer_attribute(column, "max", default=None)
        if minimum is None or maximum is None or minimum < 1 or maximum < minimum:
            raise ValueError("Rango de columna OOXML inválido")
        styled_column_ranges.append((minimum, maximum))
    for xpath, attribute in (
        (f".//{{{MAIN}}}c[@s]", "s"),
        (f".//{{{MAIN}}}row[@s]", "s"),
        (f".//{{{MAIN}}}col[@style]", "style"),
    ):
        for element in root.findall(xpath):
            old_id = int(element.attrib[attribute])
            if old_id not in style_map:
                raise ValueError(f"Referencia de estilo sin remapeo: {old_id}")
            element.attrib[attribute] = str(style_map[old_id])
    for row in root.findall(f".//{{{MAIN}}}sheetData/{{{MAIN}}}row"):
        inherits_row_style = "s" in row.attrib
        for cell in row.findall(f"{{{MAIN}}}c"):
            if "s" in cell.attrib or inherits_row_style:
                continue
            column_number = _cell_column_number(cell.attrib["r"])
            if any(start <= column_number <= end for start, end in styled_column_ranges):
                continue
            cell.attrib["s"] = str(style_map[0])
    for element in root.iter():
        if "dxfId" in element.attrib:
            old_id = int(element.attrib["dxfId"])
            if old_id not in merger.dxf_map:
                raise ValueError(f"Referencia dxf sin remapeo: {old_id}")
            element.attrib["dxfId"] = str(merger.dxf_map[old_id])
    for name, table in tables.items():
        style_info = table.find(f"{{{MAIN}}}tableStyleInfo")
        if style_info is not None:
            old_name = style_info.attrib["name"]
            style_info.attrib["name"] = merger.table_style_name_map[old_name]
        rewritten_parts[name] = _xml_bytes(table)
    return _xml_bytes(root), merger.to_xml(), rewritten_parts


def _parse_worksheet(content: bytes) -> ET.Element:
    if not isinstance(content, bytes):
        raise TypeError("Worksheet OOXML debe ser bytes")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("Worksheet OOXML inválido") from error
    if root.tag != f"{{{MAIN}}}worksheet":
        raise ValueError("Raíz de worksheet OOXML inválida")
    return root


def _validate_unique_cell_references(root: ET.Element) -> None:
    seen: set[str] = set()
    for cell in root.findall(f".//{{{MAIN}}}c"):
        reference = cell.get("r")
        if not reference or re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", reference) is None:
            raise ValueError("Referencia de celda OOXML inválida")
        if reference in seen:
            raise ValueError(f"Celda OOXML duplicada: {reference}")
        seen.add(reference)


def _cell_column_number(reference: str) -> int:
    letters = reference.rstrip("0123456789")
    value = 0
    for character in letters:
        value = value * 26 + ord(character) - ord("A") + 1
    if value < 1 or value > 16_384:
        raise ValueError(f"Columna OOXML fuera de rango: {reference}")
    return value


def _shared_string_children(value: object) -> tuple[ET.Element, ...]:
    if isinstance(value, str):
        text = ET.Element(f"{{{MAIN}}}t")
        if value != value.strip():
            text.attrib[f"{{{XML_NS}}}space"] = "preserve"
        text.text = value
        return (text,)
    if isinstance(value, bytes):
        try:
            element = ET.fromstring(value)
        except ET.ParseError as error:
            raise ValueError("Shared string OOXML inválido") from error
    elif isinstance(value, ET.Element):
        element = value
    else:
        raise TypeError("Entrada de shared string inválida")
    if element.tag != f"{{{MAIN}}}si":
        raise ValueError("Shared string OOXML inválido")
    allowed = {
        f"{{{MAIN}}}t",
        f"{{{MAIN}}}r",
        f"{{{MAIN}}}rPh",
        f"{{{MAIN}}}phoneticPr",
    }
    if any(child.tag not in allowed for child in element):
        raise ValueError("Shared string OOXML contiene metadata no permitida")
    if element.text and element.text.strip():
        raise ValueError("Shared string OOXML ambiguo")
    return tuple(deepcopy(child) for child in element)


def _parse_style_sheet(content: bytes) -> ET.Element:
    if not isinstance(content, bytes):
        raise TypeError("Styles OOXML debe ser bytes")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("Styles OOXML inválidos") from error
    if root.tag != f"{{{MAIN}}}styleSheet":
        raise ValueError("Raíz de styles OOXML inválida")
    counts: dict[str, int] = {}
    for child in root:
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name in _STYLE_SECTIONS:
            counts[local_name] = counts.get(local_name, 0) + 1
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"Sección de styles duplicada: {duplicates}")
    for section_name, child_name in _STYLE_SECTION_CHILDREN.items():
        section = root.find(f"{{{MAIN}}}{section_name}")
        if section is not None and any(
            child.tag != f"{{{MAIN}}}{child_name}" for child in section
        ):
            raise ValueError(f"Sección de styles inválida: {section_name}")
    return root


def _section_children(
    root: ET.Element,
    section_name: str,
    child_name: str,
    *,
    required: bool = True,
) -> list[ET.Element]:
    section = root.find(f"{{{MAIN}}}{section_name}")
    if section is None:
        if required:
            raise ValueError(f"Sección de styles ausente: {section_name}")
        return []
    expected = f"{{{MAIN}}}{child_name}"
    if any(child.tag != expected for child in section):
        raise ValueError(f"Sección de styles inválida: {section_name}")
    return list(section)


def _insert_style_section(root: ET.Element, section_name: str) -> ET.Element:
    section = ET.Element(f"{{{MAIN}}}{section_name}", {"count": "0"})
    desired_order = _STYLE_ORDER[section_name]
    position = len(root)
    for index, child in enumerate(root):
        child_order = _STYLE_ORDER.get(child.tag.rsplit("}", 1)[-1], len(_STYLE_ORDER))
        if child_order > desired_order:
            position = index
            break
    root.insert(position, section)
    return section


def _element_key(element: ET.Element) -> tuple:
    text = element.text or ""
    if not text.strip():
        text = ""
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        text,
        tuple(_element_key(child) for child in element),
    )


def _num_fmts_by_id(root: ET.Element) -> dict[int, ET.Element]:
    result: dict[int, ET.Element] = {}
    for element in _section_children(
        root, "numFmts", "numFmt", required=False
    ):
        number = _integer_attribute(element, "numFmtId", default=None)
        format_code = element.get("formatCode")
        if number is None or format_code is None or number in result:
            raise ValueError("numFmt duplicado o inválido")
        result[number] = element
    return result


def _integer_attribute(
    element: ET.Element,
    name: str,
    *,
    default: int | None,
) -> int | None:
    value = element.get(name)
    if value is None:
        return default
    if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise ValueError(f"Atributo de estilo inválido: {name}")
    return int(value)


def _quotation_local_defined_names(
    package: XlsxPackage,
) -> tuple[LocalDefinedName, ...]:
    source_index = package.sheet_index("Quotation")
    try:
        workbook = ET.fromstring(package.parts["xl/workbook.xml"])
    except ET.ParseError as error:
        raise ValueError("Workbook OOXML inválido") from error
    sheets = workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet")
    containers = workbook.findall(f"{{{MAIN}}}definedNames")
    if len(containers) > 1:
        raise ValueError("definedNames OOXML duplicado")
    if not containers:
        return ()
    result: list[LocalDefinedName] = []
    seen: set[str] = set()
    for element in containers[0]:
        if element.tag != f"{{{MAIN}}}definedName":
            raise ValueError("Nombre definido OOXML inválido")
        local_raw = element.get("localSheetId")
        if local_raw is None:
            continue
        if re.fullmatch(r"0|[1-9][0-9]*", local_raw) is None:
            raise ValueError("localSheetId OOXML inválido")
        local_index = int(local_raw)
        if local_index >= len(sheets):
            raise ValueError("localSheetId OOXML fuera de rango")
        if local_index != source_index:
            continue
        name = element.get("name")
        if not name or name.casefold() in seen:
            raise ValueError("Nombre definido local duplicado o inválido")
        seen.add(name.casefold())
        attributes = {
            key: value
            for key, value in element.attrib.items()
            if key not in {"name", "localSheetId"}
        }
        result.append(
            LocalDefinedName(
                name=name,
                text=element.text or "",
                attributes=attributes,
            )
        )
    return tuple(result)


def _validate_passive_closure(closure: Mapping[str, bytes]) -> None:
    for name, content in closure.items():
        if not name.endswith(".rels"):
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError as error:
            raise ValueError(f"Relaciones OOXML inválidas: {name}") from error
        if root.tag != f"{{{PACKAGE_RELATIONSHIPS}}}Relationships":
            raise ValueError(f"Relaciones OOXML inválidas: {name}")
        for relationship in root:
            if relationship.tag != f"{{{PACKAGE_RELATIONSHIPS}}}Relationship":
                raise ValueError(f"Relaciones OOXML inválidas: {name}")
            relationship_type = relationship.get("Type")
            if not relationship_type:
                raise ValueError(f"Relación OOXML sin tipo: {name}")
            kind = relationship_type.rsplit("/", 1)[-1]
            if relationship_type not in _SAFE_CLOSURE_RELATIONSHIP_TYPES:
                raise ValueError(
                    f"Relación OOXML no permitida en Quotation: {kind}"
                )


def _reject_unsupported_active_content(package: XlsxPackage) -> None:
    lowered_names = tuple(name.casefold() for name in package.parts)
    for marker in _ACTIVE_PART_MARKERS:
        if any(name.startswith(marker) if marker.endswith("/") else marker in name for name in lowered_names):
            raise ValueError(f"Contenido activo no permitido: {marker}")
    content_types = package.parts.get("[Content_Types].xml", b"").lower()
    if any(
        marker in content_types
        for marker in (b"macroenabled", b"vbaproject", b"oleobject", b"activex")
    ):
        raise ValueError("Contenido activo no permitido por content type")
    for name, content in package.parts.items():
        if not name.endswith(".rels"):
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError as error:
            raise ValueError(f"Relaciones OOXML inválidas: {name}") from error
        for relationship in root:
            relationship_type = relationship.get("Type", "").casefold()
            if any(marker in relationship_type for marker in _ACTIVE_RELATIONSHIP_MARKERS):
                raise ValueError("Contenido activo no permitido por relación")


def _immutable_bytes_mapping(
    source: Mapping[str, bytes],
    label: str,
) -> Mapping[str, bytes]:
    copied: dict[str, bytes] = {}
    for name, content in source.items():
        if not isinstance(name, str) or not isinstance(content, bytes):
            raise TypeError(f"{label} de SheetAddition inválidas")
        copied[name] = bytes(content)
    return MappingProxyType(copied)


def _xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


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
