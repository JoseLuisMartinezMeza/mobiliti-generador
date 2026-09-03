from copy import deepcopy
import json
from pathlib import Path

import pytest

from mobiliti_saas.worker.catalog_sync.mondecasa_links import (
    MondecasaLinkResourceError,
    build_mondecasa_link_index,
    load_mondecasa_link_index,
    resolve_mondecasa_link,
)


RESOURCE = Path("mobiliti_saas/worker/catalog_sync/data/mondecasa_links.v1.json")


def _resource():
    return json.loads(RESOURCE.read_text(encoding="utf-8"))


def test_official_mondecasa_index_resolves_exact_collection_and_general_links():
    index = load_mondecasa_link_index()

    exact = resolve_mondecasa_link("AC2001N04ROP", "IBIZA", index)
    ambiguous = resolve_mondecasa_link("AT5624H77TEK", "MEDITERREAN", index)
    fallback = resolve_mondecasa_link("SIN-CODIGO", "OASI", index)

    assert exact.status == "exact_index"
    assert exact.product_url == "https://www.mondecasa.com.sg/all-products/ibiza-dining-armchair"
    assert ambiguous.status == "collection_index"
    assert ambiguous.product_url == "https://www.mondecasa.com/collection/27/MEDITERRANEAN.html"
    assert fallback.status == "catalog_fallback"
    assert fallback.product_url == "https://www.mondecasa.com/products"


def test_mondecasa_index_rejects_untrusted_hosts_and_out_of_range_ids():
    malicious = deepcopy(_resource())
    malicious["collection_links"]["IBIZA"]["url"] = "https://attacker.invalid/collection/5/IBIZA.html"
    with pytest.raises(MondecasaLinkResourceError, match="MONDECASA_URL"):
        build_mondecasa_link_index(malicious)

    invalid_id = deepcopy(_resource())
    invalid_id["reference_url_ids"]["AC2001N04ROP"] = [999999]
    with pytest.raises(MondecasaLinkResourceError, match="MONDECASA_REFERENCES"):
        build_mondecasa_link_index(invalid_id)
