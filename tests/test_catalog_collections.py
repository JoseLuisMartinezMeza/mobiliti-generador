from collections import Counter

import pytest

from mobiliti_saas.quote_engine.catalog_collections import resolve_catalog_collection
from mobiliti_saas.quote_engine.offiho_catalog import load_offiho_catalog
from mobiliti_saas.quote_engine.tarkett_catalog import load_tarkett_catalog


@pytest.mark.parametrize(
    ("supplier", "item", "expected"),
    [
        (
            "tarkett",
            {"name": "Piso Ambienta Series Canela 208x1230mm"},
            "Ambienta Series",
        ),
        (
            "tarkett",
            {"name": "Desso Grezzo Bloom AD04 9096-V B8 50x50"},
            "Desso Grezzo Bloom",
        ),
        (
            "tarkett",
            {"name": "Ultrabond Eco 4 LVT bucket 14 kg"},
            "Adhesivos",
        ),
        (
            "offiho",
            {"product_url": "https://www.offihoblack.com/producto/glove"},
            "Offiho Black",
        ),
        (
            "offiho",
            {
                "product_url": (
                    "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
                    "lp-black-colos-jul2026.pdf#page=16"
                )
            },
            "Colos",
        ),
        (
            "offiho",
            {
                "product_url": (
                    "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
                    "lp-offiho-econo-sillas-jul2026.pdf#page=16"
                )
            },
            "Econosillas",
        ),
        (
            "offiho",
            {"product_url": "https://www.offiho.com/ejecutivos/alufsen"},
            "Offiho",
        ),
        (
            "cr-global",
            {"collection": "Estructuras Elevables", "name": "Base eléctrica"},
            "Estructuras Elevables",
        ),
        (
            "sonara",
            {"name": "SONARA SHAPES CORTES .60 m."},
            "Sonara Shapes",
        ),
        (
            "sonara",
            {"name": "Herraje de aluminio - Canal U de 2.40mts"},
            "Herrajes y perfiles",
        ),
        (
            "sunon",
            {"name": "Aulenti Task Chair"},
            "Sillas operativas",
        ),
        (
            "sunon",
            {"name": "Manager Desk"},
            "Escritorios",
        ),
        (
            "sunon",
            {"name": "Flower 6 Lounge Modular Seating"},
            "Salas y lounge",
        ),
        (
            "sunon",
            {"name": "UC Lounge Chair"},
            "Salas y lounge",
        ),
        (
            "sunon",
            {"name": "Olive II Chair"},
            "Sillas",
        ),
        (
            "sunon",
            {"name": "3 Drawer Lateral File"},
            "Almacenamiento",
        ),
        (
            "sunon",
            {"name": "Worksurface"},
            "Escritorios",
        ),
        (
            "sunon",
            {"name": "mounted socket[America]"},
            "Accesorios",
        ),
        (
            "lumbro",
            {"collection": "Empotrables", "name": "Barcelona"},
            "Empotrables",
        ),
        (
            "lumbro",
            {"name": "JUMP-1.5M", "description": "Cable de interconexión"},
            "Interconexión",
        ),
        (
            "lumbro",
            {"name": "Barcelona Carga", "description": "Multicontacto Barcelona"},
            "Multicontactos",
        ),
        (
            "lauco",
            {"collection": "Sofas", "name": "RED 2P"},
            "RED",
        ),
        (
            "conceptos",
            {"collection": "Sofas", "name": "Sofá aspetta de 3 plazas"},
            "Aspetta",
        ),
        (
            "conceptos",
            {"collection": "Sofas", "name": "Ottoman curvo conector modular"},
            "Modulares",
        ),
        (
            "alma",
            {"collection": "VATICAN", "name": "Vatican lounge armchair"},
            "VATICAN",
        ),
        (
            "jome",
            {"collection": "Archiveros Fijos", "name": "Archivero 2 gavetas"},
            "Archiveros Fijos",
        ),
    ],
)
def test_catalog_collection_uses_official_family_or_shared_product_characteristic(
    supplier, item, expected
):
    assert resolve_catalog_collection(supplier, item) == expected


def test_catalog_collection_never_leaves_an_allowed_supplier_without_a_group():
    suppliers = (
        "tarkett",
        "offiho",
        "cr-global",
        "sonara",
        "sunon",
        "alma",
        "lumbro",
        "jome",
        "lauco",
        "idelika",
        "conceptos",
    )

    assert {
        supplier: resolve_catalog_collection(supplier, {"name": "Producto sin familia"})
        for supplier in suppliers
    } == {
        "tarkett": "Otros Tarkett",
        "offiho": "Offiho",
        "cr-global": "Otros",
        "sonara": "Otros Sonara",
        "sunon": "Otros Sunon",
        "alma": "Otros",
        "lumbro": "Multicontactos",
        "jome": "Otros",
        "lauco": "Producto",
        "idelika": "Otros",
        "conceptos": "Otros Conceptos",
    }


def test_checked_in_offiho_catalog_exposes_the_four_verified_collections():
    catalog = load_offiho_catalog()

    assert Counter(
        item.to_public_dict()["collection"] for item in catalog["items"]
    ) == {
        "Offiho": 743,
        "Colos": 379,
        "Econosillas": 92,
        "Offiho Black": 74,
    }


def test_checked_in_tarkett_catalog_assigns_a_meaningful_collection_to_every_item():
    catalog = load_tarkett_catalog()
    collections = [item.to_public_dict()["collection"] for item in catalog["items"]]

    assert len(collections) == 125
    assert "Otros Tarkett" not in collections
    assert {"Ambienta Series", "Aurea Tech", "Desso Grezzo", "Injoy"}.issubset(
        collections
    )
