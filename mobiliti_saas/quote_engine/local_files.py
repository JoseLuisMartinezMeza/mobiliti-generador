"""Preservación de artefactos para la revisión local, sin cambiar producción."""

from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl.drawing.spreadsheet_drawing import SpreadsheetDrawing
from openpyxl.worksheet._writer import WorksheetWriter
from openpyxl.writer.excel import ExcelWriter


def local_files_preserved() -> bool:
    return os.environ.get("MOBILITI_DEV_MODE", "").lower() in {
        "1", "true", "yes",
    }


def temporary_directory(*, prefix: str):
    if not local_files_preserved():
        return tempfile.TemporaryDirectory(prefix=prefix)
    default_store = Path(__file__).resolve().parents[2] / ".mobiliti_dev_store"
    root = Path(os.environ.get("MOBILITI_DEV_STORE_DIR", default_store)) / "worker-runs"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=root, delete=False)


class _MemoryWorksheetWriter(ExcelWriter):
    """Serializa hojas normales sin crear los XML temporales que openpyxl borra."""

    def write_worksheet(self, worksheet):
        worksheet._drawing = SpreadsheetDrawing()
        worksheet._drawing.charts = worksheet._charts
        worksheet._drawing.images = worksheet._images
        with BytesIO() as xml:
            writer = WorksheetWriter(worksheet, out=xml)
            writer.write()
            worksheet._rels = writer._rels
            self._archive.writestr(worksheet.path[1:], xml.getvalue())
        self.manifest.append(worksheet)


def save_quotation_workbook(workbook, output) -> None:
    if not local_files_preserved():
        workbook.save(output)
        return
    if workbook.write_only:
        raise ValueError("La preservación local requiere un workbook normal")
    workbook.properties.modified = datetime.now(timezone.utc).replace(tzinfo=None)
    with ZipFile(output, "w", ZIP_DEFLATED, allowZip64=True) as archive:
        _MemoryWorksheetWriter(workbook, archive).save()
