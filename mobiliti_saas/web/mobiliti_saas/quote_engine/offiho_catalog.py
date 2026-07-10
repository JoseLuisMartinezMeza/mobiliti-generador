from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import json
from urllib.parse import urlsplit

from .catalog_cart import OFFICIAL_IMAGE_HOSTS, parse_commercial_quantity


CATALOG_PATH = Path(__file__).resolve().parent / "data" / "offiho_catalog.json"
OFFIHO_CART_SOURCE_TYPE = "offiho_cart"
EXPECTED_UNIQUE_ITEM_COUNT = 1206
EXPECTED_SOURCE_ROW_COUNT = 1286
EXPECTED_DUPLICATE_ROW_COUNT = 80
MAX_CART_LINES = 200
MAX_CATALOG_DECIMAL_PLACES = 6
MAX_CATALOG_DECIMAL_TEXT_LENGTH = 64
CATALOG_DECIMAL_LIMITS = {
    "pieces_per_box": Decimal("1000000"),
    "available_quantity": Decimal("1000000000"),
    "unit_price": Decimal("1000000000"),
}
CATALOG_SIGNIFICANT_DIGIT_LIMITS = {
    "pieces_per_box": 13,
    "available_quantity": 16,
    "unit_price": 16,
}
MAX_JSON_NUMBER = Decimal("1000000000")


