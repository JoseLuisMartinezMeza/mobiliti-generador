from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping
from urllib.parse import quote, urlsplit


DEFAULT_MONDECASA_LINKS_PATH = (
    Path(__file__).resolve().parent / "data" / "mondecasa_links.v1.json"
)
_PRODUCT_PREFIX = "https://www.mondecasa.com.sg/all-products/"
_COLLECTION_SITE = "https://www.mondecasa.com"
_FALLBACK_URL = "https://www.mondecasa.com/products"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_REFERENCE = re.compile(r"[A-Z0-9]+")


class MondecasaLinkResourceError(ValueError):
    """El índice oficial de Mondecasa no cumple su contrato cerrado."""


@dataclass(frozen=True)
class MondecasaLinkIndex:
    resource_fingerprint: str
    product_urls: tuple[str, ...]
    url_ids_by_reference: Mapping[str, tuple[int, ...]]
    collection_links: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class MondecasaLinkResolution:
    product_url: str
    status: Literal["exact_index", "collection_index", "catalog_fallback"]
    evidence: Mapping[str, object]

    @property
    def metadata(self) -> dict:
        return {
            "status": self.status,
            "evidence": dict(self.evidence),
        }


def _fail(code: str):
    raise MondecasaLinkResourceError(code)


def _require_object(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(code)
    return value


def _normalize_reference(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return "".join(re.findall(r"[A-Z0-9]", text.upper()))


def _collection_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.upper().split())


def _validate_https_url(value: object, *, product: bool) -> str:
    url = _require_text(value, "MONDECASA_URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        _fail("MONDECASA_URL")
    expected_host = "www.mondecasa.com.sg" if product else "www.mondecasa.com"
    valid_path = (
        parsed.path.startswith("/all-products/")
        if product
        else parsed.path == "/products"
        or re.fullmatch(r"/collection/[1-9][0-9]*/[^/]+\.html", parsed.path) is not None
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.netloc != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not valid_path
    ):
        _fail("MONDECASA_URL")
    return f"https://{expected_host}{quote(parsed.path, safe='/-._~')}"


def build_mondecasa_link_index(resource: object) -> MondecasaLinkIndex:
    root = _require_object(
        resource,
        {
            "schema_version",
            "artifact_kind",
            "generated_on",
            "production_input",
            "workbook",
            "official_sources",
            "provenance",
            "counts",
            "official_product_slugs",
            "reference_url_ids",
            "collection_links",
        },
        "MONDECASA_RESOURCE",
    )
    fingerprint = hashlib.sha256(
        json.dumps(root, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    if (
        root["schema_version"] != "1.1"
        or root["artifact_kind"] != "official_product_link_audit"
        or root["production_input"] is not False
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(root["generated_on"])) is None
    ):
        _fail("MONDECASA_METADATA")

    workbook = _require_object(root["workbook"], {"path", "sha256", "sheets"}, "MONDECASA_WORKBOOK")
    if (
        not isinstance(workbook["path"], str)
        or _SHA256.fullmatch(str(workbook["sha256"])) is None
        or workbook["sheets"] != ["MONDECASA", "PAVILIONS"]
    ):
        _fail("MONDECASA_WORKBOOK")

    sources = _require_object(
        root["official_sources"],
        {"product_sitemap", "product_url_prefix", "collection_site", "collection_index"},
        "MONDECASA_SOURCES",
    )
    if (
        sources["product_sitemap"] != "https://www.mondecasa.com.sg/sitemap.xml"
        or sources["product_url_prefix"] != _PRODUCT_PREFIX
        or sources["collection_site"] != _COLLECTION_SITE
        or sources["collection_index"] != _FALLBACK_URL
    ):
        _fail("MONDECASA_SOURCES")
    provenance = root["provenance"]
    if not isinstance(provenance, dict) or not provenance or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in provenance.items()
    ):
        _fail("MONDECASA_PROVENANCE")

    counts = root["counts"]
    if not isinstance(counts, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for key, value in counts.items()
        if key != "matches_by_sheet"
    ):
        _fail("MONDECASA_COUNTS")
    sheets = counts.get("matches_by_sheet")
    if (
        not isinstance(sheets, dict)
        or set(sheets) != {"MONDECASA", "PAVILIONS"}
        or sum(sheets.values()) != counts.get("matched_records")
        or counts.get("unique_url_records", 0) + counts.get("multi_url_records", 0)
        != counts.get("matched_records")
    ):
        _fail("MONDECASA_COUNTS")

    slugs = root["official_product_slugs"]
    if (
        not isinstance(slugs, list)
        or len(slugs) != counts.get("matched_official_product_urls")
        or len(set(slugs)) != len(slugs)
        or any(not isinstance(slug, str) or _SLUG.fullmatch(slug) is None for slug in slugs)
    ):
        _fail("MONDECASA_PRODUCTS")
    product_urls = tuple(
        _validate_https_url(f"{_PRODUCT_PREFIX}{slug}", product=True) for slug in slugs
    )

    raw_references = root["reference_url_ids"]
    if (
        not isinstance(raw_references, dict)
        or len(raw_references) != counts.get("matched_normalized_references")
    ):
        _fail("MONDECASA_REFERENCES")
    references = {}
    for reference, raw_ids in raw_references.items():
        if (
            not isinstance(reference, str)
            or _REFERENCE.fullmatch(reference) is None
            or _normalize_reference(reference) != reference
            or not isinstance(raw_ids, list)
            or not raw_ids
            or any(isinstance(value, bool) or not isinstance(value, int) for value in raw_ids)
            or raw_ids != sorted(set(raw_ids))
            or any(value < 0 or value >= len(product_urls) for value in raw_ids)
        ):
            _fail("MONDECASA_REFERENCES")
        references[reference] = tuple(raw_ids)

    raw_collections = root["collection_links"]
    if not isinstance(raw_collections, dict) or len(raw_collections) != counts.get("collections"):
        _fail("MONDECASA_COLLECTIONS")
    collections = {}
    for name, raw_link in raw_collections.items():
        row = _require_object(raw_link, {"url", "status"}, "MONDECASA_COLLECTION")
        key = _collection_key(name)
        status = _require_text(row["status"], "MONDECASA_COLLECTION")
        url = _validate_https_url(row["url"], product=False)
        if key != name or key in collections or not (
            status == "exact_collection"
            or status == "generic_products_index"
            or status.startswith("alias:")
        ):
            _fail("MONDECASA_COLLECTION")
        collections[key] = (url, status)

    return MondecasaLinkIndex(
        fingerprint,
        product_urls,
        MappingProxyType(dict(sorted(references.items()))),
        MappingProxyType(dict(sorted(collections.items()))),
    )


def load_mondecasa_link_index(
    path: Path = DEFAULT_MONDECASA_LINKS_PATH,
) -> MondecasaLinkIndex:
    try:
        resource = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MondecasaLinkResourceError("MONDECASA_RESOURCE_READ") from exc
    return build_mondecasa_link_index(resource)


def resolve_mondecasa_link(
    source_code: object,
    collection: object,
    index: MondecasaLinkIndex,
) -> MondecasaLinkResolution:
    normalized_code = _normalize_reference(source_code)
    if normalized_code in index.url_ids_by_reference:
        references = [normalized_code]
    else:
        contained = [
            reference
            for reference in index.url_ids_by_reference
            if reference in normalized_code
        ]
        references = [
            reference
            for reference in contained
            if not any(reference != other and reference in other for other in contained)
        ]
    url_ids = sorted(
        {
            url_id
            for reference in references
            for url_id in index.url_ids_by_reference[reference]
        }
    )
    urls = [index.product_urls[url_id] for url_id in url_ids]
    if len(urls) == 1:
        return MondecasaLinkResolution(
            urls[0],
            "exact_index",
            MappingProxyType(
                {"normalized_source_code": normalized_code, "references": references}
            ),
        )

    collection_key = _collection_key(collection)
    collection_link = index.collection_links.get(collection_key)
    if collection_link is not None and collection_link[0] != _FALLBACK_URL:
        return MondecasaLinkResolution(
            collection_link[0],
            "collection_index",
            MappingProxyType(
                {
                    "normalized_source_code": normalized_code,
                    "references": references,
                    "distinct_product_urls": urls,
                    "collection": collection_key,
                    "collection_status": collection_link[1],
                }
            ),
        )

    return MondecasaLinkResolution(
        _FALLBACK_URL,
        "catalog_fallback",
        MappingProxyType(
            {
                "normalized_source_code": normalized_code,
                "references": references,
                "distinct_product_urls": urls,
                "collection": collection_key,
                "reason": "ambiguous_or_missing_product_link",
            }
        ),
    )
