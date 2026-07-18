import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from openpyxl import load_workbook

from mobiliti_saas.worker.catalog_sync.importers import alma
from mobiliti_saas.worker.catalog_sync.kundesign_links import (
    DEFAULT_KUNDESIGN_LINKS_PATH,
    KundesignLinkResourceError,
    build_kundesign_link_index,
    load_kundesign_link_index,
    normalize_product_key,
    resolve_kundesign_link,
)


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FALLBACK = "https://www.kundesign.com/products"


def _resource(*, products=None, overrides=None):
    return {
        "schema_version": 1,
        "captured_at": "2026-07-17",
        "source_url": FALLBACK,
        "fallback_url": FALLBACK,
        "provenance": {
            "algorithm": "sha256",
            "source_sha256": "a" * 64,
            "source_product_count": len(products or []),
        },
        "products": products or [],
        "overrides": overrides or [],
    }


def _product(collection, product_type, detail_url):
    return {"collection": collection, "type": product_type, "detail_url": detail_url}


def test_normalization_uses_collection_and_only_the_first_description_line():
    assert normalize_product_key("  Clógs  ", "CLOGS Dining Chair\nBase: aluminium") == (
        "clogs|dining chair"
    )
    assert normalize_product_key("Lotus Planter", "LOTUS Planter (XL)\nAluminium") == (
        "lotus planter|xl"
    )


def test_resolver_returns_unique_exact_detail_or_official_fallback():
    exact_url = "https://www.kundesign.com/s/131/Dining%20Chair.html"
    ambiguous_url = "https://www.kundesign.com/s/999/Dining%20Chair.html"
    index = build_kundesign_link_index(
        _resource(
            products=[
                _product("Clogs", "Dining Chair", exact_url),
                _product("String", "Dining Chair", exact_url),
                _product("String", "Dining Chair", ambiguous_url),
            ]
        )
    )

    exact = resolve_kundesign_link("Clogs", "CLOGS Dining Chair\nBase", index)
    missing = resolve_kundesign_link("Clogs", "Producto inexistente", index)
    ambiguous = resolve_kundesign_link("String", "STRING Dining Chair", index)

    assert exact.product_url == exact_url
    assert exact.metadata == {
        "status": "exact_index",
        "key": "clogs|dining chair",
        "evidence": {"distinct_detail_urls": [exact_url]},
    }
    assert missing.product_url == FALLBACK
    assert missing.metadata["status"] == "catalog_fallback"
    assert missing.metadata["evidence"]["reason"] == "no_exact_detail"
    assert ambiguous.product_url == FALLBACK
    assert ambiguous.metadata["status"] == "catalog_fallback"
    assert ambiguous.metadata["evidence"]["reason"] == "ambiguous_exact_detail"


def test_empty_description_returns_official_fallback_without_error():
    index = build_kundesign_link_index(_resource())

    match = resolve_kundesign_link("PAVILION", "", index)

    assert match.product_url == FALLBACK
    assert match.metadata["status"] == "catalog_fallback"
    assert match.metadata["key"] == "pavilion|"
    assert match.metadata["evidence"]["reason"] == "no_exact_detail"


def test_curated_override_is_exact_keyed_and_auditable():
    detail_url = "https://www.kundesign.com/s/131/Dining%20Chair.html"
    index = build_kundesign_link_index(
        _resource(
            products=[_product("Clogs", "Dining Chair", detail_url)],
            overrides=[
                {
                    "source_key": "clogs|dining side chair",
                    "target_key": "clogs|dining chair",
                    "detail_url": detail_url,
                    "reason": "Alias editorial explícito del índice oficial.",
                }
            ],
        )
    )

    match = resolve_kundesign_link("Clogs", "CLOGS Dining Side Chair", index)

    assert match.product_url == detail_url
    assert match.metadata["status"] == "curated_override"
    assert match.metadata["key"] == "clogs|dining side chair"
    assert match.metadata["evidence"] == {
        "target_key": "clogs|dining chair",
        "detail_url": detail_url,
        "reason": "Alias editorial explícito del índice oficial.",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(detail_url="http://www.kundesign.com/s/1/A.html"),
        lambda row: row.update(detail_url="https://kundesign.com/s/1/A.html"),
        lambda row: row.update(detail_url="https://www.kundesign.com:/s/1/A.html"),
        lambda row: row.update(detail_url="https://www.kundesign.com/products?x=1"),
        lambda row: row.update(detail_url="https://www.kundesign.com/other"),
        lambda row: row.update(thumbnail_url="https://www.kundesign.com/s/1/thumb.jpg"),
    ],
)
def test_resource_rejects_unsafe_urls_and_unsanitized_fields(mutation):
    product = _product("A", "Chair", "https://www.kundesign.com/s/1/A.html")
    mutation(product)
    with pytest.raises(KundesignLinkResourceError):
        build_kundesign_link_index(_resource(products=[product]))


