from copy import deepcopy
from pathlib import Path

import pytest


def _item(inventory_key: str, *, stock=4, description="Descripcion oficial"):
    code = inventory_key.split()[0]
    return {
        "inventory_key": inventory_key,
        "code": code,
        "name": "Modelo",
        "variant": "NEGRO",
        "unit": "PZA",
        "pieces_per_box": 1,
        "available_quantity": stock,
        "unit_price": 100,
        "price_source": "inventory",
        "product_url": "",
        "image_url": "",
        "description": description,
        "description_source": "official_site",
        "match_status": "official_code_match",
        "source_updated_at": "2026-07-01T00:00:00Z",
    }


def _payload(items, *, duplicate_count=0):
    items = deepcopy(items)
    return {
        "source_hash": "a" * 64,
        "generated_at": "2026-07-01T00:00:00Z",
        "source_row_count": len(items) + duplicate_count,
        "duplicate_row_count": duplicate_count,
        "unique_item_count": len(items),
        "total": len(items),
        "items": items,
    }


def test_offiho_loader_accepts_dynamic_consistent_cardinality():
    from mobiliti_saas.quote_engine.offiho_catalog import load_offiho_catalog_data

    loaded = load_offiho_catalog_data(
        _payload([_item("OHE-1 NEGRO MODELO"), _item("OHE-2 NEGRO MODELO")], duplicate_count=1)
    )

    assert loaded["source_row_count"] == 3
    assert loaded["duplicate_row_count"] == 1
    assert loaded["unique_item_count"] == 2
    assert len(loaded["items"]) == 2


def test_offiho_loader_rejects_inconsistent_audit_counts():
    from mobiliti_saas.quote_engine.offiho_catalog import load_offiho_catalog_data

    payload = _payload([_item("OHE-1 NEGRO MODELO")])
    payload["source_row_count"] = 7

    with pytest.raises(ValueError, match="conteos"):
        load_offiho_catalog_data(payload)


def test_runtime_parser_matches_legacy_inventory_values_on_checked_in_workbook():
    from mobiliti_saas.quote_engine.offiho_inventory import parse_offiho_inventory
    from scripts import build_offiho_catalog as legacy

    path = Path("catalog_sources/offiho/existencias.xls")
    current_items, current_audit = parse_offiho_inventory(path)
    legacy_items, legacy_audit = legacy._parse_inventory_xls(path)
    fields = (
        "code",
        "name",
        "variant",
        "pieces_per_box",
        "available_quantity",
        "unit_price",
        "price_source",
    )

    assert {field: current_audit[field] for field in legacy_audit} == legacy_audit
    assert current_audit["workbook_generated_at"]
    assert {
        row["inventory_key"]: tuple(row[field] for field in fields)
        for row in current_items
    } == {
        row["inventory_key"]: tuple(row[field] for field in fields)
        for row in legacy_items
    }


def test_runtime_parser_keeps_highest_stock_for_duplicate_key_with_same_commercial_data(tmp_path):
    from mobiliti_saas.quote_engine.offiho_inventory import parse_offiho_inventory

    path = tmp_path / "existencias.xls"
    path.write_text(
        """<html><table>
        <tr><th>CODIGO</th><th>Existencia</th><th>Piezas por Caja</th><th>Precio Lista 1</th></tr>
        <tr><td>ARO CROMADO CROMADO</td><td>1772</td><td>0</td><td>699</td></tr>
        <tr><td>ARO CROMADO CROMADO</td><td>0</td><td>0</td><td>699</td></tr>
        </table></html>""",
        encoding="utf-8",
    )

    items, audit = parse_offiho_inventory(path)

    assert len(items) == 1
    assert items[0]["available_quantity"] == 1772
    assert audit["source_row_count"] == 2
    assert audit["duplicate_row_count"] == 1
    assert audit["unique_item_count"] == 1


@pytest.mark.parametrize(
    ("inventory_key", "expected_name", "expected_variant"),
    [
        ("OHV-94 PLUS CR NEGRO SLING *", "CR SLING *", "PLUS NEGRO"),
        ("OHV-20 GRIS OBSCURO CORE", "CORE", "GRIS OBSCURO"),
        ("OHV-81 YB AMARILLO JOYOUS", "YB JOYOUS", "AMARILLO"),
    ],
)
def test_runtime_identity_parser_matches_builder_for_compound_offiho_variants(
    inventory_key,
    expected_name,
    expected_variant,
):
    from mobiliti_saas.quote_engine.offiho_inventory import _extract_identity
    from scripts.build_offiho_catalog import extract_offiho_identity

    runtime = _extract_identity(inventory_key)
    builder = extract_offiho_identity(inventory_key)

    assert (runtime.code, runtime.name, runtime.variant) == (
        builder.code,
        builder.name,
        builder.variant,
    )
    assert runtime.name == expected_name
    assert runtime.variant == expected_variant


