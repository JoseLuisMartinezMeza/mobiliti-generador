from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from scripts.research_labenze_requiez_images import (
    CANONICAL_INVENTORY_SHA256,
    CachedHttpClient,
    HttpResponse,
    IdentityCandidate,
    ResearchCandidate,
    CandidateEnumeration,
    RequiezSource,
    ShopifySource,
    LabenzeLegacySource,
    WooCommerceSource,
    InfinitiSource,
    UrllibTransport,
    _default_downloader,
    download_original,
    enumerate_requiez_candidates,
    enumerate_explicit_visual_candidates,
    enumerate_shopify_candidates,
    load_inventory,
    match_exact_identity,
    normalize_identity,
    run_research,
    should_download_candidate,
    validate_candidate_urls,
    validate_candidate_source_policy,
    validate_source_resource_url,
    validate_output_path,
)


def _write_inventory(path: Path, rows: list[dict]) -> str:
    payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _inventory_rows() -> list[dict]:
    rows = []
    for supplier, count in (("labenze", 462), ("requiez", 314)):
        for index in range(count):
            code = f"{supplier[:1].upper()}-{index:04d}"
            rows.append(
                {
                    "supplier": supplier,
                    "internal_id": f"{supplier}:{index:04d}",
                    "product_key": code.lower(),
                    "sku": code,
                    "source_code": code,
                    "name": f"Producto {index}",
                    "description": "",
                    "collection": "Prueba",
                    "source_page": index + 1,
                    "product_url": "https://sharepoint.example/catalogo.pdf#page=1",
                    "visual_signature": {"sha256": "a" * 64, "fields": {}},
                    "review": {
                        "approved": False,
                        "reviewer": "",
                        "status": "pending_human_review",
                        "checks": {
                            "full_product_visible": None,
                            "not_cropped": None,
                            "configuration_supported": None,
                        },
                    },
                }
            )
    return rows


def _image_bytes(format_name="PNG", size=(640, 480)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, (240, 240, 240)).save(stream, format=format_name)
    return stream.getvalue()


class _TransportResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self, limit):
        return self._body[:limit]


class _TransportConnection:
    def __init__(self, *, peer_ip, response):
        self.peer_ip = peer_ip
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method, target, *, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_secure_transport_never_connects_to_a_loopback_redirect_target():
    connections = []

    def connector(host, ip, port, timeout, ssl_context):
        connection = _TransportConnection(
            peer_ip=ip,
            response=_TransportResponse(302, {"location": "https://127.0.0.1/admin"}),
        )
        connections.append((host, ip, connection))
        return connection

    transport = UrllibTransport(
        resolver=lambda host: ["151.101.1.12"] if host == "api-productos.requiez.com" else [host],
        connector=connector,
    )

    with pytest.raises(ValueError, match="pública|privada|permitido"):
        transport.fetch(
            "https://api-productos.requiez.com/productos",
            source_name="requiez",
            resource_kind="api",
        )

    assert [(host, ip) for host, ip, _ in connections] == [
        ("api-productos.requiez.com", "151.101.1.12")
    ]
    assert len(connections[0][2].requests) == 1


def test_secure_transport_rejects_dns_rebind_peer_mismatch_before_http_request():
    connection = _TransportConnection(
        peer_ip="151.101.1.99",
        response=_TransportResponse(200, {"content-type": "application/json"}, b"[]"),
    )
    transport = UrllibTransport(
        resolver=lambda host: ["151.101.1.12"],
        connector=lambda host, ip, port, timeout, ssl_context: connection,
    )

    with pytest.raises(ValueError, match="peer|DNS binding"):
        transport.fetch(
            "https://api-productos.requiez.com/productos",
            source_name="requiez",
            resource_kind="api",
        )

    assert connection.requests == []
    assert connection.closed is True


def test_secure_transport_pins_public_ip_but_preserves_original_tls_host_and_http_host():
    connection = _TransportConnection(
        peer_ip="151.101.1.12",
        response=_TransportResponse(200, {"content-type": "application/json"}, b"[]"),
    )
    connector_calls = []

    def connector(host, ip, port, timeout, ssl_context):
        connector_calls.append((host, ip, port, timeout, ssl_context))
        return connection

    transport = UrllibTransport(
        resolver=lambda host: ["151.101.1.12"],
        connector=connector,
        timeout=7,
    )

    response = transport.fetch(
        "https://api-productos.requiez.com/productos",
        source_name="requiez",
        resource_kind="api",
    )

    assert response.status == 200
    assert connector_calls[0][:4] == (
        "api-productos.requiez.com",
        "151.101.1.12",
        443,
        7,
    )
    assert connection.requests[0][0:2] == ("GET", "/productos")
    assert connection.requests[0][2]["Host"] == "api-productos.requiez.com"


@pytest.mark.parametrize(
    ("url", "resolved"),
    [
        ("http://api-productos.requiez.com/productos", ["151.101.1.12"]),
        ("https://user:pass@api-productos.requiez.com/productos", ["151.101.1.12"]),
        ("https://api-productos.requiez.com:444/productos", ["151.101.1.12"]),
        ("https://api-productos.requiez.com/productos", ["151.101.1.12", "10.0.0.5"]),
    ],
)
def test_secure_transport_rejects_downgrade_userinfo_port_and_mixed_dns_before_connect(url, resolved):
    connect_calls = []
    transport = UrllibTransport(
        resolver=lambda host: resolved,
        connector=lambda *args: connect_calls.append(args),
    )

    with pytest.raises(ValueError):
        transport.fetch(url, source_name="requiez", resource_kind="api")

    assert connect_calls == []


def test_source_policy_rejects_arterio_candidate_using_another_approved_host_without_download(tmp_path):
    transport_calls = []
    client = CachedHttpClient(
        tmp_path / "cache",
        transport=lambda url: transport_calls.append(url),
    )
    candidate = ResearchCandidate(
        source_name="arterio.mx",
        source_kind="authorized_distributor",
        source_id="1",
        query="SKU-1",
        matched_field="variation.sku",
        product_url="https://arterio.mx/producto/silla/",
        image_source_url="https://cdn.shopify.com/s/files/foreign.jpg",
        evidence={},
    )

    with pytest.raises(ValueError, match="política de fuente|pol.tica de fuente"):
        _default_downloader(client)(candidate, tmp_path / "originals")

    assert transport_calls == []


def test_redirect_is_path_and_source_validated_before_second_connection():
    connections = []

    def connector(host, ip, port, timeout, ssl_context):
        connection = _TransportConnection(
            peer_ip=ip,
            response=_TransportResponse(
                302,
                {"location": "https://cdn.shopify.com/s/files/foreign.json"},
            ),
        )
        connections.append(connection)
        return connection

    transport = UrllibTransport(
        resolver=lambda host: ["151.101.1.12"],
        connector=connector,
    )

    with pytest.raises(ValueError, match="política de fuente|pol.tica de fuente"):
        transport.fetch(
            "https://arterio.mx/wp-json/wc/store/v1/products?per_page=100&page=1",
            source_name="arterio",
            resource_kind="api",
        )

    assert len(connections) == 1
    assert len(connections[0].requests) == 1


@pytest.mark.parametrize(
    "candidate",
    [
        ResearchCandidate(
            "api-productos.requiez.com", "manufacturer_official", "1", "R-1", "code",
            "https://requiez.com/producto/R-1", "https://requiez.com/img/products/r-1/a.webp", {},
        ),
        ResearchCandidate(
            "nogalbeat.com", "authorized_distributor", "2", "L-1", "variant.sku",
            "https://nogalbeat.com/products/chair?variant=2", "https://cdn.shopify.com/s/files/a.jpg", {},
        ),
        ResearchCandidate(
            "arterio.mx", "authorized_distributor", "3", "L-2", "variation.sku",
            "https://arterio.mx/producto/chair/", "https://arterio.mx/wp-content/uploads/a.jpg", {},
        ),
        ResearchCandidate(
            "infinitidesign.it", "manufacturer_official", "4", "L-3", "curated",
            "https://www.infinitidesign.it/en/product/chair/",
            "https://www.infinitidesign.it/wp-content/uploads/a.jpg", {},
        ),
    ],
)
def test_candidate_source_policy_accepts_only_its_own_product_and_image_routes(candidate):
    assert validate_candidate_source_policy(candidate) is candidate


