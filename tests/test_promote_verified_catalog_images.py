import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import scripts.promote_verified_catalog_images as promoter
from scripts.build_verified_catalog_images import build_verified_catalog_images
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
    assert promoted_alma["product_url"] == "https://old.example/item"
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
        item["product_url"] = f"https://activo.example/productos/{item['internal_id']}"
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
        assert after["product_url"] == before["product_url"]
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
    assert report["rollback"]["status"] == "not_required"
    assert report["rollback"]["restore_performed"] is False
    assert report["rollback"]["backup_verified"] is True
    assert "os.replace" in report["rollback"]["procedure"]


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


def test_labenze_requiez_keeps_active_db_intact_if_atomic_publish_fails(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    import os

    real_replace = os.replace

    def fail_active_publish(source, destination):
        if Path(source) == fixture["staged"]:
            raise OSError("publicación atómica interrumpida")
        return real_replace(source, destination)

    monkeypatch.setattr(promoter, "os", os, raising=False)
    monkeypatch.setattr(promoter.os, "replace", fail_active_publish)
    with pytest.raises(OSError, match="atómica"):
        _promote_labenze_requiez(fixture)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    assert fixture["backup"].read_bytes() == fixture["active_bytes"]
    assert fixture["staged"].is_file()


def test_labenze_requiez_preserves_a_write_detected_before_final_sha_recheck(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    concurrent_bytes = b'{"concurrent": true}'
    real_copy = promoter._copy_asset_atomically
    copies = 0

    def write_before_final_recheck(source, destination, object_name):
        nonlocal copies
        copied = real_copy(source, destination, object_name)
        copies += 1
        if copies == 2:
            fixture["active_path"].write_bytes(concurrent_bytes)
        return copied

    monkeypatch.setattr(promoter, "_copy_asset_atomically", write_before_final_recheck)
    with pytest.raises(ValueError, match="concurrentemente"):
        _promote_labenze_requiez(fixture, report_path=tmp_path / "concurrent-report.json")

    assert fixture["active_path"].read_bytes() == concurrent_bytes


@pytest.mark.parametrize("missing", ("evidence", "assignments", "source"))
def test_labenze_requiez_rejects_shared_asset_without_global_v2_equivalence(tmp_path, missing):
    fixture = _labenze_requiez_fixture(tmp_path)
    verified = json.loads(fixture["verified_path"].read_text(encoding="utf-8"))
    labenze_item = verified["catalog_published_snapshots"]["labenze"]["payload"]["items"][0]
    requiez_item = deepcopy(labenze_item)
    requiez_item["internal_id"] = "requiez:one"
    reference = requiez_item["attributes"]["image_reference"]
    labenze_reference = labenze_item["attributes"]["image_reference"]
    for candidate in (labenze_reference, reference):
        candidate["shared_visual_evidence"] = {
            "source_url": "https://www.labenze.example/series/serie-compartida",
            "assigned_variant_ids": ["labenze:one", "requiez:one"],
        }
    if missing == "evidence":
        reference.pop("shared_visual_evidence")
    elif missing == "assignments":
        reference["shared_visual_evidence"]["assigned_variant_ids"] = ["requiez:one"]
    else:
        reference["shared_visual_evidence"]["source_url"] = "https://www.requiez.example/otra-serie"
    verified["catalog_published_snapshots"]["requiez"]["payload"]["items"] = [requiez_item]
    fixture["verified_path"].write_text(json.dumps(verified), encoding="utf-8")

    with pytest.raises(ValueError, match="shared_visual"):
        _promote_labenze_requiez(fixture)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    assert not fixture["backup"].exists()


def test_labenze_requiez_uses_atomic_asset_temp_and_retries_after_partial_copy(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    real_copy2 = promoter.shutil.copy2
    calls = 0

    def copy_second_asset_partially(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            Path(target).write_bytes(b"copia parcial")
            raise OSError("copia parcial")
        return real_copy2(source, target)

    with monkeypatch.context() as patched:
        patched.setattr(promoter.shutil, "copy2", copy_second_asset_partially)
        with pytest.raises(OSError, match="copia parcial"):
            _promote_labenze_requiez(fixture)

    published = list(fixture["target_assets"].glob("*.png"))
    assert len(published) == 1
    assert hashlib.sha256(published[0].read_bytes()).hexdigest() == published[0].stem

    retry_report = _promote_labenze_requiez(
        fixture,
        backup_path=tmp_path / "active.retry.before.json",
        staged_path=tmp_path / "active.retry.staged.json",
    )
    assert retry_report["assets"]["copied"] == 1
    assert retry_report["assets"]["already_present"] == 1


def test_labenze_requiez_restores_active_after_post_publish_verification_failure(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    report_path = tmp_path / "failed-promotion-report.json"
    import os

    real_replace = os.replace

    def corrupt_after_publish(source, destination):
        result = real_replace(source, destination)
        if Path(source) == fixture["staged"] and Path(destination) == fixture["active_path"]:
            fixture["active_path"].write_bytes(b"db-corrupto-despues-del-replace")
        return result

    monkeypatch.setattr(promoter.os, "replace", corrupt_after_publish)
    with pytest.raises(RuntimeError, match="activo no coincide"):
        _promote_labenze_requiez(fixture, report_path=report_path)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    failed_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert failed_report["status"] == "failed"
    assert failed_report["rollback"]["restore_attempted"] is True
    assert failed_report["rollback"]["restore_performed"] is True
    assert failed_report["rollback"]["restored"] is True
    assert failed_report["rollback"]["verified"] is True
    assert failed_report["rollback"]["backup_verified"] is True
    assert failed_report["staging"]["state"] == "published"
    failed_path = Path(failed_report["transaction"]["failed_publish_path"])
    assert failed_path.read_bytes() == b"db-corrupto-despues-del-replace"
    assert failed_report["transaction"]["failed_publish_sha256"] == hashlib.sha256(
        b"db-corrupto-despues-del-replace"
    ).hexdigest()


def test_labenze_requiez_refuses_restore_from_backup_changed_after_validation(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    report_path = tmp_path / "invalid-backup-report.json"
    import os

    real_replace = os.replace
    corrupt_bytes = b"db-corrupto-despues-del-replace"
    changed_backup = b"backup-modificado-concurrentemente"

    def corrupt_active_and_backup(source, destination):
        result = real_replace(source, destination)
        if Path(source) == fixture["staged"] and Path(destination) == fixture["active_path"]:
            fixture["active_path"].write_bytes(corrupt_bytes)
            fixture["backup"].write_bytes(changed_backup)
        return result

    monkeypatch.setattr(promoter.os, "replace", corrupt_active_and_backup)
    with pytest.raises(RuntimeError, match="activo no coincide"):
        _promote_labenze_requiez(fixture, report_path=report_path)

    failed_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert fixture["active_path"].read_bytes() == corrupt_bytes
    assert failed_report["rollback"]["backup_verified"] is False
    assert failed_report["rollback"]["backup_expected_sha256"] == fixture["sha"]
    assert failed_report["rollback"]["backup_observed_sha256"] == hashlib.sha256(changed_backup).hexdigest()
    assert failed_report["rollback"]["restore_performed"] is False


def test_labenze_requiez_rejects_existing_report_before_any_mutation(tmp_path):
    fixture = _labenze_requiez_fixture(tmp_path)
    report_path = tmp_path / "already-exists.json"
    report_path.write_text("reporte previo", encoding="utf-8")

    with pytest.raises(ValueError, match="reporte ya existe"):
        _promote_labenze_requiez(fixture, report_path=report_path)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    assert not fixture["backup"].exists()
    assert not fixture["staged"].exists()
    assert not fixture["target_assets"].exists()


def test_labenze_requiez_rejects_a_second_cooperative_promoter_before_validation(tmp_path):
    fixture = _labenze_requiez_fixture(tmp_path)
    lock_path = fixture["active_path"].with_name(f".{fixture['active_path'].name}.promotion.lock")
    lock_path.write_text('{"owner": "otro-promotor"}', encoding="utf-8")

    with pytest.raises(ValueError, match="promoción activa"):
        _promote_labenze_requiez(fixture)

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
    assert not fixture["backup"].exists()


def test_labenze_requiez_never_renames_active_away_during_publish(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    import os

    real_replace = os.replace

    def reject_removing_active(source, destination):
        assert Path(source) != fixture["active_path"], "el path activo nunca puede desaparecer"
        assert fixture["active_path"].exists()
        return real_replace(source, destination)

    monkeypatch.setattr(promoter.os, "replace", reject_removing_active)
    _promote_labenze_requiez(fixture)

    assert fixture["active_path"].is_file()


def test_labenze_requiez_reserves_report_before_first_mutation(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    report_path = tmp_path / "reserved-report.json"

    def stop_after_reservation(source, target):
        reservation = json.loads(report_path.read_text(encoding="utf-8"))
        assert reservation["status"] == "reserved"
        assert reservation["reservation_token"]
        raise OSError("detener tras reservar reporte")

    monkeypatch.setattr(promoter.shutil, "copy2", stop_after_reservation)
    with pytest.raises(OSError, match="reservar reporte"):
        _promote_labenze_requiez(fixture, report_path=report_path)

    assert report_path.is_file()


def test_labenze_requiez_persists_lock_release_receipt_after_success(tmp_path):
    fixture = _labenze_requiez_fixture(tmp_path)
    report_path = tmp_path / "success-report.json"

    report = _promote_labenze_requiez(fixture, report_path=report_path)

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["lock"]["state"] == "released"
    assert persisted["lock"]["state"] == "released"
    assert Path(report["lock"]["released_path"]).is_file()
    assert report["lock"]["receipt"] == persisted["lock"]["receipt"]


def test_labenze_requiez_records_lock_release_permission_error_without_failing_success(tmp_path, monkeypatch):
    fixture = _labenze_requiez_fixture(tmp_path)
    report_path = tmp_path / "release-failed-report.json"
    import os

    real_replace = os.replace

    def deny_lock_release(source, destination):
        if Path(source).name.endswith(".promotion.lock"):
            raise PermissionError("lock todavía abierto")
        return real_replace(source, destination)

    monkeypatch.setattr(promoter.os, "replace", deny_lock_release)
    report = _promote_labenze_requiez(fixture, report_path=report_path)

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["lock"]["state"] == "release_failed"
    assert "PermissionError" in report["lock"]["error"]
    assert Path(report["lock"]["blocking_path"]).is_file()
    assert persisted["lock"] == report["lock"]


def _authentic_shared_v2_fixture(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir(parents=True)
    asset_name, _ = _v2_asset(assets, (40, 100, 160))
    active = {
        "catalog_published_snapshots": {
            supplier: _snapshot(supplier, [{
                "internal_id": f"{supplier}:one",
                "name": "Silla compartida",
                "price": 200,
                "image_url": "",
                "image_kind": "placeholder",
                "attributes": {"commercial_note": "preservar"},
            }])
            for supplier in ("labenze", "requiez")
        }
    }
    active_path = tmp_path / "active.json"
    active_bytes = json.dumps(active, ensure_ascii=False, indent=2).encode("utf-8")
    active_path.write_bytes(active_bytes)
    common_evidence = {
        "source_url": "https://www.labenze.example/series/silla-compartida",
        "assigned_variant_ids": ["labenze:one", "requiez:one"],
    }
    verified_path = active_path
    for supplier in ("labenze", "requiez"):
        reference = {
            "status": "official_exact",
            "generated": False,
            "source_kind": "manufacturer_official",
            "image_source_url": f"https://media.{supplier}.example/silla.png",
            "source_locator": f"SKU-{supplier}",
            "source_dimensions": {"width": 512, "height": 512},
            "reviewer": "visual.reviewer@mobiliti.mx",
            "reviewed_at": "2026-08-18T12:00:00Z",
            "full_product_visible": True,
            "not_cropped": True,
            "configuration_supported": True,
            "approved": True,
            "shared_visual_evidence": deepcopy(common_evidence),
        }
        manifest = {
            "schema_version": 2,
            "supplier": supplier,
            "expected_snapshot_id": f"current-{supplier}",
            "expected_source_hash": f"hash-{supplier}",
            "decisions": [{
                "internal_id": f"{supplier}:one",
                "name": "Silla compartida",
                "decision": "retain",
                "asset": asset_name,
                "image_kind": "official",
                "direct_product_reference": True,
                "reason": "El fabricante demuestra la equivalencia visual.",
                "product_url": f"https://www.{supplier}.example/productos/silla-compartida",
                "shared_visual_group": "silla-compartida",
                "image_reference": reference,
            }],
            "shared_visual_equivalence_matrix": {
                "silla-compartida": {
                    "variant_internal_ids": ["labenze:one", "requiez:one"],
                    "same_source_url": common_evidence["source_url"],
                    "evidence": "La serie oficial usa la misma toma para ambas variantes.",
                }
            },
        }
        manifest_path = tmp_path / f"manifest-{supplier}.json"
        output_path = tmp_path / f"verified-{supplier}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        build_verified_catalog_images(
            active_db_path=verified_path,
            manifest_path=manifest_path,
            assets_dir=assets,
            output_path=output_path,
        )
        verified_path = output_path
    return {
        "active_path": active_path,
        "active_bytes": active_bytes,
        "verified_path": verified_path,
        "assets": assets,
        "target_assets": tmp_path / "published-assets",
        "backup": tmp_path / "before.json",
        "staged": tmp_path / "staged.json",
        "sha": hashlib.sha256(active_bytes).hexdigest(),
    }


def test_labenze_requiez_accepts_shared_visual_evidence_persisted_by_builder_v2(tmp_path):
    fixture = _authentic_shared_v2_fixture(tmp_path)

    report = promote_verified_catalog_images(
        active_db_path=fixture["active_path"],
        verified_db_path=fixture["verified_path"],
        source_assets_dir=fixture["assets"],
        target_assets_dir=fixture["target_assets"],
        suppliers=("labenze", "requiez"),
        backup_path=fixture["backup"],
        staged_path=fixture["staged"],
        expected_active_sha256=fixture["sha"],
    )

    assert report["assets"]["unique_objects"] == 1


def test_labenze_requiez_rejects_shared_visual_when_builder_evidence_is_removed(tmp_path):
    fixture = _authentic_shared_v2_fixture(tmp_path)
    verified = json.loads(fixture["verified_path"].read_text(encoding="utf-8"))
    verified["catalog_published_snapshots"]["requiez"]["payload"]["items"][0]["attributes"]["image_reference"].pop(
        "shared_visual_evidence"
    )
    fixture["verified_path"].write_text(json.dumps(verified), encoding="utf-8")

    with pytest.raises(ValueError, match="shared_visual"):
        promote_verified_catalog_images(
            active_db_path=fixture["active_path"],
            verified_db_path=fixture["verified_path"],
            source_assets_dir=fixture["assets"],
            target_assets_dir=fixture["target_assets"],
            suppliers=("labenze", "requiez"),
            backup_path=fixture["backup"],
            staged_path=fixture["staged"],
            expected_active_sha256=fixture["sha"],
        )

    assert fixture["active_path"].read_bytes() == fixture["active_bytes"]
