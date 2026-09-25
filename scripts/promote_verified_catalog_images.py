"""Promueve imágenes verificadas a un dev-store sin reemplazar sus datos operativos."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from scripts.build_verified_catalog_images import (
    VALID_DECISIONS,
    _validate_v2_asset,
    _validate_v2_reference,
)


ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
IMAGE_ATTRIBUTE_KEYS = ("image_reference", "source_image_url", "web_image_quality")
VALID_IMAGE_KINDS = {"official", "generated_reference"}
SUPPORTED_SUPPLIERS = ("alma", "cr-global", "labenze", "lumbro", "requiez", "sonara", "sunon")
V2_REQUIRED_SUPPLIERS = {"labenze", "requiez"}
MAX_BATCH_ASSET_BYTES = 256 * 1024 * 1024


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


def _assert_active_sha(path: Path, expected_sha256: str) -> None:
    actual_sha256 = _sha256(Path(path).read_bytes())
    if actual_sha256 != expected_sha256:
        raise ValueError(
            "El dev-store cambió concurrentemente antes de publicar: "
            f"esperado {expected_sha256}, actual {actual_sha256}"
        )


def _existing_parent(path: Path) -> Path:
    parent = Path(path).parent
    while not parent.exists():
        if parent == parent.parent:
            raise ValueError(f"No existe directorio para staging: {path}")
        parent = parent.parent
    return parent


def _require_staging_same_volume(active_db_path: Path, staged_path: Path) -> None:
    if os.stat(active_db_path).st_dev != os.stat(_existing_parent(staged_path)).st_dev:
        raise ValueError("El staging debe estar en el mismo volumen que el dev-store activo")


def _validate_target_asset(path: Path, object_name: str) -> None:
    if not path.is_file() or _sha256(path.read_bytes()) != Path(object_name).stem:
        raise ValueError(f"Asset destino incompatible: {path}")


def _copy_asset_atomically(source: Path, target: Path, object_name: str) -> bool:
    """Publica un asset sólo después de verificar una copia temporal del mismo volumen."""

    if target.exists():
        _validate_target_asset(target, object_name)
        return False
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, temporary)
    _validate_target_asset(temporary, object_name)
    if target.exists():
        _validate_target_asset(target, object_name)
        return False
    os.replace(temporary, target)
    _validate_target_asset(target, object_name)
    return True


def _validate_persisted_shared_v2_assets(asset_entries: dict[str, list[tuple[str, dict]]]) -> None:
    """Valida sólo la evidencia que el builder v2 persiste en el catálogo."""

    for object_name, entries in asset_entries.items():
        if len(entries) < 2:
            continue
        internal_ids = {internal_id for internal_id, _ in entries}
        source_urls: set[str] = set()
        for internal_id, item in entries:
            evidence = item["image_reference"].get("shared_visual_evidence")
            if not isinstance(evidence, dict):
                raise ValueError(f"Asset compartido sin shared_visual_evidence: {object_name}")
            source_url = str(evidence.get("source_url") or "").strip()
            parsed = urlsplit(source_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"Evidencia shared_visual sin fuente HTTPS: {internal_id}")
            if set(evidence.get("assigned_variant_ids") or []) != internal_ids:
                raise ValueError(f"Evidencia shared_visual incompleta para {internal_id}")
            source_urls.add(source_url)
        if len(source_urls) != 1:
            raise ValueError(f"Evidencia shared_visual no coincide para {object_name}")


def _validate_report_path(report_path: Path | None) -> Path | None:
    if report_path is None:
        return None
    path = Path(report_path)
    if not path.parent.is_dir():
        raise ValueError(f"El directorio del reporte no existe: {path.parent}")
    return path


def _sha256_if_file(path: Path) -> str | None:
    return _sha256(path.read_bytes()) if path.is_file() else None


def _reserve_report_path(report_path: Path | None) -> dict | None:
    if report_path is None:
        return None
    token = uuid.uuid4().hex
    reservation = {"status": "reserved", "reservation_token": token}
    try:
        with report_path.open("x", encoding="utf-8") as reserved:
            json.dump(reservation, reserved, ensure_ascii=False)
            reserved.flush()
            os.fsync(reserved.fileno())
    except FileExistsError as exc:
        raise ValueError(f"El reporte ya existe: {report_path}") from exc
    return {"path": report_path, "token": token}


def _write_report(reservation: dict | None, report: dict) -> None:
    if reservation is None:
        return
    report_path = reservation["path"]
    token = reservation["token"]
    try:
        current = _read_json(report_path)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"La reserva del reporte no es legible: {report_path}") from exc
    if current.get("reservation_token") != token:
        raise RuntimeError(f"La reserva del reporte pertenece a otro proceso: {report_path}")
    payload = {**report, "reservation_token": token}
    temporary = report_path.with_name(f".{report_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if _read_json(report_path).get("reservation_token") != token:
        raise RuntimeError(f"La reserva del reporte cambió antes de escribir: {report_path}")
    os.replace(temporary, report_path)


def _write_report_receipt_fallback(reservation: dict | None, report: dict) -> str | None:
    if reservation is None:
        return None
    receipt_path = reservation["path"].with_name(
        f".{reservation['path'].name}.{reservation['token']}.receipt"
    )
    with receipt_path.open("x", encoding="utf-8") as receipt:
        json.dump({**report, "reservation_token": reservation["token"]}, receipt, ensure_ascii=False, indent=2)
        receipt.flush()
        os.fsync(receipt.fileno())
    return str(receipt_path)


def _acquire_promotion_lock(active_db_path: Path) -> dict:
    lock_path = active_db_path.with_name(f".{active_db_path.name}.promotion.lock")
    token = uuid.uuid4().hex
    try:
        with lock_path.open("x", encoding="utf-8") as lock:
            json.dump({"owner": token, "active_db": str(active_db_path)}, lock, ensure_ascii=False)
            lock.flush()
            os.fsync(lock.fileno())
    except FileExistsError as exc:
        raise ValueError(f"Ya existe una promoción activa para {active_db_path}") from exc
    return {"path": lock_path, "token": token, "state": "held"}


def _release_promotion_lock(lock: dict | None) -> None:
    if lock is None or not lock["path"].exists():
        return
    try:
        owner = _read_json(lock["path"]).get("owner")
    except (OSError, ValueError):
        return
    if owner != lock["token"]:
        return
    released_path = lock["path"].with_name(f".{lock['path'].name}.{uuid.uuid4().hex}.released")
    try:
        os.replace(lock["path"], released_path)
    except OSError as exc:
        lock.update(
            state="release_failed",
            error=f"{type(exc).__name__}: {exc}",
            blocking_path=str(lock["path"]),
        )
        return
    lock.update(
        state="released",
        released_path=str(released_path),
        receipt={"owner": lock["token"], "released_path": str(released_path)},
    )


def _sync_lock_audit(audit: dict, lock: dict | None) -> None:
    if lock is None:
        return
    audit["lock"].update(
        {
            key: value
            for key, value in lock.items()
            if key in {"state", "released_path", "receipt", "error", "blocking_path"}
        }
    )


def _restore_from_backup(
    active_db_path: Path,
    backup_path: Path,
    expected_sha256: str,
    transaction: dict,
    rollback: dict,
) -> bool:
    rollback["restore_attempted"] = True
    observed_backup_sha256 = _sha256_if_file(backup_path)
    rollback.update(
        backup_expected_sha256=expected_sha256,
        backup_observed_sha256=observed_backup_sha256,
        backup_verified=observed_backup_sha256 == expected_sha256,
    )
    if observed_backup_sha256 != expected_sha256:
        rollback.update(restored=False, restore_performed=False, verified=False, status="backup_invalid")
        return False
    transaction_path = active_db_path.with_name(f".{active_db_path.name}.{uuid.uuid4().hex}.rollback")
    transaction.update(path=str(transaction_path), state="preparing_restore", sha256=expected_sha256)
    shutil.copy2(backup_path, transaction_path)
    if _sha256_if_file(transaction_path) != expected_sha256:
        rollback.update(restored=False, restore_performed=False, verified=False, status="rollback_copy_invalid")
        return False
    try:
        os.replace(transaction_path, active_db_path)
    except OSError as exc:
        rollback.update(
            restored=False,
            restore_performed=False,
            verified=False,
            status="restore_replace_failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        transaction["state"] = "restore_replace_failed"
        return False
    restored_sha256 = _sha256(active_db_path.read_bytes())
    rollback.update(
        restored=True,
        restore_performed=True,
        verified=restored_sha256 == expected_sha256,
        restored_sha256=restored_sha256,
        status="restored" if restored_sha256 == expected_sha256 else "restored_different_bytes",
    )
    transaction["state"] = "restored_to_active" if rollback["verified"] else "restore_failed"
    return rollback["verified"]


def _publish_active_transactionally(
    *,
    active_db_path: Path,
    staged_path: Path,
    backup_path: Path,
    before_sha256: str,
    after_sha256: str,
    transaction: dict,
    rollback: dict,
    staging: dict,
) -> None:
    try:
        os.replace(staged_path, active_db_path)
        staging["state"] = "published"
    except Exception:
        raise

    if _sha256(active_db_path.read_bytes()) == after_sha256:
        transaction["state"] = "retained_verified_rollback"
        return

    failed_bytes = active_db_path.read_bytes()
    failed_sha256 = _sha256(failed_bytes)
    failed_publish_path = active_db_path.with_name(f".{active_db_path.name}.{uuid.uuid4().hex}.failed-publish")
    failed_temporary = failed_publish_path.with_name(f".{failed_publish_path.name}.{uuid.uuid4().hex}.tmp")
    failed_temporary.write_bytes(failed_bytes)
    if _sha256_if_file(failed_temporary) != failed_sha256:
        raise RuntimeError("El artefacto failed-publish no coincide byte a byte")
    os.replace(failed_temporary, failed_publish_path)
    if _sha256_if_file(failed_publish_path) != failed_sha256:
        raise RuntimeError("El artefacto failed-publish publicado no coincide")
    transaction.update(
        failed_publish_path=str(failed_publish_path),
        failed_publish_sha256=failed_sha256,
        state="failed_publish_preserved",
    )
    _restore_from_backup(active_db_path, backup_path, before_sha256, transaction, rollback)
    raise RuntimeError("El dev-store activo no coincide con el staging después de publicar")


def _validated_v2_asset(source_assets_dir: Path, item: dict, supplier: str) -> tuple[str, Path]:
    """Reutiliza el contrato v2 del builder para una imagen ya verificada."""

    internal_id = str(item.get("internal_id") or f"{supplier}:<sin-id>")
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        raise ValueError(f"attributes v2 ausentes para {internal_id}")
    reference = attributes.get("image_reference")
    approved_asset = attributes.get("approved_asset")
    if not isinstance(reference, dict) or not isinstance(approved_asset, dict):
        raise ValueError(f"Revisión global v2 incompleta para {internal_id}")

    image_kind = str(item.get("image_kind") or "")
    object_name = str(approved_asset.get("path") or "").lower()
    if (
        approved_asset.get("bucket") != "catalog-assets"
        or approved_asset.get("approved") is not True
        or approved_asset.get("image_kind") != image_kind
    ):
        raise ValueError(f"approved_asset v2 inválido para {internal_id}")
    image_url_name = Path(urlsplit(str(item.get("image_url") or "")).path).name.lower()
    if image_url_name != object_name:
        raise ValueError(f"image_url v2 no coincide con approved_asset para {internal_id}")
    if reference.get("asset_sha256") != Path(object_name).stem:
        raise ValueError(f"asset_sha256 v2 inválido para {internal_id}")
    if reference.get("direct_product_reference") is not True:
        raise ValueError(f"Revisión global v2 incompleta para {internal_id}")
    if str(reference.get("decision") or "") not in VALID_DECISIONS:
        raise ValueError(f"Decisión v2 inválida para {internal_id}")
    if not str(reference.get("reason") or "").strip():
        raise ValueError(f"Razón v2 ausente para {internal_id}")

    entry = {
        "image_reference": copy.deepcopy(reference),
        "image_kind": image_kind,
        "product_url": item.get("product_url"),
        "reason": reference.get("reason"),
        "decision": reference.get("decision"),
        "status": reference.get("status"),
    }
    validated_reference, _ = _validate_v2_reference(entry, internal_id, image_kind)
    source, calculated_quality = _validate_v2_asset(
        source_assets_dir, object_name, validated_reference, internal_id
    )
    if reference.get("asset_quality") != calculated_quality:
        raise ValueError(f"asset_quality v2 inválido para {internal_id}")
    return object_name, source


def _operational_counts(data: dict) -> dict[str, int]:
    return {
        key: len(value)
        for key, value in data.items()
        if key != "catalog_published_snapshots" and isinstance(value, (list, dict))
    }


def _promote_verified_catalog_images(
    *,
    active_db_path: Path,
    verified_db_path: Path,
    source_assets_dir: Path,
    target_assets_dir: Path,
    suppliers: tuple[str, ...],
    backup_path: Path,
    staged_path: Path,
    expected_active_sha256: str | None = None,
    audit: dict,
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
    audit["before_sha256"] = active_sha
    normalized_suppliers = tuple(str(raw_supplier or "").strip().lower() for raw_supplier in suppliers)
    if not normalized_suppliers or any(not supplier for supplier in normalized_suppliers):
        raise ValueError("Se requiere al menos un proveedor válido")
    if len(set(normalized_suppliers)) != len(normalized_suppliers):
        raise ValueError("Un proveedor no puede promoverse más de una vez")
    requires_v2 = any(supplier in V2_REQUIRED_SUPPLIERS for supplier in normalized_suppliers)
    if requires_v2 and not str(expected_active_sha256 or "").strip():
        raise ValueError("expected_active_sha256 es obligatorio para Labenze/Requiez")
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
    shared_v2_assets: dict[str, list[tuple[str, dict]]] = {}

    for supplier in normalized_suppliers:
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
            if supplier in V2_REQUIRED_SUPPLIERS:
                object_name, source = _validated_v2_asset(source_assets_dir, verified_item, supplier)
                reference = verified_item["attributes"]["image_reference"]
                shared_v2_assets.setdefault(object_name, []).append(
                    (
                        internal_id,
                        {
                            "image_reference": copy.deepcopy(reference),
                            "shared_visual_group": reference.get("shared_visual_group"),
                        },
                    )
                )
            else:
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
            current_attributes["approved_asset"] = {
                "bucket": "catalog-assets",
                "path": object_name,
                "image_kind": image_kind,
                "label": "Imagen oficial verificada" if image_kind == "official" else "Imagen de referencia",
                "approved": True,
            }
            kinds[image_kind] += 1

        supplier_report = {
            "items": len(current_items),
            "official": kinds["official"],
            "generated_reference": kinds["generated_reference"],
            "unique_assets": len(supplier_assets),
        }
        if supplier in V2_REQUIRED_SUPPLIERS:
            snapshot_id = promoted["catalog_published_snapshots"][supplier].get("id")
            supplier_report.update(
                snapshot_id_before=snapshot_id,
                snapshot_id_after=snapshot_id,
            )
        report_suppliers[supplier] = supplier_report

    _validate_persisted_shared_v2_assets(shared_v2_assets)
    required_bytes = sum(source.stat().st_size for source in required_assets.values())
    if required_bytes > MAX_BATCH_ASSET_BYTES:
        raise ValueError("El lote de assets únicos supera 256 MiB")
    for object_name in required_assets:
        target = target_assets_dir / object_name
        if not target.exists():
            continue
        _validate_target_asset(target, object_name)

    promoted_bytes = json.dumps(promoted, ensure_ascii=False, indent=2).encode("utf-8")
    audit["after_sha256"] = _sha256(promoted_bytes)
    audit["suppliers"] = report_suppliers
    audit["operational_counts"] = {
        "before": _operational_counts(active),
        "after": _operational_counts(promoted),
    }
    _require_staging_same_volume(active_db_path, staged_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(active_bytes)
    if backup_path.read_bytes() != active_bytes:
        raise RuntimeError("El respaldo del dev-store no coincide byte a byte")
    audit["rollback"].update(
        backup_verified=True,
        backup_expected_sha256=active_sha,
        backup_observed_sha256=active_sha,
    )
    staged_path.write_bytes(promoted_bytes)
    staging_sha = _sha256(promoted_bytes)
    if _sha256(staged_path.read_bytes()) != staging_sha:
        raise RuntimeError("El staging del dev-store no coincide")
    audit["staging"].update(state="ready", sha256=staging_sha)

    target_assets_dir.mkdir(parents=True, exist_ok=True)
    copied = existing = 0
    for object_name, source in sorted(required_assets.items()):
        target = target_assets_dir / object_name
        if _copy_asset_atomically(source, target, object_name):
            copied += 1
        else:
            existing += 1

    _assert_active_sha(active_db_path, active_sha)
    _publish_active_transactionally(
        active_db_path=active_db_path,
        staged_path=staged_path,
        backup_path=backup_path,
        before_sha256=active_sha,
        after_sha256=staging_sha,
        transaction=audit["transaction"],
        rollback=audit["rollback"],
        staging=audit["staging"],
    )
    audit["rollback"].update(
        status="not_required",
        restore_attempted=False,
        restore_performed=False,
        restored=False,
        verified=False,
        backup_verified=True,
    )

    return {
        "status": "passed",
        "active_db": str(active_db_path),
        "verified_db": str(verified_db_path),
        "backup": str(backup_path),
        "staged": str(staged_path),
        "before_sha256": active_sha,
        "after_sha256": staging_sha,
        "backup_sha256": _sha256(backup_path.read_bytes()),
        "staging_sha256": staging_sha,
        "suppliers": report_suppliers,
        "operational_counts": audit["operational_counts"],
        "staging": {**audit["staging"], "exists": staged_path.exists()},
        "transaction": audit["transaction"],
        "rollback": audit["rollback"],
        "assets": {
            "required": len(required_assets),
            "unique_objects": len(required_assets),
            "unique_bytes": required_bytes,
            "copied": copied,
            "already_present": existing,
        },
    }


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
    report_path: Path | None = None,
) -> dict:
    """Promueve imágenes con respaldo transaccional y reporte auditable opcional."""

    active_db_path = Path(active_db_path)
    verified_db_path = Path(verified_db_path)
    source_assets_dir = Path(source_assets_dir)
    target_assets_dir = Path(target_assets_dir)
    backup_path = Path(backup_path)
    staged_path = Path(staged_path)
    report_path = _validate_report_path(report_path)
    report_reservation = _reserve_report_path(report_path)
    audit = {
        "before_sha256": None,
        "after_sha256": None,
        "suppliers": {},
        "operational_counts": {},
        "staging": {"path": str(staged_path), "state": "not_created", "sha256": None},
        "transaction": {"path": None, "state": "not_created", "sha256": None, "failed_publish_path": None},
        "lock": {"path": None, "state": "not_acquired"},
        "rollback": {
            "status": "not_required",
            "restore_attempted": False,
            "restore_performed": False,
            "restored": False,
            "verified": False,
            "backup_verified": False,
            "procedure": (
                "Rollback manual seguro: copiar el backup a un temporal adyacente al activo, "
                "verificar su SHA-256, copiar el activo actual a un artefacto .failed-publish y verificarlo, "
                "después usar os.replace del temporal al activo; no consumir ni mover el backup original "
                "ni asumir que backup y activo están en el mismo volumen."
            ),
        },
    }
    lock = None
    try:
        lock = _acquire_promotion_lock(active_db_path)
        audit["lock"] = {"path": str(lock["path"]), "state": "held"}
        report = _promote_verified_catalog_images(
            active_db_path=active_db_path,
            verified_db_path=verified_db_path,
            source_assets_dir=source_assets_dir,
            target_assets_dir=target_assets_dir,
            suppliers=suppliers,
            backup_path=backup_path,
            staged_path=staged_path,
            expected_active_sha256=expected_active_sha256,
            audit=audit,
        )
        audit["lock"]["state"] = "held_until_release_receipt"
        report["lock"] = audit["lock"]
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "active_db": str(active_db_path),
            "verified_db": str(verified_db_path),
            "before_sha256": audit["before_sha256"],
            "after_sha256": audit["after_sha256"],
            "backup": {
                "path": str(backup_path),
                "exists": backup_path.exists(),
                "sha256": _sha256_if_file(backup_path),
            },
            "staging": {**audit["staging"], "exists": staged_path.exists()},
            "transaction": audit["transaction"],
            "lock": audit["lock"],
            "rollback": audit["rollback"],
            "suppliers": audit["suppliers"],
            "operational_counts": audit["operational_counts"],
        }
        error = exc
    else:
        error = None
    try:
        _write_report(report_reservation, report)
    finally:
        _release_promotion_lock(lock)
    _sync_lock_audit(audit, lock)
    report["lock"] = audit["lock"]
    try:
        _write_report(report_reservation, report)
    except OSError as exc:
        if error is not None:
            raise
        report.update(
            report_receipt_write_failed=True,
            report_receipt_write_error=f"{type(exc).__name__}: {exc}",
        )
        try:
            report["report_receipt_fallback_path"] = _write_report_receipt_fallback(report_reservation, report)
        except OSError as receipt_exc:
            report["report_receipt_fallback_error"] = f"{type(receipt_exc).__name__}: {receipt_exc}"
    if error is not None:
        raise error
    return report


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
        choices=SUPPORTED_SUPPLIERS,
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
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
