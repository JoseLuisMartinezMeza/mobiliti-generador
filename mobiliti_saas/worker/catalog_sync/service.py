import argparse
import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import warnings
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from PIL import Image

from mobiliti_saas.quote_engine.supplier_catalog import (
    PUBLIC_ITEM_FIELDS,
    load_supplier_catalog_data,
)

from . import SupplierSourceConfig, load_source_config
from .graph import DeltaExpiredError, DeltaResult, DownloadedFile, GraphCatalogClient, GraphItem
from .importers import (
    AlmaSnapshotBuild,
    SunonSnapshotBuild,
    build_alma_snapshot,
    build_alma_snapshot_with_assets,
    build_cr_global_snapshot_with_assets,
    build_lumbro_snapshot_with_assets,
    build_jome_snapshot_with_assets,
    build_lauco_snapshot_with_assets,
    build_idelika_snapshot_with_assets,
    build_conceptos_snapshot_with_assets,
    build_sonara_snapshot_with_assets,
    build_sunon_snapshot_with_assets,
)
from .importers.common import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_PIXELS,
    CatalogSnapshotBuildLike,
)
from .repository import (
    CatalogRepository,
    SnapshotRecord,
    SourceFileRecord,
    SourceRecord,
)


_OPERATIONAL_FIELDS = {"stock", "lead_time"}
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_RESULT_STATUSES = {
    "lost_claim", "no_changes", "awaiting_approval", "published", "dry_run", "failed",
}
_REPOSITORY_METHODS = (
    "get_source", "start_run", "get_published_snapshot", "find_file",
    "list_latest_files", "store_raw_if_absent", "materialize_raw_if_present",
    "record_source_file", "mark_file_deleted",
    "stage_candidate", "finish_no_changes", "finish_failed", "auto_publish_candidate",
)
_GRAPH_METHODS = ("iter_delta", "download_content")
_SUPPLIERS = (
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
    "idelika", "conceptos",
)
ADAPTERS = {
    "cr_global": build_cr_global_snapshot_with_assets,
    "sonara": build_sonara_snapshot_with_assets,
    "sunon": build_sunon_snapshot_with_assets,
    "alma": build_alma_snapshot_with_assets,
    "lumbro": build_lumbro_snapshot_with_assets,
    "jome": build_jome_snapshot_with_assets,
    "lauco": build_lauco_snapshot_with_assets,
    "idelika": build_idelika_snapshot_with_assets,
    "conceptos": build_conceptos_snapshot_with_assets,
}
CATALOG_EXIT_WORKED = 0
CATALOG_EXIT_FAILED = 1
CATALOG_EXIT_NO_WORK = 2
CATALOG_EXIT_DISABLED = 3
_WORKED_STATUSES = {"no_changes", "awaiting_approval", "published"}


@dataclass(frozen=True)
class CatalogDiff:
    added_count: int
    pending_removal_count: int
    changed_count: int
    operational_count: int
    material_count: int
    changed_fields: tuple[str, ...]
    auto_publishable: bool


@dataclass(frozen=True)
class SyncResult:
    status: str
    run_id: UUID | None
    candidate_id: UUID | None
    diff: CatalogDiff | None
    metrics: tuple[tuple[str, int], ...]
    error_code: str | None
    auto_publish_attempted: bool


@dataclass(frozen=True)
class _AdapterFile:
    path: str
    kind: str
    brand: str | None
    sha256: str
    mime_type: str
    local_path: Path | None


@dataclass(frozen=True)
class _WorkingFile:
    drive_item_id: str
    path: str
    e_tag: str
    size_bytes: int
    sha256: str
    mime_type: str
    private_object_path: str
    source_file: SourceFileRecord | None


def classify_snapshot_diff(previous: dict | None, candidate: dict) -> CatalogDiff:
    current = _validate_snapshot(candidate)
    prior = None if previous is None else _validate_snapshot(previous)
    if prior is not None and prior["supplier"] != current["supplier"]:
        raise ValueError("Invalid snapshot")
    before = {} if prior is None else {row["internal_id"]: row for row in prior["items"]}
    after = {row["internal_id"]: row for row in current["items"]}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed_fields = set()
    changed_count = operational_count = material_count = 0
    for internal_id in sorted(set(before) & set(after)):
        fields = {
            field for field in PUBLIC_ITEM_FIELDS
            if before[internal_id][field] != after[internal_id][field]
        }
        if not fields:
            continue
        changed_count += 1
        changed_fields.update(fields)
        if fields <= _OPERATIONAL_FIELDS:
            operational_count += 1
        else:
            material_count += 1
    if added:
        changed_fields.add("added")
    if removed:
        changed_fields.add("pending_removal")
    material_count += len(added) + len(removed)
    fields = tuple(sorted(changed_fields))
    auto = (
        changed_count > 0 and not added and not removed and material_count == 0
        and set(fields) <= _OPERATIONAL_FIELDS
    )
    return CatalogDiff(
        len(added), len(removed), changed_count, operational_count,
        material_count, fields, auto,
    )


def run_supplier_sync(
    supplier: str,
    trigger: str,
    requested_by: int | None,
    dry_run: bool,
    *,
    repository=None,
    graph_client=None,
    adapters=None,
    source_config_path=None,
    claimed_run_id=None,
) -> SyncResult:
    with tempfile.TemporaryDirectory(prefix="mobiliti-catalog-") as temp_dir:
        return _run_supplier_sync(
            supplier,
            trigger,
            requested_by,
            dry_run,
            repository=repository,
            graph_client=graph_client,
            adapters=adapters,
            source_config_path=source_config_path,
            claimed_run_id=claimed_run_id,
            temp_dir=Path(temp_dir),
        )


