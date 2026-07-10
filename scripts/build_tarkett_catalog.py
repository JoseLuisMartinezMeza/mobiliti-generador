from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import argparse
import concurrent.futures
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "Inventario Tarkett- 6 Julio .xls"
DEFAULT_OUTPUT = PROJECT_ROOT / "mobiliti_saas" / "quote_engine" / "data" / "tarkett_catalog.json"
DEFAULT_CACHE = PROJECT_ROOT / ".cache" / "tarkett-products.json"
TARKETT_API = "https://tarkett.com.mx/wp-json/wp/v2/product"
TARKETT_PROFESSIONAL_SITEMAP_INDEX = "https://profesional.tarkett.es/es_ES/sitemap_index.xml"
TARKETT_PROFESSIONAL_EXTRA_URLS = [
    "https://profesional.tarkett.es/es_ES/categor%C3%ADa-es_C02-suelos-vinilicos",
    "https://profesional.tarkett.es/es_ES/categor%C3%ADa-es_C01001-tarkett-suelos-vinilicos-homogeneos-trafico-intenso",
    "https://profesional.tarkett.es/es_ES/categor%C3%ADa-es_C01002-suelos-vinilicos-heterogeneos",
    "https://profesional.tarkett.es/es_ES/categor%C3%ADa-es_C01018-moqueta-en-losetas",
]
TARKETT_SECONDARY_OFFICIAL_PAGES = {
    "aurea_tech": "https://tarkett.com.mx/linea-2/linea-aurea-tech/",
    "ambienta": "https://tarkett.com.mx/linea-2/linea-ambienta/",
    "argentina_accessories": "https://tarkett.com.ar/productos/accesorios/",
}
USER_AGENT = "Mobiliti Tarkett Catalog Builder/1.0"


@dataclass
class InventoryRow:
    code: str
    name: str
    unit: str
    available_quantity: Decimal


class FirstTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.done = False
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        tag = tag.lower()
        if tag == "table":
            self.table_depth += 1
            return
        if self.table_depth != 1:
            return
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if self.done:
            return
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            text = " ".join("".join(self.current_cell).split())
            self.current_row.append(unescape(text))
            self.in_cell = False
        elif tag == "tr" and self.in_row and self.table_depth == 1:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0:
                self.done = True

    def handle_data(self, data: str) -> None:
        if self.in_cell and not self.done:
            self.current_cell.append(data)


def parse_inventory_html(path: Path) -> list[InventoryRow]:
    parser = FirstTableParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    if not parser.rows:
        raise RuntimeError(f"No se encontro tabla de inventario en {path}")
    headers = [_normalize_header(value) for value in parser.rows[0]]
    expected = ["clave", "producto", "unidad base", "cant disponible"]
    if headers[:4] != expected:
        raise RuntimeError(f"Encabezados inesperados: {parser.rows[0]}")
    rows: list[InventoryRow] = []
    for raw in parser.rows[1:]:
        if len(raw) < 4:
            continue
        code = _code(raw[0])
        name = raw[1].strip()
        unit = raw[2].strip()
        quantity = _decimal(raw[3])
        if not code or not name:
            continue
        rows.append(InventoryRow(code=code, name=name, unit=unit, available_quantity=quantity))
    return rows


