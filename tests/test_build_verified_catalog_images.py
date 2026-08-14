import hashlib
import json

from scripts.build_verified_catalog_images import build_verified_catalog_images


def _asset(directory, payload, suffix="png"):
    digest = hashlib.sha256(payload).hexdigest()
    name = f"{digest}.{suffix}"
    (directory / name).write_bytes(payload)
    return name


def test_builds_full_verified_catalog_from_visual_manifest_without_touching_commercial_data(tmp_path):
    assets = tmp_path / "catalog-assets"
    assets.mkdir()
    official_asset = _asset(assets, b"official-panel")
    generated_asset = _asset(assets, b"generated-panel")

    active = {
        "catalog_published_snapshots": {
            "sonara": {
                "id": "snapshot-sonara",
                "supplier": "sonara",
                "source_hash": "source-sonara",
                "payload": {
                    "items": [
                        {
                            "internal_id": "sonara:official",
                            "name": "Panel suspendido",
                            "price": 100,
                            "image_url": "",
                            "image_kind": "official",
                            "attributes": {
                                "commercial_note": "preservar",
                                "approved_asset": {"path": official_asset},
                            },
                        },
                        {
                            "internal_id": "sonara:generated",
                            "name": "Panel liso",
                            "price": 200,
                            "image_url": "",
                            "image_kind": "official",
                            "attributes": {
                                "commercial_note": "preservar también",
                                "approved_asset": {"path": official_asset},
                            },
                        },
                    ]
                },
            }
        },
        "quote_jobs": [{"id": "job-preservado"}],
    }
    manifest = {
        "schema_version": 1,
        "supplier": "sonara",
        "expected_snapshot_id": "snapshot-sonara",
        "expected_source_hash": "source-sonara",
        "decisions": [
            {
                "internal_id": "sonara:official",
                "name": "Panel suspendido",
                "decision": "retain",
                "asset": official_asset,
                "image_kind": "official",
                "direct_product_reference": True,
                "reason": "Imagen oficial exacta y aislada.",
                "image_reference": {
                    "status": "official_exact_pdf",
                    "generated": False,
                },
            },
            {
                "internal_id": "sonara:generated",
                "name": "Panel liso",
                "decision": "replace",
                "asset": generated_asset,
                "image_kind": "generated_reference",
                "direct_product_reference": True,
                "reason": "La imagen oficial era una escena de oficina ambigua.",
                "product_url": "https://sonara.mx/soluciones-sonara/",
                "image_reference": {
                    "status": "generated_from_product_description",
                    "generated": True,
                    "source": "openai-imagegen",
                },
            },
        ],
    }
    active_path = tmp_path / "active.json"
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "verified.json"
    active_path.write_text(json.dumps(active, ensure_ascii=False), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = build_verified_catalog_images(
        active_db_path=active_path,
        manifest_path=manifest_path,
        assets_dir=assets,
        output_path=output_path,
    )

    verified = json.loads(output_path.read_text(encoding="utf-8"))
    items = {
        item["internal_id"]: item
        for item in verified["catalog_published_snapshots"]["sonara"]["payload"]["items"]
    }
    assert verified["quote_jobs"] == active["quote_jobs"]
    assert items["sonara:official"]["price"] == 100
    assert items["sonara:official"]["attributes"]["commercial_note"] == "preservar"
    assert items["sonara:generated"]["price"] == 200
    assert items["sonara:generated"]["attributes"]["commercial_note"] == "preservar también"
    assert items["sonara:official"]["image_url"].endswith(official_asset)
    assert items["sonara:generated"]["image_url"].endswith(generated_asset)
    assert items["sonara:generated"]["image_kind"] == "generated_reference"
    assert items["sonara:generated"]["attributes"]["image_reference"]["generated"] is True
    assert items["sonara:generated"]["attributes"]["image_reference"]["direct_product_reference"] is True
    assert report["decisions"] == {"retain": 1, "replace": 1}
    assert report["items"] == 2


def test_rejects_a_manifest_that_does_not_cover_every_supplier_item(tmp_path):
    assets = tmp_path / "catalog-assets"
    assets.mkdir()
    asset = _asset(assets, b"one-panel")
    active = {
        "catalog_published_snapshots": {
            "sonara": {
                "id": "snapshot-sonara",
                "supplier": "sonara",
                "source_hash": "source-sonara",
                "payload": {
                    "items": [
                        {"internal_id": "sonara:one", "name": "Uno", "attributes": {}},
                        {"internal_id": "sonara:two", "name": "Dos", "attributes": {}},
                    ]
                },
            }
        }
    }
    manifest = {
        "schema_version": 1,
        "supplier": "sonara",
        "decisions": [
            {
                "internal_id": "sonara:one",
                "name": "Uno",
                "decision": "retain",
                "asset": asset,
                "image_kind": "official",
                "direct_product_reference": True,
                "reason": "Exacta.",
                "image_reference": {"status": "official_exact_pdf", "generated": False},
            }
        ],
    }
    active_path = tmp_path / "active.json"
    manifest_path = tmp_path / "manifest.json"
    active_path.write_text(json.dumps(active), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    try:
        build_verified_catalog_images(
            active_db_path=active_path,
            manifest_path=manifest_path,
            assets_dir=assets,
            output_path=tmp_path / "verified.json",
        )
    except ValueError as exc:
        assert "cobertura completa" in str(exc)
        assert "sonara:two" in str(exc)
    else:
        raise AssertionError("Se esperaba rechazo por manifiesto incompleto")
