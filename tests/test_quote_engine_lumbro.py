from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine import engine  # noqa: E402
from mobiliti_saas.quote_engine.engine import (  # noqa: E402
    _item_auto_electrification,
)
from mobiliti_saas.quote_engine.parser import QuoteItem  # noqa: E402


def test_legacy_electrification_metadata_retains_its_validation_contract():
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


@pytest.mark.parametrize(
    "name",
    ["DU688-Lido Ejecutivo", "Estacion Lido 8PAX", "Sala de juntas para 8 pax"],
)
@pytest.mark.parametrize("legacy_mixed_flag", [False, True])
def test_quote_presentation_keeps_only_requested_products(name, legacy_mixed_flag):
    item = QuoteItem(
        tipo="producto",
        row=9,
        nombre=name,
        cantidad=2,
        precio=100,
        proveedor="Offiho",
        modo_precio="list",
        descuento=40,
        moneda_original="MXN",
        precio_original=100,
        tipo_cambio_congelado=1,
        electrificacion_automatica=True,
    )
    metadata = {"tipo_cambio": 1}
    if legacy_mixed_flag:
        metadata.update(
            catalog_price_mode="mixed_catalog_converted",
            quote_currency="MXN",
            auto_electrification_rate={
                "base_currency": "MXN",
                "quote_currency": "MXN",
                "exchange_rate": "1.000000",
                "rate_source": "identity",
                "rate_effective_date": "2026-08-30",
                "rate_retrieved_at": "",
            },
        )
    lines, needs = engine._official_presentation_lines(
        (item,), metadata, {},
    )

    assert [line.name for line in lines] == [name]
    assert lines[0].quantity == 2
    assert lines[0].parent_item_key is None
    assert sum(section.item_count for section in needs) == 1


@pytest.mark.parametrize("code", ["MULT-LIDO-INT", "LIDO.OP-INT", "JUMP-1.5M", "CAJA-FUS"])
def test_quote_presentation_preserves_explicit_lumbro_products(code):
    item = QuoteItem(
        tipo="producto", row=9, nombre=code, cantidad=3, precio=120,
        descripcion="Accesorio elegido por el usuario",
    )

    lines, needs = engine._official_presentation_lines(
        (item,), {"catalog_supplier_label": "Lumbro CH", "tipo_cambio": 1},
        {9: (b"imagen-explicita", "image/png")},
    )

    assert [line.name for line in lines] == [code]
    assert lines[0].description == "Accesorio elegido por el usuario"
    assert lines[0].quantity == 3
    assert lines[0].converted_cost == 120
    assert lines[0].provider == "Lumbro CH"
    assert lines[0].parent_item_key is None
    assert lines[0].image_content == b"imagen-explicita"
    assert lines[0].image_content_type == "image/png"
    assert sum(section.item_count for section in needs) == 1


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
