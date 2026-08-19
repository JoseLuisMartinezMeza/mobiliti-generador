"""Normaliza candidatos visuales locales desde un plan explícito y auditable."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import re
import stat
import sys
import warnings
import zlib
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import urlsplit

import PIL
from PIL import Image, ImageChops, UnidentifiedImageError


MIN_SOURCE_SIDE = 512
MIN_FINAL_SIDE = 1024
MAX_FINAL_SIDE = 8192
MAX_IMAGE_PIXELS = 25_000_000
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FINAL_BYTES = 8 * 1024 * 1024
MAX_REVIEW_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_AGGREGATE_ASSET_BYTES = 128 * 1024 * 1024
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
ALGORITHM_PROVENANCE = {
    "name": "labenze_requiez_visual_candidate_normalizer",
    "schema_version": 1,
    "version": "1.0.0",
    "foreground_gate": "builder_alpha16_corner_delta20_v1",
}


class PlanError(ValueError):
    """El plan completo es ambiguo o inseguro y no debe producir salida."""


class CandidateError(ValueError):
    """Un candidato individual falló de forma recuperable y debe dejar receipt."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.file_binding = None
        self.public_binding = None


class TransactionDrift(PlanError):
    """Un input o directorio dejó de coincidir con el binding validado."""


class FileBinding(NamedTuple):
    label: str
    relative: str
    resolved: str
    sha256: str
    size: int
    device: int
    inode: int
    mode: int
    nlink: int
    mtime_ns: int
    ctime_ns: int
    max_bytes: int


class DirectoryBinding(NamedTuple):
    lexical: str
    resolved: str
    device: int
    inode: int
    mode: int


class ArtifactBinding(NamedTuple):
    relative: str
    sha256: str
    size: int
    device: int
    inode: int
    mode: int
    nlink: int
    mtime_ns: int
    ctime_ns: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _runtime_provenance() -> dict:
    script_sha = _sha256(Path(__file__).read_bytes())
    return {
        "algorithm": dict(ALGORITHM_PROVENANCE),
        "implementation": {"script_sha256": script_sha},
        "runtime": {
            "python": platform.python_version(),
            "pillow": PIL.__version__,
            "zlib": zlib.ZLIB_VERSION,
            "zlib_runtime": getattr(zlib, "ZLIB_RUNTIME_VERSION", zlib.ZLIB_VERSION),
        },
        "limits": {
            "aggregate_asset_memory_bytes": MAX_AGGREGATE_ASSET_BYTES,
        },
    }


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
    return _parse_plan_bytes(payload), payload


def _parse_plan_bytes(payload: bytes) -> dict:
    if not payload or len(payload) > MAX_PLAN_BYTES:
        raise PlanError("plan vacío o demasiado grande")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError("plan JSON malformado") from exc
    if not isinstance(value, dict):
        raise PlanError("plan debe ser objeto JSON")
    return value


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


def _capture_directory(path: Path, label: str) -> DirectoryBinding:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or path.is_symlink()
            or _has_reparse_flag(path)
        ):
            raise PlanError(f"{label} usa enlace, reparse o no es directorio")
        resolved = path.resolve(strict=True)
    except PlanError:
        raise
    except OSError as exc:
        raise PlanError(f"{label} ausente o ilegible") from exc
    return DirectoryBinding(
        lexical=str(path.absolute()),
        resolved=str(resolved),
        device=getattr(metadata, "st_dev", 0),
        inode=getattr(metadata, "st_ino", 0),
        mode=metadata.st_mode,
    )


def _capture_workspace_root(workspace_root: Path) -> tuple[Path, tuple[DirectoryBinding, ...]]:
    lexical = Path(workspace_root)
    lexical = lexical if lexical.is_absolute() else Path.cwd() / lexical
    lexical = lexical.absolute()
    chain = list(reversed(lexical.parents)) + [lexical]
    bindings = tuple(_capture_directory(path, "workspace") for path in chain)
    return Path(bindings[-1].resolved), bindings


