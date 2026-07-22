from pathlib import Path
import posixpath
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest
from openpyxl import load_workbook

from mobiliti_saas.quote_engine.mobiliti_layout import SectionNeed, plan_mobiliti_layout
from mobiliti_saas.quote_engine.ooxml_formula import translate_formula
from mobiliti_saas.quote_engine.ooxml_package import (
    PackageMutation,
    XlsxPackage,
    assert_package_preserved,
)
from mobiliti_saas.quote_engine.ooxml_worksheet import (
    MobilitiCellWrite,
    build_mobiliti_sheet,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    ROOT
    / "mobiliti_saas"
    / "worker"
    / "templates"
    / "Formato Cotizacion 2026 GDL.xlsx"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XM = "http://schemas.microsoft.com/office/excel/2006/main"

pytestmark = [
    pytest.mark.filterwarnings("ignore:Data Validation extension is not supported"),
    pytest.mark.filterwarnings("ignore:wmf image format is not supported"),
]


def _official_part(path: Path, sheet_name: str) -> str:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(f"{{{PACKAGE_REL}}}Relationship")
    }
    sheet = next(
        node
        for node in workbook.findall(f".//{{{MAIN}}}sheet")
        if node.attrib["name"] == sheet_name
    )
    target = targets[sheet.attrib[f"{{{OFFICE_REL}}}id"]]
    return posixpath.normpath(posixpath.join("xl", target.lstrip("/")))


def _part_bytes(path: Path, part: str) -> bytes:
    with ZipFile(path) as archive:
        return archive.read(part)


def _render(tmp_path: Path, count: int):
    part = _official_part(TEMPLATE, "Mobiliti")
    writes = [
        MobilitiCellWrite(f"D{14 + index}", "text", f"SKU-{index:03d}")
        for index in range(count)
    ]
    mutation = build_mobiliti_sheet(
        _part_bytes(TEMPLATE, part),
        [SectionNeed("chairs", "SILLAS", count)],
        writes,
    )
    output = tmp_path / f"mobiliti-{count}.xlsx"
    XlsxPackage.read(TEMPLATE).write_new(
        output,
        PackageMutation(replacements={part: mutation.xml}),
    )
    assert assert_package_preserved(TEMPLATE, output, {part}).changed_parts == {part}
    workbook = load_workbook(output, data_only=False, keep_links=True)
    return mutation.row_map, workbook, workbook["Mobiliti"]


def _render_sections(tmp_path: Path, section_count: int):
    needs = [
        SectionNeed(f"section-{index}", f"SECCION {index + 1}", 1)
        for index in range(section_count)
    ]
    planned = plan_mobiliti_layout(needs)
    writes = [
        MobilitiCellWrite(f"D{section.product_start}", "text", f"SKU-{index:03d}")
        for index, section in enumerate(planned.sections[:section_count])
    ]
    part = _official_part(TEMPLATE, "Mobiliti")
    mutation = build_mobiliti_sheet(_part_bytes(TEMPLATE, part), needs, writes)
    output = tmp_path / f"mobiliti-sections-{section_count}.xlsx"
    XlsxPackage.read(TEMPLATE).write_new(
        output,
        PackageMutation(replacements={part: mutation.xml}),
    )
    workbook = load_workbook(output, data_only=False, keep_links=True)
    return mutation, workbook, workbook["Mobiliti"]


def _official_formula(coordinate: str) -> str:
    part = _official_part(TEMPLATE, "Mobiliti")
    root = ET.fromstring(_part_bytes(TEMPLATE, part))
    formula = root.find(f".//{{{MAIN}}}c[@r='{coordinate}']/{{{MAIN}}}f")
    assert formula is not None and formula.text
    return "=" + formula.text


def _formula_rows(formula: str, column: str) -> list[int]:
    return [int(row) for row in __import__("re").findall(fr"\b{column}(\d+)\b", formula)]


@pytest.mark.parametrize("count", [34, 100])
def test_one_section_keeps_every_product_and_official_formulas(tmp_path, count):
    row_map, workbook, worksheet = _render(tmp_path, count)
    try:
        rows = list(row_map.item_rows)
        assert len(rows) == count
        assert rows == list(range(14, 14 + count))
        assert worksheet.cell(14 + count, 1).value == "Subtotales Sección 1"
        assert all(worksheet.cell(row, 4).value == f"SKU-{row - 14:03d}" for row in rows)
        assert "#REF!" not in "\n".join(
            value
            for row in worksheet.iter_rows()
            for cell in row
            if isinstance((value := cell.value), str) and value.startswith("=")
        )
    finally:
        workbook.close()


