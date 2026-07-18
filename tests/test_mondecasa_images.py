from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
import pytest

from mobiliti_saas.worker.catalog_sync.mondecasa_images import (
    MondecasaImageResourceError,
    build_mondecasa_image_index,
    load_mondecasa_image_index,
    resolve_mondecasa_image,
)


def _jpeg(*, size: tuple[int, int] = (16, 12), color=(20, 90, 140)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG", quality=90)
    return output.getvalue()


def _archive(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def _fixture() -> tuple[dict, bytes, bytes]:
    image = _jpeg()
    sha256 = hashlib.sha256(image).hexdigest()
    archive_name = f"images/{sha256}.jpg"
    manifest = {
        "schema_version": "1.0",
        "artifact_kind": "official_product_image_archive",
        "generated_on": "2026-07-17",
        "official_sources": {
            "product_page_origin": "https://www.mondecasa.com.sg",
            "image_origin": "https://cdn.prod.website-files.com",
        },
        "provenance": {
            "selection": "Manual review of the official product gallery.",
            "network": "All redirects resolved before packaging; runtime is offline.",
        },
        "counts": {
            "pages": 1,
            "assignments": 1,
            "unique_assets": 1,
            "total_asset_bytes": len(image),
        },
        "pages": [
            {
                "page_url": "https://www.mondecasa.com.sg/all-products/ibiza-dining-armchair",
                "gallery_image_url": (
                    "https://cdn.prod.website-files.com/66fcf3946613a3f9499ea3b8/gallery%20image.jpg"
                ),
                "chosen_image_url": (
                    "https://cdn.prod.website-files.com/66fcf3946613a3f9499ea3b8/chosen-image.jpg"
                ),
                "reference_numbers": ["AC2001N04ROP"],
                "assignments": [
                    {"source_code": "AC2001N04ROP", "match_status": "exact_web"},
                ],
                "asset": {
                    "archive_name": archive_name,
                    "sha256": sha256,
                    "size_bytes": len(image),
                    "media_type": "image/jpeg",
                    "width": 16,
                    "height": 12,
                },
            }
        ],
    }
    return manifest, _archive({archive_name: image}), image


def test_build_index_resolves_normalized_codes_with_immutable_bytes_and_metadata():
    manifest, archive, image = _fixture()

    index = build_mondecasa_image_index(manifest, archive)
    exact = resolve_mondecasa_image(" ac-2001-n04-rop ", index)

    assert len(index.resource_fingerprint) == 64
    assert exact is not None
    assert exact.match_status == "exact_web"
    assert exact.asset_bytes == image
    assert exact.page_url.endswith("/ibiza-dining-armchair")
    assert exact.image_url.endswith("/chosen-image.jpg")
    assert exact.reference_numbers == ("AC2001N04ROP",)
    assert exact.original_sha256 == hashlib.sha256(image).hexdigest()
    assert (exact.width, exact.height) == (16, 12)
    assert resolve_mondecasa_image("UNKNOWN", index) is None
    with pytest.raises(TypeError):
        index.resolutions_by_code["ATTACK"] = exact


def test_load_index_reads_only_the_offline_manifest_and_archive(tmp_path: Path):
    manifest, archive, _ = _fixture()
    manifest_path = tmp_path / "mondecasa-images.json"
    archive_path = tmp_path / "mondecasa-images.zip"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive_path.write_bytes(archive)

    first = load_mondecasa_image_index(manifest_path, archive_path)
    second = load_mondecasa_image_index(manifest_path, archive_path)

    assert first.resource_fingerprint == second.resource_fingerprint
    assert resolve_mondecasa_image("AC2001N04ROP", first).asset_bytes


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("root", "unexpected"),
        ("page", "unexpected"),
        ("assignment", "unexpected"),
        ("asset", "unexpected"),
    ],
)
def test_closed_schema_rejects_unexpected_fields(location: str, field: str):
    manifest, archive, _ = _fixture()
    target = {
        "root": manifest,
        "page": manifest["pages"][0],
        "assignment": manifest["pages"][0]["assignments"][0],
        "asset": manifest["pages"][0]["asset"],
    }[location]
    target[field] = "not allowed"

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_"):
        build_mondecasa_image_index(manifest, archive)


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("page_url", "https://attacker.invalid/all-products/ibiza-dining-armchair"),
        ("page_url", "https://www.mondecasa.com.sg/collections/ibiza"),
        (
            "gallery_image_url",
            "https://attacker.invalid/site-id/gallery-image.jpg",
        ),
        (
            "chosen_image_url",
            "https://cdn.prod.website-files.com/site-id/chosen-image.jpg?redirect=1",
        ),
    ],
)
def test_urls_are_canonical_https_final_urls_on_the_two_official_hosts(field: str, url: str):
    manifest, archive, _ = _fixture()
    manifest["pages"][0][field] = url

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_URL"):
        build_mondecasa_image_index(manifest, archive)


