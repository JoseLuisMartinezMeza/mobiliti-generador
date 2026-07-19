import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

from .graph import DownloadedFile, GraphItem
from .rates import ExchangeRate


_TIMEOUT = 15
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_SNAPSHOT_PAYLOAD_BYTES = 100 * 1024 * 1024
_SNAPSHOT_ENVELOPE_MARGIN_BYTES = 4 * 1024 * 1024
_MAX_SNAPSHOT_REQUEST_BYTES = _MAX_SNAPSHOT_PAYLOAD_BYTES + _SNAPSHOT_ENVELOPE_MARGIN_BYTES
_MAX_SNAPSHOT_RESPONSE_BYTES = _MAX_SNAPSHOT_PAYLOAD_BYTES + _SNAPSHOT_ENVELOPE_MARGIN_BYTES
_MAX_STORAGE_RESPONSE_BYTES = 64 * 1024
_MAX_CATALOG_ASSET_BYTES = 8 * 1024 * 1024
_MAX_RAW_BYTES = 64 * 1024 * 1024
_MAX_FILES = 10_000
_FILE_PAGE_SIZE = 1_000
_MAX_RATES = 1_000
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CATALOG_ASSET_NAME = re.compile(r"([0-9a-f]{64})\.png")
_PROJECT_HOST = re.compile(r"[a-z]{20}\.supabase\.co")
_CONTENT_RANGE = re.compile(r"(\d+)-(\d+)/(\d+)")
_MIME_BY_EXTENSION = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
_SOURCE_FIELDS = (
    "id,supplier,label,adapter,graph_drive_id,graph_root_item_id,delta_link,"
    "enabled,published_version_id"
)
_RUN_FIELDS = (
    "id,source_id,trigger_type,status,requested_by,candidate_version_id,metrics,error_summary"
)
_SNAPSHOT_FIELDS = "id,supplier,source_hash,generated_at,status,payload"
_FILE_FIELDS = (
    "id,source_id,drive_item_id,path,e_tag,c_tag,size_bytes,sha256,mime_type,"
    "private_object_path,validation_status,validation_summary,last_sync_run_id,"
    "is_deleted,deleted_at,deleted_sync_run_id,discovered_at,validated_at"
)
_SYNC_SUPPLIERS = {"cr-global", "sonara", "sunon", "alma", "lumbro"}


class CatalogRepositoryError(ValueError):
    pass


class _HTTPStatus(Exception):
    def __init__(self, code):
        self.code = code


def _fail(kind="response"):
    raise CatalogRepositoryError(f"Invalid catalog {kind}")


def _uuid(value):
    if not isinstance(value, str):
        _fail()
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        _fail()
    if str(parsed) != value.lower():
        _fail()
    return parsed


def _optional_uuid(value):
    return None if value is None else _uuid(value)