def test_runtime_identity_keeps_terminal_star_as_source_product_name(tmp_path):
    from mobiliti_saas.quote_engine.offiho_inventory import parse_offiho_inventory

    path = tmp_path / "existencias.xls"
    path.write_text(
        """<html><table>
        <tr><th>CODIGO</th><th>Existencia</th><th>Piezas por Caja</th><th>Precio Lista 1</th></tr>
        <tr><td>OHV-408 NEGRO ALUFSEN *</td><td>5</td><td>1</td><td>7999</td></tr>
        </table></html>""",
        encoding="utf-8",
    )

    items, _audit = parse_offiho_inventory(path)

    assert items[0]["code"] == "OHV-408"
    assert items[0]["variant"] == "NEGRO"
    assert items[0]["name"] == "ALUFSEN *"


def test_inventory_refresh_replaces_population_and_preserves_unique_star_enrichment():
    from mobiliti_saas.quote_engine.offiho_inventory import build_refreshed_offiho_catalog

    base = _payload(
        [
            {
                **_item("OHE-1 NEGRO MODELO"),
                "image_url": "https://www.offiho.com/modelo.jpg",
            },
            _item("OHE-OLD NEGRO RETIRADO"),
        ]
    )
    inventory_items = [
        {
            **_item("OHE-1 NEGRO MODELO*", stock=9, description=""),
            "product_url": "",
            "image_url": "",
            "description_source": "inventory_label",
            "match_status": "unmatched",
            "source_updated_at": "",
        },
        {
            **_item("OHE-NEW NEGRO NUEVO", stock=2, description=""),
            "product_url": "",
            "image_url": "",
            "description_source": "inventory_label",
            "match_status": "unmatched",
            "source_updated_at": "",
        },
    ]
    audit = {
        "source_row_count": 3,
        "duplicate_row_count": 1,
        "unique_item_count": 2,
        "excluded_stock_status_count": 0,
        "excluded_header_row_count": 0,
        "defaulted_pieces_status_count": 0,
        "excluded_blank_stock_count": 0,
    }

    refreshed = build_refreshed_offiho_catalog(
        base,
        inventory_items,
        audit,
        inventory_sha256="b" * 64,
        inventory_size_bytes=1234,
        synchronized_at="2026-08-11T20:00:00Z",
        inventory_last_modified="2026-08-11T14:46:00Z",
    )

    assert [row["inventory_key"] for row in refreshed["items"]] == [
        "OHE-1 NEGRO MODELO*",
        "OHE-NEW NEGRO NUEVO",
    ]
    assert refreshed["items"][0]["available_quantity"] == 9
    assert refreshed["items"][0]["image_url"] == "https://www.offiho.com/modelo.jpg"
    assert refreshed["items"][1]["image_url"] == ""
    assert refreshed["source_row_count"] == 3
    assert refreshed["duplicate_row_count"] == 1
    assert refreshed["unique_item_count"] == refreshed["total"] == 2
    assert refreshed["generated_at"] == "2026-08-11T20:00:00Z"
    assert refreshed["catalog_built_at"] == "2026-08-11T20:00:00Z"
    assert refreshed["inventory_last_modified"] == "2026-08-11T14:46:00Z"
    assert refreshed["sources"]["inventory"]["sha256"] == "b" * 64


def test_inventory_refresh_source_hash_is_stable_when_only_check_time_changes():
    from mobiliti_saas.quote_engine.offiho_inventory import build_refreshed_offiho_catalog

    base = _payload([_item("OHE-1 NEGRO MODELO")])
    inventory = [_item("OHE-1 NEGRO MODELO", stock=8)]
    audit = {"source_row_count": 1, "duplicate_row_count": 0, "unique_item_count": 1}

    first = build_refreshed_offiho_catalog(
        base, inventory, audit, inventory_sha256="c" * 64,
        inventory_size_bytes=20, synchronized_at="2026-08-11T20:00:00Z",
    )
    second = build_refreshed_offiho_catalog(
        first, inventory, audit, inventory_sha256="c" * 64,
        inventory_size_bytes=20, synchronized_at="2026-08-11T21:00:00Z",
    )

    assert first["source_hash"] == second["source_hash"]
    assert first["generated_at"] != second["generated_at"]


def test_inventory_refresh_rejects_anomalous_population_drop():
    from mobiliti_saas.quote_engine.offiho_inventory import build_refreshed_offiho_catalog

    base = _payload([_item(f"OHE-{index} NEGRO MODELO") for index in range(1, 9)])
    inventory = [_item("OHE-1 NEGRO MODELO")]
    audit = {"source_row_count": 1, "duplicate_row_count": 0, "unique_item_count": 1}

    with pytest.raises(ValueError, match="cardinalidad"):
        build_refreshed_offiho_catalog(
            base, inventory, audit, inventory_sha256="d" * 64,
            inventory_size_bytes=20, synchronized_at="2026-08-11T20:00:00Z",
        )


