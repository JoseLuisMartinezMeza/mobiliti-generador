"""Migrador verificable, create-only y reanudable de assets de catalogo a R2.

El manifiesto autoritativo siempre es externo a Git. El modo por defecto es un
dry-run local: no lee variables de entorno ni construye clientes de red.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / ".mobiliti_dev_store" / "catalog-assets"
LOGICAL_BUCKET = "catalog-assets"
CACHE_CONTROL = "public, max-age=31536000, immutable"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_NAME = re.compile(r"^([0-9a-f]{64})\.(png|jpg|jpeg|webp)$")
_MIME_BY_EXTENSION = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
_REPARSE_POINT = 0x400
_CHUNK_SIZE = 1024 * 1024


class MigrationError(RuntimeError):
    """Error cerrado cuyo mensaje es un codigo sanitizado apto para reporte."""


@dataclass(frozen=True)
class ManifestContract:
    entry_count: int
    total_bytes: int
    mime_counts: Mapping[str, int]
    keyset_digest: str | None = None
    manifest_digest: str | None = None


PRODUCTION_CONTRACT = ManifestContract(
    entry_count=2214,
    total_bytes=678_858_152,
    mime_counts={"image/png": 1568, "image/webp": 556, "image/jpeg": 90},
    keyset_digest="93e30738942bc0c4b85d85d63239c82588ec1d163c5c3820ef2de01dc07caeb7",
    manifest_digest="72ecc6b84bfec9ba012a24dea9c5bcdf6d1beaad8d81c68eb4697f8e83e188ff",
)


@dataclass(frozen=True)
class ManifestData:
    entries: list[dict[str, Any]]
    manifest_file_sha256: str
    keyset_digest: str
    manifest_digest: str


@dataclass(frozen=True)
class ExcludedAudit:
    count: int
    total_bytes: int
    digest: str


@dataclass(frozen=True)
class LocalAudit:
    count: int
    total_bytes: int
    mime_counts: Mapping[str, int]
    excluded_unmanifested: ExcludedAudit


@dataclass(frozen=True)
class PreparedMigration:
    entries: list[dict[str, Any]]
    source_dir: Path
    manifest_file_sha256: str
    keyset_digest: str
    manifest_digest: str
    audit: LocalAudit


@dataclass
class TransferStats:
    attempts: int = 0
    retries: int = 0
    head: int = 0
    put: int = 0
    existing: int = 0
    created: int = 0
    precondition_412: int = 0
    full_get: int = 0


_TRANSFER_FIELDS = (
    "attempts", "retries", "head", "put", "existing", "created",
    "precondition_412", "full_get",
)


def _stats_dict(stats: TransferStats) -> dict[str, int]:
    return {name: getattr(stats, name) for name in _TRANSFER_FIELDS}


def _stats_from(value: Any) -> TransferStats:
    if (
        not isinstance(value, dict)
        or set(value) != set(_TRANSFER_FIELDS)
        or any(not _is_plain_int(value[name]) or value[name] < 0 for name in _TRANSFER_FIELDS)
    ):
        raise MigrationError("checkpoint_binding_mismatch")
    return TransferStats(**{name: value[name] for name in _TRANSFER_FIELDS})


def _stats_sum(first: TransferStats, second: TransferStats) -> TransferStats:
    return TransferStats(**{
        name: getattr(first, name) + getattr(second, name) for name in _TRANSFER_FIELDS
    })


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.25
    max_delay: float = 4.0
    sleep: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = field(default_factory=lambda: __import__("random").random)

    def __post_init__(self):
        if self.max_attempts < 1 or self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("invalid retry policy")


@dataclass(frozen=True)
class ExecuteConfig:
    r2_endpoint_url: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_session_token: str
    r2_region: str
    r2_bucket: str
    supabase_url: str
    supabase_service_key: str


@dataclass(frozen=True)
class MigrationOutcome:
    certified: bool
    batch_id: str
    stats: TransferStats
    cumulative_stats: TransferStats
    rpc_count: int
    rpc_status: str
    database_checks: int


class _DuplicateJsonMember(ValueError):
    pass


def _strict_json_loads(raw: str) -> Any:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonMember(key)
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=reject_duplicates)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_plain_int(value: Any) -> bool:
    return type(value) is int


def _task3_digests(entries: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    ordered = sorted(entries, key=lambda item: item["object_name"])
    keyset_payload = "\n".join(item["object_name"] for item in ordered).encode("utf-8")
    manifest_payload = "\n".join(
        f'{item["object_name"]}|{item["sha256"]}|{item["byte_size"]}|{item["mime_type"]}'
        for item in ordered
    ).encode("utf-8")
    return hashlib.sha256(keyset_payload).hexdigest(), hashlib.sha256(manifest_payload).hexdigest()


def load_manifest(path: Path | str, expected_file_sha256: str, contract: ManifestContract) -> ManifestData:
    path = Path(path)
    if not _HEX64.fullmatch(expected_file_sha256 or ""):
        raise MigrationError("manifest_anchor_invalid")
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise MigrationError("manifest_unreadable") from None
    actual_anchor = hashlib.sha256(raw).hexdigest()
    if actual_anchor != expected_file_sha256:
        raise MigrationError("manifest_anchor_mismatch")
    try:
        document = _strict_json_loads(raw.decode("utf-8"))
    except _DuplicateJsonMember:
        raise MigrationError("manifest_json_duplicate") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MigrationError("manifest_json_invalid") from None
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise MigrationError("manifest_schema_invalid")
    if document.get("logical_bucket") != LOGICAL_BUCKET:
        raise MigrationError("manifest_bucket_invalid")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise MigrationError("manifest_entries_invalid")

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "object_name", "sha256", "byte_size", "mime_type"
        }:
            raise MigrationError("manifest_entry_invalid")
        object_name = raw_entry.get("object_name")
        sha256 = raw_entry.get("sha256")
        byte_size = raw_entry.get("byte_size")
        mime_type = raw_entry.get("mime_type")
        match = _OBJECT_NAME.fullmatch(object_name) if isinstance(object_name, str) else None
        if match is None or Path(object_name).name != object_name:
            raise MigrationError("manifest_name_invalid")
        if object_name in seen:
            raise MigrationError("manifest_duplicate_name")
        seen.add(object_name)
        if not isinstance(sha256, str) or not _HEX64.fullmatch(sha256) or sha256 != match.group(1):
            raise MigrationError("manifest_sha_name_mismatch")
        if not _is_plain_int(byte_size) or byte_size <= 0:
            raise MigrationError("manifest_size_invalid")
        if mime_type != _MIME_BY_EXTENSION[match.group(2)]:
            raise MigrationError("manifest_mime_name_mismatch")
        entries.append({
            "object_name": object_name,
            "sha256": sha256,
            "byte_size": byte_size,
            "mime_type": mime_type,
        })

    if document.get("entry_count") != contract.entry_count or len(entries) != contract.entry_count:
        raise MigrationError("manifest_count_mismatch")
    computed_bytes = sum(entry["byte_size"] for entry in entries)
    if document.get("total_bytes") != contract.total_bytes or computed_bytes != contract.total_bytes:
        raise MigrationError("manifest_bytes_mismatch")
    computed_mimes = {"image/png": 0, "image/webp": 0, "image/jpeg": 0}
    for entry in entries:
        computed_mimes[entry["mime_type"]] += 1
    if document.get("mime_counts") != dict(contract.mime_counts) or computed_mimes != dict(contract.mime_counts):
        raise MigrationError("manifest_mime_mismatch")
    keyset_digest, manifest_digest = _task3_digests(entries)
    if (
        document.get("keyset_digest") != keyset_digest
        or contract.keyset_digest is not None
        and contract.keyset_digest != keyset_digest
    ):
        raise MigrationError("manifest_keyset_digest_mismatch")
    if (
        document.get("manifest_digest") != manifest_digest
        or contract.manifest_digest is not None
        and contract.manifest_digest != manifest_digest
    ):
        raise MigrationError("manifest_digest_mismatch")
    return ManifestData(
        entries=sorted(entries, key=lambda entry: entry["object_name"]),
        manifest_file_sha256=actual_anchor,
        keyset_digest=keyset_digest,
        manifest_digest=manifest_digest,
    )


def _is_regular_non_reparse(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    return stat.S_ISREG(value.st_mode) and not (attributes & _REPARSE_POINT)


def _same_file(before: os.stat_result, opened: os.stat_result) -> bool:
    return (
        before.st_dev == opened.st_dev
        and before.st_ino == opened.st_ino
        and before.st_mode == opened.st_mode
        and before.st_size == opened.st_size
    )


def _validate_magic(mime_type: str, first: bytes, last: bytes, byte_size: int) -> bool:
    if mime_type == "image/png":
        return first.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return first.startswith(b"\xff\xd8\xff") and last.endswith(b"\xff\xd9")
    if mime_type == "image/webp":
        return (
            len(first) >= 12
            and first[:4] == b"RIFF"
            and first[8:12] == b"WEBP"
        )
    return False


def _audit_manifested_file(
    path: Path, expected: Mapping[str, Any], *, return_bytes: bool = False
) -> bytes | None:
    try:
        before = path.lstat()
    except OSError:
        raise MigrationError("local_asset_missing") from None
    if not _is_regular_non_reparse(before):
        raise MigrationError("local_asset_not_regular")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not _is_regular_non_reparse(opened) or not _same_file(before, opened):
            raise MigrationError("local_asset_changed")
        if opened.st_size != expected["byte_size"]:
            raise MigrationError("local_asset_size_mismatch")
        digest = hashlib.sha256()
        first = b""
        last = b""
        captured = bytearray() if return_bytes else None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                if captured is not None:
                    captured.extend(chunk)
                if len(first) < 16:
                    first = (first + chunk)[:16]
                last = (last + chunk)[-2:]
        after_fd = os.fstat(descriptor)
        try:
            after_path = path.lstat()
        except OSError:
            raise MigrationError("local_asset_changed") from None
        if not _same_file(before, after_fd) or not _same_file(before, after_path):
            raise MigrationError("local_asset_changed")
        if getattr(before, "st_mtime_ns", None) != getattr(after_path, "st_mtime_ns", None):
            raise MigrationError("local_asset_changed")
        if digest.hexdigest() != expected["sha256"]:
            raise MigrationError("local_asset_hash_mismatch")
        if not _validate_magic(expected["mime_type"], first, last, expected["byte_size"]):
            raise MigrationError("local_asset_magic_mismatch")
        return bytes(captured) if captured is not None else None
    except MigrationError:
        raise
    except (OSError, ValueError):
        raise MigrationError("local_asset_unreadable") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def audit_local_source(source_dir: Path | str, entries: Sequence[Mapping[str, Any]]) -> LocalAudit:
    source_dir = Path(source_dir)
    try:
        source_metadata = source_dir.lstat()
    except OSError:
        raise MigrationError("local_source_missing") from None
    if (
        not stat.S_ISDIR(source_metadata.st_mode)
        or getattr(source_metadata, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise MigrationError("local_source_not_directory")
    names = {entry["object_name"] for entry in entries}
    total_bytes = 0
    mime_counts = {"image/png": 0, "image/webp": 0, "image/jpeg": 0}
    for entry in entries:
        _audit_manifested_file(source_dir / entry["object_name"], entry)
        total_bytes += entry["byte_size"]
        mime_counts[entry["mime_type"]] += 1

    excluded_rows: list[str] = []
    excluded_bytes = 0
    try:
        directory_entries = sorted(os.scandir(source_dir), key=lambda item: item.name)
    except OSError:
        raise MigrationError("local_source_unreadable") from None
    for item in directory_entries:
        if item.name in names:
            continue
        try:
            metadata = item.stat(follow_symlinks=False)
        except OSError:
            raise MigrationError("local_extra_unreadable") from None
        size = metadata.st_size
        excluded_bytes += size
        excluded_rows.append(f"{item.name}|{size}")
    excluded_digest = hashlib.sha256("\n".join(excluded_rows).encode("utf-8")).hexdigest()
    return LocalAudit(
        count=len(entries),
        total_bytes=total_bytes,
        mime_counts=mime_counts,
        excluded_unmanifested=ExcludedAudit(
            count=len(excluded_rows), total_bytes=excluded_bytes, digest=excluded_digest
        ),
    )


def prepare(manifest_path: Path | str, expected_file_sha256: str, contract: ManifestContract) -> PreparedMigration:
    manifest = load_manifest(manifest_path, expected_file_sha256, contract)
    audit = audit_local_source(SOURCE_DIR, manifest.entries)
    return PreparedMigration(
        entries=manifest.entries,
        source_dir=SOURCE_DIR,
        manifest_file_sha256=manifest.manifest_file_sha256,
        keyset_digest=manifest.keyset_digest,
        manifest_digest=manifest.manifest_digest,
        audit=audit,
    )


def _r2_status(error: BaseException) -> tuple[int | None, str]:
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return None, ""
    metadata = response.get("ResponseMetadata")
    details = response.get("Error")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    code = details.get("Code") if isinstance(details, dict) else ""
    return status if type(status) is int else None, str(code or "")


def _retry_delay(policy: RetryPolicy, retry_number: int) -> float:
    base = min(policy.max_delay, policy.base_delay * (2 ** (retry_number - 1)))
    jitter = min(policy.max_delay - base, policy.base_delay * max(0.0, policy.random_fn()))
    return base + max(0.0, jitter)


def _retryable_transport_error(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return (
        error.__class__.__module__ == "botocore.exceptions"
        and error.__class__.__name__ in {
            "EndpointConnectionError",
            "ConnectionClosedError",
            "ConnectTimeoutError",
            "ReadTimeoutError",
        }
    )


def _call_r2(
    operation: Callable[[], Any], *, stats: TransferStats, retry: RetryPolicy,
    passthrough_statuses: set[int] | None = None, failure_code: str,
) -> Any:
    passthrough_statuses = passthrough_statuses or set()
    for attempt in range(1, retry.max_attempts + 1):
        stats.attempts += 1
        try:
            return operation()
        except MigrationError:
            raise
        except BaseException as error:
            status, _ = _r2_status(error)
            if status in passthrough_statuses:
                raise
            if status in {401, 403}:
                raise MigrationError("r2_access_denied") from None
            retryable = status in {408, 429} or (status is not None and 500 <= status <= 599)
            retryable = retryable or _retryable_transport_error(error)
            if not retryable or attempt >= retry.max_attempts:
                raise MigrationError(failure_code) from None
            stats.retries += 1
            retry.sleep(_retry_delay(retry, attempt))
    raise MigrationError(failure_code)


def _assert_r2_headers(info: Any, entry: Mapping[str, Any], code: str) -> None:
    metadata = info.get("Metadata") if isinstance(info, dict) else None
    if not (
        isinstance(info, dict)
        and type(info.get("ContentLength")) is int
        and info["ContentLength"] == entry["byte_size"]
        and info.get("ContentType") == entry["mime_type"]
        and info.get("CacheControl") == CACHE_CONTROL
        and isinstance(metadata, dict)
        and metadata.get("sha256") == entry["sha256"]
    ):
        raise MigrationError(code)


def _head_r2(client: Any, bucket: str, entry: Mapping[str, Any], stats: TransferStats, retry: RetryPolicy) -> Any:
    def operation():
        stats.head += 1
        return client.head_object(Bucket=bucket, Key=entry["object_name"])

    return _call_r2(
        operation, stats=stats, retry=retry, passthrough_statuses={404}, failure_code="r2_head_failed"
    )


def ensure_r2_object(
    client: Any, bucket: str, entry: Mapping[str, Any], local_path: Path,
    *, stats: TransferStats, retry: RetryPolicy | None = None,
) -> str:
    retry = retry or RetryPolicy()
    try:
        info = _head_r2(client, bucket, entry, stats, retry)
    except BaseException as error:
        status, code = _r2_status(error)
        if status != 404 and code not in {"404", "NoSuchKey", "NotFound"}:
            if isinstance(error, MigrationError):
                raise
            raise MigrationError("r2_head_failed") from None
    else:
        _assert_r2_headers(info, entry, "r2_head_mismatch")
        stats.existing += 1
        return "existing"

    verified_body = _audit_manifested_file(local_path, entry, return_bytes=True)
    if type(verified_body) is not bytes:
        raise MigrationError("local_asset_unreadable")

    def put_operation():
        stats.put += 1
        return client.put_object(
            Bucket=bucket,
            Key=entry["object_name"],
            Body=verified_body,
            IfNoneMatch="*",
            ContentType=entry["mime_type"],
            CacheControl=CACHE_CONTROL,
            Metadata={"sha256": entry["sha256"]},
        )

    raced = False
    try:
        _call_r2(
            put_operation, stats=stats, retry=retry, passthrough_statuses={412}, failure_code="r2_put_failed"
        )
    except BaseException as error:
        status, code = _r2_status(error)
        if status != 412 and code not in {"412", "PreconditionFailed"}:
            if isinstance(error, MigrationError):
                raise
            raise MigrationError("r2_put_failed") from None
        raced = True
        stats.precondition_412 += 1
    info = _head_r2(client, bucket, entry, stats, retry)
    _assert_r2_headers(info, entry, "r2_head_mismatch")
    if raced:
        stats.existing += 1
        return "precondition_existing"
    stats.created += 1
    return "created"


def verify_r2_body(
    client: Any, bucket: str, entry: Mapping[str, Any], *, stats: TransferStats,
    retry: RetryPolicy | None = None, chunk_size: int = _CHUNK_SIZE,
) -> None:
    retry = retry or RetryPolicy()

    def operation():
        response = client.get_object(Bucket=bucket, Key=entry["object_name"])
        body = response.get("Body") if isinstance(response, dict) else None
        if body is None or not callable(getattr(body, "read", None)) or not callable(getattr(body, "close", None)):
            raise MigrationError("r2_get_body_invalid")
        digest = hashlib.sha256()
        total = 0
        try:
            _assert_r2_headers(response, entry, "r2_get_header_mismatch")
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise MigrationError("r2_get_body_invalid")
                total += len(chunk)
                if total > entry["byte_size"]:
                    raise MigrationError("r2_get_size_mismatch")
                digest.update(chunk)
        finally:
            try:
                body.close()
            except BaseException:
                pass
        if total != entry["byte_size"]:
            raise MigrationError("r2_get_size_mismatch")
        if digest.hexdigest() != entry["sha256"]:
            raise MigrationError("r2_get_hash_mismatch")

    _call_r2(operation, stats=stats, retry=retry, failure_code="r2_get_body_failed")
    stats.full_get += 1


def deterministic_batch_id(file_sha256: str, manifest_digest: str, keyset_digest: str) -> str:
    binding = f"mobiliti:catalog-assets-cutover:v1:{file_sha256}:{manifest_digest}:{keyset_digest}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, binding))


def run_registry_cutover(
    rpc: Any, entries: Sequence[Mapping[str, Any]], batch_id: str,
    manifest_digest: str, keyset_digest: str,
) -> int:
    count = 0

    def call(name: str, payload: dict[str, Any], expected: str):
        nonlocal count
        result = rpc.call(name, payload)
        count += 1
        if result != expected:
            raise MigrationError("rpc_response_mismatch")

    try:
        call(
            "saas_start_catalog_asset_cutover_batch",
            {
                "p_batch_id": batch_id,
                "p_expected_count": len(entries),
                "p_manifest_digest": manifest_digest,
                "p_keyset_digest": keyset_digest,
            },
            batch_id,
        )
        for entry in entries:
            call(
                "saas_add_catalog_asset_cutover_entry",
                {"p_batch_id": batch_id, **{f"p_{key}": entry[key] for key in (
                    "object_name", "sha256", "byte_size", "mime_type"
                )}},
                entry["object_name"],
            )
        for entry in entries:
            call(
                "saas_register_catalog_asset",
                {
                    "p_object_name": entry["object_name"],
                    "p_storage_provider": "r2",
                    "p_physical_bucket": LOGICAL_BUCKET,
                    "p_byte_size": entry["byte_size"],
                    "p_mime_type": entry["mime_type"],
                },
                entry["object_name"],
            )
        call(
            "saas_finalize_catalog_asset_cutover_batch",
            {"p_batch_id": batch_id},
            batch_id,
        )
    except MigrationError as error:
        error.rpc_count = count
        raise
    return count


def _binding(prepared: Any, batch_id: str) -> dict[str, str]:
    return {
        "manifest_file_sha256": prepared.manifest_file_sha256,
        "manifest_digest": prepared.manifest_digest,
        "keyset_digest": prepared.keyset_digest,
        "batch_id": batch_id,
    }


def new_checkpoint(
    prepared: Any, batch_id: str, *, prepared_names: Sequence[str] = (),
    rpc_status: str = "not_started", cumulative_stats: TransferStats | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "binding": _binding(prepared, batch_id),
        "prepared_objects": sorted(set(prepared_names)),
        "rpc_status": rpc_status,
        "cumulative_stats": _stats_dict(cumulative_stats or TransferStats()),
        "updated_at": _utc_now(),
    }


_SENSITIVE_KEY_PARTS = (
    "authorization", "secret", "token", "endpoint", "access_key", "service_key",
    "raw_exception", "headers",
)


def _sanitize_output(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            safe_key = str(key)
            lowered = safe_key.lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                continue
            result[safe_key] = _sanitize_output(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_output(item) for item in value]
    if isinstance(value, BaseException):
        return "[redacted_exception]"
    if isinstance(value, str):
        lowered = value.lower()
        if "://" in value or "bearer " in lowered:
            return "[redacted]"
        return value
    if value is None or type(value) in {bool, int, float}:
        return value
    return "[redacted]"


def _atomic_json(path: Path | str, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    temporary_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sanitized = _sanitize_output(payload)
        data = (json.dumps(sanitized, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            try:
                os.chmod(temporary_name, 0o600)
            except OSError:
                pass
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except (OSError, TypeError, ValueError):
        raise MigrationError("atomic_output_failed") from None


def write_checkpoint(path: Path | str, payload: Mapping[str, Any]) -> None:
    _atomic_json(path, payload)


def write_report(path: Path | str, payload: Mapping[str, Any]) -> None:
    _atomic_json(path, payload)


def load_checkpoint(path: Path | str, prepared: Any, batch_id: str) -> dict[str, Any]:
    path = Path(path)
    try:
        document = _strict_json_loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return new_checkpoint(prepared, batch_id)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonMember):
        raise MigrationError("checkpoint_invalid") from None
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("binding") != _binding(prepared, batch_id)
        or document.get("rpc_status") not in {"not_started", "finalized"}
        or not isinstance(document.get("prepared_objects"), list)
        or any(not isinstance(name, str) for name in document["prepared_objects"])
    ):
        raise MigrationError("checkpoint_binding_mismatch")
    _stats_from(document.get("cumulative_stats"))
    allowed = {entry["object_name"] for entry in prepared.entries}
    if len(document["prepared_objects"]) != len(set(document["prepared_objects"])) or not set(document["prepared_objects"]) <= allowed:
        raise MigrationError("checkpoint_binding_mismatch")
    return document


_BATCH_ROW_FIELDS = {
    "batch_id", "manifest_digest", "keyset_digest", "expected_count",
    "status", "verified_count", "missing_count", "failed_count",
}


def _cutover_batch_status(row: Any, prepared: Any, batch_id: str) -> str:
    if row is None:
        return "absent"
    if not isinstance(row, dict) or set(row) != _BATCH_ROW_FIELDS:
        raise MigrationError("cutover_batch_db_mismatch")
    if (
        any(not _is_plain_int(row[name]) for name in (
            "expected_count", "verified_count", "missing_count", "failed_count"
        ))
        or any(not isinstance(row[name], str) for name in (
            "batch_id", "manifest_digest", "keyset_digest", "status"
        ))
    ):
        raise MigrationError("cutover_batch_db_mismatch")
    common_matches = (
        row["batch_id"] == batch_id
        and row["manifest_digest"] == prepared.manifest_digest
        and row["keyset_digest"] == prepared.keyset_digest
        and row["expected_count"] == len(prepared.entries)
        and row["missing_count"] == 0
        and row["failed_count"] == 0
    )
    status = row["status"]
    counts_match = (
        status == "verified" and row["verified_count"] == len(prepared.entries)
        or status in {"pending", "loading"} and row["verified_count"] == 0
    )
    if not common_matches or not counts_match:
        raise MigrationError("cutover_batch_db_mismatch")
    return status


def execute_migration(
    prepared: Any, r2_client: Any, rpc_client: Any, *, checkpoint_path: Path | str,
    bucket: str = LOGICAL_BUCKET, retry: RetryPolicy | None = None,
) -> MigrationOutcome:
    if bucket != LOGICAL_BUCKET:
        raise MigrationError("r2_bucket_invalid")
    retry = retry or RetryPolicy()
    stats = TransferStats()
    batch_id = deterministic_batch_id(
        prepared.manifest_file_sha256, prepared.manifest_digest, prepared.keyset_digest
    )
    rpc_count = 0
    database_checks = 0
    previous_stats = TransferStats()
    try:
        checkpoint = load_checkpoint(checkpoint_path, prepared, batch_id)
        previous_stats = _stats_from(checkpoint["cumulative_stats"])
        completed = set(checkpoint["prepared_objects"])
        for entry in prepared.entries:
            name = entry["object_name"]
            if name not in completed:
                ensure_r2_object(
                    r2_client, bucket, entry, prepared.source_dir / name, stats=stats, retry=retry
                )
                completed.add(name)
                checkpoint = new_checkpoint(
                    prepared, batch_id, prepared_names=sorted(completed),
                    rpc_status=checkpoint["rpc_status"],
                    cumulative_stats=_stats_sum(previous_stats, stats),
                )
                write_checkpoint(checkpoint_path, checkpoint)
        for entry in prepared.entries:
            verify_r2_body(r2_client, bucket, entry, stats=stats, retry=retry)
        checkpoint = new_checkpoint(
            prepared, batch_id, prepared_names=sorted(completed),
            rpc_status=checkpoint["rpc_status"],
            cumulative_stats=_stats_sum(previous_stats, stats),
        )
        write_checkpoint(checkpoint_path, checkpoint)

        row = rpc_client.get_cutover_batch(batch_id)
        database_checks += 1
        database_status = _cutover_batch_status(row, prepared, batch_id)
        if database_status == "verified":
            result = rpc_client.call(
                "saas_finalize_catalog_asset_cutover_batch", {"p_batch_id": batch_id}
            )
            rpc_count += 1
            if result != batch_id:
                raise MigrationError("rpc_response_mismatch")
        else:
            rpc_count = run_registry_cutover(
                rpc_client, prepared.entries, batch_id, prepared.manifest_digest, prepared.keyset_digest
            )
        final_row = rpc_client.get_cutover_batch(batch_id)
        database_checks += 1
        if final_row is None:
            raise MigrationError("cutover_batch_db_unverified")
        if _cutover_batch_status(final_row, prepared, batch_id) != "verified":
            raise MigrationError("cutover_batch_db_unverified")
        checkpoint = new_checkpoint(
            prepared, batch_id, prepared_names=sorted(completed), rpc_status="finalized",
            cumulative_stats=_stats_sum(previous_stats, stats),
        )
        write_checkpoint(checkpoint_path, checkpoint)
        cumulative_stats = _stats_sum(previous_stats, stats)
        return MigrationOutcome(
            certified=True,
            batch_id=batch_id,
            stats=stats,
            cumulative_stats=cumulative_stats,
            rpc_count=rpc_count,
            rpc_status="finalized",
            database_checks=database_checks,
        )
    except MigrationError as error:
        error.stats = stats
        error.cumulative_stats = _stats_sum(previous_stats, stats)
        error.rpc_count = getattr(error, "rpc_count", rpc_count)
        error.database_checks = database_checks
        raise


def _https_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https" or not host or host != host.lower()
        or parsed.username is not None or parsed.password is not None or port is not None
        or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
    ):
        return None
    return f"https://{host}"


def load_execute_config(environ: Mapping[str, str]) -> ExecuteConfig:
    endpoint = (environ.get("CATALOG_ASSET_R2_ENDPOINT_URL", "") or "").strip()
    access = (environ.get("CATALOG_ASSET_R2_ACCESS_KEY_ID", "") or "").strip()
    secret = (environ.get("CATALOG_ASSET_R2_SECRET_ACCESS_KEY", "") or "").strip()
    session_token = (environ.get("CATALOG_ASSET_R2_SESSION_TOKEN", "") or "").strip()
    region = (environ.get("CATALOG_ASSET_R2_REGION", "") or "").strip()
    bucket = (environ.get("CATALOG_ASSET_R2_BUCKET", "") or "").strip()
    supabase_url = (environ.get("SUPABASE_URL", "") or "").strip()
    service_key = (environ.get("SUPABASE_SERVICE_KEY", "") or "").strip()
    endpoint_origin = _https_origin(endpoint)
    supabase_origin = _https_origin(supabase_url)
    if not endpoint_origin or not access or not secret or not region or bucket != LOGICAL_BUCKET:
        raise MigrationError("r2_configuration_invalid")
    if not supabase_origin or not service_key:
        raise MigrationError("supabase_configuration_invalid")
    return ExecuteConfig(
        r2_endpoint_url=endpoint_origin,
        r2_access_key_id=access,
        r2_secret_access_key=secret,
        r2_session_token=session_token,
        r2_region=region,
        r2_bucket=bucket,
        supabase_url=supabase_origin,
        supabase_service_key=service_key,
    )


def create_r2_client(config: ExecuteConfig, *, boto3_module: Any | None = None) -> Any:
    if boto3_module is None:
        try:
            import boto3 as boto3_module
            from botocore.config import Config
        except ImportError:
            raise MigrationError("boto3_dependency_missing") from None
        client_config = Config(retries={"max_attempts": 0, "mode": "standard"})
    else:
        client_config = None
    arguments = {
        "endpoint_url": config.r2_endpoint_url,
        "aws_access_key_id": config.r2_access_key_id,
        "aws_secret_access_key": config.r2_secret_access_key,
        "region_name": config.r2_region,
    }
    if config.r2_session_token:
        arguments["aws_session_token"] = config.r2_session_token
    if client_config is not None:
        arguments["config"] = client_config
    try:
        return boto3_module.client("s3", **arguments)
    except BaseException:
        raise MigrationError("r2_client_failed") from None


class SupabaseRpcClient:
    def __init__(self, base_url: str, service_key: str, *, opener: Callable[..., Any] = urlopen, timeout: float = 30):
        self._base_url = base_url
        self._service_key = service_key
        self._opener = opener
        self._timeout = timeout

    def _request_json(self, request: Request) -> Any:
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                if getattr(response, "status", 200) != 200:
                    raise MigrationError("rpc_failed")
                raw = response.read(4096)
                if response.read(1):
                    raise MigrationError("rpc_response_too_large")
        except MigrationError:
            raise
        except (HTTPError, URLError, OSError, TimeoutError):
            raise MigrationError("rpc_failed") from None
        try:
            return _strict_json_loads(raw.decode("utf-8"))
        except _DuplicateJsonMember:
            raise MigrationError("rpc_response_duplicate") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MigrationError("rpc_response_invalid") from None

    def call(self, name: str, payload: Mapping[str, Any]) -> Any:
        request = Request(
            f"{self._base_url}/rest/v1/rpc/{name}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._service_key}",
                "apikey": self._service_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        return self._request_json(request)

    def get_cutover_batch(self, batch_id: str) -> dict[str, Any] | None:
        try:
            if str(uuid.UUID(batch_id)) != batch_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise MigrationError("cutover_batch_id_invalid") from None
        select = (
            "batch_id,manifest_digest,keyset_digest,expected_count,status,"
            "verified_count,missing_count,failed_count"
        )
        query = urlencode({"select": select, "batch_id": f"eq.{batch_id}"})
        request = Request(
            f"{self._base_url}/rest/v1/saas_catalog_asset_cutover_batches?{query}",
            method="GET",
            headers={
                "Authorization": f"Bearer {self._service_key}",
                "apikey": self._service_key,
                "Accept": "application/json",
            },
        )
        result = self._request_json(request)
        if not isinstance(result, list) or len(result) > 1 or any(not isinstance(row, dict) for row in result):
            raise MigrationError("cutover_batch_db_response_invalid")
        return result[0] if result else None


def create_rpc_client(config: ExecuteConfig) -> SupabaseRpcClient:
    return SupabaseRpcClient(config.supabase_url, config.supabase_service_key)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migra catalog-assets a R2 con verificacion completa")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-manifest-file-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="habilita R2 y RPC; default es dry-run")
    parser.add_argument("--checkpoint", type=Path)
    return parser


def resolve_checkpoint_path(args: argparse.Namespace) -> Path:
    if args.checkpoint is not None:
        return args.checkpoint
    report = Path(args.report)
    if report.suffix:
        return report.with_suffix(".checkpoint.json")
    return report.with_name(report.name + ".checkpoint.json")


def _resolved(path: Path | str) -> Path:
    try:
        return Path(path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise MigrationError("output_path_invalid") from None


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def validate_output_paths(args: argparse.Namespace) -> None:
    manifest = _resolved(args.manifest)
    report = _resolved(args.report)
    source = _resolved(SOURCE_DIR)
    if report == manifest or _inside(report, source):
        raise MigrationError("output_path_unsafe")
    if args.execute:
        checkpoint = _resolved(resolve_checkpoint_path(args))
        if checkpoint in {manifest, report} or _inside(checkpoint, source):
            raise MigrationError("output_path_unsafe")


def _base_report(mode: str, prepared: PreparedMigration, contract: ManifestContract) -> dict[str, Any]:
    extras = prepared.audit.excluded_unmanifested
    return {
        "schema_version": 1,
        "mode": mode,
        "certified": False,
        "started_at": _utc_now(),
        "finished_at": None,
        "manifest": {
            "file_sha256": prepared.manifest_file_sha256,
            "keyset_digest": prepared.keyset_digest,
            "manifest_digest": prepared.manifest_digest,
        },
        "expected": {
            "count": contract.entry_count,
            "bytes": contract.total_bytes,
            "mime_counts": dict(contract.mime_counts),
        },
        "observed": {
            "count": prepared.audit.count,
            "bytes": prepared.audit.total_bytes,
            "mime_counts": dict(prepared.audit.mime_counts),
        },
        "excluded_unmanifested": {
            "count": extras.count,
            "bytes": extras.total_bytes,
            "digest": extras.digest,
        },
        "transfer": {
            "current": _stats_dict(TransferStats()),
            "cumulative": _stats_dict(TransferStats()),
        },
        "rpc": {"status": "not_started", "count": 0},
        "database": {"checks": 0},
        "failures": [],
    }


def run(
    argv: Sequence[str] | None = None, *, contract: ManifestContract = PRODUCTION_CONTRACT,
    environ: Mapping[str, str] = os.environ, r2_factory: Callable[[ExecuteConfig], Any] | None = None,
    rpc_factory: Callable[[ExecuteConfig], Any] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    mode = "execute" if args.execute else "dry-run"
    try:
        validate_output_paths(args)
    except MigrationError:
        return 2
    report: dict[str, Any] | None = None
    try:
        prepared = prepare(args.manifest, args.expected_manifest_file_sha256, contract)
        report = _base_report(mode, prepared, contract)
        if args.execute:
            config = load_execute_config(environ)
            r2_client = (r2_factory or create_r2_client)(config)
            rpc_client = (rpc_factory or create_rpc_client)(config)
            outcome = execute_migration(
                prepared, r2_client, rpc_client,
                checkpoint_path=resolve_checkpoint_path(args), bucket=config.r2_bucket,
            )
            report["certified"] = outcome.certified
            report["batch_id"] = outcome.batch_id
            report["transfer"] = {
                "current": _stats_dict(outcome.stats),
                "cumulative": _stats_dict(outcome.cumulative_stats),
            }
            report["rpc"] = {"status": outcome.rpc_status, "count": outcome.rpc_count}
            report["database"] = {"checks": outcome.database_checks}
        report["finished_at"] = _utc_now()
        write_report(args.report, report)
        return 0
    except MigrationError as error:
        partial_stats = getattr(error, "stats", None)
        if report is not None and isinstance(partial_stats, TransferStats):
            report["transfer"] = {
                "current": _stats_dict(partial_stats),
                "cumulative": _stats_dict(
                    getattr(error, "cumulative_stats", partial_stats)
                ),
            }
            report["rpc"] = {
                "status": "failed" if getattr(error, "rpc_count", 0) else "not_started",
                "count": getattr(error, "rpc_count", 0),
            }
            report["database"] = {"checks": getattr(error, "database_checks", 0)}
        failure = {"code": str(error) if _safe_code(str(error)) else "migration_failed"}
        if report is None:
            report = {
                "schema_version": 1, "mode": mode, "certified": False,
                "started_at": _utc_now(), "finished_at": _utc_now(),
                "failures": [failure], "rpc": {"status": "not_started", "count": 0},
            }
        else:
            report["certified"] = False
            report["finished_at"] = _utc_now()
            report["failures"] = [failure]
        try:
            write_report(args.report, report)
        except MigrationError:
            pass
        return 2


def _safe_code(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]{1,80}", value))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
