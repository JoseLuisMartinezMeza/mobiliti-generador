from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from .offiho_catalog import OffihoCatalogItem, load_offiho_catalog_data


OFFIHO_INVENTORY_URL = "https://www.offiho.com/existencias.xls"
OFFICIAL_HOSTS = frozenset({"offiho.com", "www.offiho.com"})
USER_AGENT = "Mobiliti Offiho Inventory Sync/1.0"
MAX_INVENTORY_BYTES = 10 * 1024 * 1024
MIN_GUARDED_BASE_ITEMS = 8
MIN_POPULATION_RATIO = Decimal("0.60")
MAX_POPULATION_RATIO = Decimal("1.50")
MIN_IDENTITY_COVERAGE = Decimal("0.70")
OLE_COMPOUND_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
NON_QUANTITATIVE_STOCK_STATUSES = frozenset({"CONSULTAR EXISTENCIAS", "SOBRE PEDIDO"})
CODE_RE = re.compile(r"\b[A-Z]{2,}(?:-\d+[A-Z0-9]*)+", re.ASCII | re.IGNORECASE)
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
        "MAMEY",
        "MARINO",
        "MARRON",
        "MATE",
        "MEDIO",
        "MORADO",
        "MOSTAZA",
        "NARANJA",
        "NARANANJA",
        "NEGRA",
        "NEGRO",
        "OBSCURO",
        "OCEANO",
        "OCENAO",
        "OLIVO",
        "ORO",
        "OXFORD",
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
ENRICHMENT_FIELDS = (
    "product_url",
    "image_url",
    "description",
    "description_source",
    "match_status",
    "source_updated_at",
    "image_kind",
    "image_label",
    "image_references",
    "generation_prompt",
    "generation_model",
    "image_source_sha256",
)


@dataclass(frozen=True)
class OffihoInventoryDownload:
    path: Path
    sha256: str
    size_bytes: int
    last_modified: str = ""
    etag: str = ""
    content_type: str = "application/vnd.ms-excel"
    source_url: str = OFFIHO_INVENTORY_URL


@dataclass(frozen=True)
class _OffihoIdentity:
    code: str
    name: str
    variant: str


