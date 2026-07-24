from __future__ import annotations

import colorsys
from dataclasses import replace
from decimal import Decimal
import hashlib
from pathlib import Path
import posixpath
from types import MappingProxyType
from typing import Mapping
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import mobiliti_saas.quote_engine.quotation_sheets as quotation_sheets_module

from mobiliti_saas.quote_engine.ooxml_package import PackageMutation, XlsxPackage
from mobiliti_saas.quote_engine.engine import _source_product_images
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


def quotation_semantic_signature(path: Path) -> tuple:
    """Firma pública de la hoja Quotation y todo su cierre OOXML."""

    return _quotation_signature(path)


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
    assert quotation_semantic_signature(output) == quotation_semantic_signature(source)
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


def test_table_without_style_info_materializes_and_merges_custom_source_default(
    tmp_path,
):
    source = _source_without_table_style_info(
        tmp_path, default_style="CustomQuoteStyle", filename="custom-default.xlsx"
    )

    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    table_part = next(name for name in addition.parts if name.startswith("xl/tables/"))
    style_info = ET.fromstring(addition.parts[table_part]).find(
        "m:tableStyleInfo", NS
    )

    assert style_info is not None
    assert _table_style_signature(
        addition.replacements["xl/styles.xml"], style_info.attrib["name"]
    ) == _table_style_signature(_part_bytes(source, "xl/styles.xml"), "CustomQuoteStyle")


def test_table_without_style_info_is_inserted_before_ext_lst(tmp_path):
    source = _source_without_table_style_info(
        tmp_path,
        default_style="CustomQuoteStyle",
        filename="custom-default-with-ext.xlsx",
        with_ext_lst=True,
    )

    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    table_part = next(name for name in addition.parts if name.startswith("xl/tables/"))
    table = ET.fromstring(addition.parts[table_part])
    child_names = tuple(child.tag.rsplit("}", 1)[-1] for child in table)

    assert child_names[-2:] == ("tableStyleInfo", "extLst")


def test_table_without_style_info_rejects_theme_dependent_builtin_default_when_unsafe(
    tmp_path,
):
    source = _source_without_table_style_info(
        tmp_path, default_style="TableStyleMedium2", filename="unsafe-builtin.xlsx"
    )

    with pytest.raises(ValueError, match="(?i)table.*style|estilo.*tabla|tema|theme"):
        transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_table_without_style_info_materializes_builtin_default_when_theme_is_identical(
    tmp_path,
):
    source = _source_without_table_style_info(
        tmp_path, default_style="TableStyleMedium2", filename="safe-builtin.xlsx"
    )
    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)
    destination_theme = destination.workbook_related_part("theme")
    assert destination_theme is not None
    theme_safe_destination = replace(
        destination,
        parts={
            **destination.parts,
            destination_theme: _part_bytes(source, "xl/theme/theme7.xml"),
        },
    )

    addition = transplant_quotation(source, theme_safe_destination)
    assert addition is not None
    table_part = next(name for name in addition.parts if name.startswith("xl/tables/"))
    style_info = ET.fromstring(addition.parts[table_part]).find(
        "m:tableStyleInfo", NS
    )

    assert style_info is not None
    assert style_info.attrib["name"] == "TableStyleMedium2"


@pytest.mark.parametrize(
    "style_name",
    (
        "TableStyleLight1",
        "TableStyleLight21",
        "TableStyleMedium1",
        "TableStyleMedium28",
        "TableStyleDark1",
        "TableStyleDark11",
    ),
)
def test_builtin_table_style_exact_boundaries_are_allowed_with_identical_theme(
    style_name, tmp_path
):
    source = _source_without_table_style_info(
        tmp_path,
        default_style=style_name,
        filename=f"valid-{style_name}.xlsx",
    )
    destination = _destination_with_source_theme(source)

    addition = transplant_quotation(source, destination)
    assert addition is not None
    table_part = next(name for name in addition.parts if name.startswith("xl/tables/"))
    style_info = ET.fromstring(addition.parts[table_part]).find(
        "m:tableStyleInfo", NS
    )

    assert style_info is not None
    assert style_info.attrib["name"] == style_name


@pytest.mark.parametrize(
    "style_name",
    (
        "TableStyleLight0",
        "TableStyleLight22",
        "TableStyleLight999",
        "TableStyleMedium0",
        "TableStyleMedium29",
        "TableStyleMedium999",
        "TableStyleDark0",
        "TableStyleDark12",
        "TableStyleDark999",
        "None",
    ),
)
def test_builtin_table_style_out_of_range_is_rejected_even_with_identical_theme(
    style_name, tmp_path
):
    source = _source_without_table_style_info(
        tmp_path,
        default_style=style_name,
        filename=f"invalid-{style_name}.xlsx",
    )

    with pytest.raises(ValueError, match="(?i)table.*style|estilo.*tabla"):
        transplant_quotation(source, _destination_with_source_theme(source))


def test_remap_source_styles_covers_cell_row_and_column_references(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_package = XlsxPackage.read(source)
    source_sheet = source_package.parts[source_package.sheet_part("Quotation")]

    remapped_sheet, merged = remap_source_styles(
        source_sheet,
        source_package.parts["xl/styles.xml"],
        XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"],
        source_theme=source_package.parts["xl/theme/theme7.xml"],
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
    ).replace(b'<scheme val="minor"/>', b"", 1)
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
        source_theme=_part_bytes(source, "xl/theme/theme7.xml"),
    )
    first_xml = merger.to_xml()
    second = merger.merge_referenced_styles(
        source_styles,
        {1, 2},
        dxf_ids={0},
        table_style_names={"CustomQuoteStyle"},
        source_theme=_part_bytes(source, "xl/theme/theme7.xml"),
    )

    assert second == first
    assert merger.to_xml() == first_xml


