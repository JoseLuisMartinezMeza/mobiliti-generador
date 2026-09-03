"""Reconstruye y publica solo ALMA en el dev-store local con respaldo verificable."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mobiliti_saas.worker.catalog_sync.importers.alma import (
    AlmaSnapshotBuild,
    build_alma_snapshot_with_assets,
)


MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
VALID_IMAGE_KINDS = {"official", "generated_reference"}
VISUAL_ATTRIBUTE_KEYS = (
    "approved_asset",
    "image_reference",
    "source_image_url",
    "web_image_quality",
)
CELL_ROW_RE = re.compile(r"(?<![A-Za-z0-9])\$?[A-Z]{1,3}\$?(\d+)(?![A-Za-z0-9])")
BASE_OPTION_ROW_RE = re.compile(r"^base-r(\d+)-c\d+$")


@dataclass(frozen=True)
class LocalSourceFile:
    path: str
    kind: str
    brand: str
    sha256: str
    mime_type: str
    local_path: Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _items_by_id(payload: dict) -> dict[str, dict]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Items ALMA invalidos")
    result: dict[str, dict] = {}
    for item in items:
        internal_id = str(item.get("internal_id") or "") if isinstance(item, dict) else ""
        if not internal_id or internal_id in result:
            raise ValueError(f"internal_id ALMA ausente o duplicado: {internal_id!r}")
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


def _fold_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _source_rows(item: dict) -> set[int]:
    """Extrae filas de evidencia sin depender del formato JSON serializado."""

    values: list[object] = [item.get("source_reference")]
    attributes = item.get("attributes")
    if isinstance(attributes, dict):
        values.append(attributes.get("price_evidence"))
    rows: set[int] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
            return
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, str):
            return
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                visit(json.loads(stripped))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        for match in CELL_ROW_RE.finditer(value):
            rows.add(int(match.group(1)))

    for value in values:
        visit(value)
    return rows


def _legacy_continuation_targets(
    current_items: dict[str, dict],
    refreshed_items: dict[str, dict],
    retired_ids: set[str],
) -> dict[str, tuple[dict, dict]]:
    """Relaciona productos falsos de filas continuacion con su opcion canonica."""

    option_index: dict[tuple[int, str], list[tuple[dict, dict]]] = {}
    for item in refreshed_items.values():
        options = item.get("base_price_options")
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            match = BASE_OPTION_ROW_RE.fullmatch(str(option.get("id") or ""))
            option_name = str(option.get("name") or "").split(" | ", 1)[0].strip()
            if match is None or not option_name:
                continue
            key = (int(match.group(1)), _fold_text(option_name))
            option_index.setdefault(key, []).append((item, option))

    targets: dict[str, tuple[dict, dict]] = {}
    for internal_id in retired_ids:
        legacy = current_items[internal_id]
        if not internal_id.startswith("alma:mondecasa:"):
            continue
        if not str(legacy.get("product_key") or "").startswith("review:"):
            continue
        name_key = _fold_text(legacy.get("name"))
        matches: list[tuple[dict, dict]] = []
        for row in _source_rows(legacy):
            matches.extend(option_index.get((row, name_key), []))
        unique = {
            (str(item.get("internal_id") or ""), str(option.get("id") or "")): (item, option)
            for item, option in matches
        }
        if len(unique) == 1:
            targets[internal_id] = next(iter(unique.values()))
    return targets


def _migrate_project_continuation_lines(
    projects: object,
    targets: dict[str, tuple[dict, dict]],
) -> int:
    migrated = 0
    if not isinstance(projects, list):
        return migrated
    for project in projects:
        payload = project.get("payload") if isinstance(project, dict) else None
        lines = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict):
                continue
            identity = line.get("identity")
            old_id = str(identity.get("internal_id") or "") if isinstance(identity, dict) else ""
            target = targets.get(old_id)
            if target is None:
                continue
            item, option = target
            new_id = str(item.get("internal_id") or "")
            option_id = str(option.get("id") or "")
            option_label = str(option.get("name") or "").strip()
            product_label = option_label.split(" | ", 1)[0].strip() or str(item.get("name") or "")
            official_code = str(item.get("code") or item.get("sku") or new_id)
            identity["internal_id"] = new_id
            identity["base_option_id"] = option_id
            identity["add_on_option_ids"] = []
            line["official_code"] = official_code
            display_cache = line.get("display_cache")
            if not isinstance(display_cache, dict):
                display_cache = {}
                line["display_cache"] = display_cache
            display_cache["code"] = official_code
            display_cache["name"] = product_label
            display_cache["configuration"] = option_label
            migrated += 1
    return migrated


def _approved_asset(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
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


def merge_alma_snapshot(
    active: dict,
    build: AlmaSnapshotBuild,
    *,
    assets_dir: Path,
) -> tuple[dict, dict]:
    """Sustituye solo ALMA y conserva las imagenes curadas por identidad exacta."""

    snapshots = active.get("catalog_published_snapshots")
    current = snapshots.get("alma") if isinstance(snapshots, dict) else None
    current_payload = current.get("payload") if isinstance(current, dict) else None
    if not isinstance(current_payload, dict):
        raise ValueError("Catalogo ALMA activo ausente")
    if build.snapshot.get("supplier") != "alma":
        raise ValueError("Build ALMA invalido")

    current_items = _items_by_id(current_payload)
    refreshed_payload = copy.deepcopy(build.snapshot)
    refreshed_items = _items_by_id(refreshed_payload)
    retired_ids = set(current_items) - set(refreshed_items)
    refreshed = copy.deepcopy(active)
    migration_targets = _legacy_continuation_targets(
        current_items,
        refreshed_items,
        retired_ids,
    )
    migrated_references = _migrate_project_continuation_lines(
        refreshed.get("projects", []),
        migration_targets,
    )
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
            "No se pueden retirar productos ALMA con referencias activas: "
            + json.dumps(blocking_references, ensure_ascii=False, sort_keys=True)
        )

    assets_dir = Path(assets_dir)
    preserved_images = 0
    for internal_id, item in refreshed_items.items():
        current_item = current_items.get(internal_id)
        current_asset = _approved_asset(current_item)
        if current_asset is None:
            continue
        source = assets_dir / current_asset["path"]
        if not source.is_file() or _sha256(source.read_bytes()) != Path(current_asset["path"]).stem:
            raise ValueError(f"Asset ALMA curado ausente o invalido: {current_asset['path']}")
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
            item["attributes"] = attributes
        current_attributes = current_item.get("attributes")
        if not isinstance(current_attributes, dict):
            current_attributes = {}
        for key in VISUAL_ATTRIBUTE_KEYS:
            if key in current_attributes:
                attributes[key] = copy.deepcopy(current_attributes[key])
        attributes["approved_asset"] = current_asset
        item["image_kind"] = current_asset["image_kind"]
        item["image_url"] = ""
        preserved_images += 1

    final_assets: set[str] = set()
    for internal_id, item in refreshed_items.items():
        asset = _approved_asset(item)
        if asset is None:
            raise ValueError(f"Imagen final ALMA no publicable para {internal_id}")
        final_assets.add(asset["path"])
        item["image_url"] = ""

    generated_at = str(refreshed_payload.get("generated_at") or "")
    source_hash = str(refreshed_payload.get("source_hash") or "")
    refreshed["catalog_published_snapshots"]["alma"] = {
        "id": f"local-alma-configurable-{source_hash[:16]}",
        "supplier": "alma",
        "source_hash": source_hash,
        "generated_at": generated_at,
        "status": "published",
        "payload": refreshed_payload,
        "created_at": generated_at,
    }
    report = {
        "before_items": len(current_items),
        "after_items": len(refreshed_items),
        "new_items": len(set(refreshed_items) - set(current_items)),
        "retired_items": len(retired_ids),
        "preserved_images": preserved_images,
        "unique_final_assets": len(final_assets),
        "verified_codes": sum(
            item.get("code_status") == "verified" for item in refreshed_items.values()
        ),
        "needs_review_codes": sum(
            item.get("code_status") == "needs_review" for item in refreshed_items.values()
        ),
        "configurable_items": sum(
            bool(item.get("base_price_options")) for item in refreshed_items.values()
        ),
        "migrated_legacy_products": len(migration_targets),
        "migrated_legacy_references": migrated_references,
    }
    return refreshed, report


def _source(path: str, brand: str, local_path: Path) -> LocalSourceFile:
    local_path = Path(local_path)
    data = local_path.read_bytes()
    return LocalSourceFile(
        path=path,
        kind="spec_guide",
        brand=brand,
        sha256=_sha256(data),
        mime_type=MIME_XLSX,
        local_path=local_path,
    )


def build_source_bundle(
    *,
    kun_identity: Path,
    kun_cost: Path,
    mondecasa: Path,
) -> tuple[LocalSourceFile, ...]:
    return (
        _source("SPEC Guide-Alma-KUN.xlsx", "KUN", kun_identity),
        _source(
            "SPEC GUIDES 2026/ALMA/Spec guide-Alma-KUN Design.xlsx",
            "KUN",
            kun_cost,
        ),
        _source("SPEC Guide-Alma-Mondecasa.xlsx", "Mondecasa", mondecasa),
    )


def refresh_local_alma_catalog(
    *,
    kun_identity: Path,
    kun_cost: Path,
    mondecasa: Path,
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
            f"El dev-store cambio: esperado {expected_active_sha256.lower()}, actual {active_sha}"
        )
    if backup_path.exists():
        raise ValueError(f"El respaldo ya existe: {backup_path}")
    if staged_path.exists():
        raise ValueError(f"El staging ya existe: {staged_path}")

    bundle = build_source_bundle(
        kun_identity=kun_identity,
        kun_cost=kun_cost,
        mondecasa=mondecasa,
    )
    build = build_alma_snapshot_with_assets(bundle)
    active = json.loads(active_bytes.decode("utf-8"))
    refreshed, catalog_report = merge_alma_snapshot(active, build, assets_dir=assets_dir)

    assets_dir.mkdir(parents=True, exist_ok=True)
    copied = existing = 0
    for digest, asset in sorted(build.assets_by_sha256.items()):
        if digest != asset.sha256 or _sha256(asset.data) != digest:
            raise ValueError(f"SHA-256 de asset ALMA invalido: {digest}")
        object_name = f"{digest}.png"
        target = assets_dir / object_name
        if target.exists():
            if not target.is_file() or _sha256(target.read_bytes()) != digest:
                raise ValueError(f"Colision de asset ALMA: {object_name}")
            existing += 1
            continue
        target.write_bytes(asset.data)
        if _sha256(target.read_bytes()) != digest:
            raise RuntimeError(f"Fallo al copiar asset ALMA: {object_name}")
        copied += 1

    refreshed_bytes = json.dumps(refreshed, ensure_ascii=False, indent=2).encode("utf-8")
    if _sha256(active_db_path.read_bytes()) != active_sha:
        raise ValueError("El dev-store cambio durante la reconstruccion; no se publico ALMA")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(active_bytes)
    if backup_path.read_bytes() != active_bytes:
        raise RuntimeError("El respaldo ALMA no coincide byte a byte")
    staged_path.write_bytes(refreshed_bytes)
    if _sha256(staged_path.read_bytes()) != _sha256(refreshed_bytes):
        raise RuntimeError("El staging ALMA no coincide")
    shutil.copyfile(staged_path, active_db_path)
    if active_db_path.read_bytes() != refreshed_bytes:
        raise RuntimeError("El dev-store activo no coincide con el staging ALMA")

    return {
        "status": "passed",
        "active_db": str(active_db_path),
        "backup": str(backup_path),
        "staged": str(staged_path),
        "before_sha256": active_sha,
        "after_sha256": _sha256(refreshed_bytes),
        "sources": {source.path: source.sha256 for source in bundle},
        "catalog": catalog_report,
        "official_assets": {
            "required": len(build.assets_by_sha256),
            "copied": copied,
            "already_present": existing,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kun-identity", required=True, type=Path)
    parser.add_argument("--kun-cost", required=True, type=Path)
    parser.add_argument("--mondecasa", required=True, type=Path)
    parser.add_argument("--active-db", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--staged", required=True, type=Path)
    parser.add_argument("--expected-active-sha256")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = refresh_local_alma_catalog(
        kun_identity=args.kun_identity,
        kun_cost=args.kun_cost,
        mondecasa=args.mondecasa,
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
