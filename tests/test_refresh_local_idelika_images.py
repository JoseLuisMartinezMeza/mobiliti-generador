from __future__ import annotations

import hashlib
import json
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, PngImagePlugin

from scripts.refresh_local_idelika_images import (
    AuditedPdfCandidate,
    ResolvedProductImage,
    ShopCandidate,
    _download_official_image,
    load_audited_image_overrides,
    merge_idelika_visuals,
    prepare_official_product_asset,
    select_shop_candidate,
)


def _candidate(name: str, template_id: int) -> ShopCandidate:
    return ShopCandidate(
        name=name,
        product_url=f"https://idelika.com/shop/product-{template_id}",
        image_url=(
            f"https://idelika.com/web/image/product.template/{template_id}/"
            f"image_1024/product-{template_id}"
        ),
        template_id=template_id,
    )


def test_jalisco_match_prefers_the_official_variant_and_never_crosses_product_type():
    candidates = (
        _candidate(
            "Jalisco banco counter aluminio negro cuerda negra // Exterior 100%",
            10,
        ),
        _candidate(
            "Jalisco silla metal negro cuerda beige // Exterior techado",
            11,
        ),
        _candidate(
            "Jalisco silla estructura en aluminio cuerda textilene negra // Exterior 100%",
            12,
        ),
    )

    exterior = select_shop_candidate(
        "Jalisco silla",
        variant="aluminio 100%",
        description="69*59*79 exterior Cojines se venden por separado",
        candidates=candidates,
    )
    techado = select_shop_candidate(
        "Jalisco silla",
        variant="exterior techado",
        description="69*59*79 exterior Cojines se venden por separado",
        candidates=candidates,
    )

    assert exterior is not None and exterior.template_id == 12
    assert techado is not None and techado.template_id == 11


def test_catalog_aliases_match_the_same_model_without_accepting_an_unrelated_result():
    assert (
        select_shop_candidate(
            "Tulum sofá cama",
            variant="",
            description="230*115*80",
            candidates=(
                _candidate("Tulum sofacama // Interior", 21),
                _candidate("Tulum mesa de centro // Interior", 22),
            ),
        ).template_id
        == 21
    )
    assert select_shop_candidate(
        "Morelia pantalla",
        variant="",
        description="Fibras naturales 50 diámetro",
        candidates=(_candidate("Morelia silla beige lisa // Exterior techado", 23),),
    ) is None


def test_variant_and_capacity_conflicts_are_rejected_before_scoring():
    candidates = (
        _candidate("Chacala camastro para 2 personas // Exterior 100%", 31),
        _candidate("Chacala camastro individual // Exterior 100%", 32),
    )

    individual = select_shop_candidate(
        "Chacala Camastro 1 Plaza",
        variant="",
        description="168*70*90",
        candidates=candidates,
    )
    assert individual is not None and individual.template_id == 32

    assert select_shop_candidate(
        "Chetumal banco",
        variant="exterior techado",
        description="",
        candidates=(_candidate("Chetumal banco // Exterior 100%", 33),),
    ) is None


def test_official_product_cleanup_keeps_the_complete_object_and_removes_white_background():
    source = Image.new("RGB", (320, 320), "white")
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((70, 55, 250, 185), radius=24, fill=(32, 42, 52))
    draw.line((88, 176, 58, 282), fill=(32, 42, 52), width=12)
    draw.line((232, 176, 262, 282), fill=(32, 42, 52), width=12)
    stream = BytesIO()
    source.save(stream, "PNG")

    asset = prepare_official_product_asset(stream.getvalue(), "image/png", min_size=320)

    with Image.open(BytesIO(asset.data)) as result:
        rgba = result.convert("RGBA")
        alpha = rgba.getchannel("A")
        bbox = alpha.getbbox()
        assert bbox is not None
        assert rgba.getpixel((0, 0))[3] == 0
        assert bbox[1] < rgba.height * 0.15
        assert bbox[3] > rgba.height * 0.85
        assert rgba.width >= 320 and rgba.height >= 320


