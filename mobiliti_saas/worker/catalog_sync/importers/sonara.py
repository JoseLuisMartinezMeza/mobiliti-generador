from __future__ import annotations

import base64
import hashlib
import io
import json
import multiprocessing
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz
from PIL import Image

from . import common as _common
from .common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    ImageAsset,
    _normalize_image,
    neutralize_spreadsheet_text,
    read_validated_source,
    source_ref,
)


_EXPECTED = {"catalog", "price_list"}
_MONEY = re.compile(r"^(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{2})?$")
_CODE = re.compile(r"\b(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)([A-Z0-9]+(?:-[A-Z0-9]+)*)\b")
_MEASUREMENT = re.compile(
    r"(?i)(?:\d*\.?\d+\s*(?:mm|cm|m|mts?)?\s*x\s*){0,2}"
    r"\d*\.?\d+\s*(?:mm|cm|m|mts?|kg)\b"
)
_SONARA_CATALOG_SHA256 = "35c4abd3c4b3fef5c11cb8b7b22509f9913343b9ee79bf4cc6ae9c6aac3f0099"
_SONARA_PRICE_SHA256 = "c497314221f5e700d6722deb92a3dbb02c4686e7b39e17766332bee6a6e05128"
_SONARA_MXN_RULE = "sonara_mxn_confirmed_2026-07-19"
_SUSPENDED_COLLECTION_URL = "https://sonara.mx/soluciones-sonara/paneles-suspendidos/"
_MAX_GEOMETRY_RESULT_BYTES = 32 * 1024 * 1024
_MAX_GEOMETRY_SECONDS = 60
_SACC_VARIANT_REFS = {
    "1.20": {
        "suffix": "01",
        "variant_bbox": [845.5, 663.8, 855.3, 677.7],
        "measure_bbox": [918.0, 663.8, 960.9, 677.7],
    },
    "2.40": {
        "suffix": "02",
        "variant_bbox": [845.5, 685.8, 857.1, 699.7],
        "measure_bbox": [918.0, 685.8, 965.7, 699.7],
    },
}
_SACC_MODELS = {
    "SACC003": {
        "variants": {"1.20", "2.40"},
        "label_bbox": [78.3, 239.0, 140.8, 253.7],
        "image_bbox": [65.3, 40.9, 294.6, 250.7],
        "asset_sha256": "19679a8d9a4cd40fbe723fb8e6d45b53d20536d98699faf4f65fcd0c0bac24cf",
    },
    "SACC004": {
        "variants": {"1.20", "2.40"},
        "label_bbox": [336.5, 239.0, 399.3, 253.7],
        "asset_candidates": [
            "9411b717bb539e7f4642481a07c46777514c80d55973bd5dca29e9a6aa9ccbcf",
            "c9c6c67a0a56bcabe3266dcd017df288176883568f9ff19e3d9655a52d8c98e6",
        ],
    },
    "SACC005": {
        "variants": {"1.20", "2.40"},
        "label_bbox": [78.3, 470.3, 140.9, 485.0],
        "image_bbox": [54.7, 265.1, 305.2, 474.1],
        "asset_sha256": "775dc2635db149379cffd00b3031a2701fa7bd34d906618667738b2e7f6ef60f",
    },
    "SACC006": {
        "variants": {"1.20"},
        "label_bbox": [336.5, 470.3, 399.5, 485.0],
        "image_bbox": [310.6, 267.5, 553.6, 474.1],
        "asset_sha256": "ca4479f4bb1066c806c7d9aa59b6922cfeb96be6ed32b74c0fbeee5f88576f65",
    },
}

_FOREIGN_CURRENCY_EVIDENCE = re.compile(
    r"(?i)(?:\bUSD\b|\bEUR\b|\bUS\s*\$|€)"
)


def _plain(value) -> str:
    return re.sub(r"\s+", " ", neutralize_spreadsheet_text(value)).strip()


def _fold(value) -> str:
    text = unicodedata.normalize("NFKD", _plain(value))
    return " ".join(
        re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)).casefold())
    )


def _code(value) -> str:
    match = _CODE.search(unicodedata.normalize("NFKC", _plain(value)).upper())
    return re.sub(r"[\s-]+", "", match.group(1)) if match else ""


