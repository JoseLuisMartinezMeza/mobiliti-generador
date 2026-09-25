import hashlib
import io
import json

import pytest
from PIL import Image, ImageDraw

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


def _png_asset(directory, *, bbox=(256, 256, 767, 767), size=(1024, 1024), suffix="png"):
    image = Image.new("RGBA", size, (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle(bbox, fill=(31, 91, 160, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return _asset(directory, buffer.getvalue(), suffix=suffix)


def _transparent_white_png_asset(directory, *, bbox=(256, 256, 767, 767)):
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(bbox, fill=(255, 255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return _asset(directory, buffer.getvalue())


def _v2_fixture(tmp_path):
    assets = tmp_path / "catalog-assets"
    assets.mkdir(parents=True)
    official_asset = _png_asset(assets)
    pdf_asset = _png_asset(assets, bbox=(270, 270, 753, 753))
    generated_asset = _png_asset(assets, bbox=(300, 300, 723, 723))
    shared_asset = _png_asset(assets, bbox=(280, 280, 743, 743))
    active = {
        "catalog_published_snapshots": {
            "labenze": {
                "id": "snapshot-labenze",
                "source_hash": "source-labenze",
                "payload": {
                    "items": [
                        {"internal_id": "labenze:individual", "name": "Silla Exacta", "price": 125,
                         "attributes": {"commercial_note": "sin cambios", "operation_code": "OP-1"}},
                        {"internal_id": "labenze:pdf", "name": "Mesa PDF", "price": 275,
                         "attributes": {"commercial_note": "sin cambios PDF", "operation_code": "OP-2"}},
                        {"internal_id": "labenze:generated", "name": "Banco Referencia", "price": 90,
                         "attributes": {"commercial_note": "sin cambios generado", "operation_code": "OP-3"}},
                        {"internal_id": "labenze:variant-a", "name": "Silla Serie A roja", "price": 130,
                         "attributes": {"commercial_note": "variante A", "operation_code": "OP-4"}},
                        {"internal_id": "labenze:variant-b", "name": "Silla Serie A azul", "price": 131,
                         "attributes": {"commercial_note": "variante B", "operation_code": "OP-5"}},
                    ]
                },
            }
        },
        "quote_jobs": [{"id": "operativo-preservado", "state": "ready"}],
    }
    source_hash = "a" * 64
    base_reference = {
        "generated": False,
        "source_kind": "manufacturer_official",
        "image_source_url": "https://media.labenze.example/images/silla-exacta.png",
        "source_locator": "SKU LAB-001",
        "source_dimensions": {"width": 512, "height": 512},
        "reviewer": "visual.reviewer@mobiliti.mx",
        "reviewed_at": "2026-08-18T12:00:00Z",
        "full_product_visible": True,
        "not_cropped": True,
        "configuration_supported": True,
        "approved": True,
    }
    manifest = {
        "schema_version": 2,
        "supplier": "labenze",
        "expected_snapshot_id": "snapshot-labenze",
        "expected_source_hash": "source-labenze",
        "decisions": [
            {
                "internal_id": "labenze:individual", "name": "Silla Exacta", "decision": "retain",
                "asset": pdf_asset, "image_kind": "official", "direct_product_reference": True,
                "reason": "Página individual oficial con SKU exacto.",
                "product_url": "https://www.labenze.example/productos/silla-exacta-lab-001",
                "image_reference": base_reference,
            },
            {
                "internal_id": "labenze:pdf", "name": "Mesa PDF", "decision": "retain",
                "asset": official_asset, "image_kind": "official", "direct_product_reference": True,
                "reason": "Ficha PDF oficial con código exacto.",
                "product_url": "https://www.labenze.example/productos/mesa-pdf-mesa-44",
                "image_reference": {
                    **base_reference,
                    "source_kind": "catalog_pdf",
                    "image_source_url": "https://sharepoint.example/sites/catalogos/mesa.pdf#page=4",
                    "source_locator": "Página 4, código MESA-44",
                },
            },
            {
                "internal_id": "labenze:generated", "name": "Banco Referencia", "decision": "replace",
                "asset": generated_asset, "image_kind": "generated_reference", "direct_product_reference": True,
                "reason": "Sin fotografía aislada tras búsqueda exacta.",
                "product_url": "https://www.labenze.example/productos/banco-referencia",
                "image_reference": {
                    **base_reference,
                    "generated": True,
                    "source_kind": "manufacturer_official",
                    "image_source_url": "https://media.labenze.example/images/banco-referencia.png",
                    "exact_search": {"exhausted": True, "queries": ["Labenze Banco Referencia"]},
                    "generation": {
                        "prompt": "Banco Referencia LAB-090 aislado, vista frontal, fondo blanco.",
                        "model": "gpt-image-1",
                        "references": [{"url": "https://www.labenze.example/productos/banco-referencia", "sha256": source_hash}],
                    },
                },
            },
            *[
                {
                    "internal_id": internal_id, "name": name, "decision": "retain", "asset": shared_asset,
                    "image_kind": "official", "direct_product_reference": True,
                    "reason": "El fabricante asigna la misma imagen a ambas variantes.",
                    "product_url": f"https://www.labenze.example/productos/{internal_id.rsplit(':', 1)[1]}",
                    "shared_visual_group": "serie-a",
                    "image_reference": {
                        **base_reference,
                        "image_source_url": "https://media.labenze.example/images/serie-a.png",
                        "source_locator": "Serie A, variantes roja y azul",
                        "shared_visual_evidence": {
                            "source_url": "https://www.labenze.example/series/serie-a",
                            "assigned_variant_ids": ["labenze:variant-a", "labenze:variant-b"],
                        },
                    },
                }
                for internal_id, name in [
                    ("labenze:variant-a", "Silla Serie A roja"),
                    ("labenze:variant-b", "Silla Serie A azul"),
                ]
            ],
        ],
        "shared_visual_equivalence_matrix": {
            "serie-a": {
                "variant_internal_ids": ["labenze:variant-a", "labenze:variant-b"],
                "same_source_url": "https://www.labenze.example/series/serie-a",
                "evidence": "La página de la serie asigna la misma foto a ambas variantes.",
            }
        },
    }
    active_path = tmp_path / "active-v2.json"
    manifest_path = tmp_path / "manifest-v2.json"
    active_path.write_text(json.dumps(active, ensure_ascii=False), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return assets, active, manifest, active_path, manifest_path


def _build_v2(tmp_path, manifest=None, assets=None, active_path=None):
    if manifest is None:
        assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest_path = tmp_path / "manifest-run.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return build_verified_catalog_images(
        active_db_path=active_path,
        manifest_path=manifest_path,
        assets_dir=assets,
        output_path=tmp_path / "verified-v2.json",
    )


def test_v2_builds_auditable_exact_pdf_generated_and_shared_visual_references(tmp_path):
    assets, active, manifest, active_path, _ = _v2_fixture(tmp_path)
    report = _build_v2(tmp_path, manifest, assets, active_path)

    verified = json.loads((tmp_path / "verified-v2.json").read_text(encoding="utf-8"))
    items = {item["internal_id"]: item for item in verified["catalog_published_snapshots"]["labenze"]["payload"]["items"]}
    assert verified["quote_jobs"] == active["quote_jobs"]
    assert items["labenze:individual"]["product_url"] == manifest["decisions"][0]["product_url"]
    assert items["labenze:individual"]["attributes"]["image_reference"]["image_source_url"] != items["labenze:individual"]["product_url"]
    assert items["labenze:pdf"]["attributes"]["image_reference"]["image_source_url"].endswith("mesa.pdf#page=4")
    assert items["labenze:pdf"]["attributes"]["image_reference"]["source_locator"] == "Página 4, código MESA-44"
    assert items["labenze:generated"]["attributes"]["image_reference"]["generation"]["model"] == "gpt-image-1"
    assert items["labenze:generated"]["image_kind"] == "generated_reference"
    assert 0.12 <= items["labenze:individual"]["attributes"]["image_reference"]["asset_quality"]["occupancy"] <= 0.80
    assert items["labenze:variant-a"]["attributes"]["commercial_note"] == "variante A"
    assert report["items"] == 5


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest["decisions"][0].update(reason="placeholder"), "placeholder"),
        (lambda manifest: manifest["decisions"][0]["image_reference"].pop("reviewer"), "reviewer"),
        (lambda manifest: manifest["decisions"][0]["image_reference"].update(approved=False), "approved"),
    ],
)
def test_v2_rejects_placeholder_or_incomplete_review(tmp_path, mutate, message):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    mutate(manifest)
    with pytest.raises(ValueError, match=message):
        _build_v2(tmp_path, manifest, assets, active_path)


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://cdn.labenze.example/images/silla.png",
        "https://www.labenze.example/",
        "https://www.labenze.example/buscar?q=silla",
        "https://www.labenze.example/familia/sillas",
        "https://media.labenze.example/images/silla-exacta.png",
    ],
)
def test_v2_rejects_non_product_or_image_source_product_url(tmp_path, bad_url):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][0]["product_url"] = bad_url
    with pytest.raises(ValueError, match="product_url"):
        _build_v2(tmp_path, manifest, assets, active_path)


