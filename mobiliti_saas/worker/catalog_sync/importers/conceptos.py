"""Importador local del libro oficial de sofás Conceptos.

Los dos tableros del libro se unen por código publicado y por el bloque de
filas que el propio XLSX prueba (celda de código combinada o fila con código).
No se infiere parentesco desde las descripciones comerciales.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    CellRef,
    ImageAsset,
    extract_xlsx_images_from_bytes,
    neutralize_spreadsheet_text,
    open_xlsx_data_only_from_bytes,
    read_validated_source,
    source_ref,
)


_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SPEC_SHEET = "Spec sofas - Cdmx-Gdl-Qro"
_COST_SHEET = "Costo Sofas - Cdmx-Gdl-Qro"
_HEADER_ROW = 8
_MONEY_LIMIT = Decimal("1000000000")
_SPEC_HEADERS = ("imagen", "codigo", "descripcion", "material", "medidas", "unidad")
_COST_HEADERS = (
    "imagen", "codigo", "descripcion", "material", "costo", "unidad", "referencia", "medidas"
)
_OFFICIAL_SPEC_HEADERS = (
    "imagen", "codigo", "descripcion", "unidad", "precio-venta", "moneda",
)
_OFFICIAL_COST_HEADERS = (
    "imagen", "codigo", "descripcion", "unidad", "precio-unitario",
    "utilidad-50", "precio-venta", "moneda",
)


@dataclass(frozen=True)
class _Block:
    code: str
    code_key: str
    start_row: int
    rows: tuple[int, ...]


@dataclass(frozen=True)
class _AnchoredImage:
    reference: CellRef
    asset: ImageAsset
    start_row: int
    end_row_exclusive: int


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", neutralize_spreadsheet_text(value)).strip()


def _key(value: object) -> str:
    text = unicodedata.normalize("NFKD", _text(value))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return "-".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _decimal(value: object, *, required: bool = False) -> Decimal | None:
    if value is None or isinstance(value, bool):
        if required:
            raise ValueError("CONCEPTOS_COST")
        return None
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, float)):
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            result = Decimal("NaN")
    elif isinstance(value, str):
        clean = value.strip().replace("$", "").replace(",", "")
        if not clean:
            if required:
                raise ValueError("CONCEPTOS_COST")
            return None
        try:
            result = Decimal(clean)
        except InvalidOperation:
            result = Decimal("NaN")
    else:
        if required:
            raise ValueError("CONCEPTOS_COST")
        return None
    if not result.is_finite() or result <= 0 or result > _MONEY_LIMIT:
        if required:
            raise ValueError("CONCEPTOS_COST")
        return None
    return result.normalize()


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.6f}"


def _validated_file(files: object) -> tuple[object, bytes]:
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("CONCEPTOS_BUNDLE") from None
    if len(rows) != 1:
        raise ValueError("CONCEPTOS_BUNDLE")
    document = rows[0]
    path = getattr(document, "path", None)
    local_path = getattr(document, "local_path", None)
    declared_hash = getattr(document, "sha256", None)
    if (
        getattr(document, "kind", None) != "spec_guide"
        or not isinstance(path, str)
        or Path(path).suffix.casefold() != ".xlsx"
        or not isinstance(local_path, Path)
        or local_path.suffix.casefold() != ".xlsx"
        or getattr(document, "mime_type", None) != _MIME
        or not isinstance(declared_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
    ):
        raise ValueError("CONCEPTOS_BUNDLE")
    validated, data = read_validated_source(local_path, ".xlsx")
    if validated.sha256 != declared_hash:
        raise ValueError("CONCEPTOS_HASH")
    return document, data


def _code_blocks(sheet, *, contiguous: bool = False) -> tuple[_Block, ...]:
    """Extrae bloques demostrados; un vacío nunca hereda del renglón previo."""
    merged_by_row: dict[int, tuple[int, int]] = {}
    for merged in sheet.merged_cells.ranges:
        if merged.min_col <= 2 <= merged.max_col and merged.max_row >= _HEADER_ROW + 1:
            anchor = _text(sheet.cell(merged.min_row, 2).value)
            if anchor:
                for row in range(merged.min_row, merged.max_row + 1):
                    merged_by_row[row] = (merged.min_row, merged.max_row)

    draft: list[tuple[str, int, tuple[int, ...]]] = []
    seen_starts: set[int] = set()
    for row in range(_HEADER_ROW + 1, sheet.max_row + 1):
        merged = merged_by_row.get(row)
        if merged is not None:
            start, end = merged
            if start in seen_starts or row != start:
                continue
            seen_starts.add(start)
            code = _text(sheet.cell(start, 2).value)
            draft.append((code, start, tuple(range(start, end + 1))))
            continue
        code = _text(sheet.cell(row, 2).value)
        if code:
            draft.append((code, row, (row,)))

    blocks = []
    for code, start, rows in draft:
        normalized = _key(code)
        if not normalized:
            continue
        blocks.append(_Block(code, normalized, start, rows))
    if contiguous:
        grouped: list[_Block] = []
        for block in blocks:
            if grouped and block.start_row == grouped[-1].rows[-1] + 1:
                previous = grouped[-1]
                grouped[-1] = _Block(
                    previous.code,
                    previous.code_key,
                    previous.start_row,
                    previous.rows + block.rows,
                )
            else:
                grouped.append(block)
        return tuple(grouped)
    return tuple(blocks)


def _header_layout(spec, cost) -> str:
    spec_headers = tuple(_key(spec.cell(_HEADER_ROW, column).value) for column in range(1, 7))
    cost_headers = tuple(_key(cost.cell(_HEADER_ROW, column).value) for column in range(1, 9))
    if spec_headers == _SPEC_HEADERS and cost_headers == _COST_HEADERS:
        return "detailed"
    if spec_headers == _OFFICIAL_SPEC_HEADERS and cost_headers == _OFFICIAL_COST_HEADERS:
        return "official"
    if spec_headers != _SPEC_HEADERS or cost_headers != _COST_HEADERS:
        raise ValueError("CONCEPTOS_HEADERS")


def _sheet_image_spans(
    images: dict[CellRef, ImageAsset], sheet, sheet_name: str,
) -> tuple[_AnchoredImage, ...]:
    """Conserva el intervalo OOXML real; el dibujo puede iniciar antes del bloque."""

    found: list[_AnchoredImage] = []
    for drawing in getattr(sheet, "_images", ()):
        anchor = getattr(drawing, "anchor", None)
        start = getattr(anchor, "_from", None)
        if start is None or start.col != 0:
            continue
        start_row = start.row + 1
        end = getattr(anchor, "to", None)
        end_row_exclusive = end.row + 1 if end is not None else start_row + 1
        if end_row_exclusive <= start_row:
            end_row_exclusive = start_row + 1
        reference = CellRef(sheet_name, f"A{start_row}")
        asset = images.get(reference)
        if asset is None:
            continue
        found.append(
            _AnchoredImage(reference, asset, start_row, end_row_exclusive)
        )
    found.sort(key=lambda row: (row.start_row, row.end_row_exclusive, row.asset.sha256))
    return tuple(found)


def _image_for_block(
    block: _Block, images: tuple[_AnchoredImage, ...],
) -> _AnchoredImage | None:
    candidates = tuple(
        image
        for image in images
        if image.start_row <= block.rows[-1]
        and image.end_row_exclusive > block.start_row
    )
    if len(candidates) > 1:
        raise ValueError("CONCEPTOS_IMAGE_BLOCK")
    return candidates[0] if candidates else None


def _block_key(block: _Block) -> tuple[str, int, int]:
    """Identidad de unión publicada por ambos tableros, no por descripción."""
    return block.code_key, block.start_row, len(block.rows)


def _source_hash(document: object) -> str:
    material = f"conceptos-v1\0{document.path}\0{document.sha256}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _identity(code_key: str, block_start: int) -> str:
    return f"conceptos:{code_key}:{block_start}"


def _variant_id(
    identity: str, material: str, dimensions: str, unit: str, published_code: str = "",
) -> str:
    material_key = "\0".join(
        (_key(material), _key(dimensions), _key(unit), _key(published_code))
    )
    digest = hashlib.sha256(material_key.encode("utf-8")).hexdigest()[:16]
    return f"{identity}:variante:{digest}"


def parse_conceptos_rows(files) -> tuple[dict, ...]:
    """Lee las variantes Conceptos, usando solo E como costo y G como referencia."""
    document, data = _validated_file(files)
    images = extract_xlsx_images_from_bytes(data)
    workbook = open_xlsx_data_only_from_bytes(data)
    try:
        if _SPEC_SHEET not in workbook.sheetnames or _COST_SHEET not in workbook.sheetnames:
            raise ValueError("CONCEPTOS_SHEETS")
        spec = workbook[_SPEC_SHEET]
        cost = workbook[_COST_SHEET]
        layout = _header_layout(spec, cost)
        spec_blocks = {
            _block_key(block): block
            for block in _code_blocks(spec, contiguous=layout == "official")
        }
        cost_blocks = {_block_key(block): block for block in _code_blocks(cost)}
        if not spec_blocks or not cost_blocks:
            raise ValueError("CONCEPTOS_BLOCK_MISMATCH")
        if layout == "detailed" and set(spec_blocks) != set(cost_blocks):
            raise ValueError("CONCEPTOS_BLOCK_MISMATCH")

        image_spans = _sheet_image_spans(images, spec, _SPEC_SHEET)
        records: list[dict] = []
        known_costs: dict[tuple[str, str, str, str, str], Decimal] = {}
        known_variants: set[tuple[str, str, str, str, str]] = set()
        for key in sorted(spec_blocks, key=lambda value: spec_blocks[value].start_row):
            spec_block = spec_blocks[key]
            if layout == "detailed":
                cost_block = cost_blocks[key]
                if len(spec_block.rows) != len(cost_block.rows):
                    raise ValueError("CONCEPTOS_BLOCK_MISMATCH")
            else:
                if any(
                    _key(cost.cell(row, 2).value) != _key(spec.cell(row, 2).value)
                    for row in spec_block.rows
                ):
                    raise ValueError("CONCEPTOS_BLOCK_MISMATCH")
                cost_block = _Block(
                    spec_block.code, spec_block.code_key,
                    spec_block.start_row, spec_block.rows,
                )
            image = _image_for_block(spec_block, image_spans)
            image_payload = None
            if image is not None:
                reference, asset = image.reference, image.asset
                image_payload = {
                    "sha256": asset.sha256,
                    "media_type": asset.media_type,
                    "width": asset.width,
                    "height": asset.height,
                    "source_reference": source_ref(document.sha256, reference.sheet, reference.cell),
                }
            identity = _identity(spec_block.code_key, spec_block.start_row)
            for spec_row, cost_row in zip(spec_block.rows, cost_block.rows, strict=True):
                name = _text(spec.cell(spec_row, 3).value)
                material = _text(spec.cell(spec_row, 4).value) if layout == "detailed" else ""
                dimensions = _text(spec.cell(spec_row, 5).value) if layout == "detailed" else ""
                unit = _text(spec.cell(spec_row, 6).value if layout == "detailed" else spec.cell(spec_row, 4).value)
                raw_cost = _decimal(cost.cell(cost_row, 5).value)
                if not name or raw_cost is None:
                    continue
                reference_price = _decimal(cost.cell(cost_row, 7).value)
                published_variant_code = _text(spec.cell(spec_row, 2).value)
                variant_key = (
                    identity,
                    _key(material),
                    _key(dimensions),
                    _key(unit),
                    _key(published_variant_code) if layout == "official" else "",
                )
                existing_cost = known_costs.get(variant_key)
                if existing_cost is not None and existing_cost != raw_cost:
                    raise ValueError("CONCEPTOS_CONFLICTING_COST")
                if variant_key in known_variants:
                    continue
                known_costs[variant_key] = raw_cost
                known_variants.add(variant_key)
                record = {
                    "identity": identity,
                    "variant_id": _variant_id(
                        identity,
                        material,
                        dimensions,
                        unit,
                        published_variant_code if layout == "official" else "",
                    ),
                    "code": spec_block.code,
                    "variant_code": published_variant_code,
                    "name": name,
                    "description": name,
                    "material": material,
                    "dimensions": dimensions,
                    "unit": unit,
                    "raw_cost": raw_cost,
                    "reference_price_mxn": reference_price,
                    "base_currency": "MXN",
                    "provenance": {
                        "file": document.path,
                        "file_hash": document.sha256,
                        "spec_sheet": _SPEC_SHEET,
                        "spec_row": spec_row,
                        "cost_sheet": _COST_SHEET,
                        "cost_row": cost_row,
                        "code_cell": f"B{spec_block.start_row}",
                        "cost_cell": f"E{cost_row}",
                        "reference_cell": f"G{cost_row}",
                    },
                }
                if image_payload is not None:
                    record["image"] = image_payload
                records.append(record)
    finally:
        workbook.close()
    if not records:
        raise ValueError("CONCEPTOS_EMPTY")
    return tuple(records)


def _public_snapshot(rows: tuple[dict, ...], document: object, *, include_assets: bool):
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["identity"], []).append(row)
    code_counts = Counter(rows[0]["code"].casefold() for rows in grouped.values())
    items = []
    selected_assets: dict[str, ImageAsset] = {}
    bindings = []
    source_assets = (
        extract_xlsx_images_from_bytes(_validated_file((document,))[1])
        if include_assets
        else {}
    )
    for identity, variants in sorted(grouped.items()):
        primary = variants[0]
        published_code = primary["code"]
        duplicate_code = code_counts[published_code.casefold()] > 1
        options = [
            {
                "id": row["variant_id"],
                "name": " · ".join(value for value in (row["material"], row["dimensions"]) if value) or row["name"],
                "price_net": _money(row["raw_cost"]),
                "available": True,
            }
            for row in variants
        ]
        references = [
            source_ref(row["provenance"]["file_hash"], row["provenance"]["cost_sheet"], row["provenance"]["cost_cell"])
            for row in variants
        ]
        attributes = {
            "source_code": published_code,
            "variants": [
                {
                    "id": row["variant_id"],
                    "material": row["material"],
                    "dimensions": row["dimensions"],
                    "unit": row["unit"],
                    "cost_mxn": _money(row["raw_cost"]),
                    "reference_price_mxn": _money(row["reference_price_mxn"]),
                    "provenance": row["provenance"],
                }
                for row in variants
            ],
        }
        warnings = ["Codigo duplicado entre bloques; SKU requiere revision."] if duplicate_code else []
        image = next((row.get("image") for row in variants if row.get("image") is not None), None)
        image_kind = "placeholder"
        if include_assets and image is not None:
            asset = source_assets.get(
                CellRef(_SPEC_SHEET, image["source_reference"]["cell_or_bbox"])
            )
            if asset is not None:
                object_name = f"{asset.sha256}.png"
                image_kind = "official"
                selected_assets[asset.sha256] = asset
                attributes["image_match"] = {
                    "status": "merged_xlsx",
                    "asset_sha256": asset.sha256,
                    "source_references": [image["source_reference"]],
                }
                attributes["approved_asset"] = {
                    "bucket": "catalog-assets",
                    "path": object_name,
                    "image_kind": "official",
                    "label": "Imagen oficial del bloque XLSX Conceptos",
                    "approved": True,
                }
                bindings.append(
                    CatalogAssetBinding(identity, asset.sha256, object_name, "official", "merged_xlsx", (image["source_reference"],))
                )
        items.append(
            {
                "internal_id": identity,
                "supplier": "conceptos",
                "product_key": identity,
                "sku": "" if duplicate_code else published_code,
                "code_status": "needs_review" if duplicate_code else "verified",
                "brand": "Conceptos",
                "collection": "Sofas",
                "name": primary["name"],
                "description": primary["description"],
                "unit": primary["unit"] or "PZA",
                "availability_type": "made_to_order",
                "stock": None,
                "lead_time": "",
                "base_price_options": options,
                "add_on_options": [],
                "base_currency": "MXN",
                "price_net": options[0]["price_net"],
                "tax_rate": "0.160000",
                "attributes": attributes,
                "image_url": "",
                "image_kind": image_kind,
                "product_url": "",
                "warnings": warnings,
                "source_reference": json.dumps(references, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
            }
        )
    items.sort(key=lambda item: item["internal_id"])
    bindings.sort(key=lambda binding: binding.internal_id)
    snapshot = {
        "supplier": "conceptos",
        "source_hash": _source_hash(document),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    return snapshot, selected_assets, tuple(bindings)


def build_conceptos_snapshot(files) -> dict:
    """Construye el snapshot público sin cargar activos binarios."""
    document, _ = _validated_file(files)
    rows = parse_conceptos_rows((document,))
    snapshot, _, _ = _public_snapshot(rows, document, include_assets=False)
    return snapshot


def build_conceptos_snapshot_with_assets(files) -> CatalogSnapshotBuild:
    """Construye el snapshot público y sus únicos activos OOXML aprobados."""
    document, _ = _validated_file(files)
    rows = parse_conceptos_rows((document,))
    snapshot, assets, bindings = _public_snapshot(rows, document, include_assets=True)
    return CatalogSnapshotBuild(snapshot, assets, bindings)