@pytest.mark.parametrize(
    ("url", "source_name", "resource_kind"),
    [
        ("https://arterio.mx/wp-json/wc/store/v1/products-evil", "arterio", "api"),
        ("https://test.diagrama.labenze.com/productos-evil", "labenze_legacy", "api"),
        ("https://nogalbeat.com/products.json?limit=250&page=internal", "nogalbeat.com", "feed"),
        ("https://nogalbeat.com/products.json?limit=all&page=1", "nogalbeat.com", "feed"),
    ],
)
def test_source_policy_rejects_prefix_confusion_and_non_numeric_shopify_pagination(
    url, source_name, resource_kind
):
    with pytest.raises(ValueError, match="política de fuente|pol.tica de fuente"):
        validate_source_resource_url(url, source_name=source_name, resource_kind=resource_kind)


@pytest.mark.parametrize(
    ("source_name", "resource_kind", "url"),
    [
        ("requiez", "api", "https://api-productos.requiez.com/producto/code/%252e%252e/admin"),
        ("nogalbeat.com", "product", "https://nogalbeat.com/products/%252fadmin"),
        ("nogalbeatstore.com", "product", "https://nogalbeatstore.com/products/%252e%252e/admin"),
        ("3rin.com.mx", "product", "https://3rin.com.mx/products/%250Aadmin"),
        ("labenze_legacy", "api", "https://test.diagrama.labenze.com/productos/%255cadmin"),
        ("arterio", "image", "https://arterio.mx/wp-content/uploads/%250Asecret.jpg"),
        ("infiniti", "api", "https://www.infinitidesign.it/wp-json/wp/v2/product/%252fadmin"),
    ],
)
def test_every_source_rejects_recursively_encoded_path_delimiters(
    source_name, resource_kind, url
):
    with pytest.raises(ValueError, match="ruta|canonical|política|pol.tica"):
        validate_source_resource_url(url, source_name=source_name, resource_kind=resource_kind)


@pytest.mark.parametrize(
    "url",
    [
        "https://arterio.mx/wp-content/uploads/%2Fsecret.jpg",
        "https://arterio.mx/wp-content/uploads/%25252e%25252e/secret.jpg",
        "https://arterio.mx/wp-content/uploads/name%c3%a9.jpg",
        "https://arterio.mx/wp-content/uploads/name\u00a0.jpg",
        "https://arterio.mx/wp-content/uploads\uff0fsecret.jpg",
        "https://arterio.mx/wp-content/uploads/..\u2044secret.jpg",
        "https://arterio.mx/wp-content/uploads//secret.jpg",
    ],
)
def test_path_canonicalization_rejects_encoded_slashes_noncanonical_roundtrips_and_unicode_confusables(url):
    with pytest.raises(ValueError, match="ruta|canonical|Unicode|política|pol.tica"):
        validate_source_resource_url(url, source_name="arterio", resource_kind="image")


def test_redirect_with_double_encoded_traversal_is_rejected_before_second_connection():
    connections = []

    def connector(host, ip, port, timeout, ssl_context):
        connection = _TransportConnection(
            peer_ip=ip,
            response=_TransportResponse(
                302,
                {"location": "/producto/code/%252e%252e/admin"},
            ),
        )
        connections.append(connection)
        return connection

    transport = UrllibTransport(
        resolver=lambda host: ["151.101.1.12"],
        connector=connector,
    )

    with pytest.raises(ValueError, match="ruta|canonical"):
        transport.fetch(
            "https://api-productos.requiez.com/productos",
            source_name="requiez",
            resource_kind="api",
        )

    assert len(connections) == 1
    assert len(connections[0].requests) == 1


@pytest.mark.parametrize(
    ("resource_kind", "url"),
    [
        ("api", "https://www.infinitidesign.it/wp-json/wp/v2/product?lang=en&per_page=100&page=1"),
        ("api", "https://www.infinitidesign.it/wp-json/wp/v2/product/22995"),
        ("api", "https://www.infinitidesign.it/wp-json/wc/store/v1/products/22995"),
        ("product", "https://www.infinitidesign.it/en/product/pure-loop-mono-4-legs/"),
        ("product", "https://infinitidesign.it/es/product/canova/"),
        ("product", "https://www.infinitidesign.it/it/product/22995/"),
        ("image", "https://www.infinitidesign.it/wp-content/uploads/2026/08/canova.jpg"),
    ],
)
def test_infiniti_accepts_only_exact_documented_resource_shapes(resource_kind, url):
    assert validate_source_resource_url(url, source_name="infiniti", resource_kind=resource_kind)


@pytest.mark.parametrize(
    ("resource_kind", "url"),
    [
        ("api", "https://www.infinitidesign.it/evil/wp-json/wp/v2/product"),
        ("api", "https://www.infinitidesign.it/wp-json/product/22995"),
        ("api", "https://www.infinitidesign.it/wp-json/wp/v2/product-evil"),
        ("api", "https://www.infinitidesign.it/wp-json/wc/store/v1/products/22995/images"),
        ("product", "https://www.infinitidesign.it/evil/product/canova/"),
        ("product", "https://www.infinitidesign.it/product/canova/"),
        ("product", "https://www.infinitidesign.it/fr/product/canova/"),
        ("product", "https://www.infinitidesign.it/en/product//"),
        ("product", "https://www.infinitidesign.it/en/product/12345/"),
        ("product", "https://www.infinitidesign.it/en/product/canova/more/"),
        ("image", "https://www.infinitidesign.it/wp-content/uploads-evil/canova.jpg"),
        ("image", "https://www.infinitidesign.it/evil/wp-content/uploads/canova.jpg"),
    ],
)
def test_infiniti_rejects_lookalike_and_overbroad_resource_paths(resource_kind, url):
    with pytest.raises(ValueError, match="ruta|política de fuente|pol.tica de fuente"):
        validate_source_resource_url(url, source_name="infiniti", resource_kind=resource_kind)


def test_inventory_contract_fixes_the_canonical_sha_and_exact_identities(tmp_path):
    assert CANONICAL_INVENTORY_SHA256 == "476013bf863552d4e622f510c39a019fc1549859714edbd1e8b76994d31a0812"
    path = tmp_path / "inventory.jsonl"
    rows = _inventory_rows()
    digest = _write_inventory(path, rows)

    loaded = load_inventory(path, expected_sha256=digest)

    assert len(loaded) == 776
    assert len({row["internal_id"] for row in loaded}) == 776


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.pop(), "776"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "internal_id": rows[0]["internal_id"]}), "duplicado"),
        (lambda rows: rows.__setitem__(1, {**rows[1], "supplier": "inventado"}), "supplier"),
    ],
)
def test_inventory_rejects_wrong_cardinality_duplicate_or_supplier(tmp_path, mutate, message):
    rows = _inventory_rows()
    mutate(rows)
    path = tmp_path / "inventory.jsonl"
    digest = _write_inventory(path, rows)

    with pytest.raises(ValueError, match=message):
        load_inventory(path, expected_sha256=digest)


def test_inventory_rejects_a_sha_mismatch(tmp_path):
    path = tmp_path / "inventory.jsonl"
    _write_inventory(path, _inventory_rows())

    with pytest.raises(ValueError, match="SHA-256"):
        load_inventory(path, expected_sha256="0" * 64)