def test_v2_rejects_unknown_source_kind_and_incomplete_generated_trace(tmp_path):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][0]["image_reference"]["source_kind"] = "social_media"
    with pytest.raises(ValueError, match="source_kind"):
        _build_v2(tmp_path, manifest, assets, active_path)

    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path / "generated")
    manifest["decisions"][2]["image_reference"]["generation"].pop("prompt")
    with pytest.raises(ValueError, match="prompt"):
        _build_v2(tmp_path / "generated", manifest, assets, active_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda reference: reference["exact_search"].update(exhausted=False), "Búsqueda exacta"),
        (lambda reference: reference["generation"]["references"][0].update(url="http://inseguro.example/ref"), "HTTPS"),
        (lambda reference: reference["generation"]["references"][0].update(sha256="sin-hash"), "Hash"),
    ],
)
def test_v2_rejects_generated_reference_without_exhaustive_hashed_trace(tmp_path, mutate, message):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    mutate(manifest["decisions"][2]["image_reference"])
    with pytest.raises(ValueError, match=message):
        _build_v2(tmp_path, manifest, assets, active_path)


def test_v2_rejects_shared_asset_without_visible_equivalence_evidence(tmp_path):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest.pop("shared_visual_equivalence_matrix")
    with pytest.raises(ValueError, match="shared_visual"):
        _build_v2(tmp_path, manifest, assets, active_path)


