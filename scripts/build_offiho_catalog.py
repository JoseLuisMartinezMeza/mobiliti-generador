from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence
import argparse
import concurrent.futures
import hashlib
import json
import re
import urllib.parse
import urllib.request

import xlrd
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY_URL = "https://www.offiho.com/existencias.xls"
DEFAULT_INVENTORY_PATH = PROJECT_ROOT / ".cache" / "offiho-existencias.xls"
DEFAULT_CACHE_PATH = PROJECT_ROOT / ".cache" / "offiho-products.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "mobiliti_saas" / "quote_engine" / "data" / "offiho_catalog.json"
OFFICIAL_HOSTS = frozenset({"offiho.com", "www.offiho.com", "offihoblack.com", "www.offihoblack.com"})
SITE_SEEDS = (
    "https://www.offiho.com/",
    "https://www.offiho.com/econosillas/",
    "https://www.offihoblack.com/",
)
USER_AGENT = "Mobiliti Offiho Catalog Builder/1.0"
CODE_RE = re.compile(r"\b[A-Z]{2,}(?:-\d+[A-Z0-9]*)+", re.ASCII)
PRICE_RE = re.compile(r"\$\s*([\d][\d,]*)")
VARIANT_WORDS = frozenset(
    {
        "ALUMINIO",
        "ARENA",
        "AZUL",
        "BAJA",
        "BEIGE",
        "BLANCA",
        "BLANCO",
        "CAFE",
        "CALIDO",
        "CEREZO",
        "CHOCOLATE",
        "CLARO",
        "CORAL",
        "CROMO",
        "GRIS",
        "MADERA",
        "MARINO",
        "MATE",
        "NARANJA",
        "NEGRA",
        "NEGRO",
        "OXFORD",
        "PLUS",
        "ROBLE",
        "ROJA",
        "ROJO",
        "VERDE",
        "VINO",
    }
)


@dataclass(frozen=True)
class OffihoIdentity:
    code: str
    name: str
    variant: str


def normalize_space(value: Any) -> str:
    return " ".join(str("" if value is None else value).split())


