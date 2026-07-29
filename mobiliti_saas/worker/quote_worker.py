"""
Worker para procesar cotizaciones web.

Default/final: QUOTE_ENGINE=python, sin Microsoft Excel.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from datetime import timedelta
from io import BytesIO
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


BUCKET = os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files")
STORAGE_PROVIDER = os.environ.get("QUOTE_STORAGE_PROVIDER", "supabase").strip().lower()
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "").strip().rstrip("/")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.environ.get("R2_BUCKET", BUCKET).strip() or BUCKET
R2_REGION = os.environ.get("R2_REGION", "auto").strip() or "auto"
POLL_SECONDS = int(os.environ.get("WORKER_POLL_SECONDS", "10"))
STALE_MINUTES = int(os.environ.get("WORKER_STALE_MINUTES", "30"))
WORKER_LEASE_SECONDS = max(60, STALE_MINUTES * 60 if STALE_MINUTES > 0 else 1800)
WORKER_HEARTBEAT_SECONDS = max(
    1.0,
    float(os.environ.get("WORKER_HEARTBEAT_SECONDS", str(min(60, WORKER_LEASE_SECONDS / 3)))),
)
MAX_QUOTE_OUTPUT_MB = int(os.environ.get("MAX_QUOTE_OUTPUT_MB", "100"))
MAX_IMPORTED_SOURCE_BYTES = 25 * 1024 * 1024
QUOTE_ENGINE = os.environ.get("QUOTE_ENGINE", "python").strip().lower()
DATABASE_URL = os.environ.get("DATABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")
MOBILITI_REST_SECRET = os.environ.get("MOBILITI_REST_SECRET")
MOBILITI_API_URL = os.environ.get("MOBILITI_API_URL", "https://web-lemon-one-45.vercel.app").strip().rstrip("/")
DEV_MODE = os.environ.get("MOBILITI_DEV_MODE", "").lower() in {"1", "true", "yes"}
TARKETT_CART_SOURCE_TYPE = "tarkett_cart"
OFFIHO_CART_SOURCE_TYPE = "offiho_cart"
SUPPLIER_CART_SOURCE_TYPE = "supplier_cart"
MIXED_CATALOG_CART_SOURCE_TYPE = "mixed_catalog_cart"
JSON_CART_SOURCE_TYPES = frozenset(
    {
        TARKETT_CART_SOURCE_TYPE,
        OFFIHO_CART_SOURCE_TYPE,
        SUPPLIER_CART_SOURCE_TYPE,
        MIXED_CATALOG_CART_SOURCE_TYPE,
    }
)
SUPPLIER_LABELS = {
    "cr-global": "CR Global",
    "sonara": "Sonara",
    "sunon": "Sunon",
    "alma": "ALMA",
    "lumbro": "Lumbro",
    "jome": "JOME",
    "lauco": "Lauco",
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEV_STORE_DIR = Path(os.environ.get("MOBILITI_DEV_STORE_DIR", PROJECT_ROOT / ".mobiliti_dev_store")).resolve()
TARKETT_SYNC_ENABLED = os.environ.get("TARKETT_SYNC_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
TARKETT_SYNC_INTERVAL_SECONDS = max(900, int(os.environ.get("TARKETT_SYNC_INTERVAL_SECONDS", "21600")))
TARKETTNET_EMAIL = os.environ.get("TARKETTNET_EMAIL", "").strip()
TARKETTNET_PASSWORD = os.environ.get("TARKETTNET_PASSWORD", "")
TARKETT_CATALOG_FALLBACK_PATH = PROJECT_ROOT / "mobiliti_saas" / "quote_engine" / "data" / "tarkett_catalog.json"
_TARKETT_LAST_SYNC_ATTEMPT = 0.0

from mobiliti_saas.quote_engine.tarkettnet_catalog import sync_catalog_from_tarkettnet  # noqa: E402
from mobiliti_saas.quote_engine.quotation_sheets import (  # noqa: E402
    QuotationDataRow,
    quotation_data_rows,
)


@dataclass(frozen=True)
class PreparedGeneratorInput:
    """Fuentes validadas que el worker entrega al compositor oficial."""

    parser_source: Path
    original_quotation: Path | None
    quotation_data: tuple[QuotationDataRow, ...]

    @property
    def name(self) -> str:
        """Compatibilidad de lectura para callers antiguos que recibían un Path."""

        return self.parser_source.name

    def is_file(self) -> bool:
        return self.parser_source.is_file()

    def __fspath__(self) -> str:
        return os.fspath(self.parser_source)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return value


def _format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def _validate_output_size(source: Path) -> None:
    max_bytes = MAX_QUOTE_OUTPUT_MB * 1024 * 1024
    size = source.stat().st_size
    if size > max_bytes:
        raise RuntimeError(
            "Cotizacion generada pesa "
            f"{_format_size_mb(size)} y supera el limite de Storage de {MAX_QUOTE_OUTPUT_MB} MB"
        )


_R2_CLIENT = None


def _use_r2_storage() -> bool:
    return STORAGE_PROVIDER in {"r2", "cloudflare-r2", "cloudflare"}


def _provider_uses_r2(provider: str | None) -> bool:
    return str(provider or "").strip().lower() in {"r2", "cloudflare-r2", "cloudflare"}


def _r2_endpoint_url() -> str:
    if R2_ENDPOINT_URL:
        return R2_ENDPOINT_URL
    if R2_ACCOUNT_ID:
        return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return ""


def _r2_configured() -> bool:
    return bool(_r2_endpoint_url() and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET)


def _r2_client():
    global _R2_CLIENT
    if _R2_CLIENT is not None:
        return _R2_CLIENT
    if not _r2_configured():
        raise RuntimeError(
            "Cloudflare R2 no configurado: define R2_ACCOUNT_ID/R2_ENDPOINT_URL, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY y R2_BUCKET"
        )
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("Falta dependencia boto3 para Cloudflare R2") from exc
    _R2_CLIENT = boto3.client(
        "s3",
        endpoint_url=_r2_endpoint_url(),
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name=R2_REGION,
        config=Config(signature_version="s3v4"),
    )
    return _R2_CLIENT


def _quote_object_content_type(path: str) -> str:
    return (
        "application/pdf"
        if str(path or "").lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


class SupabaseClient:
    def __init__(self) -> None:
        self.base_url = _required_env("SUPABASE_URL").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_ANON_KEY
        if not self.key:
            raise RuntimeError("Falta SUPABASE_SERVICE_KEY o SUPABASE_ANON_KEY")

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": content_type,
            "Prefer": "return=representation",
            **({"x-mobiliti-rest-secret": MOBILITI_REST_SECRET} if MOBILITI_REST_SECRET else {}),
        }

    def rest(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        url = f"{self.base_url}/rest/v1{path}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=payload, method=method)
        for key, value in self._headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Supabase REST {exc.code}: {body}") from exc

    def catalog_snapshot_get(self, supplier: str) -> dict | None:
        if not os.environ.get("SUPABASE_SERVICE_KEY") and MOBILITI_REST_SECRET:
            return self._catalog_api_request("GET")
        rows = self.rest(
            "GET",
            "/saas_supplier_catalog_snapshots",
            params={
                "supplier": f"eq.{supplier}",
                "select": "supplier,source_hash,generated_at,payload,updated_at",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def catalog_snapshot_upsert(self, supplier: str, payload: dict) -> dict:
        if not os.environ.get("SUPABASE_SERVICE_KEY") and MOBILITI_REST_SECRET:
            return self._catalog_api_request("PUT", {"payload": payload})
        url = f"{self.base_url}/rest/v1/saas_supplier_catalog_snapshots?on_conflict=supplier"
        row = {
            "supplier": supplier,
            "source_hash": payload["source_hash"],
            "generated_at": payload["generated_at"],
            "payload": payload,
            "updated_at": _utc_now(),
        }
        req = urllib.request.Request(url, data=json.dumps(row).encode("utf-8"), method="POST")
        for key, value in self._headers().items():
            req.add_header(key, value)
        req.add_header("Prefer", "resolution=merge-duplicates,return=representation")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                rows = json.loads(body) if body else []
                return rows[0] if isinstance(rows, list) and rows else row
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Supabase catalog snapshot {exc.code}: {body}") from exc

    def _catalog_api_request(self, method: str, payload: dict | None = None) -> dict | None:
        parsed = urllib.parse.urlparse(MOBILITI_API_URL)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("MOBILITI_API_URL invalida")
        url = f"{MOBILITI_API_URL}/internal/catalogs/tarkett"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("x-mobiliti-rest-secret", MOBILITI_REST_SECRET or "")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = resp.read().decode("utf-8")
                return json.loads(content) if content else None
        except urllib.error.HTTPError as exc:
            exc.read()
            raise RuntimeError(f"Mobiliti catalog API {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Mobiliti catalog API connection error: {exc.reason}") from exc

    def storage_download(self, object_path: str, dest: Path) -> None:
        self.storage_download_from_provider(object_path, dest, STORAGE_PROVIDER)

    def storage_download_from_provider(self, object_path: str, dest: Path, provider: str | None) -> None:
        if _provider_uses_r2(provider):
            try:
                obj = _r2_client().get_object(Bucket=R2_BUCKET, Key=str(object_path).strip("/"))
                dest.write_bytes(obj["Body"].read())
            except Exception as exc:
                raise RuntimeError(f"R2 download error: {exc.__class__.__name__}") from exc
            return

        encoded = urllib.parse.quote(object_path, safe="/")
        url = f"{self.base_url}/storage/v1/object/{BUCKET}/{encoded}"
        req = urllib.request.Request(url, method="GET")
        for key, value in self._headers("application/octet-stream").items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                dest.write_bytes(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Storage download {exc.code}: {body}") from exc

    def storage_upload(self, object_path: str, source: Path) -> None:
        if _use_r2_storage():
            try:
                _r2_client().put_object(
                    Bucket=R2_BUCKET,
                    Key=str(object_path).strip("/"),
                    Body=source.read_bytes(),
                    ContentType=_quote_object_content_type(object_path),
                )
            except Exception as exc:
                raise RuntimeError(f"R2 upload error: {exc.__class__.__name__}") from exc
            return

        encoded = urllib.parse.quote(object_path, safe="/")
        url = f"{self.base_url}/storage/v1/object/{BUCKET}/{encoded}"
        req = urllib.request.Request(url, data=source.read_bytes(), method="PUT")
        for key, value in self._headers("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").items():
            req.add_header(key, value)
        req.add_header("x-upsert", "true")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Storage upload {exc.code}: {body}") from exc

    def storage_delete(self, object_path: str) -> None:
        self.storage_delete_from_provider(object_path, STORAGE_PROVIDER)

    def storage_delete_from_provider(self, object_path: str, provider: str | None) -> None:
        clean_path = str(object_path or "").strip().lstrip("/")
        if not clean_path:
            return
        if _provider_uses_r2(provider):
            try:
                _r2_client().delete_object(Bucket=R2_BUCKET, Key=clean_path)
            except Exception as exc:
                raise RuntimeError(f"R2 delete error: {exc.__class__.__name__}") from exc
            return

        url = f"{self.base_url}/storage/v1/object/{BUCKET}"
        req = urllib.request.Request(url, data=json.dumps({"prefixes": [clean_path]}).encode("utf-8"), method="DELETE")
        for key, value in self._headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Storage delete {exc.code}: {body}") from exc


class PostgresClient(SupabaseClient):
    def __init__(self) -> None:
        super().__init__()
        self.db_url = _required_env("DATABASE_URL")

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [_jsonable_row(row) for row in rows]

    def _write(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
        adapted = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, adapted)
                rows = cur.fetchall()
            conn.commit()
        return [_jsonable_row(row) for row in rows]

    def rest(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        params = params or {}
        data = data or {}
        if method == "GET" and path == "/saas_quote_jobs":
            limit = int(params.get("limit", "1") or 1)
            where = []
            values = []
            status_filter = params.get("status")
            if status_filter:
                where.append("status = %s")
                values.append(str(status_filter).split(".", 1)[1])
            id_filter = params.get("id")
            if id_filter:
                where.append("id = %s")
                values.append(str(id_filter).split(".", 1)[1])
            where_sql = " WHERE " + " AND ".join(where) if where else ""
            return self._rows(
                f"SELECT * FROM saas_quote_jobs{where_sql} ORDER BY created_at ASC LIMIT %s",
                tuple(values) + (limit,),
            )

        if method == "PATCH" and path == "/saas_quote_jobs":
            status = str(params.get("status", "eq.processing")).split(".", 1)[1]
            updated_raw = str(params.get("updated_at", ""))
            if not updated_raw.startswith("lt."):
                raise RuntimeError("PATCH saas_quote_jobs requiere updated_at lt.")
            cutoff = updated_raw.split(".", 1)[1]
            return self._update_jobs(
                data,
                "status = %s AND updated_at < %s",
                (status, cutoff),
            )

        if method == "PATCH" and path.startswith("/saas_quote_jobs?id=eq."):
            filters = _parse_rest_filters(path)
            where = []
            values = []
            for key, operator, value in filters:
                if key not in {"id", "status", "attempt_token", "lease_expires_at", "updated_at"}:
                    raise RuntimeError(f"Filtro Postgres no soportado: {key}")
                if operator == "eq":
                    where.append(f"{key} = %s")
                    values.append(value)
                elif operator == "lt":
                    where.append(f"{key} < %s")
                    values.append(value)
                elif operator == "null":
                    where.append(f"{key} IS NULL")
                else:
                    raise RuntimeError(f"Operador Postgres no soportado: {operator}")
            if not any(key == "id" for key, _operator, _value in filters):
                raise RuntimeError("PATCH saas_quote_jobs requiere id")
            return self._update_jobs(data, " AND ".join(where), tuple(values))

        raise RuntimeError(f"Operacion Postgres no soportada: {method} {path}")

    def catalog_snapshot_get(self, supplier: str) -> dict | None:
        rows = self._rows(
            """
            SELECT supplier, source_hash, generated_at, payload, updated_at
            FROM saas_supplier_catalog_snapshots
            WHERE supplier = %s
            LIMIT 1
            """,
            (supplier,),
        )
        return rows[0] if rows else None

    def catalog_snapshot_upsert(self, supplier: str, payload: dict) -> dict:
        rows = self._write(
            """
            INSERT INTO saas_supplier_catalog_snapshots
                (supplier, source_hash, generated_at, payload, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (supplier) DO UPDATE SET
                source_hash = EXCLUDED.source_hash,
                generated_at = EXCLUDED.generated_at,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING supplier, source_hash, generated_at, payload, updated_at
            """,
            (supplier, payload["source_hash"], payload["generated_at"], payload),
        )
        return rows[0]

    def _update_jobs(self, data: dict, where_sql: str, where_params: tuple) -> list[dict]:
        payload = dict(data)
        if not payload:
            return []
        set_clause = ", ".join(f"{key} = %s" for key in payload.keys())
        return self._write(
            f"UPDATE saas_quote_jobs SET {set_clause} WHERE {where_sql} RETURNING *",
            tuple(payload.values()) + where_params,
        )


def _jsonable_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    clean = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            clean[key] = value.isoformat()
        else:
            clean[key] = value
    return clean


def _parse_rest_filters(path: str) -> list[tuple[str, str, str | None]]:
    query = path.split("?", 1)[1] if "?" in path else ""
    filters: list[tuple[str, str, str | None]] = []
    for part in query.split("&"):
        if "=eq." in part:
            key, value = part.split("=eq.", 1)
            filters.append((key, "eq", urllib.parse.unquote(value)))
        elif "=lt." in part:
            key, value = part.split("=lt.", 1)
            filters.append((key, "lt", urllib.parse.unquote(value)))
        elif part.endswith("=is.null"):
            filters.append((part[:-8], "null", None))
    return filters


_LOCAL_DEV_STORE_LOCK = threading.RLock()


def _write_json_snapshot(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


def _read_json_snapshot(path: Path) -> dict:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.01)
    assert last_error is not None
    raise last_error


class LocalDevClient:
    def __init__(self) -> None:
        self.db_path = DEV_STORE_DIR / "db.json"
        self.storage_root = DEV_STORE_DIR / "storage" / BUCKET

    def _load(self) -> dict:
        if not self.db_path.exists():
            raise RuntimeError("Store dev no existe. Inicia backend con MOBILITI_DEV_MODE=1 y haz login/upload primero.")
        return _read_json_snapshot(self.db_path)

    def _save(self, data: dict) -> None:
        _write_json_snapshot(self.db_path, data)

    def rest(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        with _LOCAL_DEV_STORE_LOCK:
            return self._rest_locked(method, path, params=params, data=data)

    def _rest_locked(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        store = self._load()
        if path == "/saas_quote_jobs" and method == "GET":
            rows = list(store.get("quote_jobs", []))
            status_filter = (params or {}).get("status")
            if isinstance(status_filter, str) and status_filter.startswith("eq."):
                wanted = status_filter.split(".", 1)[1]
                rows = [row for row in rows if row.get("status") == wanted]
            id_filter = (params or {}).get("id")
            if isinstance(id_filter, str) and id_filter.startswith("eq."):
                wanted_id = id_filter.split(".", 1)[1]
                rows = [row for row in rows if str(row.get("id")) == wanted_id]
            rows.sort(key=lambda row: row.get("created_at", ""))
            limit = int((params or {}).get("limit", len(rows)) or len(rows))
            return rows[:limit]

        if path == "/saas_quote_jobs" and method == "PATCH":
            params = params or {}
            rows = []
            for row in store.get("quote_jobs", []):
                if params.get("status") and row.get("status") != params["status"].split(".", 1)[1]:
                    continue
                updated_filter = params.get("updated_at")
                if isinstance(updated_filter, str) and updated_filter.startswith("lt."):
                    cutoff = datetime.fromisoformat(updated_filter.split(".", 1)[1])
                    updated_at = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))
                    if updated_at >= cutoff:
                        continue
                row.update(data or {})
                rows.append(dict(row))
            if rows:
                self._save(store)
            return rows

        if path.startswith("/saas_quote_jobs?id=eq.") and method == "PATCH":
            filters = _parse_rest_filters(path)
            for row in store.get("quote_jobs", []):
                matches = True
                for key, operator, value in filters:
                    current = row.get(key)
                    if operator == "eq" and str(current) != str(value):
                        matches = False
                    elif operator == "null" and current is not None:
                        matches = False
                    elif operator == "lt":
                        if current is None or datetime.fromisoformat(str(current).replace("Z", "+00:00")) >= datetime.fromisoformat(str(value).replace("Z", "+00:00")):
                            matches = False
                if matches:
                    row.update(data or {})
                    self._save(store)
                    return [dict(row)]
            return []

        raise RuntimeError(f"Operacion dev no soportada: {method} {path}")

    def catalog_snapshot_get(self, supplier: str) -> dict | None:
        return self._load().get("supplier_catalog_snapshots", {}).get(supplier)

    def catalog_snapshot_upsert(self, supplier: str, payload: dict) -> dict:
        store = self._load()
        row = {
            "supplier": supplier,
            "source_hash": payload["source_hash"],
            "generated_at": payload["generated_at"],
            "payload": payload,
            "updated_at": _utc_now(),
        }
        store.setdefault("supplier_catalog_snapshots", {})[supplier] = row
        self._save(store)
        return row

    def _storage_file(self, object_path: str) -> Path:
        safe_path = object_path.replace("\\", "/").lstrip("/")
        if ".." in safe_path.split("/"):
            raise RuntimeError("Ruta de storage invalida")
        return self.storage_root / safe_path

    def storage_download(self, object_path: str, dest: Path) -> None:
        source = self._storage_file(object_path)
        if not source.exists():
            raise RuntimeError(f"Archivo no existe en storage dev: {object_path}")
        dest.write_bytes(source.read_bytes())

    def storage_download_from_provider(self, object_path: str, dest: Path, provider: str | None) -> None:
        self.storage_download(object_path, dest)

    def storage_upload(self, object_path: str, source: Path) -> None:
        dest = self._storage_file(object_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())

    def storage_delete(self, object_path: str) -> None:
        self._storage_file(object_path).unlink(missing_ok=True)

    def storage_delete_from_provider(self, object_path: str, provider: str | None) -> None:
        self.storage_delete(object_path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=WORKER_LEASE_SECONDS)).isoformat()


def _resolve_project_root() -> Path:
    history_dir = PROJECT_ROOT / "versiones historial" / "HISTORIAL DE VERSIONES" / "Mobiliti_Generador_Windows"
    return history_dir if history_dir.exists() else PROJECT_ROOT


def _default_template() -> Path:
    template = (
        PROJECT_ROOT
        / "mobiliti_saas"
        / "worker"
        / "templates"
        / "Formato Cotizacion 2026 Oficial.xlsx"
    )
    if not template.is_file():
        raise FileNotFoundError(f"Plantilla oficial no disponible: {template}")
    return template


def _template_path() -> str:
    return os.environ.get("TEMPLATE_PATH") or str(_default_template())


def _json_job_source_type(job: dict) -> str | None:
    metadata = job.get("metadata") or {}
    value = metadata.get("source_type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _has_supported_json_cart_source_type(job: dict) -> bool:
    return _json_job_source_type(job) in JSON_CART_SOURCE_TYPES


def _has_json_input_hint(job: dict) -> bool:
    input_path = str(job.get("input_path") or "").lower()
    metadata = job.get("metadata") or {}
    original_filename = str(metadata.get("original_filename") or "").lower()
    metadata_extension = str(metadata.get("input_extension") or "").lower()
    return input_path.endswith(".json") or original_filename.endswith(".json") or metadata_extension == ".json"


def _input_extension_for_job(job: dict) -> str:
    input_path = str(job.get("input_path") or "").lower()
    metadata = job.get("metadata") or {}
    original_filename = str(metadata.get("original_filename") or "").lower()
    if _has_json_input_hint(job) or _has_supported_json_cart_source_type(job):
        return ".json"
    if input_path.endswith(".pdf") or original_filename.endswith(".pdf"):
        return ".pdf"
    return ".xlsx"


def _convert_pdf_to_quotation(source_pdf: Path, output_xlsx: Path, reference_xlsx: str | Path) -> None:
    from pdf_quotation_import import convert_pdf_to_quotation

    convert_pdf_to_quotation(source_pdf, output_xlsx, reference_xlsx=reference_xlsx)


def _convert_tarkett_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.tarkett_catalog import create_tarkett_quotation_workbook

    create_tarkett_quotation_workbook(payload, output_xlsx)


def _convert_offiho_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.offiho_catalog import create_offiho_quotation_workbook

    create_offiho_quotation_workbook(payload, output_xlsx)


def _convert_supplier_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.supplier_catalog import create_supplier_quotation_workbook

    create_supplier_quotation_workbook(payload, output_xlsx)


def _convert_mixed_catalog_cart_to_quotation(
    source_json: Path,
    output_xlsx: Path,
    payload: dict,
    *,
    imported_source_path: Path | None = None,
) -> None:
    from mobiliti_saas.quote_engine.mixed_catalog import create_mixed_catalog_quotation_workbook

    create_mixed_catalog_quotation_workbook(
        payload,
        output_xlsx,
        imported_source_path=imported_source_path,
    )


def _path_is_symlink_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _safe_tmp_target(tmp_dir: Path, filename: str) -> Path:
    root = Path(tmp_dir)
    if Path(filename).name != filename or not filename:
        raise RuntimeError("Nombre temporal invalido")
    try:
        root_metadata = os.lstat(root)
    except OSError as exc:
        raise RuntimeError("Directorio temporal no disponible") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or _path_is_symlink_or_reparse(root):
        raise RuntimeError("Directorio temporal inseguro")
    try:
        resolved_root = root.resolve(strict=True)
        if resolved_root != root.absolute():
            raise RuntimeError("Directorio temporal inseguro")
    except OSError as exc:
        raise RuntimeError("Directorio temporal inseguro") from exc
    target = root / filename
    if target.parent.resolve(strict=True) != resolved_root:
        raise RuntimeError("Ruta temporal fuera del directorio permitido")
    try:
        os.lstat(target)
    except FileNotFoundError:
        return target
    except OSError as exc:
        raise RuntimeError("No se pudo validar la ruta temporal") from exc
    raise RuntimeError("La ruta temporal de salida ya existe")


def _read_regular_file_once(path: Path, *, max_bytes: int) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("No se pudo leer la fuente importada descargada") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or _path_is_symlink_or_reparse(path)
        or before.st_size <= 0
        or before.st_size > max_bytes
    ):
        raise RuntimeError("La fuente importada excede el limite permitido o es insegura")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("No se pudo abrir la fuente importada descargada") from exc
    chunks: list[bytes] = []
    total = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeError("La fuente importada cambio durante su validacion")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError("La fuente importada excede el limite permitido")
    finally:
        os.close(descriptor)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise RuntimeError("La fuente importada cambio durante su validacion") from exc
    if (
        _path_is_symlink_or_reparse(path)
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
        or total != after.st_size
    ):
        raise RuntimeError("La fuente importada cambio durante su validacion")
    return b"".join(chunks)


def _validate_xlsx_mime(source_bytes: bytes) -> None:
    if not source_bytes.startswith(b"PK") or not zipfile.is_zipfile(BytesIO(source_bytes)):
        raise RuntimeError("La fuente importada no es un XLSX valido")
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as package:
            names = set(package.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError("La fuente importada no es un XLSX valido") from exc
    if not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
        raise RuntimeError("La fuente importada no coincide con el MIME XLSX")


def _validate_mixed_job_provenance(job: dict, payload: dict) -> None:
    metadata = job.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise RuntimeError("Metadata de job invalida")
    expected_values = {
        "mixed_item_count": payload["item_count"],
        "mixed_section_count": len(payload["sections"]),
        "catalog_item_counts": {
            group["catalog"]: len(group["items"]) for group in payload["groups"]
        },
        "catalog_source_hashes": {
            group["catalog"]: group["catalog_source_hash"]
            for group in payload["groups"]
        },
        "quote_currency": payload["quote_currency"],
    }
    for key, expected in expected_values.items():
        if key in metadata and metadata[key] != expected:
            raise RuntimeError(f"{key} de metadata no coincide con JSON de entrada")

    imported = payload["imported_source"]
    if imported is None:
        if any(
            key in metadata
            for key in ("import_source", "import_item_count", "import_source_path")
        ):
            raise RuntimeError("Metadata importada inesperada para carrito de catalogo")
        return
    expected_import = {
        "import_id": imported["import_id"],
        "original_filename": imported["original_filename"],
        "source_hash": imported["source_hash"],
    }
    if "import_source" in metadata and metadata["import_source"] != expected_import:
        raise RuntimeError("Procedencia importada de metadata no coincide con JSON")
    if (
        "import_item_count" in metadata
        and metadata["import_item_count"] != len(imported["items"])
    ):
        raise RuntimeError("Conteo importado de metadata no coincide con JSON")
    expected_path = imported.get("storage_path", imported.get("source_path"))
    if "import_source_path" in metadata and metadata["import_source_path"] != expected_path:
        raise RuntimeError("Ruta importada de metadata no coincide con JSON")


def _download_imported_source(
    client,
    payload: dict,
    tmp_dir: Path,
    *,
    job: dict | None = None,
) -> Path | None:
    from mobiliti_saas.quote_engine.mixed_catalog import validate_mixed_catalog_payload

    checked = validate_mixed_catalog_payload(payload)
    imported = checked.get("imported_source")
    if imported is None:
        return None
    storage_path = imported.get("storage_path", imported.get("source_path"))
    if not isinstance(storage_path, str) or not storage_path:
        raise RuntimeError("Fuente importada sin ruta de storage validada")
    storage_provider = imported.get("storage_provider")
    if storage_provider is None and job is not None:
        storage_provider = _job_input_storage_provider(job)
    if storage_provider not in {"supabase", "r2", "cloudflare-r2", "cloudflare"}:
        raise RuntimeError("Fuente importada sin proveedor de storage validado")
    if job is None:
        raise RuntimeError("Job requerido para validar la fuente importada")
    expected_storage_path = (
        f"users/{job.get('usuario_id')}/jobs/{job.get('id')}/import-source.xlsx"
    )
    if storage_path != expected_storage_path:
        raise RuntimeError("Ruta de fuente importada no corresponde al job")
    if storage_provider != _job_input_storage_provider(job):
        raise RuntimeError("Proveedor de fuente importada no corresponde al job")

    target = _safe_tmp_target(tmp_dir, "import-source.xlsx")
    client.storage_download_from_provider(
        storage_path,
        target,
        storage_provider,
    )
    if target.parent.resolve(strict=True) != Path(tmp_dir).resolve(strict=True):
        raise RuntimeError("Ruta de fuente importada fuera del temporal")
    source_bytes = _read_regular_file_once(
        target,
        max_bytes=MAX_IMPORTED_SOURCE_BYTES,
    )
    if not hmac.compare_digest(
        hashlib.sha256(source_bytes).hexdigest(),
        imported["source_hash"],
    ):
        raise RuntimeError("La fuente importada cambio despues de validarse")
    _validate_xlsx_mime(source_bytes)
    return target


def _read_cart_payload(source_json: Path) -> dict:
    try:
        payload = json.loads(source_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("JSON de entrada invalido") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("JSON de entrada debe ser un objeto")
    return payload


def _is_json_cart_job(job: dict) -> bool:
    metadata = job.get("metadata") or {}
    return _has_json_input_hint(job) or _has_supported_json_cart_source_type(job) or "source_type" in metadata


def _validate_local_input_file(job: dict, local_input: Path, input_extension: str) -> None:
    try:
        source_stat = os.lstat(local_input)
    except OSError as exc:
        raise RuntimeError("Archivo de entrada local no disponible") from exc
    if not stat.S_ISREG(source_stat.st_mode) or _path_is_symlink_or_reparse(local_input):
        raise RuntimeError("Archivo de entrada local inseguro")
    if source_stat.st_size <= 0 or source_stat.st_size > MAX_IMPORTED_SOURCE_BYTES:
        raise RuntimeError("Archivo de entrada fuera del limite permitido")
    metadata = job.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise RuntimeError("Metadata de job invalida")
    expected_size = metadata.get("file_size")
    if expected_size is not None and (
        type(expected_size) is not int or expected_size != source_stat.st_size
    ):
        raise RuntimeError("Tamano de entrada no coincide con metadata")
    if input_extension == ".xlsx" and expected_size is not None:
        source_bytes = _read_regular_file_once(
            local_input,
            max_bytes=MAX_IMPORTED_SOURCE_BYTES,
        )
        _validate_xlsx_mime(source_bytes)
    if input_extension == ".pdf" and expected_size is not None:
        with local_input.open("rb") as source_file:
            if source_file.read(5) != b"%PDF-":
                raise RuntimeError("El archivo de entrada no coincide con el MIME PDF")


def convert_validated_payload(
    source_type: str,
    payload: dict,
    local_input: Path,
    tmp_dir: Path,
    imported_source_path: Path | None,
) -> Path:
    """Convierte una sola vez un payload ya validado a la fuente del parser."""

    if not isinstance(payload, dict) or payload.get("source_type") != source_type:
        raise RuntimeError("source_type validado no coincide con payload")
    conversions = {
        TARKETT_CART_SOURCE_TYPE: (
            "quotation_from_tarkett.xlsx",
            _convert_tarkett_cart_to_quotation,
        ),
        OFFIHO_CART_SOURCE_TYPE: (
            "quotation_from_offiho.xlsx",
            _convert_offiho_cart_to_quotation,
        ),
        SUPPLIER_CART_SOURCE_TYPE: (
            "quotation_from_supplier.xlsx",
            _convert_supplier_cart_to_quotation,
        ),
        MIXED_CATALOG_CART_SOURCE_TYPE: (
            "quotation_from_mixed_catalog.xlsx",
            _convert_mixed_catalog_cart_to_quotation,
        ),
    }
    conversion = conversions.get(source_type)
    if conversion is None:
        raise RuntimeError("Tipo de fuente JSON no soportado")
    has_import = (
        source_type == MIXED_CATALOG_CART_SOURCE_TYPE
        and payload.get("imported_source") is not None
    )
    if has_import != (imported_source_path is not None):
        raise RuntimeError("Fuente importada validada no coincide con payload")
    if source_type != MIXED_CATALOG_CART_SOURCE_TYPE and imported_source_path is not None:
        raise RuntimeError("Fuente importada inesperada para tipo de payload")

    output_name, converter = conversion
    converted_input = _safe_tmp_target(tmp_dir, output_name)
    if has_import:
        converter(
            local_input,
            converted_input,
            payload,
            imported_source_path=imported_source_path,
        )
    else:
        converter(local_input, converted_input, payload)
    try:
        converted_stat = os.lstat(converted_input)
    except OSError as exc:
        raise RuntimeError("El convertidor no produjo una fuente para el parser") from exc
    if (
        not stat.S_ISREG(converted_stat.st_mode)
        or _path_is_symlink_or_reparse(converted_input)
        or converted_input.parent.resolve(strict=True) != Path(tmp_dir).resolve(strict=True)
    ):
        raise RuntimeError("El convertidor produjo una fuente insegura")
    converted_bytes = _read_regular_file_once(
        converted_input,
        max_bytes=MAX_IMPORTED_SOURCE_BYTES,
    )
    _validate_xlsx_mime(converted_bytes)
    return converted_input


def _prepare_generator_input(
    job: dict,
    local_input: Path,
    tmp_dir: Path,
    *,
    client=None,
) -> PreparedGeneratorInput:
    input_extension = _input_extension_for_job(job)
    _validate_local_input_file(job, local_input, input_extension)
    if _is_json_cart_job(job):
        payload = _read_cart_payload(local_input)
        source_type = payload.get("source_type")
        if not isinstance(source_type, str) or not source_type.strip():
            raise RuntimeError("JSON de entrada sin source_type")
        source_type = source_type.strip()

        metadata = job.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise RuntimeError("Metadata de job invalida")
        metadata_source_type = _json_job_source_type(job)
        if metadata_source_type is None:
            raise RuntimeError("source_type de metadata ausente para JSON de entrada")
        if metadata_source_type != source_type:
            raise RuntimeError("source_type de metadata no coincide con JSON de entrada")

        imported_source_path = None
        canonical_rows: tuple[QuotationDataRow, ...] = ()
        if source_type == MIXED_CATALOG_CART_SOURCE_TYPE:
            from mobiliti_saas.quote_engine.mixed_catalog import validate_mixed_catalog_payload

            try:
                payload = validate_mixed_catalog_payload(payload)
            except ValueError as exc:
                raise RuntimeError(f"Payload de cotizacion mixta invalido: {exc}") from exc
            _validate_mixed_job_provenance(job, payload)
            try:
                canonical_rows = tuple(quotation_data_rows(payload))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Filas canonicas de cotizacion invalidas: {exc}") from exc
            if not canonical_rows or len(canonical_rows) != payload["item_count"]:
                raise RuntimeError("Carrito mixto sin filas canonicas completas")
            project_context = deepcopy(payload.get("project_context"))
            if project_context is not None:
                metadata["project_context"] = project_context
                metadata["project_id"] = project_context["project_id"]
                metadata["project_revision"] = project_context["project_revision"]
                metadata["project_payload_hash"] = project_context[
                    "project_payload_hash"
                ]
            if payload["imported_source"] is not None:
                if client is None:
                    raise RuntimeError("Cliente de storage requerido para fuente importada")
                imported_source_path = _download_imported_source(
                    client,
                    payload,
                    tmp_dir,
                    job=job,
                )

        conversion_flags = {
            TARKETT_CART_SOURCE_TYPE: "tarkett_converted",
            OFFIHO_CART_SOURCE_TYPE: "offiho_converted",
            SUPPLIER_CART_SOURCE_TYPE: "supplier_converted",
            MIXED_CATALOG_CART_SOURCE_TYPE: "mixed_catalog_converted",
        }
        conversion_flag = conversion_flags.get(source_type)
        if conversion_flag is None:
            raise RuntimeError("Tipo de fuente JSON no soportado")
        converted_input = convert_validated_payload(
            source_type,
            payload,
            local_input,
            tmp_dir,
            imported_source_path,
        )
        metadata["input_extension"] = ".json"
        metadata[conversion_flag] = True
        if source_type == SUPPLIER_CART_SOURCE_TYPE:
            supplier = str(payload.get("supplier") or "").strip().lower()
            supplier_label = SUPPLIER_LABELS.get(supplier)
            if supplier_label is None:
                raise RuntimeError("Proveedor de catalogo no soportado")
            metadata.update(
                {
                    "catalog_supplier": supplier,
                    "catalog_supplier_label": supplier_label,
                    "catalog_price_mode": "list_price_net",
                    "descuento": 0,
                    "base_currency": payload.get("base_currency"),
                    "quote_currency": payload.get("quote_currency"),
                    "exchange_rate": payload.get("exchange_rate"),
                    "rate_source": payload.get("rate_source"),
                    "rate_effective_date": payload.get("rate_effective_date"),
                    "rate_retrieved_at": payload.get("rate_retrieved_at"),
                }
            )
        if source_type == MIXED_CATALOG_CART_SOURCE_TYPE:
            quote_currency = payload["quote_currency"]
            metadata.update(
                {
                    "catalog_price_mode": "mixed_catalog_converted",
                    "base_currency": quote_currency,
                    "quote_currency": quote_currency,
                    "exchange_rate": "1.000000",
                    "rate_summary": deepcopy(payload["rate_summary"]),
                    "auto_electrification_rate": deepcopy(
                        payload["auto_electrification_rate"]
                    ),
                }
            )
        job["metadata"] = metadata
        return PreparedGeneratorInput(
            parser_source=converted_input,
            original_quotation=imported_source_path,
            quotation_data=canonical_rows,
        )
    if input_extension != ".pdf":
        return PreparedGeneratorInput(
            parser_source=local_input,
            original_quotation=local_input,
            quotation_data=(),
        )

    converted_input = _safe_tmp_target(tmp_dir, "quotation_from_pdf.xlsx")
    _convert_pdf_to_quotation(local_input, converted_input, _template_path())
    metadata = job.get("metadata") or {}
    metadata["input_extension"] = ".pdf"
    metadata["pdf_converted"] = True
    job["metadata"] = metadata
    return PreparedGeneratorInput(
        parser_source=converted_input,
        original_quotation=converted_input,
        quotation_data=(),
    )


def _job_input_storage_provider(job: dict) -> str:
    metadata = job.get("metadata") or {}
    return str(
        metadata.get("resolved_input_storage_provider")
        or metadata.get("input_storage_provider")
        or metadata.get("storage_provider")
        or metadata.get("quote_storage_provider")
        or STORAGE_PROVIDER
    ).strip().lower()


def _job_input_storage_provider_candidates(job: dict) -> list[str]:
    metadata = job.get("metadata") or {}
    explicit = (
        metadata.get("resolved_input_storage_provider")
        or metadata.get("input_storage_provider")
        or metadata.get("storage_provider")
        or metadata.get("quote_storage_provider")
    )
    primary = str(explicit or STORAGE_PROVIDER).strip().lower()
    candidates = [primary]
    if not explicit:
        if _provider_uses_r2(primary):
            candidates.append("supabase")
        elif _r2_configured():
            candidates.append("r2")

    unique: list[str] = []
    seen: set[str] = set()
    for provider in candidates:
        if provider and provider not in seen:
            seen.add(provider)
            unique.append(provider)
    return unique or [STORAGE_PROVIDER]


def _set_resolved_input_storage_provider(job: dict, provider: str) -> None:
    metadata = job.get("metadata") or {}
    metadata["resolved_input_storage_provider"] = provider
    job["metadata"] = metadata


def _download_job_input(client: SupabaseClient, job: dict, dest: Path) -> None:
    if hasattr(client, "storage_download_from_provider"):
        errors: list[str] = []
        for provider in _job_input_storage_provider_candidates(job):
            try:
                client.storage_download_from_provider(job["input_path"], dest, provider)
                _set_resolved_input_storage_provider(job, provider)
                return
            except Exception as exc:
                errors.append(f"{provider}: {exc.__class__.__name__}")
        raise RuntimeError(f"No se pudo descargar input desde storage providers: {', '.join(errors)}")
    client.storage_download(job["input_path"], dest)
    _set_resolved_input_storage_provider(job, _job_input_storage_provider(job))


def _delete_job_input(client: SupabaseClient, job: dict) -> None:
    provider = _job_input_storage_provider(job)
    if hasattr(client, "storage_delete_from_provider"):
        client.storage_delete_from_provider(job.get("input_path") or "", provider)
        return
    client.storage_delete(job.get("input_path") or "")


def _validate_job_input_reference(job: dict) -> None:
    job_id = str(job.get("id") or "").strip()
    user_id = str(job.get("usuario_id") or "").strip()
    object_path = str(job.get("input_path") or "").strip()
    if not job_id or not user_id or not object_path:
        raise RuntimeError("Job sin procedencia de entrada completa")
    if (
        "\\" in object_path
        or object_path.startswith("/")
        or "?" in object_path
        or "#" in object_path
        or ".." in object_path.split("/")
        or any(not segment for segment in object_path.split("/"))
    ):
        raise RuntimeError("Ruta de entrada de job invalida")
    expected_prefix = f"users/{user_id}/jobs/{job_id}/"
    if not object_path.startswith(expected_prefix):
        raise RuntimeError("Ruta de entrada no corresponde al job")
    metadata = job.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise RuntimeError("Metadata de job invalida")
    explicit_provider = (
        metadata.get("resolved_input_storage_provider")
        or metadata.get("input_storage_provider")
        or metadata.get("storage_provider")
        or metadata.get("quote_storage_provider")
    )
    if explicit_provider is not None and str(explicit_provider).strip().lower() not in {
        "supabase",
        "r2",
        "cloudflare-r2",
        "cloudflare",
    }:
        raise RuntimeError("Proveedor de storage del job invalido")


def _cleanup_completed_import_source(client: SupabaseClient, final_job: dict) -> bool:
    metadata = final_job.get("metadata") or {}
    imported = metadata.get("import_source") if isinstance(metadata, dict) else None
    if not isinstance(imported, dict):
        return False
    try:
        import_id = str(uuid.UUID(str(imported.get("import_id") or "")))
        final_job_id = str(uuid.UUID(str(final_job.get("id") or "")))
    except (TypeError, ValueError, AttributeError):
        return False
    user_id = final_job.get("usuario_id")
    rows = client.rest(
        "GET",
        "/saas_quote_jobs",
        params={"id": f"eq.{import_id}", "select": "*", "limit": "2"},
    )
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    source = rows[0]
    source_metadata = source.get("metadata") or {}
    if (
        source.get("status") != "failed"
        or str(source.get("usuario_id")) != str(user_id)
        or not isinstance(source_metadata, dict)
        or source_metadata.get("import_consumed_by_job_id") != final_job_id
    ):
        return False
    prefix = f"users/{user_id}/jobs/{import_id}/"
    preview_paths = source_metadata.get("import_preview_paths")
    if preview_paths is None:
        preview_paths = {}
    if not isinstance(preview_paths, dict):
        return False
    paths = [
        source.get("input_path"),
        source_metadata.get("import_manifest_path"),
        *preview_paths.values(),
    ]
    clean_paths = []
    for raw_path in paths:
        path = str(raw_path or "").strip().lstrip("/")
        if not path:
            continue
        if not path.startswith(prefix) or ".." in path.split("/"):
            return False
        if path not in clean_paths:
            clean_paths.append(path)
    provider = str(
        source_metadata.get("input_storage_provider")
        or source_metadata.get("storage_provider")
        or STORAGE_PROVIDER
    ).strip().lower()
    for path in clean_paths:
        if hasattr(client, "storage_delete_from_provider"):
            client.storage_delete_from_provider(path, provider)
        else:
            client.storage_delete(path)
    cleaned_metadata = dict(source_metadata)
    cleaned_metadata.pop("import_manifest_path", None)
    cleaned_metadata.pop("import_preview_paths", None)
    cleaned_metadata.pop("import_source_hash", None)
    cleaned_metadata.pop("import_item_count", None)
    cleaned_metadata["import_consumed_cleanup_at"] = _utc_now()
    updated = client.rest(
        "PATCH",
        (
            f"/saas_quote_jobs?id=eq.{urllib.parse.quote(import_id, safe='-')}"
            "&status=eq.failed"
        ),
        data={"input_path": None, "metadata": cleaned_metadata, "updated_at": _utc_now()},
    )
    return isinstance(updated, list) and len(updated) == 1 and isinstance(updated[0], dict)


def _run_generator(
    job: dict,
    generator_input: PreparedGeneratorInput | Path,
    output_path: Path,
) -> None:
    metadata = job.get("metadata") or {}
    job["metadata"] = metadata
    if isinstance(generator_input, Path):
        generator_input = PreparedGeneratorInput(
            parser_source=generator_input,
            original_quotation=generator_input,
            quotation_data=(),
        )
    if not isinstance(generator_input, PreparedGeneratorInput):
        raise TypeError("Entrada preparada del generador invalida")
    engine = QUOTE_ENGINE
    if engine == "auto":
        engine = "python"

    if engine in {"python", "openpyxl", "online"}:
        from online_quote_generator import generate_online_quote

        generate_online_quote(
            source_path=generator_input.parser_source,
            output_path=output_path,
            metadata=metadata,
            template_path=_template_path(),
            original_quotation_path=generator_input.original_quotation,
            quotation_data_rows=generator_input.quotation_data,
        )
        return
    raise RuntimeError(
        f"QUOTE_ENGINE invalido: {QUOTE_ENGINE}. "
        "La version final SaaS usa QUOTE_ENGINE=python; xlwings quedo archivado en historial."
    )


def fetch_next_job(client: SupabaseClient) -> dict | None:
    rows = client.rest(
        "GET",
        "/saas_quote_jobs",
        params={"status": "eq.queued", "select": "*", "order": "created_at.asc", "limit": "1"},
    )
    return rows[0] if rows else None


class WorkerLeaseLost(RuntimeError):
    """El trabajo ya no pertenece al intento que sigue ejecutandose."""


class WorkerCompletionFailed(RuntimeError):
    """El resultado se genero, pero no se pudo confirmar durablemente."""


def _attempt_patch_path(
    job: dict,
    *,
    status: str = "processing",
    lease_before: str | None = None,
) -> str:
    job_id = str(job.get("id") or "").strip()
    attempt_token = str(job.get("attempt_token") or "").strip()
    if not job_id or not attempt_token:
        raise WorkerLeaseLost("El intento del worker no tiene un lease valido")
    path = (
        f"/saas_quote_jobs?id=eq.{urllib.parse.quote(job_id, safe='-')}"
        f"&status=eq.{urllib.parse.quote(status, safe='-')}"
        f"&attempt_token=eq.{urllib.parse.quote(attempt_token, safe='-')}"
    )
    if lease_before is not None:
        path += f"&lease_expires_at=lt.{urllib.parse.quote(lease_before, safe=':-TZ')}"
    return path


def _single_attempt_row(rows, action: str) -> dict:
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        if not rows:
            raise WorkerLeaseLost(f"Lease perdido durante {action}")
        raise RuntimeError(f"Actualizacion ambigua durante {action}")
    return rows[0]


def _patch_current_attempt(client: SupabaseClient, job: dict, data: dict, action: str) -> dict:
    row = _single_attempt_row(
        client.rest("PATCH", _attempt_patch_path(job), data=data),
        action,
    )
    job.update(row)
    return row


def _heartbeat_attempt(client: SupabaseClient, job: dict) -> None:
    row = _single_attempt_row(
        client.rest(
            "PATCH",
            _attempt_patch_path(job),
            data={"lease_expires_at": _lease_deadline(), "updated_at": _utc_now()},
        ),
        "heartbeat",
    )
    if (
        row.get("status") != "processing"
        or str(row.get("attempt_token") or "") != str(job.get("attempt_token") or "")
    ):
        raise WorkerLeaseLost("Heartbeat no confirmo la propiedad del intento")


def _is_exact_attempt_output(job: dict, output_path: str) -> bool:
    expected = (
        f"users/{job.get('usuario_id')}/jobs/{job.get('id')}/attempts/"
        f"{job.get('attempt_token')}/output.xlsx"
    )
    return bool(job.get("attempt_token")) and output_path == expected


def _cleanup_unpersisted_attempt_output(
    client: SupabaseClient,
    job: dict,
    output_path: str,
) -> bool:
    if not _is_exact_attempt_output(job, output_path):
        print(f"WARN: ruta de output de intento invalida; se conserva {output_path}")
        return False
    try:
        rows = client.rest(
            "GET",
            "/saas_quote_jobs",
            params={"id": f"eq.{job['id']}", "select": "id,output_path", "limit": "2"},
        )
    except Exception as exc:
        print(f"WARN: no se verifico output persistido de {job['id']}: {exc}")
        return False
    if not isinstance(rows, list) or len(rows) > 1:
        print(f"WARN: consulta ambigua de output persistido; se conserva {output_path}")
        return False
    current = rows[0] if rows else None
    if isinstance(current, dict) and str(current.get("output_path") or "") == output_path:
        return False
    try:
        client.storage_delete(output_path)
    except Exception as exc:
        print(f"WARN: no se pudo limpiar output huerfano de {job['id']}: {exc}")
        return False
    return True


class _LeaseHeartbeat:
    def __init__(self, client: SupabaseClient, job: dict):
        self.client = client
        self.job = job
        self.stop_event = threading.Event()
        self.failure: BaseException | None = None
        self.thread = threading.Thread(
            target=self._run,
            name=f"quote-heartbeat-{job.get('id')}",
            daemon=True,
        )

    def _run(self) -> None:
        while not self.stop_event.wait(WORKER_HEARTBEAT_SECONDS):
            try:
                _heartbeat_attempt(self.client, self.job)
            except BaseException as exc:  # la hebra principal valida antes de efectos finales
                self.failure = exc
                self.stop_event.set()

    def __enter__(self):
        self.thread.start()
        return self

    def ensure_owned(self) -> None:
        if self.failure is not None:
            if isinstance(self.failure, WorkerLeaseLost):
                raise self.failure
            raise WorkerLeaseLost("No se pudo renovar el lease del worker") from self.failure

    def __exit__(self, _exc_type, _exc, _traceback):
        self.stop_event.set()
        self.thread.join(timeout=35.0)
        if _exc_type is None:
            if self.thread.is_alive():
                raise WorkerLeaseLost("El heartbeat no termino antes de finalizar")
            self.ensure_owned()
        return False


def recover_stale_jobs(client: SupabaseClient) -> int:
    if STALE_MINUTES <= 0:
        return 0
    now = datetime.now(timezone.utc)
    legacy_cutoff = (now - timedelta(minutes=STALE_MINUTES)).isoformat()
    candidates = client.rest(
        "GET", "/saas_quote_jobs",
        params={"status": "eq.processing", "select": "*", "order": "updated_at.asc", "limit": "100"},
    )
    recovered = 0
    for candidate in candidates or []:
        token = str(candidate.get("attempt_token") or "").strip()
        lease_expires_at = candidate.get("lease_expires_at")
        updated_at = candidate.get("updated_at")
        try:
            stale = (
                bool(token)
                and lease_expires_at is not None
                and datetime.fromisoformat(str(lease_expires_at).replace("Z", "+00:00")) < now
            ) or (
                not token
                and updated_at is not None
                and datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                < now - timedelta(minutes=STALE_MINUTES)
            )
        except (TypeError, ValueError):
            stale = False
        if not stale:
            continue
        if token:
            path = _attempt_patch_path(candidate, lease_before=now.isoformat())
        else:
            job_id = urllib.parse.quote(str(candidate.get("id") or ""), safe="-")
            path = (
                f"/saas_quote_jobs?id=eq.{job_id}&status=eq.processing"
                f"&attempt_token=is.null&updated_at=lt.{urllib.parse.quote(legacy_cutoff, safe=':-TZ')}"
            )
        rows = client.rest(
            "PATCH",
            path,
            data={
                "status": "queued",
                "attempt_token": None,
                "lease_expires_at": None,
                "error_message": "Reintentado automaticamente: worker anterior quedo stale",
                "updated_at": _utc_now(),
            },
        )
        if rows:
            if not isinstance(rows, list) or len(rows) != 1:
                raise RuntimeError("Recuperacion stale devolvio un resultado ambiguo")
            recovered += 1
    if recovered:
        print(f"Jobs stale reencolados: {recovered}")
    return recovered


def claim_job(client: SupabaseClient, job: dict) -> dict | None:
    job_id = job["id"]
    attempt_token = str(uuid.uuid4())
    rows = client.rest(
        "PATCH",
        f"/saas_quote_jobs?id=eq.{job_id}&status=eq.queued",
        data={
            "status": "processing",
            "attempt_token": attempt_token,
            "lease_expires_at": _lease_deadline(),
            "updated_at": _utc_now(),
        },
    )
    if not rows:
        return None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError("Claim de cotizacion devolvio un resultado ambiguo")
    claimed = rows[0]
    if claimed.get("attempt_token") != attempt_token or claimed.get("status") != "processing":
        raise WorkerLeaseLost("Claim de cotizacion no confirmo el lease")
    return claimed


def update_progress(client: SupabaseClient, job: dict, percent: int) -> None:
    metadata = {**(job.get("metadata") or {}), "progress_percent": percent}
    _patch_current_attempt(
        client,
        job,
        {
            "metadata": metadata,
            "lease_expires_at": _lease_deadline(),
            "updated_at": _utc_now(),
        },
        f"progreso {percent}",
    )


_PROJECT_WORKER_COUNT_FIELDS = (
    "project_section_count",
    "project_principal_count",
    "project_complement_count",
    "project_physical_line_count",
    "project_max_section_lines",
)


def _emit_project_quote_worker_stage(
    job: dict,
    stage: str,
    *,
    started_at: float,
    error_code: str | None = None,
) -> None:
    metadata = job.get("metadata") or {}
    project_id = metadata.get("project_id")
    project_revision = metadata.get("project_revision")
    project_payload_hash = metadata.get("project_payload_hash")
    if (
        not isinstance(project_id, str)
        or not project_id
        or not isinstance(project_revision, int)
        or isinstance(project_revision, bool)
        or not isinstance(project_payload_hash, str)
        or len(project_payload_hash) != 64
    ):
        return
    event = {
        "event": "project_quote_worker",
        "stage": stage,
        "duration_ms": max(
            0,
            int(round((time.perf_counter() - started_at) * 1000)),
        ),
        "job_id": str(job.get("id") or ""),
        "project_id": project_id,
        "project_revision": project_revision,
        "project_payload_hash": project_payload_hash,
    }
    for field in _PROJECT_WORKER_COUNT_FIELDS:
        value = metadata.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            event[field] = value
    if error_code:
        event["error_code"] = error_code
    print(json.dumps(event, ensure_ascii=True, separators=(",", ":")))


def process_job(client: SupabaseClient, job: dict) -> dict | None:
    worker_started_at = time.perf_counter()
    claimed = claim_job(client, job)
    if not claimed:
        print(f"Job {job['id']} ya fue tomado por otro worker.")
        return None

    job = {**job, **claimed}
    job_id = job["id"]
    attempt_token = job["attempt_token"]
    output_path = f"users/{job['usuario_id']}/jobs/{job_id}/attempts/{attempt_token}/output.xlsx"
    output_may_exist = False
    completed_durable = False
    _emit_project_quote_worker_stage(
        job,
        "claimed",
        started_at=worker_started_at,
    )

    with tempfile.TemporaryDirectory(prefix="mobiliti-quote-") as tmp:
        tmp_dir = Path(tmp)
        local_input = tmp_dir / f"input{_input_extension_for_job(job)}"
        local_output = tmp_dir / "output.xlsx"
        started_at = time.perf_counter()
        try:
            _validate_job_input_reference(job)
            with _LeaseHeartbeat(client, job) as heartbeat:
                update_progress(client, job, 45)
                _download_job_input(client, job, local_input)
                update_progress(client, job, 55)
                generator_input = _prepare_generator_input(
                    job,
                    local_input,
                    tmp_dir,
                    client=client,
                )
                _emit_project_quote_worker_stage(
                    job,
                    "input_prepared",
                    started_at=worker_started_at,
                )
                heartbeat.ensure_owned()
                _run_generator(job, generator_input, local_output)
                _emit_project_quote_worker_stage(
                    job,
                    "workbook_composed",
                    started_at=worker_started_at,
                )
                heartbeat.ensure_owned()
                generation_seconds = round(time.perf_counter() - started_at, 1)
                update_progress(client, job, 90)
                _validate_output_size(local_output)
                output_may_exist = True
                client.storage_upload(output_path, local_output)
                heartbeat.ensure_owned()
            metadata = {
                **(job.get("metadata") or {}),
                "progress_percent": 100,
                "generation_seconds": generation_seconds,
            }
            try:
                completed = _patch_current_attempt(
                    client,
                    job,
                    {
                        "status": "completed",
                        "output_path": output_path,
                        "metadata": metadata,
                        "error_message": None,
                        "lease_expires_at": None,
                        "updated_at": _utc_now(),
                        "completed_at": _utc_now(),
                    },
                    "finalizacion",
                )
                completed_durable = True
                _emit_project_quote_worker_stage(
                    job,
                    "completed",
                    started_at=worker_started_at,
                )
            except WorkerLeaseLost as completion_error:
                try:
                    _patch_current_attempt(
                        client,
                        job,
                        {
                            "status": "failed",
                            "metadata": metadata,
                            "error_message": "No se pudo confirmar la finalizacion; reintenta la cotizacion",
                            "lease_expires_at": None,
                            "updated_at": _utc_now(),
                        },
                        "fallo de finalizacion",
                    )
                except WorkerLeaseLost:
                    raise completion_error
                raise WorkerCompletionFailed(
                    "No se pudo confirmar durablemente la finalizacion"
                ) from completion_error
            try:
                _cleanup_completed_import_source(client, job)
            except Exception as exc:
                print(f"WARN: no se pudo limpiar fuente importada consumida de {job_id}: {exc}")
            try:
                _delete_job_input(client, job)
            except Exception as exc:
                print(f"WARN: no se pudo borrar input de job {job_id}: {exc}")
                return [completed]
            try:
                cleared_rows = client.rest(
                    "PATCH",
                    _attempt_patch_path(job, status="completed"),
                    data={"input_path": None, "updated_at": _utc_now()},
                )
                if isinstance(cleared_rows, list) and len(cleared_rows) == 1 and isinstance(cleared_rows[0], dict):
                    job.update(cleared_rows[0])
                    completed = cleared_rows[0]
                else:
                    print(f"WARN: no se pudo limpiar input_path del job completado {job_id}")
            except Exception as exc:
                print(f"WARN: no se pudo limpiar input_path del job completado {job_id}: {exc}")
            return [completed]
        except WorkerLeaseLost:
            raise
        except WorkerCompletionFailed:
            raise
        except Exception as exc:
            generation_seconds = round(time.perf_counter() - started_at, 1)
            _emit_project_quote_worker_stage(
                job,
                "failed",
                started_at=worker_started_at,
                error_code="project_quote_worker_failed",
            )
            try:
                _patch_current_attempt(
                    client,
                    job,
                    {
                        "status": "failed",
                        "metadata": {
                            **(job.get("metadata") or {}),
                            "progress_percent": 100,
                            "generation_seconds": generation_seconds,
                        },
                        "error_message": str(exc)[:1000],
                        "lease_expires_at": None,
                        "updated_at": _utc_now(),
                    },
                    "fallo",
                )
            except WorkerLeaseLost:
                raise WorkerLeaseLost("Lease perdido antes de registrar el fallo") from exc
            raise
        finally:
            if output_may_exist and not completed_durable:
                _cleanup_unpersisted_attempt_output(client, job, output_path)


def _fallback_tarkett_catalog_payload() -> dict:
    try:
        payload = json.loads(TARKETT_CATALOG_FALLBACK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Catalogo Tarkett de respaldo no disponible") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or not payload["items"]:
        raise RuntimeError("Catalogo Tarkett de respaldo invalido")
    return payload


def sync_tarkett_catalog_if_due(client, *, force: bool = False) -> bool:
    global _TARKETT_LAST_SYNC_ATTEMPT
    if not TARKETT_SYNC_ENABLED:
        return False
    now = time.monotonic()
    if not force and now - _TARKETT_LAST_SYNC_ATTEMPT < TARKETT_SYNC_INTERVAL_SECONDS:
        return False
    _TARKETT_LAST_SYNC_ATTEMPT = now
    if not TARKETTNET_EMAIL or not TARKETTNET_PASSWORD:
        print("WARN: sincronizacion Tarkett habilitada sin credenciales Tarkettnet.")
        return False

    snapshot = client.catalog_snapshot_get("tarkett")
    base_payload = snapshot.get("payload") if isinstance(snapshot, dict) else None
    if not isinstance(base_payload, dict):
        base_payload = _fallback_tarkett_catalog_payload()
    enriched = sync_catalog_from_tarkettnet(
        base_payload,
        email=TARKETTNET_EMAIL,
        password=TARKETTNET_PASSWORD,
    )
    if str(enriched.get("source_hash")) == str((snapshot or {}).get("source_hash")):
        print("Catalogo Tarkett sin cambios.")
        return False
    client.catalog_snapshot_upsert("tarkett", enriched)
    print(
        "Catalogo Tarkett actualizado: "
        f"{enriched.get('tarkettnet_matches', 0)} coincidencias, "
        f"{enriched.get('tarkettnet_price_matches', 0)} precios."
    )
    return True


def run_once() -> bool:
    client = LocalDevClient() if DEV_MODE else (PostgresClient() if DATABASE_URL else SupabaseClient())
    recover_stale_jobs(client)
    job = fetch_next_job(client)
    if not job:
        sync_tarkett_catalog_if_due(client)
        print("Sin jobs pendientes.")
        return False
    print(f"Procesando job {job['id']}...")
    process_job(client, job)
    print(f"Job {job['id']} completado.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker de cotizaciones Mobiliti")
    parser.add_argument("--once", action="store_true", help="Procesa un solo job y termina")
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    print("Worker Mobiliti iniciado.")
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"ERROR: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