def _run_supplier_sync(
    supplier: str,
    trigger: str,
    requested_by: int | None,
    dry_run: bool,
    *,
    repository,
    graph_client,
    adapters,
    source_config_path,
    claimed_run_id,
    temp_dir: Path,
) -> SyncResult:
    config, adapter, repository, graph_client = _dependencies(
        supplier, trigger, requested_by, dry_run, repository, graph_client,
        adapters, source_config_path,
    )
    try:
        source = repository.get_source(supplier)
    except Exception:
        raise ValueError("Invalid sync request") from None
    if (
        not isinstance(source, SourceRecord) or source.supplier != config.supplier
        or source.adapter != config.adapter or not source.enabled
    ):
        raise ValueError("Invalid sync request")
    if claimed_run_id is not None and (dry_run or not isinstance(claimed_run_id, UUID)):
        raise ValueError("Invalid sync request")
    if claimed_run_id is not None:
        run_id = claimed_run_id
    elif not dry_run:
        try:
            run_id = repository.start_run(source.id, trigger, requested_by)
            if run_id is None:
                return _result("lost_claim", None)
            if not isinstance(run_id, UUID):
                raise ValueError
        except Exception:
            raise ValueError("Invalid sync request") from None
    else:
        run_id = None

    counters = {
        "active_files": 0,
        "delta_items": 0,
        "downloaded_files": 0,
        "ignored_items": 0,
        "metadata_records": 0,
        "raw_reused": 0,
        "raw_stored": 0,
        "tombstones": 0,
    }
    error_code = "repository_failed"
    try:
        allowed_paths = tuple(row.path for row in config.files)
        active_rows = repository.list_latest_files(source.id, allowed_paths)
        working = _working_state(active_rows, source.id, set(allowed_paths))

        error_code = "graph_failed"
        delta_link = source.delta_link
        if (
            isinstance(delta_link, str)
            and re.fullmatch(
                rf"manual://validated-local-snapshot/{re.escape(source.supplier)}/[0-9a-f]{{64}}",
                delta_link,
            )
        ):
            delta_link = None
        full_crawl = delta_link is None
        try:
            delta = graph_client.iter_delta(
                source.graph_drive_id, source.graph_root_item_id, delta_link
            )
        except DeltaExpiredError:
            full_crawl = True
            delta = graph_client.iter_delta(
                source.graph_drive_id, source.graph_root_item_id, None
            )
        error_code = "graph_invalid"
        changes = _delta_changes(delta, config, working, source.graph_drive_id)
        counters["delta_items"] = len(delta.items)
        if full_crawl:
            live_items = {
                path: graph_row.id
                for path, graph_row in changes
                if graph_row.deleted is None
            }
            for previous_file in tuple(working.values()):
                if live_items.get(previous_file.path) == previous_file.drive_item_id:
                    continue
                if not dry_run:
                    repository.mark_file_deleted(
                        source.id, previous_file.drive_item_id, run_id
                    )
                working.pop(previous_file.drive_item_id, None)
                counters["tombstones"] += 1
        local_paths = {}
        for path, graph_row in changes:
            if graph_row.deleted is not None:
                previous_file = working.get(graph_row.id)
                if previous_file is None:
                    continue
                if previous_file.path != path:
                    raise ValueError
                if not dry_run:
                    repository.mark_file_deleted(source.id, graph_row.id, run_id)
                working.pop(graph_row.id, None)
                counters["tombstones"] += 1
                continue

            normalized = GraphItem(
                graph_row.id, PurePosixPath(path).name, path, graph_row.size,
                graph_row.e_tag, graph_row.c_tag, graph_row.mime_type, False, None,
            )
            existing = repository.find_file(source.id, graph_row.id, graph_row.e_tag)
            if existing is not None and not existing.is_deleted:
                if not isinstance(existing, SourceFileRecord) or existing.path != path:
                    raise ValueError
                working[graph_row.id] = _working_file(existing)
                continue

            error_code = "file_failed"
            destination = temp_dir / (
                f"mobiliti-catalog-{secrets.token_hex(16)}{PurePosixPath(path).suffix.lower()}"
            )
            downloaded = graph_client.download_content(
                source.graph_drive_id, normalized, destination, _MAX_DOWNLOAD_BYTES
            )
            if (
                not isinstance(downloaded, DownloadedFile)
                or downloaded.path != destination or downloaded.size != graph_row.size
                or not isinstance(downloaded.sha256, str) or len(downloaded.sha256) != 64
                or any(character not in "0123456789abcdef" for character in downloaded.sha256)
            ):
                raise ValueError
            counters["downloaded_files"] += 1
            local_paths[graph_row.id] = downloaded.path
            same_content = next(
                (
                    row for row in working.values()
                    if row.sha256 == downloaded.sha256 and row.mime_type == normalized.mime_type
                ),
                None,
            )
            if same_content is not None:
                object_path = same_content.private_object_path
                counters["raw_reused"] += 1
            elif dry_run:
                extension = PurePosixPath(path).suffix.lower().removeprefix(".")
                object_path = f"catalog-sources/{downloaded.sha256}.{extension}"
            else:
                object_path = repository.store_raw_if_absent(
                    downloaded.path, downloaded.sha256, PurePosixPath(path).suffix,
                    normalized.mime_type,
                )
                counters["raw_stored"] += 1
            if dry_run:
                working[graph_row.id] = _WorkingFile(
                    graph_row.id, path, graph_row.e_tag, downloaded.size,
                    downloaded.sha256, normalized.mime_type, object_path, None,
                )
            else:
                recorded = repository.record_source_file(
                    source.id, normalized, downloaded, object_path, run_id,
                    {"status": "pending", "summary": {}},
                )
                if not isinstance(recorded, SourceFileRecord):
                    raise ValueError
                working[graph_row.id] = _working_file(recorded)
                counters["metadata_records"] += 1
            error_code = "graph_invalid"

        error_code = "repository_failed"
        if not dry_run:
            active_rows = repository.list_latest_files(source.id, allowed_paths)
            working = _working_state(active_rows, source.id, set(allowed_paths))
        for row in working.values():
            if row.drive_item_id in local_paths:
                continue
            if row.source_file is None:
                raise ValueError
            error_code = "file_failed"
            destination = temp_dir / (
                f"mobiliti-catalog-{secrets.token_hex(16)}{PurePosixPath(row.path).suffix.lower()}"
            )
            downloaded = repository.materialize_raw_if_present(row.source_file, destination)
            if (
                not isinstance(downloaded, DownloadedFile) or downloaded.path != destination
                or downloaded.size != row.size_bytes or downloaded.sha256 != row.sha256
            ):
                raise ValueError
            local_paths[row.drive_item_id] = downloaded.path
            counters["downloaded_files"] += 1
        error_code = "repository_failed"
        adapter_files = _adapter_files(config, working, local_paths)
        counters["active_files"] = len(adapter_files)

        error_code = "snapshot_invalid"
        adapter_result = adapter(adapter_files)
        asset_build = adapter_result if isinstance(adapter_result, CatalogSnapshotBuildLike) else None
        raw_candidate = asset_build.snapshot if asset_build is not None else adapter_result
        candidate = _validate_snapshot(raw_candidate, expected_supplier=supplier)
        if asset_build is not None:
            counters.update(_asset_metrics(candidate, asset_build))
            if not dry_run:
                error_code = "repository_failed"
                store_asset = getattr(repository, "store_catalog_asset_if_absent", None)
                if not callable(store_asset):
                    raise ValueError
                for sha256, asset in sorted(asset_build.assets_by_sha256.items()):
                    object_name = f"{sha256}.png"
                    if store_asset(object_name, asset.data, asset.media_type) != object_name:
                        raise ValueError
        error_code = "repository_failed"
        previous = repository.get_published_snapshot(source)
        if previous is not None and not isinstance(previous, SnapshotRecord):
            raise ValueError
        error_code = "snapshot_invalid"
        previous_payload = None if previous is None else _validate_snapshot(previous.payload)
        candidate = _preserve_curated_visuals(previous_payload, candidate)
        candidate = _validate_snapshot(candidate, expected_supplier=supplier)
        diff = classify_snapshot_diff(previous_payload, candidate)
        counters.update(_catalog_metrics(candidate, diff))
        public_metrics = _metrics(counters)

        if dry_run:
            return _result("dry_run", None, diff=diff, metrics=public_metrics)
        if previous_payload is not None and _identity(previous_payload) == _identity(candidate):
            error_code = "repository_failed"
            repository.finish_no_changes(run_id, dict(public_metrics), delta.delta_link)
            return _result("no_changes", run_id, diff=diff, metrics=public_metrics)

        error_code = "repository_failed"
        staged = dict(candidate)
        staged["generated_at"] = _datetime(candidate["generated_at"])
        try:
            candidate_id = repository.stage_candidate(
                run_id, staged, dict(public_metrics), delta.delta_link
            )
        except Exception:
            candidate_id = repository.stage_candidate(
                run_id, staged, dict(public_metrics), delta.delta_link
            )
        if not isinstance(candidate_id, UUID):
            raise ValueError
        if diff.auto_publishable:
            try:
                published_id = repository.auto_publish_candidate(candidate_id)
                if published_id != candidate_id:
                    raise ValueError
                return _result(
                    "published", run_id, candidate_id, diff, public_metrics,
                    auto_publish_attempted=True,
                )
            except Exception:
                return _result(
                    "awaiting_approval", run_id, candidate_id, diff, public_metrics,
                    auto_publish_attempted=True,
                )
        return _result("awaiting_approval", run_id, candidate_id, diff, public_metrics)
    except Exception:
        public_metrics = _metrics(counters)
        if not dry_run:
            try:
                repository.finish_failed(run_id, error_code, dict(public_metrics))
            except Exception:
                pass
        return _result("failed", run_id, metrics=public_metrics, error_code=error_code)


