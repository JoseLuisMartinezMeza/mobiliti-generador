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
import urllib.request
import urllib.error
from urllib.parse import quote, unquote
from datetime import datetime, timedelta, timezone
from pathlib import Path
from jose import JWTError, jwt
import bcrypt
from fastapi import FastAPI, HTTPException, Header, Depends, Request, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from mangum import Mangum


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
QUOTE_STORAGE_BUCKET = os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files")
QUOTE_STORAGE_PROVIDER = os.environ.get("QUOTE_STORAGE_PROVIDER", "supabase").strip().lower()
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "").strip().rstrip("/")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.environ.get("R2_BUCKET", QUOTE_STORAGE_BUCKET).strip() or QUOTE_STORAGE_BUCKET
R2_REGION = os.environ.get("R2_REGION", "auto").strip() or "auto"
MAX_QUOTE_UPLOAD_MB = int(os.environ.get("MAX_QUOTE_UPLOAD_MB", "25"))
MAX_QUOTE_HISTORY_PER_USER = int(os.environ.get("MAX_QUOTE_HISTORY_PER_USER", "3"))
MAX_ACTIVE_QUOTE_JOBS_PER_USER = max(1, min(20, int(os.environ.get("MAX_ACTIVE_QUOTE_JOBS_PER_USER", "3"))))
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
TARKETT_CATALOG_PATH = os.environ.get("TARKETT_CATALOG_PATH")
TARKETT_CATALOG_DB_ENABLED = _env_bool("TARKETT_CATALOG_DB_ENABLED", bool(os.environ.get("VERCEL")))
TARKETT_CATALOG_DB_TTL_SECONDS = max(30, int(os.environ.get("TARKETT_CATALOG_DB_TTL_SECONDS", "300")))
OFFIHO_CATALOG_PATH = os.environ.get("OFFIHO_CATALOG_PATH")
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
    safe_path = object_path.replace("\\", "/").lstrip("/")
    if ".." in safe_path.split("/"):
        raise RuntimeError("Ruta de storage invalida")
    return DEV_STORE_DIR / "storage" / QUOTE_STORAGE_BUCKET / safe_path


def _dev_save(data: dict):
    DEV_STORE_DIR.mkdir(parents=True, exist_ok=True)
    _dev_db_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _dev_load() -> dict:
    path = _dev_db_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

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
        "tarkett_reservations": [],
        "offiho_reservations": [],
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
        if isinstance(value, datetime):
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


