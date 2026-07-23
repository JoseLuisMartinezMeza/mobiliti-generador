import json

import pytest

from mobiliti_saas.quote_engine.catalog_search import search_catalog_products


def _supplier_item(catalog: str, name: str, code: str) -> dict:
    return {
        "internal_id": f"{catalog}:{code}",
        "sku": code,
        "name": name,
        "image_url": f"https://assets.example/{catalog}.png",
        "price_net": "100",
        "base_currency": "USD",
        "product_url": f"https://supplier.example/{catalog}",
        "source_reference": "https://source.example/private",
        "stock": 7,
        "warnings": ["Precio privado https://private.example"],
    }


def test_search_returns_canonical_references_without_commercial_or_source_fields():
    result = search_catalog_products(
        {"sunon": {"items": [_supplier_item("sunon", "Olíve II Chair", "OLIVE-II")] }},
        query="olÍve",
        supplier=None,
        offset=0,
        limit=20,
    )

    assert result["total"] == 1
    item = result["items"][0]
    assert item == {
        "catalog": "sunon",
        "official_code": "OLIVE-II",
        "identity": {
            "internal_id": "sunon:OLIVE-II",
            "base_option_id": "",
            "add_on_option_ids": [],
        },
        "snapshot": {
            "name": "Olíve II Chair",
            "code": "OLIVE-II",
            "image_url": "https://assets.example/sunon.png",
            "availability": "Disponible",
            "configuration": "",
            "warnings": [],
        },
    }
    serialized = repr(item)
    for forbidden in (
        "price_net", "base_currency", "product_url", "source_reference",
        "private.example", "Existencia: 7",
    ):
        assert forbidden not in serialized


def test_search_uses_all_seven_catalogs_and_stable_pagination():
    catalogs = {
        "lumbro": {"items": [_supplier_item("lumbro", "Silla Álfa", "L-2")]},
        "sunon": {"items": [_supplier_item("sunon", "Silla Alfa", "S-2")]},
        "alma": {"items": [_supplier_item("alma", "Silla Alfa", "A-2")]},
        "sonara": {"items": [_supplier_item("sonara", "Silla Alfa", "SO-2")]},
        "cr-global": {"items": [_supplier_item("cr-global", "Silla Alfa", "C-2")]},
        "offiho": {"items": [{
            "inventory_key": "offiho:O-2",
            "code": "O-2",
            "name": "Silla Alfa",
            "available_quantity": 0,
        }]},
        "tarkett": {"items": [{
            "code": "T-2",
            "name": "Silla Alfa",
            "available_quantity": 2,
        }]},
    }

    first = search_catalog_products(catalogs, query="SÍLLA", supplier=None, offset=0, limit=3)
    second = search_catalog_products(catalogs, query="silla", supplier=None, offset=3, limit=3)
    last = search_catalog_products(catalogs, query="silla", supplier=None, offset=6, limit=3)

    assert first["total"] == 7
    assert [item["catalog"] for item in first["items"]] == ["tarkett", "offiho", "cr-global"]
    assert [item["catalog"] for item in second["items"]] == ["sonara", "sunon", "alma"]
    assert [item["catalog"] for item in last["items"]] == ["lumbro"]
    assert first["next_offset"] == 3
    assert second["next_offset"] == 6
    assert last["next_offset"] is None
    assert first["items"][1]["snapshot"]["availability"] == "Agotado"
    assert first["items"][0]["snapshot"]["availability"] == "Disponible"


def test_search_omits_identity_that_fails_mixed_catalog_preflight():
    result = search_catalog_products(
        {"offiho": {"items": [{"code": "MISSING-INVENTORY", "name": "Silla"}]}},
        query="silla",
        supplier=None,
        offset=0,
        limit=20,
    )

    assert result == {"items": [], "total": 0, "next_offset": None}


def test_search_rejects_non_allowlisted_supplier():
    with pytest.raises(ValueError, match="Catalogo no permitido"):
        search_catalog_products({}, query="silla", supplier="cliente", offset=0, limit=20)


def test_search_sanitizes_metadata_with_closed_availability_and_warnings():
    malicious_text = "Existencia: 73 USD https://supplier.example/private"
    result = search_catalog_products(
        {"sunon": {"items": [
            {
                "internal_id": "sunon:out",
                "sku": "OUT",
                "name": "Silla agotada",
                "is_out_of_stock": True,
                "lead_time": malicious_text,
                "warnings": [malicious_text],
            },
            {
                "internal_id": "sunon:made",
                "sku": "MADE",
                "name": "Silla fabricación",
                "availability_type": "made_to_order",
                "lead_time": malicious_text,
                "warnings": [malicious_text],
            },
            {
                "internal_id": "sunon:available",
                "sku": "AVAILABLE",
                "name": "Silla disponible",
                "stock": 4,
                "lead_time": malicious_text,
                "warnings": [malicious_text],
            },
            {
                "internal_id": "sunon:unknown",
                "sku": "UNKNOWN",
                "name": "Silla confirmar",
                "lead_time": malicious_text,
                "warnings": [malicious_text],
            },
        ]}},
        query="silla",
        supplier=None,
        offset=0,
        limit=20,
    )

    snapshots = {item["official_code"]: item["snapshot"] for item in result["items"]}
    assert snapshots["OUT"]["availability"] == "Agotado"
    assert snapshots["MADE"]["availability"] == "Fabricación por confirmar"
    assert snapshots["AVAILABLE"]["availability"] == "Disponible"
    assert snapshots["UNKNOWN"]["availability"] == "Disponibilidad por confirmar"
    allowed_warnings = {
        "Producto agotado",
        "Fabricación por confirmar",
        "Disponibilidad por confirmar",
    }
    assert all(
        isinstance(snapshot["warnings"], list)
        and set(snapshot["warnings"]) <= allowed_warnings
        for snapshot in snapshots.values()
    )
    assert snapshots["OUT"]["warnings"] == ["Producto agotado"]
    assert snapshots["MADE"]["warnings"] == ["Fabricación por confirmar"]
    assert snapshots["AVAILABLE"]["warnings"] == []
    assert snapshots["UNKNOWN"]["warnings"] == ["Disponibilidad por confirmar"]
    serialized = json.dumps(result, ensure_ascii=False)
    for forbidden in (malicious_text, "https://", "USD", "Existencia: 73"):
        assert forbidden not in serialized
