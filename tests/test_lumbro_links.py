from copy import deepcopy
import json
import re
from pathlib import Path

import pytest

from mobiliti_saas.worker.catalog_sync.lumbro_links import (
    DEFAULT_LUMBRO_LINKS_PATH,
    LumbroLinkIndex,
    LumbroLinkResourceError,
    build_lumbro_link_index,
    load_lumbro_link_index,
    resolve_lumbro_link,
    resource_fingerprint,
)


RESOURCE = Path("mobiliti_saas/worker/catalog_sync/data/lumbro_links.v1.json")


def _resource():
    return json.loads(RESOURCE.read_text(encoding="utf-8"))


def test_exact_official_product_links_are_normalized_and_never_guessed():
    venecia = resolve_lumbro_link("  VÉNECIA  ", "Empotrables")
    ibiza = resolve_lumbro_link("IBIZA", "Productos")

    assert venecia.url == "https://www.lumbromx.com/product-page/venecia"
    assert venecia.status == "exact_index"
    assert ibiza.url == "https://www.lumbromx.com/product-page/ibiza"
    assert ibiza.status == "exact_index"


def test_resolver_uses_only_explicit_category_and_general_fallbacks():
    category = resolve_lumbro_link("MODELO SIN FICHA", "Empotrables")
    generic_category = resolve_lumbro_link("MODELO SIN FICHA", "Productos")
    fallback = resolve_lumbro_link("MODELO SIN FICHA", "Categoría inexistente")

    assert category.url == "https://www.lumbromx.com/empotrados"
    assert category.status == "collection_index"
    assert generic_category.url == "https://www.lumbromx.com/productos-1"
    assert generic_category.status == "collection_index"
    assert fallback.url == "https://www.lumbromx.com/category/all-products"
    assert fallback.status == "catalog_fallback"


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("fallback_url", "http://www.lumbromx.com/category/all-products"),
        ("fallback_url", "https://lumbromx.com/category/all-products"),
        ("fallback_url", "https://attacker.invalid/category/all-products"),
        ("fallback_url", "https://user:pass@www.lumbromx.com/category/all-products"),
        ("fallback_url", "https://www.lumbromx.com:443/category/all-products"),
        ("fallback_url", "https://www.lumbromx.com/category/all-products?source=test"),
        ("fallback_url", "https://www.lumbromx.com/category/all-products#section"),
        ("products", "http://www.lumbromx.com/product-page/venecia"),
    ],
)
def test_manifest_rejects_non_official_https_urls(field, url):
    resource = _resource()
    if field == "products":
        resource[field][0]["url"] = url
    else:
        resource[field] = url

    with pytest.raises(LumbroLinkResourceError, match="LUMBRO_URL"):
        build_lumbro_link_index(resource)


def test_resolver_rejects_an_externally_constructed_index_with_an_unsafe_url():
    unsafe_index = LumbroLinkIndex(
        resource_fingerprint="0" * 64,
        product_urls_by_model={"venecia": "https://attacker.invalid/x"},
        category_urls_by_category={},
        fallback_url="https://www.lumbromx.com/category/all-products",
    )

    with pytest.raises(LumbroLinkResourceError, match="LUMBRO_URL"):
        resolve_lumbro_link("VENECIA", "Empotrables", unsafe_index)


def test_manifest_rejects_duplicate_normalized_model_keys():
    resource = _resource()
    duplicate = deepcopy(resource["products"][0])
    resource["products"].append(duplicate)

    with pytest.raises(LumbroLinkResourceError, match="LUMBRO_MODEL"):
        build_lumbro_link_index(resource)


def test_resource_fingerprint_uses_canonical_json_and_is_stable():
    resource = _resource()
    reordered = json.loads(json.dumps(resource, sort_keys=True))
    changed = deepcopy(resource)
    changed["products"][0]["url"] = "https://www.lumbromx.com/product-page/venecia-v2"

    original = build_lumbro_link_index(resource)
    reordered_index = build_lumbro_link_index(reordered)
    changed_index = build_lumbro_link_index(changed)

    assert original.resource_fingerprint == reordered_index.resource_fingerprint
    assert re.fullmatch(r"[0-9a-f]{64}", original.resource_fingerprint)
    assert changed_index.resource_fingerprint != original.resource_fingerprint
    assert resource_fingerprint() == original.resource_fingerprint
    assert load_lumbro_link_index().resource_fingerprint == original.resource_fingerprint
    assert DEFAULT_LUMBRO_LINKS_PATH.resolve() == RESOURCE.resolve()
