from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import posixpath
from types import MappingProxyType
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from mobiliti_saas.quote_engine.ooxml_package import PackageMutation, XlsxPackage
from mobiliti_saas.quote_engine.quotation_sheets import (
    LocalDefinedName,
    SheetAddition,
    StyleTableMerger,
    build_quotation_data_sheet,
    inline_source_shared_strings,
    remap_source_styles,
    transplant_quotation,
)
from quotation_import_fixtures import build_rich_quotation_fixture


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 Oficial.xlsx"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"m": MAIN, "r": OFFICE_REL, "p": PACKAGE_REL, "ct": CONTENT_TYPES}
STRICT_OFFICE_REL = "http://purl.oclc.org/ooxml/officeDocument/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"


def test_transplanted_quotation_preserves_semantic_signature_and_official_parts(tmp_path):
    source = build_rich_quotation_fixture(
        tmp_path / "source.xlsx",
        formulas={"N9": "=G9*J9"},
        merges=["A1:N1", "B9:C9"],
        image_anchor="B9",
        print_area="A1:N40",
        hidden_rows=[12],
        state="hidden",
    )
    before_source = hashlib.sha256(source.read_bytes()).hexdigest()
    before_destination = hashlib.sha256(OFFICIAL_TEMPLATE.read_bytes()).hexdigest()

    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)
    addition = transplant_quotation(source.read_bytes(), destination)
    assert addition is not None
    output = _compose_additions(OFFICIAL_TEMPLATE, tmp_path / "output.xlsx", (addition,))

    XlsxPackage.read(output)
    assert _quotation_signature(output) == _quotation_signature(source)
    assert _part_bytes(output, _sheet_part(output, "Fletes")) == _part_bytes(
        OFFICIAL_TEMPLATE, _sheet_part(OFFICIAL_TEMPLATE, "Fletes")
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_source
    assert hashlib.sha256(OFFICIAL_TEMPLATE.read_bytes()).hexdigest() == before_destination


def test_catalog_only_output_has_no_fake_visible_quotation(tmp_path):
    addition = build_quotation_data_sheet(())

    output = _compose_additions(
        OFFICIAL_TEMPLATE,
        tmp_path / "catalog.xlsx",
        (addition,),
    )

    assert "Quotation" not in _workbook_sheet_names(output)
    assert _sheet_state(output, "Quotation_Data") == "veryHidden"
    assert transplant_quotation(_catalog_only_source(tmp_path), XlsxPackage.read(OFFICIAL_TEMPLATE)) is None


def test_transplant_resolves_non_sheetn_part_and_carries_composer_metadata(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx", state="veryHidden")

    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))

    assert addition is not None
    assert addition.name == "Quotation"
    assert addition.state == "veryHidden"
    assert addition.sheet_part in addition.parts
    assert addition.parts[addition.sheet_part] == addition.xml
    assert "original-quotation.xml" not in addition.sheet_part
    assert "xl/styles.xml" in addition.replacements
    assert "xl/styles.xml" not in addition.parts
    assert addition.relationship_type.endswith("/worksheet")
    assert addition.content_types[addition.sheet_part].endswith("worksheet+xml")
    assert {name.name for name in addition.defined_names} == {
        "_xlnm.Print_Area",
        "_xlnm.Print_Titles",
        "QuoteLocal",
    }
    names = {name.name: name.text for name in addition.defined_names}
    assert names["_xlnm.Print_Area"] == "Quotation!$A$1:$N$40"
    assert names["_xlnm.Print_Titles"] == "Quotation!$1:$7"
    assert all("localSheetId" not in name.attributes for name in addition.defined_names)
    assert isinstance(addition.parts, MappingProxyType)
    assert isinstance(addition.replacements, MappingProxyType)
    with pytest.raises(TypeError):
        addition.parts["xl/evil.xml"] = b"evil"


