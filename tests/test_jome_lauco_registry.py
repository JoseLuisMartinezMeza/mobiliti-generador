from pathlib import Path
import re

from mobiliti_saas.quote_engine.mixed_catalog import (
    MIXED_CATALOG_LABELS,
    MIXED_CATALOG_ORDER,
    MIXED_EXPECTED_BASE_CURRENCY,
    MIXED_GROUP_SOURCE_TYPES,
)
from mobiliti_saas.quote_engine.supplier_catalog import (
    ALLOWED_SUPPLIERS,
    EXPECTED_SUPPLIER_BASE_CURRENCY,
    SUPPLIER_LABELS,
)
from mobiliti_saas.quote_engine.quotation_sheets import official_provider_name
from mobiliti_saas.worker.catalog_sync import load_source_config


SOURCES_PATH = Path("mobiliti_saas/worker/catalog_sync/sources.json")


def test_supplier_contract_registers_jome_and_lauco_as_mxn():
    assert ALLOWED_SUPPLIERS == {
        "cr-global",
        "sonara",
        "sunon",
        "alma",
        "lumbro",
        "jome",
        "lauco",
        "idelika",
        "conceptos",
        "labenze",
        "requiez",
    }
    assert SUPPLIER_LABELS["jome"] == "JOME"
    assert SUPPLIER_LABELS["lauco"] == "Lauco"
    assert EXPECTED_SUPPLIER_BASE_CURRENCY["jome"] == "MXN"
    assert EXPECTED_SUPPLIER_BASE_CURRENCY["lauco"] == "MXN"


def test_mixed_catalog_contract_has_thirteen_visible_catalogs():
    assert MIXED_CATALOG_ORDER == (
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
        "labenze",
        "requiez",
    )
    for supplier, label in (("jome", "JOME"), ("lauco", "Lauco")):
        assert MIXED_CATALOG_LABELS[supplier] == label
        assert MIXED_GROUP_SOURCE_TYPES[supplier] == "supplier_cart"
        assert MIXED_EXPECTED_BASE_CURRENCY[supplier] == "MXN"


def test_jome_and_lauco_use_the_exact_official_mobiliti_provider_names():
    assert official_provider_name("JOME") == "Jome"
    assert official_provider_name("Lauco") == "Lauco Sofas"


def test_source_config_pins_all_three_official_documents():
    sources = load_source_config(SOURCES_PATH)
    assert [source.supplier for source in sources] == [
        "cr-global",
        "sonara",
        "sunon",
        "alma",
        "lumbro",
        "jome",
        "lauco",
        "idelika",
        "conceptos",
        "labenze",
        "requiez",
    ]
    by_supplier = {source.supplier: source for source in sources}
    assert {
        (file.path, file.kind, file.drive_item_id)
        for file in by_supplier["jome"].files
    } == {
        (
            "SPEC GUIDES 2026/JOME/Spec guide-Estructuras Jome-2026.xlsx",
            "spec_guide",
            "01DHXXN73FNX632SXL3JBZ5O6FNNULR67U",
        ),
        (
            "SPEC GUIDES 2026/JOME/Spec guide-Laminado-2026.xlsx",
            "spec_guide",
            "01DHXXN72IXFY22JUPD5GJT5B6PPGWE7ZX",
        ),
    }
    assert {file.brand for file in by_supplier["jome"].files} == {
        "estructuras",
        "laminado",
    }
    assert {
        (file.path, file.kind, file.drive_item_id)
        for file in by_supplier["lauco"].files
    } == {
        (
            "SPEC GUIDES 2026/LAUCO/Spec Guide Lauco-2026.xlsb",
            "spec_guide",
            "01DHXXN73QZOUEEWNH4BE2NO5YPBUJ5HNK",
        ),
    }
    assert by_supplier["lauco"].files[0].brand == "Lauco"


def test_frontend_exposes_jome_and_lauco_in_project_catalogs():
    mixed_cart = Path("mobiliti_saas/web/src/mixedCart.js").read_text(encoding="utf-8")
    product_picker = Path("mobiliti_saas/web/src/productPicker.js").read_text(
        encoding="utf-8"
    )
    main = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")
    admin = Path("mobiliti_saas/web/src/CatalogAdminPanel.jsx").read_text(
        encoding="utf-8"
    )
    drawer = Path("mobiliti_saas/web/src/MixedCartDrawer.jsx").read_text(
        encoding="utf-8"
    )
    for supplier, label in (
        ("lumbro", "Lumbro"),
        ("jome", "JOME"),
        ("lauco", "Lauco"),
    ):
        assert f'"{supplier}"' in mixed_cart
        assert f'value: "{supplier}", label: "{label}"' in product_picker
        assert re.search(rf'(?:"{supplier}"|{supplier}):\s*"{label}"', main)
        assert re.search(rf'(?:"{supplier}"|{supplier}):\s*"{label}"', drawer)
        assert f'["{supplier}", "{label}"]' in admin