def _capture_output_ancestors(root: Path, parent: Path) -> tuple[DirectoryBinding, ...]:
    try:
        relative_parent = parent.absolute().relative_to(root)
    except ValueError as exc:
        raise PlanError("directorio padre de salida escapa workspace") from exc
    current = root
    bindings = [_capture_directory(current, "ancestro de salida")]
    for part in relative_parent.parts:
        current = current / part
        bindings.append(_capture_directory(current, "ancestro de salida"))
    return tuple(bindings)


def _assert_directory_bindings(bindings: tuple[DirectoryBinding, ...]) -> None:
    for expected in bindings:
        try:
            current = _capture_directory(Path(expected.lexical), "binding de directorio")
        except PlanError as exc:
            raise TransactionDrift(f"binding cambió: {exc}") from exc
        if current != expected:
            raise TransactionDrift(f"binding cambió para directorio: {expected.lexical}")


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
    expected_sha: str | None,
    label: str,
    max_bytes: int,
) -> tuple[Path, bytes, dict, FileBinding]:
    path = _candidate_file(root, relative, label)
    before = path.stat()
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise CandidateError(f"{label}_BYTES_LIMIT", f"{label.lower()} excede límite de bytes")
    data = path.read_bytes()
    if len(data) != before.st_size:
        raise CandidateError(f"{label}_CHANGED_DURING_READ", f"{label.lower()} cambió durante lectura")
    actual_sha = _sha256(data)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise CandidateError(f"{label}_CHANGED_DURING_READ", f"{label.lower()} cambió durante lectura")
    binding = FileBinding(
        label=label,
        relative=relative,
        resolved=str(path.resolve(strict=True)),
        sha256=actual_sha,
        size=after.st_size,
        device=getattr(after, "st_dev", 0),
        inode=getattr(after, "st_ino", 0),
        mode=after.st_mode,
        nlink=getattr(after, "st_nlink", 1),
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
        max_bytes=max_bytes,
    )
    public_binding = {"bytes": len(data), "sha256": actual_sha}
    if expected_sha is not None and actual_sha != expected_sha:
        error = CandidateError(
            f"{label}_SHA256_MISMATCH",
            f"hash de {label.lower()} divergente",
        )
        error.file_binding = binding
        error.public_binding = public_binding
        raise error
    return path, data, public_binding, binding


def _assert_file_binding(root: Path, expected: FileBinding) -> None:
    try:
        _path, _data, _public, current = _read_bound_file(
            root,
            expected.relative,
            expected.sha256,
            expected.label,
            expected.max_bytes,
        )
    except CandidateError as exc:
        raise TransactionDrift(f"binding cambió para {expected.relative}: {exc.message}") from exc
    if current != expected:
        raise TransactionDrift(f"binding cambió para archivo: {expected.relative}")


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
            with Image.open(io.BytesIO(data)) as metadata_probe:
                orientation = metadata_probe.getexif().get(274)
                if orientation not in (None, 1):
                    raise CandidateError(
                        "SOURCE_EXIF_ORIENTATION_UNSUPPORTED",
                        "EXIF Orientation distinto de 1 no se rota ni normaliza implícitamente",
                    )
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


def _base_receipt(entry: dict, plan_sha: str, provenance: dict) -> dict:
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
        "provenance": provenance,
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
    provenance: dict,
    code: str,
    message: str,
    previous: dict | None = None,
) -> dict:
    receipt = _base_receipt(entry, plan_sha, provenance)
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
    if previous is not None:
        for field in ("source", "source_review"):
            if field in previous:
                receipt[field].update(previous[field])
    return receipt