def test_shared_string_inlining_preserves_rich_runs_whitespace_and_phonetics(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    root = ET.fromstring(addition.xml)
    rich_cell = root.find(".//m:c[@r='A1']", NS)
    assert rich_cell is not None and rich_cell.attrib["t"] == "inlineStr"
    inline = rich_cell.find("m:is", NS)

    assert inline is not None
    assert [node.text for node in inline.findall("m:r/m:t", NS)] == [" Rich ", "Text"]
    assert inline.find("m:r/m:rPr/m:b", NS) is not None
    assert inline.find("m:rPh", NS).attrib == {"sb": "0", "eb": "4"}
    assert inline.find("m:phoneticPr", NS).attrib["type"] == "noConversion"
    assert inline.find("m:r/m:t", NS).attrib["{http://www.w3.org/XML/1998/namespace}space"] == "preserve"
    assert not root.findall(".//m:c[@t='s']", NS)
    assert not any("sharedStrings" in name for name in addition.parts)


@pytest.mark.parametrize(
    "sheet_xml, shared_strings, message",
    (
        (
            f'<worksheet xmlns="{MAIN}"><sheetData><row><c r="A1" t="s"><v>2</v></c></row></sheetData></worksheet>'.encode(),
            ("one",),
            "fuera de rango",
        ),
        (
            f'<worksheet xmlns="{MAIN}"><sheetData><row><c r="A1" t="s"/></row></sheetData></worksheet>'.encode(),
            ("one",),
            "índice",
        ),
        (
            f'<worksheet xmlns="{MAIN}"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="A1"><v>1</v></c></row></sheetData></worksheet>'.encode(),
            ("one",),
            "duplicada",
        ),
    ),
)
def test_shared_string_inlining_fails_closed_on_invalid_cells(sheet_xml, shared_strings, message):
    with pytest.raises(ValueError, match=message):
        inline_source_shared_strings(sheet_xml, shared_strings)


def test_style_merge_keeps_official_records_and_remaps_custom_numfmt_cf_and_table(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)
    original_styles = destination.parts["xl/styles.xml"]

    addition = transplant_quotation(source, destination)
    assert addition is not None
    merged_styles = addition.replacements["xl/styles.xml"]
    source_styles = _part_bytes(source, "xl/styles.xml")
    output_sheet = ET.fromstring(addition.xml)
    output_style_id = int(output_sheet.find(".//m:c[@r='A1']", NS).attrib["s"])
    output_num_fmt = _style_num_fmt(merged_styles, output_style_id)
    source_num_fmt = _style_num_fmt(source_styles, 1)

    assert output_num_fmt == source_num_fmt == '"Q-"0.000'
    assert _existing_style_records(merged_styles, original_styles)
    assert output_sheet.find(".//m:cfRule", NS).attrib["dxfId"] != "0"
    table_part = next(name for name in addition.parts if name.startswith("xl/tables/"))
    table_style_name = ET.fromstring(addition.parts[table_part]).find("m:tableStyleInfo", NS).attrib["name"]
    assert _table_style_signature(merged_styles, table_style_name) == _table_style_signature(
        source_styles, "CustomQuoteStyle"
    )
    assert _all_style_counts_are_exact(merged_styles)


def test_remap_source_styles_covers_cell_row_and_column_references(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_package = XlsxPackage.read(source)
    source_sheet = source_package.parts[source_package.sheet_part("Quotation")]

    remapped_sheet, merged = remap_source_styles(
        source_sheet,
        source_package.parts["xl/styles.xml"],
        XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"],
    )
    root = ET.fromstring(remapped_sheet)

    refs = {
        int(element.attrib[attribute])
        for xpath, attribute in ((".//m:c[@s]", "s"), (".//m:row[@s]", "s"), (".//m:col[@style]", "style"))
        for element in root.findall(xpath, NS)
    }
    assert refs
    assert all(_style_num_fmt(merged, style_id) == '"Q-"0.000' for style_id in refs)


def test_remap_source_styles_materializes_a_different_source_default_style(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_styles = _part_bytes(source, "xl/styles.xml").replace(
        b'<color theme="1"/><name val="Calibri"/>',
        b'<color rgb="FF778899"/><name val="SourceDefault"/>',
        1,
    )
    sheet_xml = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Default</t></is></c></row></sheetData></worksheet>'
    ).encode()

    remapped_sheet, merged = remap_source_styles(
        sheet_xml,
        source_styles,
        XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"],
    )
    cell = ET.fromstring(remapped_sheet).find(".//m:c[@r='A1']", NS)

    assert "s" in cell.attrib
    assert _style_signature(merged, int(cell.attrib["s"])) == _style_signature(source_styles, 0)


def test_style_merge_is_idempotent_for_the_same_dependency_graph(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_styles = _part_bytes(source, "xl/styles.xml")
    merger = StyleTableMerger.from_xml(
        XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"]
    )

    first = merger.merge_referenced_styles(
        source_styles,
        {1, 2},
        dxf_ids={0},
        table_style_names={"CustomQuoteStyle"},
    )
    first_xml = merger.to_xml()
    second = merger.merge_referenced_styles(
        source_styles,
        {1, 2},
        dxf_ids={0},
        table_style_names={"CustomQuoteStyle"},
    )

    assert second == first
    assert merger.to_xml() == first_xml


def test_closure_allocation_is_deterministic_collision_free_and_rewrites_relative_targets(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)

    first = transplant_quotation(source, destination)
    second = transplant_quotation(source, destination)
    assert first is not None and second is not None
    assert tuple(first.parts) == tuple(second.parts)
    occupied_destination = replace(
        destination,
        parts={**destination.parts, **first.parts},
    )
    collided = transplant_quotation(source, occupied_destination)
    assert collided is not None

    assert set(first.parts).isdisjoint(collided.parts)
    assert set(collided.parts).isdisjoint(destination.parts)
    assert all(not name.startswith(("xl/externalLinks/", "xl/richData/")) for name in collided.parts)
    _assert_all_internal_targets_resolve(collided.parts)
    assert _closure_payloads(first.parts, first.sheet_part) == _closure_payloads(
        collided.parts, collided.sheet_part
    )


def test_relationship_closure_keeps_external_hyperlink_and_binary_bytes(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_package = XlsxPackage.read(source)

    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    rels = ET.fromstring(addition.parts[_rels_name(addition.sheet_part)])
    external = [
        relationship
        for relationship in rels
        if relationship.attrib.get("TargetMode") == "External"
    ]
    media = next(name for name in addition.parts if name.startswith("xl/media/"))
    printer = next(name for name in addition.parts if name.startswith("xl/printerSettings/"))

    assert len(external) == 1
    assert external[0].attrib["Target"] == "https://example.com/spec?q=1"
    assert addition.parts[media] == source_package.parts["xl/media/image7.png"]
    assert addition.parts[printer] == source_package.parts["xl/printerSettings/printerSettings7.bin"]


def test_relationship_profiles_reject_executable_and_mismatched_images(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    content_types = _part_bytes(source, "[Content_Types].xml")
    drawing_rels = _part_bytes(source, "xl/drawings/_rels/drawing7.xml.rels")
    png = _part_bytes(source, "xl/media/image7.png")
    cases = (
        (
            "executable-as-image.xlsx",
            {"xl/media/image7.png": b"MZ" + b"\x00" * 128},
            None,
        ),
        (
            "executable-mime.xlsx",
            {
                "[Content_Types].xml": content_types.replace(
                    b'ContentType="image/png"',
                    b'ContentType="application/x-msdownload"',
                    1,
                )
            },
            None,
        ),
        (
            "png-as-jpeg.xlsx",
            {
                "xl/drawings/_rels/drawing7.xml.rels": drawing_rels.replace(
                    b"../media/image7.png", b"../media/image7.jpg"
                ),
                "xl/media/image7.jpg": png,
                "[Content_Types].xml": content_types.replace(
                    b'<Default Extension="png" ContentType="image/png"/>',
                    b'<Default Extension="png" ContentType="image/png"/><Default Extension="jpg" ContentType="image/jpeg"/>',
                ),
            },
            None,
        ),
    )

    for filename, replacements, content_type in cases:
        malformed = _rewrite_package(
            source,
            tmp_path / filename,
            replacements,
            content_type=content_type,
        )
        with pytest.raises(ValueError, match="(?i)imagen|ejecutable|firma|content type"):
            transplant_quotation(malformed, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_relationship_profiles_validate_external_schemes_target_mode_and_printer_signature(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    sheet_rels_name = "xl/worksheets/_rels/original-quotation.xml.rels"
    sheet_rels = _part_bytes(source, sheet_rels_name)
    cases = (
        (
            "javascript-link.xlsx",
            {sheet_rels_name: sheet_rels.replace(b"https://example.com/spec?q=1", b"javascript:alert(1)")},
            "(?i)esquema|externa|hyperlink",
        ),
        (
            "external-image.xlsx",
            {
                "xl/drawings/_rels/drawing7.xml.rels": _rels_document(
                    ("rIdImage", f"{OFFICE_REL}/image", "https://example.com/image.png", "External")
                )
            },
            "(?i)TargetMode|externa|image",
        ),
        (
            "invalid-printer.xlsx",
            {"xl/printerSettings/printerSettings7.bin": b"PRINTER\x00SETTINGS\xff"},
            "(?i)printer|firma|DEVMODE",
        ),
    )

    for filename, replacements, message in cases:
        malformed = _rewrite_package(source, tmp_path / filename, replacements)
        with pytest.raises(ValueError, match=message):
            transplant_quotation(malformed, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_exact_transitional_and_strict_relationship_uris_are_enforced(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    workbook_rels_name = "xl/_rels/workbook.xml.rels"
    spoofed_workbook = _rewrite_package(
        source,
        tmp_path / "spoofed-workbook.xlsx",
        {
            workbook_rels_name: _part_bytes(source, workbook_rels_name).replace(
                f'{OFFICE_REL}/worksheet'.encode(),
                b"https://attacker.invalid/worksheet",
                1,
            )
        },
    )
    spoofed_root = _rewrite_package(
        source,
        tmp_path / "spoofed-root.xlsx",
        {
            "_rels/.rels": _part_bytes(source, "_rels/.rels").replace(
                f'{OFFICE_REL}/officeDocument'.encode(),
                b"https://attacker.invalid/officeDocument",
            )
        },
    )
    strict = _rewrite_package(
        source,
        tmp_path / "strict-rel-types.xlsx",
        {
            name: payload.replace(OFFICE_REL.encode(), STRICT_OFFICE_REL.encode())
            for name, payload in _read_parts(source).items()
            if name.endswith(".rels")
        },
    )

    with pytest.raises(ValueError, match="(?i)relaci.n.*hoja|worksheet"):
        transplant_quotation(spoofed_workbook, XlsxPackage.read(OFFICIAL_TEMPLATE))
    with pytest.raises(ValueError, match="(?i)officeDocument|ra.z"):
        XlsxPackage.read(spoofed_root)
    assert transplant_quotation(strict, XlsxPackage.read(OFFICIAL_TEMPLATE)) is not None


def test_mc_ignorable_prefixes_survive_mutated_sheet_and_styles(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    worksheet_name = "xl/worksheets/original-quotation.xml"
    worksheet = _part_bytes(source, worksheet_name).replace(
        f'<worksheet xmlns="{MAIN}" xmlns:r="{OFFICE_REL}">'.encode(),
        (
            f'<worksheet xmlns="{MAIN}" xmlns:r="{OFFICE_REL}" xmlns:mc="{MC}" '
            f'xmlns:x14ac="{X14AC}" xmlns:xr="{XR}" mc:Ignorable="x14ac xr">'
        ).encode(),
    ).replace(
        b"</worksheet>",
        b'<extLst><ext uri="{fixture}"><x14ac:dyDescent val="0.25"/><xr:future/></ext></extLst></worksheet>',
    )
    styles = _part_bytes(source, "xl/styles.xml").replace(
        f'<styleSheet xmlns="{MAIN}">'.encode(),
        (
            f'<styleSheet xmlns="{MAIN}" xmlns:mc="{MC}" xmlns:xr="{XR}" '
            'mc:Ignorable="xr">'
        ).encode(),
    ).replace(
        b"</styleSheet>",
        b'<extLst><ext uri="{fixture}"><xr:future/></ext></extLst></styleSheet>',
    )
    enriched = _rewrite_package(
        source,
        tmp_path / "mc.xlsx",
        {worksheet_name: worksheet, "xl/styles.xml": styles},
    )

    addition = transplant_quotation(enriched, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    _assert_ignorable_prefixes(addition.xml, ("x14ac", "xr"))

    merger = StyleTableMerger.from_xml(styles)
    merger.merge_referenced_styles(_part_bytes(source, "xl/styles.xml"), {1})
    _assert_ignorable_prefixes(merger.to_xml(), ("xr",))


def test_mc_ignorable_rejects_undeclared_prefix(tmp_path):
    sheet = (
        f'<worksheet xmlns="{MAIN}" xmlns:mc="{MC}" mc:Ignorable="x14ac">'
        '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()

    with pytest.raises(ValueError, match="(?i)Ignorable|prefijo"):
        inline_source_shared_strings(sheet, ("valor",))


@pytest.mark.parametrize(
    "shared_string",
    (
        f'<si xmlns="{MAIN}"><t>A</t><r><t>B</t></r></si>',
        f'<si xmlns="{MAIN}"><r><t>A</t><t>B</t></r></si>',
        f'<si xmlns="{MAIN}"><phoneticPr fontId="0"/><rPh sb="0" eb="1"><t>a</t></rPh><t>A</t></si>',
        f'<si xmlns="{MAIN}"><t>A</t><rPh sb="0" eb="2"><t>a</t></rPh></si>',
    ),
)
def test_shared_string_ct_rst_rejects_invalid_cardinality_order_and_bounds(shared_string):
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()

    with pytest.raises(ValueError, match="(?i)shared string|CT_Rst|rich|fon.t"):
        inline_source_shared_strings(sheet, (shared_string.encode(),))


def test_sheet_addition_content_types_have_exact_safe_coverage():
    sheet_xml = f'<worksheet xmlns="{MAIN}"><sheetData/></worksheet>'.encode()
    valid = SheetAddition(
        name="Quotation",
        state="visible",
        xml=sheet_xml,
        parts={"xl/worksheets/q.xml": sheet_xml},
        replacements={"xl/styles.xml": b"styles"},
        content_types={
            "xl/worksheets/q.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
        },
        replacement_content_types={
            "xl/styles.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"
        },
        sheet_part="xl/worksheets/q.xml",
    )
    assert valid.content_types.keys() == valid.parts.keys()
    assert valid.replacement_content_types.keys() == valid.replacements.keys()

    with pytest.raises(ValueError, match="(?i)coverage|cobertura|content type"):
        replace(valid, content_types={})
    with pytest.raises(ValueError, match="(?i)coverage|cobertura|content type"):
        replace(valid, replacement_content_types={})
    with pytest.raises(ValueError, match="(?i)ruta|OOXML"):
        replace(
            valid,
            parts={"../q.xml": sheet_xml},
            content_types={"../q.xml": "application/xml"},
            sheet_part="../q.xml",
        )
    with pytest.raises(ValueError, match="(?i)worksheet|relaci.n"):
        replace(valid, relationship_type="https://attacker.invalid/worksheet")


def test_closure_allocation_reserves_orphan_content_type_overrides():
    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)
    root = ET.fromstring(destination.parts["[Content_Types].xml"])
    ET.SubElement(
        root,
        f"{{{CONTENT_TYPES}}}Override",
        {"PartName": "/xl/media/quotation_original1.png", "ContentType": "image/png"},
    )
    with_reserved_override = replace(
        destination,
        parts={**destination.parts, "[Content_Types].xml": _xml_bytes(root)},
    )

    allocated = with_reserved_override.allocate_closure(
        {"xl/media/source.png": b"\x89PNG\r\n\x1a\n"},
        prefix="quotation_original",
    )

    assert allocated["xl/media/source.png"] == "xl/media/quotation_original2.png"


def test_relationship_closure_rejects_cycles_dangling_targets_and_traversal(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    drawing_rels = "xl/drawings/_rels/drawing7.xml.rels"
    sheet_rels = "xl/worksheets/_rels/original-quotation.xml.rels"
    cases = (
        (
            "cycle.xlsx",
            drawing_rels,
            _rels_document(
                ("rIdCycle", f"{OFFICE_REL}/drawing", "../worksheets/original-quotation.xml", None)
            ),
            "(?i)ciclo",
        ),
        (
            "dangling.xlsx",
            drawing_rels,
            _rels_document(
                ("rIdMissing", f"{OFFICE_REL}/image", "../media/missing.png", None)
            ),
            "(?i)sin destino",
        ),
        (
            "traversal.xlsx",
            sheet_rels,
            _rels_document(
                ("rIdEscape", f"{OFFICE_REL}/drawing", "../../../escape.xml", None)
            ),
            "(?i)traversal",
        ),
    )

    for filename, part_name, content, message in cases:
        broken = _rewrite_package(source, tmp_path / filename, {part_name: content})
        with pytest.raises(ValueError, match=message):
            transplant_quotation(broken, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_transplant_rejects_active_content_and_duplicate_quotation_sheet(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    active = _rewrite_package(
        source,
        tmp_path / "active.xlsx",
        {
            "xl/vbaProject.bin": b"macro",
            "xl/_rels/workbook.xml.rels": _append_relationship(
                _part_bytes(source, "xl/_rels/workbook.xml.rels"),
                ("rIdMacro", f"{OFFICE_REL}/vbaProject", "vbaProject.bin", None),
            ),
        },
        content_type=(
            "/xl/vbaProject.bin",
            "application/vnd.ms-office.vbaProject",
        ),
    )
    duplicate_workbook = _append_sheet(
        _part_bytes(source, "xl/workbook.xml"),
        name="Quotation",
        sheet_id="18",
        relationship_id="rIdDuplicate",
    )
    duplicate = _rewrite_package(
        source,
        tmp_path / "duplicate.xlsx",
        {
            "xl/workbook.xml": duplicate_workbook,
            "xl/_rels/workbook.xml.rels": _append_relationship(
                _part_bytes(source, "xl/_rels/workbook.xml.rels"),
                (
                    "rIdDuplicate",
                    f"{OFFICE_REL}/worksheet",
                    "worksheets/original-quotation.xml",
                    None,
                ),
            ),
        },
    )

    with pytest.raises(ValueError, match="activo no permitido"):
        transplant_quotation(active, XlsxPackage.read(OFFICIAL_TEMPLATE))
    with pytest.raises(ValueError, match="duplicada"):
        transplant_quotation(duplicate, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_transplant_rejects_spoofed_relationship_type_and_duplicate_sheet_id(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    spoofed = _rewrite_package(
        source,
        tmp_path / "spoofed.xlsx",
        {
            "xl/drawings/_rels/drawing7.xml.rels": _rels_document(
                ("rIdImage", "https://invalid.example/drawing", "../media/image7.png", None)
            )
        },
    )
    duplicate_id = _rewrite_package(
        source,
        tmp_path / "duplicate-id.xlsx",
        {
            "xl/workbook.xml": _append_sheet(
                _part_bytes(source, "xl/workbook.xml"),
                name="Other",
                sheet_id="17",
                relationship_id="rId1",
            )
        },
    )

    with pytest.raises(ValueError, match="no permitida"):
        transplant_quotation(spoofed, XlsxPackage.read(OFFICIAL_TEMPLATE))
    with pytest.raises(ValueError, match="sheetId.*duplicado"):
        transplant_quotation(duplicate_id, XlsxPackage.read(OFFICIAL_TEMPLATE))


@pytest.mark.parametrize(
    "part_name",
    (
        "xl/activeX/activeX1.bin",
        "xl/ctrlProps/ctrlProp1.xml",
        "xl/customUI/customUI.xml",
        "customUI/customUI.xml",
        "xl/media/PAYLOAD.EXE",
    ),
)
def test_transplant_rejects_orphan_active_parts_by_normalized_name(tmp_path, part_name):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    active = _rewrite_package(
        source,
        tmp_path / (part_name.replace("/", "-") + ".xlsx"),
        {part_name: b"active"},
    )

    with pytest.raises(ValueError, match="activo no permitido"):
        transplant_quotation(active, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_style_merger_rejects_invalid_references_and_duplicate_style_sections(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_package = XlsxPackage.read(source)
    bad_sheet = source_package.parts[source_package.sheet_part("Quotation")].replace(
        b'<c r="A1" s="1"', b'<c r="A1" s="999"'
    )
    duplicate_sections = source_package.parts["xl/styles.xml"].replace(
        b"</fonts>", b"</fonts><fonts count=\"0\"/>", 1
    )
    wrong_namespace = source_package.parts["xl/styles.xml"].replace(
        b'<tableStyle name="CustomQuoteStyle"',
        b'<tableStyle xmlns="" name="CustomQuoteStyle"',
        1,
    )

    with pytest.raises(ValueError, match="estilo.*fuera de rango"):
        remap_source_styles(
            bad_sheet,
            source_package.parts["xl/styles.xml"],
            XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"],
        )
    with pytest.raises(ValueError, match="(?i)sección.*duplicada"):
        StyleTableMerger.from_xml(duplicate_sections)
    with pytest.raises(ValueError, match="(?i)sección.*inválida"):
        StyleTableMerger.from_xml(wrong_namespace)


def test_local_defined_name_can_be_reassigned_without_mutating_metadata():
    name = LocalDefinedName(
        name="_xlnm.Print_Area",
        text="Quotation!$A$1:$N$40",
        attributes={"hidden": "1"},
    )

    element = ET.fromstring(name.xml_for_sheet_index(12))

    assert element.attrib == {
        "name": "_xlnm.Print_Area",
        "hidden": "1",
        "localSheetId": "12",
    }
    assert element.text == "Quotation!$A$1:$N$40"
    assert "localSheetId" not in name.attributes


def _compose_additions(template: Path, output: Path, additions: tuple[SheetAddition, ...]) -> Path:
    package = XlsxPackage.read(template)
    workbook = ET.fromstring(package.parts["xl/workbook.xml"])
    workbook_rels = ET.fromstring(package.parts["xl/_rels/workbook.xml.rels"])
    content_types = ET.fromstring(package.parts["[Content_Types].xml"])
    sheets = workbook.find("m:sheets", NS)
    assert sheets is not None
    defined_names = workbook.find("m:definedNames", NS)
    if defined_names is None:
        defined_names = ET.Element(f"{{{MAIN}}}definedNames")
        workbook.insert(list(workbook).index(sheets) + 1, defined_names)
    used_relationship_ids = {relationship.attrib["Id"] for relationship in workbook_rels}
    used_parts = set(package.parts)
    replacements: dict[str, bytes] = {}
    new_parts: dict[str, bytes] = {}

    for addition in additions:
        sheet_index = len(sheets)
        sheet_id = max((int(sheet.attrib["sheetId"]) for sheet in sheets), default=0) + 1
        relationship_id = _next_relationship_id(used_relationship_ids)
        used_relationship_ids.add(relationship_id)
        if addition.sheet_part is None:
            sheet_part = _next_sheet_part(used_parts | set(new_parts), addition.name)
            addition_parts = {sheet_part: addition.xml}
            content_metadata = {
                sheet_part: "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
            }
        else:
            sheet_part = addition.sheet_part
            addition_parts = dict(addition.parts)
            content_metadata = dict(addition.content_types)
        if set(addition_parts) & (used_parts | set(new_parts)):
            raise AssertionError("Fixture compositor recibió partes colisionadas")
        new_parts.update(addition_parts)
        replacements.update(addition.replacements)

        sheet_attributes = {
            "name": addition.name,
            "sheetId": str(sheet_id),
            f"{{{OFFICE_REL}}}id": relationship_id,
        }
        if addition.state != "visible":
            sheet_attributes["state"] = addition.state
        ET.SubElement(sheets, f"{{{MAIN}}}sheet", sheet_attributes)
        ET.SubElement(
            workbook_rels,
            f"{{{PACKAGE_REL}}}Relationship",
            {
                "Id": relationship_id,
                "Type": addition.relationship_type,
                "Target": posixpath.relpath(sheet_part, "xl"),
            },
        )
        for local_name in addition.defined_names:
            defined_names.append(ET.fromstring(local_name.xml_for_sheet_index(sheet_index)))
        existing_overrides = {
            child.attrib["PartName"]
            for child in content_types.findall("ct:Override", NS)
        }
        for part_name, content_type in content_metadata.items():
            override_name = "/" + part_name
            if override_name not in existing_overrides:
                ET.SubElement(
                    content_types,
                    f"{{{CONTENT_TYPES}}}Override",
                    {"PartName": override_name, "ContentType": content_type},
                )
                existing_overrides.add(override_name)

    replacements.update(
        {
            "xl/workbook.xml": _xml_bytes(workbook),
            "xl/_rels/workbook.xml.rels": _xml_bytes(workbook_rels),
            "[Content_Types].xml": _xml_bytes(content_types),
        }
    )
    package.write_new(output, PackageMutation(replacements=replacements, additions=new_parts))
    return output


def _quotation_signature(path: Path) -> tuple:
    parts = _read_parts(path)
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    sheets = workbook.findall("m:sheets/m:sheet", NS)
    matches = [sheet for sheet in sheets if sheet.attrib["name"] == "Quotation"]
    assert len(matches) == 1
    sheet = matches[0]
    sheet_index = sheets.index(sheet)
    sheet_part = _sheet_part_from_parts(parts, "Quotation")
    root = ET.fromstring(parts[sheet_part])
    shared = _shared_strings(parts)
    styles = parts["xl/styles.xml"]

    cells = []
    for cell in root.findall(".//m:c", NS):
        style_id = int(cell.attrib.get("s", "0"))
        formula = cell.findtext("m:f", default=None, namespaces=NS)
        value = _cell_semantic_value(cell, shared)
        cells.append((cell.attrib["r"], formula, value, _style_signature(styles, style_id)))
    rows = tuple(
        (
            row.attrib.get("r"),
            row.attrib.get("hidden", "0"),
            row.attrib.get("ht"),
            _style_signature(styles, int(row.attrib["s"])) if "s" in row.attrib else None,
        )
        for row in root.findall("m:sheetData/m:row", NS)
    )
    columns = tuple(
        (
            tuple(sorted((key, value) for key, value in column.attrib.items() if key != "style")),
            _style_signature(styles, int(column.attrib["style"])) if "style" in column.attrib else None,
        )
        for column in root.findall("m:cols/m:col", NS)
    )
    conditional = tuple(
        (
            parent.attrib.get("sqref"),
            tuple(sorted((key, value) for key, value in rule.attrib.items() if key != "dxfId")),
            _dxf_signature(styles, int(rule.attrib["dxfId"])) if "dxfId" in rule.attrib else None,
            tuple(rule.itertext()),
        )
        for parent in root.findall("m:conditionalFormatting", NS)
        for rule in parent.findall("m:cfRule", NS)
    )
    direct_tags = (
        "dimension",
        "sheetViews",
        "sheetFormatPr",
        "mergeCells",
        "hyperlinks",
        "printOptions",
        "pageMargins",
        "pageSetup",
        "legacyDrawing",
        "drawing",
        "tableParts",
    )
    sheet_semantics = tuple(
        (tag, tuple(_canonical(element) for element in root.findall(f"m:{tag}", NS)))
        for tag in direct_tags
    )
    local_names = tuple(
        sorted(
            (
                name.attrib["name"],
                tuple(sorted((key, value) for key, value in name.attrib.items() if key != "localSheetId")),
                name.text or "",
            )
            for name in workbook.findall("m:definedNames/m:definedName", NS)
            if name.attrib.get("localSheetId") == str(sheet_index)
        )
    )
    return (
        sheet.attrib.get("state", "visible"),
        tuple(cells),
        rows,
        columns,
        conditional,
        sheet_semantics,
        local_names,
        _relationship_graph_signature(parts, sheet_part),
    )


def _relationship_graph_signature(parts: dict[str, bytes], start: str) -> tuple:
    def visit(owner: str, stack: tuple[str, ...]) -> tuple:
        if owner in stack:
            raise AssertionError("ciclo en firma")
        rels_name = _rels_name(owner)
        if rels_name not in parts:
            return ()
        rows = []
        for relationship in ET.fromstring(parts[rels_name]):
            attrs = relationship.attrib
            if attrs.get("TargetMode") == "External":
                rows.append((attrs["Id"], attrs["Type"], "External", attrs["Target"]))
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(owner), attrs["Target"]))
            payload = parts[target]
            if target.endswith((".png", ".bin")):
                content = payload
            else:
                content = _canonical(ET.fromstring(payload))
            rows.append(
                (
                    attrs["Id"],
                    attrs["Type"],
                    "Internal",
                    content,
                    visit(target, (*stack, owner)),
                )
            )
        return tuple(sorted(rows, key=lambda row: row[:2]))

    return visit(start, ())


def _style_signature(styles_xml: bytes, style_id: int) -> tuple:
    root = ET.fromstring(styles_xml)
    cell_xfs = root.findall("m:cellXfs/m:xf", NS)
    if style_id >= len(cell_xfs):
        raise AssertionError("style fuera de rango")
    return _xf_signature(root, cell_xfs[style_id])


def _xf_signature(root: ET.Element, xf: ET.Element) -> tuple:
    attributes = dict(xf.attrib)
    result = []
    for attribute, section, child in (
        ("fontId", "fonts", "font"),
        ("fillId", "fills", "fill"),
        ("borderId", "borders", "border"),
    ):
        index = int(attributes.pop(attribute, "0"))
        values = root.findall(f"m:{section}/m:{child}", NS)
        result.append((attribute, _canonical(values[index])))
    num_fmt_id = int(attributes.pop("numFmtId", "0"))
    result.append(("numFmt", _num_fmt_code(root, num_fmt_id)))
    xf_id = attributes.pop("xfId", None)
    if xf_id is not None:
        bases = root.findall("m:cellStyleXfs/m:xf", NS)
        result.append(("xf", _xf_signature(root, bases[int(xf_id)])))
    result.append(("attributes", tuple(sorted(attributes.items()))))
    result.append(("children", tuple(_canonical(child) for child in xf)))
    return tuple(result)


def _dxf_signature(styles_xml: bytes, dxf_id: int) -> tuple:
    root = ET.fromstring(styles_xml)
    dxf = root.findall("m:dxfs/m:dxf", NS)[dxf_id]
    return _canonical_without_num_fmt_id(dxf)


def _table_style_signature(styles_xml: bytes, name: str) -> tuple:
    root = ET.fromstring(styles_xml)
    matches = [item for item in root.findall("m:tableStyles/m:tableStyle", NS) if item.attrib["name"] == name]
    assert len(matches) == 1
    dxfs = root.findall("m:dxfs/m:dxf", NS)
    style = matches[0]
    elements = []
    for element in style.findall("m:tableStyleElement", NS):
        attributes = dict(element.attrib)
        dxf_id = int(attributes.pop("dxfId"))
        elements.append((tuple(sorted(attributes.items())), _canonical_without_num_fmt_id(dxfs[dxf_id])))
    return tuple(sorted((key, value) for key, value in style.attrib.items() if key != "name")), tuple(elements)


def _style_num_fmt(styles_xml: bytes, style_id: int) -> str:
    root = ET.fromstring(styles_xml)
    xf = root.findall("m:cellXfs/m:xf", NS)[style_id]
    return _num_fmt_code(root, int(xf.attrib.get("numFmtId", "0")))


def _num_fmt_code(root: ET.Element, num_fmt_id: int) -> str:
    for item in root.findall("m:numFmts/m:numFmt", NS):
        if int(item.attrib["numFmtId"]) == num_fmt_id:
            return item.attrib["formatCode"]
    return f"builtin:{num_fmt_id}"


def _existing_style_records(merged: bytes, original: bytes) -> bool:
    merged_root = ET.fromstring(merged)
    original_root = ET.fromstring(original)
    for section in ("numFmts", "fonts", "fills", "borders", "cellStyleXfs", "cellXfs", "cellStyles", "dxfs", "tableStyles"):
        before = original_root.find(f"m:{section}", NS)
        after = merged_root.find(f"m:{section}", NS)
        if before is None:
            continue
        if after is None or [_canonical(child) for child in list(after)[: len(before)]] != [_canonical(child) for child in before]:
            return False
    return True


def _all_style_counts_are_exact(styles_xml: bytes) -> bool:
    root = ET.fromstring(styles_xml)
    return all(
        section.attrib.get("count") == str(len(section))
        for section in root
        if section.tag.rsplit("}", 1)[-1] in {"numFmts", "fonts", "fills", "borders", "cellStyleXfs", "cellXfs", "cellStyles", "dxfs", "tableStyles"}
    )


def _canonical(element: ET.Element) -> tuple:
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        element.text or "",
        tuple(_canonical(child) for child in element),
    )


def _assert_ignorable_prefixes(content: bytes, expected: tuple[str, ...]) -> None:
    root_offset = content.find(b"<", content.find(b"?>") + 2)
    start_tag = content[root_offset : content.find(b">", root_offset)].decode("utf-8")
    root = ET.fromstring(content)
    assert root.attrib[f"{{{MC}}}Ignorable"].split() == list(expected)
    for prefix in expected:
        assert f'xmlns:{prefix}="' in start_tag
        assert f"<{prefix}:" in content.decode("utf-8")


def _canonical_without_num_fmt_id(element: ET.Element) -> tuple:
    clone = ET.fromstring(ET.tostring(element))
    for node in clone.iter():
        if node.tag == f"{{{MAIN}}}numFmt":
            node.attrib.pop("numFmtId", None)
    return _canonical(clone)


def _cell_semantic_value(cell: ET.Element, shared: tuple[ET.Element, ...]) -> tuple:
    if cell.attrib.get("t") == "s":
        index = int(cell.findtext("m:v", namespaces=NS))
        return "text", tuple(_canonical(child) for child in shared[index])
    if cell.attrib.get("t") == "inlineStr":
        inline = cell.find("m:is", NS)
        assert inline is not None
        return "text", tuple(_canonical(child) for child in inline)
    return cell.attrib.get("t", "n"), cell.findtext("m:v", default="", namespaces=NS)


def _shared_strings(parts: dict[str, bytes]) -> tuple[ET.Element, ...]:
    if "xl/sharedStrings.xml" not in parts:
        return ()
    return tuple(ET.fromstring(parts["xl/sharedStrings.xml"]).findall("m:si", NS))


def _closure_payloads(parts: dict[str, bytes], sheet_part: str) -> tuple:
    signature = _relationship_graph_signature(parts, sheet_part)
    return signature


def _assert_all_internal_targets_resolve(parts: dict[str, bytes]) -> None:
    for rels_name, payload in parts.items():
        if not rels_name.endswith(".rels"):
            continue
        owner = _rels_owner(rels_name)
        for relationship in ET.fromstring(payload):
            if relationship.attrib.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(owner), relationship.attrib["Target"])
            )
            assert target in parts


def _sheet_part(path: Path, name: str) -> str:
    return _sheet_part_from_parts(_read_parts(path), name)


def _sheet_part_from_parts(parts: dict[str, bytes], name: str) -> str:
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    relationships = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
    }
    matches = [item for item in workbook.findall("m:sheets/m:sheet", NS) if item.attrib["name"] == name]
    assert len(matches) == 1
    target = relationships[matches[0].attrib[f"{{{OFFICE_REL}}}id"]]
    return posixpath.normpath(posixpath.join("xl", target))


def _sheet_state(path: Path, name: str) -> str:
    workbook = ET.fromstring(_read_parts(path)["xl/workbook.xml"])
    match = next(item for item in workbook.findall("m:sheets/m:sheet", NS) if item.attrib["name"] == name)
    return match.attrib.get("state", "visible")


def _workbook_sheet_names(path: Path) -> tuple[str, ...]:
    workbook = ET.fromstring(_read_parts(path)["xl/workbook.xml"])
    return tuple(item.attrib["name"] for item in workbook.findall("m:sheets/m:sheet", NS))


def _part_bytes(path: Path, name: str) -> bytes:
    with ZipFile(path) as archive:
        return archive.read(name)


def _read_parts(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rewrite_package(
    source: Path,
    target: Path,
    replacements: dict[str, bytes],
    *,
    content_type: tuple[str, str] | None = None,
) -> Path:
    parts = _read_parts(source)
    parts.update(replacements)
    if content_type:
        root = ET.fromstring(parts["[Content_Types].xml"])
        ET.SubElement(
            root,
            f"{{{CONTENT_TYPES}}}Override",
            {"PartName": content_type[0], "ContentType": content_type[1]},
        )
        parts["[Content_Types].xml"] = _xml_bytes(root)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return target


def _catalog_only_source(tmp_path: Path) -> Path:
    source = build_rich_quotation_fixture(tmp_path / "catalog-source.xlsx")
    workbook = ET.fromstring(_part_bytes(source, "xl/workbook.xml"))
    sheets = workbook.find("m:sheets", NS)
    quotation = next(item for item in sheets if item.attrib["name"] == "Quotation")
    sheets.remove(quotation)
    return _rewrite_package(
        source,
        tmp_path / "catalog-only-source.xlsx",
        {"xl/workbook.xml": _xml_bytes(workbook)},
    )


def _append_relationship(content: bytes, relationship: tuple[str, str, str, str | None]) -> bytes:
    root = ET.fromstring(content)
    relationship_id, relationship_type, target, mode = relationship
    attributes = {"Id": relationship_id, "Type": relationship_type, "Target": target}
    if mode:
        attributes["TargetMode"] = mode
    ET.SubElement(root, f"{{{PACKAGE_REL}}}Relationship", attributes)
    return _xml_bytes(root)


def _append_sheet(content: bytes, *, name: str, sheet_id: str, relationship_id: str) -> bytes:
    root = ET.fromstring(content)
    sheets = root.find("m:sheets", NS)
    ET.SubElement(
        sheets,
        f"{{{MAIN}}}sheet",
        {"name": name, "sheetId": sheet_id, f"{{{OFFICE_REL}}}id": relationship_id},
    )
    return _xml_bytes(root)


def _rels_document(*relationships: tuple[str, str, str, str | None]) -> bytes:
    root = ET.Element(f"{{{PACKAGE_REL}}}Relationships")
    for relationship in relationships:
        root.append(ET.fromstring(_append_relationship(_xml_bytes(ET.Element(f"{{{PACKAGE_REL}}}Relationships")), relationship))[0])
    return _xml_bytes(root)


def _rels_name(owner: str) -> str:
    directory, filename = posixpath.split(owner)
    return posixpath.join(directory, "_rels", filename + ".rels")


def _rels_owner(rels_name: str) -> str:
    directory, filename = posixpath.split(rels_name)
    return posixpath.join(directory.removesuffix("/_rels"), filename.removesuffix(".rels"))


def _next_relationship_id(used: set[str]) -> str:
    value = 1
    while f"rId{value}" in used:
        value += 1
    return f"rId{value}"


def _next_sheet_part(used: set[str], name: str) -> str:
    stem = "quotation_data" if name == "Quotation_Data" else "quotation"
    value = 1
    while f"xl/worksheets/{stem}{value}.xml" in used:
        value += 1
    return f"xl/worksheets/{stem}{value}.xml"


def _xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