def build_refreshed_offiho_catalog(
    base_catalog: dict[str, Any],
    inventory_items: list[dict[str, Any]],
    audit: dict[str, Any],
    *,
    inventory_sha256: str,
    inventory_size_bytes: int,
    synchronized_at: str,
    inventory_last_modified: str = "",
) -> dict[str, Any]:
    base_items = _validated_base_items(base_catalog)
    _validate_inventory_input(inventory_items, audit)
    _validate_source_metadata(inventory_sha256, inventory_size_bytes, synchronized_at)
    if len(base_items) >= MIN_GUARDED_BASE_ITEMS:
        population_ratio = Decimal(len(inventory_items)) / Decimal(len(base_items))
        if not MIN_POPULATION_RATIO <= population_ratio <= MAX_POPULATION_RATIO:
            raise ValueError("Actualizacion Offiho rechazada por cardinalidad anomala")

    base_by_key = {item["inventory_key"]: item for item in base_items}
    base_by_canonical = _unique_canonical_rows(base_items)
    incoming_canonical_counts: dict[str, int] = {}
    for item in inventory_items:
        key = _canonical_inventory_key(item["inventory_key"])
        incoming_canonical_counts[key] = incoming_canonical_counts.get(key, 0) + 1

    merged_items: list[dict[str, Any]] = []
    matched_keys: set[str] = set()
    stock_changed = 0
    positive_to_zero = 0
    zero_to_positive = 0
    for raw_item in inventory_items:
        item = _inventory_item(raw_item)
        base_item = base_by_key.get(item["inventory_key"])
        if base_item is None:
            canonical_key = _canonical_inventory_key(item["inventory_key"])
            if incoming_canonical_counts.get(canonical_key) == 1:
                base_item = base_by_canonical.get(canonical_key)
        if base_item is not None:
            matched_keys.add(base_item["inventory_key"])
            previous_stock = Decimal(str(base_item["available_quantity"]))
            current_stock = Decimal(str(item["available_quantity"]))
            if previous_stock != current_stock:
                stock_changed += 1
            if previous_stock > 0 and current_stock == 0:
                positive_to_zero += 1
            if previous_stock == 0 and current_stock > 0:
                zero_to_positive += 1
            for field in ENRICHMENT_FIELDS:
                item[field] = base_item[field]
            if item["price_source"] == "missing" and str(base_item["price_source"]).startswith("pdf_"):
                item["unit_price"] = base_item["unit_price"]
                item["price_source"] = base_item["price_source"]
        merged_items.append(item)

    if (
        len(base_items) >= MIN_GUARDED_BASE_ITEMS
        and Decimal(len(matched_keys)) / Decimal(len(base_items)) < MIN_IDENTITY_COVERAGE
    ):
        raise ValueError("Actualizacion Offiho rechazada por cobertura de claves anomala")

    enrichment_source_hash = _canonical_hash(
        [
            {
                "inventory_key": item["inventory_key"],
                **{field: item[field] for field in ENRICHMENT_FIELDS},
                "pdf_price": item["unit_price"] if str(item["price_source"]).startswith("pdf_") else None,
                "price_source": item["price_source"] if str(item["price_source"]).startswith("pdf_") else None,
            }
            for item in merged_items
        ]
    )
    source_hash = _canonical_hash(
        {
            "inventory_sha256": inventory_sha256.lower(),
            "enrichment_source_hash": enrichment_source_hash,
            "items": merged_items,
        }
    )
    sources = deepcopy(base_catalog.get("sources")) if isinstance(base_catalog.get("sources"), dict) else {}
    sources["inventory"] = {
        "name": "offiho-existencias.xls",
        "sha256": inventory_sha256.lower(),
        "size_bytes": inventory_size_bytes,
    }
    result: dict[str, Any] = {
        "source_hash": source_hash,
        "generated_at": synchronized_at,
        "catalog_built_at": synchronized_at,
        "inventory_fetched_at": synchronized_at,
        "inventory_last_modified": str(inventory_last_modified or "").strip(),
        "stock_snapshot_hash": inventory_sha256.lower(),
        "enrichment_source_hash": enrichment_source_hash,
        "sources": sources,
        "total": len(merged_items),
        "source_row_count": audit["source_row_count"],
        "duplicate_row_count": audit["duplicate_row_count"],
        "unique_item_count": len(merged_items),
        "sync_audit": {
            "matched_item_count": len(matched_keys),
            "added_item_count": len(merged_items) - len(matched_keys),
            "removed_item_count": len(base_items) - len(matched_keys),
            "stock_changed_count": stock_changed,
            "positive_to_zero_count": positive_to_zero,
            "zero_to_positive_count": zero_to_positive,
        },
        "out_of_stock": sum(Decimal(str(item["available_quantity"])) == 0 for item in merged_items),
        "inventory_prices": sum(item["price_source"] == "inventory" for item in merged_items),
        "pdf_prices": sum(str(item["price_source"]).startswith("pdf_") for item in merged_items),
        "official_images": sum(bool(item["image_url"]) for item in merged_items),
        "described_items": sum(bool(item["description"]) for item in merged_items),
        "items": merged_items,
    }
    for field in (
        "excluded_stock_status_count",
        "excluded_header_row_count",
        "defaulted_pieces_status_count",
        "excluded_blank_stock_count",
    ):
        result[field] = _optional_audit_count(audit, field)
    workbook_generated_at = str(audit.get("workbook_generated_at") or "").strip()
    if workbook_generated_at:
        result["workbook_generated_at"] = workbook_generated_at

    load_offiho_catalog_data(result)
    return result


def refresh_offiho_catalog_from_file(
    base_catalog: dict[str, Any],
    path: str | Path,
    inventory_sha256: str,
    inventory_size_bytes: int,
    synchronized_at: str,
    inventory_last_modified: str = "",
) -> dict[str, Any]:
    items, audit = parse_offiho_inventory(path)
    return build_refreshed_offiho_catalog(
        base_catalog,
        items,
        audit,
        inventory_sha256=inventory_sha256,
        inventory_size_bytes=inventory_size_bytes,
        synchronized_at=synchronized_at,
        inventory_last_modified=inventory_last_modified,
    )