def test_normalized_identity_is_nfkc_uppercase_and_alphanumeric_only():
    assert normalize_identity("  rm－9025n/ng  ") == "RM9025NNG"
    assert normalize_identity("ri-５０") == "RI50"
    assert normalize_identity("á-1") == "1"


def test_identity_match_accepts_only_one_exact_code_or_shortcode():
    candidates = [
        IdentityCandidate(source_id="uuid-1", code="RM-9025N/NG", short_code="9025N"),
        IdentityCandidate(source_id="uuid-2", code="RM-9025", short_code="9025"),
    ]

    result = match_exact_identity("RM-9025N/NG", candidates)

    assert result.status == "found_exact"
    assert result.candidate.source_id == "uuid-1"
    assert result.matched_field == "code"


@pytest.mark.parametrize("query", ["RM-9025N", "9025N/NG", "Silla Requiez", "RM9025N/NG extra"])
def test_identity_match_rejects_partial_fuzzy_prefix_and_name_only(query):
    candidates = [IdentityCandidate(source_id="uuid-1", code="RM-9025N/NG", short_code="9025N")]

    result = match_exact_identity(query, candidates)

    assert result.status == "rejected"
    assert result.candidate is None


def test_identity_collision_is_rejected_instead_of_auto_resolved():
    candidates = [
        IdentityCandidate(source_id="uuid-1", code="RI-50", short_code="RI50"),
        IdentityCandidate(source_id="uuid-2", code="RI 50", short_code="50"),
    ]

    result = match_exact_identity("RI-50", candidates)

    assert result.status == "rejected"
    assert result.reason == "identity_collision"


def test_output_must_be_new_and_disjoint_from_every_input_tree(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "catalog-assets"
    inventory.parent.mkdir()
    store.parent.mkdir()
    assets.mkdir()
    inventory.write_text("x")
    store.write_text("{}")

    safe = tmp_path / "research-new"
    assert validate_output_path(safe, protected_paths=[inventory, store, assets]) == safe.resolve()

    safe.mkdir()
    with pytest.raises(ValueError, match="ya existe"):
        validate_output_path(safe, protected_paths=[inventory, store, assets])
    with pytest.raises(ValueError, match="solapa"):
        validate_output_path(assets / "research", protected_paths=[inventory, store, assets])
    with pytest.raises(ValueError, match="solapa"):
        validate_output_path(tmp_path, protected_paths=[inventory, store, assets])


def test_shopify_accepts_only_images_explicitly_bound_to_the_exact_variant():
    row = {"sku": "101-0220G", "source_code": "101-0220G", "internal_id": "labenze:101-0220g"}
    products = [
        {
            "id": 10,
            "handle": "areta-base-giratoria",
            "vendor": "Labenze",
            "title": "Areta",
            "featured_image": "https://cdn.shopify.com/featured.jpg",
            "variants": [
                {"id": 101, "sku": "101-0220G"},
                {"id": 102, "sku": "101-0220N"},
            ],
            "images": [
                {"id": 1, "src": "https://cdn.shopify.com/unbound.jpg", "variant_ids": []},
                {"id": 2, "src": "https://cdn.shopify.com/wrong.jpg", "variant_ids": [102]},
                {"id": 3, "src": "https://cdn.shopify.com/exact.jpg", "variant_ids": [101]},
            ],
        }
    ]

    candidates = enumerate_shopify_candidates(
        row,
        products,
        source_name="nogalbeat.com",
        storefront_url="https://nogalbeat.com",
    )

    assert [candidate.image_source_url for candidate in candidates] == ["https://cdn.shopify.com/exact.jpg"]
    assert candidates[0].product_url == "https://nogalbeat.com/products/areta-base-giratoria?variant=101"
    assert candidates[0].evidence["variant_id"] == 101
    assert candidates[0].approved is False


def test_shopify_does_not_fall_back_to_first_or_featured_image_without_binding():
    row = {"sku": "LAB-1", "source_code": "LAB-1", "internal_id": "labenze:lab-1"}
    products = [
        {
            "id": 10,
            "handle": "lab-1",
            "vendor": "Labenze",
            "featured_image": "https://cdn.shopify.com/featured.jpg",
            "variants": [{"id": 101, "sku": "LAB-1"}],
            "images": [{"src": "https://cdn.shopify.com/first.jpg", "variant_ids": []}],
        }
    ]

    assert enumerate_shopify_candidates(
        row, products, source_name="nogalbeatstore.com", storefront_url="https://nogalbeatstore.com"
    ) == []


@pytest.mark.parametrize(
    ("source_name", "vendor", "expected"),
    [
        ("3rin.com.mx", "LABENZE", 1),
        ("3rin.com.mx", "Otra marca", 0),
        ("nogalbeat.com", "LABENZE", 0),
    ],
)
def test_kl_prefix_exception_is_limited_to_3r_declared_labenze(source_name, vendor, expected):
    row = {"sku": "101-0220G", "source_code": "101-0220G", "internal_id": "labenze:101-0220g"}
    products = [
        {
            "id": 10,
            "handle": "areta",
            "vendor": vendor,
            "variants": [{"id": 101, "sku": "KL-101-0220G"}],
            "images": [{"src": "https://cdn.example/exact.jpg", "variant_ids": [101]}],
        }
    ]

    candidates = enumerate_shopify_candidates(
        row, products, source_name=source_name, storefront_url=f"https://{source_name}"
    )

    assert len(candidates) == expected


def test_requiez_uses_unique_exact_identity_and_emits_every_detail_image():
    row = {"sku": "RM-9025N/NG", "source_code": "RM-9025N/NG", "internal_id": "requiez:rm-9025n-ng"}
    listing = [{"id": "p-1", "code": "RM-9025N/NG", "shortCode": "9025N", "name": "Mesa"}]
    details = {
        "p-1": {
            "id": "p-1",
            "code": "RM-9025N/NG",
            "shortCode": "9025N",
            "imgs": [
                {"url": "https://cdn.requiez.com/front.jpg", "id": "front"},
                {"url": "https://cdn.requiez.com/side.jpg", "id": "side"},
            ],
        }
    }

    result = enumerate_requiez_candidates(row, listing, details)

    assert result.status == "found_exact"
    assert [candidate.image_source_url for candidate in result.candidates] == [
        "https://cdn.requiez.com/front.jpg",
        "https://cdn.requiez.com/side.jpg",
    ]
    assert all(candidate.product_url == "https://requiez.com/producto/RM-9025N%2FNG" for candidate in result.candidates)
    assert all(candidate.approved is False for candidate in result.candidates)


def test_requiez_rejects_listing_collisions_and_does_not_treat_spa_200_as_identity():
    row = {"sku": "RI-50", "source_code": "RI-50", "internal_id": "requiez:ri-50"}
    listing = [
        {"id": "p-1", "code": "RI-50", "shortCode": "50"},
        {"id": "p-2", "code": "RI 50", "shortCode": "RI50"},
    ]

    result = enumerate_requiez_candidates(
        row,
        listing,
        {},
        page_observations={"https://requiez.com/producto/RI-50": {"status": 200, "body": "SPA"}},
    )

    assert result.status == "rejected"
    assert result.reason == "identity_collision"
    assert result.candidates == []


def test_requiez_gallery_evaluates_every_img_and_marks_filename_identity_support():
    row = {"sku": "RS-460/45", "source_code": "RS-460/45", "internal_id": "requiez:rs-460-45"}
    listing = [{"id": 215, "code": "RS-460-40-45", "shortCode": "RS-460-45"}]
    details = {
        "215": {
            "id": 215,
            "code": "RS-460-40-45",
            "shortCode": "RS-460-45",
            "imgs": [
                {"id": 694, "name": "rs-460-45", "img": "https://requiez.com/img/products/rs-460-40/RS460_40_A.webp"},
                {"id": 691, "name": "rs-460-45-frente", "img": "https://requiez.com/img/products/rs-460-45/RS460_45_A.webp"},
            ],
        }
    }

    result = enumerate_requiez_candidates(row, listing, details)

    assert result.status == "found_exact"
    assert len(result.candidates) == 2
    assert result.candidates[0].evidence["image_identity_supported"] is False
    assert result.candidates[1].evidence["image_identity_supported"] is True
    assert result.candidates[1].evidence["image_name"] == "rs-460-45-frente"
    assert should_download_candidate(result.candidates[0]) is True
    assert should_download_candidate(result.candidates[1]) is True


def test_family_or_manufacturer_mapping_requires_explicit_sku_and_visual_signature():
    row = {
        "sku": "106-00603-BAT",
        "source_code": "106-00603-BAT",
        "internal_id": "labenze:106-00603-bat",
        "visual_signature": {"sha256": "a" * 64, "fields": {"base": "trineo"}},
    }
    records = [
        {
            "source_id": "family-1",
            "assignments": [
                {
                    "sku": "106-00603-BAT",
                    "visual_signature_sha256": "a" * 64,
                    "product_url": "https://test.labenze.com/producto/family-1",
                    "image_source_url": "https://test.diagrama.labenze.com/media/exact.png",
                    "configuration": {"base": "trineo"},
                },
                {
                    "sku": "106-00603-OTRA",
                    "visual_signature_sha256": "b" * 64,
                    "product_url": "https://test.labenze.com/producto/family-1",
                    "image_source_url": "https://test.diagrama.labenze.com/media/other.png",
                },
            ],
        }
    ]

    candidates = enumerate_explicit_visual_candidates(
        row,
        records,
        source_name="labenze-legacy-api",
        source_kind="manufacturer_official",
    )

    assert len(candidates) == 1
    assert candidates[0].image_source_url.endswith("exact.png")
    assert candidates[0].evidence["visual_signature_sha256"] == "a" * 64


@pytest.mark.parametrize(
    "assignment",
    [
        {"sku": "106-00603-BAT", "image_source_url": "https://cdn.example/family.png"},
        {
            "sku": "106-00603-BAT",
            "visual_signature_sha256": "b" * 64,
            "image_source_url": "https://cdn.example/family.png",
        },
        {
            "sku": "106-00603",
            "visual_signature_sha256": "a" * 64,
            "image_source_url": "https://cdn.example/family.png",
        },
    ],
)
def test_family_mapping_rejects_missing_signature_wrong_signature_or_partial_sku(assignment):
    row = {
        "sku": "106-00603-BAT",
        "source_code": "106-00603-BAT",
        "internal_id": "labenze:106-00603-bat",
        "visual_signature": {"sha256": "a" * 64, "fields": {}},
    }
    records = [{"source_id": "family-1", "assignments": [assignment]}]

    assert enumerate_explicit_visual_candidates(
        row,
        records,
        source_name="infiniti-api",
        source_kind="manufacturer_official",
    ) == []


def _candidate(**overrides):
    values = {
        "source_name": "nogalbeat.com",
        "source_kind": "authorized_distributor",
        "source_id": "10",
        "query": "101-0220G",
        "matched_field": "variant.sku",
        "product_url": "https://nogalbeat.com/products/areta",
        "image_source_url": "https://cdn.shopify.com/exact.png",
        "evidence": {"variant_id": 101},
    }
    values.update(overrides)
    return ResearchCandidate(**values)


@pytest.mark.parametrize(
    "product_url",
    [
        "https://nogalbeat.com/",
        "https://nogalbeat.com/search?q=areta",
        "https://nogalbeat.com/collections/sillas",
        "https://cdn.shopify.com/exact.png",
    ],
)
def test_product_url_must_be_an_individual_page_not_home_search_collection_or_cdn(product_url):
    with pytest.raises(ValueError, match="product_url"):
        validate_candidate_urls(
            _candidate(product_url=product_url),
            allowed_product_hosts={"nogalbeat.com"},
            allowed_image_hosts={"cdn.shopify.com"},
        )


def test_candidate_urls_keep_product_page_separate_from_cdn():
    validated = validate_candidate_urls(
        _candidate(),
        allowed_product_hosts={"nogalbeat.com"},
        allowed_image_hosts={"cdn.shopify.com"},
    )

    assert validated.product_url == "https://nogalbeat.com/products/areta"
    assert validated.image_source_url == "https://cdn.shopify.com/exact.png"


def test_downloader_validates_redirect_mime_magic_dimensions_and_writes_content_addressed(tmp_path):
    payload = _image_bytes("PNG", (640, 480))
    response = HttpResponse(
        status=200,
        url="https://cdn.shopify.com/final/exact.png",
        headers={"content-type": "image/png"},
        body=payload,
    )

    result = download_original(
        _candidate(),
        tmp_path,
        allowed_image_hosts={"cdn.shopify.com"},
        fetcher=lambda url: response,
        resolver=lambda host: ["151.101.1.12"],
    )

    digest = hashlib.sha256(payload).hexdigest()
    assert result.sha256 == digest
    assert result.path == tmp_path / f"{digest}.png"
    assert result.path.read_bytes() == payload
    assert result.final_url == response.url
    assert result.dimensions == {"width": 640, "height": 480}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (HttpResponse(404, "https://cdn.shopify.com/x.png", {"content-type": "image/png"}, b"x"), "status"),
        (HttpResponse(200, "https://evil.example/x.png", {"content-type": "image/png"}, _image_bytes()), "host"),
        (HttpResponse(200, "https://cdn.shopify.com/x.png", {"content-type": "text/html"}, b"<html>"), "MIME"),
        (HttpResponse(200, "https://cdn.shopify.com/x.png", {"content-type": "image/png"}, b"<svg></svg>"), "magic"),
        (HttpResponse(200, "https://cdn.shopify.com/x.svg", {"content-type": "image/svg+xml"}, b"<svg></svg>"), "SVG"),
    ],
)
def test_downloader_rejects_bad_status_redirect_host_html_svg_and_magic(tmp_path, response, message):
    with pytest.raises(ValueError, match=message):
        download_original(
            _candidate(),
            tmp_path,
            allowed_image_hosts={"cdn.shopify.com"},
            fetcher=lambda url: response,
            resolver=lambda host: ["151.101.1.12"],
        )


