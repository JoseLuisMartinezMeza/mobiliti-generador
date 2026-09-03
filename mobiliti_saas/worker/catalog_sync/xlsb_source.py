"""Lector XLSB acotado para fuentes de catálogo no confiables.

El archivo se inspecciona como paquete OPC antes de delegar la lectura binaria a
``pyxlsb``.  Este módulo no evalúa fórmulas: pyxlsb entrega sus valores
cacheados y únicamente después de que el paquete pasa el prefiltro.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterator
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .importers.common import (
    CellRef,
    ImageAsset,
    MAX_FILE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_EXPANDED_BYTES,
    MAX_ZIP_RATIO,
    SourceSafetyError,
    _normalize_image,
)


_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_DRAWING_MAIN_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_ALLOWED_RELATIONSHIP_SUFFIXES = {
    "/officeDocument",
    "/core-properties",
    "/extended-properties",
    "/worksheet",
    "/sharedStrings",
    "/styles",
    "/theme",
    "/calcChain",
    "/sheetMetadata",
    "/drawing",
    "/image",
    "/printerSettings",
    "/xlBinaryIndex",
    "/rdRichValueTypes",
    "/rdRichValueStructure",
    "/rdRichValue",
    "/richValueRel",
}
_DANGEROUS_PART = re.compile(
    r"(?:^|/)(?:vbaproject|embeddings|activex|connections|externallinks|oleobjects?)(?:/|\.|$)",
    re.IGNORECASE,
)
_ALLOWED_CONTENT_TYPES = {
    "application/xml",
    "application/vnd.openxmlformats-package.relationships+xml",
    "application/vnd.openxmlformats-package.core-properties+xml",
    "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "application/vnd.openxmlformats-officedocument.theme+xml",
    "application/vnd.openxmlformats-officedocument.drawing+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.printersettings",
    "application/vnd.ms-excel.sheet.binary.macroenabled.main",
    "application/vnd.ms-excel.worksheet",
    "application/vnd.ms-excel.binindexws",
    "application/vnd.ms-excel.styles",
    "application/vnd.ms-excel.sharedstrings",
    "application/vnd.ms-excel.sheetmetadata",
    "application/vnd.ms-excel.richvaluerel+xml",
    "application/vnd.ms-excel.rdrichvalue+xml",
    "application/vnd.ms-excel.rdrichvaluestructure+xml",
    "application/vnd.ms-excel.rdrichvaluetypes+xml",
    "application/vnd.ms-excel.calcchain",
    "image/jpeg",
    "image/png",
}


def _fail(code: str) -> None:
    raise SourceSafetyError(code) from None


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    windows = PureWindowsPath(name)
    return bool(name) and not (
        "\\" in name
        or path.is_absolute()
        or windows.drive
        or windows.root
        or any(part in {"", ".", ".."} for part in path.parts)
    )


def _relationship_source(name: str) -> str | None:
    if name == "_rels/.rels":
        return ""
    marker = "/_rels/"
    if marker not in name or not name.endswith(".rels"):
        return None
    directory, leaf = name.split(marker, 1)
    return f"{directory}/{leaf[:-5]}"


def _part_target(source: str, target: str) -> str | None:
    if not target or "\\" in target or target.startswith("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    return resolved if _safe_member(resolved) else None


def _relationships(parts: dict[str, bytes], name: str) -> dict[str, tuple[str, str]]:
    if name not in parts:
        return {}
    source = _relationship_source(name)
    if source is None:
        _fail("XLSB_INVALID")
    try:
        root = ElementTree.fromstring(parts[name])
    except Exception:
        _fail("XLSB_INVALID")
    if root.tag != f"{{{_REL_NS}}}Relationships":
        _fail("XLSB_INVALID")
    resolved: dict[str, tuple[str, str]] = {}
    for relationship in root:
        if relationship.tag != f"{{{_REL_NS}}}Relationship" or set(relationship.attrib) - {"Id", "Type", "Target", "TargetMode"}:
            _fail("XLSB_INVALID")
        relation_id = relationship.get("Id")
        relation_type = relationship.get("Type")
        target = relationship.get("Target")
        mode = relationship.get("TargetMode", "Internal")
        if (
            not relation_id
            or relation_id in resolved
            or not relation_type
            or not target
            or mode not in {"Internal", "External"}
        ):
            _fail("XLSB_UNSAFE")
        if mode == "External":
            try:
                link = urlsplit(target)
            except ValueError:
                _fail("XLSB_UNSAFE")
            if (
                not relation_type.endswith("/hyperlink")
                or link.scheme.lower() not in {"http", "https"}
                or not link.hostname
                or link.username is not None
                or link.password is not None
                or len(target) > 2048
            ):
                _fail("XLSB_UNSAFE")
            # Los hipervínculos HTTP(S) son metadatos pasivos: no se resuelven ni se exponen.
            continue
        if not any(relation_type.endswith(suffix) for suffix in _ALLOWED_RELATIONSHIP_SUFFIXES):
            _fail("XLSB_UNSAFE")
        part = _part_target(source, target)
        if part is None or part not in parts:
            _fail("XLSB_INVALID")
        resolved[relation_id] = (relation_type, part)
    return resolved


def _read_parts(data: bytes) -> dict[str, bytes]:
    if type(data) is not bytes or not 0 < len(data) <= MAX_FILE_BYTES or not data.startswith(b"PK\x03\x04"):
        _fail("XLSB_SOURCE")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            infos = package.infolist()
            if not infos or len(infos) > MAX_ZIP_ENTRIES:
                _fail("XLSB_LIMIT")
            names = [info.filename for info in infos]
            if len({name.casefold() for name in names}) != len(names):
                _fail("XLSB_UNSAFE")
            expanded = 0
            for info in infos:
                local_flags = int.from_bytes(data[info.header_offset + 6 : info.header_offset + 8], "little")
                if (
                    not _safe_member(info.filename)
                    or info.flag_bits & 1
                    or local_flags & 1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                ):
                    _fail("XLSB_UNSAFE")
                if _DANGEROUS_PART.search(info.filename):
                    _fail("XLSB_UNSAFE")
                expanded += info.file_size
                if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_ZIP_RATIO):
                    _fail("XLSB_LIMIT")
            if expanded > MAX_ZIP_EXPANDED_BYTES:
                _fail("XLSB_LIMIT")
            parts = {info.filename: package.read(info) for info in infos}
    except SourceSafetyError:
        raise
    except Exception:
        _fail("XLSB_INVALID")
    if "[Content_Types].xml" not in parts or "xl/workbook.bin" not in parts:
        _fail("XLSB_INVALID")
    return parts


def _validate_content_types(parts: dict[str, bytes]) -> None:
    raw = parts["[Content_Types].xml"]
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        _fail("XLSB_UNSAFE")
    namespace = "http://schemas.openxmlformats.org/package/2006/content-types"
    try:
        root = ElementTree.fromstring(raw)
    except Exception:
        _fail("XLSB_INVALID")
    if root.tag != f"{{{namespace}}}Types" or root.attrib:
        _fail("XLSB_INVALID")
    defaults: set[str] = set()
    overrides: set[str] = set()
    for entry in root:
        if entry.tag == f"{{{namespace}}}Default" and set(entry.attrib) == {"Extension", "ContentType"}:
            key = entry.get("Extension", "").casefold()
            if not re.fullmatch(r"[a-z0-9]{1,20}", key) or key in defaults:
                _fail("XLSB_INVALID")
            defaults.add(key)
        elif entry.tag == f"{{{namespace}}}Override" and set(entry.attrib) == {"PartName", "ContentType"}:
            name = entry.get("PartName", "")
            key = name[1:] if name.startswith("/") else ""
            if not _safe_member(key) or key.casefold() in overrides or key not in parts:
                _fail("XLSB_INVALID")
            overrides.add(key.casefold())
        else:
            _fail("XLSB_INVALID")
        if entry.get("ContentType", "").casefold() not in _ALLOWED_CONTENT_TYPES:
            _fail("XLSB_UNSAFE")
    if not defaults:
        _fail("XLSB_INVALID")
    for part in parts:
        if part.endswith("/") or part == "[Content_Types].xml":
            continue
        extension = part.rsplit(".", 1)[-1].casefold() if "." in part else ""
        if part.casefold() not in overrides and extension not in defaults:
            _fail("XLSB_INVALID")


def _validate_parts(parts: dict[str, bytes]) -> None:
    for name in parts:
        if name.endswith(".rels"):
            _relationships(parts, name)
    if "_rels/.rels" not in parts or "xl/_rels/workbook.bin.rels" not in parts:
        _fail("XLSB_INVALID")
    root_relations = _relationships(parts, "_rels/.rels")
    if not any(kind.endswith("/officeDocument") and target == "xl/workbook.bin" for kind, target in root_relations.values()):
        _fail("XLSB_INVALID")
    workbook_relations = _relationships(parts, "xl/_rels/workbook.bin.rels")
    if not any(kind.endswith("/worksheet") for kind, _ in workbook_relations.values()):
        _fail("XLSB_INVALID")
    _validate_content_types(parts)


def _rels_name(part: str) -> str:
    directory, leaf = posixpath.split(part)
    return f"{directory}/_rels/{leaf}.rels"


@dataclass(frozen=True)
class XlsbSource:
    data: bytes
    sha256: str
    parts: dict[str, bytes]

    def iter_rows(self, sheet_name: str) -> Iterator[tuple[object, ...]]:
        try:
            from pyxlsb import open_workbook
        except ImportError:
            _fail("XLSB_READER_UNAVAILABLE")
        try:
            with open_workbook(io.BytesIO(self.data)) as workbook:
                with workbook.get_sheet(sheet_name) as sheet:
                    for row in sheet.rows():
                        yield tuple(cell.v for cell in row)
        except SourceSafetyError:
            raise
        except Exception:
            _fail("XLSB_INVALID")

    def image_anchors(self) -> dict[CellRef, ImageAsset]:
        """Resuelve anclas sheet -> drawing -> media sin abrir Excel."""
        try:
            from pyxlsb import open_workbook
        except ImportError:
            _fail("XLSB_READER_UNAVAILABLE")
        try:
            with open_workbook(io.BytesIO(self.data)) as workbook:
                sheet_parts = {name: f"xl/{part}" for name, part in workbook._sheets}
        except Exception:
            _fail("XLSB_INVALID")
        result: dict[CellRef, ImageAsset] = {}
        for sheet_name, sheet_part in sheet_parts.items():
            for relation_type, drawing_part in _relationships(self.parts, _rels_name(sheet_part)).values():
                if not relation_type.endswith("/drawing"):
                    continue
                try:
                    drawing = ElementTree.fromstring(self.parts[drawing_part])
                except Exception:
                    _fail("XLSB_INVALID")
                drawing_relationships = _relationships(self.parts, _rels_name(drawing_part))
                for anchor in drawing:
                    start = anchor.find(f"{{{_DRAWING_NS}}}from")
                    blip = anchor.find(f".//{{{_DRAWING_MAIN_NS}}}blip")
                    embed = blip.get(f"{{{_DOC_REL_NS}}}embed") if blip is not None else None
                    relation = drawing_relationships.get(embed)
                    if start is None or relation is None or not relation[0].endswith("/image"):
                        continue
                    try:
                        row = int(start.find(f"{{{_DRAWING_NS}}}row").text) + 1
                        column = int(start.find(f"{{{_DRAWING_NS}}}col").text) + 1
                    except Exception:
                        _fail("XLSB_INVALID")
                    if row < 1 or column < 1:
                        _fail("XLSB_INVALID")
                    target = relation[1]
                    if not target.startswith("xl/media/") or target not in self.parts:
                        _fail("XLSB_UNSAFE")
                    asset = _normalize_image(self.parts[target])
                    if len(asset.data) > MAX_IMAGE_BYTES or asset.width * asset.height > MAX_IMAGE_PIXELS:
                        _fail("XLSB_LIMIT")
                    reference = CellRef(sheet_name, f"{_column_letter(column)}{row}")
                    if reference in result:
                        # Anclas duplicadas no identifican una imagen única; no inventamos una.
                        continue
                    result[reference] = asset
        return result


def _column_letter(column: int) -> str:
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(65 + remainder) + result
    return result


def read_validated_xlsb_source(data: bytes) -> XlsbSource:
    """Valida el paquete antes de permitir que pyxlsb lea valores cacheados."""
    parts = _read_parts(data)
    _validate_parts(parts)
    return XlsbSource(data, hashlib.sha256(data).hexdigest(), parts)
