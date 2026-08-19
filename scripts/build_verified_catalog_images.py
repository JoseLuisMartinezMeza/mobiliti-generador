"""Construye un catálogo visual verificado desde un manifiesto completo y auditable."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from PIL import Image, UnidentifiedImageError


ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
V2_ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.png$")
VALID_DECISIONS = {"retain", "replace"}
VALID_IMAGE_KINDS = {"official", "generated_reference"}
VALID_SOURCE_KINDS = {
    "catalog_pdf",
    "manufacturer_official",
    "authorized_distributor",
    "third_party_exact",
}
MAX_ASSET_BYTES = 8 * 1024 * 1024
MIN_CANVAS_SIDE = 1024
MAX_CANVAS_SIDE = 8192
MAX_CANVAS_PIXELS = 25_000_000
MAX_ASPECT_DELTA = 0.01


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON raíz inválido: {path}")
    return value


def _snapshot(data: dict, supplier: str) -> dict:
    try:
        value = data["catalog_published_snapshots"][supplier]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Catálogo ausente o inválido: {supplier}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Snapshot inválido: {supplier}")
    return value


def _items_by_id(snapshot: dict, supplier: str) -> dict[str, dict]:
    try:
        items = snapshot["payload"]["items"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Items ausentes o inválidos: {supplier}") from exc
    if not isinstance(items, list):
        raise ValueError(f"Items ausentes o inválidos: {supplier}")
    result: dict[str, dict] = {}
    for item in items:
        internal_id = str(item.get("internal_id") or "") if isinstance(item, dict) else ""
        if not internal_id or internal_id in result:
            raise ValueError(f"internal_id ausente o duplicado en {supplier}: {internal_id!r}")
        result[internal_id] = item
    return result


def _validate_asset(assets_dir: Path, object_name: str) -> Path:
    object_name = str(object_name or "").lower()
    if not ASSET_NAME_RE.fullmatch(object_name):
        raise ValueError(f"Nombre de asset inválido: {object_name!r}")
    path = Path(assets_dir) / object_name
    if not path.is_file():
        raise ValueError(f"Asset ausente: {object_name}")
    actual_sha256 = _sha256(path.read_bytes())
    if actual_sha256 != Path(object_name).stem:
        raise ValueError(f"SHA-256 inválido para asset: {object_name}")
    return path


def _is_https_url(value: object) -> bool:
    parsed = urlsplit(str(value or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def _canonical_url(value: object) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL con puerto inválido") from exc
    if (parsed.scheme.lower(), port) in {("https", 443), ("http", 80)}:
        port = None
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port, parsed.path.rstrip("/") or "/"


def _validate_product_url(value: object, image_source_url: object, source_kind: str, internal_id: str) -> str:
    product_url = str(value or "").strip()
    if not _is_https_url(product_url):
        raise ValueError(f"product_url inválido para {internal_id}")
    if _canonical_url(product_url) == _canonical_url(image_source_url):
        raise ValueError(f"product_url no puede confundirse con image_source_url para {internal_id}")

    parsed = urlsplit(product_url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    forbidden_segments = ("/buscar", "/search", "/familia", "/family", "/categoria", "/category", "/collection")
    image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif")
    search_query_keys = {"q", "query", "search", "s", "buscar", "keyword", "keywords", "term", "terms"}
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if (
        host.startswith("cdn.")
        or ".cdn." in host
        or path in {"", "/"}
        or path.endswith("/index.html")
        or any(segment in path for segment in forbidden_segments)
        or bool(query_keys & search_query_keys)
        or path.endswith(image_extensions)
    ):
        raise ValueError(f"product_url no es una página o ficha exacta para {internal_id}")
    if path.endswith(".pdf"):
        raise ValueError(f"product_url PDF requiere source_kind catalog_pdf para {internal_id}")
    return product_url


def _foreground_bbox(path: Path, internal_id: str) -> tuple[int, int, int, int, float]:
    data = path.read_bytes()
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError(f"Asset supera 8 MiB para {internal_id}")
    try:
        with Image.open(path) as checked:
            if checked.format != "PNG":
                raise ValueError(f"Asset v2 no es PNG real para {internal_id}")
            checked.verify()
        with Image.open(path) as source:
            width, height = source.size
            if width != height or width < MIN_CANVAS_SIDE or height < MIN_CANVAS_SIDE:
                raise ValueError(f"Lienzo v2 debe ser cuadrado y de al menos 1024×1024 para {internal_id}")
            if width > MAX_CANVAS_SIDE or height > MAX_CANVAS_SIDE:
                raise ValueError(f"Lienzo v2 supera 8192 px para {internal_id}")
            if width * height > MAX_CANVAS_PIXELS:
                raise ValueError(f"Lienzo v2 supera 25 Mpx para {internal_id}")
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Asset PNG inválido para {internal_id}") from exc

    corners = [image.getpixel(point) for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))]
    opaque_corners = [pixel for pixel in corners if pixel[3] >= 16]
    if opaque_corners and any(max(pixel[:3]) - min(pixel[:3]) > 16 for pixel in opaque_corners):
        raise ValueError(f"Fondo v2 no blanco o neutro para {internal_id}")
    transparent_canvas = not opaque_corners
    if opaque_corners:
        background = tuple(round(sum(pixel[channel] for pixel in opaque_corners) / len(opaque_corners)) for channel in range(3))
    else:
        background = (255, 255, 255)

    left, top, right, bottom = width, height, -1, -1
    foreground_pixels = 0
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = image.getpixel((x, y))
            if alpha < 16 or (
                not transparent_canvas
                and max(abs(red - background[0]), abs(green - background[1]), abs(blue - background[2])) <= 20
            ):
                continue
            foreground_pixels += 1
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if foreground_pixels == 0:
        raise ValueError(f"Asset v2 sin producto visible para {internal_id}")
    return left, top, right, bottom, foreground_pixels / (width * height)


def _validate_v2_asset(assets_dir: Path, object_name: str, image_reference: dict, internal_id: str) -> tuple[Path, dict]:
    object_name = str(object_name or "").lower()
    if not V2_ASSET_NAME_RE.fullmatch(object_name):
        raise ValueError(f"Asset v2 debe ser <sha256>.png para {internal_id}")
    path = _validate_asset(assets_dir, object_name)
    left, top, right, bottom, occupancy = _foreground_bbox(path, internal_id)
    with Image.open(path) as image:
        width, height = image.size
    bbox_width, bbox_height = right - left + 1, bottom - top + 1
    margin = min(left / width, top / height, (width - 1 - right) / width, (height - 1 - bottom) / height)
    if bbox_width / width > 0.92 or bbox_height / height > 0.92:
        raise ValueError(f"Asset v2 tiene caja mayor a 92 % para {internal_id}")
    if margin < 0.04:
        raise ValueError(f"Asset v2 tiene margen menor a 4 % para {internal_id}")
    if not 0.12 <= occupancy <= 0.80:
        raise ValueError(f"Asset v2 tiene ocupación fuera de 12–80 % para {internal_id}")
    dimensions = image_reference.get("source_dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError(f"source_dimensions ausentes para {internal_id}")
    try:
        source_width = float(dimensions["width"])
        source_height = float(dimensions["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"source_dimensions inválidas para {internal_id}") from exc
    if not all(math.isfinite(value) and value > 0 for value in (source_width, source_height)):
        raise ValueError(f"source_dimensions inválidas para {internal_id}")
    source_aspect = source_width / source_height
    final_aspect = bbox_width / bbox_height
    if abs(final_aspect / source_aspect - 1) > MAX_ASPECT_DELTA:
        raise ValueError(f"La relación de aspecto supera 1 % para {internal_id}")
    return path, {
        "sha256": Path(object_name).stem,
        "canvas": {"width": width, "height": height},
        "bbox": {"left": left, "top": top, "width": bbox_width, "height": bbox_height},
        "margin": margin,
        "occupancy": occupancy,
        "aspect_ratio": final_aspect,
    }


def _validate_v2_reference(entry: dict, internal_id: str, image_kind: str) -> tuple[dict, str]:
    reference = entry.get("image_reference")
    if not isinstance(reference, dict):
        raise ValueError(f"image_reference inválida para {internal_id}")
    if "placeholder" in str(entry.get("reason") or "").casefold() or "placeholder" in str(entry.get("status") or "").casefold():
        raise ValueError(f"Razón placeholder no permitida para {internal_id}")
    if "placeholder" in str(reference.get("status") or "").casefold():
        raise ValueError(f"image_reference placeholder no permitida para {internal_id}")
    if entry.get("quality_exception") is not None or reference.get("quality_exception") is not None:
        raise ValueError(f"quality_exception no puede permitir recorte, bordes o deformación para {internal_id}")
    source_kind = str(reference.get("source_kind") or "")
    if source_kind not in VALID_SOURCE_KINDS:
        raise ValueError(f"source_kind inválido para {internal_id}: {source_kind!r}")
    image_source_url = str(reference.get("image_source_url") or "").strip()
    if not _is_https_url(image_source_url):
        raise ValueError(f"image_source_url inválida para {internal_id}")
    if source_kind == "catalog_pdf":
        source = urlsplit(image_source_url)
        if not source.path.lower().endswith(".pdf") or not re.search(r"(?:^|&)page=\d+(?:&|$)", source.fragment):
            raise ValueError(f"image_source_url PDF debe incluir #page=N para {internal_id}")
    if not str(reference.get("source_locator") or "").strip():
        raise ValueError(f"source_locator ausente para {internal_id}")
    for field in ("reviewer", "reviewed_at"):
        if not str(reference.get(field) or "").strip():
            raise ValueError(f"{field} ausente para {internal_id}")
    try:
        datetime.fromisoformat(str(reference["reviewed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"reviewed_at inválido para {internal_id}") from exc
    for field in ("full_product_visible", "not_cropped", "configuration_supported", "approved"):
        if reference.get(field) is not True:
            raise ValueError(f"{field} debe ser true para {internal_id}")
    generated = reference.get("generated")
    if image_kind == "generated_reference":
        if generated is not True:
            raise ValueError(f"Referencia generada sin trazabilidad para {internal_id}")
        search = reference.get("exact_search")
        generation = reference.get("generation")
        if not isinstance(search, dict) or search.get("exhausted") is not True or not search.get("queries"):
            raise ValueError(f"Búsqueda exacta no agotada para {internal_id}")
        if not isinstance(generation, dict):
            raise ValueError(f"Trazabilidad de generación ausente para {internal_id}")
        for field in ("prompt", "model"):
            if not str(generation.get(field) or "").strip():
                raise ValueError(f"{field} de generación ausente para {internal_id}")
        references = generation.get("references")
        if not isinstance(references, list) or not references:
            raise ValueError(f"Referencias de generación ausentes para {internal_id}")
        for generated_reference in references:
            if not isinstance(generated_reference, dict) or not _is_https_url(generated_reference.get("url")):
                raise ValueError(f"Referencia HTTPS inválida para {internal_id}")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(generated_reference.get("sha256") or "")):
                raise ValueError(f"Hash de referencia inválido para {internal_id}")
    elif generated is not False:
        raise ValueError(f"Imagen oficial marcada como generada para {internal_id}")
    product_url = _validate_product_url(entry.get("product_url"), image_source_url, source_kind, internal_id)
    return reference, product_url


def _validate_v2_shared_assets(asset_entries: dict[str, list[tuple[str, dict]]], manifest: dict) -> None:
    matrix = manifest.get("shared_visual_equivalence_matrix")
    for asset, entries in asset_entries.items():
        if len(entries) < 2:
            continue
        groups = {str(entry.get("shared_visual_group") or "") for _, entry in entries}
        if len(groups) != 1 or not next(iter(groups)):
            raise ValueError(f"Asset compartido sin shared_visual_group: {asset}")
        group = next(iter(groups))
        if not isinstance(matrix, dict) or not isinstance(matrix.get(group), dict):
            raise ValueError(f"shared_visual_equivalence_matrix ausente para {group}")
        row = matrix[group]
        internal_ids = {internal_id for internal_id, _ in entries}
        if set(row.get("variant_internal_ids") or []) != internal_ids or not str(row.get("evidence") or "").strip():
            raise ValueError(f"Matriz shared_visual incompleta para {group}")
        source_url = str(row.get("same_source_url") or "").strip()
        if not _is_https_url(source_url):
            raise ValueError(f"Matriz shared_visual sin fuente HTTPS para {group}")
        for internal_id, entry in entries:
            evidence = entry["image_reference"].get("shared_visual_evidence")
            if not isinstance(evidence, dict) or evidence.get("source_url") != source_url:
                raise ValueError(f"Evidencia shared_visual inválida para {internal_id}")
            if set(evidence.get("assigned_variant_ids") or []) != internal_ids:
                raise ValueError(f"Evidencia shared_visual incompleta para {internal_id}")


def build_verified_catalog_images(
    *,
    active_db_path: Path,
    manifest_path: Path,
    assets_dir: Path,
    output_path: Path,
) -> dict:
    """Aplica decisiones visuales completas sin cambiar datos comerciales u operativos."""

    active_db_path = Path(active_db_path)
    manifest_path = Path(manifest_path)
    assets_dir = Path(assets_dir)
    output_path = Path(output_path)
    if output_path.exists():
        raise ValueError(f"El catálogo verificado ya existe: {output_path}")

    active_bytes = active_db_path.read_bytes()
    active = _read_json(active_db_path)
    manifest = _read_json(manifest_path)
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        raise ValueError("schema_version visual no soportada")

    supplier = str(manifest.get("supplier") or "").strip().lower()
    if not supplier:
        raise ValueError("supplier visual ausente")
    snapshot = _snapshot(active, supplier)
    if manifest.get("expected_snapshot_id") and manifest["expected_snapshot_id"] != snapshot.get("id"):
        raise ValueError("El snapshot activo no coincide con el manifiesto visual")
    if manifest.get("expected_source_hash") and manifest["expected_source_hash"] != snapshot.get("source_hash"):
        raise ValueError("El source_hash activo no coincide con el manifiesto visual")
    if manifest.get("expected_active_sha256"):
        actual_active_sha256 = _sha256(active_bytes)
        if str(manifest["expected_active_sha256"]).lower() != actual_active_sha256:
            raise ValueError("El dev-store activo cambió después de la auditoría visual")

    current_items = _items_by_id(snapshot, supplier)
    raw_decisions = manifest.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("decisions debe ser una lista")
    decisions: dict[str, dict] = {}
    for entry in raw_decisions:
        internal_id = str(entry.get("internal_id") or "") if isinstance(entry, dict) else ""
        if not internal_id or internal_id in decisions:
            raise ValueError(f"Decisión visual ausente o duplicada: {internal_id!r}")
        decisions[internal_id] = entry

    if set(decisions) != set(current_items):
        missing = sorted(set(current_items) - set(decisions))
        unexpected = sorted(set(decisions) - set(current_items))
        raise ValueError(
            f"El manifiesto requiere cobertura completa; faltan={missing}, sobran={unexpected}"
        )

    verified = copy.deepcopy(active)
    verified_items = _items_by_id(_snapshot(verified, supplier), supplier)
    counts = {"retain": 0, "replace": 0}
    kinds = {"official": 0, "generated_reference": 0}
    assets: set[str] = set()
    v2_asset_entries: dict[str, list[tuple[str, dict]]] = {}

    for internal_id, item in verified_items.items():
        entry = decisions[internal_id]
        expected_name = str(entry.get("name") or "")
        if expected_name != str(item.get("name") or ""):
            raise ValueError(f"Nombre incompatible para {internal_id}: {expected_name!r}")
        decision = str(entry.get("decision") or "")
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Decisión visual inválida para {internal_id}: {decision!r}")
        if entry.get("direct_product_reference") is not True:
            raise ValueError(f"La decisión no acredita referencia directa: {internal_id}")
        reason = str(entry.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Razón visual ausente para {internal_id}")

        image_kind = str(entry.get("image_kind") or "")
        if image_kind not in VALID_IMAGE_KINDS:
            raise ValueError(f"image_kind inválido para {internal_id}: {image_kind!r}")
        image_reference = entry.get("image_reference")
        if not isinstance(image_reference, dict):
            raise ValueError(f"image_reference inválida para {internal_id}")
        if schema_version == 2:
            image_reference, product_url = _validate_v2_reference(entry, internal_id, image_kind)
        else:
            generated = image_reference.get("generated")
            if image_kind == "generated_reference" and generated is not True:
                raise ValueError(f"Referencia generada sin trazabilidad para {internal_id}")
            if image_kind == "official" and generated is not False:
                raise ValueError(f"Imagen oficial marcada como generada para {internal_id}")
            product_url = entry.get("product_url")

        object_name = str(entry.get("asset") or "").lower()
        if schema_version == 2:
            _, asset_quality = _validate_v2_asset(assets_dir, object_name, image_reference, internal_id)
            image_reference["asset_quality"] = asset_quality
            v2_asset_entries.setdefault(object_name, []).append((internal_id, entry))
        else:
            _validate_asset(assets_dir, object_name)
        assets.add(object_name)
        item["image_url"] = f"/dev/catalog-assets/{object_name}"
        item["image_kind"] = image_kind
        if product_url:
            item["product_url"] = product_url

        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
            item["attributes"] = attributes
        reference = copy.deepcopy(image_reference)
        reference.update(
            {
                "direct_product_reference": True,
                "decision": decision,
                "reason": reason,
                "asset_sha256": Path(object_name).stem,
            }
        )
        attributes["image_reference"] = reference
        if schema_version == 1 and "source_image_url" in entry:
            attributes["source_image_url"] = entry["source_image_url"]
        elif schema_version == 1 and decision == "replace":
            attributes.pop("source_image_url", None)
        web_quality = entry.get("web_image_quality")
        if schema_version == 2 and isinstance(web_quality, dict):
            reference["web_image_quality"] = copy.deepcopy(web_quality)
            reference["web_image_quality"]["sha256"] = Path(object_name).stem
        elif isinstance(web_quality, dict):
            attributes["web_image_quality"] = copy.deepcopy(web_quality)
            attributes["web_image_quality"]["sha256"] = Path(object_name).stem
        elif schema_version == 1 and decision == "replace":
            attributes["web_image_quality"] = {
                "status": "generated_reference",
                "sha256": Path(object_name).stem,
            }
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": object_name,
            "image_kind": image_kind,
            "label": "Imagen oficial verificada" if image_kind == "official" else "Imagen de referencia",
            "approved": True,
        }
        counts[decision] += 1
        kinds[image_kind] += 1

    if schema_version == 2:
        _validate_v2_shared_assets(v2_asset_entries, manifest)

    output_bytes = json.dumps(verified, ensure_ascii=False, indent=2).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output_bytes)
    if _sha256(output_path.read_bytes()) != _sha256(output_bytes):
        raise RuntimeError("El catálogo visual verificado no coincide con el contenido preparado")

    return {
        "status": "passed",
        "supplier": supplier,
        "items": len(verified_items),
        "decisions": counts,
        "image_kinds": kinds,
        "unique_assets": len(assets),
        "active_sha256": _sha256(active_bytes),
        "verified_sha256": _sha256(output_bytes),
        "output": str(output_path),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-db", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    report = build_verified_catalog_images(
        active_db_path=args.active_db,
        manifest_path=args.manifest,
        assets_dir=args.assets,
        output_path=args.output,
    )
    if args.report:
        if args.report.exists():
            raise ValueError(f"El reporte ya existe: {args.report}")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