def _dependencies(
    supplier, trigger, requested_by, dry_run, repository, graph_client,
    adapters, source_config_path,
):
    if (
        not isinstance(supplier, str) or not supplier or trigger not in {"manual", "scheduled"}
        or (requested_by is not None and (type(requested_by) is not int or requested_by < 1))
        or type(dry_run) is not bool or not isinstance(adapters, dict)
    ):
        raise ValueError("Invalid sync request")
    try:
        path = Path(source_config_path) if source_config_path is not None else Path(__file__).with_name("sources.json")
        configs = load_source_config(path)
        matches = [row for row in configs if row.supplier == supplier]
        if len(matches) != 1:
            raise ValueError
        config = matches[0]
        adapter = adapters.get(config.adapter)
        if not callable(adapter):
            raise ValueError
        repository = repository if repository is not None else CatalogRepository.from_environment()
        graph_client = graph_client if graph_client is not None else GraphCatalogClient.from_environment()
        if any(not callable(getattr(repository, name, None)) for name in _REPOSITORY_METHODS):
            raise ValueError
        if any(not callable(getattr(graph_client, name, None)) for name in _GRAPH_METHODS):
            raise ValueError
        return config, adapter, repository, graph_client
    except Exception:
        raise ValueError("Invalid sync request") from None


def _validate_snapshot(raw, expected_supplier=None):
    required_fields = {"supplier", "source_hash", "generated_at", "items"}
    if (
        not isinstance(raw, dict)
        or not required_fields <= set(raw)
        or set(raw) - required_fields not in (set(), {"metadata"})
    ):
        raise ValueError("Invalid snapshot")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Invalid snapshot")
    seen = set()
    for row in raw_items:
        internal_id = row.get("internal_id") if isinstance(row, dict) else None
        if not isinstance(internal_id, str) or internal_id in seen:
            raise ValueError("Invalid snapshot")
        seen.add(internal_id)
    try:
        loaded = load_supplier_catalog_data(raw, expected_supplier=expected_supplier)
    except Exception:
        raise ValueError("Invalid snapshot") from None
    snapshot = {
        "supplier": loaded["supplier"],
        "source_hash": loaded["source_hash"].lower(),
        "generated_at": raw["generated_at"],
        "items": loaded["items"],
    }
    if "metadata" in loaded:
        snapshot["metadata"] = loaded["metadata"]
    return snapshot


def _preserve_curated_visuals(previous, candidate):
    supplier = candidate.get("supplier")
    if (
        previous is None
        or supplier not in {"sunon", "labenze", "requiez"}
        or previous.get("supplier") != supplier
    ):
        return candidate
    previous_by_id = {row["internal_id"]: row for row in previous["items"]}
    shared_visual_ids = _shared_visual_preservation_ids(previous, candidate)
    for row in candidate["items"]:
        prior = previous_by_id.get(row["internal_id"])
        if (
            row["internal_id"] not in shared_visual_ids
            or not _can_preserve_curated_visual(prior, row)
        ):
            continue
        asset = prior["attributes"]["approved_asset"]
        image_kind = prior["image_kind"]
        attributes = {
            key: value for key, value in row["attributes"].items()
            if key not in {
                "approved_asset", "image_match", "image_reference",
                "source_image_url", "web_image_quality",
            }
        }
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": asset["path"],
            "image_kind": image_kind,
            "label": (
                "Imagen oficial verificada"
                if image_kind == "official"
                else "Imagen de referencia"
            ),
            "approved": True,
        }
        for key in ("image_reference", "source_image_url", "web_image_quality"):
            if key in prior["attributes"]:
                attributes[key] = json.loads(json.dumps(prior["attributes"][key]))
        if _has_approved_exact_visual(prior):
            attributes["image_match"] = json.loads(
                json.dumps(prior["attributes"]["image_match"])
            )
        row["image_url"] = ""
        row["image_kind"] = image_kind
        row["product_url"] = prior["product_url"]
        row["attributes"] = attributes
    return candidate


def _shared_visual_preservation_ids(previous, candidate):
    items = previous.get("items")
    candidate_items = candidate.get("items")
    if not isinstance(items, list) or not isinstance(candidate_items, list):
        return set()
    if previous.get("supplier") == "sunon":
        return {
            row.get("internal_id") for row in items
            if isinstance(row, dict) and isinstance(row.get("internal_id"), str)
        }
    candidates = {
        row.get("internal_id"): row for row in candidate_items
        if isinstance(row, dict) and isinstance(row.get("internal_id"), str)
    }
    by_asset = {}
    allowed = set()
    for row in items:
        if not isinstance(row, dict) or not isinstance(row.get("internal_id"), str):
            continue
        if _has_approved_exact_visual(row):
            allowed.add(row["internal_id"])
            continue
        attributes = row.get("attributes")
        asset = attributes.get("approved_asset") if isinstance(attributes, dict) else None
        path = asset.get("path") if isinstance(asset, dict) else None
        if not isinstance(path, str):
            allowed.add(row["internal_id"])
            continue
        by_asset.setdefault(path, []).append(row)
    for rows in by_asset.values():
        if len(rows) == 1:
            allowed.add(rows[0]["internal_id"])
        elif _valid_shared_visual_group(rows, candidates):
            allowed.update(row["internal_id"] for row in rows)
    return allowed


def _valid_shared_visual_group(rows, candidates):
    internal_ids = {row["internal_id"] for row in rows}
    evidence_urls = set()
    for row in rows:
        attributes = row.get("attributes")
        reference = attributes.get("image_reference") if isinstance(attributes, dict) else None
        if not isinstance(reference, dict):
            return False
        evidence = reference.get("shared_visual_evidence")
        assigned = evidence.get("assigned_variant_ids") if isinstance(evidence, dict) else None
        source_url = evidence.get("source_url") if isinstance(evidence, dict) else None
        if (
            not isinstance(assigned, list)
            or len(assigned) != len(internal_ids)
            or any(not _nonempty_text(internal_id) for internal_id in assigned)
            or set(assigned) != internal_ids
            or not _is_secure_visual_url(source_url)
        ):
            return False
        candidate = candidates.get(row["internal_id"])
        if (
            not isinstance(candidate, dict)
            or row.get("supplier") != candidate.get("supplier")
            or row.get("product_key") != candidate.get("product_key")
            or row.get("sku") != candidate.get("sku")
            or not _same_visual_configuration(row, candidate)
        ):
            return False
        evidence_urls.add(source_url.strip())
    return len(evidence_urls) == 1


