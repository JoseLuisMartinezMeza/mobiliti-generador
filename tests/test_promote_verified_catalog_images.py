import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import scripts.promote_verified_catalog_images as promoter
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


def _v2_asset(directory, color):
    image = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle((256, 256, 767, 767), fill=(*color, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    payload = buffer.getvalue()
    name = f"{hashlib.sha256(payload).hexdigest()}.png"
    (directory / name).write_bytes(payload)
    return name, payload


def _v2_visual_item(supplier, code, asset_name):
    asset_sha256 = Path(asset_name).stem
    return {
        "internal_id": f"{supplier}:{code}",
        "name": f"Producto {supplier} {code}",
        "sku": f"SKU-{supplier}-{code}",
        "price": 1250,
        "stock": 3,
        "options": [{"color": "Negro"}],
        "warnings": ["Advertencia comercial preservada"],
        "product_url": f"https://www.{supplier}.example/productos/{code}",
        "image_url": f"/dev/catalog-assets/{asset_name}",
        "image_kind": "official",
        "attributes": {
            "commercial_note": "No modificar",
            "image_reference": {
                "status": "official_exact",
                "generated": False,
                "source_kind": "manufacturer_official",
                "image_source_url": f"https://media.{supplier}.example/images/{code}.png",
                "source_locator": f"SKU-{supplier}-{code}",
                "source_dimensions": {"width": 512, "height": 512},
                "reviewer": "visual.reviewer@mobiliti.mx",
                "reviewed_at": "2026-08-18T12:00:00Z",
                "full_product_visible": True,
                "not_cropped": True,
                "configuration_supported": True,
                "approved": True,
                "direct_product_reference": True,
                "decision": "retain",
                "reason": "Imagen oficial exacta revisada.",
                "asset_sha256": asset_sha256,
                "asset_quality": {
                    "sha256": asset_sha256,
                    "canvas": {"width": 1024, "height": 1024},
                    "bbox": {"left": 256, "top": 256, "width": 512, "height": 512},
                    "margin": 0.25,
                    "occupancy": 0.25,
                    "aspect_ratio": 1.0,
                },
            },
            "approved_asset": {
                "bucket": "catalog-assets",
                "path": asset_name,
                "image_kind": "official",
                "label": "Imagen oficial verificada",
                "approved": True,
            },
        },
    }


def _labenze_requiez_fixture(tmp_path):
    source_assets = tmp_path / "verified-assets"
    source_assets.mkdir()
    labenze_asset, _ = _v2_asset(source_assets, (20, 80, 140))
    requiez_asset, _ = _v2_asset(source_assets, (160, 80, 20))
    verified_labenze = _v2_visual_item("labenze", "one", labenze_asset)
    verified_requiez = _v2_visual_item("requiez", "one", requiez_asset)
    active_labenze = deepcopy(verified_labenze)
    active_requiez = deepcopy(verified_requiez)
    for item in (active_labenze, active_requiez):
        item["image_url"] = ""
        item["image_kind"] = "placeholder"
        item["attributes"].pop("image_reference")
        item["attributes"].pop("approved_asset")
    active = {
        "catalog_published_snapshots": {
            "labenze": _snapshot("labenze", [active_labenze]),
            "requiez": _snapshot("requiez", [active_requiez]),
            "sonara": _snapshot("sonara", [{"internal_id": "sonara:untouched", "price": 77}]),
        },
        "usuarios": [{"id": "user-1"}],
        "projects": [{"id": "project-1", "revision": 7}],
        "catalog_reservations": [{"id": "reservation-1"}],
        "quote_jobs": [{"id": "job-1"}],
        "quote_snapshots": [{"id": "pdf-snapshot-1", "pdf_reference": "keep.pdf"}],
    }
    verified = deepcopy(active)
    verified["catalog_published_snapshots"]["labenze"]["payload"]["items"] = [verified_labenze]
    verified["catalog_published_snapshots"]["requiez"]["payload"]["items"] = [verified_requiez]
    active_path = tmp_path / "active.json"
    verified_path = tmp_path / "verified.json"
    active_bytes = json.dumps(active, ensure_ascii=False, indent=2).encode("utf-8")
    active_path.write_bytes(active_bytes)
    verified_path.write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "active": active,
        "active_bytes": active_bytes,
        "active_path": active_path,
        "verified": verified,
        "verified_path": verified_path,
        "source_assets": source_assets,
        "target_assets": tmp_path / "catalog-assets",
        "backup": tmp_path / "active.before.json",
        "staged": tmp_path / "active.staged.json",
        "sha": hashlib.sha256(active_bytes).hexdigest(),
    }


def _promote_labenze_requiez(fixture, **changes):
    kwargs = {
        "active_db_path": fixture["active_path"],
        "verified_db_path": fixture["verified_path"],
        "source_assets_dir": fixture["source_assets"],
        "target_assets_dir": fixture["target_assets"],
        "suppliers": ("labenze", "requiez"),
        "backup_path": fixture["backup"],
        "staged_path": fixture["staged"],
        "expected_active_sha256": fixture["sha"],
    }
    kwargs.update(changes)
    return promote_verified_catalog_images(**kwargs)


def test_cli_accepts_labenze_and_requiez_suppliers():
    args = promoter._parser().parse_args(
        [
            "--active-db", "active.json", "--verified-db", "verified.json",
            "--source-assets", "source", "--target-assets", "target",
            "--backup", "backup.json", "--staged", "staged.json",
            "--supplier", "labenze", "--supplier", "requiez",
        ]
    )

    assert args.suppliers == ["labenze", "requiez"]


def test_joint_labenze_requiez_promotion_is_visual_only_and_auditable(tmp_path):
    fixture = _labenze_requiez_fixture(tmp_path)

    report = _promote_labenze_requiez(fixture)

    promoted = json.loads(fixture["active_path"].read_text(encoding="utf-8"))
    for supplier in ("labenze", "requiez"):
        before = fixture["active"]["catalog_published_snapshots"][supplier]["payload"]["items"][0]
        after = promoted["catalog_published_snapshots"][supplier]["payload"]["items"][0]
        assert after["price"] == before["price"]
        assert after["sku"] == before["sku"]
        assert after["options"] == before["options"]
        assert after["stock"] == before["stock"]
        assert after["warnings"] == before["warnings"]
        assert after["attributes"]["commercial_note"] == before["attributes"]["commercial_note"]
        assert after["image_url"] == ""
        assert after["attributes"]["approved_asset"]["path"].endswith(".png")
        assert report["suppliers"][supplier]["snapshot_id_before"] == f"current-{supplier}"
        assert report["suppliers"][supplier]["snapshot_id_after"] == f"current-{supplier}"
    for key in ("usuarios", "projects", "catalog_reservations", "quote_jobs", "quote_snapshots"):
        assert promoted[key] == fixture["active"][key]
    assert promoted["catalog_published_snapshots"]["sonara"] == fixture["active"]["catalog_published_snapshots"]["sonara"]
    assert fixture["backup"].read_bytes() == fixture["active_bytes"]
    assert report["before_sha256"] == fixture["sha"]
    assert report["backup_sha256"] == fixture["sha"]
    assert report["staging_sha256"] == report["after_sha256"]
    assert report["assets"]["unique_objects"] == 2
    assert report["assets"]["unique_bytes"] > 0
    assert report["operational_counts"]["before"] == report["operational_counts"]["after"]


@pytest.mark.parametrize("failure", ("review", "png"))
def test_labenze_requiez_rejects_incomplete_v2_review_or_invalid_asset(tmp_path, failure):
    fixture = _labenze_requiez_fixture(tmp_path)
    verified = json.loads(fixture["verified_path"].read_text(encoding="utf-8"))
    item = verified["catalog_published_snapshots"]["labenze"]["payload"]["items"][0]
    if failure == "review":
        item["attributes"]["image_reference"].pop("reviewer")
        expected = "reviewer"
    else:
        payload = b"no es un PNG"
        name = f"{hashlib.sha256(payload).hexdigest()}.png"
        (fixture["source_assets"] / name).write_bytes(payload)
        item["image_url"] = f"/dev/catalog-assets/{name}"
        item["attributes"]["approved_asset"]["path"] = name
        item["attributes"]["image_reference"]["asset_sha256"] = Path(name).stem
        item["attributes"]["image_reference"]["asset_quality"]["sha256"] = Path(name).stem
        expected = "PNG"
    fixture["verified_path"].write_text(json.dumps(verified), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        _promote_labenze_requiez(fixture)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    assert not fixture["backup"].exists()


@pytest.mark.parametrize("failure", ("missing", "extra", "changed"))
def test_labenze_requiez_rejects_identity_mismatch_or_changed_dev_store(tmp_path, failure):
    fixture = _labenze_requiez_fixture(tmp_path)
    if failure == "changed":
        fixture["active_path"].write_bytes(fixture["active_bytes"] + b"\n")
        expected = "cambió"
    else:
        verified = json.loads(fixture["verified_path"].read_text(encoding="utf-8"))
        items = verified["catalog_published_snapshots"]["requiez"]["payload"]["items"]
        if failure == "missing":
            items.clear()
            expected = "faltan"
        else:
            extra = deepcopy(items[0])
            extra["internal_id"] = "requiez:extra"
            items.append(extra)
            expected = "sobran"
        fixture["verified_path"].write_text(json.dumps(verified), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        _promote_labenze_requiez(fixture)

    assert not fixture["backup"].exists()


def test_labenze_requiez_requires_expected_active_sha256(tmp_path):
    fixture = _labenze_requiez_fixture(tmp_path)

    with pytest.raises(ValueError, match="expected_active_sha256"):
        _promote_labenze_requiez(fixture, expected_active_sha256=None)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]


def test_labenze_requiez_counts_combined_unique_assets_before_writing(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    monkeypatch.setattr(promoter, "MAX_BATCH_ASSET_BYTES", 1, raising=False)

    with pytest.raises(ValueError, match="256 MiB"):
        _promote_labenze_requiez(fixture)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    assert not fixture["backup"].exists()
    assert not fixture["target_assets"].exists()


def test_labenze_requiez_verifies_byte_backup_before_copying_any_asset(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)

    def stop_after_backup(source, target):
        assert fixture["backup"].read_bytes() == fixture["active_bytes"]
        raise OSError("interrumpir después del respaldo")

    monkeypatch.setattr(promoter.shutil, "copy2", stop_after_backup)
    with pytest.raises(OSError, match="interrumpir"):
        _promote_labenze_requiez(fixture)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