def _string(value, *, maximum=8192, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        _fail()
    return value


def _integer(value, *, optional=False, minimum=None):
    if optional and value is None:
        return None
    if type(value) is not int or (minimum is not None and value < minimum):
        _fail()
    return value


def _object(value, *, maximum=1024 * 1024):
    if not isinstance(value, dict):
        _fail()
    try:
        if len(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()) > maximum:
            _fail()
    except (TypeError, ValueError, OverflowError):
        _fail()
    return value


def _timestamp(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        _fail()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()
    if parsed.tzinfo is None:
        _fail()
    return parsed.astimezone(timezone.utc)


def _iso(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CatalogRepositoryError("Invalid catalog timestamp")
    value = value.astimezone(timezone.utc)
    text = value.isoformat(timespec="microseconds" if value.microsecond else "seconds")
    return text.replace("+00:00", "Z")


def _exact_row(row, fields):
    if not isinstance(row, dict) or set(row) != set(fields):
        _fail()
    return row


@dataclass(frozen=True)
class SourceRecord:
    id: UUID
    supplier: str
    label: str
    adapter: str
    graph_drive_id: str
    graph_root_item_id: str
    delta_link: str | None
    enabled: bool
    published_version_id: UUID | None

    @classmethod
    def from_row(cls, row):
        row = _exact_row(row, cls.__dataclass_fields__)
        enabled = row["enabled"]
        if type(enabled) is not bool:
            _fail()
        return cls(
            _uuid(row["id"]), _string(row["supplier"], maximum=64),
            _string(row["label"], maximum=256), _string(row["adapter"], maximum=128),
            _string(row["graph_drive_id"], maximum=512),
            _string(row["graph_root_item_id"], maximum=512),
            _string(row["delta_link"], optional=True), enabled,
            _optional_uuid(row["published_version_id"]),
        )


@dataclass(frozen=True)
class RunRecord:
    id: UUID
    source_id: UUID
    trigger_type: str
    status: str
    requested_by: int | None
    candidate_version_id: UUID | None
    metrics: dict
    error_summary: str | None

    @classmethod
    def from_row(cls, row):
        row = _exact_row(row, cls.__dataclass_fields__)
        trigger = _string(row["trigger_type"], maximum=16)
        status_value = _string(row["status"], maximum=32)
        if trigger not in {"scheduled", "manual"} or status_value not in {
            "requested", "running", "no_changes", "awaiting_approval", "published",
            "rejected", "failed",
        }:
            _fail()
        return cls(
            _uuid(row["id"]), _uuid(row["source_id"]), trigger, status_value,
            _integer(row["requested_by"], optional=True, minimum=1),
            _optional_uuid(row["candidate_version_id"]), _object(row["metrics"]),
            _string(row["error_summary"], maximum=256, optional=True),
        )


@dataclass(frozen=True)
class SyncClaim:
    run_id: UUID
    supplier: str
    trigger_type: str
    requested_by: int | None

    @classmethod
    def from_row(cls, row):
        row = _exact_row(row, cls.__dataclass_fields__)
        supplier = _string(row["supplier"], maximum=64)
        trigger = _string(row["trigger_type"], maximum=16)
        requested_by = _integer(row["requested_by"], optional=True, minimum=1)
        if supplier not in _SYNC_SUPPLIERS or trigger not in {"manual", "scheduled"}:
            _fail()
        if trigger == "scheduled" and requested_by is not None:
            _fail()
        return cls(_uuid(row["run_id"]), supplier, trigger, requested_by)


@dataclass(frozen=True)
class SnapshotRecord:
    id: UUID
    supplier: str
    source_hash: str
    generated_at: datetime
    status: str
    payload: dict

    @classmethod
    def from_row(cls, row):
        row = _exact_row(row, cls.__dataclass_fields__)
        source_hash = row["source_hash"]
        status_value = row["status"]
        if not isinstance(source_hash, str) or _SHA256.fullmatch(source_hash) is None:
            _fail()
        if status_value not in {"candidate", "published", "superseded", "rejected"}:
            _fail()
        generated_at = _timestamp(row["generated_at"])
        payload = _object(row["payload"], maximum=_MAX_SNAPSHOT_PAYLOAD_BYTES)
        if (
            payload.get("supplier") != row["supplier"]
            or payload.get("source_hash") != source_hash
            or not isinstance(payload.get("items"), list)
            or len(payload["items"]) > 100_000
            or _timestamp(payload.get("generated_at")) != generated_at
        ):
            _fail()
        return cls(
            _uuid(row["id"]), _string(row["supplier"], maximum=64), source_hash,
            generated_at, status_value, payload,
        )


@dataclass(frozen=True)
class SourceFileRecord:
    id: UUID
    source_id: UUID
    drive_item_id: str
    path: str
    e_tag: str
    c_tag: str | None
    size_bytes: int
    sha256: str
    mime_type: str
    private_object_path: str
    validation_status: str
    validation_summary: dict
    last_sync_run_id: UUID | None
    is_deleted: bool
    deleted_at: datetime | None
    deleted_sync_run_id: UUID | None
    discovered_at: datetime
    validated_at: datetime | None

    @classmethod
    def from_row(cls, row):
        row = _exact_row(row, cls.__dataclass_fields__)
        digest = row["sha256"]
        mime = row["mime_type"]
        validation = row["validation_status"]
        deleted = row["is_deleted"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            _fail()
        if mime not in set(_MIME_BY_EXTENSION.values()) or validation not in {"pending", "valid", "invalid"}:
            _fail()
        if type(deleted) is not bool:
            _fail()
        deleted_at = _timestamp(row["deleted_at"], optional=True)
        deleted_run = _optional_uuid(row["deleted_sync_run_id"])
        if deleted != (deleted_at is not None and deleted_run is not None):
            _fail()
        object_path = _string(row["private_object_path"], maximum=96)
        extension = "xlsx" if mime == _MIME_BY_EXTENSION["xlsx"] else "pdf"
        if object_path != f"catalog-sources/{digest}.{extension}":
            _fail()
        return cls(
            _uuid(row["id"]), _uuid(row["source_id"]),
            _string(row["drive_item_id"], maximum=512), _string(row["path"], maximum=2048),
            _string(row["e_tag"], maximum=1024), _string(row["c_tag"], maximum=1024, optional=True),
            _integer(row["size_bytes"], minimum=0), digest, mime, object_path, validation,
            _object(row["validation_summary"]), _optional_uuid(row["last_sync_run_id"]),
            deleted, deleted_at, deleted_run, _timestamp(row["discovered_at"]),
            _timestamp(row["validated_at"], optional=True),
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    return build_opener(ProxyHandler({}), _NoRedirect())


class CatalogRepository:
    def __init__(self, base_url, service_key, *, opener=None, timeout=_TIMEOUT):
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except (TypeError, ValueError):
            raise CatalogRepositoryError("Invalid catalog configuration") from None
        if (
            not isinstance(base_url, str) or parsed.scheme != "https" or parsed.hostname is None
            or _PROJECT_HOST.fullmatch(parsed.hostname) is None or parsed.hostname != parsed.hostname.lower()
            or parsed.netloc != parsed.hostname
            or port is not None or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
        ):
            raise CatalogRepositoryError("Invalid catalog configuration")
        if (
            not isinstance(service_key, str) or not service_key or len(service_key) > 16_384
            or any(ord(character) < 33 or ord(character) == 127 for character in service_key)
        ):
            raise CatalogRepositoryError("Invalid catalog configuration")
        if type(timeout) not in {int, float} or not 0 < timeout <= 30:
            raise CatalogRepositoryError("Invalid catalog configuration")
        self._base_url = f"https://{parsed.hostname}"
        self._service_key = service_key
        self._opener = opener or _default_opener()
        self._timeout = timeout

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY"))

    def _open(self, request, expected, *, max_bytes, require_json=False):
        if type(max_bytes) is not int or max_bytes < 0:
            raise CatalogRepositoryError("Invalid catalog response limit")
        try:
            response = self._opener.open(request, timeout=self._timeout)
            with response:
                status_code = getattr(response, "status", None)
                if type(status_code) is not int or status_code not in expected:
                    raise _HTTPStatus(status_code)
                if require_json:
                    headers = getattr(response, "headers", None)
                    content_type = headers.get("Content-Type") if hasattr(headers, "get") else None
                    if not isinstance(content_type, str) or content_type.split(";", 1)[0].strip().lower() != "application/json":
                        _fail()
                response_headers = {
                    str(key).lower(): value
                    for key, value in getattr(response, "headers", {}).items()
                    if isinstance(value, str)
                }
                raw = response.read(max_bytes + 1)
        except _HTTPStatus:
            raise
        except CatalogRepositoryError:
            raise
        except HTTPError as error:
            raise _HTTPStatus(error.code) from None
        except Exception:
            raise CatalogRepositoryError("Catalog request failed") from None
        if not isinstance(raw, bytes) or len(raw) > max_bytes:
            _fail()
        return raw, response_headers

    def _request(self, method, path, *, query=None, payload=None, expected=(200,), headers=None,
                 require_json=False, max_bytes, max_request_bytes, conflict_none=False):
        if type(max_request_bytes) is not int or max_request_bytes < 0:
            raise CatalogRepositoryError("Invalid catalog request limit")
        url = self._base_url + path
        if query:
            url += "?" + urlencode(query)
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._service_key}",
            "apikey": self._service_key,
        }
        data = None
        if payload is not None:
            try:
                data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
            except (TypeError, ValueError, OverflowError):
                raise CatalogRepositoryError("Invalid catalog payload") from None
            if len(data) > max_request_bytes:
                raise CatalogRepositoryError("Invalid catalog payload")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            return self._open(request, expected, require_json=require_json, max_bytes=max_bytes)
        except _HTTPStatus as error:
            if conflict_none and error.code == 409:
                return None
            raise CatalogRepositoryError("Catalog request failed") from None

    def _json(self, method, path, *, query=None, payload=None, expected=(200,), headers=None,
              max_bytes, with_headers=False, max_request_bytes=_MAX_REQUEST_BYTES,
              conflict_none=False):
        response = self._request(
            method, path, query=query, payload=payload, expected=expected,
            headers=headers, require_json=True, max_bytes=max_bytes,
            max_request_bytes=max_request_bytes, conflict_none=conflict_none,
        )
        if response is None:
            return None
        raw, response_headers = response
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail()
        return (value, response_headers) if with_headers else value

    def _rows(self, method, table, fields, record_type, *, query=None, payload=None,
              expected=(200,), max_bytes, conflict_none=False):
        query = dict(query or {})
        query["select"] = fields
        rows = self._json(
            method, f"/rest/v1/{table}", query=query, payload=payload, expected=expected,
            headers={"Prefer": "return=representation"} if method != "GET" else None,
            max_bytes=max_bytes, conflict_none=conflict_none,
        )
        if rows is None:
            return None
        if not isinstance(rows, list) or len(rows) > _MAX_FILES:
            _fail()
        return tuple(record_type.from_row(row) for row in rows)

    def get_source(self, supplier):
        supplier = _input_string(supplier, "supplier", 64)
        rows = self._rows("GET", "saas_catalog_sources", _SOURCE_FIELDS, SourceRecord,
                          query={"supplier": f"eq.{supplier}", "limit": "2"},
                          max_bytes=_MAX_RESPONSE_BYTES)
        if len(rows) != 1:
            _fail()
        row = rows[0]
        if row.supplier != supplier:
            _fail()
        return row

    def create_run(self, source_id, trigger, requested_by):
        source_id = _input_uuid(source_id)
        if trigger not in {"scheduled", "manual"}:
            raise CatalogRepositoryError("Invalid catalog trigger")
        if requested_by is not None and (type(requested_by) is not int or requested_by < 1):
            raise CatalogRepositoryError("Invalid catalog requester")
        rows = self._rows(
            "POST", "saas_catalog_sync_runs", _RUN_FIELDS, RunRecord,
            payload={"source_id": str(source_id), "trigger_type": trigger,
                     "requested_by": requested_by, "metrics": {}}, expected=(201,),
            max_bytes=_MAX_RESPONSE_BYTES, conflict_none=True,
        )
        if rows is None:
            return None
        row = _one(rows)
        if (
            row.source_id != source_id or row.trigger_type != trigger
            or row.requested_by != requested_by or row.status != "requested"
            or row.candidate_version_id is not None or row.metrics != {}
            or row.error_summary is not None
        ):
            _fail()
        return row

    def start_run(self, source_id, trigger, requested_by):
        source_id = _input_uuid(source_id)
        if trigger not in {"scheduled", "manual"}:
            raise CatalogRepositoryError("Invalid catalog trigger")
        if requested_by is not None and (type(requested_by) is not int or requested_by < 1):
            raise CatalogRepositoryError("Invalid catalog requester")
        payload = {
            "p_source_id": str(source_id),
            "p_trigger_type": trigger,
            "p_requested_by": requested_by,
            "p_request_key": str(uuid4()),
        }
        for attempt in range(2):
            try:
                value = self._json(
                    "POST", "/rest/v1/rpc/saas_start_catalog_sync", payload=payload,
                    max_bytes=_MAX_RESPONSE_BYTES,
                )
                return None if value is None else _uuid(value)
            except CatalogRepositoryError:
                if attempt:
                    raise

    def claim_run(self, run_id):
        run_id = _input_uuid(run_id)
        now = _iso(datetime.now(timezone.utc))
        rows = self._rows(
            "PATCH", "saas_catalog_sync_runs", _RUN_FIELDS, RunRecord,
            query={"id": f"eq.{run_id}", "status": "eq.requested"},
            payload={"status": "running", "started_at": now, "updated_at": now},
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        if len(rows) > 1:
            _fail()
        if rows and (
            rows[0].id != run_id or rows[0].status != "running"
            or rows[0].candidate_version_id is not None or rows[0].error_summary is not None
        ):
            _fail()
        return rows[0] if rows else None

    def claim_next_sync(self, enabled_suppliers):
        suppliers = _sync_supplier_whitelist(enabled_suppliers)
        rows = self._json(
            "POST", "/rest/v1/rpc/saas_claim_next_catalog_sync",
            payload={"p_enabled_suppliers": suppliers},
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        if not isinstance(rows, list) or len(rows) > 1:
            _fail()
        if not rows:
            return None
        claim = SyncClaim.from_row(rows[0])
        if claim.supplier not in suppliers:
            _fail()
        return claim

    def recover_stale_syncs(self, enabled_suppliers):
        suppliers = _sync_supplier_whitelist(enabled_suppliers)
        value = self._json(
            "POST", "/rest/v1/rpc/saas_recover_stale_catalog_sync_runs",
            payload={"p_enabled_suppliers": suppliers},
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        if type(value) is not int or value < 0:
            _fail()
        return value

    def get_published_snapshot(self, source):
        if not isinstance(source, SourceRecord):
            raise CatalogRepositoryError("Invalid catalog source")
        if source.published_version_id is None:
            return None
        rows = self._rows(
            "GET", "saas_catalog_snapshot_versions", _SNAPSHOT_FIELDS, SnapshotRecord,
            query={"id": f"eq.{source.published_version_id}", "status": "eq.published", "limit": "2"},
            max_bytes=_MAX_SNAPSHOT_RESPONSE_BYTES,
        )
        if (
            len(rows) != 1 or rows[0].id != source.published_version_id
            or rows[0].supplier != source.supplier or rows[0].status != "published"
        ):
            _fail()
        return rows[0]

    def find_file(self, source_id, drive_item_id, e_tag):
        source_id = _input_uuid(source_id)
        drive_item_id = _input_string(drive_item_id, "drive item", 512)
        e_tag = _input_string(e_tag, "etag", 1024)
        rows = self._rows(
            "GET", "saas_catalog_source_files", _FILE_FIELDS, SourceFileRecord,
            query={"source_id": f"eq.{source_id}", "drive_item_id": f"eq.{drive_item_id}",
                   "e_tag": f"eq.{e_tag}", "is_deleted": "eq.false", "limit": "2"},
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        if len(rows) > 1:
            _fail()
        if rows and (
            rows[0].source_id != source_id or rows[0].drive_item_id != drive_item_id
            or rows[0].e_tag != e_tag or rows[0].is_deleted
        ):
            _fail()
        return rows[0] if rows else None

    def list_latest_files(self, source_id, allowed_paths):
        source_id = _input_uuid(source_id)
        paths = _allowlist(allowed_paths)
        if not paths:
            return ()
        query = {
            "source_id": f"eq.{source_id}",
            "order": "drive_item_id.asc,discovered_at.desc,id.desc",
            "select": _FILE_FIELDS,
        }
        rows = []
        seen_ids = set()
        expected_total = None
        offset = 0
        previous = None
        while expected_total is None or offset < expected_total:
            raw_rows, response_headers = self._json(
                "GET", "/rest/v1/saas_catalog_source_files", query=query,
                expected=(200, 206),
                headers={
                    "Range-Unit": "items",
                    "Range": f"{offset}-{offset + _FILE_PAGE_SIZE - 1}",
                    "Prefer": "count=exact",
                },
                max_bytes=_MAX_RESPONSE_BYTES,
                with_headers=True,
            )
            if not isinstance(raw_rows, list) or len(raw_rows) > _FILE_PAGE_SIZE:
                _fail()
            start, end, total = _content_range(response_headers, len(raw_rows))
            if total > _MAX_FILES or (expected_total is not None and total != expected_total):
                _fail()
            expected_total = total
            if not raw_rows:
                if offset != 0 or total != 0:
                    _fail()
                break
            if start != offset or end != offset + len(raw_rows) - 1:
                _fail()
            if offset + len(raw_rows) < total and len(raw_rows) != _FILE_PAGE_SIZE:
                _fail()
            page = tuple(SourceFileRecord.from_row(row) for row in raw_rows)
            for row in page:
                if row.source_id != source_id or row.id in seen_ids:
                    _fail()
                if previous is not None and not _file_follows(previous, row):
                    _fail()
                seen_ids.add(row.id)
                previous = row
            rows.extend(page)
            offset += len(page)
            if offset > total:
                _fail()
        if expected_total is None or len(rows) != expected_total:
            _fail()
        latest = {}
        for row in rows:
            if row.source_id != source_id:
                _fail()
            current = latest.get(row.drive_item_id)
            if current is None or (row.discovered_at, row.id.int) > (current.discovered_at, current.id.int):
                latest[row.drive_item_id] = row
        return tuple(
            sorted((row for row in latest.values() if not row.is_deleted and row.path in paths),
                   key=lambda row: (row.path, row.drive_item_id))
        )

    def store_raw_if_absent(self, local_path, sha256, extension, mime_type):
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise CatalogRepositoryError("Invalid catalog raw hash")
        if not isinstance(extension, str) or extension not in {"xlsx", ".xlsx", "pdf", ".pdf"}:
            raise CatalogRepositoryError("Invalid catalog raw type")
        extension = extension.removeprefix(".")
        if extension not in _MIME_BY_EXTENSION or _MIME_BY_EXTENSION[extension] != mime_type:
            raise CatalogRepositoryError("Invalid catalog raw type")
        descriptor = None
        try:
            path = os.fspath(Path(local_path))
            before = os.lstat(path)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if not _same_raw_file(before, opened):
                raise CatalogRepositoryError("Invalid catalog raw file")
            digest = hashlib.sha256()
            content = bytearray()
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, _MAX_RAW_BYTES + 1 - len(content)))
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise CatalogRepositoryError("Invalid catalog raw file")
                content.extend(chunk)
                if len(content) > _MAX_RAW_BYTES:
                    raise CatalogRepositoryError("Invalid catalog raw file")
                digest.update(chunk)
            after = os.fstat(descriptor)
            if not _same_raw_file(opened, after) or len(content) != after.st_size:
                raise CatalogRepositoryError("Invalid catalog raw file")
        except CatalogRepositoryError:
            raise
        except (OSError, TypeError, ValueError):
            raise CatalogRepositoryError("Invalid catalog raw file") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    raise CatalogRepositoryError("Invalid catalog raw file") from None
        if digest.hexdigest() != sha256:
            raise CatalogRepositoryError("Invalid catalog raw file")
        name = f"{sha256}.{extension}"
        request = Request(
            f"{self._base_url}/storage/v1/object/catalog-sources/{name}", data=bytes(content), method="POST",
            headers={"Authorization": f"Bearer {self._service_key}", "apikey": self._service_key,
                     "Content-Type": mime_type, "x-upsert": "false"},
        )
        try:
            self._open(request, (200, 201), max_bytes=_MAX_STORAGE_RESPONSE_BYTES)
        except _HTTPStatus as error:
            if error.code != 409 or not self._raw_exists(name):
                raise CatalogRepositoryError("Catalog storage request failed") from None
        return f"catalog-sources/{name}"

    def store_catalog_asset_if_absent(self, object_name, content, content_type):
        match = (
            _CATALOG_ASSET_NAME.fullmatch(object_name)
            if isinstance(object_name, str)
            else None
        )
        if (
            match is None
            or type(content) is not bytes
            or len(content) > _MAX_CATALOG_ASSET_BYTES
            or content_type != "image/png"
            or hashlib.sha256(content).hexdigest() != match.group(1)
        ):
            raise CatalogRepositoryError("Invalid catalog asset")

        request = Request(
            f"{self._base_url}/storage/v1/object/catalog-assets/{object_name}",
            data=content,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._service_key}",
                "apikey": self._service_key,
                "Content-Type": content_type,
                "x-upsert": "false",
            },
        )
        try:
            self._open(request, (200, 201), max_bytes=_MAX_STORAGE_RESPONSE_BYTES)
        except _HTTPStatus as error:
            if error.code != 409 or not self._catalog_asset_matches(object_name, match.group(1)):
                raise CatalogRepositoryError("Catalog storage request failed") from None
        return object_name

    def _catalog_asset_matches(self, object_name, expected_sha256):
        request = Request(
            f"{self._base_url}/storage/v1/object/authenticated/catalog-assets/{object_name}",
            method="GET",
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self._service_key}",
                "apikey": self._service_key,
            },
        )
        try:
            content, _ = self._open(request, (200,), max_bytes=_MAX_CATALOG_ASSET_BYTES)
        except (_HTTPStatus, CatalogRepositoryError):
            return False
        return hashlib.sha256(content).hexdigest() == expected_sha256

    def _raw_exists(self, name):
        request = Request(
            f"{self._base_url}/storage/v1/object/catalog-sources/{name}", method="HEAD",
            headers={"Authorization": f"Bearer {self._service_key}", "apikey": self._service_key},
        )
        try:
            self._open(request, (200,), max_bytes=0)
            return True
        except (_HTTPStatus, CatalogRepositoryError):
            return False

    def materialize_raw_if_present(self, source_file, destination):
        if not isinstance(source_file, SourceFileRecord) or source_file.is_deleted:
            raise CatalogRepositoryError("Invalid catalog raw file")
        if not 0 <= source_file.size_bytes <= _MAX_RAW_BYTES:
            raise CatalogRepositoryError("Invalid catalog raw file")
        try:
            destination = Path(destination)
            parent = os.lstat(destination.parent)
            if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
                raise CatalogRepositoryError("Invalid catalog raw destination")
            try:
                os.lstat(destination)
            except FileNotFoundError:
                pass
            else:
                raise CatalogRepositoryError("Invalid catalog raw destination")
        except CatalogRepositoryError:
            raise
        except (OSError, TypeError, ValueError):
            raise CatalogRepositoryError("Invalid catalog raw destination") from None

        request = Request(
            f"{self._base_url}/storage/v1/object/authenticated/{source_file.private_object_path}",
            method="GET",
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self._service_key}",
                "apikey": self._service_key,
            },
        )
        try:
            content, _ = self._open(request, (200,), max_bytes=source_file.size_bytes)
        except _HTTPStatus as error:
            if error.code == 404:
                return None
            raise CatalogRepositoryError("Catalog storage request failed") from None
        if len(content) != source_file.size_bytes or hashlib.sha256(content).hexdigest() != source_file.sha256:
            raise CatalogRepositoryError("Invalid catalog raw file")

        descriptor = None
        try:
            flags = (
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(os.fspath(destination), flags, 0o600)
            written = 0
            while written < len(content):
                count = os.write(descriptor, content[written:])
                if type(count) is not int or count <= 0:
                    raise CatalogRepositoryError("Invalid catalog raw file")
                written += count
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != source_file.size_bytes:
                raise CatalogRepositoryError("Invalid catalog raw file")
            os.lseek(descriptor, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            read_size = 0
            while read_size < source_file.size_bytes:
                chunk = os.read(descriptor, min(1024 * 1024, source_file.size_bytes - read_size))
                if not chunk:
                    raise CatalogRepositoryError("Invalid catalog raw file")
                digest.update(chunk)
                read_size += len(chunk)
            after = os.fstat(descriptor)
            if (
                not _same_raw_file(opened, after) or read_size != after.st_size
                or digest.hexdigest() != source_file.sha256
            ):
                raise CatalogRepositoryError("Invalid catalog raw file")
        except CatalogRepositoryError:
            raise
        except (OSError, TypeError, ValueError):
            raise CatalogRepositoryError("Invalid catalog raw destination") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    raise CatalogRepositoryError("Invalid catalog raw file") from None
        return DownloadedFile(destination, source_file.size_bytes, source_file.sha256)

    def record_source_file(self, source_id, item, downloaded, object_path, run_id, validation):
        source_id, run_id = _input_uuid(source_id), _input_uuid(run_id)
        if not isinstance(item, GraphItem) or item.is_folder or item.deleted is not None:
            raise CatalogRepositoryError("Invalid catalog source file")
        if not isinstance(downloaded, DownloadedFile) or not isinstance(validation, dict) or set(validation) != {"status", "summary"}:
            raise CatalogRepositoryError("Invalid catalog source file")
        status_value, summary = validation["status"], validation["summary"]
        if status_value not in {"pending", "valid", "invalid"}:
            raise CatalogRepositoryError("Invalid catalog validation")
        _object(summary)
        path = _input_path(item.path)
        e_tag = _input_string(item.e_tag, "etag", 1024)
        if type(downloaded.size) is not int or downloaded.size < 0 or item.size not in {None, downloaded.size}:
            raise CatalogRepositoryError("Invalid catalog source file")
        if not isinstance(downloaded.sha256, str) or _SHA256.fullmatch(downloaded.sha256) is None:
            raise CatalogRepositoryError("Invalid catalog source file")
        extension = PurePosixPath(path).suffix.lower().removeprefix(".")
        mime = item.mime_type or _MIME_BY_EXTENSION.get(extension)
        if extension not in _MIME_BY_EXTENSION or mime != _MIME_BY_EXTENSION[extension]:
            raise CatalogRepositoryError("Invalid catalog source file")
        if object_path != f"catalog-sources/{downloaded.sha256}.{extension}":
            raise CatalogRepositoryError("Invalid catalog source file")
        now = _iso(datetime.now(timezone.utc))
        payload = {
            "source_id": str(source_id), "drive_item_id": _input_string(item.id, "drive item", 512),
            "path": path, "e_tag": e_tag, "c_tag": item.c_tag, "size_bytes": downloaded.size,
            "sha256": downloaded.sha256, "mime_type": mime, "private_object_path": object_path,
            "validation_status": status_value, "validation_summary": summary,
            "last_sync_run_id": str(run_id), "discovered_at": now,
            "validated_at": None if status_value == "pending" else now,
        }
        rows = self._rows("POST", "saas_catalog_source_files", _FILE_FIELDS, SourceFileRecord,
                          payload=payload, expected=(201,), max_bytes=_MAX_RESPONSE_BYTES)
        row = _one(rows)
        if (
            row.source_id != source_id or row.drive_item_id != item.id or row.path != path
            or row.e_tag != e_tag or row.c_tag != item.c_tag or row.size_bytes != downloaded.size
            or row.sha256 != downloaded.sha256 or row.mime_type != mime
            or row.private_object_path != object_path or row.validation_status != status_value
            or row.validation_summary != summary or row.last_sync_run_id != run_id
            or row.is_deleted or row.deleted_at is not None or row.deleted_sync_run_id is not None
        ):
            _fail()
        return row

    def mark_file_deleted(self, source_id, drive_item_id, run_id):
        source_id, run_id = _input_uuid(source_id), _input_uuid(run_id)
        drive_item_id = _input_string(drive_item_id, "drive item", 1024)
        self._rpc_uuid("saas_mark_catalog_source_file_deleted", {
            "p_source_id": str(source_id),
            "p_drive_item_id": drive_item_id,
            "p_run_id": str(run_id),
        })

    def stage_candidate(self, run_id, snapshot, metrics, delta_link):
        run_id = _input_uuid(run_id)
        if not isinstance(snapshot, dict) or not {"supplier", "source_hash", "generated_at", "items"} <= set(snapshot):
            raise CatalogRepositoryError("Invalid catalog candidate")
        digest = snapshot["source_hash"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise CatalogRepositoryError("Invalid catalog candidate")
        if not isinstance(snapshot["items"], list) or len(snapshot["items"]) > 100_000:
            raise CatalogRepositoryError("Invalid catalog candidate")
        _input_string(snapshot["supplier"], "supplier", 64)
        generated_at = _iso(snapshot["generated_at"])
        payload = dict(snapshot)
        payload["generated_at"] = generated_at
        payload = _object(payload, maximum=_MAX_SNAPSHOT_PAYLOAD_BYTES)
        delta_link = _input_delta_link(delta_link)
        return self._rpc_uuid("saas_stage_catalog_candidate", {
            "p_run_id": str(run_id), "p_source_hash": digest,
            "p_generated_at": generated_at, "p_payload": payload,
            "p_metrics": _input_object(metrics, "metrics"),
            "p_delta_link": delta_link,
        }, max_request_bytes=_MAX_SNAPSHOT_REQUEST_BYTES)

    def finish_no_changes(self, run_id, metrics, delta_link):
        run_id = _input_uuid(run_id)
        metrics = _input_object(metrics, "metrics")
        delta_link = _input_delta_link(delta_link)
        self._rpc_uuid("saas_finish_catalog_sync_no_changes", {
            "p_run_id": str(run_id),
            "p_metrics": metrics,
            "p_delta_link": delta_link,
        }, expected_id=run_id)

    def finish_failed(self, run_id, error_code, metrics):
        run_id = _input_uuid(run_id)
        error_code = _input_string(error_code, "error code", 128)
        if re.fullmatch(r"[a-z][a-z0-9_]*", error_code) is None:
            raise CatalogRepositoryError("Invalid catalog error code")
        now = _iso(datetime.now(timezone.utc))
        rows = self._rows(
            "PATCH", "saas_catalog_sync_runs", _RUN_FIELDS, RunRecord,
            query={"id": f"eq.{run_id}", "status": "eq.running"},
            payload={"status": "failed", "error_summary": error_code,
                     "metrics": _input_object(metrics, "metrics"), "finished_at": now, "updated_at": now},
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        row = _one(rows)
        if (
            row.id != run_id or row.status != "failed" or row.error_summary != error_code
            or row.metrics != metrics or row.candidate_version_id is not None
        ):
            _fail()

    def auto_publish_candidate(self, candidate_id):
        candidate_id = _input_uuid(candidate_id)
        return self._rpc_uuid("saas_auto_publish_catalog_snapshot",
                              {"p_candidate_id": str(candidate_id)}, expected_id=candidate_id)

    def publish_candidate(self, candidate_id, reviewed_by, note=None):
        candidate_id = _input_uuid(candidate_id)
        if type(reviewed_by) is not int or reviewed_by < 1:
            raise CatalogRepositoryError("Invalid catalog reviewer")
        if note is not None:
            note = _input_string(note, "review note", 2000)
        return self._rpc_uuid("saas_publish_catalog_snapshot", {
            "p_candidate_id": str(candidate_id), "p_reviewed_by": reviewed_by, "p_review_note": note,
        }, expected_id=candidate_id)

    def _rpc_uuid(self, name, payload, *, expected_id=None,
                  max_request_bytes=_MAX_REQUEST_BYTES):
        value = self._json("POST", f"/rest/v1/rpc/{name}", payload=payload,
                           max_bytes=_MAX_RESPONSE_BYTES,
                           max_request_bytes=max_request_bytes)
        value = _uuid(value)
        if expected_id is not None and value != expected_id:
            _fail()
        return value

    def insert_rates_if_absent(self, rates):
        if not isinstance(rates, tuple) or not 0 < len(rates) <= _MAX_RATES:
            raise CatalogRepositoryError("Invalid catalog rate batch")
        rows = []
        seen = set()
        for rate in rates:
            if not isinstance(rate, ExchangeRate):
                raise CatalogRepositoryError("Invalid catalog rate batch")
            expected_series = {"USD": "SF43718", "EUR": "SF46410"}.get(rate.currency)
            key = (rate.currency, rate.effective_date)
            if (
                type(rate.effective_date) is not date or expected_series != rate.series_id
                or rate.source != "BANXICO_SIE" or key in seen
                or not isinstance(rate.mxn_per_unit, Decimal) or not rate.mxn_per_unit.is_finite()
                or rate.mxn_per_unit <= 0 or not isinstance(rate.raw_hash, str)
                or _SHA256.fullmatch(rate.raw_hash) is None
            ):
                raise CatalogRepositoryError("Invalid catalog rate batch")
            try:
                decimal_text = format(rate.mxn_per_unit.quantize(Decimal("0.000001")), "f")
            except InvalidOperation:
                raise CatalogRepositoryError("Invalid catalog rate batch") from None
            if Decimal(decimal_text) != rate.mxn_per_unit or re.fullmatch(r"(0|[1-9][0-9]{0,11})\.[0-9]{6}", decimal_text) is None:
                raise CatalogRepositoryError("Invalid catalog rate batch")
            seen.add(key)
            rows.append({
                "currency": rate.currency, "effective_date": rate.effective_date.isoformat(),
                "mxn_per_unit": decimal_text, "series_id": rate.series_id, "source": rate.source,
                "retrieved_at": _iso(rate.retrieved_at), "raw_hash": rate.raw_hash,
            })
        result = self._json("POST", "/rest/v1/rpc/saas_insert_exchange_rates_if_absent",
                            payload={"p_rates": rows}, max_bytes=_MAX_RESPONSE_BYTES)
        if type(result) is not int or not 0 <= result <= len(rows):
            _fail()
        return result


def _one(rows):
    if len(rows) != 1:
        _fail()
    return rows[0]


def _sync_supplier_whitelist(values):
    if (
        not isinstance(values, tuple)
        or not 0 < len(values) <= len(_SYNC_SUPPLIERS)
        or len(set(values)) != len(values)
        or any(value not in _SYNC_SUPPLIERS for value in values)
    ):
        raise CatalogRepositoryError("Invalid catalog supplier whitelist")
    return list(values)


def _input_uuid(value):
    if not isinstance(value, UUID):
        raise CatalogRepositoryError("Invalid catalog identifier")
    return value


def _input_string(value, name, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise CatalogRepositoryError(f"Invalid catalog {name}")
    return value


def _input_object(value, name):
    try:
        return _object(value)
    except CatalogRepositoryError:
        raise CatalogRepositoryError(f"Invalid catalog {name}") from None


def _input_delta_link(value):
    value = _input_string(value, "delta token", 8192)
    if not value.strip():
        raise CatalogRepositoryError("Invalid catalog delta token")
    return value


def _input_path(value):
    value = _input_string(value, "path", 2048)
    windows = PureWindowsPath(value)
    parts = value.replace("\\", "/").split("/")
    if PurePosixPath(value).is_absolute() or windows.drive or windows.root or "\\" in value or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise CatalogRepositoryError("Invalid catalog path")
    return value


def _allowlist(values):
    if not isinstance(values, tuple) or len(values) > _MAX_FILES:
        raise CatalogRepositoryError("Invalid catalog path allowlist")
    try:
        paths = tuple(_input_path(value) for value in values)
    except CatalogRepositoryError:
        raise CatalogRepositoryError("Invalid catalog path allowlist") from None
    if len(set(paths)) != len(paths):
        raise CatalogRepositoryError("Invalid catalog path allowlist")
    return frozenset(paths)


def _content_range(headers, row_count):
    value = headers.get("content-range")
    if value == "*/0" and row_count == 0:
        return 0, -1, 0
    if not isinstance(value, str):
        _fail()
    match = _CONTENT_RANGE.fullmatch(value)
    if match is None:
        _fail()
    start, end, total = (int(part) for part in match.groups())
    if start > end or end >= total or end - start + 1 != row_count:
        _fail()
    return start, end, total


def _file_follows(previous, current):
    if previous.drive_item_id != current.drive_item_id:
        return previous.drive_item_id < current.drive_item_id
    if previous.discovered_at != current.discovered_at:
        return previous.discovered_at > current.discovered_at
    return previous.id.int > current.id.int


def _same_raw_file(left, right):
    return (
        stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
        and 0 <= left.st_size <= _MAX_RAW_BYTES
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )
