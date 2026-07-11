from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import re
import shutil
import unicodedata
import urllib.parse
import urllib.request

import xlrd
import pdfplumber
from PIL import Image
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY_URL = "https://www.offiho.com/existencias.xls"
DEFAULT_INVENTORY_PATH = PROJECT_ROOT / ".cache" / "offiho-existencias.xls"
DEFAULT_CACHE_PATH = PROJECT_ROOT / ".cache" / "offiho-products.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "mobiliti_saas" / "quote_engine" / "data" / "offiho_catalog.json"
DEFAULT_ASSETS_DIR = PROJECT_ROOT / "mobiliti_saas" / "web" / "public" / "catalog-assets" / "offiho"
DEFAULT_ASSET_BASE_URL = "https://web-lemon-one-45.vercel.app/catalog-assets/offiho"
OFFICIAL_HOSTS = frozenset({"offiho.com", "www.offiho.com", "offihoblack.com", "www.offihoblack.com"})
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
SITE_SEEDS = (
    "https://www.offiho.com/",
    *(f"https://www.offiho.com/{section}/" for section in OFFIHO_CATALOG_SECTIONS),
    "https://www.offiho.com/econosillas/",
    "https://www.offihoblack.com/",
    "https://www.offiho.com/econosillas/penguin-modelo-OHV-7067F",
)
USER_AGENT = "Mobiliti Offiho Catalog Builder/1.0"
CACHE_VERSION = 19
CACHE_TTL_SECONDS = 24 * 60 * 60
LEGACY_CACHE_TIMESTAMP = "1970-01-01T00:00:00+00:00"
SOURCE_MANIFEST_VERSION = 4
MAX_INVENTORY_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_VALIDATION_TIMEOUT = 10
MAX_DISCOVERED_PAGES = 800
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
NON_QUANTITATIVE_STOCK_STATUSES = frozenset({"CONSULTAR EXISTENCIAS", "SOBRE PEDIDO"})
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
VARIANT_CANONICAL_WORDS = {
    "BLANCA": "BLANCO",
    "NEGRA": "NEGRO",
    "ROJA": "ROJO",
}


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
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _inventory_decimal(
    value: Any,
    *,
    row_number: int,
    column_name: str,
    field: str,
    required: bool,
) -> Decimal | None:
    if normalize_space(value) == "":
        if not required:
            return None
        raise RuntimeError(
            f"Fila {row_number}, columna {column_name}, campo {field}: valor numerico requerido"
        )
    parsed = decimal_value(value)
    if parsed is None:
        raise RuntimeError(
            f"Fila {row_number}, columna {column_name}, campo {field}: valor numerico invalido {value!r}"
        )
    return parsed


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
    items: list[dict[str, Any]] = []
    by_inventory_key: dict[str, dict[str, Any]] = {}
    source_row_count = 0
    duplicate_row_count = 0
    excluded_stock_status_count = 0
    excluded_header_row_count = 0
    defaulted_pieces_status_count = 0
    excluded_blank_stock_count = 0
    for row_number, raw_key, raw_stock, raw_pieces, raw_price in _inventory_source_rows(path):
        inventory_key = normalize_space(raw_key).upper()
        if not inventory_key:
            continue
        if _normalize_header(raw_key) == "codigo" and _normalize_header(raw_stock) == "existencia":
            excluded_header_row_count += 1
            continue
        if normalize_space(raw_stock) == "":
            excluded_blank_stock_count += 1
            continue
        if normalize_space(raw_stock).upper() in NON_QUANTITATIVE_STOCK_STATUSES:
            excluded_stock_status_count += 1
            continue
        stock = _inventory_decimal(
            raw_stock,
            row_number=row_number,
            column_name="C",
            field="Existencia",
            required=True,
        )
        source_row_count += 1
        identity = extract_offiho_identity(inventory_key)
        if normalize_space(raw_pieces).upper() in NON_QUANTITATIVE_STOCK_STATUSES:
            defaulted_pieces_status_count += 1
            pieces_per_box = Decimal("1")
        else:
            pieces_per_box = _inventory_decimal(
                raw_pieces,
                row_number=row_number,
                column_name="D",
                field="Piezas por Caja",
                required=False,
            ) or Decimal("1")
        unit_price = _inventory_decimal(
            raw_price,
            row_number=row_number,
            column_name="E",
            field="Precio Lista 1",
            required=False,
        )
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
        "excluded_stock_status_count": excluded_stock_status_count,
        "excluded_header_row_count": excluded_header_row_count,
        "defaulted_pieces_status_count": defaulted_pieces_status_count,
        "excluded_blank_stock_count": excluded_blank_stock_count,
    }


