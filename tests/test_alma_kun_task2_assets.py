import hashlib
from io import BytesIO
import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace

import pytest
from PIL import Image

from mobiliti_saas.worker.catalog_sync.importers import alma
from mobiliti_saas.worker.catalog_sync.importers.common import ImageAsset
from mobiliti_saas.worker.catalog_sync.kundesign_links import build_kundesign_link_index
from mobiliti_saas.worker.catalog_sync.mondecasa_images import (
    MondecasaImageIndex,
    MondecasaImageResolution,
)
from mobiliti_saas.worker.catalog_sync.mondecasa_links import MondecasaLinkIndex
from mobiliti_saas.worker.catalog_sync import service as catalog_service


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class _File:
    path: str
    kind: str
    brand: str
    sha256: str
    mime_type: str
    local_path: Path


def _asset(label: str) -> ImageAsset:
    data = label.encode("ascii")
    return ImageAsset(data, "image/png", 1, 1, hashlib.sha256(data).hexdigest())


def _record(identity: str, description: str, collection: str = "TEST") -> dict:
    return {
        "identity": identity,
        "brand": "KUN",
        "collection": collection,
        "description": description,
    }


def _candidate(identity: str, status: str, asset: ImageAsset, cell: str):
    return alma._AlmaImageCandidate(
        identity=identity,
        asset=asset,
        match_status=status,
        source_reference={
            "file_id": "a" * 64,
            "sheet_or_page": "KUN DESIGN",
            "cell_or_bbox": cell,
        },
    )


def test_asset_resolution_prefers_exact_then_merged_and_reuses_closed_family():
    exact = _asset("exact")
    merged = _asset("merged")
    records = [
        _record("exact", "TEST Chair\nMaterial A"),
        _record("merged", "TEST Table\nMaterial A"),
        _record("family", "TEST Chair\nMaterial B"),
    ]
    candidates = [
        _candidate("exact", "merged_xlsx", merged, "B9"),
        _candidate("merged", "merged_xlsx", merged, "B10"),
        _candidate("exact", "exact_xlsx", exact, "B8"),
    ]

    first = alma._resolve_kun_asset_candidates(records, candidates)
    second = alma._resolve_kun_asset_candidates(list(reversed(records)), list(reversed(candidates)))

    assert {
        identity: (match.match_status, match.asset.sha256)
        for identity, match in first.items()
    } == {
        "exact": ("exact_xlsx", exact.sha256),
        "merged": ("merged_xlsx", merged.sha256),
        "family": ("family_xlsx", exact.sha256),
    }
    assert {
        identity: (match.match_status, match.asset.sha256)
        for identity, match in second.items()
    } == {
        identity: (match.match_status, match.asset.sha256)
        for identity, match in first.items()
    }


@pytest.mark.parametrize(
    "candidates",
    (
        [],
        [
            _candidate("donor-a", "exact_xlsx", _asset("a"), "B8"),
            _candidate("donor-b", "exact_xlsx", _asset("b"), "B9"),
        ],
    ),
)
def test_asset_resolution_fails_closed_for_missing_or_ambiguous_family(candidates):
    records = [_record("target", "TEST Chair")]
    if candidates:
        records.extend(
            [
                _record("donor-a", "TEST Chair"),
                _record("donor-b", "TEST Chair"),
            ]
        )

    with pytest.raises(ValueError, match="ALMA_IMAGE_COVERAGE"):
        alma._resolve_kun_asset_candidates(records, candidates)


def test_sidecar_dataclasses_are_typed_and_snapshot_never_contains_asset_bytes():
    asset = _asset("official")
    binding = alma.AlmaAssetBinding(
        internal_id="alma:kun:variant:1",
        asset_sha256=asset.sha256,
        object_name=f"{asset.sha256}.png",
        image_kind="official",
        match_status="exact_xlsx",
        source_references=(),
    )
    build = alma.AlmaSnapshotBuild(
        snapshot={"supplier": "alma", "source_hash": "a" * 64, "generated_at": "2026-07-17T00:00:00Z", "items": []},
        assets_by_sha256={asset.sha256: asset},
        bindings=(binding,),
    )

    assert build.bindings == (binding,)
    assert build.assets_by_sha256[asset.sha256] is asset
    assert b"official" not in repr(build.snapshot).encode()


