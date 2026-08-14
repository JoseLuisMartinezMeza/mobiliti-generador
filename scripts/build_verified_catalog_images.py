"""Construye un catálogo visual verificado desde un manifiesto completo y auditable."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path


ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
VALID_DECISIONS = {"retain", "replace"}
VALID_IMAGE_KINDS = {"official", "generated_reference"}


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
    if manifest.get("schema_version") != 1:
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
        generated = image_reference.get("generated")
        if image_kind == "generated_reference" and generated is not True:
            raise ValueError(f"Referencia generada sin trazabilidad para {internal_id}")
        if image_kind == "official" and generated is not False:
            raise ValueError(f"Imagen oficial marcada como generada para {internal_id}")

        object_name = str(entry.get("asset") or "").lower()
        _validate_asset(assets_dir, object_name)
        assets.add(object_name)
        item["image_url"] = f"/dev/catalog-assets/{object_name}"
        item["image_kind"] = image_kind
        if entry.get("product_url"):
            item["product_url"] = entry["product_url"]

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
        if "source_image_url" in entry:
            attributes["source_image_url"] = entry["source_image_url"]
        elif decision == "replace":
            attributes.pop("source_image_url", None)
        web_quality = entry.get("web_image_quality")
        if isinstance(web_quality, dict):
            attributes["web_image_quality"] = copy.deepcopy(web_quality)
            attributes["web_image_quality"]["sha256"] = Path(object_name).stem
        elif decision == "replace":
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