def _inventory_source_rows(path: Path) -> list[tuple[int, Any, Any, Any, Any]]:
    try:
        workbook = xlrd.open_workbook(path)
    except xlrd.biffh.XLRDError:
        payload = path.read_bytes()
        if not payload.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower().startswith((b"<!doctype html", b"<html")):
            raise
        return _html_inventory_rows(payload)

    try:
        sheet = workbook.sheet_by_name("Publicaci\u00f3n")
    except xlrd.biffh.XLRDError:
        raise RuntimeError("No se encontro la hoja Publicaci\u00f3n") from None
    return [
        (
            row + 1,
            sheet.cell_value(row, 1),
            sheet.cell_value(row, 2),
            sheet.cell_value(row, 3),
            sheet.cell_value(row, 4),
        )
        for row in range(5, sheet.nrows)
    ]


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
    if canonical == "AZUL MARINO":
        keys.extend(["MARINO", "AZUL"])
    return list(dict.fromkeys(keys))


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
        if not any(_official_code_matches(identity, code) for code in codes) or not is_official_url(url):
            continue
        if image_url == url:
            image_url = ""
        code_matches.append(
            {
                "url": url,
                "image_url": image_url,
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
        if not matched_name or not is_official_url(url):
            continue
        name_matches.append(
            {
                "url": url,
                "image_url": _trusted_cached_image(candidate)["image_url"],
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


def _official_code_matches(identity: OffihoIdentity, candidate_code: str) -> bool:
    candidate = str(candidate_code or "").upper()
    if not candidate:
        return False
    variant_tokens = {
        re.sub(r"[^A-Z0-9]", "", token)
        for token in str(identity.variant or "").upper().split()
    }
    compound_variant = re.sub(r"[^A-Z0-9]", "", str(identity.variant or "").upper())
    if compound_variant:
        variant_tokens.add(compound_variant)
    for target in _identity_code_targets(identity):
        if candidate == target:
            return True
        if not candidate.startswith(target):
            continue
        suffix = re.sub(r"[^A-Z0-9]", "", candidate[len(target) :])
        if suffix and suffix in variant_tokens:
            return True
    return False


def _select_official_product(
    identity: OffihoIdentity,
    matches: Sequence[dict[str, Any]],
    match_status: str,
) -> dict[str, Any]:
    def url_rank(product: dict[str, Any]) -> tuple[int, int, int, int, str]:
        url = product["url"]
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        compact_path = re.sub(r"[^A-Z0-9]", "", path.upper())
        compact_code = re.sub(r"[^A-Z0-9]", "", identity.code.upper())
        leaf_name = _product_name_key(path.rstrip("/").rsplit("/", 1)[-1], codes=[identity.code])
        name_key = product.get("matched_name") or _product_name_key(identity.name)
        if match_status == "official_code_match":
            variant_specific = int(
                bool(identity.variant)
                and any(
                    key and compact_code + key in compact_path
                    for key in map(_compact_variant_value, _variant_lookup_keys(identity.variant))
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
        return variant_specific, primary, secondary, depth, url

    url_product = max(matches, key=url_rank)
    variant_image_products = [
        (product, _variant_image_for_identity(product, identity))
        for product in matches
    ]
    variant_image_products = [pair for pair in variant_image_products if pair[1]]
    has_variant_catalog = any(
        _product_targets_identity(product, identity)
        and bool(_trusted_cached_variant_images(product))
        for product in matches
    )
    if variant_image_products:
        image_url = max(variant_image_products, key=lambda pair: url_rank(pair[0]))[1]
    else:
        image_products = [
            product
            for product in matches
            if product.get("image_url")
            and not _image_conflicts_with_identity_variant(str(product["image_url"]), identity)
            and (
                product.get("curated_name_match") is True
                or _product_targets_identity(product, identity)
                or _image_targets_identity(str(product["image_url"]), identity)
            )
        ]
        image_url = str(max(image_products, key=url_rank)["image_url"]) if image_products else ""
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


def _variant_image_for_identity(product: dict[str, Any], identity: OffihoIdentity) -> str:
    if not _product_targets_identity(product, identity):
        return ""
    images = product.get("variant_images", {})
    if not isinstance(images, dict):
        return ""
    for key in _variant_lookup_keys(identity.variant):
        metadata = images.get(key)
        if isinstance(metadata, dict):
            image_url = _trusted_cached_image(metadata)["image_url"]
            if image_url and _image_targets_identity(image_url, identity):
                return image_url
    return ""


def _product_targets_identity(product: dict[str, Any], identity: OffihoIdentity) -> bool:
    return _image_targets_identity(str(product.get("url", "")), identity)


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


def _image_conflicts_with_identity_variant(value: str, identity: OffihoIdentity) -> bool:
    requested = set(_variant_lookup_keys(identity.variant))
    if not requested:
        return False
    labeled = set(_variant_keys_from_image_reference(value, _identity_code_targets(identity)))
    return bool(labeled and requested.isdisjoint(labeled))


def _identity_code_targets(identity: OffihoIdentity) -> tuple[str, ...]:
    code = str(identity.code or "").upper()
    alias = OFFICIAL_CODE_ALIASES.get(code, "")
    return tuple(value for value in (code, alias) if value)


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
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "https" and parsed.hostname in OFFICIAL_HOSTS and not parsed.username and not parsed.password


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
    for image_url in sorted(set(images.values())):
        metadata = _verify_official_image(image_url)
        if metadata["image_url"]:
            verified_by_url[image_url] = metadata
    return {
        key: verified_by_url[image_url]
        for key, image_url in sorted(images.items())
        if image_url in verified_by_url
    }


def _verify_code_images(images: dict[str, str]) -> dict[str, dict[str, Any]]:
    verified_by_url: dict[str, dict[str, Any]] = {}
    for image_url in sorted(set(images.values())):
        metadata = _verify_official_image(image_url)
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
        self.collection_images: list[dict[str, str]] = []
        self._name_tag = ""
        self._name_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag in {"img", "source"}:
            for name in ("src", "data-src", "data-original", "data-image", "data-zoom-image"):
                if values.get(name):
                    self.images.append(values[name])
            if values.get("srcset"):
                self.images.extend(_srcset_urls(values["srcset"]))
            classes = {value.casefold() for value in values.get("class", "").split()}
            cloudzoom = values.get("data-cloudzoom", "")
            if "colecciones" in classes and cloudzoom:
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
        if tag == self._name_tag:
            value = normalize_space(" ".join(self._name_parts))
            if value:
                self.names.append(value)
            self._name_tag = ""
            self._name_parts = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._name_tag:
            self._name_parts.append(data)


def _srcset_urls(value: str) -> list[str]:
    return [candidate.strip().split(" ", 1)[0] for candidate in value.split(",") if candidate.strip()]


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
        if compact_code and compact.startswith(compact_code):
            compact = compact[len(compact_code) :]
            break

    words_by_compact = sorted(
        {(_compact_variant_value(word), word) for word in VARIANT_WORDS if _compact_variant_value(word)},
        key=lambda item: (-len(item[0]), item[0]),
    )
    words: list[str] = []
    remaining = compact
    while remaining:
        matched = next((pair for pair in words_by_compact if remaining.startswith(pair[0])), None)
        if matched is None:
            break
        words.append(matched[1])
        remaining = remaining[len(matched[0]) :]
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
        ]
        for key in keys:
            images.setdefault(key, resolved)
    return dict(sorted(images.items()))


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


def build_site_product_index(
    cache: dict[str, Any],
    *,
    no_network: bool = False,
    now: datetime | None = None,
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

    for _ in range(2):
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

    index: dict[str, dict[str, Any]] = {}
    for record in records:
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
    cache["site_index"] = index
    cache["site_index_created_at"] = current_time.isoformat()
    cache["site_index_expires_at"] = (current_time + timedelta(seconds=CACHE_TTL_SECONDS)).isoformat()
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
    page_codes = sorted(
        {
            code.upper()
            for code in CODE_RE.findall(
                urllib.parse.unquote(urllib.parse.urlsplit(page_url).path)
            )
        }
    )
    product_codes = page_codes or codes
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
    variant_images = _verify_variant_images(
        _extract_variant_image_urls(page_url, parser, codes=product_codes)
    )
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


def build_catalog(
    inventory_path: Path,
    pdf_paths: Sequence[Path],
    cache_path: Path,
    output_path: Path,
    *,
    no_network: bool = False,
    assets_dir: Path = DEFAULT_ASSETS_DIR,
    asset_base_url: str = DEFAULT_ASSET_BASE_URL,
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
    source_manifest = {
        "manifest_version": SOURCE_MANIFEST_VERSION,
        "inventory_sha256": inventory_sha256,
        "pdf_sha256": sorted(source["sha256"] for source in pdf_sources),
        "site_index_sha256": site_index_sha256,
        "pdf_index_sha256": pdf_index_sha256,
        "brochure_index_sha256": brochure_index_sha256,
        "site_cache_version": CACHE_VERSION,
        "generated_at": generated_at,
        "generated_at_source": generated_at_source,
    }
    site_candidates = []
    for key, product in site_index.items():
        codes = list(product.get("codes", []))
        names = list(product.get("names", []))
        if key.startswith("name:"):
            names.append(key.removeprefix("name:"))
        else:
            codes.append(key)
        site_candidates.append(
            {
                **product,
                "codes": sorted({str(code).upper() for code in codes if str(code).strip()}),
                "names": sorted({_product_name_key(name) for name in names if _product_name_key(name)}),
            }
        )
    for item in items:
        identity = OffihoIdentity(item["code"], item["name"], item["variant"])
        if item["price_source"] == "missing":
            amount = pdf_prices.get(price_key(identity.code, identity.variant))
            if amount is not None:
                item["unit_price"] = json_number(amount)
                item["price_source"] = "pdf_exact"
        product = match_official_product(identity, site_candidates)
        pdf_product = pdf_products.get(item["inventory_key"], {})
        brochure_product = match_official_brochure_product(item, assets_dir, asset_base_url)
        support_product = pdf_product if pdf_product.get("image_url") else brochure_product
        if item["price_source"] == "missing" and pdf_product.get("unit_price") is not None:
            item["unit_price"] = json_number(pdf_product["unit_price"])
            item["price_source"] = "pdf_catalog"
        item["product_url"] = (
            product["url"]
            or str(pdf_product.get("product_url", ""))
            or str(brochure_product.get("product_url", ""))
        )
        item["image_url"] = (
            product["image_url"]
            if product.get("has_variant_catalog")
            else product["image_url"] or str(support_product.get("image_url", ""))
        )
        item["description"] = product["description"] or str(pdf_product.get("description", "")) or _inventory_description(item)
        item["description_source"] = (
            "official_site"
            if product["description"]
            else "pdf_catalog"
            if pdf_product.get("description")
            else "inventory_label"
        )
        item["match_status"] = (
            product["match_status"]
            if product["url"] or product["image_url"]
            else str(support_product.get("match_status", "unmatched"))
        )
        item["source_updated_at"] = (
            product["source_updated_at"]
            or str(support_product.get("source_updated_at", ""))
        )

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
        "official_images": sum(bool(item["image_url"]) for item in items),
        "described_items": sum(bool(item.get("description")) for item in items),
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


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
    if not is_official_url(url):
        raise ValueError("La URL de inventario debe ser HTTPS de un host oficial Offiho")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.ms-excel"})
    with _open_official(request, timeout=30) as response:
        if not is_official_url(response.geturl()):
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
