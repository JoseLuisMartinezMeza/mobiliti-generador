from __future__ import annotations

from decimal import Decimal, InvalidOperation
import unicodedata

from .mixed_catalog import MIXED_CATALOG_ORDER, preflight_mixed_catalog_items


MAX_SEARCH_QUERY_LENGTH = 160
MAX_SEARCH_LIMIT = 50
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def _fold(value: object) -> str:
    return " ".join(
        "".join(
            character
            for character in unicodedata.normalize("NFKD", str(value or ""))
            if not unicodedata.combining(character)
        ).casefold().split()
    )


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    clean = value.strip()
    if any(unicodedata.category(character) in _CONTROL_CATEGORIES for character in clean):
        return ""
    return clean


def _base_choices(raw: dict) -> list[dict]:
    options = raw.get("base_price_options")
    if not isinstance(options, list):
        return []
    choices = []
    seen = set()
    for option in options:
        if not isinstance(option, dict) or option.get("available") is False:
            continue
        option_id = _text(option.get("id"))
        if not option_id or option_id in seen:
            continue
        seen.add(option_id)
        choices.append({
            "id": option_id,
            "name": _text(option.get("name")) or option_id,
        })
    return choices


def _add_on_choices(raw: dict, base_choices: list[dict]) -> list[dict]:
    options = raw.get("add_on_options")
    if not isinstance(options, list):
        return []
    base_ids = {choice["id"] for choice in base_choices}
    choices = []
    seen = set()
    for option in options:
        if not isinstance(option, dict) or option.get("available") is False:
            continue
        option_id = _text(option.get("id"))
        family = _text(option.get("family"))
        if not option_id or not family or option_id in seen:
            continue
        compatible_raw = option.get("compatible_base_option_ids", [])
        compatible = []
        if isinstance(compatible_raw, list):
            compatible = [
                candidate
                for candidate in (_text(value) for value in compatible_raw)
                if candidate and (not base_ids or candidate in base_ids)
            ]
        seen.add(option_id)
        choices.append({
            "id": option_id,
            "name": _text(option.get("name")) or option_id,
            "family": family,
            "compatible_base_option_ids": compatible,
        })
    return choices


def _catalog_identity(catalog: str, raw: dict, base_choices: list[dict]) -> dict:
    if catalog == "tarkett":
        return {"code": _text(raw.get("code"))}
    if catalog == "offiho":
        return {"inventory_key": _text(raw.get("inventory_key"))}
    return {
        "internal_id": _text(raw.get("internal_id")),
        "base_option_id": base_choices[0]["id"] if len(base_choices) == 1 else "",
        "add_on_option_ids": [],
    }


def _non_positive(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return decimal.is_finite() and decimal <= 0


def _availability_label(raw: dict) -> str:
    if raw.get("is_out_of_stock") is True or _non_positive(raw.get("available_quantity")) or _non_positive(raw.get("stock")):
        return "Agotado"
    availability_type = _text(raw.get("availability_type"))
    if availability_type == "made_to_order":
        return "Fabricación por confirmar"
    if availability_type == "stocked" or raw.get("available_quantity") is not None or raw.get("stock") is not None:
        return "Disponible"
    return "Disponibilidad por confirmar"


def _availability_warnings(availability: str) -> list[str]:
    return {
        "Agotado": ["Producto agotado"],
        "Fabricación por confirmar": ["Fabricación por confirmar"],
        "Disponibilidad por confirmar": ["Disponibilidad por confirmar"],
    }.get(availability, [])


def _page_value(value: object, field: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{field} invalido")
    return value


def _search_query(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError("q invalido")
    if any(unicodedata.category(character) in _CONTROL_CATEGORIES for character in value):
        raise ValueError("q invalido")
    return _fold(value)


def _supplier(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("supplier invalido")
    clean = value.strip().lower()
    if clean not in MIXED_CATALOG_ORDER:
        raise ValueError("Catalogo no permitido")
    return clean


def _canonical_item(catalog: str, raw: dict) -> dict | None:
    base_choices = _base_choices(raw)
    add_on_choices = _add_on_choices(raw, base_choices)
    identity = _catalog_identity(catalog, raw, base_choices)
    try:
        preflight = preflight_mixed_catalog_items([
            {"catalog": catalog, **identity, "quantity": "1"}
        ])[0]
    except ValueError:
        return None
    identity = {
        key: value
        for key, value in preflight.items()
        if key not in {"catalog", "quantity"}
    }
    official_code = _text(raw.get("code")) or _text(raw.get("sku")) or _text(raw.get("internal_id"))
    if not official_code:
        return None
    name = _text(raw.get("name")) or official_code
    availability = _availability_label(raw)
    result = {
        "catalog": catalog,
        "official_code": official_code,
        "identity": identity,
        "snapshot": {
            "name": name,
            "code": official_code,
            "image_url": _text(raw.get("image_url")),
            "availability": availability,
            "configuration": base_choices[0]["name"] if len(base_choices) == 1 else "",
            "warnings": _availability_warnings(availability),
        },
    }
    if base_choices:
        result["base_options"] = base_choices
    if add_on_choices:
        result["add_on_options"] = add_on_choices
    return result


def search_catalog_products(catalogs, *, query, supplier, offset, limit) -> dict:
    """Busca snapshots publicados sin exponer datos comerciales del catálogo."""
    needle = _search_query(query)
    supplier = _supplier(supplier)
    offset = _page_value(offset, "offset", 0)
    limit = _page_value(limit, "limit", 1, MAX_SEARCH_LIMIT)
    if not isinstance(catalogs, dict):
        raise ValueError("Catalogos invalidos")

    rows: list[dict] = []
    for catalog in MIXED_CATALOG_ORDER:
        if supplier is not None and catalog != supplier:
            continue
        snapshot = catalogs.get(catalog)
        raw_items = snapshot.get("items") if isinstance(snapshot, dict) else None
        if not isinstance(raw_items, list):
            continue
        catalog_rows = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            haystack = _fold(
                f"{raw.get('code', '')} {raw.get('sku', '')} "
                f"{raw.get('name', '')} {raw.get('description', '')}"
            )
            if needle and needle not in haystack:
                continue
            item = _canonical_item(catalog, raw)
            if item is not None:
                catalog_rows.append(item)
        rows.extend(sorted(
            catalog_rows,
            key=lambda item: (
                _fold(item["official_code"]),
                _fold(item["snapshot"]["name"]),
                item["official_code"],
                item["snapshot"]["name"],
            ),
        ))

    total = len(rows)
    next_offset = offset + limit if offset + limit < total else None
    return {
        "items": rows[offset:offset + limit],
        "total": total,
        "next_offset": next_offset,
    }