def _can_preserve_curated_visual(previous, candidate):
    if (
        not isinstance(previous, dict)
        or candidate.get("supplier") not in {"sunon", "labenze", "requiez"}
        or previous.get("supplier") != candidate.get("supplier")
        or previous.get("internal_id") != candidate.get("internal_id")
        or previous.get("product_key") != candidate.get("product_key")
        or previous.get("sku") != candidate.get("sku")
        or not _same_visual_configuration(previous, candidate)
        or candidate.get("image_url") != ""
        or previous.get("image_kind") not in {"official", "generated_reference"}
    ):
        return False
    if candidate["supplier"] == "sunon":
        if candidate.get("image_kind") != "placeholder":
            return False
    elif _has_approved_exact_visual(candidate):
        return False
    attributes = previous.get("attributes")
    asset = attributes.get("approved_asset") if isinstance(attributes, dict) else None
    safe_asset = (
        isinstance(asset, dict)
        and asset.get("bucket") == "catalog-assets"
        and isinstance(asset.get("path"), str)
        and re.fullmatch(r"[0-9a-f]{64}\.png", asset["path"]) is not None
        and asset.get("approved") is True
        and asset.get("image_kind") in {None, previous["image_kind"]}
    )
    if not safe_asset or candidate["supplier"] == "sunon":
        return safe_asset
    if _has_approved_exact_visual(previous):
        return True
    return _valid_curated_v2_reference(previous, asset)