@pytest.mark.parametrize("section_count", [17, 20])
def test_more_than_sixteen_sections_clone_official_blocks(tmp_path, section_count):
    mutation, workbook, worksheet = _render_sections(tmp_path, section_count)
    try:
        row_map = mutation.row_map
        assert len(row_map.sections) == section_count
        assert all(
            worksheet.cell(section.section_row, 4 if index == 0 else 1).value
            == f"SECCION {index + 1}"
            for index, section in enumerate(row_map.sections)
        )
        last = row_map.sections[-1]
        assert worksheet.cell(last.product_start, 4).value == f"SKU-{section_count - 1:03d}"
        assert worksheet.cell(last.product_start + 1, 4).value is None
        assert worksheet.cell(last.product_start + 1, 23).value == translate_formula(
            _official_formula("W49"),
            origin="W49",
            target=f"W{last.product_start + 1}",
        )
        assert worksheet.cell(last.subtotal_row, 8).value == (
            f"=SUM(IFERROR(H{last.product_start}:H{last.product_start},0))"
        )
        assert set(_formula_rows(worksheet.cell(row_map.total_row, 8).value, "H")) == set(
            row_map.subtotal_rows
        )
    finally:
        workbook.close()


def test_validations_and_conditional_formatting_reach_twentieth_section(tmp_path):
    mutation, workbook, _worksheet = _render_sections(tmp_path, 20)
    workbook.close()
    root = ET.fromstring(mutation.xml)
    last = mutation.row_map.sections[-1]
    last_range = f"{last.product_start}:{last.product_start + last.capacity - 1}"

    validation_sqrefs = " ".join(
        validation.attrib.get("sqref", "")
        for validation in root.findall(f".//{{{MAIN}}}dataValidation")
    )
    extension_sqrefs = " ".join(
        node.text or "" for node in root.findall(f".//{{{XM}}}sqref")
    )
    conditional_sqrefs = " ".join(
        node.attrib.get("sqref", "")
        for node in root.findall(f"{{{MAIN}}}conditionalFormatting")
    )

    assert f"E{last_range.replace(':', ':E')}" in validation_sqrefs
    assert f"F{last_range.replace(':', ':F')}" in extension_sqrefs
    assert f"AH{last_range.replace(':', ':AH')}" in conditional_sqrefs


def test_static_sidecar_formulas_follow_relocated_total_and_auxiliary_rows(tmp_path):
    mutation, workbook, worksheet = _render_sections(tmp_path, 20)
    official = load_workbook(TEMPLATE, data_only=False, keep_links=True)
    try:
        assert worksheet["P9"].value == f"=P8/H{mutation.row_map.total_row}"
        assert worksheet["AS15"].value == f"=AC{mutation.row_map.total_row}"
        assert worksheet.cell(mutation.row_map.total_row + 37, 36).style_id == official["Mobiliti"]["AJ610"].style_id
        assert worksheet.row_dimensions[mutation.row_map.total_row + 37].height == (
            official["Mobiliti"].row_dimensions[610].height
        )
    finally:
        official.close()
        workbook.close()


def test_unknown_formula_metadata_and_extension_nodes_survive_cloning():
    part = _official_part(TEMPLATE, "Mobiliti")
    root = ET.fromstring(_part_bytes(TEMPLATE, part))
    custom_namespace = "urn:mobiliti:test:unknown"
    formula = root.find(f".//{{{MAIN}}}c[@r='W14']/{{{MAIN}}}f")
    assert formula is not None
    formula.set(f"{{{custom_namespace}}}audit", "keep")
    marker = ET.SubElement(root, f"{{{custom_namespace}}}marker", {"keep": "yes"})
    marker.text = "opaque"

    mutation = build_mobiliti_sheet(
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        [SectionNeed("chairs", "SILLAS", 1)],
        [],
    )
    output = ET.fromstring(mutation.xml)
    cloned_formula = output.find(f".//{{{MAIN}}}c[@r='W15']/{{{MAIN}}}f")

    assert cloned_formula is not None
    assert cloned_formula.attrib[f"{{{custom_namespace}}}audit"] == "keep"
    assert not ({"t", "ref", "si"} & set(cloned_formula.attrib))
    assert output.find(f"{{{custom_namespace}}}marker").text == "opaque"
    assert output.find(f"{{{MAIN}}}extLst") is not None
