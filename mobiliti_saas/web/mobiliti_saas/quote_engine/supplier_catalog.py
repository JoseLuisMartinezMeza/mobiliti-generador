from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any
import ipaddress
import json
import unicodedata
from urllib.parse import urlsplit

from .catalog_cart import create_catalog_quotation_workbook


ALLOWED_SUPPLIERS = {"cr-global", "sonara", "sunon", "alma", "lumbro"}
SUPPLIER_LABELS = {
    "cr-global": "CR Global",
    "sonara": "Sonara",
    "sunon": "Sunon",
    "alma": "ALMA",
    "lumbro": "Lumbro",
}
ALLOWED_CURRENCIES = {"USD", "MXN", "EUR"}
UNKNOWN_BASE_CURRENCY = "XXX"
REVIEW_QUOTABLE_SUPPLIERS = frozenset({"lumbro", "sonara"})
EXPECTED_SUPPLIER_BASE_CURRENCY = {
    "cr-global": "MXN", "sonara": "MXN", "sunon": "USD", "alma": "USD", "lumbro": "MXN",
}
PUBLIC_ITEM_FIELDS = (
    "internal_id", "supplier", "product_key", "sku", "code_status",
    "brand", "collection", "name", "description", "unit",
    "availability_type", "stock", "lead_time", "base_price_options",
    "add_on_options", "base_currency", "price_net", "tax_rate",
    "attributes", "image_url", "image_kind", "product_url", "warnings",
    "source_reference",
)
MONEY_LIMIT = Decimal("1000000000")
QUANTITY_LIMIT = Decimal("1000000")
SIX_PLACES = Decimal("0.000001")
TWO_PLACES = Decimal("0.01")
MAX_CATALOG_ITEMS = 10_000
MAX_CART_ROWS = 500
MAX_OPTIONS_PER_ITEM = 200
MAX_COMPATIBLE_OPTION_IDS = 200
MAX_WARNINGS_PER_ITEM = 100
MAX_RATE_ROWS = 5_000
MAX_IDENTIFIER_LENGTH = 256
MAX_TEXT_LENGTH = 1_000
MAX_DESCRIPTION_LENGTH = 10_000
MAX_SOURCE_REFERENCE_LENGTH = 2_048
MAX_URL_LENGTH = 2_048
MAX_WARNING_LENGTH = 2_000
MAX_ATTRIBUTES_JSON_BYTES = 32_768
MAX_ATTRIBUTES_DEPTH = 8
MAX_METADATA_JSON_BYTES = 262_144
MAX_METADATA_DEPTH = 8
MAX_METADATA_NODES = 10_000
_METADATA_UTF8_CHUNK_CHARS = 16_384
DERIVED_DECIMAL_PRECISION = 80
MAX_DERIVED_EXCHANGE_RATE = Decimal("1000000")
MAX_CONFIGURED_AMOUNT = Decimal("250000000000")
MAX_CONVERTED_UNIT_AMOUNT = Decimal("1000000000000000")
MAX_LINE_TOTAL = Decimal("1000000000000000")
RATE_ROW_FIELDS = {"currency", "effective_date", "mxn_per_unit", "retrieved_at"}
BASE_OPTION_FIELDS = {"id", "name", "price_net", "available"}
ADD_ON_REQUIRED_FIELDS = BASE_OPTION_FIELDS | {"family"}
ADD_ON_OPTION_FIELDS = ADD_ON_REQUIRED_FIELDS | {"compatible_base_option_ids"}
_NORMALIZED_CATALOG_TOKEN = object()


@dataclass(frozen=True)
class RateSnapshot:
    base_currency: str
    quote_currency: str
    exchange_rate: Decimal
    rate_source: str
    rate_effective_date: date
    rate_retrieved_at: str


class _NormalizedSupplierCatalog(dict):
    def __init__(self, value: dict[str, Any]):
        super().__init__(value)
        self._normalization_token = _NORMALIZED_CATALOG_TOKEN


