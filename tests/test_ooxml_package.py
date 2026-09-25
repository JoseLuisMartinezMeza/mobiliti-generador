from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

import mobiliti_saas.quote_engine.ooxml_package as ooxml_package_module
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


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"


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


def test_audit_accepts_internal_target_normalized_within_package(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "relative.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("xl/media/image1.png", b"image")
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            _relationships(("rId1", "../media/image1.png", None)),
        )

    XlsxPackage.read(source).audit()


def test_audit_accepts_root_owner_relationship_part(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "root-owner.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("foo.xml", b"<foo/>")
        archive.writestr(
            "_rels/foo.xml.rels",
            _relationships(("rId1", "xl/styles.xml", None)),
        )

    XlsxPackage.read(source).audit()


def test_audit_rejects_external_relationship_without_target(tmp_path):
    source = make_minimal_xlsx_package(tmp_path / "external-without-target.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            (
                f'<Relationships xmlns="{RELATIONSHIPS_NAMESPACE}">'
                '<Relationship Id="rId1" Type="test" TargetMode="External"/>'
                "</Relationships>"
            ).encode("utf-8"),
        )

    with pytest.raises(ValueError, match="Relacion OOXML sin destino"):
        XlsxPackage.read(source).audit()


@pytest.mark.parametrize(
    "content",
    [
        b"<NotRelationships/>",
        (
            f'<Relationships xmlns="{RELATIONSHIPS_NAMESPACE}">'
            "<Unexpected/>"
            "</Relationships>"
        ).encode("utf-8"),
        (
            f'<Relationships xmlns="{RELATIONSHIPS_NAMESPACE}">'
            '<Relationship xmlns="" Id="rId1" Type="test" Target="../../styles.xml"/>'
            "</Relationships>"
        ).encode("utf-8"),
    ],
)
def test_audit_rejects_invalid_relationship_document(tmp_path, content):
    source = make_minimal_xlsx_package(tmp_path / "invalid-rels.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", content)

    with pytest.raises(ValueError, match="Relaciones OOXML invalidas"):
        XlsxPackage.read(source).audit()


def test_audit_accepts_promoted_official_template():
    XlsxPackage.read(OFFICIAL_TEMPLATE).audit()


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("/xl/styles.xml", "Ruta absoluta"),
        ("../../../styles.xml", "Ruta con traversal"),
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