def test_official_product_cleanup_preserves_a_white_surface_inside_a_closed_frame():
    source = Image.new("RGB", (800, 400), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((22, 22, 778, 378), outline=(150, 155, 158), width=18)
    stream = BytesIO()
    source.save(stream, "PNG")

    asset = prepare_official_product_asset(stream.getvalue(), "image/png", min_size=400)

    with Image.open(BytesIO(asset.data)) as result:
        rgba = result.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        assert rgba.getpixel((rgba.width // 2, rgba.height // 2))[3] >= 245


def test_official_product_cleanup_ignores_bounded_compressed_png_text_metadata():
    source = Image.new("RGB", (320, 320), "white")
    ImageDraw.Draw(source).rectangle((65, 55, 255, 275), fill=(42, 52, 62))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("official-description", "x" * 9_000_000, zip=True)
    stream = BytesIO()
    source.save(stream, "PNG", pnginfo=metadata)

    asset = prepare_official_product_asset(stream.getvalue(), "image/png", min_size=320)

    assert asset.width >= 320
    assert asset.height >= 320


def test_official_download_accepts_the_webp_served_by_idelika(tmp_path):
    source = Image.new("RGB", (32, 32), "white")
    stream = BytesIO()
    source.save(stream, "WEBP")

    class Response:
        url = "https://idelika.com/web/image/product.template/41/image_1024/product"
        headers = {
            "content-type": "image/webp",
            "content-length": str(len(stream.getvalue())),
        }

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            assert chunk_size > 0
            yield stream.getvalue()

    class Session:
        @staticmethod
        def get(url, timeout, stream):
            assert url.startswith("https://idelika.com/")
            assert timeout and stream is True
            return Response()

    data, content_type = _download_official_image(
        Session(),
        _candidate("Jalisco silla", 41),
        tmp_path,
    )

    assert data == stream.getvalue()
    assert content_type == "image/webp"


def test_unresolved_items_lose_the_wrong_asset_instead_of_republishing_it():
    active = {
        "catalog_published_snapshots": {
            "idelika": {
                "payload": {
                    "items": [
                        {
                            "internal_id": "idelika:jalisco:1",
                            "name": "Jalisco silla",
                            "image_kind": "official",
                            "image_url": "",
                            "product_url": "https://graph.microsoft.com/wrong",
                            "attributes": {
                                "approved_asset": {
                                    "bucket": "catalog-assets",
                                    "path": f"{'a' * 64}.png",
                                    "image_kind": "official",
                                    "approved": True,
                                },
                                "image_match": {"status": "exact_pdf"},
                            },
                        },
                        {
                            "internal_id": "idelika:morelia-pantalla:1",
                            "name": "Morelia pantalla",
                            "image_kind": "official",
                            "image_url": "",
                            "product_url": "https://graph.microsoft.com/wrong",
                            "attributes": {
                                "approved_asset": {
                                    "bucket": "catalog-assets",
                                    "path": f"{'b' * 64}.png",
                                    "image_kind": "official",
                                    "approved": True,
                                },
                                "image_match": {"status": "exact_pdf"},
                            },
                        },
                    ]
                }
            }
        }
    }
    resolved = {
        "idelika:jalisco:1": ResolvedProductImage(
            candidate=_candidate("Jalisco silla aluminio // Exterior techado", 31),
            asset_sha256="c" * 64,
            width=900,
            height=900,
        )
    }

    merged, report = merge_idelika_visuals(active, resolved)
    items = merged["catalog_published_snapshots"]["idelika"]["payload"]["items"]

    assert items[0]["attributes"]["approved_asset"]["path"] == f"{'c' * 64}.png"
    assert items[0]["attributes"]["image_match"]["status"] == "exact_web"
    assert items[0]["product_url"] == resolved["idelika:jalisco:1"].candidate.product_url
    assert "approved_asset" not in items[1]["attributes"]
    assert "image_match" not in items[1]["attributes"]
    assert items[1]["image_kind"] == "placeholder"
    assert items[1]["product_url"] == ""
    assert items[1]["attributes"]["image_review_status"] == "needs_review"
    assert report == {"resolved": 1, "needs_review": 1, "items": 2}


def test_pdf_audit_keeps_provenance_without_publishing_a_fake_shop_link():
    active = {
        "catalog_published_snapshots": {
            "idelika": {
                "payload": {
                    "items": [
                        {
                            "internal_id": "idelika:morelia:1",
                            "name": "Morelia pantalla",
                            "product_url": "https://idelika.com/shop/wrong",
                            "attributes": {},
                        }
                    ]
                }
            }
        }
    }
    candidate = AuditedPdfCandidate(
        name="Morelia pantalla",
        source_file="1 CATALOGO FABRICACION 2026B.pdf",
        page=4,
        xref=51,
        crop=(0.62, 0.0, 0.89, 0.31),
        edit_method="strict_background_extraction",
    )

    merged, report = merge_idelika_visuals(
        active,
        {
            "idelika:morelia:1": ResolvedProductImage(
                candidate=candidate,
                asset_sha256="d" * 64,
                width=900,
                height=900,
            )
        },
    )
    item = merged["catalog_published_snapshots"]["idelika"]["payload"]["items"][0]

    assert item["product_url"] == ""
    assert item["attributes"]["image_match"]["status"] == "exact_pdf_visual_audit"
    assert item["attributes"]["image_reference"]["catalog_file"].endswith("2026B.pdf")
    assert "source_image_url" not in item["attributes"]
    assert report == {"resolved": 1, "needs_review": 0, "items": 1}


def test_audited_manifest_validates_web_sources_and_transparent_pdf_assets(tmp_path):
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    source = Image.new("RGBA", (320, 320), (255, 255, 255, 0))
    ImageDraw.Draw(source).rectangle((60, 50, 260, 280), fill=(60, 70, 80, 255))
    stream = BytesIO()
    source.save(stream, "PNG")
    asset_path = asset_dir / "morelia.png"
    asset_path.write_bytes(stream.getvalue())
    manifest = {
        "version": 1,
        "catalog": "idelika",
        "entries": [
            {
                "internal_ids": ["idelika:web:1"],
                "source": {
                    "kind": "official_web",
                    "product": "Jalisco silla",
                    "product_url": "https://idelika.com/shop/jalisco-41",
                    "image_url": (
                        "https://idelika.com/web/image/product.template/41/"
                        "image_1024/Jalisco"
                    ),
                    "template_id": 41,
                },
            },
            {
                "internal_ids": ["idelika:pdf:1"],
                "source": {
                    "kind": "official_pdf",
                    "product": "Morelia pantalla",
                    "file": "1 CATALOGO FABRICACION 2026B.pdf",
                    "page": 4,
                    "xref": 51,
                    "crop": [0.62, 0, 0.89, 0.31],
                },
                "asset": "assets/morelia.png",
                "asset_sha256": hashlib.sha256(asset_path.read_bytes()).hexdigest(),
                "edit_method": "strict_background_extraction",
            },
            {
                "internal_ids": ["idelika:web-asset:1"],
                "source": {
                    "kind": "official_web_asset",
                    "product": "Pintarrón interactivo",
                    "product_url": "https://idelika.com/shop/pintarron-42",
                    "image_url": (
                        "https://idelika.com/web/image/product.template/42/"
                        "image_1024/Pintarron"
                    ),
                    "template_id": 42,
                },
                "asset": "assets/morelia.png",
                "asset_sha256": hashlib.sha256(asset_path.read_bytes()).hexdigest(),
                "edit_method": "strict_background_extraction",
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    web, pdf, report = load_audited_image_overrides(
        manifest_path,
        valid_ids={"idelika:web:1", "idelika:pdf:1", "idelika:web-asset:1"},
    )

    assert web["idelika:web:1"].template_id == 41
    assert pdf["idelika:pdf:1"][0].page == 4
    assert pdf["idelika:pdf:1"][1].width == 320
    assert pdf["idelika:web-asset:1"][0].template_id == 42
    assert report == {
        "audited_manifest_entries": 3,
        "audited_web_overrides": 1,
        "audited_web_asset_overrides": 1,
        "audited_pdf_overrides": 1,
    }


def test_audited_manifest_rejects_assets_outside_its_directory(tmp_path):
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    outside = tmp_path / "outside-idelika-audit.png"
    source = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    stream = BytesIO()
    source.save(stream, "PNG")
    outside.write_bytes(stream.getvalue())
    manifest = {
        "version": 1,
        "catalog": "idelika",
        "entries": [
            {
                "internal_ids": ["idelika:pdf:1"],
                "source": {
                    "kind": "official_pdf",
                    "product": "Morelia pantalla",
                    "file": "catalog.pdf",
                    "page": 1,
                    "xref": 1,
                },
                "asset": "../outside-idelika-audit.png",
                "asset_sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                "edit_method": "strict_background_extraction",
            }
        ],
    }
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="fuera del manifiesto"):
        load_audited_image_overrides(
            manifest_path,
            valid_ids={"idelika:pdf:1"},
        )
