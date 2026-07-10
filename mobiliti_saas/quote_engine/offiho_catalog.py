from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import json


CATALOG_PATH = Path(__file__).resolve().parent / "data" / "offiho_catalog.json"
OFFIHO_CART_SOURCE_TYPE = "offiho_cart"
EXPECTED_UNIQUE_ITEM_COUNT = 1206


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

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OffihoCatalogItem":
        return cls(
            inventory_key=str(raw.get("inventory_key", "")).strip(),
            code=str(raw.get("code", "")).strip(),
            name=str(raw.get("name", "")).strip(),
            variant=str(raw.get("variant", "")).strip(),
            unit=str(raw.get("unit", "")).strip(),
            pieces_per_box=_decimal(raw.get("pieces_per_box", 1)),
            available_quantity=_decimal(raw.get("available_quantity", 0)),
            unit_price=_decimal(raw.get("unit_price", 0)),
            price_source=str(raw.get("price_source", "missing") or "missing").strip(),
            product_url=str(raw.get("product_url", "") or "").strip(),
            image_url=str(raw.get("image_url", "") or "").strip(),
            match_status=str(raw.get("match_status", "unmatched") or "unmatched").strip(),
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
    items = [OffihoCatalogItem.from_dict(item) for item in raw.get("items", [])]
    keys = [item.inventory_key for item in items]
    declared_count = raw.get("unique_item_count", raw.get("total"))
    if declared_count != EXPECTED_UNIQUE_ITEM_COUNT:
        raise ValueError("Catalogo Offiho invalido: indice unico esperado de 1206")
    if len(items) != EXPECTED_UNIQUE_ITEM_COUNT or len(set(keys)) != EXPECTED_UNIQUE_ITEM_COUNT or not all(keys):
        raise ValueError("Catalogo Offiho invalido: claves de inventario no unicas")
    return {
        "source_hash": str(raw.get("source_hash", "")),
        "generated_at": str(raw.get("generated_at", "")),
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

    lines: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto Offiho debe ser un objeto")
        inventory_key = str(raw.get("inventory_key", "")).strip()
        if not inventory_key:
            raise ValueError("Cada producto Offiho requiere inventory_key")
        item = by_inventory_key.get(inventory_key)
        if item is None:
            raise ValueError(f"Producto Offiho no encontrado: {inventory_key}")
        quantity = _decimal(raw.get("quantity", 0))
        if quantity <= 0:
            raise ValueError(f"Cantidad invalida para {inventory_key}")
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


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
        return parsed if parsed.is_finite() else Decimal("0")
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral():
        return int(value)
    return float(value)