def test_phonetic_font_reference_is_validated_merged_and_remapped(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    inline = ET.fromstring(addition.xml).find(".//m:c[@r='A1']/m:is", NS)
    phonetic = inline.find("m:phoneticPr", NS)
    output_fonts = ET.fromstring(addition.replacements["xl/styles.xml"]).findall(
        "m:fonts/m:font", NS
    )
    source_fonts = ET.fromstring(_part_bytes(source, "xl/styles.xml")).findall(
        "m:fonts/m:font", NS
    )

    assert _canonical(output_fonts[int(phonetic.attrib["fontId"])]) == _canonical(
        source_fonts[1]
    )

    invalid_shared = _part_bytes(source, "xl/sharedStrings.xml").replace(
        b'phoneticPr fontId="1"', b'phoneticPr fontId="999"'
    )
    invalid = _rewrite_package(
        source,
        tmp_path / "invalid-phonetic-font.xlsx",
        {"xl/sharedStrings.xml": invalid_shared},
    )
    with pytest.raises(ValueError, match="(?i)fon.t|font.*rango"):
        transplant_quotation(invalid, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_direct_table_dxf_attributes_are_all_semantically_remapped(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    source_table = ET.fromstring(_part_bytes(source, "xl/tables/table7.xml"))
    target_table_name = next(
        name for name in addition.parts if name.startswith("xl/tables/")
    )
    target_table = ET.fromstring(addition.parts[target_table_name])
    source_refs = _direct_dxf_attributes(source_table)
    target_refs = _direct_dxf_attributes(target_table)

    assert len(source_refs) == len(target_refs) == 2
    source_styles = _part_bytes(source, "xl/styles.xml")
    target_styles = addition.replacements["xl/styles.xml"]
    assert [
        _dxf_signature(target_styles, dxf_id) for _name, dxf_id in target_refs
    ] == [
        _dxf_signature(source_styles, dxf_id) for _name, dxf_id in source_refs
    ]


def test_style_merge_failure_is_transactional(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_styles = _styles_without_theme_refs(_part_bytes(source, "xl/styles.xml"))
    merger = StyleTableMerger.from_xml(
        XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"]
    )
    before = merger.to_xml()

    with pytest.raises(ValueError, match="(?i)estilo.*fuera de rango"):
        merger.merge_referenced_styles(source_styles, {1, 999})

    assert merger.to_xml() == before


def test_style_name_collisions_are_casefolded_deterministic_and_idempotent(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_styles = _styles_without_theme_refs(_part_bytes(source, "xl/styles.xml"))
    target_styles = XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"]
    target_styles = target_styles.replace(
        b"</cellStyles>",
        b'<cellStyle name="fixture style" xfId="0"/></cellStyles>',
        1,
    ).replace(
        b"</tableStyles>",
        b'<tableStyle name="customquotestyle" pivot="0" table="1" count="0"/></tableStyles>',
        1,
    )
    merger = StyleTableMerger.from_xml(target_styles)

    merger.merge_referenced_styles(
        source_styles,
        {1},
        dxf_ids={0},
        table_style_names={"CustomQuoteStyle"},
    )
    first = merger.to_xml()
    merger.merge_referenced_styles(
        source_styles,
        {1},
        dxf_ids={0},
        table_style_names={"CustomQuoteStyle"},
    )
    second = merger.to_xml()
    result = ET.fromstring(second)
    cell_names = [item.attrib["name"] for item in result.findall("m:cellStyles/m:cellStyle", NS)]
    table_names = [item.attrib["name"] for item in result.findall("m:tableStyles/m:tableStyle", NS)]
    fixture_cell_names = [
        name for name in cell_names if name.casefold().startswith("fixture style")
    ]
    fixture_table_names = [
        name for name in table_names if name.casefold().startswith("customquotestyle")
    ]

    assert first == second
    assert len({name.casefold() for name in fixture_cell_names}) == len(fixture_cell_names)
    assert len({name.casefold() for name in fixture_table_names}) == len(fixture_table_names)
    assert "Fixture Style Quotation 1" in cell_names
    assert "CustomQuoteStyle_Quotation_1" in table_names
    assert not any(
        name.endswith((" 2", "_2"))
        for name in (*fixture_cell_names, *fixture_table_names)
    )


@pytest.mark.parametrize("theme_source", ("fixture", "official"))
def test_theme_references_are_materialized_against_source_theme(theme_source, tmp_path):
    source = build_rich_quotation_fixture(tmp_path / f"{theme_source}.xlsx")
    source_styles = _part_bytes(source, "xl/styles.xml").replace(
        b'<color theme="1"/>', b'<color theme="4" tint="0.25"/>', 1
    )
    if theme_source == "fixture":
        theme = _part_bytes(source, "xl/theme/theme7.xml")
    else:
        theme = XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/theme/theme1.xml"]
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Theme</t></is></c></row></sheetData></worksheet>'
    ).encode()

    remapped, merged = remap_source_styles(
        sheet,
        source_styles,
        XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"],
        source_theme=theme,
    )
    style_id = int(ET.fromstring(remapped).find(".//m:c", NS).attrib["s"])
    font = _font_for_style(merged, style_id)
    expected_rgb, expected_typeface = _theme_font_semantics(theme, 4, "0.25", "minor")

    color = font.find("m:color", NS)
    assert color.attrib == {"rgb": expected_rgb}
    assert font.find("m:name", NS).attrib["val"] == expected_typeface
    assert font.find("m:scheme", NS) is None


@pytest.mark.parametrize(
    ("rgb", "tint", "expected"),
    (
        ("123456", "0.25", "2467AA"),
        ("336699", "0.5", "8CB2D9"),
        ("ABCDEF", "-0.4", "277BCF"),
        ("C0504D", "0.6", "E6B9B8"),
    ),
)
def test_spreadsheet_tint_uses_hls_luminance_with_independent_vectors(
    rgb, tint, expected
):
    assert quotation_sheets_module._apply_tint(rgb, Decimal(tint)) == expected


def test_semantic_signature_resolves_source_theme_in_default_style(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    worksheet_name = "xl/worksheets/original-quotation.xml"
    worksheet = ET.fromstring(_part_bytes(source, worksheet_name))
    row = worksheet.find("m:sheetData/m:row[@r='12']", NS)
    cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": "M12", "t": "inlineStr"})
    inline = ET.SubElement(cell, f"{{{MAIN}}}is")
    text = ET.SubElement(inline, f"{{{MAIN}}}t")
    text.text = "Tema fuente"
    themed_source = _rewrite_package(
        source,
        tmp_path / "theme-signature.xlsx",
        {worksheet_name: _xml_bytes(worksheet)},
    )
    addition = transplant_quotation(
        themed_source, XlsxPackage.read(OFFICIAL_TEMPLATE)
    )
    output = _compose_additions(
        OFFICIAL_TEMPLATE,
        tmp_path / "theme-output.xlsx",
        (addition,),
    )

    assert _quotation_signature(output) == _quotation_signature(themed_source)


def test_theme_references_fail_closed_when_missing_or_unsupported(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    source_styles = _part_bytes(source, "xl/styles.xml").replace(
        b'<color theme="1"/>', b'<color theme="99"/>', 1
    )
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"/></row></sheetData></worksheet>'
    ).encode()
    target_styles = XlsxPackage.read(OFFICIAL_TEMPLATE).parts["xl/styles.xml"]

    with pytest.raises(ValueError, match="(?i)tema|theme"):
        remap_source_styles(
            sheet,
            source_styles,
            target_styles,
            source_theme=_part_bytes(source, "xl/theme/theme7.xml"),
        )
    with pytest.raises(ValueError, match="(?i)tema|theme"):
        remap_source_styles(sheet, _part_bytes(source, "xl/styles.xml"), target_styles)

    drawing_name = "xl/drawings/drawing7.xml"
    drawing = _part_bytes(source, drawing_name).replace(
        b"<xdr:spPr>", b'<xdr:spPr><a:rPr typeface="+mn-ea"/>', 1
    )
    unsupported = _rewrite_package(
        source,
        tmp_path / "unsupported-theme-token.xlsx",
        {drawing_name: drawing},
    )
    with pytest.raises(ValueError, match="(?i)typeface|tema|theme"):
        transplant_quotation(unsupported, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_drawing_theme_tokens_are_materialized_with_supported_transforms(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    drawing_name = "xl/drawings/drawing7.xml"
    drawing = _part_bytes(source, drawing_name).replace(
        b"<xdr:spPr>",
        (
            b'<xdr:spPr><a:solidFill><a:schemeClr val="accent1">'
            b'<a:tint val="50000"/></a:schemeClr></a:solidFill>'
            b'<a:latin typeface="+mn-lt"/>'
        ),
        1,
    )
    themed = _rewrite_package(
        source,
        tmp_path / "drawing-theme.xlsx",
        {drawing_name: drawing},
    )

    addition = transplant_quotation(themed, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    output_drawing_name = next(
        name for name in addition.parts if name.startswith("xl/drawings/") and name.endswith(".xml")
    )
    output_drawing = addition.parts[output_drawing_name]

    assert b"schemeClr" not in output_drawing
    assert b'+mn-lt' not in output_drawing
    assert b'val="123456"' in output_drawing
    assert b'typeface="Fixture Minor"' in output_drawing
    assert b'<a:tint val="50000"' in output_drawing


@pytest.mark.parametrize("reference_tag", ("fontRef", "fillRef", "lnRef", "effectRef"))
def test_drawing_theme_style_references_fail_before_transplant_when_themes_differ(
    reference_tag, tmp_path
):
    source = build_rich_quotation_fixture(tmp_path / f"{reference_tag}.xlsx")
    drawing_name = "xl/drawings/drawing7.xml"
    drawing = _part_bytes(source, drawing_name).replace(
        b"<xdr:spPr>",
        (
            b"<xdr:spPr>"
            + f'<a:{reference_tag} idx="1"><a:schemeClr val="accent1"/></a:{reference_tag}>'.encode()
        ),
        1,
    )
    themed = _rewrite_package(
        source,
        tmp_path / f"{reference_tag}-theme-reference.xlsx",
        {drawing_name: drawing},
    )

    with pytest.raises(ValueError, match="(?i)tema|theme|formatScheme|referencia"):
        transplant_quotation(themed, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_transplant_never_replaces_official_theme(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))

    assert addition is not None
    assert "xl/theme/theme1.xml" not in addition.replacements


def test_table_identity_collisions_remap_id_name_and_all_structured_references(tmp_path):
    source = _source_with_table_identity(
        tmp_path,
        table_id=1,
        name="Table1",
        display_name="Table1",
        formula="SUM(Table1[Columna 1])",
    )
    destination = _destination_with_table_registry((1, "Table1"))

    first = transplant_quotation(source, destination)
    second = transplant_quotation(source, destination)
    assert first is not None and second is not None
    first_table_name = next(
        name for name in first.parts if name.startswith("xl/tables/")
    )
    second_table_name = next(
        name for name in second.parts if name.startswith("xl/tables/")
    )
    first_table = ET.fromstring(first.parts[first_table_name])
    second_table = ET.fromstring(second.parts[second_table_name])
    allocated_name = first_table.attrib["name"]
    destination_ids, destination_names = _destination_table_registry(destination)

    assert int(first_table.attrib["id"]) not in destination_ids
    assert allocated_name == first_table.attrib["displayName"]
    assert allocated_name.casefold() not in destination_names
    assert second_table.attrib == first_table.attrib
    assert allocated_name == "Table1_Quotation_1"
    assert allocated_name in ET.fromstring(first.xml).findtext(".//m:c[@r='N12']/m:f", namespaces=NS)
    assert allocated_name in first_table.findtext(".//m:calculatedColumnFormula", namespaces=NS)
    quote_local = next(name for name in first.defined_names if name.name == "QuoteLocal")
    assert allocated_name in quote_local.text


def test_table_formula_tokenizer_rewrites_bare_range_operands_but_never_text(tmp_path):
    source = _source_with_table_identity(
        tmp_path,
        table_id=1,
        name="Table1",
        display_name="Table1",
        formula='CONCAT("Table1",SUM(Table1))',
        filename="bare-table-reference.xlsx",
    )

    addition = transplant_quotation(
        source,
        _destination_with_table_registry((1, "Table1")),
    )
    assert addition is not None
    table_part = next(name for name in addition.parts if name.startswith("xl/tables/"))
    table = ET.fromstring(addition.parts[table_part])
    formulas = (
        ET.fromstring(addition.xml).findtext(".//m:c[@r='N12']/m:f", namespaces=NS),
        table.findtext(".//m:calculatedColumnFormula", namespaces=NS),
        next(name.text for name in addition.defined_names if name.name == "QuoteLocal"),
    )

    assert formulas == (
        'CONCAT("Table1",SUM(Table1_Quotation_1))',
        'CONCAT("Table1",SUM(Table1_Quotation_1))',
        'CONCAT("Table1",SUM(Table1_Quotation_1))',
    )


def test_table_formula_tokenizer_rewrites_all_compound_range_identifiers(tmp_path):
    formula = (
        'CONCAT("Table1:Table2",'
        "SUM(Table1[Columna 1]:Table2[Columna 2]),"
        "SUM(Table1[Columna 1]:Table1[Columna 2]),"
        "SUM(Table1:Table2),SUM((Table1,Table2)),Table1!A1,'Table2'!A1)"
    )
    source = _source_with_two_table_identities(
        tmp_path,
        formula=formula,
        filename="compound-table-ranges.xlsx",
    )

    addition = transplant_quotation(
        source,
        _destination_with_table_registry((1, "Table1"), (2, "Table2")),
    )
    assert addition is not None
    table = next(
        ET.fromstring(content)
        for name, content in addition.parts.items()
        if name.startswith("xl/tables/")
        and ET.fromstring(content).attrib["name"].startswith("Table1_Quotation_")
    )
    expected = (
        'CONCAT("Table1:Table2",'
        "SUM(Table1_Quotation_1[Columna 1]:Table2_Quotation_1[Columna 2]),"
        "SUM(Table1_Quotation_1[Columna 1]:Table1_Quotation_1[Columna 2]),"
        "SUM(Table1_Quotation_1:Table2_Quotation_1),"
        "SUM((Table1_Quotation_1,Table2_Quotation_1)),"
        "Table1!A1,'Table2'!A1)"
    )
    formulas = (
        ET.fromstring(addition.xml).findtext(
            ".//m:c[@r='N12']/m:f", namespaces=NS
        ),
        table.findtext(".//m:calculatedColumnFormula", namespaces=NS),
        next(
            name.text for name in addition.defined_names if name.name == "QuoteLocal"
        ),
    )

    assert formulas == (expected, expected, expected)


def test_table_formula_tokenizer_fails_closed_on_unbalanced_mapped_range(tmp_path):
    source = _source_with_table_identity(
        tmp_path,
        table_id=1,
        name="Table1",
        display_name="Table1",
        formula="SUM(Table1])",
        filename="unbalanced-table-range.xlsx",
    )

    with pytest.raises(ValueError, match="(?i)tabla|referencia|ambigua"):
        transplant_quotation(
            source,
            _destination_with_table_registry((1, "Table1")),
        )


def test_table_formula_tokenizer_protects_complete_sheet_and_external_qualifiers(
    tmp_path,
):
    formula = (
        'CONCAT("Table1:Table2!A1",'
        "Table1:Table2!A1,'Table1:Table2'!A1,"
        "[Book.xlsx]Table1:Table2!A1,Sheet!Table1[Columna 1])"
    )
    source = _source_with_two_table_identities(
        tmp_path,
        formula=formula,
        filename="qualified-table-ranges.xlsx",
    )

    addition = transplant_quotation(
        source,
        _destination_with_table_registry((1, "Table1"), (2, "Table2")),
    )
    assert addition is not None
    table = next(
        ET.fromstring(content)
        for name, content in addition.parts.items()
        if name.startswith("xl/tables/")
        and ET.fromstring(content).attrib["name"].startswith("Table1_Quotation_")
    )
    expected = (
        'CONCAT("Table1:Table2!A1",'
        "Table1:Table2!A1,'Table1:Table2'!A1,"
        "[Book.xlsx]Table1:Table2!A1,Sheet!Table1_Quotation_1[Columna 1])"
    )
    formulas = (
        ET.fromstring(addition.xml).findtext(
            ".//m:c[@r='N12']/m:f", namespaces=NS
        ),
        table.findtext(".//m:calculatedColumnFormula", namespaces=NS),
        next(
            name.text for name in addition.defined_names if name.name == "QuoteLocal"
        ),
    )

    assert formulas == (expected, expected, expected)


def test_table_formula_tokenizer_fails_closed_on_multiple_unquoted_qualifiers(
    tmp_path,
):
    source = _source_with_two_table_identities(
        tmp_path,
        formula="SUM(Table1!Table2!A1)",
        filename="ambiguous-qualified-table-range.xlsx",
    )

    with pytest.raises(ValueError, match="(?i)tabla|referencia|ambigua|calificador"):
        transplant_quotation(
            source,
            _destination_with_table_registry((1, "Table1"), (2, "Table2")),
        )


def test_table_identity_preflight_rejects_ambiguous_names_duplicate_destination_and_literal_refs(tmp_path):
    destination = _destination_with_table_registry((1, "Table1"), (2, "Table2"))
    ambiguous = _source_with_table_identity(
        tmp_path,
        table_id=1,
        name="Table1",
        display_name="DifferentName",
        formula="SUM(Table1[Columna 1])",
        filename="ambiguous.xlsx",
    )
    literal = _source_with_table_identity(
        tmp_path,
        table_id=1,
        name="Table1",
        display_name="Table1",
        formula='INDIRECT("Table1[Columna 1]")',
        filename="literal.xlsx",
    )
    table_parts = sorted(
        name for name in destination.parts if name.startswith("xl/tables/") and name.endswith(".xml")
    )
    duplicate_table = destination.parts[table_parts[1]].replace(
        b'id="2"', b'id="1"', 1
    )
    duplicate_destination = replace(
        destination,
        parts={**destination.parts, table_parts[1]: duplicate_table},
    )
    normal_source = build_rich_quotation_fixture(tmp_path / "normal.xlsx")

    with pytest.raises(ValueError, match="(?i)name.*displayName|identidad.*tabla"):
        transplant_quotation(ambiguous, destination)
    with pytest.raises(ValueError, match="(?i)literal|estructurada"):
        transplant_quotation(literal, destination)
    with pytest.raises(ValueError, match="(?i)id.*tabla.*duplicado|tabla.*id.*duplicado"):
        transplant_quotation(normal_source, duplicate_destination)


def test_closure_allocation_is_deterministic_collision_free_and_rewrites_relative_targets(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)

    first = transplant_quotation(source, destination)
    second = transplant_quotation(source, destination)
    assert first is not None and second is not None
    assert tuple(first.parts) == tuple(second.parts)
    reserved_content_types = ET.fromstring(destination.parts["[Content_Types].xml"])
    for part_name, content_type in first.content_types.items():
        ET.SubElement(
            reserved_content_types,
            f"{{{CONTENT_TYPES}}}Override",
            {"PartName": "/" + part_name, "ContentType": content_type},
        )
    occupied_destination = replace(
        destination,
        parts={
            **destination.parts,
            "[Content_Types].xml": _xml_bytes(reserved_content_types),
        },
    )
    collided = transplant_quotation(source, occupied_destination)
    assert collided is not None

    assert set(first.parts).isdisjoint(collided.parts)
    assert set(collided.parts).isdisjoint(destination.parts)
    assert all(not name.startswith(("xl/externalLinks/", "xl/richData/")) for name in collided.parts)
    _assert_all_internal_targets_resolve(collided.parts)
    assert _closure_payloads(
        first.parts, first.sheet_part, first.content_types
    ) == _closure_payloads(
        collided.parts, collided.sheet_part, collided.content_types
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


@pytest.mark.parametrize(
    ("extension", "content_type", "signature"),
    (
        ("png", "image/png", b"\x89PNG\r\n\x1a\nfixture"),
        ("jpg", "image/jpeg", b"\xff\xd8\xff\xe0fixture"),
        ("gif", "image/gif", b"GIF89afixture"),
        ("bmp", "image/bmp", b"BMfixture"),
        ("tiff", "image/tiff", b"II*\x00fixture"),
    ),
)
def test_all_allowed_image_profiles_preserve_independent_binary_signatures(
    extension, content_type, signature, tmp_path
):
    source = build_rich_quotation_fixture(tmp_path / f"source-{extension}.xlsx")
    rels_name = "xl/drawings/_rels/drawing7.xml.rels"
    media_name = f"xl/media/image7.{extension}"
    source_with_format = _rewrite_package(
        source,
        tmp_path / f"image-{extension}.xlsx",
        {
            rels_name: _part_bytes(source, rels_name).replace(
                b"../media/image7.png", f"../media/image7.{extension}".encode()
            ),
            media_name: signature,
        },
        content_type=("/" + media_name, content_type),
    )

    addition = transplant_quotation(
        source_with_format, XlsxPackage.read(OFFICIAL_TEMPLATE)
    )
    assert addition is not None
    output_media = next(
        name for name in addition.parts if name.endswith("." + extension)
    )

    assert addition.parts[output_media] == signature
    assert addition.content_types[output_media] == content_type


@pytest.mark.parametrize(
    ("extension", "content_type", "signature"),
    (
        ("png", "image/png", b"\x89PNG\r\n\x1a\nsemantic"),
        ("jpg", "image/jpeg", b"\xff\xd8\xff\xe0semantic"),
        ("jpeg", "image/jpeg", b"\xff\xd8\xff\xe1semantic"),
        ("gif", "image/gif", b"GIF87asemantic"),
        ("gif", "image/gif", b"GIF89asemantic"),
        ("bmp", "image/bmp", b"BMsemantic"),
        ("tif", "image/tiff", b"II*\x00semantic"),
        ("tiff", "image/tiff", b"MM\x00*semantic"),
    ),
)
def test_semantic_signature_classifies_every_valid_binary_image_profile(
    extension, content_type, signature, tmp_path
):
    source = _source_with_image_profile(
        tmp_path,
        extension=extension,
        content_type=content_type,
        signature=signature,
        filename=f"semantic-{extension}-{signature[:6].hex()}.xlsx",
    )
    addition = transplant_quotation(source, XlsxPackage.read(OFFICIAL_TEMPLATE))
    assert addition is not None
    output = _compose_additions(
        OFFICIAL_TEMPLATE,
        tmp_path / f"semantic-output-{extension}-{signature[:6].hex()}.xlsx",
        (addition,),
    )

    assert _quotation_signature(output) == _quotation_signature(source)


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
        (
            "encoded-controls-link.xlsx",
            {
                sheet_rels_name: sheet_rels.replace(
                    b"https://example.com/spec?q=1",
                    b"https://example.com/%250D%250AInjected",
                )
            },
            "(?i)control|hyperlink|invisible",
        ),
    )

    for filename, replacements, message in cases:
        malformed = _rewrite_package(source, tmp_path / filename, replacements)
        with pytest.raises(ValueError, match=message):
            transplant_quotation(malformed, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_linked_image_with_embedded_fallback_imports_without_external_file_reference(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    drawing_name = "xl/drawings/drawing7.xml"
    drawing_rels_name = "xl/drawings/_rels/drawing7.xml.rels"
    linked = _rewrite_package(
        source,
        tmp_path / "linked-image.xlsx",
        {
            drawing_name: _part_bytes(source, drawing_name).replace(
                b'<a:blip r:embed="rIdImage"/>',
                b'<a:blip r:embed="rIdImage" r:link="rIdLinkedImage"/>',
            ),
            drawing_rels_name: _append_relationship(
                _part_bytes(source, drawing_rels_name),
                (
                    "rIdLinkedImage",
                    f"{OFFICE_REL}/image",
                    "file:///C:/Temp/vendor-source.png",
                    "External",
                ),
            ),
        },
    )

    images = _source_product_images(linked)
    addition = transplant_quotation(linked, XlsxPackage.read(OFFICIAL_TEMPLATE))

    assert images.keys() == {9}
    assert addition is not None
    transplanted_drawing = next(
        content
        for name, content in addition.parts.items()
        if name.startswith("xl/drawings/") and name.endswith(".xml")
    )
    transplanted_rels = next(
        content
        for name, content in addition.parts.items()
        if name.startswith("xl/drawings/_rels/") and name.endswith(".rels")
    )
    blip = ET.fromstring(transplanted_drawing).find(
        ".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
    )
    relationships = ET.fromstring(transplanted_rels)

    assert blip is not None
    assert f"{{{OFFICE_REL}}}embed" in blip.attrib
    assert f"{{{OFFICE_REL}}}link" not in blip.attrib
    assert not any(
        relationship.attrib.get("TargetMode") == "External"
        and relationship.attrib.get("Type") == f"{OFFICE_REL}/image"
        for relationship in relationships
    )


def test_exact_transitional_relationship_uris_are_enforced_and_strict_types_rejected(tmp_path):
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
    strict_relationship_types = _rewrite_package(
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
    with pytest.raises(ValueError, match="(?i)Strict.*no soportado|no soporta.*Strict"):
        transplant_quotation(
            strict_relationship_types, XlsxPackage.read(OFFICIAL_TEMPLATE)
        )


def test_workbook_relationship_contract_documents_transitional_only():
    documentation = XlsxPackage.workbook_related_part.__doc__ or ""

    assert "transitional" in documentation.casefold()
    assert "strict" not in documentation.casefold()


def test_real_strict_spreadsheet_namespace_fails_closed_early(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    strict_main = "http://purl.oclc.org/ooxml/spreadsheetml/main"
    strict = _rewrite_package(
        source,
        tmp_path / "strict-package.xlsx",
        {
            "xl/workbook.xml": _part_bytes(source, "xl/workbook.xml").replace(
                MAIN.encode(), strict_main.encode()
            )
        },
    )

    with pytest.raises(ValueError, match="(?i)Strict.*no soportado|no soporta.*Strict"):
        transplant_quotation(strict, XlsxPackage.read(OFFICIAL_TEMPLATE))


@pytest.mark.parametrize(
    ("part_name", "bad_content_type"),
    (
        ("xl/workbook.xml", "application/xml"),
        ("xl/styles.xml", "application/xml"),
        ("xl/theme/theme7.xml", "application/xml"),
    ),
)
def test_workbook_relationship_profiles_validate_exact_content_types(
    part_name,
    bad_content_type,
    tmp_path,
):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    content_types = ET.fromstring(_part_bytes(source, "[Content_Types].xml"))
    override = next(
        item
        for item in content_types.findall("ct:Override", NS)
        if item.attrib["PartName"] == "/" + part_name
    )
    override.attrib["ContentType"] = bad_content_type
    malformed = _rewrite_package(
        source,
        tmp_path / (part_name.replace("/", "-") + ".xlsx"),
        {"[Content_Types].xml": _xml_bytes(content_types)},
    )

    with pytest.raises(ValueError, match="(?i)content type"):
        transplant_quotation(malformed, XlsxPackage.read(OFFICIAL_TEMPLATE))


@pytest.mark.parametrize(
    "replacement",
    (
        b'<Default Extension="rels" ContentType="application/xml"/>',
        b"",
    ),
)
def test_every_included_relationship_part_requires_exact_package_content_type(
    replacement, tmp_path
):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    content_types = _part_bytes(source, "[Content_Types].xml")
    declaration = (
        b'<Default Extension="rels" '
        b'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    )
    malformed = _rewrite_package(
        source,
        tmp_path / ("wrong-rels-type.xlsx" if replacement else "missing-rels-type.xlsx"),
        {"[Content_Types].xml": content_types.replace(declaration, replacement, 1)},
    )

    with pytest.raises(ValueError, match="(?i)content type.*rels|rels.*content type|ausente"):
        transplant_quotation(malformed, XlsxPackage.read(OFFICIAL_TEMPLATE))


def test_workbook_relationship_profiles_reject_theme_outside_allowed_path(tmp_path):
    source = build_rich_quotation_fixture(tmp_path / "source.xlsx")
    rels_name = "xl/_rels/workbook.xml.rels"
    rels = _part_bytes(source, rels_name).replace(
        b'theme/theme7.xml', b'custom/theme7.xml'
    )
    content_types = ET.fromstring(_part_bytes(source, "[Content_Types].xml"))
    theme_override = next(
        item
        for item in content_types.findall("ct:Override", NS)
        if item.attrib["PartName"] == "/xl/theme/theme7.xml"
    )
    theme_override.attrib["PartName"] = "/xl/custom/theme7.xml"
    malformed = _rewrite_package(
        source,
        tmp_path / "theme-path.xlsx",
        {
            rels_name: rels,
            "xl/custom/theme7.xml": _part_bytes(source, "xl/theme/theme7.xml"),
            "[Content_Types].xml": _xml_bytes(content_types),
        },
    )

    with pytest.raises(ValueError, match="(?i)ruta.*theme|theme.*ruta|tema.*ruta"):
        transplant_quotation(malformed, XlsxPackage.read(OFFICIAL_TEMPLATE))


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
    merger.merge_referenced_styles(
        _part_bytes(source, "xl/styles.xml"),
        {1},
        source_theme=_part_bytes(source, "xl/theme/theme7.xml"),
    )
    _assert_ignorable_prefixes(merger.to_xml(), ("xr",))


def test_mc_ignorable_rejects_undeclared_prefix(tmp_path):
    sheet = (
        f'<worksheet xmlns="{MAIN}" xmlns:mc="{MC}" mc:Ignorable="x14ac">'
        '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()

    with pytest.raises(ValueError, match="(?i)Ignorable|prefijo"):
        inline_source_shared_strings(sheet, ("valor",))


def test_mc_descendant_namespaces_and_qname_lists_are_promoted_and_preserved():
    x14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
    sheet = f'''<worksheet xmlns="{MAIN}" xmlns:mc="{MC}">
      <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData>
      <mc:AlternateContent>
        <mc:Choice xmlns:x14="{x14}" Requires="x14" mc:ProcessContent="x14:future" mc:PreserveAttributes="x14:flag" mc:PreserveElements="x14:future">
          <x14:future x14:flag="1"/>
        </mc:Choice>
        <mc:Fallback/>
      </mc:AlternateContent>
    </worksheet>'''.encode()

    output = inline_source_shared_strings(sheet, ("valor",))
    root = ET.fromstring(output)
    choice = root.find(f".//{{{MC}}}Choice")
    root_start = output[output.find(b"<", output.find(b"?>") + 2) : output.find(b">", output.find(b"?>") + 2)]

    assert choice.attrib["Requires"] == "x14"
    assert choice.attrib[f"{{{MC}}}ProcessContent"] == "x14:future"
    assert b'xmlns:x14="' + x14.encode() + b'"' in root_start
    assert b"<x14:future" in output


def test_mc_namespace_rebinding_to_a_different_uri_fails_closed():
    sheet = f'''<worksheet xmlns="{MAIN}" xmlns:mc="{MC}" xmlns:x14="urn:first">
      <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData>
      <mc:AlternateContent><mc:Choice xmlns:x14="urn:second" Requires="x14"><x14:future/></mc:Choice></mc:AlternateContent>
    </worksheet>'''.encode()

    with pytest.raises(ValueError, match="(?i)prefijo.*ambiguo|rebind"):
        inline_source_shared_strings(sheet, ("valor",))


def test_xml_serialization_registers_namespaces_while_holding_module_lock(monkeypatch):
    lock = quotation_sheets_module._XML_SERIALIZATION_LOCK
    original = quotation_sheets_module.ET.register_namespace
    observed = []

    def checked_register(prefix, uri):
        observed.append(lock._is_owned())
        return original(prefix, uri)

    monkeypatch.setattr(
        quotation_sheets_module.ET,
        "register_namespace",
        checked_register,
    )
    root = ET.fromstring('<root xmlns:p="urn:lock"><p:item/></root>')

    output = quotation_sheets_module._xml_bytes(root, {"p": "urn:lock"})

    assert observed and all(observed)
    assert b"<p:item" in output


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


@pytest.mark.parametrize(
    "shared_string",
    (
        f'<si xmlns="{MAIN}" unexpected="1"><t>A</t></si>',
        f'<si xmlns="{MAIN}">intruso<t>A</t></si>',
        f'<si xmlns="{MAIN}"><r unexpected="1"><t>A</t></r></si>',
        f'<si xmlns="{MAIN}"><r>intruso<t>A</t></r></si>',
        f'<si xmlns="{MAIN}"><r><rPr><b/></rPr>intruso<t>A</t></r></si>',
        f'<si xmlns="{MAIN}"><r><t>A</t>intruso</r></si>',
        f'<si xmlns="{MAIN}"><t>A</t><rPh sb="0" eb="1">intruso<t>a</t></rPh></si>',
        f'<si xmlns="{MAIN}"><t>A</t><rPh sb="0" eb="1"><t>a</t>intruso</rPh></si>',
        f'<si xmlns="{MAIN}"><t>A</t><rPh sb="0" eb="1" unexpected="1"><t>a</t></rPh></si>',
        f'<si xmlns="{MAIN}"><t>A</t><rPh sb="0" eb="1"><t>a</t><t>b</t></rPh></si>',
        f'<si xmlns="{MAIN}"><t>A</t><phoneticPr fontId="0">intruso</phoneticPr></si>',
        f'<si xmlns="{MAIN}"><t>A</t><phoneticPr fontId="0" unexpected="1"/></si>',
    ),
)
def test_shared_string_wrappers_reject_attributes_and_non_whitespace_mixed_content(
    shared_string,
):
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()

    with pytest.raises(ValueError, match="(?i)shared|string|rich|fon.t|atributo"):
        inline_source_shared_strings(sheet, (shared_string.encode(),))


def test_shared_string_wrappers_allow_only_formatting_whitespace_between_nodes():
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()
    shared_string = f'''<si xmlns="{MAIN}">
      <r>
        <rPr><b/></rPr>
        <t>A</t>
      </r>
      <rPh sb="0" eb="1">
        <t>a</t>
      </rPh>
      <phoneticPr fontId="0" type="Hiragana" alignment="distributed"/>
    </si>'''.encode()

    output = inline_source_shared_strings(sheet, (shared_string,))

    assert ET.fromstring(output).find(".//m:phoneticPr", NS) is not None


@pytest.mark.parametrize(
    "inline_string",
    (
        '<is unexpected="1"><r><t>A</t></r></is>',
        '<is>intruso<r><t>A</t></r></is>',
        '<is><r unexpected="1"><t>A</t></r></is>',
        '<is><r><t>A</t></r>intruso</is>',
    ),
)
def test_existing_inline_strings_validate_root_and_rich_mixed_content(inline_string):
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
        f'<c r="A1" t="inlineStr">{inline_string}</c>'
        "</row></sheetData></worksheet>"
    ).encode()

    with pytest.raises(ValueError, match="(?i)inline|CT_Rst|rich|string|atributo"):
        inline_source_shared_strings(sheet, ())


def test_existing_inline_string_accepts_formatting_whitespace_and_rich_text():
    sheet = f'''<worksheet xmlns="{MAIN}"><sheetData><row r="1">
      <c r="A1" t="inlineStr"><is>
        <r>
          <rPr><b/></rPr>
          <t>A</t>
        </r>
      </is></c>
    </row></sheetData></worksheet>'''.encode()

    output = inline_source_shared_strings(sheet, ())

    root = ET.fromstring(output)
    assert root.find(".//m:c[@t='inlineStr']/m:is/m:r/m:rPr/m:b", NS) is not None
    assert root.findtext(".//m:c[@t='inlineStr']/m:is/m:r/m:t", namespaces=NS) == "A"


@pytest.mark.parametrize(
    "run_properties",
    (
        "<rPr><i/><b/></rPr>",
        '<rPr unexpected="1"><b/></rPr>',
        '<rPr><b val="yes"/></rPr>',
        '<rPr><u val="wavy"/></rPr>',
        '<rPr><vertAlign val="middle"/></rPr>',
        '<rPr><scheme val="fixture"/></rPr>',
        "<rPr><rFont/></rPr>",
    ),
)
def test_shared_string_ct_rpr_rejects_invalid_order_attributes_and_enums(
    run_properties,
):
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()
    shared_string = (
        f'<si xmlns="{MAIN}"><r>{run_properties}<t>A</t></r></si>'
    ).encode()

    with pytest.raises(ValueError, match="(?i)rich|rPr|propiedad|orden|enum"):
        inline_source_shared_strings(sheet, (shared_string,))


def test_shared_string_ct_rpr_accepts_schema_order_and_valid_simple_values():
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()
    shared_string = f'''<si xmlns="{MAIN}"><r><rPr>
      <rFont val="Arial"/><charset val="1"/><family val="2"/>
      <b val="0"/><i/><strike val="false"/><outline/><shadow/><condense/><extend/>
      <color rgb="FF123456"/><sz val="11.5"/><u val="double"/>
      <vertAlign val="superscript"/><scheme val="minor"/>
    </rPr><t>A</t></r></si>'''.encode()

    output = inline_source_shared_strings(sheet, (shared_string,))

    assert ET.fromstring(output).find(".//m:rPr/m:scheme", NS).attrib == {
        "val": "minor"
    }


@pytest.mark.parametrize(
    "attributes",
    (
        'fontId="0" type="katakana"',
        'fontId="0" alignment="right"',
    ),
)
def test_shared_string_phonetic_properties_reject_invalid_simple_enums(attributes):
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()
    shared_string = (
        f'<si xmlns="{MAIN}"><t>A</t><phoneticPr {attributes}/></si>'
    ).encode()

    with pytest.raises(ValueError, match="(?i)phonetic|fon.t|tipo|alineaci"):
        inline_source_shared_strings(sheet, (shared_string,))


def test_shared_string_phonetic_properties_accept_valid_type_and_alignment():
    sheet = (
        f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    ).encode()
    shared_string = (
        f'<si xmlns="{MAIN}"><t>A</t><phoneticPr fontId="0" '
        'type="Hiragana" alignment="distributed"/></si>'
    ).encode()

    output = inline_source_shared_strings(sheet, (shared_string,))

    assert ET.fromstring(output).find(".//m:phoneticPr", NS).attrib == {
        "fontId": "0",
        "type": "Hiragana",
        "alignment": "distributed",
    }


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
            source_theme=source_package.parts["xl/theme/theme7.xml"],
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
    theme = _workbook_theme(parts)

    cells = []
    for cell in root.findall(".//m:c", NS):
        style_id = int(cell.attrib.get("s", "0"))
        formula = cell.findtext("m:f", default=None, namespaces=NS)
        value = _cell_semantic_value(cell, shared, styles, theme)
        cells.append((cell.attrib["r"], formula, value, _style_signature(styles, style_id, theme)))
    rows = tuple(
        (
            row.attrib.get("r"),
            row.attrib.get("hidden", "0"),
            row.attrib.get("ht"),
            _style_signature(styles, int(row.attrib["s"]), theme) if "s" in row.attrib else None,
        )
        for row in root.findall("m:sheetData/m:row", NS)
    )
    columns = tuple(
        (
            tuple(sorted((key, value) for key, value in column.attrib.items() if key != "style")),
            _style_signature(styles, int(column.attrib["style"]), theme) if "style" in column.attrib else None,
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


def _relationship_graph_signature(
    parts: dict[str, bytes],
    start: str,
    explicit_content_types: Mapping[str, str] | None = None,
) -> tuple:
    content_type_map = _content_type_map(parts)
    if explicit_content_types is not None:
        content_type_map.update(explicit_content_types)

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
            content_type = content_type_map.get(target)
            relationship_type = attrs["Type"]
            if relationship_type == f"{OFFICE_REL}/image":
                content = _validated_image_payload(content_type, payload)
            elif relationship_type == f"{OFFICE_REL}/printerSettings":
                expected = (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.printerSettings"
                )
                if content_type != expected:
                    raise AssertionError("content type de printerSettings inválido")
                content = payload
            else:
                parsed = ET.fromstring(payload)
                if parsed.tag == f"{{{MAIN}}}table" and "xl/styles.xml" in parts:
                    content = _table_part_signature(payload, parts["xl/styles.xml"])
                else:
                    content = _canonical(parsed)
            rows.append(
                (
                    attrs["Id"],
                    attrs["Type"],
                    "Internal",
                    content_type,
                    content,
                    visit(target, (*stack, owner)),
                )
            )
        return tuple(sorted(rows, key=lambda row: row[:2]))

    return visit(start, ())


def _validated_image_payload(content_type: str | None, payload: bytes) -> bytes:
    signatures = {
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/bmp": (b"BM",),
        "image/tiff": (b"II*\x00", b"MM\x00*"),
    }
    allowed = signatures.get(content_type or "")
    if allowed is None or not payload.startswith(allowed):
        raise AssertionError("perfil binario de imagen inválido")
    return payload


def _style_signature(
    styles_xml: bytes,
    style_id: int,
    theme_xml: bytes | None = None,
) -> tuple:
    root = ET.fromstring(styles_xml)
    cell_xfs = root.findall("m:cellXfs/m:xf", NS)
    if style_id >= len(cell_xfs):
        raise AssertionError("style fuera de rango")
    return _xf_signature(root, cell_xfs[style_id], theme_xml)


def _xf_signature(
    root: ET.Element,
    xf: ET.Element,
    theme_xml: bytes | None = None,
) -> tuple:
    attributes = dict(xf.attrib)
    result = []
    for attribute, section, child in (
        ("fontId", "fonts", "font"),
        ("fillId", "fills", "fill"),
        ("borderId", "borders", "border"),
    ):
        index = int(attributes.pop(attribute, "0"))
        values = root.findall(f"m:{section}/m:{child}", NS)
        result.append(
            (attribute, _resolved_style_component(values[index], theme_xml))
        )
    num_fmt_id = int(attributes.pop("numFmtId", "0"))
    result.append(("numFmt", _num_fmt_code(root, num_fmt_id)))
    xf_id = attributes.pop("xfId", None)
    if xf_id is not None:
        bases = root.findall("m:cellStyleXfs/m:xf", NS)
        result.append(("xf", _xf_signature(root, bases[int(xf_id)], theme_xml)))
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


def _font_for_style(styles_xml: bytes, style_id: int) -> ET.Element:
    root = ET.fromstring(styles_xml)
    xf = root.findall("m:cellXfs/m:xf", NS)[style_id]
    return root.findall("m:fonts/m:font", NS)[int(xf.attrib.get("fontId", "0"))]


def _direct_dxf_attributes(table: ET.Element) -> list[tuple[str, int]]:
    result = []
    for element in table.iter():
        for name, value in element.attrib.items():
            if name.rsplit("}", 1)[-1].casefold().endswith("dxfid"):
                result.append((name, int(value)))
    return result


def _styles_without_theme_refs(styles_xml: bytes) -> bytes:
    return styles_xml.replace(
        b'<color theme="1"/>', b'<color rgb="FF010203"/>', 1
    ).replace(b'<scheme val="minor"/>', b"", 1)


def _theme_font_semantics(
    theme_xml: bytes,
    theme_index: int,
    tint: str,
    scheme: str,
) -> tuple[str, str]:
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ns = {"a": drawing}
    root = ET.fromstring(theme_xml)
    order = (
        "lt1", "dk1", "lt2", "dk2", "accent1", "accent2",
        "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink",
    )
    slot = root.find(f"a:themeElements/a:clrScheme/a:{order[theme_index]}", ns)
    value = list(slot)[0].attrib.get("lastClr") or list(slot)[0].attrib["val"]
    red, green, blue = (int(value[offset : offset + 2], 16) / 255 for offset in (0, 2, 4))
    hue, luminance, saturation = colorsys.rgb_to_hls(red, green, blue)
    tint_value = float(Decimal(tint))
    if tint_value < 0:
        luminance *= 1 + tint_value
    else:
        luminance = luminance * (1 - tint_value) + tint_value
    channels = tuple(
        int(channel * 255 + 0.5)
        for channel in colorsys.hls_to_rgb(hue, luminance, saturation)
    )
    font = root.find(f"a:themeElements/a:fontScheme/a:{scheme}Font/a:latin", ns)
    return "FF" + "".join(f"{value:02X}" for value in channels), font.attrib["typeface"]


def _resolved_style_component(
    element: ET.Element,
    theme_xml: bytes | None,
) -> tuple:
    clone = ET.fromstring(ET.tostring(element))
    for color in clone.iter():
        if "theme" not in color.attrib:
            continue
        assert theme_xml is not None
        rgb, _typeface = _theme_font_semantics(
            theme_xml,
            int(color.attrib["theme"]),
            color.attrib.get("tint", "0"),
            "minor",
        )
        color.attrib.pop("theme", None)
        color.attrib.pop("tint", None)
        color.attrib["rgb"] = rgb
    if clone.tag == f"{{{MAIN}}}font":
        scheme = clone.find("m:scheme", NS)
        if scheme is not None:
            assert theme_xml is not None
            _rgb, typeface = _theme_font_semantics(
                theme_xml,
                0,
                "0",
                scheme.attrib["val"],
            )
            name = clone.find("m:name", NS)
            if name is None:
                name = ET.Element(f"{{{MAIN}}}name")
                clone.insert(0, name)
            name.attrib["val"] = typeface
            clone.remove(scheme)
    return _canonical(clone)


def _workbook_theme(parts: dict[str, bytes]) -> bytes | None:
    rels = parts.get("xl/_rels/workbook.xml.rels")
    if rels is None:
        return None
    matches = [
        item
        for item in ET.fromstring(rels)
        if item.attrib.get("Type", "").rsplit("/", 1)[-1] == "theme"
        and item.attrib.get("TargetMode", "").casefold() != "external"
    ]
    if not matches:
        return None
    assert len(matches) == 1
    target = posixpath.normpath(
        posixpath.join("xl", matches[0].attrib["Target"])
    )
    return parts[target]


def _content_type_map(parts: dict[str, bytes]) -> dict[str, str]:
    content = parts.get("[Content_Types].xml")
    if content is None:
        return {}
    root = ET.fromstring(content)
    defaults = {
        item.attrib["Extension"].casefold(): item.attrib["ContentType"]
        for item in root.findall("ct:Default", NS)
    }
    overrides = {
        item.attrib["PartName"].removeprefix("/"): item.attrib["ContentType"]
        for item in root.findall("ct:Override", NS)
    }
    return {
        name: overrides.get(
            name,
            defaults.get(posixpath.splitext(name)[1].removeprefix(".").casefold()),
        )
        for name in parts
        if name != "[Content_Types].xml"
    }


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


def _cell_semantic_value(
    cell: ET.Element,
    shared: tuple[ET.Element, ...],
    styles: bytes,
    theme: bytes | None,
) -> tuple:
    if cell.attrib.get("t") == "s":
        index = int(cell.findtext("m:v", namespaces=NS))
        return "text", tuple(
            _rich_text_node_signature(child, styles, theme) for child in shared[index]
        )
    if cell.attrib.get("t") == "inlineStr":
        inline = cell.find("m:is", NS)
        assert inline is not None
        return "text", tuple(
            _rich_text_node_signature(child, styles, theme) for child in inline
        )
    return cell.attrib.get("t", "n"), cell.findtext("m:v", default="", namespaces=NS)


def _rich_text_node_signature(
    element: ET.Element,
    styles: bytes,
    theme: bytes | None,
) -> tuple:
    attributes: list[tuple[str, object]] = []
    for name, value in element.attrib.items():
        if element.tag == f"{{{MAIN}}}phoneticPr" and name == "fontId":
            fonts = ET.fromstring(styles).findall("m:fonts/m:font", NS)
            attributes.append(
                ("font", _resolved_style_component(fonts[int(value)], theme))
            )
        else:
            attributes.append((name, value))
    return (
        element.tag,
        tuple(sorted(attributes, key=lambda item: item[0])),
        element.text or "",
        tuple(_rich_text_node_signature(child, styles, theme) for child in element),
    )


def _table_part_signature(table_xml: bytes, styles_xml: bytes) -> tuple:
    def visit(element: ET.Element) -> tuple:
        attributes: list[tuple[str, object]] = []
        for name, value in element.attrib.items():
            if element.tag == f"{{{MAIN}}}table" and name == "id":
                continue
            if name.rsplit("}", 1)[-1].casefold().endswith("dxfid"):
                attributes.append((name, _dxf_signature(styles_xml, int(value))))
            elif element.tag == f"{{{MAIN}}}tableStyleInfo" and name == "name":
                attributes.append((name, _table_style_signature(styles_xml, value)))
            else:
                attributes.append((name, value))
        return (
            element.tag,
            tuple(sorted(attributes, key=lambda item: item[0])),
            element.text or "",
            tuple(visit(child) for child in element),
        )

    return visit(ET.fromstring(table_xml))


def _shared_strings(parts: dict[str, bytes]) -> tuple[ET.Element, ...]:
    if "xl/sharedStrings.xml" not in parts:
        return ()
    return tuple(ET.fromstring(parts["xl/sharedStrings.xml"]).findall("m:si", NS))


def _closure_payloads(
    parts: dict[str, bytes],
    sheet_part: str,
    content_types: Mapping[str, str],
) -> tuple:
    signature = _relationship_graph_signature(parts, sheet_part, content_types)
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


def _source_with_table_identity(
    tmp_path: Path,
    *,
    table_id: int,
    name: str,
    display_name: str,
    formula: str,
    filename: str = "table-collision.xlsx",
) -> Path:
    source = build_rich_quotation_fixture(tmp_path / ("base-" + filename))
    table_name = "xl/tables/table7.xml"
    table = ET.fromstring(_part_bytes(source, table_name))
    table.attrib.update(
        {"id": str(table_id), "name": name, "displayName": display_name}
    )
    first_column = table.find("m:tableColumns/m:tableColumn", NS)
    calculated = ET.Element(f"{{{MAIN}}}calculatedColumnFormula")
    calculated.text = formula
    first_column.insert(0, calculated)

    worksheet_name = "xl/worksheets/original-quotation.xml"
    worksheet = ET.fromstring(_part_bytes(source, worksheet_name))
    row = worksheet.find("m:sheetData/m:row[@r='12']", NS)
    cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": "N12"})
    formula_element = ET.SubElement(cell, f"{{{MAIN}}}f")
    formula_element.text = formula

    workbook = ET.fromstring(_part_bytes(source, "xl/workbook.xml"))
    quote_local = next(
        item
        for item in workbook.findall("m:definedNames/m:definedName", NS)
        if item.attrib.get("name") == "QuoteLocal"
    )
    quote_local.text = formula
    return _rewrite_package(
        source,
        tmp_path / filename,
        {
            table_name: _xml_bytes(table),
            worksheet_name: _xml_bytes(worksheet),
            "xl/workbook.xml": _xml_bytes(workbook),
        },
    )


def _source_with_two_table_identities(
    tmp_path: Path,
    *,
    formula: str,
    filename: str,
) -> Path:
    source = build_rich_quotation_fixture(tmp_path / ("base-" + filename))
    first_table_name = "xl/tables/table7.xml"
    second_table_name = "xl/tables/table8.xml"
    original_table = _part_bytes(source, first_table_name)
    first_table = ET.fromstring(original_table)
    first_table.attrib.update(
        {"id": "1", "name": "Table1", "displayName": "Table1"}
    )
    first_column = first_table.find("m:tableColumns/m:tableColumn", NS)
    calculated = ET.Element(f"{{{MAIN}}}calculatedColumnFormula")
    calculated.text = formula
    first_column.insert(0, calculated)
    second_table = ET.fromstring(original_table)
    second_table.attrib.update(
        {"id": "2", "name": "Table2", "displayName": "Table2"}
    )

    worksheet_name = "xl/worksheets/original-quotation.xml"
    worksheet = ET.fromstring(_part_bytes(source, worksheet_name))
    row = worksheet.find("m:sheetData/m:row[@r='12']", NS)
    cell = ET.SubElement(row, f"{{{MAIN}}}c", {"r": "N12"})
    ET.SubElement(cell, f"{{{MAIN}}}f").text = formula
    table_parts = worksheet.find("m:tableParts", NS)
    table_parts.attrib["count"] = "2"
    ET.SubElement(
        table_parts,
        f"{{{MAIN}}}tablePart",
        {f"{{{OFFICE_REL}}}id": "rIdTable2"},
    )

    sheet_rels_name = "xl/worksheets/_rels/original-quotation.xml.rels"
    sheet_rels = _append_relationship(
        _part_bytes(source, sheet_rels_name),
        (
            "rIdTable2",
            f"{OFFICE_REL}/table",
            "../tables/table8.xml",
            None,
        ),
    )
    workbook = ET.fromstring(_part_bytes(source, "xl/workbook.xml"))
    quote_local = next(
        item
        for item in workbook.findall("m:definedNames/m:definedName", NS)
        if item.attrib.get("name") == "QuoteLocal"
    )
    quote_local.text = formula
    content_types = ET.fromstring(_part_bytes(source, "[Content_Types].xml"))
    ET.SubElement(
        content_types,
        f"{{{CONTENT_TYPES}}}Override",
        {
            "PartName": "/" + second_table_name,
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml",
        },
    )
    return _rewrite_package(
        source,
        tmp_path / filename,
        {
            first_table_name: _xml_bytes(first_table),
            second_table_name: _xml_bytes(second_table),
            worksheet_name: _xml_bytes(worksheet),
            sheet_rels_name: sheet_rels,
            "xl/workbook.xml": _xml_bytes(workbook),
            "[Content_Types].xml": _xml_bytes(content_types),
        },
    )


def _source_without_table_style_info(
    tmp_path: Path,
    *,
    default_style: str,
    filename: str,
    with_ext_lst: bool = False,
) -> Path:
    source = build_rich_quotation_fixture(tmp_path / ("base-" + filename))
    table_name = "xl/tables/table7.xml"
    table = ET.fromstring(_part_bytes(source, table_name))
    style_info = table.find("m:tableStyleInfo", NS)
    assert style_info is not None
    table.remove(style_info)
    if with_ext_lst:
        ET.SubElement(table, f"{{{MAIN}}}extLst")
    styles = ET.fromstring(_part_bytes(source, "xl/styles.xml"))
    table_styles = styles.find("m:tableStyles", NS)
    assert table_styles is not None
    table_styles.attrib["defaultTableStyle"] = default_style
    return _rewrite_package(
        source,
        tmp_path / filename,
        {
            table_name: _xml_bytes(table),
            "xl/styles.xml": _xml_bytes(styles),
        },
    )


def _destination_with_source_theme(source: Path) -> XlsxPackage:
    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)
    destination_theme = destination.workbook_related_part("theme")
    assert destination_theme is not None
    return replace(
        destination,
        parts={
            **destination.parts,
            destination_theme: _part_bytes(source, "xl/theme/theme7.xml"),
        },
    )


def _source_with_image_profile(
    tmp_path: Path,
    *,
    extension: str,
    content_type: str,
    signature: bytes,
    filename: str,
) -> Path:
    source = build_rich_quotation_fixture(tmp_path / ("base-" + filename))
    rels_name = "xl/drawings/_rels/drawing7.xml.rels"
    media_name = f"xl/media/image7.{extension}"
    return _rewrite_package(
        source,
        tmp_path / filename,
        {
            rels_name: _part_bytes(source, rels_name).replace(
                b"../media/image7.png", f"../media/image7.{extension}".encode()
            ),
            media_name: signature,
        },
        content_type=("/" + media_name, content_type),
    )


def _destination_table_registry(package: XlsxPackage) -> tuple[set[int], set[str]]:
    identifiers: set[int] = set()
    names: set[str] = set()
    for part_name, content in package.parts.items():
        if not part_name.startswith("xl/tables/") or not part_name.endswith(".xml"):
            continue
        table = ET.fromstring(content)
        identifiers.add(int(table.attrib["id"]))
        names.update(
            {
                table.attrib["name"].casefold(),
                table.attrib["displayName"].casefold(),
            }
        )
    return identifiers, names


def _destination_with_table_registry(
    *identities: tuple[int, str],
) -> XlsxPackage:
    """Crea colisiones de tabla explícitas sin depender de la plantilla oficial."""

    destination = XlsxPackage.read(OFFICIAL_TEMPLATE)
    parts = dict(destination.parts)
    content_types = ET.fromstring(parts["[Content_Types].xml"])
    for offset, (table_id, table_name) in enumerate(identities, start=1):
        part_name = f"xl/tables/destination-table-{offset}.xml"
        table = ET.Element(
            f"{{{MAIN}}}table",
            {
                "id": str(table_id),
                "name": table_name,
                "displayName": table_name,
                "ref": "A1:A2",
                "totalsRowShown": "0",
            },
        )
        ET.SubElement(table, f"{{{MAIN}}}autoFilter", {"ref": "A1:A2"})
        columns = ET.SubElement(
            table,
            f"{{{MAIN}}}tableColumns",
            {"count": "1"},
        )
        ET.SubElement(
            columns,
            f"{{{MAIN}}}tableColumn",
            {"id": "1", "name": "Column1"},
        )
        ET.SubElement(
            table,
            f"{{{MAIN}}}tableStyleInfo",
            {
                "name": "TableStyleMedium2",
                "showFirstColumn": "0",
                "showLastColumn": "0",
                "showRowStripes": "1",
                "showColumnStripes": "0",
            },
        )
        parts[part_name] = _xml_bytes(table)
        ET.SubElement(
            content_types,
            f"{{{CONTENT_TYPES}}}Override",
            {
                "PartName": "/" + part_name,
                "ContentType": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.table+xml"
                ),
            },
        )
    parts["[Content_Types].xml"] = _xml_bytes(content_types)
    return replace(destination, parts=parts)


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
