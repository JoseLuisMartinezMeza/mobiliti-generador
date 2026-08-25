from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine import engine  # noqa: E402
from mobiliti_saas.quote_engine.engine import (  # noqa: E402
    _item_auto_electrification,
    _load_lumbro_prices,
    _lumbro_accessories_for_item,
)
from mobiliti_saas.quote_engine.parser import QuoteItem  # noqa: E402


TEMPLATE = ROOT / "mobiliti_saas" / "worker" / "templates" / "Formato Cotizacion 2026 GDL.xlsx"


def test_lumbro_accessories_for_workstation_pax_multiplies_quantity():
    item = QuoteItem(tipo="producto", row=9, nombre="Estacion Lido 8PAX", cantidad=2)

    accessories = _lumbro_accessories_for_item(item, "Escritorios-WorkStation")

    assert accessories == [
        ("LIDO.OP-INT", 16),
        ("JUMP-1.5M", 16),
        ("CAJA-FUS", 4),
    ]


def test_mixed_auto_electrification_is_per_line_while_legacy_remains_enabled():
    enabled = QuoteItem(
        tipo="producto",
        row=9,
        proveedor="Tarkett",
        electrificacion_automatica=True,
    )
    disabled = QuoteItem(
        tipo="producto",
        row=10,
        proveedor="ALMA",
        electrificacion_automatica=False,
    )
    mixed = {"catalog_price_mode": "mixed_catalog_converted"}

    assert _item_auto_electrification(enabled, mixed) is True
    assert _item_auto_electrification(disabled, mixed) is False
    assert _item_auto_electrification(QuoteItem(tipo="producto", row=11), {}) is True
    assert _item_auto_electrification(
        QuoteItem(tipo="producto", row=12),
        {"catalog_price_mode": "list_price_net"},
    ) is False


def test_lumbro_price_rows_keep_exact_automatic_codes_and_source_rows():
    assert engine.LUMBRO_PRICE_ROWS == {
        "MULT-LIDO-INT": 348,
        "LIDO.OP-INT": 380,
        "JUMP-1.5M": 396,
        "CAJA-FUS": 406,
    }


def test_lumbro_accessories_for_meeting_table_adds_jump_plus_one():
    item = QuoteItem(tipo="producto", row=14, nombre="Sala de juntas para 8 pax", cantidad=1)

    accessories = _lumbro_accessories_for_item(item, "Mesas de Juntas")

    assert accessories == [
        ("MULT-LIDO-INT", 2),
        ("JUMP-1.5M", 3),
    ]


def test_lumbro_accessories_for_workstation_without_pax_adds_default_multicontact():
    item = QuoteItem(tipo="producto", row=21, nombre="DU688-Lido Ejecutivo", cantidad=1)

    accessories = _lumbro_accessories_for_item(item, "Escritorios-WorkStation")

    assert accessories == [("MULT-LIDO-INT", 1)]


def test_lumbro_accessories_for_workstation_without_pax_respects_quantity():
    item = QuoteItem(tipo="producto", row=21, nombre="UP 1 IND Escritorio", cantidad=4)

    accessories = _lumbro_accessories_for_item(item, "Escritorios-WorkStation")

    assert accessories == [("MULT-LIDO-INT", 4)]


def test_load_lumbro_prices_preserves_source_row_reference():
    assert TEMPLATE.exists()

    prices = _load_lumbro_prices(TEMPLATE)

    assert prices["LIDO.OP-INT"].row == 380
    assert prices["LIDO.OP-INT"].price_mxn > 0


def test_automatic_lumbro_lines_attach_the_reference_image_to_future_quotes():
    item = QuoteItem(
        tipo="producto",
        row=9,
        nombre="DU688-Lido Ejecutivo",
        cantidad=1,
        precio=100,
    )

    lines, _ = engine._official_presentation_lines(
        (item,),
        {},
        TEMPLATE,
        _load_lumbro_prices(TEMPLATE),
        {},
    )
    accessory = next(line for line in lines if line.parent_item_key is not None)

    assert accessory.name == "MULT-LIDO-INT"
    assert accessory.image_content == engine.LUMBRO_ACCESSORY_IMAGE.read_bytes()
    assert accessory.image_content_type == "image/png"


def test_lauco_cub_hive_uses_the_reviewed_reference_when_catalog_has_no_image():
    item = QuoteItem(
        tipo="producto",
        row=9,
        nombre="CUB HIVE",
        descripcion="Cubiculo modular hexagonal tipo panal",
        proveedor="Lauco",
        cantidad=1,
        precio=100,
    )

    lines, _ = engine._official_presentation_lines(
        (item,),
        {},
        TEMPLATE,
        _load_lumbro_prices(TEMPLATE),
        {},
    )

    assert lines[0].image_content == engine.CUB_HIVE_REFERENCE_IMAGE.read_bytes()
    assert lines[0].image_content_type == "image/png"


def test_exchange_rate_prefers_explicit_metadata(monkeypatch):
    monkeypatch.setattr(engine, "_fetch_usd_mxn_exchange_rate", lambda: 17.25)

    assert engine._exchange_rate({"tipo_cambio": "20"}) == 20


def test_exchange_rate_fetches_live_rate_when_no_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "EXCHANGE_RATE_CACHE_PATH", tmp_path / "rate.json")
    monkeypatch.setattr(engine, "_fetch_usd_mxn_exchange_rate", lambda: 17.25)

    assert engine._exchange_rate({}) == 17.25