def parse_offiho_inventory(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_path = Path(path)
    workbook_generated_at = ""
    payload = source_path.read_bytes() if source_path.exists() else b""
    if payload and _is_html_payload(payload):
        source_rows = _html_inventory_rows(payload)
    else:
        source_rows, workbook_generated_at = _xls_inventory_rows(source_path)

    items: list[dict[str, Any]] = []
    by_inventory_key: dict[str, dict[str, Any]] = {}
    audit: dict[str, Any] = {
        "source_row_count": 0,
        "duplicate_row_count": 0,
        "unique_item_count": 0,
        "excluded_stock_status_count": 0,
        "excluded_header_row_count": 0,
        "defaulted_pieces_status_count": 0,
        "excluded_blank_stock_count": 0,
    }
    for row_number, raw_key, raw_stock, raw_pieces, raw_price in source_rows:
        inventory_key = _normalize_space(raw_key).upper()
        if not inventory_key:
            continue
        if _normalize_header(raw_key) == "codigo" and _normalize_header(raw_stock) == "existencia":
            audit["excluded_header_row_count"] += 1
            continue
        stock_text = _normalize_space(raw_stock)
        if not stock_text:
            audit["excluded_blank_stock_count"] += 1
            continue
        if stock_text.upper() in NON_QUANTITATIVE_STOCK_STATUSES:
            audit["excluded_stock_status_count"] += 1
            continue
        stock = _inventory_decimal(
            raw_stock,
            row_number=row_number,
            column_name="C",
            field="Existencia",
            required=True,
        )
        audit["source_row_count"] += 1
        identity = _extract_identity(inventory_key)
        if _normalize_space(raw_pieces).upper() in NON_QUANTITATIVE_STOCK_STATUSES:
            audit["defaulted_pieces_status_count"] += 1
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
            "pieces_per_box": _json_number(pieces_per_box),
            "available_quantity": _json_number(stock),
            "unit_price": _json_number(unit_price or Decimal("0")),
            "price_source": "inventory" if unit_price is not None else "missing",
        }
        existing = by_inventory_key.get(inventory_key)
        if existing is not None:
            same_commercial_data = all(
                existing[field] == item[field]
                for field in (
                    "inventory_key",
                    "code",
                    "name",
                    "variant",
                    "unit",
                    "pieces_per_box",
                    "unit_price",
                    "price_source",
                )
            )
            if same_commercial_data:
                audit["duplicate_row_count"] += 1
                existing["available_quantity"] = max(
                    existing["available_quantity"],
                    item["available_quantity"],
                )
                continue
            raise ValueError(f"La clave {inventory_key} aparece con datos distintos")
        by_inventory_key[inventory_key] = item
        items.append(item)
    audit["unique_item_count"] = len(items)
    if workbook_generated_at:
        audit["workbook_generated_at"] = workbook_generated_at
    _validate_inventory_input(items, audit)
    return items, audit


def download_offiho_inventory(
    path: str | Path,
    *,
    url: str = OFFIHO_INVENTORY_URL,
    timeout_seconds: int = 30,
    max_bytes: int = MAX_INVENTORY_BYTES,
) -> OffihoInventoryDownload:
    if not _is_official_url(url):
        raise ValueError("La URL de inventario debe ser HTTPS de un host oficial Offiho")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.ms-excel"},
    )
    with _OFFICIAL_OPENER.open(request, timeout=timeout_seconds) as response:
        final_url = response.geturl()
        if not _is_official_url(final_url):
            raise ValueError("La descarga de inventario redirigio fuera de los hosts oficiales")
        content_type = _response_content_type(response.headers)
        if content_type not in {"application/vnd.ms-excel", "application/octet-stream", "text/html"}:
            raise ValueError("La URL de inventario no devolvio un archivo XLS")
        payload = response.read(max_bytes + 1)
        last_modified = _http_timestamp(response.headers.get("Last-Modified", ""))
        etag = str(response.headers.get("ETag", "") or "").strip()
    if not payload:
        raise ValueError("El inventario Offiho esta vacio")
    if len(payload) > max_bytes:
        raise ValueError("El inventario excede el limite permitido")
    is_html = _is_html_payload(payload)
    if content_type == "text/html" or is_html:
        if not is_html or not _html_inventory_rows(payload):
            raise ValueError("La respuesta HTML no contiene una tabla valida de inventario")
    elif not payload.startswith(OLE_COMPOUND_MAGIC):
        raise ValueError("La respuesta no contiene un libro XLS valido")

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return OffihoInventoryDownload(
        path=output_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        last_modified=last_modified,
        etag=etag,
        content_type=content_type,
        source_url=final_url,
    )