def test_downloader_rejects_private_initial_or_redirect_destination(tmp_path):
    public_response = HttpResponse(
        200,
        "https://cdn.shopify.com/x.png",
        {"content-type": "image/png"},
        _image_bytes(),
    )
    with pytest.raises(ValueError, match="privada"):
        download_original(
            _candidate(image_source_url="https://127.0.0.1/x.png"),
            tmp_path,
            allowed_image_hosts={"127.0.0.1"},
            fetcher=lambda url: public_response,
            resolver=lambda host: [host],
        )

    private_redirect = HttpResponse(
        200,
        "https://internal.example/x.png",
        {"content-type": "image/png"},
        _image_bytes(),
    )
    with pytest.raises(ValueError, match="privada"):
        download_original(
            _candidate(),
            tmp_path,
            allowed_image_hosts={"cdn.shopify.com", "internal.example"},
            fetcher=lambda url: private_redirect,
            resolver=lambda host: ["10.0.0.5" if host == "internal.example" else "151.101.1.12"],
        )


def test_downloader_rejects_non_global_shared_address_space(tmp_path):
    with pytest.raises(ValueError, match="privada|pública|p.blica"):
        download_original(
            _candidate(),
            tmp_path,
            allowed_image_hosts={"cdn.shopify.com"},
            fetcher=lambda url: (_ for _ in ()).throw(AssertionError("no debe descargar")),
            resolver=lambda host: ["100.64.0.1"],
        )