def _has_approved_exact_visual(candidate):
    if candidate.get("image_kind") != "official":
        return False
    attributes = candidate.get("attributes")
    if not isinstance(attributes, dict):
        return False
    image_match = attributes.get("image_match")
    asset = attributes.get("approved_asset")
    if not isinstance(image_match, dict) or not isinstance(asset, dict):
        return False
    sha256 = image_match.get("asset_sha256")
    source_references = image_match.get("source_references")
    return (
        candidate.get("image_url") == ""
        and image_match.get("status") in {"exact_pdf", "exact_xlsx", "exact_web"}
        and isinstance(sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", sha256) is not None
        and isinstance(source_references, list)
        and bool(source_references)
        and all(_valid_exact_source_reference(reference) for reference in source_references)
        and asset.get("bucket") == "catalog-assets"
        and asset.get("path") == f"{sha256}.png"
        and asset.get("image_kind") == "official"
        and isinstance(asset.get("label"), str)
        and bool(asset["label"].strip())
        and asset.get("approved") is True
    )


def _valid_exact_source_reference(reference):
    if (
        not isinstance(reference, dict)
        or not isinstance(reference.get("file_id"), str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", reference["file_id"]) is None
    ):
        return False
    location = reference.get("sheet_or_page")
    if isinstance(location, str):
        valid_location = (
            1 <= len(location) <= 128
            and all(ord(character) >= 32 and ord(character) != 127 for character in location)
        )
    else:
        valid_location = type(location) is int and 1 <= location <= 2_000
    if not valid_location:
        return False
    position = reference.get("cell_or_bbox")
    if isinstance(position, str):
        return bool(re.fullmatch(
            r"[A-Z]{1,3}[1-9][0-9]{0,6}(?::[A-Z]{1,3}[1-9][0-9]{0,6})?\Z",
            position,
        ))
    if not isinstance(position, (tuple, list)) or len(position) != 4:
        return False
    if not all(_strict_number(value) and abs(value) <= 1_000_000 for value in position):
        return False
    return position[0] <= position[2] and position[1] <= position[3]


def _valid_curated_v2_reference(previous, asset):
    attributes = previous.get("attributes")
    reference = attributes.get("image_reference") if isinstance(attributes, dict) else None
    image_kind = previous.get("image_kind")
    if (
        not isinstance(reference, dict)
        or image_kind not in {"official", "generated_reference"}
        or previous.get("image_url") != ""
        or asset.get("image_kind") != image_kind
        or not isinstance(asset.get("label"), str)
        or not asset["label"].strip()
        or reference.get("asset_sha256") != asset["path"][:-4]
        or any(
            reference.get(field) is not True
            for field in (
                "approved", "configuration_supported", "full_product_visible",
                "not_cropped", "direct_product_reference",
            )
        )
        or not _nonempty_text(reference.get("source_locator"))
        or not _nonempty_text(reference.get("reviewer"))
        or reference.get("decision") not in {"retain", "replace"}
        or not _nonempty_text(reference.get("reason"))
        or (
            "status" in reference
            and (
                not isinstance(reference["status"], str)
                or "placeholder" in reference["status"].casefold()
            )
        )
        or not _valid_reviewed_at(reference.get("reviewed_at"))
        or reference.get("source_kind") not in {
            "catalog_pdf", "manufacturer_official", "authorized_distributor",
            "third_party_exact",
        }
        or not _valid_source_dimensions(reference.get("source_dimensions"))
        or not _is_secure_visual_url(reference.get("image_source_url"))
        or not _valid_product_visual_url(
            previous.get("product_url"),
            reference.get("image_source_url"),
            reference.get("source_kind"),
        )
        or not _valid_asset_quality(
            reference.get("asset_quality"),
            reference.get("source_dimensions"),
            asset["path"][:-4],
        )
        or reference.get("quality_exception") is not None
    ):
        return False
    if reference["source_kind"] == "catalog_pdf" and not re.search(
        r"\.pdf(?:\?[^#]*)?#(?:[^#]*&)?page=\d+(?:&|$)",
        reference["image_source_url"],
        re.IGNORECASE,
    ):
        return False
    source_image_url = attributes.get("source_image_url")
    if source_image_url is not None and not _is_secure_visual_url(source_image_url):
        return False
    for quality in (
        attributes.get("web_image_quality"),
        reference.get("web_image_quality"),
    ):
        if not _valid_optional_web_quality(quality, asset["path"][:-4]):
            return False
    return _valid_generated_provenance(reference, image_kind)


def _valid_generated_provenance(reference, image_kind):
    generated = reference.get("generated")
    if image_kind == "official":
        return generated is False
    if generated is not True:
        return False
    search = reference.get("exact_search")
    generation = reference.get("generation")
    if (
        not isinstance(search, dict)
        or search.get("exhausted") is not True
        or not isinstance(search.get("queries"), list)
        or not search["queries"]
        or any(not _nonempty_text(query) for query in search["queries"])
        or not isinstance(generation, dict)
        or not _nonempty_text(generation.get("prompt"))
        or not _nonempty_text(generation.get("model"))
        or not isinstance(generation.get("references"), list)
        or not generation["references"]
    ):
        return False
    for generated_reference in generation["references"]:
        if (
            not isinstance(generated_reference, dict)
            or not _is_secure_visual_url(generated_reference.get("url"))
            or not isinstance(generated_reference.get("sha256"), str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", generated_reference["sha256"]) is None
        ):
            return False
    return True


def _valid_asset_quality(quality, source_dimensions, expected_sha256):
    if not isinstance(quality, dict) or quality.get("sha256") != expected_sha256:
        return False
    canvas = quality.get("canvas")
    bbox = quality.get("bbox")
    if not isinstance(canvas, dict) or not isinstance(bbox, dict):
        return False
    width, height = canvas.get("width"), canvas.get("height")
    left, top = bbox.get("left"), bbox.get("top")
    bbox_width, bbox_height = bbox.get("width"), bbox.get("height")
    if (
        type(width) is not int
        or type(height) is not int
        or width != height
        or not 1024 <= width <= 8192
        or width * height > 25_000_000
        or any(type(value) is not int for value in (left, top, bbox_width, bbox_height))
        or left < 0
        or top < 0
        or bbox_width <= 0
        or bbox_height <= 0
        or left + bbox_width > width
        or top + bbox_height > height
        or bbox_width / width > 0.92
        or bbox_height / height > 0.92
    ):
        return False
    margin = quality.get("margin")
    occupancy = quality.get("occupancy")
    aspect_ratio = quality.get("aspect_ratio")
    if not all(_strict_number(value) for value in (margin, occupancy, aspect_ratio)):
        return False
    calculated_margin = min(
        left / width,
        top / height,
        (width - left - bbox_width) / width,
        (height - top - bbox_height) / height,
    )
    calculated_aspect = bbox_width / bbox_height
    source_aspect = source_dimensions["width"] / source_dimensions["height"]
    return (
        margin >= 0.04
        and abs(margin - calculated_margin) <= 1e-9
        and 0.12 <= occupancy <= 0.80
        and aspect_ratio > 0
        and abs(aspect_ratio - calculated_aspect) <= 1e-9
        and abs(calculated_aspect / source_aspect - 1) <= 0.01
    )


def _valid_source_dimensions(dimensions):
    return (
        isinstance(dimensions, dict)
        and _strict_number(dimensions.get("width"))
        and _strict_number(dimensions.get("height"))
        and dimensions["width"] > 0
        and dimensions["height"] > 0
    )


def _valid_optional_web_quality(quality, expected_sha256):
    if quality is None:
        return True
    if not isinstance(quality, dict) or quality.get("sha256") != expected_sha256:
        return False
    for field in ("width", "height"):
        if field in quality and (type(quality[field]) is not int or quality[field] <= 0):
            return False
    return True


def _valid_reviewed_at(value):
    if not _nonempty_text(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_product_visual_url(value, image_source_url, source_kind):
    if not _is_secure_visual_url(value):
        return False
    try:
        if _canonical_visual_url(value) == _canonical_visual_url(image_source_url):
            return False
    except ValueError:
        return False
    parsed = urlsplit(value.strip())
    path = parsed.path.casefold()
    host = parsed.netloc.casefold()
    query_keys = {
        key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    }
    if (
        host.startswith("cdn.")
        or ".cdn." in host
        or path in {"", "/"}
        or path.endswith("/index.html")
        or any(
            segment in path
            for segment in (
                "/buscar", "/search", "/familia", "/family",
                "/categoria", "/category", "/collection",
            )
        )
        or bool(query_keys & {
            "q", "query", "search", "s", "buscar",
            "keyword", "keywords", "term", "terms",
        })
        or path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"))
        or path.endswith(".pdf")
    ):
        return False
    return True


def _canonical_visual_url(value):
    parsed = urlsplit(value.strip())
    port = parsed.port
    if (parsed.scheme.casefold(), port) in {("https", 443), ("http", 80)}:
        port = None
    return (
        parsed.scheme.casefold(),
        (parsed.hostname or "").casefold(),
        port,
        parsed.path.rstrip("/") or "/",
    )


def _strict_number(value):
    return type(value) in {int, float} and value == value and -float("inf") < value < float("inf")


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _same_visual_configuration(previous, candidate):
    if any(
        previous.get(field) != candidate.get(field)
        for field in ("name", "description")
    ):
        return False
    previous_attributes = previous.get("attributes")
    candidate_attributes = candidate.get("attributes")
    if not isinstance(previous_attributes, dict) or not isinstance(candidate_attributes, dict):
        return False
    if any(
        previous_attributes.get(field) != candidate_attributes.get(field)
        for field in ("variant", "dimensions")
    ):
        return False
    return all(
        _option_structure(previous.get(field)) == _option_structure(candidate.get(field))
        for field in ("base_price_options", "add_on_options")
    )


def _option_structure(options):
    if not isinstance(options, list):
        return None
    structural = []
    for option in options:
        if not isinstance(option, dict):
            return None
        value = {
            key: nested for key, nested in option.items()
            if key not in {"price_net", "available"}
        }
        structural.append(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return tuple(sorted(structural))


def _is_secure_visual_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    return parsed.scheme.casefold() == "https" and bool(host) and _valid_visual_host(
        f"{host}:{port}" if port is not None else host
    )


def _valid_visual_host(host_with_port):
    host, separator, port = host_with_port.rpartition(":")
    if not separator:
        host, port = host_with_port, ""
    elif not port.isdigit() or not 1 <= int(port) <= 65535:
        return False
    labels = host.split(".")
    return all(
        label
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _identity(snapshot):
    loaded = _validate_snapshot(snapshot)
    value = {
        "supplier": loaded["supplier"],
        "source_hash": loaded["source_hash"],
        "items": sorted(loaded["items"], key=lambda row: row["internal_id"]),
    }
    if "metadata" in loaded:
        value["metadata"] = loaded["metadata"]
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _working_state(rows, source_id, allowed_paths):
    if not isinstance(rows, tuple) or len(rows) > 10_000:
        raise ValueError
    state = {}
    for row in rows:
        if (
            not isinstance(row, SourceFileRecord) or row.source_id != source_id
            or row.path not in allowed_paths or row.drive_item_id in state or row.is_deleted
        ):
            raise ValueError
        state[row.drive_item_id] = _working_file(row)
    return state


def _working_file(row):
    return _WorkingFile(
        row.drive_item_id, row.path, row.e_tag, row.size_bytes, row.sha256,
        row.mime_type, row.private_object_path, row,
    )


def _delta_changes(delta, config, working, drive_id):
    if not isinstance(delta, DeltaResult) or not isinstance(delta.items, tuple) or not delta.delta_link:
        raise ValueError
    allowed = {row.path for row in config.files}
    seen_ids = set()
    seen_paths = set()
    changes = []
    for row in delta.items:
        if not isinstance(row, GraphItem) or row.id in seen_ids:
            raise ValueError
        seen_ids.add(row.id)
        if row.deleted is not None and row.id in working:
            path = working[row.id].path
        else:
            path = _configured_path(row, config, drive_id)
            if path is None and row.deleted is not None and row.path is None and row.id in working:
                path = working[row.id].path
        if path is None:
            continue
        if path in seen_paths or row.is_folder:
            raise ValueError
        seen_paths.add(path)
        if row.deleted is None and (
            not row.e_tag or row.size is None or not row.mime_type
        ):
            raise ValueError
        changes.append((path, row))
    return tuple(sorted(changes, key=lambda pair: (pair[0], pair[1].id)))


def _configured_path(row, config, drive_id):
    if row.path is None or not isinstance(row.path, str) or not isinstance(row.name, str):
        return None
    if "\\" in row.path or any(ord(character) < 32 for character in row.path + row.name):
        raise ValueError
    prefix = f"/drives/{drive_id}/root:/"
    if not row.path.startswith(prefix) or row.path.count("/root:/") != 1:
        return None
    parent = row.path[len(prefix):].rstrip("/")
    matches = []
    for configured in config.files:
        configured_path = PurePosixPath(configured.path)
        expected_parent = str(PurePosixPath(config.root_path) / configured_path.parent)
        if (
            row.name == configured_path.name
            and parent == expected_parent
            and (configured.drive_item_id is None or configured.drive_item_id == row.id)
        ):
            matches.append(configured.path)
    if len(matches) > 1:
        raise ValueError
    return matches[0] if matches else None


def _adapter_files(config: SupplierSourceConfig, working, local_paths):
    by_path = {row.path: row for row in working.values()}
    result = []
    for configured in config.files:
        row = by_path.get(configured.path)
        if row is None:
            continue
        result.append(_AdapterFile(
            configured.path, configured.kind, configured.brand, row.sha256,
            row.mime_type,
            local_paths[row.drive_item_id],
        ))
    return tuple(result)


def _catalog_metrics(candidate, diff):
    rows = candidate["items"]
    return {
        "added_items": diff.added_count,
        "availability_made_to_order": sum(row["availability_type"] == "made_to_order" for row in rows),
        "availability_stocked": sum(row["availability_type"] == "stocked" for row in rows),
        "availability_unknown": sum(row["availability_type"] == "unknown" for row in rows),
        "catalog_items": len(rows),
        "changed_items": diff.changed_count,
        "code_needs_review": sum(row["code_status"] == "needs_review" for row in rows),
        "code_verified": sum(row["code_status"] == "verified" for row in rows),
        "described_items": sum(bool(row["description"]) for row in rows),
        "generated_images": sum(row["image_kind"] == "generated_reference" for row in rows),
        "official_images": sum(row["image_kind"] == "official" and bool(row["image_url"]) for row in rows),
        "pending_removals": diff.pending_removal_count,
        "priced_items": sum(
            row["price_net"] is not None and Decimal(row["price_net"]) > 0
            for row in rows
        ),
    }


def _verified_png_dimensions(data: bytes) -> tuple[int, int]:
    if type(data) is not bytes or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                width, height = probe.size
                if (
                    probe.format != "PNG"
                    or width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ValueError
                probe.verify()
    except Exception:
        raise ValueError from None
    return width, height


def _asset_metrics(candidate, build: CatalogSnapshotBuildLike):
    items_by_id = {row["internal_id"]: row for row in candidate["items"]}
    kun_ids = {
        row["internal_id"] for row in candidate["items"] if row["brand"] == "KUN"
    }
    official_asset_ids = {
        row["internal_id"]
        for row in candidate["items"]
        if row["image_kind"] == "official" and row["image_url"] == ""
    }
    binding_ids = [binding.internal_id for binding in build.bindings]
    binding_id_set = set(binding_ids)
    if len(binding_ids) != len(binding_id_set) or binding_id_set != official_asset_ids:
        raise ValueError
    referenced_assets = {binding.asset_sha256 for binding in build.bindings}
    if set(build.assets_by_sha256) != referenced_assets:
        raise ValueError
    if isinstance(build, AlmaSnapshotBuild) and not kun_ids <= binding_id_set:
        raise ValueError
    statuses = {
        "exact_xlsx": 0,
        "merged_xlsx": 0,
        "family_xlsx": 0,
        "exact_pdf": 0,
        "exact_web": 0,
        "model_web": 0,
    }
    for binding in build.bindings:
        asset = build.assets_by_sha256.get(binding.asset_sha256)
        actual_dimensions = _verified_png_dimensions(asset.data) if asset is not None else None
        if (
            asset is None
            or asset.sha256 != binding.asset_sha256
            or hashlib.sha256(asset.data).hexdigest() != binding.asset_sha256
            or asset.media_type != "image/png"
            or type(asset.width) is not int
            or type(asset.height) is not int
            or asset.width <= 0
            or asset.height <= 0
            or actual_dimensions != (asset.width, asset.height)
            or binding.object_name != f"{binding.asset_sha256}.png"
            or binding.image_kind != "official"
            or binding.match_status not in statuses
        ):
            raise ValueError
        item = items_by_id[binding.internal_id]
        attributes = item["attributes"]
        approved = attributes.get("approved_asset")
        image_match = attributes.get("image_match")
        if (
            item["image_url"] != ""
            or item["image_kind"] != "official"
            or not isinstance(approved, dict)
            or approved.get("bucket") != "catalog-assets"
            or approved.get("path") != binding.object_name
            or approved.get("image_kind") != "official"
            or approved.get("approved") is not True
            or not isinstance(image_match, dict)
            or image_match.get("status") != binding.match_status
            or image_match.get("asset_sha256") != binding.asset_sha256
            or tuple(image_match.get("source_references") or ()) != binding.source_references
        ):
            raise ValueError
        statuses[binding.match_status] += 1
    return {
        "official_images_planned": len(build.bindings),
        "unique_assets_planned": len(build.assets_by_sha256),
        "image_exact_xlsx": statuses["exact_xlsx"],
        "image_merged_xlsx": statuses["merged_xlsx"],
        "image_family_xlsx": statuses["family_xlsx"],
        "image_exact_pdf": statuses["exact_pdf"],
        "image_exact_web": statuses["exact_web"],
        "image_model_web": statuses["model_web"],
    }


def _alma_asset_metrics(candidate, build: CatalogSnapshotBuildLike):
    """Compatibilidad para verificaciones de ALMA previas al sidecar genérico."""
    return _asset_metrics(candidate, build)


def _metrics(values):
    clean = []
    for key, value in sorted(values.items()):
        if not isinstance(key, str) or len(key) > 64 or type(value) is not int or not 0 <= value <= 10_000:
            raise ValueError
        clean.append((key, value))
    if len(clean) > 32:
        raise ValueError
    return tuple(clean)


def _datetime(value):
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


def _result(
    status, run_id, candidate_id=None, diff=None, metrics=(),
    error_code=None, auto_publish_attempted=False,
):
    if status not in _RESULT_STATUSES or len(metrics) > 32:
        raise ValueError
    return SyncResult(
        status, run_id, candidate_id, diff, tuple(metrics), error_code,
        auto_publish_attempted,
    )


def _enabled_suppliers():
    enabled = os.environ.get("CATALOG_SYNC_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes"}:
        return ()
    raw = os.environ.get("CATALOG_ENABLED_SUPPLIERS", "")
    suppliers = tuple(part.strip() for part in raw.split(",") if part.strip())
    if (
        not suppliers or len(set(suppliers)) != len(suppliers)
        or any(supplier not in _SUPPLIERS for supplier in suppliers)
    ):
        return ()
    return suppliers


def run_due_once():
    suppliers = _enabled_suppliers()
    if not suppliers:
        return "disabled"
    repository = CatalogRepository.from_environment()
    repository.recover_stale_syncs(suppliers)
    claim = repository.claim_next_sync(suppliers)
    if claim is None:
        return "no_work"
    try:
        graph_client = GraphCatalogClient.from_environment()
        result = run_supplier_sync(
            claim.supplier,
            claim.trigger_type,
            claim.requested_by,
            False,
            repository=repository,
            graph_client=graph_client,
            adapters=ADAPTERS,
            claimed_run_id=claim.run_id,
        )
    except Exception:
        try:
            repository.finish_failed(claim.run_id, "dependency_failed", {})
        except Exception:
            pass
        return "failed"
    return result.status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synchronize one supplier catalog")
    parser.add_argument("--due", action="store_true")
    args = parser.parse_args(argv)
    if not args.due:
        parser.error("--due is required")
    try:
        status = run_due_once()
    except Exception:
        return CATALOG_EXIT_FAILED
    if status in _WORKED_STATUSES:
        return CATALOG_EXIT_WORKED
    if status == "no_work":
        return CATALOG_EXIT_NO_WORK
    if status == "disabled":
        return CATALOG_EXIT_DISABLED
    return CATALOG_EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
