import os
import sys
from pathlib import Path

from PIL import Image
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as WorkbookImage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "worker"))

from online_quote_generator import generate_online_quote, read_items


def _sample_quotation(path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    headers = {
        1: "No.",
        2: "Item",
        4: "Description",
        5: "Dimension",
        7: "Qty",
        10: "List Price",
    }
    for col, value in headers.items():
        ws.cell(row=7, column=col, value=value)
    ws.cell(row=8, column=1, value="- Escritorios")
    ws.cell(row=9, column=1, value=1)
    ws.cell(row=9, column=2, value="Mesa Uno")
    ws.cell(row=9, column=4, value="Mesa operativa")
    ws.cell(row=9, column=5, value="1200 x 600")
    ws.cell(row=9, column=7, value=2)
    ws.cell(row=9, column=10, value=1000)
    wb.save(path)


def _sample_quotation_with_image(path: Path, image_path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    headers = {
        1: "No.",
        2: "Item",
        4: "Description",
        5: "Dimension",
        7: "Qty",
        10: "List Price",
    }
    for col, value in headers.items():
        ws.cell(row=7, column=col, value=value)
    ws.cell(row=8, column=1, value="- Escritorios")
    ws.cell(row=9, column=1, value=1)
    ws.cell(row=9, column=2, value="Mesa Uno")
    ws.cell(row=9, column=4, value="Mesa operativa")
    ws.cell(row=9, column=5, value="1200 x 600")
    ws.cell(row=9, column=7, value=2)
    ws.cell(row=9, column=10, value=1000)
    ws.add_image(WorkbookImage(str(image_path)), "B9")
    wb.save(path)


def test_read_items_from_quotation(tmp_path):
    source = tmp_path / "quotation.xlsx"
    _sample_quotation(source)

    items, column_map = read_items(source)

    assert column_map["cantidad"] == "G"
    assert column_map["list_price"] == "J"
    assert [item.tipo for item in items] == ["categoria", "producto"]
    assert items[1].nombre == "Mesa Uno"


def test_generate_online_quote_creates_valid_xlsx(tmp_path):
    source = tmp_path / "quotation.xlsx"
    output = tmp_path / "cotizacion.xlsx"
    _sample_quotation(source)

    generate_online_quote(
        source,
        output,
        {
            "cotizacion": "COT-001",
            "proyecto": "Demo",
            "cliente": "Cliente Test",
            "descuento": "30",
        },
    )

    wb = load_workbook(output, data_only=False)
    assert "Cotizacion" in wb.sheetnames
    assert "Mobiliti" in wb.sheetnames
    assert "Quotation" in wb.sheetnames
    ws = wb["Cotizacion"]
    mob = wb["Mobiliti"]
    assert ws["A1"].value == "MOBILITI - COTIZACION"
    assert ws["B3"].value == "COT-001"
    assert ws["A16"].value == "=Quotation!A8"
    assert ws["A17"].value == "=Quotation!B9"
    assert ws["D17"].value == "=Quotation!E9"
    assert ws["G17"].value == 0.3
    assert ws["J17"].value == "=I17*E17"
    assert mob["K14"].value == "=Quotation!E9"
    assert mob["D14"].value == "=Quotation!B9"
    wb.close()


def test_generate_online_quote_inserts_dezgo_enhanced_image_not_original(monkeypatch, tmp_path):
    source = tmp_path / "quotation.xlsx"
    original = tmp_path / "original.png"
    output = tmp_path / "cotizacion.xlsx"
    Image.new("RGB", (80, 60), (20, 20, 20)).save(original)
    _sample_quotation_with_image(source, original)
    monkeypatch.setenv("DEZGO_API_KEY", "fake-key")

    def fake_enhance_with_dezgo(_source_path, output_path, _config=None):
        Image.new("RGBA", (90, 90), (255, 0, 0, 255)).save(output_path, "PNG")
        return Path(output_path)

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.image_processing.enhance_with_dezgo",
        fake_enhance_with_dezgo,
    )

    generate_online_quote(
        source,
        output,
        {
            "cotizacion": "COT-IA",
            "proyecto": "Demo IA",
            "cliente": "Cliente Test",
            "image_provider": "dezgo",
            "image_background": "white",
            "image_prompt": "Mejora la calidad de imagen y que este en fondo blanco",
        },
    )

    wb = load_workbook(output, data_only=False)
    try:
        cotizacion_images = [
            img
            for img in wb["Cotizacion"]._images
            if int(getattr(img.anchor, "_from").row) == 16 and int(getattr(img.anchor, "_from").col) == 1
        ]
        assert cotizacion_images
        with Image.open(cotizacion_images[0].ref) as embedded:
            pixel = embedded.convert("RGB").getpixel((embedded.width // 2, embedded.height // 2))
        assert pixel == (255, 0, 0)
    finally:
        wb.close()


def test_generate_online_quote_uses_vol_for_mobiliti_m3(tmp_path):
    source = tmp_path / "quotation.xlsx"
    output = tmp_path / "cotizacion.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    for col, value in {1: "No.", 2: "Item", 4: "Description", 5: "Dimension", 7: "Qty", 8: "Vol", 10: "List Price"}.items():
        ws.cell(row=7, column=col, value=value)
    ws.cell(row=8, column=1, value="- Sillas")
    ws.cell(row=9, column=1, value=1)
    ws.cell(row=9, column=2, value="Silla Uno")
    ws.cell(row=9, column=4, value="Silla operativa")
    ws.cell(row=9, column=5, value="600 x 600")
    ws.cell(row=9, column=7, value=3)
    ws.cell(row=9, column=8, value=0.45)
    ws.cell(row=9, column=10, value=500)
    wb.save(source)

    generate_online_quote(source, output, {})

    out = load_workbook(output, data_only=False)
    assert out["Cotizacion"]["D17"].value == "=Quotation!E9"
    assert out["Mobiliti"]["K14"].value == "=Quotation!H9"
    out.close()