def test_references_and_assignments_must_be_normalized_and_globally_unambiguous():
    manifest, archive, _ = _fixture()
    manifest["pages"][0]["assignments"][0]["source_code"] = "AC-2001-N04-ROP"
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSIGNMENT"):
        build_mondecasa_image_index(manifest, archive)

    manifest, archive, _ = _fixture()
    duplicate_page = deepcopy(manifest["pages"][0])
    duplicate_page["page_url"] = "https://www.mondecasa.com.sg/all-products/another-product"
    manifest["pages"].append(duplicate_page)
    manifest["counts"]["pages"] = 2
    manifest["counts"]["assignments"] = 2
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_(REFERENCE|ASSIGNMENT)"):
        build_mondecasa_image_index(manifest, archive)


def test_assignments_must_belong_to_the_page_reference_numbers():
    manifest, archive, _ = _fixture()
    manifest["pages"][0]["assignments"][0]["source_code"] = "AC2001N04ALU"

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSIGNMENT"):
        build_mondecasa_image_index(manifest, archive)


def test_page_assignments_share_status_and_only_exact_web_requires_one_code():
    manifest, archive, _ = _fixture()
    manifest["pages"][0]["assignments"][0]["match_status"] = "model_web"
    index = build_mondecasa_image_index(manifest, archive)
    assert resolve_mondecasa_image("AC2001N04ROP", index).match_status == "model_web"

    manifest, archive, _ = _fixture()
    page = manifest["pages"][0]
    page["reference_numbers"].append("AC2001N04TEK")
    page["assignments"] = [
        {"source_code": "AC2001N04ROP", "match_status": "model_web"},
        {"source_code": "AC2001N04TEK", "match_status": "model_web"},
    ]
    manifest["counts"]["assignments"] = 2
    index = build_mondecasa_image_index(manifest, archive)
    assert resolve_mondecasa_image("AC2001N04ROP", index).match_status == "model_web"
    assert resolve_mondecasa_image("AC2001N04TEK", index).match_status == "model_web"

    page["assignments"][0]["match_status"] = "exact_web"
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSIGNMENT"):
        build_mondecasa_image_index(manifest, archive)

    page["assignments"][1]["match_status"] = "exact_web"
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSIGNMENT"):
        build_mondecasa_image_index(manifest, archive)


def test_cdn_images_must_stay_under_the_pinned_mondecasa_site_path():
    manifest, archive, _ = _fixture()
    manifest["pages"][0]["chosen_image_url"] = (
        "https://cdn.prod.website-files.com/another-site/chosen-image.jpg"
    )

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_URL"):
        build_mondecasa_image_index(manifest, archive)


@pytest.mark.parametrize("escape", ["%2F", "%2f", "%2520", "%00", "%41"])
def test_cdn_paths_only_allow_canonical_percent_20(escape: str):
    manifest, archive, _ = _fixture()
    manifest["pages"][0]["chosen_image_url"] = (
        "https://cdn.prod.website-files.com/66fcf3946613a3f9499ea3b8/"
        f"chosen{escape}image.jpg"
    )

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_URL"):
        build_mondecasa_image_index(manifest, archive)


