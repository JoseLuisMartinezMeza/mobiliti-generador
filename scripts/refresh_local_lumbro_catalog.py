"""Publica el catálogo Lumbro auditado en el dev-store sin tocar otros datos."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_lumbro_catalog import _source_bundle
from mobiliti_saas.worker.catalog_sync.importers import (
    CatalogSnapshotBuild,
    build_lumbro_snapshot_with_assets,
)


ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
VISUAL_ATTRIBUTE_KEYS = (
    "approved_asset",
    "image_reference",
    "source_image_url",
    "web_image_quality",
)
VALID_IMAGE_KINDS = {"official", "generated_reference"}
REFERENCE_MIGRATION_CODES = {
    "lumbro:interconnection:2d632c7ea90d2bfe5371": "MULT-LIDO-CLAVIJA",
    "lumbro:interconnection:78483498abe20b48684a": "JUMP-1.5M",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON raíz inválido: {path}")
    return value


def _items_by_id(payload: dict) -> dict[str, dict]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Items Lumbro inválidos")
    result: dict[str, dict] = {}
    for item in items:
        internal_id = str(item.get("internal_id") or "") if isinstance(item, dict) else ""
        if not internal_id or internal_id in result:
            raise ValueError(f"internal_id Lumbro ausente o duplicado: {internal_id!r}")
        result[internal_id] = item
    return result


def _referenced_ids(value, candidates: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(_referenced_ids(child, candidates))
    elif isinstance(value, list):
        for child in value:
            found.update(_referenced_ids(child, candidates))
    elif isinstance(value, str) and value in candidates:
        found.add(value)
    return found


def _approved_asset(item: dict) -> dict | None:
    attributes = item.get("attributes")
    asset = attributes.get("approved_asset") if isinstance(attributes, dict) else None
    if not isinstance(asset, dict) or asset.get("approved") is not True:
        return None
    if asset.get("bucket") != "catalog-assets":
        return None
    object_name = str(asset.get("path") or "").lower()
    if not ASSET_NAME_RE.fullmatch(object_name):
        return None
    image_kind = str(asset.get("image_kind") or item.get("image_kind") or "")
    if image_kind not in VALID_IMAGE_KINDS:
        return None
    return {**copy.deepcopy(asset), "path": object_name, "image_kind": image_kind}


def _migrate_active_references(
    active: dict,
    refreshed_items: dict[str, dict],
) -> list[dict]:
    candidates_by_code: dict[str, list[dict]] = {}
    for item in refreshed_items.values():
        attributes = item.get("attributes")
        source_code = (
            str(attributes.get("source_code") or "").strip().upper()
            if isinstance(attributes, dict)
            else ""
        )
        if source_code:
            candidates_by_code.setdefault(source_code, []).append(item)

    migrations: list[dict] = []
    projects = active.get("projects")
    if not isinstance(projects, list):
        projects = []
    reservations = active.get("catalog_reservations")
    if not isinstance(reservations, list):
        reservations = []
    for old_internal_id, source_code in REFERENCE_MIGRATION_CODES.items():
        if not (
            _referenced_ids(projects, {old_internal_id})
            or _referenced_ids(reservations, {old_internal_id})
        ):
            continue
        matches = candidates_by_code.get(source_code.upper(), [])
        if len(matches) != 1:
            raise ValueError(
                f"Migración Lumbro no unívoca para {old_internal_id}: "
                f"{source_code} tiene {len(matches)} candidatos"
            )
        replacement = matches[0]
        new_internal_id = replacement["internal_id"]
        asset = _approved_asset(replacement)
        image_url = (
            f"http://127.0.0.1:8000/dev/catalog-assets/{asset['path']}"
            if asset is not None
            else ""
        )
        occurrences = 0
        for project in projects:
            payload = project.get("payload") if isinstance(project, dict) else None
            lines = payload.get("lines") if isinstance(payload, dict) else None
            if not isinstance(lines, list):
                continue
            for line in lines:
                identity = line.get("identity") if isinstance(line, dict) else None
                if not isinstance(identity, dict) or identity.get("internal_id") != old_internal_id:
                    continue
                identity["internal_id"] = new_internal_id
                line["official_code"] = new_internal_id
                display_cache = line.get("display_cache")
                if not isinstance(display_cache, dict):
                    display_cache = {}
                    line["display_cache"] = display_cache
                display_cache["name"] = replacement.get("name") or display_cache.get("name") or ""
                display_cache["code"] = new_internal_id
                display_cache["image_url"] = image_url
                occurrences += 1
        for reservation in reservations:
            if (
                isinstance(reservation, dict)
                and reservation.get("internal_id") == old_internal_id
            ):
                reservation["internal_id"] = new_internal_id
                occurrences += 1
        if occurrences:
            migrations.append(
                {
                    "from_internal_id": old_internal_id,
                    "to_internal_id": new_internal_id,
                    "source_code": source_code,
                    "project_or_reservation_references": occurrences,
                }
            )
    return migrations


def merge_lumbro_snapshot(
    active: dict,
    build: CatalogSnapshotBuild,
    *,
    assets_dir: Path,
) -> tuple[dict, dict]:
    """Sustituye solo Lumbro y preserva referencias visuales por identidad exacta."""

    snapshots = active.get("catalog_published_snapshots")
    current = snapshots.get("lumbro") if isinstance(snapshots, dict) else None
    current_payload = current.get("payload") if isinstance(current, dict) else None
    if not isinstance(current_payload, dict):
        raise ValueError("Catálogo Lumbro activo ausente")
    if build.snapshot.get("supplier") != "lumbro":
        raise ValueError("Build Lumbro inválido")

    current_items = _items_by_id(current_payload)
    refreshed_payload = copy.deepcopy(build.snapshot)
    refreshed_items = _items_by_id(refreshed_payload)
    retired_ids = set(current_items) - set(refreshed_items)
    refreshed = copy.deepcopy(active)
    migrations = _migrate_active_references(refreshed, refreshed_items)
    blocking_references = {
        "projects": sorted(_referenced_ids(refreshed.get("projects", []), retired_ids)),
        "catalog_reservations": sorted(
            _referenced_ids(refreshed.get("catalog_reservations", []), retired_ids)
        ),
    }
    blocking_references = {
        key: values for key, values in blocking_references.items() if values
    }
    if blocking_references:
        raise ValueError(
            "No se pueden retirar productos Lumbro con referencias activas: "
            + json.dumps(blocking_references, ensure_ascii=False, sort_keys=True)
        )

    preserved_references = 0
    for internal_id, item in refreshed_items.items():
        if _approved_asset(item) is not None:
            continue
        current_item = current_items.get(internal_id)
        asset = _approved_asset(current_item) if isinstance(current_item, dict) else None
        if asset is None:
            raise ValueError(f"Imagen aprobada ausente para {internal_id}")
        object_name = asset["path"]
        source = Path(assets_dir) / object_name
        if not source.is_file() or _sha256(source.read_bytes()) != Path(object_name).stem:
            raise ValueError(f"Asset preservado ausente o inválido: {object_name}")
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
            item["attributes"] = attributes
        old_attributes = current_item.get("attributes")
        if not isinstance(old_attributes, dict):
            old_attributes = {}
        for key in VISUAL_ATTRIBUTE_KEYS:
            if key in old_attributes:
                attributes[key] = copy.deepcopy(old_attributes[key])
        attributes["approved_asset"] = asset
        item["image_kind"] = asset["image_kind"]
        item["image_url"] = ""
        preserved_references += 1

    final_assets = set()
    image_kinds = {"official": 0, "generated_reference": 0}
    for internal_id, item in refreshed_items.items():
        asset = _approved_asset(item)
        if asset is None:
            raise ValueError(f"Imagen final no publicable para {internal_id}")
        final_assets.add(asset["path"])
        image_kinds[asset["image_kind"]] += 1
        item["image_url"] = ""

    generated_at = str(refreshed_payload.get("generated_at") or "")
    source_hash = str(refreshed_payload.get("source_hash") or "")
    refreshed["catalog_published_snapshots"]["lumbro"] = {
        "id": f"local-lumbro-column-e-{source_hash[:16]}",
        "supplier": "lumbro",
        "source_hash": source_hash,
        "generated_at": generated_at,
        "status": "published",
        "payload": refreshed_payload,
        "created_at": generated_at,
    }
    report = {
        "before_items": len(current_items),
        "after_items": len(refreshed_items),
        "retired_items": len(retired_ids),
        "retired_internal_ids": sorted(retired_ids),
        "reference_migrations": migrations,
        "preserved_reference_images": preserved_references,
        "official_images": image_kinds["official"],
        "generated_reference_images": image_kinds["generated_reference"],
        "unique_final_assets": len(final_assets),
        "product_groups": len(
            {str(item.get("product_key") or "") for item in refreshed_items.values()}
        ),
        "price_authority": refreshed_payload.get("metadata", {})
        .get("coverage", {})
        .get("price_authority"),
    }
    return refreshed, report


def refresh_local_lumbro_catalog(
    *,
    source_dir: Path,
    active_db_path: Path,
    assets_dir: Path,
    backup_path: Path,
    staged_path: Path,
    expected_active_sha256: str | None = None,
) -> dict:
    active_db_path = Path(active_db_path)
    assets_dir = Path(assets_dir)
    backup_path = Path(backup_path)
    staged_path = Path(staged_path)
    active_bytes = active_db_path.read_bytes()
    active_sha = _sha256(active_bytes)
    if expected_active_sha256 and active_sha != expected_active_sha256.lower():
        raise ValueError(
            f"El dev-store cambió: esperado {expected_active_sha256.lower()}, actual {active_sha}"
        )
    if backup_path.exists():
        raise ValueError(f"El respaldo ya existe: {backup_path}")
    if staged_path.exists():
        raise ValueError(f"El staging ya existe: {staged_path}")

    build = build_lumbro_snapshot_with_assets(_source_bundle(Path(source_dir)))
    active = json.loads(active_bytes.decode("utf-8"))
    refreshed, catalog_report = merge_lumbro_snapshot(
        active,
        build,
        assets_dir=assets_dir,
    )

    assets_dir.mkdir(parents=True, exist_ok=True)
    copied = existing = 0
    for digest, asset in sorted(build.assets_by_sha256.items()):
        if digest != asset.sha256 or _sha256(asset.data) != digest:
            raise ValueError(f"SHA-256 de asset oficial inválido: {digest}")
        object_name = f"{digest}.png"
        target = assets_dir / object_name
        if target.exists():
            if not target.is_file() or _sha256(target.read_bytes()) != digest:
                raise ValueError(f"Colisión de asset oficial: {object_name}")
            existing += 1
            continue
        target.write_bytes(asset.data)
        if _sha256(target.read_bytes()) != digest:
            raise RuntimeError(f"Fallo al copiar asset oficial: {object_name}")
        copied += 1

    refreshed_bytes = json.dumps(
        refreshed, ensure_ascii=False, indent=2
    ).encode("utf-8")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(active_bytes)
    if backup_path.read_bytes() != active_bytes:
        raise RuntimeError("El respaldo no coincide byte a byte")
    staged_path.write_bytes(refreshed_bytes)
    if _sha256(staged_path.read_bytes()) != _sha256(refreshed_bytes):
        raise RuntimeError("El staging no coincide")
    shutil.copyfile(staged_path, active_db_path)
    if active_db_path.read_bytes() != refreshed_bytes:
        raise RuntimeError("El dev-store activo no coincide con el staging")

    return {
        "status": "passed",
        "active_db": str(active_db_path),
        "backup": str(backup_path),
        "staged": str(staged_path),
        "before_sha256": active_sha,
        "after_sha256": _sha256(refreshed_bytes),
        "catalog": catalog_report,
        "official_assets": {
            "required": len(build.assets_by_sha256),
            "copied": copied,
            "already_present": existing,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--active-db", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--staged", required=True, type=Path)
    parser.add_argument("--expected-active-sha256")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = refresh_local_lumbro_catalog(
        source_dir=args.source_dir,
        active_db_path=args.active_db,
        assets_dir=args.assets_dir,
        backup_path=args.backup,
        staged_path=args.staged,
        expected_active_sha256=args.expected_active_sha256,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if args.report.exists():
            raise ValueError(f"El reporte ya existe: {args.report}")
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
