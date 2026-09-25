from types import SimpleNamespace

import pytest

from scripts.refresh_local_lumbro_catalog import merge_lumbro_snapshot


def _item(internal_id, *, product_key, approved_asset=None):
    attributes = {}
    image_kind = "placeholder"
    if approved_asset:
        attributes["approved_asset"] = approved_asset
        attributes["image_reference"] = {"status": "preserved"}
        image_kind = approved_asset["image_kind"]
    return {
        "internal_id": internal_id,
        "supplier": "lumbro",
        "product_key": product_key,
        "attributes": attributes,
        "image_url": "",
        "image_kind": image_kind,
    }


def _asset(digest, image_kind="generated_reference"):
    return {
        "bucket": "catalog-assets",
        "path": f"{digest}.png",
        "image_kind": image_kind,
        "label": "Imagen de referencia",
        "approved": True,
    }


def test_merge_retires_legacy_rows_and_preserves_approved_image(tmp_path):
    digest = "a" * 64
    asset = _asset(digest)
    (tmp_path / f"{digest}.png").write_bytes(b"")
    # El archivo sintético debe obedecer al nombre content-addressed.
    import hashlib

    data = b"referencia-preservada"
    digest = hashlib.sha256(data).hexdigest()
    asset = _asset(digest)
    (tmp_path / f"{digest}.png").write_bytes(data)
    active = {
        "catalog_published_snapshots": {
            "lumbro": {
                "payload": {
                    "items": [
                        _item("lumbro:official:one", product_key="one", approved_asset=asset),
                        _item("lumbro:legacy:retired", product_key="retired"),
                    ]
                }
            },
            "alma": {"payload": {"items": [{"internal_id": "alma:one"}]}},
        },
        "projects": [],
        "catalog_reservations": [],
        "quote_jobs": [{"id": "preservar"}],
    }
    build = SimpleNamespace(
        snapshot={
            "supplier": "lumbro",
            "source_hash": "b" * 64,
            "generated_at": "2026-07-26T20:00:00+00:00",
            "items": [_item("lumbro:official:one", product_key="one")],
            "metadata": {"coverage": {"price_authority": "COSTO LUMBRO !E"}},
        },
        assets_by_sha256={},
        bindings=(),
    )

    refreshed, report = merge_lumbro_snapshot(active, build, assets_dir=tmp_path)

    item = refreshed["catalog_published_snapshots"]["lumbro"]["payload"]["items"][0]
    assert item["attributes"]["approved_asset"] == asset
    assert item["attributes"]["image_reference"] == {"status": "preserved"}
    assert item["image_kind"] == "generated_reference"
    assert report["retired_items"] == 1
    assert report["preserved_reference_images"] == 1
    assert refreshed["quote_jobs"] == active["quote_jobs"]
    assert refreshed["catalog_published_snapshots"]["alma"] == active[
        "catalog_published_snapshots"
    ]["alma"]


def test_merge_rejects_retired_product_referenced_by_active_project(tmp_path):
    active = {
        "catalog_published_snapshots": {
            "lumbro": {
                "payload": {
                    "items": [_item("lumbro:legacy:retired", product_key="retired")]
                }
            }
        },
        "projects": [{"items": [{"internal_id": "lumbro:legacy:retired"}]}],
        "catalog_reservations": [],
    }
    build = SimpleNamespace(
        snapshot={
            "supplier": "lumbro",
            "source_hash": "c" * 64,
            "generated_at": "2026-07-26T20:00:00+00:00",
            "items": [],
            "metadata": {"coverage": {"price_authority": "COSTO LUMBRO !E"}},
        },
        assets_by_sha256={},
        bindings=(),
    )

    with pytest.raises(ValueError, match="referencias activas"):
        merge_lumbro_snapshot(active, build, assets_dir=tmp_path)


def test_merge_migrates_known_project_reference_to_current_official_code(tmp_path):
    import hashlib

    old_id = "lumbro:interconnection:78483498abe20b48684a"
    new_id = "lumbro:variant:current-jumper"
    image_data = b"jumper-oficial"
    digest = hashlib.sha256(image_data).hexdigest()
    (tmp_path / f"{digest}.png").write_bytes(image_data)
    active = {
        "catalog_published_snapshots": {
            "lumbro": {
                "payload": {
                    "items": [_item(old_id, product_key="legacy-jumper")]
                }
            }
        },
        "projects": [
            {
                "payload": {
                    "lines": [
                        {
                            "official_code": old_id,
                            "identity": {"internal_id": old_id},
                            "display_cache": {
                                "name": "Jumper anterior",
                                "code": old_id,
                                "image_url": "",
                            },
                        }
                    ]
                }
            }
        ],
        "catalog_reservations": [],
    }
    current = _item(
        new_id,
        product_key="jumper",
        approved_asset=_asset(digest, "official"),
    )
    current["name"] = "JUMPER 1.5 M"
    current["attributes"]["source_code"] = "JUMP-1.5M"
    build = SimpleNamespace(
        snapshot={
            "supplier": "lumbro",
            "source_hash": "d" * 64,
            "generated_at": "2026-07-26T20:00:00+00:00",
            "items": [current],
            "metadata": {"coverage": {"price_authority": "COSTO LUMBRO !E"}},
        },
        assets_by_sha256={},
        bindings=(),
    )

    refreshed, report = merge_lumbro_snapshot(active, build, assets_dir=tmp_path)

    line = refreshed["projects"][0]["payload"]["lines"][0]
    assert line["identity"]["internal_id"] == new_id
    assert line["official_code"] == new_id
    assert line["display_cache"]["code"] == new_id
    assert line["display_cache"]["name"] == "JUMPER 1.5 M"
    assert report["reference_migrations"] == [
        {
            "from_internal_id": old_id,
            "to_internal_id": new_id,
            "source_code": "JUMP-1.5M",
            "project_or_reservation_references": 1,
        }
    ]
