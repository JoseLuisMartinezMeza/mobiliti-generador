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
        return cls(
            code=str(raw.get("code", "")).strip(),
            name=str(raw.get("name", "")).strip(),
            unit=str(raw.get("unit", "")).strip(),
            available_quantity=_decimal(raw.get("available_quantity", 0)),
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
    items = [TarkettCatalogItem.from_dict(item) for item in raw.get("items", [])]
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

    lines: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto Tarkett debe ser un objeto")
        code = str(raw.get("code", raw.get("clave", ""))).strip()
        if not code:
            raise ValueError("Cada producto Tarkett requiere clave")
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
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _json_number(value: Any) -> int | float:
    if not isinstance(value, Decimal):
        value = _decimal(value)
    if value == value.to_integral():
        return int(value)
    return float(value)
