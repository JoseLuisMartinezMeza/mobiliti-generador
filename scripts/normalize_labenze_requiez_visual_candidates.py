"""Normaliza candidatos visuales locales desde un plan explícito y auditable."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import warnings
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from PIL import Image, ImageChops, UnidentifiedImageError


MIN_SOURCE_SIDE = 512
MIN_FINAL_SIDE = 1024
MAX_FINAL_SIDE = 8192
MAX_IMAGE_PIXELS = 25_000_000
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FINAL_BYTES = 8 * 1024 * 1024
MAX_REVIEW_BYTES = 8 * 1024 * 1024
MAX_ASPECT_DEFORMATION = 0.01
MIN_MARGIN = 0.04
MAX_BBOX_AXIS = 0.92
MIN_OCCUPANCY = 0.12
MAX_OCCUPANCY = 0.80
ALLOWED_ACTIONS = {"validate_exact", "centered_canvas_padding_no_scale"}
ALLOWED_SUPPLIERS = {"labenze", "requiez"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ENTRY_FIELDS = {
    "internal_id",
    "supplier",
    "sku",
    "product_key",
    "source_path",
    "source_sha256",
    "source_dimensions",
    "source_review_path",
    "source_review_sha256",
    "action",
}
OPTIONAL_ENTRY_FIELDS = {"shared_visual_evidence"}
SHARED_VISUAL_FIELDS = {
    "group_id",
    "assigned_internal_ids",
    "evidence_url",
    "reason",
    "visual_signature",
    "configuration_equivalence",
}
MIME_BY_FORMAT = {
    "PNG": ("image/png", {".png"}),
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "WEBP": ("image/webp", {".webp"}),
}


class PlanError(ValueError):
    """El plan completo es ambiguo o inseguro y no debe producir salida."""


class CandidateError(ValueError):
    """Un candidato individual falló de forma recuperable y debe dejar receipt."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlanError(f"clave JSON duplicada: {key}")
        result[key] = value
    return result


def _load_plan(path: Path) -> tuple[dict, bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PlanError(f"plan ilegible: {path.name}") from exc
    if not payload or len(payload) > 4 * 1024 * 1024:
        raise PlanError("plan vacío o demasiado grande")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError("plan JSON malformado") from exc
    if not isinstance(value, dict):
        raise PlanError("plan debe ser objeto JSON")
    return value, payload


def _validate_relative_path(value: object, field: str) -> str:
    raw = str(value or "")
    pure = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or "\x00" in raw
        or pure.is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in pure.parts)
        or pure.as_posix() != raw
    ):
        raise PlanError(f"ruta insegura en {field}: {raw!r}")
    return raw


def _require_text(entry: dict, field: str, *, allow_empty: bool = False) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise PlanError(f"{field} inválido")
    return value


def _shared_evidence_error(detail: str) -> PlanError:
    return PlanError(f"evidencia visual compartida inválida: {detail}")