def test_downloader_rejects_over_8_mib_and_dimension_or_pixel_bombs_before_writing(tmp_path):
    oversized = HttpResponse(
        200,
        "https://cdn.shopify.com/x.png",
        {"content-type": "image/png"},
        b"\x89PNG\r\n\x1a\n" + b"0" * (8 * 1024 * 1024),
    )
    with pytest.raises(ValueError, match="8 MiB"):
        download_original(
            _candidate(), tmp_path, allowed_image_hosts={"cdn.shopify.com"},
            fetcher=lambda url: oversized, resolver=lambda host: ["151.101.1.12"]
        )

    huge_pixels = HttpResponse(
        200,
        "https://cdn.shopify.com/x.png",
        {"content-type": "image/png"},
        _image_bytes("PNG", (6000, 5000)),
    )
    with pytest.raises(ValueError, match="25 Mpx"):
        download_original(
            _candidate(), tmp_path, allowed_image_hosts={"cdn.shopify.com"},
            fetcher=lambda url: huge_pixels, resolver=lambda host: ["151.101.1.12"]
        )
    assert list(tmp_path.iterdir()) == []


def test_http_client_caches_a_listing_once_as_immutable_raw_evidence(tmp_path):
    calls = []
    response = HttpResponse(
        200,
        "https://api-productos.requiez.com/productos",
        {"content-type": "application/json", "etag": "v1"},
        b'[{"code":"RI-50"}]',
    )

    def transport(url):
        calls.append(url)
        return response

    client = CachedHttpClient(
        tmp_path / "cache",
        transport=transport,
        sleeper=lambda seconds: None,
        clock=lambda: datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
    )
    first = client.get_json("https://api-productos.requiez.com/productos")
    second = client.get_json("https://api-productos.requiez.com/productos")

    assert first == second == [{"code": "RI-50"}]
    assert calls == ["https://api-productos.requiez.com/productos"]
    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 1
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cached["request_url"] == calls[0]
    assert cached["body_sha256"] == hashlib.sha256(response.body).hexdigest()
    assert cached["fetched_at"] == "2026-08-19T12:00:00Z"


def test_http_client_retries_with_retry_after_before_caching_success(tmp_path):
    responses = iter(
        [
            HttpResponse(429, "https://api.example/items", {"retry-after": "3"}, b"rate limited"),
            HttpResponse(503, "https://api.example/items", {}, b"unavailable"),
            HttpResponse(200, "https://api.example/items", {"content-type": "application/json"}, b"[]"),
        ]
    )
    sleeps = []
    client = CachedHttpClient(
        tmp_path / "cache",
        transport=lambda url: next(responses),
        sleeper=sleeps.append,
        max_attempts=3,
        backoff_seconds=2,
    )

    assert client.get_json("https://api.example/items") == []
    assert sleeps == [3.0, 4.0]


def test_http_client_honors_retry_after_http_date_with_a_bounded_delay(tmp_path):
    responses = iter(
        [
            HttpResponse(
                429,
                "https://api.example/items",
                {"retry-after": "Wed, 19 Aug 2026 12:00:05 GMT"},
                b"rate limited",
            ),
            HttpResponse(200, "https://api.example/items", {"content-type": "application/json"}, b"[]"),
        ]
    )
    sleeps = []
    client = CachedHttpClient(
        tmp_path / "cache",
        transport=lambda url: next(responses),
        sleeper=sleeps.append,
        clock=lambda: datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
        max_attempts=2,
        backoff_seconds=2,
    )

    assert client.get_json("https://api.example/items") == []
    assert sleeps == [5.0]


def test_offline_http_client_replays_cache_without_transport_and_rejects_miss(tmp_path):
    cache = tmp_path / "cache"
    online = CachedHttpClient(
        cache,
        transport=lambda url: HttpResponse(
            200, url, {"content-type": "application/json"}, b'{"value":42}'
        ),
        sleeper=lambda seconds: None,
    )
    assert online.get_json("https://api.example/value") == {"value": 42}
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in cache.iterdir()}

    def forbidden_transport(url):
        raise AssertionError("offline no debe tocar transporte")

    offline = CachedHttpClient(cache, transport=forbidden_transport, offline=True)
    assert offline.get_json("https://api.example/value") == {"value": 42}
    with pytest.raises(ValueError, match="cache offline"):
        offline.get_json("https://api.example/missing")
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in cache.iterdir()}
    assert after == before


def test_offline_http_replay_with_allowlist_does_not_resolve_dns(tmp_path):
    cache = tmp_path / "cache"
    online = CachedHttpClient(
        cache,
        transport=lambda url: HttpResponse(200, url, {"content-type": "application/json"}, b"[]"),
        allowed_hosts={"api.example"},
        resolver=lambda host: ["151.101.1.12"],
    )
    online.get_json("https://api.example/items")

    def forbidden_resolver(host):
        raise AssertionError("offline no debe resolver DNS")

    offline = CachedHttpClient(
        cache,
        transport=lambda url: (_ for _ in ()).throw(AssertionError("offline no transport")),
        offline=True,
        allowed_hosts={"api.example"},
        resolver=forbidden_resolver,
    )

    assert offline.get_json("https://api.example/items") == []


def test_http_cache_detects_tampering_before_offline_replay(tmp_path):
    cache = tmp_path / "cache"
    client = CachedHttpClient(
        cache,
        transport=lambda url: HttpResponse(200, url, {"content-type": "application/json"}, b"[]"),
    )
    client.get_json("https://api.example/items")
    cache_file = next(cache.glob("*.json"))
    entry = json.loads(cache_file.read_text(encoding="utf-8"))
    entry["body_base64"] = "e30="
    cache_file.write_text(json.dumps(entry), encoding="utf-8")

    with pytest.raises(ValueError, match="integridad"):
        CachedHttpClient(cache, transport=lambda url: None, offline=True).get_json(
            "https://api.example/items"
        )


class _LocalSource:
    name = "local-fixture"

    def research(self, row):
        if row["internal_id"] == "labenze:0000":
            return CandidateEnumeration(
                "found_exact",
                [
                    _candidate(
                        source_name=self.name,
                        source_id="fixture-1",
                        query=row["sku"],
                        product_url="https://nogalbeat.com/products/fixture-1",
                        image_source_url="https://cdn.shopify.com/fixture-1.png",
                    )
                ],
                "unique_exact_identity",
            )
        return CandidateEnumeration("exhausted", [], "approved_sources_exhausted")


def test_pipeline_emits_one_terminal_record_per_identity_and_preserves_inputs(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "catalog-assets"
    assets.mkdir(parents=True)
    store.write_text('{"sentinel":true}', encoding="utf-8")
    (assets / "sentinel.png").write_bytes(b"asset-sentinel")
    store_before = hashlib.sha256(store.read_bytes()).hexdigest()
    asset_before = hashlib.sha256((assets / "sentinel.png").read_bytes()).hexdigest()
    output = tmp_path / "research-1"

    summary = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=output,
        sources=[_LocalSource()],
        expected_inventory_sha256=inventory_sha,
        researched_at="2026-08-19T12:00:00Z",
    )

    records = [json.loads(line) for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 776
    assert len({record["internal_id"] for record in records}) == 776
    assert {record["status"] for record in records} == {"found_exact", "exhausted"}
    found = next(record for record in records if record["status"] == "found_exact")
    assert found["query"] == {"raw": "L-0000", "normalized": "L0000"}
    assert found["name"] == "Producto 0"
    assert found["collection"] == "Prueba"
    assert found["description"] == ""
    assert found["source_page"] == 1
    assert found["candidate"]["image_source_url"].endswith("fixture-1.png")
    assert found["evidence"][0]["variant_id"] == 101
    assert found["source_kind"] == "authorized_distributor"
    assert found["review"] == {
        "approved": False,
        "reviewer": "",
        "reviewed_at": None,
        "checks": {
            "full_product_visible": None,
            "not_cropped": None,
            "configuration_supported": None,
        },
    }
    assert summary["counts"] == {"found_exact": 1, "rejected": 0, "exhausted": 775}
    assert summary["inputs_unchanged"] is True
    assert hashlib.sha256(store.read_bytes()).hexdigest() == store_before
    assert hashlib.sha256((assets / "sentinel.png").read_bytes()).hexdigest() == asset_before
    assert (output / "http-cache").is_dir()
    assert (output / "originals").is_dir()
    assert (output / "candidates.csv").is_file()
    assert (output / "artifact-hashes.json").is_file()


def test_pipeline_logical_hash_is_stable_across_new_outputs_and_dates(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "catalog-assets"
    assets.mkdir(parents=True)
    store.write_text("{}", encoding="utf-8")

    first = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=tmp_path / "research-online",
        sources=[_LocalSource()],
        expected_inventory_sha256=inventory_sha,
        researched_at="2026-08-19T12:00:00Z",
    )
    second = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=tmp_path / "research-offline",
        sources=[_LocalSource()],
        expected_inventory_sha256=inventory_sha,
        researched_at="2026-08-20T09:00:00Z",
        offline=True,
        cache_from=tmp_path / "research-online" / "http-cache",
    )

    assert second["offline"] is True
    assert first["logical_candidates_sha256"] == second["logical_candidates_sha256"]


