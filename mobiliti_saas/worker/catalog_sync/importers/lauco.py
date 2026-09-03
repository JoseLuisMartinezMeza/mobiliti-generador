"""Importador de costos MXN del catálogo oficial Lauco."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping

from ..xlsb_source import XlsbSource, read_validated_xlsb_source
from .common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    MAX_FILE_BYTES,
    SourceSafetyError,
    neutralize_spreadsheet_text,
    source_ref,
)


_SHEET = "COSTO-LAUCO-2026"
_MIME = "application/vnd.ms-excel.sheet.binary.macroEnabled.12"


def _fail(code: str) -> None:
    raise SourceSafetyError(code) from None


def _text(value: object) -> str:
    return " ".join(str(value or "").split())[:1000]


def _money(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    result = result.normalize()
    return result if result.is_finite() and 0 < result <= Decimal("1000000000") else None


def _key(value: str) -> str:
    return "-".join("".join(character if character.isalnum() else " " for character in value.casefold()).split()) or "sin-codigo"


def _is_legs(option: str) -> bool:
    normalized = option.casefold()
    return "pata" in normalized


def parse_lauco_rows(
    rows: Iterable[Mapping[str, object]], *, file_id: str, source_hash: str
) -> list[dict]:
    """Convierte filas B:K en opciones de costo, sin consultar nunca K.

    Los renglones que omiten B/C/D heredan el producto de su bloque inmediato.
    La fila fuente es parte de la identidad para no colapsar códigos duplicados.
    """
    if not isinstance(file_id, str) or not file_id or len(source_hash) != 64:
        _fail("LAUCO_SOURCE")
    products: list[dict] = []
    current: dict | None = None
    for raw in rows:
        if not isinstance(raw, Mapping) or type(raw.get("row")) is not int or raw["row"] < 1:
            _fail("LAUCO_ROW")
        row_number = raw["row"]
        code = _text(raw.get("B"))
        description = _text(raw.get("C"))
        measure = _text(raw.get("D"))
        if code:
            current = {"code": code, "description": description, "measure": measure, "start_row": row_number, "options": []}
            products.append(current)
        if current is None:
            continue
        option_name = _text(raw.get("E"))
        cost = _money(raw.get("F"))
        if not option_name or cost is None:
            continue
        declared_currency = _text(raw.get("G")).upper() or "UNDECLARED"
        option_id = f"lauco:{_key(current['code'])}:{row_number}"
        current["options"].append(
            {
                "id": option_id,
                "internal_id": option_id,
                "product_id": f"lauco:{_key(current['code'])}:{current['start_row']}",
                "source_row": row_number,
                "source_start_row": current["start_row"],
                "source_code": current["code"],
                "name": neutralize_spreadsheet_text(option_name),
                "description": neutralize_spreadsheet_text(current["description"]),
                "measure": neutralize_spreadsheet_text(current["measure"]),
                "raw_cost": cost,
                "base_currency": "MXN",
                "option_kind": "add_on" if _is_legs(option_name) else "base",
                "compatible_base_option_ids": [],
                "provenance": {
                    "file_id": file_id,
                    "source_hash": source_hash,
                    "sheet": _SHEET,
                    "cost_cell": f"F{row_number}",
                    "currency_cell": f"G{row_number}",
                    "declared_currency": declared_currency,
                    "currency_normalization": (
                        None if declared_currency == "MXN" else "human_source_error_to_mxn"
                    ),
                },
            }
        )
    flattened = []
    for product in products:
        bases = [option["id"] for option in product["options"] if option["option_kind"] == "base"]
        for option in product["options"]:
            if option["option_kind"] == "add_on":
                option["compatible_base_option_ids"] = bases
            flattened.append(option)
    return flattened


def _document_source(document: object) -> tuple[XlsbSource, str]:
    if isinstance(document, XlsbSource):
        return document, document.sha256
    if isinstance(document, bytes):
        source = read_validated_xlsb_source(document)
        return source, source.sha256
    local_path = getattr(document, "local_path", None)
    if (
        not isinstance(local_path, Path)
        or local_path.suffix.casefold() != ".xlsb"
        or getattr(document, "mime_type", None) != _MIME
    ):
        _fail("LAUCO_SOURCE")
    try:
        data = local_path.read_bytes()
    except OSError:
        _fail("LAUCO_SOURCE")
    if not 0 < len(data) <= MAX_FILE_BYTES:
        _fail("LAUCO_SOURCE")
    source = read_validated_xlsb_source(data)
    documented_hash = getattr(document, "sha256", None)
    if documented_hash is not None and documented_hash != source.sha256:
        _fail("LAUCO_SOURCE")
    return source, source.sha256


def import_lauco_catalog(document: object, *, synced_at: str | None = None) -> dict:
    """Importa únicamente las opciones costeadas de ``COSTO-LAUCO-2026``."""
    source, source_hash = _document_source(document)
    rows = []
    for number, values in enumerate(source.iter_rows(_SHEET), 1):
        row = {"row": number}
        for column, name in enumerate(("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K")):
            row[name] = values[column] if column < len(values) else None
        rows.append(row)
    items = parse_lauco_rows(rows, file_id=source_hash, source_hash=source_hash)
    if not items:
        _fail("LAUCO_STRUCTURE")
    return {
        "supplier": "lauco",
        "source_hash": source_hash,
        "generated_at": synced_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
    }


def _option_snapshot(option: dict) -> dict:
    return {
        "id": option["id"],
        "name": option["name"],
        "price_net": f"{option['raw_cost']:.6f}",
        "available": True,
    }


def _asset_for_product(images, options):
    start = min(option["source_start_row"] for option in options)
    end = max(option["source_row"] for option in options)
    candidates = [
        (reference, asset)
        for reference, asset in images.items()
        if reference.sheet == _SHEET
        and reference.cell.startswith("A")
        and start <= int(reference.cell[1:]) <= end
    ]
    return min(candidates, key=lambda row: row[0].cell) if candidates else None


def _snapshot_item(product_id: str, options: list[dict], *, duplicate_code: bool, image):
    first = options[0]
    bases = [option for option in options if option["option_kind"] == "base"]
    add_ons = [option for option in options if option["option_kind"] == "add_on"]
    code = first["source_code"]
    code_status = "needs_review" if duplicate_code else "verified"
    warnings = []
    if duplicate_code:
        warnings.append("Código duplicado con configuraciones distintas; verificar antes de cotizar.")
    evidence = [
        {
            "raw_cost": format(option["raw_cost"], "f"),
            "declared_currency": option["provenance"]["declared_currency"],
            "currency_normalization": option["provenance"]["currency_normalization"],
            "source": source_ref(
                option["provenance"]["source_hash"], _SHEET, option["provenance"]["cost_cell"]
            ),
        }
        for option in options
    ]
    attributes = {
        "source_code": code,
        "dimensions": first["measure"],
        "price_evidence": evidence,
    }
    assets = []
    if image is not None:
        reference, asset = image
        image_source = source_ref(first["provenance"]["source_hash"], _SHEET, reference.cell)
        attributes["source_images"] = [{
            "sha256": asset.sha256,
            "width": asset.width,
            "height": asset.height,
            "source": image_source,
        }]
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": f"{asset.sha256}.png",
            "image_kind": "official",
            "approved": True,
        }
        attributes["image_match"] = {
            "status": "exact_xlsx",
            "asset_sha256": asset.sha256,
            "source_references": [image_source],
        }
        assets.append((asset, image_source))
    item = {
        "internal_id": product_id,
        "supplier": "lauco",
        "product_key": product_id.removeprefix("lauco:"),
        "sku": "" if duplicate_code else code,
        "code_status": code_status,
        "brand": "Lauco",
        "collection": "Sofas",
        "name": neutralize_spreadsheet_text(code),
        "description": first["description"],
        "unit": "PZA",
        "availability_type": "made_to_order",
        "stock": None,
        "lead_time": "Sobre pedido",
        "base_price_options": [_option_snapshot(option) for option in bases],
        "add_on_options": [
            _option_snapshot(option) | {
                "family": "legs",
                "compatible_base_option_ids": option["compatible_base_option_ids"],
            }
            for option in add_ons
        ],
        "base_currency": "MXN",
        "price_net": "0.000000" if bases else f"{add_ons[0]['raw_cost']:.6f}" if add_ons else "0.000000",
        "tax_rate": "0.160000",
        "attributes": attributes,
        "image_url": "",
        "image_kind": "official" if assets else "placeholder",
        "product_url": "",
        "warnings": warnings,
        "source_reference": json.dumps(
            [row["source"] for row in evidence],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    }
    return item, assets


def _validated_adapter_file(files) -> object:
    rows = tuple(files)
    if len(rows) != 1:
        _fail("LAUCO_BUNDLE")
    row = rows[0]
    if (
        not isinstance(getattr(row, "path", None), str)
        or not getattr(row, "path").casefold().endswith(".xlsb")
        or getattr(row, "kind", None) != "spec_guide"
        or getattr(row, "brand", None) != "Lauco"
        or getattr(row, "mime_type", None) != _MIME
        or not isinstance(getattr(row, "sha256", None), str)
        or re.fullmatch(r"[0-9a-f]{64}", row.sha256) is None
        or not isinstance(getattr(row, "local_path", None), Path)
    ):
        _fail("LAUCO_BUNDLE")
    return row


def _build_lauco(files, *, include_assets: bool):
    document = _validated_adapter_file(files)
    source, source_hash = _document_source(document)
    raw = import_lauco_catalog(source)
    grouped: dict[str, list[dict]] = {}
    for option in raw["items"]:
        grouped.setdefault(option["product_id"], []).append(option)
    code_counts: dict[str, int] = {}
    for options in grouped.values():
        code = options[0]["source_code"].casefold()
        code_counts[code] = code_counts.get(code, 0) + 1
    images = source.image_anchors() if include_assets else {}
    items = []
    bindings = []
    assets_by_sha256 = {}
    for product_id, options in sorted(grouped.items()):
        item, assets = _snapshot_item(
            product_id,
            options,
            duplicate_code=code_counts[options[0]["source_code"].casefold()] > 1,
            image=_asset_for_product(images, options),
        )
        items.append(item)
        for asset, reference in assets:
            assets_by_sha256[asset.sha256] = asset
            bindings.append(CatalogAssetBinding(
                item["internal_id"],
                asset.sha256,
                f"{asset.sha256}.png",
                "official",
                "exact_xlsx",
                (reference,),
            ))
    if not items:
        _fail("LAUCO_STRUCTURE")
    snapshot = {
        "supplier": "lauco",
        "source_hash": source_hash,
        "generated_at": raw["generated_at"],
        "items": items,
    }
    if not include_assets:
        return snapshot
    return CatalogSnapshotBuild(
        snapshot,
        assets_by_sha256,
        tuple(sorted(bindings, key=lambda binding: binding.internal_id)),
    )


def build_lauco_snapshot(files) -> dict:
    return _build_lauco(files, include_assets=False)


def build_lauco_snapshot_with_assets(files) -> CatalogSnapshotBuild:
    return _build_lauco(files, include_assets=True)
