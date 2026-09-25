from typing import get_args

from mobiliti_saas.worker.catalog_sync.importers.common import (
    CatalogAssetBinding,
    CatalogAssetMatchStatus,
)


def test_family_pdf_is_an_explicit_non_variant_exact_asset_status():
    assert "family_pdf" in get_args(CatalogAssetMatchStatus)

    binding = CatalogAssetBinding(
        internal_id="requiez:rp-1400-gc",
        asset_sha256="a" * 64,
        object_name=f"{'a' * 64}.png",
        image_kind="official",
        match_status="family_pdf",
        source_references=(
            {
                "file_id": "requiez-a26",
                "sheet_or_page": 3,
                "cell_or_bbox": [250, 350, 575, 735],
            },
        ),
    )

    assert binding.match_status == "family_pdf"
