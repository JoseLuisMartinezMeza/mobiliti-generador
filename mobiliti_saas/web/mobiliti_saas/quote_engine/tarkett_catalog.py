from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
import json

from .catalog_cart import create_catalog_quotation_workbook, parse_commercial_quantity


CATALOG_PATH = Path(__file__).resolve().parent / "data" / "tarkett_catalog.json"
TARKETT_CART_SOURCE_TYPE = "tarkett_cart"
MAX_CART_LINES = 200


@dataclass(frozen=True)
class TarkettCatalogItem:
    code: str
    name: str
    unit: str
    available_quantity: Decimal
    product_url: str = ""
    image_url: str = ""
    match_status: str = "unmatched"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TarkettCatalogItem":
        if not isinstance(raw, dict):
            raise ValueError("Item Tarkett invalido: se esperaba un objeto")
        return cls(
            code=_required_text(raw, "code"),
            name=_required_text(raw, "name"),
            unit=_required_text(raw, "unit"),
            available_quantity=_strict_stock(raw),
            product_url=str(raw.get("product_url", "") or "").strip(),
            image_url=str(raw.get("image_url", "") or "").strip(),
            match_status=str(raw.get("match_status", "unmatched") or "unmatched").strip(),
        )

    def to_public_dict(self, reserved_quantity: Decimal = Decimal("0"), reserved_by_others: bool = False) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "unit": self.unit,
            "available_quantity": _json_number(self.available_quantity),
            "reserved_quantity": _json_number(reserved_quantity),
            "reserved_by_others": bool(reserved_by_others),
            "product_url": self.product_url,
            "image_url": self.image_url,
            "match_status": self.match_status,
        }


def load_tarkett_catalog(path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = Path(path or CATALOG_PATH)
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Catalogo Tarkett invalido: raiz no es un objeto")
    for field in ("source_hash", "generated_at"):
        _required_text(raw, field)
    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Catalogo Tarkett invalido: catalogo vacio")
    items: list[TarkettCatalogItem] = []
    for index, item in enumerate(raw_items):
        try:
            items.append(TarkettCatalogItem.from_dict(item))
        except ValueError as exc:
            raise ValueError(f"Catalogo Tarkett invalido en item {index}: {exc}") from exc
    codes = [item.code for item in items]
    if len(set(codes)) != len(codes):
        raise ValueError("Catalogo Tarkett invalido: claves duplicadas")
    declared_total = raw.get("total")
    if declared_total is not None and (isinstance(declared_total, bool) or not isinstance(declared_total, int) or declared_total != len(items)):
        raise ValueError("Catalogo Tarkett invalido: total no coincide con items")
    return {
        "source_hash": str(raw.get("source_hash", "")),
        "generated_at": str(raw.get("generated_at", "")),
        "source_file": str(raw.get("source_file", "")),
        "items": items,
        "by_code": {item.code: item for item in items},
    }


def build_tarkett_cart_payload(
    raw_items: list[dict[str, Any]],
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded_catalog = catalog or load_tarkett_catalog()
    by_code: dict[str, TarkettCatalogItem] = loaded_catalog["by_code"]
    if not raw_items:
        raise ValueError("El carrito Tarkett esta vacio")
    if len(raw_items) > MAX_CART_LINES:
        raise ValueError(f"El carrito Tarkett excede el limite de {MAX_CART_LINES} productos")

    lines: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto Tarkett debe ser un objeto")
        code = str(raw.get("code", raw.get("clave", ""))).strip()
        if not code:
            raise ValueError("Cada producto Tarkett requiere clave")
        if code in seen_codes:
            raise ValueError(f"Codigo Tarkett duplicado: {code}")
        seen_codes.add(code)
        item = by_code.get(code)
        if item is None:
            raise ValueError(f"Producto Tarkett no encontrado: {code}")
        quantity = parse_commercial_quantity(
            raw.get("quantity", raw.get("cantidad", 0)),
            item_label=code,
            max_decimal_places=6,
        )
        if quantity > item.available_quantity:
            raise ValueError(f"Cantidad mayor a existencia para {code}")
        lines.append(
            {
                "code": item.code,
                "name": item.name,
                "unit": item.unit,
                "quantity": _json_number(quantity),
                "unit_price": 0,
                "available_quantity": _json_number(item.available_quantity),
                "product_url": item.product_url,
                "image_url": item.image_url,
            }
        )

    return {
        "source_type": TARKETT_CART_SOURCE_TYPE,
        "catalog_source_hash": loaded_catalog["source_hash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": lines,
    }


def create_tarkett_quotation_workbook(
    cart_payload: dict[str, Any],
    output_path: str | Path,
    *,
    image_dir: str | Path | None = None,
) -> Path:
    sanitized_payload = {
        **cart_payload,
        "items": [
            {**item, "unit_price": 0}
            for item in list(cart_payload.get("items") or [])
        ],
    }
    return create_catalog_quotation_workbook(
        sanitized_payload,
        output_path,
        source_type=TARKETT_CART_SOURCE_TYPE,
        category_label="Tarkett",
        image_dir=image_dir,
    )


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError("Numero Tarkett invalido") from None


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = str(raw.get(field, "") or "").strip()
    if not value:
        raise ValueError(f"Campo obligatorio Tarkett invalido: {field}")
    return value


def _strict_stock(raw: dict[str, Any]) -> Decimal:
    if "available_quantity" not in raw:
        raise ValueError("Campo obligatorio Tarkett invalido: available_quantity")
    try:
        value = Decimal(str(raw["available_quantity"]).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        raise ValueError("Campo numerico Tarkett invalido: available_quantity") from None
    if not value.is_finite() or value < 0:
        raise ValueError("Campo numerico Tarkett invalido: available_quantity")
    return value


def _json_number(value: Any) -> int | float:
    if not isinstance(value, Decimal):
        value = _decimal(value)
    if not value.is_finite():
        raise ValueError("Numero Tarkett invalido")
    if value == value.to_integral():
        return int(value)
    return float(value)
