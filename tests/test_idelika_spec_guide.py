from __future__ import annotations

import importlib.util
import json
import re
import struct
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs" / "019f7907-1ecc-7001-b3f3-8eb209086fa8"
WORKBOOK = OUTPUT_DIR / "Spec guide-IDELIKA-2026.xlsx"
SUMMARY = OUTPUT_DIR / "Spec guide-IDELIKA-2026.validation.json"
PYTHON_BUILDER = (
    ROOT / "mobiliti_saas" / "worker" / "catalog_sync" / "tools" / "build_idelika_spec_guide.py"
)
JS_BUILDER = (
    ROOT / "mobiliti_saas" / "worker" / "catalog_sync" / "tools" / "build_idelika_spec_guide.mjs"
)

SHEET_NAMES = ["Consolidado", "Fabricacion", "Stock", "School Series", "Fuentes_Reglas"]
DATA_SHEETS = SHEET_NAMES[:4]
EXPECTED_COLUMNS = [
    "Proveedor",
    "Subcatalogo",
    "Archivo_origen",
    "Pagina_origen",
    "Clave_estable",
    "SKU",
    "Estado_codigo",
    "Producto",
    "Familia",
    "Variante",
    "Material",
    "Medidas",
    "Descripcion",
    "Unidad",
    "Costo_MXN",
    "Precio_referencia_MXN",
    "Precio_original",
    "Estado_precio",
    "Cotizable",
    "Minimo_compra",
    "Imagen_referencia",
    "URL_fuente",
    "Identidad_hash",
    "Notas",
]
EXPECTED_COUNTS = {
    "Consolidado": 220,
    "Fabricacion": 138,
    "Stock": 62,
    "School Series": 20,
}

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": DOC_REL_NS, "pr": PKG_REL_NS}


def _part_path(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath(base).parent.joinpath(target))


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value - 1


def _xml_signature(node: ET.Element) -> tuple[object, ...]:
    return (
        node.tag,
        tuple(sorted(node.attrib.items())),
        node.text,
        node.tail,
        tuple(_xml_signature(child) for child in node),
    )


def _load_python_builder():
    spec = importlib.util.spec_from_file_location("_idelika_spec_guide_test_builder", PYTHON_BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_programmatic_builder_contract_is_available():
    builder = _load_python_builder()

    assert callable(getattr(builder, "build_idelika_spec_guide", None))


def _write_unfrozen_pane_fixture(path: Path) -> dict[str, str]:
    sheet_parts = {
        "Consolidado": "xl/worksheets/consolidated.xml",
        "Fabricacion": "xl/worksheets/fabrication.xml",
        "Stock": "xl/worksheets/inventory.xml",
        "School Series": "xl/worksheets/school-series.xml",
        "Fuentes_Reglas": "xl/worksheets/sources-rules.xml",
    }
    relation_ids = {
        "Consolidado": "rConsolidated",
        "Fabricacion": "rFabrication",
        "Stock": "rInventory",
        "School Series": "rSchool",
        "Fuentes_Reglas": "rSources",
    }
    workbook = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<x:workbook xmlns:x="{MAIN_NS}" xmlns:r="{DOC_REL_NS}">'
        "<x:sheets>"
        + "".join(
            f'<x:sheet name="{name}" sheetId="{index}" r:id="{relation_ids[name]}" />'
            for index, name in enumerate(SHEET_NAMES, start=1)
        )
        + "</x:sheets></x:workbook>"
    ).encode("utf-8")
    relationships = (
        '\ufeff<?xml version="1.0" encoding="utf-8"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        + "".join(
            '<Relationship '
            f'Id="{relation_ids[name]}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="/{sheet_parts[name]}" />'
            for name in reversed(SHEET_NAMES)
        )
        + "</Relationships>"
    ).encode("utf-8")

    members: list[tuple[str, bytes]] = [
        ("[Content_Types].xml", b"<Types />"),
        ("xl/workbook.xml", workbook),
        ("xl/_rels/workbook.xml.rels", relationships),
        ("docProps/custom.bin", b"unchanged-commercial-payload"),
    ]
    for name in reversed(SHEET_NAMES):
        worksheet = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<x:worksheet xmlns:x="{MAIN_NS}">'
            '<x:sheetViews><x:sheetView showGridLines="0" workbookViewId="0" /></x:sheetViews>'
            '<x:sheetData><x:row r="1"><x:c r="A1" t="inlineStr"><x:is>'
            f"<x:t>{name}</x:t>"
            "</x:is></x:c></x:row></x:sheetData>"
            "</x:worksheet>"
        ).encode("utf-8")
        members.append((sheet_parts[name], worksheet))

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b"idelika-pane-fixture"
        for member, payload in members:
            archive.writestr(member, payload)
    return sheet_parts


