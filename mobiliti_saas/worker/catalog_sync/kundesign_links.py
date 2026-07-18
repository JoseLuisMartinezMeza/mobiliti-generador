from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping
from urllib.parse import urlsplit


DEFAULT_KUNDESIGN_LINKS_PATH = (
    Path(__file__).resolve().parent / "data" / "kundesign_links.v1.json"
)
_FALLBACK_URL = "https://www.kundesign.com/products"
_RESOURCE_FIELDS = {
    "schema_version",
    "captured_at",
    "source_url",
    "fallback_url",
    "provenance",
    "products",
    "overrides",
}
_PRODUCT_FIELDS = {"collection", "type", "detail_url"}
_OVERRIDE_FIELDS = {
    "source_key",
    "target_key",
    "detail_url",
    "reason",
}
_PROVENANCE_FIELDS = {
    "algorithm",
    "source_sha256",
    "source_product_count",
}
_DETAIL_PATH = re.compile(r"/s/[1-9][0-9]*/.+\.html")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class KundesignLinkResourceError(ValueError):
    """El recurso Kundesign no cumple el esquema sanitizado y cerrado."""


@dataclass(frozen=True)
class KundesignProductLink:
    collection: str
    type: str
    detail_url: str


@dataclass(frozen=True)
class KundesignLinkOverride:
    source_key: str
    target_key: str
    detail_url: str
    reason: str


@dataclass(frozen=True)
class KundesignLinkIndex:
    schema_version: int
    resource_fingerprint: str
    captured_at: str
    source_url: str
    fallback_url: str
    provenance: Mapping[str, object]
    products: tuple[KundesignProductLink, ...]
    detail_urls_by_key: Mapping[str, frozenset[str]]
    overrides_by_key: Mapping[str, KundesignLinkOverride]


@dataclass(frozen=True)
class KundesignLinkResolution:
    product_url: str
    status: Literal["exact_index", "curated_override", "catalog_fallback"]
    key: str
    evidence: Mapping[str, object]

    @property
    def metadata(self) -> dict:
        return {
            "status": self.status,
            "key": self.key,
            "evidence": dict(self.evidence),
        }


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))


def normalize_product_key(collection: object, description: object) -> str:
    """Construye `colección|tipo` desde la primera línea, sin aproximaciones."""

    normalized_collection = _fold(collection)
    description_lines = str(description or "").splitlines()
    first_line = description_lines[0] if description_lines else ""
    normalized_type = _fold(first_line)
    collection_prefix = f"{normalized_collection} "
    if normalized_type.startswith(collection_prefix):
        normalized_type = normalized_type[len(collection_prefix) :]
    elif normalized_type == normalized_collection:
        normalized_type = ""
    return f"{normalized_collection}|{normalized_type}"


def _canonical_resource_key(value: object) -> str:
    if not isinstance(value, str) or value.count("|") != 1:
        raise KundesignLinkResourceError("KUNDESIGN_OVERRIDE_KEY")
    collection, product_type = value.split("|", 1)
    canonical = f"{_fold(collection)}|{_fold(product_type)}"
    if value != canonical or not canonical.split("|", 1)[0] or not canonical.split("|", 1)[1]:
        raise KundesignLinkResourceError("KUNDESIGN_OVERRIDE_KEY")
    return canonical


