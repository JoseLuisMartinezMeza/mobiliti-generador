"""Promueve imágenes verificadas a un dev-store sin reemplazar sus datos operativos."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit


ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
IMAGE_ATTRIBUTE_KEYS = ("image_reference", "source_image_url", "web_image_quality")
VALID_IMAGE_KINDS = {"official", "generated_reference"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON raíz inválido: {path}")
    return value


def _items_by_id(data: dict, supplier: str) -> dict[str, dict]:
    try:
        items = data["catalog_published_snapshots"][supplier]["payload"]["items"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Catálogo ausente o inválido: {supplier}") from exc
    if not isinstance(items, list):
        raise ValueError(f"Items inválidos: {supplier}")
    result: dict[str, dict] = {}
    for item in items:
        internal_id = str(item.get("internal_id") or "") if isinstance(item, dict) else ""
        if not internal_id or internal_id in result:
            raise ValueError(f"internal_id ausente o duplicado en {supplier}: {internal_id!r}")
        result[internal_id] = item
    return result


def _verified_object_name(item: dict) -> str:
    object_name = Path(urlsplit(str(item.get("image_url") or "")).path).name.lower()
    if not ASSET_NAME_RE.fullmatch(object_name):
        raise ValueError(f"image_url verificada inválida para {item.get('internal_id')}: {object_name!r}")
    return object_name


def _validate_asset(source_assets_dir: Path, object_name: str) -> Path:
    source = source_assets_dir / object_name
    if not source.is_file():
        raise ValueError(f"Asset verificado ausente: {object_name}")
    actual = _sha256(source.read_bytes())
    if actual != Path(object_name).stem:
        raise ValueError(f"SHA-256 inválido para asset verificado: {object_name}")
    return source


def promote_verified_catalog_images(
    *,
    active_db_path: Path,
    verified_db_path: Path,
    source_assets_dir: Path,
    target_assets_dir: Path,
    suppliers: tuple[str, ...],
    backup_path: Path,
    staged_path: Path,
    expected_active_sha256: str | None = None,
) -> dict:
    """Promueve solo campos visuales y conserva intacto el resto del dev-store."""

    active_db_path = Path(active_db_path)
    verified_db_path = Path(verified_db_path)
    source_assets_dir = Path(source_assets_dir)
    target_assets_dir = Path(target_assets_dir)
    backup_path = Path(backup_path)
    staged_path = Path(staged_path)

    active_bytes = active_db_path.read_bytes()
    active_sha = _sha256(active_bytes)
    if expected_active_sha256 and active_sha != expected_active_sha256.lower():
        raise ValueError(
            f"El dev-store cambió antes de promover imágenes: esperado "
            f"{expected_active_sha256.lower()}, actual {active_sha}"
        )
    if backup_path.exists():
        raise ValueError(f"El respaldo ya existe: {backup_path}")
    if staged_path.exists():
        raise ValueError(f"El staging ya existe: {staged_path}")

    active = _read_json(active_db_path)
    verified = _read_json(verified_db_path)
    promoted = copy.deepcopy(active)
    report_suppliers: dict[str, dict] = {}
    required_assets: dict[str, Path] = {}

    for raw_supplier in suppliers:
        supplier = str(raw_supplier or "").strip().lower()
        current_items = _items_by_id(promoted, supplier)
        verified_items = _items_by_id(verified, supplier)
        if set(current_items) != set(verified_items):
            missing = sorted(set(current_items) - set(verified_items))[:5]
            unexpected = sorted(set(verified_items) - set(current_items))[:5]
            raise ValueError(
                f"Identidades incompatibles en {supplier}; faltan={missing}, sobran={unexpected}"
            )

        kinds = {"official": 0, "generated_reference": 0}
        supplier_assets: set[str] = set()
        for internal_id, current_item in current_items.items():
            verified_item = verified_items[internal_id]
            image_kind = str(verified_item.get("image_kind") or "")
            if image_kind not in VALID_IMAGE_KINDS:
                raise ValueError(f"image_kind verificado inválido para {internal_id}: {image_kind!r}")
            object_name = _verified_object_name(verified_item)
            source = _validate_asset(source_assets_dir, object_name)
            required_assets.setdefault(object_name, source)
            supplier_assets.add(object_name)

            current_attributes = current_item.get("attributes")
            if not isinstance(current_attributes, dict):
                current_attributes = {}
                current_item["attributes"] = current_attributes
            verified_attributes = verified_item.get("attributes")
            if not isinstance(verified_attributes, dict):
                verified_attributes = {}
            for key in IMAGE_ATTRIBUTE_KEYS:
                if key in verified_attributes:
                    current_attributes[key] = copy.deepcopy(verified_attributes[key])
                else:
                    current_attributes.pop(key, None)

            current_item["image_url"] = ""
            current_item["image_kind"] = image_kind
            if verified_item.get("product_url"):
                current_item["product_url"] = verified_item["product_url"]
            current_attributes["approved_asset"] = {
                "bucket": "catalog-assets",
                "path": object_name,
                "image_kind": image_kind,
                "label": "Imagen oficial verificada" if image_kind == "official" else "Imagen de referencia",
                "approved": True,
            }
            kinds[image_kind] += 1

        report_suppliers[supplier] = {
            "items": len(current_items),
            "official": kinds["official"],
            "generated_reference": kinds["generated_reference"],
            "unique_assets": len(supplier_assets),
        }

    target_assets_dir.mkdir(parents=True, exist_ok=True)
    copied = existing = 0
    for object_name, source in sorted(required_assets.items()):
        target = target_assets_dir / object_name
        if target.exists():
            if not target.is_file() or _sha256(target.read_bytes()) != Path(object_name).stem:
                raise ValueError(f"Asset destino incompatible: {target}")
            existing += 1
            continue
        shutil.copy2(source, target)
        if _sha256(target.read_bytes()) != Path(object_name).stem:
            raise RuntimeError(f"La copia del asset no coincide: {object_name}")
        copied += 1

    promoted_bytes = json.dumps(promoted, ensure_ascii=False, indent=2).encode("utf-8")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(active_bytes)
    if backup_path.read_bytes() != active_bytes:
        raise RuntimeError("El respaldo del dev-store no coincide byte a byte")
    staged_path.write_bytes(promoted_bytes)
    if _sha256(staged_path.read_bytes()) != _sha256(promoted_bytes):
        raise RuntimeError("El staging del dev-store no coincide")
    shutil.copyfile(staged_path, active_db_path)
    if _sha256(active_db_path.read_bytes()) != _sha256(promoted_bytes):
        raise RuntimeError("El dev-store activo no coincide con el staging")

    return {
        "status": "passed",
        "active_db": str(active_db_path),
        "verified_db": str(verified_db_path),
        "backup": str(backup_path),
        "staged": str(staged_path),
        "before_sha256": active_sha,
        "after_sha256": _sha256(promoted_bytes),
        "suppliers": report_suppliers,
        "assets": {
            "required": len(required_assets),
            "copied": copied,
            "already_present": existing,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-db", required=True, type=Path)
    parser.add_argument("--verified-db", required=True, type=Path)
    parser.add_argument("--source-assets", required=True, type=Path)
    parser.add_argument("--target-assets", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--staged", required=True, type=Path)
    parser.add_argument(
        "--supplier",
        action="append",
        dest="suppliers",
        choices=("alma", "cr-global", "lumbro", "sonara", "sunon"),
        required=True,
    )
    parser.add_argument("--expected-active-sha256")
    parser.add_argument("--report", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    report = promote_verified_catalog_images(
        active_db_path=args.active_db,
        verified_db_path=args.verified_db,
        source_assets_dir=args.source_assets,
        target_assets_dir=args.target_assets,
        suppliers=tuple(args.suppliers),
        backup_path=args.backup,
        staged_path=args.staged,
        expected_active_sha256=args.expected_active_sha256,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if args.report.exists():
            raise ValueError(f"El reporte ya existe: {args.report}")
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