def _validated_base_items(base_catalog: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = base_catalog.get("items") if isinstance(base_catalog, dict) else None
    if isinstance(raw_items, list) and raw_items and all(isinstance(item, OffihoCatalogItem) for item in raw_items):
        return [item.to_public_dict() for item in raw_items]
    loaded = load_offiho_catalog_data(base_catalog)
    return [item.to_public_dict() for item in loaded["items"]]


def _inventory_item(raw: dict[str, Any]) -> dict[str, Any]:
    item = {
        "inventory_key": _required_text(raw, "inventory_key"),
        "code": _required_text(raw, "code"),
        "name": str(raw.get("name") or "").strip(),
        "variant": str(raw.get("variant") or "").strip(),
        "unit": _required_text(raw, "unit"),
        "pieces_per_box": raw.get("pieces_per_box"),
        "available_quantity": raw.get("available_quantity"),
        "unit_price": raw.get("unit_price"),
        "price_source": _required_text(raw, "price_source"),
        "product_url": "",
        "image_url": "",
        "description": "",
        "description_source": "inventory_label",
        "match_status": "unmatched",
        "source_updated_at": "",
        "image_kind": "placeholder",
        "image_label": "",
        "image_references": [],
        "generation_prompt": "",
        "generation_model": "",
        "image_source_sha256": "",
    }
    item["description"] = _inventory_description(item)
    OffihoCatalogItem.from_dict(item)
    return item


def _validate_inventory_input(items: list[dict[str, Any]], audit: dict[str, Any]) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError("Inventario Offiho invalido: no contiene productos")
    source_count = _required_audit_count(audit, "source_row_count")
    duplicate_count = _required_audit_count(audit, "duplicate_row_count")
    unique_count = _required_audit_count(audit, "unique_item_count")
    if unique_count != len(items) or source_count != unique_count + duplicate_count:
        raise ValueError("Inventario Offiho invalido: conteos de auditoria inconsistentes")
    keys: list[str] = []
    for raw in items:
        item = _inventory_item(raw)
        keys.append(item["inventory_key"])
    if len(set(keys)) != len(keys):
        raise ValueError("Inventario Offiho invalido: claves no unicas")


def _validate_source_metadata(sha256: str, size_bytes: int, synchronized_at: str) -> None:
    if re.fullmatch(r"[0-9a-fA-F]{64}", str(sha256 or "")) is None:
        raise ValueError("Hash SHA-256 de inventario invalido")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
        raise ValueError("Tamano de inventario invalido")
    text = str(synchronized_at or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Timestamp de sincronizacion invalido") from None
    if parsed.tzinfo is None:
        raise ValueError("Timestamp de sincronizacion invalido")


def _unique_canonical_rows(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(_canonical_inventory_key(item["inventory_key"]), []).append(item)
    return {key: rows[0] for key, rows in grouped.items() if len(rows) == 1}


def _canonical_inventory_key(value: Any) -> str:
    normalized = _normalize_space(value).upper()
    return re.sub(r"\*+\s*$", "", normalized).rstrip()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_audit_count(audit: dict[str, Any], field: str) -> int:
    value = audit.get(field) if isinstance(audit, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Inventario Offiho invalido: conteo {field}")
    return value


def _optional_audit_count(audit: dict[str, Any], field: str) -> int:
    if field not in audit:
        return 0
    return _required_audit_count(audit, field)


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = str(raw.get(field) or "").strip()
    if not value:
        raise ValueError(f"Campo obligatorio Offiho invalido: {field}")
    return value


def _inventory_description(item: dict[str, Any]) -> str:
    name = _normalize_space(item.get("name")) or _normalize_space(item.get("code"))
    variant = _normalize_space(item.get("variant"))
    unit = _normalize_space(item.get("unit"))
    parts = [f"Producto Offiho {name}." if name else "Producto Offiho."]
    if variant:
        parts.append(f"Variante: {variant}.")
    if unit:
        parts.append(f"Unidad: {unit}.")
    return _normalize_space(" ".join(parts))


def _normalize_space(value: Any) -> str:
    return " ".join(str("" if value is None else value).split())


def _variant_word_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", _normalize_space(value).upper())
    return re.sub(
        r"[^A-Z0-9]",
        "",
        "".join(char for char in normalized if not unicodedata.combining(char)),
    )


_VARIANT_WORD_KEYS = frozenset(_variant_word_key(word) for word in VARIANT_WORDS)


def _is_variant_token(value: str) -> bool:
    parts = [part for part in re.split(r"/+", value) if part]
    return bool(parts) and all(_variant_word_key(part) in _VARIANT_WORD_KEYS for part in parts)


def _normalize_variant(value: str) -> str:
    text = _normalize_space(value).upper()
    return re.sub(r"\s*/\s*", " ", text)


def _decimal_value(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    text = _normalize_space(value).replace("$", "").replace(",", "")
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
    if _normalize_space(value) == "":
        if not required:
            return None
        raise ValueError(f"Fila {row_number}, columna {column_name}, campo {field}: valor numerico requerido")
    parsed = _decimal_value(value)
    if parsed is None:
        raise ValueError(
            f"Fila {row_number}, columna {column_name}, campo {field}: valor numerico invalido {value!r}"
        )
    return parsed


def _json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def _extract_identity(inventory_key: str) -> _OffihoIdentity:
    normalized = _normalize_space(inventory_key).upper()
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
    variant = _normalize_variant(" ".join(variant_tokens))
    after_tokens = [token for token in after_tokens if token not in variant_tokens]
    name = _normalize_space(
        " ".join(
            part
            for part in (before, " ".join(configuration_tokens), " ".join(after_tokens))
            if part
        )
    )
    return _OffihoIdentity(code=code, name=name, variant=variant)


def _xls_inventory_rows(path: Path) -> tuple[list[tuple[int, Any, Any, Any, Any]], str]:
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("La sincronizacion Offiho requiere xlrd") from None
    try:
        workbook = xlrd.open_workbook(path)
        sheet = workbook.sheet_by_name("Publicaci\u00f3n")
    except xlrd.biffh.XLRDError as exc:
        raise ValueError("No se encontro un libro Offiho con hoja Publicacion valida") from exc
    try:
        rows = [
            (
                row + 1,
                sheet.cell_value(row, 1),
                sheet.cell_value(row, 2),
                sheet.cell_value(row, 3),
                sheet.cell_value(row, 4),
            )
            for row in range(5, sheet.nrows)
        ]
        workbook_generated_at = ""
        if sheet.nrows > 3 and getattr(sheet, "ncols", 2) > 1:
            raw_timestamp = sheet.cell_value(3, 1)
            if isinstance(raw_timestamp, (int, float)) and raw_timestamp > 0:
                workbook_generated_at = xlrd.xldate_as_datetime(
                    raw_timestamp,
                    getattr(workbook, "datemode", 0),
                ).isoformat()
    finally:
        release_resources = getattr(workbook, "release_resources", None)
        if callable(release_resources):
            release_resources()
    return rows, workbook_generated_at


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
            self.current_row.append(_normalize_space("".join(self.current_cell)))
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
    headers = {
        "codigo": "inventory_key",
        "existencia": "stock",
        "piezas por caja": "pieces_per_box",
        "precio lista 1": "unit_price",
    }
    for header_index, row in enumerate(parser.rows):
        normalized = [_normalize_header(cell) for cell in row]
        if not all(header in normalized for header in headers):
            continue
        columns = {field: normalized.index(header) for header, field in headers.items()}
        rows: list[tuple[int, Any, Any, Any, Any]] = []
        for row_index, values in enumerate(parser.rows[header_index + 1 :], start=header_index + 2):
            def get(field: str) -> str:
                return values[columns[field]] if columns[field] < len(values) else ""

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
    raise ValueError("No se encontraron encabezados de inventario en el XLS HTML")


def _normalize_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _normalize_space(value)).encode("ascii", "ignore").decode("ascii")
    return text.casefold()


def _is_html_payload(payload: bytes) -> bool:
    stripped = payload.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return stripped.startswith((b"<!doctype html", b"<html"))


def _is_official_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower().rstrip(".") in OFFICIAL_HOSTS
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
    )


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
        if not _is_official_url(target):
            raise ValueError("La redireccion no apunta a un host oficial HTTPS de Offiho")
        return super().redirect_request(req, fp, code, msg, headers, target)


_OFFICIAL_OPENER = urllib.request.build_opener(_OfficialRedirectHandler())


def _response_content_type(headers: Any) -> str:
    if hasattr(headers, "get_content_type"):
        return str(headers.get_content_type()).lower()
    return str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()


def _http_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