def build_catalog(
    source: Path,
    output: Path,
    cache_path: Path,
    *,
    no_network: bool = False,
    validate_sku: bool = True,
    delay_seconds: float = 0.12,
    limit: int | None = None,
) -> dict[str, Any]:
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    rows = parse_inventory_html(source)
    if limit:
        rows = rows[:limit]
    cache = _load_cache(cache_path)
    product_index = _fetch_all_products(cache, no_network=no_network)
    sku_index = _build_sku_match_index(product_index, cache, no_network=no_network)
    items = []
    for row in rows:
        match = resolve_tarkett_product(
            row,
            cache,
            no_network=no_network,
            validate_sku=validate_sku,
            product_index=product_index,
            sku_index=sku_index,
        )
        items.append(
            {
                "code": row.code,
                "name": row.name,
                "unit": row.unit,
                "available_quantity": _json_number(row.available_quantity),
                "product_url": match.get("product_url", ""),
                "image_url": match.get("image_url", ""),
                "match_status": match.get("match_status", "unmatched"),
            }
        )
        if not no_network:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not no_network and delay_seconds > 0:
            time.sleep(delay_seconds)

    result = {
        "source_file": source.name,
        "source_hash": source_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "items": items,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def resolve_tarkett_product(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
    validate_sku: bool = True,
    product_index: list[dict[str, Any]] | None = None,
    sku_index: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    cache_key = f"{row.code}|{row.name}"
    cached = cache.get(cache_key)
    if cached and cached.get("product_url") and cached.get("image_url"):
        return cached
    fallback = {"product_url": "", "image_url": "", "match_status": "unmatched"}
    if sku_index is None:
        sku_index = _build_sku_match_index(product_index or _fetch_all_products(cache, no_network=no_network), cache, no_network=no_network)
    sku_match = sku_index.get(row.code)
    if sku_match:
        cache[cache_key] = sku_match
        return sku_match
    media_sku_match = _resolve_media_asset(row, cache, no_network=no_network, name_fallback=False)
    if media_sku_match:
        cache[cache_key] = media_sku_match
        return media_sku_match
    if cached and no_network:
        return cached
    if no_network:
        return fallback
    norm_name = _normalize_text(row.name)
    best: dict[str, Any] | None = None
    best_score = -1
    for term in _search_terms(row.name):
        products = _fetch_products(term, cache)
        for product in products:
            title = _strip_tags(product.get("title", {}).get("rendered", ""))
            score = _score_match(norm_name, title, term)
            if score > best_score:
                best = product
                best_score = score
        if best_score >= 90:
            break
    if not best or best_score < 35:
        media_name_match = _resolve_media_asset(row, cache, no_network=no_network, name_fallback=True)
        professional_match = (
            _resolve_professional_es_asset(row, cache, no_network=no_network)
            or _resolve_professional_es_collection_asset(row, cache, no_network=no_network)
            or _resolve_secondary_official_asset(row, cache, no_network=no_network)
        )
        match = media_name_match or professional_match or fallback
        cache[cache_key] = match
        return match

    media = (best.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
    product_url = str(best.get("link", "") or "")
    image_url = str(media.get("source_url", "") or "")
    status = "name_match"
    if validate_sku and product_url and not _page_contains_code(product_url, row.code, cache):
        status = "name_match_no_sku"
    match = {"product_url": product_url, "image_url": image_url, "match_status": status}
    if not image_url:
        media_name_match = _resolve_media_asset(row, cache, no_network=no_network, name_fallback=True)
        if media_name_match:
            match = media_name_match
    if not match.get("image_url"):
        professional_match = (
            _resolve_professional_es_asset(row, cache, no_network=no_network)
            or _resolve_professional_es_collection_asset(row, cache, no_network=no_network)
            or _resolve_secondary_official_asset(row, cache, no_network=no_network)
        )
        if professional_match:
            match = professional_match
    cache[cache_key] = match
    return match


def _fetch_all_products(cache: dict[str, Any], *, no_network: bool = False) -> list[dict[str, Any]]:
    cache_key = "all_products::wp_v2::_embed::v1"
    if cache_key in cache:
        products = cache[cache_key]
        return products if isinstance(products, list) else []
    if no_network:
        return []

    products: list[dict[str, Any]] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"{TARKETT_API}?{urllib.parse.urlencode({'per_page': '100', 'page': str(page), '_embed': '1'})}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as response:
                total_pages = int(response.headers.get("X-WP-TotalPages") or total_pages)
                page_products = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            break
        if not isinstance(page_products, list) or not page_products:
            break
        products.extend(_compact_product(product) for product in page_products)
        page += 1

    cache[cache_key] = products
    return products


def _compact_product(product: dict[str, Any]) -> dict[str, Any]:
    media = (product.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
    return {
        "id": product.get("id"),
        "slug": product.get("slug", ""),
        "link": product.get("link", ""),
        "title": {"rendered": product.get("title", {}).get("rendered", "")},
        "content": {"rendered": product.get("content", {}).get("rendered", "")},
        "excerpt": {"rendered": product.get("excerpt", {}).get("rendered", "")},
        "_embedded": {
            "wp:featuredmedia": [
                {
                    "source_url": media.get("source_url", ""),
                    "title": {"rendered": media.get("title", {}).get("rendered", "")},
                }
            ]
        },
    }


def _build_sku_match_index(
    products: list[dict[str, Any]],
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> dict[str, dict[str, str]]:
    cache_key = "sku_match_index::wp_v2::product_pages::v2"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return {
            str(code): match
            for code, match in cached.items()
            if isinstance(match, dict) and match.get("product_url") and match.get("image_url")
        }

    index: dict[str, dict[str, str]] = {}
    products_by_link = {str(product.get("link") or "").strip(): product for product in products if product.get("link")}
    if not no_network:
        _cache_product_page_codes(products_by_link, cache)

    for product in products:
        match = _product_match(product, "sku_match")
        if not match["product_url"] or not match["image_url"]:
            continue
        for code in _extract_product_codes(product, cache, no_network=no_network):
            index.setdefault(code, match)
    cache[cache_key] = index
    return index


def _cache_product_page_codes(products_by_link: dict[str, dict[str, Any]], cache: dict[str, Any]) -> None:
    pending = [link for link in products_by_link if f"product_page_codes::{link}" not in cache]
    if not pending:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(_fetch_product_page_codes, link): link for link in pending}
        for future in concurrent.futures.as_completed(futures):
            link = futures[future]
            try:
                codes = future.result()
            except Exception:
                codes = []
            cache[f"product_page_codes::{link}"] = codes


def _fetch_product_page_codes(link: str) -> list[str]:
    try:
        req = urllib.request.Request(link, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=18) as response:
            body = response.read(500_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return []
    return sorted(set(re.findall(r"(?<!\d)\d{6,10}(?!\d)", body)))


def _extract_product_codes(product: dict[str, Any], cache: dict[str, Any], *, no_network: bool = False) -> set[str]:
    media = (product.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
    text = " ".join(
        str(part or "")
        for part in (
            product.get("slug", ""),
            product.get("link", ""),
            product.get("title", {}).get("rendered", ""),
            product.get("content", {}).get("rendered", ""),
            product.get("excerpt", {}).get("rendered", ""),
            media.get("source_url", ""),
            media.get("title", {}).get("rendered", ""),
        )
    )
    codes = set(re.findall(r"(?<!\d)\d{6,10}(?!\d)", text))
    link = str(product.get("link") or "").strip()
    if not link:
        return codes

    cache_key = f"product_page_codes::{link}"
    if cache_key in cache:
        page_codes = cache[cache_key]
        if isinstance(page_codes, list):
            codes.update(str(code) for code in page_codes)
        return codes
    return codes


def _product_match(product: dict[str, Any], status: str) -> dict[str, str]:
    media = (product.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
    return {
        "product_url": str(product.get("link", "") or ""),
        "image_url": str(media.get("source_url", "") or ""),
        "match_status": status,
    }


def _fetch_products(term: str, cache: dict[str, Any]) -> list[dict[str, Any]]:
    cache_key = f"search::{_normalize_text(term)}"
    if cache_key in cache:
        return cache[cache_key]
    url = f"{TARKETT_API}?{urllib.parse.urlencode({'search': term, '_embed': '1', 'per_page': '5'})}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as response:
            products = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        products = []
    cache[cache_key] = products if isinstance(products, list) else []
    return cache[cache_key]


def _resolve_media_asset(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
    name_fallback: bool = False,
) -> dict[str, str] | None:
    for media in _fetch_media(row.code, cache, no_network=no_network):
        if row.code in _media_text(media):
            return _media_match(media, "media_sku_match")
    if not name_fallback or not _allows_media_name_fallback(row.name):
        return None

    best: dict[str, Any] | None = None
    best_score = -1
    for term in _media_search_terms(row.name):
        for media in _fetch_media(term, cache, no_network=no_network):
            score = _score_media_match(row, media)
            if score > best_score:
                best = media
                best_score = score
        if best_score >= 40:
            break
    if best is None or best_score < 12:
        return None
    return _media_match(best, "media_name_match")


def _fetch_media(term: str, cache: dict[str, Any], *, no_network: bool = False) -> list[dict[str, Any]]:
    clean_term = " ".join(str(term or "").split())
    if not clean_term:
        return []
    cache_key = f"media::{_normalize_text(clean_term)}::v1"
    if cache_key in cache:
        media = cache[cache_key]
        return media if isinstance(media, list) else []
    if no_network:
        return []
    url = f"https://tarkett.com.mx/wp-json/wp/v2/media?{urllib.parse.urlencode({'search': clean_term, 'per_page': '10'})}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=12) as response:
            media = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        media = []
    clean_media = []
    for item in media if isinstance(media, list) else []:
        if str(item.get("mime_type", "")).startswith("image/") and item.get("source_url"):
            clean_media.append(
                {
                    "link": item.get("link", ""),
                    "source_url": item.get("source_url", ""),
                    "slug": item.get("slug", ""),
                    "title": {"rendered": item.get("title", {}).get("rendered", "")},
                    "mime_type": item.get("mime_type", ""),
                }
            )
    cache[cache_key] = clean_media
    return clean_media


def _media_match(media: dict[str, Any], status: str) -> dict[str, str]:
    source_url = str(media.get("source_url", "") or "")
    return {
        "product_url": str(media.get("link", "") or source_url),
        "image_url": source_url,
        "match_status": status,
    }


def _media_text(media: dict[str, Any]) -> str:
    return " ".join(
        str(part or "")
        for part in (
            media.get("link", ""),
            media.get("source_url", ""),
            media.get("slug", ""),
            media.get("title", {}).get("rendered", ""),
        )
    )


def _allows_media_name_fallback(name: str) -> bool:
    norm_name = _normalize_text(name)
    blocked = {"catalogo", "catalog", "chaveiro", "box", "bucket", "buckets", "ultrabond"}
    if any(token in norm_name.split() for token in blocked):
        return False
    return norm_name.startswith("loseta ") or norm_name.startswith("piso ")


def _media_search_terms(name: str) -> list[str]:
    clean = re.sub(r"\b\d+(?:[.,]\d+)?\s*mm\b", " ", name, flags=re.I)
    clean = re.sub(r"\b\d+(?:[.,]\d+)?x\d+(?:[.,]\d+)?\s*mm\b", " ", clean, flags=re.I)
    stop = {
        "piso",
        "loseta",
        "aurea",
        "tech",
        "pro",
        "ambienta",
        "stone",
        "square",
        "set",
        "acoustic",
    }
    words = [word for word in re.split(r"[^A-Za-zÀ-ÿ0-9]+", clean) if len(word) > 2 and _normalize_text(word) not in stop]
    terms = []
    if len(words) >= 2:
        terms.append(" ".join(words[-2:]))
    if words:
        terms.append(words[-1])
    terms.append(clean.strip())
    unique = []
    seen = set()
    for term in terms:
        key = _normalize_text(term)
        if key and key not in seen:
            seen.add(key)
            unique.append(" ".join(str(term).split()))
    return unique


def _score_media_match(row: InventoryRow, media: dict[str, Any]) -> int:
    if row.code and row.code in _media_text(media):
        return 100
    media_tokens = set(_normalize_text(_media_text(media)).split())
    row_tokens = set(_normalize_text(row.name).split())
    ignored = {"piso", "loseta", "aurea", "tech", "pro", "mm"}
    row_tokens = {token for token in row_tokens if token not in ignored and len(token) > 2}
    return len(media_tokens & row_tokens) * 12


def _resolve_professional_es_asset(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> dict[str, str] | None:
    for url in _professional_es_candidate_urls(row, cache, no_network=no_network):
        snapshot = _fetch_professional_page_snapshot(url, cache, no_network=no_network)
        if not snapshot:
            continue
        codes = {str(code) for code in snapshot.get("codes", [])}
        if row.code not in codes and row.code not in str(snapshot.get("text", "")):
            continue
        image_url = str(snapshot.get("image_url", "") or "")
        if not image_url:
            continue
        return {
            "product_url": url,
            "image_url": image_url,
            "match_status": "professional_es_sku_match",
        }
    return None


def _resolve_professional_es_collection_asset(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> dict[str, str] | None:
    for url in _professional_es_collection_candidate_urls(row, cache, no_network=no_network):
        snapshot = _fetch_professional_page_snapshot(url, cache, no_network=no_network)
        if not snapshot:
            continue
        image_url = str(snapshot.get("image_url", "") or "")
        if not image_url:
            continue
        return {
            "product_url": url,
            "image_url": image_url,
            "match_status": "professional_es_collection_match",
        }
    return None


def _resolve_secondary_official_asset(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> dict[str, str] | None:
    for url, status in _secondary_official_candidates(row.name):
        snapshot = _fetch_secondary_official_page_snapshot(url, cache, no_network=no_network)
        if not snapshot:
            continue
        image_url = _secondary_image_by_code(row.code, snapshot)
        if image_url:
            return {"product_url": url, "image_url": image_url, "match_status": f"{status}_sku_match"}

        image_url = _secondary_image_by_name(row.name, snapshot)
        if image_url:
            return {"product_url": url, "image_url": image_url, "match_status": f"{status}_name_match"}

        if status == "tarkett_mx_collection":
            image_url = str(snapshot.get("image_url", "") or "")
            if image_url:
                return {"product_url": url, "image_url": image_url, "match_status": status}
    return None


def _secondary_official_candidates(name: str) -> list[tuple[str, str]]:
    norm_name = _normalize_text(name)
    candidates: list[tuple[str, str]] = []
    if "aurea tech" in norm_name:
        candidates.append((TARKETT_SECONDARY_OFFICIAL_PAGES["aurea_tech"], "tarkett_mx_line"))
    if "catalogo ambienta" in norm_name:
        candidates.append((TARKETT_SECONDARY_OFFICIAL_PAGES["ambienta"], "tarkett_mx_collection"))
    if "ultrabond eco 4 lvt" in norm_name:
        candidates.append((TARKETT_SECONDARY_OFFICIAL_PAGES["argentina_accessories"], "tarkett_ar_accessory"))
    return candidates


def _fetch_secondary_official_page_snapshot(
    url: str,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> dict[str, Any]:
    cache_key = f"secondary_official_page::{url}::v1"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    if no_network:
        return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read(1_500_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        cache[cache_key] = {}
        return {}

    snapshot = {
        "image_url": _extract_professional_image_url(body),
        "images": _extract_image_urls(body),
        "text": " ".join(_strip_tags(body[:350_000]).split())[:30_000],
        "windows_by_code": {
            code: body[max(0, match.start() - 3_000) : match.end() + 1_000]
            for code in sorted(set(re.findall(r"(?<!\d)\d{6,10}(?!\d)", body)))
            for match in [re.search(rf"(?<!\d){re.escape(code)}(?!\d)", body)]
            if match
        },
    }
    cache[cache_key] = snapshot
    return snapshot


def _secondary_image_by_code(code: str, snapshot: dict[str, Any]) -> str:
    window = str(snapshot.get("windows_by_code", {}).get(code, "") or "")
    images = _extract_image_urls(window)
    return _best_secondary_image(images)


def _secondary_image_by_name(name: str, snapshot: dict[str, Any]) -> str:
    tokens = _secondary_image_name_tokens(name)
    if not tokens:
        return ""
    scored: list[tuple[int, str]] = []
    for image_url in snapshot.get("images", []):
        norm_url = _normalize_text(str(image_url))
        score = sum(30 for token in tokens if token in norm_url)
        if score:
            scored.append((score + _secondary_image_quality_score(str(image_url)), str(image_url)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1] if scored else ""


def _secondary_image_name_tokens(name: str) -> list[str]:
    norm_name = _normalize_text(name)
    if "maiorca" in norm_name or "mallorca" in norm_name:
        return ["maiorca", "mallorca"]
    return []


def _extract_image_urls(html: str) -> list[str]:
    urls = re.findall(
        r"""(?:src|data-src|content)=["'](https?://[^"']+\.(?:jpg|jpeg|png|webp)(?:\?[^"']*)?)["']""",
        html,
        flags=re.I,
    )
    urls.extend(re.findall(r"https?://[^\"'<> ]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'<> ]*)?", html, flags=re.I))
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        clean = unescape(str(url)).strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique


def _best_secondary_image(images: list[str]) -> str:
    scored = [( _secondary_image_quality_score(image_url), image_url) for image_url in images if _is_secondary_official_image(image_url)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1] if scored else ""


def _is_secondary_official_image(url: str) -> bool:
    norm_url = _normalize_text(url)
    blocked = {"logo", "favicon", "placeholder"}
    if any(token in norm_url for token in blocked):
        return False
    return any(host in norm_url for host in ("tarkett com mx", "tarkett com ar", "consolidado tarkett com br"))


def _secondary_image_quality_score(url: str) -> int:
    norm_url = _normalize_text(url)
    score = 0
    if "180x180" in norm_url or "100x100" in norm_url or "150x150" in norm_url:
        score -= 20
    if "280x280" in norm_url or "300x300" in norm_url:
        score += 5
    if "600x600" in norm_url or "large" in norm_url:
        score += 20
    if "elementor thumbs" in norm_url:
        score -= 10
    return score


def _professional_es_candidate_urls(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> list[str]:
    tokens = _professional_es_tokens(row.name)
    if not tokens:
        return []
    scored: list[tuple[int, str]] = []
    for url in _fetch_professional_sitemap_urls(cache, no_network=no_network):
        decoded = urllib.parse.unquote(str(url))
        norm_url = _normalize_text(decoded)
        url_tokens = set(norm_url.split())
        score = 0
        for token in tokens:
            if token in url_tokens:
                score += 20 + min(len(token), 10)
            elif len(token) >= 4 and token in norm_url:
                score += 8
        if score:
            scored.append((score, str(url)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in scored[:8]]


def _professional_es_collection_candidate_urls(
    row: InventoryRow,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> list[str]:
    hint_groups = _professional_es_collection_hint_groups(row.name)
    if not hint_groups:
        return []
    scored: list[tuple[int, int, str]] = []
    for url in _fetch_professional_sitemap_urls(cache, no_network=no_network):
        norm_url = _normalize_text(urllib.parse.unquote(str(url)))
        for priority, tokens in enumerate(hint_groups):
            if all(token in norm_url.split() or token in norm_url for token in tokens):
                score = 1000 - priority * 100 + sum(min(len(token), 12) for token in tokens)
                scored.append((score, str(url).count("/"), str(url)))
                break
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    urls: list[str] = []
    seen: set[str] = set()
    for _, _, url in scored:
        if url not in seen:
            seen.add(url)
            urls.append(url)
        if len(urls) >= 4:
            break
    return urls


def _professional_es_collection_hint_groups(name: str) -> list[list[str]]:
    norm_name = _normalize_text(name)
    hints: list[list[str]] = []

    if "square set acoustic" in norm_name:
        hints.append(["c000117", "id", "square", "cement"])
    if "inspiration" in norm_name and "lvt" in norm_name:
        hints.append(["c000115", "id", "inspiration", "loose", "lay"])
    if "inspiration" in norm_name and ("manta" in norm_name or "mantas" in norm_name):
        hints.append(["c02", "suelos", "vinilicos"])
        hints.append(["c01002", "suelos", "vinilicos", "heterogeneos"])
        hints.append(["c01001", "suelos", "vinilicos", "homogeneos"])
    if "catalogo" in norm_name and "desso" in norm_name:
        hints.append(["c01018", "moqueta", "losetas"])
    if "eclipse premium" in norm_name:
        hints.append(["c000043", "eclipse", "premium"])
    if "iq eminent" in norm_name:
        hints.append(["c000119", "iq", "eminent"])
    if "linha iq" in norm_name or norm_name == "catalogo linha iq 2024":
        hints.append(["iq", "range", "commercial", "homogeneous", "flooring"])
        hints.append(["c000119", "iq", "eminent"])
    if "rodape" in norm_name or "rodapie" in norm_name:
        hints.append(["c000210", "rodapie", "rigido", "decorativo"])
        hints.append(["c000200", "rodapies", "pvc", "preformados"])
    if "standard plus" in norm_name:
        hints.append(["c000259", "standard", "plus"])
        hints.append(["c000258", "standard", "plus"])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for hint in hints:
        key = tuple(hint)
        if key not in seen:
            seen.add(key)
            unique.append(hint)
    return unique


def _fetch_professional_sitemap_urls(cache: dict[str, Any], *, no_network: bool = False) -> list[str]:
    cache_key = "professional_es_sitemap_urls::v2"
    cached = cache.get(cache_key)
    if isinstance(cached, list):
        return [str(url) for url in cached if url]
    if no_network:
        return []

    sitemap_urls = _fetch_sitemap_locs(TARKETT_PROFESSIONAL_SITEMAP_INDEX, max_bytes=200_000)
    product_urls: list[str] = []
    for sitemap_url in sitemap_urls or ["https://profesional.tarkett.es/es_ES/sitemap_1.xml"]:
        if not str(sitemap_url).endswith(".xml"):
            continue
        product_urls.extend(_fetch_sitemap_locs(str(sitemap_url), max_bytes=5_000_000))
    product_urls.extend(TARKETT_PROFESSIONAL_EXTRA_URLS)

    seen: set[str] = set()
    urls: list[str] = []
    for url in product_urls:
        clean_url = str(url).strip()
        if not clean_url or clean_url in seen:
            continue
        seen.add(clean_url)
        urls.append(clean_url)
    cache[cache_key] = urls
    return urls


def _fetch_sitemap_locs(url: str, *, max_bytes: int) -> list[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(max_bytes).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return []
    return [unescape(match).strip() for match in re.findall(r"<loc>(.*?)</loc>", body, flags=re.I | re.S)]


def _fetch_professional_page_snapshot(
    url: str,
    cache: dict[str, Any],
    *,
    no_network: bool = False,
) -> dict[str, Any]:
    cache_key = f"professional_es_page::{url}::v1"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    if no_network:
        return {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read(1_500_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        cache[cache_key] = {}
        return {}

    snapshot = {
        "codes": sorted(set(re.findall(r"(?<!\d)\d{6,10}(?!\d)", body))),
        "image_url": _extract_professional_image_url(body),
        "title": _extract_html_title(body),
        "text": " ".join(_strip_tags(body[:250_000]).split())[:20_000],
    }
    cache[cache_key] = snapshot
    return snapshot


def _extract_professional_image_url(html: str) -> str:
    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ):
        match = re.search(pattern, html, flags=re.I)
        if match:
            return unescape(match.group(1)).strip()

    candidates = re.findall(r"https?://[^\"'<> ]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'<> ]*)?", html, flags=re.I)
    for candidate in candidates:
        if _is_professional_product_image(candidate):
            return unescape(candidate).strip()
    return ""


def _is_professional_product_image(url: str) -> bool:
    norm_url = _normalize_text(url)
    blocked = {"logo", "brandguidelines", "calculator", "icon", "favicon"}
    if any(token in norm_url for token in blocked):
        return False
    return "tarkett-image" in norm_url or "tarkett" in norm_url


def _extract_html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if not match:
        return ""
    return " ".join(_strip_tags(match.group(1)).split())


def _professional_es_tokens(name: str) -> list[str]:
    normalized_tokens = _normalize_text(name).split()
    collateral = {"box", "catalogo", "catalog", "chaveiro", "ultrabond"}
    if any(token in normalized_tokens for token in collateral):
        return []

    stop = {
        "piso",
        "loseta",
        "desso",
        "ess",
        "strct",
        "b1",
        "b8",
        "lvt",
        "box",
        "catalogo",
        "catalog",
        "linha",
        "chaveiro",
        "bucket",
        "buckets",
        "metro",
        "cuadrado",
        "pieza",
        "kg",
        "he",
        "ho",
    }
    tokens: list[str] = []
    for token in normalized_tokens:
        if token in stop or len(token) < 3:
            continue
        if re.fullmatch(r"\d+mm", token) or token == "0mm":
            continue
        tokens.append(token)

    has_variant_token = any(re.search(r"[a-z]+\d|\d+[a-z]", token) for token in tokens)
    has_color_token = any(re.fullmatch(r"\d{4}", token) for token in tokens)
    if not (has_variant_token and has_color_token):
        return []

    if "strct" in normalized_tokens:
        tokens.extend(["essence", "structure"])
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def _page_contains_code(url: str, code: str, cache: dict[str, Any]) -> bool:
    cache_key = f"sku::{url}::{code}"
    if cache_key in cache:
        return bool(cache[cache_key])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=6) as response:
            body = response.read(250_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        cache[cache_key] = False
        return False
    cache[cache_key] = code in body
    return bool(cache[cache_key])


def _search_terms(name: str) -> list[str]:
    clean = re.sub(r"\b\d+(?:[.,]\d+)?\s*mm\b", " ", name, flags=re.I)
    clean = re.sub(r"\b\d+(?:[.,]\d+)?x\d+(?:[.,]\d+)?\s*mm\b", " ", clean, flags=re.I)
    clean = re.sub(r"\b(piso|loseta|aurea tech|ambienta series|ambienta stone)\b", " ", clean, flags=re.I)
    terms = [name, clean.strip()]
    words = [word for word in re.split(r"[^A-Za-zÀ-ÿ0-9]+", clean) if len(word) > 2]
    if words:
        terms.append(" ".join(words[-3:]))
        terms.extend(reversed(words))
    unique = []
    seen = set()
    for term in terms:
        term = " ".join(str(term or "").split())
        key = _normalize_text(term)
        if key and key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def _score_match(norm_name: str, title: str, term: str) -> int:
    norm_title = _normalize_text(title)
    norm_term = _normalize_text(term)
    if not norm_title:
        return 0
    if norm_title in norm_name:
        return 100
    title_tokens = set(norm_title.split())
    name_tokens = set(norm_name.split())
    term_tokens = set(norm_term.split())
    overlap = len(title_tokens & name_tokens) * 10 + len(title_tokens & term_tokens) * 5
    return min(80, overlap)


def _normalize_header(value: str) -> str:
    return _normalize_text(value).replace(".", "")


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(str(value or ""))).strip()


def _code(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return re.sub(r"\s+", "", text)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral():
        return int(value)
    return float(value)


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye catalogo Tarkett para Mobiliti")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--skip-sku-validation", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = build_catalog(
        Path(args.source),
        Path(args.output),
        Path(args.cache),
        no_network=args.no_network,
        validate_sku=not args.skip_sku_validation,
        limit=args.limit,
    )
    matched = sum(1 for item in result["items"] if item["product_url"])
    print(json.dumps({"total": result["total"], "matched": matched, "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
