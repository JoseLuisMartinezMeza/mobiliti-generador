"""
Backend Mobiliti SaaS para Vercel (Serverless).
FastAPI + Supabase REST API.
TODO EN UN SOLO ARCHIVO para evitar problemas de imports en Vercel.

NOTA: Usa urllib.request (std lib) en lugar de httpx/requests porque
Vercel serverless tiene problemas con conexiones persistentes async y
con la resolucion DNS de algunos dominios mediante esas librerias.
"""

import os
import json
import time
import uuid
import sys
import hashlib
import hmac
import io
import logging
import mimetypes
import re
import threading
import unicodedata
import urllib.request
import urllib.error
import warnings
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote, unquote, urlparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from jose import JWTError, jwt
import bcrypt
from fastapi import FastAPI, HTTPException, Header, Depends, Request, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from mangum import Mangum
from PIL import Image, UnidentifiedImageError


PROJECT_QUOTE_LOGGER = logging.getLogger("mobiliti.project_quote")


for _root in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else None):
    if _root and str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from mobiliti_saas.quote_engine.tarkett_catalog import (  # noqa: E402
    CATALOG_PATH,
    build_tarkett_cart_payload,
    load_tarkett_catalog,
    load_tarkett_catalog_data,
)
from mobiliti_saas.quote_engine.offiho_catalog import (  # noqa: E402
    CATALOG_PATH as OFFIHO_DEFAULT_CATALOG_PATH,
    build_offiho_cart_payload,
    load_offiho_catalog,
)
from mobiliti_saas.quote_engine.supplier_catalog import (  # noqa: E402
    ALLOWED_CURRENCIES,
    ALLOWED_SUPPLIERS,
    build_supplier_cart_payload,
    load_supplier_catalog_data,
    resolve_conversion_rate,
)
from mobiliti_saas.quote_engine.mixed_catalog import (  # noqa: E402
    MAX_MIXED_CATALOG_LINES,
    MAX_MIXED_REQUEST_BYTES,
    MAX_QUOTE_REQUEST_BYTES,
    MIXED_CATALOG_ORDER,
    build_mixed_catalog_cart_payload,
    build_mixed_reservation_groups,
    preflight_mixed_catalog_items,
    validate_quote_size,
    validate_mixed_catalog_payload,
)
from mobiliti_saas.quote_engine.catalog_search import search_catalog_products  # noqa: E402
from mobiliti_saas.quote_engine.project_model import (  # noqa: E402
    ASSET_KEY,
    normalize_project_payload,
    project_summary,
)
from mobiliti_saas.quote_engine.project_quote import (  # noqa: E402
    project_context,
    project_quote_projection,
)
from mobiliti_saas.quote_engine.quotation_import import (  # noqa: E402
    build_import_manifest,
    validate_import_manifest,
)
from mobiliti_saas.quote_engine.engine import _fetch_latest_usd_mxn_row  # noqa: E402
from mobiliti_saas.quote_engine.template_profiles import (  # noqa: E402
    DEFAULT_TEMPLATE_PROFILE_ID,
    lookup_template_profile,
)


def _canonical_template_id(value: object | None) -> str:
    try:
        return lookup_template_profile(value).id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# ═══════════════════════════════════════════════════════════════
# CONFIGURACION
# ═══════════════════════════════════════════════════════════════

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
MOBILITI_REST_SECRET = os.environ.get("MOBILITI_REST_SECRET")
QUOTE_RETENTION_TOKEN = os.environ.get("QUOTE_RETENTION_TOKEN")
WORKER_WAKE_ENABLED = os.environ.get("WORKER_WAKE_ENABLED", "").lower() in {"1", "true", "yes"}
WORKER_WAKE_URL = os.environ.get("WORKER_WAKE_URL") if WORKER_WAKE_ENABLED else None

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
MIXED_QUOTE_BODY_FIELDS = frozenset({
    "items", "sections", "quote_currency", "descuento", "proyecto", "cliente", "correo",
    "telefono", "direccion", "razon_social", "cotizacion", "template",
    "description_language", "image_provider", "image_cleanup_strength",
    "image_background", "image_prompt",
})
QUOTE_STORAGE_BUCKET = os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files")
QUOTE_STORAGE_PROVIDER = os.environ.get("QUOTE_STORAGE_PROVIDER", "supabase").strip().lower()
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "").strip().rstrip("/")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.environ.get("R2_BUCKET", QUOTE_STORAGE_BUCKET).strip() or QUOTE_STORAGE_BUCKET
R2_REGION = os.environ.get("R2_REGION", "auto").strip() or "auto"
MAX_QUOTE_UPLOAD_MB = int(os.environ.get("MAX_QUOTE_UPLOAD_MB", "25"))
MAX_QUOTE_HISTORY_PER_USER = int(os.environ.get("MAX_QUOTE_HISTORY_PER_USER", "5"))
MAX_ACTIVE_QUOTE_JOBS_PER_USER = max(1, min(20, int(os.environ.get("MAX_ACTIVE_QUOTE_JOBS_PER_USER", "5"))))
QUOTE_DOWNLOADED_OUTPUT_RETENTION_DAYS = int(os.environ.get("QUOTE_DOWNLOADED_OUTPUT_RETENTION_DAYS", "14"))
DELETE_COMPLETED_QUOTE_INPUTS = _env_bool("DELETE_COMPLETED_QUOTE_INPUTS", True)
QUOTE_STORAGE_RETENTION_MIN_AGE_DAYS = int(os.environ.get("QUOTE_STORAGE_RETENTION_MIN_AGE_DAYS", "1"))
ALLOWED_QUOTE_INPUT_EXTENSIONS = (".xlsx", ".pdf")
SIGNED_UPLOAD_TTL_SECONDS = int(os.environ.get("SIGNED_UPLOAD_TTL_SECONDS", "3600"))
SIGNED_DOWNLOAD_TTL_SECONDS = int(os.environ.get("SIGNED_DOWNLOAD_TTL_SECONDS", "3600"))
DEV_MODE = os.environ.get("MOBILITI_DEV_MODE", "").lower() in {"1", "true", "yes"}
DEV_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_STORE_DIR = Path(os.environ.get("MOBILITI_DEV_STORE_DIR", DEV_PROJECT_ROOT / ".mobiliti_dev_store")).resolve()
DEV_PUBLIC_BASE_URL = os.environ.get("MOBILITI_DEV_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEV_USER_EMAIL = os.environ.get("MOBILITI_DEV_USER_EMAIL", "dev@mobiliti.local")
DEV_USER_PASSWORD = os.environ.get("MOBILITI_DEV_USER_PASSWORD", "dev12345")
_DEV_CATALOG_RESERVATION_LOCK = threading.RLock()
_MIXED_CART_RESERVATION_LOCK = _DEV_CATALOG_RESERVATION_LOCK
TARKETT_CATALOG_PATH = os.environ.get("TARKETT_CATALOG_PATH")
TARKETT_CATALOG_DB_ENABLED = _env_bool("TARKETT_CATALOG_DB_ENABLED", bool(os.environ.get("VERCEL")))
TARKETT_CATALOG_DB_TTL_SECONDS = max(30, int(os.environ.get("TARKETT_CATALOG_DB_TTL_SECONDS", "300")))
OFFIHO_CATALOG_PATH = os.environ.get("OFFIHO_CATALOG_PATH")
CATALOG_SUPPLIER_ORDER = (
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco", "idelika", "conceptos",
)
CATALOG_SUPPLIER_LABELS = {
    "cr-global": "CR Global",
    "sonara": "Sonara",
    "sunon": "Sunon",
    "alma": "ALMA",
    "lumbro": "Lumbro",
    "jome": "JOME",
    "lauco": "Lauco",
    "idelika": "IDÉLIKA",
    "conceptos": "Conceptos",
}


def _canonical_enabled_catalog_suppliers(values) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    seen: set[str] = set()
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or value != value.lower()
            or value not in ALLOWED_SUPPLIERS
            or value not in CATALOG_SUPPLIER_ORDER
            or value in seen
        ):
            return ()
        seen.add(value)
    return tuple(supplier for supplier in CATALOG_SUPPLIER_ORDER if supplier in seen)


def _parse_enabled_catalog_suppliers(raw: str) -> tuple[str, ...]:
    if raw == "":
        return ()
    return _canonical_enabled_catalog_suppliers(raw.split(","))


CATALOG_ENABLED_SUPPLIERS = _parse_enabled_catalog_suppliers(
    os.environ.get("CATALOG_ENABLED_SUPPLIERS", "")
)
CATALOG_ASSET_BUCKET = "catalog-assets"
CATALOG_ASSET_MAX_BYTES = 8 * 1024 * 1024
CATALOG_ASSET_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
CATALOG_ASSET_MAX_WIDTH = 8192
CATALOG_ASSET_MAX_HEIGHT = 8192
CATALOG_ASSET_MAX_PIXELS = 25_000_000
IMPORT_PREVIEW_IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMPORT_PREVIEW_IMAGE_MAX_WIDTH = 8192
IMPORT_PREVIEW_IMAGE_MAX_HEIGHT = 8192
IMPORT_PREVIEW_IMAGE_MAX_PIXELS = 25_000_000
IMPORT_PREVIEW_THUMBNAIL_MAX_SIDE = 640
CATALOG_ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)$")
CATALOG_DIFF_LIMIT = 100
CATALOG_DIFF_FIELDS = (
    "sku", "code_status", "brand", "collection", "name", "description", "unit",
    "availability_type", "stock", "lead_time", "base_price_options", "add_on_options",
    "base_currency", "price_net", "tax_rate", "attributes", "image_url", "image_kind",
    "product_url", "warnings",
)
DEFAULT_CORS_ORIGINS = (
    "https://web-lemon-one-45.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)
QUOTE_NUMBER_PREFIX_BY_EMAIL = {
    "joel.meza@mobiliti.mx": "100",
    "karen.merin@mobiliti.mx": "200",
    "jl.martinez@mobiliti.mx": "300",
    "gabriela.zavala@mobiliti.mx": "400",
    "susana@mobiliti.mx": "500",
    "emiliano.quevedo@mobiliti.mx": "600",
}

# ═══════════════════════════════════════════════════════════════
# RATE LIMITING (in-memory, simple, para serverless)
# ═══════════════════════════════════════════════════════════════

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 900  # 15 minutos
_RATE_LIMIT_STORE: dict[str, list[float]] = {}


def _check_rate_limit(ip: str) -> bool:
    """Retorna True si el IP esta dentro del limite."""
    now = time.monotonic()
    attempts = _RATE_LIMIT_STORE.get(ip, [])
    # Filtrar intentos dentro de la ventana
    attempts = [t for t in attempts if now - t < _WINDOW_SECONDS]
    _RATE_LIMIT_STORE[ip] = attempts
    return len(attempts) < _MAX_ATTEMPTS


def _record_attempt(ip: str):
    """Registra un intento de login fallido."""
    now = time.monotonic()
    attempts = _RATE_LIMIT_STORE.get(ip, [])
    attempts.append(now)
    _RATE_LIMIT_STORE[ip] = attempts

# Password hashing helpers
def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _dev_db_path() -> Path:
    return DEV_STORE_DIR / "db.json"


def _dev_storage_file(object_path: str) -> Path:
    if (
        not isinstance(object_path, str)
        or not object_path
        or object_path != object_path.strip()
        or any(ord(character) < 32 for character in object_path)
    ):
        raise RuntimeError("Ruta de storage invalida")
    normalized = object_path.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise RuntimeError("Ruta de storage invalida")
    parts = normalized.split("/")
    if any(
        part in {"", ".", ".."}
        or ":" in part
        or part != part.strip()
        or part.endswith(".")
        for part in parts
    ):
        raise RuntimeError("Ruta de storage invalida")
    try:
        root = (DEV_STORE_DIR / "storage" / QUOTE_STORAGE_BUCKET).resolve()
        candidate = root.joinpath(*parts).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("Ruta de storage invalida") from exc
    return candidate


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


def _dev_save(data: dict):
    _write_json_snapshot(_dev_db_path(), data)


def _dev_load() -> dict:
    path = _dev_db_path()
    if path.exists():
        return _read_json_snapshot(path)

    now = _iso(datetime.now(timezone.utc))
    data = {
        "usuarios": [
            {
                "id": 1,
                "email": DEV_USER_EMAIL.lower(),
                "hashed_password": _hash_password(DEV_USER_PASSWORD),
                "nombre": "Usuario Dev",
                "empresa": "Mobiliti Dev",
                "es_admin": True,
                "activo": True,
                "creado": now,
            }
        ],
        "suscripciones": [
            {
                "id": 1,
                "usuario_id": 1,
                "estado": "activa",
                "plan": "dev",
                "fecha_inicio": now,
                "fecha_fin": _iso(datetime.now(timezone.utc) + timedelta(days=365)),
                "creado": now,
            }
        ],
        "quote_jobs": [],
        "projects": [],
        "tarkett_reservations": [],
        "offiho_reservations": [],
        "catalog_reservations": [],
        "catalog_published_snapshots": {},
        "catalog_sync_runs": [],
        "exchange_rates": [],
        "supplier_catalog_snapshots": {},
    }
    _dev_save(data)
    return data


def _dev_next_id(rows: list[dict]) -> int:
    return max([int(row.get("id", 0)) for row in rows] or [0]) + 1


# ═══════════════════════════════════════════════════════════════
# SUPABASE REST HELPERS (urllib.request)
# ═══════════════════════════════════════════════════════════════

def _storage_key() -> str:
    return SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY or ""


_R2_CLIENT = None


def _use_r2_storage() -> bool:
    return QUOTE_STORAGE_PROVIDER in {"r2", "cloudflare-r2", "cloudflare"}


def _storage_bucket_name() -> str:
    return R2_BUCKET if _use_r2_storage() else QUOTE_STORAGE_BUCKET


def _storage_provider_name() -> str:
    return "r2" if _use_r2_storage() else "supabase"


def _r2_endpoint_url() -> str:
    if R2_ENDPOINT_URL:
        return R2_ENDPOINT_URL
    if R2_ACCOUNT_ID:
        return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return ""


def _r2_configured() -> bool:
    return bool(_r2_endpoint_url() and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET)


def _storage_configured() -> bool:
    if DEV_MODE:
        return True
    if _use_r2_storage():
        return _r2_configured()
    return bool(SUPABASE_URL and _storage_key())


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


def _get_supabase_headers():
    key = _storage_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
        **({"x-mobiliti-rest-secret": MOBILITI_REST_SECRET} if MOBILITI_REST_SECRET else {}),
    }


def _use_postgres() -> bool:
    return bool(DATABASE_URL) and not DEV_MODE


def _pg_connect_kwargs(dict_row) -> dict:
    return {
        "row_factory": dict_row,
        "connect_timeout": 10,
        "prepare_threshold": None,
    }


def _raise_pg_runtime_error(exc: Exception) -> None:
    raise RuntimeError(f"Postgres connection/query error: {exc.__class__.__name__}") from exc


def _pg_rows(sql: str, params: tuple = ()) -> list[dict]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
    try:
        with psycopg.connect(DATABASE_URL, **_pg_connect_kwargs(dict_row)) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except psycopg.Error as exc:
        _raise_pg_runtime_error(exc)
    return [_jsonable_row(row) for row in rows]


def _pg_one(sql: str, params: tuple = ()) -> dict | None:
    rows = _pg_rows(sql, params)
    return rows[0] if rows else None


def _pg_write(sql: str, params: tuple = ()) -> dict | None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
    adapted = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
    try:
        with psycopg.connect(DATABASE_URL, **_pg_connect_kwargs(dict_row)) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, adapted)
                row = cur.fetchone()
            conn.commit()
    except psycopg.Error as exc:
        _raise_pg_runtime_error(exc)
    return _jsonable_row(row) if row else None


def _jsonable_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    clean = {}
    for key, value in dict(row).items():
        if isinstance(value, (date, datetime)):
            clean[key] = value.isoformat()
        else:
            clean[key] = value
    return clean


def _pg_update(table: str, key_name: str, key_value, updates: dict) -> dict:
    allowed_tables = {"saas_suscripciones", "saas_quote_jobs"}
    if table not in allowed_tables:
        raise RuntimeError("Tabla no permitida")
    payload = {k: v for k, v in updates.items() if k}
    if not payload:
        return {}
    set_clause = ", ".join(f"{key} = %s" for key in payload.keys())
    params = tuple(payload.values()) + (key_value,)
    row = _pg_write(f"UPDATE {table} SET {set_clause} WHERE {key_name} = %s RETURNING *", params)
    return row or {}


def _safe_http_error(service: str, code: int, body: str) -> str:
    raw = str(body or "")
    known_reasons = (
        "exceed_storage_size_quota",
        "Payload too large",
        "InvalidRequest",
        "Unauthorized",
        "Forbidden",
        "Not Found",
    )
    for reason in known_reasons:
        if reason in raw:
            return f"{service} HTTP {code}: {reason}"
    return f"{service} HTTP {code}"