def _process_entry(
    root: Path,
    entry: dict,
    plan_sha: str,
    provenance: dict,
) -> tuple[dict, bytes | None, tuple[FileBinding, ...]]:
    receipt = _base_receipt(entry, plan_sha, provenance)
    source_path = None
    source_data = None
    source_binding = None
    source_file_binding = None
    review_binding = None
    review_file_binding = None
    read_error = None
    try:
        try:
            source_path, source_data, source_binding, source_file_binding = _read_bound_file(
                root,
                entry["source_path"],
                entry["source_sha256"],
                "SOURCE",
                MAX_SOURCE_BYTES,
            )
        except CandidateError as exc:
            source_binding = exc.public_binding
            source_file_binding = exc.file_binding
            read_error = exc
        try:
            _review_path, _review_data, review_binding, review_file_binding = _read_bound_file(
                root,
                entry["source_review_path"],
                entry["source_review_sha256"],
                "SOURCE_REVIEW",
                MAX_REVIEW_BYTES,
            )
        except CandidateError as exc:
            review_binding = exc.public_binding
            review_file_binding = exc.file_binding
            if read_error is None:
                read_error = exc
        if read_error is not None:
            raise read_error
        assert source_path is not None and source_data is not None
        assert source_binding is not None and source_file_binding is not None
        assert review_binding is not None and review_file_binding is not None
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
            }
        )
        return receipt, normalized["bytes"], (source_file_binding, review_file_binding)
    except CandidateError as exc:
        source_receipt = {
            "path": entry["source_path"],
            "declared_sha256": entry["source_sha256"],
            "declared_dimensions": entry["source_dimensions"],
        }
        if source_binding is not None:
            source_receipt.update(source_binding)
        review_receipt = {
            "path": entry["source_review_path"],
            "declared_sha256": entry["source_review_sha256"],
        }
        if review_binding is not None:
            review_receipt.update(review_binding)
        receipt.update(
            {
                "status": "FAILED",
                "failure": {"code": exc.code, "message": exc.message},
                "source": source_receipt,
                "source_review": review_receipt,
            }
        )
        bindings = tuple(
            binding
            for binding in (source_file_binding, review_file_binding)
            if binding is not None
        )
        return receipt, None, bindings


def _safe_output(
    root: Path,
    output: Path,
    plan: Path,
    entries: list[dict],
    plan_sha: str,
) -> tuple[Path, Path, tuple[DirectoryBinding, ...]]:
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
    parent = output.parent
    ancestor_bindings = _capture_output_ancestors(root, parent)
    stage = parent / f".{output.name}.staging-{plan_sha[:12]}"
    if stage.exists():
        raise PlanError("staging previo existe; se preserva para inspección")
    return output, stage, ancestor_bindings


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _artifact_path(directory: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise TransactionDrift(f"ruta de artifact inválida: {relative}")
    current = directory
    try:
        for part in pure.parts[:-1]:
            current = current / part
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or current.is_symlink()
                or _has_reparse_flag(current)
            ):
                raise TransactionDrift(f"ancestro de artifact inseguro: {relative}")
    except TransactionDrift:
        raise
    except OSError as exc:
        raise TransactionDrift(f"ancestro de artifact ausente: {relative}") from exc
    return directory.joinpath(*pure.parts)


def _read_artifact_binding(directory: Path, relative: str) -> ArtifactBinding:
    path = _artifact_path(directory, relative)
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or path.is_symlink()
            or _has_reparse_flag(path)
            or getattr(before, "st_nlink", 1) != 1
        ):
            raise TransactionDrift(f"artifact no regular o con alias: {relative}")
        data = path.read_bytes()
        after = path.lstat()
    except TransactionDrift:
        raise
    except OSError as exc:
        raise TransactionDrift(f"artifact ausente o ilegible: {relative}") from exc
    stable_fields = (
        "st_size",
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, field, 0) != getattr(after, field, 0) for field in stable_fields):
        raise TransactionDrift(f"artifact cambió durante lectura: {relative}")
    if len(data) != after.st_size:
        raise TransactionDrift(f"bytes de artifact divergentes: {relative}")
    return ArtifactBinding(
        relative=relative,
        sha256=_sha256(data),
        size=after.st_size,
        device=getattr(after, "st_dev", 0),
        inode=getattr(after, "st_ino", 0),
        mode=after.st_mode,
        nlink=getattr(after, "st_nlink", 1),
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )


def _bind_written_artifact(directory: Path, relative: str, payload: bytes) -> ArtifactBinding:
    binding = _read_artifact_binding(directory, relative)
    if binding.sha256 != _sha256(payload) or binding.size != len(payload):
        raise TransactionDrift(f"artifact escrito diverge del payload: {relative}")
    return binding


