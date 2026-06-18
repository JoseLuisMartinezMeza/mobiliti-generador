from pathlib import Path
import sys

import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine import (  # noqa: E402
    classify_product_name,
    generate_quote,
    load_category_dictionary,
)


DOWNLOADS = Path(r"C:\Users\pepem\Downloads")
TEMPLATE_DIR = ROOT / "versiones historial" / "HISTORIAL DE VERSIONES" / "Mobiliti_Generador_Windows"
TEMPLATE = next(TEMPLATE_DIR.glob("Formato*.xlsx"), TEMPLATE_DIR / "Formato Cotizacion 2026 GDL (1).xlsx")


def test_batch_alias_learning_keeps_task_chair_as_silla():
    dictionary = load_category_dictionary(["CLG65SW Locke Task Chair"])

    assert classify_product_name("CLG65SW Locke Task Chair", dictionary) == "Silla"
    assert classify_product_name("CLG65SW Locke", dictionary) == "Silla"


def test_meeting_name_classifies_as_meeting_table_without_breaking_meeting_chair():
    dictionary = load_category_dictionary(["Lido meeting ch 6px", "CLG65SW Meeting Chair"])

    assert classify_product_name("Lido meeting ch 6px", dictionary) == "Mesas de Juntas"
    assert classify_product_name("CLG65SW Meeting Chair", dictionary) == "Silla"


def test_lounge_and_modular_names_classify_as_sillones():
    dictionary = load_category_dictionary([
        "SH31.2.MR Flower 6 Lounge Seating",
        "MR Tetris Modular Seating",
        "Modular meeting table",
    ])

    assert classify_product_name("SH31.2.MR Flower 6 Lounge Seating", dictionary) == "Sillones"
    assert classify_product_name("MR Tetris Modular Seating", dictionary) == "Sillones"
    assert classify_product_name("Modular meeting table", dictionary) == "Mesas de Juntas"


def test_python_engine_writes_product_category_from_product_name(tmp_path):
    source = DOWNLOADS / "IZA REFORMA-Quotation Sheet - V1.xlsx"
    if not source.exists() or not TEMPLATE.exists():
        pytest.skip("Golden input/template not available on this machine")

    output = tmp_path / "iza_python_categories.xlsx"
    generate_quote(
        source,
        output,
        {"cotizacion": "GOLDEN", "proyecto": "Golden", "cliente": "Cliente"},
        TEMPLATE,
    )

    wb = load_workbook(output, data_only=False)
    mob = wb["Mobiliti"]
    assert mob["E14"].value == "Silla"
    assert mob["E49"].value == "Archiveros Moviles y Fijos"
    wb.close()
