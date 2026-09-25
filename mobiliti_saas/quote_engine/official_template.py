"""Contrato inmutable de la plantilla oficial de cotizaciones."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import posixpath
import shutil
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile


OFFICE_DOCUMENT_RELATIONSHIPS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
SPREADSHEETML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NAMESPACES = {
    "main": SPREADSHEETML,
    "rel": OFFICE_DOCUMENT_RELATIONSHIPS,
    "pkg": PACKAGE_RELATIONSHIPS,
}


@dataclass(frozen=True)
class TemplateContract:
    sha256: str
    sheet_states: dict[str, str]
    defined_name_count: int
    external_link_parts: int
    spec_formula_count: int
    mutable_sheets: tuple[str, ...]
    mutable_cells: dict[str, tuple[str, ...]]
    addable_sheets: tuple[str, ...]
    mutable_drawing_regions: dict[str, tuple[str, ...]]
    protected_prefixes: tuple[str, ...]
    translated_parts: tuple[str, ...]


@dataclass(frozen=True)
class TemplateInspection:
    sha256: str
    sheet_states: dict[str, str]
    defined_name_count: int
    external_link_parts: int
    spec_formula_count: int


def _as_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value)


def _as_tuple_mapping(value: Any) -> dict[str, tuple[str, ...]]:
    return {str(key): _as_tuple(items) for key, items in value.items()}


def load_template_contract(path: Path) -> TemplateContract:
    """Carga el contrato serializado de la plantilla oficial."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return TemplateContract(
        sha256=str(data["sha256"]),
        sheet_states={str(key): str(value) for key, value in data["sheet_states"].items()},
        defined_name_count=int(data["defined_name_count"]),
        external_link_parts=int(data["external_link_parts"]),
        spec_formula_count=int(data["spec_formula_count"]),
        mutable_sheets=_as_tuple(data["mutable_sheets"]),
        mutable_cells=_as_tuple_mapping(data["mutable_cells"]),
        addable_sheets=_as_tuple(data["addable_sheets"]),
        mutable_drawing_regions=_as_tuple_mapping(data["mutable_drawing_regions"]),
        protected_prefixes=_as_tuple(data["protected_prefixes"]),
        translated_parts=_as_tuple(data["translated_parts"]),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _package_part(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _sheet_parts(archive: ZipFile) -> list[tuple[str, str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall("pkg:Relationship", XML_NAMESPACES)
    }
    result = []
    for sheet in workbook.findall("main:sheets/main:sheet", XML_NAMESPACES):
        relationship_id = sheet.attrib[f"{{{OFFICE_DOCUMENT_RELATIONSHIPS}}}id"]
        result.append(
            (
                sheet.attrib["name"],
                sheet.attrib.get("state", "visible"),
                _package_part(targets[relationship_id]),
            )
        )
    return result


def inspect_template(path: Path) -> TemplateInspection:
    """Inspecciona las propiedades OOXML incluidas en el contrato oficial."""

    path = Path(path)
    with ZipFile(path) as archive:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        sheet_parts = _sheet_parts(archive)
        sheet_states = {name: state for name, state, _part in sheet_parts}
        spec_formula_count = sum(
            len(
                ElementTree.fromstring(archive.read(part)).findall(
                    ".//main:f", XML_NAMESPACES
                )
            )
            for name, _state, part in sheet_parts
            if name.strip().casefold().startswith("spec")
        )
        defined_name_count = len(
            workbook.findall("main:definedNames/main:definedName", XML_NAMESPACES)
        )
        external_link_parts = sum(
            name.startswith("xl/externalLinks/") for name in archive.namelist()
        )

    return TemplateInspection(
        sha256=_sha256(path),
        sheet_states=sheet_states,
        defined_name_count=defined_name_count,
        external_link_parts=external_link_parts,
        spec_formula_count=spec_formula_count,
    )


def verify_official_template(path: Path, contract: TemplateContract) -> TemplateInspection:
    """Falla cerrada si una plantilla no coincide con el contrato oficial."""

    inspection = inspect_template(path)
    mismatches = []
    if inspection.sha256 != contract.sha256:
        mismatches.append("sha256")
    if inspection.sheet_states != contract.sheet_states:
        mismatches.append("sheet_states")
    if inspection.defined_name_count != contract.defined_name_count:
        mismatches.append("defined_names")
    if inspection.external_link_parts != contract.external_link_parts:
        mismatches.append("external_links")
    if inspection.spec_formula_count != contract.spec_formula_count:
        mismatches.append("spec_formulas")
    if mismatches:
        raise ValueError(f"Plantilla oficial incompatible: {', '.join(mismatches)}")
    return inspection


def promote(source: Path, destination: Path, contract_path: Path) -> None:
    """Copia una plantilla auditada solo después de verificarla byte a byte."""

    contract = load_template_contract(contract_path)
    verify_official_template(source, contract)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"Destino ya existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    verify_official_template(destination, contract)
