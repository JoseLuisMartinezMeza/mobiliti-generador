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


DEFAULT_LUMBRO_LINKS_PATH = Path(__file__).resolve().parent / "data" / "lumbro_links.v1.json"
_OFFICIAL_HOST = "www.lumbromx.com"
_RESOURCE_FIELDS = {"schema_version", "official_host", "products", "categories", "fallback_url"}
_PRODUCT_FIELDS = {"model_key", "url"}
_CATEGORY_FIELDS = {"category_key", "url"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class LumbroLinkResourceError(ValueError):
    """El recurso local de enlaces oficiales Lumbro no es válido."""


@dataclass(frozen=True)
class LumbroLinkIndex:
    resource_fingerprint: str
    product_urls_by_model: Mapping[str, str]
    category_urls_by_category: Mapping[str, str]
    fallback_url: str


@dataclass(frozen=True)
class LumbroLinkResolution:
    url: str
    status: Literal["exact_index", "collection_index", "catalog_fallback"]
    model_key: str
    category_key: str

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "status": self.status,
            "model_key": self.model_key,
            "category_key": self.category_key,
        }


def _fail(code: str) -> None:
    raise LumbroLinkResourceError(code)


def _require_object(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(code)
    return value


def normalize_lumbro_key(value: object) -> str:
    """Normaliza una clave para coincidencias exactas, sin crear slugs."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))


def _validate_url(value: object) -> str:
    url = _require_text(value, "LUMBRO_URL")
    if any(character.isspace() for character in url):
        _fail("LUMBRO_URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise LumbroLinkResourceError("LUMBRO_URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != _OFFICIAL_HOST
        or parsed.netloc != _OFFICIAL_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        _fail("LUMBRO_URL")
    return url


def _canonical_resource_bytes(resource: object) -> bytes:
    try:
        return json.dumps(
            resource,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LumbroLinkResourceError("LUMBRO_RESOURCE") from exc


def build_lumbro_link_index(resource: object) -> LumbroLinkIndex:
    """Valida y compila la manifestación local, sin E/S ni red."""

    root = _require_object(resource, _RESOURCE_FIELDS, "LUMBRO_RESOURCE")
    if root["schema_version"] != 1 or root["official_host"] != _OFFICIAL_HOST:
        _fail("LUMBRO_RESOURCE")

    raw_products = root["products"]
    if not isinstance(raw_products, list):
        _fail("LUMBRO_PRODUCTS")
    products: dict[str, str] = {}
    for raw_product in raw_products:
        product = _require_object(raw_product, _PRODUCT_FIELDS, "LUMBRO_MODEL")
        key = _require_text(product["model_key"], "LUMBRO_MODEL")
        if key != normalize_lumbro_key(key) or not key or key in products:
            _fail("LUMBRO_MODEL")
        products[key] = _validate_url(product["url"])

    raw_categories = root["categories"]
    if not isinstance(raw_categories, list):
        _fail("LUMBRO_CATEGORIES")
    categories: dict[str, str] = {}
    for raw_category in raw_categories:
        category = _require_object(raw_category, _CATEGORY_FIELDS, "LUMBRO_CATEGORY")
        key = _require_text(category["category_key"], "LUMBRO_CATEGORY")
        if key != normalize_lumbro_key(key) or not key or key in categories:
            _fail("LUMBRO_CATEGORY")
        categories[key] = _validate_url(category["url"])

    return LumbroLinkIndex(
        resource_fingerprint=hashlib.sha256(_canonical_resource_bytes(root)).hexdigest(),
        product_urls_by_model=MappingProxyType(dict(sorted(products.items()))),
        category_urls_by_category=MappingProxyType(dict(sorted(categories.items()))),
        fallback_url=_validate_url(root["fallback_url"]),
    )


def _validated_mapping(value: object, code: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        _fail(code)
    entries: dict[str, str] = {}
    for raw_key, raw_url in value.items():
        key = _require_text(raw_key, code)
        if key != normalize_lumbro_key(key) or key in entries:
            _fail(code)
        entries[key] = _validate_url(raw_url)
    return MappingProxyType(dict(sorted(entries.items())))


def _validated_index(index: object) -> LumbroLinkIndex:
    if not isinstance(index, LumbroLinkIndex):
        _fail("LUMBRO_INDEX")
    fingerprint = _require_text(index.resource_fingerprint, "LUMBRO_INDEX")
    if _SHA256.fullmatch(fingerprint) is None:
        _fail("LUMBRO_INDEX")
    return LumbroLinkIndex(
        resource_fingerprint=fingerprint,
        product_urls_by_model=_validated_mapping(
            index.product_urls_by_model, "LUMBRO_MODEL"
        ),
        category_urls_by_category=_validated_mapping(
            index.category_urls_by_category, "LUMBRO_CATEGORY"
        ),
        fallback_url=_validate_url(index.fallback_url),
    )


def load_lumbro_link_index(path: Path = DEFAULT_LUMBRO_LINKS_PATH) -> LumbroLinkIndex:
    try:
        resource = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LumbroLinkResourceError("LUMBRO_RESOURCE_READ") from exc
    return build_lumbro_link_index(resource)


def resource_fingerprint(path: Path = DEFAULT_LUMBRO_LINKS_PATH) -> str:
    """Devuelve el SHA-256 de los bytes JSON canónicos de la manifestación."""

    return load_lumbro_link_index(path).resource_fingerprint


def resolve_lumbro_link(
    model: object,
    category: object,
    index: LumbroLinkIndex | None = None,
) -> LumbroLinkResolution:
    """Resuelve sólo modelos y categorías explícitamente indexados."""

    index = _validated_index(index or load_lumbro_link_index())
    model_key = normalize_lumbro_key(model)
    category_key = normalize_lumbro_key(category)
    product_url = index.product_urls_by_model.get(model_key)
    if product_url is not None:
        return LumbroLinkResolution(product_url, "exact_index", model_key, category_key)
    category_url = index.category_urls_by_category.get(category_key)
    if category_url is not None:
        return LumbroLinkResolution(category_url, "collection_index", model_key, category_key)
    return LumbroLinkResolution(index.fallback_url, "catalog_fallback", model_key, category_key)
