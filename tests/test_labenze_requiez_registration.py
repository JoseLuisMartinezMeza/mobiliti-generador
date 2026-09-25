import json
import re
from pathlib import Path

import pytest
from fastapi import Response

from mobiliti_saas.api import index as api
from mobiliti_saas.quote_engine.engine import (
    MIXED_CATALOG_BASE_CURRENCIES as ENGINE_CURRENCIES,
    MIXED_CATALOG_LABELS as ENGINE_LABELS,
    MIXED_CATALOG_ORDER as ENGINE_ORDER,
)
from mobiliti_saas.quote_engine.mixed_catalog import (
    MIXED_CATALOG_LABELS,
    MIXED_CATALOG_ORDER,
    MIXED_EXPECTED_BASE_CURRENCY,
)
from mobiliti_saas.quote_engine.supplier_catalog import (
    ALLOWED_SUPPLIERS,
    EXPECTED_SUPPLIER_BASE_CURRENCY,
    SUPPLIER_LABELS,
)
from mobiliti_saas.web.api import index as web_api
from mobiliti_saas.worker import quote_worker
from mobiliti_saas.worker.catalog_sync import load_source_config
from mobiliti_saas.worker.catalog_sync.repository import _SYNC_SUPPLIERS
from mobiliti_saas.worker.catalog_sync.service import ADAPTERS, _SUPPLIERS
from scripts import promote_validated_catalogs
from vercel_deploy.api import index as deploy_api


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "mobiliti_saas" / "worker" / "catalog_sync" / "sources.json"
BOOTSTRAP = ROOT / "mobiliti_saas" / "supabase_setup" / "create_tables.sql"
MIGRATION = (
    ROOT / "mobiliti_saas" / "supabase_setup"
    / "2026_08_labenze_requiez_catalogs.sql"
)
GENERIC_SUPPLIERS = (
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
    "idelika", "conceptos", "labenze", "requiez",
)
MIXED_SUPPLIERS = ("tarkett", "offiho", *GENERIC_SUPPLIERS)
NEW_LABELS = {"labenze": "Labenze", "requiez": "Requiez"}
EXPECTED_FILES = {
    "labenze": {
        "path": "LABENZE/LP Labenze B26.pdf",
        "kind": "price_list",
        "drive_item_id": "01DHXXN77SAPUFK56QHVBLKXH7BBV7DOL7",
        "mime_type": "application/pdf",
        "sha256": "c4fc2d2152b5e854f7c36c9106c71cd21853abb50efcde96ba2566cb72f1d6f3",
    },
    "requiez": {
        "path": "REQUIEZ/Lista de precios A-26.pdf",
        "kind": "price_list",
        "drive_item_id": "01DHXXN74NDZ6P4EL3B5CI2G2HFZ47ISNT",
        "mime_type": "application/pdf",
        "sha256": "7f3281d1965c67a234bac55112800067019ad471f835de59ff758e759eca56ba",
    },
}


def test_sharepoint_sources_are_exactly_pinned_by_path_graph_id_mime_and_hash(tmp_path):
    sources = load_source_config(SOURCES)
    assert tuple(source.supplier for source in sources) == GENERIC_SUPPLIERS

    by_supplier = {source.supplier: source for source in sources}
    for supplier, expected in EXPECTED_FILES.items():
        assert by_supplier[supplier].adapter == supplier
        assert len(by_supplier[supplier].files) == 1
        source_file = by_supplier[supplier].files[0]
        assert {
            "path": source_file.path,
            "kind": source_file.kind,
            "drive_item_id": source_file.drive_item_id,
            "mime_type": source_file.mime_type,
            "sha256": source_file.sha256,
        } == expected

    tampered = json.loads(SOURCES.read_text(encoding="utf-8"))
    tampered[-1]["files"][0]["sha256"] = "0" * 64
    tampered_path = tmp_path / "tampered-sources.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError):
        load_source_config(tampered_path)

def test_all_runtime_registries_append_eleven_generic_and_thirteen_mixed_catalogs():
    assert _SUPPLIERS == GENERIC_SUPPLIERS
    assert _SYNC_SUPPLIERS == set(GENERIC_SUPPLIERS)
    assert ALLOWED_SUPPLIERS == set(GENERIC_SUPPLIERS)
    assert MIXED_CATALOG_ORDER == MIXED_SUPPLIERS
    assert ENGINE_ORDER == MIXED_SUPPLIERS
    assert ADAPTERS["labenze"].__name__ == "build_labenze_snapshot_with_assets"
    assert ADAPTERS["requiez"].__name__ == "build_requiez_snapshot_with_assets"
    assert promote_validated_catalogs.SUPPLIERS == GENERIC_SUPPLIERS

    for supplier, label in NEW_LABELS.items():
        assert SUPPLIER_LABELS[supplier] == label
        assert MIXED_CATALOG_LABELS[supplier] == label
        assert ENGINE_LABELS[supplier] == label
        assert quote_worker.SUPPLIER_LABELS[supplier] == label
        assert EXPECTED_SUPPLIER_BASE_CURRENCY[supplier] == "MXN"
        assert MIXED_EXPECTED_BASE_CURRENCY[supplier] == "MXN"
        assert ENGINE_CURRENCIES[supplier] == "MXN"


