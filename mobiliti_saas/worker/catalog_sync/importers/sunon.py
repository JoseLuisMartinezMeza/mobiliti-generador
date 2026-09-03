from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Mapping

from mobiliti_saas.quote_engine.sunon_image_provider import (
    SUNON_CATALOG_PATH,
    extract_product_code,
    find_sunon_catalog_match,
    normalize_sunon_code,
)
from mobiliti_saas.quote_engine.supplier_catalog import load_supplier_catalog_data

from .common import (
    CellRef,
    ImageAsset,
    extract_xlsx_images_from_bytes,
    neutralize_spreadsheet_text,
    open_xlsx_data_only_from_bytes,
    read_validated_source,
    source_ref,
)


@dataclass(frozen=True)
class SunonAssetBinding:
    internal_id: str
    asset_sha256: str
    object_name: str
    image_kind: Literal["official"]
    match_status: Literal["exact_xlsx", "merged_xlsx"]
    source_references: tuple[dict, ...]


@dataclass(frozen=True)
class SunonSnapshotBuild:
    snapshot: dict
    assets_by_sha256: Mapping[str, ImageAsset]
    bindings: tuple[SunonAssetBinding, ...]


_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SPEC_PATH = "SPEC GUIDES 2026/SUNON MTY/Spec guide-Sunon MTY-2026.xlsx"
_CHAIRS_PATH = "SUNON MTY/2026 updated price-Chairs _ Mexico Stock Reserves \uff084-6 weeks).xlsx"
_FAST_PATH = "SUNON MTY/2026 updated price-Fast inventory(1-2 Weeks) 02-09.xlsx"
_RAW_PATH = (
    "SUNON MTY/2026 updated price-Raw material preparation \u2605 Mexican inventory list "
    "\uff084-6 weeks).xlsx"
)
_MALL_PATH = "SUNON MTY/INVENTORY MALL 1 \uff084-6weeks).xlsx"
_EXPECTED_KINDS = {
    _SPEC_PATH: "spec_guide",
    _CHAIRS_PATH: "inventory",
    _FAST_PATH: "inventory",
    _RAW_PATH: "inventory",
    _MALL_PATH: "inventory",
}
_SPEC_SHEETS = {"SPEC Sunon Mty", "Costo Sunon Mty"}
_INVENTORIES = {
    _CHAIRS_PATH: {
        "all_sheets": {"Raw material inventory for Chai"},
        "product_sheets": ("Raw material inventory for Chai",),
        "header_row": 3,
        "fast": False,
        "lead_time": "4-6 semanas",
    },
    _FAST_PATH: {
        "all_sheets": {
            "The 1st and 2nd batch inventory",
            "The 3rd batch",
            "The 4th batch",
        },
        "product_sheets": (
            "The 1st and 2nd batch inventory",
            "The 3rd batch",
            "The 4th batch",
        ),
        "header_row": 5,
        "fast": True,
        "lead_time": "1-2 semanas",
    },
    _RAW_PATH: {
        "all_sheets": {
            "Available Color Option",
            "Mall",
            "Mandis",
            "Universal Table",
            "M Cabinet",
            "Total",
        },
        "product_sheets": ("Mall", "Mandis", "Universal Table", "M Cabinet"),
        "header_row": 3,
        "fast": False,
        "lead_time": "4-6 semanas",
    },
    _MALL_PATH: {
        "all_sheets": {"Quotation (2)", "Quotation"},
        "product_sheets": ("Quotation (2)",),
        "header_row": 3,
        "fast": False,
        "lead_time": "4-6 semanas",
    },
}
_DIRECT_PRICE_HEADING = "Unit Price in CET Usa Dollar"
_LEAD_ORDER = {"1-2 semanas": 0, "4-6 semanas": 1}
_IMAGE_SOURCE_PRECEDENCE = {
    path: index
    for index, path in enumerate((_CHAIRS_PATH, _FAST_PATH, _RAW_PATH, _MALL_PATH))
}


def _plain(value) -> str:
    return re.sub(r"\s+", " ", neutralize_spreadsheet_text(value)).strip()