def decimal_value(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    text = normalize_space(value).replace("$", "").replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def extract_offiho_identity(inventory_key: str) -> OffihoIdentity:
    normalized = normalize_space(inventory_key).upper()
    match = CODE_RE.search(normalized)
    if match:
        code = match.group(0)
        before = normalized[: match.start()].strip()
        after = normalized[match.end() :].strip()
    else:
        parts = normalized.split(maxsplit=1)
        code = parts[0] if parts else ""
        before = ""
        after = parts[1] if len(parts) == 2 else ""

    after_tokens = after.replace("/", " / ").split()
    variant_tokens: list[str] = []
    while after_tokens:
        token = after_tokens[0]
        normalized_token = token.strip("/")
        if normalized_token not in VARIANT_WORDS and token != "/":
            break
        variant_tokens.append(after_tokens.pop(0))
    variant = normalize_variant(" ".join(variant_tokens))
    name = normalize_space(" ".join(part for part in (before, " ".join(after_tokens)) if part))
    return OffihoIdentity(code=code, name=name, variant=variant)


def parse_inventory_xls(path: Path) -> list[dict[str, Any]]:
    items, _ = _parse_inventory_xls(path)
    return items


def _parse_inventory_xls(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    workbook = xlrd.open_workbook(path)
    try:
        sheet = workbook.sheet_by_name("Publicaci\u00f3n")
    except xlrd.biffh.XLRDError:
        raise RuntimeError("No se encontro la hoja Publicaci\u00f3n") from None
    items: list[dict[str, Any]] = []
    by_inventory_key: dict[str, dict[str, Any]] = {}
    source_row_count = 0
    duplicate_row_count = 0
    for row in range(5, sheet.nrows):
        inventory_key = normalize_space(sheet.cell_value(row, 1)).upper()
        stock = decimal_value(sheet.cell_value(row, 2))
        if not inventory_key or stock is None:
            continue
        source_row_count += 1
        identity = extract_offiho_identity(inventory_key)
        pieces_per_box = decimal_value(sheet.cell_value(row, 3)) or Decimal("1")
        unit_price = decimal_value(sheet.cell_value(row, 4))
        item = {
            "inventory_key": inventory_key,
            "code": identity.code,
            "name": identity.name,
            "variant": identity.variant,
            "unit": "PZA",
            "pieces_per_box": json_number(pieces_per_box),
            "available_quantity": json_number(stock),
            "unit_price": json_number(unit_price or Decimal("0")),
            "price_source": "inventory" if unit_price is not None else "missing",
        }
        existing = by_inventory_key.get(inventory_key)
        if existing is not None:
            if existing == item:
                duplicate_row_count += 1
                continue
            raise RuntimeError(f"La clave {inventory_key} aparece con datos distintos")
        by_inventory_key[inventory_key] = item
        items.append(item)
    return items, {
        "source_row_count": source_row_count,
        "duplicate_row_count": duplicate_row_count,
        "unique_item_count": len(items),
    }


def normalize_variant(value: str) -> str:
    text = normalize_space(value).upper()
    text = re.sub(r"\s*/\s*", " ", text)
    return text


def price_key(code: str, variant: str) -> str:
    return normalize_space(f"{code} {normalize_variant(variant)}")


def extract_pdf_pages(paths: Sequence[Path]) -> list[str]:
    pages: list[str] = []
    for path in paths:
        reader = PdfReader(path)
        pages.extend(page.extract_text() or "" for page in reader.pages)
    return pages


def parse_pdf_price_index(paths: Sequence[Path]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for page in extract_pdf_pages(paths):
        for match in CODE_RE.finditer(page):
            code = match.group(0)
            window = page[match.end() : match.end() + 220]
            next_code = CODE_RE.search(window)
            if next_code:
                window = window[: next_code.start()]
            price_match = PRICE_RE.search(window)
            if not price_match:
                continue
            variant = _variant_from_pdf_text(page[match.end() : match.end() + price_match.start()])
            key = price_key(code, variant)
            amount = Decimal(price_match.group(1).replace(",", ""))
            existing = prices.get(key)
            if existing is None or existing == amount:
                prices[key] = amount
    return prices


def _variant_from_pdf_text(value: str) -> str:
    words = re.findall(r"[A-Za-z\u00c0-\u017f]+", value.upper())
    variants = [word for word in words if word in VARIANT_WORDS]
    return normalize_variant(" ".join(variants))


def match_official_product(identity: OffihoIdentity, candidates: Sequence[dict[str, Any]]) -> dict[str, str]:
    if not identity.code:
        return {"url": "", "image_url": "", "match_status": "unmatched", "source_updated_at": ""}
    matches = []
    for candidate in candidates:
        url = str(candidate.get("url", ""))
        image_url = str(candidate.get("image_url", ""))
        codes = {str(code).upper() for code in candidate.get("codes", [])}
        if identity.code not in codes or not is_official_url(url):
            continue
        if image_url and not is_official_url(image_url):
            image_url = ""
        matches.append(
            {
                "url": url,
                "image_url": image_url,
                "match_status": "official_code_match",
                "source_updated_at": str(candidate.get("source_updated_at", "")),
            }
        )
    if not matches:
        return {"url": "", "image_url": "", "match_status": "unmatched", "source_updated_at": ""}
    return sorted(matches, key=lambda product: (not bool(product["image_url"]), product["url"]))[0]


def is_official_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in OFFICIAL_HOSTS and not parsed.username and not parsed.password


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []
        self.meta: dict[str, str] = {}
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag == "img" and values.get("src"):
            self.images.append(values["src"])
        elif tag == "meta":
            key = values.get("property", values.get("name", "")).lower()
            content = values.get("content", "")
            if key and content:
                self.meta[key] = content

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def build_site_product_index(cache: dict[str, Any]) -> dict[str, dict[str, str]]:
    cached = cache.get("site_index")
    if isinstance(cached, dict):
        return {
            str(code): value
            for code, value in cached.items()
            if isinstance(value, dict) and is_official_url(str(value.get("url", "")))
        }

    pages = cache.setdefault("site_pages", {})
    if not isinstance(pages, dict):
        pages = {}
        cache["site_pages"] = pages
    records: list[dict[str, Any]] = []
    for url in SITE_SEEDS:
        record = _cached_or_fetch_page(url, pages)
        if record:
            records.append(record)

    discovered = sorted(
        {
            link
            for record in records
            for link in record.get("links", [])
            if is_official_url(link) and link not in SITE_SEEDS
        }
    )[:500]
    pending = [url for url in discovered if not isinstance(pages.get(url), dict)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        for url, record in zip(pending, executor.map(_fetch_official_page, pending)):
            pages[url] = record
    for url in discovered:
        record = pages.get(url)
        if isinstance(record, dict):
            if record:
                records.append(record)

    index: dict[str, dict[str, str]] = {}
    for record in records:
        for code in record.get("codes", []):
            candidate = {
                "url": str(record.get("url", "")),
                "image_url": str(record.get("image_url", "")),
                "source_updated_at": str(record.get("source_updated_at", "")),
            }
            existing = index.get(code)
            if existing is None or (not existing.get("image_url") and candidate["image_url"]):
                index[code] = candidate
    cache["site_index"] = index
    return index


def _cached_or_fetch_page(url: str, pages: dict[str, Any]) -> dict[str, Any]:
    record = pages.get(url)
    if isinstance(record, dict):
        return record
    record = _fetch_official_page(url)
    pages[url] = record
    return record


def _fetch_official_page(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if not is_official_url(response.geturl()):
                return {}
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return {}
            payload = response.read(1_500_000).decode("utf-8", errors="replace")
            source_updated_at = response.headers.get("Last-Modified", "")
    except (OSError, ValueError, urllib.error.URLError):
        return {}

    parser = _PageParser()
    parser.feed(payload)
    links = sorted(
        {
            urllib.parse.urldefrag(urllib.parse.urljoin(url, link))[0]
            for link in parser.links
            if is_official_url(urllib.parse.urldefrag(urllib.parse.urljoin(url, link))[0])
        }
    )
    image_candidates = [parser.meta.get("og:image", ""), *parser.images]
    image_url = next(
        (
            urllib.parse.urljoin(url, image)
            for image in image_candidates
            if is_official_url(urllib.parse.urljoin(url, image))
        ),
        "",
    )
    page_text = " ".join(parser.text)
    metadata_text = " ".join(parser.meta.values())
    return {
        "url": url,
        "links": links,
        "codes": sorted(set(CODE_RE.findall(unescape(f"{page_text} {metadata_text}")))),
        "image_url": image_url,
        "source_updated_at": source_updated_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def build_catalog(
    inventory_path: Path,
    pdf_paths: Sequence[Path],
    cache_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    cache = _load_cache(cache_path)
    inventory_bytes = inventory_path.read_bytes()
    items, inventory_audit = _parse_inventory_xls(inventory_path)
    pdf_prices = parse_pdf_price_index(pdf_paths)
    site_index = build_site_product_index(cache)
    site_candidates = [
        {"codes": [code], **product}
        for code, product in site_index.items()
    ]
    for item in items:
        identity = OffihoIdentity(item["code"], item["name"], item["variant"])
        if item["price_source"] == "missing":
            amount = pdf_prices.get(price_key(identity.code, identity.variant))
            if amount is not None:
                item["unit_price"] = json_number(amount)
                item["price_source"] = "pdf_exact"
        product = match_official_product(identity, site_candidates)
        item["product_url"] = product["url"]
        item["image_url"] = product["image_url"]
        item["match_status"] = product["match_status"]
        item["source_updated_at"] = product["source_updated_at"]

    result = {
        "source_hash": hashlib.sha256(inventory_bytes).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "inventory": inventory_path.name,
            "pdfs": [path.name for path in pdf_paths],
        },
        "total": len(items),
        **inventory_audit,
        "out_of_stock": sum(item["available_quantity"] == 0 for item in items),
        "inventory_prices": sum(item["price_source"] == "inventory" for item in items),
        "pdf_prices": sum(item["price_source"] == "pdf_exact" for item in items),
        "official_images": sum(bool(item["image_url"]) for item in items),
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def download_inventory(url: str, output_path: Path) -> Path:
    if not is_official_url(url):
        raise ValueError("La URL de inventario debe ser HTTPS de un host oficial Offiho")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.ms-excel"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if not is_official_url(response.geturl()):
            raise ValueError("La descarga de inventario redirigio fuera de los hosts oficiales")
        if response.headers.get_content_type() not in {"application/vnd.ms-excel", "application/octet-stream"}:
            raise ValueError("La URL de inventario no devolvio un archivo XLS")
        payload = response.read(10 * 1024 * 1024 + 1)
    if len(payload) > 10 * 1024 * 1024:
        raise ValueError("El inventario excede el limite permitido")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye el catalogo Offiho para Mobiliti")
    parser.add_argument("--inventory-url", default=DEFAULT_INVENTORY_URL)
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY_PATH))
    parser.add_argument("--pdf", action="append", default=[])
    parser.add_argument("--cache", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    inventory_path = download_inventory(args.inventory_url, Path(args.inventory))
    result = build_catalog(
        inventory_path,
        [Path(path) for path in args.pdf],
        Path(args.cache),
        Path(args.output),
    )
    print(
        json.dumps(
            {
                "total": result["total"],
                "out_of_stock": result["out_of_stock"],
                "inventory_prices": result["inventory_prices"],
                "pdf_prices": result["pdf_prices"],
                "official_images": result["official_images"],
                "output": args.output,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
