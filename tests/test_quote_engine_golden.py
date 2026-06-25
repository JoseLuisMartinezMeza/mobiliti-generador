from pathlib import Path
import os
import sys

import pytest
from openpyxl import Workbook, load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine import generate_quote  # noqa: E402
from mobiliti_saas.quote_engine.engine import SECTION_PROD_STARTS, _copy_source_sheet, _write_estrategia_comercial  # noqa: E402


DOWNLOADS = Path(r"C:\Users\pepem\Downloads")
TEMPLATE_DIR = ROOT / "versiones historial" / "HISTORIAL DE VERSIONES" / "Mobiliti_Generador_Windows"
TEMPLATE = next(TEMPLATE_DIR.glob("Formato*.xlsx"), TEMPLATE_DIR / "Formato Cotizacion 2026 GDL (1).xlsx")
GOLDENS = [
    DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx",
    ROOT / "versiones historial" / "KIVO BRAVANTE-Quotation Sheet - V1.xlsx",
]
TARGET_VISUAL = next(
    (path for path in DOWNLOADS.glob("*.xlsx") if "ESSENTIA EVIDIKA" in path.name and not path.name.startswith("~$")),
    DOWNLOADS / "Cotizacion ESSENTIA EVIDIKA.xlsx",
)
IZA_CORRECTA = DOWNLOADS / "Cotizacion_IZA_REFORMA.xlsx"
LEAC_QUOTATION = DOWNLOADS / "LEAC" / "LEAC- GOTAPP - MORGINS-Quotation Sheet - V1.xlsx"
EXPECTED_TEMPLATE_SHEETS = {
    "Cotizacion",
    "Mobiliti",
    "Estrategia Comercial ",
    "Fletes",
    "Proveedores",
    "SPEC LAMINADO JOME",
    "SPEC-GUIDE-LUMBRO",
    "SPEC-GUIDE ESTRUCTURAS",
    "Spec Guide Estructura ",
    "SPEC-GUIDE-CR GLOBAL",
    "Meses Sin Intereses Tarjetas",
    "Quotation",
}


@pytest.mark.parametrize("source", GOLDENS)
def test_python_engine_generates_golden_structure(source, tmp_path):
    if not source.exists() or not TEMPLATE.exists():
        pytest.skip("Golden input/template not available on this machine")

    output = tmp_path / f"{source.stem}_python.xlsx"
    generate_quote(
        source,
        output,
        {"cotizacion": "GOLDEN", "proyecto": "Golden", "cliente": "Cliente"},
        TEMPLATE,
    )

    wb = load_workbook(output, data_only=False)
    assert "Cotizacion" in wb.sheetnames
    assert "Mobiliti" in wb.sheetnames
    assert "Quotation" in wb.sheetnames
    assert wb["Cotizacion"]["D17"].value == "=Quotation!E9"
    assert str(wb["Mobiliti"]["K14"].value).startswith("=Quotation!")
    assert wb["Cotizacion"].print_area
    assert len(wb["Cotizacion"]._images) > 0
    wb.close()


def test_copy_source_sheet_handles_leac_external_styles(tmp_path):
    if os.environ.get("RUN_SLOW_QUOTE_TESTS") != "1":
        pytest.skip("LEAC copy is a slow local regression test")
    if not LEAC_QUOTATION.exists():
        pytest.skip("LEAC quotation fixture not available on this machine")

    workbook = Workbook()
    _copy_source_sheet(LEAC_QUOTATION, workbook)
    output = tmp_path / "leac-copy.xlsx"
    workbook.save(output)
    workbook.close()

    copied = load_workbook(output)
    assert "Quotation" in copied.sheetnames
    assert len(copied["Quotation"].merged_cells.ranges) > 0
    copied.close()


def test_visual_golden_references_are_intact():
    if not TARGET_VISUAL.exists() or not IZA_CORRECTA.exists():
        pytest.skip("Visual golden files not available on this machine")

    for path in [TARGET_VISUAL, IZA_CORRECTA]:
        wb = load_workbook(path, data_only=False)
        assert EXPECTED_TEMPLATE_SHEETS.issubset(set(wb.sheetnames))
        cot = wb["Cotizacion"]
        assert cot.max_column >= 53
        assert cot.print_area
        assert len(cot._images) >= 30
        assert len(cot.merged_cells.ranges) >= 65
        assert cot["B4"].value is None
        assert cot["D17"].value == "=Quotation!E9"
        assert str(cot["F17"].value).startswith("=Mobiliti!W")
        assert (cot.row_dimensions[17].height or 0) >= 300
        wb.close()


def test_python_engine_does_not_fallback_to_blank_workbook(tmp_path):
    source = DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx"
    if not source.exists():
        pytest.skip("IZA input not available on this machine")

    with pytest.raises(FileNotFoundError):
        generate_quote(source, tmp_path / "out.xlsx", {}, tmp_path / "missing-template.xlsx")