@dataclass(frozen=True)
class OffihoCatalogItem:
    inventory_key: str
    code: str
    name: str
    variant: str
    unit: str
    pieces_per_box: Decimal
    available_quantity: Decimal
    unit_price: Decimal
    price_source: str = "missing"
    product_url: str = ""
    image_url: str = ""
    match_status: str = "unmatched"
    source_updated_at: str = ""

    def __post_init__(self) -> None:
        for field in ("inventory_key", "code", "unit", "price_source", "match_status"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Campo obligatorio Offiho invalido: {field}")
        _validate_catalog_decimal("pieces_per_box", self.pieces_per_box, positive=True)
        _validate_catalog_decimal("available_quantity", self.available_quantity)
        _validate_catalog_decimal("unit_price", self.unit_price)
        _validate_optional_official_url("product_url", self.product_url)
        _validate_optional_official_url("image_url", self.image_url)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OffihoCatalogItem":
        if not isinstance(raw, dict):
            raise ValueError("Item Offiho invalido: se esperaba un objeto")
        return cls(
            inventory_key=_required_text(raw, "inventory_key"),
            code=_required_text(raw, "code"),
            name=str(raw.get("name", "")).strip(),
            variant=str(raw.get("variant", "")).strip(),
            unit=_required_text(raw, "unit"),
            pieces_per_box=_strict_catalog_decimal(raw, "pieces_per_box", positive=True),
            available_quantity=_strict_catalog_decimal(raw, "available_quantity"),
            unit_price=_strict_catalog_decimal(raw, "unit_price"),
            price_source=_required_text(raw, "price_source"),
            product_url=str(raw.get("product_url", "") or "").strip(),
            image_url=str(raw.get("image_url", "") or "").strip(),
            match_status=_required_text(raw, "match_status"),
            source_updated_at=str(raw.get("source_updated_at", "") or "").strip(),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "inventory_key": self.inventory_key,
            "code": self.code,
            "name": self.name,
            "variant": self.variant,
            "unit": self.unit,
            "pieces_per_box": _json_number(self.pieces_per_box),
            "available_quantity": _json_number(self.available_quantity),
            "unit_price": _json_number(self.unit_price),
            "price_source": self.price_source,
            "product_url": self.product_url,
            "image_url": self.image_url,
            "match_status": self.match_status,
            "source_updated_at": self.source_updated_at,
        }


def load_offiho_catalog(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = Path(path or CATALOG_PATH)
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Catalogo Offiho invalido: raiz no es un objeto")
    for field in ("source_hash", "generated_at"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Catalogo Offiho invalido: {field} obligatorio")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Catalogo Offiho invalido: items debe ser una lista")
    items: list[OffihoCatalogItem] = []
    for index, item in enumerate(raw_items):
        try:
            items.append(OffihoCatalogItem.from_dict(item))
        except ValueError as exc:
            raise ValueError(f"Catalogo Offiho invalido en item {index}: {exc}") from exc
    keys = [item.inventory_key for item in items]
    audit = {
        "source_row_count": raw.get("source_row_count"),
        "duplicate_row_count": raw.get("duplicate_row_count"),
        "unique_item_count": raw.get("unique_item_count", raw.get("total")),
    }
    expected_audit = {
        "source_row_count": EXPECTED_SOURCE_ROW_COUNT,
        "duplicate_row_count": EXPECTED_DUPLICATE_ROW_COUNT,
        "unique_item_count": EXPECTED_UNIQUE_ITEM_COUNT,
    }
    if audit != expected_audit:
        raise ValueError("Catalogo Offiho invalido: indice unico esperado de 1206")
    if len(items) != EXPECTED_UNIQUE_ITEM_COUNT or len(set(keys)) != EXPECTED_UNIQUE_ITEM_COUNT or not all(keys):
        raise ValueError("Catalogo Offiho invalido: claves de inventario no unicas")
    return {
        "source_hash": str(raw.get("source_hash", "")),
        "generated_at": str(raw.get("generated_at", "")),
        **audit,
        "items": items,
        "by_inventory_key": {item.inventory_key: item for item in items},
    }


def stock_status(quantity: Decimal, available: Decimal) -> str:
    if available <= 0:
        return "out_of_stock"
    if quantity > available:
        return "insufficient_stock"
    return "available"


def build_offiho_cart_payload(
    raw_items: list[dict[str, Any]],
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded_catalog = catalog or load_offiho_catalog()
    by_inventory_key: dict[str, OffihoCatalogItem] = loaded_catalog["by_inventory_key"]
    if not raw_items:
        raise ValueError("El carrito Offiho esta vacio")
    if len(raw_items) > MAX_CART_LINES:
        raise ValueError(f"El carrito Offiho excede el limite de {MAX_CART_LINES} productos")

    lines: list[dict[str, Any]] = []
    seen_inventory_keys: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto Offiho debe ser un objeto")
        inventory_key = str(raw.get("inventory_key", "")).strip()
        if not inventory_key:
            raise ValueError("Cada producto Offiho requiere inventory_key")
        if inventory_key in seen_inventory_keys:
            raise ValueError(f"Clave de inventario Offiho duplicada: {inventory_key}")
        seen_inventory_keys.add(inventory_key)
        item = by_inventory_key.get(inventory_key)
        if item is None:
            raise ValueError(f"Producto Offiho no encontrado: {inventory_key}")
        quantity = parse_commercial_quantity(raw.get("quantity", 0), item_label=inventory_key)
        status = stock_status(quantity, item.available_quantity)
        lines.append(
            {
                "inventory_key": item.inventory_key,
                "code": item.code,
                "name": item.name,
                "variant": item.variant,
                "unit": item.unit,
                "quantity": _json_number(quantity),
                "unit_price": _json_number(item.unit_price),
                "price_source": item.price_source,
                "available_quantity": _json_number(item.available_quantity),
                "stock_status": status,
                "product_url": item.product_url,
                "image_url": item.image_url,
            }
        )

    return {
        "source_type": OFFIHO_CART_SOURCE_TYPE,
        "catalog_source_hash": str(loaded_catalog.get("source_hash", "")),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": lines,
    }


def create_offiho_quotation_workbook(
    cart_payload: dict[str, Any],
    output_path: str | Path,
    *,
    image_dir: str | Path | None = None,
) -> Path:
    from .catalog_cart import create_catalog_quotation_workbook

    return create_catalog_quotation_workbook(
        cart_payload,
        output_path,
        source_type=OFFIHO_CART_SOURCE_TYPE,
        category_label="Offiho",
        image_dir=image_dir,
    )


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = str(raw.get(field, "") or "").strip()
    if not value:
        raise ValueError(f"Campo obligatorio Offiho invalido: {field}")
    return value


def _strict_catalog_decimal(raw: dict[str, Any], field: str, *, positive: bool = False) -> Decimal:
    if field not in raw:
        raise ValueError(f"Campo numerico Offiho faltante: {field}")
    text = str(raw[field]).replace(",", "").strip()
    if not text or len(text) > MAX_CATALOG_DECIMAL_TEXT_LENGTH:
        raise ValueError(f"Campo numerico Offiho invalido: {field}")
    try:
        value = Decimal(text)
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError(f"Campo numerico Offiho invalido: {field}") from None
    _validate_catalog_decimal(field, value, positive=positive)
    return value


def _validate_catalog_decimal(field: str, value: Any, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"Campo numerico Offiho invalido: {field}")
    if value < 0 or (positive and value <= 0):
        raise ValueError(f"Campo numerico Offiho fuera de rango: {field}")
    limit = CATALOG_DECIMAL_LIMITS[field]
    significant_digits, decimal_places = _decimal_shape(value)
    if (
        value > limit
        or significant_digits > CATALOG_SIGNIFICANT_DIGIT_LIMITS[field]
        or decimal_places > MAX_CATALOG_DECIMAL_PLACES
    ):
        raise ValueError(f"Campo numerico Offiho fuera de rango: {field}")


def _validate_optional_official_url(field: str, value: Any) -> None:
    clean_url = str(value or "").strip()
    if not clean_url:
        return
    try:
        parsed = urlsplit(clean_url)
        port = parsed.port
    except ValueError:
        raise ValueError(f"URL Offiho invalida: {field}") from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or host not in OFFICIAL_IMAGE_HOSTS[OFFIHO_CART_SOURCE_TYPE]
    ):
        raise ValueError(f"URL Offiho no oficial: {field}")


def _decimal_shape(value: Decimal) -> tuple[int, int]:
    _, digits_tuple, exponent = value.as_tuple()
    digits = list(digits_tuple)
    if not any(digits):
        return 1, 0
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return len(digits), max(-exponent, 0)


def _json_number(value: Decimal) -> int | float:
    if not isinstance(value, Decimal) or not value.is_finite() or abs(value) > MAX_JSON_NUMBER:
        raise ValueError("Numero JSON fuera de rango")
    significant_digits, decimal_places = _decimal_shape(value)
    if significant_digits > 16 or decimal_places > MAX_CATALOG_DECIMAL_PLACES:
        raise ValueError("Numero JSON fuera de rango")
    if value == value.to_integral():
        return int(value)
    return float(value)
