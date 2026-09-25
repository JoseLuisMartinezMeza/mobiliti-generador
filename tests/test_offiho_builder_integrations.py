from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image

from scripts import build_offiho_catalog as build


def _write_spec_guide(path: Path, source_image: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SPEC Offiho"
    sheet["A8"] = "Cod."
    sheet["B8"] = "Imagen."
    sheet["C8"] = "Descripcion."
    sheet["A9"] = "OHE-705gris"
    sheet["C9"] = "Silla ejecutiva Aiko, acabado gris."
    sheet.add_image(WorkbookImage(str(source_image)), "B9")
    workbook.save(path)
    workbook.close()


def _build_with_spec(
    monkeypatch,
    tmp_path: Path,
    spec_path: Path,
    suffix: str,
    *,
    spec_source_urls: dict[str, str] | None = None,
) -> dict:
    item = {
        "inventory_key": "OHE-705 GRIS AIKO",
        "code": "OHE-705",
        "name": "AIKO",
        "variant": "GRIS",
        "unit": "PZA",
        "pieces_per_box": 1,
        "available_quantity": 1,
        "unit_price": 100,
        "price_source": "inventory",
    }
    audit = {
        "source_row_count": 1,
        "duplicate_row_count": 0,
        "unique_item_count": 1,
        "excluded_stock_status_count": 0,
        "excluded_header_row_count": 0,
        "defaulted_pieces_status_count": 0,
        "excluded_blank_stock_count": 0,
    }
    monkeypatch.setattr(build, "_parse_inventory_xls", lambda _path: ([dict(item)], audit))
    monkeypatch.setattr(build, "parse_pdf_price_index", lambda _paths: {})
    monkeypatch.setattr(
        build,
        "parse_pdf_product_index",
        lambda _paths, _items, _assets, _base_url: {},
    )
    monkeypatch.setattr(build, "build_site_product_index", lambda _cache, **_kwargs: {})
    inventory = tmp_path / "inventory.xls"
    inventory.write_bytes(b"same inventory")
    return build.build_catalog(
        inventory,
        [],
        tmp_path / f"cache-{suffix}.json",
        tmp_path / f"catalog-{suffix}.json",
        assets_dir=tmp_path / f"assets-{suffix}",
        asset_base_url="https://assets.example.test/offiho",
        spec_guide_paths=[spec_path],
        spec_source_urls=spec_source_urls,
        colos_exact_manifest_path=None,
        offiho_exact_manifest_path=None,
    )


def test_explicit_missing_exact_manifest_fails_closed(tmp_path):
    missing = tmp_path / "missing-exact-images.json"

    with pytest.raises(RuntimeError, match="No existe.*manifiesto"):
        build.load_exact_image_manifest(
            missing,
            allowed_hosts=build.OFFIHO_HOSTS,
            match_status="official_variant_exact",
        )


def test_spec_index_hash_does_not_depend_on_local_checkout_path(monkeypatch, tmp_path):
    source_image = tmp_path / "aiko.png"
    Image.new("RGB", (24, 16), (31, 96, 145)).save(source_image, "PNG")
    first_dir = tmp_path / "checkout-a"
    second_dir = tmp_path / "checkout-b"
    first_dir.mkdir()
    second_dir.mkdir()
    first_spec = first_dir / "offiho-spec.xlsx"
    second_spec = second_dir / "offiho-spec.xlsx"
    _write_spec_guide(first_spec, source_image)
    second_spec.write_bytes(first_spec.read_bytes())

    first = _build_with_spec(monkeypatch, tmp_path, first_spec, "a")
    second = _build_with_spec(monkeypatch, tmp_path, second_spec, "b")

    assert first["sources"]["spec_image_index"]["sha256"] == second["sources"][
        "spec_image_index"
    ]["sha256"]
    assert first["source_hash"] == second["source_hash"]
    assert first["items"][0]["product_url"] == ""
    assert second["items"][0]["product_url"] == ""


def test_builder_preserves_explicit_sharepoint_spec_source_url(monkeypatch, tmp_path):
    source_image = tmp_path / "aiko.png"
    Image.new("RGB", (24, 16), (31, 96, 145)).save(source_image, "PNG")
    spec = tmp_path / "offiho-spec.xlsx"
    _write_spec_guide(spec, source_image)
    sharepoint_url = (
        "https://mobiliti.sharepoint.com/sites/catalogos/"
        "Shared%20Documents/offiho-spec.xlsx"
    )

    catalog = _build_with_spec(
        monkeypatch,
        tmp_path,
        spec,
        "sharepoint",
        spec_source_urls={spec.name: sharepoint_url},
    )

    assert catalog["items"][0]["product_url"] == sharepoint_url


@pytest.mark.parametrize(
    "source_url",
    [
        "file:///C:/checkout/offiho-spec.xlsx",
        "http://mobiliti.sharepoint.com/offiho-spec.xlsx",
        "https://mobiliti.sharepoint.com.evil.example/offiho-spec.xlsx",
        "https://user@mobiliti.sharepoint.com/offiho-spec.xlsx",
        "https://mobiliti.sharepoint.com:444/offiho-spec.xlsx",
    ],
)
def test_spec_product_url_trust_boundary_rejects_untrusted_sources(source_url):
    assert build._trusted_spec_product_url({"product_url": source_url}) == ""
