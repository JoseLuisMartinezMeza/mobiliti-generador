"""Validación y normalización del formato persistente de proyectos."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import re
import unicodedata
import uuid

from .mixed_catalog import MIXED_CATALOG_ORDER


PROJECT_SCHEMA_VERSION = 1
PROJECT_CURRENCIES = frozenset({"MXN", "USD", "EUR"})
PROJECT_ROLES = frozenset({"principal", "complement"})
COMPLEMENT_QUANTITY_MODES = frozenset({"per_parent_unit", "fixed_project"})
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
FORMULA_PREFIXES = frozenset({"=", "+", "-", "@"})
ASSET_KEY = re.compile(
    r"projects/(\d+)/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"(sources|images)/([A-Za-z0-9][A-Za-z0-9._ -]{0,255})\Z"
)

COMMON_LINE_FIELDS = frozenset({
    "line_id", "role", "section_id", "parent_line_id", "position", "quantity",
    "source", "official_code", "display_cache", "quantity_mode",
})
CATALOG_LINE_FIELDS = COMMON_LINE_FIELDS | frozenset({
    "catalog", "identity", "quantity_rules_cache",
})
IMPORTED_LINE_FIELDS = COMMON_LINE_FIELDS | frozenset({
    "import_id", "source_row", "source_currency", "provider", "name",
    "description", "dimension", "unit_price", "image_asset_key",
    "source_asset_key",
})
DISPLAY_CACHE_FIELDS = frozenset({"name", "code", "image_url"})


def _text(value: object, field: str, *, required: bool = True, limit: int = 2_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} inválido")
    result = value.strip()
    if (
        (required and not result)
        or len(result) > limit
        or CONTROL.search(result)
        or any(unicodedata.category(char) in {"Cf", "Cs"} for char in result)
    ):
        raise ValueError(f"{field} inválido")
    return result


def _imported_text(value: object, field: str, *, required: bool = True, limit: int = 2_000) -> str:
    result = _text(value, field, required=required, limit=limit)
    if result[:1] in FORMULA_PREFIXES:
        raise ValueError(f"{field} inválido")
    return result


def _uuid(value: object, field: str) -> str:
    text = _text(value, field, limit=36)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise ValueError(f"{field} inválido") from exc
    if parsed.version != 4 or str(parsed) != text.lower():
        raise ValueError(f"{field} inválido")
    return str(parsed)


def _positive_decimal(value: object, field: str) -> str:
    text = _text(value, field, limit=32)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} inválida") from exc
    if not number.is_finite() or number <= 0 or number > Decimal("1000000"):
        raise ValueError(f"{field} inválida")
    return format(number, "f")


def _nonnegative_decimal(value: object, field: str) -> str:
    text = _text(value, field, limit=32)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} inválida") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} inválida")
    return format(number, "f")


def normalized_match_key(provider: object, official_code: object) -> tuple[str, str] | None:
    """Devuelve la clave comparable de proveedor y código cuando ambos existen."""
    if not isinstance(provider, str) or not isinstance(official_code, str):
        return None
    clean_provider = " ".join(
        "".join(
            char for char in unicodedata.normalize("NFKD", provider)
            if not unicodedata.combining(char)
        ).casefold().split()
    )
    clean_code = official_code.strip().upper()
    return (clean_provider, clean_code) if clean_provider and clean_code else None


def normalize_project_payload(raw: object) -> dict:
    """Valida un objeto JSON de proyecto y devuelve su representación normalizada."""
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "quote_fields", "sections", "lines",
    }:
        raise ValueError("Proyecto inválido")
    if raw["schema_version"] != PROJECT_SCHEMA_VERSION:
        raise ValueError("Versión de Proyecto no soportada")

    sections = _normalize_sections(raw["sections"])
    lines = _normalize_lines(raw["lines"], sections)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "quote_fields": _normalize_quote_fields(raw["quote_fields"]),
        "sections": sections,
        "lines": lines,
    }


def _normalize_sections(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Secciones inválidas")

    sections: list[dict] = []
    ids: set[str] = set()
    positions: set[int] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"section_id", "concept", "position"}:
            raise ValueError("Sección inválida")
        section_id = _text(item["section_id"], "Sección", limit=64)
        position = item["position"]
        if (
            section_id in ids
            or type(position) is not int
            or position < 0
            or position in positions
        ):
            raise ValueError("Sección duplicada")
        ids.add(section_id)
        positions.add(position)
        sections.append({
            "section_id": section_id,
            "concept": _text(item["concept"], "Concepto", limit=120),
            "position": position,
        })
    if sorted(positions) != list(range(len(sections))):
        raise ValueError("Orden de secciones inválido")
    return sorted(sections, key=lambda item: item["position"])


def _normalize_lines(raw: object, sections: list[dict]) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("Líneas inválidas")

    section_ids = {item["section_id"] for item in sections}
    ids: set[str] = set()
    normalized: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Línea inválida")
        line_id = _uuid(item.get("line_id"), "line_id")
        if line_id in ids:
            raise ValueError("line_id duplicado")
        ids.add(line_id)

        role = item.get("role")
        if role not in PROJECT_ROLES:
            raise ValueError("Rol de línea inválido")
        source = item.get("source")
        if source == "catalog":
            line = _normalize_catalog_line(item, role, line_id)
        elif source == "imported":
            line = _normalize_imported_line(item, role, line_id)
        else:
            raise ValueError("Origen de línea inválido")

        _normalize_line_relationship(line, section_ids)
        normalized.append(line)

    by_id = {item["line_id"]: item for item in normalized}
    for item in normalized:
        if item["role"] == "complement":
            parent = by_id.get(item["parent_line_id"])
            if parent is None or parent["role"] != "principal":
                raise ValueError("Padre de complemento inválido")
    _validate_line_positions(normalized)
    return normalized


def _normalize_catalog_line(item: dict, role: str, line_id: str) -> dict:
    _validate_line_fields(item, CATALOG_LINE_FIELDS, role)
    catalog = item.get("catalog")
    if catalog not in MIXED_CATALOG_ORDER:
        raise ValueError("Catálogo inválido")

    line = _normalize_common_line(item, role, line_id)
    line["catalog"] = catalog
    line["identity"] = _normalize_catalog_identity(catalog, item.get("identity"))
    if "quantity_rules_cache" in item:
        if not isinstance(item["quantity_rules_cache"], dict):
            raise ValueError("Reglas de cantidad inválidas")
        line["quantity_rules_cache"] = dict(item["quantity_rules_cache"])
    return line


def _normalize_imported_line(item: dict, role: str, line_id: str) -> dict:
    _validate_line_fields(item, IMPORTED_LINE_FIELDS, role)
    line = _normalize_common_line(item, role, line_id)
    line.update({
        "import_id": _uuid(item.get("import_id"), "import_id"),
        "source_row": _positive_int(item.get("source_row"), "source_row"),
        "source_currency": _currency(item.get("source_currency"), "source_currency"),
        "provider": _imported_text(item.get("provider"), "provider", limit=500),
        "name": _imported_text(item.get("name"), "name", limit=500),
        "description": _imported_text(item.get("description"), "description", required=False, limit=2_000),
        "dimension": _imported_text(item.get("dimension"), "dimension", required=False, limit=500),
        "unit_price": _nonnegative_decimal(item.get("unit_price"), "unit_price"),
        "image_asset_key": _asset_key(item.get("image_asset_key"), "image_asset_key"),
        "source_asset_key": _asset_key(item.get("source_asset_key"), "source_asset_key"),
    })
    return line


def _validate_line_fields(item: dict, allowed: frozenset[str], role: str) -> None:
    allowed_for_role = allowed - {"quantity_mode"}
    if role == "complement":
        allowed_for_role = allowed
    unexpected = set(item) - allowed_for_role
    if unexpected:
        raise ValueError("Campo de línea no permitido")

    required = set(COMMON_LINE_FIELDS - {"quantity_mode"})
    if allowed is CATALOG_LINE_FIELDS:
        required.update({"catalog", "identity"})
    else:
        required.update(IMPORTED_LINE_FIELDS - COMMON_LINE_FIELDS)
    if role == "complement":
        required.add("quantity_mode")
    missing = required - set(item)
    if missing:
        raise ValueError("Campo de línea requerido")


def _normalize_common_line(item: dict, role: str, line_id: str) -> dict:
    text_normalizer = _imported_text if item["source"] == "imported" else _text
    line = {
        "line_id": line_id,
        "role": role,
        "section_id": item.get("section_id"),
        "parent_line_id": item.get("parent_line_id"),
        "position": _nonnegative_int(item.get("position"), "position"),
        "quantity": _positive_decimal(item.get("quantity"), "Cantidad"),
        "source": item["source"],
        "official_code": text_normalizer(
            item.get("official_code"),
            "official_code",
            required=item["source"] != "imported",
            limit=500,
        ),
        "display_cache": _normalize_display_cache(item.get("display_cache")),
    }
    if role == "complement":
        quantity_mode = item.get("quantity_mode")
        if quantity_mode not in COMPLEMENT_QUANTITY_MODES:
            raise ValueError("Complemento inválido")
        line["quantity_mode"] = quantity_mode
    return line


def _normalize_catalog_identity(catalog: str, raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Identidad de catálogo inválida")
    if catalog == "tarkett":
        required = {"code"}
    elif catalog == "offiho":
        required = {"inventory_key"}
    else:
        required = {"internal_id", "base_option_id", "add_on_option_ids"}
    if set(raw) != required:
        raise ValueError("Identidad de catálogo inválida")
    if catalog == "tarkett":
        return {"code": _text(raw["code"], "code", limit=1_000)}
    if catalog == "offiho":
        return {"inventory_key": _text(raw["inventory_key"], "inventory_key", limit=1_000)}

    add_ons = raw["add_on_option_ids"]
    if not isinstance(add_ons, list) or len(add_ons) > 200:
        raise ValueError("add_on_option_ids inválido")
    normalized_add_ons = [_text(value, "add_on_option_ids", limit=500) for value in add_ons]
    if len(normalized_add_ons) != len(set(normalized_add_ons)):
        raise ValueError("add_on_option_ids duplicado")
    return {
        "internal_id": _text(raw["internal_id"], "internal_id", limit=1_000),
        "base_option_id": _text(raw["base_option_id"], "base_option_id", required=False, limit=500),
        "add_on_option_ids": sorted(normalized_add_ons),
    }


def _normalize_line_relationship(line: dict, section_ids: set[str]) -> None:
    if line["role"] == "principal":
        if line["section_id"] not in section_ids or line["parent_line_id"] is not None:
            raise ValueError("Principal fuera de sección")
        return
    if line["section_id"] is not None:
        raise ValueError("Complemento inválido")
    line["parent_line_id"] = _uuid(line["parent_line_id"], "parent_line_id")


def _validate_line_positions(lines: list[dict]) -> None:
    principal_positions: dict[str, set[int]] = {}
    complement_positions: dict[str, set[int]] = {}
    for line in lines:
        groups = principal_positions if line["role"] == "principal" else complement_positions
        group_id = line["section_id"] if line["role"] == "principal" else line["parent_line_id"]
        positions = groups.setdefault(group_id, set())
        if line["position"] in positions:
            raise ValueError("Posición de línea duplicada")
        positions.add(line["position"])
    for positions in (*principal_positions.values(), *complement_positions.values()):
        if sorted(positions) != list(range(len(positions))):
            raise ValueError("Posición de línea inválida")


def _normalize_display_cache(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != DISPLAY_CACHE_FIELDS:
        raise ValueError("display_cache inválido")
    return {
        "name": _text(raw["name"], "display_cache.name", required=False, limit=500),
        "code": _text(raw["code"], "display_cache.code", required=False, limit=500),
        "image_url": _text(raw["image_url"], "display_cache.image_url", required=False, limit=2_000),
    }


def _asset_key(value: object, field: str) -> str:
    text = _text(value, field, required=False, limit=500)
    if not text:
        return text
    if ".." in text or "\\" in text or "://" in text:
        raise ValueError(f"{field} inválido")
    match = ASSET_KEY.fullmatch(text)
    if match is None:
        raise ValueError(f"{field} inválido")
    _uuid(match.group(2), field)
    return text


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} inválido")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} inválido")
    return value


def _currency(value: object, field: str) -> str:
    currency = _text(value, field, limit=3).upper()
    if currency not in PROJECT_CURRENCIES:
        raise ValueError(f"{field} inválida")
    return currency


def _normalize_quote_fields(raw: object) -> dict:
    required = (
        "proyecto", "cliente", "correo", "telefono", "direccion", "razon_social",
        "quote_currency", "descuento",
    )
    if not isinstance(raw, dict) or set(raw) != set(required):
        raise ValueError("Datos de cotización inválidos")
    result = {
        field: _text(raw[field], field, required=False, limit=500)
        for field in required
    }
    result["quote_currency"] = _currency(result["quote_currency"], "quote_currency")
    try:
        discount = Decimal(result["descuento"])
    except InvalidOperation as exc:
        raise ValueError("Descuento inválido") from exc
    if not discount.is_finite() or not Decimal("0") <= discount <= Decimal("100"):
        raise ValueError("Descuento inválido")
    return result


def project_summary(payload: Mapping[str, object]) -> dict[str, int]:
    """Resume secciones y líneas físicas por rol en un proyecto normalizado."""
    lines = payload["lines"]
    return {
        "sections": len(payload["sections"]),
        "principals": sum(item["role"] == "principal" for item in lines),
        "complements": sum(item["role"] == "complement" for item in lines),
    }


def project_physical_line_count(payload: Mapping[str, object]) -> int:
    """Cuenta las filas físicas persistidas, sin expandir complementos."""
    return len(payload["lines"])
