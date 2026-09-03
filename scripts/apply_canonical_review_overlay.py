"""Aplica un overlay visual canónico al dev-store sin afirmar aprobación o promoción."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from scripts.promote_verified_catalog_images import (
    _copy_asset_atomically,
    _publish_active_transactionally,
)


OVERLAY_KEY = "canonical_review_overlay"
ALLOWED_CLASSIFICATIONS = {
    "valid_exact_reviewed",
    "candidate_qa_pass_unapproved",
    "candidate_semantic_pass_technical_blocked",
    "candidate_pending_qa",
    "blocked_no_deterministic_asset",
    "conflict",
    "missing",
}
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(data: bytes, label: str) -> dict:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} debe tener una raíz JSON de objeto")
    return value


def _catalog_items(data: dict, supplier: str) -> list[dict]:
    try:
        items = data["catalog_published_snapshots"][supplier]["payload"]["items"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Catálogo activo ausente o inválido: {supplier}") from exc
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"Items activos inválidos: {supplier}")
    return items


def _items_by_id(items: list[dict], supplier: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        internal_id = str(item.get("internal_id") or "")
        if not internal_id or internal_id in result:
            raise ValueError(f"internal_id activo ausente o duplicado en {supplier}: {internal_id!r}")
        result[internal_id] = item
    return result


def _mapped_internal_id(row: dict, current_ids: set[str]) -> str:
    internal_id = str(row.get("internal_id") or "")
    if internal_id in current_ids:
        return internal_id
    migration = row.get("migration")
    migrated_from = str(migration.get("from_internal_id") or "") if isinstance(migration, dict) else ""
    if migrated_from in current_ids:
        return migrated_from
    raise ValueError(f"Identidad canónica no existe en el snapshot activo: {internal_id!r}")


def _asset_for_row(row: dict, workspace: Path) -> tuple[dict | None, Path | None]:
    candidate = row.get("asset_or_candidate")
    if candidate is None:
        return None, None
    if not isinstance(candidate, dict) or not isinstance(candidate.get("exists"), bool):
        raise ValueError(f"asset_or_candidate inválido para {row.get('internal_id')}")
    if candidate["exists"] is False:
        return None, None
    declared_raw = candidate.get("declared_sha256")
    declared = str(declared_raw or "").lower()
    observed = str(candidate.get("actual_sha256") or "").lower()
    valid_observed = len(observed) == 64 and all(character in "0123456789abcdef" for character in observed)
    valid_declared = len(declared) == 64 and all(character in "0123456789abcdef" for character in declared)
    if declared_raw is None:
        expected = observed
        hash_metadata_valid = valid_observed and candidate.get("hash_match") is None
    else:
        expected = declared
        hash_metadata_valid = valid_declared and observed == declared and candidate.get("hash_match") is True
    if not hash_metadata_valid:
        raise ValueError(f"Hash canónico inválido para {row.get('internal_id')}")
    relative = str(candidate.get("path") or "")
    relative_path = Path(*relative.replace("\\", "/").split("/"))
    if not relative or relative_path.is_absolute():
        raise ValueError(f"Ruta canónica inválida para {row.get('internal_id')}: {relative!r}")
    workspace = Path(workspace).resolve()
    source = (workspace / relative_path).resolve()
    try:
        source.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"Ruta canónica fuera del workspace: {relative!r}") from exc
    if not source.is_file():
        raise ValueError(f"Activo canónico ausente: {relative}")
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"Extensión canónica no permitida: {suffix}")
    if _sha256_file(source) != expected:
        raise ValueError(f"El activo canónico cambió: {relative}")
    image_kind = "official" if row.get("classification") == "valid_exact_reviewed" else "generated_reference"
    return {
        "bucket": "catalog-assets",
        "path": f"{expected}{suffix}",
        "image_kind": image_kind,
        "review_only": True,
    }, source


def build_canonical_review_overlay(
    active: dict,
    canonical: dict,
    *,
    workspace: Path,
    bundle: str,
    suppliers: tuple[str, ...],
) -> tuple[dict, dict[str, Path], dict]:
    """Proyecta únicamente campos visuales y devuelve el plan de assets verificados."""

    if not isinstance(active, dict) or not isinstance(canonical, dict):
        raise ValueError("El dev-store y el reporte canónico deben ser objetos")
    clean_bundle = str(bundle or "").strip()
    normalized_suppliers = tuple(str(supplier or "").strip().lower() for supplier in suppliers)
    if not clean_bundle or not normalized_suppliers or len(set(normalized_suppliers)) != len(normalized_suppliers):
        raise ValueError("Bundle y proveedores válidos son obligatorios")
    rows = canonical.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("rows canónicas inválidas")

    updated = copy.deepcopy(active)
    asset_plan: dict[str, Path] = {}
    classification_counts: Counter[str] = Counter()
    supplier_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    coverage_status = str(canonical.get("status") or "").strip()

    for supplier in normalized_suppliers:
        current_items = _catalog_items(updated, supplier)
        current_by_id = _items_by_id(current_items, supplier)
        supplier_rows = [row for row in rows if str(row.get("supplier") or "").strip().lower() == supplier]
        mapped_rows: dict[str, dict] = {}
        for row in supplier_rows:
            classification = str(row.get("classification") or "")
            if classification not in ALLOWED_CLASSIFICATIONS:
                raise ValueError(f"Clasificación canónica inválida: {classification!r}")
            for flag in ("has_valid_image", "selected", "approved", "promoted"):
                if not isinstance(row.get(flag), bool):
                    raise ValueError(f"Flag canónico inválido {flag}: {row.get('internal_id')}")
            mapped_id = _mapped_internal_id(row, set(current_by_id))
            if mapped_id in mapped_rows:
                raise ValueError(f"Dos filas canónicas apuntan a {mapped_id}")
            mapped_rows[mapped_id] = row
        if set(mapped_rows) != set(current_by_id):
            missing = sorted(set(current_by_id) - set(mapped_rows))[:5]
            unexpected = sorted(set(mapped_rows) - set(current_by_id))[:5]
            raise ValueError(f"Cobertura canónica incompleta en {supplier}; faltan={missing}, sobran={unexpected}")

        for internal_id, item in current_by_id.items():
            row = mapped_rows[internal_id]
            classification = str(row["classification"])
            asset, source = _asset_for_row(row, Path(workspace))
            if asset is None:
                image_kind = "placeholder"
            else:
                image_kind = str(asset["image_kind"])
                object_name = str(asset["path"])
                prior_source = asset_plan.setdefault(object_name, source.resolve())
                if _sha256_file(prior_source) != Path(object_name).stem:
                    raise ValueError(f"Colisión de activo canónico: {object_name}")

            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                raise ValueError(f"attributes activos inválidos para {internal_id}")
            attributes[OVERLAY_KEY] = {
                "bundle": clean_bundle,
                "coverage_status": coverage_status,
                "classification": classification,
                "has_valid_image": row["has_valid_image"],
                "source_internal_id": str(row.get("internal_id") or ""),
                "selected": row["selected"],
                "approved": row["approved"],
                "promoted": row["promoted"],
                "asset": asset,
            }
            item["image_url"] = ""
            item["image_kind"] = image_kind
            classification_counts[classification] += 1
            supplier_counts[supplier] += 1
            kind_counts[image_kind] += 1

    asset_bytes = sum(path.stat().st_size for path in asset_plan.values())
    summary = {
        "rows": sum(supplier_counts.values()),
        "with_asset": kind_counts["official"] + kind_counts["generated_reference"],
        "without_asset": kind_counts["placeholder"],
        "official": kind_counts["official"],
        "generated_reference": kind_counts["generated_reference"],
        "placeholder": kind_counts["placeholder"],
        "unique_assets": len(asset_plan),
        "asset_bytes": asset_bytes,
        "by_classification": dict(sorted(classification_counts.items())),
        "by_supplier": dict(sorted(supplier_counts.items())),
    }
    return updated, asset_plan, summary


def build_approved_catalog_overlay(
    active: dict,
    *,
    suppliers: tuple[str, ...],
    approved_by: str,
    approval_note: str,
    approved_at: str,
) -> tuple[dict, dict]:
    """Materializa un overlay revisado como activos aprobados para publicación."""

    normalized_suppliers = tuple(str(value or "").strip().lower() for value in suppliers)
    clean_approved_by = str(approved_by or "").strip()
    clean_note = str(approval_note or "").strip()
    clean_approved_at = str(approved_at or "").strip()
    if (
        not isinstance(active, dict)
        or not normalized_suppliers
        or len(set(normalized_suppliers)) != len(normalized_suppliers)
        or not clean_approved_by
        or not clean_note
        or not clean_approved_at
    ):
        raise ValueError("Proveedores y evidencia de aprobación son obligatorios")

    approved = copy.deepcopy(active)
    supplier_counts: Counter[str] = Counter()
    kind_counts: Counter[str] = Counter()
    for supplier in normalized_suppliers:
        for item in _catalog_items(approved, supplier):
            internal_id = str(item.get("internal_id") or "")
            attributes = item.get("attributes")
            if not isinstance(attributes, dict):
                raise ValueError(f"attributes activos inválidos para {internal_id}")
            overlay = attributes.get(OVERLAY_KEY)
            if not isinstance(overlay, dict):
                raise ValueError(f"Overlay canónico ausente para {internal_id}")
            if any(overlay.get(flag) is not False for flag in ("selected", "approved", "promoted")):
                raise ValueError(f"Overlay canónico ya transitado para {internal_id}")
            asset = overlay.get("asset")
            if not isinstance(asset, dict) or asset.get("review_only") is not True:
                raise ValueError(f"Activo revisado ausente para {internal_id}")
            object_name = str(asset.get("path") or "").lower()
            suffix = Path(object_name).suffix.lower()
            digest = Path(object_name).stem
            if (
                asset.get("bucket") != "catalog-assets"
                or asset.get("image_kind") not in {"official", "generated_reference"}
                or suffix not in ALLOWED_SUFFIXES
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"Activo revisado inválido para {internal_id}")

            image_kind = str(asset["image_kind"])
            attributes["approved_asset"] = {
                "bucket": "catalog-assets",
                "path": object_name,
                "image_kind": image_kind,
                "label": (
                    "Imagen oficial verificada"
                    if image_kind == "official"
                    else "Imagen de referencia aprobada"
                ),
                "approved": True,
                "approved_by": clean_approved_by,
                "approved_at": clean_approved_at,
                "approval_note": clean_note,
                "source_bundle": str(overlay.get("bundle") or "").strip(),
                "source_classification": str(overlay.get("classification") or "").strip(),
            }
            attributes.pop(OVERLAY_KEY)
            item["image_url"] = ""
            item["image_kind"] = image_kind
            supplier_counts[supplier] += 1
            kind_counts[image_kind] += 1

    summary = {
        "items": sum(supplier_counts.values()),
        "official": kind_counts["official"],
        "generated_reference": kind_counts["generated_reference"],
        "by_supplier": dict(sorted(supplier_counts.items())),
    }
    return approved, summary


def apply_canonical_review_overlay(
    *,
    active_db: Path,
    canonical_report: Path,
    workspace: Path,
    target_assets: Path,
    backup: Path,
    staged: Path,
    bundle: str,
    suppliers: tuple[str, ...],
    expected_active_sha256: str,
    expected_report_sha256: str,
    receipt: Path | None = None,
) -> dict:
    active_db = Path(active_db)
    canonical_report = Path(canonical_report)
    target_assets = Path(target_assets)
    backup = Path(backup)
    staged = Path(staged)
    receipt = Path(receipt) if receipt is not None else None
    for output in (backup, staged, receipt):
        if output is not None and output.exists():
            raise ValueError(f"El destino ya existe: {output}")

    active_bytes = active_db.read_bytes()
    report_bytes = canonical_report.read_bytes()
    before_sha = _sha256_bytes(active_bytes)
    report_sha = _sha256_bytes(report_bytes)
    if before_sha != str(expected_active_sha256 or "").strip().lower():
        raise ValueError(f"El dev-store cambió: esperado {expected_active_sha256}, actual {before_sha}")
    if report_sha != str(expected_report_sha256 or "").strip().lower():
        raise ValueError(f"El reporte R10/v8 cambió: esperado {expected_report_sha256}, actual {report_sha}")

    active = _json_object(active_bytes, "dev-store")
    canonical = _json_object(report_bytes, "reporte canónico")
    updated, assets, summary = build_canonical_review_overlay(
        active,
        canonical,
        workspace=Path(workspace),
        bundle=bundle,
        suppliers=suppliers,
    )
    staged_bytes = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8")
    after_sha = _sha256_bytes(staged_bytes)

    backup.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("xb") as destination:
        destination.write(active_bytes)
    if _sha256_file(backup) != before_sha:
        raise RuntimeError("El respaldo no coincide byte a byte")
    with staged.open("xb") as destination:
        destination.write(staged_bytes)
    if _sha256_file(staged) != after_sha:
        raise RuntimeError("El staging no coincide byte a byte")

    target_assets.mkdir(parents=True, exist_ok=True)
    copied = already_present = 0
    for object_name, source in sorted(assets.items()):
        if _copy_asset_atomically(source, target_assets / object_name, object_name):
            copied += 1
        else:
            already_present += 1
    if _sha256_file(active_db) != before_sha:
        raise RuntimeError("El dev-store cambió antes de la publicación atómica")

    transaction = {"state": "staged", "failed_publish_path": None}
    rollback = {"status": "not_required", "restore_attempted": False}
    staging = {"path": str(staged), "state": "ready", "sha256": after_sha}
    _publish_active_transactionally(
        active_db_path=active_db,
        staged_path=staged,
        backup_path=backup,
        before_sha256=before_sha,
        after_sha256=after_sha,
        transaction=transaction,
        rollback=rollback,
        staging=staging,
    )
    result = {
        "status": "passed",
        "bundle": bundle,
        "coverage_status": canonical.get("status"),
        "active_db": str(active_db),
        "canonical_report": str(canonical_report),
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "canonical_report_sha256": report_sha,
        "backup": str(backup),
        "backup_sha256": _sha256_file(backup),
        "summary": summary,
        "assets": {"copied": copied, "already_present": already_present},
        "transaction": transaction,
        "rollback": rollback,
    }
    if receipt is not None:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        with receipt.open("xb") as destination:
            destination.write(receipt_bytes)
        result["receipt"] = str(receipt)
        result["receipt_sha256"] = _sha256_file(receipt)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-db", required=True, type=Path)
    parser.add_argument("--canonical-report", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--target-assets", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--staged", required=True, type=Path)
    parser.add_argument("--bundle", default="R10/v8")
    parser.add_argument("--supplier", action="append", dest="suppliers", required=True)
    parser.add_argument("--expected-active-sha256", required=True)
    parser.add_argument("--expected-report-sha256", required=True)
    parser.add_argument("--receipt", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = apply_canonical_review_overlay(
        active_db=args.active_db,
        canonical_report=args.canonical_report,
        workspace=args.workspace,
        target_assets=args.target_assets,
        backup=args.backup,
        staged=args.staged,
        bundle=args.bundle,
        suppliers=tuple(args.suppliers),
        expected_active_sha256=args.expected_active_sha256,
        expected_report_sha256=args.expected_report_sha256,
        receipt=args.receipt,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