def _supabase_req(method: str, path: str, params=None, json_data=None):
    """Ejecuta una peticion sincronica a Supabase REST usando urllib."""
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL no configurada")

    url = f"{SUPABASE_URL}/rest/v1{path}"
    if params:
        query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{query}"

    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    for k, v in _get_supabase_headers().items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            if body:
                return json.loads(body)
            return {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(_safe_http_error("Supabase", e.code, body)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase connection error: {e.reason}") from e


def _storage_req(method: str, path: str, json_data=None):
    """Ejecuta peticiones a Supabase Storage desde backend."""
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL no configurada")
    if not _storage_key():
        raise RuntimeError("Falta SUPABASE_SERVICE_KEY o SUPABASE_ANON_KEY para storage")

    url = f"{SUPABASE_URL}/storage/v1{path}"
    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    for key, value in _get_supabase_headers().items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(_safe_http_error("Supabase Storage", e.code, body)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase Storage connection error: {e.reason}") from e


class _StorageObjectNotFound(RuntimeError):
    """Indica una ausencia confirmada por el proveedor de storage."""


class _StorageObjectAlreadyExists(RuntimeError):
    """Indica que una escritura condicional encontro un objeto existente."""


def _r2_error_details(exc: Exception) -> tuple[str | None, int | None]:
    """Extrae un codigo estructurado de errores compatibles con boto3/R2."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None, None
    error = response.get("Error")
    metadata = response.get("ResponseMetadata")
    code = error.get("Code") if isinstance(error, dict) else None
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    return str(code) if code is not None else None, status if isinstance(status, int) else None


def _is_supabase_create_conflict(status: int, body: str) -> bool:
    """Reconoce exclusivamente conflictos documentados del create de Storage."""
    if status == 409:
        return True
    if status != 400:
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    code = str(payload.get("code") or payload.get("error") or "").strip()
    if code in {"ResourceAlreadyExists", "KeyAlreadyExists"}:
        return True
    message = str(payload.get("message") or "").strip().lower()
    return message in {
        "resource already exists",
        "the resource already exists",
        "key already exists",
        "the key already exists",
        "duplicate key value violates unique constraint",
    }


def _storage_download_bytes(path: str) -> bytes:
    """Descarga un objeto privado del proveedor de storage desde backend."""
    if DEV_MODE:
        try:
            return _dev_storage_file(path).read_bytes()
        except FileNotFoundError as exc:
            raise _StorageObjectNotFound("Objeto de storage no encontrado") from exc
        except OSError as exc:
            raise RuntimeError("Dev storage download error") from exc
    if _use_r2_storage():
        try:
            obj = _r2_client().get_object(Bucket=R2_BUCKET, Key=path.strip("/"))
            return obj["Body"].read()
        except Exception as exc:
            code, status = _r2_error_details(exc)
            if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
                raise _StorageObjectNotFound("Objeto de storage no encontrado") from exc
            raise RuntimeError(f"Cloudflare R2 download error: {exc.__class__.__name__}") from exc

    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL no configurada")
    if not _storage_key():
        raise RuntimeError("Falta SUPABASE_SERVICE_KEY o SUPABASE_ANON_KEY para storage")

    encoded_path = quote(path, safe="/")
    url = f"{SUPABASE_URL}/storage/v1/object/{QUOTE_STORAGE_BUCKET}/{encoded_path}"
    req = urllib.request.Request(url, method="GET")
    for key, value in _get_supabase_headers().items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise _StorageObjectNotFound("Objeto de storage no encontrado") from e
        body = e.read().decode("utf-8")
        raise RuntimeError(_safe_http_error("Supabase Storage", e.code, body)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase Storage connection error: {e.reason}") from e


# ═══════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════

def _storage_upload_bytes(path: str, content: bytes, content_type: str = "application/octet-stream") -> None:
    """Sube un objeto interno al proveedor de storage desde backend."""
    clean_path = str(path or "").strip().lstrip("/")
    if not clean_path:
        raise RuntimeError("Ruta de storage invalida")
    if DEV_MODE:
        dest = _dev_storage_file(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return
    if _use_r2_storage():
        try:
            _r2_client().put_object(Bucket=R2_BUCKET, Key=clean_path, Body=content, ContentType=content_type)
            return
        except Exception as exc:
            raise RuntimeError(f"Cloudflare R2 upload error: {exc.__class__.__name__}") from exc
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL no configurada")
    if not _storage_key():
        raise RuntimeError("Falta SUPABASE_SERVICE_KEY o SUPABASE_ANON_KEY para storage")

    encoded_path = quote(clean_path, safe="/")
    url = f"{SUPABASE_URL}/storage/v1/object/{QUOTE_STORAGE_BUCKET}/{encoded_path}"
    req = urllib.request.Request(
        url,
        data=content,
        headers={"Content-Type": content_type, "x-upsert": "true"},
        method="PUT",
    )
    for key, value in _get_supabase_headers().items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=60):
            return
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(_safe_http_error("Supabase Storage", e.code, body)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase Storage connection error: {e.reason}") from e


def _storage_create_bytes_if_absent(
    path: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    """Crea un objeto una sola vez sin sobrescribir una llave existente."""
    clean_path = str(path or "").strip().lstrip("/")
    if not clean_path:
        raise RuntimeError("Ruta de storage invalida")
    if DEV_MODE:
        dest = _dev_storage_file(clean_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest.open("xb") as output:
                output.write(content)
            return
        except FileExistsError as exc:
            raise _StorageObjectAlreadyExists("El objeto de storage ya existe") from exc
    if _use_r2_storage():
        try:
            _r2_client().put_object(
                Bucket=R2_BUCKET,
                Key=clean_path,
                Body=content,
                ContentType=content_type,
                IfNoneMatch="*",
            )
            return
        except Exception as exc:
            code, status = _r2_error_details(exc)
            if code == "PreconditionFailed" or status == 412:
                raise _StorageObjectAlreadyExists("El objeto de storage ya existe") from exc
            raise RuntimeError(f"Cloudflare R2 upload error: {exc.__class__.__name__}") from exc
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL no configurada")
    if not _storage_key():
        raise RuntimeError("Falta SUPABASE_SERVICE_KEY o SUPABASE_ANON_KEY para storage")

    encoded_path = quote(clean_path, safe="/")
    url = f"{SUPABASE_URL}/storage/v1/object/{QUOTE_STORAGE_BUCKET}/{encoded_path}"
    req = urllib.request.Request(
        url,
        data=content,
        headers={"Content-Type": content_type, "x-upsert": "false"},
        method="POST",
    )
    for key, value in _get_supabase_headers().items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=60):
            return
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if _is_supabase_create_conflict(e.code, body):
            raise _StorageObjectAlreadyExists("El objeto de storage ya existe") from e
        raise RuntimeError(_safe_http_error("Supabase Storage", e.code, body)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase Storage connection error: {e.reason}") from e


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _verify_password(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return _hash_password(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ═══════════════════════════════════════════════════════════════
# DB HELPERS (Supabase REST)
# ═══════════════════════════════════════════════════════════════

def db_get_usuario_by_email(email: str):
    if DEV_MODE:
        data = _dev_load()
        email = email.lower().strip()
        return next((u for u in data["usuarios"] if u["email"] == email), None)
    if _use_postgres():
        return _pg_one("SELECT * FROM saas_usuarios WHERE lower(email) = lower(%s) LIMIT 1", (email,))
    rows = _supabase_req("GET", "/saas_usuarios", params={"email": f"eq.{email}"})
    return rows[0] if rows else None


def db_get_usuario_by_id(user_id: int):
    if DEV_MODE:
        data = _dev_load()
        return next((u for u in data["usuarios"] if int(u["id"]) == int(user_id)), None)
    if _use_postgres():
        return _pg_one("SELECT * FROM saas_usuarios WHERE id = %s LIMIT 1", (user_id,))
    rows = _supabase_req("GET", "/saas_usuarios", params={"id": f"eq.{user_id}"})
    return rows[0] if rows else None


def db_list_usuarios():
    if DEV_MODE:
        return sorted(_dev_load()["usuarios"], key=lambda row: row.get("creado", ""), reverse=True)
    if _use_postgres():
        return _pg_rows("SELECT * FROM saas_usuarios ORDER BY creado DESC")
    return _supabase_req("GET", "/saas_usuarios", params={"select": "*", "order": "creado.desc"})


def db_create_usuario(email, hashed_password, nombre, empresa, es_admin=False):
    if DEV_MODE:
        data = _dev_load()
        row = {
            "id": _dev_next_id(data["usuarios"]),
            "email": email.lower().strip(),
            "hashed_password": hashed_password,
            "nombre": nombre,
            "empresa": empresa,
            "es_admin": es_admin,
            "activo": True,
            "creado": _iso(datetime.now(timezone.utc)),
        }
        data["usuarios"].append(row)
        _dev_save(data)
        return row
    data = {
        "email": email.lower().strip(),
        "hashed_password": hashed_password,
        "nombre": nombre,
        "empresa": empresa,
        "es_admin": es_admin,
        "activo": True,
        "creado": _iso(datetime.now(timezone.utc)),
    }
    if _use_postgres():
        return _pg_write(
            """
            INSERT INTO saas_usuarios (email, hashed_password, nombre, empresa, es_admin, activo, creado)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                data["email"],
                data["hashed_password"],
                data["nombre"],
                data["empresa"],
                data["es_admin"],
                data["activo"],
                data["creado"],
            ),
        )
    rows = _supabase_req("POST", "/saas_usuarios", json_data=data)
    if isinstance(rows, list) and rows:
        return rows[0]
    return db_get_usuario_by_email(data["email"]) or data


def db_get_suscripcion_by_usuario(usuario_id: int):
    if DEV_MODE:
        data = _dev_load()
        rows = [s for s in data["suscripciones"] if int(s["usuario_id"]) == int(usuario_id)]
        return sorted(rows, key=lambda row: row.get("creado", ""), reverse=True)[0] if rows else None
    if _use_postgres():
        return _pg_one(
            "SELECT * FROM saas_suscripciones WHERE usuario_id = %s ORDER BY creado DESC LIMIT 1",
            (usuario_id,),
        )
    rows = _supabase_req(
        "GET",
        "/saas_suscripciones",
        params={"usuario_id": f"eq.{usuario_id}", "order": "creado.desc", "limit": "1"}
    )
    return rows[0] if rows else None


def db_list_suscripciones():
    if DEV_MODE:
        data = _dev_load()
        users = {int(u["id"]): u for u in data["usuarios"]}
        rows = []
        for sub in data["suscripciones"]:
            row = dict(sub)
            user = users.get(int(sub["usuario_id"]))
            row["saas_usuarios"] = {"email": user["email"], "nombre": user["nombre"]} if user else None
            rows.append(row)
        return sorted(rows, key=lambda row: row.get("creado", ""), reverse=True)
    if _use_postgres():
        return _pg_rows(
            """
            SELECT s.*, json_build_object('email', u.email, 'nombre', u.nombre) AS saas_usuarios
            FROM saas_suscripciones s
            LEFT JOIN saas_usuarios u ON u.id = s.usuario_id
            ORDER BY s.creado DESC
            """
        )
    return _supabase_req(
        "GET",
        "/saas_suscripciones",
        params={"select": "*,saas_usuarios(email,nombre)", "order": "creado.desc"}
    )


def db_create_suscripcion(usuario_id, plan, fecha_inicio, fecha_fin, estado="activa"):
    if DEV_MODE:
        data = _dev_load()
        row = {
            "id": _dev_next_id(data["suscripciones"]),
            "usuario_id": int(usuario_id),
            "estado": estado,
            "plan": plan,
            "fecha_inicio": _iso(fecha_inicio),
            "fecha_fin": _iso(fecha_fin),
            "creado": _iso(datetime.now(timezone.utc)),
        }
        data["suscripciones"].append(row)
        _dev_save(data)
        return row
    data = {
        "usuario_id": usuario_id,
        "estado": estado,
        "plan": plan,
        "fecha_inicio": _iso(fecha_inicio),
        "fecha_fin": _iso(fecha_fin),
        "creado": _iso(datetime.now(timezone.utc)),
    }
    if _use_postgres():
        return _pg_write(
            """
            INSERT INTO saas_suscripciones (usuario_id, estado, plan, fecha_inicio, fecha_fin, creado)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                data["usuario_id"],
                data["estado"],
                data["plan"],
                data["fecha_inicio"],
                data["fecha_fin"],
                data["creado"],
            ),
        )
    rows = _supabase_req("POST", "/saas_suscripciones", json_data=data)
    if isinstance(rows, list) and rows:
        return rows[0]
    return db_get_suscripcion_by_usuario(usuario_id) or data


def db_update_suscripcion(suscripcion_id, updates):
    if DEV_MODE:
        data = _dev_load()
        for row in data["suscripciones"]:
            if int(row["id"]) == int(suscripcion_id):
                row.update(updates)
                _dev_save(data)
                return row
        return {}
    if _use_postgres():
        return _pg_update("saas_suscripciones", "id", suscripcion_id, updates)
    rows = _supabase_req("PATCH", f"/saas_suscripciones?id=eq.{suscripcion_id}", json_data=updates)
    return rows[0] if rows else {}


_PROJECT_ROW_FIELDS = (
    "id", "usuario_id", "name", "status", "revision", "schema_version",
    "payload", "last_operation_id", "created_at", "updated_at", "archived_at",
)


def _project_result(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = {field: deepcopy(row.get(field)) for field in _PROJECT_ROW_FIELDS}
    result["summary"] = project_summary(result["payload"])
    return result


def _project_retry_or_conflict(project_id: str, usuario_id: int, operation_id: str) -> dict:
    current = db_get_project(project_id, usuario_id)
    if current and current["last_operation_id"] == operation_id:
        return _project_result(current)
    return {}


def db_create_project(usuario_id: int, name: str, payload: dict) -> dict:
    now = _iso(datetime.now(timezone.utc))
    data = {
        "id": str(uuid.uuid4()),
        "usuario_id": int(usuario_id),
        "name": name,
        "status": "active",
        "revision": 0,
        "schema_version": deepcopy(payload["schema_version"]),
        "payload": deepcopy(payload),
        "last_operation_id": None,
        "created_at": now,
        "updated_at": now,
        "archived_at": None,
    }
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            store = _dev_load()
            store.setdefault("projects", []).append(deepcopy(data))
            _dev_save(store)
            return _project_result(data)
    if _use_postgres():
        row = _pg_write(
            """
            INSERT INTO saas_projects
                (id, usuario_id, name, status, revision, schema_version, payload,
                 last_operation_id, created_at, updated_at, archived_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            tuple(data[field] for field in _PROJECT_ROW_FIELDS),
        )
        return _project_result(row or data)
    rows = _supabase_req("POST", "/saas_projects", json_data=data)
    if isinstance(rows, list) and rows:
        return _project_result(rows[0])
    return db_get_project(data["id"], data["usuario_id"]) or _project_result(data)


def db_get_project(project_id: str, usuario_id: int) -> dict | None:
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            store = _dev_load()
            row = next(
                (
                    candidate for candidate in store.setdefault("projects", [])
                    if candidate.get("id") == project_id
                    and int(candidate.get("usuario_id", 0)) == int(usuario_id)
                ),
                None,
            )
            return _project_result(row)
    if _use_postgres():
        return _project_result(_pg_one(
            "SELECT * FROM saas_projects WHERE id = %s AND usuario_id = %s LIMIT 1",
            (project_id, usuario_id),
        ))
    rows = _supabase_req(
        "GET",
        "/saas_projects",
        params={"id": f"eq.{project_id}", "usuario_id": f"eq.{usuario_id}", "limit": "1"},
    )
    return _project_result(rows[0]) if rows else None


def db_list_projects(usuario_id: int, status: str) -> list[dict]:
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            store = _dev_load()
            rows = [
                row for row in store.setdefault("projects", [])
                if int(row.get("usuario_id", 0)) == int(usuario_id)
                and row.get("status") == status
            ]
            return [_project_result(row) for row in sorted(
                rows, key=lambda row: row.get("updated_at", ""), reverse=True
            )]
    if _use_postgres():
        rows = _pg_rows(
            "SELECT * FROM saas_projects WHERE usuario_id = %s AND status = %s ORDER BY updated_at DESC",
            (usuario_id, status),
        )
    else:
        rows = _supabase_req(
            "GET",
            "/saas_projects",
            params={
                "usuario_id": f"eq.{usuario_id}", "status": f"eq.{status}",
                "select": "*", "order": "updated_at.desc",
            },
        )
    return [_project_result(row) for row in rows]


def db_save_project(
    project_id: str, usuario_id: int, name: str, payload: dict, *,
    expected_revision: int, operation_id: str,
) -> dict:
    now = _iso(datetime.now(timezone.utc))
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            store = _dev_load()
            row = next(
                (
                    candidate for candidate in store.setdefault("projects", [])
                    if candidate.get("id") == project_id
                    and int(candidate.get("usuario_id", 0)) == int(usuario_id)
                ),
                None,
            )
            if row is None:
                return {}
            if row.get("last_operation_id") == operation_id:
                return _project_result(row)
            if int(row.get("revision", 0)) != int(expected_revision):
                return {}
            row.update({
                "name": name,
                "payload": deepcopy(payload),
                "schema_version": deepcopy(payload["schema_version"]),
                "revision": int(row["revision"]) + 1,
                "last_operation_id": operation_id,
                "updated_at": now,
            })
            _dev_save(store)
            return _project_result(row)
    if _use_postgres():
        row = _pg_write(
            """UPDATE saas_projects
SET name = %s,
    payload = %s,
    revision = revision + 1,
    last_operation_id = %s,
    updated_at = %s
WHERE id = %s
  AND usuario_id = %s
  AND revision = %s
RETURNING *""",
            (name, deepcopy(payload), operation_id, now, project_id, usuario_id, expected_revision),
        )
        return _project_result(row) if row else _project_retry_or_conflict(
            project_id, usuario_id, operation_id
        )
    rows = _supabase_req(
        "PATCH",
        f"/saas_projects?id=eq.{project_id}&usuario_id=eq.{usuario_id}&revision=eq.{expected_revision}",
        json_data={
            "name": name,
            "payload": deepcopy(payload),
            "schema_version": deepcopy(payload["schema_version"]),
            "revision": int(expected_revision) + 1,
            "last_operation_id": operation_id,
            "updated_at": now,
        },
    )
    return _project_result(rows[0]) if rows else _project_retry_or_conflict(
        project_id, usuario_id, operation_id
    )


def db_set_project_status(
    project_id: str, usuario_id: int, status: str, *, expected_revision: int,
    operation_id: str,
) -> dict:
    if status not in {"active", "archived"}:
        raise ValueError("Estado de proyecto invalido")
    now = _iso(datetime.now(timezone.utc))
    archived_at = now if status == "archived" else None
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            store = _dev_load()
            row = next(
                (
                    candidate for candidate in store.setdefault("projects", [])
                    if candidate.get("id") == project_id
                    and int(candidate.get("usuario_id", 0)) == int(usuario_id)
                ),
                None,
            )
            if row is None:
                return {}
            if row.get("last_operation_id") == operation_id:
                return _project_result(row)
            if int(row.get("revision", 0)) != int(expected_revision):
                return {}
            row.update({
                "status": status,
                "revision": int(row["revision"]) + 1,
                "last_operation_id": operation_id,
                "updated_at": now,
                "archived_at": archived_at,
            })
            _dev_save(store)
            return _project_result(row)
    if _use_postgres():
        row = _pg_write(
            """
            UPDATE saas_projects
            SET status = %s,
                archived_at = %s,
                revision = revision + 1,
                last_operation_id = %s,
                updated_at = %s
            WHERE id = %s
              AND usuario_id = %s
              AND revision = %s
            RETURNING *
            """,
            (status, archived_at, operation_id, now, project_id, usuario_id, expected_revision),
        )
        return _project_result(row) if row else _project_retry_or_conflict(
            project_id, usuario_id, operation_id
        )
    rows = _supabase_req(
        "PATCH",
        f"/saas_projects?id=eq.{project_id}&usuario_id=eq.{usuario_id}&revision=eq.{expected_revision}",
        json_data={
            "status": status,
            "revision": int(expected_revision) + 1,
            "last_operation_id": operation_id,
            "updated_at": now,
            "archived_at": archived_at,
        },
    )
    return _project_result(rows[0]) if rows else _project_retry_or_conflict(
        project_id, usuario_id, operation_id
    )


def db_delete_archived_project(
    project_id: str,
    usuario_id: int,
    *,
    expected_revision: int,
) -> bool:
    """Elimina sólo el registro archivado; conserva activos compartidos."""

    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            store = _dev_load()
            projects = store.setdefault("projects", [])
            index = next(
                (
                    position
                    for position, row in enumerate(projects)
                    if row.get("id") == project_id
                    and int(row.get("usuario_id", 0)) == int(usuario_id)
                    and row.get("status") == "archived"
                    and int(row.get("revision", 0)) == int(expected_revision)
                ),
                None,
            )
            if index is None:
                return False
            projects.pop(index)
            _dev_save(store)
            return True
    if _use_postgres():
        row = _pg_write(
            """
            DELETE FROM saas_projects
            WHERE id = %s
              AND usuario_id = %s
              AND status = 'archived'
              AND revision = %s
            RETURNING id
            """,
            (project_id, usuario_id, expected_revision),
        )
        return bool(row)
    rows = _supabase_req(
        "DELETE",
        (
            f"/saas_projects?id=eq.{project_id}"
            f"&usuario_id=eq.{usuario_id}"
            f"&status=eq.archived"
            f"&revision=eq.{expected_revision}"
        ),
    )
    return bool(rows)


def db_create_quote_job(usuario_id: int, template: str, metadata: dict, input_path: str, job_id: str = None):
    now = _iso(datetime.now(timezone.utc))
    data = {
        "id": job_id or str(uuid.uuid4()),
        "usuario_id": usuario_id,
        "status": "draft",
        "input_path": input_path,
        "output_path": None,
        "template": template,
        "metadata": metadata,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "attempt_token": None,
        "lease_expires_at": None,
    }
    if DEV_MODE:
        store = _dev_load()
        store["quote_jobs"].append(data)
        _dev_save(store)
        return data
    if _use_postgres():
        return _pg_write(
            """
            INSERT INTO saas_quote_jobs
                (id, usuario_id, status, input_path, output_path, template, metadata, error_message, created_at, updated_at, completed_at, attempt_token, lease_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                data["id"],
                data["usuario_id"],
                data["status"],
                data["input_path"],
                data["output_path"],
                data["template"],
                data["metadata"],
                data["error_message"],
                data["created_at"],
                data["updated_at"],
                data["completed_at"],
                data["attempt_token"],
                data["lease_expires_at"],
            ),
        )
    rows = _supabase_req("POST", "/saas_quote_jobs", json_data=data)
    if isinstance(rows, list) and rows:
        return rows[0]
    return db_get_quote_job(data["id"]) or data


def db_get_quote_job(job_id: str):
    if DEV_MODE:
        data = _dev_load()
        return next((j for j in data["quote_jobs"] if j["id"] == job_id), None)
    if _use_postgres():
        return _pg_one("SELECT * FROM saas_quote_jobs WHERE id = %s LIMIT 1", (job_id,))
    rows = _supabase_req("GET", "/saas_quote_jobs", params={"id": f"eq.{job_id}", "limit": "1"})
    return rows[0] if rows else None


def db_list_quote_jobs(usuario_id: int):
    if DEV_MODE:
        data = _dev_load()
        rows = [j for j in data["quote_jobs"] if int(j["usuario_id"]) == int(usuario_id)]
        return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)[:50]
    if _use_postgres():
        return _pg_rows(
            "SELECT * FROM saas_quote_jobs WHERE usuario_id = %s ORDER BY created_at DESC LIMIT 50",
            (usuario_id,),
        )
    return _supabase_req(
        "GET",
        "/saas_quote_jobs",
        params={
            "usuario_id": f"eq.{usuario_id}",
            "select": "*",
            "order": "created_at.desc",
            "limit": "50",
        },
    )


def db_update_quote_job(job_id: str, updates: dict, *, expected_status: str | None = None):
    payload = {**updates, "updated_at": _iso(datetime.now(timezone.utc))}
    if DEV_MODE:
        data = _dev_load()
        for row in data["quote_jobs"]:
            if row["id"] == job_id and (
                expected_status is None or row.get("status") == expected_status
            ):
                row.update(payload)
                _dev_save(data)
                return row
        return {}
    if _use_postgres():
        if expected_status is None:
            return _pg_update("saas_quote_jobs", "id", job_id, payload)
        set_clause = ", ".join(f"{key} = %s" for key in payload)
        return _pg_write(
            f"UPDATE saas_quote_jobs SET {set_clause} WHERE id = %s AND status = %s RETURNING *",
            tuple(payload.values()) + (job_id, expected_status),
        ) or {}
    path = f"/saas_quote_jobs?id=eq.{job_id}"
    if expected_status is not None:
        path += f"&status=eq.{expected_status}"
    rows = _supabase_req("PATCH", path, json_data=payload)
    return rows[0] if rows else {}


def db_delete_quote_job(job_id: str):
    if DEV_MODE:
        data = _dev_load()
        for index, row in enumerate(data["quote_jobs"]):
            if row["id"] == job_id:
                deleted = data["quote_jobs"].pop(index)
                _dev_save(data)
                return deleted
        return {}
    if _use_postgres():
        return _pg_write("DELETE FROM saas_quote_jobs WHERE id = %s RETURNING *", (job_id,)) or {}
    rows = _supabase_req("DELETE", f"/saas_quote_jobs?id=eq.{job_id}")
    return rows[0] if isinstance(rows, list) and rows else {}


def db_list_tarkett_reservations(status: str = "active"):
    if DEV_MODE:
        data = _dev_load()
        rows = data.setdefault("tarkett_reservations", [])
        return [row for row in rows if row.get("status") == status]
    if _use_postgres():
        return _pg_rows(
            "SELECT * FROM saas_tarkett_reservations WHERE status = %s ORDER BY created_at DESC",
            (status,),
        )
    return _supabase_req(
        "GET",
        "/saas_tarkett_reservations",
        params={"status": f"eq.{status}", "select": "*", "order": "created_at.desc"},
    )


_MIXED_RESERVATION_CATALOGS = (
    "tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro",
    "jome", "lauco",
)


def _mixed_reservation_decimal(value, field, *, positive):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,6})?", value
    ):
        raise RuntimeError("Reserva mixta invalida")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError("Reserva mixta invalida") from None
    scale = max(-number.as_tuple().exponent, 0)
    lower_ok = number > 0 if positive else number >= 0
    maximum = Decimal("1000000") if positive else Decimal("1000000000")
    if not number.is_finite() or not lower_ok or number > maximum or scale > 6:
        raise RuntimeError("Reserva mixta invalida")
    return f"{number:.6f}"


def _mixed_reservation_text(value, field, *, allow_empty=False):
    if not isinstance(value, str):
        raise RuntimeError("Reserva mixta invalida")
    text = value.strip()
    if (not text and not allow_empty) or len(text) > 500:
        raise RuntimeError("Reserva mixta invalida")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in text):
        raise RuntimeError("Reserva mixta invalida")
    return text


def _mixed_reservation_result_decimal(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,6})?", value
    ):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    number = Decimal(value)
    if not number.is_finite() or number > Decimal("99999999999999.999999"):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    return f"{number:.6f}"


def _normalize_mixed_reservation_groups(groups):
    if not isinstance(groups, list) or len(groups) > 7:
        raise RuntimeError("Reserva mixta invalida")
    normalized = []
    seen_catalogs = set()
    seen_keys = set()
    total = 0
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"catalog", "items"}:
            raise RuntimeError("Reserva mixta invalida")
        catalog = str(group.get("catalog") or "").strip()
        items = group.get("items")
        if catalog not in _MIXED_RESERVATION_CATALOGS or catalog in seen_catalogs:
            raise RuntimeError("Reserva mixta invalida")
        if not isinstance(items, list) or not items:
            raise RuntimeError("Reserva mixta invalida")
        seen_catalogs.add(catalog)
        clean_items = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"identity", "sku", "quantity", "stock"}:
                raise RuntimeError("Reserva mixta invalida")
            identity = _mixed_reservation_text(item.get("identity"), "identity")
            sku = _mixed_reservation_text(
                item.get("sku"), "sku", allow_empty=catalog in {"sonara", "lumbro"}
            )
            key = (catalog, identity)
            if key in seen_keys:
                raise RuntimeError("Reserva mixta invalida")
            seen_keys.add(key)
            clean_items.append({
                "identity": identity,
                "sku": sku,
                "quantity": _mixed_reservation_decimal(
                    item.get("quantity"), "quantity", positive=True
                ),
                "stock": _mixed_reservation_decimal(
                    item.get("stock"), "stock", positive=False
                ),
            })
            total += 1
        normalized.append({
            "catalog": catalog,
            "items": sorted(clean_items, key=lambda row: row["identity"]),
        })
    if total:
        try:
            validate_quote_size(section_counts=[total], encoded_bytes=0)
        except ValueError as exc:
            raise RuntimeError(f"Reserva mixta invalida: {exc}") from exc
    return sorted(
        normalized, key=lambda group: _MIXED_RESERVATION_CATALOGS.index(group["catalog"])
    )


def _dev_reserve_mixed_cart(clean_user_id, clean_job_id, normalized):
    with _MIXED_CART_RESERVATION_LOCK:
        data = _dev_load()
        job = next(
            (row for row in data.get("quote_jobs", []) if str(row.get("id")) == clean_job_id),
            None,
        )
        if (
            not job
            or int(job.get("usuario_id") or 0) != clean_user_id
            or job.get("status") != "draft"
        ):
            raise RuntimeError("Cotizacion de reserva mixta invalida")

        tables = {
            "tarkett": data.setdefault("tarkett_reservations", []),
            "offiho": data.setdefault("offiho_reservations", []),
            "supplier": data.setdefault("catalog_reservations", []),
        }
        if any(
            str(row.get("quote_job_id") or "") == clean_job_id
            for rows in tables.values() for row in rows
        ):
            raise RuntimeError("La cotizacion ya tiene reservas mixtas")

        now = _iso(datetime.now(timezone.utc))
        snapshot = []
        pending = {name: [] for name in tables}
        for group in normalized:
            catalog = group["catalog"]
            table_name = catalog if catalog in {"tarkett", "offiho"} else "supplier"
            identity_field = "product_code" if table_name != "supplier" else "internal_id"
            for item in group["items"]:
                reserved_before = Decimal(0)
                reserved_by_others = False
                for row in tables[table_name]:
                    same_identity = row.get(identity_field) == item["identity"]
                    same_supplier = table_name != "supplier" or row.get("supplier") == catalog
                    if not same_identity or not same_supplier or row.get("status") != "active":
                        continue
                    try:
                        stored = Decimal(str(row.get("quantity")))
                    except (InvalidOperation, TypeError, ValueError):
                        raise RuntimeError("Reserva mixta almacenada invalida") from None
                    if not stored.is_finite() or stored <= 0:
                        raise RuntimeError("Reserva mixta almacenada invalida")
                    reserved_before += stored
                    if reserved_before > Decimal("99999999999999.999999"):
                        raise RuntimeError("Reserva mixta almacenada invalida")
                    reserved_by_others |= int(row.get("usuario_id") or 0) != clean_user_id

                quantity = Decimal(item["quantity"])
                stock = Decimal(item["stock"])
                available_before = max(stock - reserved_before, Decimal(0))
                snapshot.append({
                    "catalog": catalog,
                    "identity": item["identity"],
                    "reserved_before": f"{reserved_before:.6f}",
                    "available_before": f"{available_before:.6f}",
                    "insufficient": quantity > available_before,
                    "reserved_by_others": reserved_by_others,
                })
                common = {
                    "id": str(uuid.uuid4()), "usuario_id": clean_user_id,
                    "quote_job_id": clean_job_id, "quantity": item["quantity"],
                    "status": "active", "created_at": now, "updated_at": now,
                }
                if table_name == "supplier":
                    pending[table_name].append({
                        **common, "supplier": catalog, "internal_id": item["identity"],
                        "sku": item["sku"],
                    })
                else:
                    pending[table_name].append({
                        **common, "product_code": item["identity"]
                    })

        if snapshot:
            for name, rows in pending.items():
                tables[name].extend(rows)
            _dev_save(data)
        return snapshot


def _validate_mixed_reservation_response(response, normalized):
    fields = {
        "catalog", "identity", "reserved_before", "available_before",
        "insufficient", "reserved_by_others",
    }
    expected = {
        (group["catalog"], item["identity"]): item
        for group in normalized for item in group["items"]
    }
    if not isinstance(response, list) or len(response) != len(expected):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    seen = set()
    result = []
    for candidate in response:
        if not isinstance(candidate, dict) or set(candidate) != fields:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        key = (candidate.get("catalog"), candidate.get("identity"))
        if key not in expected or key in seen:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        seen.add(key)
        reserved = _mixed_reservation_result_decimal(candidate.get("reserved_before"))
        available = _mixed_reservation_result_decimal(candidate.get("available_before"))
        if type(candidate.get("insufficient")) is not bool or type(
            candidate.get("reserved_by_others")
        ) is not bool:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        item = expected[key]
        expected_available = max(
            Decimal(item["stock"]) - Decimal(reserved), Decimal(0)
        )
        expected_insufficient = Decimal(item["quantity"]) > expected_available
        if Decimal(available) != expected_available or candidate["insufficient"] != expected_insufficient:
            raise RuntimeError("Respuesta de reserva mixta invalida")
        result.append({
            "catalog": key[0], "identity": key[1],
            "reserved_before": reserved, "available_before": available,
            "insufficient": candidate["insufficient"],
            "reserved_by_others": candidate["reserved_by_others"],
        })
    if seen != set(expected):
        raise RuntimeError("Respuesta de reserva mixta invalida")
    return sorted(
        result,
        key=lambda row: (
            _MIXED_RESERVATION_CATALOGS.index(row["catalog"]), row["identity"]
        ),
    )


def db_reserve_mixed_cart(usuario_id, quote_job_id, groups):
    try:
        clean_user_id = int(usuario_id)
        clean_job_id = str(uuid.UUID(str(quote_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Cotizacion de reserva mixta invalida") from None
    if clean_user_id <= 0:
        raise RuntimeError("Cotizacion de reserva mixta invalida")
    normalized = _normalize_mixed_reservation_groups(groups)
    if DEV_MODE:
        response = _dev_reserve_mixed_cart(clean_user_id, clean_job_id, normalized)
    elif _use_postgres():
        rows = _pg_rows(
            "SELECT saas_reserve_mixed_cart(%s, %s, %s::jsonb) AS snapshot",
            (clean_user_id, clean_job_id, json.dumps(normalized, separators=(",", ":"))),
        )
        response = rows[0].get("snapshot") if len(rows) == 1 else None
    else:
        response = _supabase_req(
            "POST",
            "/rpc/saas_reserve_mixed_cart",
            json_data={
                "p_usuario_id": clean_user_id,
                "p_quote_job_id": clean_job_id,
                "p_groups": normalized,
            },
        )
    return _validate_mixed_reservation_response(response, normalized)


def _legacy_reservation_decimal(value, field: str, *, positive: bool) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RuntimeError("Reserva mixta invalida") from None
    maximum = Decimal("1000000") if positive else Decimal("1000000000")
    lower_ok = number > 0 if positive else number >= 0
    if not number.is_finite() or not lower_ok or number > maximum:
        raise RuntimeError("Reserva mixta invalida")
    normalized = number.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if positive and normalized <= 0:
        raise RuntimeError("Reserva mixta invalida")
    return f"{normalized:.6f}"


def _legacy_mixed_group(catalog: str, lines: list[dict]) -> list[dict]:
    identity_field = "code" if catalog == "tarkett" else "inventory_key"
    return [{
        "catalog": catalog,
        "items": [{
            "identity": str(line[identity_field]),
            "sku": str(line.get("sku") or line[identity_field]),
            "quantity": _legacy_reservation_decimal(
                line["quantity"], "quantity", positive=True
            ),
            "stock": _legacy_reservation_decimal(
                line["available_quantity"], "stock", positive=False
            ),
        } for line in lines],
    }]


def _legacy_mixed_reservation_rows(catalog, usuario_id, quote_job_id, lines, snapshots):
    identity_field = "code" if catalog == "tarkett" else "inventory_key"
    snapshots_by_identity = {row["identity"]: row for row in snapshots}
    return [
        {
            "usuario_id": int(usuario_id),
            "quote_job_id": str(quote_job_id),
            "product_code": str(line[identity_field]),
            "quantity": _legacy_reservation_decimal(
                line["quantity"], "quantity", positive=True
            ),
            "status": "active",
            "reserved_before": snapshots_by_identity[str(line[identity_field])]["reserved_before"],
            "available_before": snapshots_by_identity[str(line[identity_field])]["available_before"],
            "insufficient": snapshots_by_identity[str(line[identity_field])]["insufficient"],
            "reserved_by_others": snapshots_by_identity[str(line[identity_field])]["reserved_by_others"],
        }
        for line in lines
    ]


def db_create_tarkett_reservations(usuario_id: int, quote_job_id: str, lines: list[dict]):
    snapshots = db_reserve_mixed_cart(
        usuario_id, quote_job_id, _legacy_mixed_group("tarkett", lines)
    )
    return _legacy_mixed_reservation_rows(
        "tarkett", usuario_id, quote_job_id, lines, snapshots
    )


def db_release_tarkett_reservations(quote_job_id: str):
    now = _iso(datetime.now(timezone.utc))
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            data = _dev_load()
            released = []
            for row in data.setdefault("tarkett_reservations", []):
                if str(row.get("quote_job_id")) == str(quote_job_id) and row.get("status") == "active":
                    row["status"] = "released"
                    row["updated_at"] = now
                    released.append(row)
            if released:
                _dev_save(data)
            return released
    if _use_postgres():
        return _pg_rows(
            """
            UPDATE saas_tarkett_reservations
            SET status = 'released', updated_at = %s
            WHERE quote_job_id = %s AND status = 'active'
            RETURNING *
            """,
            (now, quote_job_id),
        )
    return _supabase_req(
        "PATCH",
        f"/saas_tarkett_reservations?quote_job_id=eq.{quote_job_id}&status=eq.active",
        json_data={"status": "released", "updated_at": now},
    )


def db_list_offiho_reservations(status: str = "active"):
    if DEV_MODE:
        data = _dev_load()
        rows = data.setdefault("offiho_reservations", [])
        return [row for row in rows if row.get("status") == status]
    if _use_postgres():
        return _pg_rows(
            "SELECT * FROM saas_offiho_reservations WHERE status = %s ORDER BY created_at DESC",
            (status,),
        )
    return _supabase_req(
        "GET",
        "/saas_offiho_reservations",
        params={"status": f"eq.{status}", "select": "*", "order": "created_at.desc"},
    )


def db_create_offiho_reservations(usuario_id: int, quote_job_id: str, lines: list[dict]):
    snapshots = db_reserve_mixed_cart(
        usuario_id, quote_job_id, _legacy_mixed_group("offiho", lines)
    )
    return _legacy_mixed_reservation_rows(
        "offiho", usuario_id, quote_job_id, lines, snapshots
    )


def db_release_offiho_reservations(quote_job_id: str):
    now = _iso(datetime.now(timezone.utc))
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            data = _dev_load()
            released = []
            for row in data.setdefault("offiho_reservations", []):
                if str(row.get("quote_job_id")) == str(quote_job_id) and row.get("status") == "active":
                    row["status"] = "released"
                    row["updated_at"] = now
                    released.append(row)
            if released:
                _dev_save(data)
            return released
    if _use_postgres():
        return _pg_rows(
            """
            UPDATE saas_offiho_reservations
            SET status = 'released', updated_at = %s
            WHERE quote_job_id = %s AND status = 'active'
            RETURNING *
            """,
            (now, quote_job_id),
        )
    return _supabase_req(
        "PATCH",
        f"/saas_offiho_reservations?quote_job_id=eq.{quote_job_id}&status=eq.active",
        json_data={"status": "released", "updated_at": now},
    )


def _dev_release_mixed_cart(clean_job_id):
    with _MIXED_CART_RESERVATION_LOCK:
        data = _dev_load()
        job = next(
            (row for row in data.get("quote_jobs", []) if str(row.get("id")) == clean_job_id),
            None,
        )
        if not job:
            raise RuntimeError("Cotizacion de reserva mixta invalida")
        now = _iso(datetime.now(timezone.utc))
        changed = False
        if job.get("status") == "draft":
            job.update({
                "status": "failed",
                "error_message": job.get("error_message") or "mixed reservations released",
                "updated_at": now,
            })
            changed = True
        counts = {"tarkett": 0, "offiho": 0, "supplier": 0}
        for name, key in (
            ("tarkett_reservations", "tarkett"),
            ("offiho_reservations", "offiho"),
            ("catalog_reservations", "supplier"),
        ):
            for row in data.setdefault(name, []):
                if (
                    str(row.get("quote_job_id") or "") == clean_job_id
                    and row.get("status") == "active"
                ):
                    row.update({"status": "released", "updated_at": now})
                    counts[key] += 1
                    changed = True
        if changed:
            _dev_save(data)
        return counts


def _validate_mixed_release_response(response):
    if (
        not isinstance(response, dict)
        or set(response) != {"tarkett", "offiho", "supplier"}
        or any(type(response[key]) is not int or response[key] < 0 for key in response)
    ):
        raise RuntimeError("Respuesta de liberacion mixta invalida")
    return response


def db_release_mixed_cart(quote_job_id: str) -> dict:
    try:
        clean_job_id = str(uuid.UUID(str(quote_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Cotizacion de reserva mixta invalida") from None
    if DEV_MODE:
        response = _dev_release_mixed_cart(clean_job_id)
    elif _use_postgres():
        rows = _pg_rows(
            "SELECT saas_release_mixed_cart(%s) AS snapshot", (clean_job_id,)
        )
        response = rows[0].get("snapshot") if len(rows) == 1 else None
    else:
        response = _supabase_req(
            "POST",
            "/rpc/saas_release_mixed_cart",
            json_data={"p_quote_job_id": clean_job_id},
        )
    return _validate_mixed_release_response(response)


def db_queue_mixed_quote_job(quote_job_id: str, metadata: dict) -> dict:
    try:
        clean_job_id = str(uuid.UUID(str(quote_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Cotizacion mixta invalida") from None
    if not isinstance(metadata, dict):
        raise RuntimeError("Cotizacion mixta invalida")
    now = _iso(datetime.now(timezone.utc))
    payload = {
        "status": "queued", "metadata": deepcopy(metadata),
        "error_message": None, "attempt_token": None,
        "lease_expires_at": None, "updated_at": now,
    }
    if DEV_MODE:
        with _MIXED_CART_RESERVATION_LOCK:
            data = _dev_load()
            matches = [
                row for row in data.get("quote_jobs", [])
                if str(row.get("id")) == clean_job_id
            ]
            if len(matches) != 1 or matches[0].get("status") != "draft":
                raise RuntimeError("La cotizacion mixta ya no esta en borrador")
            matches[0].update(payload)
            _dev_save(data)
            row = deepcopy(matches[0])
    elif _use_postgres():
        row = _pg_write(
            """
            UPDATE saas_quote_jobs
            SET status = 'queued', metadata = %s, error_message = NULL,
                attempt_token = NULL, lease_expires_at = NULL, updated_at = %s
            WHERE id = %s AND status = 'draft'
            RETURNING *
            """,
            (payload["metadata"], now, clean_job_id),
        )
    else:
        rows = _supabase_req(
            "PATCH", "/saas_quote_jobs",
            params={"id": f"eq.{clean_job_id}", "status": "eq.draft"},
            json_data=payload,
        )
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
    if not isinstance(row, dict) or row.get("status") != "queued":
        raise RuntimeError("La cotizacion mixta ya no esta en borrador")
    return row


def _catalog_supplier(value: object) -> str:
    supplier = str(value or "").strip().lower()
    if supplier not in ALLOWED_SUPPLIERS:
        raise RuntimeError("Proveedor de catalogo no permitido")
    return supplier


def _require_catalog_service_backend() -> None:
    if not DEV_MODE and not DATABASE_URL and not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY requerida para catalogos")


def db_get_catalog_source(supplier: str) -> dict | None:
    supplier = _catalog_supplier(supplier)
    if DEV_MODE:
        rows = _dev_load().setdefault("catalog_sources", [])
        return next((row for row in rows if row.get("supplier") == supplier and row.get("enabled", True)), None)
    _require_catalog_service_backend()
    if _use_postgres():
        return _pg_one(
            """
            SELECT id, supplier, label, adapter, enabled, published_version_id
            FROM saas_catalog_sources
            WHERE supplier = %s AND enabled IS TRUE
            LIMIT 1
            """,
            (supplier,),
        )
    rows = _supabase_req(
        "GET",
        "/saas_catalog_sources",
        params={
            "supplier": f"eq.{supplier}",
            "enabled": "eq.true",
            "select": "id,supplier,label,adapter,enabled,published_version_id",
            "limit": "1",
        },
    )
    return rows[0] if isinstance(rows, list) and rows else None


def db_get_published_catalog_snapshot(supplier: str) -> dict | None:
    supplier = _catalog_supplier(supplier)
    if DEV_MODE:
        return _dev_load().setdefault("catalog_published_snapshots", {}).get(supplier)
    source = db_get_catalog_source(supplier)
    version_id = source.get("published_version_id") if isinstance(source, dict) else None
    if not version_id:
        return None
    if _use_postgres():
        row = _pg_one(
            """
            SELECT id, supplier, source_hash, generated_at, status, payload, created_at
            FROM saas_catalog_snapshot_versions
            WHERE id = %s AND supplier = %s AND status = 'published'
            LIMIT 1
            """,
            (version_id, supplier),
        )
    else:
        rows = _supabase_req(
            "GET",
            "/saas_catalog_snapshot_versions",
            params={
                "id": f"eq.{version_id}",
                "supplier": f"eq.{supplier}",
                "status": "eq.published",
                "select": "id,supplier,source_hash,generated_at,status,payload,created_at",
                "limit": "1",
            },
        )
        row = rows[0] if isinstance(rows, list) and rows else None
    if row:
        row = {**row, "source_id": source.get("id"), "label": source.get("label")}
    return row


def db_list_catalog_reservations(supplier: str, status: str = "active") -> list[dict]:
    supplier = _catalog_supplier(supplier)
    if status not in {"active", "released"}:
        raise RuntimeError("Estado de reserva invalido")
    if DEV_MODE:
        rows = _dev_load().setdefault("catalog_reservations", [])
        return [row for row in rows if row.get("supplier") == supplier and row.get("status") == status]
    _require_catalog_service_backend()
    if _use_postgres():
        return _pg_rows(
            """
            SELECT id, supplier, internal_id, sku, quantity, usuario_id,
                   quote_job_id, status, created_at, updated_at
            FROM saas_catalog_reservations
            WHERE supplier = %s AND status = %s
            ORDER BY created_at DESC
            """,
            (supplier, status),
        )
    rows: list[dict] = []
    page_size = 1000
    while True:
        page = _supabase_req(
            "GET",
            "/saas_catalog_reservations",
            params={
                "supplier": f"eq.{supplier}",
                "status": f"eq.{status}",
                "select": "id,supplier,internal_id,sku,quantity,usuario_id,quote_job_id,status,created_at,updated_at",
                "order": "created_at.desc,id.desc",
                "limit": str(page_size),
                "offset": str(len(rows)),
            },
        )
        if not isinstance(page, list):
            raise RuntimeError("Respuesta de reservas de catalogo invalida")
        rows.extend(page)
        if len(page) < page_size:
            return rows


def db_catalog_reservation_summary(supplier: str, usuario_id: int) -> list[dict]:
    supplier = _catalog_supplier(supplier)
    try:
        clean_user_id = int(usuario_id)
    except (TypeError, ValueError):
        raise RuntimeError("Usuario de reservas invalido") from None
    if clean_user_id <= 0:
        raise RuntimeError("Usuario de reservas invalido")

    if DEV_MODE:
        totals: dict[str, Decimal] = {}
        reserved_by_others: set[str] = set()
        for row in _dev_load().setdefault("catalog_reservations", []):
            if row.get("supplier") != supplier or row.get("status") != "active":
                continue
            internal_id = str(row.get("internal_id") or "").strip()
            try:
                quantity = Decimal(str(row.get("quantity") or 0))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if not internal_id or not quantity.is_finite() or quantity <= 0:
                continue
            totals[internal_id] = totals.get(internal_id, Decimal(0)) + quantity
            if int(row.get("usuario_id") or 0) != clean_user_id:
                reserved_by_others.add(internal_id)
        return [
            {
                "internal_id": internal_id,
                "reserved_quantity": f"{quantity:.6f}",
                "reserved_by_others": internal_id in reserved_by_others,
            }
            for internal_id, quantity in sorted(totals.items())
        ]

    _require_catalog_service_backend()
    if _use_postgres():
        return _pg_rows(
            """
            SELECT internal_id,
                   SUM(quantity)::NUMERIC(18,6) AS reserved_quantity,
                   BOOL_OR(usuario_id <> %s) AS reserved_by_others
            FROM saas_catalog_reservations
            WHERE supplier = %s AND status = 'active'
            GROUP BY internal_id
            ORDER BY internal_id
            """,
            (clean_user_id, supplier),
        )
    rows = _supabase_req(
        "POST",
        "/rpc/saas_catalog_reservation_summary",
        json_data={"p_supplier": supplier, "p_usuario_id": clean_user_id},
    )
    if not isinstance(rows, list):
        raise RuntimeError("Respuesta de resumen de reservas invalida")
    return rows


def db_reserve_catalog_items(
    usuario_id: int,
    quote_job_id: str,
    supplier: str,
    lines: list[dict],
) -> list[dict]:
    supplier = _catalog_supplier(supplier)
    try:
        clean_user_id = int(usuario_id)
        clean_job_id = str(uuid.UUID(str(quote_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Reserva de catalogo invalida") from None
    if clean_user_id <= 0 or not isinstance(lines, list) or not lines:
        raise RuntimeError("Reserva de catalogo invalida")
    try:
        validate_quote_size(section_counts=[len(lines)], encoded_bytes=0)
    except ValueError as exc:
        raise RuntimeError(f"Reserva de catalogo invalida: {exc}") from exc

    normalized = []
    internal_ids = set()
    for line in lines:
        if not isinstance(line, dict) or set(line) != {"internal_id", "sku", "quantity", "stock"}:
            raise RuntimeError("Reserva de catalogo invalida")
        internal_id = str(line.get("internal_id") or "").strip()
        sku = str(line.get("sku") or "").strip()
        try:
            quantity = Decimal(str(line.get("quantity")))
            stock = Decimal(str(line.get("stock")))
        except (InvalidOperation, TypeError, ValueError):
            raise RuntimeError("Reserva de catalogo invalida") from None
        quantity_scale = max(-quantity.as_tuple().exponent, 0)
        stock_scale = max(-stock.as_tuple().exponent, 0)
        if (
            not internal_id
            or len(internal_id) > 300
            or not sku
            or len(sku) > 300
            or internal_id in internal_ids
            or not quantity.is_finite()
            or quantity <= 0
            or quantity_scale > 6
            or not stock.is_finite()
            or stock < 0
            or stock_scale > 6
        ):
            raise RuntimeError("Reserva de catalogo invalida")
        internal_ids.add(internal_id)
        normalized.append(
            {
                "internal_id": internal_id,
                "sku": sku,
                "quantity": format(quantity, "f"),
                "stock": format(stock, "f"),
            }
        )

    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            data = _dev_load()
            job = next(
                (row for row in data.get("quote_jobs", []) if str(row.get("id")) == clean_job_id),
                None,
            )
            if (
                not job
                or int(job.get("usuario_id") or 0) != clean_user_id
                or job.get("status") != "draft"
            ):
                raise RuntimeError("Cotizacion de reserva invalida")
            reservations = data.setdefault("catalog_reservations", [])
            if any(
                row.get("supplier") == supplier
                and str(row.get("quote_job_id") or "") == clean_job_id
                for row in reservations
            ):
                raise RuntimeError("La cotizacion ya tiene reservas")

            now = _iso(datetime.now(timezone.utc))
            snapshot = []
            new_rows = []
            for line in sorted(normalized, key=lambda row: row["internal_id"]):
                reserved_before = Decimal(0)
                reserved_by_others = False
                for row in reservations:
                    if (
                        row.get("supplier") != supplier
                        or row.get("internal_id") != line["internal_id"]
                        or row.get("status") != "active"
                    ):
                        continue
                    try:
                        reserved_before += Decimal(str(row.get("quantity") or 0))
                    except (InvalidOperation, TypeError, ValueError):
                        raise RuntimeError("Reserva de catalogo almacenada invalida") from None
                    if int(row.get("usuario_id") or 0) != clean_user_id:
                        reserved_by_others = True
                quantity = Decimal(line["quantity"])
                stock = Decimal(line["stock"])
                available_before = max(stock - reserved_before, Decimal(0))
                snapshot.append(
                    {
                        "internal_id": line["internal_id"],
                        "reserved_before": f"{reserved_before:.6f}",
                        "available_before": f"{available_before:.6f}",
                        "insufficient": quantity > available_before,
                        "reserved_by_others": reserved_by_others,
                    }
                )
                new_rows.append(
                    {
                        "id": str(uuid.uuid4()),
                        "supplier": supplier,
                        "internal_id": line["internal_id"],
                        "sku": line["sku"],
                        "quantity": line["quantity"],
                        "usuario_id": clean_user_id,
                        "quote_job_id": clean_job_id,
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            reservations.extend(new_rows)
            _dev_save(data)
            return snapshot

    _require_catalog_service_backend()
    if _use_postgres():
        rows = _pg_rows(
            "SELECT * FROM saas_reserve_catalog_items(%s, %s, %s, %s::jsonb)",
            (clean_user_id, clean_job_id, supplier, json.dumps(normalized, separators=(",", ":"))),
        )
    else:
        rows = _supabase_req(
            "POST",
            "/rpc/saas_reserve_catalog_items",
            json_data={
                "p_usuario_id": clean_user_id,
                "p_quote_job_id": clean_job_id,
                "p_supplier": supplier,
                "p_lines": normalized,
            },
        )
    if not isinstance(rows, list) or len(rows) != len(normalized):
        raise RuntimeError("Respuesta de reserva de catalogo invalida")

    expected_ids = {line["internal_id"] for line in normalized}
    snapshot = []
    seen_ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Respuesta de reserva de catalogo invalida")
        internal_id = str(row.get("internal_id") or "").strip()
        try:
            reserved_before = Decimal(str(row.get("reserved_before")))
            available_before = Decimal(str(row.get("available_before")))
        except (InvalidOperation, TypeError, ValueError):
            raise RuntimeError("Respuesta de reserva de catalogo invalida") from None
        if (
            internal_id not in expected_ids
            or internal_id in seen_ids
            or not reserved_before.is_finite()
            or reserved_before < 0
            or not available_before.is_finite()
            or available_before < 0
            or not isinstance(row.get("insufficient"), bool)
            or not isinstance(row.get("reserved_by_others"), bool)
        ):
            raise RuntimeError("Respuesta de reserva de catalogo invalida")
        seen_ids.add(internal_id)
        snapshot.append(
            {
                "internal_id": internal_id,
                "reserved_before": f"{reserved_before:.6f}",
                "available_before": f"{available_before:.6f}",
                "insufficient": row["insufficient"],
                "reserved_by_others": row["reserved_by_others"],
            }
        )
    if seen_ids != expected_ids:
        raise RuntimeError("Respuesta de reserva de catalogo invalida")
    return snapshot


def db_create_catalog_reservations(
    usuario_id: int,
    quote_job_id: str,
    supplier: str,
    lines: list[dict],
) -> list[dict]:
    supplier = _catalog_supplier(supplier)
    now = _iso(datetime.now(timezone.utc))
    aggregated: dict[str, dict] = {}
    for line in lines:
        internal_id = str(line.get("internal_id") or "").strip()
        sku = str(line.get("sku") or "").strip()
        if not internal_id or not sku:
            raise RuntimeError("Reserva de catalogo invalida")
        try:
            quantity = Decimal(str(line.get("quantity")))
        except (InvalidOperation, TypeError, ValueError):
            raise RuntimeError("Cantidad de reserva invalida") from None
        if not quantity.is_finite() or quantity <= 0:
            raise RuntimeError("Cantidad de reserva invalida")
        if internal_id in aggregated:
            aggregated[internal_id]["quantity"] += quantity
        else:
            aggregated[internal_id] = {"internal_id": internal_id, "sku": sku, "quantity": quantity}
    rows = [
        {
            "id": str(uuid.uuid4()),
            "supplier": supplier,
            "internal_id": line["internal_id"],
            "sku": line["sku"],
            "quantity": format(line["quantity"], "f"),
            "usuario_id": int(usuario_id),
            "quote_job_id": str(quote_job_id),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        for line in aggregated.values()
    ]
    if not rows:
        return []
    if DEV_MODE:
        data = _dev_load()
        data.setdefault("catalog_reservations", []).extend(rows)
        _dev_save(data)
        return rows
    _require_catalog_service_backend()
    if _use_postgres():
        created = []
        for row in rows:
            saved = _pg_write(
                """
                INSERT INTO saas_catalog_reservations
                    (id, supplier, internal_id, sku, quantity, usuario_id,
                     quote_job_id, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    row["id"], row["supplier"], row["internal_id"], row["sku"],
                    row["quantity"], row["usuario_id"], row["quote_job_id"],
                    row["status"], row["created_at"], row["updated_at"],
                ),
            )
            if saved:
                created.append(saved)
        return created
    created = _supabase_req("POST", "/saas_catalog_reservations", json_data=rows)
    return created if isinstance(created, list) else rows


def db_release_catalog_reservations(quote_job_id: str) -> list[dict]:
    now = _iso(datetime.now(timezone.utc))
    if DEV_MODE:
        with _DEV_CATALOG_RESERVATION_LOCK:
            data = _dev_load()
            released = []
            for row in data.setdefault("catalog_reservations", []):
                if str(row.get("quote_job_id")) == str(quote_job_id) and row.get("status") == "active":
                    row["status"] = "released"
                    row["updated_at"] = now
                    released.append(row)
            if released:
                _dev_save(data)
            return released
    _require_catalog_service_backend()
    if _use_postgres():
        return _pg_rows(
            """
            UPDATE saas_catalog_reservations
            SET status = 'released', updated_at = %s
            WHERE quote_job_id = %s AND status = 'active'
            RETURNING *
            """,
            (now, quote_job_id),
        )
    rows = _supabase_req(
        "PATCH",
        "/saas_catalog_reservations",
        params={"quote_job_id": f"eq.{quote_job_id}", "status": "eq.active"},
        json_data={"status": "released", "updated_at": now},
    )
    return rows if isinstance(rows, list) else []


def db_list_exchange_rates() -> list[dict]:
    if DEV_MODE:
        rows = _dev_load().setdefault("exchange_rates", [])
    else:
        _require_catalog_service_backend()
        if _use_postgres():
            rows = _pg_rows(
                """
                SELECT currency, effective_date, mxn_per_unit, retrieved_at
                FROM saas_exchange_rates
                ORDER BY effective_date DESC, currency ASC
                LIMIT 30
                """
            )
        else:
            rows = _supabase_req(
                "GET",
                "/saas_exchange_rates",
                params={
                    "select": "currency,effective_date,mxn_per_unit,retrieved_at",
                    "order": "effective_date.desc,currency.asc",
                    "limit": "30",
                },
            )
    normalized = [
        {
            "currency": str(row.get("currency") or ""),
            "effective_date": row.get("effective_date").isoformat()
            if isinstance(row.get("effective_date"), date)
            else str(row.get("effective_date") or ""),
            "mxn_per_unit": str(row.get("mxn_per_unit") or ""),
            "retrieved_at": row.get("retrieved_at").isoformat()
            if isinstance(row.get("retrieved_at"), datetime)
            else str(row.get("retrieved_at") or ""),
        }
        for row in (rows if isinstance(rows, (list, tuple)) else [])
    ]
    latest_usd = _fetch_latest_usd_mxn_row()
    if latest_usd is not None:
        normalized = [
            row for row in normalized if row["currency"].upper() != "USD"
        ]
        normalized.insert(0, latest_usd)
    return normalized


def db_list_catalog_sync_runs(limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    if DEV_MODE:
        rows = _dev_load().setdefault("catalog_sync_runs", [])
        return sorted(rows, key=lambda row: row.get("requested_at", ""), reverse=True)[:limit]
    _require_catalog_service_backend()
    if _use_postgres():
        return _pg_rows(
            """
            SELECT r.*, s.supplier, s.label
            FROM saas_catalog_sync_runs r
            JOIN saas_catalog_sources s ON s.id = r.source_id
            ORDER BY r.requested_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    rows = _supabase_req(
        "GET",
        "/saas_catalog_sync_runs",
        params={"select": "*", "order": "requested_at.desc", "limit": str(limit)},
    )
    sources = _supabase_req(
        "GET",
        "/saas_catalog_sources",
        params={"select": "id,supplier,label", "limit": "100"},
    )
    by_id = {str(row.get("id")): row for row in sources if isinstance(row, dict)} if isinstance(sources, list) else {}
    return [
        {**row, **{key: source.get(key) for key in ("supplier", "label")}}
        for row in (rows if isinstance(rows, list) else [])
        for source in [by_id.get(str(row.get("source_id")), {})]
    ]


def db_get_catalog_sync_run(run_id: str) -> dict | None:
    if DEV_MODE:
        return next((row for row in _dev_load().setdefault("catalog_sync_runs", []) if str(row.get("id")) == str(run_id)), None)
    _require_catalog_service_backend()
    if _use_postgres():
        return _pg_one(
            """
            SELECT r.*, s.supplier, s.label
            FROM saas_catalog_sync_runs r
            JOIN saas_catalog_sources s ON s.id = r.source_id
            WHERE r.id = %s
            LIMIT 1
            """,
            (run_id,),
        )
    rows = _supabase_req(
        "GET",
        "/saas_catalog_sync_runs",
        params={"id": f"eq.{run_id}", "select": "*", "limit": "1"},
    )
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    source_rows = _supabase_req(
        "GET",
        "/saas_catalog_sources",
        params={"id": f"eq.{row.get('source_id')}", "select": "supplier,label", "limit": "1"},
    )
    return {**row, **(source_rows[0] if isinstance(source_rows, list) and source_rows else {})}


def db_get_catalog_snapshot_version(version_id: str) -> dict | None:
    if DEV_MODE:
        rows = _dev_load().setdefault("catalog_snapshot_versions", [])
        return next((row for row in rows if str(row.get("id")) == str(version_id)), None)
    _require_catalog_service_backend()
    fields = (
        "id,supplier,status,payload,previous_snapshot_id,base_published_version_id,"
        "sync_run_id,created_at"
    )
    if _use_postgres():
        return _pg_one(
            f"SELECT {fields} FROM saas_catalog_snapshot_versions WHERE id = %s LIMIT 1",
            (version_id,),
        )
    rows = _supabase_req(
        "GET",
        "/saas_catalog_snapshot_versions",
        params={"id": f"eq.{version_id}", "select": fields, "limit": "1"},
    )
    return rows[0] if isinstance(rows, list) and rows else None


def db_create_catalog_sync_run(supplier: str, requested_by: int, trigger_type: str = "manual") -> dict:
    supplier = _catalog_supplier(supplier)
    if trigger_type not in {"manual", "scheduled"}:
        raise RuntimeError("Trigger de catalogo invalido")
    source = db_get_catalog_source(supplier)
    if not source:
        raise RuntimeError("Fuente de catalogo no configurada")
    now = _iso(datetime.now(timezone.utc))
    row = {
        "id": str(uuid.uuid4()),
        "source_id": source["id"],
        "supplier": supplier,
        "label": source.get("label") or CATALOG_SUPPLIER_LABELS[supplier],
        "trigger_type": trigger_type,
        "status": "requested",
        "requested_by": int(requested_by),
        "metrics": {},
        "requested_at": now,
        "updated_at": now,
    }
    if DEV_MODE:
        data = _dev_load()
        data.setdefault("catalog_sync_runs", []).append(row)
        _dev_save(data)
        return row
    _require_catalog_service_backend()
    payload = {key: row[key] for key in ("source_id", "trigger_type", "requested_by", "metrics")}
    if _use_postgres():
        saved = _pg_write(
            """
            INSERT INTO saas_catalog_sync_runs
                (source_id, trigger_type, requested_by, metrics)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            tuple(payload.values()),
        )
    else:
        rows = _supabase_req("POST", "/saas_catalog_sync_runs", json_data=payload)
        saved = rows[0] if isinstance(rows, list) and rows else None
    if not saved:
        raise RuntimeError("No fue posible solicitar sincronizacion")
    return {**saved, "supplier": supplier, "label": row["label"]}


def _catalog_rpc(name: str, payload: dict, result_status: str) -> dict:
    _require_catalog_service_backend()
    if _use_postgres():
        params = []
        placeholders = []
        for value in payload.values():
            if isinstance(value, list):
                if any(not isinstance(item, str) for item in value):
                    raise RuntimeError("Argumento array de RPC invalido")
                if not value:
                    placeholders.append("ARRAY[]::TEXT[]")
                    continue
                placeholders.append(f"ARRAY[{', '.join(['%s'] * len(value))}]::TEXT[]")
                params.extend(value)
            else:
                placeholders.append("%s")
                params.append(value)
        row = _pg_write(f"SELECT {name}({', '.join(placeholders)}) AS value", tuple(params))
        value = row.get("value") if row else None
    else:
        value = _supabase_req("POST", f"/rpc/{name}", json_data=payload)
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, dict):
        value = value.get("value") or value.get(name)
    if not value:
        raise RuntimeError("RPC de catalogo no devolvio resultado")
    return {"candidate_id": str(value), "status": result_status}


def db_publish_catalog_snapshot(candidate_id: str, reviewed_by: int, review_note: str) -> dict:
    return _catalog_rpc(
        "saas_publish_catalog_snapshot",
        {"p_candidate_id": candidate_id, "p_reviewed_by": int(reviewed_by), "p_review_note": review_note},
        "published",
    )


def db_reject_catalog_snapshot(candidate_id: str, reviewed_by: int, review_note: str) -> dict:
    return _catalog_rpc(
        "saas_reject_catalog_snapshot",
        {"p_candidate_id": candidate_id, "p_reviewed_by": int(reviewed_by), "p_review_note": review_note},
        "rejected",
    )


def db_clone_catalog_candidate_with_asset(
    candidate_id: str,
    reviewed_by: int,
    object_name: str,
    json_path: list[str],
) -> str:
    result = _catalog_rpc(
        "saas_clone_catalog_candidate_with_asset",
        {
            "p_candidate_id": candidate_id,
            "p_reviewed_by": int(reviewed_by),
            "p_asset_object_name": object_name,
            "p_json_path": json_path,
        },
        "candidate",
    )
    return result["candidate_id"]


def db_clone_catalog_candidate_with_image_metadata(
    candidate_id: str,
    reviewed_by: int,
    object_name: str,
    json_path: list[str],
    image_kind: str,
    image_label: str,
    image_references: list[str],
) -> str:
    result = _catalog_rpc(
        "saas_clone_catalog_candidate_with_image_metadata",
        {
            "p_candidate_id": candidate_id,
            "p_reviewed_by": int(reviewed_by),
            "p_asset_object_name": object_name,
            "p_json_path": json_path,
            "p_image_kind": image_kind,
            "p_image_label": image_label,
            "p_image_references": image_references,
        },
        "candidate",
    )
    return result["candidate_id"]


_SUPPLIER_CATALOG_CACHE: dict[str, dict] = {}


def _enabled_catalog_suppliers() -> tuple[str, ...]:
    return _canonical_enabled_catalog_suppliers(CATALOG_ENABLED_SUPPLIERS)


def _require_enabled_catalog_supplier(supplier: str) -> str:
    clean = str(supplier or "").strip().lower()
    if clean not in _enabled_catalog_suppliers():
        raise HTTPException(status_code=404, detail="Catalogo no disponible")
    return clean


def _catalog_asset_public_url(object_name: str) -> str:
    clean_name = str(object_name or "")
    if not CATALOG_ASSET_NAME_RE.fullmatch(clean_name):
        raise ValueError("Nombre de asset invalido")
    if DEV_MODE:
        return f"{DEV_PUBLIC_BASE_URL}/dev/catalog-assets/{quote(clean_name, safe='')}"
    base_url = str(SUPABASE_URL or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("SUPABASE_URL requerida para asset de catalogo")
    return f"{base_url}/storage/v1/object/public/{CATALOG_ASSET_BUCKET}/{quote(clean_name, safe='')}"


def _hydrate_catalog_asset_urls(payload: dict) -> dict:
    hydrated = deepcopy(payload)
    for item in hydrated.get("items", []):
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            continue
        asset = attributes.get("approved_asset")
        attributes.pop("image_match", None)
        if not isinstance(asset, dict) or asset.get("approved") is not True:
            continue
        if asset.get("bucket") != CATALOG_ASSET_BUCKET:
            continue
        try:
            item["image_url"] = _catalog_asset_public_url(str(asset.get("path") or ""))
        except ValueError:
            continue
        image_kind = asset.get("image_kind")
        if image_kind not in {"official", "generated_reference"}:
            current_kind = item.get("image_kind")
            image_kind = current_kind if current_kind in {"official", "generated_reference"} else "generated_reference"
        item["image_kind"] = image_kind
        attributes.pop("approved_asset", None)
    return hydrated


def _catalog_diff_value(value, depth: int = 0):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if depth >= 3:
        return "..."
    if isinstance(value, list):
        return [_catalog_diff_value(item, depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        result = {}
        for key in sorted(value, key=str):
            clean_key = str(key)[:80]
            folded = clean_key.lower()
            if any(token in folded for token in ("path", "file_id", "sha", "hash", "secret", "token")):
                continue
            if folded in {"approved_asset", "source_images"}:
                continue
            result[clean_key] = _catalog_diff_value(value[key], depth + 1)
            if len(result) >= 20:
                break
        return result
    return str(value)[:500]


def _catalog_source_coordinate(source_reference) -> str:
    if not isinstance(source_reference, str) or len(source_reference) > 2048:
        return ""
    try:
        parsed = json.loads(source_reference)
    except (TypeError, ValueError):
        return ""
    pending = list(parsed) if isinstance(parsed, list) else [parsed]
    coordinates = []
    for reference in pending[:20]:
        if not isinstance(reference, dict):
            continue
        sheet = reference.get("sheet_or_page")
        position = reference.get("cell_or_bbox")
        if isinstance(sheet, str):
            sheet = sheet.strip()[:128]
            if not sheet or any(character in sheet for character in ("/", "\\")):
                continue
        elif type(sheet) is int and 1 <= sheet <= 2000:
            sheet = f"Pagina {sheet}"
        else:
            continue
        if isinstance(position, str) and re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", position):
            clean_position = position
        elif (
            isinstance(position, list) and len(position) == 4
            and all(type(value) in {int, float} and abs(value) <= 1_000_000 for value in position)
        ):
            clean_position = "[" + ", ".join(str(value) for value in position) + "]"
        else:
            continue
        coordinates.append(f"{sheet}!{clean_position}")
        if len(coordinates) == 3:
            break
    return "; ".join(coordinates)[:500]


def _catalog_diff_material_type(field: str, before: dict | None, after: dict | None) -> str:
    if field == "base_price_options":
        return "base_price"
    if field == "add_on_options":
        options = (after or before or {}).get("add_on_options") or []
        families = sorted({
            str(option.get("family"))[:60]
            for option in options
            if isinstance(option, dict) and option.get("family")
        })
        return "add_on:" + ",".join(families[:3]) if families else "add_on"
    if field in {"base_currency", "price_net", "tax_rate"}:
        return "commercial"
    if field in {"stock", "lead_time", "availability_type"}:
        return "operational"
    if field in {"image_url", "image_kind", "product_url"}:
        return "image"
    if field == "attributes":
        attributes = (after or before or {}).get("attributes") or {}
        if isinstance(attributes, dict):
            keys = [str(key)[:60] for key in attributes if "material" in str(key).lower() or "finish" in str(key).lower()]
            if keys:
                return "attributes:" + ",".join(sorted(keys)[:3])
        return "attributes"
    return "product"


def _catalog_detailed_diff(candidate_payload: dict, base_payload: dict | None) -> dict:
    candidate_items = candidate_payload.get("items") if isinstance(candidate_payload, dict) else None
    base_items = base_payload.get("items") if isinstance(base_payload, dict) else []
    if not isinstance(candidate_items, list) or not isinstance(base_items, list):
        raise RuntimeError("Payload de catalogo invalido para diferencias")
    before = {
        str(item.get("internal_id")): item
        for item in base_items
        if isinstance(item, dict) and str(item.get("internal_id") or "").strip()
    }
    after = {
        str(item.get("internal_id")): item
        for item in candidate_items
        if isinstance(item, dict) and str(item.get("internal_id") or "").strip()
    }
    items = []
    total = 0
    for item_id in sorted(set(before) | set(after)):
        old_item, new_item = before.get(item_id), after.get(item_id)
        if old_item is None or new_item is None:
            total += 1
            source_item = new_item or old_item or {}
            if len(items) < CATALOG_DIFF_LIMIT:
                items.append({
                    "item_id": item_id[:256],
                    "field": "item",
                    "before": None if old_item is None else _catalog_diff_value(old_item.get("name") or item_id),
                    "after": None if new_item is None else _catalog_diff_value(new_item.get("name") or item_id),
                    "source_coordinate": _catalog_source_coordinate(source_item.get("source_reference")),
                    "material_type": "product",
                })
            continue
        for field in CATALOG_DIFF_FIELDS:
            if old_item.get(field) == new_item.get(field):
                continue
            total += 1
            if len(items) >= CATALOG_DIFF_LIMIT:
                continue
            items.append({
                "item_id": item_id[:256],
                "field": field,
                "before": _catalog_diff_value(old_item.get(field)),
                "after": _catalog_diff_value(new_item.get(field)),
                "source_coordinate": _catalog_source_coordinate(
                    new_item.get("source_reference") or old_item.get("source_reference")
                ),
                "material_type": _catalog_diff_material_type(field, old_item, new_item),
            })
    return {"items": items, "total": total, "truncated": total > len(items)}


def _catalog_run_detailed_diff(run: dict) -> dict:
    candidate_id = str(run.get("candidate_version_id") or "").strip()
    candidate = db_get_catalog_snapshot_version(candidate_id)
    if (
        not isinstance(candidate, dict)
        or candidate.get("status") != "candidate"
        or candidate.get("supplier") != run.get("supplier")
        or not isinstance(candidate.get("payload"), dict)
    ):
        raise RuntimeError("Candidato de catalogo invalido")
    base_id = candidate.get("base_published_version_id")
    current = candidate
    seen = {candidate_id}
    for _index in range(20):
        if base_id:
            break
        previous_id = str(current.get("previous_snapshot_id") or "").strip()
        if not previous_id or previous_id in seen:
            break
        seen.add(previous_id)
        current = db_get_catalog_snapshot_version(previous_id)
        if not isinstance(current, dict) or current.get("supplier") != run.get("supplier"):
            raise RuntimeError("Cadena de candidato invalida")
        base_id = current.get("base_published_version_id")
    base = db_get_catalog_snapshot_version(str(base_id)) if base_id else None
    if base is not None and (
        not isinstance(base, dict)
        or base.get("supplier") != run.get("supplier")
        or not isinstance(base.get("payload"), dict)
    ):
        raise RuntimeError("Base de catalogo invalida")
    return _catalog_detailed_diff(candidate["payload"], base.get("payload") if base else None)


def _load_supplier_catalog_cached(supplier: str) -> dict:
    supplier = _catalog_supplier(supplier)
    snapshot = db_get_published_catalog_snapshot(supplier)
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else None
    if not isinstance(payload, dict):
        raise RuntimeError("Catalogo publicado no disponible")
    cache_key = str(snapshot.get("id") or snapshot.get("source_hash") or payload.get("source_hash") or "")
    if DEV_MODE:
        payload_fingerprint = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cache_key = f"{cache_key}:{payload_fingerprint}"
    cached = _SUPPLIER_CATALOG_CACHE.get(supplier)
    if cached and cached.get("cache_key") == cache_key:
        return cached["catalog"]
    try:
        catalog = load_supplier_catalog_data(_hydrate_catalog_asset_urls(payload), expected_supplier=supplier)
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("Catalogo publicado invalido") from exc
    _SUPPLIER_CATALOG_CACHE[supplier] = {"cache_key": cache_key, "catalog": catalog}
    return catalog


def _display_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def _catalog_reservation_totals(rows: list[dict]) -> tuple[dict[str, Decimal], set[str]]:
    if not isinstance(rows, list):
        raise RuntimeError("Resumen de reservas invalido")
    totals: dict[str, Decimal] = {}
    reserved_by_others: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Resumen de reservas invalido")
        internal_id = str(row.get("internal_id") or "").strip()
        if not internal_id:
            raise RuntimeError("Resumen de reservas invalido")
        try:
            quantity = Decimal(str(row.get("reserved_quantity") or 0))
        except (InvalidOperation, TypeError, ValueError):
            raise RuntimeError("Resumen de reservas invalido") from None
        if not quantity.is_finite() or quantity <= 0:
            raise RuntimeError("Resumen de reservas invalido")
        if not isinstance(row.get("reserved_by_others"), bool):
            raise RuntimeError("Resumen de reservas invalido")
        totals[internal_id] = quantity
        if row.get("reserved_by_others") is True:
            reserved_by_others.add(internal_id)
    return totals, reserved_by_others


def _supplier_catalog_response(supplier: str, usuario_id: int) -> dict:
    catalog = load_supplier_catalog_data(_load_supplier_catalog_cached(supplier), expected_supplier=supplier)
    totals, reserved_by_others = _catalog_reservation_totals(
        db_catalog_reservation_summary(supplier, usuario_id)
    )
    items = []
    for source_item in catalog["items"]:
        item = deepcopy(source_item)
        reserved = totals.get(item["internal_id"], Decimal(0)) if item["availability_type"] == "stocked" else Decimal(0)
        try:
            stock = Decimal(str(item["stock"])) if item["stock"] is not None else None
        except (InvalidOperation, TypeError, ValueError):
            stock = None
        item.update(
            {
                "reserved_quantity": _display_number(reserved),
                "reserved_by_others": item["internal_id"] in reserved_by_others,
                "is_out_of_stock": item["availability_type"] == "stocked" and stock is not None and stock <= 0,
            }
        )
        items.append(item)
    return {
        "supplier": supplier,
        "source_hash": catalog["source_hash"],
        "generated_at": catalog["generated_at"],
        "total": len(items),
        "items": items,
    }


def _catalog_search_snapshots(usuario_id: int, supplier: str | None) -> dict:
    suppliers = (supplier,) if supplier is not None else MIXED_CATALOG_ORDER
    snapshots = {}
    for catalog in suppliers:
        if catalog == "tarkett":
            snapshots[catalog] = _tarkett_catalog_response(usuario_id)
        elif catalog == "offiho":
            snapshots[catalog] = _offiho_catalog_response(usuario_id)
        else:
            snapshots[catalog] = _supplier_catalog_response(catalog, usuario_id)
    return snapshots


def _catalog_search_integer(value: object, field: str, minimum: int, maximum: int | None = None) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{field} invalido")
    parsed = int(value)
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(f"{field} invalido")
    return parsed


def _catalog_reservation_request_lines(cart_payload: dict) -> list[dict]:
    rows = []
    for line in cart_payload.get("items", []):
        if line.get("availability_type") != "stocked":
            continue
        try:
            quantity = Decimal(str(line.get("quantity") or 0))
            stock = Decimal(str(line.get("stock") or 0))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("Existencia de catalogo invalida") from None
        if not quantity.is_finite() or quantity <= 0 or not stock.is_finite() or stock < 0:
            raise ValueError("Existencia de catalogo invalida")
        rows.append(
            {
                "internal_id": line["internal_id"],
                "sku": line["sku"],
                "quantity": line["quantity"],
                "stock": line["stock"],
            }
        )
    return rows


def _apply_catalog_reservation_snapshot(cart_payload: dict, snapshot: list[dict]) -> None:
    stocked = {
        str(line.get("internal_id") or ""): line
        for line in cart_payload.get("items", [])
        if line.get("availability_type") == "stocked"
    }
    if not isinstance(snapshot, list) or len(snapshot) != len(stocked):
        raise ValueError("Respuesta de reserva de catalogo invalida")
    seen = set()
    for row in snapshot:
        if not isinstance(row, dict):
            raise ValueError("Respuesta de reserva de catalogo invalida")
        internal_id = str(row.get("internal_id") or "").strip()
        line = stocked.get(internal_id)
        try:
            reserved = Decimal(str(row.get("reserved_before")))
            available = Decimal(str(row.get("available_before")))
            stock = Decimal(str(line.get("stock"))) if line else Decimal(-1)
            quantity = Decimal(str(line.get("quantity"))) if line else Decimal(-1)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("Respuesta de reserva de catalogo invalida") from None
        expected_available = max(stock - reserved, Decimal(0))
        insufficient = row.get("insufficient")
        if (
            line is None
            or internal_id in seen
            or not reserved.is_finite()
            or reserved < 0
            or not available.is_finite()
            or available < 0
            or available != expected_available
            or not isinstance(insufficient, bool)
            or insufficient != (quantity > available)
            or not isinstance(row.get("reserved_by_others"), bool)
        ):
            raise ValueError("Respuesta de reserva de catalogo invalida")
        seen.add(internal_id)
        line["reserved_quantity"] = f"{reserved:.6f}"
        line["available_after_reservations"] = f"{available:.6f}"
        line["reserved_by_others"] = row["reserved_by_others"]
        if insufficient:
            warning = (
                "Cantidad solicitada supera la existencia disponible; verificar disponibilidad."
                if reserved > 0
                else "Cantidad solicitada supera la existencia; verificar disponibilidad."
            )
            if warning not in line["warnings"]:
                line["warnings"].append(warning)
            line["stock_status"] = "insufficient"
    if seen != set(stocked):
        raise ValueError("Respuesta de reserva de catalogo invalida")


def _catalog_exchange_rates_response(base_currency: str) -> dict:
    clean_base = str(base_currency or "USD").strip().upper()
    if clean_base not in ALLOWED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Moneda base invalida")
    rows = db_list_exchange_rates()
    rates = []
    for quote_currency in ("USD", "MXN", "EUR"):
        try:
            rate = resolve_conversion_rate(clean_base, quote_currency, rows, date.today())
            rates.append(
                {
                    "quote_currency": quote_currency,
                    "available": True,
                    "exchange_rate": f"{rate.exchange_rate:.6f}",
                    "rate_source": rate.rate_source,
                    "rate_effective_date": rate.rate_effective_date.isoformat(),
                    "rate_retrieved_at": rate.rate_retrieved_at,
                }
            )
        except ValueError:
            rates.append({"quote_currency": quote_currency, "available": False, "reason": "rate_unavailable"})
    return {"base_currency": clean_base, "rates": rates}


def _catalog_image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _catalog_jpeg_has_exact_end(content: bytes) -> bool:
    if not content.startswith(b"\xff\xd8"):
        return False
    offset = 2
    in_scan = False
    while offset < len(content):
        if in_scan:
            offset = content.find(b"\xff", offset)
            if offset < 0:
                return False
        elif content[offset] != 0xFF:
            return False
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            return False
        marker = content[offset]
        offset += 1
        if in_scan and (marker == 0x00 or 0xD0 <= marker <= 0xD7):
            continue
        in_scan = False
        if marker == 0xD9:
            return offset == len(content)
        if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(content):
            return False
        segment_length = int.from_bytes(content[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            return False
        offset += segment_length
        in_scan = marker == 0xDA
    return False


def _catalog_image_has_exact_end(content: bytes, extension: str) -> bool:
    if extension == "jpg":
        return _catalog_jpeg_has_exact_end(content)
    if extension == "webp":
        return len(content) >= 12 and int.from_bytes(content[4:8], "little") + 8 == len(content)
    if extension != "png" or not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset:offset + 4], "big")
        chunk_type = content[offset + 4:offset + 8]
        offset += length + 12
        if offset > len(content):
            return False
        if chunk_type == b"IEND":
            return length == 0 and offset == len(content)
    return False


def _normalize_catalog_image(content: bytes, filename: str, declared_mime: str) -> bytes:
    detected = _catalog_image_type(content)
    if not detected:
        raise HTTPException(status_code=400, detail="Formato de imagen invalido")
    extension, detected_mime = detected
    filename_extension = Path(str(filename or "")).suffix.lower().lstrip(".")
    if filename_extension == "jpeg":
        filename_extension = "jpg"
    if str(declared_mime or "").lower() != detected_mime or filename_extension != extension:
        raise HTTPException(status_code=400, detail="Tipo de imagen no coincide con su contenido")
    if not _catalog_image_has_exact_end(content, extension):
        raise HTTPException(status_code=400, detail="Imagen truncada o con contenido adicional")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                if probe.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("unsupported format")
                width, height = probe.size
                if (
                    width < 1 or height < 1
                    or width > CATALOG_ASSET_MAX_WIDTH
                    or height > CATALOG_ASSET_MAX_HEIGHT
                    or width * height > CATALOG_ASSET_MAX_PIXELS
                ):
                    raise ValueError("unsafe dimensions")
                if getattr(probe, "is_animated", False) or getattr(probe, "n_frames", 1) != 1:
                    raise ValueError("animated image")
                probe.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
                if getattr(decoded, "is_animated", False) or getattr(decoded, "n_frames", 1) != 1:
                    raise ValueError("animated image")
                has_alpha = "A" in decoded.getbands() or "transparency" in decoded.info
                normalized = decoded.convert("RGBA" if has_alpha else "RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Imagen corrupta o no segura") from exc
    output = io.BytesIO()
    normalized.save(output, format="PNG", optimize=False, compress_level=9)
    canonical = output.getvalue()
    if len(canonical) > CATALOG_ASSET_MAX_OUTPUT_BYTES:
        raise HTTPException(status_code=413, detail="Imagen normalizada mayor a 8 MB")
    return canonical


def _catalog_image_metadata(image_kind: str, image_label: str, image_references: str) -> tuple[str, str, list[str]]:
    kind = str(image_kind or "").strip()
    label = str(image_label or "").strip()
    try:
        references = json.loads(image_references or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Referencias de imagen invalidas") from exc
    if kind not in {"official", "generated_reference"} or not isinstance(references, list):
        raise HTTPException(status_code=400, detail="Metadata de imagen invalida")
    clean_references = [str(reference).strip() for reference in references]
    if any(not reference or len(reference) > 2000 for reference in clean_references):
        raise HTTPException(status_code=400, detail="Referencia de imagen invalida")
    if kind == "generated_reference":
        if not label or len(label) > 300 or not clean_references:
            raise HTTPException(status_code=400, detail="La imagen de referencia requiere etiqueta y referencia HTTPS")
        if any((parsed := urlparse(reference)).scheme != "https" or not parsed.netloc for reference in clean_references):
            raise HTTPException(status_code=400, detail="La imagen de referencia requiere URLs HTTPS verificables")
    return kind, label, clean_references


def _upload_catalog_asset(object_name: str, content: bytes, content_type: str) -> None:
    if not CATALOG_ASSET_NAME_RE.fullmatch(str(object_name or "")):
        raise RuntimeError("Nombre de asset invalido")
    if DEV_MODE:
        destination = DEV_STORE_DIR / CATALOG_ASSET_BUCKET / object_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != content:
                raise RuntimeError("Colision de asset de catalogo")
            return
        destination.write_bytes(content)
        return
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("Storage de catalogos no configurado")
    encoded_name = quote(object_name, safe="")
    url = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/{CATALOG_ASSET_BUCKET}/{encoded_name}"
    headers = _get_supabase_headers()
    headers.update({"Content-Type": content_type, "x-upsert": "false"})
    request = urllib.request.Request(url, data=content, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60):
            return
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_safe_http_error("Supabase Storage", exc.code, body)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Supabase Storage connection error: {exc.reason}") from exc


_TARKETT_CATALOG_CACHE = {
    "path": None,
    "fingerprint": None,
    "source_hash": None,
    "catalog": None,
    "db_checked_at": 0.0,
}


def db_get_supplier_catalog_snapshot(supplier: str) -> dict | None:
    clean_supplier = str(supplier or "").strip().lower()
    if clean_supplier not in {"tarkett"}:
        raise RuntimeError("Proveedor de catalogo no permitido")
    if DEV_MODE:
        return _dev_load().get("supplier_catalog_snapshots", {}).get(clean_supplier)
    if DATABASE_URL:
        return _pg_one(
            """
            SELECT supplier, source_hash, generated_at, payload, updated_at
            FROM saas_supplier_catalog_snapshots
            WHERE supplier = %s
            LIMIT 1
            """,
            (clean_supplier,),
        )
    rows = _supabase_req(
        "GET",
        "/saas_supplier_catalog_snapshots",
        params={
            "supplier": f"eq.{clean_supplier}",
            "select": "supplier,source_hash,generated_at,payload,updated_at",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def db_upsert_supplier_catalog_snapshot(supplier: str, payload: dict) -> dict:
    clean_supplier = str(supplier or "").strip().lower()
    if clean_supplier not in {"tarkett"}:
        raise RuntimeError("Proveedor de catalogo no permitido")
    catalog = load_tarkett_catalog_data(payload)
    row = {
        "supplier": clean_supplier,
        "source_hash": catalog["source_hash"],
        "generated_at": catalog["generated_at"],
        "payload": payload,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
    if DEV_MODE:
        data = _dev_load()
        data.setdefault("supplier_catalog_snapshots", {})[clean_supplier] = row
        _dev_save(data)
        return row
    if DATABASE_URL:
        return _pg_write(
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
            (clean_supplier, catalog["source_hash"], catalog["generated_at"], payload),
        ) or row
    existing = db_get_supplier_catalog_snapshot(clean_supplier)
    if existing:
        rows = _supabase_req(
            "PATCH",
            "/saas_supplier_catalog_snapshots",
            params={"supplier": f"eq.{clean_supplier}"},
            json_data=row,
        )
    else:
        rows = _supabase_req("POST", "/saas_supplier_catalog_snapshots", json_data=row)
    return rows[0] if isinstance(rows, list) and rows else row


def _catalog_file_fingerprint(catalog_path: Path) -> dict:
    content = catalog_path.read_bytes()
    stat = catalog_path.stat()
    return {
        "path": str(catalog_path),
        "mtime_ns": stat.st_mtime_ns,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _load_catalog_cached(cache: dict, catalog_path: Path, loader, label: str) -> dict:
    try:
        fingerprint = _catalog_file_fingerprint(catalog_path)
    except OSError as exc:
        if cache.get("catalog") is not None:
            return cache["catalog"]
        raise RuntimeError(f"Catalogo {label} no disponible") from exc

    if cache.get("catalog") is not None and cache.get("fingerprint") == fingerprint:
        return cache["catalog"]

    try:
        catalog = loader(catalog_path)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if cache.get("catalog") is not None:
            return cache["catalog"]
        raise RuntimeError(f"Catalogo {label} invalido") from exc

    cache.clear()
    cache.update(
        {
            "path": str(catalog_path),
            "fingerprint": fingerprint,
            "source_hash": catalog.get("source_hash"),
            "catalog": catalog,
        }
    )
    return catalog


def _load_tarkett_catalog_cached() -> dict:
    now = time.monotonic()
    if TARKETT_CATALOG_DB_ENABLED:
        checked_at = float(_TARKETT_CATALOG_CACHE.get("db_checked_at") or 0)
        if _TARKETT_CATALOG_CACHE.get("catalog") is not None and now - checked_at < TARKETT_CATALOG_DB_TTL_SECONDS:
            return _TARKETT_CATALOG_CACHE["catalog"]
        try:
            snapshot = db_get_supplier_catalog_snapshot("tarkett")
            payload = snapshot.get("payload") if isinstance(snapshot, dict) else None
            if isinstance(payload, dict):
                catalog = load_tarkett_catalog_data(payload)
                _TARKETT_CATALOG_CACHE.clear()
                _TARKETT_CATALOG_CACHE.update(
                    {
                        "path": "supabase:saas_supplier_catalog_snapshots/tarkett",
                        "fingerprint": {"source_hash": catalog.get("source_hash")},
                        "source_hash": catalog.get("source_hash"),
                        "catalog": catalog,
                        "db_checked_at": now,
                    }
                )
                return catalog
        except (RuntimeError, OSError, ValueError, TypeError, KeyError):
            if _TARKETT_CATALOG_CACHE.get("catalog") is not None:
                _TARKETT_CATALOG_CACHE["db_checked_at"] = now
                return _TARKETT_CATALOG_CACHE["catalog"]
    catalog_path = (Path(TARKETT_CATALOG_PATH) if TARKETT_CATALOG_PATH else CATALOG_PATH).resolve()
    catalog = _load_catalog_cached(_TARKETT_CATALOG_CACHE, catalog_path, load_tarkett_catalog, "Tarkett")
    _TARKETT_CATALOG_CACHE["db_checked_at"] = now
    return catalog


def _tarkett_catalog_response(usuario_id: int) -> dict:
    catalog = _load_tarkett_catalog_cached()
    reservations = db_list_tarkett_reservations("active")
    totals: dict[str, float] = {}
    reserved_by_others: set[str] = set()
    for row in reservations:
        code = str(row.get("product_code") or "").strip()
        if not code:
            continue
        qty = float(row.get("quantity") or 0)
        totals[code] = totals.get(code, 0) + qty
        if int(row.get("usuario_id") or 0) != int(usuario_id):
            reserved_by_others.add(code)
    items = [
        item.to_public_dict(
            reserved_quantity=totals.get(item.code, 0),
            reserved_by_others=item.code in reserved_by_others,
        )
        for item in catalog["items"]
    ]
    return {
        "source_hash": catalog["source_hash"],
        "generated_at": catalog["generated_at"],
        "total": len(items),
        "items": items,
    }


_OFFIHO_CATALOG_CACHE = {"path": None, "fingerprint": None, "source_hash": None, "catalog": None}


def _load_offiho_catalog_cached() -> dict:
    catalog_path = Path(OFFIHO_CATALOG_PATH).resolve() if OFFIHO_CATALOG_PATH else OFFIHO_DEFAULT_CATALOG_PATH.resolve()
    return _load_catalog_cached(_OFFIHO_CATALOG_CACHE, catalog_path, load_offiho_catalog, "Offiho")


def _offiho_catalog_response(usuario_id: int) -> dict:
    catalog = _load_offiho_catalog_cached()
    reservations = db_list_offiho_reservations("active")
    totals: dict[str, float] = {}
    reserved_by_others: set[str] = set()
    for row in reservations:
        inventory_key = str(row.get("product_code") or "").strip()
        if not inventory_key:
            continue
        quantity = float(row.get("quantity") or 0)
        totals[inventory_key] = totals.get(inventory_key, 0) + quantity
        if int(row.get("usuario_id") or 0) != int(usuario_id):
            reserved_by_others.add(inventory_key)
    items = []
    for item in catalog["items"]:
        payload = item.to_public_dict()
        payload.update(
            {
                "is_out_of_stock": item.available_quantity <= 0,
                "reserved_quantity": totals.get(item.inventory_key, 0),
                "reserved_by_others": item.inventory_key in reserved_by_others,
            }
        )
        items.append(payload)
    return {
        "source_hash": catalog["source_hash"],
        "generated_at": catalog["generated_at"],
        "source_row_count": catalog["source_row_count"],
        "duplicate_row_count": catalog["duplicate_row_count"],
        "unique_item_count": catalog["unique_item_count"],
        "total": len(items),
        "items": items,
    }


def _require_queued_quote_job(updated: dict) -> dict:
    if not isinstance(updated, dict) or updated.get("status") != "queued":
        raise RuntimeError("Quote job update did not confirm queued status")
    return updated


def _cleanup_failed_catalog_quote(
    job_id: str,
    input_path: str,
    release_reservations=None,
    *,
    additional_storage_paths: list[str] | None = None,
    primary_failure: str | None = None,
) -> None:
    def pending_error(stage: str) -> str:
        cleanup_error = f"cleanup_pending:{stage}"
        return (
            f"{cleanup_error}|{primary_failure}"
            if primary_failure
            else cleanup_error
        )

    def mark_pending(stage: str) -> None:
        try:
            db_update_quote_job(
                job_id,
                {
                    "status": "failed",
                    "error_message": pending_error(stage),
                },
            )
        except Exception:
            pass
        event = {
            "event": "catalog_quote_cleanup_pending",
            "job_id": job_id,
            "stage": stage,
        }
        if primary_failure:
            event["primary_failure"] = primary_failure
        print(json.dumps(event, separators=(",", ":")))

    if release_reservations is not None:
        try:
            release_reservations(job_id)
        except Exception:
            mark_pending("release_reservations")
            return
    try:
        storage_paths = [input_path, *(additional_storage_paths or [])]
        _delete_storage_paths(list(dict.fromkeys(storage_paths)))
    except Exception:
        mark_pending("delete_input")
        return
    try:
        db_delete_quote_job(job_id)
    except Exception:
        mark_pending("delete_job")


def _append_mixed_warning_once(line: dict[str, Any], warning: str) -> None:
    def normalized(value: object) -> str:
        decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
        without_marks = "".join(
            character for character in decomposed
            if not unicodedata.combining(character)
        )
        return " ".join(without_marks.split())

    warning_key = normalized(warning)
    if all(normalized(current) != warning_key for current in line["warnings"]):
        line["warnings"].append(warning)


def _mixed_snapshot_decimal(value: object) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("Snapshot de reserva mixta invalido")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Snapshot de reserva mixta invalido") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Snapshot de reserva mixta invalido")
    return number


def _apply_mixed_reservation_snapshot(cart_payload: dict, snapshot: list[dict]) -> None:
    reservation_groups = build_mixed_reservation_groups(cart_payload)
    expected = {
        (group["catalog"], item["identity"]): item
        for group in reservation_groups for item in group["items"]
    }
    fields = {
        "catalog", "identity", "reserved_before", "available_before",
        "insufficient", "reserved_by_others",
    }
    if not isinstance(snapshot, list) or len(snapshot) != len(expected):
        raise ValueError("Snapshot de reserva mixta invalido")

    indexed = {}
    for candidate in snapshot:
        if not isinstance(candidate, dict) or set(candidate) != fields:
            raise ValueError("Snapshot de reserva mixta invalido")
        key = (candidate.get("catalog"), candidate.get("identity"))
        if key not in expected or key in indexed:
            raise ValueError("Snapshot de reserva mixta invalido")
        reserved_before = _mixed_snapshot_decimal(candidate["reserved_before"])
        available_before = _mixed_snapshot_decimal(candidate["available_before"])
        if type(candidate["insufficient"]) is not bool or type(candidate["reserved_by_others"]) is not bool:
            raise ValueError("Snapshot de reserva mixta invalido")
        expected_item = expected[key]
        expected_available = max(
            Decimal(expected_item["stock"]) - reserved_before, Decimal(0)
        )
        expected_insufficient = Decimal(expected_item["quantity"]) > expected_available
        if available_before != expected_available or candidate["insufficient"] != expected_insufficient:
            raise ValueError("Snapshot de reserva mixta invalido")
        indexed[key] = candidate

    for (catalog, identity), row in indexed.items():
        if catalog == "tarkett" and row["insufficient"]:
            raise ValueError(f"tarkett:{identity} sin existencia suficiente")

    for group in cart_payload["groups"]:
        catalog = group["catalog"]
        for line in group["items"]:
            reservation = line["reservation"]
            if reservation is None:
                continue
            row = indexed[(catalog, reservation["identity"])]
            line["reserved_quantity"] = row["reserved_before"]
            line["available_after_reservations"] = row["available_before"]
            line["reserved_by_others"] = row["reserved_by_others"]
            if row["insufficient"]:
                _append_mixed_warning_once(
                    line, "Existencia insuficiente; verificar disponibilidad."
                )


def _require_active_subscription(usuario_id: int):
    suscripcion = db_get_suscripcion_by_usuario(usuario_id)
    now = datetime.now(timezone.utc)
    if not suscripcion:
        raise HTTPException(status_code=403, detail="Sin suscripcion")
    fecha_fin = datetime.fromisoformat(suscripcion["fecha_fin"].replace("Z", "+00:00"))
    if suscripcion["estado"] != "activa" or fecha_fin < now:
        raise HTTPException(status_code=403, detail="Suscripcion no activa")
    return suscripcion


def _quote_job_for_user(job_id: str, usuario_id: int):
    try:
        job = db_get_quote_job(job_id)
    except RuntimeError as exc:
        print(json.dumps({
            "event": "quote_job_lookup_failed",
            "error_type": exc.__class__.__name__,
        }, separators=(",", ":")))
        raise HTTPException(
            status_code=503,
            detail="Servicio de cotizaciones no disponible",
        ) from None
    if not job:
        raise HTTPException(status_code=404, detail="Cotizacion no encontrada")
    if int(job["usuario_id"]) != int(usuario_id):
        raise HTTPException(status_code=403, detail="No puedes acceder a esta cotizacion")
    return job


def _quote_storage_paths(job: dict) -> list[str]:
    paths = []
    for key in ("input_path", "output_path"):
        path = str(job.get(key) or "").strip().lstrip("/")
        if path:
            paths.append(path)
    preview_prefix = f"users/{job.get('usuario_id')}/jobs/{job.get('id')}/preview/"
    job_prefix = f"users/{job.get('usuario_id')}/jobs/{job.get('id')}/"
    metadata = _quote_job_metadata(job)
    import_source_path = str(metadata.get("import_source_path") or "").strip().lstrip("/")
    if import_source_path.startswith(job_prefix):
        paths.append(import_source_path)
    preview_paths = [metadata.get("import_manifest_path")]
    image_paths = metadata.get("import_preview_paths")
    if isinstance(image_paths, dict):
        preview_paths.extend(image_paths.values())
    for raw_path in preview_paths:
        path = str(raw_path or "").strip().lstrip("/")
        if path.startswith(preview_prefix):
            paths.append(path)
    return list(dict.fromkeys(paths))


def _release_quote_reservations(job: dict) -> None:
    job_id = str(job.get("id") or "").strip()
    if not job_id:
        raise RuntimeError("Cotizacion sin identificador")
    metadata = _quote_job_metadata(job)
    if metadata.get("source_type") == "mixed_catalog_cart":
        db_release_mixed_cart(job_id)
        return
    db_release_tarkett_reservations(job_id)
    db_release_offiho_reservations(job_id)
    db_release_catalog_reservations(job_id)


def _atomic_delete_quote_job(job: dict) -> None:
    try:
        job_id = str(uuid.UUID(str(job.get("id") or "")))
        usuario_id = int(job.get("usuario_id"))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Cotizacion invalida para eliminacion") from None
    if usuario_id <= 0:
        raise RuntimeError("Cotizacion invalida para eliminacion")
    deleted = _supabase_req(
        "POST",
        "/rpc/saas_delete_quote_job",
        json_data={"p_quote_job_id": job_id, "p_usuario_id": usuario_id},
    )
    if deleted is not True:
        raise RuntimeError("Cotizacion no encontrada para eliminacion")


def _is_supabase_schema_compat_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        message.startswith("Supabase HTTP 400")
        or message.startswith("Supabase HTTP 404")
    )


def _release_quote_reservations_rest_compat(job: dict) -> None:
    job_id = str(job.get("id") or "").strip()
    if not job_id:
        raise RuntimeError("Cotizacion sin identificador")
    for release in (
        db_release_tarkett_reservations,
        db_release_offiho_reservations,
        db_release_catalog_reservations,
    ):
        try:
            release(job_id)
        except RuntimeError as exc:
            if not _is_supabase_schema_compat_error(exc):
                raise


def _release_and_delete_quote_job(job: dict) -> None:
    try:
        _release_quote_reservations(job)
        db_delete_quote_job(str(job["id"]))
    except RuntimeError as exc:
        if (
            DEV_MODE
            or _use_postgres()
            or not _is_supabase_schema_compat_error(exc)
        ):
            raise
        _release_quote_reservations_rest_compat(job)
        try:
            db_delete_quote_job(str(job["id"]))
        except RuntimeError as delete_exc:
            if not _is_supabase_schema_compat_error(delete_exc):
                raise
            _atomic_delete_quote_job(job)


def _delete_storage_paths(paths: list[str]) -> None:
    raw_paths = [str(path or "") for path in paths if str(path or "").strip()]
    if not raw_paths:
        return
    if DEV_MODE:
        for path in raw_paths:
            _dev_storage_file(path).unlink(missing_ok=True)
        return
    clean_paths = [path.strip().lstrip("/") for path in raw_paths]
    if _use_r2_storage():
        try:
            client = _r2_client()
            for path in list(dict.fromkeys(clean_paths)):
                client.delete_object(Bucket=R2_BUCKET, Key=path)
        except Exception as exc:
            raise RuntimeError(f"Cloudflare R2 delete error: {exc.__class__.__name__}") from exc
        return
    _storage_req("DELETE", f"/object/{QUOTE_STORAGE_BUCKET}", json_data={"prefixes": list(dict.fromkeys(clean_paths))})


def _delete_quote_storage(job: dict) -> None:
    paths = _quote_storage_paths(job)
    _delete_storage_paths(paths)


def _quote_storage_job_dir(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "users" and parts[2] == "jobs":
        return "/".join(parts[:4])
    return None


def _storage_object_size_mb(obj: dict) -> float:
    metadata = obj.get("metadata") or {}
    try:
        return float(metadata.get("size") or 0) / 1024.0 / 1024.0
    except (TypeError, ValueError):
        return 0.0


def _storage_list_prefix(bucket: str, prefix: str) -> list[dict]:
    rows = []
    offset = 0
    while True:
        batch = _storage_req(
            "POST",
            f"/object/list/{bucket}",
            json_data={
                "prefix": prefix,
                "limit": 1000,
                "offset": offset,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            name = str(item.get("name") or "").strip("/")
            if not name:
                continue
            item["_full_name"] = f"{prefix.strip('/')}/{name}" if prefix else name
            rows.append(item)
        if len(batch) < 1000:
            break
        offset += len(batch)
    return rows


def _storage_list_recursive(bucket: str, prefix: str) -> list[dict]:
    if _use_r2_storage():
        found = []
        clean_prefix = prefix.strip("/")
        try:
            paginator = _r2_client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=clean_prefix):
                for obj in page.get("Contents", []):
                    key = str(obj.get("Key") or "").strip("/")
                    if not key:
                        continue
                    updated_at = obj.get("LastModified")
                    if hasattr(updated_at, "isoformat"):
                        updated_at = updated_at.isoformat()
                    found.append(
                        {
                            "id": str(obj.get("ETag") or key),
                            "name": key.rsplit("/", 1)[-1],
                            "_full_name": key,
                            "created_at": updated_at,
                            "updated_at": updated_at,
                            "metadata": {"size": obj.get("Size") or 0},
                        }
                    )
        except Exception as exc:
            raise RuntimeError(f"Cloudflare R2 list error: {exc.__class__.__name__}") from exc
        return found

    found = []
    pending = [prefix.strip("/")]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for item in _storage_list_prefix(bucket, current):
            full_name = item["_full_name"]
            if item.get("id"):
                found.append(item)
            else:
                pending.append(full_name)
    return found


def _build_storage_retention_plan(
    objects: list[dict],
    max_per_user: int,
    min_age_days: int = QUOTE_STORAGE_RETENTION_MIN_AGE_DAYS,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, int(min_age_days)))
    by_job = {}
    for obj in objects:
        path = str(obj.get("_full_name") or "").strip("/")
        job_dir = _quote_storage_job_dir(path)
        if not job_dir:
            continue
        leaf = path.rsplit("/", 1)[-1].lower()
        by_job.setdefault(job_dir, {})
        if leaf.startswith("output") and leaf.endswith(".xlsx"):
            by_job[job_dir]["output"] = obj
        elif leaf.startswith("input") and (leaf.endswith(".xlsx") or leaf.endswith(".pdf")):
            by_job[job_dir]["input"] = obj

    by_user = {}
    for job_dir, files in by_job.items():
        if "output" not in files:
            continue
        user_id = job_dir.split("/")[1]
        sort_date = _parse_iso_datetime(files["output"].get("updated_at") or files["output"].get("created_at"))
        by_user.setdefault(user_id, []).append((sort_date or datetime.fromtimestamp(0, timezone.utc), files))

    delete_paths = []
    summary = {
        "users_reviewed": len(by_user),
        "jobs_with_outputs": sum(len(rows) for rows in by_user.values()),
        "old_jobs_deleted": 0,
        "completed_inputs_deleted": 0,
        "recent_jobs_skipped": 0,
        "recent_inputs_skipped": 0,
        "objects_planned": 0,
        "estimated_mb": 0.0,
    }

    for jobs in by_user.values():
        jobs.sort(key=lambda row: row[0], reverse=True)
        for index, (sort_date, files) in enumerate(jobs):
            old_enough = sort_date <= cutoff
            input_obj = files.get("input")
            output_obj = files.get("output")
            if index >= max_per_user:
                if not old_enough:
                    summary["recent_jobs_skipped"] += 1
                    continue
                for obj in (input_obj, output_obj):
                    if obj:
                        delete_paths.append(obj["_full_name"])
                        summary["estimated_mb"] += _storage_object_size_mb(obj)
                summary["old_jobs_deleted"] += 1
            elif input_obj:
                if not old_enough:
                    summary["recent_inputs_skipped"] += 1
                    continue
                delete_paths.append(input_obj["_full_name"])
                summary["estimated_mb"] += _storage_object_size_mb(input_obj)
                summary["completed_inputs_deleted"] += 1

    delete_paths = list(dict.fromkeys(delete_paths))
    summary["objects_planned"] = len(delete_paths)
    summary["estimated_mb"] = round(summary["estimated_mb"], 2)
    return {"summary": summary, "delete_paths": delete_paths}


def _validate_emergency_delete_paths(paths: list[object]) -> list[str]:
    clean_paths = []
    for raw_path in paths:
        path = str(raw_path or "").strip().lstrip("/")
        parts = path.split("/")
        leaf = parts[-1].lower() if parts else ""
        if (
            len(parts) != 5
            or parts[0] != "users"
            or not parts[1].isdigit()
            or parts[2] != "jobs"
            or not parts[3]
            or not (leaf.startswith("input.") or leaf.startswith("output."))
            or not (leaf.endswith(".xlsx") or leaf.endswith(".pdf"))
        ):
            raise RuntimeError("Ruta de borrado de storage invalida")
        clean_paths.append(path)
    return list(dict.fromkeys(clean_paths))


def _created_at_sort_key(job: dict) -> str:
    return str(job.get("created_at") or job.get("updated_at") or "")


def _quote_job_metadata(job: dict) -> dict:
    metadata = job.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _parse_iso_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _downloaded_output_expired(job: dict, now: datetime) -> bool:
    if QUOTE_DOWNLOADED_OUTPUT_RETENTION_DAYS <= 0:
        return False
    metadata = _quote_job_metadata(job)
    downloaded_at = _parse_iso_datetime(metadata.get("last_downloaded_at"))
    if not downloaded_at:
        return False
    return downloaded_at <= now - timedelta(days=QUOTE_DOWNLOADED_OUTPUT_RETENTION_DAYS)


def _mark_quote_downloaded(job: dict) -> None:
    metadata = _quote_job_metadata(job)
    metadata["last_downloaded_at"] = _iso(datetime.now(timezone.utc))
    metadata["download_count"] = int(metadata.get("download_count") or 0) + 1
    db_update_quote_job(job["id"], {"metadata": metadata})


def _run_quote_retention(usuario_id: int, jobs: list[dict] | None = None, dry_run: bool = False) -> dict:
    current_jobs = list(jobs if jobs is not None else db_list_quote_jobs(usuario_id))
    sorted_jobs = sorted(current_jobs, key=_created_at_sort_key, reverse=True)
    completed_jobs = [job for job in sorted_jobs if job.get("status") == "completed"]
    now = datetime.now(timezone.utc)
    deleted_job_ids: set[str] = set()
    summary = {
        "usuario_id": usuario_id,
        "dry_run": dry_run,
        "max_completed_outputs_per_user": MAX_QUOTE_HISTORY_PER_USER,
        "downloaded_output_retention_days": QUOTE_DOWNLOADED_OUTPUT_RETENTION_DAYS,
        "delete_completed_inputs": DELETE_COMPLETED_QUOTE_INPUTS,
        "jobs_reviewed": len(sorted_jobs),
        "jobs_deleted": 0,
        "completed_inputs_deleted": 0,
        "storage_objects_deleted": 0,
        "storage_objects_planned": 0,
        "deleted_reasons": {},
    }

    for index, job in enumerate(completed_jobs):
        reason = None
        if index >= MAX_QUOTE_HISTORY_PER_USER:
            reason = "beyond_user_limit"
        elif _downloaded_output_expired(job, now):
            reason = "downloaded_output_expired"
        if not reason:
            continue

        paths = _quote_storage_paths(job)
        summary["storage_objects_planned"] += len(paths)
        summary["jobs_deleted"] += 1
        summary["deleted_reasons"][reason] = summary["deleted_reasons"].get(reason, 0) + 1
        deleted_job_ids.add(str(job["id"]))
        if not dry_run:
            _release_and_delete_quote_job(job)
            _delete_quote_storage(job)
            summary["storage_objects_deleted"] += len(paths)

    if DELETE_COMPLETED_QUOTE_INPUTS:
        for job in completed_jobs:
            if str(job["id"]) in deleted_job_ids:
                continue
            input_path = str(job.get("input_path") or "").strip().lstrip("/")
            if not input_path:
                continue
            summary["storage_objects_planned"] += 1
            summary["completed_inputs_deleted"] += 1
            if not dry_run:
                db_update_quote_job(job["id"], {"input_path": None})
                job["input_path"] = None
                _delete_storage_paths([input_path])
                summary["storage_objects_deleted"] += 1

    if not dry_run:
        print(
            json.dumps(
                {
                    "event": "quote_retention",
                    "usuario_id": usuario_id,
                    "dry_run": False,
                    "jobs_deleted": summary["jobs_deleted"],
                    "completed_inputs_deleted": summary["completed_inputs_deleted"],
                    "storage_objects_deleted": summary["storage_objects_deleted"],
                },
                separators=(",", ":"),
            )
        )

    remaining_jobs = [job for job in sorted_jobs if str(job["id"]) not in deleted_job_ids]
    summary["remaining_jobs"] = remaining_jobs
    return summary


def _enforce_quote_history_limit(usuario_id: int, jobs: list[dict] | None = None) -> list[dict]:
    return _run_quote_retention(usuario_id, jobs, dry_run=False)["remaining_jobs"]


def _enforce_active_quote_limit(usuario_id: int, *, exclude_job_id: str | None = None) -> None:
    try:
        jobs = db_list_quote_jobs(usuario_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error leyendo cotizaciones activas") from exc
    active = [
        job
        for job in jobs
        if str(job.get("id")) != str(exclude_job_id)
        and str(job.get("status") or "").strip().lower() in {"draft", "queued", "processing"}
    ]
    if len(active) >= MAX_ACTIVE_QUOTE_JOBS_PER_USER:
        raise HTTPException(
            status_code=429,
            detail=f"Limite de {MAX_ACTIVE_QUOTE_JOBS_PER_USER} cotizaciones activas por usuario",
        )


def _safe_filename_part(value: object, limit: int = 80) -> str:
    raw = str(value or "").strip()
    name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return "_".join(part for part in name.split("_") if part)[:limit]


def _quote_input_extension(filename: str) -> str:
    lower_name = str(filename or "").strip().lower()
    for extension in ALLOWED_QUOTE_INPUT_EXTENSIONS:
        if lower_name.endswith(extension):
            return extension
    raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx o .pdf")


def _safe_quote_filename(job: dict) -> str:
    metadata = job.get("metadata") or {}
    project = _safe_filename_part(metadata.get("proyecto"), 80)
    quote_number = _safe_filename_part(metadata.get("cotizacion"), 40)
    fallback = _safe_filename_part(job.get("id"), 80) or "cotizacion"
    name = f"{project}_{quote_number}" if project and quote_number else quote_number or project or fallback
    return f"Cotizacion_{name[:140]}.xlsx"


def _quote_number_prefix_for_user(user: dict) -> str | None:
    return QUOTE_NUMBER_PREFIX_BY_EMAIL.get(str(user.get("email", "")).strip().lower())


def _next_quote_number_for_user(user: dict) -> str | None:
    prefix = _quote_number_prefix_for_user(user)
    if not prefix:
        return None
    try:
        jobs = db_list_quote_jobs(int(user["id"]))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error leyendo folio de cotizacion: {e}")

    max_suffix = 0
    for job in jobs:
        raw = str((job.get("metadata") or {}).get("cotizacion", "")).strip()
        head, separator, tail = raw.partition("-")
        if head != prefix or separator != "-" or not tail.isdigit():
            continue
        max_suffix = max(max_suffix, int(tail))
    return f"{prefix}-{max_suffix + 1:05d}"


def _validate_metadata(body: dict) -> dict:
    fields = {
        "proyecto": "Proyecto",
        "cliente": "Cliente",
        "correo": "Correo",
        "telefono": "Telefono",
        "direccion": "Direccion",
        "razon_social": "Razon social",
    }
    clean = {}
    for key, label in fields.items():
        value = str(body.get(key, "")).strip()
        if not value:
            raise HTTPException(status_code=400, detail=f"{label} requerido")
        clean[key] = value[:500]
    quote_number = str(body.get("cotizacion", "")).strip()
    if quote_number:
        clean["cotizacion"] = quote_number[:500]
    raw_discount = str(body.get("descuento", "40")).replace("%", "").strip() or "40"
    try:
        discount = float(raw_discount)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Descuento invalido") from exc
    if discount < 0 or discount > 100:
        raise HTTPException(status_code=400, detail="Descuento debe estar entre 0 y 100")
    clean["descuento"] = discount
    description_language = str(body.get("description_language", "es")).strip().lower()
    if description_language not in {"es", "en"}:
        raise HTTPException(status_code=400, detail="Idioma de descripcion invalido")
    clean["description_language"] = description_language
    image_provider = str(body.get("image_provider", body.get("proveedor_imagen", "dezgo"))).strip().lower()
    image_provider = {
        "local": "pillow",
        "gratis": "pillow",
        "free": "pillow",
        "ia": "dezgo",
        "ai": "dezgo",
        "flux": "dezgo",
        "flux2": "dezgo",
        "flux_2": "dezgo",
        "sunon": "sunon_web",
        "sunon-web": "sunon_web",
        "web_sunon": "sunon_web",
        "catalogo_sunon": "sunon_catalog",
        "sunon-catalog": "sunon_catalog",
        "sunon_precise": "sunon_catalog",
    }.get(image_provider, image_provider)
    if image_provider not in {"pillow", "dezgo", "sunon_web", "sunon_catalog"}:
        raise HTTPException(status_code=400, detail="Proveedor de imagen invalido")
    clean["image_provider"] = image_provider
    cleanup_strength = str(body.get("image_cleanup_strength", body.get("limpieza_imagen", "balanced"))).strip().lower()
    cleanup_strength = {
        "suave": "normal",
        "conservadora": "normal",
        "fuerte": "aggressive",
        "agresiva": "aggressive",
    }.get(cleanup_strength, cleanup_strength)
    if cleanup_strength not in {"normal", "balanced", "aggressive"}:
        raise HTTPException(status_code=400, detail="Limpieza de imagen invalida")
    clean["image_cleanup_strength"] = cleanup_strength
    image_background = str(body.get("image_background", body.get("fondo_imagen", "white"))).strip().lower()
    image_background = {"blanco": "white", "transparente": "transparent"}.get(image_background, image_background)
    if image_background not in {"white", "transparent"}:
        raise HTTPException(status_code=400, detail="Fondo de imagen invalido")
    clean["image_background"] = image_background
    image_prompt = str(
        body.get("image_prompt", body.get("prompt_imagen", "Mejora la calidad de imagen y que este en fondo blanco"))
    ).strip()
    clean["image_prompt"] = (image_prompt or "Mejora la calidad de imagen y que este en fondo blanco")[:1000]
    clean["estimated_duration_seconds"] = 360 if image_provider == "dezgo" else 180 if image_provider in {"sunon_web", "sunon_catalog"} else 90
    return clean


def _create_signed_upload(path: str):
    if DEV_MODE:
        return {"token": "dev-upload-token"}
    if _use_r2_storage():
        try:
            signed_url = _r2_client().generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": R2_BUCKET,
                    "Key": path.strip("/"),
                    "ContentType": _quote_object_content_type(path),
                },
                ExpiresIn=SIGNED_UPLOAD_TTL_SECONDS,
            )
        except Exception as exc:
            raise RuntimeError(f"Cloudflare R2 signed upload error: {exc.__class__.__name__}") from exc
        return {"provider": "r2", "signed_upload_url": signed_url}
    encoded_path = quote(path, safe="/")
    return _storage_req(
        "POST",
        f"/object/upload/sign/{QUOTE_STORAGE_BUCKET}/{encoded_path}",
        json_data={"upsert": True, "expiresIn": SIGNED_UPLOAD_TTL_SECONDS},
    )


def _signed_upload_url(path: str, token: str) -> str:
    if _use_r2_storage():
        raise RuntimeError("Cloudflare R2 usa signed_upload_url directo, no token Supabase")
    encoded_path = quote(path, safe="/")
    return f"{SUPABASE_URL}/storage/v1/object/upload/sign/{QUOTE_STORAGE_BUCKET}/{encoded_path}?token={quote(token, safe='')}"


def _wake_worker():
    if DEV_MODE or not WORKER_WAKE_URL:
        return
    try:
        req = urllib.request.Request(WORKER_WAKE_URL, method="GET")
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:
        pass


def _create_signed_download(path: str, filename: str | None = None):
    if DEV_MODE:
        return f"{DEV_PUBLIC_BASE_URL}/dev/storage/{quote(path, safe='')}"
    if _use_r2_storage():
        params = {"Bucket": R2_BUCKET, "Key": path.strip("/")}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        try:
            return _r2_client().generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=SIGNED_DOWNLOAD_TTL_SECONDS,
            )
        except Exception as exc:
            raise RuntimeError(f"Cloudflare R2 signed download error: {exc.__class__.__name__}") from exc
    encoded_path = quote(path, safe="/")
    data = _storage_req(
        "POST",
        f"/object/sign/{QUOTE_STORAGE_BUCKET}/{encoded_path}",
        json_data={"expiresIn": SIGNED_DOWNLOAD_TTL_SECONDS},
    )
    signed_url = data.get("signedURL") or data.get("signedUrl") or data.get("url")
    if signed_url and signed_url.startswith("/"):
        signed_url = f"{SUPABASE_URL}/storage/v1{signed_url}"
    return signed_url


# ═══════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════

def _origins():
    raw = os.environ.get("CORS_ORIGINS", "")
    configured = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    allow_wildcard = os.environ.get("ALLOW_WILDCARD_CORS", "").lower() in {"1", "true", "yes"}

    if "*" in configured and allow_wildcard:
        return ["*"]

    origins = [origin for origin in configured if origin != "*"] or list(DEFAULT_CORS_ORIGINS)
    for env_name in ("PUBLIC_APP_ORIGIN", "APP_ORIGIN", "FRONTEND_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
        value = os.environ.get(env_name, "").strip().rstrip("/")
        if value:
            origins.append(value if value.startswith(("http://", "https://")) else f"https://{value}")
    return sorted(set(origins))


app = FastAPI(title="Mobiliti SaaS API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token invalido")
    try:
        usuario = db_get_usuario_by_id(int(user_id))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if not usuario.get("activo"):
        raise HTTPException(status_code=403, detail="Usuario desactivado")
    return usuario


def require_admin(current_user: dict = Depends(get_current_user)):
    if not current_user.get("es_admin"):
        raise HTTPException(status_code=403, detail="Se requieren permisos de administrador")
    return current_user


def require_retention_token(
    x_quote_retention_token: str = Header(None),
    x_mobiliti_rest_secret: str = Header(None),
):
    if QUOTE_RETENTION_TOKEN and x_quote_retention_token == QUOTE_RETENTION_TOKEN:
        return True
    if MOBILITI_REST_SECRET and x_mobiliti_rest_secret == MOBILITI_REST_SECRET:
        return True
    raise HTTPException(status_code=403, detail="Token de retencion requerido")


def require_worker_secret(x_mobiliti_rest_secret: str = Header(None)):
    if (
        MOBILITI_REST_SECRET
        and x_mobiliti_rest_secret
        and hmac.compare_digest(x_mobiliti_rest_secret, MOBILITI_REST_SECRET)
    ):
        return True
    raise HTTPException(status_code=403, detail="Credencial interna requerida")


_PROJECT_MUTATION_FIELDS = frozenset({
    "name", "payload", "expected_revision", "operation_id",
})
_PROJECT_STATUS_FIELDS = frozenset({"expected_revision", "operation_id"})
_PROJECT_DELETE_FIELDS = frozenset({"expected_revision", "confirm_name"})


def _project_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="ID de Proyecto invalido")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="ID de Proyecto invalido") from None
    if parsed.version != 4 or str(parsed) != value.lower():
        raise HTTPException(status_code=400, detail="ID de Proyecto invalido")
    return str(parsed)


def _project_name(value: object) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="Nombre de Proyecto invalido")
    name = value.strip()
    if (
        not name
        or len(name) > 120
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or any(unicodedata.category(char) in {"Cf", "Cs"} for char in name)
    ):
        raise HTTPException(status_code=400, detail="Nombre de Proyecto invalido")
    return name


def _project_expected_revision(value: object) -> int:
    if type(value) is not int or value < 0:
        raise HTTPException(status_code=400, detail="expected_revision invalido")
    return value


def _project_operation_id(value: object) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="operation_id invalido")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="operation_id invalido") from None
    if parsed.version != 4 or str(parsed) != value.lower():
        raise HTTPException(status_code=400, detail="operation_id invalido")
    return str(parsed)


def _project_unexpected_fields(body: dict, allowed: frozenset[str]) -> None:
    unexpected = set(body) - allowed
    if unexpected:
        raise HTTPException(
            status_code=400,
            detail=f"Campo de Proyecto no permitido: {min(unexpected)}",
        )


def _project_payload(value: object) -> dict:
    try:
        return normalize_project_payload(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _project_asset_keys(payload: dict) -> set[str]:
    return {
        asset_key
        for line in payload["lines"]
        for field in ("image_asset_key", "source_asset_key")
        if isinstance(line.get(field), str)
        if (asset_key := line[field])
    }


def _validate_project_asset_ownership(
    payload: dict,
    usuario_id: int,
    *,
    project_id: str | None = None,
    inherited_asset_keys: set[str] | None = None,
) -> None:
    """Acepta solo activos del usuario y evita inyectar activos de otro Proyecto."""
    inherited_asset_keys = inherited_asset_keys or set()
    for asset_key in _project_asset_keys(payload):
        match = ASSET_KEY.fullmatch(asset_key)
        if match is None or int(match.group(1)) != int(usuario_id):
            raise HTTPException(status_code=400, detail="Activo de Proyecto no permitido")
        if (
            project_id is not None
            and match.group(2) != project_id
            and asset_key not in inherited_asset_keys
        ):
            raise HTTPException(status_code=400, detail="Activo de Proyecto no permitido")


def _project_conflict(project: dict) -> None:
    raise HTTPException(
        status_code=409,
        detail={"code": "project_revision_conflict", "project": project},
    )


def _project_for_current_user(project_id: str, usuario_id: int) -> dict:
    project = db_get_project(project_id, usuario_id)
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return project


def _project_with_visible_import_images(project: dict) -> dict:
    visible = deepcopy(project)
    for line in visible["payload"]["lines"]:
        image_key = line.get("image_asset_key")
        if line.get("source") == "imported" and image_key:
            line["display_cache"]["image_url"] = _create_signed_download(image_key)
    return visible


def _copy_project_import_asset(path: str, content: bytes, content_type: str) -> None:
    """Crea una copia inmutable o verifica un reintento con la misma llave."""
    try:
        _storage_create_bytes_if_absent(path, content, content_type)
        return
    except _StorageObjectAlreadyExists:
        pass
    try:
        existing = _storage_download_bytes(path)
    except _StorageObjectNotFound as exc:
        raise RuntimeError("No se pudo verificar el activo de Proyecto") from exc
    if existing != content:
        raise ValueError("El activo de Proyecto ya existe con contenido diferente")


def _validate_project_import_preview_png(content: object) -> bytes | None:
    """Acepta solo una imagen PNG no animada, segura y dentro de los limites."""
    if (
        not isinstance(content, bytes)
        or not content
        or len(content) > IMPORT_PREVIEW_IMAGE_MAX_BYTES
        or _catalog_image_type(content) != ("png", "image/png")
        or not _catalog_image_has_exact_end(content, "png")
    ):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                width, height = probe.size
                if (
                    probe.format != "PNG"
                    or width < 1 or height < 1
                    or width > IMPORT_PREVIEW_IMAGE_MAX_WIDTH
                    or height > IMPORT_PREVIEW_IMAGE_MAX_HEIGHT
                    or width * height > IMPORT_PREVIEW_IMAGE_MAX_PIXELS
                    or getattr(probe, "is_animated", False)
                    or getattr(probe, "n_frames", 1) != 1
                ):
                    return None
                probe.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
                if (
                    decoded.format != "PNG"
                    or getattr(decoded, "is_animated", False)
                    or getattr(decoded, "n_frames", 1) != 1
                ):
                    return None
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None
    return content


_PROJECT_QUOTE_FIELDS = frozenset({"expected_revision", "template"})
_PROJECT_QUOTE_TEMPLATE = DEFAULT_TEMPLATE_PROFILE_ID


def _project_normalized_payload(value: dict) -> dict:
    if isinstance(value.get("lines"), list):
        return value
    context = value.get("project_context")
    normalized = (
        context.get("normalized_project_payload")
        if isinstance(context, dict)
        else None
    )
    if not isinstance(normalized, dict):
        raise ValueError("Contexto de Proyecto invalido")
    return normalized


def _project_import_source_keys(payload: dict) -> list[str]:
    payload = _project_normalized_payload(payload)
    imported_lines = [
        line for line in payload["lines"] if line["source"] == "imported"
    ]
    keys = {
        str(line.get("source_asset_key") or "").strip()
        for line in imported_lines
    }
    if "" in keys:
        raise ValueError("El Proyecto contiene una referencia importada sin fuente")
    if len(keys) > 1:
        raise ValueError("El Proyecto contiene mas de una Quotation de origen")
    return sorted(keys)


def _project_import_source_bytes(payload: dict) -> bytes | None:
    """Descarga la unica Quotation inmutable promovida del Proyecto."""

    keys = _project_import_source_keys(payload)
    if not keys:
        return None
    source_key = keys[0]
    match = ASSET_KEY.fullmatch(source_key)
    if match is None or match.group(3) != "sources":
        raise ValueError("Fuente importada de Proyecto no permitida")
    expected_hash = Path(match.group(4)).stem.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("Fuente importada de Proyecto no permitida")
    try:
        content = _storage_download_bytes(source_key)
    except _StorageObjectNotFound as exc:
        raise ValueError("Fuente importada de Proyecto no disponible") from exc
    if (
        not isinstance(content, bytes)
        or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected_hash)
    ):
        raise ValueError("La fuente importada no coincide con su manifiesto")
    return content


def _project_import_bundle(payload: dict) -> tuple[dict | None, bytes | None]:
    payload = _project_normalized_payload(payload)
    imported = [line for line in payload["lines"] if line["source"] == "imported"]
    source = _project_import_source_bytes(payload)
    if not imported:
        return None, None
    if source is None:
        raise ValueError("El Proyecto contiene una referencia importada sin fuente")
    import_ids = {line["import_id"] for line in imported}
    if len(import_ids) != 1:
        raise ValueError("El Proyecto contiene mas de una Quotation de origen")
    source_key = _project_import_source_keys(payload)[0]
    manifest, _images = build_import_manifest(
        source,
        next(iter(import_ids)),
        Path(source_key).name,
    )
    expected_hash = Path(source_key).stem.lower()
    if not hmac.compare_digest(manifest["source_hash"], expected_hash):
        raise ValueError("La fuente importada no coincide con su manifiesto")
    return manifest, source


def _project_catalog_browser_row(line: dict, quantity: Decimal) -> dict:
    row = {
        "line_id": line["line_id"],
        "catalog": line["catalog"],
        "quantity": format(quantity, "f"),
    }
    row.update(deepcopy(line["identity"]))
    return row


def _project_import_browser_row(line: dict, quantity: Decimal) -> dict:
    return {
        "kind": "imported",
        "line_id": line["line_id"],
        "import_id": line["import_id"],
        "source_row": line["source_row"],
        "source_currency": line["source_currency"],
        "quantity": format(quantity, "f"),
        "overrides": {
            "name": line["name"],
            "description": line["description"],
            "dimension": line["dimension"],
            "unit_price": line["unit_price"],
            "provider": line["provider"],
        },
        "official_code": line["official_code"],
        "image_asset_key": line["image_asset_key"],
        "source_asset_key": line["source_asset_key"],
    }


def _load_project_catalogs(browser_rows: list[dict]) -> tuple[dict, list[dict]]:
    requested = {row["catalog"] for row in browser_rows}
    if not requested <= set(MIXED_CATALOG_ORDER):
        raise ValueError("Catalogo mixto no soportado")
    catalogs: dict[str, dict] = {}
    if "tarkett" in requested:
        catalogs["tarkett"] = _load_tarkett_catalog_cached()
    if "offiho" in requested:
        catalogs["offiho"] = _load_offiho_catalog_cached()
    for supplier in sorted(requested - {"tarkett", "offiho"}):
        _require_enabled_catalog_supplier(supplier)
        catalogs[supplier] = _load_supplier_catalog_cached(supplier)
    return catalogs, db_list_exchange_rates()


def _project_payload_hash(payload: dict) -> str:
    checked = normalize_project_payload(deepcopy(payload))
    canonical = json.dumps(
        checked,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _project_quote_diagnostics(cart_payload: dict) -> dict[str, int]:
    compositions = cart_payload["project_context"]["compositions"]
    section_counts = [
        len(section["line_ids"]) for section in cart_payload["sections"]
    ]
    principal_count = len(compositions)
    complement_count = sum(
        max(0, len(composition["component_line_ids"]) - 1)
        for composition in compositions
    )
    return {
        "project_section_count": len(section_counts),
        "project_principal_count": principal_count,
        "project_complement_count": complement_count,
        "project_physical_line_count": sum(section_counts),
        "project_max_section_lines": max(section_counts, default=0),
    }


def _log_project_quote_stage(
    stage: str,
    *,
    started_at: float,
    project_id: str,
    project_revision: int,
    project_payload_hash: str,
    diagnostics: dict[str, int] | None = None,
    error_code: str | None = None,
) -> None:
    event: dict[str, Any] = {
        "event": "project_quote",
        "stage": stage,
        "duration_ms": max(
            0,
            int(round((time.perf_counter() - started_at) * 1000)),
        ),
        "project_id": project_id,
        "project_revision": project_revision,
        "project_payload_hash": project_payload_hash,
    }
    if diagnostics:
        event.update(diagnostics)
    if error_code:
        event["error_code"] = error_code
    PROJECT_QUOTE_LOGGER.info(
        json.dumps(
            event,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _build_saved_project_quote_payload(
    project: dict,
    usuario_id: int,
) -> tuple[dict, bytes | None, dict | None]:
    checked = normalize_project_payload(deepcopy(project["payload"]))
    _validate_project_asset_ownership(checked, usuario_id)
    projection = project_quote_projection(checked)
    lines_by_id = {line["line_id"]: line for line in checked["lines"]}

    catalog_rows: list[dict] = []
    imported_rows: list[dict] = []
    for component in projection.components:
        line = lines_by_id[component.line_id]
        if line["source"] == "catalog":
            catalog_rows.append(
                _project_catalog_browser_row(line, component.physical_quantity)
            )
        else:
            imported_rows.append(
                _project_import_browser_row(line, component.physical_quantity)
            )

    manifest, import_source_bytes = _project_import_bundle(checked)
    catalogs, rate_rows = _load_project_catalogs(catalog_rows)
    compositions_by_section: dict[str, list[str]] = {}
    for composition in projection.compositions:
        compositions_by_section.setdefault(composition.section_id, []).extend(
            composition.component_line_ids
        )
    sections = [
        {
            "id": section["section_id"],
            "title": section["concept"],
            "line_ids": compositions_by_section.get(section["section_id"], []),
        }
        for section in checked["sections"]
        if compositions_by_section.get(section["section_id"])
    ]
    context = project_context(checked, project["id"], project["revision"])
    payload = build_mixed_catalog_cart_payload(
        catalog_rows,
        catalogs=catalogs,
        rate_rows=rate_rows,
        quote_currency=checked["quote_fields"]["quote_currency"],
        commercial_discount_percent=checked["quote_fields"]["descuento"],
        presentation_sections=sections,
        imported_source=(
            {
                "manifest": manifest,
                "items": imported_rows,
                "source_currency": None,
            }
            if imported_rows
            else None
        ),
        project_context=context,
    )
    return payload, import_source_bytes, manifest


@app.post("/projects", status_code=201)
def projects_create(body: dict, current_user: dict = Depends(get_current_user)):
    _require_active_subscription(current_user["id"])
    _project_unexpected_fields(body, frozenset({"name", "payload"}))
    name = _project_name(body.get("name"))
    payload = _project_payload(body.get("payload"))
    # El contrato no entrega un UUID antes de crear; por ello en este borde solo
    # se aceptan rutas del usuario. Las actualizaciones restringen además el UUID.
    _validate_project_asset_ownership(payload, current_user["id"])
    return {"project": db_create_project(current_user["id"], name, payload)}


@app.get("/projects")
def projects_list(status: str = "active", current_user: dict = Depends(get_current_user)):
    if status not in {"active", "archived"}:
        raise HTTPException(status_code=400, detail="Estado de Proyecto invalido")
    return {"projects": db_list_projects(current_user["id"], status)}


@app.get("/projects/{project_id}")
def projects_get(project_id: str, current_user: dict = Depends(get_current_user)):
    project = _project_for_current_user(_project_uuid(project_id), current_user["id"])
    return {"project": _project_with_visible_import_images(project)}


@app.post("/projects/{project_id}/imports/{job_id}")
def projects_promote_import(
    project_id: str,
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    _require_active_subscription(current_user["id"])
    project_id = _project_uuid(project_id)
    job_id = _project_uuid(job_id)
    project = _project_for_current_user(project_id, current_user["id"])
    if project.get("status") != "active":
        raise HTTPException(status_code=409, detail="Proyecto archivado")
    manifest, _job, source = _validated_import_source(
        current_user["id"], [{"import_id": job_id}]
    )
    if len(source) > MAX_QUOTE_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="Fuente importada excede el limite de tamano")

    preview_paths = _quote_job_metadata(_job).get("import_preview_paths")
    if not isinstance(preview_paths, dict):
        raise HTTPException(status_code=409, detail="La fuente debe volver a importarse")
    valid_rows = {item["source_row"] for item in manifest["items"]}
    previews: list[tuple[str, bytes]] = []
    try:
        for row_text, preview_path in sorted(preview_paths.items(), key=lambda item: int(item[0])):
            row = int(row_text)
            if str(row) != row_text or row not in valid_rows:
                raise ValueError("Fila de previsualizacion invalida")
            content = _storage_download_bytes(preview_path)
            if isinstance(content, bytes) and len(content) > IMPORT_PREVIEW_IMAGE_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Imagen de previsualizacion excede el limite de tamano",
                )
            preview_png = _validate_project_import_preview_png(content)
            if preview_png is None:
                raise ValueError("Imagen de previsualizacion invalida")
            previews.append((row_text, preview_png))
    except HTTPException:
        raise
    except RuntimeError:
        raise HTTPException(status_code=503, detail="No se pudo leer la previsualizacion importada") from None
    except (TypeError, ValueError):
        raise HTTPException(status_code=409, detail="La fuente debe volver a importarse") from None

    prefix = f"projects/{current_user['id']}/{project_id}"
    source_key = f"{prefix}/sources/{manifest['source_hash']}.xlsx"
    image_asset_keys: dict[str, str] = {}
    try:
        _copy_project_import_asset(
            source_key,
            source,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        for row_text, content in previews:
            image_key = f"{prefix}/images/{manifest['source_hash'][:16]}-row-{int(row_text)}.png"
            _copy_project_import_asset(image_key, content, "image/png")
            image_asset_keys[row_text] = image_key
    except ValueError:
        raise HTTPException(status_code=409, detail="El activo importado no coincide con el Proyecto") from None
    except RuntimeError:
        raise HTTPException(status_code=503, detail="No se pudo guardar el activo importado") from None
    return {
        "source_asset_key": source_key,
        "image_asset_keys": image_asset_keys,
        "manifest": manifest,
    }


@app.patch("/projects/{project_id}")
def projects_patch(project_id: str, body: dict, current_user: dict = Depends(get_current_user)):
    _require_active_subscription(current_user["id"])
    _project_unexpected_fields(body, _PROJECT_MUTATION_FIELDS)
    project_id = _project_uuid(project_id)
    current = _project_for_current_user(project_id, current_user["id"])
    expected_revision = _project_expected_revision(body.get("expected_revision"))
    operation_id = _project_operation_id(body.get("operation_id"))
    if current["revision"] != expected_revision and current.get("last_operation_id") != operation_id:
        _project_conflict(current)
    name = _project_name(body.get("name"))
    payload = _project_payload(body.get("payload"))
    _validate_project_asset_ownership(
        payload,
        current_user["id"],
        project_id=project_id,
        inherited_asset_keys=_project_asset_keys(current["payload"]),
    )
    saved = db_save_project(
        project_id, current_user["id"], name, payload,
        expected_revision=expected_revision, operation_id=operation_id,
    )
    if saved:
        return {"project": saved}
    _project_conflict(_project_for_current_user(project_id, current_user["id"]))


@app.delete("/projects/{project_id}")
def projects_delete(project_id: str, body: dict, current_user: dict = Depends(get_current_user)):
    _require_active_subscription(current_user["id"])
    _project_unexpected_fields(body, _PROJECT_DELETE_FIELDS)
    project_id = _project_uuid(project_id)
    current = _project_for_current_user(project_id, current_user["id"])
    if current.get("status") != "archived":
        raise HTTPException(
            status_code=409,
            detail="Archiva el Proyecto antes de eliminarlo",
        )
    expected_revision = _project_expected_revision(body.get("expected_revision"))
    if current["revision"] != expected_revision:
        _project_conflict(current)
    if body.get("confirm_name") != current["name"]:
        raise HTTPException(
            status_code=400,
            detail="Escribe el nombre exacto del Proyecto",
        )
    if db_delete_archived_project(
        project_id,
        current_user["id"],
        expected_revision=expected_revision,
    ):
        return {"deleted": True, "project_id": project_id}
    latest = db_get_project(project_id, current_user["id"])
    if latest:
        _project_conflict(latest)
    raise HTTPException(status_code=404, detail="Proyecto no encontrado")


def _projects_set_status(
    project_id: str,
    body: dict,
    current_user: dict,
    status: str,
) -> dict:
    _require_active_subscription(current_user["id"])
    _project_unexpected_fields(body, _PROJECT_STATUS_FIELDS)
    project_id = _project_uuid(project_id)
    current = _project_for_current_user(project_id, current_user["id"])
    expected_revision = _project_expected_revision(body.get("expected_revision"))
    operation_id = _project_operation_id(body.get("operation_id"))
    if current["revision"] != expected_revision and current.get("last_operation_id") != operation_id:
        _project_conflict(current)
    changed = db_set_project_status(
        project_id, current_user["id"], status,
        expected_revision=expected_revision, operation_id=operation_id,
    )
    if changed:
        return {"project": changed}
    _project_conflict(_project_for_current_user(project_id, current_user["id"]))


@app.post("/projects/{project_id}/archive")
def projects_archive(project_id: str, body: dict, current_user: dict = Depends(get_current_user)):
    return _projects_set_status(project_id, body, current_user, "archived")


@app.post("/projects/{project_id}/restore")
def projects_restore(project_id: str, body: dict, current_user: dict = Depends(get_current_user)):
    return _projects_set_status(project_id, body, current_user, "active")


@app.post("/projects/{project_id}/duplicate", status_code=201)
def projects_duplicate(
    project_id: str,
    body: dict | None = None,
    current_user: dict = Depends(get_current_user),
):
    _require_active_subscription(current_user["id"])
    _project_unexpected_fields(body or {}, frozenset())
    project_id = _project_uuid(project_id)
    current = _project_for_current_user(project_id, current_user["id"])
    duplicate_name = _project_name(f"{current['name']} (copia)")
    payload = _project_payload(deepcopy(current["payload"]))
    # El duplicado conserva referencias inmutables del Proyecto de origen; el
    # prefijo del usuario mantiene el aislamiento sin reescribir objetos.
    _validate_project_asset_ownership(payload, current_user["id"])
    return {
        "project": db_create_project(
            current_user["id"], duplicate_name, deepcopy(payload)
        )
    }


# ─── Health Check ─────────────────────────────────────────────

@app.post("/projects/{project_id}/quote", status_code=202)
def projects_quote(
    project_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    _require_active_subscription(current_user["id"])
    _project_unexpected_fields(body, _PROJECT_QUOTE_FIELDS)
    template = _canonical_template_id(body.get("template"))
    project_id = _project_uuid(project_id)
    project = _project_for_current_user(project_id, current_user["id"])
    expected_revision = _project_expected_revision(body.get("expected_revision"))
    if project["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail="Restaura el Proyecto antes de cotizar",
        )
    if project["revision"] != expected_revision:
        _project_conflict(project)

    preflight_started_at = time.perf_counter()
    persisted_payload_hash = _project_payload_hash(project["payload"])
    try:
        cart_payload, import_source_bytes, import_manifest = (
            _build_saved_project_quote_payload(project, current_user["id"])
        )
        encoded = json.dumps(
            cart_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        validate_quote_size(
            section_counts=[
                len(section["line_ids"]) for section in cart_payload["sections"]
            ],
            encoded_bytes=len(encoded),
        )
    except HTTPException:
        raise
    except (TypeError, ValueError, RuntimeError) as exc:
        error_code = (
            "project_section_mapping_invalid"
            if "Contexto de Proyecto invalido" in str(exc)
            else "project_quote_preflight_invalid"
        )
        _log_project_quote_stage(
            "preflight_failed",
            started_at=preflight_started_at,
            project_id=project_id,
            project_revision=expected_revision,
            project_payload_hash=persisted_payload_hash,
            error_code=error_code,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from None

    diagnostics = _project_quote_diagnostics(cart_payload)
    _log_project_quote_stage(
        "preflight_validated",
        started_at=preflight_started_at,
        project_id=project_id,
        project_revision=expected_revision,
        project_payload_hash=persisted_payload_hash,
        diagnostics=diagnostics,
    )
    quote_fields = cart_payload["project_context"][
        "normalized_project_payload"
    ]["quote_fields"]
    metadata = _validate_metadata({
        **quote_fields,
        "image_provider": "pillow",
    })
    contract_hash = lookup_template_profile(template).template_contract_sha256
    metadata.update({
        "source_type": "mixed_catalog_cart",
        "original_filename": f"project-{project_id}-r{expected_revision}.json",
        "input_extension": ".json",
        "storage_provider": _storage_provider_name(),
        "input_storage_provider": _storage_provider_name(),
        "mixed_item_count": cart_payload["item_count"],
        "mixed_section_count": len(cart_payload["sections"]),
        "catalog_item_counts": {
            group["catalog"]: len(group["items"])
            for group in cart_payload["groups"]
        },
        "catalog_source_hashes": {
            group["catalog"]: group["catalog_source_hash"]
            for group in cart_payload["groups"]
        },
        "quote_currency": cart_payload["quote_currency"],
        "rate_summary": cart_payload["rate_summary"],
        "auto_electrification_rate": cart_payload["auto_electrification_rate"],
        "project_id": project_id,
        "project_revision": expected_revision,
        "project_payload_hash": cart_payload["project_context"][
            "project_payload_hash"
        ],
        **diagnostics,
        "template_contract_hash": contract_hash,
        "estimated_duration_seconds": 120,
    })
    if import_manifest is not None:
        metadata.update(_import_metadata(
            import_manifest,
            cart_payload["imported_source"],
            cart_payload["quote_currency"],
        ))
    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]

    _enforce_active_quote_limit(current_user["id"])
    enqueue_started_at = time.perf_counter()
    _log_project_quote_stage(
        "enqueue_started",
        started_at=enqueue_started_at,
        project_id=project_id,
        project_revision=expected_revision,
        project_payload_hash=persisted_payload_hash,
        diagnostics=diagnostics,
    )
    try:
        job = _enqueue_mixed_payload(
            current_user=current_user,
            cart_payload=cart_payload,
            template=template,
            metadata=metadata,
            import_job=None,
            import_source_bytes=import_source_bytes,
        )
    except Exception as exc:
        _log_project_quote_stage(
            "enqueue_failed",
            started_at=enqueue_started_at,
            project_id=project_id,
            project_revision=expected_revision,
            project_payload_hash=persisted_payload_hash,
            diagnostics=diagnostics,
            error_code="project_quote_enqueue_failed",
        )
        raise HTTPException(
            status_code=503,
            detail="No fue posible crear la cotizacion del Proyecto",
        ) from exc
    _log_project_quote_stage(
        "enqueued",
        started_at=enqueue_started_at,
        project_id=project_id,
        project_revision=expected_revision,
        project_payload_hash=persisted_payload_hash,
        diagnostics=diagnostics,
    )
    _wake_worker()
    return {"mensaje": "Cotizacion del Proyecto en cola", "job": job}


@app.get("/")
def root():
    return {"status": "ok", "service": "Mobiliti SaaS API"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "db_backend": "postgres" if _use_postgres() else "supabase_rest",
        "storage_provider": "r2" if _use_r2_storage() else "supabase",
        "storage_configured": _storage_configured(),
    }


# ─── LOGIN ────────────────────────────────────────────────────

@app.post("/login")
def login_endpoint(body: dict, request: Request):
    client_ip = request.client.host if request.client else "unknown"

    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera 15 minutos.")

    email = body.get("email", "").lower().strip()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")

    try:
        usuario = db_get_usuario_by_email(email)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if not usuario:
        _record_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Credenciales invalidas")

    if not verify_password(password, usuario["hashed_password"]):
        _record_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Credenciales invalidas")

    try:
        suscripcion = db_get_suscripcion_by_usuario(usuario["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    now = datetime.now(timezone.utc)

    if not suscripcion:
        raise HTTPException(status_code=403, detail="No tienes una suscripcion activa")

    fecha_fin = datetime.fromisoformat(suscripcion["fecha_fin"].replace("Z", "+00:00"))
    if suscripcion["estado"] != "activa" or fecha_fin < now:
        raise HTTPException(status_code=403, detail="Tu suscripcion ha expirado o esta suspendida")

    token = create_access_token(data={"sub": str(usuario["id"]), "email": email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": {
            "id": usuario["id"],
            "email": usuario["email"],
            "nombre": usuario["nombre"],
            "empresa": usuario["empresa"],
            "es_admin": bool(usuario["es_admin"]),
        },
        "suscripcion": {
            "id": suscripcion["id"],
            "estado": suscripcion["estado"],
            "plan": suscripcion["plan"],
            "fecha_inicio": suscripcion["fecha_inicio"],
            "fecha_fin": suscripcion["fecha_fin"],
        }
    }


# ─── VERIFICAR SESION ─────────────────────────────────────────

@app.post("/verificar-sesion")
def verificar_sesion(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")

    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")

    user_id = payload.get("sub")
    try:
        usuario = db_get_usuario_by_id(int(user_id))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if not usuario or not usuario.get("activo"):
        raise HTTPException(status_code=403, detail="Usuario no encontrado o inactivo")

    try:
        suscripcion = db_get_suscripcion_by_usuario(usuario["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    now = datetime.now(timezone.utc)

    if not suscripcion:
        raise HTTPException(status_code=403, detail="Sin suscripcion")

    fecha_fin = datetime.fromisoformat(suscripcion["fecha_fin"].replace("Z", "+00:00"))
    if suscripcion["estado"] != "activa" or fecha_fin < now:
        raise HTTPException(status_code=403, detail="Suscripcion expirada o suspendida")

    return {
        "valido": True,
        "usuario": {"id": usuario["id"], "email": usuario["email"], "nombre": usuario["nombre"]},
        "suscripcion": {"estado": suscripcion["estado"], "plan": suscripcion["plan"], "fecha_fin": suscripcion["fecha_fin"]}
    }


# ─── ADMIN: USUARIOS ──────────────────────────────────────────

@app.get("/admin/usuarios")
def admin_list_usuarios(user: dict = Depends(require_admin)):
    try:
        return db_list_usuarios()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")


@app.post("/admin/usuarios")
def admin_create_usuario(body: dict, user: dict = Depends(require_admin)):
    email = body.get("email", "").lower().strip()
    password = body.get("password", "")
    nombre = body.get("nombre", "")
    empresa = body.get("empresa", "")
    es_admin = body.get("es_admin", False)

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")

    try:
        existing = db_get_usuario_by_email(email)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if existing:
        raise HTTPException(status_code=409, detail="El email ya esta registrado")

    hashed = get_password_hash(password)
    try:
        nuevo = db_create_usuario(email, hashed, nombre, empresa, es_admin)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    return {"mensaje": "Usuario creado", "usuario": nuevo}


# ─── ADMIN: SUSCRIPCIONES ─────────────────────────────────────

@app.get("/admin/suscripciones")
def admin_list_suscripciones(user: dict = Depends(require_admin)):
    try:
        return db_list_suscripciones()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")


@app.post("/admin/suscripciones")
def admin_create_suscripcion(body: dict, user: dict = Depends(require_admin)):
    usuario_id = body.get("usuario_id")
    plan = body.get("plan", "mensual")
    dias = body.get("dias", 30)
    estado = body.get("estado", "activa")

    if not usuario_id:
        raise HTTPException(status_code=400, detail="usuario_id requerido")

    now = datetime.now(timezone.utc)
    fecha_fin = now + timedelta(days=dias)
    try:
        nueva = db_create_suscripcion(int(usuario_id), plan, now, fecha_fin, estado)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    return {"mensaje": "Suscripcion creada", "suscripcion": nueva}


@app.patch("/admin/suscripciones/{suscripcion_id}")
def admin_update_suscripcion(suscripcion_id: int, body: dict, user: dict = Depends(require_admin)):
    allowed = {"estado", "plan", "fecha_fin"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No hay campos validos para actualizar")
    try:
        updated = db_update_suscripcion(suscripcion_id, updates)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    return {"mensaje": "Suscripcion actualizada", "suscripcion": updated}


@app.post("/admin/storage-retention")
def admin_storage_retention(body: dict | None = None, user: dict = Depends(require_admin)):
    body = body or {}
    dry_run = bool(body.get("dry_run", True))
    requested_user_id = body.get("usuario_id")

    try:
        if requested_user_id:
            user_ids = [int(requested_user_id)]
        else:
            user_ids = [int(row["id"]) for row in db_list_usuarios()]

        reports = []
        totals = {
            "users_reviewed": 0,
            "jobs_deleted": 0,
            "completed_inputs_deleted": 0,
            "storage_objects_planned": 0,
            "storage_objects_deleted": 0,
        }
        for target_user_id in user_ids:
            report = _run_quote_retention(target_user_id, dry_run=dry_run)
            report.pop("remaining_jobs", None)
            reports.append(report)
            totals["users_reviewed"] += 1
            totals["jobs_deleted"] += int(report["jobs_deleted"])
            totals["completed_inputs_deleted"] += int(report["completed_inputs_deleted"])
            totals["storage_objects_planned"] += int(report["storage_objects_planned"])
            totals["storage_objects_deleted"] += int(report["storage_objects_deleted"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error ejecutando retencion: {e}")

    return {
        "dry_run": dry_run,
        "policy": {
            "max_completed_outputs_per_user": MAX_QUOTE_HISTORY_PER_USER,
            "downloaded_output_retention_days": QUOTE_DOWNLOADED_OUTPUT_RETENTION_DAYS,
            "delete_completed_inputs": DELETE_COMPLETED_QUOTE_INPUTS,
        },
        "totals": totals,
        "users": reports,
    }


@app.post("/admin/storage-retention-emergency")
def admin_storage_retention_emergency(body: dict | None = None, _authorized: bool = Depends(require_retention_token)):
    body = body or {}
    dry_run = bool(body.get("dry_run", True))
    max_per_user = int(body.get("max_per_user", MAX_QUOTE_HISTORY_PER_USER) or MAX_QUOTE_HISTORY_PER_USER)
    min_age_days = int(body.get("min_age_days", QUOTE_STORAGE_RETENTION_MIN_AGE_DAYS) or 0)
    prefix = str(body.get("prefix", "users")).strip("/") or "users"
    try:
        if body.get("paths") is not None:
            if body.get("confirm") != "delete-quote-storage-paths":
                raise RuntimeError("Confirmacion requerida")
            paths = _validate_emergency_delete_paths(body.get("paths") or [])
            summary = {
                "bucket": _storage_bucket_name(),
                "dry_run": dry_run,
                "mode": "explicit_paths",
                "objects_planned": len(paths),
            }
            if not dry_run:
                _delete_storage_paths(paths)
                summary["objects_deleted"] = len(paths)
                print(
                    json.dumps(
                        {"event": "quote_storage_explicit_delete", "objects_deleted": len(paths)},
                        separators=(",", ":"),
                    )
                )
            return summary

        objects = _storage_list_recursive(QUOTE_STORAGE_BUCKET, prefix)
        plan = _build_storage_retention_plan(objects, max_per_user, min_age_days=min_age_days)
        summary = {
            **plan["summary"],
            "bucket": _storage_bucket_name(),
            "dry_run": dry_run,
            "max_per_user": max_per_user,
            "min_age_days": min_age_days,
        }
        if not dry_run:
            _delete_storage_paths(plan["delete_paths"])
            summary["objects_deleted"] = len(plan["delete_paths"])
            print(json.dumps({"event": "quote_storage_retention_emergency", **summary}, separators=(",", ":")))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error ejecutando retencion de storage: {e}")

    return summary


# ─── GENERAR COTIZACION ───────────────────────────────────────

# COTIZACIONES WEB

@app.get("/catalogs")
def supplier_catalog_registry(current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error leyendo suscripcion") from exc
    return {
        "suppliers": [
            {"supplier": supplier, "label": CATALOG_SUPPLIER_LABELS[supplier]}
            for supplier in _enabled_catalog_suppliers()
        ]
    }


@app.get("/catalogs/exchange-rates")
def supplier_exchange_rates(base_currency: str = "USD", current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        return _catalog_exchange_rates_response(base_currency)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Tipos de cambio no disponibles") from exc


@app.get("/catalogs/search")
def catalog_search(
    q: str = "",
    supplier: str | None = None,
    offset: str = "0",
    limit: str = "20",
    current_user: dict = Depends(get_current_user),
):
    try:
        parsed_offset = _catalog_search_integer(offset, "offset", 0)
        parsed_limit = _catalog_search_integer(limit, "limit", 1, 50)
        # Valida todos los controles antes de abrir un catalogo publicado.
        search_catalog_products(
            {}, query=q, supplier=supplier, offset=parsed_offset, limit=parsed_limit,
        )
        clean_supplier = supplier.strip().lower() if supplier is not None else None
        _require_active_subscription(current_user["id"])
        snapshots = _catalog_search_snapshots(current_user["id"], clean_supplier)
        return search_catalog_products(
            snapshots, query=q, supplier=clean_supplier,
            offset=parsed_offset, limit=parsed_limit,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Catalogos publicados no disponibles") from exc


@app.get("/catalogs/{supplier}")
def supplier_catalog(supplier: str, current_user: dict = Depends(get_current_user)):
    supplier = _require_enabled_catalog_supplier(supplier)
    try:
        _require_active_subscription(current_user["id"])
        return _supplier_catalog_response(supplier, current_user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Catalogo publicado no disponible") from exc


def _mixed_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Campo JSON duplicado: {key}")
        result[key] = value
    return result


def _reject_mixed_json_constant(value):
    raise ValueError(f"Constante JSON invalida: {value}")


async def _read_mixed_quote_body(request: Request) -> object:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            declared = int(raw_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length invalido") from exc
        if declared < 0 or declared > MAX_QUOTE_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Solicitud mixta de {declared} bytes excede el limite de "
                    f"{MAX_QUOTE_REQUEST_BYTES} bytes"
                ),
            )
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_QUOTE_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Solicitud mixta de {size} bytes excede el limite de "
                    f"{MAX_QUOTE_REQUEST_BYTES} bytes"
                ),
            )
        chunks.append(chunk)
    try:
        return json.loads(
            b"".join(chunks), object_pairs_hook=_mixed_json_object,
            parse_constant=_reject_mixed_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Solicitud mixta invalida") from exc


async def _read_supplier_quote_body(request: Request) -> object:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            declared = int(raw_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length invalido") from exc
        if declared < 0 or declared > MAX_QUOTE_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Solicitud de proveedor de {declared} bytes excede el limite de "
                    f"{MAX_QUOTE_REQUEST_BYTES} bytes"
                ),
            )
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_QUOTE_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Solicitud de proveedor de {size} bytes excede el limite de "
                    f"{MAX_QUOTE_REQUEST_BYTES} bytes"
                ),
            )
        chunks.append(chunk)
    try:
        return json.loads(
            b"".join(chunks), object_pairs_hook=_mixed_json_object,
            parse_constant=_reject_mixed_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Solicitud de proveedor invalida"
        ) from exc


def _split_mixed_quote_items(raw_items: list[dict]) -> tuple[list[dict], list[dict]]:
    catalog_items: list[dict] = []
    imported_items: list[dict] = []
    import_ids: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto mixto debe ser un objeto")
        kind = raw.get("kind")
        if kind is None:
            catalog_items.append(raw)
            continue
        if kind != "imported":
            raise ValueError("Origen mixto no soportado")
        imported_items.append(raw)
        import_id = raw.get("import_id")
        if not isinstance(import_id, str):
            raise ValueError("import_id invalido")
        import_ids.add(import_id)
    if len(import_ids) > 1:
        raise ValueError("Solo se permite una quotation importada")
    return catalog_items, imported_items


def _validated_import_source(usuario_id: int, imported_items: list[dict]) -> tuple[dict, dict, bytes]:
    import_id = imported_items[0]["import_id"]
    try:
        canonical_import_id = str(uuid.UUID(import_id))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="import_id invalido") from None
    if canonical_import_id != import_id:
        raise HTTPException(status_code=400, detail="import_id invalido")
    job = _quote_job_for_user(canonical_import_id, usuario_id)
    metadata = _quote_job_metadata(job)
    consumed_by = metadata.get("import_consumed_by_job_id")
    if job.get("status") == "failed" and isinstance(consumed_by, str):
        try:
            consumer_id = str(uuid.UUID(consumed_by))
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(status_code=409, detail="La fuente debe volver a importarse") from None
        consumer = _quote_job_for_user(consumer_id, usuario_id)
        if consumer.get("status") != "failed":
            raise HTTPException(status_code=409, detail="La fuente ya esta en uso")
        restored_metadata = dict(metadata)
        restored_metadata.pop("import_consumed_by_job_id", None)
        restored_metadata.pop("import_consumed_at", None)
        try:
            restored = db_update_quote_job(
                canonical_import_id,
                {"status": "draft", "metadata": restored_metadata, "error_message": None},
                expected_status="failed",
            )
        except RuntimeError:
            raise HTTPException(status_code=503, detail="No se pudo recuperar la fuente importada") from None
        if not restored:
            raise HTTPException(status_code=409, detail="La fuente cambio de estado")
        job = restored
        metadata = restored_metadata
        consumed_by = None
    if job.get("status") != "draft" or consumed_by is not None:
        raise HTTPException(status_code=409, detail="La fuente debe volver a importarse")
    expected_prefix = f"users/{usuario_id}/jobs/{canonical_import_id}/"
    input_path = str(job.get("input_path") or "").strip().lstrip("/")
    manifest_path = str(metadata.get("import_manifest_path") or "").strip().lstrip("/")
    expected_hash = metadata.get("import_source_hash")
    expected_count = metadata.get("import_item_count")
    preview_paths = metadata.get("import_preview_paths")
    original_filename = metadata.get("original_filename")
    if (
        input_path != f"{expected_prefix}input.xlsx"
        or manifest_path != f"{expected_prefix}preview/{expected_hash[:16]}/manifest.json"
        or not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        or type(expected_count) is not int
        or not isinstance(preview_paths, dict)
        or not isinstance(original_filename, str)
        or any(
            not isinstance(row, str)
            or not row.isdigit()
            or not isinstance(path, str)
            or path != f"{expected_prefix}preview/{expected_hash[:16]}/row-{row}.png"
            for row, path in preview_paths.items()
        )
    ):
        raise HTTPException(status_code=409, detail="La fuente debe volver a importarse")
    try:
        source_bytes = _storage_download_bytes(input_path)
        manifest_bytes = _storage_download_bytes(manifest_path)
        stored = json.loads(
            manifest_bytes,
            object_pairs_hook=_mixed_json_object,
            parse_constant=_reject_mixed_json_constant,
        )
        if not isinstance(stored, dict) or set(stored) != {
            "schema_version", "import_id", "source_hash", "original_filename", "provider",
            "source_currency", "currency_status", "columns", "sections", "items",
            "preview_image_paths",
        }:
            raise ValueError("manifest")
        stored_preview_paths = stored.pop("preview_image_paths")
        manifest = validate_import_manifest(stored)
        reparsed, _images = build_import_manifest(
            source_bytes, canonical_import_id, manifest["original_filename"]
        )
    except RuntimeError:
        raise HTTPException(status_code=503, detail="No se pudo validar la fuente importada") from None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise HTTPException(status_code=409, detail="La fuente debe volver a importarse") from None
    if (
        manifest["import_id"] != canonical_import_id
        or manifest["original_filename"] != original_filename
        or manifest["source_hash"] != expected_hash
        or len(manifest["items"]) != expected_count
        or stored_preview_paths != preview_paths
        or reparsed != manifest
    ):
        raise HTTPException(status_code=409, detail="La fuente debe volver a importarse")
    return manifest, job, source_bytes


def _consume_import_draft(job: dict, final_job_id: str) -> dict:
    metadata = _quote_job_metadata(job)
    if metadata.get("import_consumed_by_job_id") is not None:
        raise RuntimeError("Import source already consumed")
    consumed_metadata = {
        **metadata,
        "import_consumed_by_job_id": final_job_id,
        "import_consumed_at": _iso(datetime.now(timezone.utc)),
    }
    updated = db_update_quote_job(
        job["id"],
        {
            "status": "failed",
            "metadata": consumed_metadata,
            "error_message": f"import_consumed:{final_job_id}",
        },
        expected_status="draft",
    )
    if not updated:
        raise RuntimeError("Import source changed before consumption")
    return updated


def _restore_consumed_import_draft(import_job_id: str, final_job_id: str) -> bool:
    try:
        current = db_get_quote_job(import_job_id)
        metadata = _quote_job_metadata(current or {})
        if (
            not current
            or current.get("status") != "failed"
            or metadata.get("import_consumed_by_job_id") != final_job_id
        ):
            return False
        metadata.pop("import_consumed_by_job_id", None)
        metadata.pop("import_consumed_at", None)
        return bool(db_update_quote_job(
            import_job_id,
            {"status": "draft", "metadata": metadata, "error_message": None},
            expected_status="failed",
        ))
    except Exception:
        return False


def _consumer_import_source_id(job: dict) -> str | None:
    imported = _quote_job_metadata(job).get("import_source")
    if imported is None:
        return None
    if not isinstance(imported, dict):
        raise RuntimeError("Consumidor con fuente importada invalida")
    raw_import_id = imported.get("import_id")
    try:
        import_id = str(uuid.UUID(str(raw_import_id or "")))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("Consumidor con fuente importada invalida") from None
    if import_id != raw_import_id:
        raise RuntimeError("Consumidor con fuente importada invalida")
    return import_id


def _import_source_references_consumer(
    import_job_id: str,
    final_job_id: str,
    usuario_id: int,
) -> bool:
    source = db_get_quote_job(import_job_id)
    if not source:
        return False
    if int(source.get("usuario_id") or 0) != int(usuario_id):
        raise RuntimeError("Fuente importada fuera del usuario")
    return _quote_job_metadata(source).get("import_consumed_by_job_id") == final_job_id


def _preserve_failed_import_consumer(
    job_id: str,
    release_reservations=None,
    *,
    primary_failure: str | None = None,
) -> None:
    failed = None
    error_message = "import_source_restore_pending"
    if primary_failure:
        error_message = f"{error_message}|{primary_failure}"
    updates = {
        "status": "failed",
        "error_message": error_message,
    }
    for expected_status in ("draft", "queued"):
        try:
            failed = db_update_quote_job(
                job_id,
                updates,
                expected_status=expected_status,
            )
        except Exception:
            failed = None
        if failed:
            break
    if not failed:
        try:
            current = db_get_quote_job(job_id)
        except Exception:
            current = None
        if not current or current.get("status") not in {"failed", "completed", "processing"}:
            print(json.dumps({
                "event": "import_consumer_preservation_pending",
                "job_id": job_id,
            }, separators=(",", ":")))
            return
        failed = current
    if failed.get("status") != "failed":
        return
    if release_reservations is None:
        return
    try:
        release_reservations(job_id)
    except Exception:
        print(json.dumps({
            "event": "import_consumer_reservation_release_pending",
            "job_id": job_id,
        }, separators=(",", ":")))


def _archive_completed_import_source(import_job_id: str, final_job: dict) -> None:
    final_job_id = str(final_job.get("id") or "")
    source = db_get_quote_job(import_job_id)
    if not source:
        return
    if int(source.get("usuario_id") or 0) != int(final_job.get("usuario_id") or 0):
        raise RuntimeError("Fuente importada fuera del usuario")
    metadata = _quote_job_metadata(source)
    if metadata.get("import_consumed_by_job_id") != final_job_id:
        return
    if source.get("input_path") is not None or not metadata.get("import_consumed_cleanup_at"):
        raise HTTPException(
            status_code=409,
            detail="La fuente importada aun esta terminando su limpieza",
        )
    metadata.pop("import_consumed_by_job_id", None)
    metadata.pop("import_consumed_at", None)
    metadata["import_source_archived"] = True
    metadata["import_consumer_deleted_at"] = _iso(datetime.now(timezone.utc))
    updated = db_update_quote_job(
        import_job_id,
        {
            "status": "failed",
            "metadata": metadata,
            "error_message": "import_source_archived",
        },
        expected_status="failed",
    )
    if updated:
        return
    if _import_source_references_consumer(
        import_job_id,
        final_job_id,
        int(final_job.get("usuario_id") or 0),
    ):
        raise RuntimeError("No se pudo archivar la fuente importada")


def _prepare_import_consumer_delete(job: dict) -> dict:
    import_job_id = _consumer_import_source_id(job)
    if import_job_id is None:
        return job
    job_id = str(job.get("id") or "")
    usuario_id = int(job.get("usuario_id") or 0)
    status = str(job.get("status") or "")
    if status == "processing":
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar una cotizacion importada en proceso",
        )
    if status in {"draft", "queued"}:
        cancelled = db_update_quote_job(
            job_id,
            {
                "status": "failed",
                "error_message": "import_consumer_cancelled",
            },
            expected_status=status,
        )
        if not cancelled:
            raise HTTPException(
                status_code=409,
                detail="La cotizacion cambio de estado antes de eliminarse",
            )
        job = cancelled
        status = "failed"
    if status == "completed":
        _archive_completed_import_source(import_job_id, job)
        return job
    if status != "failed":
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar esta cotizacion importada",
        )
    try:
        restored = _restore_consumed_import_draft(import_job_id, job_id)
    except Exception:
        restored = False
    if not restored and _import_source_references_consumer(
        import_job_id, job_id, usuario_id
    ):
        raise RuntimeError("No se pudo restaurar la fuente importada")
    return job


def _import_metadata(manifest: dict, imported_source: dict, quote_currency: str) -> dict:
    originals = {item["source_row"]: item for item in manifest["items"]}
    edited_fields: set[str] = set()
    rates: dict[tuple[str, str], dict] = {}
    for line in imported_source["items"]:
        original = originals[line["source_row"]]
        for field in ("name", "description", "dimension"):
            if line[field] != original[field]:
                edited_fields.add(field)
        if line["quantity"] != original["quantity"]:
            edited_fields.add("quantity")
        if Decimal(line["original_unit_price"]) != Decimal(original["unit_price"]):
            edited_fields.add("unit_price")
        if line["provider"] != manifest["provider"]:
            edited_fields.add("provider")
        rate_key = (line["original_currency"], line["frozen_exchange_rate"])
        rates[rate_key] = {
            "source_currency": line["original_currency"],
            "quote_currency": quote_currency,
            "exchange_rate": line["frozen_exchange_rate"],
        }
    return {
        "import_source": {
            "import_id": imported_source["import_id"],
            "original_filename": imported_source["original_filename"],
            "source_hash": imported_source["source_hash"],
        },
        "import_item_count": len(imported_source["items"]),
        "import_source_currencies": sorted({line["original_currency"] for line in imported_source["items"]}),
        "import_edited_fields": sorted(edited_fields),
        "import_rate_summary": [rates[key] for key in sorted(rates)],
    }


def _enqueue_mixed_payload(
    *,
    current_user: dict,
    cart_payload: dict,
    template: str,
    metadata: dict,
    import_job: dict | None,
    import_source_bytes: bytes | None,
) -> dict:
    """Ejecuta una sola transaccion compensable para cotizaciones mixtas."""

    job_id = str(uuid.uuid4())
    input_path = f"users/{current_user['id']}/jobs/{job_id}/input.json"
    source_copy_path = (
        f"users/{current_user['id']}/jobs/{job_id}/import-source.xlsx"
    )
    job_created = False
    import_consumed = False
    additional_cleanup_paths: list[str] = []
    reservation_groups = build_mixed_reservation_groups(cart_payload)
    release_reservations = db_release_mixed_cart if reservation_groups else None
    stage = "create_job"
    try:
        db_create_quote_job(
            current_user["id"],
            template,
            metadata,
            input_path,
            job_id=job_id,
        )
        job_created = True
        if import_source_bytes is not None:
            stage = "copy_import_source"
            additional_cleanup_paths.append(source_copy_path)
            _storage_upload_bytes(
                source_copy_path,
                import_source_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            copied_source = _storage_download_bytes(source_copy_path)
            imported_source = cart_payload.get("imported_source")
            expected_hash = (
                imported_source.get("source_hash")
                if isinstance(imported_source, dict)
                else None
            )
            if (
                not isinstance(copied_source, bytes)
                or not isinstance(expected_hash, str)
                or not hmac.compare_digest(
                    hashlib.sha256(copied_source).hexdigest(),
                    expected_hash,
                )
            ):
                raise RuntimeError("Import source copy verification failed")
            imported_source["source_path"] = source_copy_path
            metadata["import_source_path"] = source_copy_path
            validate_mixed_catalog_payload(cart_payload)
        snapshot = []
        if reservation_groups:
            stage = "reserve"
            snapshot = db_reserve_mixed_cart(
                current_user["id"],
                job_id,
                reservation_groups,
            )
        stage = "serialize"
        _apply_mixed_reservation_snapshot(cart_payload, snapshot)
        validate_mixed_catalog_payload(cart_payload)
        content = json.dumps(
            cart_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        validate_quote_size(
            section_counts=[
                len(section["line_ids"]) for section in cart_payload["sections"]
            ],
            encoded_bytes=len(content),
        )
        stage = "upload_input"
        _storage_upload_bytes(input_path, content, "application/json")
        if import_job is not None:
            stage = "consume_import"
            _consume_import_draft(import_job, job_id)
            import_consumed = True
        stage = "queue"
        return db_queue_mixed_quote_job(job_id, metadata)
    except Exception as exc:
        primary_failure = f"enqueue:{stage}:{type(exc).__name__}"
        print(json.dumps({
            "event": "mixed_quote_enqueue_failed",
            "job_id": job_id,
            "stage": stage,
            "error_type": type(exc).__name__,
        }, separators=(",", ":")))
        import_restored = not import_consumed
        if import_consumed:
            try:
                import_restored = bool(
                    _restore_consumed_import_draft(import_job["id"], job_id)
                )
            except Exception:
                import_restored = False
            if not import_restored:
                try:
                    import_restored = not _import_source_references_consumer(
                        import_job["id"],
                        job_id,
                        current_user["id"],
                    )
                except Exception:
                    import_restored = False
        if job_created:
            if import_consumed and not import_restored:
                _preserve_failed_import_consumer(
                    job_id,
                    release_reservations,
                    primary_failure=primary_failure,
                )
            else:
                _cleanup_failed_catalog_quote(
                    job_id,
                    input_path,
                    release_reservations,
                    additional_storage_paths=additional_cleanup_paths,
                    primary_failure=primary_failure,
                )
        raise


@app.post("/catalogs/mixed-quote")
async def mixed_catalog_quote(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    try:
        _require_active_subscription(current_user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error leyendo suscripcion") from exc
    body = await _read_mixed_quote_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Solicitud mixta invalida")
    unexpected = set(body) - MIXED_QUOTE_BODY_FIELDS
    if unexpected:
        raise HTTPException(status_code=400, detail=f"Campo de cotizacion no permitido: {min(unexpected)}")
    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="Items mixtos debe ser una lista")
    try:
        validate_quote_size(section_counts=[len(raw_items)], encoded_bytes=0)
        catalog_items, imported_items = _split_mixed_quote_items(raw_items)
        preflight_items = preflight_mixed_catalog_items(catalog_items) if catalog_items else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    import_manifest = None
    import_job = None
    import_source_bytes = None
    if imported_items:
        import_manifest, import_job, import_source_bytes = _validated_import_source(
            current_user["id"], imported_items
        )

    requested_catalogs = {
        str(row.get("catalog") or "").strip().lower()
        for row in preflight_items
    }
    if not requested_catalogs <= set(MIXED_CATALOG_ORDER):
        raise HTTPException(status_code=400, detail="Catalogo mixto no soportado")

    try:
        catalogs = {}
        if "tarkett" in requested_catalogs:
            catalogs["tarkett"] = _load_tarkett_catalog_cached()
        if "offiho" in requested_catalogs:
            catalogs["offiho"] = _load_offiho_catalog_cached()
        for supplier in sorted(requested_catalogs - {"tarkett", "offiho"}):
            _require_enabled_catalog_supplier(supplier)
            catalogs[supplier] = _load_supplier_catalog_cached(supplier)
        rate_rows = db_list_exchange_rates()
    except HTTPException:
        raise
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Catalogos mixtos no disponibles") from exc

    try:
        cart_payload = build_mixed_catalog_cart_payload(
            preflight_items,
            catalogs=catalogs,
            rate_rows=rate_rows,
            quote_currency=str(body.get("quote_currency") or "MXN"),
            commercial_discount_percent=body.get("descuento", "40"),
            presentation_sections=body.get("sections"),
            imported_source={
                "manifest": import_manifest,
                "items": imported_items,
                "source_currency": None,
            } if imported_items else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    metadata = _validate_metadata({
        **body,
        "image_provider": body.get("image_provider") or "pillow",
    })
    metadata.update({
        "source_type": "mixed_catalog_cart",
        "original_filename": "mixed-catalog-cart.json",
        "input_extension": ".json",
        "storage_provider": _storage_provider_name(),
        "input_storage_provider": _storage_provider_name(),
        "mixed_item_count": cart_payload["item_count"],
        "mixed_section_count": len(cart_payload["sections"]),
        "catalog_item_counts": {
            group["catalog"]: len(group["items"]) for group in cart_payload["groups"]
        },
        "catalog_source_hashes": {
            group["catalog"]: group["catalog_source_hash"] for group in cart_payload["groups"]
        },
        "quote_currency": cart_payload["quote_currency"],
        "rate_summary": cart_payload["rate_summary"],
        "auto_electrification_rate": cart_payload["auto_electrification_rate"],
        "estimated_duration_seconds": 120,
    })
    if import_manifest is not None:
        metadata.update(_import_metadata(
            import_manifest, cart_payload["imported_source"], cart_payload["quote_currency"]
        ))

    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]

    template = _canonical_template_id(body.get("template"))
    _enforce_active_quote_limit(
        current_user["id"],
        exclude_job_id=import_job["id"] if import_job is not None else None,
    )

    try:
        updated = _enqueue_mixed_payload(
            current_user=current_user,
            cart_payload=cart_payload,
            template=template,
            metadata=metadata,
            import_job=import_job,
            import_source_bytes=import_source_bytes,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="No fue posible crear cotizacion mixta"
        ) from exc

    _wake_worker()
    return {"mensaje": "Cotizacion mixta en cola", "job": updated}


@app.post("/catalogs/{supplier}/quote")
async def supplier_quote(
    supplier: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    supplier = _require_enabled_catalog_supplier(supplier)
    try:
        _require_active_subscription(current_user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error preparando catalogo de proveedor") from exc

    body = await _read_supplier_quote_body(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Solicitud de proveedor invalida")

    try:
        catalog = load_supplier_catalog_data(
            _load_supplier_catalog_cached(supplier),
            expected_supplier=supplier,
        )
        rate_rows = db_list_exchange_rates()
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Error preparando catalogo de proveedor") from exc

    raw_items = body.get("items") or []
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="Items de proveedor debe ser una lista")
    try:
        cart_payload = build_supplier_cart_payload(
            raw_items,
            catalog,
            str(body.get("quote_currency") or "USD"),
            rate_rows,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        reservation_lines = _catalog_reservation_request_lines(cart_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    metadata = _validate_metadata({**body, "image_provider": body.get("image_provider") or "pillow"})
    frozen_rate = {
        key: cart_payload[key]
        for key in (
            "base_currency",
            "quote_currency",
            "exchange_rate",
            "rate_source",
            "rate_effective_date",
            "rate_retrieved_at",
        )
    }
    metadata.update(
        {
            "source_type": "supplier_cart",
            "supplier": supplier,
            "original_filename": f"{supplier}-cart.json",
            "input_extension": ".json",
            "storage_provider": _storage_provider_name(),
            "input_storage_provider": _storage_provider_name(),
            "catalog_source_hash": cart_payload["catalog_source_hash"],
            "supplier_item_count": len(cart_payload["items"]),
            "estimated_duration_seconds": 120,
            **frozen_rate,
        }
    )
    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]

    template = _canonical_template_id(body.get("template"))
    _enforce_active_quote_limit(current_user["id"])
    job_id = str(uuid.uuid4())
    input_path = f"users/{current_user['id']}/jobs/{job_id}/input.json"
    try:
        db_create_quote_job(current_user["id"], template, metadata, input_path, job_id=job_id)
        if reservation_lines:
            reservation_snapshot = db_reserve_catalog_items(
                current_user["id"],
                job_id,
                supplier,
                reservation_lines,
            )
            _apply_catalog_reservation_snapshot(cart_payload, reservation_snapshot)
        content = json.dumps(cart_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        validate_quote_size(
            section_counts=[len(cart_payload["items"])],
            encoded_bytes=len(content),
        )
        _storage_upload_bytes(input_path, content, "application/json")
        updated = _require_queued_quote_job(
            db_update_quote_job(
                job_id,
                {"status": "queued", "metadata": metadata, "error_message": None},
            )
        )
    except Exception as exc:
        _cleanup_failed_catalog_quote(job_id, input_path, db_release_catalog_reservations)
        raise HTTPException(status_code=503, detail="No fue posible crear cotizacion de proveedor") from exc

    _wake_worker()
    try:
        _enforce_quote_history_limit(current_user["id"])
    except RuntimeError:
        pass
    return {"mensaje": f"Cotizacion {CATALOG_SUPPLIER_LABELS[supplier]} en cola", "job": updated}


@app.get("/admin/catalog-sync-runs")
def admin_catalog_sync_runs(user: dict = Depends(require_admin)):
    try:
        return {"runs": db_list_catalog_sync_runs()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error leyendo sincronizaciones") from exc


@app.post("/admin/catalog-sync/{supplier}")
def admin_catalog_sync(supplier: str, user: dict = Depends(require_admin)):
    supplier = _require_enabled_catalog_supplier(supplier)
    try:
        run = db_create_catalog_sync_run(supplier, int(user["id"]), "manual")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="No fue posible solicitar sincronizacion") from exc
    _wake_worker()
    return {"mensaje": "Sincronizacion solicitada", "run": run}


@app.get("/admin/catalog-sync-runs/{run_id}")
def admin_catalog_sync_run(run_id: str, user: dict = Depends(require_admin)):
    try:
        run = db_get_catalog_sync_run(run_id)
        if run and run.get("status") == "awaiting_approval" and run.get("candidate_version_id"):
            run = {**run, "diff": _catalog_run_detailed_diff(run)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error leyendo sincronizacion") from exc
    if not run:
        raise HTTPException(status_code=404, detail="Sincronizacion no encontrada")
    return {"run": run}


def _catalog_review_target(run_id: str) -> tuple[dict, str]:
    run = db_get_catalog_sync_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Sincronizacion no encontrada")
    candidate_id = str(run.get("candidate_version_id") or "").strip()
    if run.get("status") != "awaiting_approval" or not candidate_id:
        raise HTTPException(status_code=409, detail="Sincronizacion no espera aprobacion")
    return run, candidate_id


def _catalog_review_note(body: dict, required: bool = True) -> str:
    note = str((body or {}).get("review_note") or "").strip()
    if required and not note:
        raise HTTPException(status_code=400, detail="Nota de revision requerida")
    return note[:2000]


@app.post("/admin/catalog-sync-runs/{run_id}/approve")
def admin_catalog_approve(run_id: str, body: dict, user: dict = Depends(require_admin)):
    try:
        _run, candidate_id = _catalog_review_target(run_id)
        result = db_publish_catalog_snapshot(candidate_id, int(user["id"]), _catalog_review_note(body, required=False))
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="No fue posible publicar candidato") from exc
    _SUPPLIER_CATALOG_CACHE.clear()
    return {"mensaje": "Catalogo publicado", "snapshot": result}


@app.post("/admin/catalog-sync-runs/{run_id}/reject")
def admin_catalog_reject(run_id: str, body: dict, user: dict = Depends(require_admin)):
    try:
        _run, candidate_id = _catalog_review_target(run_id)
        result = db_reject_catalog_snapshot(candidate_id, int(user["id"]), _catalog_review_note(body))
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="No fue posible rechazar candidato") from exc
    return {"mensaje": "Catalogo rechazado", "snapshot": result}


@app.post("/admin/catalog-sync-runs/{run_id}/images")
async def admin_catalog_image(
    run_id: str,
    item_index: int = Form(...),
    file: UploadFile = File(...),
    image_kind: str = Form("official"),
    image_label: str = Form(""),
    image_references: str = Form("[]"),
    user: dict = Depends(require_admin),
):
    if item_index < 0:
        raise HTTPException(status_code=400, detail="Indice de item invalido")
    kind, label, references = _catalog_image_metadata(image_kind, image_label, image_references)
    content = await file.read(CATALOG_ASSET_MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Imagen vacia")
    if len(content) > CATALOG_ASSET_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Imagen mayor a 8 MB")
    content = _normalize_catalog_image(content, str(file.filename or ""), str(file.content_type or ""))
    try:
        _run, candidate_id = _catalog_review_target(run_id)
        object_name = f"{hashlib.sha256(content).hexdigest()}.png"
        _upload_catalog_asset(object_name, content, "image/png")
        new_candidate_id = db_clone_catalog_candidate_with_image_metadata(
            candidate_id,
            int(user["id"]),
            object_name,
            ["items", str(item_index)],
            kind,
            label,
            references,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="No fue posible asociar imagen") from exc
    return {
        "mensaje": "Imagen aprobada",
        "object_name": object_name,
        "candidate_id": new_candidate_id,
    }

@app.get("/tarkett/catalog")
def tarkett_catalog(current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        return _tarkett_catalog_response(current_user["id"])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error leyendo catalogo Tarkett") from exc


@app.post("/tarkett/quote")
def tarkett_quote(body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        catalog = _load_tarkett_catalog_cached()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Error preparando catalogo Tarkett") from exc

    raw_items = body.get("items") or []
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="Items Tarkett debe ser una lista")

    try:
        cart_payload = build_tarkett_cart_payload(raw_items, catalog=catalog)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    metadata_body = {**body, "image_provider": body.get("image_provider") or "pillow"}
    metadata = _validate_metadata(metadata_body)
    metadata.update(
        {
            "source_type": "tarkett_cart",
            "original_filename": "tarkett-cart.json",
            "input_extension": ".json",
            "storage_provider": _storage_provider_name(),
            "input_storage_provider": _storage_provider_name(),
            "catalog_source_hash": cart_payload["catalog_source_hash"],
            "tarkett_item_count": len(cart_payload["items"]),
            "estimated_duration_seconds": 120,
        }
    )
    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]

    template = _canonical_template_id(body.get("template"))

    _enforce_active_quote_limit(current_user["id"])
    job_id = str(uuid.uuid4())
    input_path = f"users/{current_user['id']}/jobs/{job_id}/input.json"
    content = json.dumps(cart_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        _storage_upload_bytes(input_path, content, "application/json")
        job = db_create_quote_job(current_user["id"], template, metadata, input_path, job_id=job_id)
        db_create_tarkett_reservations(current_user["id"], job_id, cart_payload["items"])
        updated = _require_queued_quote_job(
            db_update_quote_job(
                job_id,
                {
                    "status": "queued",
                    "metadata": metadata,
                    "error_message": None,
                },
            )
        )
    except Exception as exc:
        _cleanup_failed_catalog_quote(job_id, input_path, db_release_tarkett_reservations)
        raise HTTPException(status_code=503, detail="No fue posible crear cotizacion Tarkett") from exc

    _wake_worker()
    try:
        _enforce_quote_history_limit(current_user["id"])
    except RuntimeError:
        pass
    return {"mensaje": "Cotizacion Tarkett en cola", "job": updated}


@app.get("/offiho/catalog")
def offiho_catalog(current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        return _offiho_catalog_response(current_user["id"])
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Error leyendo catalogo Offiho")


@app.post("/offiho/quote")
def offiho_quote(body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        catalog = _load_offiho_catalog_cached()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Error preparando catalogo Offiho")

    raw_items = body.get("items") or []
    if not isinstance(raw_items, list):
        raise HTTPException(status_code=400, detail="Items Offiho debe ser una lista")

    try:
        cart_payload = build_offiho_cart_payload(raw_items, catalog=catalog)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    metadata_body = {**body, "image_provider": body.get("image_provider") or "pillow"}
    metadata = _validate_metadata(metadata_body)
    metadata.update(
        {
            "source_type": "offiho_cart",
            "original_filename": "offiho-cart.json",
            "input_extension": ".json",
            "storage_provider": _storage_provider_name(),
            "input_storage_provider": _storage_provider_name(),
            "catalog_source_hash": cart_payload["catalog_source_hash"],
            "offiho_item_count": len(cart_payload["items"]),
            "estimated_duration_seconds": 120,
        }
    )
    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]

    template = _canonical_template_id(body.get("template"))

    _enforce_active_quote_limit(current_user["id"])
    job_id = str(uuid.uuid4())
    input_path = f"users/{current_user['id']}/jobs/{job_id}/input.json"
    content = json.dumps(cart_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        _storage_upload_bytes(input_path, content, "application/json")
        job = db_create_quote_job(current_user["id"], template, metadata, input_path, job_id=job_id)
        db_create_offiho_reservations(current_user["id"], job_id, cart_payload["items"])
        updated = _require_queued_quote_job(
            db_update_quote_job(
                job_id,
                {
                    "status": "queued",
                    "metadata": metadata,
                    "error_message": None,
                },
            )
        )
    except Exception as exc:
        _cleanup_failed_catalog_quote(job_id, input_path, db_release_offiho_reservations)
        raise HTTPException(status_code=503, detail="No fue posible crear cotizacion Offiho") from exc

    _wake_worker()
    try:
        _enforce_quote_history_limit(current_user["id"])
    except RuntimeError:
        pass
    return {"mensaje": "Cotizacion Offiho en cola", "job": updated}


def _normalize_import_preview_image(
    content: object,
    image_type: object,
) -> tuple[bytes, str, str] | None:
    if not isinstance(content, bytes) or not content or len(content) > IMPORT_PREVIEW_IMAGE_MAX_BYTES:
        return None
    declared_mime = {
        ".png": "image/png", "image/png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", "image/jpeg": "image/jpeg",
        ".webp": "image/webp", "image/webp": "image/webp",
    }.get(str(image_type or "").strip().lower())
    detected = _catalog_image_type(content)
    if declared_mime is None or detected is None or detected[1] != declared_mime:
        return None
    extension, _detected_mime = detected
    if not _catalog_image_has_exact_end(content, extension):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as probe:
                width, height = probe.size
                if (
                    probe.format not in {"PNG", "JPEG", "WEBP"}
                    or width < 1 or height < 1
                    or width > IMPORT_PREVIEW_IMAGE_MAX_WIDTH
                    or height > IMPORT_PREVIEW_IMAGE_MAX_HEIGHT
                    or width * height > IMPORT_PREVIEW_IMAGE_MAX_PIXELS
                    or getattr(probe, "is_animated", False)
                    or getattr(probe, "n_frames", 1) != 1
                ):
                    return None
                probe.verify()
            with Image.open(io.BytesIO(content)) as decoded:
                decoded.load()
                if getattr(decoded, "is_animated", False) or getattr(decoded, "n_frames", 1) != 1:
                    return None
                has_alpha = "A" in decoded.getbands() or "transparency" in decoded.info
                thumbnail = decoded.convert("RGBA" if has_alpha else "RGB")
                thumbnail.thumbnail(
                    (IMPORT_PREVIEW_THUMBNAIL_MAX_SIDE, IMPORT_PREVIEW_THUMBNAIL_MAX_SIDE),
                    Image.Resampling.LANCZOS,
                )
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None
    output = io.BytesIO()
    thumbnail.save(output, format="PNG", optimize=False, compress_level=9)
    normalized = output.getvalue()
    if not normalized or len(normalized) > IMPORT_PREVIEW_IMAGE_MAX_BYTES:
        return None
    return normalized, ".png", "image/png"


def _store_import_preview(job: dict, manifest: dict, image_map: dict[int, tuple[bytes, str]]) -> tuple[str, dict[int, str]]:
    prefix = f"users/{job['usuario_id']}/jobs/{job['id']}/preview/{manifest['source_hash'][:16]}"
    manifest_path = f"{prefix}/manifest.json"
    image_paths: dict[int, str] = {}
    uploaded_paths: list[str] = []
    try:
        for row, (content, image_type) in image_map.items():
            normalized = _normalize_import_preview_image(content, image_type)
            if normalized is not None:
                preview_bytes, suffix, content_type = normalized
                image_path = f"{prefix}/row-{row}{suffix}"
                _storage_upload_bytes(image_path, preview_bytes, content_type)
                uploaded_paths.append(image_path)
                image_paths[row] = image_path
        stored_manifest = {
            **manifest,
            "preview_image_paths": {str(row): path for row, path in image_paths.items()},
        }
        _storage_upload_bytes(
            manifest_path,
            json.dumps(stored_manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json",
        )
        uploaded_paths.append(manifest_path)
    except Exception:
        if uploaded_paths:
            try:
                _delete_storage_paths(uploaded_paths)
            except Exception:
                pass
        raise
    return manifest_path, image_paths


def _preview_response(manifest: dict, image_paths: dict[int, str]) -> dict:
    items = []
    for item in manifest["items"]:
        image_path = image_paths.get(item["source_row"])
        items.append({**item, "image_url": _create_signed_download(image_path) if image_path else ""})
    return {**manifest, "items": items}


@app.post("/cotizaciones/{job_id}/import-preview")
def quotation_import_preview(job_id: str, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        job = _quote_job_for_user(job_id, current_user["id"])
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Servicio de cotizaciones no disponible") from None

    template = _canonical_template_id(job.get("template"))
    input_path = str(job.get("input_path") or "").strip().lstrip("/")
    metadata = _quote_job_metadata(job)
    filename = metadata.get("original_filename")
    if not input_path or not isinstance(filename, str) or not filename.strip():
        raise HTTPException(status_code=400, detail="La quotation no tiene archivo de entrada")
    if job.get("status") != "draft" or Path(input_path).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=409, detail="La quotation no esta disponible para importar")

    try:
        source_bytes = _storage_download_bytes(input_path)
        manifest, image_map = build_import_manifest(source_bytes, job_id, filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="La quotation no se pudo importar") from None
    except RuntimeError:
        raise HTTPException(status_code=503, detail="No se pudo leer la quotation") from None

    old_preview_paths = {
        str(metadata.get("import_manifest_path") or "").strip().lstrip("/"),
        *(
            str(path or "").strip().lstrip("/")
            for path in (metadata.get("import_preview_paths") or {}).values()
        ),
    }
    old_preview_paths.discard("")
    try:
        manifest_path, image_paths = _store_import_preview(job, manifest, image_map)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="No se pudo guardar la previsualizacion") from None
    new_preview_paths = {manifest_path, *image_paths.values()}
    try:
        updated = db_update_quote_job(
            job_id,
            {
                "template": template,
                "metadata": {
                    **metadata,
                    "import_manifest_path": manifest_path,
                    "import_preview_paths": {str(row): path for row, path in image_paths.items()},
                    "import_source_hash": manifest["source_hash"],
                    "import_item_count": len(manifest["items"]),
                }
            },
            expected_status="draft",
        )
    except RuntimeError:
        try:
            _delete_storage_paths(sorted(new_preview_paths - old_preview_paths))
        except RuntimeError:
            pass
        raise HTTPException(status_code=503, detail="No se pudo guardar la previsualizacion") from None
    if not updated:
        try:
            _delete_storage_paths(sorted(new_preview_paths - old_preview_paths))
        except RuntimeError:
            pass
        raise HTTPException(status_code=409, detail="La quotation ya no esta disponible para importar")
    stale_preview_paths = old_preview_paths - new_preview_paths
    if stale_preview_paths:
        try:
            _delete_storage_paths(sorted(stale_preview_paths))
        except RuntimeError:
            print(json.dumps({"event": "import_preview_cleanup_pending", "job_id": job_id}, separators=(",", ":")))

    try:
        return _preview_response(manifest, image_paths)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="No se pudo preparar la previsualizacion") from None


@app.post("/cotizaciones/init-upload")
def cotizaciones_init_upload(body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    filename = str(body.get("filename", "input.xlsx")).strip()
    file_size = int(body.get("size", 0) or 0)
    template = _canonical_template_id(body.get("template"))

    input_extension = _quote_input_extension(filename)
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="Tamano de archivo requerido")
    if file_size > MAX_QUOTE_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Archivo mayor a {MAX_QUOTE_UPLOAD_MB} MB")

    _enforce_active_quote_limit(current_user["id"])
    job_id = str(uuid.uuid4())
    input_path = f"users/{current_user['id']}/jobs/{job_id}/input{input_extension}"
    metadata = {
        "original_filename": filename,
        "file_size": file_size,
        "input_extension": input_extension,
        "storage_provider": _storage_provider_name(),
        "input_storage_provider": _storage_provider_name(),
    }


    try:
        upload = _create_signed_upload(input_path)
        job = db_create_quote_job(current_user["id"], template, metadata, input_path, job_id=job_id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error preparando carga: {e}")
    try:
        _enforce_quote_history_limit(current_user["id"])
    except RuntimeError:
        pass

    token = upload.get("token") or upload.get("signedToken")
    signed_upload_url = upload.get("signed_upload_url")
    if not signed_upload_url and token and not DEV_MODE:
        signed_upload_url = _signed_upload_url(input_path, token)
    if not signed_upload_url and not token and not DEV_MODE:
        raise HTTPException(status_code=500, detail="El storage no devolvio URL de carga")

    return {
        "job_id": job["id"],
        "bucket": _storage_bucket_name(),
        "storage_provider": _storage_provider_name(),
        "path": input_path,
        "token": token,
        "signed_upload_url": signed_upload_url if not DEV_MODE else None,
        "upload_url": f"/cotizaciones/{job['id']}/dev-upload" if DEV_MODE else None,
        "max_size_mb": MAX_QUOTE_UPLOAD_MB,
        "allowed_extensions": list(ALLOWED_QUOTE_INPUT_EXTENSIONS),
    }


@app.get("/internal/catalogs/tarkett")
def internal_get_tarkett_catalog(_authorized: bool = Depends(require_worker_secret)):
    snapshot = db_get_supplier_catalog_snapshot("tarkett")
    if not snapshot:
        raise HTTPException(status_code=404, detail="Catalogo Tarkett no disponible")
    return snapshot


@app.put("/internal/catalogs/tarkett")
def internal_put_tarkett_catalog(body: dict, _authorized: bool = Depends(require_worker_secret)):
    payload = body.get("payload") if isinstance(body, dict) else None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload de catalogo requerido")
    try:
        return db_upsert_supplier_catalog_snapshot("tarkett", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/cotizaciones/{job_id}/dev-upload")
async def cotizaciones_dev_upload(job_id: str, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail="No disponible")
    job = _quote_job_for_user(job_id, current_user["id"])
    input_extension = _quote_input_extension(file.filename)
    expected_extension = Path(str(job.get("input_path") or "")).suffix.lower()
    if expected_extension and input_extension != expected_extension:
        raise HTTPException(status_code=400, detail="El archivo no coincide con el tipo de carga inicial")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacio")
    if len(content) > MAX_QUOTE_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Archivo mayor a {MAX_QUOTE_UPLOAD_MB} MB")
    dest = _dev_storage_file(job["input_path"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return {"ok": True, "path": job["input_path"], "size": len(content)}


def _require_retryable_failed_quote(job: dict) -> None:
    if job.get("status") != "failed":
        return
    if _quote_job_metadata(job).get("import_consumed_by_job_id") is not None:
        raise HTTPException(status_code=409, detail="La fuente importada es administrada por su cotizacion final")
    if not job.get("input_path"):
        raise HTTPException(status_code=400, detail="La cotizacion no tiene archivo de entrada")
    if str(job.get("error_message") or "").strip().startswith("cleanup_pending:"):
        raise HTTPException(
            status_code=409,
            detail="La cotizacion requiere limpieza administrativa antes de reintentar",
        )


@app.post("/cotizaciones/{job_id}/submit")
def cotizaciones_submit(job_id: str, body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        job = _quote_job_for_user(job_id, current_user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if job["status"] not in {"draft", "failed"}:
        raise HTTPException(status_code=409, detail="La cotizacion ya fue enviada")
    _require_retryable_failed_quote(job)
    template = _canonical_template_id(body.get("template") or job.get("template"))

    metadata = {**(job.get("metadata") or {}), **_validate_metadata(body)}
    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]
    _enforce_active_quote_limit(current_user["id"], exclude_job_id=job_id)
    try:
        updated = db_update_quote_job(
            job_id,
            {
                "status": "queued",
                "template": template,
                "metadata": metadata,
                "error_message": None,
                "attempt_token": None,
                "lease_expires_at": None,
            },
            expected_status=job["status"],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error actualizando cotizacion: {e}")

    if not updated:
        raise HTTPException(status_code=409, detail="La cotizacion cambio de estado")
    _wake_worker()
    return {"mensaje": "Cotizacion en cola", "job": updated}


@app.post("/cotizaciones/{job_id}/retry")
def cotizaciones_retry(job_id: str, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        job = _quote_job_for_user(job_id, current_user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if job["status"] != "failed":
        raise HTTPException(status_code=409, detail="Solo se pueden reintentar cotizaciones fallidas")
    _require_retryable_failed_quote(job)
    template = _canonical_template_id(job.get("template"))

    _enforce_active_quote_limit(current_user["id"], exclude_job_id=job_id)
    try:
        updated = db_update_quote_job(
            job_id,
            {
                "status": "queued",
                "template": template,
                "error_message": None,
                "output_path": None,
                "completed_at": None,
                "attempt_token": None,
                "lease_expires_at": None,
            },
            expected_status="failed",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error reintentando cotizacion: {e}")

    if not updated:
        raise HTTPException(status_code=409, detail="La cotizacion ya no esta fallida")
    _wake_worker()
    return {"mensaje": "Cotizacion reencolada", "job": updated}


@app.get("/cotizaciones")
def cotizaciones_list(current_user: dict = Depends(get_current_user)):
    try:
        jobs = db_list_quote_jobs(current_user["id"])
        jobs = _enforce_quote_history_limit(current_user["id"], jobs)
        jobs = [
            job for job in jobs
            if _quote_job_metadata(job).get("import_consumed_by_job_id") is None
            and not _quote_job_metadata(job).get("import_source_archived")
        ]
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")
    return {"cotizaciones": jobs}


@app.get("/cotizaciones/{job_id}")
def cotizaciones_get(job_id: str, current_user: dict = Depends(get_current_user)):
    job = _quote_job_for_user(job_id, current_user["id"])
    return {"job": job}


@app.delete("/cotizaciones/{job_id}")
def cotizaciones_delete(job_id: str, current_user: dict = Depends(get_current_user)):
    job = _quote_job_for_user(job_id, current_user["id"])
    try:
        job = _prepare_import_consumer_delete(job)
        _release_and_delete_quote_job(job)
        _delete_quote_storage(job)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error eliminando cotizacion: {e}")
    return {"deleted_id": job_id}


@app.get("/cotizaciones/{job_id}/download")
def cotizaciones_download(job_id: str, current_user: dict = Depends(get_current_user)):
    job = _quote_job_for_user(job_id, current_user["id"])
    if job["status"] != "completed" or not job.get("output_path"):
        raise HTTPException(status_code=409, detail="La cotizacion aun no esta lista")
    filename = _safe_quote_filename(job)
    try:
        signed_url = _create_signed_download(job["output_path"], filename=filename)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error preparando descarga: {e}")
    if not signed_url:
        raise HTTPException(status_code=500, detail="El storage no devolvio URL de descarga")
    try:
        _mark_quote_downloaded(job)
    except RuntimeError:
        print(json.dumps({"event": "quote_download_mark_failed", "job_id": job_id}, separators=(",", ":")))
    return {"download_url": signed_url, "filename": filename, "expires_in": SIGNED_DOWNLOAD_TTL_SECONDS}


@app.get("/cotizaciones/{job_id}/file")
def cotizaciones_file(job_id: str, current_user: dict = Depends(get_current_user)):
    job = _quote_job_for_user(job_id, current_user["id"])
    if job["status"] != "completed" or not job.get("output_path"):
        raise HTTPException(status_code=409, detail="La cotizacion aun no esta lista")

    filename = _safe_quote_filename(job)
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if DEV_MODE:
        source = _dev_storage_file(job["output_path"])
        if not source.exists():
            raise HTTPException(status_code=404, detail="Archivo no encontrado")
        try:
            _mark_quote_downloaded(job)
        except RuntimeError:
            print(json.dumps({"event": "quote_download_mark_failed", "job_id": job_id}, separators=(",", ":")))
        return FileResponse(source, media_type=media_type, filename=filename)

    try:
        content = _storage_download_bytes(job["output_path"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error descargando cotizacion: {e}")
    try:
        _mark_quote_downloaded(job)
    except RuntimeError:
        print(json.dumps({"event": "quote_download_mark_failed", "job_id": job_id}, separators=(",", ":")))
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/dev/storage/{encoded_path:path}")
def dev_storage_download(encoded_path: str):
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail="No disponible")
    object_path = unquote(encoded_path)
    source = _dev_storage_file(object_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return FileResponse(
        source,
        media_type=media_type,
        filename=source.name,
    )


@app.get("/dev/catalog-assets/{object_name}")
def dev_catalog_asset_download(object_name: str):
    if not DEV_MODE or not CATALOG_ASSET_NAME_RE.fullmatch(str(object_name or "")):
        raise HTTPException(status_code=404, detail="No disponible")
    source = DEV_STORE_DIR / CATALOG_ASSET_BUCKET / object_name
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(source.suffix.lower(), "application/octet-stream")
    return FileResponse(
        source,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.post("/generar-cotizacion")
def generar_cotizacion(body: dict, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")

    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido")

    user_id = payload.get("sub")
    try:
        usuario = db_get_usuario_by_id(int(user_id))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if not usuario or not usuario.get("activo"):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    try:
        suscripcion = db_get_suscripcion_by_usuario(usuario["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    now = datetime.now(timezone.utc)

    if not suscripcion:
        raise HTTPException(status_code=403, detail="Sin suscripcion")

    fecha_fin = datetime.fromisoformat(suscripcion["fecha_fin"].replace("Z", "+00:00"))
    if suscripcion["estado"] != "activa" or fecha_fin < now:
        raise HTTPException(status_code=403, detail="Suscripcion no activa")

    return {"autorizado": True, "mensaje": "Puedes generar la cotizacion localmente"}


# ─── VERSION / DOWNLOAD (Auto-Updater) ────────────────────────

# Fallback inline por si Supabase no responde
_CURRENT_VERSION_FALLBACK = {
    "version": "1.5.7",
    "download_url": "https://github.com/JoseLuisMartinezMeza/mobiliti-generador/releases/download/v1.5.7/Mobiliti_Generador_v1.5.7.zip",
    "release_notes": "Release estable v1.5.7 con ejecutable, config.json y version.txt.",
    "force_update": False,
}


def _get_version_from_db():
    """Lee la version activa desde Supabase. Retorna fallback si falla."""
    try:
        rows = _supabase_req(
            "GET",
            "/saas_versiones",
            params={"activa": "eq.true", "select": "version,download_url,release_notes,force_update", "limit": "1", "order": "id.desc"}
        )
        if rows and len(rows) > 0:
            row = rows[0]
            return {
                "version": row.get("version", _CURRENT_VERSION_FALLBACK["version"]),
                "download_url": row.get("download_url", _CURRENT_VERSION_FALLBACK["download_url"]),
                "release_notes": row.get("release_notes", _CURRENT_VERSION_FALLBACK["release_notes"]),
                "force_update": row.get("force_update", False),
            }
    except Exception:
        pass
    return _CURRENT_VERSION_FALLBACK


@app.get("/version")
def version_endpoint():
    return _get_version_from_db()


@app.get("/download/latest")
def download_latest():
    version_data = _get_version_from_db()
    return {"url": version_data["download_url"]}


# ═══════════════════════════════════════════════════════════════
# HANDLER PARA VERCEL
# ═══════════════════════════════════════════════════════════════

handler = Mangum(app)