def _source_hash(files) -> str:
    material = "\n".join(
        f"{row.path}\0{row.sha256}" for row in sorted(files, key=lambda row: (row.path, row.kind, row.sha256))
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _validated_bundle(files):
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("SONARA_BUNDLE") from None
    if len(rows) != 2:
        raise ValueError("SONARA_BUNDLE")
    by_kind = {}
    source_data = {}
    logical_paths = set()
    for row in rows:
        kind = getattr(row, "kind", None)
        local_path = getattr(row, "local_path", None)
        path = getattr(row, "path", None)
        declared_hash = getattr(row, "sha256", None)
        if (
            kind not in _EXPECTED
            or kind in by_kind
            or not isinstance(path, str)
            or Path(path).suffix.lower() != ".pdf"
            or not isinstance(local_path, Path)
            or local_path.suffix.lower() != ".pdf"
            or getattr(row, "mime_type", None) != "application/pdf"
            or not isinstance(declared_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
            or path in logical_paths
        ):
            raise ValueError("SONARA_BUNDLE")
        validated, data = read_validated_source(local_path, ".pdf")
        if validated.sha256 != declared_hash:
            raise ValueError("SONARA_HASH")
        logical_paths.add(path)
        by_kind[kind] = row
        source_data[kind] = data
    return by_kind, source_data


def _merged_vertical_length(segments):
    covered = 0.0
    current_start = None
    current_end = None
    for start, end in sorted(segments):
        if current_start is None:
            current_start, current_end = start, end
        elif start <= current_end + 1:
            current_end = max(current_end, end)
        else:
            covered += current_end - current_start
            current_start, current_end = start, end
    if current_start is not None:
        covered += current_end - current_start
    return covered


def _drawn_column_boundaries(page, found):
    segments = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.x - end.x) <= 1 and abs(start.y - end.y) >= 20:
                    segments.append((float((start.x + end.x) / 2), min(start.y, end.y), max(start.y, end.y)))
            elif item[0] == "re":
                rectangle = item[1]
                if rectangle.height >= 20:
                    segments.extend(
                        (
                            (float(rectangle.x0), float(rectangle.y0), float(rectangle.y1)),
                            (float(rectangle.x1), float(rectangle.y0), float(rectangle.y1)),
                        )
                    )
    clusters = []
    for x, start, end in sorted(segments):
        if clusters and x - clusters[-1]["max_x"] <= 4:
            clusters[-1]["xs"].append(x)
            clusters[-1]["segments"].append((start, end))
            clusters[-1]["max_x"] = x
        else:
            clusters.append({"xs": [x], "segments": [(start, end)], "max_x": x})
    candidates = [
        (sum(cluster["xs"]) / len(cluster["xs"]), _merged_vertical_length(cluster["segments"]))
        for cluster in clusters
    ]
    centers = {
        name: (word[0] + word[2]) / 2
        for name, word in found.items()
    }
    boundaries = []
    for left, right in (("product", "description"), ("description", "price")):
        low, high = sorted((centers[left], centers[right]))
        choices = [candidate for candidate in candidates if low < candidate[0] < high]
        if not choices:
            return None
        boundary, coverage = max(
            choices,
            key=lambda candidate: (candidate[1], -abs(candidate[0] - (low + high) / 2)),
        )
        if coverage < page.rect.height * 0.1:
            return None
        boundaries.append(boundary)
    return boundaries


def _column_headers(words, page=None):
    found = {}
    for word in words:
        value = _fold(word[4])
        if value == "producto":
            name = "product"
        elif value in {"descripcion", "description"}:
            name = "description"
        elif value == "precio":
            name = "price"
        else:
            continue
        if name not in found or word[1] < found[name][1]:
            found[name] = word
    if set(found) != {"product", "description", "price"}:
        raise ValueError("SONARA_PRICE_COLUMNS")
    starts = sorted((word[0], name) for name, word in found.items())
    if [name for _, name in starts] != ["product", "description", "price"]:
        raise ValueError("SONARA_PRICE_COLUMNS")
    boundaries = _drawn_column_boundaries(page, found) if page is not None else None
    if boundaries is None:
        boundaries = [starts[1][0], starts[2][0]]

    def column(word):
        center = (word[0] + word[2]) / 2
        position = 0 if center < boundaries[0] else 1 if center < boundaries[1] else 2
        return starts[position][1]
    product_left = found["product"][0] - 30
    return column, max(word[3] for word in found.values()), product_left


def _joined(words) -> str:
    return _plain(" ".join(word[4] for word in sorted(words, key=lambda word: (round(word[1], 1), word[0]))))


def _bbox(words):
    return [
        round(min(word[0] for word in words), 1),
        round(min(word[1] for word in words), 1),
        round(max(word[2] for word in words), 1),
        round(max(word[3] for word in words), 1),
    ]


def _source_currency(
    currencies: set[str],
    full_text: str,
    source_sha256: str,
    confirmed_price_sha256: str,
) -> tuple[str | None, str]:
    if _FOREIGN_CURRENCY_EVIDENCE.search(full_text) or currencies - {"MXN"}:
        return None, "rejected"
    if currencies == {"MXN"}:
        return "MXN", "verified"
    if not currencies and source_sha256 == confirmed_price_sha256:
        return "MXN", "business_override"
    return None, "rejected"


def _price_rows(row, data, confirmed_price_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", confirmed_price_sha256):
        raise ValueError("SONARA_PRICE_HASH")
    records = []
    source_sha256 = hashlib.sha256(data).hexdigest()
    document = fitz.open(stream=data, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
        currencies = {
            match.group(1).upper()
            for match in re.finditer(r"(?i)\bmoneda\s*:?\s*(MXN|USD|EUR)\b", full_text)
        }
        currency, currency_status = _source_currency(
            currencies, full_text, source_sha256, confirmed_price_sha256
        )
        plus_iva = bool(re.search(r"(?i)\bm[aá]s\s+IVA\b", full_text))
        for page_number, page in enumerate(document, 1):
            words = page.get_text("words", sort=False)
            column, header_bottom, product_left = _column_headers(words, page)
            large_boxes = [
                block["bbox"]
                for block in page.get_text("dict").get("blocks", [])
                if block.get("type") == 0
                and max(
                    (span["size"] for line in block.get("lines", []) for span in line.get("spans", [])),
                    default=0,
                ) >= 14
                and block["bbox"][1] > header_bottom
            ]
            body = [
                word
                for word in words
                if word[1] > header_bottom
                and word[3] < page.rect.height - 20
                and not any(
                    box[0] <= (word[0] + word[2]) / 2 <= box[2]
                    and box[1] <= (word[1] + word[3]) / 2 <= box[3]
                    for box in large_boxes
                )
            ]
            prices = [
                word
                for word in body
                if column(word) == "price" and _MONEY.fullmatch(word[4].strip())
            ]
            prices.sort(key=lambda word: ((word[1] + word[3]) / 2, word[0]))
            centers = [(word[1] + word[3]) / 2 for word in prices]
            for index, price_word in enumerate(prices):
                previous_gap = centers[index] - centers[index - 1] if index else 50
                next_gap = centers[index + 1] - centers[index] if index + 1 < len(centers) else previous_gap
                low = max(header_bottom, centers[index] - previous_gap / 2)
                high = min(page.rect.height - 20, centers[index] + next_gap / 2)
                band = [word for word in body if low < (word[1] + word[3]) / 2 <= high]
                product_words = [
                    word for word in band if column(word) == "product" and word[0] >= product_left
                ]
                description_words = [word for word in band if column(word) == "description"]
                if not product_words or not description_words:
                    continue
                try:
                    price = Decimal(price_word[4].replace(",", ""))
                except InvalidOperation:
                    continue
                records.append(
                    {
                        "page": page_number,
                        "bbox": _bbox(product_words + description_words + [price_word]),
                        "name": _joined(product_words),
                        "description": _joined(description_words),
                        "price": price,
                        "currency": currency,
                        "currency_ok": currency == "MXN",
                        "currency_status": currency_status,
                        "plus_iva": plus_iva,
                    }
                )
    finally:
        document.close()
    if not records:
        raise ValueError("SONARA_PRICE_EMPTY")
    return records


def _normalized_image(block):
    try:
        with Image.open(io.BytesIO(block["image"])) as source:
            image = source.convert("RGB")
            output = io.BytesIO()
            image.save(output, "PNG", optimize=False)
            data = output.getvalue()
            return {
                "image_sha256": hashlib.sha256(data).hexdigest(),
                "image_width": image.width,
                "image_height": image.height,
                "image_bbox": [round(value, 1) for value in block["bbox"]],
            }
    except Exception:
        return None


def _catalog_records(row, data):
    records = []
    document = fitz.open(stream=data, filetype="pdf")
    try:
        for page_number, page in enumerate(document, 1):
            text = page.get_text()
            folded = _fold(text)
            explicit_codes = {_code(match.group(0)) for match in re.finditer(r"(?i)\bmodelo\s*[: ]\s*[^\n]+", text)}
            explicit_codes.discard("")
            blocks = page.get_text("dict").get("blocks", [])
            text_blocks = [
                _plain(" ".join(span["text"] for line in block.get("lines", []) for span in line.get("spans", [])))
                for block in blocks
                if block.get("type") == 0
            ]
            descriptions = [
                value
                for value in text_blocks
                if len(value) >= 30 and "sonara.mx" not in value.casefold() and not _code(value)
            ]
            images = [block for block in blocks if block.get("type") == 1 and block.get("image")]
            image = None
            if images:
                areas = sorted(
                    [
                        (
                        (block["bbox"][2] - block["bbox"][0])
                        * (block["bbox"][3] - block["bbox"][1]),
                        block,
                        )
                        for block in images
                    ],
                    key=lambda row: row[0],
                )
                if len(areas) == 1 or areas[-1][0] > areas[-2][0]:
                    image = _normalized_image(areas[-1][1])
            records.append(
                {
                    "page": page_number,
                    "bbox": [0.0, 0.0, round(page.rect.width, 1), round(page.rect.height, 1)],
                    "folded": folded,
                    "codes": explicit_codes,
                    "description": max(descriptions, key=len, default=""),
                    "image": image,
                }
            )
    finally:
        document.close()
    return records


def _catalog_match(record, catalog):
    code = _code(record["name"])
    if code:
        candidates = [candidate for candidate in catalog if code in candidate["codes"]]
    else:
        name_tokens = set(_fold(record["name"]).split())
        attributes = set(re.findall(r"\b\d+(?:[.,]\d+)?\s*(?:mm|cm|cms|m|mts|m2)\b", _fold(record["description"])))
        candidates = [
            candidate
            for candidate in catalog
            if name_tokens and name_tokens <= set(candidate["folded"].split())
            and attributes and all(attribute in candidate["folded"] for attribute in attributes)
        ]
    return candidates[0] if len(candidates) == 1 else None, len(candidates) > 1


def _unit(description):
    match = re.search(r"(?i)\bunidad\s*:\s*([A-Z0-9]+)\b", description)
    if match:
        return match.group(1).upper()
    return "M2" if "costo por metro cuadrado" in _fold(description) else "PZA"


def _dimension_evidence(record):
    name_values = [_plain(match.group(0)).strip(" .") for match in _MEASUREMENT.finditer(record["name"])]
    description_values = [
        _plain(match.group(0)).strip(" .") for match in _MEASUREMENT.finditer(record["description"])
    ]
    selected = name_values or description_values
    return "; ".join(dict.fromkeys(value for value in selected if value))


def _sacc_resolution(record, price_source, catalog_source):
    if (
        price_source.sha256 != _SONARA_PRICE_SHA256
        or catalog_source.sha256 != _SONARA_CATALOG_SHA256
    ):
        return None
    model_match = re.search(r"(?i)\bSACC\s*(00[3-6])\b", record["name"])
    measure_match = re.search(r"(?i)\b(1\.20|2\.40)\s*m", record["name"])
    if not model_match or not measure_match:
        return None
    model = "SACC" + model_match.group(1)
    measure = measure_match.group(1)
    model_row = _SACC_MODELS.get(model)
    variant = _SACC_VARIANT_REFS.get(measure)
    if model_row is None or variant is None or measure not in model_row["variants"]:
        return None
    references = (
        source_ref(catalog_source.sha256, 26, model_row["label_bbox"]),
        source_ref(catalog_source.sha256, 27, variant["variant_bbox"]),
        source_ref(catalog_source.sha256, 27, variant["measure_bbox"]),
    )
    return {
        "model": model,
        "measure": measure,
        "sku": f"{model}-{variant['suffix']}",
        "references": references,
        "asset_sha256": model_row.get("asset_sha256", ""),
        "image_bbox": model_row.get("image_bbox"),
        "label_bbox": model_row["label_bbox"],
        "asset_candidates": model_row.get("asset_candidates", []),
    }


def _manifest_assets(catalog_data):
    expected = {
        row["asset_sha256"]: row["image_bbox"]
        for row in _SACC_MODELS.values()
        if row.get("asset_sha256") and row.get("image_bbox")
    }
    assets = {}
    document = fitz.open(stream=catalog_data, filetype="pdf")
    try:
        if document.page_count < 26:
            return {}
        blocks = document[25].get_text("dict").get("blocks", [])
        for block in blocks:
            if block.get("type") != 1 or not block.get("image"):
                continue
            bbox = [round(value, 1) for value in block["bbox"]]
            matching = [sha256 for sha256, expected_bbox in expected.items() if bbox == expected_bbox]
            if len(matching) != 1:
                continue
            asset = _normalize_image(block["image"])
            if asset.sha256 == matching[0]:
                assets[asset.sha256] = asset
    finally:
        document.close()
    return assets if set(assets) == set(expected) else {}


def _geometry_payload(rows, catalog, assets) -> bytes:
    payload = {
        "rows": [{**record, "price": str(record["price"])} for record in rows],
        "catalog": [
            {**record, "codes": sorted(record["codes"])} for record in catalog
        ],
        "assets": {
            sha256: {
                "data": base64.b64encode(asset.data).decode("ascii"),
                "media_type": asset.media_type,
                "width": asset.width,
                "height": asset.height,
                "sha256": asset.sha256,
            }
            for sha256, asset in assets.items()
        },
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _geometry_worker(control, output, price_data, catalog_data, include_assets, confirmed_price_sha256):
    try:
        if os.name != "nt":
            import resource

            current = resource.getrlimit(resource.RLIMIT_AS)[1]
            hard = (
                _common.MAX_PDF_TEXT_WORKER_BYTES
                if current == resource.RLIM_INFINITY
                else min(current, _common.MAX_PDF_TEXT_WORKER_BYTES)
            )
            resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
        if control.recv_bytes(1) != b"G":
            return
        rows = _price_rows(None, price_data, confirmed_price_sha256)
        catalog = _catalog_records(None, catalog_data)
        assets = _manifest_assets(catalog_data) if include_assets else {}
        payload = _geometry_payload(rows, catalog, assets)
        output.send_bytes(
            b"D" + payload if len(payload) <= _MAX_GEOMETRY_RESULT_BYTES else b"L"
        )
    except Exception:
        try:
            output.send_bytes(b"X")
        except Exception:
            pass
    finally:
        control.close()
        output.close()


def _parse_sonara_documents_isolated(price_data, catalog_data, include_assets, confirmed_price_sha256):
    if (
        type(price_data) is not bytes
        or type(catalog_data) is not bytes
        or type(include_assets) is not bool
        or not isinstance(confirmed_price_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", confirmed_price_sha256)
    ):
        raise ValueError("SONARA_PDF_ARGUMENT")
    context = multiprocessing.get_context("spawn")
    control_read, control_write = context.Pipe(duplex=False)
    output_read, output_write = context.Pipe(duplex=False)
    process = context.Process(
        target=_geometry_worker,
        args=(control_read, output_write, price_data, catalog_data, include_assets, confirmed_price_sha256),
    )
    process.daemon = True
    code = None
    job = None
    payload = b""
    deadline = time.monotonic() + _MAX_GEOMETRY_SECONDS
    try:
        process.start()
        control_read.close()
        output_write.close()
        job = _common._windows_text_job(process)
        if job is None:
            code = "SONARA_PDF_LIMIT"
        else:
            control_write.send_bytes(b"G")
        remaining = deadline - time.monotonic()
        if code is None and (
            remaining <= 0 or not output_read.poll(remaining)
        ):
            code = "SONARA_PDF_LIMIT"
        if code is None:
            try:
                message = output_read.recv_bytes(_MAX_GEOMETRY_RESULT_BYTES + 1)
            except (EOFError, OSError):
                code = "SONARA_PDF_LIMIT"
            else:
                tag, payload = message[:1], message[1:]
                if tag == b"L":
                    code = "SONARA_PDF_LIMIT"
                elif tag != b"D" or not payload:
                    code = "SONARA_PDF_INVALID"
    except Exception:
        code = code or "SONARA_PDF_INVALID"
    finally:
        shutdown_code = "PDF_LIMIT" if code else None
        shutdown_code, job = _common._shutdown_pdf_worker(process, job, shutdown_code)
        if shutdown_code and code is None:
            code = "SONARA_PDF_LIMIT"
        if _common._close_pdf_connections(
            control_read, control_write, output_read, output_write
        ):
            code = code or "SONARA_PDF_INVALID"
    if code:
        raise ValueError(code)
    try:
        decoded = json.loads(payload.decode("utf-8"))
        rows = [
            {**record, "price": Decimal(record["price"])}
            for record in decoded["rows"]
        ]
        catalog = [
            {**record, "codes": set(record["codes"])}
            for record in decoded["catalog"]
        ]
        assets = {
            sha256: ImageAsset(
                base64.b64decode(record["data"], validate=True),
                record["media_type"],
                record["width"],
                record["height"],
                record["sha256"],
            )
            for sha256, record in decoded["assets"].items()
        }
    except Exception:
        raise ValueError("SONARA_PDF_INVALID") from None
    return rows, catalog, assets


def _item(
    record,
    code,
    price_refs,
    catalog_row,
    catalog_source,
    ambiguous,
    conflict,
    code_conflict,
    resolution=None,
):
    warnings = []
    if code and not code_conflict:
        identity_key = re.sub(r"[^a-z0-9]+", "-", code.casefold()).strip("-")
        product_key = (
            resolution["model"].casefold()
            if resolution is not None
            else identity_key
        )
        internal_id = f"sonara:{identity_key}"
        code_status = "verified"
        sku = code
    else:
        identity = f"{_fold(record['name'])}\0{_fold(record['description'])}"
        product_key = "review-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        internal_id = f"sonara:{product_key}"
        code_status = "needs_review"
        sku = ""
        warnings.append(
            "Codigo duplicado incompatible; verificar antes de cotizar."
            if code_conflict
            else "Codigo de proveedor faltante; verificar antes de cotizar."
        )
    price = record["price"]
    if conflict or code_conflict:
        price = Decimal(0)
        warnings.append("Precio conflictivo en la lista vigente; requiere revision.")
    if record["currency_status"] == "rejected":
        price = Decimal(0)
        warnings.append("Moneda no confirmada, extranjera o contradictoria; verificar precio.")
    if not record["plus_iva"]:
        price = Decimal(0)
        warnings.append("Tratamiento de IVA no declarado explicitamente; requiere revision.")
    attributes = {
        "row_description": record["description"],
        "source_price_printed": f"$ {record['price']:,.2f}",
        "source_currency_status": record["currency_status"],
    }
    if record["currency_status"] == "business_override":
        attributes["source_currency_rule"] = _SONARA_MXN_RULE
    dimensions = _dimension_evidence(record)
    if dimensions:
        attributes["dimensions"] = dimensions
    description = record["description"]
    evidence = list(price_refs)
    product_url = ""
    if resolution is not None:
        attributes["source_model_code"] = resolution["model"]
        attributes["product_url_match"] = {
            "status": "collection_index",
            "lookup_code": resolution["model"],
            "matched_code": resolution["model"],
            "source": "sonara.mx",
        }
        if resolution["asset_candidates"]:
            attributes["image_candidates"] = list(resolution["asset_candidates"])
            warnings.append("Dos imagenes oficiales exactas candidatas; requiere seleccion aprobada.")
        evidence.extend(resolution["references"])
        product_url = _SUSPENDED_COLLECTION_URL
    if ambiguous:
        warnings.append("Coincidencia visual ambigua; no se selecciono imagen de catalogo.")
    elif catalog_row is not None:
        description = catalog_row["description"] or description
        if catalog_row["image"] is not None:
            attributes.update(catalog_row["image"])
        evidence.append(source_ref(catalog_source.sha256, catalog_row["page"], catalog_row["bbox"]))
    if code_status == "needs_review" and "Codigo por verificar" not in warnings:
        warnings.append("Codigo por verificar")
    return {
        "internal_id": internal_id,
        "supplier": "sonara",
        "product_key": product_key,
        "sku": sku,
        "code_status": code_status,
        "brand": "Sonara",
        "collection": "",
        "name": record["name"],
        "description": description,
        "unit": _unit(record["description"]),
        "availability_type": "unknown",
        "stock": None,
        "lead_time": "",
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": record["currency"] or "XXX",
        "price_net": f"{price:.6f}",
        "tax_rate": "0.160000" if record["plus_iva"] else "0.000000",
        "attributes": attributes,
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": product_url,
        "warnings": warnings,
        "source_reference": json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
    }


def _build_sonara(files, *, include_assets: bool):
    bundle, source_data = _validated_bundle(files)
    price_source = bundle["price_list"]
    catalog_source = bundle["catalog"]
    rows, catalog, parsed_assets = _parse_sonara_documents_isolated(
        source_data["price_list"], source_data["catalog"], include_assets, _SONARA_PRICE_SHA256
    )
    grouped = defaultdict(list)
    for row in rows:
        grouped[(_fold(row["name"]), _fold(row["description"]))].append(row)
    matched = []
    for key in sorted(grouped):
        duplicates = grouped[key]
        record = duplicates[0]
        resolution = _sacc_resolution(record, price_source, catalog_source)
        if resolution is not None:
            catalog_row, ambiguous, code = None, False, resolution["sku"]
        else:
            catalog_row, ambiguous = _catalog_match(record, catalog)
            candidate_code = _code(record["name"])
            code = candidate_code if catalog_row is not None and candidate_code in catalog_row["codes"] else ""
        matched.append((duplicates, record, catalog_row, ambiguous, code, resolution))
    code_counts = defaultdict(int)
    for _, _, _, _, code, _ in matched:
        if code:
            code_counts[code] += 1
    items = []
    item_resolutions = []
    for duplicates, record, catalog_row, ambiguous, code, resolution in matched:
        references = [source_ref(price_source.sha256, row["page"], row["bbox"]) for row in duplicates]
        code_conflict = bool(code and code_counts[code] > 1)
        if code_conflict:
            catalog_row = None
        item = _item(
                record,
                code,
                references,
                catalog_row,
                catalog_source,
                ambiguous,
                len({row["price"] for row in duplicates}) > 1,
                code_conflict,
                resolution,
            )
        items.append(item)
        item_resolutions.append((item, resolution))
    items.sort(key=lambda item: item["internal_id"])
    snapshot = {
        "supplier": "sonara",
        "source_hash": _source_hash(tuple(bundle.values())),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
    }
    if not include_assets:
        return snapshot
    assets = parsed_assets if catalog_source.sha256 == _SONARA_CATALOG_SHA256 else {}
    bindings = []
    for item, resolution in item_resolutions:
        if resolution is None or not resolution["asset_sha256"]:
            continue
        asset = assets.get(resolution["asset_sha256"])
        if asset is None:
            continue
        references = (
            source_ref(catalog_source.sha256, 26, resolution["image_bbox"]),
            source_ref(catalog_source.sha256, 26, resolution["label_bbox"]),
        )
        object_name = f"{asset.sha256}.png"
        item["image_kind"] = "official"
        item["attributes"]["image_sha256"] = asset.sha256
        item["attributes"]["image_width"] = asset.width
        item["attributes"]["image_height"] = asset.height
        item["attributes"]["image_match"] = {
            "status": "exact_pdf",
            "asset_sha256": asset.sha256,
            "source_references": list(references),
        }
        item["attributes"]["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": object_name,
            "image_kind": "official",
            "label": "Imagen oficial exacta del PDF Sonara",
            "approved": True,
        }
        bindings.append(
            CatalogAssetBinding(
                item["internal_id"],
                asset.sha256,
                object_name,
                "official",
                "exact_pdf",
                references,
            )
        )
    return CatalogSnapshotBuild(
        snapshot,
        assets,
        tuple(sorted(bindings, key=lambda binding: binding.internal_id)),
    )


def build_sonara_snapshot(files) -> dict:
    return _build_sonara(files, include_assets=False)


def build_sonara_snapshot_with_assets(files) -> CatalogSnapshotBuild:
    return _build_sonara(files, include_assets=True)