def test_multi_image_gallery_requires_explicit_identity_sha_curation():
    low = ImageAsset(b"low", "image/png", 539, 459, "17c1f97b5574ce29eb8c0767e8ef78fc3101849a40e65c8c9c9059e16fdf0955")
    high = ImageAsset(b"high", "image/png", 574, 690, "6c51076edec7df85ab59c179bd8a9bda9e9149a659f94c685074cde29fe02ae5")
    xxl = _record("xxl", "LOTUS Planter (XXL)", "LOTUS PLANTER")
    xxl["link_description"] = "LOTUS Planter (XXL)\nAluminium"
    xxxl = _record("xxxl", "LOTUS Planter (XXXL)", "LOTUS PLANTER")
    xxxl["link_description"] = "LOTUS Planter (XXXL)\nAluminium"
    unknown = _record("unknown", "TEST Unknown", "TEST")
    unknown["link_description"] = "TEST Unknown"

    assert alma._curated_gallery_assets(xxl, (low, high)) == (low,)
    assert alma._curated_gallery_assets(xxxl, (low, high)) == (high,)
    assert alma._curated_gallery_assets(unknown, (low, high)) == ()


def _link_index(captured_at="2026-07-17"):
    return build_kundesign_link_index(
        {
            "schema_version": 1,
            "captured_at": captured_at,
            "source_url": "https://www.kundesign.com/products",
            "fallback_url": "https://www.kundesign.com/products",
            "provenance": {
                "algorithm": "sha256",
                "source_sha256": "a" * 64,
                "source_product_count": 0,
            },
            "products": [],
            "overrides": [],
        }
    )


def test_alma_source_hash_covers_link_resource_adapter_and_curation_manifests(monkeypatch):
    files = (
        _File("b.xlsx", "spec_guide", "KUN", "b" * 64, MIME, Path("b.xlsx")),
        _File("a.xlsx", "spec_guide", "KUN", "a" * 64, MIME, Path("a.xlsx")),
    )
    index = _link_index()
    original = alma._source_hash(files, index)
    original_family = deepcopy(alma._FAMILY_ASSET_OVERRIDES)
    original_version = alma._ALMA_ADAPTER_VERSION

    monkeypatch.setattr(
        alma,
        "_FAMILY_ASSET_OVERRIDES",
        dict(reversed(tuple(alma._FAMILY_ASSET_OVERRIDES.items()))),
    )
    assert alma._source_hash(tuple(reversed(files)), index) == original

    changed_family = deepcopy(original_family)
    key = next(iter(changed_family))
    path, sheet, cell, sha256 = changed_family[key]
    changed_family[key] = (path, sheet, cell, "f" * 64 if sha256 != "f" * 64 else "e" * 64)
    monkeypatch.setattr(alma, "_FAMILY_ASSET_OVERRIDES", changed_family)
    assert alma._source_hash(files, index) != original

    monkeypatch.setattr(alma, "_FAMILY_ASSET_OVERRIDES", original_family)
    monkeypatch.setattr(alma, "_ALMA_ADAPTER_VERSION", "review-change")
    assert alma._source_hash(files, index) != original
    monkeypatch.setattr(alma, "_ALMA_ADAPTER_VERSION", original_version)
    assert alma._source_hash(files, _link_index("2026-07-18")) != original
    assert alma._source_hash(
        files,
        index,
        mondecasa_image_index=SimpleNamespace(resource_fingerprint="f" * 64),
    ) != original
    changed_multi = deepcopy(alma._MULTI_IMAGE_IDENTITY_CURATIONS)
    multi_key = next(iter(changed_multi))
    multi_sha, reason = changed_multi[multi_key]
    changed_multi[multi_key] = (multi_sha, reason + "-review")
    monkeypatch.setattr(alma, "_MULTI_IMAGE_IDENTITY_CURATIONS", changed_multi)
    assert alma._source_hash(files, index) != original


