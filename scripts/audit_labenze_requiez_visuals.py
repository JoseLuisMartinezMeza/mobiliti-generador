"""Auditoría local reproducible y de sólo lectura para Labenze/Requiez."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import re
import subprocess
import sys
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPECTED_PDF_SHA256 = {
    "labenze": "c4fc2d2152b5e854f7c36c9106c71cd21853abb50efcde96ba2566cb72f1d6f3",
    "requiez": "7f3281d1965c67a234bac55112800067019ad471f835de59ff758e759eca56ba",
}
EXPECTED_COUNTS = {"labenze": 462, "requiez": 314}
EXPECTED_MATCH_STATUS = {"exact_pdf": 203, "family_pdf": 417, "placeholder": 156}
ASSET_NAME = re.compile(r"^[0-9a-f]{64}\.png$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
REQUIEZ_FOCALS = (
    "RM-9025N/NG",
    "RE-1063M",
    "RE-1064M",
    "RE-1073M",
    "RM-9100/GR",
    "RM-9100/NG",
    "RM-9101/GR",
    "RE-822/PU/MP",
    "RE-828/PU/PN",
    "RA-28",
)
REQUIEZ_JUN_M = (
    "requiez:re-1063m",
    "requiez:re-1064m",
    "requiez:re-1073m",
)
REQUIEZ_EXACT_FOCALS = (
    "requiez:rm-9025n-ng",
    "requiez:rm-9100-gr",
    "requiez:rm-9100-ng",
    "requiez:rm-9101-gr",
    "requiez:re-822-pu-mp",
    "requiez:re-828-pu-pn",
    "requiez:ra-28",
)
LABENZE_BAT = "labenze:106-00603-bat"
LABENZE_ZELIG = (
    "labenze:155-19100-000",
    "labenze:155-19110-000",
    "labenze:155-19120-000",
    "labenze:155-19130",
    "labenze:155-19140",
    "labenze:155-19150",
    "labenze:155-19160",
    "labenze:155-19170-nat",
)
LABENZE_NEEDS_REVIEW = (
    "labenze:review:155-10420-xxx:1489741a93de7217494e",
    "labenze:review:155-22700-000:bd4d5204d4423966bf16",
    "labenze:review:155-22700-000:e30c754d8b6b34f71436",
    "labenze:review:155-22700-000:fb9c0eeda6fb7e0cba32",
    "labenze:review:155-23100-bas:5203792ecf94242158b1",
    "labenze:review:155-23100-bas:d19c3ee715be140145b3",
    "labenze:review:155-23100-bas:e27c0da632edbc653034",
    "labenze:review:160-090xx:098f940e62008e4b561c",
)
CONTACT_COLUMNS = 4
CONTACT_ROWS = 5
CARD_WIDTH = 360
CARD_HEIGHT = 280
IMAGE_BOX = (12, 10, 348, 196)


@dataclass(frozen=True)
class AuditSourceFile:
    path: str
    kind: str
    brand: None
    sha256: str
    mime_type: str
    local_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog_builders():
    try:
        labenze_module = importlib.import_module(
            "mobiliti_saas.worker.catalog_sync.importers.labenze"
        )
        requiez_module = importlib.import_module(
            "mobiliti_saas.worker.catalog_sync.importers.requiez"
        )
        labenze_builder = getattr(labenze_module, "build_labenze_snapshot")
        requiez_builder = getattr(requiez_module, "build_requiez_snapshot")
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("catalog importers unavailable") from exc
    if not callable(labenze_builder) or not callable(requiez_builder):
        raise RuntimeError("catalog importers unavailable")
    return labenze_builder, requiez_builder


def validate_output_path(output_path: Path, input_paths) -> Path:
    resolved_output = Path(output_path).resolve()
    for raw_input in input_paths:
        resolved_input = Path(raw_input).resolve()
        if (
            resolved_output == resolved_input
            or resolved_output.is_relative_to(resolved_input)
            or resolved_input.is_relative_to(resolved_output)
        ):
            raise ValueError(
                f"AUDIT_OUTPUT_UNSAFE:{resolved_output}:{resolved_input}"
            )
    return resolved_output


def asset_tree_fingerprint(assets_dir: Path) -> dict:
    root = Path(assets_dir).resolve()
    if not root.is_dir():
        raise ValueError("AUDIT_ASSETS_MISSING")
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"AUDIT_ASSET_OUTSIDE_ROOT:{path}")
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_hash = _sha256_file(path)
        digest.update(f"{relative}\0{size}\0{file_hash}\n".encode("utf-8"))
        files += 1
        total_bytes += size
    return {"files": files, "bytes": total_bytes, "sha256": digest.hexdigest()}


def build_reproducible_command(arguments: list[str]) -> str:
    return subprocess.list2cmdline([str(argument) for argument in arguments])


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _source_hash(item: dict) -> str:
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    return str(attributes.get("source_sha256") or attributes.get("source_file_sha256") or "")


def _match_status(item: dict) -> str:
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    image_match = attributes.get("image_match")
    if isinstance(image_match, dict) and image_match.get("status"):
        return str(image_match["status"])
    return "placeholder"


def _items(snapshot: dict, supplier: str, *, stored: bool) -> dict[str, dict]:
    if not isinstance(snapshot, dict):
        raise ValueError(f"AUDIT_SNAPSHOT_MISSING:{supplier}")
    payload = snapshot.get("payload") if stored else snapshot
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError(f"AUDIT_SNAPSHOT_MISSING:{supplier}")
    rows = payload["items"]
    if len(rows) != EXPECTED_COUNTS[supplier]:
        raise ValueError(f"AUDIT_CARDINALITY:{supplier}")
    result: dict[str, dict] = {}
    for row in rows:
        internal_id = str(row.get("internal_id") or "") if isinstance(row, dict) else ""
        if not internal_id or internal_id in result:
            raise ValueError(f"AUDIT_DUPLICATE_ID:{supplier}:{internal_id}")
        result[internal_id] = row
    return result


def _validate_inputs(rebuilt: dict, store: dict, input_hashes: dict[str, str]) -> dict[str, tuple[dict, dict]]:
    published = store.get("catalog_published_snapshots") if isinstance(store, dict) else None
    if not isinstance(published, dict):
        raise ValueError("AUDIT_SNAPSHOT_MISSING")
    paired = {}
    for supplier in EXPECTED_COUNTS:
        if supplier not in rebuilt or supplier not in published:
            raise ValueError(f"AUDIT_SNAPSHOT_MISSING:{supplier}")
        rebuilt_items = _items(rebuilt[supplier], supplier, stored=False)
        stored_items = _items(published[supplier], supplier, stored=True)
        if set(rebuilt_items) != set(stored_items):
            raise ValueError(f"AUDIT_IDENTITY_MISMATCH:{supplier}:internal_id")
        expected_source = str(input_hashes.get(supplier) or "")
        for internal_id in sorted(rebuilt_items):
            current = stored_items[internal_id]
            fresh = rebuilt_items[internal_id]
            current_identity = (
                current.get("product_key"),
                current.get("sku"),
                (current.get("attributes") or {}).get("source_code"),
            )
            rebuilt_identity = (
                fresh.get("product_key"),
                fresh.get("sku"),
                (fresh.get("attributes") or {}).get("source_code"),
            )
            if current_identity != rebuilt_identity:
                raise ValueError(f"AUDIT_IDENTITY_MISMATCH:{supplier}:{internal_id}")
            if _source_hash(current) != expected_source or _source_hash(fresh) != expected_source:
                raise ValueError(f"AUDIT_SOURCE_HASH:{supplier}:{internal_id}")
        paired[supplier] = (rebuilt_items, stored_items)
    statuses = Counter(
        _match_status(item)
        for _fresh, stored_items in paired.values()
        for item in stored_items.values()
    )
    if dict(sorted(statuses.items())) != EXPECTED_MATCH_STATUS:
        raise ValueError(f"AUDIT_BASELINE_STATUS:{dict(sorted(statuses.items()))}")
    return paired


def _mask_metrics(image: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int] | None, int]:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    corners = [
        rgba.getpixel(point)
        for point in (
            (0, 0),
            (width - 1, 0),
            (0, height - 1),
            (width - 1, height - 1),
        )
    ]
    opaque_corners = [pixel for pixel in corners if pixel[3] >= 16]
    transparent_canvas = not opaque_corners
    if opaque_corners:
        background = tuple(
            round(sum(pixel[channel] for pixel in opaque_corners) / len(opaque_corners))
            for channel in range(3)
        )
    else:
        background = (255, 255, 255)
    mask_data = []
    foreground = 0
    pixel_data = (
        rgba.get_flattened_data()
        if hasattr(rgba, "get_flattened_data")
        else rgba.getdata()
    )
    for red, green, blue, alpha in pixel_data:
        is_foreground = alpha >= 16 and (
            transparent_canvas
            or max(
                abs(red - background[0]),
                abs(green - background[1]),
                abs(blue - background[2]),
            )
            > 20
        )
        mask_data.append(255 if is_foreground else 0)
        foreground += int(is_foreground)
    mask = Image.new("L", rgba.size)
    mask.putdata(mask_data)
    bbox = mask.getbbox()
    return mask, bbox, foreground


def _has_rule_signal(mask: Image.Image, bbox: tuple[int, int, int, int] | None) -> bool:
    if bbox is None:
        return False
    width, height = mask.size
    if bbox[0] == 0 or bbox[1] == 0 or bbox[2] == width or bbox[3] == height:
        return True
    reduced = mask.resize((min(width, 512), min(height, 512)), Image.Resampling.NEAREST)
    pixels = reduced.load()
    row_hits = [sum(pixels[x, y] > 0 for x in range(reduced.width)) for y in range(reduced.height)]
    column_hits = [sum(pixels[x, y] > 0 for y in range(reduced.height)) for x in range(reduced.width)]

    def thin_run(values: list[int], threshold: float) -> bool:
        run = 0
        for value in values + [0]:
            if value >= threshold:
                run += 1
            elif run:
                if run <= 3:
                    return True
                run = 0
        return False

    return thin_run(row_hits, reduced.width * 0.7) or thin_run(column_hits, reduced.height * 0.7)


def _has_text_like_signal(mask: Image.Image) -> bool:
    """Heurística conservadora: varias secuencias cortas alineadas como glifos."""

    scale = min(1.0, 512 / max(mask.size))
    reduced = (
        mask.resize(
            (max(1, round(mask.width * scale)), max(1, round(mask.height * scale))),
            Image.Resampling.NEAREST,
        )
        if scale < 1
        else mask
    )
    pixels = reduced.load()
    rows_with_glyph_runs = 0
    for y in range(reduced.height):
        runs = 0
        active = False
        for x in range(reduced.width):
            foreground = pixels[x, y] > 0
            if foreground and not active:
                runs += 1
            active = foreground
        if runs >= 5:
            rows_with_glyph_runs += 1
            if rows_with_glyph_runs >= 2:
                return True
    return False


def inspect_asset(assets_dir: Path, object_name: str) -> dict:
    """Inspecciona un activo sin modificarlo y conserva fallos como evidencia."""

    result: dict[str, Any] = {
        "path": str(object_name or ""),
        "sha256": Path(str(object_name or "")).stem,
        "sha256_valid": False,
        "mime": "",
        "dimensions": {"width": 0, "height": 0},
        "mode": "",
        "has_alpha": False,
        "foreground_bbox": None,
        "occupancy": None,
        "margins": None,
        "border_or_rule_signal": None,
        "text_like_signal": None,
        "quality_checks": {
            "square_1024_plus": False,
            "source_shortest_side_512_plus": False,
            "margin_4pct_plus": False,
            "bbox_92pct_or_less": False,
            "occupancy_12_to_80pct": False,
            "aspect_deformation_1pct_or_less": None,
        },
        "quality_exception": "source_aspect_reference_unavailable",
        "status": "invalid",
        "reasons": [],
    }
    object_name = str(object_name or "")
    if not ASSET_NAME.fullmatch(object_name):
        result["reasons"].append("asset_path_not_png")
        return result
    path = Path(assets_dir) / object_name
    if not path.is_file():
        result["status"] = "missing"
        result["reasons"].append("asset_missing")
        return result
    data = path.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    result["actual_sha256"] = actual_sha
    result["bytes"] = len(data)
    result["sha256_valid"] = actual_sha == Path(object_name).stem
    if not result["sha256_valid"]:
        result["reasons"].append("asset_sha256_mismatch")
        return result
    if not data.startswith(PNG_MAGIC):
        result["reasons"].append("asset_magic_not_png")
        return result
    try:
        with Image.open(path) as probe:
            if probe.format != "PNG":
                result["reasons"].append("asset_format_not_png")
                return result
            probe.verify()
        with Image.open(path) as source:
            source.load()
            image = source.copy()
            image_format = source.format
    except (OSError, UnidentifiedImageError):
        result["reasons"].append("asset_not_pil_image")
        return result
    if image_format != "PNG":
        result["reasons"].append("asset_format_not_png")
        return result
    result["mime"] = "image/png"
    result["mode"] = image.mode
    result["has_alpha"] = "A" in image.getbands()
    width, height = image.size
    result["dimensions"] = {"width": width, "height": height}
    mask, bbox, foreground = _mask_metrics(image)
    if bbox is None or foreground == 0:
        result["border_or_rule_signal"] = True
        result["text_like_signal"] = False
        result["reasons"].append("asset_without_foreground")
        result["reasons"].append("border_or_rule_signal")
        return result
    left, top, right, bottom = bbox
    bbox_width, bbox_height = right - left, bottom - top
    margins = {
        "left": left / width,
        "top": top / height,
        "right": (width - right) / width,
        "bottom": (height - bottom) / height,
    }
    occupancy = foreground / (width * height)
    checks = {
        "square_1024_plus": width == height and width >= 1024,
        "source_shortest_side_512_plus": min(width, height) >= 512,
        "margin_4pct_plus": min(margins.values()) >= 0.04,
        "bbox_92pct_or_less": bbox_width / width <= 0.92 and bbox_height / height <= 0.92,
        "occupancy_12_to_80pct": 0.12 <= occupancy <= 0.80,
        "aspect_deformation_1pct_or_less": None,
    }
    text_like_signal = _has_text_like_signal(mask)
    result.update(
        foreground_bbox={"left": left, "top": top, "width": bbox_width, "height": bbox_height},
        occupancy=occupancy,
        margins=margins,
        border_or_rule_signal=_has_rule_signal(mask, bbox),
        text_like_signal=text_like_signal,
        quality_checks=checks,
    )
    result["reasons"].extend(
        f"quality_failed:{name}" for name, passed in checks.items() if passed is False
    )
    result["reasons"].append("quality_unmeasured:aspect_deformation_1pct_or_less")
    if result["border_or_rule_signal"]:
        result["reasons"].append("border_or_rule_signal")
    if text_like_signal:
        result["reasons"].append("text_like_signal")
    result["status"] = "inspected"
    return result


def _source_bbox(item: dict) -> object:
    attributes = item.get("attributes") or {}
    image_match = attributes.get("image_match") or {}
    references = image_match.get("source_references") or []
    if references and isinstance(references[0], dict):
        return references[0].get("cell_or_bbox")
    evidence = attributes.get("evidence") or {}
    if isinstance(evidence.get("image"), dict):
        return evidence["image"].get("cell_or_bbox")
    prices = attributes.get("prices") or []
    if prices and isinstance(prices[0], dict):
        return prices[0].get("code_bbox")
    return None


def _visual_signature(item: dict) -> dict:
    attributes = item.get("attributes") or {}
    fields = {
        "model": str(item.get("name") or "").strip(),
        "variant": str(attributes.get("variant") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "collection": str(item.get("collection") or "").strip(),
        "base_options": [str(row.get("name") or "") for row in item.get("base_price_options") or []],
        "add_on_options": [str(row.get("name") or "") for row in item.get("add_on_options") or []],
    }
    material = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"sha256": hashlib.sha256(material.encode("utf-8")).hexdigest(), "fields": fields}


def _inventory_row(supplier: str, item: dict, assets_dir: Path, asset_cache: dict[str, dict]) -> dict:
    attributes = item.get("attributes") or {}
    approved_asset = attributes.get("approved_asset") or {}
    object_name = str(approved_asset.get("path") or "")
    if object_name:
        if object_name not in asset_cache:
            asset_cache[object_name] = inspect_asset(assets_dir, object_name)
        current_asset = asset_cache[object_name]
    else:
        current_asset = {
            "path": "",
            "sha256": "",
            "mime": "",
            "dimensions": {"width": 0, "height": 0},
            "status": "not_assigned",
            "reasons": ["placeholder_without_asset"],
        }
    status = _match_status(item)
    if supplier == "labenze":
        decision = "replace_or_rebuild"
        reasons = ["labenze_baseline_requires_full_replacement_or_rebuild"]
    elif status == "placeholder":
        decision = "search_exact"
        reasons = ["requiez_placeholder_requires_exact_search"]
    else:
        decision = "re_audit_current"
        reasons = ["current_status_does_not_prove_visual_contract"]
    reasons.extend(current_asset.get("reasons") or [])
    return {
        "supplier": supplier,
        "internal_id": item["internal_id"],
        "product_key": item.get("product_key", ""),
        "sku": item.get("sku", ""),
        "source_code": attributes.get("source_code", ""),
        "source_hash": _source_hash(item),
        "code_status": item.get("code_status", ""),
        "collection": item.get("collection", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "source_page": attributes.get("source_page"),
        "source_bbox": _source_bbox(item),
        "current_asset": current_asset,
        "image_kind": item.get("image_kind", "placeholder"),
        "match_status": status,
        "product_url": item.get("product_url", ""),
        "visual_signature": _visual_signature(item),
        "initial_decision": decision,
        "reasons": list(dict.fromkeys(reasons)),
        "review": {
            "approved": False,
            "reviewer": "",
            "checks": {
                "full_product_visible": None,
                "not_cropped": None,
                "configuration_supported": None,
            },
            "status": "pending_human_review",
        },
    }


def _focal_cases(rows: list[dict]) -> dict:
    by_id = {row["internal_id"]: row for row in rows}
    errors = []

    def require(
        internal_id: str,
        *,
        supplier: str,
        status: str,
        decision: str,
        assigned: bool,
        code_status: str,
    ) -> dict | None:
        row = by_id.get(internal_id)
        if row is None:
            errors.append(f"missing:{internal_id}")
            return None
        asset = row["current_asset"]
        asset_matches = (
            bool(asset.get("path"))
            and asset.get("status") == "inspected"
            and asset.get("mime") == "image/png"
            and asset.get("sha256_valid") is True
            if assigned
            else not asset.get("path") and asset.get("status") == "not_assigned"
        )
        expected_image_kind = "official" if assigned else "placeholder"
        observed = (
            row["supplier"],
            row["match_status"],
            row["initial_decision"],
            row["code_status"],
            row["image_kind"],
            asset_matches,
        )
        expected = (
            supplier,
            status,
            decision,
            code_status,
            expected_image_kind,
            True,
        )
        if observed != expected:
            errors.append(f"contract:{internal_id}:{observed!r}")
        return row

    focal_rows = []
    for internal_id in REQUIEZ_JUN_M:
        focal_rows.append(
            require(
                internal_id,
                supplier="requiez",
                status="placeholder",
                decision="search_exact",
                assigned=False,
                code_status="verified",
            )
        )
    for internal_id in REQUIEZ_EXACT_FOCALS:
        focal_rows.append(
            require(
                internal_id,
                supplier="requiez",
                status="exact_pdf",
                decision="re_audit_current",
                assigned=True,
                code_status="verified",
            )
        )
    lab_replay = require(
        LABENZE_BAT,
        supplier="labenze",
        status="family_pdf",
        decision="replace_or_rebuild",
        assigned=True,
        code_status="verified",
    )
    zelig = [
        require(
            internal_id,
            supplier="labenze",
            status="family_pdf",
            decision="replace_or_rebuild",
            assigned=True,
            code_status="verified",
        )
        for internal_id in LABENZE_ZELIG
    ]
    needs_review = [
        require(
            internal_id,
            supplier="labenze",
            status="family_pdf",
            decision="replace_or_rebuild",
            assigned=True,
            code_status="needs_review",
        )
        for internal_id in LABENZE_NEEDS_REVIEW
    ]
    if errors:
        raise ValueError("AUDIT_FOCAL_CONTRACT:" + json.dumps(errors, ensure_ascii=True))
    requiez_rows = {str(row["source_code"]).upper(): row for row in focal_rows}
    return {
        "requiez": {
            code: {
                "found": True,
                "internal_id": requiez_rows[code]["internal_id"],
                "decision": requiez_rows[code]["initial_decision"],
            }
            for code in REQUIEZ_FOCALS
        },
        "labenze": {
            "106-00603-BAT": {"found": True, "internal_id": lab_replay["internal_id"], "decision": lab_replay["initial_decision"]},
            "ZELIG": {"internal_ids": sorted(row["internal_id"] for row in zelig)},
            "needs_review": {"internal_ids": sorted(row["internal_id"] for row in needs_review)},
        },
    }


def _shared_matrix(rows: list[dict]) -> dict:
    assigned: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        sha = str(row["current_asset"].get("actual_sha256") or row["current_asset"].get("sha256") or "")
        if sha:
            assigned[sha].append(row)
    groups = []
    for sha, members in sorted(assigned.items()):
        if len(members) < 2:
            continue
        groups.append(
            {
                "asset_sha256": sha,
                "assignment_count": len(members),
                "assigned_internal_ids": sorted(row["internal_id"] for row in members),
                "assigned_skus": sorted({str(row["sku"] or row["source_code"]) for row in members}),
                "visual_signatures": [
                    {
                        "internal_id": row["internal_id"],
                        "sku": row["sku"] or row["source_code"],
                        **row["visual_signature"],
                    }
                    for row in sorted(members, key=lambda value: value["internal_id"])
                ],
                "equivalence_proven": False,
                "review_status": "pending_human_review",
            }
        )
    return {"groups": groups}


def _font(size: int, *, bold: bool = False):
    font_name = "arialbd.ttf" if bold else "arial.ttf"
    path = Path("C:/Windows/Fonts") / font_name
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def _thumbnail(path: Path) -> Image.Image:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width = IMAGE_BOX[2] - IMAGE_BOX[0]
    height = IMAGE_BOX[3] - IMAGE_BOX[1]
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
    return canvas


def _contact_sheets(rows: list[dict], assets_dir: Path, output_dir: Path) -> list[str]:
    sheets_dir = output_dir / "contact-sheets"
    sheets_dir.mkdir()
    regular = _font(14)
    bold = _font(15, bold=True)
    thumbnails: dict[str, Image.Image | None] = {}
    relative_paths = []
    per_sheet = CONTACT_COLUMNS * CONTACT_ROWS
    for offset in range(0, len(rows), per_sheet):
        chunk = rows[offset : offset + per_sheet]
        sheet = Image.new(
            "RGB",
            (CONTACT_COLUMNS * CARD_WIDTH, CONTACT_ROWS * CARD_HEIGHT),
            "#e2e8f0",
        )
        for position, row in enumerate(chunk):
            card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "#f8fafc")
            draw = ImageDraw.Draw(card)
            decision = row["initial_decision"]
            border = "#b91c1c" if row["match_status"] == "placeholder" else "#d97706"
            draw.rectangle((1, 1, CARD_WIDTH - 2, CARD_HEIGHT - 2), outline=border, width=3)
            object_name = str(row["current_asset"].get("path") or "")
            if object_name not in thumbnails:
                candidate = assets_dir / object_name
                try:
                    thumbnails[object_name] = _thumbnail(candidate) if candidate.is_file() else None
                except (OSError, UnidentifiedImageError):
                    thumbnails[object_name] = None
            thumbnail = thumbnails[object_name]
            if thumbnail is None:
                draw.rectangle(IMAGE_BOX, fill="#fee2e2", outline="#ef4444", width=2)
                draw.text((105, 90), "SIN ACTIVO", fill="#991b1b", font=bold)
            else:
                card.paste(thumbnail, (IMAGE_BOX[0], IMAGE_BOX[1]))
            index = offset + position + 1
            draw.rectangle((8, 6, 72, 32), fill="white", outline=border, width=2)
            draw.text((12, 8), f"#{index:04d}", fill="#111827", font=bold)
            y = 202
            label = f"{row['supplier']} | {row['sku'] or row['source_code']}"
            draw.text((10, y), textwrap.shorten(label, width=43, placeholder="…"), fill="#111827", font=bold)
            draw.text((10, y + 20), textwrap.shorten(str(row["name"]), width=48, placeholder="…"), fill="#334155", font=regular)
            draw.text((10, y + 39), f"{row['match_status']} | {decision}", fill=border, font=regular)
            draw.text((10, y + 58), f"p.{row['source_page']} | {row['internal_id'][:35]}", fill="#475569", font=regular)
            sheet.paste(card, ((position % CONTACT_COLUMNS) * CARD_WIDTH, (position // CONTACT_COLUMNS) * CARD_HEIGHT))
        first, last = offset + 1, offset + len(chunk)
        relative = f"contact-sheets/items-{first:04d}-{last:04d}.png"
        sheet.save(output_dir / relative, format="PNG", optimize=False, compress_level=1)
        relative_paths.append(relative)
    return relative_paths


def _write_inventory_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def audit_snapshots(
    rebuilt: dict,
    store: dict,
    assets_dir: Path,
    output_dir: Path,
    *,
    input_hashes: dict[str, str],
    input_paths: dict[str, str],
    reproducible_command: str,
    input_integrity_before: dict | None = None,
    post_export_integrity=None,
) -> dict:
    """Compara snapshots y exporta el baseline sin mutar entradas."""

    assets_dir = Path(assets_dir)
    output_dir = validate_output_path(Path(output_dir), (assets_dir,))
    if output_dir.exists():
        raise FileExistsError(f"AUDIT_OUTPUT_EXISTS:{output_dir}")
    paired = _validate_inputs(rebuilt, store, input_hashes)
    asset_cache: dict[str, dict] = {}
    rows = [
        _inventory_row(supplier, item, assets_dir, asset_cache)
        for supplier in EXPECTED_COUNTS
        for item in paired[supplier][1].values()
    ]
    rows.sort(key=lambda row: (row["supplier"], row["internal_id"]))
    focal_cases = _focal_cases(rows)
    shared = _shared_matrix(rows)
    output_dir.mkdir(parents=True)
    jsonl_path = output_dir / "inventory.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    _write_inventory_csv(output_dir / "inventory.csv", rows)
    (output_dir / "shared-visual-matrix.json").write_bytes(_json_bytes(shared))
    contact_sheets = _contact_sheets(rows, assets_dir, output_dir)
    input_integrity = None
    if input_integrity_before is not None or post_export_integrity is not None:
        if input_integrity_before is None or not callable(post_export_integrity):
            raise ValueError("AUDIT_INPUT_INTEGRITY_CONFIG")
        input_integrity_after = post_export_integrity()
        if input_integrity_after != input_integrity_before:
            raise RuntimeError("AUDIT_INPUT_MUTATED")
        input_integrity = {
            "before": input_integrity_before,
            "after": input_integrity_after,
        }
    decision_counts = Counter(row["initial_decision"] for row in rows)
    status_counts = Counter(row["match_status"] for row in rows)
    summary = {
        "schema_version": 1,
        "input_hashes": dict(sorted(input_hashes.items())),
        "input_paths": dict(sorted(input_paths.items())),
        "counts": {
            "total": len(rows),
            "suppliers": dict(EXPECTED_COUNTS),
            "match_status": dict(sorted(status_counts.items())),
            "decisions": dict(sorted(decision_counts.items())),
        },
        "asset_metrics": {
            "assigned_associations": sum(bool(row["current_asset"].get("path")) for row in rows),
            "unique_assigned_assets": len(asset_cache),
            "missing_assets": sum(value.get("status") == "missing" for value in asset_cache.values()),
            "square_1024_plus": sum(
                value.get("quality_checks", {}).get("square_1024_plus") is True
                for value in asset_cache.values()
            ),
            "source_shortest_side_512_plus": sum(
                value.get("quality_checks", {}).get("source_shortest_side_512_plus") is True
                for value in asset_cache.values()
            ),
            "border_or_rule_signal": sum(value.get("border_or_rule_signal") is True for value in asset_cache.values()),
        },
        "shared_visual_groups": len(shared["groups"]),
        "focal_cases": focal_cases,
        "contact_sheets": contact_sheets,
        "reproducible_command": reproducible_command,
    }
    if input_integrity is not None:
        summary["input_integrity"] = input_integrity
    artifact_hashes = {
        str(path.relative_to(output_dir)).replace("\\", "/"): _sha256_file(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    summary["artifact_hashes"] = dict(sorted(artifact_hashes.items()))
    summary_path = output_dir / "summary.json"
    summary_path.write_bytes(_json_bytes(summary))
    artifact_hashes["summary.json"] = _sha256_file(summary_path)
    summary["artifact_hashes"] = dict(sorted(artifact_hashes.items()))
    (output_dir / "artifact-hashes.json").write_bytes(
        _json_bytes({"sha256": summary["artifact_hashes"]})
    )
    return summary


def _load_store(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("AUDIT_STORE_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("AUDIT_STORE_INVALID")
    return value


def _pdf_source(path: Path, supplier: str) -> AuditSourceFile:
    if not path.is_file():
        raise ValueError(f"AUDIT_PDF_MISSING:{supplier}")
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError(f"AUDIT_PDF_MAGIC:{supplier}")
    digest = _sha256_file(path)
    if digest != EXPECTED_PDF_SHA256[supplier]:
        raise ValueError(f"AUDIT_PDF_HASH:{supplier}:{digest}")
    logical_path = (
        "LABENZE/LP Labenze B26.pdf"
        if supplier == "labenze"
        else "REQUIEZ/Lista de precios A-26.pdf"
    )
    return AuditSourceFile(logical_path, "price_list", None, digest, "application/pdf", path)


def _real_input_integrity(
    labenze_pdf: Path,
    requiez_pdf: Path,
    store_path: Path,
    assets_dir: Path,
) -> dict:
    return {
        "pdf_sha256": {
            "labenze": _sha256_file(labenze_pdf),
            "requiez": _sha256_file(requiez_pdf),
        },
        "store_sha256": _sha256_file(store_path),
        "assets": asset_tree_fingerprint(assets_dir),
    }


def run_real_audit(
    labenze_pdf: Path,
    requiez_pdf: Path,
    store_path: Path,
    assets_dir: Path,
    output_dir: Path,
    *,
    reproducible_command: str,
) -> dict:
    labenze_pdf = Path(labenze_pdf).resolve()
    requiez_pdf = Path(requiez_pdf).resolve()
    store_path = Path(store_path).resolve()
    assets_dir = Path(assets_dir).resolve()
    output_dir = validate_output_path(
        Path(output_dir),
        (labenze_pdf, requiez_pdf, store_path, assets_dir),
    )
    labenze = _pdf_source(labenze_pdf, "labenze")
    requiez = _pdf_source(requiez_pdf, "requiez")
    if not store_path.is_file() or not assets_dir.is_dir():
        raise ValueError("AUDIT_STORE_OR_ASSETS_MISSING")
    store_hash = _sha256_file(store_path)
    store = _load_store(store_path)
    integrity_before = _real_input_integrity(
        labenze_pdf,
        requiez_pdf,
        store_path,
        assets_dir,
    )
    build_labenze_snapshot, build_requiez_snapshot = load_catalog_builders()
    fixed_time = datetime(2026, 8, 19, tzinfo=timezone.utc)
    rebuilt = {
        "labenze": build_labenze_snapshot((labenze,), synced_at=fixed_time),
        "requiez": build_requiez_snapshot((requiez,), synced_at=fixed_time),
    }
    summary = audit_snapshots(
        rebuilt,
        store,
        assets_dir,
        output_dir,
        input_hashes={"labenze": labenze.sha256, "requiez": requiez.sha256, "store": store_hash},
        input_paths={
            "labenze": str(labenze.local_path.resolve()),
            "requiez": str(requiez.local_path.resolve()),
            "store": str(store_path.resolve()),
            "assets": str(assets_dir.resolve()),
        },
        reproducible_command=reproducible_command,
        input_integrity_before=integrity_before,
        post_export_integrity=lambda: _real_input_integrity(
            labenze_pdf,
            requiez_pdf,
            store_path,
            assets_dir,
        ),
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audita offline 776 asociaciones visuales Labenze/Requiez."
    )
    parser.add_argument("--labenze-pdf", required=True, type=Path)
    parser.add_argument("--requiez-pdf", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args(argv)
    command = build_reproducible_command(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--labenze-pdf",
            str(arguments.labenze_pdf.resolve()),
            "--requiez-pdf",
            str(arguments.requiez_pdf.resolve()),
            "--store",
            str(arguments.store.resolve()),
            "--assets-dir",
            str(arguments.assets_dir.resolve()),
            "--output-dir",
            str(arguments.output_dir.resolve()),
        ]
    )
    summary = run_real_audit(
        arguments.labenze_pdf,
        arguments.requiez_pdf,
        arguments.store,
        arguments.assets_dir,
        arguments.output_dir,
        reproducible_command=command,
    )
    print(json.dumps({"output": str(arguments.output_dir.resolve()), **summary["counts"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
