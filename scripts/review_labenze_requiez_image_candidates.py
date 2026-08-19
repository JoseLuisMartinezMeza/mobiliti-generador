"""Prepara una cola local, inmutable y auditable de candidatos visuales."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

try:
    from scripts.research_labenze_requiez_images import (
        IMAGE_HOSTS,
        PRODUCT_PAGE_HOSTS,
        ResearchCandidate,
        validate_candidate_source_policy,
        validate_candidate_urls,
        validate_source_resource_url,
    )
except ModuleNotFoundError as exc:  # Permite ejecutar este archivo directamente desde scripts/.
    if not str(exc.name or "").startswith("scripts"):
        raise
    from research_labenze_requiez_images import (  # type: ignore[no-redef]
        IMAGE_HOSTS,
        PRODUCT_PAGE_HOSTS,
        ResearchCandidate,
        validate_candidate_source_policy,
        validate_candidate_urls,
        validate_source_resource_url,
    )


CANONICAL_INVENTORY_SHA256 = "476013bf863552d4e622f510c39a019fc1549859714edbd1e8b76994d31a0812"
CANONICAL_RESEARCH_LOGICAL_SHA256 = (
    "7bf193a76086c610212eb0ad4f724c149d46e491f51bc4b6c83c2166fe0165f2"
)
EXPECTED_SUPPLIER_COUNTS = {"labenze": 462, "requiez": 314}
EXPECTED_RESEARCH_COUNTS = {"found_exact": 120, "rejected": 120, "exhausted": 536}
EXPECTED_DOWNLOADED_CANDIDATES = 284
EXPECTED_UNIQUE_ORIGINALS = 282
MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 8192
MAX_IMAGE_PIXELS = 25_000_000
MAX_ASPECT_RATIO = 20 / 3
MIN_ASPECT_RATIO = 3 / 20
SHEET_COLUMNS = 4
SHEET_ROWS = 4
TILE_WIDTH = 400
TILE_HEIGHT = 420
IMAGE_AREA_MARGIN = 12
IMAGE_AREA_HEIGHT = 276
REVIEW_CHECKS = (
    "identity_exact",
    "configuration_supported",
    "full_product_visible",
    "not_cropped",
    "correct_base",
    "correct_arms",
    "correct_seats_table",
    "correct_finish",
    "clean_background",
)
ORIGINAL_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpe?g|webp)$")
MIME_BY_FORMAT = {
    "PNG": ("image/png", {".png"}),
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "WEBP": ("image/webp", {".webp"}),
}
SOURCE_KIND_POLICY = {
    "api-productos.requiez.com": {"manufacturer_official"},
    "nogalbeat.com": {"authorized_distributor"},
    "nogalbeatstore.com": {"authorized_distributor"},
    "3rin.com.mx": {"authorized_distributor"},
    "arterio.mx": {"authorized_distributor"},
    "infinitidesign.it": {"manufacturer_official"},
}


def build_candidate_id(
    inventory_row: Mapping[str, object],
    candidate: Mapping[str, object],
    image_sha256: str,
) -> str:
    """Deriva un ID estable de identidad, fuente, URLs y contenido original."""

    material = {
        "identity": {
            "supplier": inventory_row.get("supplier"),
            "internal_id": inventory_row.get("internal_id"),
            "product_key": inventory_row.get("product_key"),
            "sku": inventory_row.get("sku"),
            "source_code": inventory_row.get("source_code"),
        },
        "source": {
            "source_name": candidate.get("source_name"),
            "source_kind": candidate.get("source_kind"),
            "source_id": candidate.get("source_id"),
        },
        "urls": {
            "product_url": candidate.get("product_url"),
            "image_source_url": candidate.get("image_source_url"),
        },
        "sha256": image_sha256,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_json_bytes(value))


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    path.write_text(payload, encoding="utf-8", newline="\n")


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: _csv_cell(value)
                    for key, value in row.items()
                }
            )


def _csv_cell(value: object) -> object:
    serialized: object = (
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        if isinstance(value, (dict, list))
        else value
    )
    if not isinstance(serialized, str) or not serialized:
        return serialized
    index = 0
    leading_control = False
    while index < len(serialized):
        character = serialized[index]
        if not (character.isspace() or unicodedata.category(character).startswith("C")):
            break
        leading_control = leading_control or character in "\t\r\n" or unicodedata.category(
            character
        ).startswith("C")
        index += 1
    formula_prefix = index < len(serialized) and serialized[index] in "=+-@"
    return f"'{serialized}" if leading_control or formula_prefix else serialized


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} inválido: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} debe ser un objeto JSON: {path}")
    return value


def _load_jsonl(path: Path, label: str) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} ilegible: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} JSONL inválido en línea {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{label} contiene fila no objeto en línea {line_number}")
        rows.append(row)
    return rows


def _has_reparse_flag(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _assert_regular_file(path: Path, label: str, *, reject_alias: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} ausente: {path}") from exc
    if path.is_symlink() or _has_reparse_flag(path) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} no es archivo regular sin enlace/reparse: {path}")
    if reject_alias and getattr(metadata, "st_nlink", 1) != 1:
        raise ValueError(f"{label} es un alias/hardlink y no un original independiente: {path}")


def _safe_manifest_path(root: Path, raw_relative: object) -> Path:
    value = str(raw_relative or "")
    pure = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise ValueError(f"Ruta de manifest insegura: {value!r}")
    candidate = root.joinpath(*pure.parts)
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved_root not in resolved.parents:
        raise ValueError(f"Ruta de manifest escapa la raíz: {value!r}")
    return candidate


def _validate_artifact_manifest(root: Path, *, nested: bool) -> dict[str, str]:
    manifest_path = root / "artifact-hashes.json"
    _assert_regular_file(manifest_path, "manifest de artefactos")
    payload = _load_json(manifest_path, "manifest de artefactos")
    hashes = payload.get("sha256") if nested else payload
    if not isinstance(hashes, dict):
        raise ValueError("Manifest de artefactos no contiene el mapa SHA-256 esperado")
    declared: dict[str, str] = {}
    for raw_relative, raw_digest in hashes.items():
        relative = str(raw_relative)
        digest = str(raw_digest).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"SHA-256 inválido en manifest: {relative}")
        path = _safe_manifest_path(root, relative)
        _assert_regular_file(path, f"artefacto declarado {relative}")
        actual = _sha256_file(path)
        if actual != digest:
            raise ValueError(
                f"Manifest/hash divergente para {relative}: esperado={digest}, actual={actual}"
            )
        declared[relative] = digest
    actual_files: set[str] = set()
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_symlink() or _has_reparse_flag(path):
            raise ValueError(f"Artefacto es symlink/reparse: {path}")
        if path.is_file() and path.name != "artifact-hashes.json":
            actual_files.add(path.relative_to(root).as_posix())
    if actual_files != set(declared):
        missing = sorted(set(declared) - actual_files)
        extra = sorted(actual_files - set(declared))
        raise ValueError(f"Manifest no reconcilia artefactos: faltantes={missing}, adicionales={extra}")
    return declared


def _tree_fingerprint(path: Path) -> dict[str, object]:
    path = Path(path)
    if path.is_file():
        _assert_regular_file(path, "entrada protegida")
        candidates = [path]
        root = path.parent
    elif path.is_dir():
        candidates = []
        root = path
        for entry in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
            if entry.is_symlink() or _has_reparse_flag(entry):
                raise ValueError(f"Entrada protegida contiene symlink/reparse: {entry}")
            if entry.is_file():
                candidates.append(entry)
    else:
        raise ValueError(f"Entrada protegida ausente: {path}")
    files = []
    total_bytes = 0
    for entry in candidates:
        size = entry.stat().st_size
        total_bytes += size
        files.append(
            {
                "path": entry.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": _sha256_file(entry),
            }
        )
    material = _canonical_json_bytes(files)
    return {
        "files": len(files),
        "bytes": total_bytes,
        "sha256": hashlib.sha256(material).hexdigest(),
    }


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left in right.parents or right in left.parents


def validate_output_path(output_dir: Path, protected_paths: Sequence[Path]) -> Path:
    output = Path(output_dir).resolve()
    for protected_path in protected_paths:
        protected = Path(protected_path).resolve()
        if _paths_overlap(output, protected):
            raise ValueError(f"La salida se solapa con entrada protegida: {protected}")
    if output.exists():
        raise ValueError(f"La salida ya existe: {output}")
    return output


def _snapshot_inputs(paths: Mapping[str, Path]) -> dict[str, dict[str, object]]:
    return {name: _tree_fingerprint(path) for name, path in sorted(paths.items())}


def _normalized_code(value: object) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _validate_inventory(
    inventory_dir: Path,
    *,
    expected_inventory_sha256: str,
    pdf_hashes: Mapping[str, str],
) -> tuple[list[dict], dict]:
    _validate_artifact_manifest(inventory_dir, nested=True)
    inventory_path = inventory_dir / "inventory.jsonl"
    actual_inventory_sha = _sha256_file(inventory_path)
    if actual_inventory_sha != expected_inventory_sha256.lower():
        raise ValueError(
            "SHA-256 de inventario divergente: "
            f"esperado={expected_inventory_sha256}, actual={actual_inventory_sha}"
        )
    summary = _load_json(inventory_dir / "summary.json", "summary de inventario")
    input_hashes = summary.get("input_hashes")
    if not isinstance(input_hashes, dict):
        raise ValueError("Summary de inventario sin input_hashes")
    for supplier, actual_hash in pdf_hashes.items():
        if str(input_hashes.get(supplier) or "").lower() != actual_hash:
            raise ValueError(f"Fingerprint PDF divergente para {supplier}")
    rows = _load_jsonl(inventory_path, "inventario")
    expected_total = sum(EXPECTED_SUPPLIER_COUNTS.values())
    if len(rows) != expected_total:
        raise ValueError(
            f"Inventario debe tener exactamente {expected_total} identidades; tiene {len(rows)}"
        )
    seen: set[str] = set()
    counts = Counter()
    for row in rows:
        supplier = str(row.get("supplier") or "").casefold()
        internal_id = str(row.get("internal_id") or "")
        if supplier not in EXPECTED_SUPPLIER_COUNTS:
            raise ValueError(f"Supplier de inventario desconocido: {supplier!r}")
        if not internal_id or internal_id in seen:
            raise ValueError(f"Identidad de inventario ausente o duplicada: {internal_id!r}")
        seen.add(internal_id)
        counts[supplier] += 1
        for field in ("product_key", "source_code"):
            if not str(row.get(field) or "").strip():
                raise ValueError(f"Identidad {internal_id} carece de {field}")
        if str(row.get("source_hash") or "").lower() != pdf_hashes[supplier]:
            raise ValueError(f"source_hash divergente en inventario: {internal_id}")
        signature = row.get("visual_signature")
        if not isinstance(signature, dict) or not re.fullmatch(
            r"[0-9a-f]{64}", str(signature.get("sha256") or "")
        ):
            raise ValueError(f"visual_signature inválida en inventario: {internal_id}")
    if dict(counts) != EXPECTED_SUPPLIER_COUNTS:
        raise ValueError(f"Conteos supplier divergentes en inventario: {dict(counts)}")
    rows.sort(key=lambda row: (str(row["supplier"]), str(row["internal_id"])))
    return rows, summary


def _logical_research_sha(rows: Sequence[Mapping[str, object]]) -> str:
    logical_rows = [
        {key: value for key, value in row.items() if key != "researched_at"}
        for row in rows
    ]
    payload = b"".join(_canonical_json_bytes(row) + b"\n" for row in logical_rows)
    return hashlib.sha256(payload).hexdigest()


def _identity_tuple(row: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(
        row.get(field)
        for field in ("supplier", "internal_id", "product_key", "sku", "source_code", "source_hash")
    )


def _validate_research(
    research_dir: Path,
    inventory_rows: Sequence[Mapping[str, object]],
    *,
    expected_inventory_sha256: str,
    expected_research_logical_sha256: str,
) -> tuple[list[dict], dict]:
    _validate_artifact_manifest(research_dir, nested=False)
    summary = _load_json(research_dir / "summary.json", "summary de investigación")
    rows = _load_jsonl(research_dir / "candidates.jsonl", "investigación")
    actual_logical_sha = _logical_research_sha(rows)
    declared_logical_sha = str(summary.get("logical_candidates_sha256") or "").lower()
    expected_logical_sha = expected_research_logical_sha256.lower()
    if declared_logical_sha != expected_logical_sha or actual_logical_sha != expected_logical_sha:
        raise ValueError(
            "Hash lógico de investigación divergente: "
            f"esperado={expected_logical_sha}, declarado={declared_logical_sha}, actual={actual_logical_sha}"
        )
    if str(summary.get("inventory_sha256") or "").lower() != expected_inventory_sha256.lower():
        raise ValueError("Investigación referencia otro manifest/hash de inventario")
    expected_total = sum(EXPECTED_SUPPLIER_COUNTS.values())
    if len(rows) != expected_total or int(summary.get("rows", -1)) != expected_total:
        raise ValueError("Investigación no contiene exactamente el conjunto de 776 identidades")
    research_by_id: dict[str, dict] = {}
    for row in rows:
        internal_id = str(row.get("internal_id") or "")
        if not internal_id or internal_id in research_by_id:
            raise ValueError(f"Identidad de investigación ausente o duplicada: {internal_id!r}")
        research_by_id[internal_id] = row
    inventory_by_id = {str(row["internal_id"]): row for row in inventory_rows}
    downloaded_candidates = 0
    if set(research_by_id) != set(inventory_by_id):
        raise ValueError("Conjunto de identidades de investigación diverge del inventario")
    for internal_id, inventory_row in inventory_by_id.items():
        research_row = research_by_id[internal_id]
        if _identity_tuple(inventory_row) != _identity_tuple(research_row):
            differing = [
                field
                for field in ("supplier", "internal_id", "product_key", "sku", "source_code", "source_hash")
                if inventory_row.get(field) != research_row.get(field)
            ]
            label = "source_hash" if "source_hash" in differing else "identidad"
            raise ValueError(f"{label} divergente en investigación: {internal_id} ({differing})")
        if inventory_row.get("visual_signature") != research_row.get("visual_signature"):
            raise ValueError(f"Identidad visual divergente en investigación: {internal_id}")
        candidates = research_row.get("candidates")
        if not isinstance(candidates, list) or research_row.get("candidate_count") != len(candidates):
            raise ValueError(f"candidate_count divergente: {internal_id}")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError(f"Candidato no es objeto: {internal_id}")
            if candidate.get("approved") is not False:
                raise ValueError(f"Candidato fuente contiene aprobación no permitida: {internal_id}")
            downloaded_candidates += candidate.get("download", {}).get("status") == "downloaded"
    status_counts = Counter(str(row.get("status") or "") for row in rows)
    if {key: status_counts[key] for key in EXPECTED_RESEARCH_COUNTS} != EXPECTED_RESEARCH_COUNTS:
        raise ValueError(f"Estados estructurados divergentes: {dict(status_counts)}")
    if summary.get("counts") != EXPECTED_RESEARCH_COUNTS:
        raise ValueError("Summary de investigación contiene conteos divergentes")
    if (
        downloaded_candidates != EXPECTED_DOWNLOADED_CANDIDATES
        or summary.get("downloaded_candidates") != EXPECTED_DOWNLOADED_CANDIDATES
    ):
        raise ValueError(
            "Conteo de candidatos descargados divergente: "
            f"filas={downloaded_candidates}, summary={summary.get('downloaded_candidates')}"
        )
    rows.sort(key=lambda row: (str(row["supplier"]), str(row["internal_id"])))
    return rows, summary


def _magic_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Original tiene MIME/magic no permitido")


def _foreground_metrics(image: Image.Image) -> dict[str, object]:
    sample = image.convert("RGBA")
    sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
    width, height = sample.size
    corners = [
        sample.getpixel(point)
        for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    ]
    opaque_corners = [pixel for pixel in corners if pixel[3] >= 16]
    alpha = sample.getchannel("A")
    alpha_mask = alpha.point(lambda value: 255 if value >= 16 else 0)
    if not opaque_corners:
        mask = alpha_mask
    else:
        background = tuple(
            round(sum(pixel[channel] for pixel in opaque_corners) / len(opaque_corners))
            for channel in range(3)
        )
        difference = ImageChops.difference(sample.convert("RGB"), Image.new("RGB", sample.size, background))
        red, green, blue = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        color_mask = maximum.point(lambda value: 255 if value > 20 else 0)
        mask = ImageChops.darker(color_mask, alpha_mask)
    bbox = mask.getbbox()
    foreground = mask.histogram()[255]
    occupancy = foreground / (width * height)
    if bbox is None:
        return {
            "foreground_bbox": None,
            "occupancy": occupancy,
            "margins": None,
            "bbox_touches_border": True,
        }
    left, top, right, bottom = bbox
    original_width, original_height = image.size
    scale_x = original_width / width
    scale_y = original_height / height
    scaled_left = max(0, math.floor(left * scale_x))
    scaled_top = max(0, math.floor(top * scale_y))
    scaled_right = min(original_width, math.ceil(right * scale_x))
    scaled_bottom = min(original_height, math.ceil(bottom * scale_y))
    margins = {
        "left": left / width,
        "top": top / height,
        "right": (width - right) / width,
        "bottom": (height - bottom) / height,
    }
    return {
        "foreground_bbox": {
            "left": scaled_left,
            "top": scaled_top,
            "width": scaled_right - scaled_left,
            "height": scaled_bottom - scaled_top,
        },
        "occupancy": occupancy,
        "margins": margins,
        "bbox_touches_border": min(margins.values()) <= 0.0,
    }


def inspect_original(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    expected_mime: str,
    expected_dimensions: Mapping[str, object],
) -> dict[str, object]:
    """Valida bytes originales y calcula métricas sin alterar el archivo."""

    path = Path(path)
    _assert_regular_file(path, "original", reject_alias=True)
    if not ORIGINAL_NAME_RE.fullmatch(path.name):
        raise ValueError(f"object_name original no es content-addressed: {path.name}")
    expected_sha = str(expected_sha256).lower()
    if path.stem != expected_sha:
        raise ValueError("Nombre content-addressed no coincide con SHA declarado")
    file_size = path.stat().st_size
    if file_size != int(expected_bytes):
        raise ValueError("Bytes declarados no coinciden con original")
    if file_size <= 0 or file_size > MAX_ORIGINAL_BYTES:
        raise ValueError("Original excede límite de bytes")
    data = path.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("SHA-256 de original divergente")
    magic_mime = _magic_mime(data)
    if magic_mime != str(expected_mime):
        raise ValueError("MIME/magic de original diverge del manifest")
    try:
        with Image.open(path) as probe:
            image_format = probe.format
            decoded_size = probe.size
            frame_count = getattr(probe, "n_frames", 1)
            if image_format not in MIME_BY_FORMAT:
                raise ValueError("Formato Pillow de original no permitido")
            actual_mime, allowed_suffixes = MIME_BY_FORMAT[image_format]
            if actual_mime != magic_mime or path.suffix.lower() not in allowed_suffixes:
                raise ValueError("Extensión/MIME/formato de original inconsistente")
            if frame_count != 1:
                raise ValueError("Original animado no permitido")
            width, height = decoded_size
            if (
                width <= 0
                or height <= 0
                or width > MAX_IMAGE_SIDE
                or height > MAX_IMAGE_SIDE
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Dimensiones de original exceden límites de seguridad")
            probe.verify()
        with Image.open(path) as source:
            source.load()
            image = source.copy()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Pillow no pudo verificar/cargar el original") from exc
    expected_width = int(expected_dimensions.get("width", 0))
    expected_height = int(expected_dimensions.get("height", 0))
    if (width, height) != (expected_width, expected_height):
        raise ValueError("Dimensiones decodificadas divergen de download metadata")
    foreground = _foreground_metrics(image)
    aspect_ratio = width / height
    checks = {
        "source_shortest_side_512_plus": min(width, height) >= 512,
        "dimensions_match_download_metadata": True,
        "aspect_ratio_not_extreme": MIN_ASPECT_RATIO <= aspect_ratio <= MAX_ASPECT_RATIO,
        "foreground_detected": foreground["foreground_bbox"] is not None,
        "bbox_not_touching_edges": foreground["bbox_touches_border"] is False,
    }
    reasons = []
    if not checks["source_shortest_side_512_plus"]:
        reasons.append("source_shortest_side_below_512")
    if not checks["aspect_ratio_not_extreme"]:
        reasons.append("aspect_ratio_extreme_possible_deformation")
    if not checks["foreground_detected"]:
        reasons.append("foreground_not_detected")
    if not checks["bbox_not_touching_edges"]:
        reasons.append("foreground_bbox_touches_border")
    has_alpha = "A" in image.getbands() or "transparency" in image.info
    return {
        "sha256": actual_sha,
        "bytes": file_size,
        "mime": magic_mime,
        "mode": image.mode,
        "dimensions": {"width": width, "height": height},
        "min_dimension": min(width, height),
        "max_dimension": max(width, height),
        "aspect_ratio": aspect_ratio,
        "has_alpha": has_alpha,
        "foreground_bbox": foreground["foreground_bbox"],
        "occupancy": foreground["occupancy"],
        "margins": foreground["margins"],
        "automatic_gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "reasons": reasons,
        },
    }


def _safe_original_name(value: object) -> str:
    name = str(value or "")
    if not ORIGINAL_NAME_RE.fullmatch(name) or Path(name).name != name:
        raise ValueError(f"object_name/path original inseguro o no content-addressed: {name!r}")
    return name


def _configuration_text(row: Mapping[str, object]) -> str:
    signature = row.get("visual_signature")
    fields = signature.get("fields") if isinstance(signature, dict) else {}
    if not isinstance(fields, dict):
        return ""
    values: list[str] = []
    for key in ("model", "variant"):
        value = str(fields.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    for key in ("base_options", "add_on_options"):
        raw = fields.get(key)
        if isinstance(raw, list):
            values.extend(str(value).strip() for value in raw if str(value).strip())
    return " | ".join(values)


def _pending_review() -> dict[str, object]:
    return {
        "approved": False,
        "reviewer": "",
        "reviewed_at": None,
        "checks": {check: None for check in REVIEW_CHECKS},
    }


def _strict_url_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"URL {field} debe ser texto HTTPS sin whitespace exterior")
    if any(character.isspace() or unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"URL {field} contiene whitespace o control no permitido")
    decoded = value
    try:
        for _ in range(6):
            next_decoded = unquote(decoded, errors="strict")
            if any(
                character.isspace() or unicodedata.category(character).startswith("C")
                for character in next_decoded
            ):
                raise ValueError(f"URL {field} contiene control percent-encoded no permitido")
            if next_decoded == decoded:
                break
            decoded = next_decoded
    except UnicodeDecodeError as exc:
        raise ValueError(f"URL {field} contiene percent-encoding inválido") from exc
    return value


def _canonical_https_url(value: str) -> str:
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").casefold().rstrip(".")
    port = parsed.port
    netloc = host if port in {None, 443} else f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path, parsed.query, ""))


def _validate_redirect_evidence(
    research_dir: Path,
    *,
    requested_url: str,
    final_url: str,
    download: Mapping[str, object],
) -> None:
    cache_name = hashlib.sha256(requested_url.encode("utf-8")).hexdigest() + ".json"
    cache_path = research_dir / "http-cache" / cache_name
    _assert_regular_file(cache_path, "evidencia cache de redirect")
    evidence = _load_json(cache_path, "evidencia cache de redirect")
    try:
        body = base64.b64decode(evidence["body_base64"], validate=True)
        status = int(evidence["status"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Evidencia cache de redirect inválida") from exc
    actual_sha = hashlib.sha256(body).hexdigest()
    if (
        evidence.get("request_url") != requested_url
        or evidence.get("response_url") != final_url
        or status != 200
        or evidence.get("body_sha256") != actual_sha
        or actual_sha != str(download.get("sha256") or "").lower()
        or len(body) != int(download.get("bytes", -1))
    ):
        raise ValueError("Redirect no coincide con evidencia cache de Task 6A")


def validate_candidate_download_urls(
    candidate: Mapping[str, object],
    download: Mapping[str, object],
    research_dir: Path,
) -> None:
    """Revalida la política URL de Task 6A sin efectuar solicitudes de red."""

    source_name = str(candidate.get("source_name") or "")
    source_kind = str(candidate.get("source_kind") or "")
    canonical_source = source_name.casefold().rstrip(".")
    if source_name != source_name.strip() or source_kind not in SOURCE_KIND_POLICY.get(
        canonical_source, set()
    ):
        raise ValueError(
            f"source_kind incoherente con fuente {source_name!r}: {source_kind!r}"
        )
    product_url = _strict_url_text(candidate.get("product_url"), "product_url")
    image_url = _strict_url_text(candidate.get("image_source_url"), "image_source_url")
    requested_url = _strict_url_text(download.get("requested_url"), "download.requested_url")
    final_url = _strict_url_text(download.get("final_url"), "download.final_url")
    if requested_url != image_url:
        raise ValueError("URL download.requested_url no coincide con image_source_url")
    research_candidate = ResearchCandidate(
        source_name=source_name,
        source_kind=source_kind,
        source_id=str(candidate.get("source_id") or ""),
        query=str(candidate.get("query") or ""),
        matched_field=str(candidate.get("matched_field") or ""),
        product_url=product_url,
        image_source_url=image_url,
        evidence=candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {},
        approved=False,
    )
    validate_candidate_source_policy(research_candidate)
    validate_candidate_urls(
        research_candidate,
        allowed_product_hosts=PRODUCT_PAGE_HOSTS,
        allowed_image_hosts=IMAGE_HOSTS,
    )
    validate_source_resource_url(final_url, source_name=source_name, resource_kind="image")
    if _canonical_https_url(final_url) != _canonical_https_url(requested_url):
        _validate_redirect_evidence(
            research_dir,
            requested_url=requested_url,
            final_url=final_url,
            download=download,
        )


def _candidate_rows(
    inventory_rows: Sequence[Mapping[str, object]],
    research_rows: Sequence[Mapping[str, object]],
    research_dir: Path,
) -> tuple[list[dict], dict[str, list[dict]]]:
    originals_dir = research_dir / "originals"
    inventory_by_id = {str(row["internal_id"]): row for row in inventory_rows}
    metrics_cache: dict[str, dict] = {}
    declared_originals: set[str] = set()
    candidate_ids: set[str] = set()
    output_rows: list[dict] = []
    by_identity: dict[str, list[dict]] = defaultdict(list)
    downloaded_count = 0
    for research_row in research_rows:
        if research_row.get("status") != "found_exact":
            continue
        inventory_row = inventory_by_id[str(research_row["internal_id"])]
        for candidate in research_row["candidates"]:
            if not isinstance(candidate, dict):
                raise ValueError(f"Candidato inválido: {research_row['internal_id']}")
            download = candidate.get("download")
            if not isinstance(download, dict) or download.get("status") != "downloaded":
                raise ValueError(f"found_exact contiene candidato no descargado: {research_row['internal_id']}")
            downloaded_count += 1
            validate_candidate_download_urls(candidate, download, research_dir)
            object_name = _safe_original_name(download.get("object_name"))
            sha256 = str(download.get("sha256") or "").lower()
            declared_originals.add(object_name)
            if object_name not in metrics_cache:
                metrics_cache[object_name] = inspect_original(
                    originals_dir / object_name,
                    expected_sha256=sha256,
                    expected_bytes=int(download.get("bytes", -1)),
                    expected_mime=str(download.get("mime") or ""),
                    expected_dimensions=download.get("dimensions") or {},
                )
            metrics = metrics_cache[object_name]
            candidate_id = build_candidate_id(inventory_row, candidate, sha256)
            if candidate_id in candidate_ids:
                raise ValueError(f"candidate_id determinista duplicado: {candidate_id}")
            candidate_ids.add(candidate_id)
            row = {
                "schema_version": 1,
                "candidate_id": candidate_id,
                "candidate_id_short": candidate_id[:12],
                "supplier": inventory_row["supplier"],
                "internal_id": inventory_row["internal_id"],
                "product_key": inventory_row["product_key"],
                "sku": inventory_row["sku"],
                "source_code": inventory_row["source_code"],
                "source_hash": inventory_row["source_hash"],
                "name": inventory_row.get("name", ""),
                "collection": inventory_row.get("collection", ""),
                "description": inventory_row.get("description", ""),
                "visual_signature": inventory_row.get("visual_signature"),
                "configuration": _configuration_text(inventory_row),
                "product_url": candidate.get("product_url", ""),
                "image_source_url": candidate.get("image_source_url", ""),
                "source_kind": candidate.get("source_kind", ""),
                "source_name": candidate.get("source_name", ""),
                "source_id": candidate.get("source_id", ""),
                "matched_field": candidate.get("matched_field", ""),
                "evidence": candidate.get("evidence", {}),
                "original": {
                    "path": f"originals/{object_name}",
                    "object_name": object_name,
                    "sha256": metrics["sha256"],
                    "bytes": metrics["bytes"],
                    "mime": metrics["mime"],
                    "dimensions": metrics["dimensions"],
                    "mode": metrics["mode"],
                },
                "metrics": {
                    key: metrics[key]
                    for key in (
                        "min_dimension",
                        "max_dimension",
                        "aspect_ratio",
                        "has_alpha",
                        "foreground_bbox",
                        "occupancy",
                        "margins",
                    )
                },
                "automatic_gate": metrics["automatic_gate"],
                "review": _pending_review(),
            }
            output_rows.append(row)
            by_identity[str(inventory_row["internal_id"])].append(row)
    if downloaded_count != EXPECTED_DOWNLOADED_CANDIDATES:
        raise ValueError(
            f"Candidatos descargados divergentes: {downloaded_count} != {EXPECTED_DOWNLOADED_CANDIDATES}"
        )
    if len(declared_originals) != EXPECTED_UNIQUE_ORIGINALS:
        raise ValueError(
            f"Originales únicos divergentes: {len(declared_originals)} != {EXPECTED_UNIQUE_ORIGINALS}"
        )
    actual_originals = {
        path.relative_to(originals_dir).as_posix()
        for path in originals_dir.rglob("*")
        if path.is_file()
    }
    if actual_originals != declared_originals:
        missing = sorted(declared_originals - actual_originals)
        extra = sorted(actual_originals - declared_originals)
        raise ValueError(f"Original faltante/adicional no declarado: faltantes={missing}, adicionales={extra}")
    output_rows.sort(
        key=lambda row: (str(row["supplier"]), str(row["internal_id"]), row["candidate_id"])
    )
    for index, row in enumerate(output_rows, 1):
        row["index"] = index
    return output_rows, by_identity


def _search_rows(
    inventory_rows: Sequence[Mapping[str, object]],
    research_rows: Sequence[Mapping[str, object]],
    candidates_by_identity: Mapping[str, Sequence[Mapping[str, object]]],
) -> list[dict]:
    research_by_id = {str(row["internal_id"]): row for row in research_rows}
    code_counts = Counter(
        (str(row["supplier"]).casefold(), _normalized_code(row.get("sku") or row.get("source_code")))
        for row in inventory_rows
    )
    rows = []
    for inventory_row in inventory_rows:
        internal_id = str(inventory_row["internal_id"])
        research_row = research_by_id[internal_id]
        candidates = list(candidates_by_identity.get(internal_id, []))
        technical_passed = sum(candidate["automatic_gate"]["passed"] is True for candidate in candidates)
        collision_key = (
            str(inventory_row["supplier"]).casefold(),
            _normalized_code(inventory_row.get("sku") or inventory_row.get("source_code")),
        )
        reason = str(research_row.get("reason") or "")
        collision = code_counts[collision_key] > 1 or "collision" in reason.casefold()
        if collision:
            next_action = "additional_web_search_collision"
        elif technical_passed:
            next_action = "human_review_candidates"
        else:
            next_action = "additional_web_search"
        rows.append(
            {
                "schema_version": 1,
                "supplier": inventory_row["supplier"],
                "internal_id": internal_id,
                "product_key": inventory_row["product_key"],
                "sku": inventory_row["sku"],
                "source_code": inventory_row["source_code"],
                "source_hash": inventory_row["source_hash"],
                "name": inventory_row.get("name", ""),
                "collection": inventory_row.get("collection", ""),
                "description": inventory_row.get("description", ""),
                "visual_signature": inventory_row.get("visual_signature"),
                "structured_status": research_row.get("status"),
                "structured_reason": research_row.get("reason"),
                "structured_candidate_count": research_row.get("candidate_count"),
                "candidate_count": len(candidates),
                "technical_gate_passed_count": technical_passed,
                "next_action": next_action,
                "internet_exhausted": False,
            }
        )
    rows.sort(key=lambda row: (str(row["supplier"]), str(row["internal_id"])))
    return rows


def _wrap_label(text: object, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = " ".join(str(text or "").split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        proposed = word if not current else f"{current} {word}"
        if font.getlength(proposed) <= max_width:
            current = proposed
            continue
        if current:
            lines.append(current)
            current = ""
        remainder = word
        while remainder and font.getlength(remainder) > max_width:
            split_at = 1
            while (
                split_at < len(remainder)
                and font.getlength(remainder[: split_at + 1]) <= max_width
            ):
                split_at += 1
            lines.append(remainder[:split_at])
            remainder = remainder[split_at:]
        current = remainder
    if current:
        lines.append(current)
    return lines or [""]


def _render_contact_sheets(
    output_dir: Path,
    candidates: Sequence[Mapping[str, object]],
    originals_dir: Path,
) -> tuple[dict, dict]:
    sheets_dir = output_dir / "contact-sheets"
    sheets_dir.mkdir()
    font = ImageFont.load_default()
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["supplier"])].append(candidate)
    sheet_records = []
    tile_records = []
    per_sheet = SHEET_COLUMNS * SHEET_ROWS
    for supplier in sorted(grouped):
        supplier_candidates = grouped[supplier]
        for offset in range(0, len(supplier_candidates), per_sheet):
            page_candidates = supplier_candidates[offset : offset + per_sheet]
            local_start = offset + 1
            local_end = offset + len(page_candidates)
            relative_path = (
                f"contact-sheets/{supplier}-candidates-{local_start:04d}-{local_end:04d}.png"
            )
            canvas = Image.new(
                "RGB",
                (SHEET_COLUMNS * TILE_WIDTH, SHEET_ROWS * TILE_HEIGHT),
                "white",
            )
            draw = ImageDraw.Draw(canvas)
            for tile_number, candidate in enumerate(page_candidates, 1):
                column = (tile_number - 1) % SHEET_COLUMNS
                row_number = (tile_number - 1) // SHEET_COLUMNS
                left = column * TILE_WIDTH
                top = row_number * TILE_HEIGHT
                right = left + TILE_WIDTH
                bottom = top + TILE_HEIGHT
                draw.rectangle((left, top, right - 1, bottom - 1), outline=(45, 80, 90), width=2)
                area = (
                    left + IMAGE_AREA_MARGIN,
                    top + IMAGE_AREA_MARGIN,
                    right - IMAGE_AREA_MARGIN,
                    top + IMAGE_AREA_HEIGHT,
                )
                draw.rectangle(area, fill="white", outline=(185, 195, 200), width=1)
                object_name = str(candidate["original"]["object_name"])
                with Image.open(originals_dir / object_name) as source:
                    source.load()
                    rgba = source.convert("RGBA")
                contained = ImageOps.contain(
                    rgba,
                    (area[2] - area[0] - 16, area[3] - area[1] - 16),
                    Image.Resampling.LANCZOS,
                )
                white = Image.new("RGBA", contained.size, "white")
                white.alpha_composite(contained)
                paste_left = area[0] + (area[2] - area[0] - contained.width) // 2
                paste_top = area[1] + (area[3] - area[1] - contained.height) // 2
                canvas.paste(white.convert("RGB"), (paste_left, paste_top))
                text_y = area[3] + 8
                gate = "PASS" if candidate["automatic_gate"]["passed"] else "FAIL"
                max_label_width = TILE_WIDTH - 28
                lines = [
                    f"#{candidate['index']:03d} {candidate['candidate_id_short']}",
                    f"{str(candidate['supplier']).upper()} | {candidate['sku']}",
                ]
                lines.extend(
                    _wrap_label(
                        f"Config: {candidate['configuration']}",
                        font,
                        max_label_width,
                    )
                )
                lines.extend(
                    _wrap_label(
                        f"Fuente: {candidate['source_name']}",
                        font,
                        max_label_width,
                    )
                )
                lines.append(
                    f"{candidate['original']['dimensions']['width']}x"
                    f"{candidate['original']['dimensions']['height']} | gate {gate}"
                )
                label_records = []
                for line in lines:
                    text_bbox = draw.textbbox((left + 14, text_y), line, font=font)
                    if text_bbox[2] > right - 14 or text_bbox[3] > bottom - 10:
                        raise ValueError(
                            f"Label no cabe completo en tile de {candidate['candidate_id']}"
                        )
                    draw.text((left + 14, text_y), line, fill=(10, 28, 36), font=font)
                    label_records.append({"text": line, "bbox": list(text_bbox)})
                    text_y += 21
                tile_records.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "candidate_index": candidate["index"],
                        "sheet": relative_path,
                        "tile": tile_number,
                        "bbox": [left, top, right, bottom],
                        "image_area_bbox": list(area),
                        "image_bbox": [
                            paste_left,
                            paste_top,
                            paste_left + contained.width,
                            paste_top + contained.height,
                        ],
                        "fit": "contain",
                        "labels": label_records,
                    }
                )
            path = output_dir / relative_path
            canvas.save(path, format="PNG", optimize=False)
            sheet_records.append(
                {
                    "path": relative_path,
                    "supplier": supplier,
                    "page": offset // per_sheet + 1,
                    "candidate_count": len(page_candidates),
                    "first_candidate_index": page_candidates[0]["index"],
                    "last_candidate_index": page_candidates[-1]["index"],
                    "dimensions": {"width": canvas.width, "height": canvas.height},
                    "sha256": _sha256_file(path),
                }
            )
    return (
        {"schema_version": 1, "sheets": sheet_records},
        {"schema_version": 1, "tiles": tile_records},
    )


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(output_dir).as_posix(): _sha256_file(path)
        for path in sorted(output_dir.rglob("*"), key=lambda value: value.as_posix())
        if path.is_file() and path.name != "artifact-hashes.json"
    }


def _write_failure_receipt(output_dir: Path, exc: Exception, stage: str, reviewed_at: str) -> None:
    try:
        if output_dir.is_dir():
            receipt = output_dir / "FAILED.json"
            if not receipt.exists():
                _write_json(
                    receipt,
                    {
                        "schema_version": 1,
                        "status": "failed",
                        "stage": stage,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "reviewed_at": reviewed_at,
                    },
                )
    except OSError:
        return


def run_review(
    *,
    inventory_dir: Path,
    research_dir: Path,
    labenze_pdf: Path,
    requiez_pdf: Path,
    store_path: Path,
    assets_dir: Path,
    output_dir: Path,
    expected_inventory_sha256: str = CANONICAL_INVENTORY_SHA256,
    expected_research_logical_sha256: str = CANONICAL_RESEARCH_LOGICAL_SHA256,
    reviewed_at: str | None = None,
) -> dict[str, object]:
    """Valida Task 5/6A y crea artefactos de revisión sin mutar entradas."""

    inventory_dir = Path(inventory_dir).resolve()
    research_dir = Path(research_dir).resolve()
    labenze_pdf = Path(labenze_pdf).resolve()
    requiez_pdf = Path(requiez_pdf).resolve()
    store_path = Path(store_path).resolve()
    assets_dir = Path(assets_dir).resolve()
    protected_paths = {
        "assets": assets_dir,
        "inventory": inventory_dir,
        "labenze_pdf": labenze_pdf,
        "requiez_pdf": requiez_pdf,
        "research": research_dir,
        "store": store_path,
    }
    output_dir = validate_output_path(output_dir, list(protected_paths.values()))
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    stage = "snapshot_inputs_before"
    try:
        before = _snapshot_inputs(protected_paths)
        pdf_hashes = {
            "labenze": _sha256_file(labenze_pdf),
            "requiez": _sha256_file(requiez_pdf),
        }
        stage = "validate_inventory"
        inventory_rows, _inventory_summary = _validate_inventory(
            inventory_dir,
            expected_inventory_sha256=expected_inventory_sha256,
            pdf_hashes=pdf_hashes,
        )
        stage = "validate_research"
        research_rows, research_summary = _validate_research(
            research_dir,
            inventory_rows,
            expected_inventory_sha256=expected_inventory_sha256,
            expected_research_logical_sha256=expected_research_logical_sha256,
        )
        expected_research_inputs = research_summary.get("inputs_before")
        if not isinstance(expected_research_inputs, dict):
            raise ValueError("Summary de investigación sin fingerprints de entradas")
        for key in ("store", "assets"):
            if expected_research_inputs.get(key) != before[key]:
                raise ValueError(f"Fingerprint actual de {key} diverge de Task 6A")
        stage = "inspect_candidates"
        candidates, candidates_by_identity = _candidate_rows(
            inventory_rows,
            research_rows,
            research_dir,
        )
        search_rows = _search_rows(inventory_rows, research_rows, candidates_by_identity)
        stage = "render_contact_sheets"
        sheet_inventory, sheet_index = _render_contact_sheets(
            output_dir,
            candidates,
            research_dir / "originals",
        )
        decisions = {"schema_version": 1, "decisions": []}
        stage = "write_outputs"
        _write_jsonl(output_dir / "candidate-review.jsonl", candidates)
        _write_csv(output_dir / "candidate-review.csv", candidates)
        _write_jsonl(output_dir / "search-queue.jsonl", search_rows)
        _write_csv(output_dir / "search-queue.csv", search_rows)
        _write_json(output_dir / "contact-sheets.json", sheet_inventory)
        _write_json(output_dir / "contact-sheet-index.json", sheet_index)
        _write_json(output_dir / "decisions.json", decisions)
        logical_material = {
            "candidates": candidates,
            "search_queue": search_rows,
            "contact_sheets": sheet_inventory,
            "contact_sheet_index": sheet_index,
            "decisions": decisions,
        }
        logical_sha = hashlib.sha256(_canonical_json_bytes(logical_material)).hexdigest()
        stage = "snapshot_inputs_after"
        after = _snapshot_inputs(protected_paths)
        unchanged = before == after
        counts = {
            "candidates": len(candidates),
            "identities": len(search_rows),
            "sheets": len(sheet_inventory["sheets"]),
            "technical_gate_passed": sum(
                candidate["automatic_gate"]["passed"] is True for candidate in candidates
            ),
            "unique_originals": len({candidate["original"]["sha256"] for candidate in candidates}),
        }
        summary = {
            "schema_version": 1,
            "status": "passed" if unchanged else "failed",
            "reviewed_at": timestamp,
            "counts": counts,
            "actions": dict(sorted(Counter(row["next_action"] for row in search_rows).items())),
            "structured_statuses": dict(
                sorted(Counter(row["structured_status"] for row in search_rows).items())
            ),
            "logical_review_sha256": logical_sha,
            "input_hashes": {
                "inventory_jsonl": expected_inventory_sha256.lower(),
                "research_logical": expected_research_logical_sha256.lower(),
                "labenze_pdf": pdf_hashes["labenze"],
                "requiez_pdf": pdf_hashes["requiez"],
                "store": before["store"]["sha256"],
                "assets": before["assets"]["sha256"],
            },
            "inputs_before": before,
            "inputs_after": after,
            "inputs_unchanged": unchanged,
        }
        _write_json(output_dir / "summary.json", summary)
        _write_json(output_dir / "artifact-hashes.json", _artifact_hashes(output_dir))
        if not unchanged:
            raise RuntimeError("PDFs/DB/assets/inventario/research cambiaron durante la revisión")
        return summary
    except Exception as exc:
        _write_failure_receipt(output_dir, exc, stage, timestamp)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepara cola y láminas locales de candidatos Labenze/Requiez."
    )
    parser.add_argument("--inventory-dir", type=Path, required=True)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--labenze-pdf", type=Path, required=True)
    parser.add_argument("--requiez-pdf", type=Path, required=True)
    parser.add_argument("--store", dest="store_path", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--expected-inventory-sha256",
        default=CANONICAL_INVENTORY_SHA256,
    )
    parser.add_argument(
        "--expected-research-logical-sha256",
        default=CANONICAL_RESEARCH_LOGICAL_SHA256,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    summary = run_review(
        inventory_dir=arguments.inventory_dir,
        research_dir=arguments.research_dir,
        labenze_pdf=arguments.labenze_pdf,
        requiez_pdf=arguments.requiez_pdf,
        store_path=arguments.store_path,
        assets_dir=arguments.assets_dir,
        output_dir=arguments.output_dir,
        expected_inventory_sha256=arguments.expected_inventory_sha256,
        expected_research_logical_sha256=arguments.expected_research_logical_sha256,
    )
    print(
        json.dumps(
            {
                "output": str(arguments.output_dir.resolve()),
                **summary["counts"],
                "logical_review_sha256": summary["logical_review_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