@pytest.mark.parametrize(
    "mutation",
    [
        "sha256",
        "size_bytes",
        "dimensions",
        "media_type",
        "archive_name",
    ],
)
def test_asset_metadata_must_match_the_exact_safe_jpeg_in_the_archive(mutation: str):
    manifest, archive, _ = _fixture()
    asset = manifest["pages"][0]["asset"]
    if mutation == "sha256":
        asset["sha256"] = "0" * 64
        asset["archive_name"] = f"images/{'0' * 64}.jpg"
    elif mutation == "size_bytes":
        asset["size_bytes"] += 1
        manifest["counts"]["total_asset_bytes"] += 1
    elif mutation == "dimensions":
        asset["width"] += 1
    elif mutation == "media_type":
        asset["media_type"] = "image/png"
    else:
        asset["archive_name"] = f"../images/{asset['sha256']}.jpg"

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_(ASSET|ZIP|JPEG)"):
        build_mondecasa_image_index(manifest, archive)


def test_archive_rejects_extra_members_non_jpeg_payloads_and_oversized_input():
    manifest, archive, _ = _fixture()
    asset_name = manifest["pages"][0]["asset"]["archive_name"]
    with ZipFile(BytesIO(archive)) as source:
        image = source.read(asset_name)

    extra = _archive({asset_name: image, "images/unexpected.jpg": image})
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ZIP"):
        build_mondecasa_image_index(manifest, extra)

    fake = b"not a jpeg"
    fake_sha = hashlib.sha256(fake).hexdigest()
    page_asset = manifest["pages"][0]["asset"]
    page_asset.update(
        archive_name=f"images/{fake_sha}.jpg",
        sha256=fake_sha,
        size_bytes=len(fake),
    )
    manifest["counts"]["total_asset_bytes"] = len(fake)
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_JPEG"):
        build_mondecasa_image_index(
            manifest,
            _archive({page_asset["archive_name"]: fake}),
        )

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ZIP_LIMIT"):
        build_mondecasa_image_index(_fixture()[0], b"0" * (20 * 1024 * 1024 + 1))


def test_counts_page_limit_asset_limit_and_match_status_fail_closed():
    manifest, archive, _ = _fixture()
    manifest["counts"]["assignments"] += 1
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_COUNTS"):
        build_mondecasa_image_index(manifest, archive)

    manifest, archive, _ = _fixture()
    manifest["pages"][0]["asset"]["size_bytes"] = 1024 * 1024 + 1
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSET"):
        build_mondecasa_image_index(manifest, archive)

    manifest, archive, _ = _fixture()
    manifest["pages"][0]["asset"]["width"] = 8193
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSET"):
        build_mondecasa_image_index(manifest, archive)

    manifest, archive, _ = _fixture()
    manifest["pages"][0]["assignments"][0]["match_status"] = "family_web"
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_ASSIGNMENT"):
        build_mondecasa_image_index(manifest, archive)

    manifest, archive, _ = _fixture()
    manifest["pages"] = [deepcopy(manifest["pages"][0]) for _ in range(61)]
    manifest["counts"]["pages"] = 61
    manifest["counts"]["assignments"] = 61
    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_PAGE_LIMIT"):
        build_mondecasa_image_index(manifest, archive)


def test_file_loader_rejects_duplicate_json_keys(tmp_path: Path):
    manifest, archive, _ = _fixture()
    text = json.dumps(manifest)
    duplicate = text.replace('{"schema_version": "1.0",', '{"schema_version": "1.0", "schema_version": "1.0",', 1)
    manifest_path = tmp_path / "duplicate.json"
    archive_path = tmp_path / "images.zip"
    manifest_path.write_text(duplicate, encoding="utf-8")
    archive_path.write_bytes(archive)

    with pytest.raises(MondecasaImageResourceError, match="MONDECASA_IMAGE_MANIFEST"):
        load_mondecasa_image_index(manifest_path, archive_path)
