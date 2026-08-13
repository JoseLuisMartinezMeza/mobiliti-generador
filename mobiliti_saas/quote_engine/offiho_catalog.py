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
MAX_DESCRIPTION_LENGTH = 2000
MAX_IMAGE_METADATA_LENGTH = 4000
OFFIHO_IMAGE_KINDS = frozenset({"official", "generated_reference", "placeholder"})


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
    description: str = ""
    description_source: str = "inventory_label"
    match_status: str = "unmatched"
    source_updated_at: str = ""
    image_kind: str = ""
    image_label: str = ""
    image_references: tuple[str, ...] = ()
    generation_prompt: str = ""
    generation_model: str = ""
    image_source_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.image_kind:
            object.__setattr__(self, "image_kind", "official" if self.image_url else "placeholder")
        for field in ("inventory_key", "code", "unit", "price_source", "match_status"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Campo obligatorio Offiho invalido: {field}")
        _validate_catalog_decimal("pieces_per_box", self.pieces_per_box, positive=True)
        _validate_catalog_decimal("available_quantity", self.available_quantity)
        _validate_catalog_decimal("unit_price", self.unit_price)
        _validate_optional_official_url("product_url", self.product_url)
        _validate_optional_official_url("image_url", self.image_url)
        if len(self.description) > MAX_DESCRIPTION_LENGTH:
            raise ValueError("Campo Offiho demasiado largo: description")
        if self.image_kind not in OFFIHO_IMAGE_KINDS:
            raise ValueError("Campo Offiho invalido: image_kind")
        for field in ("image_label", "generation_prompt", "generation_model"):
            value = getattr(self, field)
            if not isinstance(value, str) or len(value) > MAX_IMAGE_METADATA_LENGTH:
                raise ValueError(f"Campo Offiho invalido: {field}")
        if not isinstance(self.image_references, tuple) or any(
            not isinstance(value, str) or not value.strip() or len(value) > MAX_IMAGE_METADATA_LENGTH
            for value in self.image_references
        ):
            raise ValueError("Campo Offiho invalido: image_references")
        if self.image_source_sha256 and (
            len(self.image_source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.image_source_sha256)
        ):
            raise ValueError("Campo Offiho invalido: image_source_sha256")
        if self.image_kind == "generated_reference" and (
            not self.image_url or not self.image_label.strip() or not self.image_references
        ):
            missing = "image_label" if not self.image_label.strip() else "image_references"
            raise ValueError(f"Campo Offiho obligatorio para imagen generada: {missing}")
        if self.image_kind == "official" and not self.image_url:
            raise ValueError("Imagen oficial Offiho sin image_url")
        if self.image_kind == "placeholder" and self.image_url:
            raise ValueError("Placeholder Offiho no puede tener image_url")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OffihoCatalogItem":
        if not isinstance(raw, dict):
            raise ValueError("Item Offiho invalido: se esperaba un objeto")
        image_url = str(raw.get("image_url", "") or "").strip()
        image_kind = str(raw.get("image_kind") or ("official" if image_url else "placeholder")).strip()
        raw_references = raw.get("image_references", ())
        if not isinstance(raw_references, (list, tuple)):
            raise ValueError("Campo Offiho invalido: image_references")
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
            image_url=image_url,
            description=str(raw.get("description", "") or "").strip(),
            description_source=str(raw.get("description_source", "inventory_label") or "inventory_label").strip(),
            match_status=_required_text(raw, "match_status"),
            source_updated_at=str(raw.get("source_updated_at", "") or "").strip(),
            image_kind=image_kind,
            image_label=str(raw.get("image_label", "") or "").strip(),
            image_references=tuple(str(value).strip() for value in raw_references),
            generation_prompt=str(raw.get("generation_prompt", "") or "").strip(),
            generation_model=str(raw.get("generation_model", "") or "").strip(),
            image_source_sha256=str(raw.get("image_source_sha256", "") or "").strip(),
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
            "description": self.description,
            "description_source": self.description_source,
            "match_status": self.match_status,
            "source_updated_at": self.source_updated_at,
            "image_kind": self.image_kind,
            "image_label": self.image_label,
            "image_references": list(self.image_references),
            "generation_prompt": self.generation_prompt,
            "generation_model": self.generation_model,
            "image_source_sha256": self.image_source_sha256,
        }