def test_pipeline_rejects_existing_output_before_calling_any_source(tmp_path):
    inventory = tmp_path / "inventory.jsonl"
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}")
    output = tmp_path / "output"
    output.mkdir()

    class ForbiddenSource:
        def research(self, row):
            raise AssertionError("no debe investigar")

    with pytest.raises(ValueError, match="ya existe"):
        run_research(
            inventory_path=inventory,
            store_path=store,
            assets_dir=assets,
            output_dir=output,
            sources=[ForbiddenSource()],
            expected_inventory_sha256=inventory_sha,
        )


def test_missing_cache_writes_failure_receipt_with_stage_and_available_input_hashes(tmp_path):
    inventory = tmp_path / "inventory.jsonl"
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}", encoding="utf-8")
    output = tmp_path / "failed-missing-cache"

    with pytest.raises(ValueError, match="Cache fuente ausente"):
        run_research(
            inventory_path=inventory,
            store_path=store,
            assets_dir=assets,
            output_dir=output,
            sources=[],
            expected_inventory_sha256=inventory_sha,
            cache_from=tmp_path / "missing-cache",
        )

    receipt = json.loads((output / "FAILED.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["stage"] == "copy_cache"
    assert receipt["error_type"] == "ValueError"
    assert "Cache fuente ausente" in receipt["error"]
    assert receipt["inputs_before"]["inventory"]["sha256"]
    assert receipt["inputs_before"]["store"]["sha256"]
    assert receipt["inputs_before"]["assets"]["sha256"]


def test_corrupt_cache_is_rejected_during_copy_and_leaves_failure_receipt(tmp_path):
    inventory = tmp_path / "inventory.jsonl"
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}", encoding="utf-8")
    cache = tmp_path / "corrupt-cache"
    cache.mkdir()
    (cache / "entry.json").write_text(
        json.dumps(
            {
                "request_url": "https://example.test/value",
                "response_url": "https://example.test/value",
                "status": 200,
                "headers": {},
                "body_base64": "W10=",
                "body_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "failed-corrupt-cache"

    with pytest.raises(ValueError, match="integridad|inválido|inv.lido"):
        run_research(
            inventory_path=inventory,
            store_path=store,
            assets_dir=assets,
            output_dir=output,
            sources=[],
            expected_inventory_sha256=inventory_sha,
            cache_from=cache,
        )

    receipt = json.loads((output / "FAILED.json").read_text(encoding="utf-8"))
    assert receipt["stage"] == "copy_cache"
    assert receipt["inputs_before"]["inventory"]["sha256"]


def test_failure_receipt_write_error_never_masks_the_original_cache_error(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.jsonl"
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}", encoding="utf-8")
    original_write_text = Path.write_text

    def fail_receipt_only(path, *args, **kwargs):
        if path.name == "FAILED.json":
            raise OSError("receipt unavailable")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_receipt_only)

    with pytest.raises(ValueError, match="Cache fuente ausente"):
        run_research(
            inventory_path=inventory,
            store_path=store,
            assets_dir=assets,
            output_dir=tmp_path / "failed-receipt-write",
            sources=[],
            expected_inventory_sha256=inventory_sha,
            cache_from=tmp_path / "missing-cache",
        )


def test_pipeline_downloads_valid_original_and_records_immutable_evidence(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}")
    payload = _image_bytes("PNG", (640, 480))

    def candidate_downloader(candidate, originals_dir):
        return download_original(
            candidate,
            originals_dir,
            allowed_image_hosts={"cdn.shopify.com"},
            fetcher=lambda url: HttpResponse(200, url, {"content-type": "image/png"}, payload),
            resolver=lambda host: ["151.101.1.12"],
        )

    output = tmp_path / "research-download"
    summary = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=output,
        sources=[_LocalSource()],
        expected_inventory_sha256=inventory_sha,
        researched_at="2026-08-19T12:00:00Z",
        candidate_downloader=candidate_downloader,
    )

    found = next(
        json.loads(line)
        for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if '"status": "found_exact"' in line
    )
    digest = hashlib.sha256(payload).hexdigest()
    assert found["candidate"]["download"] == {
        "status": "downloaded",
        "sha256": digest,
        "object_name": f"{digest}.png",
        "mime": "image/png",
        "bytes": len(payload),
        "dimensions": {"width": 640, "height": 480},
        "requested_url": "https://cdn.shopify.com/fixture-1.png",
        "final_url": "https://cdn.shopify.com/fixture-1.png",
    }
    assert (output / "originals" / f"{digest}.png").read_bytes() == payload
    assert summary["downloaded_candidates"] == 1


def test_pipeline_preserves_download_rejection_and_does_not_claim_found_exact(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}")
    output = tmp_path / "research-rejected"

    summary = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=output,
        sources=[_LocalSource()],
        expected_inventory_sha256=inventory_sha,
        candidate_downloader=lambda candidate, originals: (_ for _ in ()).throw(ValueError("MIME inválido")),
    )

    record = next(
        json.loads(line)
        for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if "labenze:0000" in line
    )
    assert record["status"] == "rejected"
    assert record["reason"] == "all_exact_candidates_failed_download"
    assert record["candidates"][0]["download"] == {"status": "rejected", "reason": "MIME inválido"}
    assert summary["counts"] == {"found_exact": 0, "rejected": 1, "exhausted": 775}
    assert list((output / "originals").iterdir()) == []


def test_pipeline_rejects_cross_source_candidates_without_auto_resolution(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}")

    class SecondSource(_LocalSource):
        name = "second-local-fixture"

    output = tmp_path / "research-collision"
    summary = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=output,
        sources=[_LocalSource(), SecondSource()],
        expected_inventory_sha256=inventory_sha,
    )

    record = next(
        json.loads(line)
        for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if "labenze:0000" in line
    )
    assert record["status"] == "rejected"
    assert record["reason"] == "cross_source_exact_candidate_collision"
    assert record["candidate"] is None
    assert record["candidate_count"] == 2
    assert summary["counts"] == {"found_exact": 0, "rejected": 1, "exhausted": 775}


def test_pipeline_keeps_candidates_from_a_rejected_source_for_qa(tmp_path):
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, _inventory_rows())
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}")

    class CollidingSource(_LocalSource):
        def research(self, row):
            base = super().research(row)
            if base.status == "found_exact":
                return CandidateEnumeration("rejected", base.candidates, "variant_sku_collision")
            return base

    output = tmp_path / "research-rejected-evidence"
    run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=output,
        sources=[CollidingSource()],
        expected_inventory_sha256=inventory_sha,
    )
    record = next(
        json.loads(line)
        for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if "labenze:0000" in line
    )

    assert record["status"] == "rejected"
    assert record["reason"] == "variant_sku_collision"
    assert record["candidate_count"] == 1
    assert record["candidates"][0]["approved"] is False