def _zip_snapshot(path: Path) -> tuple[list[str], bytes, dict[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        return names, archive.comment, {name: archive.read(name) for name in names}


class WorkbookPackage:
    def __init__(self, path: Path):
        self.archive = zipfile.ZipFile(path)
        assert self.archive.testzip() is None
        self.names = set(self.archive.namelist())
        self.shared_strings = self._shared_strings()
        self.sheet_parts = self._sheet_parts()

    def close(self) -> None:
        self.archive.close()

    def xml(self, part: str) -> ET.Element:
        return ET.fromstring(self.archive.read(part))

    def _shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self.names:
            return []
        root = self.xml("xl/sharedStrings.xml")
        return ["".join(node.itertext()) for node in root.findall("m:si", NS)]

    def _sheet_parts(self) -> dict[str, str]:
        workbook = self.xml("xl/workbook.xml")
        relationships = self.xml("xl/_rels/workbook.xml.rels")
        targets = {
            relation.attrib["Id"]: _part_path("xl/workbook.xml", relation.attrib["Target"])
            for relation in relationships.findall("pr:Relationship", NS)
        }
        return {
            sheet.attrib["name"]: targets[sheet.attrib[f"{{{DOC_REL_NS}}}id"]]
            for sheet in workbook.findall("m:sheets/m:sheet", NS)
        }

    def cell_value(self, cell: ET.Element):
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(cell.find("m:is", NS).itertext())
        value = cell.findtext("m:v", default="", namespaces=NS)
        if cell_type == "s":
            return self.shared_strings[int(value)]
        if cell_type == "b":
            return value == "1"
        if cell_type in {"str", "e"}:
            return value
        if not value:
            return None
        number = float(value)
        return int(number) if number.is_integer() else number

    def rows(self, sheet_name: str) -> list[list[object]]:
        root = self.xml(self.sheet_parts[sheet_name])
        result = []
        for row in root.findall("m:sheetData/m:row", NS):
            values: list[object] = [None] * len(EXPECTED_COLUMNS)
            for cell in row.findall("m:c", NS):
                index = _column_index(cell.attrib["r"])
                if index < len(values):
                    values[index] = self.cell_value(cell)
            result.append(values)
        return result

    def table_root(self, sheet_name: str) -> ET.Element:
        sheet_part = self.sheet_parts[sheet_name]
        relationships_part = str(
            PurePosixPath(sheet_part).parent
            / "_rels"
            / f"{PurePosixPath(sheet_part).name}.rels"
        )
        relationships = self.xml(relationships_part)
        table_targets = [
            _part_path(sheet_part, relation.attrib["Target"])
            for relation in relationships.findall("pr:Relationship", NS)
            if relation.attrib.get("Type", "").endswith("/table")
        ]
        assert len(table_targets) == 1
        return self.xml(table_targets[0])

    def mxn_style_ids(self) -> set[str]:
        styles = self.xml("xl/styles.xml")
        formats = {
            node.attrib["numFmtId"]: node.attrib["formatCode"]
            for node in styles.findall("m:numFmts/m:numFmt", NS)
        }
        xfs = styles.findall("m:cellXfs/m:xf", NS)
        return {
            str(index)
            for index, xf in enumerate(xfs)
            if "MXN" in formats.get(xf.attrib.get("numFmtId", ""), "").upper()
        }


@pytest.fixture(scope="module")
def package():
    if not WORKBOOK.exists():
        pytest.skip("El XLSX todavía no existe")
    workbook = WorkbookPackage(WORKBOOK)
    try:
        yield workbook
    finally:
        workbook.close()


def test_required_builders_exist_before_the_workbook_can_be_generated():
    assert PYTHON_BUILDER.is_file()
    assert JS_BUILDER.is_file()


def test_real_workbook_and_validation_summary_exist():
    assert WORKBOOK.is_file()
    assert SUMMARY.is_file()


def test_builder_contract_uses_only_the_bundled_artifact_tool_for_authoring():
    if not JS_BUILDER.exists() or not PYTHON_BUILDER.exists():
        pytest.skip("Los builders todavía no existen")
    javascript = JS_BUILDER.read_text(encoding="utf-8")
    python = PYTHON_BUILDER.read_text(encoding="utf-8")
    assert (
        "file:///C:/Users/pepem/.cache/codex-runtimes/codex-primary-runtime/"
        "dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs"
    ) in javascript
    assert "Workbook" in javascript and "SpreadsheetFile.exportXlsx" in javascript
    assert "extract_idelika_rows" in python
    assert "node.exe" in python and "subprocess" in python
    prohibited = ("openpyxl", "xlsxwriter", "pandas.ExcelWriter")
    assert not any(name in javascript or name in python for name in prohibited)


def test_ooxml_postprocess_changes_only_the_four_name_mapped_panes(tmp_path):
    workbook = tmp_path / "unfrozen.xlsx"
    sheet_parts = _write_unfrozen_pane_fixture(workbook)
    before_names, before_comment, before_payloads = _zip_snapshot(workbook)

    builder = _load_python_builder()
    postprocess = getattr(builder, "_restore_frozen_header_panes", lambda path: None)
    postprocess(workbook)

    after_names, after_comment, after_payloads = _zip_snapshot(workbook)
    expected_changed_parts = {sheet_parts[name] for name in DATA_SHEETS}
    changed_parts = {
        name for name in before_names if before_payloads[name] != after_payloads[name]
    }
    assert after_names == before_names
    assert after_comment == before_comment
    assert changed_parts == expected_changed_parts

    for sheet_name, part in sheet_parts.items():
        if sheet_name not in DATA_SHEETS:
            assert after_payloads[part] == before_payloads[part]
            continue

        before_root = ET.fromstring(before_payloads[part])
        after_root = ET.fromstring(after_payloads[part])
        sheet_view = after_root.find("m:sheetViews/m:sheetView", NS)
        assert sheet_view is not None
        pane = sheet_view.find("m:pane", NS)
        assert pane is not None
        assert pane.attrib == {
            "ySplit": "1",
            "topLeftCell": "A2",
            "activePane": "bottomLeft",
            "state": "frozen",
        }
        assert sheet_view.findall("m:selection", NS) == []
        sheet_view.remove(pane)
        assert _xml_signature(after_root) == _xml_signature(before_root)


def test_xlsx_package_and_sheet_order(package):
    assert "[Content_Types].xml" in package.names
    assert "xl/workbook.xml" in package.names
    assert list(package.sheet_parts) == SHEET_NAMES


@pytest.mark.parametrize("sheet_name", DATA_SHEETS)
def test_data_sheets_have_exact_columns_and_real_counts(package, sheet_name):
    rows = package.rows(sheet_name)
    assert rows[0] == EXPECTED_COLUMNS
    assert len(rows) - 1 == EXPECTED_COUNTS[sheet_name]


def test_consolidated_order_counts_provenance_and_identity_quality(package):
    rows = package.rows("Consolidado")[1:]
    subcatalogs = [row[1] for row in rows]
    assert subcatalogs == ["Fabricacion"] * 138 + ["Stock"] * 62 + ["School Series"] * 20
    assert all(row[0] == "IDÉLIKA" for row in rows)
    assert all(isinstance(row[3], int) and row[3] >= 1 for row in rows)
    assert all(isinstance(row[21], str) and row[21].startswith("https://") for row in rows)
    assert all(row[2] and row[20] for row in rows)
    assert all(row[6] == ("oficial" if row[5] else "por_verificar") for row in rows)
    assert len({row[4] for row in rows}) == 220
    assert len({row[22] for row in rows}) == 220

    costs_by_identity: dict[str, set[object]] = {}
    for row in rows:
        costs_by_identity.setdefault(row[22], set()).add(row[14])
    assert not {identity: costs for identity, costs in costs_by_identity.items() if len(costs) > 1}


def test_school_series_remains_quotable_without_fabricated_zero_cost(package):
    rows = package.rows("School Series")[1:]
    assert all(row[14] is None for row in rows)
    assert all(row[17] == "por_confirmar" for row in rows)
    assert all(row[18] == "Sí" for row in rows)


@pytest.mark.parametrize("sheet_name", DATA_SHEETS)
def test_data_sheets_have_filters_frozen_headers_and_mxn_formats(package, sheet_name):
    sheet = package.xml(package.sheet_parts[sheet_name])
    pane = sheet.find("m:sheetViews/m:sheetView/m:pane", NS)
    assert pane is not None
    assert pane.attrib == {
        "ySplit": "1",
        "topLeftCell": "A2",
        "activePane": "bottomLeft",
        "state": "frozen",
    }

    table = package.table_root(sheet_name)
    assert table.attrib["ref"] == f"A1:X{EXPECTED_COUNTS[sheet_name] + 1}"
    assert table.find("m:autoFilter", NS) is not None

    money_styles = package.mxn_style_ids()
    assert money_styles
    cells = sheet.findall("m:sheetData/m:row/m:c", NS)
    money_cells = [
        cell
        for cell in cells
        if _column_index(cell.attrib["r"]) in {14, 15}
        and int(re.search(r"\d+$", cell.attrib["r"]).group(0)) > 1
    ]
    assert money_cells
    assert all(cell.attrib.get("s") in money_styles for cell in money_cells)


def test_live_metric_formulas_and_formula_error_scan_are_visible(package):
    rules = package.xml(package.sheet_parts["Fuentes_Reglas"])
    formulas = ["".join(node.itertext()) for node in rules.findall(".//m:f", NS)]
    formula_text = "\n".join(formulas).upper()
    assert len(formulas) >= 10
    assert "COUNTA(" in formula_text
    assert "COUNTIF(" in formula_text
    assert "COUNTIFS(" in formula_text
    assert "CONSOLIDADO" in formula_text

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["formula_errors"] == []
    assert summary["inspection"]["formula_count"] >= 10
    assert summary["inspection"]["artifact_tool_inspect"] is True


def test_validation_summary_renders_and_determinism():
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert Path(summary["output_path"]).resolve() == WORKBOOK.resolve()
    assert summary["engine"] == "@oai/artifact-tool"
    assert summary["sheets"] == SHEET_NAMES
    assert summary["counts"] == {
        "Fabricacion": 138,
        "Stock": 62,
        "School Series": 20,
    }
    assert summary["total_rows"] == 220
    assert summary["duplicates"] == {"stable_keys": 0, "identities": 0}
    assert summary["price_conflicts"] == 0
    assert summary["determinism"]["passed"] is True
    assert summary["determinism"]["first_normalized_sha256"] == summary["determinism"][
        "second_normalized_sha256"
    ]

    assert list(summary["renders"]) == SHEET_NAMES
    for render in summary["renders"].values():
        path = Path(render["path"])
        assert path.is_file() and path.parent == OUTPUT_DIR
        payload = path.read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", payload[16:24])
        assert width >= 800 and height >= 300
