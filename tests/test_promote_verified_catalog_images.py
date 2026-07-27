import hashlib
import json
from copy import deepcopy

from scripts.promote_verified_catalog_images import promote_verified_catalog_images


def _snapshot(supplier, items):
    return {
        "id": f"current-{supplier}",
        "supplier": supplier,
        "source_hash": f"hash-{supplier}",
        "payload": {"items": items},
    }


def test_promotes_only_selected_images_without_replacing_operational_data(tmp_path):
    image_bytes = b"verified-catalog-image"
    digest = hashlib.sha256(image_bytes).hexdigest()
    object_name = f"{digest}.png"
    source_assets = tmp_path / "verified-assets"
    source_assets.mkdir()
    (source_assets / object_name).write_bytes(image_bytes)

    alma_current = {
        "internal_id": "alma:one",
        "name": "Mesa ALMA",
        "price": 100,
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": "https://old.example/item",
        "attributes": {"commercial_note": "preservar"},
    }
    sonara_current = {
        "internal_id": "sonara:one",
        "name": "Panel Sonara",
        "image_url": "",
        "image_kind": "placeholder",
        "attributes": {},
    }
    active = {
        "catalog_published_snapshots": {
            "alma": _snapshot("alma", [alma_current]),
            "sonara": _snapshot("sonara", [sonara_current]),
        },
        "quote_jobs": [{"id": "job-current"}],
        "catalog_reservations": [{"id": "reservation-current"}],
        "usuarios": [{"id": "user-current"}],
    }
    verified = deepcopy(active)
    verified_alma = verified["catalog_published_snapshots"]["alma"]["payload"]["items"][0]
    verified_alma.update(
        {
            "price": 999,
            "image_url": f"http://127.0.0.1:8092/alma-kun/{object_name}",
            "image_kind": "generated_reference",
            "product_url": "https://official.example/reference",
            "attributes": {
                "commercial_note": "no reemplazar",
                "image_reference": {
                    "status": "official_family_reference",
                    "generated": False,
                },
                "web_image_quality": {
                    "status": "family_web",
                    "sha256": digest,
                    "width": 900,
                    "height": 900,
                },
            },
        }
    )
    verified_sonara = verified["catalog_published_snapshots"]["sonara"]["payload"]["items"][0]
    verified_sonara["image_url"] = f"http://127.0.0.1:8092/sonara/{object_name}"

    active_path = tmp_path / "active.json"
    verified_path = tmp_path / "verified.json"
    backup_path = tmp_path / "active.before.json"
    staged_path = tmp_path / "active.promoted.json"
    target_assets = tmp_path / "catalog-assets"
    active_bytes = json.dumps(active, ensure_ascii=False, indent=2).encode("utf-8")
    active_path.write_bytes(active_bytes)
    verified_path.write_text(json.dumps(verified, ensure_ascii=False), encoding="utf-8")

    report = promote_verified_catalog_images(
        active_db_path=active_path,
        verified_db_path=verified_path,
        source_assets_dir=source_assets,
        target_assets_dir=target_assets,
        suppliers=("alma",),
        backup_path=backup_path,
        staged_path=staged_path,
    )

    promoted = json.loads(active_path.read_text(encoding="utf-8"))
    promoted_alma = promoted["catalog_published_snapshots"]["alma"]["payload"]["items"][0]
    assert backup_path.read_bytes() == active_bytes
    assert promoted["quote_jobs"] == active["quote_jobs"]
    assert promoted["catalog_reservations"] == active["catalog_reservations"]
    assert promoted["usuarios"] == active["usuarios"]
    assert promoted["catalog_published_snapshots"]["sonara"] == active["catalog_published_snapshots"]["sonara"]
    assert promoted_alma["price"] == 100
    assert promoted_alma["attributes"]["commercial_note"] == "preservar"
    assert promoted_alma["product_url"] == "https://official.example/reference"
    assert promoted_alma["image_url"] == ""
    assert promoted_alma["image_kind"] == "generated_reference"
    assert promoted_alma["attributes"]["image_reference"]["status"] == "official_family_reference"
    assert promoted_alma["attributes"]["web_image_quality"]["sha256"] == digest
    assert promoted_alma["attributes"]["approved_asset"] == {
        "bucket": "catalog-assets",
        "path": object_name,
        "image_kind": "generated_reference",
        "label": "Imagen de referencia",
        "approved": True,
    }
    assert (target_assets / object_name).read_bytes() == image_bytes
    assert report["suppliers"]["alma"] == {
        "items": 1,
        "official": 0,
        "generated_reference": 1,
        "unique_assets": 1,
    }


def test_promotes_sonara_images(tmp_path):
    image_bytes = b"verified-sonara-image"
    digest = hashlib.sha256(image_bytes).hexdigest()
    object_name = f"{digest}.png"
    source_assets = tmp_path / "verified-assets"
    source_assets.mkdir()
    (source_assets / object_name).write_bytes(image_bytes)

    current_item = {
        "internal_id": "sonara:one",
        "name": "Panel Sonara",
        "price": 321,
        "image_url": "",
        "image_kind": "placeholder",
        "attributes": {"commercial_note": "preservar"},
    }
    active = {
        "catalog_published_snapshots": {
            "sonara": _snapshot("sonara", [current_item]),
        },
        "quote_jobs": [{"id": "job-current"}],
    }
    verified = deepcopy(active)
    verified_item = verified["catalog_published_snapshots"]["sonara"]["payload"]["items"][0]
    verified_item.update(
        {
            "image_url": f"http://127.0.0.1:8000/dev/catalog-assets/{object_name}",
            "image_kind": "official",
            "attributes": {
                "image_reference": {"status": "official_exact_pdf", "generated": False},
                "web_image_quality": {"status": "official_pdf", "sha256": digest},
            },
        }
    )

    active_path = tmp_path / "active.json"
    verified_path = tmp_path / "verified.json"
    backup_path = tmp_path / "active.before.json"
    staged_path = tmp_path / "active.promoted.json"
    target_assets = tmp_path / "catalog-assets"
    active_path.write_text(json.dumps(active), encoding="utf-8")
    verified_path.write_text(json.dumps(verified), encoding="utf-8")

    report = promote_verified_catalog_images(
        active_db_path=active_path,
        verified_db_path=verified_path,
        source_assets_dir=source_assets,
        target_assets_dir=target_assets,
        suppliers=("sonara",),
        backup_path=backup_path,
        staged_path=staged_path,
    )

    promoted_item = json.loads(active_path.read_text(encoding="utf-8"))[
        "catalog_published_snapshots"
    ]["sonara"]["payload"]["items"][0]
    assert promoted_item["price"] == 321
    assert promoted_item["attributes"]["commercial_note"] == "preservar"
    assert promoted_item["image_url"] == ""
    assert promoted_item["image_kind"] == "official"
    assert promoted_item["attributes"]["approved_asset"]["path"] == object_name
    assert report["suppliers"]["sonara"] == {
        "items": 1,
        "official": 1,
        "generated_reference": 0,
        "unique_assets": 1,
    }