def _storage_download_bytes(path: str) -> bytes:
    """Descarga un objeto privado del proveedor de storage desde backend."""
    if _use_r2_storage():
        try:
            obj = _r2_client().get_object(Bucket=R2_BUCKET, Key=path.strip("/"))
            return obj["Body"].read()
        except Exception as exc:
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
        dest = _dev_storage_file(clean_path)
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
                (id, usuario_id, status, input_path, output_path, template, metadata, error_message, created_at, updated_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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


def db_update_quote_job(job_id: str, updates: dict):
    payload = {**updates, "updated_at": _iso(datetime.now(timezone.utc))}
    if DEV_MODE:
        data = _dev_load()
        for row in data["quote_jobs"]:
            if row["id"] == job_id:
                row.update(payload)
                _dev_save(data)
                return row
        return {}
    if _use_postgres():
        return _pg_update("saas_quote_jobs", "id", job_id, payload)
    rows = _supabase_req("PATCH", f"/saas_quote_jobs?id=eq.{job_id}", json_data=payload)
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


def db_create_tarkett_reservations(usuario_id: int, quote_job_id: str, lines: list[dict]):
    now = _iso(datetime.now(timezone.utc))
    rows = [
        {
            "id": str(uuid.uuid4()),
            "usuario_id": usuario_id,
            "quote_job_id": quote_job_id,
            "product_code": str(line["code"]),
            "quantity": float(line["quantity"]),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        for line in lines
    ]
    if DEV_MODE:
        data = _dev_load()
        data.setdefault("tarkett_reservations", []).extend(rows)
        _dev_save(data)
        return rows
    if _use_postgres():
        created = []
        for row in rows:
            created.extend(
                _pg_write(
                    """
                    INSERT INTO saas_tarkett_reservations
                        (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        row["id"],
                        row["usuario_id"],
                        row["quote_job_id"],
                        row["product_code"],
                        row["quantity"],
                        row["status"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            )
        return created
    return _supabase_req("POST", "/saas_tarkett_reservations", json_data=rows)


def db_release_tarkett_reservations(quote_job_id: str):
    now = _iso(datetime.now(timezone.utc))
    if DEV_MODE:
        data = _dev_load()
        released = []
        for row in data.setdefault("tarkett_reservations", []):
            if str(row.get("quote_job_id")) == str(quote_job_id) and row.get("status") == "active":
                row["status"] = "released"
                row["updated_at"] = now
                released.append(row)
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
    now = _iso(datetime.now(timezone.utc))
    rows = [
        {
            "id": str(uuid.uuid4()),
            "usuario_id": usuario_id,
            "quote_job_id": quote_job_id,
            "product_code": str(line["inventory_key"]),
            "quantity": float(line["quantity"]),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        for line in lines
    ]
    if DEV_MODE:
        data = _dev_load()
        data.setdefault("offiho_reservations", []).extend(rows)
        _dev_save(data)
        return rows
    if _use_postgres():
        created = []
        for row in rows:
            created.extend(
                _pg_write(
                    """
                    INSERT INTO saas_offiho_reservations
                        (id, usuario_id, quote_job_id, product_code, quantity, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        row["id"],
                        row["usuario_id"],
                        row["quote_job_id"],
                        row["product_code"],
                        row["quantity"],
                        row["status"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            )
        return created
    return _supabase_req("POST", "/saas_offiho_reservations", json_data=rows)


def db_release_offiho_reservations(quote_job_id: str):
    now = _iso(datetime.now(timezone.utc))
    if DEV_MODE:
        data = _dev_load()
        released = []
        for row in data.setdefault("offiho_reservations", []):
            if str(row.get("quote_job_id")) == str(quote_job_id) and row.get("status") == "active":
                row["status"] = "released"
                row["updated_at"] = now
                released.append(row)
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


def _cleanup_failed_catalog_quote(job_id: str, input_path: str, release_reservations) -> None:
    operations = (
        lambda: release_reservations(job_id),
        lambda: db_delete_quote_job(job_id),
        lambda: _delete_storage_paths([input_path]),
    )
    for operation in operations:
        try:
            operation()
        except Exception:
            pass


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
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")
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
    return list(dict.fromkeys(paths))


def _delete_storage_paths(paths: list[str]) -> None:
    clean_paths = [str(path or "").strip().lstrip("/") for path in paths if str(path or "").strip()]
    if not clean_paths:
        return
    if DEV_MODE:
        for path in clean_paths:
            _dev_storage_file(path).unlink(missing_ok=True)
        return
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
            _delete_quote_storage(job)
            db_release_tarkett_reservations(job["id"])
            db_release_offiho_reservations(job["id"])
            db_delete_quote_job(job["id"])
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
                _delete_storage_paths([input_path])
                db_update_quote_job(job["id"], {"input_path": None})
                job["input_path"] = None
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


# ─── Health Check ─────────────────────────────────────────────

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

    template = str(body.get("template") or "Formato Cotizacion 2026 GDL (1).xlsx").strip()
    if not template:
        raise HTTPException(status_code=400, detail="Template requerido")

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

    template = str(body.get("template") or "Formato Cotizacion 2026 GDL (1).xlsx").strip()
    if not template:
        raise HTTPException(status_code=400, detail="Template requerido")

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


@app.post("/cotizaciones/init-upload")
def cotizaciones_init_upload(body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    filename = str(body.get("filename", "input.xlsx")).strip()
    file_size = int(body.get("size", 0) or 0)
    template = str(body.get("template", "Formato Cotizacion 2026 GDL (1).xlsx")).strip()

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


@app.post("/cotizaciones/{job_id}/submit")
def cotizaciones_submit(job_id: str, body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
        job = _quote_job_for_user(job_id, current_user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    if job["status"] not in {"draft", "failed"}:
        raise HTTPException(status_code=409, detail="La cotizacion ya fue enviada")

    metadata = {**(job.get("metadata") or {}), **_validate_metadata(body)}
    assigned_quote_number = _next_quote_number_for_user(current_user)
    if assigned_quote_number:
        metadata["cotizacion"] = assigned_quote_number
    elif not metadata.get("cotizacion"):
        metadata["cotizacion"] = metadata["proyecto"]
    template = str(body.get("template") or job.get("template") or "").strip()
    if not template:
        raise HTTPException(status_code=400, detail="Template requerido")

    _enforce_active_quote_limit(current_user["id"], exclude_job_id=job_id)
    try:
        updated = db_update_quote_job(
            job_id,
            {
                "status": "queued",
                "template": template,
                "metadata": metadata,
                "error_message": None,
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error actualizando cotizacion: {e}")

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
    if not job.get("input_path"):
        raise HTTPException(status_code=400, detail="La cotizacion no tiene archivo de entrada")

    _enforce_active_quote_limit(current_user["id"], exclude_job_id=job_id)
    try:
        updated = db_update_quote_job(
            job_id,
            {
                "status": "queued",
                "error_message": None,
                "output_path": None,
                "completed_at": None,
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error reintentando cotizacion: {e}")

    _wake_worker()
    return {"mensaje": "Cotizacion reencolada", "job": updated}


@app.get("/cotizaciones")
def cotizaciones_list(current_user: dict = Depends(get_current_user)):
    try:
        jobs = db_list_quote_jobs(current_user["id"])
        jobs = _enforce_quote_history_limit(current_user["id"], jobs)
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
        _delete_quote_storage(job)
        db_release_tarkett_reservations(job_id)
        db_release_offiho_reservations(job_id)
        db_delete_quote_job(job_id)
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
    return FileResponse(
        source,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=source.name,
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
