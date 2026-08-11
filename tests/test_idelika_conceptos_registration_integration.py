from pathlib import Path

from mobiliti_saas.api import index as api
from mobiliti_saas.web.api import index as web_api
from mobiliti_saas.worker import quote_worker
from mobiliti_saas.worker.catalog_sync import load_source_config
from mobiliti_saas.worker.catalog_sync.service import _enabled_suppliers
from vercel_deploy.api import index as dev_api


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = PROJECT_ROOT / "mobiliti_saas" / "worker" / "catalog_sync" / "sources.json"
ALL_GENERIC_SUPPLIERS = "cr-global,sonara,sunon,alma,lumbro,jome,lauco,idelika,conceptos"


def test_source_registry_and_due_scheduler_accept_all_nine_suppliers(monkeypatch):
    sources = load_source_config(SOURCES_PATH)
    assert tuple(source.supplier for source in sources) == tuple(ALL_GENERIC_SUPPLIERS.split(","))

    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", ALL_GENERIC_SUPPLIERS)
    assert _enabled_suppliers() == tuple(ALL_GENERIC_SUPPLIERS.split(","))


def test_api_y_worker_habilitan_los_dos_catalogos_con_las_etiquetas_publicadas(monkeypatch):
    expected = ("idelika", "conceptos")
    labels = {"idelika": "IDÉLIKA", "conceptos": "Conceptos"}

    for runtime in (api, web_api, dev_api):
        enabled = runtime._parse_enabled_catalog_suppliers(ALL_GENERIC_SUPPLIERS)
        assert enabled == tuple(ALL_GENERIC_SUPPLIERS.split(","))
        monkeypatch.setattr(runtime, "CATALOG_ENABLED_SUPPLIERS", enabled)
        assert runtime._enabled_catalog_suppliers() == enabled
        assert [runtime._require_enabled_catalog_supplier(supplier) for supplier in expected] == list(expected)
        assert {
            supplier: runtime.CATALOG_SUPPLIER_LABELS[supplier]
            for supplier in expected
        } == labels

    assert {supplier: quote_worker.SUPPLIER_LABELS[supplier] for supplier in expected} == labels