def test_audit_rejects_casefold_colliding_zip_part_names(tmp_path: Path) -> None:
    source = make_minimal_xlsx_package(tmp_path / "casefold-collision.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("XL/styles.xml", b"<styleSheet/>")

    with pytest.raises(ValueError, match="identidad.*duplicada"):
        XlsxPackage.read(source)


@pytest.mark.parametrize(
    "part_name",
    (
        "custom//item.xml",
        "custom/./item.xml",
        "custom/%2e%2e/item.xml",
        "custom/item.xml?query=1",
        "custom/item.xml#fragment",
        "custom/directory/",
    ),
)
def test_audit_rejects_ambiguous_or_aliasing_part_names(
    tmp_path: Path,
    part_name: str,
) -> None:
    source = make_minimal_xlsx_package(tmp_path / "ambiguous-name.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr(part_name, b"<part/>")

    with pytest.raises(ValueError, match="Ruta OOXML"):
        XlsxPackage.read(source)


def test_mutation_rejects_casefold_collision_with_existing_part(tmp_path: Path) -> None:
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    output = tmp_path / "must-not-exist.xlsx"

    with pytest.raises(ValueError, match="identidad.*colisionada"):
        XlsxPackage.read(source).write_new(
            output,
            PackageMutation(additions={"XL/styles.xml": b"<styleSheet/>"}),
        )

    assert not output.exists()


def test_zip_preflight_rejects_high_compression_ratio_before_loading_parts(
    tmp_path: Path,
) -> None:
    source = make_minimal_xlsx_package(tmp_path / "compression-bomb.xlsx")
    with ZipFile(source, "a", ZIP_DEFLATED) as archive:
        archive.writestr("custom/compression-bomb.bin", b"0" * (2 * 1024 * 1024))

    with pytest.raises(ValueError, match="ratio de compresión"):
        XlsxPackage.read(source)


def test_zip_preflight_enforces_per_part_limit_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_minimal_xlsx_package(tmp_path / "oversized-part.xlsx")
    monkeypatch.setattr(ooxml_package_module, "MAX_ZIP_PART_BYTES", 8, raising=False)

    with pytest.raises(ValueError, match="límite por parte"):
        XlsxPackage.read(source)


def test_package_serialization_is_byte_deterministic_with_additions(tmp_path: Path) -> None:
    source = make_minimal_xlsx_package(tmp_path / "source.xlsx")
    package = XlsxPackage.read(source)
    first_mutation = PackageMutation(
        additions={
            "custom/a.xml": b"<a/>",
            "custom/b.xml": b"<b/>",
        }
    )
    reversed_mutation = PackageMutation(
        additions={
            "custom/b.xml": b"<b/>",
            "custom/a.xml": b"<a/>",
        }
    )

    first = package.to_bytes(first_mutation)
    second = package.to_bytes(reversed_mutation)

    assert first == second
    assert XlsxPackage.from_bytes(first).parts["custom/a.xml"] == b"<a/>"


@pytest.mark.parametrize(
    "protected_name",
    (
        "XL/EXTERNALLINKS/externalLink1.xml",
        "XL/RICHDATA/richValue1.xml",
        "xl/ExternalLinks/externalLink1.xml",
    ),
)
def test_allocation_rejects_ascii_case_variants_of_protected_prefixes(
    protected_name: str,
) -> None:
    package = XlsxPackage.read(OFFICIAL_TEMPLATE)

    with pytest.raises(ValueError, match="Prefijo OOXML protegido"):
        package.allocate_closure({protected_name: b"<part/>"}, prefix="safe")


def test_unicode_part_identity_does_not_collapse_sharp_s_to_ascii_ss(
) -> None:
    package = XlsxPackage.read(OFFICIAL_TEMPLATE)
    payload = package.to_bytes(
        PackageMutation(
            additions={
                "custom/straße.xml": b"<sharp-s/>",
                "custom/strasse.xml": b"<ascii-ss/>",
            }
        )
    )

    with ZipFile(BytesIO(payload)) as archive:
        assert archive.read("custom/straße.xml") == b"<sharp-s/>"
        assert archive.read("custom/strasse.xml") == b"<ascii-ss/>"


def test_existing_zip_entries_follow_declared_safe_metadata_profile() -> None:
    info = ZipInfo("custom/data.bin", date_time=(2024, 2, 3, 4, 5, 6))
    info.compress_type = ZIP_STORED
    info.flag_bits = 0x08
    info.create_system = 0
    info.create_version = 63
    info.extract_version = 10
    info.external_attr = 0x20
    info.internal_attr = 1
    info.comment = b"stable-entry-comment"
    info.extra = b"\x55\x54\x05\x00\x01\x01\x02\x03\x04"
    package = XlsxPackage(
        path=None,
        infos={info.filename: info},
        parts={info.filename: b"exact-part-bytes"},
        archive_names=(info.filename,),
    )

    payload = package.to_bytes(
        PackageMutation(replacements={info.filename: b"exact-part-bytes"})
    )

    with ZipFile(BytesIO(payload)) as archive:
        result = archive.getinfo(info.filename)
        assert archive.read(info.filename) == b"exact-part-bytes"
        assert result.date_time == info.date_time
        assert result.compress_type == ZIP_STORED
        assert result.comment == info.comment
        assert result.internal_attr == info.internal_attr
        assert result.flag_bits & 0x08 == 0
        assert result.extra == b""
        assert result.create_system == 3
        assert result.create_version == 20
        assert result.extract_version == 20
        assert result.external_attr == 0o600 << 16