def test_v2_rejects_shared_asset_when_source_does_not_assign_every_variant(tmp_path):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][3]["image_reference"]["shared_visual_evidence"]["assigned_variant_ids"] = ["labenze:variant-a"]
    with pytest.raises(ValueError, match="shared_visual"):
        _build_v2(tmp_path, manifest, assets, active_path)


@pytest.mark.parametrize(
    ("bbox", "message"),
    [
        ((10, 256, 767, 767), "margen"),
        ((40, 40, 983, 983), "caja"),
        ((450, 450, 550, 550), "ocupación"),
    ],
)
def test_v2_calculates_and_rejects_invalid_asset_geometry(tmp_path, bbox, message):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    invalid_asset = _png_asset(assets, bbox=bbox)
    manifest["decisions"][0]["asset"] = invalid_asset
    with pytest.raises(ValueError, match=message):
        _build_v2(tmp_path, manifest, assets, active_path)


def test_v2_rejects_deformation_and_dangerous_quality_exceptions(tmp_path):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][0]["image_reference"]["source_dimensions"] = {"width": 600, "height": 400}
    with pytest.raises(ValueError, match="aspecto"):
        _build_v2(tmp_path, manifest, assets, active_path)

    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path / "exception")
    manifest["decisions"][0]["quality_exception"] = {"cropping_allowed": True}
    with pytest.raises(ValueError, match="quality_exception"):
        _build_v2(tmp_path / "exception", manifest, assets, active_path)