def test_inventory_refresh_rejects_anomalous_population_growth():
    from mobiliti_saas.quote_engine.offiho_inventory import build_refreshed_offiho_catalog

    base = _payload([_item(f"OHE-{index} NEGRO MODELO") for index in range(1, 9)])
    inventory = [_item(f"FAKE-{index} NEGRO MODELO") for index in range(1, 14)]
    audit = {"source_row_count": 13, "duplicate_row_count": 0, "unique_item_count": 13}

    with pytest.raises(ValueError, match="cardinalidad"):
        build_refreshed_offiho_catalog(
            base, inventory, audit, inventory_sha256="e" * 64,
            inventory_size_bytes=20, synchronized_at="2026-08-11T20:00:00Z",
        )


def test_inventory_refresh_rejects_same_size_population_with_unrelated_keys():
    from mobiliti_saas.quote_engine.offiho_inventory import build_refreshed_offiho_catalog

    base = _payload([_item(f"OHE-{index} NEGRO MODELO") for index in range(1, 9)])
    inventory = [_item(f"FAKE-{index} NEGRO MODELO") for index in range(1, 9)]
    audit = {"source_row_count": 8, "duplicate_row_count": 0, "unique_item_count": 8}

    with pytest.raises(ValueError, match="cobertura"):
        build_refreshed_offiho_catalog(
            base, inventory, audit, inventory_sha256="f" * 64,
            inventory_size_bytes=20, synchronized_at="2026-08-11T20:00:00Z",
        )


def test_worker_offiho_sync_publishes_changed_snapshot_and_respects_interval(monkeypatch, tmp_path):
    from mobiliti_saas.quote_engine.offiho_inventory import OffihoInventoryDownload
    from mobiliti_saas.worker import quote_worker

    base = _payload([_item("OHE-1 NEGRO MODELO")])
    refreshed = {**base, "source_hash": "e" * 64, "generated_at": "2026-08-11T20:00:00Z"}
    inventory_path = tmp_path / "existencias.xls"
    inventory_path.write_bytes(b"xls")
    saved = []

    class CatalogClient:
        def catalog_snapshot_get(self, supplier):
            assert supplier == "offiho"
            return {"supplier": supplier, "source_hash": base["source_hash"], "payload": base}

        def catalog_snapshot_upsert(self, supplier, payload):
            saved.append((supplier, payload))
            return {"supplier": supplier, "source_hash": payload["source_hash"]}

    monkeypatch.setattr(quote_worker, "OFFIHO_SYNC_ENABLED", True)
    monkeypatch.setattr(quote_worker, "OFFIHO_SYNC_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(quote_worker, "_OFFIHO_LAST_SYNC_ATTEMPT", 0.0)
    monkeypatch.setattr(
        quote_worker,
        "download_offiho_inventory",
        lambda path, **_kwargs: OffihoInventoryDownload(
            path=Path(path), sha256="f" * 64, size_bytes=3,
            last_modified="2026-08-11T14:46:00Z",
        ),
    )
    monkeypatch.setattr(
        quote_worker,
        "refresh_offiho_catalog_from_file",
        lambda payload, *_args, **_kwargs: refreshed,
    )
    client = CatalogClient()

    assert quote_worker.sync_offiho_catalog_if_due(client, force=True) is True
    assert saved == [("offiho", refreshed)]
    assert quote_worker.sync_offiho_catalog_if_due(client) is False
    assert saved == [("offiho", refreshed)]


@pytest.mark.parametrize(
    ("final_url", "content_type", "payload", "error"),
    [
        (
            "https://attacker.invalid/existencias.xls",
            "application/vnd.ms-excel",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1safe",
            "redirigio",
        ),
        (
            "https://www.offiho.com/existencias.xls",
            "application/pdf",
            b"%PDF-1.7",
            "no devolvio un archivo XLS",
        ),
        (
            "https://www.offiho.com/existencias.xls",
            "application/vnd.ms-excel",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1too-large",
            "excede el limite",
        ),
    ],
)
def test_offiho_downloader_rejects_redirect_mime_and_oversize(
    monkeypatch, tmp_path, final_url, content_type, payload, error,
):
    from mobiliti_saas.quote_engine import offiho_inventory

    class Headers(dict):
        def get_content_type(self):
            return content_type

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return final_url

        def read(self, _limit):
            return payload

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(offiho_inventory, "_OFFICIAL_OPENER", Opener())
    max_bytes = 8 if "excede" in error else 1024

    with pytest.raises(ValueError, match=error):
        offiho_inventory.download_offiho_inventory(
            tmp_path / "existencias.xls",
            max_bytes=max_bytes,
        )

    assert not (tmp_path / "existencias.xls").exists()
