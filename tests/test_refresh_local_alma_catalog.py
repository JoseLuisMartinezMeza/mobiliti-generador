import hashlib
from types import SimpleNamespace

from scripts.refresh_local_alma_catalog import merge_alma_snapshot


def _item(*, internal_id, price_options=None, approved_asset=None):
    attributes = {}
    if approved_asset is not None:
        attributes["approved_asset"] = approved_asset
        attributes["image_reference"] = "referencia-curada"
    return {
        "internal_id": internal_id,
        "product_key": "kun:kc8804b10tex",
        "name": "PILLOW Single Sofa Rim: Teak",
        "base_price_options": price_options or [],
        "attributes": attributes,
        "image_kind": "official",
        "image_url": "",
    }


def test_merge_alma_replaces_only_catalog_and_preserves_curated_image(tmp_path):
    data = b"curated-alma-image"
    digest = hashlib.sha256(data).hexdigest()
    asset = {
        "bucket": "catalog-assets",
        "path": f"{digest}.png",
        "image_kind": "official",
        "approved": True,
    }
    (tmp_path / asset["path"]).write_bytes(data)
    current = _item(internal_id="alma:kun:variant:1", approved_asset=asset)
    active = {
        "catalog_published_snapshots": {
            "alma": {"payload": {"supplier": "alma", "items": [current]}},
            "sunon": {"payload": {"supplier": "sunon", "items": [{"id": "keep"}]}},
        },
        "projects": [{"payload": {"lines": [{"identity": {"internal_id": current["internal_id"]}}]}}],
    }
    fresh = _item(
        internal_id=current["internal_id"],
        price_options=[{"id": "base-c7", "name": "Tela A", "price_net": "1104.14", "available": True}],
    )
    build = SimpleNamespace(
        snapshot={
            "supplier": "alma",
            "source_hash": "b" * 64,
            "generated_at": "2026-08-01T12:00:00Z",
            "items": [fresh],
        },
        assets_by_sha256={},
    )

    refreshed, report = merge_alma_snapshot(active, build, assets_dir=tmp_path)

    merged = refreshed["catalog_published_snapshots"]["alma"]["payload"]["items"][0]
    assert merged["base_price_options"][0]["name"] == "Tela A"
    assert merged["attributes"]["approved_asset"] == asset
    assert merged["attributes"]["image_reference"] == "referencia-curada"
    assert refreshed["catalog_published_snapshots"]["sunon"] == active["catalog_published_snapshots"]["sunon"]
    assert report["before_items"] == report["after_items"] == 1


def test_merge_alma_blocks_retiring_a_product_used_by_a_project(tmp_path):
    current = _item(internal_id="alma:kun:variant:used")
    active = {
        "catalog_published_snapshots": {
            "alma": {"payload": {"supplier": "alma", "items": [current]}},
        },
        "projects": [{"payload": {"lines": [{"identity": {"internal_id": current["internal_id"]}}]}}],
    }
    build = SimpleNamespace(
        snapshot={
            "supplier": "alma",
            "source_hash": "c" * 64,
            "generated_at": "2026-08-01T12:00:00Z",
            "items": [],
        },
        assets_by_sha256={},
    )

    try:
        merge_alma_snapshot(active, build, assets_dir=tmp_path)
    except ValueError as exc:
        assert "referencias activas" in str(exc)
    else:
        raise AssertionError("El retiro de un producto referenciado debio bloquearse")


def test_merge_alma_migrates_legacy_continuation_line_to_canonical_option(tmp_path):
    data = b"official-mondecasa-image"
    digest = hashlib.sha256(data).hexdigest()
    asset = {
        "bucket": "catalog-assets",
        "path": f"{digest}.png",
        "image_kind": "official",
        "approved": True,
    }
    (tmp_path / asset["path"]).write_bytes(data)
    legacy_id = "alma:mondecasa:variant:legacy-ropes"
    canonical_id = "alma:mondecasa:variant:spaghetti-2-seat"
    legacy = _item(internal_id=legacy_id, approved_asset=asset)
    legacy.update(
        {
            "product_key": "review:legacy-ropes",
            "name": "Spaghetti 2 seat sofa Aluminium frame with ropes",
            "source_reference": '[{"cell_or_bbox":"E273","sheet_or_page":"MONDECASA"}]',
        }
    )
    legacy["attributes"]["price_evidence"] = [
        {"source": {"cell_or_bbox": "E273", "sheet_or_page": "MONDECASA"}}
    ]
    active = {
        "catalog_published_snapshots": {
            "alma": {"payload": {"supplier": "alma", "items": [legacy]}},
        },
        "projects": [
            {
                "payload": {
                    "lines": [
                        {
                            "official_code": legacy_id,
                            "catalog": "alma",
                            "identity": {
                                "internal_id": legacy_id,
                                "base_option_id": "",
                                "add_on_option_ids": [],
                            },
                            "display_cache": {
                                "code": legacy_id,
                                "name": legacy["name"],
                                "configuration": "",
                            },
                        }
                    ]
                }
            }
        ],
        "catalog_reservations": [],
    }
    canonical = _item(
        internal_id=canonical_id,
        approved_asset=asset,
        price_options=[
            {
                "id": "base-r272-c5",
                "name": "Spaghetti 2 seat sofa Aluminium frame with straps | 168*82*80 cm",
                "price_net": "323.712510",
                "available": True,
            },
            {
                "id": "base-r273-c5",
                "name": "Spaghetti 2 seat sofa Aluminium frame with ropes | 168*82*80 cm",
                "price_net": "357.533220",
                "available": True,
            },
        ],
    )
    canonical.update(
        {
            "product_key": "mondecasa:ac5795a11rat",
            "name": "Spaghetti 2 seat sofa",
        }
    )
    build = SimpleNamespace(
        snapshot={
            "supplier": "alma",
            "source_hash": "d" * 64,
            "generated_at": "2026-08-01T12:00:00Z",
            "items": [canonical],
        },
        assets_by_sha256={},
    )

    refreshed, report = merge_alma_snapshot(active, build, assets_dir=tmp_path)

    line = refreshed["projects"][0]["payload"]["lines"][0]
    assert line["identity"] == {
        "internal_id": canonical_id,
        "base_option_id": "base-r273-c5",
        "add_on_option_ids": [],
    }
    assert line["official_code"] == canonical_id
    assert line["display_cache"]["code"] == canonical_id
    assert line["display_cache"]["name"] == "Spaghetti 2 seat sofa Aluminium frame with ropes"
    assert line["display_cache"]["configuration"] == (
        "Spaghetti 2 seat sofa Aluminium frame with ropes | 168*82*80 cm"
    )
    assert report["migrated_legacy_references"] == 1