def test_python_engine_passes_image_provider_metadata(monkeypatch, tmp_path):
    source = DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx"
    if not source.exists() or not TEMPLATE.exists():
        pytest.skip("Golden input/template not available on this machine")

    captured = {}

    def fake_improve_image_map(image_map, temp_dir, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr("mobiliti_saas.quote_engine.engine.improve_image_map", fake_improve_image_map)
    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.engine._generate_missing_dezgo_images",
        lambda image_map, items, temp_dir, metadata, stats=None: image_map,
    )

    generate_quote(
        source,
        tmp_path / "out.xlsx",
        {"cotizacion": "GOLDEN", "image_provider": "dezgo"},
        TEMPLATE,
    )

    assert captured["image_provider"] == "dezgo"


def test_estrategia_comercial_references_generated_total_row():
    if not TEMPLATE.exists():
        pytest.skip("Template not available on this machine")

    wb = load_workbook(TEMPLATE, data_only=False)
    if "Estrategia Comercial " not in wb.sheetnames:
        wb.close()
        pytest.skip("Template with Estrategia Comercial not available on this machine")

    ws = wb["Estrategia Comercial "]
    _write_estrategia_comercial(ws, total_row=74)

    assert ws["D59"].value == "=Cotizacion!H74"
    wb.close()


def test_python_engine_preserves_workbook_contract(tmp_path):
    source = DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx"
    if not source.exists() or not TEMPLATE.exists():
        pytest.skip("Golden input/template not available on this machine")

    output = tmp_path / "iza_python_contract.xlsx"
    generate_quote(
        source,
        output,
        {"cotizacion": "GOLDEN", "proyecto": "Golden", "cliente": "Cliente"},
        TEMPLATE,
    )

    wb = load_workbook(output, data_only=False)
    assert EXPECTED_TEMPLATE_SHEETS.issubset(set(wb.sheetnames))
    cot = wb["Cotizacion"]
    assert cot.max_column >= 53
    assert cot.print_area
    assert len(cot._images) >= 30
    assert len(cot.merged_cells.ranges) >= 50
    assert cot["B4"].value is None
    assert cot["A16"].value == "=Quotation!A8"
    assert cot["D17"].value == "=Quotation!E9"
    assert str(cot["F17"].value).startswith(("=Mobiliti!W", "=IFERROR((Mobiliti!X"))
    assert (cot.row_dimensions[19].height or 0) >= 300
    merged_ranges = {str(rng) for rng in cot.merged_cells.ranges}
    assert "D58:G58" in merged_ranges
    assert "H58:J58" in merged_ranges
    for row in range(58, 63):
        assert cot[f"H{row}"].alignment.horizontal == "right"
    assert (cot.row_dimensions[58].height or 0) < 50
    assert (cot.row_dimensions[59].height or 0) < 50
    assert cot["C20"].alignment.vertical == "top"
    assert cot["C32"].alignment.vertical == "top"
    assert any(
        isinstance(cot.cell(row, 6).value, str) and "+Mobiliti!X" in cot.cell(row, 6).value
        for row in range(1, cot.max_row + 1)
    )
    assert any(
        getattr(getattr(img, "anchor", None), "_from", None) is not None
        and int(img.anchor._from.col) == 2
        for img in cot._images
    )
    assert cot["F32"].value == (
        "=IFERROR((Mobiliti!X155+Mobiliti!X156+Mobiliti!X157+Mobiliti!X158)/Mobiliti!H155,0)"
    )
    mob = wb["Mobiliti"]
    assert mob["J6"].value == "USD/MXN"
    assert mob["K6"].value == 20
    assert mob["K8"].value == "Guadalajara"
    mob_merged_ranges = {str(rng) for rng in mob.merged_cells.ranges}
    assert "D13:J13" in mob_merged_ranges
    assert "A47:F47" in mob_merged_ranges
    assert "A48:J48" in mob_merged_ranges
    assert str(mob["D13"].value).startswith("Sección 1 - ")
    assert str(mob["A48"].value).startswith("Sección 2 - ")
    estrategia = wb["Estrategia Comercial "]
    total_rows = [
        row
        for row in range(1, cot.max_row + 1)
        if str(cot.cell(row, 4).value or "").strip().upper() == "TOTAL:"
    ]
    assert total_rows
    assert estrategia["D59"].value == f"=Cotizacion!H{total_rows[-1]}"
    fletes = wb["Fletes"]
    assert fletes["A5"].value == "Guadalajara"
    assert fletes["C5"].value == "Monterrey"
    assert fletes["A17"].value == "Guadalajara"
    assert fletes["C17"].value == "Guadalajara"
    assert fletes["I8"].value == "Escritorios-WorkStation"
    assert fletes["M8"].value == "Escritorios-WorkStation"
    template_wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    template_fletes = template_wb["Fletes"]
    for cell in ["I16", "M16", "I17", "M17", "I18", "M18"]:
        assert fletes[cell].value == template_fletes[cell].value
    template_wb.close()
    provider_validations = [
        dv
        for dv in mob.data_validations.dataValidation
        if dv.type == "list" and dv.formula1 == "=Proveedores!$A$2:$A$32"
    ]
    assert provider_validations
    assert "F14:F45" in str(provider_validations[0].sqref)
    region_validations = [
        dv
        for dv in mob.data_validations.dataValidation
        if dv.type == "list" and dv.formula1 == "Taba_Region"
    ]
    assert region_validations
    assert "P14:P45" in str(region_validations[0].sqref)
    populated_rows = [
        row
        for start in [14, 49, 84, 119, 154, 189, 224, 259, 294, 329, 364, 398, 432]
        for row in range(start, start + 32)
        if any(mob.cell(row, col).value for col in (4, 5, 6))
    ]
    assert populated_rows
    for row in populated_rows:
        assert mob.cell(row, 16).value == "Centro"
        assert mob.cell(row, 26).value == f"=MIN(0.4,Y{row})"
        assert "ERROR" not in str(mob.cell(row, 28).value or "")
        assert str(mob.cell(row, 29).value or "") == f"=AB{row}*H{row}"
    blank_formula_errors = []
    for start in [14, 49, 84, 119, 154, 189, 224, 259, 294, 329, 364, 398, 432]:
        for row in range(start, start + 32):
            if any(mob.cell(row, col).value for col in (4, 5, 6)):
                continue
            values = [str(mob.cell(row, col).value or "") for col in range(11, 32)]
            if any("#REF!" in value or "ERROR" in value for value in values):
                blank_formula_errors.append(row)
    assert blank_formula_errors == []
    assert wb["Cotizacion"]["G17"].value == 0.4
    quotation = wb["Quotation"]
    assert len(quotation._images) >= 30
    assert quotation.freeze_panes is None
    assert quotation.sheet_view.zoomScale == 110
    assert quotation.page_setup.orientation == "landscape"
    assert quotation.page_setup.paperSize == 9
    assert quotation.sheet_properties.pageSetUpPr.fitToPage is True
    assert quotation.print_title_rows == "$7:$7"
    assert quotation.page_margins.left < 0.3
    assert quotation.page_margins.right < 0.3
    wb.close()


