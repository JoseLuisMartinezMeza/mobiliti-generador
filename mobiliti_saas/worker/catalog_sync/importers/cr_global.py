from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from mobiliti_saas.worker.catalog_sync.cr_global_links import resolve_cr_global_link

from .common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    CellRef,
    extract_xlsx_images,
    iter_pdf_pages,
    neutralize_spreadsheet_text,
    open_xlsx_data_only,
    source_ref,
    validate_source_file,
)


_EXPECTED = {
    "spec_guide": (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "price_list": (".pdf", "application/pdf"),
    "catalog": (".pdf", "application/pdf"),
}
_MONEY = re.compile(r"(?:\$\s*)?([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})\s*$")
_MEASUREMENT = re.compile(
    r"(?i)(?:\d+(?:[.,]\d+)?\s*(?:-|a)\s*)?\d+(?:[.,]\d+)?\s*(?:mm|cm|m|mts?|pulgadas?|\")"
)


def _plain(value) -> str:
    text = neutralize_spreadsheet_text(value).strip()
    return re.sub(r"\s+", " ", text)


def _fold(value) -> str:
    text = unicodedata.normalize("NFKD", _plain(value))
    return "".join(character for character in text if not unicodedata.combining(character)).casefold()


def _code(value) -> str:
    text = unicodedata.normalize("NFKC", _plain(value)).upper()
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", re.sub(r"\s*-\s*", "-", text)).strip()


def _key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(value)).strip("-")