def test_pipeline_rejects_inventory_sku_collisions_before_source_matching(tmp_path):
    rows = _inventory_rows()
    for index, name in enumerate(("AMITHA", "AMITHA CON BRAZOS", "AMITHA BANCO")):
        rows[index] = {
            **rows[index],
            "sku": "155-22700-000",
            "source_code": "155-22700-000",
            "name": name,
        }
    inventory = tmp_path / "inputs" / "inventory.jsonl"
    inventory.parent.mkdir()
    inventory_sha = _write_inventory(inventory, rows)
    store = tmp_path / "store" / "db.json"
    assets = tmp_path / "store" / "assets"
    assets.mkdir(parents=True)
    store.write_text("{}")
    calls = []

    class ExhaustedSource:
        def research(self, row):
            calls.append(row["internal_id"])
            return CandidateEnumeration("exhausted", [], "none")

    output = tmp_path / "research-inventory-collision"
    summary = run_research(
        inventory_path=inventory,
        store_path=store,
        assets_dir=assets,
        output_dir=output,
        sources=[ExhaustedSource()],
        expected_inventory_sha256=inventory_sha,
    )
    records = [json.loads(line) for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    collisions = [record for record in records if record["reason"] == "inventory_identity_collision"]

    assert len(collisions) == 3
    assert all(record["status"] == "rejected" for record in collisions)
    assert all(record["internal_id"] not in calls for record in collisions)
    assert summary["counts"] == {"found_exact": 0, "rejected": 3, "exhausted": 773}


def test_requiez_source_caches_listing_and_fetches_detail_only_for_unique_exact_codes(tmp_path):
    calls = []
    payloads = {
        "https://api-productos.requiez.com/productos": [
            {"_id": "uuid-1", "code": "RM-9025N-NG", "shortCode": "RM-9025N-NG"},
            {"_id": "uuid-2", "code": "RI-50", "shortCode": "RI-50"},
        ],
        "https://api-productos.requiez.com/producto/code/RM-9025N-NG": {
            "_id": "uuid-1",
            "code": "RM-9025N-NG",
            "shortCode": "RM-9025N-NG",
            "imgs": [{"url": "https://api-productos.requiez.com/img/rm.webp"}],
        },
        "https://api-productos.requiez.com/producto/code/RI-50": {
            "_id": "uuid-2",
            "code": "RI-50",
            "shortCode": "RI-50",
            "imgs": [{"url": "https://api-productos.requiez.com/img/ri.webp"}],
        },
    }

    def transport(url):
        calls.append(url)
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payloads[url]).encode())

    source = RequiezSource(CachedHttpClient(tmp_path / "cache", transport=transport))
    first = source.research(
        {"supplier": "requiez", "internal_id": "requiez:rm", "sku": "RM-9025N/NG", "source_code": "RM-9025N/NG"}
    )
    second = source.research(
        {"supplier": "requiez", "internal_id": "requiez:ri", "sku": "RI-50", "source_code": "RI-50"}
    )
    missing = source.research(
        {"supplier": "requiez", "internal_id": "requiez:jun", "sku": "RE-1063M", "source_code": "RE-1063M"}
    )

    assert first.status == second.status == "found_exact"
    assert first.candidates[0].product_url == "https://requiez.com/producto/RM-9025N-NG"
    assert missing.status == "exhausted"
    assert calls.count("https://api-productos.requiez.com/productos") == 1
    assert all("RE-1063" not in url for url in calls)


def test_requiez_source_rejects_detail_with_a_different_source_id(tmp_path):
    listing_url = "https://api-productos.requiez.com/productos"
    detail_url = "https://api-productos.requiez.com/producto/code/RI-50"
    payloads = {
        listing_url: [{"_id": "uuid-list", "code": "RI-50", "shortCode": "RI-50"}],
        detail_url: {
            "_id": "uuid-other",
            "code": "RI-50",
            "shortCode": "RI-50",
            "imgs": [{"url": "https://requiez.com/images/ri.webp"}],
        },
    }
    source = RequiezSource(
        CachedHttpClient(
            tmp_path / "cache",
            transport=lambda url: HttpResponse(
                200, url, {"content-type": "application/json"}, json.dumps(payloads[url]).encode()
            ),
        )
    )

    result = source.research(
        {"supplier": "requiez", "internal_id": "requiez:ri", "sku": "RI-50", "source_code": "RI-50"}
    )

    assert result.status == "rejected"
    assert result.reason == "detail_identity_mismatch"


def test_shopify_source_paginates_once_and_reuses_cache_for_all_rows(tmp_path):
    calls = []
    pages = {
        "https://nogalbeat.com/products.json?limit=250&page=1": {
            "products": [
                {
                    "id": 1,
                    "handle": "areta",
                    "vendor": "Labenze",
                    "variants": [{"id": 11, "sku": "LAB-1"}],
                    "images": [{"id": 111, "src": "https://nogalbeat.com/cdn/lab-1.png", "variant_ids": [11]}],
                }
            ]
        },
        "https://nogalbeat.com/products.json?limit=250&page=2": {"products": []},
    }

    def transport(url):
        calls.append(url)
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(pages[url]).encode())

    source = ShopifySource(
        CachedHttpClient(tmp_path / "cache", transport=transport),
        source_name="nogalbeat.com",
        storefront_url="https://nogalbeat.com",
    )
    found = source.research(
        {"supplier": "labenze", "internal_id": "labenze:lab-1", "sku": "LAB-1", "source_code": "LAB-1"}
    )
    exhausted = source.research(
        {"supplier": "labenze", "internal_id": "labenze:lab-2", "sku": "LAB-2", "source_code": "LAB-2"}
    )

    assert found.status == "found_exact"
    assert exhausted.status == "exhausted"
    assert calls == list(pages)


def test_shopify_source_rejects_duplicate_exact_sku_variants(tmp_path):
    products = {
        "products": [
            {
                "id": 1,
                "handle": "areta",
                "vendor": "Labenze",
                "variants": [{"id": 11, "sku": "LAB-1"}, {"id": 12, "sku": "LAB-1"}],
                "images": [
                    {"id": 111, "src": "https://cdn.shopify.com/a.png", "variant_ids": [11]},
                    {"id": 112, "src": "https://cdn.shopify.com/b.png", "variant_ids": [12]},
                ],
            }
        ]
    }

    def transport(url):
        payload = products if "page=1" in url else {"products": []}
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payload).encode())

    source = ShopifySource(
        CachedHttpClient(tmp_path / "cache", transport=transport),
        source_name="nogalbeat.com",
        storefront_url="https://nogalbeat.com",
    )
    result = source.research(
        {"supplier": "labenze", "internal_id": "labenze:lab-1", "sku": "LAB-1", "source_code": "LAB-1"}
    )

    assert result.status == "rejected"
    assert result.reason == "variant_sku_collision"
    assert len(result.candidates) == 2


def test_3r_kl_prefix_allows_spacing_only_for_normalized_labenze_vendor():
    row = {"sku": "101-0220G", "source_code": "101-0220G", "internal_id": "labenze:101-0220g"}
    products = [
        {
            "id": 10,
            "handle": "areta",
            "vendor": " LABENZE ",
            "variants": [{"id": 101, "sku": " KL - 101-0220G"}],
            "images": [{"src": "https://cdn.shopify.com/exact.jpg", "variant_ids": [101]}],
        }
    ]

    assert len(
        enumerate_shopify_candidates(
            row, products, source_name="3rin.com.mx", storefront_url="https://3rin.com.mx"
        )
    ) == 1


