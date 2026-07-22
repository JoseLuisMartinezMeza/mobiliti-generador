"""Lectura, mutación write-once y auditoría de paquetes OOXML."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import ntpath
from pathlib import Path
import posixpath
from typing import AbstractSet, Mapping
from xml.etree import ElementTree
import zipfile


PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class PackageMutation:
    """Partes a reemplazar o agregar en una única escritura de paquete."""

    replacements: Mapping[str, bytes] = field(default_factory=dict)
    additions: Mapping[str, bytes] = field(default_factory=dict)

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

    path: Path
    infos: Mapping[str, zipfile.ZipInfo]
    parts: Mapping[str, bytes]
    archive_names: tuple[str, ...] = ()

    @classmethod
    def read(cls, path: Path) -> "XlsxPackage":
        """Lee un paquete y falla cerrada si su estructura OOXML es insegura."""

        path = Path(path)
        with zipfile.ZipFile(path, "r") as archive:
            entries = archive.infolist()
            names = tuple(item.filename for item in entries)
            infos = {item.filename: item for item in entries}
            parts = {item.filename: archive.read(item) for item in entries}
        package = cls(path=path, infos=infos, parts=parts, archive_names=names)
        package.audit()
        return package

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
        for name in names:
            _validate_part_name(name)

        for rels_name in (name for name in names if name.endswith(".rels")):
            owner = _relationship_owner(rels_name)
            if owner is not None and owner not in self.parts:
                raise ValueError(f"Relacion OOXML sin propietario: {rels_name} -> {owner}")
            relationships = _parse_relationships(rels_name, self.parts[rels_name])
            for relationship in relationships:
                relationship_id = relationship.get("Id")
                if not relationship_id:
                    raise ValueError(f"Relacion OOXML sin Id: {rels_name}")
            duplicate_ids = _duplicates(
                tuple(relationship["Id"] for relationship in relationships)
            )
            if duplicate_ids:
                raise ValueError(
                    f"IDs de relacion duplicados: {rels_name} -> {sorted(duplicate_ids)}"
                )

            for relationship in relationships:
                if relationship.get("TargetMode", "").casefold() == "external":
                    continue
                target = relationship.get("Target")
                if not target:
                    raise ValueError(f"Relacion OOXML sin destino: {rels_name}")
                resolved = _resolve_internal_target(owner, target)
                if resolved not in self.parts:
                    raise ValueError(
                        f"Relacion OOXML sin destino: {rels_name} -> {resolved}"
                    )

    def write_new(self, output: Path, mutation: PackageMutation) -> None:
        """Escribe un paquete nuevo preservando las partes no modificadas."""

        output = Path(output)
        if output.exists():
            raise FileExistsError(f"La salida ya existe: {output}")
        overlap = set(mutation.replacements) & set(mutation.additions)
        if overlap:
            raise ValueError(f"Partes duplicadas: {sorted(overlap)}")
        for name in (*mutation.replacements, *mutation.additions):
            _validate_part_name(name)
        unknown = set(mutation.replacements) - set(self.parts)
        if unknown:
            raise ValueError(f"Reemplazos inexistentes: {sorted(unknown)}")
        existing_additions = set(mutation.additions) & set(self.parts)
        if existing_additions:
            raise ValueError(f"Adiciones existentes: {sorted(existing_additions)}")

        with zipfile.ZipFile(
            output,
            "x",
            zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as target:
            for name, info in self.infos.items():
                target.writestr(info, mutation.replacements.get(name, self.parts[name]))
            for name in sorted(mutation.additions):
                target.writestr(name, mutation.additions[name])


def assert_package_preserved(
    source: Path,
    output: Path,
    allowed_parts: AbstractSet[str],
) -> PackageAudit:
    """Comprueba que únicamente se alteraron las partes permitidas."""

    before = XlsxPackage.read(source)
    after = XlsxPackage.read(output)
    changed = {
        name
        for name in set(before.parts) | set(after.parts)
        if before.parts.get(name) != after.parts.get(name)
    }
    unexpected = changed - set(allowed_parts)
    if unexpected:
        raise ValueError(f"Partes protegidas modificadas: {sorted(unexpected)}")
    return PackageAudit(
        changed_parts=frozenset(changed),
        protected_hashes=before.hashes(exclude=allowed_parts),
    )


def _duplicates(values: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _validate_part_name(name: str) -> None:
    if not name:
        raise ValueError("Ruta OOXML vacia")
    if name.startswith(("/", "\\")) or ntpath.isabs(name):
        raise ValueError(f"Ruta absoluta OOXML: {name}")
    if "\\" in name or ":" in name:
        raise ValueError(f"Ruta OOXML invalida: {name}")
    if ".." in name.split("/"):
        raise ValueError(f"Ruta con traversal OOXML: {name}")


def _relationship_owner(rels_name: str) -> str | None:
    if rels_name == "_rels/.rels":
        return None
    directory, filename = posixpath.split(rels_name)
    if not directory.endswith("/_rels") or not filename.endswith(".rels"):
        raise ValueError(f"Ruta de relaciones OOXML invalida: {rels_name}")
    owner_directory = directory[: -len("/_rels")]
    owner_filename = filename[: -len(".rels")]
    if not owner_filename:
        raise ValueError(f"Ruta de relaciones OOXML invalida: {rels_name}")
    return posixpath.join(owner_directory, owner_filename)


def _parse_relationships(rels_name: str, content: bytes) -> list[dict[str, str]]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError(f"Relaciones OOXML invalidas: {rels_name}") from error
    return [
        dict(element.attrib)
        for element in root.findall(f"{{{PACKAGE_RELATIONSHIPS}}}Relationship")
    ]


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
    if "\\" in target or ":" in target:
        raise ValueError(f"Ruta OOXML invalida: {target}")