def _source_hash(files) -> str:
    material = "\n".join(
        f"{row.path}\0{row.sha256}" for row in sorted(files, key=lambda row: row.path)
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _validated_bundle(files):
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("CR_GLOBAL_BUNDLE") from None
    if len(rows) != len(_EXPECTED):
        raise ValueError("CR_GLOBAL_BUNDLE")
    by_kind = {}
    for row in rows:
        kind = getattr(row, "kind", None)
        if kind not in _EXPECTED or kind in by_kind:
            raise ValueError("CR_GLOBAL_BUNDLE")
        extension, mime_type = _EXPECTED[kind]
        local_path = getattr(row, "local_path", None)
        path = getattr(row, "path", None)
        declared_hash = getattr(row, "sha256", None)
        if (
            not isinstance(path, str)
            or Path(path).suffix.lower() != extension
            or not isinstance(local_path, Path)
            or local_path.suffix.lower() != extension
            or getattr(row, "mime_type", None) != mime_type
            or not isinstance(declared_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
        ):
            raise ValueError("CR_GLOBAL_BUNDLE")
        validated = validate_source_file(local_path, extension)
        if validated.sha256 != declared_hash:
            raise ValueError("CR_GLOBAL_HASH")
        by_kind[kind] = row
    return by_kind


def _heading_columns(sheet):
    aliases = {
        "code": {"cod", "codigo", "clave"},
        "image": {"imagen"},
        "description": {"descripcion"},
        "dimensions": {"medida/unidad", "medidas/unidad", "dimension", "dimensiones"},
        "unit_price": {"p. unitario", "p unitario", "precio unitario"},
        "currency": {"moneda"},
        "sale_price": {"precio venta 50% gp", "precio de venta 50% gp"},
        "pricing_factor": {"lab cedis"},
    }
    required = {"code", "image", "description", "dimensions"}
    for row in range(1, min(sheet.max_row, 100) + 1):
        columns = {}
        for column in range(1, min(sheet.max_column, 30) + 1):
            heading = _fold(sheet.cell(row, column).value).strip(". ")
            for name, accepted in aliases.items():
                if heading in accepted:
                    columns[name] = column
        if required <= set(columns):
            return row, columns
    return None


def _section_metadata(sheet, header_row):
    collection = ""
    system = ""
    lead_time = ""
    metadata = {}
    for row in range(1, sheet.max_row + 1):
        for column in range(1, min(sheet.max_column, 12) + 1):
            text = _plain(sheet.cell(row, column).value)
            if re.match(r"(?i)^fami(?:lia|la)\s*:", text):
                collection = text.split(":", 1)[1].strip()
            elif re.match(r"(?i)^sistema\s*:", text):
                system = text.split(":", 1)[1].strip()
            elif re.match(r"(?i)^tiempo\s+entrega\s*:", text):
                lead_time = text.split(":", 1)[1].strip()
        if row >= header_row:
            metadata[row] = {
                "collection": collection,
                "system": system,
                "lead_time": lead_time,
            }
    return metadata


def _dimension_evidence(dimensions, description) -> str:
    direct = _plain(dimensions)
    if direct:
        return direct
    segments = re.split(r"(?<=[.;])\s+|\s{2,}", _plain(description))
    found = []
    for segment in segments:
        if _MEASUREMENT.search(segment):
            clean = segment.strip(" .;,-")
            if clean and clean not in found:
                found.append(clean)
    return " | ".join(found)


def _availability_type(lead_time) -> str:
    folded = _fold(lead_time)
    if "sobre pedido" in folded or re.search(
        r"\b\d+(?:[.,]\d+)?\s*(?:dia|dias|semana|semanas|mes|meses)\b",
        folded,
    ):
        return "made_to_order"
    return "unknown"


def _configuration(sheet, row, description_column):
    if row <= 1:
        return ""
    value = _plain(sheet.cell(row - 1, description_column).value)
    return value if value and len(value) <= 200 else ""


def _description(sheet, row, description_column, product_rows):
    parts = []
    next_product = next((candidate for candidate in product_rows if candidate > row), sheet.max_row + 1)
    for current in range(row, next_product):
        value = _plain(sheet.cell(current, description_column).value)
        if not value:
            break
        parts.append(value)
    return " ".join(parts)


def _supplementary_fields(sheet, row, description_column, product_rows):
    next_product = next((candidate for candidate in product_rows if candidate > row), sheet.max_row + 1)
    color = ""
    warranty = ""
    notes = []
    for current in range(row + 1, next_product):
        value = _plain(sheet.cell(current, description_column).value)
        folded = _fold(value)
        if re.match(r"^color\s*:", folded):
            color = value.split(":", 1)[1].strip()
        elif "garantia" in folded or "grantia" in folded:
            warranty = value
        elif folded.startswith("no incluye") and value not in notes:
            notes.append(value)
    return color, warranty, notes


def _spec_records(row, images):
    workbook = open_xlsx_data_only(row.local_path)
    try:
        candidates = [
            (sheet, detected)
            for sheet in workbook.worksheets
            if (detected := _heading_columns(sheet)) is not None
        ]
        sale_candidates = [candidate for candidate in candidates if "sale_price" in candidate[1][1]]
        companion_candidates = [
            candidate
            for candidate in candidates
            if "sale_price" not in candidate[1][1]
            and {"unit_price", "currency"} <= set(candidate[1][1])
        ]
        if len(sale_candidates) == 1:
            sheet, (header_row, columns) = sale_candidates[0]
            price_companion = companion_candidates[0] if len(companion_candidates) == 1 else None
        elif len(candidates) == 1:
            sheet, (header_row, columns) = candidates[0]
            price_companion = None
        else:
            raise ValueError("CR_GLOBAL_SPEC_SHEET")
        metadata = _section_metadata(sheet, header_row)
        sheet_images = {
            reference.cell: image
            for reference, image in images.items()
            if reference.sheet == sheet.title
        }
        product_rows = []
        for current in range(header_row + 1, sheet.max_row + 1):
            code = _plain(sheet.cell(current, columns["code"]).value)
            image_cell = f"{sheet.cell(current, columns['image']).column_letter}{current}"
            has_product_data = bool(
                _plain(sheet.cell(current, columns["description"]).value)
                and _plain(sheet.cell(current, columns["dimensions"]).value)
            )
            if code or (image_cell in sheet_images and has_product_data):
                product_rows.append(current)
        records = []
        for current in product_rows:
            raw_code = _code(sheet.cell(current, columns["code"]).value)
            image_cell = f"{sheet.cell(current, columns['image']).column_letter}{current}"
            image = sheet_images.get(image_cell)
            configuration = _configuration(sheet, current, columns["description"])
            description = _description(sheet, current, columns["description"], product_rows)
            color, warranty, notes = _supplementary_fields(
                sheet, current, columns["description"], product_rows
            )
            dimensions = _dimension_evidence(
                sheet.cell(current, columns["dimensions"]).value,
                description,
            )
            row_metadata = metadata.get(current, {})
            price_column = columns.get("sale_price") or columns.get("unit_price")
            currency_column = columns.get("currency")
            price = None
            price_sheet = sheet.title
            if price_column is not None:
                try:
                    price = Decimal(str(sheet.cell(current, price_column).value))
                except (InvalidOperation, TypeError, ValueError):
                    price = None
            currency = _code(sheet.cell(current, currency_column).value) if currency_column else ""
            price_input_cells = []
            if (
                price is None
                and "sale_price" in columns
                and {"unit_price", "pricing_factor"} <= set(columns)
            ):
                try:
                    cost = Decimal(str(sheet.cell(current, columns["unit_price"]).value))
                    factor = Decimal(str(sheet.cell(current, columns["pricing_factor"]).value))
                    price = cost / factor if cost > 0 and factor > 0 else None
                except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
                    price = None
                if price is not None:
                    price_input_cells = [
                        f"{sheet.cell(current, columns['unit_price']).column_letter}{current}",
                        f"{sheet.cell(current, columns['pricing_factor']).column_letter}{current}",
                    ]
            if price is None and price_companion is not None:
                companion_sheet, (_, companion_columns) = price_companion
                price_column = companion_columns["unit_price"]
                currency_column = companion_columns["currency"]
                try:
                    price = Decimal(str(companion_sheet.cell(current, price_column).value))
                except (InvalidOperation, TypeError, ValueError):
                    price = None
                currency = _code(companion_sheet.cell(current, currency_column).value)
                price_sheet = companion_sheet.title
            name = configuration or description or raw_code or "Producto CR Global por verificar"
            records.append(
                {
                    "row": current,
                    "sheet": sheet.title,
                    "code": raw_code,
                    "name": name,
                    "configuration": configuration,
                    "description": description,
                    "color": color,
                    "warranty": warranty,
                    "notes": notes,
                    "dimensions": dimensions,
                    "collection": row_metadata.get("collection", ""),
                    "system": row_metadata.get("system", ""),
                    "section_lead_time": row_metadata.get("lead_time", ""),
                    "spec_price": price,
                    "spec_currency": currency,
                    "price_sheet": price_sheet,
                    "code_cell": f"{sheet.cell(current, columns['code']).column_letter}{current}",
                    "price_cell": (
                        f"{sheet.cell(current, price_column).column_letter}{current}"
                        if price_column is not None
                        else ""
                    ),
                    "currency_cell": (
                        f"{sheet.cell(current, currency_column).column_letter}{current}"
                        if currency_column is not None
                        else ""
                    ),
                    "price_input_cells": price_input_cells,
                    "image_cell": image_cell,
                    "image": image,
                }
            )
        if not records:
            raise ValueError("CR_GLOBAL_SPEC_EMPTY")
        return records
    finally:
        workbook.close()


def _codes_before_first_price(lines, known_codes):
    first_price = next(
        (index for index, line in enumerate(lines) if "$" in line and _MONEY.search(line)),
        None,
    )
    if first_price is None:
        first_price = next(
            (
                index
                for index in range(len(lines) - 1)
                if lines[index].strip() == "$" and _MONEY.search(lines[index + 1])
            ),
            len(lines),
        )
    found = []
    index = 0
    while index < first_price:
        matched = None
        for width in (3, 2, 1):
            if index + width > first_price:
                continue
            candidate = _code("".join(lines[index : index + width]))
            if candidate in known_codes:
                matched = candidate
                index += width
                break
            candidate = _code(" ".join(lines[index : index + width]))
            if candidate in known_codes:
                matched = candidate
                index += width
                break
        if matched:
            found.append(matched)
        else:
            index += 1
    return found


def _matched_codes_before_first_price(lines, records):
    known_codes = {record["code"] for record in records if record["code"]}
    exact = _codes_before_first_price(lines, known_codes)
    first_price = next((index for index, line in enumerate(lines) if "$" in line), len(lines))
    matched = list(exact)
    for index, line in enumerate(lines[:first_price]):
        base = _code(line)
        if base in known_codes or not re.search(r"[A-Z].*\d|\d.*[A-Z]", base):
            continue
        candidates = [record for record in records if record["code"].startswith(base + "-")]
        if not candidates:
            continue
        token_sets = [set(re.findall(r"[a-z0-9]+", _fold(record["configuration"]))) for record in candidates]
        common = set.intersection(*token_sets) if len(token_sets) > 1 else set()
        context_tokens = set(re.findall(r"[a-z0-9]+", _fold(" ".join(lines[index : index + 2]))))
        resolved = [
            record["code"]
            for record, tokens in zip(candidates, token_sets)
            if tokens - common and tokens - common <= context_tokens
        ]
        if len(resolved) == 1:
            matched.append(resolved[0])
    return matched


def _unique_name_prices(lines, records):
    by_name = defaultdict(list)
    for record in records:
        if record["code"]:
            by_name[_fold(record["name"])].append(record["code"])
    known_names = {name: codes[0] for name, codes in by_name.items() if name and len(codes) == 1}
    found = []
    for index, line in enumerate(lines):
        code = known_names.get(_fold(line))
        if not code:
            continue
        adjacent = lines[index + 1 : index + 3]
        if len(adjacent) == 2 and adjacent[0] == "$":
            match = _MONEY.fullmatch(adjacent[1])
        elif adjacent:
            match = _MONEY.fullmatch(adjacent[0]) if "$" in adjacent[0] else None
        else:
            match = None
        if match:
            found.append((code, Decimal(match.group(1).replace(",", ""))))
    return found


def _page_prices(lines):
    prices = []
    for index, line in enumerate(lines):
        match = _MONEY.search(line)
        has_currency = "$" in line or (index and lines[index - 1].strip() == "$")
        if match and has_currency:
            try:
                prices.append(Decimal(match.group(1).replace(",", "")))
            except InvalidOperation:
                pass
    return prices


def _price_data(row, records):
    values = defaultdict(list)
    references = defaultdict(list)
    lead_time = ""
    blocked_currency = set()
    for page in iter_pdf_pages(row.local_path):
        text = page.text
        lines = [_plain(line) for line in text.splitlines() if _plain(line)]
        if not lead_time:
            match = re.search(r"(?i)tiempos? de entrega\s*:\s*([^\n]+)", text)
            if match:
                lead_time = _plain(match.group(1))
        codes = _matched_codes_before_first_price(lines, records)
        prices = _page_prices(lines)
        foreign = re.search(r"(?i)\b(?:USD|EUR|d[oó]lares?|euros?)\b", text)
        currency_present = bool(re.search(r"(?i)\bMXN\b", text))
        pairs = list(zip(codes, prices)) if len(codes) == len(prices) else _unique_name_prices(lines, records)
        paired_codes = {code for code, _ in pairs}
        if len(codes) != len(prices):
            blocked_currency.update(set(codes) - paired_codes)
        for code, price in pairs:
            if foreign or not currency_present:
                blocked_currency.add(code)
                continue
            values[code].append(price)
            references[code].append(source_ref(row.sha256, page.number, (0, 0, 0, 0)))
    return values, references, blocked_currency, lead_time


def _technical_data(row, known_codes):
    descriptions = {}
    references = {}
    for page in iter_pdf_pages(row.local_path):
        text = _plain(page.text)
        matches = re.findall(r"(?i)\bModelo\s*:\s*([^\n]+)", page.text)
        for raw in matches:
            code = _code(raw)
            if code in known_codes and code not in descriptions:
                descriptions[code] = text
                references[code] = source_ref(row.sha256, page.number, (0, 0, 0, 0))
    return descriptions, references


def _price_for(record, values, blocked):
    code = record["code"]
    warnings = []
    if record["spec_price"] is not None:
        if record["spec_currency"] != "MXN":
            return Decimal(0), ["Moneda del precio XLSX no verificable; verificar precio."]
        if record["spec_price"] <= 0:
            return Decimal(0), ["Precio XLSX no positivo; requiere revision."]
        return record["spec_price"], warnings
    if code in blocked:
        return Decimal(0), ["Moneda o estructura de precio no verificable; verificar precio."]
    distinct = set(values.get(code, ()))
    if len(distinct) > 1:
        return Decimal(0), ["Precio conflictivo en la lista vigente; requiere revision."]
    if not distinct:
        return Decimal(0), ["Precio no encontrado en la lista vigente; requiere revision."]
    return next(iter(distinct)), warnings


def _item(record, spec, prices, price_refs, blocked, technical, technical_refs, lead_time, ambiguous_codes):
    code = record["code"]
    warnings = []
    if code and code not in ambiguous_codes:
        product_key = _key(code)
        internal_id = f"cr-global:{product_key}"
        code_status = "verified"
        sku = code
    else:
        identity = f"{code}\0{record['name']}\0{record['dimensions']}\0{record['row']}"
        product_key = "review-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        internal_id = f"cr-global:{product_key}"
        code_status = "needs_review"
        sku = ""
        warnings.append(
            "Codigo duplicado incompatible; verificar codigo antes de cotizar."
            if code
            else "Codigo de proveedor faltante; verificar codigo antes de cotizar."
        )
    price, price_warnings = (
        _price_for(record, prices, blocked)
        if code and code not in ambiguous_codes
        else (Decimal(0), [])
    )
    warnings.extend(price_warnings)
    description = record["description"]
    evidence = [source_ref(spec.sha256, record["sheet"], record["code_cell"])]
    if record["spec_price"] is not None:
        evidence.append(source_ref(spec.sha256, record["price_sheet"], record["price_cell"]))
        evidence.extend(
            source_ref(spec.sha256, record["price_sheet"], cell)
            for cell in record["price_input_cells"]
        )
        if record["currency_cell"]:
            evidence.append(source_ref(spec.sha256, record["price_sheet"], record["currency_cell"]))
    elif code and code in price_refs:
        evidence.extend(price_refs[code])
    if not description and code in technical:
        description = technical[code]
        evidence.append(technical_refs[code])
    attributes = {
        "configuration": record["configuration"],
        "dimensions": record["dimensions"],
        "system": record["system"],
    }
    if record["color"]:
        attributes["color"] = record["color"]
    if record["warranty"]:
        attributes["warranty"] = record["warranty"]
    if record["notes"]:
        attributes["product_notes"] = record["notes"]
    if record["image"] is not None:
        attributes.update(
            image_sha256=record["image"].sha256,
            image_width=record["image"].width,
            image_height=record["image"].height,
        )
    product_link = resolve_cr_global_link(code, spec.sha256) if code_status == "verified" else {}
    if product_link.get("url"):
        attributes["product_url_match"] = {
            key: value for key, value in product_link.items() if key != "url"
        }
    resolved_lead_time = record["section_lead_time"] or lead_time
    return {
        "internal_id": internal_id,
        "supplier": "cr-global",
        "product_key": product_key,
        "sku": sku,
        "code_status": code_status,
        "brand": "CR Global",
        "collection": record["collection"],
        "name": record["name"],
        "description": description,
        "unit": "PZA",
        "availability_type": _availability_type(resolved_lead_time),
        "stock": None,
        "lead_time": resolved_lead_time,
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": "MXN",
        "price_net": f"{price:.6f}",
        "tax_rate": "0.160000",
        "attributes": attributes,
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": product_link.get("url", ""),
        "warnings": warnings,
        "source_reference": json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
    }


def _build_cr_global(files, *, include_assets: bool):
    bundle = _validated_bundle(files)
    spec = bundle["spec_guide"]
    images = extract_xlsx_images(spec.local_path)
    records = _spec_records(spec, images)
    known_codes = {record["code"] for record in records if record["code"]}
    if any(record["spec_price"] is None for record in records):
        prices, price_refs, blocked, lead_time = _price_data(bundle["price_list"], records)
    else:
        prices, price_refs, blocked, lead_time = {}, {}, set(), ""
    if any(not record["description"] for record in records):
        technical, technical_refs = _technical_data(bundle["catalog"], known_codes)
    else:
        technical, technical_refs = {}, {}
    code_counts = defaultdict(int)
    for record in records:
        if record["code"]:
            code_counts[record["code"]] += 1
    ambiguous_codes = {code for code, count in code_counts.items() if count > 1}
    item_records = [
        (
            _item(
            record,
            spec,
            prices,
            price_refs,
            blocked,
            technical,
            technical_refs,
            lead_time,
            ambiguous_codes,
            ),
            record,
        )
        for record in records
    ]
    assets = {}
    bindings = []
    if include_assets:
        for item, record in item_records:
            asset = record["image"]
            if asset is None:
                continue
            reference = source_ref(spec.sha256, record["sheet"], record["image_cell"])
            object_name = f"{asset.sha256}.png"
            item["image_kind"] = "official"
            item["attributes"]["image_match"] = {
                "status": "exact_xlsx",
                "asset_sha256": asset.sha256,
                "source_references": [reference],
            }
            item["attributes"]["approved_asset"] = {
                "bucket": "catalog-assets",
                "path": object_name,
                "image_kind": "official",
                "label": "Imagen oficial del XLSX CR Global",
                "approved": True,
            }
            assets[asset.sha256] = asset
            bindings.append(
                CatalogAssetBinding(
                    item["internal_id"],
                    asset.sha256,
                    object_name,
                    "official",
                    "exact_xlsx",
                    (reference,),
                )
            )
    items = [item for item, _ in item_records]
    items.sort(key=lambda item: item["internal_id"])
    snapshot = {
        "supplier": "cr-global",
        "source_hash": _source_hash(tuple(bundle.values())),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
    }
    if include_assets:
        return CatalogSnapshotBuild(
            snapshot,
            assets,
            tuple(sorted(bindings, key=lambda binding: binding.internal_id)),
        )
    return snapshot


def build_cr_global_snapshot(files) -> dict:
    return _build_cr_global(files, include_assets=False)


def build_cr_global_snapshot_with_assets(files) -> CatalogSnapshotBuild:
    return _build_cr_global(files, include_assets=True)