def test_labenze_legacy_enumerates_but_never_matches_a_family_by_name(tmp_path):
    calls = []
    listing_url = "https://test.diagrama.labenze.com/productos"
    payload = [{"id": "family-1", "nombre": "ARETA", "imagenes": []}]

    def transport(url):
        calls.append(url)
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payload).encode())

    source = LabenzeLegacySource(
        CachedHttpClient(tmp_path / "cache", transport=transport), enumerate_details=False
    )
    row = {
        "supplier": "labenze",
        "internal_id": "labenze:101-0220g",
        "sku": "101-0220G",
        "source_code": "101-0220G",
        "name": "ARETA",
    }

    assert source.research(row) == CandidateEnumeration(
        "exhausted", [], "legacy_family_has_no_explicit_sku_configuration_binding"
    )
    assert calls == [listing_url]


def test_labenze_legacy_caches_each_uuid_detail_once_without_claiming_exactness(tmp_path):
    listing_url = "https://test.diagrama.labenze.com/productos"
    detail_url = "https://test.diagrama.labenze.com/productos/family-1"
    payloads = {
        listing_url: [{"id": "family-1", "nombre": "ARETA", "imagenes": []}],
        detail_url: {
            "id": "family-1",
            "nombre": "ARETA",
            "imagenes": [{"id": "image-1", "url": "https://labenze.com/areta.png", "tipo": "aislada"}],
        },
    }
    calls = []

    def transport(url):
        calls.append(url)
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payloads[url]).encode())

    source = LabenzeLegacySource(CachedHttpClient(tmp_path / "cache", transport=transport))
    row = {"supplier": "labenze", "internal_id": "labenze:x", "sku": "X-1", "name": "ARETA"}

    source.research(row)
    source.research(row)

    assert calls == [listing_url, detail_url]


def test_script_help_uses_main_guard_without_touching_network():
    result = subprocess.run(
        [sys.executable, "scripts/research_labenze_requiez_images.py", "--help"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--inventory" in result.stdout
    assert "--offline" in result.stdout


def test_woocommerce_accepts_only_unique_labenze_variation_sku_with_own_image(tmp_path):
    def transport(url):
        if "type=variation" in url and url.endswith("page=1"):
            payload = [
                {
                    "id": 201,
                    "parent": 100,
                    "type": "variation",
                    "sku": "LAB-1",
                    "permalink": "https://arterio.mx/producto/areta/?attribute=color-azul",
                    "variation": "Azul",
                    "images": [{"id": 301, "src": "https://arterio.mx/wp-content/lab-1.jpg"}],
                }
            ]
        elif "type=variation" in url:
            payload = []
        elif url.endswith("page=1"):
            payload = [{"id": 100, "brands": [{"name": "Labenze"}], "sku": "", "images": []}]
        else:
            payload = []
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payload).encode())

    source = WooCommerceSource(CachedHttpClient(tmp_path / "cache", transport=transport))
    result = source.research(
        {"supplier": "labenze", "internal_id": "labenze:lab-1", "sku": "LAB-1", "source_code": "LAB-1"}
    )

    assert result.status == "found_exact"
    assert result.candidates[0].matched_field == "variation.sku"
    assert result.candidates[0].image_source_url.endswith("lab-1.jpg")


def test_woocommerce_rejects_repeated_sku_across_color_variations(tmp_path):
    def transport(url):
        if "type=variation" in url and url.endswith("page=1"):
            payload = [
                {"id": 201, "parent": 100, "type": "variation", "sku": "LAB-1", "permalink": "https://arterio.mx/a", "images": [{"src": "https://arterio.mx/a.jpg"}]},
                {"id": 202, "parent": 100, "type": "variation", "sku": "LAB-1", "permalink": "https://arterio.mx/b", "images": [{"src": "https://arterio.mx/b.jpg"}]},
            ]
        elif "type=variation" in url:
            payload = []
        elif url.endswith("page=1"):
            payload = [{"id": 100, "brands": [{"name": "LABENZE"}]}]
        else:
            payload = []
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payload).encode())

    result = WooCommerceSource(CachedHttpClient(tmp_path / "cache", transport=transport)).research(
        {"supplier": "labenze", "internal_id": "labenze:lab-1", "sku": "LAB-1", "source_code": "LAB-1"}
    )

    assert result.status == "rejected"
    assert result.reason == "variation_sku_collision"


def test_woocommerce_respects_total_pages_header_without_requesting_invalid_extra_page(tmp_path):
    calls = []

    def transport(url):
        calls.append(url)
        if not url.endswith("page=1"):
            raise AssertionError("no debe pedir una página posterior a X-WP-TotalPages")
        if "type=variation" in url:
            payload = []
        else:
            payload = [{"id": 100, "brands": [{"name": "Labenze"}]}]
        return HttpResponse(
            200,
            url,
            {"content-type": "application/json", "X-WP-TotalPages": "1"},
            json.dumps(payload).encode(),
        )

    result = WooCommerceSource(CachedHttpClient(tmp_path / "cache", transport=transport)).research(
        {"supplier": "labenze", "internal_id": "labenze:x", "sku": "X-1", "source_code": "X-1"}
    )

    assert result.status == "exhausted"
    assert len(calls) == 2


def test_infiniti_requires_external_exact_id_sku_signature_binding(tmp_path):
    wp_url = "https://www.infinitidesign.it/wp-json/wp/v2/product?lang=en&per_page=100&page=1"
    woo_url = "https://www.infinitidesign.it/wp-json/wc/store/v1/products/7967"
    payloads = {
        wp_url: [
            {
                "id": 7967,
                "slug": "pure-loop-mono-4-legs",
                "link": "https://www.infinitidesign.it/en/product/pure-loop-mono-4-legs/",
                "lang": "en",
                "title": {"rendered": "Pure Loop Mono 4 legs"},
            }
        ],
        woo_url: {
            "id": 7967,
            "permalink": "https://www.infinitidesign.it/en/product/pure-loop-mono-4-legs/",
            "sku": "",
            "images": [
                {
                    "id": 900,
                    "src": "https://www.infinitidesign.it/wp-content/uploads/pure-loop.jpg",
                    "alt": "Pure Loop Mono 4 legs",
                }
            ],
        },
    }

    def transport(url):
        payload = payloads.get(url, [])
        return HttpResponse(200, url, {"content-type": "application/json"}, json.dumps(payload).encode())

    row = {
        "supplier": "labenze",
        "internal_id": "labenze:pure-loop",
        "sku": "INF-PL-4",
        "source_code": "INF-PL-4",
        "name": "PURE LOOP MONO 4 PATAS",
        "visual_signature": {"sha256": "a" * 64, "fields": {}},
    }
    binding = {
        "internal_id": "labenze:pure-loop",
        "sku": "INF-PL-4",
        "visual_signature_sha256": "a" * 64,
        "wp_product_id": 7967,
    }

    found = InfinitiSource(
        CachedHttpClient(tmp_path / "cache", transport=transport), bindings=[binding]
    ).research(row)

    assert found.status == "found_exact"
    assert found.candidates[0].product_url.endswith("/pure-loop-mono-4-legs/")
    assert found.candidates[0].image_source_url.startswith(
        "https://www.infinitidesign.it/wp-content/uploads/"
    )
    assert found.candidates[0].evidence["binding_visual_signature_sha256"] == "a" * 64


def test_infiniti_does_not_match_by_family_name_or_incomplete_binding(tmp_path):
    source = InfinitiSource(
        CachedHttpClient(
            tmp_path / "cache",
            transport=lambda url: HttpResponse(200, url, {"content-type": "application/json"}, b"[]"),
        ),
        bindings=[{"name": "PURE LOOP", "wp_product_id": 7967}],
    )
    row = {
        "supplier": "labenze",
        "internal_id": "labenze:pure-loop",
        "sku": "INF-PL-4",
        "source_code": "INF-PL-4",
        "name": "PURE LOOP MONO 4 PATAS",
        "visual_signature": {"sha256": "a" * 64, "fields": {}},
    }

    assert source.research(row) == CandidateEnumeration(
        "exhausted", [], "no_curated_infiniti_configuration_binding"
    )
