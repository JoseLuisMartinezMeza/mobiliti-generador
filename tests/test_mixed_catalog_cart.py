from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.mixed_catalog import (
    MIXED_CATALOG_ORDER,
    build_mixed_catalog_cart_payload,
    build_mixed_reservation_groups,
    mixed_cart_key,
    preflight_mixed_catalog_items,
    validate_mixed_catalog_payload,
)
from mobiliti_saas.quote_engine.offiho_catalog import OffihoCatalogItem
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem


def _supplier_item(catalog, *, internal_id, price="100.000000", stock="5.000000", availability="stocked"):
    return {
        "internal_id": internal_id, "supplier": catalog, "product_key": internal_id,
        "sku": internal_id.upper(), "code_status": "verified", "brand": catalog,
        "collection": catalog, "name": f"Producto {catalog}", "description": "",
        "unit": "pieza", "availability_type": availability,
        "stock": stock if availability == "stocked" else None,
        "lead_time": "Sobre pedido" if availability != "stocked" else "",
        "base_price_options": [], "add_on_options": [],
        "base_currency": "USD" if catalog in {"sunon", "alma"} else "MXN",
        "price_net": price, "tax_rate": "0.160000", "attributes": {},
        "image_url": "", "image_kind": "placeholder", "product_url": "",
        "warnings": [], "source_reference": f"{catalog}:source",
    }


@pytest.fixture
def mixed_catalogs():
    tarkett = TarkettCatalogItem("25731726", "Piso Tarkett", "m2", Decimal("10"), unit_price=Decimal("100"), price_source="catalog")
    offiho = OffihoCatalogItem("offiho:desk-1", "OHE-1", "Escritorio", "Negro", "PZA", Decimal("1"), Decimal("8"), Decimal("200"), price_source="catalog")
    catalogs = {
        "tarkett": {"source_hash": "a" * 64, "items": [tarkett], "by_code": {tarkett.code: tarkett}},
        "offiho": {"source_hash": "b" * 64, "items": [offiho], "by_inventory_key": {offiho.inventory_key: offiho}},
    }
    for index, catalog in enumerate(MIXED_CATALOG_ORDER[2:], start=2):
        item = _supplier_item(catalog, internal_id=f"{catalog}:desk-1")
        catalogs[catalog] = {
            "supplier": catalog, "source_hash": f"{index:x}" * 64,
            "generated_at": "2026-07-19T00:00:00+00:00", "items": [item],
        }
    return catalogs


@pytest.fixture
def rate_rows():
    return [
        {"currency": "USD", "effective_date": "2026-07-19", "mxn_per_unit": "18.500000", "retrieved_at": "2026-07-19T12:00:00+00:00"},
        {"currency": "EUR", "effective_date": "2026-07-19", "mxn_per_unit": "20.500000", "retrieved_at": "2026-07-19T12:00:00+00:00"},
    ]


def browser_rows_for_all_catalogs():
    return [
        {"catalog": "tarkett", "code": "25731726", "quantity": "1"},
        {"catalog": "offiho", "inventory_key": "offiho:desk-1", "quantity": "1"},
        *[
            {"catalog": catalog, "internal_id": f"{catalog}:desk-1", "quantity": "1"}
            for catalog in MIXED_CATALOG_ORDER[2:]
        ],
    ]


def test_mixed_cart_groups_seven_catalogs_in_canonical_order(mixed_catalogs, rate_rows):
    payload = build_mixed_catalog_cart_payload(browser_rows_for_all_catalogs(), catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert payload["source_type"] == "mixed_catalog_cart"
    assert [group["catalog"] for group in payload["groups"]] == list(MIXED_CATALOG_ORDER)
    assert sum(len(group["items"]) for group in payload["groups"]) == 7


@pytest.mark.parametrize("field", ("unit_price", "base_currency", "exchange_rate", "stock", "image_url", "product_url", "supplier", "warnings"))
def test_mixed_cart_rejects_browser_owned_commercial_fields(mixed_catalogs, rate_rows, field):
    row = {"catalog": "tarkett", "code": "25731726", "quantity": "1", field: "tampered"}
    with pytest.raises(ValueError, match="Campo mixto no permitido"):
        build_mixed_catalog_cart_payload([row], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))