def test_versioned_resource_is_sanitized_and_has_verified_provenance():
    raw = json.loads(DEFAULT_KUNDESIGN_LINKS_PATH.read_text(encoding="utf-8"))
    index = load_kundesign_link_index()

    assert raw["schema_version"] == 1
    assert raw["captured_at"] == "2026-07-17"
    assert raw["source_url"] == FALLBACK == raw["fallback_url"]
    assert raw["provenance"]["algorithm"] == "sha256"
    assert raw["provenance"]["source_product_count"] == 287
    assert len(raw["products"]) == 287
    assert len(index.products) == 287
    assert all(set(row) == {"collection", "type", "detail_url"} for row in raw["products"])
    serialized = json.dumps(raw, sort_keys=True).casefold()
    assert "thumbnail" not in serialized
    assert "hover_url" not in serialized
    assert "site_product_id" not in serialized
    assert "http://" not in serialized

    cache_path = Path(
        ".cache/catalog_sources/alma/sharepoint_2026-07-17/"
        "kundesign-products-2026-07-17.json"
    )
    if cache_path.exists():
        captured = json.loads(cache_path.read_text(encoding="utf-8"))
        assert raw["products"] == [
            {
                "collection": row["collection"],
                "type": row["type"],
                "detail_url": row["detail_url"],
            }
            for row in captured["products"]
        ]
        assert raw["provenance"]["source_sha256"] == hashlib.sha256(
            cache_path.read_bytes()
        ).hexdigest()


def test_resource_fingerprint_is_canonical_and_changes_with_content():
    resource = _resource(
        products=[
            _product(
                "Clogs",
                "Dining Chair",
                "https://www.kundesign.com/s/131/Dining%20Chair.html",
            )
        ]
    )
    reordered_keys = json.loads(json.dumps(resource, sort_keys=True))
    changed = json.loads(json.dumps(resource))
    changed["captured_at"] = "2026-07-18"

    original = build_kundesign_link_index(resource)
    reordered = build_kundesign_link_index(reordered_keys)
    updated = build_kundesign_link_index(changed)

    assert original.resource_fingerprint == reordered.resource_fingerprint
    assert re.fullmatch(r"[0-9a-f]{64}", original.resource_fingerprint)
    assert updated.resource_fingerprint != original.resource_fingerprint


@dataclass(frozen=True)
class _AdapterFile:
    path: str
    kind: str
    brand: str
    sha256: str
    mime_type: str
    local_path: Path


def _file(logical_path, brand, local_path):
    return _AdapterFile(
        logical_path,
        "spec_guide",
        brand,
        hashlib.sha256(local_path.read_bytes()).hexdigest(),
        MIME,
        local_path,
    )


def test_real_2026_kun_rows_have_310_nonempty_safe_links_with_explicit_breakdown():
    root = Path(".cache/catalog_sources/alma/sharepoint_2026-07-17")
    paths = (
        (alma._KUN_PATH, "KUN", root / "SPEC Guide-Alma-KUN.root.xlsx"),
        (alma._KUN_PRICE_PATH, "KUN", root / "Spec guide-Alma-KUN Design.current.xlsx"),
        (alma._MONDECASA_PATH, "Mondecasa", root / "SPEC Guide-Alma-Mondecasa.current.xlsx"),
    )
    cache_path = root / "kundesign-products-2026-07-17.json"
    if not cache_path.exists() or any(not local.exists() for _, _, local in paths):
        pytest.skip("Caché local ALMA/Kundesign 2026-07-17 no disponible")

    files = tuple(_file(path, brand, local) for path, brand, local in paths)
    bundle, source_data = alma._validated_bundle(files)
    records = alma._parse_kun(
        bundle[alma._KUN_PATH],
        source_data[alma._KUN_PATH],
        bundle[alma._KUN_PRICE_PATH],
        source_data[alma._KUN_PRICE_PATH],
    )
    workbook = load_workbook(paths[0][2], data_only=True, read_only=False)
    index = load_kundesign_link_index()
    try:
        resolutions = []
        for record in records:
            description_ref = next(
                reference
                for reference in record["refs"]
                if reference["sheet_or_page"] in {"KUN DESIGN", "PAVILION "}
                and re.fullmatch(r"C[1-9][0-9]*", reference["cell_or_bbox"])
            )
            raw_description = workbook[description_ref["sheet_or_page"]][
                description_ref["cell_or_bbox"]
            ].value
            resolutions.append(
                resolve_kundesign_link(record["collection"], raw_description, index)
            )
    finally:
        workbook.close()

    breakdown = {}
    for resolution in resolutions:
        status = resolution.metadata["status"]
        breakdown[status] = breakdown.get(status, 0) + 1

    assert len(records) == len(resolutions) == 310
    assert all(resolution.product_url.startswith("https://www.kundesign.com/") for resolution in resolutions)
    assert breakdown == {
        "exact_index": 87,
        "curated_override": 83,
        "catalog_fallback": 140,
    }
    assert sum(
        resolution.metadata["status"] == "catalog_fallback"
        for resolution, record in zip(resolutions, records)
        if record["collection"] == "PAVILION"
    ) == 3