@pytest.mark.parametrize("runtime", (api, web_api, deploy_api))
def test_api_mirrors_register_labels_and_enforce_a_512_kib_page(runtime):
    assert runtime.CATALOG_SUPPLIER_ORDER == GENERIC_SUPPLIERS
    assert {
        supplier: runtime.CATALOG_SUPPLIER_LABELS[supplier]
        for supplier in NEW_LABELS
    } == NEW_LABELS
    assert runtime.CATALOG_PAGE_MAX_ITEMS == 50
    assert runtime.MAX_CATALOG_PAGE_BYTES == 512 * 1024

    small = {"items": [{"name": "Silla"}], "total": 1}
    assert runtime._ensure_catalog_page_size(small) is small
    with pytest.raises(RuntimeError, match="excede"):
        runtime._ensure_catalog_page_size(
            {"items": [{"description": "x" * runtime.MAX_CATALOG_PAGE_BYTES}]}
        )


@pytest.mark.parametrize("runtime", (api, web_api, deploy_api))
def test_api_mirrors_page_supplier_variants_server_side(runtime, monkeypatch):
    items = []
    for index in range(60):
        code = f"LAB-{index:03d}"
        items.append({
            "internal_id": f"labenze:{code.lower()}",
            "supplier": "labenze",
            "product_key": f"labenze:{code.lower()}",
            "sku": code,
            "code_status": "verified",
            "brand": "Labenze",
            "collection": "Sillas" if index < 30 else "Mesas",
            "name": f"Producto Labenze {index}",
            "description": "Producto oficial sobre pedido.",
            "unit": "PZA",
            "availability_type": "made_to_order",
            "stock": None,
            "lead_time": "Por confirmar",
            "base_price_options": [],
            "add_on_options": [],
            "base_currency": "MXN",
            "price_net": "1000.000000",
            "tax_rate": "0.160000",
            "attributes": {"quotable": True},
            "image_url": f"https://labenze.com/img/{code}.png",
            "image_kind": "official",
            "product_url": f"https://labenze.com/producto/{code}",
            "warnings": [],
            "source_reference": f"sharepoint:labenze:page:{index + 1}",
        })
    snapshot = {
        "supplier": "labenze",
        "source_hash": "c" * 64,
        "generated_at": "2026-08-18T00:00:00+00:00",
        "items": items,
    }
    monkeypatch.setattr(runtime, "_load_supplier_catalog_cached", lambda _supplier: snapshot)
    monkeypatch.setattr(runtime, "db_catalog_reservation_summary", lambda *_args: [])

    first = runtime._supplier_catalog_response("labenze", 1, offset=0, limit=24)
    second = runtime._supplier_catalog_response("labenze", 1, offset=24, limit=24)
    filtered = runtime._supplier_catalog_response(
        "labenze", 1, query="producto labenze 59", offset=0, limit=24
    )

    assert len(first["items"]) == 24
    assert first["product_total"] == 60
    assert first["next_offset"] == 24
    assert second["offset"] == 24
    assert second["next_offset"] == 48
    assert [item["sku"] for item in filtered["items"]] == ["LAB-059"]
    assert len(json.dumps(first, ensure_ascii=False).encode("utf-8")) <= 512 * 1024


@pytest.mark.parametrize("runtime", (api, web_api, deploy_api))
def test_catalog_search_only_loads_enabled_generic_suppliers(runtime, monkeypatch):
    generic_calls = []
    monkeypatch.setattr(runtime, "CATALOG_ENABLED_SUPPLIERS", ("labenze",))
    monkeypatch.setattr(
        runtime,
        "_tarkett_catalog_response",
        lambda _user_id: {"supplier": "tarkett", "items": []},
    )
    monkeypatch.setattr(
        runtime,
        "_offiho_catalog_response",
        lambda _user_id: {"supplier": "offiho", "items": []},
    )
    monkeypatch.setattr(
        runtime,
        "_load_supplier_catalog_cached",
        lambda supplier: generic_calls.append(supplier)
        or {"supplier": supplier, "items": []},
    )

    snapshots = runtime._catalog_search_snapshots(7, None)

    assert tuple(snapshots) == ("tarkett", "offiho", "labenze")
    assert generic_calls == ["labenze"]
    assert runtime._catalog_search_snapshots(7, "labenze")["labenze"]["supplier"] == "labenze"
    with pytest.raises(runtime.HTTPException) as disabled:
        runtime._catalog_search_snapshots(7, "requiez")
    assert disabled.value.status_code == 404


