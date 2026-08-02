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
        "attributes": {"dimensions": "600 x 600 mm"},
        "warnings": ["Precio privado https://private.example"],
    }


def test_search_returns_safe_display_price_and_dimensions_without_private_source_fields():
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
        "price_net": "100",
        "base_currency": "USD",
        "identity": {
            "internal_id": "sunon:OLIVE-II",
            "base_option_id": "",
            "add_on_option_ids": [],
        },
        "snapshot": {
            "name": "Olíve II Chair",
            "code": "OLIVE-II",
            "image_url": "https://assets.example/sunon.png",
            "dimensions": "600 x 600 mm",
            "availability": "Disponible",
            "configuration": "",
            "warnings": [],
        },
    }
    serialized = repr(item)
    for forbidden in (
        "product_url", "source_reference",
        "private.example", "Existencia: 7",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("catalog", "identity", "code", "unit_price"),
    [
        ("tarkett", {"code": "T-LEGACY"}, "T-LEGACY", "472.63"),
        (
            "offiho",
            {"inventory_key": "offiho:legacy", "code": "O-LEGACY"},
            "O-LEGACY",
            "6199.00",
        ),
    ],
)
def test_search_maps_legacy_unit_price_and_expected_currency(catalog, identity, code, unit_price):
    raw = {
        **identity,
        "name": f"Producto {catalog}",
        "unit_price": unit_price,
        "available_quantity": 1,
    }

    item = search_catalog_products(
        {catalog: {"items": [raw]}},
        query="producto",
        supplier=catalog,
        offset=0,
        limit=20,
    )["items"][0]

    assert item["official_code"] == code
    assert item["price_net"] == unit_price
    assert item["base_currency"] == "MXN"


def test_search_exposes_safe_base_choice_prices_and_prefills_only_a_single_choice():
    single = _supplier_item("lauco", "Silla única", "L-1")
    single["base_price_options"] = [
        {"id": "lauco:l-1:grade-1", "name": "Tapiz Grado 1", "price_net": "11780", "available": True},
        {"id": "lauco:l-1:unavailable", "name": "No disponible", "price_net": "14990", "available": False},
    ]
    multiple = _supplier_item("lauco", "Silla múltiple", "L-2")
    multiple["base_price_options"] = [
        {"id": "lauco:l-2:grade-1", "name": "Tapiz Grado 1", "price_net": "15350", "available": True},
        {"id": "lauco:l-2:grade-2", "name": "Tapiz Grado 2", "price_net": "19630", "available": True},
    ]

    result = search_catalog_products(
        {"lauco": {"items": [single, multiple]}},
        query="silla",
        supplier="lauco",
        offset=0,
        limit=20,
    )

    by_code = {item["official_code"]: item for item in result["items"]}
    assert by_code["L-1"]["base_options"] == [
        {"id": "lauco:l-1:grade-1", "name": "Tapiz Grado 1", "price_net": "11780"},
    ]
    assert by_code["L-1"]["identity"]["base_option_id"] == "lauco:l-1:grade-1"
    assert by_code["L-1"]["snapshot"]["configuration"] == "Tapiz Grado 1"
    assert by_code["L-2"]["base_options"] == [
        {"id": "lauco:l-2:grade-1", "name": "Tapiz Grado 1", "price_net": "15350"},
        {"id": "lauco:l-2:grade-2", "name": "Tapiz Grado 2", "price_net": "19630"},
    ]
    assert by_code["L-2"]["identity"]["base_option_id"] == ""
    assert by_code["L-2"]["snapshot"]["configuration"] == ""
    serialized = json.dumps(result, ensure_ascii=False)
    assert "11780" in serialized
    assert "14990" not in serialized
    assert "15350" in serialized
    assert "19630" in serialized


def test_search_exposes_available_add_on_choices_with_safe_prices():
    configurable = _supplier_item("alma", "Silla configurable", "A-1")
    configurable["base_price_options"] = [
        {"id": "base-aluminio", "name": "Aluminio", "price_net": "250", "available": True},
        {"id": "base-madera", "name": "Madera", "price_net": "300", "available": True},
    ]
    configurable["add_on_options"] = [
        {
            "id": "cojin-a",
            "name": "Cojín A",
            "family": "cojin",
            "price_net": "35.10",
            "available": True,
            "compatible_base_option_ids": ["base-aluminio"],
        },
        {
            "id": "cojin-b",
            "name": "Cojín B",
            "family": "cojin",
            "price_net": "50",
            "available": True,
        },
        {
            "id": "no-disponible",
            "name": "No disponible",
            "family": "cojin",
            "price_net": "99",
            "available": False,
        },
    ]

    result = search_catalog_products(
        {"alma": {"items": [configurable]}},
        query="configurable",
        supplier="alma",
        offset=0,
        limit=20,
    )

    item = result["items"][0]
    assert item["add_on_options"] == [
        {
            "id": "cojin-a",
            "name": "Cojín A",
            "family": "cojin",
            "price_net": "35.10",
            "compatible_base_option_ids": ["base-aluminio"],
        },
        {
            "id": "cojin-b",
            "name": "Cojín B",
            "family": "cojin",
            "price_net": "50",
            "compatible_base_option_ids": [],
        },
    ]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "35.10" in serialized
    assert '"50"' in serialized
    assert "99" not in serialized


def test_search_omits_invalid_prices_and_currency_instead_of_leaking_bad_values():
    invalid = _supplier_item("sunon", "Precio invalido", "BAD-1")
    invalid["price_net"] = "NaN"
    invalid["base_currency"] = "USD<script>"
    invalid["base_price_options"] = [
        {"id": "zero", "name": "Cero", "price_net": "0", "available": True},
        {"id": "negative", "name": "Negativo", "price_net": "-10", "available": True},
    ]

    item = search_catalog_products(
        {"sunon": {"items": [invalid]}},
        query="invalido",
        supplier="sunon",
        offset=0,
        limit=20,
    )["items"][0]

    assert "price_net" not in item
    assert "base_currency" not in item
    assert item["base_options"] == [
        {"id": "zero", "name": "Cero"},
        {"id": "negative", "name": "Negativo"},
    ]


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