def load_offiho_catalog(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = Path(path or CATALOG_PATH)
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    return load_offiho_catalog_data(raw)


def load_offiho_catalog_data(raw: dict[str, Any]) -> dict[str, Any]:
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
        "source_row_count": _audit_count(raw, "source_row_count"),
        "duplicate_row_count": _audit_count(raw, "duplicate_row_count"),
        "unique_item_count": _audit_count(raw, "unique_item_count", fallback="total"),
    }
    if (
        not items
        or audit["unique_item_count"] != len(items)
        or audit["source_row_count"] != len(items) + audit["duplicate_row_count"]
    ):
        raise ValueError("Catalogo Offiho invalido: conteos de auditoria inconsistentes")
    if len(set(keys)) != len(items) or not all(keys):
        raise ValueError("Catalogo Offiho invalido: claves de inventario no unicas")
    total = raw.get("total", len(items))
    if isinstance(total, bool) or not isinstance(total, int) or total != len(items):
        raise ValueError("Catalogo Offiho invalido: conteos de auditoria inconsistentes")
    result = {
        "source_hash": str(raw.get("source_hash", "")),
        "generated_at": str(raw.get("generated_at", "")),
        **audit,
        "total": total,
        "items": items,
        "by_inventory_key": {item.inventory_key: item for item in items},
    }
    for field in (
        "catalog_built_at",
        "inventory_fetched_at",
        "inventory_last_modified",
        "workbook_generated_at",
        "stock_snapshot_hash",
        "enrichment_source_hash",
        "sync_audit",
        "sources",
    ):
        if field in raw:
            result[field] = raw[field]
    return result


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
                "name": _quote_item_name(item),
                "variant": item.variant,
                "unit": item.unit,
                "quantity": _json_number(quantity),
                "unit_price": _json_number(item.unit_price),
                "price_source": item.price_source,
                "available_quantity": _json_number(item.available_quantity),
                "stock_status": status,
                "product_url": item.product_url,
                "image_url": item.image_url,
                "description": item.description,
                "image_kind": item.image_kind,
                "image_label": item.image_label,
                "image_references": list(item.image_references),
                "warnings": ["Imagen de referencia"] if item.image_kind == "generated_reference" else [],
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


def _quote_item_name(item: OffihoCatalogItem) -> str:
    if item.name:
        return item.name
    if "/" in item.code:
        return " ".join(part for part in (item.code.split("/", 1)[0], item.variant) if part).strip()
    return item.inventory_key


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = str(raw.get(field, "") or "").strip()
    if not value:
        raise ValueError(f"Campo obligatorio Offiho invalido: {field}")
    return value


def _audit_count(raw: dict[str, Any], field: str, *, fallback: str | None = None) -> int:
    value = raw.get(field, raw.get(fallback) if fallback else None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Catalogo Offiho invalido: conteos de auditoria inconsistentes ({field})")
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
    is_catalog_asset = (
        host == "web-lemon-one-45.vercel.app"
        and parsed.path.startswith("/catalog-assets/offiho/")
    )
    is_catalog_source = (
        field == "product_url" and host == "mobiliti11-my.sharepoint.com"
    )
    if (
        parsed.scheme.lower() != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or (
            host not in OFFICIAL_IMAGE_HOSTS[OFFIHO_CART_SOURCE_TYPE]
            and not is_catalog_source
        )
        or (host == "web-lemon-one-45.vercel.app" and not is_catalog_asset)
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
