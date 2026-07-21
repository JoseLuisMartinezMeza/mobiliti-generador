from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from openpyxl.styles import PatternFill
from PIL import Image


def write_import_fixture(path: Path, *, currency: str | None = None) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quotation"
    for column, title in {
        1: "No.",
        2: "Item Name",
        3: "Photo",
        4: "Description",
        5: "Dimension",
        7: "Q'ty",
        8: "Vol.",
        10: "Unit Price",
    }.items():
        sheet.cell(7, column, title)
    if currency:
        sheet.cell(7, 14, "Original Currency")

    rows = [
        (8, "category", "SALA DE JUNTAS SECUNDARIO"),
        (9, "product", "DV74 I-Varna II Conference Table"),
        (10, "category", "MUESTRAS"),
        (11, "product", "CAI63SW Alien Task Chair"),
        (12, "product", "CAL61KC Aulenti Task Chair"),
        (13, "product", "CAT60SC Altaes Task Chair"),
        (14, "product", "DL60 Single Seat Workstation"),
        (15, "product", "DL61 Double Seat Workstation"),
        (16, "category", "CONCEJO"),
        (17, "product", "DV74 I-Varna II Conference Table"),
    ]
    product_index = 0
    for row, kind, value in rows:
        if kind == "category":
            sheet.cell(row, 1, f"- {value}")
            continue
        product_index += 1
        sheet.cell(row, 1, product_index)
        sheet.cell(row, 2, value)
        sheet.cell(row, 4, f"Descripción {product_index}")
        sheet.cell(row, 5, f"{600 + product_index} x 600 mm")
        sheet.cell(row, 7, 1 if row != 14 else 2)
        sheet.cell(row, 8, Decimal("0.25"))
        sheet.cell(row, 10, Decimal("80.50") if row == 11 else Decimal("100.00"))
        if currency:
            sheet.cell(row, 14, currency)
        image_path = path.parent / f"fixture-{row}.png"
        Image.new("RGB", (80, 60), (20 * product_index, 80, 120)).save(image_path)
        sheet.add_image(WorkbookImage(str(image_path)), f"C{row}")
    sheet["A1"] = "SUNON TECHNOLOGY CO.,LTD."
    sheet.cell(65536, 14).fill = PatternFill("solid", fgColor="FFFFFF")
    workbook.save(path)
    workbook.close()
    return path