def load_supplier_catalog_data(payload: dict, expected_supplier: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Catalogo de proveedor invalido: raiz no es un objeto")
    required_fields = {"supplier", "source_hash", "generated_at", "items"}
    allowed_fields = required_fields | {"metadata"}
    is_normalized = (
        type(payload) is _NormalizedSupplierCatalog
        and getattr(payload, "_normalization_token", None) is _NORMALIZED_CATALOG_TOKEN
    )
    if is_normalized:
        internal_fields = allowed_fields | {"by_internal_id"}
        if not required_fields <= set(payload) or set(payload) - internal_fields:
            raise ValueError("Catalogo de proveedor normalizado invalido")
        payload = {
            key: payload[key]
            for key in allowed_fields
            if key in payload
        }
    missing = required_fields - set(payload)
    unexpected = set(payload) - allowed_fields
    if missing or unexpected:
        problems = []
        if missing:
            problems.append("campos raiz faltantes: " + ", ".join(sorted(missing)))
        if unexpected:
            problems.append("campos raiz inesperados: " + ", ".join(sorted(map(str, unexpected))))
        raise ValueError("Catalogo de proveedor invalido: " + "; ".join(problems))
    supplier = _enum_text(payload, "supplier", ALLOWED_SUPPLIERS)
    if expected_supplier is not None and supplier != expected_supplier:
        raise ValueError("Catalogo de proveedor invalido: supplier no coincide")
    source_hash = _sha256(payload.get("source_hash"))
    generated_at = _required_text(payload, "generated_at")
    try:
        _iso_datetime(generated_at)
    except ValueError:
        raise ValueError("generated_at invalido") from None
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Catalogo de proveedor invalido: catalogo vacio")
    if len(raw_items) > MAX_CATALOG_ITEMS:
        raise ValueError(f"Catalogo de proveedor excede el limite de items: {MAX_CATALOG_ITEMS}")

    items: list[dict[str, Any]] = []
    by_internal_id: dict[str, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_items):
        try:
            item = _validate_item(raw, supplier)
        except ValueError as exc:
            raise ValueError(f"Catalogo de proveedor invalido en item {index}: {exc}") from exc
        existing = by_internal_id.get(item["internal_id"])
        if existing is not None:
            if existing != item:
                raise ValueError(f"internal_id duplicado incompatible: {item['internal_id']}")
            continue
        sku = item["sku"]
        sku_key = sku.casefold()
        if sku_key and sku_key in by_sku:
            raise ValueError(f"sku duplicado incompatible: {sku}")
        items.append(item)
        by_internal_id[item["internal_id"]] = item
        if sku_key:
            by_sku[sku_key] = item

    result = _NormalizedSupplierCatalog({
        "supplier": supplier,
        "source_hash": source_hash,
        "generated_at": generated_at,
        "items": items,
        "by_internal_id": by_internal_id,
    })
    if "metadata" in payload:
        result["metadata"] = _normalize_metadata(payload["metadata"])
    return result


def build_supplier_cart_payload(
    raw_items: list[dict],
    catalog: dict,
    quote_currency: str,
    rate_rows: list[dict],
) -> dict:
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("El carrito de proveedor esta vacio")
    if len(raw_items) > MAX_CART_ROWS:
        raise ValueError(f"El carrito excede el limite de filas: {MAX_CART_ROWS}")
    loaded = load_supplier_catalog_data(catalog)
    quote_currency = _currency(quote_currency)
    by_id = loaded["by_internal_id"]
    prepared: list[tuple[dict[str, Any], Decimal, dict[str, Any] | None, list[dict[str, Any]]]] = []
    seen_configurations: set[tuple[str, str, tuple[str, ...]]] = set()
    base_currencies: set[str] = set()

    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto del carrito debe ser un objeto")
        unexpected = set(raw) - {"internal_id", "quantity", "base_option_id", "add_on_option_ids"}
        if unexpected:
            raise ValueError(f"Campo de carrito no permitido: {min(unexpected)}")
        internal_id = _browser_string(raw.get("internal_id"), "internal_id")
        item = by_id.get(internal_id)
        if item is None:
            raise ValueError(f"Producto de proveedor no encontrado: {internal_id}")
        expected_currency = EXPECTED_SUPPLIER_BASE_CURRENCY[loaded["supplier"]]
        if item["base_currency"] != expected_currency:
            raise ValueError("moneda base por verificar; el producto no se puede cotizar")
        if item["code_status"] != "verified":
            if loaded["supplier"] not in REVIEW_QUOTABLE_SUPPLIERS:
                raise ValueError("codigo por verificar; el producto no se puede cotizar")
            if item["base_currency"] != "MXN":
                raise ValueError("moneda base por verificar; el producto no se puede cotizar")
            if Decimal(item["tax_rate"]) != Decimal("0.160000"):
                raise ValueError("IVA 16% requerido para codigo por verificar")
        quantity = _quantity(
            raw.get("quantity"),
            allow_fractional=_is_square_meter_unit(item["unit"]),
        )
        raw_base_option = raw.get("base_option_id")
        if "base_option_id" in raw and not isinstance(raw_base_option, str):
            raise ValueError("base_option_id debe ser texto")
        base_option = _select_base_option(item, raw_base_option)
        add_ons = _select_add_ons(item, raw.get("add_on_option_ids", []), base_option)
        configured_price = Decimal(base_option["price_net"] if base_option else item["price_net"]) + sum(
            (Decimal(option["price_net"]) for option in add_ons),
            Decimal(0),
        )
        if configured_price <= 0:
            raise ValueError("precio por confirmar; el producto no se puede cotizar")
        configuration_key = (
            internal_id,
            base_option["id"] if base_option else "",
            tuple(sorted(option["id"] for option in add_ons)),
        )
        if configuration_key in seen_configurations:
            raise ValueError(f"configuracion duplicada para {internal_id}")
        seen_configurations.add(configuration_key)
        base_currencies.add(item["base_currency"])
        prepared.append((item, quantity, base_option, add_ons))

    if len(base_currencies) != 1:
        raise ValueError("El carrito no puede mezclar monedas base")
    base_currency = next(iter(base_currencies))
    if base_currency == UNKNOWN_BASE_CURRENCY:
        raise ValueError("moneda base por verificar; el producto no se puede cotizar")
    rate = resolve_conversion_rate(base_currency, quote_currency, rate_rows, date.today())
    lines = [
        _cart_line(item, quantity, base_option, add_ons, rate.exchange_rate)
        for item, quantity, base_option, add_ons in prepared
    ]
    return {
        "source_type": "supplier_cart",
        "supplier": loaded["supplier"],
        "catalog_source_hash": loaded["source_hash"],
        "base_currency": rate.base_currency,
        "quote_currency": rate.quote_currency,
        "exchange_rate": _fixed(rate.exchange_rate, 6),
        "rate_source": rate.rate_source,
        "rate_effective_date": rate.rate_effective_date.isoformat(),
        "rate_retrieved_at": rate.rate_retrieved_at,
        "items": lines,
    }


def resolve_conversion_rate(
    base_currency: str,
    quote_currency: str,
    rate_rows: list[dict],
    today: date,
) -> RateSnapshot:
    base_currency = _currency(base_currency)
    quote_currency = _currency(quote_currency)
    if isinstance(today, datetime) or not isinstance(today, date):
        raise ValueError("Fecha de conversion invalida")
    if not isinstance(rate_rows, list):
        raise ValueError("Filas de tasa invalidas")
    if len(rate_rows) > MAX_RATE_ROWS:
        raise ValueError(f"Se excede el limite de tasas: {MAX_RATE_ROWS}")

    rates: dict[tuple[str, date], tuple[Decimal, str]] = {}
    for raw in rate_rows:
        if not isinstance(raw, dict):
            raise ValueError("Fila de tasa invalida")
        if set(raw) != RATE_ROW_FIELDS:
            raise ValueError("Fila de tasa con estructura invalida")
        currency = _enum_text(raw, "currency", {"USD", "EUR"})
        effective_date = _iso_date(raw.get("effective_date"), "effective_date")
        if effective_date > today:
            raise ValueError("Fila de tasa con fecha futura")
        value = Decimal(_decimal_string(raw.get("mxn_per_unit"), "tasa", positive=True))
        retrieved_at = _required_text(raw, "retrieved_at")
        _iso_datetime(retrieved_at)
        key = (currency, effective_date)
        candidate = (value, retrieved_at)
        if key in rates and rates[key] != candidate:
            raise ValueError("Fila de tasa duplicada incompatible")
        rates[key] = candidate

    if base_currency == quote_currency:
        identity_rate = Decimal("1.000000")
        _validate_derived_decimal(identity_rate, "exchange_rate", MAX_DERIVED_EXCHANGE_RATE)
        return RateSnapshot(base_currency, quote_currency, identity_rate, "identity", today, "")

    needed = {currency for currency in (base_currency, quote_currency) if currency != "MXN"}
    usable_dates = {
        effective_date
        for currency, effective_date in rates
        if all((needed_currency, effective_date) in rates for needed_currency in needed)
    }
    if not usable_dates:
        raise ValueError("No existe una tasa utilizable para la conversion")
    effective_date = max(usable_dates)
    if (today - effective_date).days > 5:
        raise ValueError("La tasa de conversion esta vencida")

    retrieved_at = max(
        (rates[(currency, effective_date)][1] for currency in needed),
        key=_iso_datetime,
    )
    try:
        with localcontext(Context(prec=DERIVED_DECIMAL_PRECISION, rounding=ROUND_HALF_UP)):
            if base_currency == "MXN":
                value = Decimal(1) / rates[(quote_currency, effective_date)][0]
            elif quote_currency == "MXN":
                value = rates[(base_currency, effective_date)][0]
            else:
                value = rates[(base_currency, effective_date)][0] / rates[(quote_currency, effective_date)][0]
            _validate_derived_decimal(value, "exchange_rate", MAX_DERIVED_EXCHANGE_RATE)
            exchange_rate = value.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)
            _validate_derived_decimal(exchange_rate, "exchange_rate", MAX_DERIVED_EXCHANGE_RATE)
    except DecimalException:
        raise ValueError("calculo de exchange_rate invalido") from None
    return RateSnapshot(
        base_currency,
        quote_currency,
        exchange_rate,
        "saas_exchange_rates",
        effective_date,
        retrieved_at,
    )