@pytest.mark.parametrize("runtime", (api, web_api, deploy_api))
def test_catalog_search_page_shrinks_below_512_kib_and_advances_exactly(runtime):
    large_items = [
        {
            "display_key": f"labenze:{index}",
            "base_options": [
                {
                    "id": f"option-{option}-" + "x" * 470,
                    "name": "Configuracion " + "n" * 480,
                    "price_net": "1000.000000",
                }
                for option in range(200)
            ],
        }
        for index in range(50)
    ]
    unbounded = {
        "items": large_items,
        "total": len(large_items),
        "next_offset": None,
    }
    assert len(json.dumps(unbounded, ensure_ascii=False).encode("utf-8")) > 4_500_000

    bounded = runtime._bounded_catalog_search_response(unbounded, offset=0)

    encoded = json.dumps(
        bounded, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    assert len(encoded) <= runtime.MAX_CATALOG_PAGE_BYTES
    assert 0 < len(bounded["items"]) < len(large_items)
    assert bounded["items"] == large_items[: len(bounded["items"])]
    assert bounded["next_offset"] == len(bounded["items"])

    one_too_large = {
        "items": [{"description": "x" * runtime.MAX_CATALOG_PAGE_BYTES}],
        "total": 1,
        "next_offset": None,
    }
    with pytest.raises(RuntimeError, match="excede"):
        runtime._bounded_catalog_search_response(one_too_large, offset=0)


@pytest.mark.parametrize("runtime", (api, web_api, deploy_api))
def test_catalog_search_endpoint_applies_bounded_response_helper(runtime, monkeypatch):
    raw = {"items": [{"display_key": "labenze:uno"}], "total": 1, "next_offset": None}
    bounded = {**raw, "bounded": True}
    seen = []
    monkeypatch.setattr(runtime, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(runtime, "_catalog_search_snapshots", lambda *_args: {})
    monkeypatch.setattr(runtime, "search_catalog_products", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        runtime,
        "_bounded_catalog_search_response",
        lambda payload, *, offset: seen.append((payload, offset)) or bounded,
    )

    response = runtime.catalog_search(
        response=Response(),
        q="",
        supplier="labenze",
        collection=None,
        offset="17",
        limit="20",
        current_user={"id": 7},
    )

    assert response is bounded
    assert seen == [(raw, 17)]


def test_supplier_view_uses_bounded_server_pagination_without_session_storage_snapshot():
    component = (ROOT / "mobiliti_saas" / "web" / "src" / "SupplierCatalogView.jsx").read_text(
        encoding="utf-8"
    )
    main = (ROOT / "mobiliti_saas" / "web" / "src" / "main.jsx").read_text(encoding="utf-8")
    picker = (ROOT / "mobiliti_saas" / "web" / "src" / "productPicker.js").read_text(
        encoding="utf-8"
    )

    assert "sessionStorage" not in component
    assert re.search(r"const\s+SUPPLIER_PAGE_SIZE\s*=\s*24\s*;", component)
    assert "offset: String((page - 1) * SUPPLIER_PAGE_SIZE)" in component
    assert "limit: String(SUPPLIER_PAGE_SIZE)" in component
    assert "request(`/catalogs/${supplier}?${params.toString()}`)" in component
    assert "next_offset" in component

    assert main.index('["conceptos", "Conceptos"') < main.index('["labenze", "Labenze"')
    assert main.index('["labenze", "Labenze"') < main.index('["requiez", "Requiez"')
    options = re.findall(
        r'\{value:\s*"([^"]+)",\s*label:\s*"([^"]+)"\}', picker
    )
    assert options[-2:] == [("labenze", "Labenze"), ("requiez", "Requiez")]


def test_forward_sql_migration_is_additive_and_bootstrap_has_final_limits():
    migration = MIGRATION.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    final_generic = (
        "'cr-global','sonara','sunon','alma','lumbro','jome','lauco',"
        "'idelika','conceptos','labenze','requiez'"
    )
    final_mixed = f"'tarkett','offiho',{final_generic}"

    for sql in (migration, bootstrap):
        assert final_generic in sql
        assert "CARDINALITY(p_enabled_suppliers) NOT BETWEEN 1 AND 11" in sql
        assert "jsonb_array_length(p_groups) NOT BETWEEN 0 AND 13" in sql
    assert final_mixed in bootstrap

    assert "('labenze', 'Labenze', 'labenze')" in migration
    assert "('requiez', 'Requiez', 'requiez')" in migration
    upper = migration.upper()
    for destructive in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert destructive not in upper


def test_worker_preflight_and_dev_environment_accept_both_new_suppliers():
    preflight = (ROOT / "deploy" / "hetzner" / "preflight.py").read_text(encoding="utf-8")
    dev_start = (ROOT / "scripts" / "dev-start.ps1").read_text(encoding="utf-8")
    env_example = (ROOT / "mobiliti_saas" / ".env.example").read_text(encoding="utf-8")
    enabled = ",".join(GENERIC_SUPPLIERS)

    assert '"labenze"' in preflight and '"requiez"' in preflight
    assert dev_start.count(f'CATALOG_ENABLED_SUPPLIERS = "{enabled}"') == 2
    assert f"# Valores aceptados (CSV sin espacios): {enabled}" in env_example
    assert "CATALOG_ENABLED_SUPPLIERS=\n" in env_example
