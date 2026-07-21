from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.quotation_import import (
    build_import_manifest,
    normalize_imported_items,
    validate_import_manifest,
)
from quotation_import_fixtures import write_import_fixture


IMPORT_ID = "7b1d6d42-236a-4bc1-9aa8-8d9db793c30b"


@pytest.fixture
def import_manifest(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")
    manifest, _ = build_import_manifest(
        source.read_bytes(), import_id=IMPORT_ID, original_filename=source.name
    )
    return manifest


def test_build_import_manifest_preserves_sections_rows_images_and_requires_currency(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx")

    manifest, image_map = build_import_manifest(
        source.read_bytes(),
        import_id=IMPORT_ID,
        original_filename=source.name,
    )

    assert manifest["source_currency"] is None
    assert manifest["currency_status"] == "required"
    assert manifest["provider"] == "SUNON TECHNOLOGY CO.,LTD."
    assert [row["title"] for row in manifest["sections"]] == [
        "SALA DE JUNTAS SECUNDARIO",
        "MUESTRAS",
        "CONCEJO",
    ]
    assert len(manifest["items"]) == 7
    assert manifest["items"][0]["key"].endswith(":9")
    assert sorted(image_map) == [9, 11, 12, 13, 14, 15, 17]
    assert all(image and media_type.startswith("image/") for image, media_type in image_map.values())


def test_build_import_manifest_detects_explicit_currency(tmp_path):
    source = write_import_fixture(tmp_path / "source.xlsx", currency="USD")

    manifest, _ = build_import_manifest(
        source.read_bytes(), import_id=IMPORT_ID, original_filename=source.name
    )

    assert manifest["source_currency"] == "USD"
    assert manifest["currency_status"] == "detected"
    assert {item["source_currency"] for item in manifest["items"]} == {"USD"}
    assert len({item["row_hash"] for item in manifest["items"]}) == 7


def test_normalize_imported_items_uses_selected_currency_and_allowed_overrides(import_manifest):
    rows = normalize_imported_items(
        [
            {
                "kind": "imported",
                "import_id": import_manifest["import_id"],
                "source_row": 11,
                "source_currency": "USD",
                "quantity": "2",
                "overrides": {
                    "name": "Alien Task Chair revisada",
                    "description": "Silla operativa revisada",
                    "dimension": "630 x 565 x 1000 mm",
                    "unit_price": "82.00",
                    "provider": "Sunon",
                },
            }
        ],
        import_manifest,
        source_currency="USD",
        quote_currency="MXN",
        rate_rows=[
            {
                "currency": "USD",
                "mxn_per_unit": "18.50",
                "effective_date": "2026-07-21",
                "retrieved_at": "2026-07-21T00:00:00Z",
            }
        ],
        discount_percent="40",
    )

    assert rows[0]["original_unit_price"] == "82.000000"
    assert rows[0]["unit_price"] == "1517.00"
    assert rows[0]["frozen_exchange_rate"] == "18.500000"
    assert rows[0]["source_reference"].endswith("#Quotation!11")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item["overrides"].update(name="=SUM(A1:A2)"),
        lambda item: item.update(quantity="0"),
        lambda item: item["overrides"].update(unit_price="-1"),
        lambda item: item.update(source_currency=""),
        lambda item: item["overrides"].update(unexpected="no permitido"),
    ],
)
def test_normalize_imported_items_rejects_unsafe_or_invalid_values(import_manifest, mutation):
    item = {
        "kind": "imported",
        "import_id": import_manifest["import_id"],
        "source_row": 11,
        "source_currency": "USD",
        "quantity": "1",
        "overrides": {
            "name": "Alien Task Chair",
            "description": "Silla operativa",
            "dimension": "630 x 565 x 1000 mm",
            "unit_price": "82.00",
            "provider": "Sunon",
        },
    }
    mutation(item)

    with pytest.raises(ValueError):
        normalize_imported_items(
            [item],
            import_manifest,
            source_currency=None,
            quote_currency="MXN",
            rate_rows=[],
            discount_percent="40",
        )


def test_normalize_imported_items_rejects_duplicate_rows(import_manifest):
    item = {
        "kind": "imported",
        "import_id": import_manifest["import_id"],
        "source_row": 11,
        "source_currency": "MXN",
        "quantity": "1",
        "overrides": {
            "name": "Alien Task Chair",
            "description": "Silla operativa",
            "dimension": "630 x 565 x 1000 mm",
            "unit_price": "82.00",
            "provider": "Sunon",
        },
    }

    with pytest.raises(ValueError, match="Fila importada invalida"):
        normalize_imported_items(
            [item, deepcopy(item)],
            import_manifest,
            source_currency="MXN",
            quote_currency="MXN",
            rate_rows=[],
            discount_percent="40",
        )


def test_validate_import_manifest_rejects_more_than_500_lines(import_manifest):
    invalid = deepcopy(import_manifest)
    invalid["items"] = invalid["items"] * 72

    with pytest.raises(ValueError, match="500"):
        validate_import_manifest(invalid)
