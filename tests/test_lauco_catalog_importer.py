from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest


def _unsafe_xlsb(*members: tuple[str, bytes]) -> bytes:
    """Construye un paquete deliberadamente minimo para probar el prefiltro ZIP."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", b"<Types/>")
        package.writestr("xl/workbook.bin", b"workbook")
        for name, value in members:
            package.writestr(name, value)
    return output.getvalue()


def _minimal_xlsb(
    content_type: str,
    *,
    inert_https_hyperlink: bool = False,
    inert_http_hyperlink: bool = False,
) -> bytes:
    content_types = (
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        b'<Default Extension="bin" ContentType="' + content_type.encode("ascii") + b'"/>'
        b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        b'<Default Extension="xml" ContentType="application/xml"/>'
        b"</Types>"
    )
    root_rels = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.bin"/>'
        b"</Relationships>"
    )
    workbook_rels = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.bin"/>'
        b"</Relationships>"
    )
    if inert_https_hyperlink or inert_http_hyperlink:
        target = b"http://www.mobiliti.mx/" if inert_http_hyperlink else b"https://example.invalid/catalog"
        workbook_rels = workbook_rels.replace(
            b"</Relationships>",
            b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            b'Target="' + target + b'" TargetMode="External"/>'
            b"</Relationships>",
        )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("xl/workbook.bin", b"workbook")
        package.writestr("_rels/.rels", root_rels)
        package.writestr("xl/_rels/workbook.bin.rels", workbook_rels)
        package.writestr("xl/worksheets/sheet1.bin", b"sheet")
    return output.getvalue()


def test_parser_uses_column_f_never_k_and_preserves_mxn_provenance():
    from mobiliti_saas.worker.catalog_sync.importers.lauco import parse_lauco_rows

    rows = (
        {"row": 8, "B": "LAU-01", "C": "Sofá Lauco", "D": "220 cm", "E": "Tapiz Grado 1", "F": 11780, "G": "USD", "K": 999999},
        {"row": 9, "B": "", "C": "", "D": "", "E": "Tapiz Grado 2", "F": 13500, "G": "MXN", "K": 888888},
        {"row": 10, "B": "", "C": "", "D": "", "E": "Patas cromadas", "F": 135, "G": "MXN", "K": 777777},
    )

    options = parse_lauco_rows(rows, file_id="lauco-source", source_hash="a" * 64)
    grade_1 = next(option for option in options if option["name"] == "Tapiz Grado 1")
    grade_2 = next(option for option in options if option["name"] == "Tapiz Grado 2")
    chrome_legs = next(option for option in options if option["name"] == "Patas cromadas")

    assert grade_1["raw_cost"] == Decimal("11780")
    assert grade_1["raw_cost"] != Decimal("999999")
    assert grade_1["base_currency"] == "MXN"
    assert grade_1["provenance"]["declared_currency"] == "USD"
    assert grade_1["provenance"]["currency_normalization"] == "human_source_error_to_mxn"
    assert grade_1["option_kind"] == "base"
    assert grade_2["option_kind"] == "base"
    assert chrome_legs["option_kind"] == "add_on"
    assert chrome_legs["compatible_base_option_ids"] == [grade_1["id"], grade_2["id"]]


@pytest.mark.parametrize(
    ("member", "value"),
    (
        ("../escape.bin", b"x"),
        ("xl/vbaProject.bin", b"x"),
        ("xl/embeddings/oleObject1.bin", b"x"),
        ("xl/activeX/activeX1.bin", b"x"),
        ("xl/connections.xml", b"<connections/>"),
        ("xl/externalLinks/externalLink1.bin", b"x"),
    ),
)
def test_xlsb_reader_rejects_unsafe_package_members(member, value):
    from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    with pytest.raises(SourceSafetyError, match="XLSB_UNSAFE"):
        read_validated_xlsb_source(_unsafe_xlsb((member, value)))


def test_xlsb_reader_rejects_external_link_relationships():
    from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    relationship = (
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink" '
        b'Target="https://example.invalid/catalog" TargetMode="External"/>'
        b"</Relationships>"
    )
    with pytest.raises(SourceSafetyError, match="XLSB_UNSAFE"):
        read_validated_xlsb_source(_unsafe_xlsb(("xl/_rels/workbook.bin.rels", relationship)))


def test_xlsb_reader_ignores_inert_https_hyperlinks_without_following_them():
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    source = read_validated_xlsb_source(
        _minimal_xlsb(
            "application/vnd.ms-excel.sheet.binary.macroEnabled.main",
            inert_https_hyperlink=True,
        )
    )

    assert source.sha256


def test_xlsb_reader_ignores_inert_http_hyperlinks_from_official_legacy_metadata():
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    source = read_validated_xlsb_source(
        _minimal_xlsb(
            "application/vnd.ms-excel.sheet.binary.macroEnabled.main",
            inert_http_hyperlink=True,
        )
    )

    assert source.sha256


def test_xlsb_reader_rejects_excessive_compression_ratio():
    from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    with pytest.raises(SourceSafetyError, match="XLSB_LIMIT"):
        read_validated_xlsb_source(_unsafe_xlsb(("xl/media/padded.bin", b"A" * 100_000)))


def test_xlsb_reader_rejects_unsafe_content_type():
    from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    with pytest.raises(SourceSafetyError, match="XLSB_UNSAFE"):
        read_validated_xlsb_source(_minimal_xlsb("application/vnd.ms-office.vbaProject"))


@dataclass(frozen=True)
class _AdapterFile:
    path: str
    kind: str
    brand: str | None
    sha256: str
    mime_type: str
    local_path: Path


def test_official_lauco_xlsb_reads_cached_f_costs_and_builds_service_snapshot():
    from mobiliti_saas.quote_engine.supplier_catalog import PUBLIC_ITEM_FIELDS
    from mobiliti_saas.worker.catalog_sync.importers.lauco import build_lauco_snapshot_with_assets
    from mobiliti_saas.worker.catalog_sync.xlsb_source import read_validated_xlsb_source

    source = Path("tmp/jome-lauco-source/Spec Guide Lauco-2026.xlsb")
    if not source.exists():
        pytest.skip("No está disponible la copia oficial de Lauco")

    validated = read_validated_xlsb_source(source.read_bytes())
    first_rows = list(validated.iter_rows("COSTO-LAUCO-2026"))[:11]
    assert first_rows[9][4] == "Tapiz Grado 1"
    assert first_rows[9][5] == 11780
    assert first_rows[9][10] == 23560

    build = build_lauco_snapshot_with_assets(
        (_AdapterFile(
            "Spec Guide Lauco-2026.xlsb",
            "spec_guide",
            "Lauco",
            validated.sha256,
            "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
            source,
        ),)
    )
    snapshot = build.snapshot
    first = next(item for item in snapshot["items"] if item["sku"] == "A4 1P")

    assert snapshot["supplier"] == "lauco"
    assert all(set(item) == set(PUBLIC_ITEM_FIELDS) for item in snapshot["items"])
    assert first["base_currency"] == "MXN"
    assert [option["price_net"] for option in first["base_price_options"]] == [
        "11780.000000", "14990.000000",
    ]
    assert first["attributes"]["price_evidence"][0]["raw_cost"] == "11780"
    assert first["attributes"]["price_evidence"][0]["declared_currency"] == "MXN"
    assert all(option["price_net"] != "23560.000000" for option in first["base_price_options"])