def _scan_artifact_tree(directory: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()

    def visit(current: Path, prefix: PurePosixPath) -> None:
        try:
            children = sorted(current.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise TransactionDrift(f"árbol de artifacts ilegible: {prefix.as_posix()}") from exc
        for child in children:
            relative = (prefix / child.name).as_posix()
            try:
                metadata = child.lstat()
                aliased = child.is_symlink() or _has_reparse_flag(child)
            except OSError as exc:
                raise TransactionDrift(f"artifact cambió durante recorrido: {relative}") from exc
            if aliased:
                raise TransactionDrift(f"artifact usa enlace/reparse: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
                visit(child, prefix / child.name)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(relative)
            else:
                raise TransactionDrift(f"artifact no regular: {relative}")

    visit(directory, PurePosixPath())
    return files, directories


def _expected_artifact_directories(relative_paths: set[str]) -> set[str]:
    expected: set[str] = set()
    for relative in relative_paths:
        parent = PurePosixPath(relative).parent
        while parent.parts:
            expected.add(parent.as_posix())
            parent = parent.parent
    return expected


def _assert_artifact_tree(
    directory: Path,
    expected_directory: DirectoryBinding,
    expected_artifacts: dict[str, ArtifactBinding],
) -> None:
    try:
        current_directory = _capture_directory(directory, "árbol de artifacts")
    except PlanError as exc:
        raise TransactionDrift(f"identidad del árbol cambió: {exc}") from exc
    expected_identity = (
        expected_directory.device,
        expected_directory.inode,
        expected_directory.mode,
    )
    current_identity = (
        current_directory.device,
        current_directory.inode,
        current_directory.mode,
    )
    if current_identity != expected_identity:
        raise TransactionDrift("identidad del directorio de artifacts cambió")

    actual_files, actual_directories = _scan_artifact_tree(directory)
    expected_files = set(expected_artifacts)
    if actual_files != expected_files:
        raise TransactionDrift("rutas del árbol de artifacts cambiaron")
    if actual_directories != _expected_artifact_directories(expected_files):
        raise TransactionDrift("directorios del árbol de artifacts cambiaron")
    for relative in sorted(expected_artifacts):
        if _read_artifact_binding(directory, relative) != expected_artifacts[relative]:
            raise TransactionDrift(f"binding de artifact cambió: {relative}")


def _deduplicate_file_bindings(bindings: list[FileBinding]) -> tuple[FileBinding, ...]:
    unique: dict[tuple[str, str], FileBinding] = {}
    for binding in bindings:
        key = (binding.label, binding.relative)
        previous = unique.setdefault(key, binding)
        if previous != binding:
            raise TransactionDrift(f"binding cambió durante procesamiento: {binding.relative}")
    return tuple(unique[key] for key in sorted(unique))


def _assert_transaction_bindings(
    root: Path,
    workspace_bindings: tuple[DirectoryBinding, ...],
    output_bindings: tuple[DirectoryBinding, ...],
    file_bindings: tuple[FileBinding, ...],
    provenance: dict,
) -> None:
    _assert_directory_bindings(workspace_bindings)
    _assert_directory_bindings(output_bindings)
    for binding in file_bindings:
        _assert_file_binding(root, binding)
    if _runtime_provenance() != provenance:
        raise TransactionDrift("binding cambió para el script ejecutado")


def _safe_evidence_directory(root: Path, directory: Path) -> bool:
    try:
        metadata = directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or directory.is_symlink()
            or _has_reparse_flag(directory)
        ):
            return False
        resolved = directory.resolve(strict=True)
    except OSError:
        return False
    return resolved != root and root in resolved.parents


def _write_evidence_marker(root: Path, directory: Path, name: str, payload: dict) -> None:
    if not _safe_evidence_directory(root, directory):
        return
    marker = directory / name
    try:
        if marker.exists() or marker.is_symlink() or _has_reparse_flag(marker):
            return
    except OSError:
        pass
    with marker.open("xb") as handle:
        handle.write(_json_bytes(payload))


def _write_failed_marker(root: Path, directory: Path, message: str, provenance: dict) -> None:
    marker = directory / "FAILED.json"
    if marker.exists():
        return
    _write_evidence_marker(
        root,
        directory,
        "FAILED.json",
        {
            "schema_version": 1,
            "status": "FAILED",
            "failure": {"code": "TRANSACTION_BINDING_DRIFT", "message": message},
            "approved": False,
            "promotion": {"allowed": False},
            "provenance": provenance,
        },
    )


def _write_invalidated_marker(root: Path, directory: Path, message: str, provenance: dict) -> None:
    _write_evidence_marker(
        root,
        directory,
        "INVALIDATED.json",
        {
            "schema_version": 1,
            "status": "INVALIDATED",
            "failure": {"code": "TRANSACTION_ARTIFACT_INVALIDATED", "message": message},
            "approved": False,
            "promotion": {"allowed": False},
            "provenance": provenance,
        },
    )


def _quarantine_uncommitted_pass_artifacts(root: Path, directory: Path) -> None:
    if not _safe_evidence_directory(root, directory):
        return
    moves = (
        (directory / "manifest.json", directory / "INVALIDATED_PASS_MANIFEST.json"),
        (directory / "receipts", directory / "INVALIDATED_RECEIPTS"),
    )
    for source, target in moves:
        try:
            metadata = source.lstat()
            safe_source = (
                not source.is_symlink()
                and not _has_reparse_flag(source)
                and (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
            )
            target_absent = not target.exists() and not target.is_symlink()
        except OSError:
            continue
        if safe_source and target_absent:
            os.rename(source, target)


def normalize_plan(plan_path: Path, output_dir: Path, workspace_root: Path) -> dict:
    """Ejecuta un plan local; errores de entry generan FAILED y errores de plan no escriben."""

    root, workspace_bindings = _capture_workspace_root(Path(workspace_root))
    provenance = _runtime_provenance()
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
        plan_path, plan_bytes, _plan_public, plan_binding = _read_bound_file(
            root,
            plan_relative,
            None,
            "PLAN",
            MAX_PLAN_BYTES,
        )
    except CandidateError as exc:
        raise PlanError(exc.message) from exc
    plan = _parse_plan_bytes(plan_bytes)
    entries = _validate_plan(plan)
    plan_sha = _sha256(plan_bytes)
    output, stage, output_bindings = _safe_output(
        root,
        Path(output_dir),
        plan_path,
        entries,
        plan_sha,
    )

    processed = []
    asset_pool: dict[str, bytes] = {}
    aggregate_asset_bytes = 0
    budget_exceeded = False
    for entry in entries:
        receipt, asset, bindings = _process_entry(root, entry, plan_sha, provenance)
        if asset is not None:
            if budget_exceeded:
                receipt = _declared_failure_receipt(
                    entry,
                    plan_sha,
                    provenance,
                    "AGGREGATE_ASSET_MEMORY_BUDGET_EXCEEDED",
                    "presupuesto agregado de assets excedido; candidato no retenido",
                    receipt,
                )
                asset = None
            else:
                asset_sha = receipt["output"]["sha256"]
                pooled = asset_pool.get(asset_sha)
                if pooled is not None:
                    asset = pooled
                elif len(asset) > MAX_AGGREGATE_ASSET_BYTES - aggregate_asset_bytes:
                    budget_exceeded = True
                    receipt = _declared_failure_receipt(
                        entry,
                        plan_sha,
                        provenance,
                        "AGGREGATE_ASSET_MEMORY_BUDGET_EXCEEDED",
                        (
                            "presupuesto agregado de assets excedido; "
                            f"límite {MAX_AGGREGATE_ASSET_BYTES} bytes"
                        ),
                        receipt,
                    )
                    asset = None
                else:
                    asset_pool[asset_sha] = asset
                    aggregate_asset_bytes += len(asset)
        processed.append(
            {"entry": entry, "receipt": receipt, "asset": asset, "bindings": bindings}
        )

    if budget_exceeded:
        for item in processed:
            if item["receipt"]["status"] == "PASS":
                item["receipt"] = _declared_failure_receipt(
                    item["entry"],
                    plan_sha,
                    provenance,
                    "AGGREGATE_ASSET_MEMORY_BUDGET_EXCEEDED",
                    "presupuesto agregado de assets excedido; lote cerrado sin assets",
                    item["receipt"],
                )
                item["asset"] = None
        asset_pool.clear()

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
                provenance,
                "OUTPUT_CONTENT_DUPLICATE",
                "asset idéntico no se comparte automáticamente",
                item["receipt"],
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
                    provenance,
                    "SHARED_VISUAL_GROUP_MEMBER_FAILED",
                    "otro miembro del grupo visual compartido falló; no se emite asset",
                    item["receipt"],
                )
            item["receipt"]["shared_visual_group"] = group_status
            item["asset"] = None

    file_bindings = [plan_binding]
    for item in processed:
        file_bindings.extend(item["bindings"])
    transaction_files = _deduplicate_file_bindings(file_bindings)

    def assert_stable() -> None:
        _assert_transaction_bindings(
            root,
            workspace_bindings,
            output_bindings,
            transaction_files,
            provenance,
        )

    published = False
    try:
        assert_stable()
        stage.mkdir()
        _assert_directory_bindings(output_bindings)
        try:
            stage_binding = _capture_directory(stage, "staging")
        except PlanError as exc:
            raise TransactionDrift(f"identidad de staging inválida: {exc}") from exc
        expected_artifacts: dict[str, ArtifactBinding] = {}

        def write_staged(relative: str, payload: bytes) -> None:
            if relative in expected_artifacts:
                raise TransactionDrift(f"artifact duplicado en staging: {relative}")
            _write_new(stage / relative, payload)
            expected_artifacts[relative] = _bind_written_artifact(stage, relative, payload)

        prepared = {
            "schema_version": 1,
            "status": "PREPARED",
            "plan_sha256": plan_sha,
            "approved": False,
            "promotion": {"allowed": False},
            "provenance": provenance,
        }
        write_staged("TRANSACTION_PREPARED.json", _json_bytes(prepared))
        written_assets: set[str] = set()
        for item in processed:
            if item["asset"] is None:
                continue
            asset_path = item["receipt"]["output"]["path"]
            if asset_path not in written_assets:
                write_staged(asset_path, item["asset"])
                written_assets.add(asset_path)

        for item in processed:
            if item["receipt"]["status"] == "PASS":
                item["receipt"]["inputs_unchanged"] = True

        receipt_payloads = []
        manifest_entries = []
        for item in processed:
            entry, receipt, asset = item["entry"], item["receipt"], item["asset"]
            entry_sha = _sha256(_canonical_bytes(entry))
            receipt_path = f"receipts/{entry_sha}.json"
            receipt_bytes = _json_bytes(receipt)
            receipt_payloads.append((receipt_path, receipt_bytes))
            record = {
                "internal_id": entry["internal_id"],
                "status": receipt["status"],
                "receipt_path": receipt_path,
                "receipt_sha256": _sha256(receipt_bytes),
            }
            if asset is not None:
                record.update(
                    asset_path=receipt["output"]["path"],
                    asset_sha256=receipt["output"]["sha256"],
                )
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
            "provenance": provenance,
        }
        manifest["logical_sha256"] = _sha256(_canonical_bytes(manifest))

        for receipt_path, receipt_bytes in receipt_payloads:
            write_staged(receipt_path, receipt_bytes)
        write_staged("manifest.json", _json_bytes(manifest))

        assert_stable()
        _assert_artifact_tree(stage, stage_binding, expected_artifacts)

        try:
            os.rename(stage, output)
        except FileExistsError as exc:
            raise TransactionDrift(
                "salida apareció durante publicación; staging preservado"
            ) from exc
        published = True
        _assert_artifact_tree(output, stage_binding, expected_artifacts)
        assert_stable()
        return manifest
    except TransactionDrift as exc:
        location = output if published else stage
        if location.is_dir():
            _quarantine_uncommitted_pass_artifacts(root, location)
            _write_invalidated_marker(root, location, str(exc), provenance)
            _write_failed_marker(root, location, str(exc), provenance)
        raise


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
    output = Path(args.output_dir)
    output = output if output.is_absolute() else Path(args.workspace_root) / output
    print(output.resolve(strict=True) / "manifest.json")
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