def _fold(value) -> str:
    text = unicodedata.normalize("NFKD", _plain(value))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W_]+", text.casefold(), re.UNICODE))


def _code(value) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    return normalize_sunon_code(_plain(value))


def _model_and_name(value) -> tuple[str, str, str]:
    text = neutralize_spreadsheet_text(value).strip()
    if not text:
        return "", "", ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    extracted = extract_product_code(text)
    raw_code = extracted or lines[0]
    model = normalize_sunon_code(raw_code)
    code_line = 0
    if extracted:
        for index, line in enumerate(lines):
            if model and model in normalize_sunon_code(line):
                code_line = index
                break
    name = " ".join(lines[code_line + 1 :]).strip()
    return model, name or _plain(text) or model, _plain(raw_code)


def _number(value, *, positive: bool, maximum: Decimal) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if (
        not number.is_finite()
        or number > maximum
        or (number <= 0 if positive else number < 0)
        or max(-number.as_tuple().exponent, 0) > 6
    ):
        return None
    return number


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _is_serial(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value) and value > 0
    return isinstance(value, str) and bool(re.fullmatch(r"\s*\d+(?:\.0+)?\s*", value))


def _source_hash(files) -> str:
    source_material = "\n".join(
        f"{row.path}\0{row.kind}\0{row.sha256}"
        for row in sorted(files, key=lambda value: (value.path, value.kind, value.sha256))
    )
    try:
        catalog_digest = hashlib.sha256(SUNON_CATALOG_PATH.read_bytes()).hexdigest()
    except OSError:
        raise ValueError("SUNON_CATALOG_INDEX") from None
    material = f"{source_material}\nsunon_catalog\0{catalog_digest}"
    return hashlib.sha256(material.encode()).hexdigest()