def _validate_shared_visual_evidence(value: object, expected_ids: list[str]) -> dict:
    if not isinstance(value, dict) or set(value) != SHARED_VISUAL_FIELDS:
        raise _shared_evidence_error("campos incompletos o desconocidos")
    evidence = dict(value)
    for field in (
        "group_id",
        "evidence_url",
        "reason",
        "visual_signature",
        "configuration_equivalence",
    ):
        item = evidence.get(field)
        if (
            not isinstance(item, str)
            or not item.strip()
            or item != item.strip()
            or any(ord(character) < 32 for character in item)
        ):
            raise _shared_evidence_error(f"{field} vacío o malformado")
    if len(evidence["group_id"]) > 200 or len(evidence["evidence_url"]) > 2048:
        raise _shared_evidence_error("group_id/evidence_url excede límite")
    if len(evidence["reason"]) > 2000:
        raise _shared_evidence_error("reason excede límite")
    if (
        len(evidence["visual_signature"]) > 512
        or len(evidence["configuration_equivalence"]) > 512
    ):
        raise _shared_evidence_error("firma/equivalencia excede límite")
    try:
        parsed_url = urlsplit(evidence["evidence_url"])
        hostname = parsed_url.hostname
    except ValueError as exc:
        raise _shared_evidence_error("evidence_url malformado") from exc
    if (
        parsed_url.scheme != "https"
        or not hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise _shared_evidence_error("evidence_url debe ser HTTPS absoluto sin credenciales")
    assigned = evidence.get("assigned_internal_ids")
    if (
        not isinstance(assigned, list)
        or any(not isinstance(item, str) or not item for item in assigned)
        or assigned != sorted(set(assigned))
        or assigned != expected_ids
    ):
        raise _shared_evidence_error("assigned_internal_ids no coincide exactamente con el grupo")
    return evidence


def _validate_plan(value: dict) -> list[dict]:
    if set(value) != {"schema_version", "entries"} or value.get("schema_version") != 1:
        raise PlanError("schema de plan inválido")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise PlanError("entries debe ser lista no vacía")
    seen_ids: set[str] = set()
    seen_products: set[tuple[str, str]] = set()
    validated = []
    for index, raw in enumerate(entries):
        fields = set(raw) if isinstance(raw, dict) else set()
        if (
            not isinstance(raw, dict)
            or not REQUIRED_ENTRY_FIELDS.issubset(fields)
            or not fields.issubset(REQUIRED_ENTRY_FIELDS | OPTIONAL_ENTRY_FIELDS)
        ):
            raise PlanError(f"campos de entry inválidos en índice {index}")
        entry = dict(raw)
        supplier = _require_text(entry, "supplier")
        internal_id = _require_text(entry, "internal_id")
        product_key = _require_text(entry, "product_key")
        _require_text(entry, "sku", allow_empty=True)
        if supplier not in ALLOWED_SUPPLIERS or not internal_id.startswith(f"{supplier}:"):
            raise PlanError(f"supplier/internal_id incoherentes en índice {index}")
        if entry.get("action") not in ALLOWED_ACTIONS:
            raise PlanError(f"action inválida en índice {index}")
        for field in ("source_sha256", "source_review_sha256"):
            if not isinstance(entry.get(field), str) or not SHA_RE.fullmatch(entry[field]):
                raise PlanError(f"{field} inválido en índice {index}")
        dimensions = entry.get("source_dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != {"width", "height"}:
            raise PlanError(f"source_dimensions inválidas en índice {index}")
        if any(
            isinstance(dimensions[field], bool)
            or not isinstance(dimensions[field], int)
            or dimensions[field] <= 0
            for field in ("width", "height")
        ):
            raise PlanError(f"source_dimensions inválidas en índice {index}")
        _validate_relative_path(entry["source_path"], "source_path")
        _validate_relative_path(entry["source_review_path"], "source_review_path")
        identity = (supplier, product_key)
        if internal_id in seen_ids or identity in seen_products:
            raise PlanError(f"entry duplicado en índice {index}")
        seen_ids.add(internal_id)
        seen_products.add(identity)
        validated.append(entry)

    by_hash: dict[str, list[dict]] = {}
    by_path: dict[str, list[dict]] = {}
    for entry in validated:
        by_hash.setdefault(entry["source_sha256"], []).append(entry)
        by_path.setdefault(entry["source_path"], []).append(entry)
    if any(len({entry["source_sha256"] for entry in group}) != 1 for group in by_path.values()):
        raise PlanError("entry duplicado: una ruta source declara hashes incompatibles")

    evidence_groups: dict[str, str] = {}
    for source_sha, group in by_hash.items():
        if len(group) == 1:
            if "shared_visual_evidence" in group[0]:
                raise _shared_evidence_error("no puede declararse para un hash único")
            continue
        expected_ids = sorted(entry["internal_id"] for entry in group)
        evidence_values = []
        for entry in group:
            if "shared_visual_evidence" not in entry:
                raise _shared_evidence_error("falta en una entrada del hash duplicado")
            evidence_values.append(
                _validate_shared_visual_evidence(entry["shared_visual_evidence"], expected_ids)
            )
        canonical = {_canonical_bytes(evidence) for evidence in evidence_values}
        if len(canonical) != 1:
            raise _shared_evidence_error(
                "el grupo no es simétrico en URL, razón, firma o equivalencia"
            )
        group_id = evidence_values[0]["group_id"]
        previous_sha = evidence_groups.setdefault(group_id, source_sha)
        if previous_sha != source_sha:
            raise _shared_evidence_error("group_id reutilizado para contenido distinto")
    return validated


def _has_reparse_flag(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _candidate_file(root: Path, relative: str, label: str) -> Path:
    path = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    try:
        for part in PurePosixPath(relative).parts:
            current = current / part
            metadata = current.lstat()
            if current.is_symlink() or _has_reparse_flag(current):
                raise CandidateError(f"{label}_ALIAS_FORBIDDEN", f"{label.lower()} usa enlace/reparse")
    except CandidateError:
        raise
    except OSError as exc:
        raise CandidateError(f"{label}_MISSING", f"{label.lower()} ausente") from exc
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise CandidateError(f"{label}_NOT_REGULAR", f"{label.lower()} no es archivo regular")
    if getattr(metadata, "st_nlink", 1) != 1:
        raise CandidateError(f"{label}_ALIAS_FORBIDDEN", f"{label.lower()} usa hardlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CandidateError(f"{label}_MISSING", f"{label.lower()} ausente") from exc
    resolved_root = root.resolve(strict=True)
    if resolved_root not in resolved.parents:
        raise CandidateError(f"{label}_PATH_ESCAPE", f"{label.lower()} escapa workspace")
    return path


def _read_bound_file(
    root: Path,
    relative: str,
    expected_sha: str,
    label: str,
    max_bytes: int,
) -> tuple[Path, bytes, dict]:
    path = _candidate_file(root, relative, label)
    before = path.stat()
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise CandidateError(f"{label}_BYTES_LIMIT", f"{label.lower()} excede límite de bytes")
    data = path.read_bytes()
    if len(data) != before.st_size:
        raise CandidateError(f"{label}_CHANGED_DURING_READ", f"{label.lower()} cambió durante lectura")
    actual_sha = _sha256(data)
    if actual_sha != expected_sha:
        raise CandidateError(f"{label}_SHA256_MISMATCH", f"hash de {label.lower()} divergente")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise CandidateError(f"{label}_CHANGED_DURING_READ", f"{label.lower()} cambió durante lectura")
    return path, data, {"bytes": len(data), "sha256": actual_sha}


def _magic_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise CandidateError("SOURCE_MAGIC_UNSUPPORTED", "magic/MIME de source no permitido")


def _decode_source(data: bytes, suffix: str, expected_dimensions: dict) -> tuple[Image.Image, dict]:
    magic = _magic_mime(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                image_format = probe.format
                frame_count = getattr(probe, "n_frames", 1)
                width, height = probe.size
                if frame_count != 1:
                    raise CandidateError("SOURCE_ANIMATED", "source animado no permitido")
                if image_format not in MIME_BY_FORMAT:
                    raise CandidateError("SOURCE_FORMAT_UNSUPPORTED", "formato Pillow no permitido")
                expected_mime, suffixes = MIME_BY_FORMAT[image_format]
                if expected_mime != magic or suffix.casefold() not in suffixes:
                    raise CandidateError(
                        "SOURCE_MIME_EXTENSION_MISMATCH",
                        "extensión/MIME/magic/formato inconsistentes",
                    )
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_FINAL_SIDE
                    or height > MAX_FINAL_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise CandidateError("SOURCE_DIMENSIONS_LIMIT", "source excede límites de imagen")
                probe.verify()
            with Image.open(io.BytesIO(data)) as loaded:
                loaded.load()
                image = loaded.copy()
    except CandidateError:
        raise
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise CandidateError("SOURCE_IMAGE_INVALID", "Pillow no pudo verificar/cargar source") from exc
    if (width, height) != (
        expected_dimensions["width"],
        expected_dimensions["height"],
    ):
        raise CandidateError("SOURCE_DIMENSIONS_MISMATCH", "dimensiones declaradas divergen")
    return image, {"mime": magic, "format": image_format, "width": width, "height": height}


def inspect_foreground(image: Image.Image) -> dict:
    """Replica el umbral del builder: alpha>=16 y diferencia de fondo>20."""

    rgba = image.convert("RGBA")
    width, height = rgba.size
    corners = [
        rgba.getpixel(point)
        for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    ]
    opaque = [pixel for pixel in corners if pixel[3] >= 16]
    if opaque and any(max(pixel[:3]) - min(pixel[:3]) > 16 for pixel in opaque):
        raise CandidateError("SOURCE_BACKGROUND_NOT_NEUTRAL", "esquinas no forman fondo neutro")
    transparent = not opaque
    if transparent:
        mask = rgba.getchannel("A").point(lambda value: 255 if value >= 16 else 0)
        background = (255, 255, 255)
    else:
        background = tuple(
            round(sum(pixel[channel] for pixel in opaque) / len(opaque)) for channel in range(3)
        )
        difference = ImageChops.difference(
            rgba.convert("RGB"), Image.new("RGB", rgba.size, background)
        )
        red, green, blue = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        color_mask = maximum.point(lambda value: 255 if value > 20 else 0)
        alpha_mask = rgba.getchannel("A").point(lambda value: 255 if value >= 16 else 0)
        mask = ImageChops.darker(color_mask, alpha_mask)
    bbox = mask.getbbox()
    foreground = mask.histogram()[255]
    if bbox is None or foreground == 0:
        raise CandidateError("SOURCE_WITHOUT_FOREGROUND", "source sin producto detectable")
    left, top, right, bottom = bbox
    bbox_width, bbox_height = right - left, bottom - top
    margins = {
        "left": left / width,
        "top": top / height,
        "right": (width - right) / width,
        "bottom": (height - bottom) / height,
    }
    return {
        "canvas": {"width": width, "height": height},
        "bbox": {"left": left, "top": top, "width": bbox_width, "height": bbox_height},
        "bbox_axis_ratios": {"width": bbox_width / width, "height": bbox_height / height},
        "margins": margins,
        "minimum_margin": min(margins.values()),
        "occupancy": foreground / (width * height),
        "foreground_pixels": foreground,
        "background_rgb": list(background),
        "transparent_canvas": transparent,
    }


def _contract_passes(geometry: dict) -> bool:
    canvas = geometry["canvas"]
    return (
        canvas["width"] == canvas["height"]
        and MIN_FINAL_SIDE <= canvas["width"] <= MAX_FINAL_SIDE
        and canvas["width"] * canvas["height"] <= MAX_IMAGE_PIXELS
        and geometry["minimum_margin"] >= MIN_MARGIN
        and max(geometry["bbox_axis_ratios"].values()) <= MAX_BBOX_AXIS
        and MIN_OCCUPANCY <= geometry["occupancy"] <= MAX_OCCUPANCY
    )


def _translated_geometry(before: dict, side: int, x: int, y: int) -> dict:
    bbox = before["bbox"]
    left, top = x + bbox["left"], y + bbox["top"]
    right, bottom = left + bbox["width"], top + bbox["height"]
    margins = {
        "left": left / side,
        "top": top / side,
        "right": (side - right) / side,
        "bottom": (side - bottom) / side,
    }
    return {
        "canvas": {"width": side, "height": side},
        "bbox": {"left": left, "top": top, "width": bbox["width"], "height": bbox["height"]},
        "bbox_axis_ratios": {"width": bbox["width"] / side, "height": bbox["height"] / side},
        "margins": margins,
        "minimum_margin": min(margins.values()),
        "occupancy": before["foreground_pixels"] / (side * side),
        "foreground_pixels": before["foreground_pixels"],
        "background_rgb": [255, 255, 255],
        "transparent_canvas": before["transparent_canvas"],
    }


def _smallest_canvas(before: dict) -> tuple[int, int, int]:
    width, height = before["canvas"]["width"], before["canvas"]["height"]
    maximum = min(MAX_FINAL_SIDE, math.isqrt(MAX_IMAGE_PIXELS))
    for side in range(max(MIN_FINAL_SIDE, width, height), maximum + 1):
        x, y = (side - width) // 2, (side - height) // 2
        if _contract_passes(_translated_geometry(before, side, x, y)):
            return side, x, y
    raise CandidateError("NO_FEASIBLE_CANVAS", "ningún lienzo permitido cumple el contrato")


def _white_composite(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert("RGB")


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _normalization_result(entry: dict, source_data: bytes, image: Image.Image, source_info: dict) -> dict:
    width, height = image.size
    if min(width, height) < MIN_SOURCE_SIDE:
        raise CandidateError(
            "SOURCE_SHORTEST_SIDE_BELOW_512", "lado menor de source es inferior a 512"
        )
    before = inspect_foreground(image)
    pixel_mode = "RGBA" if before["transparent_canvas"] else "RGB"
    source_pixel_image = (
        image.convert("RGBA") if pixel_mode == "RGBA" else _white_composite(image)
    )
    source_pixel_sha = _sha256(source_pixel_image.tobytes())
    action = entry["action"]
    can_copy_exact = (
        action == "validate_exact"
        and source_info["format"] == "PNG"
        and _contract_passes(before)
    )
    if can_copy_exact:
        output_data = source_data
        after = before
        transformation = {
            "canvas_padding": False,
            "crop": False,
            "reframe": False,
            "resize": False,
            "scale": 1.0,
        }
        final_pixel_sha = source_pixel_sha
    else:
        if before["minimum_margin"] <= 0:
            raise CandidateError(
                "SOURCE_FOREGROUND_TOUCHES_BORDER", "padding no puede recuperar un source recortado"
            )
        if not before["transparent_canvas"] and any(
            channel < 235 for channel in before["background_rgb"]
        ):
            raise CandidateError(
                "SOURCE_BACKGROUND_NOT_WHITE_FOR_PADDING", "padding exige fondo blanco compatible"
            )
        side, x, y = _smallest_canvas(before)
        canvas_background = (255, 255, 255, 0) if pixel_mode == "RGBA" else "white"
        canvas = Image.new(pixel_mode, (side, side), canvas_background)
        canvas.paste(source_pixel_image, (x, y))
        output_data = _png_bytes(canvas)
        after = inspect_foreground(canvas)
        crop = canvas.crop((x, y, x + width, y + height))
        final_pixel_sha = _sha256(crop.tobytes())
        transformation = {
            "canvas_padding": True,
            "crop": False,
            "paste_offset": {"x": x, "y": y},
            "reframe": False,
            "resize": False,
            "scale": 1.0,
        }
        if after != _translated_geometry(before, side, x, y):
            raise CandidateError("FOREGROUND_GATE_DIVERGENCE", "geometría post-padding divergente")
    if len(output_data) > MAX_FINAL_BYTES:
        raise CandidateError("FINAL_BYTES_OVER_8_MIB", "PNG final supera 8 MiB")
    before_aspect = before["bbox"]["width"] / before["bbox"]["height"]
    after_aspect = after["bbox"]["width"] / after["bbox"]["height"]
    deformation = abs(after_aspect / before_aspect - 1)
    if deformation > MAX_ASPECT_DEFORMATION:
        raise CandidateError("ASPECT_DEFORMATION_OVER_1_PERCENT", "deformación supera 1 %")
    if not _contract_passes(after):
        raise CandidateError("FINAL_CONTRACT_FAILED", "PNG final no cumple contrato")
    if source_pixel_sha != final_pixel_sha:
        raise CandidateError("PIXELS_CHANGED", "píxeles del source cambiaron")
    output_sha = _sha256(output_data)
    return {
        "bytes": output_data,
        "sha256": output_sha,
        "path": f"assets/{output_sha}.png",
        "geometry": {"before": before, "after": after, "aspect_deformation": deformation},
        "transformation": transformation,
        "pixel_identity": {
            "before_sha256": source_pixel_sha,
            "after_sha256": final_pixel_sha,
            "mode": pixel_mode,
            "preserved": True,
        },
    }


def _base_receipt(entry: dict, plan_sha: str) -> dict:
    receipt = {
        "schema_version": 1,
        "artifact_type": "local_visual_normalization_receipt",
        "plan_sha256": plan_sha,
        "plan_entry_sha256": _sha256(_canonical_bytes(entry)),
        "internal_id": entry["internal_id"],
        "supplier": entry["supplier"],
        "sku": entry["sku"],
        "product_key": entry["product_key"],
        "action": entry["action"],
        "approved": False,
        "promotion": {"allowed": False},
        "mutations": {
            "catalog_store_written": False,
            "production_written": False,
            "promotion_performed": False,
            "remote_upload": False,
        },
    }
    if "shared_visual_evidence" in entry:
        receipt["shared_visual_evidence"] = entry["shared_visual_evidence"]
    return receipt


def _declared_failure_receipt(
    entry: dict,
    plan_sha: str,
    code: str,
    message: str,
) -> dict:
    receipt = _base_receipt(entry, plan_sha)
    receipt.update(
        {
            "status": "FAILED",
            "failure": {"code": code, "message": message},
            "source": {
                "path": entry["source_path"],
                "declared_sha256": entry["source_sha256"],
                "declared_dimensions": entry["source_dimensions"],
            },
            "source_review": {
                "path": entry["source_review_path"],
                "declared_sha256": entry["source_review_sha256"],
            },
        }
    )
    return receipt


def _process_entry(root: Path, entry: dict, plan_sha: str) -> tuple[dict, bytes | None]:
    receipt = _base_receipt(entry, plan_sha)
    try:
        source_path, source_data, source_binding = _read_bound_file(
            root,
            entry["source_path"],
            entry["source_sha256"],
            "SOURCE",
            MAX_SOURCE_BYTES,
        )
        _review_path, _review_data, review_binding = _read_bound_file(
            root,
            entry["source_review_path"],
            entry["source_review_sha256"],
            "SOURCE_REVIEW",
            MAX_REVIEW_BYTES,
        )
        image, source_info = _decode_source(
            source_data, source_path.suffix, entry["source_dimensions"]
        )
        normalized = _normalization_result(entry, source_data, image, source_info)
        receipt.update(
            {
                "status": "PASS",
                "source": {
                    "path": entry["source_path"],
                    **source_binding,
                    "dimensions": entry["source_dimensions"],
                    "mime": source_info["mime"],
                },
                "source_review": {"path": entry["source_review_path"], **review_binding},
                "output": {
                    "path": normalized["path"],
                    "sha256": normalized["sha256"],
                    "bytes": len(normalized["bytes"]),
                    "mime": "image/png",
                    "content_addressed": True,
                },
                "geometry": normalized["geometry"],
                "transformation": normalized["transformation"],
                "pixel_identity": normalized["pixel_identity"],
                "contract": {
                    "source_shortest_side_512_plus": True,
                    "square_1024_to_8192": True,
                    "pixels_25m_or_less": True,
                    "bytes_8mib_or_less": True,
                    "margin_4pct_plus": True,
                    "bbox_axes_92pct_or_less": True,
                    "occupancy_12_to_80pct": True,
                    "aspect_deformation_1pct_or_less": True,
                },
                "inputs_unchanged": True,
            }
        )
        return receipt, normalized["bytes"]
    except CandidateError as exc:
        receipt.update(
            {
                "status": "FAILED",
                "failure": {"code": exc.code, "message": exc.message},
                "source": {
                    "path": entry["source_path"],
                    "declared_sha256": entry["source_sha256"],
                    "declared_dimensions": entry["source_dimensions"],
                },
                "source_review": {
                    "path": entry["source_review_path"],
                    "declared_sha256": entry["source_review_sha256"],
                },
            }
        )
        return receipt, None


def _safe_output(root: Path, output: Path, plan: Path, entries: list[dict], plan_sha: str) -> tuple[Path, Path]:
    root = root.resolve(strict=True)
    output = output if output.is_absolute() else root / output
    output = output.absolute()
    lexical_inputs = [plan.absolute()]
    lexical_inputs.extend((root / entry[field]).absolute() for entry in entries for field in ("source_path", "source_review_path"))
    if any(
        output == item or output in item.parents or item in output.parents
        for item in lexical_inputs
    ):
        raise PlanError("ruta de salida se solapa con entradas")
    try:
        relative = output.relative_to(root)
    except ValueError as exc:
        raise PlanError("salida escapa workspace") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise PlanError("salida inválida")
    if output.exists():
        raise PlanError("salida ya existe; nunca se sobrescribe")
    current = root
    try:
        for part in relative.parent.parts:
            current = current / part
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or current.is_symlink()
                or _has_reparse_flag(current)
            ):
                raise PlanError("ancestro de salida inseguro, enlace o reparse")
    except PlanError:
        raise
    except OSError as exc:
        raise PlanError("directorio padre de salida inseguro o ausente") from exc
    parent = output.parent
    stage = parent / f".{output.name}.staging-{plan_sha[:12]}"
    if stage.exists():
        raise PlanError("staging previo existe; se preserva para inspección")
    return output, stage


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def normalize_plan(plan_path: Path, output_dir: Path, workspace_root: Path) -> dict:
    """Ejecuta un plan local; errores de entry generan FAILED y errores de plan no escriben."""

    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or _has_reparse_flag(root):
        raise PlanError("workspace inseguro")
    plan_path = Path(plan_path)
    lexical_plan = plan_path if plan_path.is_absolute() else root / plan_path
    lexical_plan = lexical_plan.absolute()
    try:
        relative_plan = lexical_plan.relative_to(root)
    except ValueError as exc:
        raise PlanError("plan debe estar dentro de workspace") from exc
    if not relative_plan.parts or any(part in {"", ".", ".."} for part in relative_plan.parts):
        raise PlanError("ruta léxica de plan insegura")
    plan_relative = relative_plan.as_posix()
    try:
        plan_path = _candidate_file(root, plan_relative, "PLAN")
    except CandidateError as exc:
        raise PlanError(exc.message) from exc
    plan, plan_bytes = _load_plan(plan_path)
    entries = _validate_plan(plan)
    plan_sha = _sha256(plan_bytes)
    output, stage = _safe_output(root, Path(output_dir), plan_path, entries, plan_sha)

    processed = []
    for entry in entries:
        receipt, asset = _process_entry(root, entry, plan_sha)
        processed.append({"entry": entry, "receipt": receipt, "asset": asset})

    by_output: dict[str, list[dict]] = {}
    for item in processed:
        if item["asset"] is not None:
            by_output.setdefault(item["receipt"]["output"]["sha256"], []).append(item)
    for output_group in by_output.values():
        if len(output_group) < 2:
            continue
        source_hashes = {item["entry"]["source_sha256"] for item in output_group}
        explicitly_shared = len(source_hashes) == 1 and all(
            "shared_visual_evidence" in item["entry"] for item in output_group
        )
        if explicitly_shared:
            continue
        for item in output_group:
            item["receipt"] = _declared_failure_receipt(
                item["entry"],
                plan_sha,
                "OUTPUT_CONTENT_DUPLICATE",
                "asset idéntico no se comparte automáticamente",
            )
            item["asset"] = None

    shared_groups: dict[str, list[dict]] = {}
    for item in processed:
        if "shared_visual_evidence" in item["entry"]:
            shared_groups.setdefault(item["entry"]["source_sha256"], []).append(item)
    for shared_group in shared_groups.values():
        failed_members = sorted(
            (
                {
                    "internal_id": item["entry"]["internal_id"],
                    "code": item["receipt"]["failure"]["code"],
                }
                for item in shared_group
                if item["receipt"]["status"] == "FAILED"
            ),
            key=lambda value: value["internal_id"],
        )
        if not failed_members:
            continue
        group_status = {"atomic_status": "FAILED", "failed_members": failed_members}
        for item in shared_group:
            if item["receipt"]["status"] == "PASS":
                item["receipt"] = _declared_failure_receipt(
                    item["entry"],
                    plan_sha,
                    "SHARED_VISUAL_GROUP_MEMBER_FAILED",
                    "otro miembro del grupo visual compartido falló; no se emite asset",
                )
            item["receipt"]["shared_visual_group"] = group_status
            item["asset"] = None

    # Verificación final previa a publicar; este script nunca modifica ni limpia inputs.
    for item in processed:
        entry = item["entry"]
        if item["receipt"]["status"] == "PASS":
            for path_field, sha_field, label, limit in (
                ("source_path", "source_sha256", "SOURCE", MAX_SOURCE_BYTES),
                ("source_review_path", "source_review_sha256", "SOURCE_REVIEW", MAX_REVIEW_BYTES),
            ):
                _read_bound_file(root, entry[path_field], entry[sha_field], label, limit)

    stage.mkdir()
    manifest_entries = []
    written_assets: set[str] = set()
    for item in processed:
        entry, receipt, asset = item["entry"], item["receipt"], item["asset"]
        entry_sha = _sha256(_canonical_bytes(entry))
        receipt_path = f"receipts/{entry_sha}.json"
        receipt_bytes = _json_bytes(receipt)
        _write_new(stage / receipt_path, receipt_bytes)
        record = {
            "internal_id": entry["internal_id"],
            "status": receipt["status"],
            "receipt_path": receipt_path,
            "receipt_sha256": _sha256(receipt_bytes),
        }
        if asset is not None:
            asset_path = receipt["output"]["path"]
            if asset_path not in written_assets:
                _write_new(stage / asset_path, asset)
                written_assets.add(asset_path)
            record.update(asset_path=asset_path, asset_sha256=receipt["output"]["sha256"])
        manifest_entries.append(record)
    passed = sum(row["status"] == "PASS" for row in manifest_entries)
    failed = len(manifest_entries) - passed
    manifest = {
        "schema_version": 1,
        "artifact_type": "local_visual_normalization_manifest",
        "plan": {"path": plan_relative, "sha256": plan_sha},
        "status": "PASS" if failed == 0 else "FAILED",
        "summary": {"failed": failed, "passed": passed, "total": len(manifest_entries)},
        "entries": manifest_entries,
        "approved": False,
        "promotion": {"allowed": False},
        "mutations": {
            "catalog_store_written": False,
            "production_written": False,
            "promotion_performed": False,
            "remote_upload": False,
        },
    }
    manifest["logical_sha256"] = _sha256(_canonical_bytes(manifest))
    _write_new(stage / "manifest.json", _json_bytes(manifest))
    try:
        os.rename(stage, output)
    except FileExistsError as exc:
        raise PlanError("salida apareció durante publicación; staging preservado") from exc
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        manifest = normalize_plan(args.plan, args.output_dir, args.workspace_root)
    except PlanError as exc:
        print(f"[BLOQUEADO] {exc}", file=sys.stderr)
        return 2
    print(Path(args.output_dir) / "manifest.json")
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
