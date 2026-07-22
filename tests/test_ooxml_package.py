from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from mobiliti_saas.quote_engine.ooxml_package import (
    PackageMutation,
    XlsxPackage,
    assert_package_preserved,
)
from ooxml_test_helpers import (
    _relationships,
    make_minimal_xlsx_package,
    make_package_with_dangling_relationship,
)


def part_bytes(path: Path, name: str) -> bytes:
    with ZipFile(path) as archive:
        return archive.read(name)


def test_write_new_changes_only_allowlisted_part(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    target = tmp_path / "target.xlsx"

    package = XlsxPackage.read(source)
    package.write_new(
        target,
        PackageMutation(replacements={"xl/worksheets/sheet1.xml": b"<changed/>"}),
    )

    audit = assert_package_preserved(
        source,
        target,
        allowed_parts={"xl/worksheets/sheet1.xml"},
    )

    assert audit.changed_parts == frozenset({"xl/worksheets/sheet1.xml"})
    assert part_bytes(target, "xl/styles.xml") == part_bytes(source, "xl/styles.xml")
    assert audit.protected_hashes["xl/styles.xml"]


def test_audit_rejects_dangling_internal_relationship(tmp_path):
    source = make_package_with_dangling_relationship(tmp_path / "bad.xlsx")

    with pytest.raises(ValueError, match="Relacion OOXML sin destino"):
        XlsxPackage.read(source).audit()


def test_audit_ignores_external_relationship_destinations(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "external.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            _relationships(("rId1", "https://example.com/image.png", "External")),
        )

    XlsxPackage.read(source).audit()


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("/xl/styles.xml", "Ruta absoluta"),
        ("../styles.xml", "Ruta con traversal"),
    ],
)
def test_audit_rejects_unsafe_internal_relationship_targets(tmp_path, target, message):
    source = make_minimal_xlsx_package(tmp_path / "unsafe.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            _relationships(("rId1", target, None)),
        )

    with pytest.raises(ValueError, match=message):
        XlsxPackage.read(source).audit()


def test_audit_rejects_duplicate_relationship_ids(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "duplicate-ids.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            _relationships(
                ("rId1", "../../styles.xml", None),
                ("rId1", "../../styles.xml", None),
            ),
        )

    with pytest.raises(ValueError, match="IDs de relacion duplicados"):
        XlsxPackage.read(source).audit()


def test_audit_rejects_duplicate_zip_names(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "duplicate-names.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("xl/styles.xml", b"<replacement/>")

    with pytest.raises(ValueError, match="Partes ZIP duplicadas"):
        XlsxPackage.read(source)


@pytest.mark.parametrize(
    ("part_name", "message"),
    [
        ("/absolute.xml", "Ruta absoluta"),
        ("xl/../traversal.xml", "Ruta con traversal"),
    ],
)
def test_audit_rejects_unsafe_zip_part_names(tmp_path, part_name, message):
    source = make_minimal_xlsx_package(tmp_path / "unsafe-name.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr(part_name, b"<part/>")

    with pytest.raises(ValueError, match=message):
        XlsxPackage.read(source)


def test_write_new_rejects_existing_output_and_invalid_mutation(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    target = tmp_path / "target.xlsx"
    target.write_bytes(b"already exists")
    package = XlsxPackage.read(source)

    with pytest.raises(FileExistsError, match="salida ya existe"):
        package.write_new(target, PackageMutation())

    with pytest.raises(ValueError, match="Reemplazos inexistentes"):
        package.write_new(
            tmp_path / "other.xlsx",
            PackageMutation(replacements={"xl/missing.xml": b"<missing/>"}),
        )


def test_write_new_rejects_overlapping_replacements_and_additions(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")

    with pytest.raises(ValueError, match="Partes duplicadas"):
        XlsxPackage.read(source).write_new(
            tmp_path / "target.xlsx",
            PackageMutation(
                replacements={"xl/styles.xml": b"<one/>"},
                additions={"xl/styles.xml": b"<two/>"},
            ),
        )


def test_write_new_adds_allowlisted_part_once(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    target = tmp_path / "target.xlsx"
    mutation = PackageMutation(additions={"custom/item.xml": b"<custom/>"})

    XlsxPackage.read(source).write_new(target, mutation)

    assert mutation.allowed_parts == frozenset({"custom/item.xml"})
    assert assert_package_preserved(
        source,
        target,
        allowed_parts=set(mutation.allowed_parts),
    ).changed_parts == frozenset({"custom/item.xml"})


def test_assert_package_preserved_rejects_protected_part_change(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    target = tmp_path / "target.xlsx"
    XlsxPackage.read(source).write_new(
        target,
        PackageMutation(replacements={"xl/styles.xml": b"<changed/>"}),
    )

    with pytest.raises(ValueError, match="Partes protegidas modificadas"):
        assert_package_preserved(
            source,
            target,
            allowed_parts={"xl/worksheets/sheet1.xml"},
        )