def test_family_override_requires_expected_sha_and_rejects_replacement():
    expected = _asset("expected")
    replacement = _asset("replacement")

    assert alma._require_family_override_asset((expected,), expected.sha256) is expected
    with pytest.raises(ValueError, match="ALMA_IMAGE_COVERAGE"):
        alma._require_family_override_asset((replacement,), expected.sha256)
    with pytest.raises(ValueError, match="ALMA_IMAGE_COVERAGE"):
        alma._require_family_override_asset((expected, replacement), expected.sha256)


def test_mondecasa_web_assets_fill_only_xlsx_gaps_with_exact_link_evidence():
    image_buffer = BytesIO()
    Image.new("RGB", (16, 12), (40, 100, 160)).save(image_buffer, format="JPEG")
    image_bytes = image_buffer.getvalue()
    original_sha256 = hashlib.sha256(image_bytes).hexdigest()
    exact_url = "https://www.mondecasa.com.sg/all-products/exact-chair"
    model_url = "https://www.mondecasa.com.sg/all-products/model-chair"
    image_url = (
        "https://cdn.prod.website-files.com/66fcf3946613a3f9499ea3b8/model%20chair.jpg"
    )
    link_index = MondecasaLinkIndex(
        "a" * 64,
        (exact_url, model_url),
        MappingProxyType({"EXACT1": (0,), "MODEL1": (1,)}),
        MappingProxyType({}),
    )
    image_index = MondecasaImageIndex(
        "b" * 64,
        MappingProxyType(
            {
                "EXACT1": MondecasaImageResolution(
                    "exact_web",
                    image_bytes,
                    exact_url,
                    image_url,
                    ("EXACT1",),
                    original_sha256,
                    16,
                    12,
                ),
                "MODEL1": MondecasaImageResolution(
                    "model_web",
                    image_bytes,
                    model_url,
                    image_url,
                    ("MODEL1",),
                    original_sha256,
                    16,
                    12,
                ),
            }
        ),
    )
    records = [
        {
            "identity": "embedded",
            "brand": "Mondecasa",
            "source_code": "EXACT1",
            "collection": "TEST",
        },
        {
            "identity": "web-model",
            "brand": "Mondecasa",
            "source_code": "MODEL1",
            "collection": "TEST",
        },
    ]
    embedded = {
        "embedded": alma._AlmaResolvedImage(
            _asset("xlsx"), "exact_xlsx", (), "exact_embedded_raster"
        )
    }

    resolved = alma._mondecasa_web_record_assets(
        records,
        embedded,
        link_index,
        image_index,
    )

    assert set(resolved) == {"web-model"}
    match = resolved["web-model"]
    assert match.match_status == "model_web"
    assert match.asset.media_type == "image/png"
    assert match.selection_reason == "official_mondecasa_model_page_gallery"
    assert match.source_references == (
        {
            "file_id": original_sha256,
            "sheet_or_page": model_url,
            "cell_or_bbox": image_url,
        },
    )


