from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Sequence
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.parse
import urllib.request

import xlrd
import pdfplumber
from PIL import Image
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mobiliti_saas.quote_engine.offiho_inventory import (  # noqa: E402
    parse_offiho_inventory as _parse_shared_offiho_inventory,
)
from mobiliti_saas.quote_engine.offiho_spec_images import (  # noqa: E402
    extract_offiho_spec_images,
)

DEFAULT_INVENTORY_URL = "https://www.offiho.com/existencias.xls"
DEFAULT_INVENTORY_PATH = PROJECT_ROOT / ".cache" / "offiho-existencias.xls"
DEFAULT_CACHE_PATH = PROJECT_ROOT / ".cache" / "offiho-products.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "mobiliti_saas" / "quote_engine" / "data" / "offiho_catalog.json"
DEFAULT_ASSETS_DIR = PROJECT_ROOT / "mobiliti_saas" / "web" / "public" / "catalog-assets" / "offiho"
DEFAULT_ASSET_BASE_URL = "https://web-lemon-one-45.vercel.app/catalog-assets/offiho"
DEFAULT_COLOS_EXACT_MANIFEST_PATH = (
    PROJECT_ROOT / "catalog_sources" / "offiho" / "colos_exact_images.json"
)
DEFAULT_OFFIHO_EXACT_MANIFEST_PATH = (
    PROJECT_ROOT / "catalog_sources" / "offiho" / "offiho_exact_variant_images.json"
)
DEFAULT_OFFICIAL_WEB_VISUAL_EXACT_MANIFEST_PATHS = (
    PROJECT_ROOT
    / "catalog_sources"
    / "offiho"
    / "offiho_official_web_visual_exact_images.json",
    PROJECT_ROOT
    / "catalog_sources"
    / "offiho"
    / "offiho_live_visual_exact_images.json",
    PROJECT_ROOT
    / "catalog_sources"
    / "offiho"
    / "offiho_residual_visual_exact_images.json",
    PROJECT_ROOT
    / "catalog_sources"
    / "offiho"
    / "offiho_hidden_variant_exact_images.json",
)
DEFAULT_CATALOG_EXACT_CROP_MANIFEST_PATHS = (
    PROJECT_ROOT / "catalog_sources" / "offiho" / "colos_pdf_exact_images.json",
    PROJECT_ROOT / "catalog_sources" / "offiho" / "offiho_catalog_exact_crops.json",
    PROJECT_ROOT / "catalog_sources" / "offiho" / "offiho_internet_exact_images.json",
)
DEFAULT_SPEC_VISUAL_EXACT_MANIFEST_PATHS = (
    PROJECT_ROOT
    / "catalog_sources"
    / "offiho"
    / "offiho_spec_visual_independent_exact_images.json",
    PROJECT_ROOT
    / "catalog_sources"
    / "offiho"
    / "offiho_spec_auto_audited_exact_images.json",
)
DEFAULT_VISUAL_REJECTION_MANIFEST_PATH = (
    PROJECT_ROOT / "catalog_sources" / "offiho" / "offiho_visual_rejections.json"
)
DEFAULT_GENERATED_IMAGE_MANIFEST_PATH = (
    PROJECT_ROOT / "catalog_sources" / "offiho" / "offiho_generated_visual_references.json"
)
OFFIHO_HOSTS = frozenset(
    {"offiho.com", "www.offiho.com", "offihoblack.com", "www.offihoblack.com"}
)
COLOS_HOSTS = frozenset({"colos.it", "www.colos.it"})
MANAGED_ASSET_HOSTS = frozenset({"web-lemon-one-45.vercel.app"})
OFFICIAL_HOSTS = OFFIHO_HOSTS | COLOS_HOSTS
SHAREPOINT_CATALOG_HOSTS = frozenset({"mobiliti11-my.sharepoint.com"})
CATALOG_SOURCE_HOSTS = OFFICIAL_HOSTS | SHAREPOINT_CATALOG_HOSTS
OFFIHO_CATALOG_SECTIONS = (
    "directivos",
    "ejecutivos",
    "operativos",
    "industrial",
    "accesorios",
    "visitantes-interior",
    "visitantes-exterior",
    "mesas",
    "bancos",
    "confortables",
    "bancas",
    "escolar",
    "nuevos-productos",
)
DEFAULT_SITE_SEEDS = (
    "https://www.offiho.com/",
    *(f"https://www.offiho.com/{section}/" for section in OFFIHO_CATALOG_SECTIONS),
    "https://www.offiho.com/econosillas/",
    "https://www.offihoblack.com/",
    "https://www.offiho.com/econosillas/penguin-modelo-OHV-7067F",
    "https://www.offiho.com/visitantes-interior/kyos-collection/kyos-malla/",
    "https://www.offiho.com/visitantes-interior/kyos-collection/kyos-plasticos-expuestos/",
    "https://www.offiho.com/visitantes-interior/kyos-collection/kyos-semitapizadas/",
    "https://www.offiho.com/visitantes-interior/kyos-collection/kyos-tapizadas/",
)
SITE_SEEDS = DEFAULT_SITE_SEEDS
OFFIHO_SEARCH_TERMS = (
    "OHE",
    "OHV",
    "OHS",
    "OHI",
    "OHT",
    "OHR",
    "OHM",
    "OHP",
    "BRAZO",
    "GAMER",
    "PANELO",
    "QU",
    "RE",
)
USER_AGENT = "Mobiliti Offiho Catalog Builder/1.0"
CACHE_VERSION = 28
CACHE_TTL_SECONDS = 24 * 60 * 60
LEGACY_CACHE_TIMESTAMP = "1970-01-01T00:00:00+00:00"
SOURCE_MANIFEST_VERSION = 8
MAX_INVENTORY_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_VALIDATION_TIMEOUT = 10
MAX_DISCOVERED_PAGES = 2000
FIRST_LEVEL_DISCOVERY_LIMIT = 250
CODE_RE = re.compile(r"\b[A-Z]{2,}(?:-\d+[A-Z0-9]*)+", re.ASCII | re.IGNORECASE)
OFFICIAL_CODE_ALIASES = {
    # Offiho publica este modelo como OHT-337; el inventario vigente lo identifica como OHV-337.
    "OHV-337": "OHT-337",
    "OHV-338": "OHT-338",
    "OHV-339CR": "OHT-339CR",
    "OHV-340CR": "OHT-340CR",
    "OHR-2800-3P": "OHR-2800-3PCR",
    "OHR-2800-4P": "OHR-2800-4PCR",
}
OFFICIAL_NAME_ALIASES = {
    # Nombres publicados que no conservan literalmente la clave del inventario.
    "GAMER-002": ("ESCRITORIO DRAGON GAMER002",),
    "OHV-90": ("VIOLET 90",),
    "SILLA": ("SILLA ELEFANTE",),
}
OFFICIAL_BROCHURE_URL = "https://www.offiho.com/folletoeconosillas.pdf"
OFFICIAL_BROCHURE_PRODUCTS = (
    ("NOVAISO SIN BRAZOS", "econosillas-novaiso-sin-brazos.jpg", 19),
    ("NOVAISO CON BRAZOS", "econosillas-novaiso-con-brazos.jpg", 20),
    ("ISO SIN BRAZOS", "econosillas-iso-sin-brazos.jpg", 17),
    ("ISO CON BRAZOS", "econosillas-iso-con-brazos.jpg", 18),
    ("ECOGERENCIAL", "econosillas-ecogerencial.jpg", 6),
    ("ECONOMALLA", "econosillas-economalla.jpg", 11),
    ("ECOVISITA", "econosillas-ecovisita.jpg", 16),
    ("OHV 64", "econosillas-sand.jpg", 26),
)
PRICE_RE = re.compile(r"\$\s*([\d][\d,]*)")
IMAGE_EXTENSIONS = frozenset({".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
NON_PRODUCT_IMAGE_TOKENS = frozenset(
    {
        "accessor",
        "arrow",
        "banner",
        "box",
        "caja",
        "cart",
        "facebook",
        "garantia",
        "guarantee",
        "icon",
        "instagram",
        "logo",
        "menu",
        "precio",
        "price",
        "social",
        "twitter",
        "whatsapp",
    }
)
PRODUCT_IMAGE_TOKENS = frozenset({"frente", "front", "principal", "producto", "product"})
PAGE_NAME_STOP_WORDS = frozenset(
    {
        "ACCESORIOS",
        "BANCAS",
        "BANCOS",
        "CATALOGO",
        "COLLECTION",
        "CONFORTABLES",
        "DIRECTIVOS",
        "ECONOSILLAS",
        "EJECUTIVOS",
        "ESCOLAR",
        "EXTERIOR",
        "HOME",
        "INDUSTRIAL",
        "INTERIOR",
        "MESAS",
        "MODELO",
        "NUEVOS",
        "OFFIHO",
        "OPERATIVOS",
        "PRODUCTO",
        "PRODUCTOS",
        "VISITANTES",
    }
)
REQUIRED_FEATURE_WORDS = frozenset(
    {
        "ARO",
        "BRAZO",
        "BRAZOS",
        "CABECERA",
        "CONECTOR",
        "CUBIERTA",
        "ESTRUCTURA",
        "KIT",
        "PEDESTAL",
        "PISTON",
    }
)
EXCLUSIVE_ACCESSORY_FEATURE_WORDS = frozenset(
    {"ARO", "CABECERA", "CONECTOR", "CUBIERTA", "ESTRUCTURA", "KIT", "PEDESTAL", "PISTON"}
)
VARIANT_WORDS = frozenset(
    {
        "ABEDUL",
        "ACEITUNA",
        "AGAVE",
        "ALUMINIO",
        "AMARILLA",
        "AMARILLO",
        "AQUA",
        "ARENA",
        "ARENILLA",
        "AZABACHE",
        "AZUL",
        "AVOCADO",
        "BAJA",
        "BEIGE",
        "BERENJENA",
        "BLANCA",
        "BLANCO",
        "BOSQUE",
        "CAFE",
        "CALIDO",
        "CAMEL",
        "CAPUCCINO",
        "CELESTE",
        "CEREZA",
        "CEREZO",
        "CHOCOLATE",
        "CLARO",
        "CORAL",
        "CROMADA",
        "CROMADO",
        "CROMO",
        "CREMA",
        "FANGO",
        "FUCSIA",
        "GRIS",
        "GRISVERDE",
        "HIELO",
        "LADRILLO",
        "LILA",
        "MADERA",
        "MARINO",
        "MARRON",
        "MAMEY",
        "MATE",
        "MEDIO",
        "MORADO",
        "MOSTAZA",
        "NARANJA",
        "NARANANJA",
        "NEGRA",
        "NEGRO",
        "OXFORD",
        "OBSCURO",
        "OCEANO",
        "OCENAO",
        "OLIVO",
        "ORO",
        "OSCURO",
        "PANTIKAN",
        "PERLA",
        "PLATA",
        "PLUS",
        "PROFUNDO",
        "ROBLE",
        "ROJA",
        "ROJO",
        "ROSA",
        "SALMON",
        "TABACO",
        "TERRACOTA",
        "TORRENTE",
        "TRAVERTINO",
        "TURQUESA",
        "VERDE",
        "VINO",
        "ZAFIRO",
    }
)
VARIANT_CANONICAL_WORDS = {
    "AZABACHE": "NEGRO",
    "AMARILLA": "AMARILLO",
    "BLANCA": "BLANCO",
    "CEREZA": "CEREZO",
    "CROMADA": "CROMO",
    "CROMADO": "CROMO",
    "GRISVERDE": "GRIS VERDE",
    "NARANANJA": "NARANJA",
    "NEGRA": "NEGRO",
    "OBSCURO": "OSCURO",
    "OCENAO": "OCEANO",
    "ROJA": "ROJO",
}
CONFIGURATION_PREFIX_WORDS = frozenset(
    {
        "ALTA",
        "ALTO",
        "BAJA",
        "B",
        "C",
        "CB",
        "CR",
        "G",
        "GC",
        "GL",
        "KIDS",
        "LOUNGE",
        "MZ",
        "N",
        "NG",
        "NR",
        "O",
        "R",
        "V",
        "W",
    }
)
CONFIGURATION_CODE_SUFFIX_ALIASES = {
    "ALTA": "ALTA",
    "ALTO": "ALTO",
    "BAJA": "BAJA",
    "BAJO": "BAJO",
    "CB": "CB",
    "CR": "CR",
}

FINISH_CODE_WORDS = frozenset({"BD", "BF", "VB", "VD", "YB", "YF"})
FINISH_CODE_RE = re.compile(r"[A-Z]{1,2}\d+")

COLOS_COLOR_ALIASES = {
    "AUBERGINE": "BERENJENA",
    "BLACK": "NEGRO",
    "BLUE": "AZUL",
    "CREAM": "CREMA",
    "DARK BLUE": "AZUL OSCURO",
    "DARK GREEN": "VERDE OSCURO",
    "DARK GREY": "GRIS OSCURO",
    "DARK GRAY": "GRIS OSCURO",
    "FOREST GREEN": "VERDE BOSQUE",
    "GREEN": "VERDE",
    "GREY": "GRIS",
    "GRAY": "GRIS",
    "GRIGIO CALDO ECO": "GRIS CALIDO",
    "ICE BLUE": "AZUL HIELO",
    "LIGHT BLUE": "AZUL CLARO",
    "MUD": "FANGO",
    "MUSTARD": "MOSTAZA",
    "PALE BLUE": "AZUL CLARO",
    "RED": "ROJO",
    "SAND": "ARENA",
    "SENF": "MOSTAZA",
    "TERRACOTTA": "TERRACOTA",
    "TOBACCO": "TABACO",
    "WHITE": "BLANCO",
}
COLOS_FINISH_CODE_TOKENS = frozenset(
    {
        "B",
        "C",
        "F",
        "G",
        "L",
        "M",
        "MZ",
        "N",
        "P",
        "R",
        "S",
        "TE",
        "TO",
        "TR",
        "V",
        "W",
        "Y",
    }
)


@dataclass(frozen=True)
class OffihoIdentity:
    code: str
    name: str
    variant: str


def normalize_space(value: Any) -> str:
    return " ".join(str("" if value is None else value).split())


def _variant_word_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_space(value).upper())
    return re.sub(
        r"[^A-Z0-9]",
        "",
        "".join(char for char in normalized if not unicodedata.combining(char)),
    )


def _is_variant_token(value: str) -> bool:
    parts = [part for part in re.split(r"/+", value) if part]
    return bool(parts) and all(
        _variant_word_key(part) in {_variant_word_key(word) for word in VARIANT_WORDS}
        for part in parts
    )


def decimal_value(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    text = normalize_space(value).replace("$", "").replace(",", "")
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


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

    after_tokens = after.split()
    original_after_tokens = list(after_tokens)
    variant_start = next(
        (
            index
            for index, token in enumerate(after_tokens)
            if _is_variant_token(token)
            and _variant_word_key(token) not in CONFIGURATION_PREFIX_WORDS
        ),
        len(after_tokens),
    )
    configuration_tokens = after_tokens[:variant_start]
    after_tokens = after_tokens[variant_start:]
    variant_tokens: list[str] = []
    while after_tokens:
        token = after_tokens[0]
        if not _is_variant_token(token):
            break
        variant_tokens.append(after_tokens.pop(0))
    if "PLUS" in {_variant_word_key(token) for token in variant_tokens}:
        for token in original_after_tokens:
            token_key = _variant_word_key(token)
            if (
                token_key in CONFIGURATION_PREFIX_WORDS
                or not _is_variant_token(token)
                or token in variant_tokens
            ):
                continue
            variant_tokens.append(token)
    variant = normalize_variant(" ".join(variant_tokens))
    after_tokens = [token for token in after_tokens if token not in variant_tokens]
    name = normalize_space(
        " ".join(
            part
            for part in (before, " ".join(configuration_tokens), " ".join(after_tokens))
            if part
        )
    )
    return OffihoIdentity(code=code, name=name, variant=variant)


def parse_inventory_xls(path: Path) -> list[dict[str, Any]]:
    items, _ = _parse_inventory_xls(path)
    return items


def _parse_inventory_xls(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        return _parse_shared_offiho_inventory(path)
    except ValueError as exc:
        # Mantiene el contrato historico del CLI mientras comparte el parser real.
        raise RuntimeError(str(exc)) from exc


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.current_cell is not None and self.current_row is not None:
            self.current_row.append(normalize_space("".join(self.current_cell)))
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)


def _html_inventory_rows(payload: bytes) -> list[tuple[int, Any, Any, Any, Any]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("cp1252")
    parser = _HtmlTableParser()
    parser.feed(text)
    expected = {
        "codigo": "inventory_key",
        "existencia": "stock",
        "piezas por caja": "pieces_per_box",
        "precio lista 1": "unit_price",
    }
    for header_index, row in enumerate(parser.rows):
        normalized = [_normalize_header(cell) for cell in row]
        if not all(header in normalized for header in expected):
            continue
        columns = {field: normalized.index(header) for header, field in expected.items()}
        rows: list[tuple[int, Any, Any, Any, Any]] = []
        for row_index, values in enumerate(parser.rows[header_index + 1 :], start=header_index + 2):
            get = lambda field: values[columns[field]] if columns[field] < len(values) else ""
            rows.append(
                (
                    row_index,
                    get("inventory_key"),
                    get("stock"),
                    get("pieces_per_box"),
                    get("unit_price"),
                )
            )
        return rows
    raise RuntimeError("No se encontraron encabezados de inventario en el XLS HTML")


def _normalize_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", normalize_space(value)).encode("ascii", "ignore").decode("ascii")
    return text.casefold()


def normalize_variant(value: str) -> str:
    text = normalize_space(value).upper()
    text = re.sub(r"\s*/\s*", " ", text)
    return text


def _variant_lookup_keys(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", normalize_variant(value))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    words = [VARIANT_CANONICAL_WORDS.get(word, word) for word in re.findall(r"[A-Z0-9]+", ascii_text)]
    canonical = normalize_space(" ".join(words))
    keys = [canonical] if canonical else []
    if len(words) > 1 and "PLUS" in words:
        keys.append(normalize_space(" ".join(word for word in words if word != "PLUS")))
    if len(words) > 1 and words[0] in {"MALLA", "TAPIZ", "TAPIZADO"}:
        keys.append(normalize_space(" ".join(words[1:])))
    if canonical == "AZUL MARINO":
        keys.extend(["MARINO", "AZUL"])
    return list(dict.fromkeys(keys))


def _identity_variant_lookup_keys(identity: OffihoIdentity) -> list[str]:
    keys = list(_variant_lookup_keys(identity.variant))
    keys.extend(_identity_finish_lookup_keys(identity))
    return list(dict.fromkeys(key for key in keys if key))


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
            code = match.group(0).upper()
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


def parse_pdf_product_index(
    paths: Sequence[Path],
    inventory_items: Sequence[dict[str, Any]],
    assets_dir: Path,
    asset_base_url: str,
) -> dict[str, dict[str, Any]]:
    """Build a deterministic PDF supplement; it is never used at request time."""
    assets_dir = Path(assets_dir)
    records: list[dict[str, Any]] = []
    source_readers: dict[Path, PdfReader] = {}
    for source_path in paths:
        source_path = Path(source_path)
        reader = PdfReader(source_path)
        source_readers[source_path] = reader
        with pdfplumber.open(source_path) as pdf:
            is_black = "BLACK" in source_path.name.upper() or any(
                "PRECIO UNITARIO" in (page.extract_text() or "").upper() for page in pdf.pages[:3]
            )
            parser = _black_pdf_records if is_black else _grid_pdf_records
            records.extend(parser(source_path, pdf.pages))

    by_title: dict[str, list[dict[str, Any]]] = {}
    by_code: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        title_key = _pdf_match_key(record.get("title", ""))
        if title_key:
            by_title.setdefault(title_key, []).append(record)
        for code in record.get("codes", []):
            by_code.setdefault(_pdf_match_key(code), []).append(record)

    matches: dict[str, dict[str, Any]] = {}
    active_family = ""
    active_record: dict[str, Any] | None = None
    for item in inventory_items:
        inventory_key = normalize_space(item.get("inventory_key", ""))
        code = normalize_space(item.get("code", ""))
        key = _pdf_match_key(inventory_key)
        code_key = _pdf_match_key(code)
        direct = _best_pdf_record(by_code.get(code_key, []), item)
        if direct is None and code_key:
            compatible = [
                record
                for candidate_code, candidate_records in by_code.items()
                if _pdf_code_matches(item, candidate_code)
                for record in candidate_records
            ]
            direct = _best_pdf_record(compatible, item)
        if direct is None:
            direct = _best_pdf_record(by_title.get(key, []), item)
        if direct is None:
            direct = _title_record_for_inventory(key, records)
        family = _pdf_family(code)
        if direct is not None:
            active_record = direct
            active_family = family
        elif "/" in code and family and active_record is not None and family == active_family:
            direct = active_record
        elif family != active_family:
            active_record = None
            active_family = ""
        if direct is not None:
            matches[inventory_key] = _materialize_pdf_record(
                direct,
                source_readers,
                assets_dir,
                asset_base_url.rstrip("/"),
            )
    return matches


def _black_pdf_records(source_path: Path, pages: Sequence[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        words = page.extract_words(extra_attrs=["size"])
        title_words = [
            word
            for word in words
            if 14 <= float(word.get("size", 0)) <= 16
            and 145 <= float(word["x0"]) <= 300
        ]
        if not title_words:
            continue
        line_tops = sorted({round(float(word["top"]), 1) for word in title_words})
        starts: list[float] = []
        for top in line_tops:
            if not starts or top - starts[-1] > 30:
                starts.append(top)
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else min(float(page.height), start + 155)
            band_words = [word for word in title_words if start - 2 <= float(word["top"]) < min(end, start + 28)]
            title = _words_as_lines(band_words)
            if not title:
                continue
            text = normalize_space(page.crop((0, max(0, start - 5), page.width, end - 2)).extract_text() or "")
            price_match = PRICE_RE.search(text)
            image = _largest_image_in_box(page.images, 0, max(0, start - 10), 155, end)
            description_text = page.crop((230, max(0, start - 2), 515, end - 3)).extract_text() or ""
            records.append(
                {
                    "source_path": source_path,
                    "page_index": page_index,
                    "title": title,
                    "codes": sorted({match.group(0).upper() for match in CODE_RE.finditer(text)}),
                    "description": _pdf_description(description_text),
                    "unit_price": Decimal(price_match.group(1).replace(",", "")) if price_match else None,
                    "image_name": str(image.get("name", "")) if image else "",
                }
            )
    return records


def _grid_pdf_records(source_path: Path, pages: Sequence[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        images = [
            image for image in page.images
            if float(image["x1"]) - float(image["x0"]) >= 45
            and float(image["bottom"]) - float(image["top"]) >= 55
            and float(image["bottom"]) < float(page.height) - 30
        ]
        if len(images) < 5:
            continue
        row_tops: list[float] = []
        for top in sorted(float(image["top"]) for image in images):
            if not row_tops or top - row_tops[-1] > 45:
                row_tops.append(top)
        column_width = float(page.width) / 5
        seen_cells: set[tuple[int, int]] = set()
        for image in sorted(images, key=lambda value: (float(value["top"]), float(value["x0"]))):
            center_x = (float(image["x0"]) + float(image["x1"])) / 2
            column = min(4, max(0, int(center_x / column_width)))
            row = min(range(len(row_tops)), key=lambda index: abs(float(image["top"]) - row_tops[index]))
            cell = (row, column)
            if cell in seen_cells:
                continue
            seen_cells.add(cell)
            left = column * column_width
            right = (column + 1) * column_width
            top = max(0, row_tops[row] - 8)
            bottom = row_tops[row + 1] - 5 if row + 1 < len(row_tops) else min(float(page.height) - 25, top + 150)
            cell_text = page.crop((left, top, right, bottom)).extract_text() or ""
            codes = sorted({match.group(0).upper() for match in CODE_RE.finditer(cell_text)})
            if not codes:
                continue
            price_match = PRICE_RE.search(cell_text)
            title = _grid_title(cell_text, codes[0])
            records.append(
                {
                    "source_path": source_path,
                    "page_index": page_index,
                    "title": title,
                    "codes": codes,
                    "description": _pdf_description(cell_text),
                    "unit_price": Decimal(price_match.group(1).replace(",", "")) if price_match else None,
                    "image_name": str(image.get("name", "")),
                }
            )
    return records


def _words_as_lines(words: Sequence[dict[str, Any]]) -> str:
    lines: list[tuple[float, list[dict[str, Any]]]] = []
    for word in sorted(words, key=lambda value: (float(value["top"]), float(value["x0"]))):
        top = float(word["top"])
        if not lines or abs(lines[-1][0] - top) > 2:
            lines.append((top, [word]))
        else:
            lines[-1][1].append(word)
    return normalize_space(
        " ".join(
            " ".join(str(word["text"]) for word in sorted(line, key=lambda value: float(value["x0"])))
            for _, line in lines
        )
    )


def _largest_image_in_box(images: Sequence[dict[str, Any]], left: float, top: float, right: float, bottom: float):
    candidates = [
        image for image in images
        if left <= (float(image["x0"]) + float(image["x1"])) / 2 <= right
        and top <= (float(image["top"]) + float(image["bottom"])) / 2 <= bottom
    ]
    return max(
        candidates,
        key=lambda image: (float(image["x1"]) - float(image["x0"])) * (float(image["bottom"]) - float(image["top"])),
        default=None,
    )


def _pdf_description(value: str) -> str:
    text = normalize_space(value.replace("•", " "))
    text = re.sub(r"\$\s*[\d,]+(?:\s*\+\s*IVA[^|]*)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPRECIO\s+UNITARIO:?\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bGARANT[IÍ]A\b.*$", "", text, flags=re.IGNORECASE)
    return normalize_space(text)[:1500]


def _grid_title(value: str, code: str) -> str:
    before = value.upper().split(code.upper(), 1)[0]
    words = re.findall(r"[A-ZÀ-ÖØ-Ý0-9-]+", before)
    return normalize_space(" ".join(words[-3:]))


def _pdf_match_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", normalize_space(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def _pdf_family(code: str) -> str:
    return _pdf_match_key(str(code).split("/", 1)[0]).split(" ", 1)[0]


def _best_pdf_record(records: Sequence[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any] | None:
    if not records:
        return None
    name_key = _pdf_match_key(item.get("name", ""))
    return max(records, key=lambda record: int(bool(name_key and name_key in _pdf_match_key(record.get("title", "")))))


def _pdf_code_matches(item: dict[str, Any], candidate_code: str) -> bool:
    target = re.sub(r"[^A-Z0-9]", "", str(item.get("code", "")).upper())
    candidate = re.sub(r"[^A-Z0-9]", "", str(candidate_code).upper())
    if not target or not candidate.startswith(target):
        return False
    suffix = candidate[len(target) :]
    if not suffix:
        return True
    variant = re.sub(r"[^A-Z0-9]", "", str(item.get("variant", "")).upper())
    inventory_key = re.sub(r"[^A-Z0-9]", "", str(item.get("inventory_key", "")).upper())
    return suffix in variant or suffix in inventory_key


def _title_record_for_inventory(key: str, records: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in records:
        title_key = _pdf_match_key(record.get("title", ""))
        if not title_key or not (key == title_key or key.startswith(f"{title_key} ")):
            continue
        candidates.append((len(title_key), record))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


def match_official_brochure_product(
    item: dict[str, Any],
    assets_dir: Path,
    asset_base_url: str,
) -> dict[str, Any]:
    inventory_key = _pdf_match_key(item.get("inventory_key", ""))
    for prefix, asset_name, page_number in OFFICIAL_BROCHURE_PRODUCTS:
        if not (inventory_key == prefix or inventory_key.startswith(f"{prefix} ")):
            continue
        asset_path = Path(assets_dir) / "images" / asset_name
        if not asset_path.is_file() or asset_path.stat().st_size <= 0:
            return {}
        return {
            "matched_title": prefix,
            "product_url": f"{OFFICIAL_BROCHURE_URL}#page={page_number}",
            "image_url": f"{asset_base_url.rstrip('/')}/images/{asset_name}",
            "description": "",
            "match_status": "official_brochure_match",
            "source_updated_at": "",
        }
    return {}


def _official_brochure_manifest(assets_dir: Path) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for prefix, asset_name, page_number in OFFICIAL_BROCHURE_PRODUCTS:
        asset_path = Path(assets_dir) / "images" / asset_name
        if not asset_path.is_file():
            continue
        manifest.append(
            {
                "prefix": prefix,
                "asset_name": asset_name,
                "page_number": page_number,
                "sha256": _sha256_bytes(asset_path.read_bytes()),
            }
        )
    return manifest


def _pdf_asset_stem(source_path: Path) -> str:
    return "lp-black-colos-jul2026" if "BLACK" in source_path.name.upper() else "lp-offiho-econo-sillas-jul2026"


def _materialize_pdf_record(
    record: dict[str, Any],
    readers: dict[Path, PdfReader],
    assets_dir: Path,
    asset_base_url: str,
) -> dict[str, Any]:
    source_path = Path(record["source_path"])
    pdf_stem = _pdf_asset_stem(source_path)
    assets_dir.mkdir(parents=True, exist_ok=True)
    target_pdf = assets_dir / f"{pdf_stem}.pdf"
    if not target_pdf.exists() or _sha256_bytes(target_pdf.read_bytes()) != _sha256_bytes(source_path.read_bytes()):
        shutil.copyfile(source_path, target_pdf)
    image_url = ""
    image_name = str(record.get("image_name", ""))
    if image_name:
        page = readers[source_path].pages[int(record["page_index"])]
        raw_image = next((image for image in page.images if Path(image.name).stem == Path(image_name).stem), None)
        if raw_image is not None:
            image_dir = assets_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", _pdf_match_key(record.get("title", "")).lower()).strip("-") or "producto"
            target_image = image_dir / f"{slug}-{int(record['page_index']) + 1}.jpg"
            if not target_image.exists():
                with Image.open(io.BytesIO(raw_image.data)) as source:
                    source.convert("RGB").save(target_image, format="JPEG", quality=88, optimize=True)
            image_url = f"{asset_base_url}/images/{target_image.name}"
    return {
        "unit_price": record.get("unit_price"),
        "matched_title": str(record.get("title", "")),
        "description": str(record.get("description", "")),
        "product_url": f"{asset_base_url}/{target_pdf.name}#page={int(record['page_index']) + 1}",
        "image_url": image_url,
        "match_status": "pdf_catalog_match",
        "source_updated_at": "",
    }


def _variant_from_pdf_text(value: str) -> str:
    words = re.findall(r"[A-Za-z\u00c0-\u017f]+", value.upper())
    variants = [word for word in words if word in VARIANT_WORDS]
    return normalize_variant(" ".join(variants))


def match_official_product(identity: OffihoIdentity, candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not identity.code and not identity.name:
        return {"url": "", "image_url": "", "description": "", "match_status": "unmatched", "source_updated_at": ""}
    code_matches = []
    for candidate in candidates:
        url = str(candidate.get("url", ""))
        image_url = _trusted_cached_image(candidate)["image_url"]
        codes = {str(code).upper() for code in candidate.get("codes", [])}
        if (
            not any(_official_code_matches(identity, code) for code in codes)
            or not is_official_url(url)
            or not _candidate_supports_identity_features(candidate, identity)
            or not _candidate_supports_identity_configuration(candidate, identity)
        ):
            continue
        if image_url == url:
            image_url = ""
        code_matches.append(
            {
                "url": url,
                "image_url": image_url,
                "codes": sorted(codes),
                "names": sorted(
                    {
                        _product_name_key(name)
                        for name in candidate.get("names", [])
                        if _product_name_key(name)
                    }
                ),
                "variant_images": _trusted_cached_variant_images(candidate),
                "description": str(candidate.get("description", "")),
                "source_updated_at": str(candidate.get("source_updated_at", "")),
            }
        )
    code_product = (
        _select_official_product(identity, code_matches, "official_code_match")
        if code_matches
        else None
    )
    if code_product and code_product["image_url"]:
        return code_product

    name_keys = _identity_name_keys(identity)
    curated_name_keys = {
        _product_name_key(alias)
        for alias in OFFICIAL_NAME_ALIASES.get(str(identity.code or "").upper(), ())
    }
    if not name_keys:
        return {"url": "", "image_url": "", "description": "", "match_status": "unmatched", "source_updated_at": ""}
    name_matches = []
    for candidate in candidates:
        url = str(candidate.get("url", ""))
        names = {_product_name_key(name) for name in candidate.get("names", [])}
        matched_name = next((name for name in name_keys if name in names), "")
        if (
            not matched_name
            or not is_official_url(url)
            or not _candidate_supports_identity_features(candidate, identity)
            or not _candidate_supports_identity_configuration(candidate, identity)
        ):
            continue
        name_matches.append(
            {
                "url": url,
                "image_url": _trusted_cached_image(candidate)["image_url"],
                "codes": sorted({str(code).upper() for code in candidate.get("codes", [])}),
                "variant_images": _trusted_cached_variant_images(candidate),
                "description": str(candidate.get("description", "")),
                "source_updated_at": str(candidate.get("source_updated_at", "")),
                "matched_name": matched_name,
                "curated_name_match": matched_name in curated_name_keys,
            }
        )
    if name_matches:
        name_product = _select_official_product(identity, name_matches, "official_name_match")
        if name_product["image_url"] or not code_product:
            return name_product
    if code_product:
        return code_product
    return {"url": "", "image_url": "", "description": "", "match_status": "unmatched", "source_updated_at": ""}


def _colos_identity_model_key(identity: OffihoIdentity) -> str:
    code = normalize_space(identity.code)
    vesper_code = re.fullmatch(r"VESPER/0*(\d+)[A-Z]*", code, re.IGNORECASE)
    parts = ["VESPER", str(int(vesper_code.group(1)))] if vesper_code else [code]
    name_tokens = normalize_space(identity.name).split()
    finish_positions = [
        index
        for index, token in enumerate(name_tokens)
        if token.upper() in COLOS_FINISH_CODE_TOKENS
    ]
    if finish_positions:
        # El ultimo codigo es el acabado. Los anteriores pueden pertenecer al
        # modelo (TORRE S, STECCA L, SPLIT GL).
        name_tokens = name_tokens[: finish_positions[-1]]
    parts.extend(name_tokens)
    return _product_name_key(" ".join(parts))


def _colos_variant_image_for_identity(
    product: dict[str, Any],
    identity: OffihoIdentity,
) -> str:
    images = _trusted_cached_variant_images(product)
    for key in _identity_variant_lookup_keys(identity):
        metadata = images.get(key)
        if isinstance(metadata, dict):
            image_url = _trusted_cached_image(metadata)["image_url"]
            if image_url:
                return image_url
    return ""


def match_colos_product(
    identity: OffihoIdentity,
    candidates: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    model_key = _colos_identity_model_key(identity)
    if not model_key:
        return {
            "url": "",
            "image_url": "",
            "description": "",
            "match_status": "unmatched",
            "source_updated_at": "",
        }
    matching = [
        candidate
        for candidate in candidates
        if urllib.parse.urlsplit(str(candidate.get("url", ""))).hostname in COLOS_HOSTS
        and model_key
        in {
            _product_name_key(name)
            for name in candidate.get("names", [])
            if _product_name_key(name)
        }
    ]
    if len(matching) != 1:
        return {
            "url": "",
            "image_url": "",
            "description": "",
            "match_status": "unmatched",
            "source_updated_at": "",
        }
    product = matching[0]
    image_url = _colos_variant_image_for_identity(product, identity)
    if not image_url and not identity.variant:
        image_url = _trusted_cached_image(product)["image_url"]
    return {
        "url": str(product.get("url", "")),
        "image_url": image_url,
        "description": str(product.get("description", "")),
        "match_status": "official_colos_match",
        "source_updated_at": str(product.get("source_updated_at", "")),
        "has_variant_catalog": bool(_trusted_cached_variant_images(product)),
    }


def load_exact_image_manifest(
    path: Path | None,
    *,
    allowed_hosts: frozenset[str],
    allowed_image_hosts: frozenset[str] | None = None,
    match_status: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if path is None:
        return {}, {"name": "", "sha256": "", "size_bytes": 0, "record_count": 0}
    if not path.is_file():
        raise RuntimeError(f"No existe el manifiesto de imagenes exactas: {path}")
    payload = path.read_bytes()
    try:
        rows = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Manifiesto de imagenes exactas invalido: {path}") from exc
    if isinstance(rows, dict) and rows.get("schema_version") == 1:
        rows = rows.get("items")
    if not isinstance(rows, list):
        raise RuntimeError(f"El manifiesto de imagenes exactas debe contener una lista: {path}")
    image_hosts = allowed_hosts if allowed_image_hosts is None else allowed_image_hosts
    index: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Registro {position} invalido en {path}")
        inventory_key = normalize_space(raw.get("inventory_key")).upper()
        product_url = normalize_space(raw.get("product_url"))
        image_url = normalize_space(raw.get("image_url"))
        product_host = urllib.parse.urlsplit(product_url).hostname
        image_host = urllib.parse.urlsplit(image_url).hostname
        if (
            not inventory_key
            or inventory_key in index
            or not _is_safe_https_url_for_hosts(product_url, allowed_hosts)
            or product_host not in allowed_hosts
            or not _is_safe_https_url_for_hosts(image_url, image_hosts)
            or Path(urllib.parse.urlsplit(image_url).path).suffix.casefold()
            not in IMAGE_EXTENSIONS
            or image_host not in image_hosts
        ):
            raise RuntimeError(
                f"Registro exacto inseguro o duplicado {position} ({inventory_key!r}) en {path}"
            )
        index[inventory_key] = {
            "url": product_url,
            "image_url": image_url,
            "description": "",
            "match_status": match_status,
            "source_updated_at": normalize_space(raw.get("evidence_as_of")),
            "has_variant_catalog": False,
        }
    return index, {
        "name": path.name,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "record_count": len(index),
    }


def load_generated_image_manifest(
    path: Path | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if path is None:
        return {}, {"name": "", "sha256": "", "size_bytes": 0, "record_count": 0}
    if not path.is_file():
        raise RuntimeError(f"No existe el manifiesto de imagenes generadas: {path}")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Manifiesto de imagenes generadas invalido: {path}") from exc
    rows = document.get("items") if isinstance(document, dict) and document.get("schema_version") == 1 else None
    generator = normalize_space(document.get("generator")) if isinstance(document, dict) else ""
    if not isinstance(rows, list) or not generator:
        raise RuntimeError(f"El manifiesto de imagenes generadas debe usar schema v1: {path}")
    index: dict[str, dict[str, Any]] = {}
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Registro generado {position} invalido en {path}")
        inventory_key = normalize_space(raw.get("inventory_key")).upper()
        product_url = normalize_space(raw.get("product_url"))
        image_url = normalize_space(raw.get("image_url"))
        image_label = normalize_space(raw.get("image_label"))
        reference_key = normalize_space(raw.get("reference_inventory_key")).upper()
        reference_url = normalize_space(raw.get("reference_image_url"))
        prompt = normalize_space(raw.get("generation_prompt"))
        source_sha256 = normalize_space(raw.get("source_sha256")).casefold()
        evidence_as_of = normalize_space(raw.get("evidence_as_of"))
        review = normalize_space(raw.get("review"))
        try:
            datetime.fromisoformat(evidence_as_of)
        except ValueError:
            evidence_as_of = ""
        if (
            not inventory_key
            or inventory_key in index
            or not image_label
            or not reference_key
            or not prompt
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256)
            or not evidence_as_of
            or not review
            or not _is_safe_https_url_for_hosts(
                product_url, CATALOG_SOURCE_HOSTS | MANAGED_ASSET_HOSTS
            )
            or not _is_safe_https_url_for_hosts(image_url, MANAGED_ASSET_HOSTS)
            or Path(urllib.parse.urlsplit(image_url).path).suffix.casefold() not in IMAGE_EXTENSIONS
            or not _is_safe_https_url_for_hosts(
                reference_url, CATALOG_SOURCE_HOSTS | MANAGED_ASSET_HOSTS
            )
        ):
            raise RuntimeError(
                f"Registro generado inseguro o incompleto {position} ({inventory_key!r}) en {path}"
            )
        index[inventory_key] = {
            "url": product_url,
            "image_url": image_url,
            "description": "",
            "match_status": "generated_visual_reference",
            "source_updated_at": evidence_as_of,
            "has_variant_catalog": False,
            "image_kind": "generated_reference",
            "image_label": image_label,
            "image_references": [reference_key, reference_url],
            "generation_prompt": prompt,
            "generation_model": generator,
            "image_source_sha256": source_sha256,
        }
    return index, {
        "name": path.name,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "record_count": len(index),
        "generator": generator,
    }


def apply_generated_images(
    items: Sequence[dict[str, Any]],
    generated_images: Mapping[str, Mapping[str, Any]],
) -> None:
    """Rellena únicamente vacíos; una evidencia oficial siempre conserva prioridad."""

    for item in items:
        if item.get("image_url"):
            item["image_kind"] = "official"
            continue
        generated = generated_images.get(normalize_space(item.get("inventory_key")).upper())
        if not generated:
            item["image_kind"] = "placeholder"
            continue
        item["product_url"] = item.get("product_url") or generated.get("url", "")
        for field in (
            "image_url",
            "match_status",
            "source_updated_at",
            "image_kind",
            "image_label",
            "image_references",
            "generation_prompt",
            "generation_model",
            "image_source_sha256",
        ):
            item[field] = generated.get(field, "")


def load_visual_rejection_manifest(
    path: Path | None,
    *,
    inventory_keys: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Carga rechazos visuales ligados a una URL concreta.

    El rechazo no bloquea futuras fotografías correctas: sólo retira el URL
    que fue inspeccionado y declarado conflictivo en el manifiesto.
    """

    if path is None:
        return {}, {"name": "", "sha256": "", "size_bytes": 0, "record_count": 0}
    if not path.is_file():
        raise RuntimeError(f"No existe el manifiesto de rechazos visuales: {path}")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Manifiesto de rechazos visuales invalido: {path}") from exc
    rows = document.get("items") if isinstance(document, dict) and document.get("schema_version") == 1 else None
    if not isinstance(rows, list):
        raise RuntimeError(f"El manifiesto de rechazos visuales debe usar schema v1: {path}")
    allowed_image_hosts = OFFICIAL_HOSTS | MANAGED_ASSET_HOSTS
    index: dict[str, dict[str, Any]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    for position, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Rechazo visual {position} invalido en {path}")
        inventory_key = normalize_space(raw.get("inventory_key")).upper()
        image_url = normalize_space(raw.get("rejected_image_url"))
        reason = normalize_space(raw.get("reason"))
        evidence_as_of = normalize_space(raw.get("evidence_as_of"))
        review = normalize_space(raw.get("review"))
        try:
            parsed_evidence_date = datetime.fromisoformat(evidence_as_of)
        except ValueError:
            parsed_evidence_date = None
        is_safe_url = _is_safe_https_url_for_hosts(image_url, allowed_image_hosts)
        canonical_image_url = (
            _canonical_visual_rejection_url(image_url) if is_safe_url else ""
        )
        pair = (inventory_key, canonical_image_url)
        if (
            not inventory_key
            or not reason
            or not review
            or parsed_evidence_date is None
            or (inventory_keys is not None and inventory_key not in inventory_keys)
            or pair in seen_pairs
            or not is_safe_url
            or Path(urllib.parse.urlsplit(image_url).path).suffix.casefold()
            not in IMAGE_EXTENSIONS
        ):
            raise RuntimeError(
                f"Rechazo visual inseguro o duplicado {position} ({inventory_key!r}) en {path}"
            )
        seen_pairs.add(pair)
        record = index.setdefault(inventory_key, {"image_urls": set(), "reason": reason})
        record["image_urls"].add(canonical_image_url)
    return index, {
        "name": path.name,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "record_count": len(seen_pairs),
    }


def apply_visual_rejections(
    items: Sequence[dict[str, Any]],
    rejections: Mapping[str, Mapping[str, Any]],
) -> None:
    for item in items:
        image_url = normalize_space(item.get("image_url"))
        if _is_visual_rejected(item.get("inventory_key"), image_url, rejections):
            item["image_url"] = ""
            item["match_status"] = "visual_conflict_rejected"


def _canonical_visual_rejection_url(value: Any) -> str:
    """Normaliza equivalencias HTTP sin confundir una imagen versionada.

    La ruta y la consulta permanecen intactas; sÃ³lo se normalizan esquema/host,
    el puerto HTTPS implÃ­cito, el alias www de Offiho y el fragmento local.
    """

    url = normalize_space(value)
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return url
    host = (parsed.hostname or "").casefold()
    official_aliases = {
        "www.offiho.com": "offiho.com",
        "www.offihoblack.com": "offihoblack.com",
        "www.colos.it": "colos.it",
    }
    host = official_aliases.get(host, host)
    netloc = host if port in {None, 443} else f"{host}:{port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), netloc, parsed.path, parsed.query, "")
    )


def _is_visual_rejected(
    inventory_key: Any,
    image_url: Any,
    rejections: Mapping[str, Mapping[str, Any]],
) -> bool:
    rejection = rejections.get(normalize_space(inventory_key).upper())
    url = normalize_space(image_url)
    if not rejection or not url:
        return False
    rejected_urls = {
        _canonical_visual_rejection_url(candidate)
        for candidate in rejection.get("image_urls", set())
    }
    return _canonical_visual_rejection_url(url) in rejected_urls


def _first_non_rejected_product(
    inventory_key: str,
    candidates: Sequence[Mapping[str, Any] | None],
    rejections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Elige por precedencia sin permitir que un rechazo tape una fuente inferior."""

    for candidate in candidates:
        if not candidate:
            continue
        if _is_visual_rejected(inventory_key, candidate.get("image_url"), rejections):
            continue
        return dict(candidate)
    return None


def _without_rejected_product_image(
    inventory_key: str,
    product: Mapping[str, Any],
    rejections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    sanitized = dict(product)
    if _is_visual_rejected(inventory_key, sanitized.get("image_url"), rejections):
        sanitized["image_url"] = ""
        sanitized["match_status"] = "visual_conflict_rejected"
    return sanitized


def _without_rejected_candidate_images(
    inventory_key: str,
    candidate: Mapping[str, Any],
    rejections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Retira evidencia rechazada antes de que el ranking elija un candidato."""

    sanitized = dict(candidate)
    if _is_visual_rejected(inventory_key, sanitized.get("image_url"), rejections):
        sanitized.update(_empty_image_metadata())
    raw_variants = sanitized.get("variant_images")
    if isinstance(raw_variants, dict):
        sanitized["variant_images"] = {
            key: metadata
            for key, metadata in raw_variants.items()
            if not isinstance(metadata, Mapping)
            or not _is_visual_rejected(
                inventory_key,
                metadata.get("image_url"),
                rejections,
            )
        }
    return sanitized


def _official_code_matches(identity: OffihoIdentity, candidate_code: str) -> bool:
    candidate = str(candidate_code or "").upper()
    if not candidate:
        return False
    requested_suffixes = _identity_requested_code_suffixes(identity)
    for target in _identity_code_targets(identity):
        if candidate == target:
            return True
        if not candidate.startswith(target):
            continue
        suffix = re.sub(r"[^A-Z0-9]", "", candidate[len(target) :])
        if suffix and suffix in requested_suffixes:
            return True
    return False


def _identity_configuration_code_suffixes(identity: OffihoIdentity) -> set[str]:
    name = unicodedata.normalize("NFKD", str(identity.name or "").upper())
    name = name.encode("ascii", "ignore").decode("ascii")
    suffixes: set[str] = set()
    if re.search(r"\bC\s*/\s*B\b", name) or re.search(r"\bCON\s+BRAZOS?\b", name):
        suffixes.add("CB")
    for token in re.findall(r"[A-Z0-9]+", name):
        suffix = CONFIGURATION_CODE_SUFFIX_ALIASES.get(token)
        if suffix:
            suffixes.add(suffix)
    return suffixes


def _identity_requested_code_suffixes(identity: OffihoIdentity) -> set[str]:
    ordered_variant_suffixes = [
        re.sub(r"[^A-Z0-9]", "", token)
        for token in str(identity.variant or "").upper().split()
        if re.sub(r"[^A-Z0-9]", "", token)
    ]
    variant_suffixes = set(ordered_variant_suffixes)
    compound_variant = "".join(ordered_variant_suffixes)
    if compound_variant:
        variant_suffixes.add(compound_variant)
    variant_suffixes.discard("")
    configuration_suffixes = _identity_configuration_code_suffixes(identity)
    combined = {
        configuration + variant
        for configuration in configuration_suffixes
        for variant in variant_suffixes
    } | {
        variant + configuration
        for configuration in configuration_suffixes
        for variant in variant_suffixes
    }
    if compound_variant:
        for configuration in configuration_suffixes:
            combined.add(compound_variant + configuration)
            if len(ordered_variant_suffixes) > 1:
                combined.add(
                    ordered_variant_suffixes[0]
                    + configuration
                    + "".join(ordered_variant_suffixes[1:])
                )
    return variant_suffixes | configuration_suffixes | combined


def _candidate_configuration_code_rank(product: dict[str, Any], identity: OffihoIdentity) -> int:
    requested = _identity_configuration_code_suffixes(identity)
    if not requested:
        return 0
    rank = 0
    for raw_code in product.get("codes", []):
        candidate = str(raw_code or "").upper()
        for target in _identity_code_targets(identity):
            if not candidate.startswith(target):
                continue
            suffix = re.sub(r"[^A-Z0-9]", "", candidate[len(target) :])
            if suffix in requested or any(suffix.startswith(value) for value in requested):
                rank = max(rank, 1)
    return rank


def _select_official_product(
    identity: OffihoIdentity,
    matches: Sequence[dict[str, Any]],
    match_status: str,
) -> dict[str, Any]:
    def url_rank(product: dict[str, Any]) -> tuple[Any, ...]:
        url = product["url"]
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        compact_path = re.sub(r"[^A-Z0-9]", "", path.upper())
        compact_code = re.sub(r"[^A-Z0-9]", "", identity.code.upper())
        leaf_name = _product_name_key(path.rstrip("/").rsplit("/", 1)[-1], codes=[identity.code])
        name_key = product.get("matched_name") or _product_name_key(identity.name)
        identity_name_tokens = set(str(name_key).split())
        candidate_names = {
            _product_name_key(name)
            for name in product.get("names", [])
            if _product_name_key(name)
        }
        name_exact = int(bool(name_key and name_key in candidate_names))
        name_token_equal = int(
            bool(identity_name_tokens)
            and any(set(candidate.split()) == identity_name_tokens for candidate in candidate_names)
        )
        name_overlap = max(
            (len(identity_name_tokens & set(candidate.split())) for candidate in candidate_names),
            default=0,
        )
        name_difference = min(
            (len(identity_name_tokens ^ set(candidate.split())) for candidate in candidate_names),
            default=999,
        )
        requested_variant_tokens = set(
            re.findall(
                r"[A-Z0-9]+",
                " ".join(_variant_lookup_keys(identity.variant)),
            )
        )
        candidate_variant_tokens = set(
            re.findall(
                r"[A-Z0-9]+",
                unicodedata.normalize(
                    "NFKD",
                    f"{' '.join(candidate_names)} {urllib.parse.unquote(path)}".upper(),
                ).encode("ascii", "ignore").decode("ascii"),
            )
        )
        variant_word_overlap = len(requested_variant_tokens & candidate_variant_tokens)
        configuration_specific = _candidate_configuration_code_rank(product, identity)
        requested_configuration_suffixes = _identity_configuration_code_suffixes(identity)
        configuration_path_specific = int(
            any(
                _compact_variant_value(target + suffix) in compact_path
                for target in _identity_code_targets(identity)
                for suffix in requested_configuration_suffixes
            )
        )
        product_code_specific = int(
            any(
                _compact_variant_value(code) in compact_path
                for code in product.get("codes", [])
                if _compact_variant_value(code)
            )
        )
        if match_status == "official_code_match":
            variant_specific = int(
                bool(identity.variant)
                and any(
                    key and compact_code + key in compact_path
                    for key in map(_compact_variant_value, _identity_variant_lookup_keys(identity))
                )
            )
            primary = int(bool(compact_code and compact_code in compact_path))
            secondary = int("/products/" in path.casefold() or "modelo-" in path.casefold())
            depth = path.count("/")
        else:
            variant_specific = 0
            primary = int(bool(name_key and leaf_name == name_key))
            secondary = -path.count("/")
            depth = int(bool(product.get("image_url")))
        return (
            configuration_path_specific,
            configuration_specific,
            product_code_specific,
            name_exact,
            name_token_equal,
            variant_word_overlap,
            name_overlap,
            -name_difference,
            variant_specific,
            primary,
            secondary,
            depth,
            url,
        )

    url_product = max(matches, key=url_rank)
    scoped_matches = [
        product
        for product in matches
        if _same_official_product_scope(product, url_product)
    ]
    variant_image_products = [
        (product, _variant_image_for_identity(product, identity))
        for product in scoped_matches
    ]
    variant_image_products = [pair for pair in variant_image_products if pair[1]]
    has_variant_catalog = any(
        _product_targets_identity(product, identity)
        and bool(_trusted_cached_variant_images(product))
        for product in scoped_matches
    )
    if variant_image_products:
        image_url = max(variant_image_products, key=lambda pair: url_rank(pair[0]))[1]
    else:
        image_products = [
            product
            for product in scoped_matches
            if product.get("image_url")
            and not _image_has_conflicting_product_code(str(product["image_url"]), identity)
            and (
                not _image_conflicts_with_identity_variant(str(product["image_url"]), identity)
                or _product_targets_exact_variant(product, identity)
            )
            and not _image_conflicts_with_identity_configuration(str(product["image_url"]), identity)
            and _generic_product_supports_identity_variant(product, identity)
            and (
                not has_variant_catalog
                or not identity.variant
                or _product_targets_exact_variant(product, identity)
            )
            and (
                product.get("curated_name_match") is True
                or _product_targets_identity(product, identity)
                or _image_targets_identity(str(product["image_url"]), identity)
            )
        ]
        image_url = str(max(image_products, key=url_rank)["image_url"]) if image_products else ""
    description_products = [product for product in scoped_matches if product.get("description")]
    if not description_products:
        description_products = [product for product in matches if product.get("description")]
    description = max(description_products, key=url_rank)["description"] if description_products else ""
    return {
        "url": url_product["url"],
        "image_url": image_url,
        "description": description,
        "match_status": match_status,
        "source_updated_at": str(url_product["source_updated_at"]),
        "has_variant_catalog": has_variant_catalog,
    }


def _same_official_product_scope(
    candidate: dict[str, Any],
    selected: dict[str, Any],
) -> bool:
    def url_key(product: dict[str, Any]) -> tuple[str, str]:
        parsed = urllib.parse.urlsplit(str(product.get("url", "")))
        host = str(parsed.hostname or "").casefold().removeprefix("www.")
        path = urllib.parse.unquote(parsed.path).casefold().rstrip("/")
        return host, path

    if url_key(candidate) == url_key(selected):
        return True
    candidate_codes = {str(code).upper() for code in candidate.get("codes", []) if str(code).strip()}
    selected_codes = {str(code).upper() for code in selected.get("codes", []) if str(code).strip()}
    candidate_names = {
        _product_name_key(name)
        for name in candidate.get("names", [])
        if _product_name_key(name)
    }
    selected_names = {
        _product_name_key(name)
        for name in selected.get("names", [])
        if _product_name_key(name)
    }
    return bool(
        candidate_codes
        and candidate_codes == selected_codes
        and candidate_names
        and candidate_names == selected_names
    )


def _variant_image_for_identity(product: dict[str, Any], identity: OffihoIdentity) -> str:
    if not _product_targets_identity(product, identity):
        return ""
    images = product.get("variant_images", {})
    if not isinstance(images, dict):
        return ""
    identity_keys = _identity_variant_lookup_keys(identity)
    finish_keys = _identity_finish_lookup_keys(identity)
    for key in [*finish_keys, *(key for key in identity_keys if key not in finish_keys)]:
        metadata = images.get(key)
        if isinstance(metadata, dict):
            image_url = _trusted_cached_image(metadata)["image_url"]
            product_host = urllib.parse.urlsplit(str(product.get("url", ""))).hostname
            exact_shopify_variant_binding = bool(
                image_url
                and product_host in {"offihoblack.com", "www.offihoblack.com"}
                and _single_code_product_targets_identity(product, identity)
            )
            if image_url and (
                _image_targets_identity(image_url, identity)
                or _single_code_product_targets_identity(product, identity)
            ) and (
                exact_shopify_variant_binding
                or not _image_has_conflicting_product_code(image_url, identity)
            ):
                if not _image_conflicts_with_identity_configuration(image_url, identity):
                    return image_url
    return ""


def _product_targets_identity(product: dict[str, Any], identity: OffihoIdentity) -> bool:
    return (
        _single_code_product_targets_identity(product, identity)
        or _image_targets_identity(str(product.get("url", "")), identity)
    )


def _single_code_product_targets_identity(
    product: dict[str, Any],
    identity: OffihoIdentity,
) -> bool:
    codes = {
        str(code).upper()
        for code in product.get("codes", [])
        if str(code).strip()
    }
    return len(codes) == 1 and any(
        _official_code_matches(identity, code)
        for code in codes
    )


def _product_targets_exact_variant(product: dict[str, Any], identity: OffihoIdentity) -> bool:
    variant_keys = [_compact_variant_value(key) for key in _identity_variant_lookup_keys(identity)]
    if not any(variant_keys):
        return False
    compact_codes = [
        _compact_variant_value(code)
        for code in _identity_code_targets(identity)
        if _compact_variant_value(code)
    ]
    for value in (str(product.get("url", "")), str(product.get("image_url", ""))):
        compact_path = _compact_variant_value(
            urllib.parse.unquote(urllib.parse.urlsplit(value).path)
        )
        if any(code + variant in compact_path for code in compact_codes for variant in variant_keys):
            return True
    return False


def _image_targets_identity(value: str, identity: OffihoIdentity) -> bool:
    if CODE_RE.fullmatch(identity.code) is None:
        return True
    compact_path = _compact_variant_value(
        urllib.parse.unquote(urllib.parse.urlsplit(value).path)
    )
    return any(
        compact_code and compact_code in compact_path
        for compact_code in map(_compact_variant_value, _identity_code_targets(identity))
    )


def _image_has_conflicting_product_code(value: str, identity: OffihoIdentity) -> bool:
    stem = Path(urllib.parse.unquote(urllib.parse.urlsplit(value).path)).stem.upper()
    referenced_codes = {match.group(0).upper() for match in CODE_RE.finditer(stem)}
    target_codes = {
        _compact_variant_value(code)
        for code in _identity_code_targets(identity)
        if _compact_variant_value(code)
    }
    return bool(
        referenced_codes
        and not any(
            _compact_variant_value(referenced_code).startswith(target)
            for referenced_code in referenced_codes
            for target in target_codes
        )
    )


def _image_conflicts_with_identity_configuration(value: str, identity: OffihoIdentity) -> bool:
    requested_tokens = set(re.findall(r"\b(?:G|B|W|N|R|V|O)\d+\b", str(identity.name or "").upper()))
    if not requested_tokens:
        return False
    image_text = urllib.parse.unquote(urllib.parse.urlsplit(value).path).upper()
    labeled_tokens = set(re.findall(r"(?:^|[^A-Z0-9])((?:G|B|W|N|R|V|O)\d+)(?=[^A-Z0-9]|[A-Z])", image_text))
    return bool(labeled_tokens and requested_tokens.isdisjoint(labeled_tokens))


def _generic_product_supports_identity_variant(
    product: dict[str, Any],
    identity: OffihoIdentity,
) -> bool:
    if not identity.variant and _identity_has_ambiguous_finish_codes(identity):
        return False
    requested_keys = [
        tuple(key.split())
        for key in _identity_variant_lookup_keys(identity)
        if key
    ]
    if not requested_keys:
        return True
    codes = list(product.get("codes", [])) or list(_identity_code_targets(identity))
    requested_suffixes = {_compact_variant_value(" ".join(key)) for key in requested_keys}
    for raw_code in codes:
        candidate_code = _compact_variant_value(raw_code)
        for target in map(_compact_variant_value, _identity_code_targets(identity)):
            if candidate_code.startswith(target) and candidate_code[len(target) :] in requested_suffixes:
                return True
    for value in (str(product.get("image_url", "")), str(product.get("url", ""))):
        labeled_keys = [
            tuple(key.split())
            for key in _variant_keys_from_image_reference(value, codes)
            if key
        ]
        if labeled_keys:
            return any(requested == labeled for requested in requested_keys for labeled in labeled_keys)

    name_keys = _variant_keys_from_free_text(" ".join(str(name) for name in product.get("names", [])))
    if name_keys:
        return any(tuple(key.split()) in requested_keys for key in name_keys)

    if len(requested_keys[0]) != len(set(requested_keys[0])):
        return False
    description_words = _explicit_variant_words_from_description(str(product.get("description", "")))
    return any(set(requested) == description_words for requested in requested_keys)


def _variant_keys_from_free_text(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    variant_words = {
        _compact_variant_value(word): word
        for word in VARIANT_WORDS
        if _compact_variant_value(word)
    }
    words = [
        variant_words[token]
        for token in re.findall(r"[A-Z0-9]+", ascii_text)
        if token in variant_words
    ]
    return _variant_lookup_keys(" ".join(words))


def _explicit_variant_words_from_description(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    words: set[str] = set()
    for segment in re.findall(r"\b(?:COLOR(?:ES)?|TAPIZ(?:ADO)?)\b\s*:?\s*([^.;\n]+)", ascii_text):
        for key in _variant_keys_from_free_text(segment):
            words.update(key.split())
    return words


def _image_conflicts_with_identity_variant(value: str, identity: OffihoIdentity) -> bool:
    requested_keys = [tuple(key.split()) for key in _identity_variant_lookup_keys(identity) if key]
    if not requested_keys:
        return False
    labeled_keys = [
        tuple(key.split())
        for key in _variant_keys_from_image_reference(value, _identity_code_targets(identity))
        if key
    ]
    if not labeled_keys or any(requested == labeled for requested in requested_keys for labeled in labeled_keys):
        return False
    if len(requested_keys[0]) > 1 and len(labeled_keys[0]) > 1:
        return True
    requested_words = {word for key in requested_keys for word in key}
    labeled_words = {word for key in labeled_keys for word in key}
    return requested_words.isdisjoint(labeled_words)


def _identity_code_targets(identity: OffihoIdentity) -> tuple[str, ...]:
    code = str(identity.code or "").upper()
    alias = OFFICIAL_CODE_ALIASES.get(code, "")
    return tuple(value for value in (code, alias) if value)


def _identity_finish_lookup_keys(identity: OffihoIdentity) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(identity.name or "").upper())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    keys: list[str] = []
    compound_pattern = r"(?:[A-Z]{1,2}\d+)(?:\s*/\s*(?:[A-Z]{1,2}\d+))+"
    compounds = re.findall(compound_pattern, ascii_text)
    finish_tokens = [
        token
        for token in re.findall(r"[A-Z0-9]+", ascii_text)
        if FINISH_CODE_RE.fullmatch(token) or token in FINISH_CODE_WORDS
    ]
    if compounds and len(finish_tokens) == len(re.findall(r"[A-Z0-9]+", " ".join(compounds))):
        for compound in compounds:
            keys.extend(_variant_lookup_keys(compound))
    if not keys:
        if len(finish_tokens) == 1:
            keys.extend(_variant_lookup_keys(finish_tokens[0]))
    return list(dict.fromkeys(key for key in keys if key))


def _identity_has_ambiguous_finish_codes(identity: OffihoIdentity) -> bool:
    normalized = unicodedata.normalize("NFKD", str(identity.name or "").upper())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    finish_tokens = [
        token
        for token in re.findall(r"[A-Z0-9]+", ascii_text)
        if FINISH_CODE_RE.fullmatch(token) or token in FINISH_CODE_WORDS
    ]
    return len(finish_tokens) > 1 and not _identity_finish_lookup_keys(identity)


def _identity_name_keys(identity: OffihoIdentity) -> list[str]:
    keys: list[str] = []

    def add(value: str) -> None:
        key = _product_name_key(value)
        if len(key) >= 3 and key not in keys:
            keys.append(key)

    add(identity.name)
    for alias in OFFICIAL_NAME_ALIASES.get(str(identity.code or "").upper(), ()):
        add(alias)
    if identity.code and CODE_RE.fullmatch(identity.code) is None:
        add(identity.code)
    return keys


def _candidate_supports_identity_features(
    candidate: dict[str, Any],
    identity: OffihoIdentity,
) -> bool:
    identity_code = "" if CODE_RE.fullmatch(identity.code) else identity.code
    required = _required_features_from_text(f"{identity_code} {identity.name}")
    candidate_text = " ".join(
        [
            urllib.parse.unquote(urllib.parse.urlsplit(str(candidate.get("url", ""))).path),
            *(str(name) for name in candidate.get("names", [])),
        ]
    )
    candidate_features = _required_features_from_text(candidate_text)
    if required:
        return required <= candidate_features
    return not (candidate_features & EXCLUSIVE_ACCESSORY_FEATURE_WORDS)


def _candidate_supports_identity_configuration(
    candidate: dict[str, Any],
    identity: OffihoIdentity,
) -> bool:
    requested = _identity_configuration_code_suffixes(identity)
    if not requested:
        return True
    candidate_text = " ".join(
        [
            urllib.parse.unquote(urllib.parse.urlsplit(str(candidate.get("url", ""))).path),
            *(str(name) for name in candidate.get("names", [])),
            *(str(code) for code in candidate.get("codes", [])),
        ]
    )
    ascii_text = (
        unicodedata.normalize("NFKD", candidate_text.upper())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    tokens = set(re.findall(r"[A-Z0-9]+", ascii_text))
    compact_codes = {
        _compact_variant_value(code)
        for code in candidate.get("codes", [])
        if _compact_variant_value(code)
    }
    raw_codes = {
        str(code).upper()
        for code in candidate.get("codes", [])
        if str(code).strip()
    }
    target_codes = {
        _compact_variant_value(code)
        for code in _identity_code_targets(identity)
        if _compact_variant_value(code)
    }

    def supports(suffix: str) -> bool:
        if suffix in tokens:
            return True
        if suffix == "CB" and re.search(r"\bCON\s+BRAZOS?\b", ascii_text):
            return True
        return any(
            _compact_variant_value(code) != target
            and _compact_variant_value(code).startswith(target)
            and _official_code_matches(identity, code)
            for target in target_codes
            for code in raw_codes
        )

    return all(supports(suffix) for suffix in requested)


def _required_features_from_text(value: str) -> set[str]:
    ascii_text = (
        unicodedata.normalize("NFKD", str(value or "").upper())
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    features: set[str] = set()
    for token in re.findall(r"[A-Z0-9]+", ascii_text):
        for feature in REQUIRED_FEATURE_WORDS:
            if token == feature or (
                token.startswith(feature)
                and (len(feature) >= 5 or feature == "KIT")
            ):
                features.add("BRAZO" if feature == "BRAZOS" else feature)
    return features


def _support_product_for_identity(
    identity: OffihoIdentity,
    pdf_product: dict[str, Any],
    brochure_product: dict[str, Any],
    *,
    inventory_key: str = "",
    visual_rejections: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rejections = visual_rejections or {}
    for product in (pdf_product, brochure_product):
        if not product.get("image_url"):
            continue
        if _is_visual_rejected(inventory_key, product.get("image_url"), rejections):
            continue
        candidate = {
            "url": product.get("product_url", ""),
            "names": [product.get("matched_title", "")],
            "codes": [],
        }
        if (
            _candidate_supports_identity_features(candidate, identity)
            and _candidate_supports_identity_configuration(candidate, identity)
            and _support_product_has_exact_variant_evidence(product, identity)
        ):
            return product
    return {}


def _support_product_has_exact_variant_evidence(
    product: dict[str, Any],
    identity: OffihoIdentity,
) -> bool:
    requested = [_canonical_variant_compact(key) for key in _identity_variant_lookup_keys(identity)]
    requested = [key for key in requested if key]
    if not requested:
        matched_title = _pdf_match_key(product.get("matched_title", ""))
        inventory_identity = _pdf_match_key(normalize_space(f"{identity.code} {identity.name}"))
        return bool(matched_title and inventory_identity and matched_title == inventory_identity)
    evidence_text = " ".join(
        [
            str(product.get("matched_title", "")),
            urllib.parse.unquote(urllib.parse.urlsplit(str(product.get("image_url", ""))).path),
        ]
    )
    evidence_keys = {
        _canonical_variant_compact(key)
        for key in _variant_keys_from_free_text(evidence_text)
        if _canonical_variant_compact(key)
    }
    requested_words = {word for key in _identity_variant_lookup_keys(identity) for word in key.split()}
    evidence_words = {
        VARIANT_CANONICAL_WORDS.get(word, word)
        for word in re.findall(r"[A-Z0-9]+", unicodedata.normalize("NFKD", evidence_text.upper()))
        if word in VARIANT_WORDS or word in VARIANT_CANONICAL_WORDS
    }
    has_variant_evidence = any(key in evidence_keys for key in requested) or bool(requested_words) and requested_words <= evidence_words
    return has_variant_evidence


def _canonical_variant_compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    words = [VARIANT_CANONICAL_WORDS.get(word, word) for word in re.findall(r"[A-Z0-9]+", ascii_text)]
    return "".join(words)


def _product_name_key(value: str, *, codes: Sequence[str] = ()) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    code_parts = {
        part
        for code in codes
        for part in re.findall(r"[A-Z0-9]+", str(code or "").upper())
    }
    tokens = [
        token
        for token in re.findall(r"[A-Z0-9]+", ascii_text)
        if token not in PAGE_NAME_STOP_WORDS and token not in code_parts
    ]
    return " ".join(tokens)


def is_official_url(value: str) -> bool:
    return _is_safe_https_url_for_hosts(value, OFFICIAL_HOSTS)


def _is_safe_https_url_for_hosts(value: str, allowed_hosts: frozenset[str]) -> bool:
    parsed = urllib.parse.urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in allowed_hosts
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
    )


def is_official_image_url(value: str) -> bool:
    if not value or not is_official_url(value):
        return False
    suffix = Path(urllib.parse.urlparse(value).path).suffix.lower()
    return suffix in IMAGE_EXTENSIONS


def _empty_image_metadata() -> dict[str, Any]:
    return {
        "image_url": "",
        "image_verified": False,
        "image_content_type": "",
        "image_content_length": 0,
    }


def _trusted_cached_image(value: dict[str, Any]) -> dict[str, Any]:
    image_url = str(value.get("image_url", ""))
    content_type = str(value.get("image_content_type", "")).split(";", 1)[0].strip().lower()
    try:
        content_length = int(value.get("image_content_length", 0))
    except (TypeError, ValueError):
        return _empty_image_metadata()
    if not (
        value.get("image_verified") is True
        and is_official_image_url(image_url)
        and _product_image_score("", image_url, ()) is not None
        and content_type.startswith("image/")
        and 0 < content_length <= MAX_IMAGE_BYTES
    ):
        return _empty_image_metadata()
    return {
        "image_url": image_url,
        "image_verified": True,
        "image_content_type": content_type,
        "image_content_length": content_length,
    }


def _trusted_cached_variant_images(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_images = value.get("variant_images", {})
    if not isinstance(raw_images, dict):
        return {}
    trusted: dict[str, dict[str, Any]] = {}
    for raw_key, metadata in raw_images.items():
        if not isinstance(metadata, dict):
            continue
        keys = _variant_lookup_keys(str(raw_key))
        image_metadata = _trusted_cached_image(metadata)
        if not keys or not image_metadata["image_url"]:
            continue
        trusted.setdefault(keys[0], image_metadata)
    return dict(sorted(trusted.items()))


def _trusted_cached_code_images(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_images = value.get("code_images", {})
    if not isinstance(raw_images, dict):
        return {}
    trusted: dict[str, dict[str, Any]] = {}
    for raw_code, metadata in raw_images.items():
        code = str(raw_code or "").upper()
        if CODE_RE.fullmatch(code) is None or not isinstance(metadata, dict):
            continue
        image_metadata = _trusted_cached_image(metadata)
        if image_metadata["image_url"]:
            trusted[code] = image_metadata
    return dict(sorted(trusted.items()))


def _verify_variant_images(images: dict[str, str]) -> dict[str, dict[str, Any]]:
    verified_by_url: dict[str, dict[str, Any]] = {}
    image_urls = sorted(set(images.values()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        metadata_by_url = executor.map(_verify_official_image, image_urls)
    for image_url, metadata in zip(image_urls, metadata_by_url):
        if metadata["image_url"]:
            verified_by_url[image_url] = metadata
    return {
        key: verified_by_url[image_url]
        for key, image_url in sorted(images.items())
        if image_url in verified_by_url
    }


def _verify_code_images(images: dict[str, str]) -> dict[str, dict[str, Any]]:
    verified_by_url: dict[str, dict[str, Any]] = {}
    image_urls = sorted(set(images.values()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        metadata_by_url = executor.map(_verify_official_image, image_urls)
    for image_url, metadata in zip(image_urls, metadata_by_url):
        if metadata["image_url"]:
            verified_by_url[image_url] = metadata
    return {
        code: verified_by_url[image_url]
        for code, image_url in sorted(images.items())
        if image_url in verified_by_url
    }


def _verify_official_image(image_url: str) -> dict[str, Any]:
    if not is_official_image_url(image_url):
        return _empty_image_metadata()
    request = urllib.request.Request(
        image_url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
        method="HEAD",
    )
    try:
        with _open_official(request, timeout=IMAGE_VALIDATION_TIMEOUT) as response:
            resolved_url = response.geturl()
            content_type = response.headers.get_content_type().lower()
            try:
                content_length = int(response.headers.get("Content-Length", ""))
            except (TypeError, ValueError):
                return _empty_image_metadata()
    except (OSError, ValueError, urllib.error.URLError):
        return _empty_image_metadata()
    metadata = {
        "image_url": resolved_url,
        "image_verified": True,
        "image_content_type": content_type,
        "image_content_length": content_length,
    }
    return _trusted_cached_image(metadata)


class _OfficialRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not is_official_url(target):
            raise ValueError("La redireccion no apunta a un host oficial HTTPS de Offiho")
        return super().redirect_request(req, fp, code, msg, headers, target)


_OFFICIAL_OPENER = urllib.request.build_opener(_OfficialRedirectHandler())


def _open_official(request: urllib.request.Request, *, timeout: int):
    if not is_official_url(request.full_url):
        raise ValueError("La URL no pertenece a un host oficial HTTPS de Offiho")
    return _OFFICIAL_OPENER.open(request, timeout=timeout)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []
        self.meta: dict[str, str] = {}
        self.text: list[str] = []
        self.names: list[str] = []
        self.primary_names: list[str] = []
        self.collection_images: list[dict[str, str]] = []
        self._name_tag = ""
        self._name_parts: list[str] = []
        self._ignored_depth = 0
        self._product_options_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        values = {name.lower(): value or "" for name, value in attrs}
        classes = {value.casefold() for value in values.get("class", "").split()}
        if tag == "div":
            if self._product_options_depth:
                self._product_options_depth += 1
            elif "product-options" in classes:
                self._product_options_depth = 1
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag in {"img", "source"}:
            for name in ("src", "data-src", "data-original", "data-image", "data-zoom-image"):
                if values.get(name):
                    self.images.append(values[name])
            if values.get("srcset"):
                self.images.extend(_srcset_urls(values["srcset"]))
            cloudzoom = values.get("data-cloudzoom", "")
            if (
                self._product_options_depth
                and classes.intersection({"colecciones", "cloudzoom-gallery"})
                and cloudzoom
            ):
                match = re.search(
                    r"(?:^|[,\s])image\s*:\s*(['\"])(?P<url>[^'\"]+)\1",
                    cloudzoom,
                    flags=re.IGNORECASE,
                )
                if match:
                    self.collection_images.append(
                        {
                            "swatch": values.get("src", values.get("data-src", "")),
                            "image": match.group("url"),
                        }
                    )
        elif tag == "meta":
            key = values.get("property", values.get("name", "")).lower()
            content = values.get("content", "")
            if key and content:
                self.meta[key] = content
        if tag in {"title", "h1", "h2"} and not self._name_tag:
            self._name_tag = tag
            self._name_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag == "div" and self._product_options_depth:
            self._product_options_depth -= 1
        if tag == self._name_tag:
            value = normalize_space(" ".join(self._name_parts))
            if value:
                self.names.append(value)
                if tag in {"title", "h1"}:
                    self.primary_names.append(value)
            self._name_tag = ""
            self._name_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text.append(data)
        if self._name_tag:
            self._name_parts.append(data)


class _ColosVariantParser(HTMLParser):
    """Relaciona cada control de color Colos con su imagen ``img-coloreN``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.labels: dict[str, str] = {}
        self.images: dict[str, str] = {}
        self._label_rel = ""
        self._label_depth = 0
        self._label_parts: list[str] = []
        self._image_rel = ""
        self._image_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        classes = values.get("class", "").split()

        if self._label_rel:
            self._label_depth += 1
        elif tag == "a" and "ico-colore" in classes and values.get("data-rel"):
            self._label_rel = values["data-rel"]
            self._label_depth = 1
            self._label_parts = []

        if tag == "div":
            if self._image_rel:
                self._image_depth += 1
            else:
                relation = next(
                    (value for value in classes if re.fullmatch(r"img-colore\d+", value)),
                    "",
                )
                if relation:
                    self._image_rel = relation
                    self._image_depth = 1
        elif tag in {"img", "source"} and self._image_rel:
            raw_image = next(
                (
                    values[name]
                    for name in ("src", "data-src", "data-original")
                    if values.get(name)
                ),
                "",
            )
            if raw_image:
                self.images.setdefault(self._image_rel, raw_image)

    def handle_endtag(self, tag: str) -> None:
        if self._label_rel:
            self._label_depth -= 1
            if self._label_depth == 0:
                label = normalize_space(" ".join(self._label_parts))
                if label:
                    self.labels[self._label_rel] = label
                self._label_rel = ""
                self._label_parts = []
        if tag == "div" and self._image_rel:
            self._image_depth -= 1
            if self._image_depth == 0:
                self._image_rel = ""

    def handle_data(self, data: str) -> None:
        if self._label_rel:
            self._label_parts.append(data)


def _srcset_urls(value: str) -> list[str]:
    return [candidate.strip().split(" ", 1)[0] for candidate in value.split(",") if candidate.strip()]


def _page_product_codes(
    page_url: str,
    parser: _PageParser,
    *,
    fallback_codes: Sequence[str] = (),
) -> list[str]:
    primary_codes = sorted(
        {
            code.upper()
            for code in CODE_RE.findall(unescape(" ".join(parser.primary_names)))
        }
    )
    if primary_codes:
        return primary_codes
    page_codes = sorted(
        {
            code.upper()
            for code in CODE_RE.findall(
                urllib.parse.unquote(urllib.parse.urlsplit(page_url).path)
            )
        }
    )
    if page_codes:
        return page_codes
    return sorted({str(code).upper() for code in fallback_codes if str(code).strip()})


def _compact_variant_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    return re.sub(
        r"[^A-Z0-9]",
        "",
        "".join(char for char in normalized if not unicodedata.combining(char)),
    )


def _variant_keys_from_image_reference(value: str, codes: Sequence[str]) -> list[str]:
    stem = Path(urllib.parse.unquote(urllib.parse.urlsplit(value).path)).stem
    compact = _compact_variant_value(stem)
    for code in sorted(codes, key=lambda item: len(_compact_variant_value(item)), reverse=True):
        compact_code = _compact_variant_value(code)
        code_index = compact.find(compact_code) if compact_code else -1
        if code_index >= 0:
            compact = compact[code_index + len(compact_code) :]
            break

    words_by_compact = sorted(
        {(_compact_variant_value(word), word) for word in VARIANT_WORDS if _compact_variant_value(word)},
        key=lambda item: (-len(item[0]), item[0]),
    )
    matches: list[tuple[int, int, str]] = []
    for compact_word, word in words_by_compact:
        start = 0
        while True:
            index = compact.find(compact_word, start)
            if index < 0:
                break
            matches.append((index, -len(compact_word), word))
            start = index + max(1, len(compact_word))
    words: list[str] = []
    occupied_until = -1
    for index, negative_length, word in sorted(matches):
        length = -negative_length
        if index < occupied_until:
            continue
        words.append(word)
        occupied_until = index + length
    return _variant_lookup_keys(" ".join(words))


def _extract_code_image_urls(
    page_url: str,
    parser: _PageParser,
    *,
    codes: Sequence[str],
) -> dict[str, str]:
    ranked: dict[str, tuple[int, int, str]] = {}
    for index, raw_image in enumerate(parser.images):
        resolved = urllib.parse.urljoin(page_url, normalize_space(raw_image))
        if not is_official_image_url(resolved):
            continue
        compact_path = _compact_variant_value(
            urllib.parse.unquote(urllib.parse.urlsplit(resolved).path)
        )
        for code in codes:
            normalized_code = str(code or "").upper()
            compact_code = _compact_variant_value(normalized_code)
            if not compact_code or compact_code not in compact_path:
                continue
            score = _product_image_score(page_url, resolved, [normalized_code])
            if score is None:
                continue
            candidate = (score, -index, resolved)
            if normalized_code not in ranked or candidate > ranked[normalized_code]:
                ranked[normalized_code] = candidate
    return {code: value[2] for code, value in sorted(ranked.items())}


def _extract_variant_image_urls(
    page_url: str,
    parser: _PageParser,
    *,
    codes: Sequence[str],
) -> dict[str, str]:
    images: dict[str, str] = {}
    for collection_image in parser.collection_images:
        raw_image = normalize_space(collection_image.get("image", ""))
        resolved = urllib.parse.urljoin(page_url, raw_image)
        if not is_official_image_url(resolved) or _product_image_score(page_url, resolved, codes) is None:
            continue
        keys = [
            *_variant_keys_from_image_reference(raw_image, codes),
            *_variant_keys_from_image_reference(collection_image.get("swatch", ""), codes),
            *_finish_code_keys_from_swatch(collection_image.get("swatch", "")),
        ]
        for key in keys:
            images.setdefault(key, resolved)
    return dict(sorted(images.items()))


def _colos_color_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_space(value).upper())
    ascii_value = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return COLOS_COLOR_ALIASES.get(ascii_value, normalize_variant(ascii_value))


def _extract_colos_variant_image_urls(page_url: str, payload: str) -> dict[str, str]:
    if urllib.parse.urlsplit(page_url).hostname not in COLOS_HOSTS:
        return {}
    parser = _ColosVariantParser()
    parser.feed(payload)
    images: dict[str, str] = {}
    for relation, label in parser.labels.items():
        raw_image = parser.images.get(relation, "")
        resolved = urllib.parse.urljoin(page_url, normalize_space(raw_image))
        key = _colos_color_key(label)
        if key and is_official_image_url(resolved):
            images.setdefault(key, resolved)
    return dict(sorted(images.items()))


def _finish_code_keys_from_swatch(value: str) -> list[str]:
    stem = Path(urllib.parse.unquote(urllib.parse.urlsplit(str(value or "")).path)).stem.upper()
    parts = re.findall(r"[A-Z0-9]+", stem)
    if not parts or not all(FINISH_CODE_RE.fullmatch(part) or part in FINISH_CODE_WORDS for part in parts):
        return []
    return _variant_lookup_keys(" ".join(parts))


def _extract_shopify_variant_image_urls(
    page_url: str,
    payload: str,
    *,
    codes: Sequence[str],
) -> dict[str, str]:
    if urllib.parse.urlsplit(page_url).hostname not in {"offihoblack.com", "www.offihoblack.com"}:
        return {}

    decoder = json.JSONDecoder()
    candidates: dict[str, set[str]] = {}
    marker = re.compile(r"\bGRFQConfigs\.product\s*=", flags=re.IGNORECASE)
    for match in marker.finditer(payload):
        remainder = payload[match.end() :].lstrip()
        try:
            product, _ = decoder.raw_decode(remainder)
        except json.JSONDecodeError:
            continue
        if not isinstance(product, dict) or not isinstance(product.get("variants"), list):
            continue

        identity_text = " ".join(
            str(product.get(field, "")) for field in ("title", "handle")
        )
        product_codes = {
            code.upper() for code in CODE_RE.findall(unescape(identity_text))
        }
        expected_codes = {str(code).upper() for code in codes if str(code).strip()}
        if product_codes and expected_codes and product_codes.isdisjoint(expected_codes):
            continue

        option_count = len(
            [option for option in product.get("options", []) if normalize_space(option)]
        ) if isinstance(product.get("options"), list) else 0
        for variant in product["variants"]:
            if not isinstance(variant, dict):
                continue
            featured_image = variant.get("featured_image")
            raw_image = featured_image.get("src", "") if isinstance(featured_image, dict) else ""
            if not raw_image:
                featured_media = variant.get("featured_media")
                preview = featured_media.get("preview_image", {}) if isinstance(featured_media, dict) else {}
                raw_image = preview.get("src", "") if isinstance(preview, dict) else ""
            resolved = urllib.parse.urljoin(page_url, normalize_space(raw_image))
            if not is_official_image_url(resolved):
                continue
            if _product_image_score(page_url, resolved, codes) is None:
                continue

            title = normalize_space(variant.get("public_title") or variant.get("title"))
            if title.casefold() == "default title":
                continue
            if option_count > 1:
                labels = [title] if title else []
            else:
                labels = [
                    str(variant.get(field, ""))
                    for field in ("option1", "public_title", "title")
                    if normalize_space(variant.get(field, ""))
                ]
            for label in labels:
                for key in _variant_lookup_keys(label):
                    if key != "DEFAULT TITLE":
                        candidates.setdefault(key, set()).add(resolved)
    return {
        key: next(iter(urls))
        for key, urls in sorted(candidates.items())
        if len(urls) == 1
    }


def _extract_official_image_url(
    page_url: str,
    parser: _PageParser,
    *,
    codes: Sequence[str] = (),
    extra_candidates: Sequence[str] = (),
) -> str:
    ranked: list[tuple[int, int, str]] = []
    for index, raw_candidate in enumerate((parser.meta.get("og:image", ""), *parser.images, *extra_candidates)):
        candidate = normalize_space(raw_candidate)
        if not candidate or "{" in candidate or "}" in candidate:
            continue
        resolved = urllib.parse.urljoin(page_url, candidate)
        if resolved == page_url or not is_official_image_url(resolved):
            continue
        score = _product_image_score(page_url, resolved, codes)
        if score is not None:
            ranked.append((score, -index, resolved))
    return max(ranked)[2] if ranked else ""


def _product_image_score(page_url: str, image_url: str, codes: Sequence[str]) -> int | None:
    image_path = urllib.parse.unquote(urllib.parse.urlsplit(image_url).path).casefold()
    image_tokens = set(re.findall(r"[a-z0-9]+", image_path))
    if any(token in image_path for token in NON_PRODUCT_IMAGE_TOKENS):
        return None

    score = 0
    compact_path = re.sub(r"[^a-z0-9]", "", image_path)
    for code in codes:
        compact_code = re.sub(r"[^a-z0-9]", "", str(code).casefold())
        if compact_code and compact_code in compact_path:
            score += 100
    page_leaf = urllib.parse.unquote(urllib.parse.urlsplit(page_url).path).rstrip("/").rsplit("/", 1)[-1]
    for token in re.findall(r"[a-z0-9]+", page_leaf.casefold()):
        if len(token) > 2 and token in image_tokens:
            score += 35
    if any(token in image_tokens for token in PRODUCT_IMAGE_TOKENS):
        score += 25
    return score


def _page_link_key(value: str) -> str:
    parsed = urllib.parse.urlsplit(_normalize_official_link(str(value or "")))
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", "")
    )


def _merge_linked_variant_pages(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Completa una ficha con la paleta extendida enlazada por el propio producto."""

    by_url = {
        _page_link_key(record.get("url", "")): record
        for record in records
        if normalize_space(record.get("url"))
    }
    enriched: list[dict[str, Any]] = []
    for record in records:
        merged = dict(record)
        variants = dict(_trusted_cached_variant_images(record))
        record_codes = {
            str(code).upper() for code in record.get("codes", []) if str(code).strip()
        }
        for raw_link in record.get("links", []):
            linked = by_url.get(_page_link_key(raw_link))
            if not isinstance(linked, dict):
                continue
            linked_path = urllib.parse.urlsplit(str(linked.get("url", ""))).path.casefold()
            linked_codes = {
                str(code).upper()
                for code in linked.get("codes", [])
                if str(code).strip()
            }
            if (
                not linked_path.rstrip("/").endswith("/colores")
                or not record_codes
                or linked_codes != record_codes
            ):
                continue
            for key, metadata in _trusted_cached_variant_images(linked).items():
                variants.setdefault(key, metadata)
        merged["variant_images"] = dict(sorted(variants.items()))
        enriched.append(merged)
    return enriched


def build_site_product_index(
    cache: dict[str, Any],
    *,
    no_network: bool = False,
    now: datetime | None = None,
    include_search: bool | None = None,
) -> dict[str, dict[str, Any]]:
    current_time = now or datetime.now(timezone.utc)
    cache_version = cache.get("cache_version")
    if no_network:
        if cache_version is None and isinstance(cache.get("site_index"), dict):
            cache["site_index"] = _sanitize_site_index(cache["site_index"])
            cache["cache_version"] = CACHE_VERSION
            cache["site_index_created_at"] = LEGACY_CACHE_TIMESTAMP
            cache["site_index_expires_at"] = LEGACY_CACHE_TIMESTAMP
            cache["migrated_from_legacy"] = True
            cache_version = CACHE_VERSION
        if cache_version != CACHE_VERSION:
            raise RuntimeError("La version del cache Offiho no es compatible con el modo sin red")
        cached = cache.get("site_index")
        if not isinstance(cached, dict):
            raise RuntimeError("El cache Offiho no contiene un indice web para el modo sin red")
        sanitized = _sanitize_site_index(cached)
        cache["site_index"] = sanitized
        return sanitized

    if cache_version != CACHE_VERSION:
        cache.clear()
        cache["cache_version"] = CACHE_VERSION

    cached = cache.get("site_index")
    expires_at = _parse_cache_datetime(cache.get("site_index_expires_at"))
    if isinstance(cached, dict) and expires_at is not None and expires_at > current_time:
        sanitized = _sanitize_site_index(cached)
        cache["site_index"] = sanitized
        return sanitized

    cache.pop("site_index", None)
    cache["site_pages"] = {}

    pages = cache.setdefault("site_pages", {})
    if not isinstance(pages, dict):
        pages = {}
        cache["site_pages"] = pages
    records: list[dict[str, Any]] = []
    for url in SITE_SEEDS:
        record = _cached_or_fetch_page(url, pages)
        if record:
            records.append(record)

    use_search = SITE_SEEDS == DEFAULT_SITE_SEEDS if include_search is None else include_search
    if use_search:
        search_products = _fetch_offiho_search_product_urls()
        remaining = max(0, MAX_DISCOVERED_PAGES - len(pages))
        _fetch_discovered_pages(search_products[:remaining], pages, records)

    discovered = _prioritize_product_pages(
        {
            link
            for record in records
            for link in record.get("links", [])
            if _is_official_page_url(link) and link not in SITE_SEEDS
        }
    )
    first_level = discovered[:FIRST_LEVEL_DISCOVERY_LIMIT]
    _fetch_discovered_pages(first_level, pages, records)

    while True:
        remaining = max(0, MAX_DISCOVERED_PAGES - len(pages))
        if not remaining:
            break
        next_level = _prioritize_product_pages(
            {
                link
                for record in records
                for link in record.get("links", [])
                if _is_official_page_url(link) and link not in SITE_SEEDS and link not in pages
            }
        )
        if not next_level:
            break
        _fetch_discovered_pages(next_level[:remaining], pages, records)

    index = _site_index_from_records(records)
    cache["site_index"] = index
    cache["site_index_created_at"] = current_time.isoformat()
    cache["site_index_expires_at"] = (current_time + timedelta(seconds=CACHE_TTL_SECONDS)).isoformat()
    return index


def _site_index_from_records(
    records: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in _merge_linked_variant_pages(records):
        codes = sorted({str(code).upper() for code in record.get("codes", []) if str(code).strip()})
        names = sorted({_product_name_key(name) for name in record.get("names", []) if _product_name_key(name)})
        code_images = _trusted_cached_code_images(record)
        candidate: dict[str, Any] = {
            "url": str(record.get("url", "")),
            "codes": codes,
            "names": names,
            "source_updated_at": str(record.get("source_updated_at", "")),
            "description": str(record.get("description", "")),
            "variant_images": _trusted_cached_variant_images(record),
            **_trusted_cached_image(record),
        }
        if candidate["image_url"] == candidate["url"]:
            candidate.update(_empty_image_metadata())
        if _product_page_priority(candidate["url"]) <= 1:
            page_key = f"page:{hashlib.sha256(candidate['url'].encode('utf-8')).hexdigest()[:20]}"
            index[page_key] = candidate
        for key in codes:
            keyed_candidate = dict(candidate)
            keyed_candidate["codes"] = [key]
            if key in code_images:
                keyed_candidate.update(code_images[key])
            existing = index.get(key)
            if existing is None or _site_candidate_rank(key, keyed_candidate) > _site_candidate_rank(key, existing):
                index[key] = keyed_candidate
        for key in (f"name:{name}" for name in names):
            existing = index.get(key)
            if existing is None or _site_candidate_rank(key, candidate) > _site_candidate_rank(key, existing):
                index[key] = candidate
    return index


def _fetch_discovered_pages(
    urls: Sequence[str],
    pages: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    pending = [url for url in urls if not isinstance(pages.get(url), dict)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        for url, record in zip(pending, executor.map(_fetch_official_page, pending)):
            pages[url] = record
    for url in urls:
        record = pages.get(url)
        if isinstance(record, dict) and record:
            records.append(record)


def _fetch_offiho_search_product_urls(
    terms: Sequence[str] = OFFIHO_SEARCH_TERMS,
) -> list[str]:
    """Enumera hojas de producto publicadas en la búsqueda oficial de Offiho."""

    urls: set[str] = set()
    endpoint = "https://www.offiho.com/search.php"
    for term in terms:
        data = urllib.parse.urlencode({"keyword": normalize_space(term)}).encode("ascii")
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with _open_official(request, timeout=15) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname not in OFFIHO_HOSTS:
                    continue
                payload = response.read(1_500_000).decode("utf-8", errors="replace")
        except (OSError, ValueError, urllib.error.URLError):
            continue
        parser = _PageParser()
        parser.feed(payload)
        for raw_link in parser.links:
            resolved = _normalize_official_link(
                urllib.parse.urldefrag(urllib.parse.urljoin(endpoint, raw_link))[0]
            )
            if (
                urllib.parse.urlsplit(resolved).hostname in {"offiho.com", "www.offiho.com"}
                and _product_page_priority(resolved) <= 1
                and _is_official_page_url(resolved)
            ):
                urls.add(_canonical_product_url(resolved))
    return _prioritize_product_pages(urls)


def _prioritize_product_pages(urls: set[str]) -> list[str]:
    canonical_urls = {_canonical_product_url(url) for url in urls}
    return sorted(canonical_urls, key=lambda url: (_product_page_priority(url), url))


def _canonical_product_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    marker = "/products/"
    if (
        parsed.hostname in {"offihoblack.com", "www.offihoblack.com"}
        and path.startswith("/collections/")
        and marker in path
    ):
        path = path[path.index(marker) :].rstrip("/")
    elif parsed.hostname in {"offihoblack.com", "www.offihoblack.com"} and path.startswith("/products/"):
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _site_candidate_rank(key: str, candidate: dict[str, Any]) -> tuple[int, int, int, str]:
    url = str(candidate.get("url", ""))
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    lookup = key.removeprefix("name:")
    compact_lookup = re.sub(r"[^A-Z0-9]", "", lookup.upper())
    compact_path = re.sub(r"[^A-Z0-9]", "", path.upper())
    leaf_name = _product_name_key(path.rstrip("/").rsplit("/", 1)[-1], codes=candidate.get("codes", []))
    product_page = int("/products/" in path.casefold() or "modelo-" in path.casefold())
    if key.startswith("name:"):
        return int(leaf_name == lookup), int(not product_page), int(bool(candidate.get("image_url"))), url
    return int(compact_lookup in compact_path), int(bool(candidate.get("image_url"))), product_page, url


def _product_page_priority(url: str) -> int:
    path = urllib.parse.urlsplit(url).path.casefold()
    host = urllib.parse.urlsplit(url).hostname
    if host in COLOS_HOSTS and re.fullmatch(r"/(?:en|es|it)/(?:products|productos|prodotti)/[^/]+/?", path):
        return 0
    if "/products/" in path:
        return 0
    if "modelo-" in path or "/galeria/" in path:
        return 1
    return 2


def _is_official_page_url(url: str) -> bool:
    if not is_official_url(url):
        return False
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.casefold()
    return suffix not in IMAGE_EXTENSIONS | {".css", ".csv", ".doc", ".docx", ".dwg", ".obj", ".pdf", ".xls", ".xlsx"}


def _normalize_official_link(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(urllib.parse.unquote(parsed.query), safe="=&?/:@!$'()*+,;%-._~")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def _sanitize_site_index(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sanitized: dict[str, dict[str, Any]] = {}
    for code, value in index.items():
        if not isinstance(value, dict):
            continue
        url = str(value.get("url", ""))
        if not is_official_url(url):
            continue
        image_metadata = _trusted_cached_image(value)
        if image_metadata["image_url"] == url:
            image_metadata = _empty_image_metadata()
        sanitized_value: dict[str, Any] = {
            "url": url,
            "source_updated_at": str(value.get("source_updated_at", "")),
            **image_metadata,
        }
        variant_images = _trusted_cached_variant_images(value)
        if variant_images:
            sanitized_value["variant_images"] = variant_images
        description = str(value.get("description", ""))
        if description:
            sanitized_value["description"] = description
        sanitized[str(code)] = sanitized_value
        if isinstance(value.get("codes"), list):
            sanitized[str(code)]["codes"] = sorted(
                {str(item).upper() for item in value["codes"] if str(item).strip()}
            )
        if isinstance(value.get("names"), list):
            sanitized[str(code)]["names"] = sorted(
                {_product_name_key(item) for item in value["names"] if _product_name_key(item)}
            )
    return sanitized


def _site_index_candidates(index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, product in index.items():
        codes = list(product.get("codes", []))
        names = list(product.get("names", []))
        if key.startswith("name:"):
            names.append(key.removeprefix("name:"))
        elif not key.startswith("page:"):
            codes.append(key)
        candidate = {
            **product,
            "codes": sorted({str(code).upper() for code in codes if str(code).strip()}),
            "names": sorted({_product_name_key(name) for name in names if _product_name_key(name)}),
        }
        signature = _canonical_hash(
            {
                "url": candidate.get("url", ""),
                "codes": candidate["codes"],
                "names": candidate["names"],
                "image_url": candidate.get("image_url", ""),
                "variant_images": candidate.get("variant_images", {}),
            }
        )
        if signature not in seen:
            seen.add(signature)
            candidates.append(candidate)
    return candidates


def _parse_cache_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        with _open_official(request, timeout=15) as response:
            page_url = _normalize_official_link(response.geturl())
            if not is_official_url(page_url):
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
            _normalize_official_link(urllib.parse.urldefrag(urllib.parse.urljoin(page_url, link))[0])
            for link in parser.links
            if is_official_url(urllib.parse.urldefrag(urllib.parse.urljoin(page_url, link))[0])
        }
    )
    page_text = " ".join(parser.text)
    metadata_text = " ".join(parser.meta.values())
    codes = sorted({code.upper() for code in CODE_RE.findall(unescape(f"{page_text} {metadata_text}"))})
    product_codes = _page_product_codes(page_url, parser, fallback_codes=codes)
    names = _page_names(page_url, parser, product_codes)
    image_url = _extract_official_image_url(
        page_url,
        parser,
        codes=product_codes,
        extra_candidates=[link for link in links if is_official_image_url(link)],
    )
    image_metadata = _verify_official_image(image_url) if image_url else _empty_image_metadata()
    code_images = (
        _verify_code_images(_extract_code_image_urls(page_url, parser, codes=product_codes))
        if len(product_codes) > 1
        else {}
    )
    variant_image_urls = _extract_variant_image_urls(page_url, parser, codes=product_codes)
    variant_image_urls.update(
        _extract_shopify_variant_image_urls(page_url, payload, codes=product_codes)
    )
    variant_image_urls.update(_extract_colos_variant_image_urls(page_url, payload))
    variant_images = _verify_variant_images(variant_image_urls)
    return {
        "url": page_url,
        "links": links,
        "codes": product_codes,
        "names": names,
        "description": _page_description(page_text),
        "code_images": code_images,
        "variant_images": variant_images,
        **image_metadata,
        "source_updated_at": source_updated_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _page_description(page_text: str) -> str:
    normalized = normalize_space(unescape(page_text))
    match = re.search(r"\bDESCRIPCI[OÓ]N\b", normalized, flags=re.IGNORECASE)
    if not match:
        return ""
    description = normalized[match.end() :]
    stop = re.search(
        r"\b(?:ACCESORIOS\s+OPCIONALES|MODELADOS\s+3D|VIDEOS|PRODUCTOS\s+RELACIONADOS)\b",
        description,
        flags=re.IGNORECASE,
    )
    if stop:
        description = description[: stop.start()]
    return normalize_space(description)[:2000]


def _page_names(page_url: str, parser: _PageParser, codes: Sequence[str]) -> list[str]:
    raw_names = [
        *parser.names,
        parser.meta.get("og:title", ""),
        parser.meta.get("twitter:title", ""),
        urllib.parse.unquote(urllib.parse.urlsplit(page_url).path).rstrip("/").rsplit("/", 1)[-1],
    ]
    names = {_product_name_key(name, codes=codes) for name in raw_names}
    return sorted(name for name in names if name)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _sha256_bytes(payload)


def _deterministic_generated_at(pdf_paths: Sequence[Path]) -> tuple[str, str]:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch is not None:
        try:
            value = datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("SOURCE_DATE_EPOCH debe ser un entero Unix valido") from exc
        return value.isoformat(), "SOURCE_DATE_EPOCH"

    source_dates: list[datetime] = []
    for path in pdf_paths:
        try:
            metadata = PdfReader(path).metadata
        except Exception:
            continue
        if metadata is None:
            continue
        for attribute in ("modification_date", "creation_date"):
            try:
                value = getattr(metadata, attribute)
            except (AttributeError, ValueError):
                continue
            if not isinstance(value, datetime):
                continue
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            source_dates.append(value.astimezone(timezone.utc))
    if source_dates:
        return max(source_dates).isoformat(), "pdf_metadata"
    return LEGACY_CACHE_TIMESTAMP, "fixed_epoch"


def _spec_index_hash_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ""
            if key == "product_url"
            and str(item or "").casefold().startswith("file:")
            else _spec_index_hash_projection(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_spec_index_hash_projection(item) for item in value]
    return value


def _trusted_spec_product_url(product: Mapping[str, Any]) -> str:
    value = normalize_space(product.get("product_url"))
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return ""
    hostname = str(parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not (hostname == "sharepoint.com" or hostname.endswith(".sharepoint.com"))
        or parsed.username
        or parsed.password
        or port not in {None, 443}
    ):
        return ""
    return value


def build_catalog(
    inventory_path: Path,
    pdf_paths: Sequence[Path],
    cache_path: Path,
    output_path: Path,
    *,
    no_network: bool = False,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    asset_base_url: str = DEFAULT_ASSET_BASE_URL,
    spec_guide_paths: Sequence[Path] = (),
    spec_source_urls: Mapping[str, str] | None = None,
    colos_exact_manifest_path: Path | None = DEFAULT_COLOS_EXACT_MANIFEST_PATH,
    offiho_exact_manifest_path: Path | None = DEFAULT_OFFIHO_EXACT_MANIFEST_PATH,
    official_web_visual_exact_manifest_paths: Sequence[Path] = DEFAULT_OFFICIAL_WEB_VISUAL_EXACT_MANIFEST_PATHS,
    catalog_exact_crop_manifest_paths: Sequence[Path] = DEFAULT_CATALOG_EXACT_CROP_MANIFEST_PATHS,
    spec_visual_exact_manifest_paths: Sequence[Path] = DEFAULT_SPEC_VISUAL_EXACT_MANIFEST_PATHS,
    visual_rejection_manifest_path: Path | None = DEFAULT_VISUAL_REJECTION_MANIFEST_PATH,
    generated_image_manifest_path: Path | None = DEFAULT_GENERATED_IMAGE_MANIFEST_PATH,
) -> dict[str, Any]:
    cache = _load_cache(cache_path)
    inventory_bytes = inventory_path.read_bytes()
    inventory_sha256 = _sha256_bytes(inventory_bytes)
    pdf_sources = []
    for path in pdf_paths:
        payload = path.read_bytes()
        pdf_sources.append(
            {
                "path": path,
                "name": path.name,
                "sha256": _sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        )
    pdf_sources.sort(key=lambda source: (source["sha256"], source["name"].casefold()))
    ordered_pdf_paths = [source["path"] for source in pdf_sources]
    items, inventory_audit = _parse_inventory_xls(inventory_path)
    generated_images, generated_image_source = load_generated_image_manifest(
        generated_image_manifest_path
    )
    ordered_spec_paths = sorted(
        (Path(path) for path in spec_guide_paths),
        key=lambda path: (path.name.casefold(), str(path.resolve()).casefold()),
    )
    spec_sources: list[dict[str, Any]] = []
    for path in ordered_spec_paths:
        if not path.is_file():
            raise RuntimeError(f"No existe la guia SPEC Offiho: {path}")
        payload = path.read_bytes()
        spec_sources.append(
            {
                "name": path.name,
                "sha256": _sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        )
    colos_exact_images, colos_manifest_source = load_exact_image_manifest(
        colos_exact_manifest_path,
        allowed_hosts=COLOS_HOSTS,
        match_status="official_colos_exact",
    )
    offiho_exact_images, offiho_manifest_source = load_exact_image_manifest(
        offiho_exact_manifest_path,
        allowed_hosts=OFFIHO_HOSTS,
        match_status="official_variant_exact",
    )
    official_web_visual_exact_images: dict[str, dict[str, Any]] = {}
    official_web_visual_manifest_sources: list[dict[str, Any]] = []
    for path in sorted(
        (Path(path) for path in official_web_visual_exact_manifest_paths),
        key=lambda value: (value.name.casefold(), str(value.resolve()).casefold()),
    ):
        web_index, web_source = load_exact_image_manifest(
            path,
            allowed_hosts=OFFICIAL_HOSTS,
            allowed_image_hosts=MANAGED_ASSET_HOSTS,
            match_status="official_web_visual_exact",
        )
        duplicate_keys = sorted(set(official_web_visual_exact_images).intersection(web_index))
        if duplicate_keys:
            raise RuntimeError(
                "Claves duplicadas entre manifiestos web visuales exactos: "
                + ", ".join(duplicate_keys)
            )
        official_web_visual_exact_images.update(web_index)
        official_web_visual_manifest_sources.append(web_source)
    catalog_exact_crops: dict[str, dict[str, Any]] = {}
    catalog_crop_sources: list[dict[str, Any]] = []
    for path in sorted(
        (Path(path) for path in catalog_exact_crop_manifest_paths),
        key=lambda value: (value.name.casefold(), str(value.resolve()).casefold()),
    ):
        crop_index, crop_source = load_exact_image_manifest(
            path,
            allowed_hosts=CATALOG_SOURCE_HOSTS,
            allowed_image_hosts=MANAGED_ASSET_HOSTS,
            match_status="official_catalog_exact_crop",
        )
        duplicate_keys = sorted(set(catalog_exact_crops).intersection(crop_index))
        if duplicate_keys:
            raise RuntimeError(
                "Claves duplicadas entre manifiestos de recortes exactos: "
                + ", ".join(duplicate_keys)
            )
        catalog_exact_crops.update(crop_index)
        catalog_crop_sources.append(crop_source)
    spec_visual_exact_images: dict[str, dict[str, Any]] = {}
    spec_visual_manifest_sources: list[dict[str, Any]] = []
    for path in sorted(
        (Path(path) for path in spec_visual_exact_manifest_paths),
        key=lambda value: (value.name.casefold(), str(value.resolve()).casefold()),
    ):
        spec_index, spec_source = load_exact_image_manifest(
            path,
            allowed_hosts=SHAREPOINT_CATALOG_HOSTS,
            allowed_image_hosts=MANAGED_ASSET_HOSTS,
            match_status="spec_guide_visual_exact",
        )
        duplicate_keys = sorted(set(spec_visual_exact_images).intersection(spec_index))
        if duplicate_keys:
            raise RuntimeError(
                "Claves duplicadas entre manifiestos SPEC visuales exactos: "
                + ", ".join(duplicate_keys)
            )
        spec_visual_exact_images.update(spec_index)
        spec_visual_manifest_sources.append(spec_source)
    visual_rejections, visual_rejection_source = load_visual_rejection_manifest(
        visual_rejection_manifest_path,
        inventory_keys=(
            {item["inventory_key"] for item in items}
            if inventory_path.resolve()
            == (PROJECT_ROOT / "catalog_sources" / "offiho" / "existencias.xls").resolve()
            else None
        ),
    )
    pdf_prices = parse_pdf_price_index(ordered_pdf_paths)
    pdf_products = parse_pdf_product_index(
        ordered_pdf_paths,
        items,
        assets_dir,
        asset_base_url,
    ) if ordered_pdf_paths else {}
    pdf_index_sha256 = _canonical_hash(
        {
            key: {
                field: json_number(value) if isinstance(value, Decimal) else value
                for field, value in product.items()
            }
            for key, product in sorted(pdf_products.items())
        }
    )
    brochure_manifest = _official_brochure_manifest(assets_dir)
    brochure_index_sha256 = _canonical_hash(brochure_manifest)
    site_index = build_site_product_index(cache, no_network=no_network)
    site_index_sha256 = _canonical_hash(site_index)
    generated_at, generated_at_source = _deterministic_generated_at(ordered_pdf_paths)
    site_candidates = _site_index_candidates(site_index)
    matched_products: dict[str, dict[str, Any]] = {}
    for item in items:
        inventory_key = item["inventory_key"]
        product = _first_non_rejected_product(
            inventory_key,
            (
                offiho_exact_images.get(inventory_key),
                official_web_visual_exact_images.get(inventory_key),
                colos_exact_images.get(inventory_key),
                catalog_exact_crops.get(inventory_key),
                spec_visual_exact_images.get(inventory_key),
            ),
            visual_rejections,
        )
        if product is None:
            filtered_site_candidates = (
                [
                    _without_rejected_candidate_images(
                        inventory_key,
                        candidate,
                        visual_rejections,
                    )
                    for candidate in site_candidates
                ]
                if inventory_key in visual_rejections
                else site_candidates
            )
            product = _without_rejected_product_image(
                inventory_key,
                match_official_product(
                    OffihoIdentity(item["code"], item["name"], item["variant"]),
                    filtered_site_candidates,
                ),
                visual_rejections,
            )
        matched_products[inventory_key] = product
    spec_inventory_items = [
        item
        for item in items
        if not matched_products[item["inventory_key"]].get("image_url")
    ]
    spec_images = (
        extract_offiho_spec_images(
            ordered_spec_paths,
            spec_inventory_items,
            assets_dir=assets_dir / "spec-images",
            base_url=f"{asset_base_url.rstrip('/')}/spec-images",
            source_urls=spec_source_urls,
        )
        if ordered_spec_paths and spec_inventory_items
        else {}
    )
    spec_index_sha256 = _canonical_hash(_spec_index_hash_projection(spec_images))
    source_manifest = {
        "manifest_version": SOURCE_MANIFEST_VERSION,
        "inventory_sha256": inventory_sha256,
        "pdf_sha256": sorted(source["sha256"] for source in pdf_sources),
        "site_index_sha256": site_index_sha256,
        "pdf_index_sha256": pdf_index_sha256,
        "brochure_index_sha256": brochure_index_sha256,
        "spec_guide_sha256": sorted(source["sha256"] for source in spec_sources),
        "spec_index_sha256": spec_index_sha256,
        "colos_exact_manifest_sha256": colos_manifest_source["sha256"],
        "offiho_exact_manifest_sha256": offiho_manifest_source["sha256"],
        "official_web_visual_exact_manifest_sha256": sorted(
            source["sha256"] for source in official_web_visual_manifest_sources
        ),
        "catalog_exact_crop_manifest_sha256": sorted(
            source["sha256"] for source in catalog_crop_sources
        ),
        "spec_visual_exact_manifest_sha256": sorted(
            source["sha256"] for source in spec_visual_manifest_sources
        ),
        "visual_rejection_manifest_sha256": visual_rejection_source["sha256"],
        "generated_image_manifest_sha256": generated_image_source["sha256"],
        "site_cache_version": CACHE_VERSION,
        "generated_at": generated_at,
        "generated_at_source": generated_at_source,
    }
    for item in items:
        identity = OffihoIdentity(item["code"], item["name"], item["variant"])
        if item["price_source"] == "missing":
            amount = pdf_prices.get(price_key(identity.code, identity.variant))
            if amount is not None:
                item["unit_price"] = json_number(amount)
                item["price_source"] = "pdf_exact"
        product = matched_products[item["inventory_key"]]
        spec_product = _without_rejected_product_image(
            item["inventory_key"],
            spec_images.get(item["inventory_key"], {}),
            visual_rejections,
        )
        pdf_product = pdf_products.get(item["inventory_key"], {})
        brochure_product = match_official_brochure_product(item, assets_dir, asset_base_url)
        support_product = _without_rejected_product_image(
            item["inventory_key"],
            _support_product_for_identity(
                identity,
                pdf_product,
                brochure_product,
                inventory_key=item["inventory_key"],
                visual_rejections=visual_rejections,
            ),
            visual_rejections,
        )
        if item["price_source"] == "missing" and pdf_product.get("unit_price") is not None:
            item["unit_price"] = json_number(pdf_product["unit_price"])
            item["price_source"] = "pdf_catalog"
        item["product_url"] = (
            product["url"]
            or _trusted_spec_product_url(spec_product)
            or str(support_product.get("product_url", ""))
        )
        site_image_url = (
            product["image_url"]
            if product.get("has_variant_catalog")
            else product["image_url"] or str(support_product.get("image_url", ""))
        )
        if not product["image_url"] and spec_product.get("image_url"):
            site_image_url = str(spec_product["image_url"])
        item["image_url"] = site_image_url
        item["description"] = (
            product["description"]
            or str(spec_product.get("description", ""))
            or str(pdf_product.get("description", ""))
            or _inventory_description(item)
        )
        item["description_source"] = (
            "official_site"
            if product["description"]
            else "spec_guide"
            if spec_product.get("description")
            else "pdf_catalog"
            if pdf_product.get("description")
            else "inventory_label"
        )
        item["match_status"] = (
            str(spec_product.get("match_status", "spec_guide_exact"))
            if not product["image_url"] and spec_product.get("image_url")
            else
            product["match_status"]
            if product["url"] or product["image_url"]
            else str(support_product.get("match_status", "unmatched"))
        )
        item["source_updated_at"] = (
            product["source_updated_at"]
            or str(support_product.get("source_updated_at", ""))
        )

    _clear_cross_model_support_images(items, asset_base_url)
    apply_visual_rejections(items, visual_rejections)
    for item in items:
        if not item.get("image_url") and item["inventory_key"] in visual_rejections:
            item["match_status"] = "visual_conflict_rejected"
    apply_generated_images(items, generated_images)

    result = {
        "source_hash": _canonical_hash(source_manifest),
        "generated_at": generated_at,
        "sources": {
            "manifest_version": SOURCE_MANIFEST_VERSION,
            "generated_at_source": generated_at_source,
            "inventory": {
                "name": inventory_path.name,
                "sha256": inventory_sha256,
                "size_bytes": len(inventory_bytes),
            },
            "pdfs": [
                {
                    "name": source["name"],
                    "sha256": source["sha256"],
                    "size_bytes": source["size_bytes"],
                }
                for source in pdf_sources
            ],
            "spec_guides": spec_sources,
            "spec_image_index": {
                "sha256": spec_index_sha256,
                "record_count": len(spec_images),
                "asset_base_url": f"{asset_base_url.rstrip('/')}/spec-images",
            },
            "colos_exact_images": colos_manifest_source,
            "offiho_exact_images": offiho_manifest_source,
            "official_web_visual_exact_images": official_web_visual_manifest_sources,
            "catalog_exact_crops": catalog_crop_sources,
            "spec_visual_exact_images": spec_visual_manifest_sources,
            "visual_rejections": visual_rejection_source,
            "generated_visual_references": generated_image_source,
            "site_index": {
                "sha256": site_index_sha256,
                "cache_version": CACHE_VERSION,
                "record_count": len(site_index),
                "created_at": str(cache.get("site_index_created_at", "")),
                "expires_at": str(cache.get("site_index_expires_at", "")),
                "offline": no_network,
            },
            "pdf_index": {
                "sha256": pdf_index_sha256,
                "record_count": len(pdf_products),
                "asset_base_url": asset_base_url,
            },
            "official_brochure": {
                "url": OFFICIAL_BROCHURE_URL,
                "sha256": brochure_index_sha256,
                "record_count": len(brochure_manifest),
            },
        },
        "total": len(items),
        **inventory_audit,
        "out_of_stock": sum(item["available_quantity"] == 0 for item in items),
        "inventory_prices": sum(item["price_source"] == "inventory" for item in items),
        "pdf_prices": sum(str(item["price_source"]).startswith("pdf_") for item in items),
        "official_images": sum(item.get("image_kind") == "official" for item in items),
        "generated_images": sum(item.get("image_kind") == "generated_reference" for item in items),
        "described_items": sum(bool(item.get("description")) for item in items),
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _clear_cross_model_support_images(
    items: Sequence[dict[str, Any]],
    asset_base_url: str,
) -> None:
    """Retira fotos PDF compartidas cuando representan modelos distintos.

    Los catalogos de apoyo a veces muestran una familia completa en una sola
    fotografia. Esa imagen no es evidencia suficiente para cada codigo: si el
    mismo activo local queda asociado a identidades de modelo diferentes, es
    mas seguro publicar la ficha sin imagen. Variantes tipograficas de una
    misma identidad (por ejemplo ``VESPER 103`` y ``VESPER/103``) se conservan.
    """

    support_prefix = f"{asset_base_url.rstrip('/')}/images/"
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        image_url = str(item.get("image_url", ""))
        if image_url.startswith(support_prefix):
            grouped.setdefault(image_url, []).append(item)

    for shared_items in grouped.values():
        model_keys = {
            _compact_variant_value(
                f"{normalize_space(item.get('code', ''))} {normalize_space(item.get('name', ''))}"
            )
            for item in shared_items
        }
        model_keys.discard("")
        if len(model_keys) > 1:
            for item in shared_items:
                item["image_url"] = ""


def _inventory_description(item: dict[str, Any]) -> str:
    name = normalize_space(item.get("name", "")) or normalize_space(item.get("code", ""))
    variant = normalize_space(item.get("variant", ""))
    unit = normalize_space(item.get("unit", ""))
    parts = [f"Producto Offiho {name}." if name else "Producto Offiho."]
    if variant:
        parts.append(f"Variante: {variant}.")
    if unit:
        parts.append(f"Unidad: {unit}.")
    return normalize_space(" ".join(parts))


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def download_inventory(url: str, output_path: Path) -> Path:
    if (
        not is_official_url(url)
        or urllib.parse.urlsplit(url).hostname not in OFFIHO_HOSTS
    ):
        raise ValueError("La URL de inventario debe ser HTTPS de un host oficial Offiho")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.ms-excel"})
    with _open_official(request, timeout=30) as response:
        if (
            not is_official_url(response.geturl())
            or urllib.parse.urlsplit(response.geturl()).hostname not in OFFIHO_HOSTS
        ):
            raise ValueError("La descarga de inventario redirigio fuera de los hosts oficiales")
        content_type = response.headers.get_content_type()
        if content_type not in {"application/vnd.ms-excel", "application/octet-stream", "text/html"}:
            raise ValueError("La URL de inventario no devolvio un archivo XLS")
        payload = response.read(MAX_INVENTORY_BYTES + 1)
    if len(payload) > MAX_INVENTORY_BYTES:
        raise ValueError("El inventario excede el limite permitido")
    is_html = _is_html_payload(payload)
    if content_type == "text/html" or is_html:
        if not is_html:
            raise ValueError("La respuesta HTML no contiene una tabla valida de inventario")
        try:
            html_rows = _html_inventory_rows(payload)
        except RuntimeError as exc:
            raise ValueError("La respuesta HTML no contiene una tabla valida de inventario") from exc
        if not any(normalize_space(row[1]) for row in html_rows):
            raise ValueError("La respuesta HTML no contiene filas de inventario")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return output_path


def _is_html_payload(payload: bytes) -> bool:
    stripped = payload.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return stripped.startswith((b"<!doctype html", b"<html"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye el catalogo Offiho para Mobiliti")
    parser.add_argument("--inventory-url", default=DEFAULT_INVENTORY_URL)
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY_PATH))
    parser.add_argument("--pdf", action="append", default=[])
    parser.add_argument("--spec-guide", action="append", default=[])
    parser.add_argument(
        "--colos-exact-manifest",
        default=str(DEFAULT_COLOS_EXACT_MANIFEST_PATH),
    )
    parser.add_argument(
        "--offiho-exact-manifest",
        default=str(DEFAULT_OFFIHO_EXACT_MANIFEST_PATH),
    )
    parser.add_argument(
        "--official-web-visual-exact-manifest",
        action="append",
        default=None,
        help=(
            "Manifiesto web visual exacto; puede repetirse. Si se indica al "
            "menos una vez, reemplaza el conjunto predeterminado."
        ),
    )
    parser.add_argument(
        "--catalog-exact-crop-manifest",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--spec-visual-exact-manifest",
        action="append",
        default=None,
    )
    parser.add_argument(
        "--visual-rejection-manifest",
        default=str(DEFAULT_VISUAL_REJECTION_MANIFEST_PATH),
        help=(
            "Manifiesto v1 de URLs visualmente rechazadas. Reemplaza el "
            "predeterminado; una cadena vacia lo desactiva explicitamente."
        ),
    )
    parser.add_argument(
        "--generated-image-manifest",
        default=str(DEFAULT_GENERATED_IMAGE_MANIFEST_PATH),
        help="Manifiesto v1 de referencias visuales generadas; una cadena vacia lo desactiva.",
    )
    parser.add_argument("--cache", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--assets-dir", default=str(DEFAULT_ASSETS_DIR))
    parser.add_argument("--asset-base-url", default=DEFAULT_ASSET_BASE_URL)
    parser.add_argument("--no-network", action="store_true")
    args = parser.parse_args()
    inventory_path = Path(args.inventory)
    if args.no_network:
        if not inventory_path.exists():
            parser.error("--no-network requiere un archivo --inventory existente")
    else:
        inventory_path = download_inventory(args.inventory_url, inventory_path)
    result = build_catalog(
        inventory_path,
        [Path(path) for path in args.pdf],
        Path(args.cache),
        Path(args.output),
        no_network=args.no_network,
        assets_dir=Path(args.assets_dir),
        asset_base_url=args.asset_base_url,
        spec_guide_paths=[Path(path) for path in args.spec_guide],
        colos_exact_manifest_path=(
            Path(args.colos_exact_manifest) if args.colos_exact_manifest else None
        ),
        offiho_exact_manifest_path=(
            Path(args.offiho_exact_manifest) if args.offiho_exact_manifest else None
        ),
        official_web_visual_exact_manifest_paths=(
            [Path(path) for path in args.official_web_visual_exact_manifest]
            if args.official_web_visual_exact_manifest is not None
            else DEFAULT_OFFICIAL_WEB_VISUAL_EXACT_MANIFEST_PATHS
        ),
        catalog_exact_crop_manifest_paths=(
            [Path(path) for path in args.catalog_exact_crop_manifest]
            if args.catalog_exact_crop_manifest is not None
            else DEFAULT_CATALOG_EXACT_CROP_MANIFEST_PATHS
        ),
        spec_visual_exact_manifest_paths=(
            [Path(path) for path in args.spec_visual_exact_manifest]
            if args.spec_visual_exact_manifest is not None
            else DEFAULT_SPEC_VISUAL_EXACT_MANIFEST_PATHS
        ),
        visual_rejection_manifest_path=(
            Path(args.visual_rejection_manifest)
            if args.visual_rejection_manifest
            else None
        ),
        generated_image_manifest_path=(
            Path(args.generated_image_manifest)
            if args.generated_image_manifest
            else None
        ),
    )
    print(
        json.dumps(
            {
                "total": result["total"],
                "source_row_count": result["source_row_count"],
                "duplicate_row_count": result["duplicate_row_count"],
                "unique_item_count": result["unique_item_count"],
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