def test_mixed_cart_distinguishes_supplier_configurations(mixed_catalogs, rate_rows):
    first = {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "base_option_id": "base-a", "add_on_option_ids": ["addon-a"]}
    second = {**first, "add_on_option_ids": ["addon-b"]}
    item = mixed_catalogs["alma"]["items"][0]
    item["base_price_options"] = [
        {"id": "base-a", "name": "Base A", "price_net": "100.000000", "available": True},
        {"id": "base-b", "name": "Base B", "price_net": "100.000000", "available": True},
    ]
    item["add_on_options"] = [
        {"id": "addon-a", "name": "A", "family": "a", "price_net": "1.000000", "available": True},
        {"id": "addon-b", "name": "B", "family": "b", "price_net": "2.000000", "available": True},
    ]
    payload = build_mixed_catalog_cart_payload([first, second], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert [item["canonical_key"] for item in payload["groups"][0]["items"]] == [
        'alma:["alma:desk-1","base-a",["addon-a"]]', 'alma:["alma:desk-1","base-a",["addon-b"]]',
    ]


@pytest.mark.parametrize("row", [
    {"catalog": "tarkett", "quantity": "1"}, {"catalog": "offiho", "quantity": "1"},
    {"catalog": "alma", "quantity": "1"}, {"catalog": "alma", "internal_id": "alma:desk-1"},
])
def test_mixed_cart_requires_canonical_identity_fields(row):
    with pytest.raises(ValueError, match="Campo mixto requerido"):
        preflight_mixed_catalog_items([row])


def test_mixed_cart_normalizes_missing_supplier_configuration():
    normalized = preflight_mixed_catalog_items([{"catalog": "sonara", "internal_id": "sonara:desk-1", "quantity": "1"}])[0]
    assert normalized["base_option_id"] == ""
    assert normalized["add_on_option_ids"] == []


def test_mixed_cart_uses_json_tuple_keys_to_avoid_delimiter_collisions():
    left = mixed_cart_key({"catalog": "alma", "internal_id": "a|b", "base_option_id": "c", "add_on_option_ids": []})
    right = mixed_cart_key({"catalog": "alma", "internal_id": "a", "base_option_id": "b|c", "add_on_option_ids": []})
    assert left != right


def test_mixed_reservations_aggregate_configurations(mixed_catalogs, rate_rows):
    item = mixed_catalogs["alma"]["items"][0]
    item["base_price_options"] = [
        {"id": "base-a", "name": "Base A", "price_net": "100.000000", "available": True},
        {"id": "base-b", "name": "Base B", "price_net": "100.000000", "available": True},
    ]
    payload = build_mixed_catalog_cart_payload([
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "1", "base_option_id": "base-a", "add_on_option_ids": []},
        {"catalog": "alma", "internal_id": "alma:desk-1", "quantity": "2", "base_option_id": "base-b", "add_on_option_ids": []},
    ], catalogs=mixed_catalogs, rate_rows=rate_rows, quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19))
    assert len(payload["groups"][0]["items"]) == 2
    assert build_mixed_reservation_groups(payload) == [{"catalog": "alma", "items": [{"identity": "alma:desk-1", "sku": "ALMA:DESK-1", "quantity": "3.000000", "stock": "5.000000"}]}]


def test_mixed_catalog_module_copies_are_byte_identical():
    paths = [Path("mobiliti_saas/quote_engine/mixed_catalog.py"), Path("mobiliti_saas/web/mobiliti_saas/quote_engine/mixed_catalog.py")]
    assert len({hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}) == 1


@pytest.fixture
def frozen_mixed_payload(mixed_catalogs, rate_rows):
    return build_mixed_catalog_cart_payload(
        browser_rows_for_all_catalogs(), catalogs=mixed_catalogs, rate_rows=rate_rows,
        quote_currency="MXN", commercial_discount_percent="40", today=date(2026, 7, 19),
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(item_count=999), "Conteo mixto inconsistente"),
        (lambda payload: payload.update(rate_summary=[]), "Resumen de tasas mixtas inconsistente"),
        (lambda payload: payload["groups"].reverse(), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0].update(catalog_source_hash="A" * 64), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0].update(rate_retrieved_at="x"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0]["items"][0].update(supplier="spoof"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][2]["items"][0].update(price_mode="list"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][2]["items"][0].update(auto_electrification="true"), "Grupos mixtos invalidos"),
        (lambda payload: payload["groups"][0]["items"][0].update(product_url="http://sonara.mx/panel"), "Grupos mixtos invalidos"),
    ],
)
def test_mixed_payload_rejects_tampering(frozen_mixed_payload, mutate, message):
    payload = deepcopy(frozen_mixed_payload)
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        validate_mixed_catalog_payload(payload)


def test_mixed_payload_keeps_commercial_product_url_inert(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    line = payload["groups"][2]["items"][0]
    line["product_url"] = "https://sonara.mx/producto/panel"
    assert validate_mixed_catalog_payload(payload) is payload
    line["image_url"] = "https://sonara.mx/producto/panel"
    assert validate_mixed_catalog_payload(payload) is payload


def test_mixed_payload_identity_rate_requires_empty_retrieval_timestamp(frozen_mixed_payload):
    payload = deepcopy(frozen_mixed_payload)
    for group in payload["groups"]:
        if group["base_currency"] == "MXN":
            group.update(exchange_rate="1.000000", rate_source="identity", rate_retrieved_at="")
            for line in group["items"]:
                line.update(frozen_exchange_rate="1.000000")
    payload["rate_summary"] = [{key: group[key] for key in ("catalog", "base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at")} for group in payload["groups"]]
    payload["auto_electrification_rate"] = {key: payload["groups"][0][key] for key in ("base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at")}
    assert validate_mixed_catalog_payload(payload) is payload
    payload["groups"][0]["rate_retrieved_at"] = "2026-07-19T12:00:00+00:00"
    with pytest.raises(ValueError, match="Grupos mixtos invalidos"):
        validate_mixed_catalog_payload(payload)


@pytest.mark.parametrize("field", ("base_currency", "quote_currency", "exchange_rate", "rate_source", "rate_effective_date", "rate_retrieved_at"))
def test_mixed_payload_rejects_each_tampered_auto_electrification_rate(frozen_mixed_payload, field):
    payload = deepcopy(frozen_mixed_payload)
    payload["auto_electrification_rate"][field] = "tampered"
    with pytest.raises(ValueError, match="Tasa de electrificacion mixta invalida"):
        validate_mixed_catalog_payload(payload)