def test_real_2026_sidecar_has_complete_official_coverage_and_links():
    root = Path(".cache/catalog_sources/alma/sharepoint_2026-07-17")
    sources = (
        (alma._KUN_PATH, "KUN", root / "SPEC Guide-Alma-KUN.root.xlsx"),
        (alma._KUN_PRICE_PATH, "KUN", root / "Spec guide-Alma-KUN Design.current.xlsx"),
        (alma._MONDECASA_PATH, "Mondecasa", root / "SPEC Guide-Alma-Mondecasa.current.xlsx"),
    )
    if any(not local_path.exists() for _, _, local_path in sources):
        pytest.skip("Cache local ALMA 2026 no disponible")
    files = tuple(
        _File(
            path,
            "spec_guide",
            brand,
            hashlib.sha256(local_path.read_bytes()).hexdigest(),
            MIME,
            local_path,
        )
        for path, brand, local_path in sources
    )

    build = alma.build_alma_snapshot_with_assets(files)
    validated = catalog_service._validate_snapshot(
        build.snapshot, expected_supplier="alma"
    )
    service_metrics = catalog_service._alma_asset_metrics(validated, build)
    kun = [item for item in build.snapshot["items"] if item["brand"] == "KUN"]
    mondecasa = [item for item in build.snapshot["items"] if item["brand"] == "Mondecasa"]
    bindings = {binding.internal_id: binding for binding in build.bindings}
    kun_bindings = {item["internal_id"]: bindings[item["internal_id"]] for item in kun}
    mondecasa_bindings = {
        item["internal_id"]: bindings[item["internal_id"]]
        for item in mondecasa
        if item["internal_id"] in bindings
    }

    assert len(kun) == len(kun_bindings) == 310
    assert len(mondecasa) == 344
    assert len(mondecasa_bindings) == 310
    assert len(bindings) == 620
    assert service_metrics["official_images_planned"] == 620
    assert service_metrics["unique_assets_planned"] == 439
    assert json.dumps(build.snapshot)
    assert all(item["image_url"] == "" and item["image_kind"] == "official" for item in kun)
    assert sum(item["image_kind"] == "official" for item in mondecasa) == 310
    assert sum(item["image_kind"] == "placeholder" for item in mondecasa) == 34
    assert all(item["product_url"].startswith("https://www.kundesign.com/") for item in kun)
    assert all(item["product_url"].startswith("https://www.mondecasa.com") for item in mondecasa)
    assert all(binding.asset_sha256 in build.assets_by_sha256 for binding in bindings.values())
    assert all(
        build.assets_by_sha256[binding.asset_sha256].sha256 == binding.asset_sha256
        and binding.object_name == f"{binding.asset_sha256}.png"
        for binding in bindings.values()
    )
    assert sum(binding.match_status == "exact_xlsx" for binding in bindings.values()) > 0
    assert sum(binding.match_status == "merged_xlsx" for binding in bindings.values()) > 0
    assert sum(binding.match_status == "family_xlsx" for binding in bindings.values()) > 0
    assert sum(binding.match_status == "exact_web" for binding in bindings.values()) == 45
    assert sum(binding.match_status == "model_web" for binding in bindings.values()) == 21
    pavilion_ids = {
        item["internal_id"] for item in kun if item["collection"] == "PAVILION"
    }
    assert len(pavilion_ids) == 3
    assert {bindings[item_id].match_status for item_id in pavilion_ids} == {"exact_xlsx"}
    assert {
        item["attributes"]["product_url_match"]["status"]
        for item in kun
    } == {"exact_index", "curated_override", "catalog_fallback"}
    assert all(
        item["attributes"]["approved_asset"] == {
            "bucket": "catalog-assets",
            "path": f"{bindings[item['internal_id']].asset_sha256}.png",
            "image_kind": "official",
            "label": "Imagen oficial del XLSX ALMA",
            "approved": True,
        }
        for item in kun
    )
    model_web_items = [
        item
        for item in mondecasa
        if item.get("attributes", {}).get("image_match", {}).get("status") == "model_web"
    ]
    assert len(model_web_items) == 21
    assert all(
        item["attributes"]["approved_asset"]["label"]
        == "Imagen oficial del modelo Mondecasa"
        and any("acabado mostrado puede variar" in warning for warning in item["warnings"])
        for item in model_web_items
    )
    print(
        "ALMA_TASK2_METRICS",
        {
            "bindings": len(bindings),
            "mondecasa_bindings": len(mondecasa_bindings),
            "unique_assets": len(build.assets_by_sha256),
            "image_statuses": dict(Counter(binding.match_status for binding in bindings.values())),
            "link_statuses": dict(
                Counter(item["attributes"]["product_url_match"]["status"] for item in kun)
            ),
        },
    )
