from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

import scripts.build_offiho_catalog as build
from mobiliti_saas.quote_engine.offiho_catalog import (
    OffihoCatalogItem,
    build_offiho_cart_payload,
)


def _runtime_item(**overrides):
    values = {
        "inventory_key": "OHV-127 MORADO FABRIZIA *",
        "code": "OHV-127",
        "name": "FABRIZIA *",
        "variant": "MORADO",
        "unit": "PZA",
        "pieces_per_box": Decimal("1"),
        "available_quantity": Decimal("4"),
        "unit_price": Decimal("1000"),
        "price_source": "inventory",
        "product_url": "https://www.offihoblack.com/products/fabrizia-ohv-127",
        "image_url": (
            "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
            "generated-reference/fabrizia-morado.jpg"
        ),
        "description": "Silla Fabrizia morada.",
        "match_status": "generated_visual_reference",
        "image_kind": "generated_reference",
        "image_label": "Imagen generada; referencia visual, no fotografía oficial",
        "image_references": (
            "OHV-127 AMARILLO FABRIZIA *",
            "https://www.offihoblack.com/products/fabrizia-ohv-127",
        ),
    }
    values.update(overrides)
    return OffihoCatalogItem(**values)


def test_generated_offiho_metadata_reaches_public_catalog_and_cart():
    item = _runtime_item()
    public = item.to_public_dict()
    catalog = {
        "source_hash": "generated-test",
        "items": [item],
        "by_inventory_key": {item.inventory_key: item},
    }

    cart = build_offiho_cart_payload(
        [{"inventory_key": item.inventory_key, "quantity": 1}],
        catalog=catalog,
    )

    assert public["image_kind"] == "generated_reference"
    assert public["image_label"].startswith("Imagen generada")
    assert public["image_references"] == [
        "OHV-127 AMARILLO FABRIZIA *",
        "https://www.offihoblack.com/products/fabrizia-ohv-127",
    ]
    assert cart["items"][0]["image_kind"] == "generated_reference"
    assert cart["items"][0]["image_label"].startswith("Imagen generada")
    assert "Imagen de referencia" in cart["items"][0]["warnings"]


def test_generated_offiho_item_requires_label_and_references():
    with pytest.raises(ValueError, match="image_label"):
        _runtime_item(image_label="")
    with pytest.raises(ValueError, match="image_references"):
        _runtime_item(image_references=())


def test_generated_manifest_is_strict_and_keeps_generation_provenance(tmp_path):
    manifest = tmp_path / "generated.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generator": "gpt-image-2",
                "items": [
                    {
                        "inventory_key": "OHV-127 MORADO FABRIZIA *",
                        "product_url": "https://www.offihoblack.com/products/fabrizia-ohv-127",
                        "image_url": (
                            "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
                            "generated-reference/fabrizia-morado.jpg"
                        ),
                        "image_label": "Imagen generada; referencia visual, no fotografía oficial",
                        "reference_inventory_key": "OHV-127 AMARILLO FABRIZIA *",
                        "reference_image_url": "https://www.offihoblack.com/cdn/shop/files/fabrizia-amarillo.jpg",
                        "generation_prompt": "Conservar el modelo; cambiar únicamente el acabado a morado.",
                        "source_sha256": "a" * 64,
                        "evidence_as_of": "2026-08-12",
                        "review": "Inspección visual individual aprobada",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    index, source = build.load_generated_image_manifest(manifest)

    row = index["OHV-127 MORADO FABRIZIA *"]
    assert row["match_status"] == "generated_visual_reference"
    assert row["image_kind"] == "generated_reference"
    assert row["generation_prompt"].startswith("Conservar el modelo")
    assert row["image_references"][0] == "OHV-127 AMARILLO FABRIZIA *"
    assert source["record_count"] == 1
    assert len(source["sha256"]) == 64


def test_generated_manifest_accepts_sharepoint_reference_and_managed_catalog_source(tmp_path):
    manifest = tmp_path / "generated-sharepoint.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generator": "gpt-image-2",
                "items": [
                    {
                        "inventory_key": "BRAZO 86S AL DER BLANCO",
                        "product_url": (
                            "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
                            "lp-black-colos-jul2026.pdf#page=20"
                        ),
                        "image_url": (
                            "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/"
                            "generated-reference/brazo-86s-blanco.png"
                        ),
                        "image_label": "Imagen generada; referencia visual, no fotografÃ­a oficial",
                        "reference_inventory_key": "BRAZO-86S",
                        "reference_image_url": (
                            "https://mobiliti11-my.sharepoint.com/personal/joel_meza_mobiliti_mx/"
                            "_layouts/15/Doc.aspx?sourcedoc=%7B337EB328-A64E-42C4-B013-"
                            "86C596D56FD0%7D"
                        ),
                        "generation_prompt": "Conservar geometrÃ­a; mostrar el brazo derecho blanco.",
                        "source_sha256": "b" * 64,
                        "evidence_as_of": "2026-08-12",
                        "review": "InspecciÃ³n visual individual aprobada",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    index, source = build.load_generated_image_manifest(manifest)

    assert index["BRAZO 86S AL DER BLANCO"]["image_references"][0] == "BRAZO-86S"
    assert source["record_count"] == 1


def test_generated_images_only_fill_items_still_without_exact_image():
    official_url = "https://www.offiho.com/modelo/exacto.jpg"
    items = [
        {
            "inventory_key": "EXACTO",
            "image_url": official_url,
            "product_url": "https://www.offiho.com/modelo/exacto",
            "match_status": "official_code_match",
        },
        {
            "inventory_key": "FALTANTE",
            "image_url": "",
            "product_url": "https://www.offiho.com/modelo/faltante",
            "match_status": "unmatched",
        },
    ]
    generated = {
        "EXACTO": {
            "image_url": "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/generated-reference/no.jpg",
            "url": "https://www.offiho.com/modelo/exacto",
            "match_status": "generated_visual_reference",
            "image_kind": "generated_reference",
            "image_label": "Imagen generada",
            "image_references": ["REF"],
        },
        "FALTANTE": {
            "image_url": "https://web-lemon-one-45.vercel.app/catalog-assets/offiho/generated-reference/si.jpg",
            "url": "https://www.offiho.com/modelo/faltante",
            "match_status": "generated_visual_reference",
            "image_kind": "generated_reference",
            "image_label": "Imagen generada",
            "image_references": ["REF"],
        },
    }

    build.apply_generated_images(items, generated)

    assert items[0]["image_url"] == official_url
    assert items[0]["match_status"] == "official_code_match"
    assert items[0]["image_kind"] == "official"
    assert items[1]["image_url"].endswith("/si.jpg")
    assert items[1]["match_status"] == "generated_visual_reference"
    assert items[1]["image_kind"] == "generated_reference"


def test_offiho_view_labels_generated_visual_references():
    source = Path("mobiliti_saas/web/src/main.jsx").read_text(encoding="utf-8")

    assert 'item.image_kind === "generated_reference"' in source
    assert "Imagen generada" in source
