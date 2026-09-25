"""Construye y valida localmente el SPEC Guide 2026 de IDÉLIKA.

Python extrae evidencia, serializa el puente JSON en memoria, invoca el runtime
Node fijado y valida el paquete OOXML resultante. La autoría comercial y la
exportación ocurren en el builder MJS; Python solo poscorrige los metadatos
``sheetViews/pane`` aprobados para las cuatro hojas de datos.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Iterable
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NODE = Path(
    r"C:\Users\pepem\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
JS_BUILDER = Path(__file__).with_suffix(".mjs")

SHEETS = ["Consolidado", "Fabricacion", "Stock", "School Series", "Fuentes_Reglas"]
DATA_SHEETS = SHEETS[:4]
COLUMNS = [
    "Proveedor",
    "Subcatalogo",
    "Archivo_origen",
    "Pagina_origen",
    "Clave_estable",
    "SKU",
    "Estado_codigo",
    "Producto",
    "Familia",
    "Variante",
    "Material",
    "Medidas",
    "Descripcion",
    "Unidad",
    "Costo_MXN",
    "Precio_referencia_MXN",
    "Precio_original",
    "Estado_precio",
    "Cotizable",
    "Minimo_compra",
    "Imagen_referencia",
    "URL_fuente",
    "Identidad_hash",
    "Notas",
]
EXPECTED_COUNTS = {"Fabricacion": 138, "Stock": 62, "School Series": 20}
SHEET_COUNTS = {"Consolidado": 220, **EXPECTED_COUNTS}
SOURCE_CONTRACT = (
    (
        "Fabricacion",
        "01DHXXN7YJMCJUVPBWNJEJPJIH7B4OTAUR",
        "1 CATALOGO FABRICACION 2026B.pdf",
    ),
    (
        "Stock",
        "01DHXXN7YASXKBZPOLSBHIX2N2T3PB4G2R",
        "2 CATALOGO STOCK 2026.pdf",
    ),
    (
        "School Series",
        "01DHXXN7YTQLPUZXRUN5E3J62UE2JQUWNC",
        "4 SCHOOL SERIES 2026.pdf",
    ),
)

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": DOC_REL_NS, "pr": PKG_REL_NS}
FORMULA_ERROR = re.compile(r"#REF!|#DIV/0!|#VALUE!|#NAME\?|#N/A", re.IGNORECASE)
FROZEN_PANE_ATTRIBUTES = {
    "ySplit": "1",
    "topLeftCell": "A2",
    "activePane": "bottomLeft",
    "state": "frozen",
}


def _source_url(item_id: str) -> str:
    return f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _row_payload(row: object) -> dict[str, object]:
    source_url = str(getattr(row, "source_url"))
    source_page = int(getattr(row, "source_page"))
    price_status = str(getattr(row, "price_status"))
    return {
        "Proveedor": "IDÉLIKA",
        "Subcatalogo": getattr(row, "subcatalog"),
        "Archivo_origen": getattr(row, "source_file"),
        "Pagina_origen": source_page,
        "Clave_estable": getattr(row, "stable_key"),
        "SKU": getattr(row, "sku"),
        "Estado_codigo": "oficial" if getattr(row, "sku") else "por_verificar",
        "Producto": getattr(row, "product"),
        "Familia": getattr(row, "family"),
        "Variante": getattr(row, "variant"),
        "Material": getattr(row, "material"),
        "Medidas": getattr(row, "dimensions"),
        "Descripcion": getattr(row, "description"),
        "Unidad": getattr(row, "unit"),
        "Costo_MXN": _decimal_text(getattr(row, "cost_mxn")),
        "Precio_referencia_MXN": _decimal_text(getattr(row, "reference_price_mxn")),
        "Precio_original": getattr(row, "original_price_text"),
        "Estado_precio": "por_confirmar" if price_status == "precio_por_confirmar" else price_status,
        "Cotizable": "Sí" if getattr(row, "quotable") else "No",
        "Minimo_compra": _decimal_text(getattr(row, "minimum_order")),
        "Imagen_referencia": f"{source_url}#page={source_page}",
        "URL_fuente": source_url,
        "Identidad_hash": getattr(row, "identity_hash"),
        "Notas": getattr(row, "notes"),
    }


def _idelika_extractor():
    """Carga solo el parser aprobado, sin importar adaptadores no relacionados."""

    package_name = "_idelika_spec_importers"
    package = ModuleType(package_name)
    package.__path__ = [str(ROOT / "mobiliti_saas" / "worker" / "catalog_sync" / "importers")]
    common = ModuleType(f"{package_name}.common")

    @dataclass(frozen=True)
    class PdfPage:
        page_number: int
        text: str
        image_count: int = 0

    common.PdfPage = PdfPage
    # El importador también expone la fase de snapshot; el constructor sólo
    # necesita el extractor, pero debe satisfacer este import explícitamente.
    common.CatalogSnapshotBuild = object
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.common"] = common
    module_name = f"{package_name}.idelika"
    module_path = ROOT / "mobiliti_saas" / "worker" / "catalog_sync" / "importers" / "idelika.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("IDELIKA_SPEC_PARSER_LOAD")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.extract_idelika_rows


def _extract_payload(fabricacion: Path, stock: Path, school: Path) -> dict[str, object]:
    extract_idelika_rows = _idelika_extractor()

    paths = (fabricacion, stock, school)
    sources = []
    documents = []
    for path, (subcatalog, item_id, logical_name) in zip(
        paths, SOURCE_CONTRACT, strict=True,
    ):
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            raise ValueError(f"IDELIKA_SPEC_SOURCE:{path}")
        source_url = _source_url(item_id)
        documents.append(
            {
                "local_path": path,
                "source_file": logical_name,
                "subcatalog": subcatalog,
                "source_url": source_url,
            }
        )
        sources.append(
            {
                "subcatalog": subcatalog,
                "source_file": logical_name,
                "source_url": source_url,
                "sha256": _sha256(path),
            }
        )

    evidence = extract_idelika_rows(documents)
    rows = [_row_payload(row) for row in evidence]
    counts = {
        name: sum(row["Subcatalogo"] == name for row in rows)
        for name in EXPECTED_COUNTS
    }
    if len(rows) != 220 or counts != EXPECTED_COUNTS:
        raise ValueError(f"IDELIKA_SPEC_COUNTS:{len(rows)}:{counts}")
    if len({row["Clave_estable"] for row in rows}) != len(rows):
        raise ValueError("IDELIKA_SPEC_DUPLICATE_KEY")
    if len({row["Identidad_hash"] for row in rows}) != len(rows):
        raise ValueError("IDELIKA_SPEC_DUPLICATE_IDENTITY")
    if any(not row["URL_fuente"] or int(row["Pagina_origen"]) < 1 for row in rows):
        raise ValueError("IDELIKA_SPEC_PROVENANCE")

    return {
        "schema_version": 1,
        "extraction_date": "2026-08-02",
        "columns": COLUMNS,
        "counts": counts,
        "sources": sources,
        "rows": rows,
    }


def _part_path(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath(base).parent.joinpath(target))


def _column_index(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference)
    if match is None:
        raise ValueError("IDELIKA_SPEC_CELL_REFERENCE")
    value = 0
    for character in match.group(0):
        value = value * 26 + ord(character) - 64
    return value - 1


def _sheet_parts_from_archive(archive: zipfile.ZipFile) -> dict[str, str]:
    names = archive.namelist()
    required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
    if not required.issubset(names):
        raise ValueError("IDELIKA_SPEC_PANE_PARTS")

    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib["Id"]: _part_path("xl/workbook.xml", relation.attrib["Target"])
        for relation in relationships.findall("pr:Relationship", NS)
    }
    sheet_parts = {}
    for sheet in workbook.findall("m:sheets/m:sheet", NS):
        name = sheet.attrib["name"]
        relationship_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
        if name in sheet_parts or relationship_id not in targets:
            raise ValueError(f"IDELIKA_SPEC_PANE_MAPPING:{name}")
        sheet_parts[name] = targets[relationship_id]
    return sheet_parts


def _insert_frozen_header_pane(payload: bytes, sheet_name: str) -> bytes:
    before_root = ET.fromstring(payload)
    sheet_views = before_root.findall("m:sheetViews", NS)
    sheet_view_nodes = before_root.findall("m:sheetViews/m:sheetView", NS)
    if len(sheet_views) != 1 or len(sheet_view_nodes) != 1:
        raise ValueError(f"IDELIKA_SPEC_PANE_VIEW:{sheet_name}")
    sheet_view = sheet_view_nodes[0]
    panes = sheet_view.findall("m:pane", NS)
    if panes:
        if len(panes) == 1 and panes[0].attrib == FROZEN_PANE_ATTRIBUTES:
            return payload
        raise ValueError(f"IDELIKA_SPEC_PANE_EXISTING:{sheet_name}")

    view_pattern = re.compile(
        rb"<(?P<prefix>(?:[A-Za-z_][A-Za-z0-9_.-]*:)?)sheetView"
        rb"(?P<attributes>(?:\s[^<>]*?)?)\s*/>"
    )
    matches = list(view_pattern.finditer(payload))
    if len(matches) != 1:
        raise ValueError(f"IDELIKA_SPEC_PANE_SERIALIZATION:{sheet_name}")
    match = matches[0]
    prefix = match.group("prefix")
    original_view = match.group(0)
    opening_view = original_view[:-2].rstrip() + b">"
    pane = (
        b"<"
        + prefix
        + b'pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen" />'
    )
    replacement = opening_view + pane + b"</" + prefix + b"sheetView>"
    patched = payload[: match.start()] + replacement + payload[match.end() :]

    after_root = ET.fromstring(patched)
    after_view = after_root.find("m:sheetViews/m:sheetView", NS)
    if after_view is None:
        raise ValueError(f"IDELIKA_SPEC_PANE_RESULT:{sheet_name}")
    after_panes = after_view.findall("m:pane", NS)
    if len(after_panes) != 1 or after_panes[0].attrib != FROZEN_PANE_ATTRIBUTES:
        raise ValueError(f"IDELIKA_SPEC_PANE_RESULT:{sheet_name}")
    after_view.remove(after_panes[0])
    if _canonical_xml(after_root) != _canonical_xml(before_root):
        raise ValueError(f"IDELIKA_SPEC_PANE_SCOPE:{sheet_name}")
    return patched


def _restore_frozen_header_panes(path: Path) -> dict[str, object]:
    path = Path(path)
    if not path.is_file() or path.suffix.casefold() != ".xlsx":
        raise ValueError("IDELIKA_SPEC_PANE_OUTPUT")

    temporary = path.with_name(f".{path.name}.pane-fix-{uuid.uuid4().hex}.xlsx")
    with zipfile.ZipFile(path) as source:
        if bad_part := source.testzip():
            raise ValueError(f"IDELIKA_SPEC_PANE_ZIP:{bad_part}")
        infos = source.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("IDELIKA_SPEC_PANE_DUPLICATE_PART")
        original = {name: source.read(name) for name in names}
        archive_comment = source.comment
        sheet_parts = _sheet_parts_from_archive(source)

    if any(name not in sheet_parts for name in DATA_SHEETS):
        missing = [name for name in DATA_SHEETS if name not in sheet_parts]
        raise ValueError(f"IDELIKA_SPEC_PANE_SHEETS:{missing}")
    target_parts = [sheet_parts[name] for name in DATA_SHEETS]
    if len(set(target_parts)) != len(DATA_SHEETS) or any(part not in original for part in target_parts):
        raise ValueError("IDELIKA_SPEC_PANE_TARGETS")

    patched = dict(original)
    for sheet_name in DATA_SHEETS:
        part = sheet_parts[sheet_name]
        patched[part] = _insert_frozen_header_pane(original[part], sheet_name)

    with zipfile.ZipFile(temporary, "x") as destination:
        destination.comment = archive_comment
        for info in infos:
            destination.writestr(copy.copy(info), patched[info.filename])

    with zipfile.ZipFile(temporary) as candidate:
        if bad_part := candidate.testzip():
            raise ValueError(f"IDELIKA_SPEC_PANE_ZIP:{bad_part}")
        candidate_names = candidate.namelist()
        if candidate_names != names or candidate.comment != archive_comment:
            raise ValueError("IDELIKA_SPEC_PANE_MEMBERS")
        candidate_payloads = {name: candidate.read(name) for name in candidate_names}
        for name in candidate_names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(candidate_payloads[name])

    changed_parts = [name for name in names if original[name] != candidate_payloads[name]]
    if set(changed_parts) != set(target_parts):
        raise ValueError(f"IDELIKA_SPEC_PANE_DIFF:{changed_parts}")
    for name in names:
        if name not in target_parts and candidate_payloads[name] != original[name]:
            raise ValueError(f"IDELIKA_SPEC_PANE_SCOPE:{name}")

    os.replace(temporary, path)
    with zipfile.ZipFile(path) as reopened:
        if reopened.testzip() is not None or reopened.namelist() != names:
            raise ValueError("IDELIKA_SPEC_PANE_REOPEN")

    return {
        "changed_parts": changed_parts,
        "member_count": len(names),
        "pane": dict(FROZEN_PANE_ATTRIBUTES),
        "sheet_parts": {name: sheet_parts[name] for name in DATA_SHEETS},
        "scope_valid": True,
        "xml_parseable": True,
        "zip_valid": True,
    }


class _Package:
    def __init__(self, path: Path):
        self.path = path
        self.archive = zipfile.ZipFile(path)
        bad_part = self.archive.testzip()
        if bad_part is not None:
            raise ValueError(f"IDELIKA_SPEC_ZIP:{bad_part}")
        self.names = set(self.archive.namelist())
        if "xl/vbaProject.bin" in self.names or any(name.startswith("xl/externalLinks/") for name in self.names):
            raise ValueError("IDELIKA_SPEC_ACTIVE_CONTENT")
        self.shared_strings = self._shared_strings()
        self.sheet_parts = self._sheet_parts()

    def close(self) -> None:
        self.archive.close()

    def xml(self, part: str) -> ET.Element:
        return ET.fromstring(self.archive.read(part))

    def _shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self.names:
            return []
        root = self.xml("xl/sharedStrings.xml")
        return ["".join(node.itertext()) for node in root.findall("m:si", NS)]

    def _sheet_parts(self) -> dict[str, str]:
        required = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"}
        if not required.issubset(self.names):
            raise ValueError("IDELIKA_SPEC_PARTS")
        workbook = self.xml("xl/workbook.xml")
        relationships = self.xml("xl/_rels/workbook.xml.rels")
        targets = {
            relation.attrib["Id"]: _part_path("xl/workbook.xml", relation.attrib["Target"])
            for relation in relationships.findall("pr:Relationship", NS)
        }
        return {
            sheet.attrib["name"]: targets[sheet.attrib[f"{{{DOC_REL_NS}}}id"]]
            for sheet in workbook.findall("m:sheets/m:sheet", NS)
        }

    def cell_value(self, cell: ET.Element) -> object:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            node = cell.find("m:is", NS)
            return "".join(node.itertext()) if node is not None else ""
        value = cell.findtext("m:v", default="", namespaces=NS)
        if cell_type == "s":
            return self.shared_strings[int(value)]
        if cell_type == "b":
            return value == "1"
        if cell_type in {"str", "e"}:
            return value
        if not value:
            return None
        number = float(value)
        return int(number) if number.is_integer() else number

    def rows(self, sheet_name: str) -> list[list[object]]:
        root = self.xml(self.sheet_parts[sheet_name])
        result = []
        for row in root.findall("m:sheetData/m:row", NS):
            values: list[object] = [None] * len(COLUMNS)
            for cell in row.findall("m:c", NS):
                index = _column_index(cell.attrib["r"])
                if index < len(values):
                    values[index] = self.cell_value(cell)
            result.append(values)
        return result

    def table(self, sheet_name: str) -> ET.Element:
        sheet_part = self.sheet_parts[sheet_name]
        rels_part = str(
            PurePosixPath(sheet_part).parent
            / "_rels"
            / f"{PurePosixPath(sheet_part).name}.rels"
        )
        if rels_part not in self.names:
            raise ValueError(f"IDELIKA_SPEC_TABLE_RELS:{sheet_name}")
        relationships = self.xml(rels_part)
        tables = [
            _part_path(sheet_part, relation.attrib["Target"])
            for relation in relationships.findall("pr:Relationship", NS)
            if relation.attrib.get("Type", "").endswith("/table")
        ]
        if len(tables) != 1:
            raise ValueError(f"IDELIKA_SPEC_TABLE:{sheet_name}")
        return self.xml(tables[0])


def _canonical_xml(node: ET.Element) -> object:
    return [
        node.tag,
        sorted(node.attrib.items()),
        (node.text or "").strip(),
        [_canonical_xml(child) for child in node],
    ]


def _normalized_digest(package: _Package) -> str:
    normalized: dict[str, Any] = {
        "sheets": list(package.sheet_parts),
        "styles": _canonical_xml(package.xml("xl/styles.xml")),
        "worksheets": {},
    }
    for sheet_name, part in package.sheet_parts.items():
        root = package.xml(part)
        cells = []
        for cell in root.findall("m:sheetData/m:row/m:c", NS):
            cells.append(
                {
                    "r": cell.attrib.get("r"),
                    "s": cell.attrib.get("s"),
                    "t": cell.attrib.get("t"),
                    "f": cell.findtext("m:f", default="", namespaces=NS),
                    "v": cell.findtext("m:v", default="", namespaces=NS),
                    "is": "".join(cell.find("m:is", NS).itertext())
                    if cell.find("m:is", NS) is not None
                    else "",
                }
            )
        rows = [sorted(row.attrib.items()) for row in root.findall("m:sheetData/m:row", NS)]
        columns = [sorted(column.attrib.items()) for column in root.findall("m:cols/m:col", NS)]
        panes = [sorted(pane.attrib.items()) for pane in root.findall("m:sheetViews/m:sheetView/m:pane", NS)]
        tables = []
        if sheet_name in DATA_SHEETS:
            tables.append(_canonical_xml(package.table(sheet_name)))
        normalized["worksheets"][sheet_name] = {
            "cells": cells,
            "rows": rows,
            "columns": columns,
            "panes": panes,
            "tables": tables,
        }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_package(path: Path) -> dict[str, object]:
    if not path.is_file() or path.suffix.casefold() != ".xlsx":
        raise ValueError("IDELIKA_SPEC_OUTPUT")
    package = _Package(path)
    try:
        freeze_failures = []
        if list(package.sheet_parts) != SHEETS:
            raise ValueError(f"IDELIKA_SPEC_SHEETS:{list(package.sheet_parts)}")

        for sheet_name in DATA_SHEETS:
            rows = package.rows(sheet_name)
            if not rows or rows[0] != COLUMNS:
                raise ValueError(f"IDELIKA_SPEC_COLUMNS:{sheet_name}")
            if len(rows) - 1 != SHEET_COUNTS[sheet_name]:
                raise ValueError(f"IDELIKA_SPEC_ROWS:{sheet_name}:{len(rows) - 1}")

            sheet = package.xml(package.sheet_parts[sheet_name])
            pane = sheet.find("m:sheetViews/m:sheetView/m:pane", NS)
            if pane is None or pane.attrib != FROZEN_PANE_ATTRIBUTES:
                freeze_failures.append(sheet_name)
            table = package.table(sheet_name)
            expected_ref = f"A1:X{SHEET_COUNTS[sheet_name] + 1}"
            if table.attrib.get("ref") != expected_ref or table.find("m:autoFilter", NS) is None:
                raise ValueError(f"IDELIKA_SPEC_FILTER:{sheet_name}")

        consolidated = package.rows("Consolidado")[1:]
        if [row[1] for row in consolidated] != (
            ["Fabricacion"] * 138 + ["Stock"] * 62 + ["School Series"] * 20
        ):
            raise ValueError("IDELIKA_SPEC_ORDER")
        if any(not row[21] or not isinstance(row[3], int) or row[3] < 1 for row in consolidated):
            raise ValueError("IDELIKA_SPEC_PROVENANCE")

        styles = package.xml("xl/styles.xml")
        formats = [node.attrib.get("formatCode", "") for node in styles.findall("m:numFmts/m:numFmt", NS)]
        if not any("MXN" in code.upper() for code in formats):
            raise ValueError("IDELIKA_SPEC_MXN")

        formula_errors = []
        formula_count = 0
        for sheet_name, part in package.sheet_parts.items():
            root = package.xml(part)
            for cell in root.findall("m:sheetData/m:row/m:c", NS):
                formula = cell.findtext("m:f", default="", namespaces=NS)
                value = str(package.cell_value(cell) or "")
                if formula:
                    formula_count += 1
                for candidate in (formula, value):
                    if match := FORMULA_ERROR.search(candidate):
                        formula_errors.append(
                            {"sheet": sheet_name, "cell": cell.attrib.get("r"), "error": match.group(0)}
                        )
        if formula_errors:
            raise ValueError(f"IDELIKA_SPEC_FORMULA_ERRORS:{formula_errors}")
        if formula_count < 10:
            raise ValueError(f"IDELIKA_SPEC_FORMULA_COUNT:{formula_count}")

        return {
            "normalized_sha256": _normalized_digest(package),
            "formula_count": formula_count,
            "formula_errors": formula_errors,
            "package_parts": len(package.names),
            "package_valid": True,
            "freeze_failures": freeze_failures,
        }
    finally:
        package.close()


def _run_builder(payload: dict[str, object], output: Path, summary: Path) -> dict[str, object]:
    if not NODE.is_file() or not JS_BUILDER.is_file():
        raise RuntimeError(f"IDELIKA_SPEC_RUNTIME:{NODE}:{JS_BUILDER}")
    command = [str(NODE), str(JS_BUILDER), "--output", str(output), "--summary", str(summary)]
    result = subprocess.run(
        command,
        cwd=ROOT,
        input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=240,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "IDELIKA_SPEC_NODE\n"
            f"exit_code={result.returncode}\n"
            f"stdout={result.stdout.strip()}\n"
            f"stderr={result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"IDELIKA_SPEC_NODE_OUTPUT:{result.stdout!r}") from error


def build_idelika_spec_guide(rows: Iterable[Any], output_path: Path) -> Path:
    """Construye el SPEC IDÉLIKA desde evidencia normalizada ya validada."""

    evidence = tuple(rows)
    payload_rows = [_row_payload(row) for row in evidence]
    counts = {
        subcatalog: sum(row["Subcatalogo"] == subcatalog for row in payload_rows)
        for subcatalog in EXPECTED_COUNTS
    }
    sources: list[dict[str, str]] = []
    for subcatalog in EXPECTED_COUNTS:
        matching = next(
            (row for row in evidence if row.subcatalog == subcatalog),
            None,
        )
        if matching is None:
            continue
        fingerprint = hashlib.sha256(
            f"{matching.source_file}|{matching.source_url}".encode("utf-8")
        ).hexdigest()
        sources.append(
            {
                "subcatalog": subcatalog,
                "source_file": matching.source_file,
                "source_url": matching.source_url,
                "sha256": fingerprint,
            }
        )

    payload: dict[str, object] = {
        "schema_version": 1,
        "extraction_date": "2026-08-02",
        "columns": COLUMNS,
        "counts": counts,
        "sources": sources,
        "rows": payload_rows,
    }
    output = Path(output_path).resolve()
    summary = output.with_suffix(".validation.json")

    _run_builder(payload, output, summary)
    _restore_frozen_header_panes(output)
    first = _validate_package(output)
    _run_builder(payload, output, summary)
    _restore_frozen_header_panes(output)
    second = _validate_package(output)
    if first["normalized_sha256"] != second["normalized_sha256"]:
        raise ValueError(
            "IDELIKA_SPEC_NONDETERMINISTIC: el SPEC cambia con la misma evidencia"
        )
    return output


def build_spec_guide(
    *,
    fabricacion: Path,
    stock: Path,
    school: Path,
    output: Path,
) -> Path:
    payload = _extract_payload(fabricacion, stock, school)
    output = output.resolve()
    summary = output.with_suffix(".validation.json")

    _run_builder(payload, output, summary)
    first_postprocess = _restore_frozen_header_panes(output)
    first = _validate_package(output)
    _run_builder(payload, output, summary)
    second_postprocess = _restore_frozen_header_panes(output)
    second = _validate_package(output)
    if first["normalized_sha256"] != second["normalized_sha256"]:
        raise ValueError(
            "IDELIKA_SPEC_NONDETERMINISTIC:"
            f"{first['normalized_sha256']}:{second['normalized_sha256']}"
        )

    node_summary = json.loads(summary.read_text(encoding="utf-8"))
    node_summary["formula_errors"] = second["formula_errors"]
    node_summary["inspection"]["formula_count"] = second["formula_count"]
    node_summary["package_validation"] = {
        "valid": second["package_valid"],
        "parts": second["package_parts"],
        "validator": "Python stdlib ZIP/OOXML pane-only postprocess and read-only validation",
    }
    node_summary["ooxml_postprocess"] = {
        "runs": 2,
        "first": first_postprocess,
        "second": second_postprocess,
    }
    node_summary["inspection"]["freeze_panes_serialized"] = not second["freeze_failures"]
    node_summary["contract_failures"] = [
        f"freeze_panes_not_serialized:{sheet_name}"
        for sheet_name in second["freeze_failures"]
    ]
    node_summary["determinism"] = {
        "passed": True,
        "runs": 2,
        "first_normalized_sha256": first["normalized_sha256"],
        "second_normalized_sha256": second["normalized_sha256"],
    }
    summary.write_text(
        json.dumps(node_summary, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    if second["freeze_failures"]:
        raise ValueError(f"IDELIKA_SPEC_FREEZE:{','.join(second['freeze_failures'])}")
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fabricacion", type=Path, required=True)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--school", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = build_spec_guide(
        fabricacion=args.fabricacion.resolve(),
        stock=args.stock.resolve(),
        school=args.school.resolve(),
        output=args.output,
    )
    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
