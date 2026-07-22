"""Lectura, mutación write-once y auditoría de paquetes OOXML."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import ntpath
import os
from pathlib import Path
import posixpath
import re
import threading
from types import MappingProxyType
from typing import AbstractSet, Mapping
import unicodedata
from xml.etree import ElementTree
import zipfile


PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOCUMENT_RELATIONSHIPS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
STRICT_OFFICE_DOCUMENT_RELATIONSHIPS = (
    "http://purl.oclc.org/ooxml/officeDocument/relationships"
)
OFFICE_DOCUMENT_RELATIONSHIP_BASES = (
    OFFICE_DOCUMENT_RELATIONSHIPS,
)
SPREADSHEETML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DRAWINGML = "http://schemas.openxmlformats.org/drawingml/2006/main"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
_SAFE_ALLOCATION_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_SAFE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,10}\Z")
_PROTECTED_ALLOCATION_PREFIXES = ("xl/externalLinks/", "xl/richData/")
_STRICT_OOXML_MARKER = b"http://purl.oclc.org/ooxml/"
XML_SERIALIZATION_LOCK = threading.RLock()
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_PART_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 100
ZIP_READ_CHUNK_BYTES = 1024 * 1024
_CANONICAL_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SUPPORTED_COMPRESSION_TYPES = frozenset((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))


@dataclass(frozen=True)
class PackageMutation:
    """Partes a reemplazar o agregar en una única escritura de paquete."""

    replacements: Mapping[str, bytes] = field(default_factory=dict)
    additions: Mapping[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        replacements = _validated_bytes_mapping(self.replacements, "reemplazos")
        additions = _validated_bytes_mapping(self.additions, "adiciones")
        _validate_unique_part_identities(
            tuple((*replacements, *additions)), "Mutación OOXML"
        )
        object.__setattr__(self, "replacements", MappingProxyType(replacements))
        object.__setattr__(self, "additions", MappingProxyType(additions))

    @property
    def allowed_parts(self) -> frozenset[str]:
        """Devuelve la allowlist concreta inducida por la mutación."""

        return frozenset((*self.replacements, *self.additions))


@dataclass(frozen=True)
class PackageAudit:
    """Resultado de comparar dos paquetes OOXML."""

    changed_parts: frozenset[str]
    protected_hashes: Mapping[str, str]
    unexpected_changed_parts: frozenset[str] = frozenset()


@dataclass
class XlsxPackage:
    """Representación en memoria de un ZIP OOXML previamente auditado."""

    path: Path | None
    infos: Mapping[str, zipfile.ZipInfo]
    parts: Mapping[str, bytes]
    archive_names: tuple[str, ...] = ()

    @classmethod
    def read(cls, path: Path) -> "XlsxPackage":
        """Lee un paquete y falla cerrada si su estructura OOXML es insegura."""

        path = Path(path)
        with zipfile.ZipFile(path, "r") as archive:
            package = cls._from_archive(archive, path)
        package.audit()
        return package

    @classmethod
    def from_bytes(cls, content: bytes) -> "XlsxPackage":
        """Lee bytes XLSX sin crear ni modificar archivos temporales."""

        if not isinstance(content, bytes):
            raise TypeError("El paquete XLSX debe recibirse como bytes")
        with zipfile.ZipFile(BytesIO(content), "r") as archive:
            package = cls._from_archive(archive, None)
        package.audit()
        return package

    @classmethod
    def _from_archive(
        cls,
        archive: zipfile.ZipFile,
        path: Path | None,
    ) -> "XlsxPackage":
        entries = archive.infolist()
        _preflight_archive(entries)
        names = tuple(item.filename for item in entries)
        infos = {item.filename: item for item in entries}
        parts: dict[str, bytes] = {}
        for item in entries:
            chunks: list[bytes] = []
            size = 0
            with archive.open(item, "r") as stream:
                while True:
                    chunk = stream.read(ZIP_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ZIP_PART_BYTES or size > item.file_size:
                        raise ValueError(
                            f"Parte ZIP excede el límite declarado: {item.filename}"
                        )
                    chunks.append(chunk)
            if size != item.file_size:
                raise ValueError(f"Tamaño ZIP inconsistente: {item.filename}")
            parts[item.filename] = b"".join(chunks)
        return cls(path=path, infos=infos, parts=parts, archive_names=names)

    def hashes(self, exclude: AbstractSet[str] | None = None) -> dict[str, str]:
        """Calcula SHA-256 por parte, excepto las partes explícitamente excluidas."""

        excluded = frozenset(exclude or ())
        return {
            name: hashlib.sha256(content).hexdigest()
            for name, content in self.parts.items()
            if name not in excluded
        }

    def audit(self) -> None:
        """Valida rutas ZIP y todas las relaciones internas del paquete."""

        names = self.archive_names or tuple(self.parts)
        duplicate_names = _duplicates(names)
        if duplicate_names:
            raise ValueError(f"Partes ZIP duplicadas: {sorted(duplicate_names)}")
        _validate_unique_part_identities(names, "Parte ZIP")
        for name in names:
            _validate_part_name(name)

        strict_parts = [
            name
            for name in names
            if (name.endswith((".xml", ".rels")) or name == "[Content_Types].xml")
            and _STRICT_OOXML_MARKER in self.parts[name]
        ]
        if strict_parts:
            raise ValueError(
                "Paquetes OOXML Strict no soportados: " + strict_parts[0]
            )

        for rels_name in (name for name in names if name.endswith(".rels")):
            owner = _relationship_owner(rels_name)
            if owner is not None and owner not in self.parts:
                raise ValueError(f"Relacion OOXML sin propietario: {rels_name} -> {owner}")
            relationships = _parse_relationships(rels_name, self.parts[rels_name])
            for relationship in relationships:
                relationship_id = relationship.get("Id")
                if not relationship_id:
                    raise ValueError(f"Relacion OOXML sin Id: {rels_name}")
                target_mode = relationship.get("TargetMode", "")
                if target_mode.casefold() not in {"", "internal", "external"}:
                    raise ValueError(f"TargetMode OOXML invalido: {rels_name}")
            duplicate_ids = _duplicates(
                tuple(relationship["Id"] for relationship in relationships)
            )
            if duplicate_ids:
                raise ValueError(
                    f"IDs de relacion duplicados: {rels_name} -> {sorted(duplicate_ids)}"
                )

            for relationship in relationships:
                target = relationship.get("Target")
                if not target:
                    raise ValueError(f"Relacion OOXML sin destino: {rels_name}")
                if relationship.get("TargetMode", "").casefold() == "external":
                    continue
                resolved = _resolve_internal_target(owner, target)
                if resolved not in self.parts:
                    raise ValueError(
                        f"Relacion OOXML sin destino: {rels_name} -> {resolved}"
                    )
        self._audit_office_document_relationship()

    def sheet_part(self, name: str) -> str:
        """Resuelve una hoja por workbook relationships, nunca por sheetN."""

        matches = [row for row in self._sheet_rows() if row[0] == name]
        if not matches:
            raise KeyError(name)
        if len(matches) != 1:
            raise ValueError(f"Hoja OOXML duplicada: {name}")
        return matches[0][3]

    def sheet_state(self, name: str) -> str:
        """Devuelve el estado real declarado por workbook.xml."""

        matches = [row for row in self._sheet_rows() if row[0] == name]
        if not matches:
            raise KeyError(name)
        if len(matches) != 1:
            raise ValueError(f"Hoja OOXML duplicada: {name}")
        return matches[0][1]

    def sheet_index(self, name: str) -> int:
        """Devuelve el índice workbook local de una hoja única."""

        matches = [row for row in self._sheet_rows() if row[0] == name]
        if not matches:
            raise KeyError(name)
        if len(matches) != 1:
            raise ValueError(f"Hoja OOXML duplicada: {name}")
        return matches[0][2]

    def workbook_related_part(self, relationship_name: str) -> str | None:
        """Resuelve una relación transitional exacta del workbook."""

        rels_name = "xl/_rels/workbook.xml.rels"
        if rels_name not in self.parts:
            raise ValueError("Relaciones del workbook ausentes")
        relationships = _parse_relationships(rels_name, self.parts[rels_name])
        if relationship_name not in {"styles", "sharedStrings", "theme"}:
            raise ValueError(f"Tipo de relacion workbook invalido: {relationship_name}")
        expected_types = relationship_type_uris(relationship_name)
        matches = [
            relationship
            for relationship in relationships
            if relationship.get("Type") in expected_types
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError(f"Relación workbook duplicada: {relationship_name}")
        if matches[0].get("TargetMode", "").casefold() == "external":
            raise ValueError(f"Relacion workbook externa invalida: {relationship_name}")
        part_name = _resolve_internal_target(
            "xl/workbook.xml", matches[0]["Target"]
        )
        self._validate_office_part_profile(relationship_name, part_name)
        return part_name

    def declared_part_names(self) -> frozenset[str]:
        """Incluye los Override OPC aunque la parte declarada sea huérfana."""

        _defaults, overrides = _parse_content_types(
            self.parts.get("[Content_Types].xml")
        )
        return frozenset(overrides)

    def _audit_office_document_relationship(self) -> None:
        workbook_content = self.parts.get("xl/workbook.xml")
        if workbook_content is None:
            return
        try:
            workbook_root = ElementTree.fromstring(workbook_content)
        except ElementTree.ParseError:
            return
        # `audit()` también sirve a paquetes OPC mínimos de pruebas. El perfil
        # Excel se activa únicamente cuando la parte declara un workbook real.
        if workbook_root.tag != f"{{{SPREADSHEETML}}}workbook":
            return
        rels_name = "_rels/.rels"
        if rels_name not in self.parts:
            raise ValueError("Relaciones raíz OOXML ausentes")
        relationships = _parse_relationships(rels_name, self.parts[rels_name])
        matches = [
            relationship
            for relationship in relationships
            if relationship.get("Type") in relationship_type_uris("officeDocument")
        ]
        if len(matches) != 1:
            raise ValueError("Relacion officeDocument raíz inválida")
        relationship = matches[0]
        if relationship.get("TargetMode", "").casefold() == "external":
            raise ValueError("Relacion officeDocument raíz externa")
        workbook_part = _resolve_internal_target(None, relationship["Target"])
        if workbook_part != "xl/workbook.xml":
            raise ValueError("Destino officeDocument raíz inválido")
        self._validate_office_part_profile("officeDocument", workbook_part)

    def _validate_office_part_profile(self, kind: str, part_name: str) -> None:
        profiles = {
            "officeDocument": (
                lambda name: name == "xl/workbook.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                f"{{{SPREADSHEETML}}}workbook",
            ),
            "worksheet": (
                lambda name: name.startswith("xl/worksheets/") and name.endswith(".xml"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
                f"{{{SPREADSHEETML}}}worksheet",
            ),
            "styles": (
                lambda name: name == "xl/styles.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
                f"{{{SPREADSHEETML}}}styleSheet",
            ),
            "sharedStrings": (
                lambda name: name == "xl/sharedStrings.xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
                f"{{{SPREADSHEETML}}}sst",
            ),
            "theme": (
                lambda name: re.fullmatch(
                    r"xl/theme/theme[1-9][0-9]*\.xml", name
                )
                is not None,
                "application/vnd.openxmlformats-officedocument.theme+xml",
                f"{{{DRAWINGML}}}theme",
            ),
        }
        profile = profiles.get(kind)
        if profile is None:
            raise ValueError(f"Perfil Office desconocido: {kind}")
        path_check, expected_content_type, expected_root = profile
        if not path_check(part_name):
            raise ValueError(f"Ruta de {kind} OOXML inválida: {part_name}")
        if part_name not in self.parts:
            raise ValueError(f"Parte de {kind} OOXML ausente: {part_name}")
        actual_content_type = self.content_types_for({part_name})[part_name]
        if actual_content_type != expected_content_type:
            raise ValueError(f"Content type de {kind} OOXML inválido")
        try:
            root = ElementTree.fromstring(self.parts[part_name])
        except ElementTree.ParseError as error:
            raise ValueError(f"XML de {kind} OOXML inválido") from error
        if root.tag != expected_root:
            raise ValueError(f"Raíz XML de {kind} OOXML inválida")

    def shared_strings(self) -> tuple[bytes, ...]:
        """Devuelve cada CT_Rst completo para no aplanar rich text."""

        part = self.workbook_related_part("sharedStrings")
        if part is None:
            return ()
        if part not in self.parts:
            raise ValueError("Shared strings sin destino")
        try:
            root = ElementTree.fromstring(self.parts[part])
        except ElementTree.ParseError as error:
            raise ValueError("Shared strings OOXML inválidos") from error
        if root.tag != f"{{{SPREADSHEETML}}}sst" or any(
            child.tag != f"{{{SPREADSHEETML}}}si" for child in root
        ):
            raise ValueError("Shared strings OOXML inválidos")
        with XML_SERIALIZATION_LOCK:
            return tuple(
                ElementTree.tostring(child, encoding="utf-8") for child in root
            )

    def relationship_closure(self, start_part: str) -> Mapping[str, bytes]:
        """Calcula la clausura OPC transitiva y rechaza ciclos."""

        _validate_part_name(start_part)
        if start_part not in self.parts:
            raise ValueError(f"Parte OOXML inexistente: {start_part}")
        result: dict[str, bytes] = {}
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(part_name: str) -> None:
            if part_name in visiting:
                raise ValueError(f"Ciclo de relaciones OOXML: {part_name}")
            if part_name in visited:
                return
            if part_name not in self.parts:
                raise ValueError(f"Relacion OOXML sin destino: {part_name}")
            visiting.add(part_name)
            result[part_name] = bytes(self.parts[part_name])
            rels_name = relationship_part_name(part_name)
            if rels_name in self.parts:
                result[rels_name] = bytes(self.parts[rels_name])
                for relationship in _parse_relationships(
                    rels_name, self.parts[rels_name]
                ):
                    if relationship.get("TargetMode", "").casefold() == "external":
                        continue
                    target = _resolve_internal_target(
                        part_name, relationship["Target"]
                    )
                    visit(target)
            visiting.remove(part_name)
            visited.add(part_name)

        visit(start_part)
        return result

    def allocate_closure(
        self,
        closure: Mapping[str, bytes],
        *,
        prefix: str,
    ) -> dict[str, str]:
        """Asigna nombres deterministas sin colisiones a una clausura OPC."""

        if not isinstance(prefix, str) or _SAFE_ALLOCATION_PREFIX.fullmatch(prefix) is None:
            raise ValueError("Prefijo de asignación OOXML inválido")
        closure_names = set(closure)
        for name in closure_names:
            _validate_part_name(name)
        owner_parts = sorted(
            name for name in closure_names if not name.endswith(".rels")
        )
        for rels_name in (name for name in closure_names if name.endswith(".rels")):
            owner = _relationship_owner(rels_name)
            if owner is None or owner not in closure_names:
                raise ValueError(f"Clausura OOXML sin propietario: {rels_name}")

        allocated: dict[str, str] = {}
        occupied = set(self.parts) | set(self.declared_part_names())
        generated: set[str] = set()
        sequence = 1
        for source_name in owner_parts:
            directory, filename = posixpath.split(source_name)
            extension = posixpath.splitext(filename)[1]
            if _SAFE_EXTENSION.fullmatch(extension) is None:
                raise ValueError(f"Extensión OOXML inválida: {source_name}")
            if source_name.startswith(_PROTECTED_ALLOCATION_PREFIXES):
                raise ValueError(f"Prefijo OOXML protegido: {source_name}")
            source_rels = relationship_part_name(source_name)
            while True:
                candidate = posixpath.join(
                    directory, f"{prefix}{sequence}{extension}"
                )
                candidate_rels = relationship_part_name(candidate)
                sequence += 1
                conflicts = {candidate}
                if source_rels in closure_names:
                    conflicts.add(candidate_rels)
                if not conflicts & (occupied | generated):
                    break
            allocated[source_name] = candidate
            generated.add(candidate)
            if source_rels in closure_names:
                allocated[source_rels] = candidate_rels
                generated.add(candidate_rels)
        if set(allocated) != closure_names:
            raise ValueError("Asignación OOXML incompleta")
        return allocated

    def content_types_for(self, parts: AbstractSet[str]) -> dict[str, str]:
        """Resuelve content types efectivos para partes concretas."""

        defaults, overrides = _parse_content_types(self.parts.get("[Content_Types].xml"))
        result: dict[str, str] = {}
        for name in parts:
            _validate_part_name(name)
            content_type = overrides.get(name)
            if content_type is None:
                extension = posixpath.splitext(name)[1].lstrip(".").casefold()
                content_type = defaults.get(extension)
            if not content_type:
                raise ValueError(f"Content type OOXML ausente: {name}")
            result[name] = content_type
        return result

    def _sheet_rows(self) -> list[tuple[str, str, int, str]]:
        if "xl/workbook.xml" not in self.parts:
            raise ValueError("Workbook OOXML ausente")
        try:
            workbook = ElementTree.fromstring(self.parts["xl/workbook.xml"])
        except ElementTree.ParseError as error:
            raise ValueError("Workbook OOXML inválido") from error
        if workbook.tag != f"{{{SPREADSHEETML}}}workbook":
            raise ValueError("Workbook OOXML inválido")
        sheet_containers = workbook.findall(f"{{{SPREADSHEETML}}}sheets")
        if len(sheet_containers) != 1:
            raise ValueError("Colección de hojas OOXML inválida")
        rels_name = "xl/_rels/workbook.xml.rels"
        if rels_name not in self.parts:
            raise ValueError("Relaciones del workbook ausentes")
        relationships = {
            item["Id"]: item
            for item in _parse_relationships(rels_name, self.parts[rels_name])
        }
        result: list[tuple[str, str, int, str]] = []
        used_sheet_ids: set[int] = set()
        used_relationship_ids: set[str] = set()
        for index, sheet in enumerate(sheet_containers[0]):
            if sheet.tag != f"{{{SPREADSHEETML}}}sheet":
                raise ValueError("Colección de hojas OOXML inválida")
            name = sheet.get("name")
            relationship_id = sheet.get(
                f"{{{OFFICE_DOCUMENT_RELATIONSHIPS}}}id"
            )
            sheet_id_raw = sheet.get("sheetId", "")
            state = sheet.get("state", "visible")
            if not name or not relationship_id or state not in {
                "visible", "hidden", "veryHidden"
            }:
                raise ValueError("Hoja OOXML inválida")
            if re.fullmatch(r"[1-9][0-9]*", sheet_id_raw) is None:
                raise ValueError(f"sheetId OOXML inválido: {name}")
            sheet_id = int(sheet_id_raw)
            if sheet_id in used_sheet_ids:
                raise ValueError(f"sheetId OOXML duplicado: {sheet_id}")
            if relationship_id in used_relationship_ids:
                raise ValueError(
                    f"Relación de hoja OOXML reutilizada: {relationship_id}"
                )
            used_sheet_ids.add(sheet_id)
            used_relationship_ids.add(relationship_id)
            relationship = relationships.get(relationship_id)
            if (
                relationship is None
                or relationship.get("Type") not in relationship_type_uris("worksheet")
                or relationship.get("TargetMode", "").casefold() == "external"
            ):
                raise ValueError(f"Relación de hoja OOXML inválida: {name}")
            target = _resolve_internal_target(
                "xl/workbook.xml", relationship["Target"]
            )
            self._validate_office_part_profile("worksheet", target)
            result.append((name, state, index, target))
        duplicates = _duplicates(tuple(row[0].casefold() for row in result))
        if duplicates:
            raise ValueError(f"Hoja OOXML duplicada: {sorted(duplicates)}")
        return result

    def to_bytes(self, mutation: PackageMutation) -> bytes:
        """Serializa una mutación con metadatos ZIP deterministas."""

        _validate_package_mutation(self, mutation)
        stream = BytesIO()
        with zipfile.ZipFile(
            stream,
            "w",
            zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as target:
            for name, info in self.infos.items():
                target.writestr(info, mutation.replacements.get(name, self.parts[name]))
            for name in sorted(mutation.additions, key=canonical_part_identity):
                target.writestr(
                    _canonical_zip_info(name),
                    mutation.additions[name],
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                )
        return stream.getvalue()

    def write_new(self, output: Path, mutation: PackageMutation) -> None:
        """Escribe de forma exclusiva bytes ya validados del paquete."""

        output = Path(output)
        content = self.to_bytes(mutation)
        try:
            stream = output.open("xb")
        except FileExistsError as error:
            raise FileExistsError(f"La salida ya existe: {output}") from error
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


def _validate_package_mutation(package: XlsxPackage, mutation: PackageMutation) -> None:
    if not isinstance(mutation, PackageMutation):
        raise TypeError("Mutación OOXML inválida")
    overlap = set(mutation.replacements) & set(mutation.additions)
    if overlap:
        raise ValueError(f"Partes duplicadas: {sorted(overlap)}")
    names = tuple((*mutation.replacements, *mutation.additions))
    _validate_unique_part_identities(names, "Mutación OOXML")
    unknown = set(mutation.replacements) - set(package.parts)
    if unknown:
        raise ValueError(f"Reemplazos inexistentes: {sorted(unknown)}")
    base_identities = {
        canonical_part_identity(name): name for name in package.parts
    }
    collisions = {
        name: base_identities[canonical_part_identity(name)]
        for name in mutation.additions
        if canonical_part_identity(name) in base_identities
    }
    if collisions:
        raise ValueError(f"identidad OOXML colisionada: {sorted(collisions)}")


def _canonical_zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_CANONICAL_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = 0o600 << 16
    return info


def assert_package_preserved(
    source: Path,
    output: Path,
    allowed_parts: AbstractSet[str],
) -> PackageAudit:
    """Comprueba que únicamente se alteraron las partes permitidas."""

    before = XlsxPackage.read(source)
    after = XlsxPackage.read(output)
    return assert_packages_preserved(before, after, allowed_parts)


def assert_packages_preserved(
    before: XlsxPackage,
    after: XlsxPackage,
    allowed_parts: AbstractSet[str],
) -> PackageAudit:
    """Audita dos paquetes ya cargados sin archivos candidatos intermedios."""

    if not isinstance(before, XlsxPackage) or not isinstance(after, XlsxPackage):
        raise TypeError("Paquetes OOXML de auditoría inválidos")
    allowed_identities = {canonical_part_identity(name) for name in allowed_parts}
    changed = {
        name
        for name in set(before.parts) | set(after.parts)
        if before.parts.get(name) != after.parts.get(name)
    }
    unexpected = {
        name
        for name in changed
        if canonical_part_identity(name) not in allowed_identities
    }
    if unexpected:
        raise ValueError(f"Partes protegidas modificadas: {sorted(unexpected)}")
    return PackageAudit(
        changed_parts=frozenset(changed),
        protected_hashes=before.hashes(exclude=allowed_parts),
    )


def relationship_part_name(owner: str) -> str:
    """Devuelve la ruta OPC de relaciones de una parte propietaria."""

    _validate_part_name(owner)
    directory, filename = posixpath.split(owner)
    return posixpath.join(directory, "_rels", filename + ".rels")


def relationship_type_uris(name: str) -> frozenset[str]:
    """URI transitional exacta permitida para una relación Office."""

    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name) is None:
        raise ValueError("Nombre de relación Office inválido")
    return frozenset(f"{base}/{name}" for base in OFFICE_DOCUMENT_RELATIONSHIP_BASES)


def validate_part_name(name: str) -> None:
    """Valida públicamente una ruta OPC antes de exponerla en metadatos."""

    _validate_part_name(name)


def canonical_part_identity(name: str) -> str:
    """Identidad canónica usada para detectar aliases de una parte OPC."""

    _validate_part_name(name)
    return unicodedata.normalize("NFC", name).casefold()


def relationship_owner(rels_name: str) -> str | None:
    """Devuelve el propietario OPC de una parte .rels validada."""

    return _relationship_owner(rels_name)


def resolve_internal_target(owner: str | None, target: str) -> str:
    """Resuelve un Target interno con las reglas de traversal del paquete."""

    return _resolve_internal_target(owner, target)


def rewrite_relationship_targets(
    closure: Mapping[str, bytes],
    allocation: Mapping[str, str],
) -> dict[str, bytes]:
    """Reubica una clausura y reescribe targets relativos desde cada owner."""

    if set(closure) != set(allocation):
        raise ValueError("Asignación OOXML incompleta")
    rewritten: dict[str, bytes] = {}
    for source_name, content in closure.items():
        target_name = allocation[source_name]
        _validate_part_name(target_name)
        if not source_name.endswith(".rels"):
            rewritten[target_name] = bytes(content)
            continue
        source_owner = _relationship_owner(source_name)
        target_owner = _relationship_owner(target_name)
        if source_owner is None or target_owner is None:
            raise ValueError(f"Clausura OOXML sin propietario: {source_name}")
        if allocation.get(source_owner) != target_owner:
            raise ValueError(f"Asignación de relaciones inconsistente: {source_name}")
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as error:
            raise ValueError(f"Relaciones OOXML invalidas: {source_name}") from error
        relationships = _parse_relationships(source_name, content)
        for element, relationship in zip(list(root), relationships, strict=True):
            if relationship.get("TargetMode", "").casefold() == "external":
                continue
            original_target = relationship["Target"]
            target_path, separator, fragment = original_target.partition("#")
            resolved = _resolve_internal_target(source_owner, target_path)
            allocated_target = allocation.get(resolved)
            if allocated_target is None:
                raise ValueError(f"Asignación OOXML sin destino: {resolved}")
            relative = posixpath.relpath(
                allocated_target,
                posixpath.dirname(target_owner),
            )
            element.attrib["Target"] = relative + (
                separator + fragment if separator else ""
            )
        with XML_SERIALIZATION_LOCK:
            rewritten[target_name] = ElementTree.tostring(
                root, encoding="utf-8", xml_declaration=True
            )
    if len(rewritten) != len(closure):
        raise ValueError("Asignación OOXML colisionada")
    return rewritten


def _duplicates(values: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _validated_bytes_mapping(value: Mapping[str, bytes], label: str) -> dict[str, bytes]:
    if not isinstance(value, Mapping):
        raise TypeError(f"Mapa de {label} OOXML inválido")
    result: dict[str, bytes] = {}
    for name, content in value.items():
        _validate_part_name(name)
        if not isinstance(content, bytes):
            raise TypeError(f"Contenido de {label} OOXML inválido: {name}")
        result[name] = bytes(content)
    return result


def _validate_unique_part_identities(names: tuple[str, ...], label: str) -> None:
    owners: dict[str, str] = {}
    collisions: set[str] = set()
    for name in names:
        identity = canonical_part_identity(name)
        previous = owners.get(identity)
        if previous is not None and previous != name:
            collisions.update((previous, name))
        owners[identity] = name
    if collisions:
        raise ValueError(f"{label} con identidad duplicada: {sorted(collisions)}")


def _preflight_archive(entries: list[zipfile.ZipInfo]) -> None:
    if len(entries) > MAX_ZIP_ENTRIES:
        raise ValueError("El paquete ZIP excede el límite de entradas")
    names = tuple(item.filename for item in entries)
    duplicates = _duplicates(names)
    if duplicates:
        raise ValueError(f"Partes ZIP duplicadas: {sorted(duplicates)}")
    _validate_unique_part_identities(names, "Parte ZIP")
    total = 0
    for item in entries:
        _validate_part_name(item.filename)
        if item.flag_bits & 0x1:
            raise ValueError(f"Parte ZIP cifrada no permitida: {item.filename}")
        if item.compress_type not in _SUPPORTED_COMPRESSION_TYPES:
            raise ValueError(f"Compresión ZIP no permitida: {item.filename}")
        if item.file_size < 0 or item.compress_size < 0:
            raise ValueError(f"Tamaño ZIP inválido: {item.filename}")
        if item.file_size > MAX_ZIP_PART_BYTES:
            raise ValueError(f"Parte ZIP excede el límite por parte: {item.filename}")
        total += item.file_size
        if total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("El paquete ZIP excede el límite total descomprimido")
        if item.file_size:
            if item.compress_size == 0:
                raise ValueError(f"Parte ZIP con ratio de compresión inválido: {item.filename}")
            ratio = item.file_size / item.compress_size
            if ratio > MAX_ZIP_COMPRESSION_RATIO:
                raise ValueError(
                    f"Parte ZIP excede el ratio de compresión: {item.filename}"
                )


def _validate_part_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("Ruta OOXML vacia")
    if name.startswith(("/", "\\")) or ntpath.isabs(name):
        raise ValueError(f"Ruta absoluta OOXML: {name}")
    if "\\" in name or ":" in name or any(character in name for character in "%?#"):
        raise ValueError(f"Ruta OOXML invalida: {name}")
    segments = name.split("/")
    if any(not segment or segment == "." for segment in segments):
        raise ValueError(f"Ruta OOXML ambigua: {name}")
    if ".." in segments:
        raise ValueError(f"Ruta con traversal OOXML: {name}")
    if posixpath.normpath(name) != name:
        raise ValueError(f"Ruta OOXML no canónica: {name}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
        raise ValueError(f"Ruta OOXML invalida: {name}")


def _relationship_owner(rels_name: str) -> str | None:
    if rels_name == "_rels/.rels":
        return None
    directory, filename = posixpath.split(rels_name)
    if not filename.endswith(".rels"):
        raise ValueError(f"Ruta de relaciones OOXML invalida: {rels_name}")
    if directory == "_rels":
        owner_directory = ""
    elif directory.endswith("/_rels"):
        owner_directory = directory[: -len("/_rels")]
    else:
        raise ValueError(f"Ruta de relaciones OOXML invalida: {rels_name}")
    owner_filename = filename[: -len(".rels")]
    if not owner_filename:
        raise ValueError(f"Ruta de relaciones OOXML invalida: {rels_name}")
    return posixpath.join(owner_directory, owner_filename)


def _parse_relationships(rels_name: str, content: bytes) -> list[dict[str, str]]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError(f"Relaciones OOXML invalidas: {rels_name}") from error
    relationships_tag = f"{{{PACKAGE_RELATIONSHIPS}}}Relationships"
    relationship_tag = f"{{{PACKAGE_RELATIONSHIPS}}}Relationship"
    if root.tag != relationships_tag or any(
        element.tag != relationship_tag for element in root
    ):
        raise ValueError(f"Relaciones OOXML invalidas: {rels_name}")
    return [dict(element.attrib) for element in root]


def _resolve_internal_target(owner: str | None, target: str) -> str:
    target_path = target.split("#", maxsplit=1)[0]
    _validate_relationship_target(target_path)
    owner_directory = "" if owner is None else posixpath.dirname(owner)
    resolved = posixpath.normpath(posixpath.join(owner_directory, target_path))
    if resolved == ".." or resolved.startswith("../"):
        raise ValueError(f"Ruta con traversal OOXML: {target}")
    _validate_part_name(resolved)
    return resolved


def _validate_relationship_target(target: str) -> None:
    if not target:
        raise ValueError("Ruta OOXML vacia")
    if target.startswith(("/", "\\")) or ntpath.isabs(target):
        raise ValueError(f"Ruta absoluta OOXML: {target}")
    if "\\" in target or ":" in target or any(character in target for character in "%?"):
        raise ValueError(f"Ruta OOXML invalida: {target}")
    segments = target.split("/")
    if any(not segment or segment == "." for segment in segments):
        raise ValueError(f"Ruta OOXML ambigua: {target}")


def _parse_content_types(
    content: bytes | None,
) -> tuple[dict[str, str], dict[str, str]]:
    if content is None:
        raise ValueError("Content types OOXML ausentes")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError("Content types OOXML inválidos") from error
    if root.tag != f"{{{CONTENT_TYPES}}}Types":
        raise ValueError("Content types OOXML inválidos")
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    override_identities: set[str] = set()
    for child in root:
        if child.tag == f"{{{CONTENT_TYPES}}}Default":
            extension = child.get("Extension", "").casefold()
            content_type = child.get("ContentType", "")
            if not extension or not content_type or extension in defaults:
                raise ValueError("Content type OOXML duplicado o inválido")
            defaults[extension] = content_type
        elif child.tag == f"{{{CONTENT_TYPES}}}Override":
            part_name = child.get("PartName", "")
            content_type = child.get("ContentType", "")
            if not part_name.startswith("/") or not content_type:
                raise ValueError("Content type OOXML inválido")
            normalized = part_name[1:]
            _validate_part_name(normalized)
            identity = canonical_part_identity(normalized)
            if normalized in overrides or identity in override_identities:
                raise ValueError("Content type OOXML duplicado o inválido")
            overrides[normalized] = content_type
            override_identities.add(identity)
        else:
            raise ValueError("Content types OOXML inválidos")
    return defaults, overrides
