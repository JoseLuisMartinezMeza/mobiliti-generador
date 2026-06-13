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

# ═══════════════════════════════════════════════════════════════
# CONFIGURACION
# ═══════════════════════════════════════════════════════════════

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
MOBILITI_REST_SECRET = os.environ.get("MOBILITI_REST_SECRET")
WORKER_WAKE_ENABLED = os.environ.get("WORKER_WAKE_ENABLED", "").lower() in {"1", "true", "yes"}
WORKER_WAKE_URL = os.environ.get("WORKER_WAKE_URL") if WORKER_WAKE_ENABLED else None

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
QUOTE_STORAGE_BUCKET = os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files")
MAX_QUOTE_UPLOAD_MB = int(os.environ.get("MAX_QUOTE_UPLOAD_MB", "25"))
SIGNED_UPLOAD_TTL_SECONDS = int(os.environ.get("SIGNED_UPLOAD_TTL_SECONDS", "3600"))
SIGNED_DOWNLOAD_TTL_SECONDS = int(os.environ.get("SIGNED_DOWNLOAD_TTL_SECONDS", "3600"))
DEV_MODE = os.environ.get("MOBILITI_DEV_MODE", "").lower() in {"1", "true", "yes"}
DEV_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_STORE_DIR = Path(os.environ.get("MOBILITI_DEV_STORE_DIR", DEV_PROJECT_ROOT / ".mobiliti_dev_store")).resolve()
DEV_PUBLIC_BASE_URL = os.environ.get("MOBILITI_DEV_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
DEV_USER_EMAIL = os.environ.get("MOBILITI_DEV_USER_EMAIL", "dev@mobiliti.local")
DEV_USER_PASSWORD = os.environ.get("MOBILITI_DEV_USER_PASSWORD", "dev12345")

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


def _pg_rows(sql: str, params: tuple = ()) -> list[dict]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
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
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, adapted)
            row = cur.fetchone()
        conn.commit()
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
        raise RuntimeError(f"Supabase HTTP {e.code}: {body}") from e
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
        raise RuntimeError(f"Supabase Storage HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase Storage connection error: {e.reason}") from e


def _storage_download_bytes(path: str) -> bytes:
    """Descarga un objeto privado de Supabase Storage desde backend."""
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
        raise RuntimeError(f"Supabase Storage HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Supabase Storage connection error: {e.reason}") from e


# ═══════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════

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


def _safe_quote_filename(job: dict) -> str:
    metadata = job.get("metadata") or {}
    raw = metadata.get("cotizacion") or metadata.get("proyecto") or job["id"]
    name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(raw).strip())
    name = "_".join(part for part in name.split("_") if part)[:80] or job["id"]
    return f"Cotizacion_{name}.xlsx"