def test_mobiliti_product_starts_are_not_merged_rows():
    if not TEMPLATE.exists():
        pytest.skip("Template not available on this machine")

    wb = load_workbook(TEMPLATE, data_only=False, keep_links=False)
    ws = wb["Mobiliti"]
    try:
        merged_rows = {
            row
            for merged in ws.merged_cells.ranges
            if merged.min_col <= 29 and merged.max_col >= 4
            for row in range(merged.min_row, merged.max_row + 1)
        }
        assert not (set(SECTION_PROD_STARTS) & merged_rows)
        assert 398 not in SECTION_PROD_STARTS
        assert 399 in SECTION_PROD_STARTS
    finally:
        wb.close()


def test_python_engine_generates_cummins_large_quote(tmp_path):
    source = DOWNLOADS / "CUMMINS-Quotation Sheet - V1.xlsx"
    template = ROOT / "mobiliti_saas" / "worker" / "templates" / "Formato Cotizacion 2026 GDL.xlsx"
    if not source.exists() or not template.exists():
        pytest.skip("CUMMINS input/template not available on this machine")

    output = tmp_path / "cummins_python.xlsx"
    generate_quote(
        source,
        output,
        {
            "cotizacion": "CUMMINS-TEST",
            "proyecto": "Cummins",
            "cliente": "Cummins",
            "image_provider": "pillow",
        },
        template,
    )

    wb = load_workbook(output, data_only=False)
    assert "Cotizacion" in wb.sheetnames
    assert "Mobiliti" in wb.sheetnames
    assert wb["Mobiliti"]["D399"].value
    cot = wb["Cotizacion"]
    collapsed_lumbro_formulas = [
        cot.cell(row, 6).value
        for row in range(1, cot.max_row + 1)
        if isinstance(cot.cell(row, 6).value, str) and "+Mobiliti!X" in cot.cell(row, 6).value
    ]
    assert collapsed_lumbro_formulas
    assert all("/Mobiliti!H" in formula for formula in collapsed_lumbro_formulas)
    assert not any(
        isinstance(cot.cell(row, 6).value, str) and "+Mobiliti!W" in cot.cell(row, 6).value
        for row in range(1, cot.max_row + 1)
    )
    assert wb["Cotizacion"].print_area
    wb.close()
