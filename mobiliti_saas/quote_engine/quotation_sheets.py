"""Filas canónicas y XML seguro para hojas auxiliares de cotización."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from io import BytesIO
from pathlib import Path
import posixpath
from types import MappingProxyType
import re
import struct
from typing import Mapping, Sequence
from urllib.parse import unquote
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .ooxml_package import (
    OFFICE_DOCUMENT_RELATIONSHIPS,
    PACKAGE_RELATIONSHIPS,
    XlsxPackage,
    relationship_owner,
    relationship_type_uris,
    resolve_internal_target,
    rewrite_relationship_targets,
    validate_part_name,
)


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
WORKSHEET_RELATIONSHIP = f"{OFFICE_DOCUMENT_RELATIONSHIPS}/worksheet"
WORKSHEET_RELATIONSHIP_TYPES = relationship_type_uris("worksheet")
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
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
_SAFE_CLOSURE_RELATIONSHIPS = (
    "drawing",
    "image",
    "comments",
    "vmlDrawing",
    "table",
    "hyperlink",
    "printerSettings",
)
_RELATIONSHIP_KIND_BY_TYPE = {
    relationship_type: kind
    for kind in _SAFE_CLOSURE_RELATIONSHIPS
    for relationship_type in relationship_type_uris(kind)
}
_UNSUPPORTED_RELATIONSHIP_KIND_BY_TYPE = {
    relationship_type: kind
    for kind in ("chart", "chartUserShapes")
    for relationship_type in relationship_type_uris(kind)
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
_ACTIVE_PATH_SEGMENTS = {
    "activex",
    "ctrlprops",
    "customui",
    "embeddings",
    "externallinks",
    "macrosheets",
    "vbaproject",
}
_EXECUTABLE_EXTENSIONS = {
    ".bat", ".cmd", ".com", ".cpl", ".dll", ".exe", ".hta", ".jar",
    ".js", ".jse", ".lnk", ".msi", ".msp", ".ps1", ".reg", ".scr",
    ".vbe", ".vbs",
}
_EXECUTABLE_CONTENT_TYPE_MARKERS = (
    "application/x-dosexec",
    "application/x-executable",
    "application/x-msdownload",
    "application/x-msi",
    "application/vnd.microsoft.portable-executable",
    "text/javascript",
)
_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_IMAGE_PROFILES = {
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".gif": ("image/gif", (b"GIF87a", b"GIF89a")),
    ".bmp": ("image/bmp", (b"BM",)),
    ".tif": ("image/tiff", (b"II*\x00", b"MM\x00*")),
    ".tiff": ("image/tiff", (b"II*\x00", b"MM\x00*")),
}
_DRAWING_MAIN = "http://schemas.openxmlformats.org/drawingml/2006/main"
_THEME_COLOR_ORDER = (
    "lt1", "dk1", "lt2", "dk2", "accent1", "accent2",
    "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink",
)
_DRAWING_COLOR_TRANSFORMS = {
    "alpha", "alphaMod", "alphaOff", "blue", "blueMod", "blueOff",
    "comp", "gamma", "gray", "green", "greenMod", "greenOff", "hue",
    "hueMod", "hueOff", "inv", "invGamma", "lum", "lumMod", "lumOff",
    "red", "redMod", "redOff", "sat", "satMod", "satOff", "shade", "tint",
}


@dataclass(frozen=True)
class _ResolvedTheme:
    colors: Mapping[str, str]
    major_latin: str
    minor_latin: str


@dataclass(frozen=True)
class _TableIdentity:
    part_name: str
    table_id: int
    name: str
    display_name: str


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
    replacement_content_types: Mapping[str, str] = field(default_factory=dict)
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
        for name in (*additions, *replacements):
            validate_part_name(name)
        content_types = _immutable_content_types(
            self.content_types,
            additions,
            "adiciones",
            allow_legacy_quotation_data=self.name == "Quotation_Data",
        )
        replacement_content_types = _immutable_content_types(
            self.replacement_content_types,
            replacements,
            "reemplazos",
        )
        if (
            not isinstance(self.relationship_type, str)
            or self.relationship_type not in WORKSHEET_RELATIONSHIP_TYPES
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
        object.__setattr__(
            self,
            "replacement_content_types",
            MappingProxyType(replacement_content_types),
        )


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

    root, namespace_prefixes = _parse_worksheet_document(sheet_xml)
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
    return _xml_bytes(root, namespace_prefixes)


@dataclass
class StyleTableMerger:
    """Fusiona estilos por dependencia sin tocar índices oficiales existentes."""

    root: ET.Element
    component_maps: dict[str, dict[tuple, int]]
    namespace_prefixes: dict[str, str] = field(default_factory=dict)
    font_map: dict[int, int] = field(default_factory=dict)
    dxf_map: dict[int, int] = field(default_factory=dict)
    table_style_name_map: dict[str, str] = field(default_factory=dict)
    _num_fmt_map: dict[int, int] = field(default_factory=dict, repr=False)
    _indexed_maps: dict[str, dict[int, int]] = field(default_factory=dict, repr=False)
    _style_xf_map: dict[int, int] = field(default_factory=dict, repr=False)
    _source: ET.Element | None = field(default=None, repr=False)

    @classmethod
    def from_xml(cls, target_styles: bytes) -> "StyleTableMerger":
        root, namespace_prefixes = _parse_style_sheet_document(target_styles)
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
        return cls(
            root=root,
            component_maps=maps,
            namespace_prefixes=namespace_prefixes,
        )

    def merge_referenced_styles(
        self,
        source_styles: bytes,
        style_ids: set[int],
        *,
        dxf_ids: set[int] | None = None,
        table_style_names: set[str] | None = None,
        font_ids: set[int] | None = None,
        source_theme: bytes | None = None,
    ) -> dict[int, int]:
        """Fusiona de forma transaccional; un preflight fallido no deja residuos."""

        working = deepcopy(self)
        mapping = working._merge_referenced_styles_in_place(
            source_styles,
            style_ids,
            dxf_ids=dxf_ids,
            table_style_names=table_style_names,
            font_ids=font_ids,
            source_theme=source_theme,
        )
        for definition in fields(self):
            setattr(self, definition.name, getattr(working, definition.name))
        return mapping

    def _merge_referenced_styles_in_place(
        self,
        source_styles: bytes,
        style_ids: set[int],
        *,
        dxf_ids: set[int] | None,
        table_style_names: set[str] | None,
        font_ids: set[int] | None,
        source_theme: bytes | None,
    ) -> dict[int, int]:
        source = _parse_style_sheet(source_styles)
        resolved_theme = _resolved_theme_or_none(source_theme)
        _materialize_spreadsheet_theme_refs(source, resolved_theme)
        self._source = source
        self._num_fmt_map = {}
        self._indexed_maps = {name: {} for name in ("fonts", "fills", "borders")}
        self._style_xf_map = {}
        self.dxf_map = {}
        self.table_style_name_map = {}
        self.font_map = {}

        mapping: dict[int, int] = {}
        for style_id in sorted(style_ids):
            if type(style_id) is not int or style_id < 0:
                raise ValueError("Referencia de estilo inválida")
            mapping[style_id] = self._merge_cell_xf(style_id)
        for dxf_id in sorted(dxf_ids or ()):
            self._merge_dxf(dxf_id)
        for font_id in sorted(font_ids or ()):
            if type(font_id) is not int or font_id < 0:
                raise ValueError("Referencia de fuente fonética inválida")
            self._merge_indexed_component("fonts", "font", font_id)
        for style_name in sorted(table_style_names or ()):
            self._merge_table_style(style_name)
        self.font_map = dict(self._indexed_maps["fonts"])
        return mapping

    def to_xml(self) -> bytes:
        for section_name in _STYLE_SECTIONS:
            section = self.root.find(f"{{{MAIN}}}{section_name}")
            if section is not None:
                section.attrib["count"] = str(len(section))
        return _xml_bytes(self.root, self.namespace_prefixes)

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
            source_name = clone.get("name")
            if not source_name:
                raise ValueError("cellStyle sin nombre")
            semantic_key = _element_key_without_attributes(
                clone, {"name", "builtinId"}
            )
            equivalent = next(
                (
                    item
                    for item in target_section
                    if item.get("name", "").casefold() == source_name.casefold()
                    and _element_key_without_attributes(
                        item, {"name", "builtinId"}
                    )
                    == semantic_key
                ),
                None,
            )
            if equivalent is not None:
                continue
            generated_prefix = f"{source_name} Quotation ".casefold()
            reusable = next(
                (
                    item
                    for item in target_section
                    if item.get("name", "").casefold().startswith(generated_prefix)
                    and _element_key_without_attributes(
                        item, {"name", "builtinId"}
                    )
                    == semantic_key
                ),
                None,
            )
            if reusable is not None:
                continue
            used_names = {
                item.get("name", "").casefold() for item in target_section
            }
            if source_name.casefold() in used_names:
                suffix = 1
                candidate = f"{source_name} Quotation {suffix}"
                while candidate.casefold() in used_names:
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
        semantic_key = _element_key_without_attributes(clone, {"name"})
        same_name = [
            item
            for item in target_section
            if item.get("name", "").casefold() == style_name.casefold()
        ]
        equivalent = next(
            (
                item
                for item in same_name
                if _element_key_without_attributes(item, {"name"}) == semantic_key
            ),
            None,
        )
        generated_prefix = f"{style_name}_Quotation_".casefold()
        renamed_equivalent = next(
            (
                item
                for item in target_section
                if item.get("name", "").casefold().startswith(generated_prefix)
                and _element_key_without_attributes(item, {"name"}) == semantic_key
            ),
            None,
        )
        if equivalent is not None:
            target_name = equivalent.attrib["name"]
        elif renamed_equivalent is not None:
            target_name = renamed_equivalent.attrib["name"]
        else:
            used_names = {
                item.get("name", "").casefold() for item in target_section
            }
            target_name = style_name
            suffix = 1
            while target_name.casefold() in used_names:
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
    *,
    source_theme: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Fusiona y remapea refs de celdas, filas, columnas y CF estándar."""

    remapped_sheet, merged_styles, _parts, _table_names = _remap_source_styles_with_parts(
        sheet_xml,
        source_styles,
        target_styles,
        {},
        source_theme=source_theme,
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
    _validate_passive_closure(source_package, closure, source_sheet)
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
    source_theme_part = source_package.workbook_related_part("theme")
    source_theme = (
        source_package.parts[source_theme_part]
        if source_theme_part is not None
        else None
    )
    sheet_xml, styles_xml, rewritten, table_name_map = _remap_source_styles_with_parts(
        sheet_xml,
        source_package.parts[source_styles_part],
        destination_package.parts[destination_styles_part],
        rewritten,
        source_theme=source_theme,
        destination_package=destination_package,
    )
    rewritten[target_sheet] = sheet_xml

    source_content_types = source_package.content_types_for(set(closure))
    allocated_content_types = {
        allocation[source_name]: content_type
        for source_name, content_type in source_content_types.items()
    }
    defined_names = _quotation_local_defined_names(
        source_package, table_name_map=table_name_map
    )
    return SheetAddition(
        name="Quotation",
        state=source_state,
        xml=sheet_xml,
        parts=rewritten,
        replacements={destination_styles_part: styles_xml},
        content_types=allocated_content_types,
        replacement_content_types=destination_package.content_types_for(
            {destination_styles_part}
        ),
        sheet_part=target_sheet,
        relationship_type=WORKSHEET_RELATIONSHIP,
        defined_names=defined_names,
    )


def _remap_source_styles_with_parts(
    sheet_xml: bytes,
    source_styles: bytes,
    target_styles: bytes,
    related_parts: Mapping[str, bytes],
    *,
    source_theme: bytes | None = None,
    destination_package: XlsxPackage | None = None,
) -> tuple[bytes, bytes, dict[str, bytes], dict[str, str]]:
    root, worksheet_namespaces = _parse_worksheet_document(sheet_xml)
    resolved_theme = _resolved_theme_or_none(source_theme)
    _materialize_spreadsheet_theme_refs(root, resolved_theme)
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
    phonetic_font_ids: set[int] = set()
    for phonetic in root.findall(f".//{{{MAIN}}}phoneticPr"):
        font_id = _integer_attribute(phonetic, "fontId", default=None)
        if font_id is None:
            raise ValueError("Referencia de fuente fonética inválida")
        phonetic_font_ids.add(font_id)

    rewritten_parts = _materialize_related_theme_refs(
        related_parts, resolved_theme
    )
    tables: dict[str, tuple[ET.Element, dict[str, str]]] = {}
    table_style_names: set[str] = set()
    for name, content in rewritten_parts.items():
        if not name.startswith("xl/tables/") or name.endswith(".rels"):
            continue
        try:
            table, table_namespaces = _parse_xml_document(
                content, f"Tabla OOXML inválida: {name}"
            )
        except ValueError as error:
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
        for element in table.iter():
            for attribute in element.attrib:
                if _is_dxf_id_attribute(attribute):
                    dxf_id = _integer_attribute(element, attribute, default=None)
                    if dxf_id is None:
                        raise ValueError("Referencia dxf de tabla inválida")
                    dxf_ids.add(dxf_id)
        tables[name] = (table, table_namespaces)

    table_name_map = _remap_workbook_global_tables(
        root,
        tables,
        destination_package,
    )

    merger = StyleTableMerger.from_xml(target_styles)
    style_map = merger.merge_referenced_styles(
        source_styles,
        style_ids,
        dxf_ids=dxf_ids,
        table_style_names=table_style_names,
        font_ids=phonetic_font_ids,
        source_theme=source_theme,
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
    for phonetic in root.findall(f".//{{{MAIN}}}phoneticPr"):
        old_id = int(phonetic.attrib["fontId"])
        if old_id not in merger.font_map:
            raise ValueError(f"Referencia de fuente fonética sin remapeo: {old_id}")
        phonetic.attrib["fontId"] = str(merger.font_map[old_id])
    for name, (table, table_namespaces) in tables.items():
        style_info = table.find(f"{{{MAIN}}}tableStyleInfo")
        if style_info is not None:
            old_name = style_info.attrib["name"]
            style_info.attrib["name"] = merger.table_style_name_map[old_name]
        for element in table.iter():
            for attribute in tuple(element.attrib):
                if not _is_dxf_id_attribute(attribute):
                    continue
                old_id = int(element.attrib[attribute])
                if old_id not in merger.dxf_map:
                    raise ValueError(
                        f"Referencia dxf de tabla sin remapeo: {old_id}"
                    )
                element.attrib[attribute] = str(merger.dxf_map[old_id])
        rewritten_parts[name] = _xml_bytes(table, table_namespaces)
    return (
        _xml_bytes(root, worksheet_namespaces),
        merger.to_xml(),
        rewritten_parts,
        table_name_map,
    )


def _parse_worksheet(content: bytes) -> ET.Element:
    return _parse_worksheet_document(content)[0]


def _parse_worksheet_document(content: bytes) -> tuple[ET.Element, dict[str, str]]:
    if not isinstance(content, bytes):
        raise TypeError("Worksheet OOXML debe ser bytes")
    root, namespace_prefixes = _parse_xml_document(
        content, "Worksheet OOXML inválido"
    )
    if root.tag != f"{{{MAIN}}}worksheet":
        raise ValueError("Raíz de worksheet OOXML inválida")
    return root, namespace_prefixes


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
    if element.text and element.text.strip():
        raise ValueError("Shared string OOXML ambiguo")
    _validate_ct_rst(element)
    return tuple(deepcopy(child) for child in element)


def _validate_ct_rst(element: ET.Element) -> None:
    direct_text = f"{{{MAIN}}}t"
    rich_run = f"{{{MAIN}}}r"
    phonetic_run = f"{{{MAIN}}}rPh"
    phonetic_properties = f"{{{MAIN}}}phoneticPr"
    children = list(element)
    index = 0
    base_length = 0
    if index < len(children) and children[index].tag == direct_text:
        base_length = len(_validate_text_node(children[index]))
        index += 1
        if index < len(children) and children[index].tag == rich_run:
            raise ValueError("CT_Rst no permite mezclar texto directo y rich runs")
    else:
        while index < len(children) and children[index].tag == rich_run:
            base_length += len(_validate_rich_run(children[index]))
            index += 1
    while index < len(children) and children[index].tag == phonetic_run:
        _validate_phonetic_run(children[index], base_length)
        index += 1
    if index < len(children) and children[index].tag == phonetic_properties:
        _validate_phonetic_properties(children[index])
        index += 1
    if index != len(children):
        raise ValueError("Orden o cardinalidad CT_Rst de shared string inválido")
    for child in children:
        if child.tail and child.tail.strip():
            raise ValueError("Shared string OOXML ambiguo")


def _validate_text_node(element: ET.Element) -> str:
    if list(element) or any(
        name != f"{{{XML_NS}}}space" for name in element.attrib
    ):
        raise ValueError("Texto CT_Rst inválido")
    if element.get(f"{{{XML_NS}}}space") not in {None, "default", "preserve"}:
        raise ValueError("xml:space de shared string inválido")
    return element.text or ""


def _validate_rich_run(element: ET.Element) -> str:
    children = list(element)
    text_tag = f"{{{MAIN}}}t"
    properties_tag = f"{{{MAIN}}}rPr"
    if len(children) not in {1, 2}:
        raise ValueError("Cardinalidad de rich run inválida")
    text_index = 0
    if children[0].tag == properties_tag:
        _validate_run_properties(children[0])
        text_index = 1
    if text_index >= len(children) or children[text_index].tag != text_tag:
        raise ValueError("Orden de rich run inválido")
    if text_index != len(children) - 1:
        raise ValueError("Cardinalidad de rich run inválida")
    return _validate_text_node(children[text_index])


def _validate_run_properties(element: ET.Element) -> None:
    allowed = {
        "rFont", "charset", "family", "b", "i", "strike", "outline",
        "shadow", "condense", "extend", "color", "sz", "u", "vertAlign",
        "scheme",
    }
    seen: set[str] = set()
    for child in element:
        if not child.tag.startswith(f"{{{MAIN}}}"):
            raise ValueError("Propiedad de rich run fuera de namespace")
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name not in allowed or local_name in seen or list(child):
            raise ValueError("Propiedad de rich run inválida")
        seen.add(local_name)


def _validate_phonetic_run(element: ET.Element, base_length: int) -> None:
    if set(element.attrib) != {"sb", "eb"}:
        raise ValueError("Atributos de fonética inválidos")
    start = _integer_attribute(element, "sb", default=None)
    end = _integer_attribute(element, "eb", default=None)
    children = list(element)
    if (
        start is None
        or end is None
        or start >= end
        or end > base_length
        or len(children) != 1
        or children[0].tag != f"{{{MAIN}}}t"
    ):
        raise ValueError("Límites de fonética inválidos")
    _validate_text_node(children[0])


def _validate_phonetic_properties(element: ET.Element) -> None:
    allowed = {"fontId", "type", "alignment"}
    if list(element) or not set(element.attrib).issubset(allowed):
        raise ValueError("phoneticPr de shared string inválido")
    font_id = _integer_attribute(element, "fontId", default=None)
    if font_id is None:
        raise ValueError("phoneticPr sin fontId")


def _parse_style_sheet(content: bytes) -> ET.Element:
    return _parse_style_sheet_document(content)[0]


def _parse_style_sheet_document(
    content: bytes,
) -> tuple[ET.Element, dict[str, str]]:
    if not isinstance(content, bytes):
        raise TypeError("Styles OOXML debe ser bytes")
    root, namespace_prefixes = _parse_xml_document(content, "Styles OOXML inválidos")
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
    return root, namespace_prefixes


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


def _element_key_without_attributes(
    element: ET.Element,
    excluded: set[str],
) -> tuple:
    text = element.text or ""
    if not text.strip():
        text = ""
    return (
        element.tag,
        tuple(
            sorted(
                (name, value)
                for name, value in element.attrib.items()
                if name not in excluded
            )
        ),
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


def _is_dxf_id_attribute(name: str) -> bool:
    return name.rsplit("}", 1)[-1].casefold().endswith("dxfid")


def _resolved_theme_or_none(content: bytes | None) -> _ResolvedTheme | None:
    if content is None:
        return None
    root, _prefixes = _parse_xml_document(content, "Tema OOXML inválido")
    if root.tag != f"{{{_DRAWING_MAIN}}}theme":
        raise ValueError("Raíz de tema OOXML inválida")
    theme_elements = root.findall(f"{{{_DRAWING_MAIN}}}themeElements")
    if len(theme_elements) != 1:
        raise ValueError("themeElements OOXML inválido")
    color_schemes = theme_elements[0].findall(f"{{{_DRAWING_MAIN}}}clrScheme")
    font_schemes = theme_elements[0].findall(f"{{{_DRAWING_MAIN}}}fontScheme")
    if len(color_schemes) != 1 or len(font_schemes) != 1:
        raise ValueError("Esquema de tema OOXML inválido")
    colors: dict[str, str] = {}
    for name in _THEME_COLOR_ORDER:
        slots = color_schemes[0].findall(f"{{{_DRAWING_MAIN}}}{name}")
        if len(slots) != 1 or len(slots[0]) != 1:
            raise ValueError(f"Color de tema ausente o ambiguo: {name}")
        color = slots[0][0]
        if color.tag == f"{{{_DRAWING_MAIN}}}srgbClr":
            value = color.get("val", "")
        elif color.tag == f"{{{_DRAWING_MAIN}}}sysClr":
            value = color.get("lastClr", "")
        else:
            raise ValueError(f"Color de tema no soportado: {name}")
        if re.fullmatch(r"[0-9A-Fa-f]{6}", value) is None:
            raise ValueError(f"Color de tema inválido: {name}")
        colors[name] = value.upper()
    colors.update(
        {
            "bg1": colors["lt1"],
            "tx1": colors["dk1"],
            "bg2": colors["lt2"],
            "tx2": colors["dk2"],
        }
    )
    major = _theme_latin_typeface(font_schemes[0], "majorFont")
    minor = _theme_latin_typeface(font_schemes[0], "minorFont")
    return _ResolvedTheme(
        colors=MappingProxyType(colors),
        major_latin=major,
        minor_latin=minor,
    )


def _theme_latin_typeface(font_scheme: ET.Element, kind: str) -> str:
    containers = font_scheme.findall(f"{{{_DRAWING_MAIN}}}{kind}")
    if len(containers) != 1:
        raise ValueError(f"Fuente de tema ausente: {kind}")
    latin = containers[0].findall(f"{{{_DRAWING_MAIN}}}latin")
    if len(latin) != 1:
        raise ValueError(f"Fuente latina de tema ausente: {kind}")
    typeface = latin[0].get("typeface", "")
    if not typeface or any(ord(character) < 32 for character in typeface):
        raise ValueError(f"Typeface de tema inválido: {kind}")
    return typeface


def _materialize_spreadsheet_theme_refs(
    root: ET.Element,
    theme: _ResolvedTheme | None,
) -> bool:
    changed = False
    color_tags = {
        f"{{{MAIN}}}color",
        f"{{{MAIN}}}fgColor",
        f"{{{MAIN}}}bgColor",
    }
    for element in root.iter():
        if "theme" not in element.attrib:
            continue
        if element.tag not in color_tags:
            raise ValueError("Referencia de tema SpreadsheetML no soportada")
        if theme is None:
            raise ValueError("Tema fuente ausente para referencia SpreadsheetML")
        if set(element.attrib) & {"rgb", "indexed", "auto"}:
            raise ValueError("Color de tema SpreadsheetML ambiguo")
        theme_id = _integer_attribute(element, "theme", default=None)
        if theme_id is None or theme_id >= len(_THEME_COLOR_ORDER):
            raise ValueError("Índice de tema SpreadsheetML fuera de rango")
        tint_raw = element.get("tint", "0")
        try:
            tint = Decimal(tint_raw)
        except InvalidOperation as error:
            raise ValueError("Tint de tema SpreadsheetML inválido") from error
        if not tint.is_finite() or tint < -1 or tint > 1:
            raise ValueError("Tint de tema SpreadsheetML fuera de rango")
        rgb = _apply_tint(theme.colors[_THEME_COLOR_ORDER[theme_id]], tint)
        element.attrib.pop("theme", None)
        element.attrib.pop("tint", None)
        element.attrib["rgb"] = "FF" + rgb
        changed = True
    for container in root.iter():
        if container.tag not in {f"{{{MAIN}}}font", f"{{{MAIN}}}rPr"}:
            continue
        schemes = [
            child
            for child in container
            if child.tag == f"{{{MAIN}}}scheme"
        ]
        if len(schemes) > 1:
            raise ValueError("Esquema de fuente SpreadsheetML ambiguo")
        if not schemes:
            continue
        scheme = schemes[0].get("val")
        if scheme not in {"major", "minor"}:
            raise ValueError(f"Esquema de fuente no soportado: {scheme}")
        if theme is None:
            raise ValueError("Tema fuente ausente para esquema de fuente")
        typeface = theme.major_latin if scheme == "major" else theme.minor_latin
        name_tag = (
            f"{{{MAIN}}}name"
            if container.tag == f"{{{MAIN}}}font"
            else f"{{{MAIN}}}rFont"
        )
        names = [child for child in container if child.tag == name_tag]
        if len(names) > 1:
            raise ValueError("Nombre de fuente SpreadsheetML ambiguo")
        if names:
            names[0].attrib["val"] = typeface
        else:
            container.insert(0, ET.Element(name_tag, {"val": typeface}))
        container.remove(schemes[0])
        changed = True
    return changed


def _apply_tint(rgb: str, tint: Decimal) -> str:
    result: list[int] = []
    for offset in (0, 2, 4):
        channel = Decimal(int(rgb[offset : offset + 2], 16))
        if tint < 0:
            adjusted = channel * (Decimal(1) + tint)
        else:
            adjusted = channel * (Decimal(1) - tint) + Decimal(255) * tint
        result.append(
            int(adjusted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        )
    return "".join(f"{channel:02X}" for channel in result)


def _materialize_related_theme_refs(
    related_parts: Mapping[str, bytes],
    theme: _ResolvedTheme | None,
) -> dict[str, bytes]:
    rewritten = {name: bytes(content) for name, content in related_parts.items()}
    for name, content in tuple(rewritten.items()):
        if name.endswith(".rels") or not name.casefold().endswith(".xml"):
            continue
        root, prefixes = _parse_xml_document(
            content, f"Parte XML relacionada inválida: {name}"
        )
        changed = _materialize_spreadsheet_theme_refs(root, theme)
        changed = _materialize_drawing_theme_refs(root, theme) or changed
        if changed:
            rewritten[name] = _xml_bytes(root, prefixes)
    return rewritten


def _materialize_drawing_theme_refs(
    root: ET.Element,
    theme: _ResolvedTheme | None,
) -> bool:
    changed = False
    for element in root.iter():
        typeface = element.get("typeface")
        if typeface and typeface.startswith("+"):
            if theme is None:
                raise ValueError("Tema fuente ausente para typeface DrawingML")
            replacements = {
                "+mj-lt": theme.major_latin,
                "+mn-lt": theme.minor_latin,
            }
            if typeface not in replacements:
                raise ValueError(f"Typeface de tema no soportado: {typeface}")
            element.attrib["typeface"] = replacements[typeface]
            changed = True
        if element.tag != f"{{{_DRAWING_MAIN}}}schemeClr":
            continue
        if theme is None:
            raise ValueError("Tema fuente ausente para schemeClr")
        value = element.get("val", "")
        rgb = theme.colors.get(value)
        if rgb is None:
            raise ValueError(f"schemeClr de tema no soportado: {value}")
        if set(element.attrib) != {"val"}:
            raise ValueError("schemeClr ambiguo")
        for transform in element:
            if (
                not transform.tag.startswith(f"{{{_DRAWING_MAIN}}}")
                or transform.tag.rsplit("}", 1)[-1]
                not in _DRAWING_COLOR_TRANSFORMS
            ):
                raise ValueError("Transformación schemeClr no soportada")
        element.tag = f"{{{_DRAWING_MAIN}}}srgbClr"
        element.attrib["val"] = rgb
        changed = True
    return changed


def _remap_workbook_global_tables(
    worksheet: ET.Element,
    source_tables: Mapping[str, tuple[ET.Element, dict[str, str]]],
    destination_package: XlsxPackage | None,
) -> dict[str, str]:
    if not source_tables:
        return {}
    used_ids: set[int] = set()
    used_names: dict[str, str] = {}
    if destination_package is not None:
        for identity in _destination_table_identities(destination_package):
            if identity.table_id in used_ids:
                raise ValueError(f"ID de tabla destino duplicado: {identity.table_id}")
            used_ids.add(identity.table_id)
            for value in {identity.name.casefold(), identity.display_name.casefold()}:
                previous = used_names.get(value)
                if previous is not None and previous != identity.part_name:
                    raise ValueError(f"Nombre de tabla destino duplicado: {value}")
                used_names[value] = identity.part_name

    source_identities: list[_TableIdentity] = []
    source_ids: set[int] = set()
    source_names: dict[str, str] = {}
    for part_name in sorted(source_tables):
        table, _prefixes = source_tables[part_name]
        identity = _table_identity(part_name, table)
        if identity.table_id in source_ids:
            raise ValueError(f"ID de tabla fuente duplicado: {identity.table_id}")
        source_ids.add(identity.table_id)
        for value in {identity.name.casefold(), identity.display_name.casefold()}:
            previous = source_names.get(value)
            if previous is not None and previous != identity.part_name:
                raise ValueError(f"Nombre de tabla fuente duplicado: {value}")
            source_names[value] = identity.part_name
        source_identities.append(identity)

    renamed: dict[str, str] = {}
    for identity in source_identities:
        table = source_tables[identity.part_name][0]
        target_id = identity.table_id
        if target_id in used_ids:
            target_id = 1
            while target_id in used_ids:
                target_id += 1
        table.attrib["id"] = str(target_id)
        used_ids.add(target_id)

        original_tokens = {
            identity.name.casefold(), identity.display_name.casefold()
        }
        collision = bool(original_tokens & set(used_names))
        target_name = identity.name
        target_display_name = identity.display_name
        if collision:
            if identity.name.casefold() != identity.display_name.casefold():
                raise ValueError(
                    "Identidad de tabla ambigua: name/displayName no se pueden reasignar"
                )
            suffix = 1
            while True:
                suffix_text = f"_Quotation_{suffix}"
                available = 255 - len(suffix_text)
                candidate = identity.name[:available] + suffix_text
                _validate_table_name(candidate, "name")
                if candidate.casefold() not in used_names:
                    break
                suffix += 1
            target_name = candidate
            target_display_name = candidate
            renamed[identity.name] = candidate
            if identity.display_name != identity.name:
                renamed[identity.display_name] = candidate
        table.attrib["name"] = target_name
        table.attrib["displayName"] = target_display_name
        for value in {target_name.casefold(), target_display_name.casefold()}:
            used_names[value] = identity.part_name

    if renamed:
        worksheet_formula_names = {"f", "formula", "formula1", "formula2"}
        for formula in worksheet.iter():
            if (
                formula.tag.rsplit("}", 1)[-1] not in worksheet_formula_names
                or formula.text is None
            ):
                continue
            formula.text = _rewrite_structured_references(
                formula.text, renamed, "fórmula de worksheet"
            )
        formula_tags = {
            f"{{{MAIN}}}calculatedColumnFormula",
            f"{{{MAIN}}}totalsRowFormula",
        }
        for part_name, (table, _prefixes) in source_tables.items():
            for formula in table.iter():
                if formula.tag not in formula_tags or formula.text is None:
                    continue
                formula.text = _rewrite_structured_references(
                    formula.text, renamed, f"fórmula de tabla {part_name}"
                )
    return renamed


def _destination_table_identities(
    package: XlsxPackage,
) -> tuple[_TableIdentity, ...]:
    identities: list[_TableIdentity] = []
    for part_name in sorted(package.parts):
        if not part_name.startswith("xl/tables/") or not part_name.endswith(".xml"):
            continue
        content_type = package.content_types_for({part_name})[part_name]
        if content_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml":
            raise ValueError(f"Content type de tabla destino inválido: {part_name}")
        try:
            table = ET.fromstring(package.parts[part_name])
        except ET.ParseError as error:
            raise ValueError(f"Tabla destino OOXML inválida: {part_name}") from error
        if table.tag != f"{{{MAIN}}}table":
            raise ValueError(f"Raíz de tabla destino inválida: {part_name}")
        identities.append(_table_identity(part_name, table))
    return tuple(identities)


def _table_identity(part_name: str, table: ET.Element) -> _TableIdentity:
    table_id = _integer_attribute(table, "id", default=None)
    if table_id is None or table_id < 1:
        raise ValueError(f"ID de tabla inválido: {part_name}")
    name = table.get("name", "")
    display_name = table.get("displayName", "")
    _validate_table_name(name, "name")
    _validate_table_name(display_name, "displayName")
    return _TableIdentity(
        part_name=part_name,
        table_id=table_id,
        name=name,
        display_name=display_name,
    )


def _validate_table_name(value: str, attribute: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) < 1
        or len(value) > 255
        or re.fullmatch(r"[A-Za-z_\\][A-Za-z0-9_.\\]*", value) is None
        or re.fullmatch(r"[A-Za-z]{1,3}[1-9][0-9]{0,6}", value) is not None
        or re.fullmatch(r"R[1-9][0-9]*C[1-9][0-9]*", value, re.IGNORECASE)
        is not None
    ):
        raise ValueError(f"{attribute} de tabla inválido: {value!r}")


def _rewrite_structured_references(
    formula: str,
    renamed: Mapping[str, str],
    context: str,
) -> str:
    if not renamed or not formula:
        return formula
    segments: list[tuple[bool, str]] = []
    start = 0
    index = 0
    in_literal = False
    while index < len(formula):
        if formula[index] != '"':
            index += 1
            continue
        if in_literal and index + 1 < len(formula) and formula[index + 1] == '"':
            index += 2
            continue
        segments.append((in_literal, formula[start:index]))
        segments.append((False, '"'))
        in_literal = not in_literal
        index += 1
        start = index
    if in_literal:
        raise ValueError(f"Literal de fórmula sin cerrar: {context}")
    segments.append((False, formula[start:]))

    patterns = [
        (
            re.compile(
                rf"(?<![A-Za-z0-9_.])(?P<quoted>'?){re.escape(old)}(?P=quoted)(?=\s*\[)",
                re.IGNORECASE,
            ),
            new,
        )
        for old, new in sorted(renamed.items(), key=lambda item: -len(item[0]))
    ]
    output: list[str] = []
    literal_state = False
    for _is_literal, segment in segments:
        if segment == '"':
            literal_state = not literal_state
            output.append(segment)
            continue
        current = segment
        for pattern, replacement in patterns:
            if literal_state and pattern.search(current):
                raise ValueError(
                    f"Referencia estructurada dentro de literal no soportada: {context}"
                )
            if not literal_state:
                current = pattern.sub(
                    lambda match: (
                        f"'{replacement}'"
                        if match.group("quoted")
                        else replacement
                    ),
                    current,
                )
        output.append(current)
    return "".join(output)


def _quotation_local_defined_names(
    package: XlsxPackage,
    *,
    table_name_map: Mapping[str, str] | None = None,
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
                text=_rewrite_structured_references(
                    element.text or "",
                    table_name_map or {},
                    f"nombre definido {name}",
                ),
                attributes=attributes,
            )
        )
    return tuple(result)


def _validate_passive_closure(
    package: XlsxPackage,
    closure: Mapping[str, bytes],
    source_sheet: str,
) -> None:
    validate_part_name(source_sheet)
    if not source_sheet.startswith("xl/worksheets/") or not source_sheet.endswith(".xml"):
        raise ValueError("Ruta de worksheet Quotation inválida")
    sheet_content_type = package.content_types_for({source_sheet})[source_sheet]
    if sheet_content_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml":
        raise ValueError("Content type de worksheet Quotation inválido")
    _parse_worksheet(package.parts[source_sheet])
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
            unsupported = _UNSUPPORTED_RELATIONSHIP_KIND_BY_TYPE.get(
                relationship_type
            )
            if unsupported is not None:
                raise ValueError(
                    f"Transformación de relación {unsupported} no soportada"
                )
            kind = _RELATIONSHIP_KIND_BY_TYPE.get(relationship_type)
            if kind is None:
                raise ValueError(
                    "Relación OOXML no permitida en Quotation: "
                    + relationship_type.rsplit("/", 1)[-1]
                )
            owner = relationship_owner(name)
            if owner is None:
                raise ValueError("Relación de clausura sin propietario")
            target_mode = relationship.get("TargetMode", "").casefold()
            target = relationship.get("Target", "")
            if kind == "hyperlink":
                if target_mode != "external":
                    raise ValueError("TargetMode de hyperlink externo inválido")
                _validate_external_hyperlink(target)
                continue
            if target_mode == "external":
                raise ValueError(f"TargetMode externo no permitido para {kind}")
            if target_mode not in {"", "internal"}:
                raise ValueError(f"TargetMode inválido para {kind}")
            resolved = resolve_internal_target(owner, target)
            if resolved not in closure:
                raise ValueError(f"Clausura OOXML sin destino: {resolved}")
            content_type = package.content_types_for({resolved})[resolved]
            _validate_relationship_payload(
                kind,
                owner,
                resolved,
                closure[resolved],
                content_type,
            )


def _validate_external_hyperlink(target: str) -> None:
    if not target or any(ord(character) < 32 for character in target):
        raise ValueError("Hyperlink externo inválido")
    parsed = urlsplit(unquote(target))
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https", "mailto"}:
        raise ValueError(f"Esquema de hyperlink externo no permitido: {scheme}")
    if scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError("Hyperlink HTTP sin host")
    if scheme == "mailto" and "@" not in parsed.path:
        raise ValueError("Hyperlink mailto inválido")


def _validate_relationship_payload(
    kind: str,
    owner: str,
    target: str,
    content: bytes,
    content_type: str,
) -> None:
    worksheet_owner = owner.startswith("xl/worksheets/")
    drawing_owner = owner.startswith("xl/drawings/")
    if kind == "drawing":
        _require_part_profile(
            worksheet_owner,
            target,
            "xl/drawings/",
            ".xml",
            content_type,
            "application/vnd.openxmlformats-officedocument.drawing+xml",
            kind,
        )
        _validate_xml_root(content, f"{{{_DRAWING}}}wsDr", kind)
        return
    if kind == "image":
        if not drawing_owner or not target.startswith("xl/media/"):
            raise ValueError("Ruta de image no permitida")
        extension = posixpath.splitext(target)[1].casefold()
        profile = _IMAGE_PROFILES.get(extension)
        if profile is None:
            raise ValueError(f"Formato de imagen no soportado: {extension}")
        expected_type, signatures = profile
        if content_type.casefold() != expected_type:
            raise ValueError("Content type de imagen no coincide con extensión")
        if not any(content.startswith(signature) for signature in signatures):
            raise ValueError("Firma binaria de imagen no coincide con extensión")
        return
    if kind == "comments":
        _require_part_profile(
            worksheet_owner,
            target,
            "xl/comments/",
            ".xml",
            content_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml",
            kind,
        )
        _validate_xml_root(content, f"{{{MAIN}}}comments", kind)
        return
    if kind == "vmlDrawing":
        _require_part_profile(
            worksheet_owner,
            target,
            "xl/drawings/",
            ".vml",
            content_type,
            "application/vnd.openxmlformats-officedocument.vmlDrawing",
            kind,
        )
        _validate_xml_root(content, "xml", kind)
        return
    if kind == "table":
        _require_part_profile(
            worksheet_owner,
            target,
            "xl/tables/",
            ".xml",
            content_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml",
            kind,
        )
        _validate_xml_root(content, f"{{{MAIN}}}table", kind)
        return
    if kind == "printerSettings":
        _require_part_profile(
            worksheet_owner,
            target,
            "xl/printerSettings/",
            ".bin",
            content_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings",
            kind,
        )
        _validate_printer_settings(content)
        return
    raise ValueError(f"Perfil de relación no implementado: {kind}")


def _require_part_profile(
    owner_allowed: bool,
    target: str,
    prefix: str,
    extension: str,
    actual_content_type: str,
    expected_content_type: str,
    kind: str,
) -> None:
    if (
        not owner_allowed
        or not target.startswith(prefix)
        or posixpath.splitext(target)[1].casefold() != extension.casefold()
    ):
        raise ValueError(f"Ruta de {kind} no permitida")
    if actual_content_type.casefold() != expected_content_type.casefold():
        raise ValueError(f"Content type de {kind} inválido")


def _validate_xml_root(content: bytes, expected_tag: str, kind: str) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"XML de {kind} inválido") from error
    if root.tag != expected_tag:
        raise ValueError(f"Raíz XML de {kind} inválida")


def _validate_printer_settings(content: bytes) -> None:
    if not isinstance(content, bytes):
        raise TypeError("printerSettings debe ser binario")
    candidates = ((64, 68, 70, "utf-16le"), (32, 36, 38, "ascii"))
    for name_end, size_offset, extra_offset, encoding in candidates:
        if len(content) < extra_offset + 2:
            continue
        size = int.from_bytes(content[size_offset : size_offset + 2], "little")
        extra = int.from_bytes(content[extra_offset : extra_offset + 2], "little")
        raw_name = content[:name_end]
        try:
            name = raw_name.decode(encoding, errors="strict").split("\x00", 1)[0]
        except UnicodeDecodeError:
            continue
        if (
            name
            and all(character.isprintable() for character in name)
            and size >= extra_offset + 2
            and size + extra <= len(content)
        ):
            return
    raise ValueError("Firma DEVMODE de printerSettings inválida")


def _reject_unsupported_active_content(package: XlsxPackage) -> None:
    lowered_names = tuple(name.casefold() for name in package.parts)
    for marker in _ACTIVE_PART_MARKERS:
        if any(name.startswith(marker) if marker.endswith("/") else marker in name for name in lowered_names):
            raise ValueError(f"Contenido activo no permitido: {marker}")
    for name, content in package.parts.items():
        lowered = name.casefold()
        segments = set(lowered.split("/"))
        extension = posixpath.splitext(lowered)[1]
        if segments & _ACTIVE_PATH_SEGMENTS or extension in _EXECUTABLE_EXTENSIONS:
            raise ValueError(f"Contenido activo no permitido: {name}")
        if content.startswith(b"MZ"):
            raise ValueError(f"Contenido ejecutable no permitido: {name}")
    content_types = package.parts.get("[Content_Types].xml", b"").lower()
    if any(
        marker in content_types
        for marker in (b"macroenabled", b"vbaproject", b"oleobject", b"activex")
    ):
        raise ValueError("Contenido activo no permitido por content type")
    try:
        decoded_content_types = content_types.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Content types OOXML inválidos") from error
    if any(marker in decoded_content_types for marker in _EXECUTABLE_CONTENT_TYPE_MARKERS):
        raise ValueError("Content type ejecutable no permitido")
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


def _immutable_content_types(
    source: Mapping[str, str],
    expected_parts: Mapping[str, bytes],
    label: str,
    *,
    allow_legacy_quotation_data: bool = False,
) -> dict[str, str]:
    if not isinstance(source, Mapping):
        raise TypeError("Content types de SheetAddition inválidos")
    values = dict(source)
    if allow_legacy_quotation_data and not values and expected_parts:
        if all(
            name.startswith("xl/worksheets/") and name.endswith(".xml")
            for name in expected_parts
        ):
            values = {
                name: "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
                for name in expected_parts
            }
    copied: dict[str, str] = {}
    for name, content_type in values.items():
        if not isinstance(name, str) or not isinstance(content_type, str) or not content_type:
            raise TypeError("Content types de SheetAddition inválidos")
        validate_part_name(name)
        copied[name] = content_type
    if set(copied) != set(expected_parts):
        raise ValueError(f"Cobertura de content type incompleta para {label}")
    return copied


def _parse_xml_document(
    content: bytes,
    message: str,
) -> tuple[ET.Element, dict[str, str]]:
    if not isinstance(content, bytes):
        raise TypeError(message)
    namespace_prefixes: dict[str, str] = {}
    try:
        for event, payload in ET.iterparse(
            BytesIO(content), events=("start-ns", "start")
        ):
            if event == "start-ns":
                prefix, uri = payload
                normalized = prefix or ""
                previous = namespace_prefixes.get(normalized)
                if previous is not None and previous != uri:
                    raise ValueError(f"{message}: prefijo XML ambiguo")
                namespace_prefixes[normalized] = uri
                continue
            break
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(message) from error
    _validate_mc_ignorable(root, namespace_prefixes, message)
    return root, namespace_prefixes


def _validate_mc_ignorable(
    root: ET.Element,
    namespace_prefixes: Mapping[str, str],
    message: str,
) -> None:
    value = root.get(f"{{{MC}}}Ignorable")
    if value is None:
        return
    tokens = value.split()
    if not tokens or any(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", token) is None
        for token in tokens
    ):
        raise ValueError(f"{message}: mc:Ignorable inválido")
    missing = [token for token in tokens if token not in namespace_prefixes]
    if missing:
        raise ValueError(
            f"{message}: prefijo mc:Ignorable no declarado: {missing[0]}"
        )


def _xml_bytes(
    root: ET.Element,
    namespace_prefixes: Mapping[str, str] | None = None,
) -> bytes:
    prefixes = dict(namespace_prefixes or {})
    ignorable = root.get(f"{{{MC}}}Ignorable", "").split()
    for prefix in ignorable:
        if prefix not in prefixes:
            raise ValueError(f"Prefijo mc:Ignorable no declarado: {prefix}")
    for prefix, uri in prefixes.items():
        if prefix in {"xml", "xmlns"}:
            continue
        try:
            ET.register_namespace(prefix, uri)
        except ValueError as error:
            if prefix in ignorable:
                raise ValueError(
                    f"Prefijo mc:Ignorable no serializable: {prefix}"
                ) from error
    content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    declaration_end = content.find(b"?>")
    root_start = content.find(b"<", declaration_end + 2)
    root_end = content.find(b">", root_start)
    if root_start < 0 or root_end < 0:
        raise ValueError("Serialización XML inválida")
    start_tag = content[root_start:root_end]
    declarations = bytearray()
    for prefix in ignorable:
        marker = f"xmlns:{prefix}=".encode()
        if marker in start_tag:
            continue
        uri = prefixes[prefix]
        escaped_uri = escape(uri, {'"': "&quot;"})
        declarations.extend(f' xmlns:{prefix}="{escaped_uri}"'.encode())
    if declarations:
        content = content[:root_end] + bytes(declarations) + content[root_end:]
    serialized_root, serialized_prefixes = _parse_xml_document(
        content, "XML serializado inválido"
    )
    _validate_mc_ignorable(serialized_root, serialized_prefixes, "XML serializado inválido")
    return content


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
