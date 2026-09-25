"""Utilidades para construir paquetes OOXML mínimos en pruebas."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"


def make_minimal_xlsx_package(path: Path) -> Path:
    """Crea un paquete XLSX mínimo, válido para auditorías de relaciones."""

    parts = {
        "[Content_Types].xml": b"<Types/>",
        "_rels/.rels": _relationships(("rId1", "xl/workbook.xml", None)),
        "xl/workbook.xml": b"<workbook/>",
        "xl/_rels/workbook.xml.rels": _relationships(
            ("rId1", "worksheets/sheet1.xml", None),
            ("rId2", "styles.xml", None),
        ),
        "xl/worksheets/sheet1.xml": b"<worksheet/>",
        "xl/styles.xml": b"<styleSheet/>",
    }
    return _write_package(path, parts)


def make_package_with_dangling_relationship(path: Path) -> Path:
    """Crea un paquete cuyo workbook apunta a una parte inexistente."""

    parts = {
        "[Content_Types].xml": b"<Types/>",
        "_rels/.rels": _relationships(("rId1", "xl/workbook.xml", None)),
        "xl/workbook.xml": b"<workbook/>",
        "xl/_rels/workbook.xml.rels": _relationships(
            ("rId1", "worksheets/missing.xml", None),
        ),
    }
    return _write_package(path, parts)


def _relationships(*relationships: tuple[str, str, str | None]) -> bytes:
    items = []
    for relationship_id, target, target_mode in relationships:
        mode = f' TargetMode="{target_mode}"' if target_mode else ""
        items.append(f'<Relationship Id="{relationship_id}" Type="test" Target="{target}"{mode}/>')
    return (
        f'<Relationships xmlns="{RELATIONSHIPS}">{"".join(items)}</Relationships>'
    ).encode("utf-8")


def _write_package(path: Path, parts: dict[str, bytes]) -> Path:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return path
