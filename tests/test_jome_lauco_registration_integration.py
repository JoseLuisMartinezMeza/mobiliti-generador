"""Contratos end-to-end de registro para los catálogos JOME y Lauco."""

import os
from datetime import date
from uuid import UUID

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

from mobiliti_saas.api import index as api
from mobiliti_saas.quote_engine.mixed_catalog import (
    build_mixed_catalog_cart_payload,
    build_mixed_reservation_groups,
)
from mobiliti_saas.worker import quote_worker
from mobiliti_saas.worker.catalog_sync import load_source_config
from mobiliti_saas.worker.catalog_sync import repository as catalog_repository
from mobiliti_saas.worker.catalog_sync import service as catalog_service
from mobiliti_saas.worker.catalog_sync.repository import SyncClaim


SOURCES_PATH = "mobiliti_saas/worker/catalog_sync/sources.json"
JOME_LAUCO = ("jome", "lauco")
JOB_ID = "11111111-1111-4111-8111-111111111111"


def _supplier_item(supplier: str) -> dict:
    return {
        "internal_id": f"{supplier}:integration:1",
        "supplier": supplier,
        "product_key": f"{supplier}-integration-1",
        "sku": f"{supplier.upper()}-INT-1",
        "code_status": "verified",
        "brand": supplier,
        "collection": "Integracion",
        "name": f"Producto {supplier}",
        "description": "Producto para validar la integración del catálogo.",
        "unit": "PZA",
        "availability_type": "stocked",
        "stock": "5.000000",
        "lead_time": "Entrega inmediata",
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": "MXN",
        "price_net": "100.000000",
        "tax_rate": "0.160000",
        "attributes": {},
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": "",
        "warnings": [],
        "source_reference": f"{supplier}:integration",
    }


def _supplier_catalog(supplier: str, source_hash: str) -> dict:
    return {
        "supplier": supplier,
        "source_hash": source_hash * 64,
        "generated_at": "2026-07-25T00:00:00+00:00",
        "items": [_supplier_item(supplier)],
    }


def test_catalog_sync_service_activates_jome_and_lauco_from_the_approved_sources(
    monkeypatch,
):
    sources = {source.supplier: source for source in load_source_config(SOURCES_PATH)}

    assert {supplier: sources[supplier].adapter for supplier in JOME_LAUCO} == {
        "jome": "jome",
        "lauco": "lauco",
    }
    assert {supplier: sources[supplier].label for supplier in JOME_LAUCO} == {
        "jome": "JOME",
        "lauco": "Lauco",
    }
    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", ",".join(JOME_LAUCO))

    assert catalog_service._enabled_suppliers() == JOME_LAUCO
    assert {"jome", "lauco"} <= set(catalog_service.ADAPTERS)


def test_catalog_repository_accepts_jome_and_lauco_claims_and_whitelists():
    assert catalog_repository._sync_supplier_whitelist(JOME_LAUCO) == list(JOME_LAUCO)

    for supplier in JOME_LAUCO:
        claim = SyncClaim.from_row(
            {
                "run_id": JOB_ID,
                "supplier": supplier,
                "trigger_type": "scheduled",
                "requested_by": None,
            }
        )
        assert claim == SyncClaim(UUID(JOB_ID), supplier, "scheduled", None)


def test_mixed_catalog_composes_jome_and_lauco_as_mxn_with_canonical_labels_and_reservations():
    catalogs = {
        "jome": _supplier_catalog("jome", "a"),
        "lauco": _supplier_catalog("lauco", "b"),
    }
    payload = build_mixed_catalog_cart_payload(
        [
            {"catalog": "jome", "internal_id": "jome:integration:1", "quantity": "1"},
            {"catalog": "lauco", "internal_id": "lauco:integration:1", "quantity": "2"},
        ],
        catalogs=catalogs,
        rate_rows=[],
        quote_currency="MXN",
        commercial_discount_percent="40",
        today=date(2026, 7, 25),
    )

    assert [
        (group["catalog"], group["base_currency"], group["items"][0]["supplier"])
        for group in payload["groups"]
    ] == [
        ("jome", "MXN", "JOME"),
        ("lauco", "MXN", "Lauco"),
    ]
    assert build_mixed_reservation_groups(payload) == [
        {
            "catalog": "jome",
            "items": [
                {
                    "identity": "jome:integration:1",
                    "sku": "JOME-INT-1",
                    "quantity": "1.000000",
                    "stock": "5.000000",
                }
            ],
        },
        {
            "catalog": "lauco",
            "items": [
                {
                    "identity": "lauco:integration:1",
                    "sku": "LAUCO-INT-1",
                    "quantity": "2.000000",
                    "stock": "5.000000",
                }
            ],
        },
    ]


def test_api_enables_jome_and_lauco_as_supplier_catalogs(monkeypatch):
    monkeypatch.setattr(api, "CATALOG_ENABLED_SUPPLIERS", JOME_LAUCO)

    assert api._enabled_catalog_suppliers() == JOME_LAUCO
    assert [api._require_enabled_catalog_supplier(supplier) for supplier in JOME_LAUCO] == list(
        JOME_LAUCO
    )


def test_api_reserves_jome_and_lauco_through_the_generic_supplier_path(monkeypatch):
    state = {
        "quote_jobs": [{"id": JOB_ID, "usuario_id": 7, "status": "draft"}],
        "tarkett_reservations": [],
        "offiho_reservations": [],
        "catalog_reservations": [],
    }
    monkeypatch.setattr(api, "DEV_MODE", True)
    monkeypatch.setattr(api, "_dev_load", lambda: state)
    monkeypatch.setattr(api, "_dev_save", lambda _state: None)

    snapshot = api.db_reserve_mixed_cart(
        7,
        JOB_ID,
        [
            {
                "catalog": "jome",
                "items": [
                    {"identity": "jome:integration:1", "sku": "JOME-INT-1", "quantity": "1", "stock": "5"}
                ],
            },
            {
                "catalog": "lauco",
                "items": [
                    {"identity": "lauco:integration:1", "sku": "LAUCO-INT-1", "quantity": "2", "stock": "5"}
                ],
            },
        ],
    )

    assert [row["catalog"] for row in snapshot] == list(JOME_LAUCO)
    assert {(row["supplier"], row["internal_id"]) for row in state["catalog_reservations"]} == {
        ("jome", "jome:integration:1"),
        ("lauco", "lauco:integration:1"),
    }


def test_quote_worker_uses_the_published_jome_and_lauco_labels():
    assert {supplier: quote_worker.SUPPLIER_LABELS[supplier] for supplier in JOME_LAUCO} == {
        "jome": "JOME",
        "lauco": "Lauco",
    }