def create_supplier_quotation_workbook(payload: dict, output_path: Path) -> Path:
    if not isinstance(payload, dict):
        raise ValueError("Payload de proveedor invalido")
    supplier = str(payload.get("supplier") or "").strip().lower()
    if supplier not in ALLOWED_SUPPLIERS:
        raise ValueError("Proveedor no soportado")
    workbook_payload = deepcopy(payload)
    for item in workbook_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        unit = str(item.get("unit") or "").strip()
        source_reference = str(item.get("source_reference") or "").strip()
        item["description"] = " | ".join(
            part
            for part in (
                description,
                f"Unidad: {unit}" if unit else "",
                f"Fuente: {source_reference}" if source_reference else "",
            )
            if part
        )
    return create_catalog_quotation_workbook(
        workbook_payload,
        output_path,
        source_type="supplier_cart",
        category_label=SUPPLIER_LABELS[supplier],
        text_transform=safe_excel_text,
    )


def safe_excel_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    first = text.lstrip()[:1]
    return "'" + text if first in {"=", "+", "-", "@"} else text


def _validate_item(raw: Any, supplier: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("item no es un objeto")
    fields = set(raw)
    expected_fields = set(PUBLIC_ITEM_FIELDS)
    missing = expected_fields - fields
    unexpected = fields - expected_fields
    if missing or unexpected:
        problems = []
        if missing:
            problems.append("campos faltantes: " + ", ".join(sorted(map(str, missing))))
        if unexpected:
            problems.append("campos inesperados: " + ", ".join(sorted(map(str, unexpected))))
        raise ValueError("; ".join(problems))

    item_supplier = _enum_text(raw, "supplier", ALLOWED_SUPPLIERS)
    if item_supplier != supplier:
        raise ValueError("supplier del item no coincide")
    code_status = _enum_text(raw, "code_status", {"verified", "needs_review"})
    sku = _text(raw, "sku")
    if code_status == "verified" and not sku:
        raise ValueError("sku obligatorio para codigo verificado")
    if code_status == "needs_review" and sku:
        raise ValueError("sku debe estar vacio cuando code_status es needs_review")

    base_options = _options(raw["base_price_options"], add_on=False)
    add_on_options = _options(raw["add_on_options"], add_on=True)
    base_ids = {option["id"] for option in base_options}
    for option in add_on_options:
        unknown = set(option.get("compatible_base_option_ids", [])) - base_ids
        if unknown:
            raise ValueError(f"opcion compatible desconocida: {min(unknown)}")

    availability_type = _enum_text(raw, "availability_type", {"stocked", "made_to_order", "unknown"})
    stock = raw["stock"]
    if stock is not None:
        stock = _decimal_string(stock, "stock", minimum=Decimal(0))
    if availability_type == "stocked" and stock is None:
        raise ValueError("stock obligatorio para producto stocked")
    attributes = raw["attributes"]
    if not isinstance(attributes, dict):
        raise ValueError("attributes debe ser un objeto")
    _validate_attributes(attributes)
    warnings = raw["warnings"]
    if not isinstance(warnings, list):
        raise ValueError("warnings debe ser una lista de texto")
    if len(warnings) > MAX_WARNINGS_PER_ITEM:
        raise ValueError(f"Se excede el limite de warnings: {MAX_WARNINGS_PER_ITEM}")
    if any(not isinstance(value, str) for value in warnings):
        raise ValueError("warnings debe ser una lista de texto")
    for warning in warnings:
        _bounded_string(warning, "warning", MAX_WARNING_LENGTH)

    base_currency = _base_currency(raw["base_currency"])
    price_net = _decimal_string(raw["price_net"], "price_net", minimum=Decimal(0))
    option_prices = [option["price_net"] for option in base_options + add_on_options]
    if base_currency == UNKNOWN_BASE_CURRENCY and (
        Decimal(price_net) != 0 or any(Decimal(value) != 0 for value in option_prices)
    ):
        raise ValueError("moneda base por verificar exige precios cero")

    return {
        "internal_id": _required_text(raw, "internal_id", MAX_IDENTIFIER_LENGTH),
        "supplier": item_supplier,
        "product_key": _required_text(raw, "product_key", MAX_IDENTIFIER_LENGTH),
        "sku": sku,
        "code_status": code_status,
        "brand": _text(raw, "brand"),
        "collection": _text(raw, "collection"),
        "name": _required_text(raw, "name"),
        "description": _text(raw, "description", MAX_DESCRIPTION_LENGTH),
        "unit": _required_text(raw, "unit"),
        "availability_type": availability_type,
        "stock": stock,
        "lead_time": _text(raw, "lead_time"),
        "base_price_options": base_options,
        "add_on_options": add_on_options,
        "base_currency": base_currency,
        "price_net": price_net,
        "tax_rate": _decimal_string(raw["tax_rate"], "tax_rate", minimum=Decimal(0), maximum=Decimal(1)),
        "attributes": deepcopy(attributes),
        "image_url": _optional_http_url(raw, "image_url"),
        "image_kind": _enum_text(raw, "image_kind", {"official", "generated_reference", "placeholder"}),
        "product_url": _optional_http_url(raw, "product_url"),
        "warnings": list(warnings),
        "source_reference": _required_text(raw, "source_reference", MAX_SOURCE_REFERENCE_LENGTH),
    }


def _options(raw_options: Any, *, add_on: bool) -> list[dict[str, Any]]:
    if not isinstance(raw_options, list):
        raise ValueError("options debe ser una lista")
    if len(raw_options) > MAX_OPTIONS_PER_ITEM:
        raise ValueError(f"Se excede el limite de options: {MAX_OPTIONS_PER_ITEM}")
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_options:
        if not isinstance(raw, dict):
            raise ValueError("option debe ser un objeto")
        required_fields = ADD_ON_REQUIRED_FIELDS if add_on else BASE_OPTION_FIELDS
        allowed_fields = ADD_ON_OPTION_FIELDS if add_on else BASE_OPTION_FIELDS
        fields = set(raw)
        missing = required_fields - fields
        unexpected = fields - allowed_fields
        if missing or unexpected:
            problems = []
            if missing:
                problems.append("campos faltantes: " + ", ".join(sorted(map(str, missing))))
            if unexpected:
                problems.append("campos inesperados: " + ", ".join(sorted(map(str, unexpected))))
            raise ValueError("option invalida: " + "; ".join(problems))
        option_id = _required_text(raw, "id", MAX_IDENTIFIER_LENGTH)
        if option_id in seen:
            raise ValueError(f"option id duplicado: {option_id}")
        seen.add(option_id)
        available = raw.get("available")
        if not isinstance(available, bool):
            raise ValueError("available de option debe ser booleano")
        option = {
            "id": option_id,
            "name": _required_text(raw, "name"),
            "price_net": _decimal_string(raw.get("price_net"), "price_net de option", minimum=Decimal(0)),
            "available": available,
        }
        if add_on:
            option["family"] = _required_text(raw, "family")
            compatible = raw.get("compatible_base_option_ids", [])
            if not isinstance(compatible, list) or any(not isinstance(value, str) or not value.strip() for value in compatible):
                raise ValueError("compatible_base_option_ids debe ser una lista de texto")
            if len(compatible) > MAX_COMPATIBLE_OPTION_IDS:
                raise ValueError(f"Se excede el limite de compatible_base_option_ids: {MAX_COMPATIBLE_OPTION_IDS}")
            for value in compatible:
                _bounded_string(value, "compatible_base_option_id", MAX_IDENTIFIER_LENGTH, required=True)
            if len(set(compatible)) != len(compatible):
                raise ValueError("compatible_base_option_ids contiene duplicados")
            if compatible:
                option["compatible_base_option_ids"] = list(compatible)
        options.append(option)
    return options


def _select_base_option(item: dict[str, Any], raw_id: Any) -> dict[str, Any] | None:
    options = item["base_price_options"]
    if raw_id is None:
        clean_id = ""
    else:
        clean_id = _browser_string(raw_id, "base_option_id")
    if not options:
        if clean_id:
            raise ValueError("El producto no admite opcion base")
        return None
    if not clean_id:
        raise ValueError("Se requiere exactamente una opcion base")
    option = next((candidate for candidate in options if candidate["id"] == clean_id), None)
    if option is None:
        raise ValueError(f"Opcion base desconocida: {clean_id}")
    if not option["available"]:
        raise ValueError(f"Opcion base no disponible: {clean_id}")
    return option


def _select_add_ons(item: dict[str, Any], raw_ids: Any, base_option: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(raw_ids, list):
        raise ValueError("add_on_option_ids debe ser una lista")
    if len(raw_ids) > MAX_OPTIONS_PER_ITEM:
        raise ValueError(f"Se excede el limite de add_on_option_ids: {MAX_OPTIONS_PER_ITEM}")
    clean_ids = [_browser_string(value, "add_on_option_id") for value in raw_ids]
    if len(set(clean_ids)) != len(clean_ids):
        raise ValueError("add_on_option_id duplicado")
    by_id = {option["id"]: option for option in item["add_on_options"]}
    selected: list[dict[str, Any]] = []
    families: set[str] = set()
    for option_id in clean_ids:
        option = by_id.get(option_id)
        if option is None:
            raise ValueError(f"Add-on desconocido: {option_id}")
        if not option["available"]:
            raise ValueError(f"Add-on no disponible: {option_id}")
        compatible = option.get("compatible_base_option_ids", [])
        if compatible and (base_option is None or base_option["id"] not in compatible):
            raise ValueError(f"Add-on incompatible con opcion base: {option_id}")
        if option["family"] in families:
            raise ValueError(f"No se permite mas de un add-on de la familia {option['family']}")
        families.add(option["family"])
        selected.append(option)
    return selected


def _cart_line(
    item: dict[str, Any],
    quantity: Decimal,
    base_option: dict[str, Any] | None,
    add_ons: list[dict[str, Any]],
    exchange_rate: Decimal,
) -> dict[str, Any]:
    base_price = Decimal(base_option["price_net"] if base_option else item["price_net"])
    try:
        with localcontext(Context(prec=DERIVED_DECIMAL_PRECISION, rounding=ROUND_HALF_UP)):
            configured = base_price + sum(
                (Decimal(option["price_net"]) for option in add_ons),
                Decimal(0),
            )
            _validate_derived_decimal(
                configured,
                "configured_amount",
                MAX_CONFIGURED_AMOUNT,
                allow_zero=True,
            )
            converted = configured * exchange_rate
            _validate_derived_decimal(
                converted,
                "converted_unit_amount",
                MAX_CONVERTED_UNIT_AMOUNT,
                allow_zero=True,
            )
            unit_price = converted.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            _validate_derived_decimal(
                unit_price,
                "unit_price",
                MAX_CONVERTED_UNIT_AMOUNT,
                allow_zero=True,
            )
            raw_line_total = unit_price * quantity
            _validate_derived_decimal(
                raw_line_total,
                "line_total",
                MAX_LINE_TOTAL,
                allow_zero=True,
            )
            line_total = raw_line_total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            _validate_derived_decimal(
                line_total,
                "line_total",
                MAX_LINE_TOTAL,
                allow_zero=True,
            )
    except DecimalException:
        raise ValueError("calculo decimal derivado invalido") from None
    names = ([base_option["name"]] if base_option else []) + [option["name"] for option in add_ons]
    return {
        "internal_id": item["internal_id"],
        "supplier": item["supplier"],
        "product_key": item["product_key"],
        "sku": item["attributes"].get("source_code") or item["sku"],
        "code_status": item["code_status"],
        "brand": item["brand"],
        "collection": item["collection"],
        "name": item["name"],
        "description": item["description"],
        "unit": item["unit"],
        "availability_type": item["availability_type"],
        "stock": item["stock"],
        "lead_time": item["lead_time"],
        "quantity": _plain_decimal(quantity),
        "base_option_id": base_option["id"] if base_option else None,
        "add_on_option_ids": [option["id"] for option in add_ons],
        "configuration": "; ".join(names) or "Standard",
        "base_currency": item["base_currency"],
        "base_price": _fixed(base_price, 6),
        "unit_price_base": _fixed(configured, 6),
        "unit_price": _fixed(unit_price, 2),
        "line_total": _fixed(line_total, 2),
        "tax_rate": item["tax_rate"],
        "attributes": deepcopy(item["attributes"]),
        "image_url": item["image_url"],
        "image_kind": item["image_kind"],
        "product_url": item["product_url"],
        "warnings": _supplier_line_warnings(item),
        "source_reference": item["source_reference"],
    }


def _normalized_supplier_warning(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_marks = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def _supplier_line_warnings(item: dict[str, Any]) -> list[str]:
    canonical = "Codigo por verificar"
    canonical_key = _normalized_supplier_warning(canonical)
    review_line = item["code_status"] == "needs_review"
    result: list[str] = []
    seen: set[str] = set()
    for raw_warning in item["warnings"]:
        warning = str(raw_warning).strip()
        key = _normalized_supplier_warning(warning)
        if not warning or not key:
            continue
        if review_line and key == canonical_key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(warning)
    if review_line:
        if len(result) >= MAX_WARNINGS_PER_ITEM:
            raise ValueError("Se excede el limite de warnings al agregar codigo por verificar")
        result.append(canonical)
    return result


def _decimal_string(
    value: Any,
    field: str,
    *,
    positive: bool = False,
    minimum: Decimal | None = None,
    maximum: Decimal = MONEY_LIMIT,
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64:
        raise ValueError(f"{field} debe ser una cadena decimal")
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError(f"{field} invalido") from None
    if not number.is_finite():
        raise ValueError(f"{field} fuera de rango")
    decimal_places = max(-number.as_tuple().exponent, 0)
    if decimal_places > 6 or abs(number) > maximum:
        raise ValueError(f"{field} fuera de rango")
    if positive and number <= 0:
        raise ValueError(f"{field} debe ser positiva")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field} fuera de rango")
    return value.strip()


def _validate_derived_decimal(
    value: Decimal,
    field: str,
    maximum: Decimal,
    *,
    allow_zero: bool = False,
) -> None:
    if (
        not value.is_finite()
        or value < 0
        or (not allow_zero and value == 0)
        or value > maximum
    ):
        raise ValueError(f"{field} fuera de rango")


def _is_square_meter_unit(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(normalized.split())
    return normalized in {"m2", "m^2"}


def _quantity(value: Any, *, allow_fractional: bool = False) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise ValueError("cantidad invalida")
    if isinstance(value, str) and (not value.strip() or len(value.strip()) > 64):
        raise ValueError("cantidad invalida")
    try:
        number = value if isinstance(value, Decimal) else Decimal(value.strip())
    except (InvalidOperation, ValueError):
        raise ValueError("cantidad invalida") from None
    if not number.is_finite():
        raise ValueError("cantidad fuera de rango")
    _, digits, exponent = number.as_tuple()
    integer_digits = max(len(digits) + exponent, 1)
    fractional_digits = max(-exponent, 0)
    if (
        number <= 0
        or number > QUANTITY_LIMIT
        or integer_digits > 7
        or fractional_digits > 6
    ):
        raise ValueError("cantidad fuera de rango")
    if not allow_fractional and number != number.to_integral_value():
        raise ValueError("cantidad debe ser entera para esta unidad")
    return number


def _text(raw: dict[str, Any], field: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} debe ser texto")
    return _bounded_string(value, field, max_length)


def _required_text(raw: dict[str, Any], field: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    value = _text(raw, field, max_length)
    if not value:
        raise ValueError(f"{field} obligatorio")
    return value


def _bounded_string(value: str, field: str, max_length: int, *, required: bool = False) -> str:
    if len(value) > max_length:
        raise ValueError(f"{field} excede el limite de {max_length} caracteres")
    clean = value.strip()
    if required and not clean:
        raise ValueError(f"{field} obligatorio")
    return clean


def _browser_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} debe ser texto")
    return _bounded_string(value, field, MAX_IDENTIFIER_LENGTH, required=True)


def _optional_http_url(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} debe ser texto")
    if len(value) > MAX_URL_LENGTH:
        raise ValueError(f"{field} excede el limite de {MAX_URL_LENGTH} caracteres")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} contiene whitespace o caracteres de control")
    clean = value.strip()
    if not clean:
        return ""
    try:
        parsed = urlsplit(clean)
        parsed.port
    except ValueError:
        raise ValueError(f"{field} invalida") from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{field} debe ser una URL http(s) absoluta sin credenciales")
    _validate_url_hostname(parsed.hostname, field)
    return clean


def _validate_url_hostname(host: str, field: str) -> None:
    if "%" in host or "\\" in host:
        raise ValueError(f"{field} contiene un hostname invalido")
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    if all(character.isdigit() or character == "." for character in host):
        raise ValueError(f"{field} contiene una direccion IP invalida")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"{field} contiene un hostname invalido") from None
    if len(ascii_host.encode("ascii")) > 253:
        raise ValueError(f"{field} contiene un hostname demasiado largo")
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label.encode("ascii")) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not (character.isascii() and (character.isalnum() or character == "-")) for character in label)
        for label in labels
    ):
        raise ValueError(f"{field} contiene un hostname invalido")
    for label in labels:
        if not label.lower().startswith("xn--"):
            continue
        try:
            decoded = label.encode("ascii").decode("idna")
            round_trip = decoded.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError(f"{field} contiene un A-label IDNA invalido") from None
        if round_trip.lower() != label.lower():
            raise ValueError(f"{field} contiene un A-label IDNA no canonico")