@pytest.mark.parametrize("asset_kind", ["bad_name", "wrong_hash", "not_png", "oversized"])
def test_v2_rejects_assets_that_are_not_real_content_addressed_pngs(tmp_path, asset_kind):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    if asset_kind == "bad_name":
        asset = "imagen.png"
    elif asset_kind == "wrong_hash":
        asset = f"{'0' * 64}.png"
        (assets / asset).write_bytes((assets / manifest["decisions"][0]["asset"]).read_bytes())
    elif asset_kind == "not_png":
        asset = _asset(assets, b"no es un PNG", suffix="png")
    else:
        valid = (assets / manifest["decisions"][0]["asset"]).read_bytes()
        asset = _asset(assets, valid + b"x" * (8 * 1024 * 1024), suffix="png")
    manifest["decisions"][0]["asset"] = asset
    with pytest.raises(ValueError):
        _build_v2(tmp_path, manifest, assets, active_path)


def test_v2_rejects_an_asset_over_25_megapixels_before_pixel_analysis(tmp_path):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    image = Image.new("1", (5001, 5001), 1)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    manifest["decisions"][0]["asset"] = _asset(assets, buffer.getvalue(), suffix="png")
    with pytest.raises(ValueError, match="25 Mpx"):
        _build_v2(tmp_path, manifest, assets, active_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda entry: entry.update(status="placeholder"), "placeholder"),
        (lambda entry: entry["image_reference"].update(status="placeholder"), "placeholder"),
        (lambda entry: entry.update(quality_exception={"cropping_allowed": True}), "quality_exception"),
        (lambda entry: entry["image_reference"].update(quality_exception={"edge_contact_allowed": True}), "quality_exception"),
    ],
)
def test_v2_rejects_placeholder_and_quality_exception_at_each_manifest_level(tmp_path, mutate, message):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    mutate(manifest["decisions"][0])
    with pytest.raises(ValueError, match=message):
        _build_v2(tmp_path, manifest, assets, active_path)


def test_v2_accepts_a_white_opaque_product_on_a_transparent_canvas(tmp_path):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][0]["asset"] = _transparent_white_png_asset(assets)

    report = _build_v2(tmp_path, manifest, assets, active_path)

    assert report["status"] == "passed"


@pytest.mark.parametrize(
    ("product_url", "image_source_url"),
    [
        ("https://www.labenze.example/productos/silla-exacta?search=silla", None),
        ("https://www.labenze.example/index.html", None),
        (
            "https://www.labenze.example/productos/silla-exacta?utm_campaign=verano",
            "https://www.labenze.example/productos/silla-exacta?source=imagen",
        ),
    ],
)
def test_v2_rejects_search_landing_and_canonical_image_source_product_urls(tmp_path, product_url, image_source_url):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][0]["product_url"] = product_url
    if image_source_url:
        manifest["decisions"][0]["image_reference"]["image_source_url"] = image_source_url
    with pytest.raises(ValueError, match="product_url"):
        _build_v2(tmp_path, manifest, assets, active_path)


@pytest.mark.parametrize(
    "dimensions",
    [
        {"width": -512, "height": 512},
        {"width": float("nan"), "height": 512},
        {"width": 512, "height": float("inf")},
        {"width": True, "height": 512},
        {"width": 512, "height": False},
        {"width": "512", "height": 512},
        {"width": 512, "height": "512"},
    ],
)
def test_v2_rejects_non_numeric_non_positive_or_non_finite_source_dimensions(tmp_path, dimensions):
    assets, _, manifest, active_path, _ = _v2_fixture(tmp_path)
    manifest["decisions"][0]["image_reference"]["source_dimensions"] = dimensions
    with pytest.raises(ValueError, match="source_dimensions"):
        _build_v2(tmp_path, manifest, assets, active_path)
