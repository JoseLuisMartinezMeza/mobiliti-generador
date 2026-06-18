from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from mobiliti_saas.quote_engine.engine import _sanitize_output_xlsx_for_excel


def test_output_sanitizer_removes_empty_color_changes_and_safe_quotation_view(tmp_path: Path):
    workbook_path = tmp_path / "bad.xlsx"
    with ZipFile(workbook_path, "w", ZIP_DEFLATED) as zf:
        zf.writestr(
            "xl/workbook.xml",
            (
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Quotation" sheetId="13" r:id="rId13"/></sheets></workbook>'
            ),
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId13" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet13.xml"/></Relationships>'
            ),
        )
        zf.writestr(
            "xl/worksheets/sheet13.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetViews><sheetView showGridLines="0" zoomScale="110" zoomScaleNormal="110" '
                'workbookViewId="0"><selection pane="bottomLeft" activeCell="A99" '
                'sqref="A99:XFD99"/></sheetView></sheetViews><sheetData/></worksheet>'
            ),
        )
        zf.writestr(
            "xl/drawings/drawing1.xml",
            (
                '<wsDr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<clrChange><clrFrom/><clrTo/></clrChange>"
                "<a:clrChange><a:clrFrom/><a:clrTo/></a:clrChange>"
                "</wsDr>"
            ),
        )

    _sanitize_output_xlsx_for_excel(workbook_path)

    with ZipFile(workbook_path) as zf:
        drawing = zf.read("xl/drawings/drawing1.xml").decode("utf-8")
        quotation = zf.read("xl/worksheets/sheet13.xml").decode("utf-8")

    assert "clrChange" not in drawing
    assert 'activeCell="A1"' in quotation
    assert 'sqref="A1"' in quotation
    assert "XFD99" not in quotation