def _validate_attributes(attributes: dict[str, Any]) -> None:
    def walk(value: Any, depth: int) -> None:
        if depth > MAX_ATTRIBUTES_DEPTH:
            raise ValueError(f"attributes excede la profundidad de {MAX_ATTRIBUTES_DEPTH}")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("attributes requiere claves de texto")
            for key, nested in value.items():
                _bounded_string(key, "attributes key", MAX_TEXT_LENGTH)
                walk(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, depth + 1)
        elif not (value is None or isinstance(value, (str, int, float, bool))):
            raise ValueError("attributes contiene un valor no JSON")

    walk(attributes, 0)
    try:
        encoded = json.dumps(
            attributes,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("attributes contiene un valor no JSON") from None
    if len(encoded) > MAX_ATTRIBUTES_JSON_BYTES:
        raise ValueError(f"attributes excede el limite de {MAX_ATTRIBUTES_JSON_BYTES} bytes")


def _bounded_metadata_string_utf8_size(
    value: str,
    maximum_bytes: int = MAX_METADATA_JSON_BYTES,
    *,
    encode_chunk=None,
) -> int:
    if type(value) is not str or type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ValueError("metadata contiene un string invalido")
    if len(value) > maximum_bytes:
        raise ValueError(f"metadata excede el limite de {maximum_bytes} bytes")
    encode_chunk = encode_chunk or (lambda chunk: chunk.encode("utf-8"))
    encoded_bytes = 0
    for offset in range(0, len(value), _METADATA_UTF8_CHUNK_CHARS):
        try:
            encoded = encode_chunk(value[offset:offset + _METADATA_UTF8_CHUNK_CHARS])
        except (UnicodeEncodeError, ValueError, OverflowError):
            raise ValueError("metadata contiene un string invalido") from None
        if type(encoded) is not bytes:
            raise ValueError("metadata contiene un string invalido")
        encoded_bytes += len(encoded)
        if encoded_bytes > maximum_bytes:
            raise ValueError(f"metadata excede el limite de {maximum_bytes} bytes")
    return encoded_bytes


def _bounded_metadata_integer_text_size(
    value: int,
    maximum_bytes: int = MAX_METADATA_JSON_BYTES,
    *,
    render_decimal=None,
) -> int:
    if type(value) is not int or type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ValueError("metadata contiene un entero invalido")
    sign_bytes = 1 if value < 0 else 0
    digit_upper_bound = (
        1 if value == 0 else (value.bit_length() * 30_103) // 100_000 + 1
    )
    if digit_upper_bound + sign_bytes > maximum_bytes:
        raise ValueError(f"metadata excede el limite de {maximum_bytes} bytes")
    render_decimal = render_decimal or str
    try:
        rendered = render_decimal(value)
    except (ValueError, OverflowError):
        raise ValueError("metadata contiene un entero invalido") from None
    if type(rendered) is not str or len(rendered) > maximum_bytes:
        raise ValueError(f"metadata excede el limite de {maximum_bytes} bytes")
    return len(rendered)


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("metadata debe ser un objeto JSON exacto")
    nodes = 0
    active: set[int] = set()

    def scalar_size(scalar: str | int) -> None:
        if type(scalar) is str:
            _bounded_metadata_string_utf8_size(scalar)
        else:
            _bounded_metadata_integer_text_size(scalar)

    def walk(current: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_METADATA_NODES:
            raise ValueError(
                f"metadata excede el limite de {MAX_METADATA_NODES} valores"
            )
        current_type = type(current)
        if current_type in {dict, list} and id(current) in active:
            raise ValueError("metadata contiene una referencia circular")
        if depth > MAX_METADATA_DEPTH:
            raise ValueError(
                f"metadata excede la profundidad de {MAX_METADATA_DEPTH}"
            )
        if current_type is dict:
            active.add(id(current))
            try:
                for key, nested in current.items():
                    if type(key) is not str:
                        raise ValueError("metadata requiere claves string exactas")
                    scalar_size(key)
                    walk(nested, depth + 1)
            finally:
                active.remove(id(current))
        elif current_type is list:
            active.add(id(current))
            try:
                for nested in current:
                    walk(nested, depth + 1)
            finally:
                active.remove(id(current))
        elif current_type is str:
            scalar_size(current)
        elif current_type is int:
            scalar_size(current)
        elif current_type is float:
            if current != current or current in {float("inf"), float("-inf")}:
                raise ValueError("metadata contiene un float no finito")
        elif current is not None:
            raise ValueError("metadata contiene un tipo JSON no permitido")

    walk(value, 0)
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).iterencode(value)
    encoded_bytes = 0
    while True:
        try:
            chunk = next(encoder)
        except StopIteration:
            break
        except (TypeError, ValueError, OverflowError):
            raise ValueError("metadata contiene un valor no JSON") from None
        try:
            encoded_bytes += len(chunk.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("metadata contiene un string invalido") from None
        if encoded_bytes > MAX_METADATA_JSON_BYTES:
            raise ValueError(
                f"metadata excede el limite de {MAX_METADATA_JSON_BYTES} bytes"
            )
    return deepcopy(value)


def _enum_text(raw: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = _required_text(raw, field)
    if value not in allowed:
        raise ValueError(f"{field} invalido")
    return value


def _currency(value: Any) -> str:
    if not isinstance(value, str) or value.strip().upper() not in ALLOWED_CURRENCIES:
        raise ValueError("Moneda invalida")
    return value.strip().upper()


def _base_currency(value: Any) -> str:
    if isinstance(value, str) and value.strip().upper() == UNKNOWN_BASE_CURRENCY:
        return UNKNOWN_BASE_CURRENCY
    return _currency(value)


def _sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError("source_hash invalido")
    return value


def _iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} invalida")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} invalida") from None


def _iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("retrieved_at invalido") from None
    if parsed.tzinfo is None:
        raise ValueError("retrieved_at invalido")
    return parsed


def _fixed(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}"


def _plain_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