def _require_object(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise KundesignLinkResourceError(code)
    return value


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise KundesignLinkResourceError(code)
    return value


def _validate_url(value: object, *, fallback: bool = False) -> str:
    url = _require_text(value, "KUNDESIGN_URL")
    if any(character.isspace() for character in url):
        raise KundesignLinkResourceError("KUNDESIGN_URL")
    if fallback:
        if url != _FALLBACK_URL:
            raise KundesignLinkResourceError("KUNDESIGN_FALLBACK_URL")
        return url
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise KundesignLinkResourceError("KUNDESIGN_URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.kundesign.com"
        or parsed.netloc != "www.kundesign.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or _DETAIL_PATH.fullmatch(parsed.path) is None
    ):
        raise KundesignLinkResourceError("KUNDESIGN_URL")
    return url


def build_kundesign_link_index(resource: object) -> KundesignLinkIndex:
    """Valida y compila un recurso ya cargado; no realiza E/S ni red."""

    root = _require_object(resource, _RESOURCE_FIELDS, "KUNDESIGN_RESOURCE")
    resource_fingerprint = hashlib.sha256(
        json.dumps(
            root,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    if root["schema_version"] != 1:
        raise KundesignLinkResourceError("KUNDESIGN_SCHEMA_VERSION")
    captured_at = _require_text(root["captured_at"], "KUNDESIGN_CAPTURED_AT")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", captured_at) is None:
        raise KundesignLinkResourceError("KUNDESIGN_CAPTURED_AT")
    source_url = _validate_url(root["source_url"], fallback=True)
    fallback_url = _validate_url(root["fallback_url"], fallback=True)

    provenance = _require_object(
        root["provenance"], _PROVENANCE_FIELDS, "KUNDESIGN_PROVENANCE"
    )
    if (
        provenance["algorithm"] != "sha256"
        or not isinstance(provenance["source_sha256"], str)
        or _SHA256.fullmatch(provenance["source_sha256"]) is None
        or isinstance(provenance["source_product_count"], bool)
        or not isinstance(provenance["source_product_count"], int)
        or provenance["source_product_count"] < 0
    ):
        raise KundesignLinkResourceError("KUNDESIGN_PROVENANCE")

    raw_products = root["products"]
    if not isinstance(raw_products, list):
        raise KundesignLinkResourceError("KUNDESIGN_PRODUCTS")
    products = []
    urls_by_key: dict[str, set[str]] = {}
    for raw_product in raw_products:
        row = _require_object(raw_product, _PRODUCT_FIELDS, "KUNDESIGN_PRODUCT")
        collection = _require_text(row["collection"], "KUNDESIGN_PRODUCT")
        product_type = _require_text(row["type"], "KUNDESIGN_PRODUCT")
        detail_url = _validate_url(row["detail_url"])
        key = f"{_fold(collection)}|{_fold(product_type)}"
        if not key.split("|", 1)[0] or not key.split("|", 1)[1]:
            raise KundesignLinkResourceError("KUNDESIGN_PRODUCT")
        products.append(KundesignProductLink(collection, product_type, detail_url))
        urls_by_key.setdefault(key, set()).add(detail_url)
    if provenance["source_product_count"] != len(products):
        raise KundesignLinkResourceError("KUNDESIGN_PROVENANCE_COUNT")

    raw_overrides = root["overrides"]
    if not isinstance(raw_overrides, list):
        raise KundesignLinkResourceError("KUNDESIGN_OVERRIDES")
    overrides = {}
    for raw_override in raw_overrides:
        row = _require_object(raw_override, _OVERRIDE_FIELDS, "KUNDESIGN_OVERRIDE")
        source_key = _canonical_resource_key(row["source_key"])
        target_key = _canonical_resource_key(row["target_key"])
        detail_url = _validate_url(row["detail_url"])
        reason = _require_text(row["reason"], "KUNDESIGN_OVERRIDE")
        target_urls = urls_by_key.get(target_key, set())
        if (
            source_key in overrides
            or len(target_urls) != 1
            or detail_url not in target_urls
            or len(urls_by_key.get(source_key, set())) == 1
        ):
            raise KundesignLinkResourceError("KUNDESIGN_OVERRIDE")
        overrides[source_key] = KundesignLinkOverride(
            source_key, target_key, detail_url, reason
        )

    frozen_urls = MappingProxyType(
        {key: frozenset(urls) for key, urls in sorted(urls_by_key.items())}
    )
    return KundesignLinkIndex(
        schema_version=1,
        resource_fingerprint=resource_fingerprint,
        captured_at=captured_at,
        source_url=source_url,
        fallback_url=fallback_url,
        provenance=MappingProxyType(dict(provenance)),
        products=tuple(products),
        detail_urls_by_key=frozen_urls,
        overrides_by_key=MappingProxyType(dict(sorted(overrides.items()))),
    )


def load_kundesign_link_index(
    path: Path = DEFAULT_KUNDESIGN_LINKS_PATH,
) -> KundesignLinkIndex:
    try:
        resource = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KundesignLinkResourceError("KUNDESIGN_RESOURCE_READ") from exc
    return build_kundesign_link_index(resource)


def resolve_kundesign_link(
    collection: object,
    description: object,
    index: KundesignLinkIndex,
) -> KundesignLinkResolution:
    """Resuelve sólo por clave exacta, override explícito o fallback oficial."""

    key = normalize_product_key(collection, description)
    distinct_urls = sorted(index.detail_urls_by_key.get(key, ()))
    if len(distinct_urls) == 1:
        return KundesignLinkResolution(
            distinct_urls[0],
            "exact_index",
            key,
            MappingProxyType({"distinct_detail_urls": distinct_urls}),
        )

    override = index.overrides_by_key.get(key)
    if override is not None:
        return KundesignLinkResolution(
            override.detail_url,
            "curated_override",
            key,
            MappingProxyType(
                {
                    "target_key": override.target_key,
                    "detail_url": override.detail_url,
                    "reason": override.reason,
                }
            ),
        )

    reason = "ambiguous_exact_detail" if distinct_urls else "no_exact_detail"
    return KundesignLinkResolution(
        index.fallback_url,
        "catalog_fallback",
        key,
        MappingProxyType(
            {
                "reason": reason,
                "distinct_detail_urls": distinct_urls,
            }
        ),
    )