def _validate_metadata(body: dict) -> dict:
    fields = {
        "cotizacion": "Numero de cotizacion",
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
    description_language = str(body.get("description_language", "es")).strip().lower()
    if description_language not in {"es", "en"}:
        raise HTTPException(status_code=400, detail="Idioma de descripcion invalido")
    clean["description_language"] = description_language
    image_provider = str(body.get("image_provider", body.get("proveedor_imagen", "pillow"))).strip().lower()
    image_provider = {
        "local": "pillow",
        "gratis": "pillow",
        "free": "pillow",
        "ia": "dezgo",
        "ai": "dezgo",
        "flux": "dezgo",
        "flux2": "dezgo",
        "flux_2": "dezgo",
    }.get(image_provider, image_provider)
    if image_provider not in {"pillow", "dezgo"}:
        raise HTTPException(status_code=400, detail="Proveedor de imagen invalido")
    clean["image_provider"] = image_provider
    return clean


def _create_signed_upload(path: str):
    if DEV_MODE:
        return {"token": "dev-upload-token"}
    encoded_path = quote(path, safe="/")
    return _storage_req(
        "POST",
        f"/object/upload/sign/{QUOTE_STORAGE_BUCKET}/{encoded_path}",
        json_data={"upsert": True, "expiresIn": SIGNED_UPLOAD_TTL_SECONDS},
    )


def _signed_upload_url(path: str, token: str) -> str:
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


def _create_signed_download(path: str):
    if DEV_MODE:
        return f"{DEV_PUBLIC_BASE_URL}/dev/storage/{quote(path, safe='')}"
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
    raw = os.environ.get("CORS_ORIGINS", "*")
    return [o.strip() for o in raw.split(",")]


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


# ─── Health Check ─────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "Mobiliti SaaS API"}


@app.get("/health")
def health():
    return {"status": "ok"}


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


# ─── GENERAR COTIZACION ───────────────────────────────────────

# COTIZACIONES WEB

@app.post("/cotizaciones/init-upload")
def cotizaciones_init_upload(body: dict, current_user: dict = Depends(get_current_user)):
    try:
        _require_active_subscription(current_user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")

    filename = str(body.get("filename", "input.xlsx")).strip()
    file_size = int(body.get("size", 0) or 0)
    template = str(body.get("template", "Formato Cotizacion 2026 GDL (1).xlsx")).strip()

    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx")
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="Tamano de archivo requerido")
    if file_size > MAX_QUOTE_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Archivo mayor a {MAX_QUOTE_UPLOAD_MB} MB")

    job_id = str(uuid.uuid4())
    input_path = f"users/{current_user['id']}/jobs/{job_id}/input.xlsx"
    metadata = {"original_filename": filename, "file_size": file_size}

    try:
        upload = _create_signed_upload(input_path)
        job = db_create_quote_job(current_user["id"], template, metadata, input_path, job_id=job_id)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error preparando carga: {e}")

    token = upload.get("token") or upload.get("signedToken")
    if not token:
        raise HTTPException(status_code=500, detail="Supabase no devolvio token de carga")

    return {
        "job_id": job["id"],
        "bucket": QUOTE_STORAGE_BUCKET,
        "path": input_path,
        "token": token,
        "signed_upload_url": _signed_upload_url(input_path, token) if not DEV_MODE else None,
        "upload_url": f"/cotizaciones/{job['id']}/dev-upload" if DEV_MODE else None,
        "max_size_mb": MAX_QUOTE_UPLOAD_MB,
    }


@app.post("/cotizaciones/{job_id}/dev-upload")
async def cotizaciones_dev_upload(job_id: str, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail="No disponible")
    job = _quote_job_for_user(job_id, current_user["id"])
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx")
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
    template = str(body.get("template") or job.get("template") or "").strip()
    if not template:
        raise HTTPException(status_code=400, detail="Template requerido")

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
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error de conexion a base de datos: {e}")
    return {"cotizaciones": jobs}


@app.get("/cotizaciones/{job_id}")
def cotizaciones_get(job_id: str, current_user: dict = Depends(get_current_user)):
    job = _quote_job_for_user(job_id, current_user["id"])
    return {"job": job}


@app.get("/cotizaciones/{job_id}/download")
def cotizaciones_download(job_id: str, current_user: dict = Depends(get_current_user)):
    job = _quote_job_for_user(job_id, current_user["id"])
    if job["status"] != "completed" or not job.get("output_path"):
        raise HTTPException(status_code=409, detail="La cotizacion aun no esta lista")
    try:
        signed_url = _create_signed_download(job["output_path"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error preparando descarga: {e}")
    if not signed_url:
        raise HTTPException(status_code=500, detail="Supabase no devolvio URL de descarga")
    return {"download_url": signed_url, "expires_in": SIGNED_DOWNLOAD_TTL_SECONDS}


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
        return FileResponse(source, media_type=media_type, filename=filename)

    try:
        content = _storage_download_bytes(job["output_path"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"Error descargando cotizacion: {e}")
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
    "download_url": "https://github.com/REMOVED_PASSWORD/mobiliti-generador/releases/download/v1.5.7/Mobiliti_Generador_v1.5.7.zip",
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