def _validated_bundle(files):
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("SUNON_BUNDLE") from None
    if len(rows) != len(_EXPECTED_KINDS):
        raise ValueError("SUNON_BUNDLE")

    by_path = {}
    source_data = {}
    for row in rows:
        logical_path = getattr(row, "path", None)
        kind = getattr(row, "kind", None)
        local_path = getattr(row, "local_path", None)
        declared_hash = getattr(row, "sha256", None)
        if (
            logical_path not in _EXPECTED_KINDS
            or logical_path in by_path
            or kind != _EXPECTED_KINDS.get(logical_path)
            or getattr(row, "mime_type", None) != _MIME
            or not isinstance(local_path, Path)
            or local_path.suffix.lower() != ".xlsx"
            or not isinstance(declared_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
        ):
            raise ValueError("SUNON_BUNDLE")
        validated, data = read_validated_source(local_path, ".xlsx")
        if validated.sha256 != declared_hash:
            raise ValueError("SUNON_HASH")
        by_path[logical_path] = row
        source_data[logical_path] = data
    if set(by_path) != set(_EXPECTED_KINDS):
        raise ValueError("SUNON_BUNDLE")
    return by_path, source_data


def _image_metadata(data):
    images = extract_xlsx_images_from_bytes(data)
    return {
        reference: {
            "sha256": image.sha256,
            "width": image.width,
            "height": image.height,
        }
        for reference, image in images.items()
    }


def _inventory_assets(source_data):
    assets = {}
    for path in (_CHAIRS_PATH, _FAST_PATH, _RAW_PATH, _MALL_PATH):
        for asset in extract_xlsx_images_from_bytes(source_data[path]).values():
            assets.setdefault(asset.sha256, asset)
    return assets


def _require_sheets(workbook, expected):
    if set(workbook.sheetnames) != set(expected) or len(workbook.sheetnames) != len(expected):
        raise ValueError("SUNON_SHEETS")


def _require_headers(sheet, row, expected):
    if any(_fold(sheet.cell(row, column).value) != _fold(heading) for column, heading in expected.items()):
        raise ValueError("SUNON_HEADER")


def _spec_records(source, data):
    images = _image_metadata(data)
    workbook = open_xlsx_data_only_from_bytes(data)
    try:
        _require_sheets(workbook, _SPEC_SHEETS)
        sheet = workbook["SPEC Sunon Mty"]
        _require_headers(
            sheet,
            8,
            {1: "Imagen", 2: "Cod", 3: "Descripcion", 5: "Color"},
        )
        grouped = defaultdict(dict)
        for row_number in range(9, sheet.max_row + 1):
            model, name, _ = _model_and_name(sheet.cell(row_number, 2).value)
            if not model:
                continue
            image = images.get(CellRef(sheet.title, f"A{row_number}"))
            description = _plain(sheet.cell(row_number, 3).value)
            color = _plain(sheet.cell(row_number, 5).value)
            reference = source_ref(source.sha256, sheet.title, f"B{row_number}:E{row_number}")
            canonical = (
                _fold(name),
                _fold(description),
                _fold(color),
                image["sha256"] if image else "",
            )
            existing = grouped[model].get(canonical)
            if existing is None:
                grouped[model][canonical] = {
                    "name": name,
                    "description": description,
                    "color": color,
                    "image": image,
                    "refs": [reference],
                }
            else:
                existing["refs"].append(reference)
        return {
            model: [records[key] for key in sorted(records)]
            for model, records in grouped.items()
        }
    finally:
        workbook.close()


def _inventory_columns(fast):
    if fast:
        return {
            "erp": 2,
            "model": 3,
            "image": 4,
            "description": 5,
            "dimensions": 6,
            "color": 7,
            "quantity": 8,
            "price": 11,
        }
    return {
        "erp": None,
        "model": 2,
        "image": 3,
        "description": 4,
        "dimensions": 5,
        "color": 6,
        "quantity": 7,
        "price": 10,
    }


def _inventory_headers(fast):
    if fast:
        return {
            2: "ERP CODE",
            3: "Item Name",
            4: "Photo",
            5: "Description",
            6: "Dimension",
            7: "Color",
            8: "Q'ty",
        }
    return {
        2: "Item Name",
        3: "Photo",
        4: "Description",
        5: "Dimension",
        6: "Color",
        7: "Q'ty",
    }


def _inventory_records(source, data, config):
    images = _image_metadata(data)
    workbook = open_xlsx_data_only_from_bytes(data)
    records = []
    try:
        _require_sheets(workbook, config["all_sheets"])
        columns = _inventory_columns(config["fast"])
        header_row = config["header_row"]
        for sheet_name in config["product_sheets"]:
            sheet = workbook[sheet_name]
            _require_headers(sheet, header_row, _inventory_headers(config["fast"]))
            price_header_ok = _fold(sheet.cell(header_row, columns["price"]).value) == _fold(
                _DIRECT_PRICE_HEADING
            )
            deduplicated = {}
            for row_number, values in enumerate(
                sheet.iter_rows(
                    min_row=header_row + 1,
                    max_col=columns["price"],
                    values_only=True,
                ),
                header_row + 1,
            ):
                if not _is_serial(values[0]):
                    continue
                model, name, model_lookup = _model_and_name(values[columns["model"] - 1])
                if not model:
                    continue
                erp = _code(values[columns["erp"] - 1]) if columns["erp"] else ""
                description = _plain(values[columns["description"] - 1])
                dimensions = _plain(values[columns["dimensions"] - 1])
                color = _plain(values[columns["color"] - 1])
                quantity = _number(
                    values[columns["quantity"] - 1],
                    positive=False,
                    maximum=Decimal("1000000"),
                )
                price = _number(
                    values[columns["price"] - 1],
                    positive=True,
                    maximum=Decimal("1000000000"),
                )
                quantity_problem = quantity is None
                price_problem = "header" if not price_header_ok else "value" if price is None else ""
                quantity = quantity if quantity is not None else Decimal(0)
                reference = source_ref(
                    source.sha256,
                    sheet.title,
                    f"B{row_number}:{'K' if config['fast'] else 'J'}{row_number}",
                )
                image = images.get(
                    CellRef(
                        sheet.title,
                        f"{'D' if config['fast'] else 'C'}{row_number}",
                    )
                )
                image_reference = (
                    source_ref(
                        source.sha256,
                        sheet.title,
                        f"{'D' if config['fast'] else 'C'}{row_number}",
                    )
                    if image is not None
                    else None
                )
                canonical = (
                    erp,
                    model,
                    _fold(name),
                    _fold(description),
                    _fold(dimensions),
                    _fold(color),
                    _decimal_text(quantity),
                    _decimal_text(price) if price is not None else _plain(values[columns["price"] - 1]),
                    price_problem,
                    quantity_problem,
                )
                existing = deduplicated.get(canonical)
                if existing is not None:
                    existing["refs"].append(reference)
                    if image is not None:
                        candidate = existing["images"].setdefault(
                            image["sha256"],
                            {**image, "origins": []},
                        )
                        candidate["origins"].append({
                            "source_priority": _IMAGE_SOURCE_PRECEDENCE[source.path],
                            "source_reference": image_reference,
                        })
                    continue
                deduplicated[canonical] = {
                    "erp": erp,
                    "model": model,
                    "model_lookup": model_lookup,
                    "name": name,
                    "description": description,
                    "dimensions": dimensions,
                    "dimensions_key": _fold(dimensions),
                    "color": color,
                    "color_key": _fold(color),
                    "quantity": quantity,
                    "quantity_problem": quantity_problem,
                    "price": price,
                    "price_problem": price_problem,
                    "bucket": (source.path, source.sha256, sheet.title),
                    "lead_time": config["lead_time"],
                    "refs": [reference],
                    "images": {
                        image["sha256"]: {
                            **image,
                            "origins": [{
                                "source_priority": _IMAGE_SOURCE_PRECEDENCE[source.path],
                                "source_reference": image_reference,
                            }],
                        }
                    } if image is not None else {},
                }
            records.extend(deduplicated[key] for key in sorted(deduplicated))
    finally:
        workbook.close()
    return records


def _signature(record):
    return record["model"], record["dimensions_key"], record["color_key"], _fold(record["name"])


def _group_inventory(records):
    explicit = defaultdict(list)
    explicit_by_signature = defaultdict(set)
    uncoded = []
    for record in records:
        if record["erp"]:
            signature = _signature(record)
            explicit[(record["erp"], signature)].append(record)
            explicit_by_signature[signature].add(record["erp"])
        else:
            uncoded.append(record)
    remaining = defaultdict(list)
    for record in uncoded:
        signature = _signature(record)
        candidates = explicit_by_signature.get(signature, set())
        if len(candidates) == 1:
            explicit[(next(iter(candidates)), signature)].append(record)
        else:
            remaining[signature].append(record)
    groups = [
        {"erp": erp, "records": explicit[(erp, signature)]}
        for erp, signature in sorted(explicit)
    ]
    groups.extend(
        {"erp": "", "records": remaining[signature]}
        for signature in sorted(remaining)
    )
    return groups


def _record_sort_key(record):
    return (
        record["model"],
        record["model_lookup"],
        record["dimensions_key"],
        record["color_key"],
        _fold(record["name"]),
        _fold(record["description"]),
        json.dumps(record["refs"][0], sort_keys=True, separators=(",", ":")),
    )


def _spec_match(records, name):
    if not records:
        return None, False
    named = [record for record in records if _fold(record["name"]) == _fold(name)]
    candidates = named or records
    return (candidates[0], False) if len(candidates) == 1 else (None, True)


def _unique_refs(refs):
    encoded = {
        json.dumps(reference, sort_keys=True, separators=(",", ":"), ensure_ascii=True): reference
        for reference in refs
    }
    return [encoded[key] for key in sorted(encoded)]


def _availability(records):
    grouped = {}
    for record in records:
        bucket = grouped.setdefault(
            record["bucket"],
            {"lead_time": record["lead_time"], "quantity": Decimal(0), "refs": []},
        )
        bucket["quantity"] += record["quantity"]
        bucket["refs"].extend(record["refs"])
    buckets = []
    for key, bucket in sorted(
        grouped.items(),
        key=lambda row: (_LEAD_ORDER[row[1]["lead_time"]], row[0][1], row[0][2], row[0][0]),
    ):
        buckets.append(
            {
                "lead_time": bucket["lead_time"],
                "quantity": _decimal_text(bucket["quantity"]),
                "source_refs": _unique_refs(bucket["refs"]),
            }
        )
    stock = sum((Decimal(bucket["quantity"]) for bucket in buckets), Decimal(0))
    nonzero = [bucket for bucket in buckets if Decimal(bucket["quantity"]) > 0]
    lead_source = nonzero or buckets
    lead_time = min(lead_source, key=lambda bucket: _LEAD_ORDER[bucket["lead_time"]])["lead_time"]
    return stock, lead_time, buckets


def _price(records, blocked, warnings):
    if blocked:
        warnings.append("Atributos conflictivos para el mismo ERP; codigo y precio requieren revision.")
        return Decimal(0)
    if any(record["price_problem"] == "header" for record in records):
        warnings.append("Encabezado o moneda del precio USD directo no verificable; precio bloqueado.")
        return Decimal(0)
    if any(record["price_problem"] == "value" for record in records):
        warnings.append("Precio USD directo faltante o invalido; precio bloqueado.")
        return Decimal(0)
    prices = {record["price"] for record in records if record["price"] is not None}
    if len(prices) != 1:
        warnings.append("Precios USD directos conflictivos para la variante; precio bloqueado.")
        return Decimal(0)
    return next(iter(prices))


def _image_origin_key(origin):
    reference = origin["source_reference"]
    return (
        origin["source_priority"],
        str(reference["sheet_or_page"]),
        str(reference["cell_or_bbox"]),
        str(reference["file_id"]),
    )


def _image_candidate_metadata(image):
    origin = min(image["origins"], key=_image_origin_key)
    return {
        "sha256": image["sha256"],
        "width": image["width"],
        "height": image["height"],
        "source_priority": origin["source_priority"],
        "selected_source_reference": origin["source_reference"],
        "source_references": _unique_refs(
            candidate["source_reference"] for candidate in image["origins"]
        ),
    }


def _embedded_images(records, attributes, warnings):
    images = {}
    for record in records:
        for sha256, image in record["images"].items():
            candidate = images.setdefault(sha256, {**image, "origins": []})
            candidate["origins"].extend(image["origins"])
    ordered = sorted(
        (_image_candidate_metadata(image) for image in images.values()),
        key=lambda image: (
            image["source_priority"],
            *_image_origin_key({
                "source_priority": image["source_priority"],
                "source_reference": image["selected_source_reference"],
            })[1:],
            image["sha256"],
        ),
    )
    if len(ordered) == 1:
        image = ordered[0]
        attributes.update(
            embedded_image_sha256=image["sha256"],
            embedded_image_width=image["width"],
            embedded_image_height=image["height"],
            embedded_image_origin="inventory",
            embedded_image_source_priority=image["source_priority"],
            embedded_image_selected_source_reference=image["selected_source_reference"],
            embedded_image_source_references=image["source_references"],
        )
    elif ordered:
        attributes["embedded_images"] = ordered
        warnings.append("Hay varias imagenes embebidas exactas para la variante; revisar referencia visual.")
    return bool(ordered)


def _catalog_image(code_status, sku, lookups, attributes, warnings):
    exact_match = None
    reference_match = None
    for lookup in dict.fromkeys(lookups):
        entry, matched_code, match_type = find_sunon_catalog_match(lookup)
        if entry is None:
            continue
        match = (entry, matched_code, match_type, lookup)
        if reference_match is None:
            reference_match = match
        if match_type == "exact_code" and entry.get("confidence") == "exact_code":
            exact_match = match
            break

    product_url = ""
    image_url = ""
    if exact_match is not None:
        entry, matched_code, _match_type, lookup = exact_match
        product_url = str(entry.get("product_url") or "").strip()
        if product_url:
            attributes["product_url_match"] = {
                "status": "exact_code",
                "matched_code": str(matched_code or ""),
                "lookup_code": str(lookup or ""),
            }
        if (
            code_status == "verified"
            and normalize_sunon_code(matched_code) == normalize_sunon_code(sku)
        ):
            image_url = str(entry.get("image_url") or "").strip()
        if not image_url and str(entry.get("image_url") or "").strip():
            reference_match = exact_match

    if reference_match is not None and not image_url:
        entry, matched_code, match_type, _lookup = reference_match
        attributes["catalog_image_reference"] = {
            "matched_code": str(matched_code or ""),
            "match_type": str(match_type or ""),
            "image_url": str(entry.get("image_url") or "").strip(),
            "product_url": str(entry.get("product_url") or "").strip(),
        }
        if str(entry.get("image_url") or "").strip():
            warnings.append("Imagen de catalogo disponible solo como referencia de modelo/base; no es variante exacta.")
    return image_url, "official" if image_url else "placeholder", product_url


def _candidate_codes(groups, model_groups, specs):
    candidates = []
    for group in groups:
        records = group["records"]
        signatures = {_signature(record) for record in records}
        representative = min(records, key=_record_sort_key)
        if group["erp"] and len(signatures) == 1:
            code = group["erp"]
        elif (
            not group["erp"]
            and representative["model"] in specs
            and len(model_groups[representative["model"]]) == 1
        ):
            code = representative["model"]
        else:
            code = ""
        candidates.append(code)
    return Counter(code.casefold() for code in candidates if code), candidates


def _item(group, code_counts, candidate_code, specs):
    records = group["records"]
    representative = min(records, key=_record_sort_key)
    signatures = {_signature(record) for record in records}
    attribute_conflict = bool(group["erp"] and len(signatures) > 1)
    duplicate_code = bool(candidate_code and code_counts[candidate_code.casefold()] > 1)
    verified = bool(candidate_code and not attribute_conflict and not duplicate_code)
    code_status = "verified" if verified else "needs_review"
    sku = candidate_code if verified else ""
    model = representative["model"]
    model_lookup = representative["model_lookup"]
    identity = "\0".join((group["erp"], *_signature(representative)))
    if verified and group["erp"]:
        internal_id = f"sunon:erp:{sku.casefold()}"
    elif verified:
        internal_id = f"sunon:model:{sku.casefold()}"
    else:
        internal_id = "sunon:variant:" + hashlib.sha256(identity.encode()).hexdigest()[:20]

    warnings = []
    if not group["erp"] and not verified:
        warnings.append("El codigo de modelo identifica varias variantes; SKU requiere revision.")
    if duplicate_code:
        warnings.append("Codigo comercial duplicado entre variantes; codigo y precio requieren revision.")
    if any(record["quantity_problem"] for record in records):
        warnings.append("Cantidad faltante, negativa o invalida; se uso cero.")

    stock, lead_time, buckets = _availability(records)
    attributes = {
        "source_model_code": model_lookup,
        "dimensions": representative["dimensions"],
        "color": representative["color"],
        "availability_buckets": buckets,
    }
    if group["erp"]:
        attributes["source_erp_code"] = group["erp"]
    price = _price(records, attribute_conflict or duplicate_code, warnings)
    has_inventory_image = _embedded_images(records, attributes, warnings)

    selected_spec, ambiguous_spec = _spec_match(specs.get(model, []), representative["name"])
    evidence = [reference for record in records for reference in record["refs"]]
    name = representative["name"]
    description = representative["description"]
    if selected_spec is not None:
        name = selected_spec["name"] or name
        description = selected_spec["description"] or description
        attributes["spec_color"] = selected_spec["color"]
        evidence.extend(selected_spec["refs"])
        if selected_spec["image"] is not None and not has_inventory_image:
            image = selected_spec["image"]
            attributes.update(
                reference_image_sha256=image["sha256"],
                reference_image_width=image["width"],
                reference_image_height=image["height"],
                reference_image_origin="spec_model",
            )
            warnings.append("Imagen embebida de spec guide conservada solo como referencia de modelo.")
    elif ambiguous_spec:
        warnings.append("Spec guide contiene varias filas no distinguibles para el modelo; se conservo descripcion de inventario.")
    else:
        warnings.append("Modelo no encontrado en spec guide; se conservo descripcion de inventario.")

    image_lookups = ([sku] if verified and group["erp"] else []) + [model_lookup, model]
    image_url, image_kind, product_url = _catalog_image(
        code_status, sku, image_lookups, attributes, warnings
    )
    warnings = list(dict.fromkeys(warnings))
    return {
        "internal_id": internal_id,
        "supplier": "sunon",
        "product_key": model.casefold(),
        "sku": sku,
        "code_status": code_status,
        "brand": "Sunon",
        "collection": "",
        "name": name or model,
        "description": description,
        "unit": "PZA",
        "availability_type": "stocked",
        "stock": _decimal_text(stock),
        "lead_time": lead_time,
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": "USD",
        "price_net": f"{price:.6f}",
        "tax_rate": "0.160000",
        "attributes": attributes,
        "image_url": image_url,
        "image_kind": image_kind,
        "product_url": product_url,
        "warnings": warnings,
        "source_reference": json.dumps(
            _unique_refs(evidence),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    }


def _bind_inventory_assets(items, available_assets):
    assets = {}
    bindings = []
    for item in items:
        attributes = item["attributes"]
        candidates = []
        sha256 = attributes.get("embedded_image_sha256")
        if isinstance(sha256, str):
            candidates.append({
                "sha256": sha256,
                "source_priority": attributes["embedded_image_source_priority"],
                "selected_source_reference": attributes["embedded_image_selected_source_reference"],
            })
        for image in attributes.get("embedded_images", []):
            if isinstance(image, dict) and isinstance(image.get("sha256"), str):
                candidates.append(image)
        if not candidates:
            continue
        if any(candidate["sha256"] not in available_assets for candidate in candidates):
            raise ValueError("SUNON_IMAGE")
        selected_candidate = min(
            candidates,
            key=lambda image: _image_origin_key({
                "source_priority": image["source_priority"],
                "source_reference": image["selected_source_reference"],
            }) + (image["sha256"],),
        )
        selected = selected_candidate["sha256"]
        status = "exact_xlsx" if len(candidates) == 1 else "merged_xlsx"
        references = (selected_candidate["selected_source_reference"],)
        reference = references[0]
        item["image_url"] = ""
        item["image_kind"] = "official"
        attributes["image_match"] = {
            "status": status,
            "asset_sha256": selected,
            "source_references": list(references),
            "selection_reason": (
                f"inventory_precedence={selected_candidate['source_priority']};"
                f"{reference['sheet_or_page']}:{reference['cell_or_bbox']}"
            ),
        }
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": f"{selected}.png",
            "image_kind": "official",
            "label": "Imagen oficial del XLSX SUNON",
            "approved": True,
        }
        assets[selected] = available_assets[selected]
        bindings.append(
            SunonAssetBinding(
                item["internal_id"], selected, f"{selected}.png", "official", status, references
            )
        )
    return assets, tuple(sorted(bindings, key=lambda binding: binding.internal_id))


def _build_sunon(files, *, include_assets: bool):
    bundle, source_data = _validated_bundle(files)
    specs = _spec_records(bundle[_SPEC_PATH], source_data[_SPEC_PATH])
    records = []
    for path in (_CHAIRS_PATH, _FAST_PATH, _RAW_PATH, _MALL_PATH):
        records.extend(
            _inventory_records(bundle[path], source_data[path], _INVENTORIES[path])
        )
    if not records:
        raise ValueError("SUNON_INVENTORY_EMPTY")

    groups = _group_inventory(records)
    model_groups = defaultdict(set)
    for index, group in enumerate(groups):
        for model in {record["model"] for record in group["records"]}:
            model_groups[model].add(index)
    code_counts, candidates = _candidate_codes(groups, model_groups, specs)
    items = [
        _item(group, code_counts, candidates[index], specs)
        for index, group in enumerate(groups)
    ]
    items.sort(key=lambda item: item["internal_id"])
    snapshot = {
        "supplier": "sunon",
        "source_hash": _source_hash(tuple(bundle.values())),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
    }
    load_supplier_catalog_data(snapshot, expected_supplier="sunon")
    if include_assets:
        assets, bindings = _bind_inventory_assets(items, _inventory_assets(source_data))
        load_supplier_catalog_data(snapshot, expected_supplier="sunon")
        return SunonSnapshotBuild(snapshot, assets, bindings)
    return snapshot


def build_sunon_snapshot(files) -> dict:
    return _build_sunon(files, include_assets=False)


def build_sunon_snapshot_with_assets(files) -> SunonSnapshotBuild:
    return _build_sunon(files, include_assets=True)
