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
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt
from fastapi import FastAPI, HTTPException, Header, Depends, Request

# Security headers middleware
class SecurityHeadersMiddleware:
    """Agrega headers de seguridad HTTP recomendados por OWASP."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                # Convertir a lista mutable
                headers_list = list(headers)
                # Agregar headers de seguridad
                security_headers = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https://*.supabase.co; frame-ancestors 'none'; base-uri 'self'; form-action 'self';"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"),
                    (b"x-xss-protection", b"1; mode=block"),
                ]
                headers_list.extend(security_headers)
                message["headers"] = headers_list
            await send(message)

        await self.app(scope, receive, send_with_headers)
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

# ═══════════════════════════════════════════════════════════════
# CONFIGURACION
# ═══════════════════════════════════════════════════════════════

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Rate limiting en memoria (protege contra fuerza bruta basica)
# Nota: En Vercel serverless esto es por instancia, no global
_rate_limit_store = {}
MAX_LOGIN_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 900  # 15 minutos

def _check_rate_limit(client_ip: str) -> bool:
    """Retorna True si el cliente esta bloqueado."""
    now = datetime.now(timezone.utc)
    attempts = _rate_limit_store.get(client_ip, [])
    # Filtrar intentos dentro de la ventana de tiempo
    attempts = [t for t in attempts if (now - t).total_seconds() < RATE_LIMIT_WINDOW_SECONDS]
    _rate_limit_store[client_ip] = attempts
    return len(attempts) >= MAX_LOGIN_ATTEMPTS

def _record_attempt(client_ip: str):
    """Registra un intento de login fallido."""
    now = datetime.now(timezone.utc)
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    _rate_limit_store[client_ip].append(now)

# Password hashing helpers
def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ═══════════════════════════════════════════════════════════════
# VERSION MANIFEST (Auto-updater)
# ═══════════════════════════════════════════════════════════════

CURRENT_VERSION = {
    "version": "1.5.3",
    "major": 1,
    "minor": 5,
    "patch": 3,
    "download_url": "https://github.com/REMOVED_PASSWORD/mobiliti-generador/releases/download/v1.5.3/Mobiliti_Generador.exe",
    "release_notes": "Fix: deteccion dinamica de columna Vol. Mejora: escalado de imagenes por categoria.",
    "release_date": "2026-06-04T10:00:00Z",
    "force_update": False,
    "min_version_required": None,
}

# ═══════════════════════════════════════════════════════════════
# SUPABASE REST HELPERS (urllib.request)
# ═══════════════════════════════════════════════════════════════

def _get_supabase_headers():
    key = SUPABASE_SERVICE_KEY or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


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
    rows = _supabase_req("GET", "/saas_usuarios", params={"email": f"eq.{email}"})
    return rows[0] if rows else None


def db_get_usuario_by_id(user_id: int):
    rows = _supabase_req("GET", "/saas_usuarios", params={"id": f"eq.{user_id}"})
    return rows[0] if rows else None


def db_list_usuarios():
    return _supabase_req("GET", "/saas_usuarios", params={"select": "*", "order": "creado.desc"})


def db_create_usuario(email, hashed_password, nombre, empresa, es_admin=False):
    data = {
        "email": email.lower().strip(),
        "hashed_password": hashed_password,
        "nombre": nombre,
        "empresa": empresa,
        "es_admin": es_admin,
        "activo": True,
        "creado": _iso(datetime.now(timezone.utc)),
    }
    return _supabase_req("POST", "/saas_usuarios", json_data=data)[0]


def db_get_suscripcion_by_usuario(usuario_id: int):
    rows = _supabase_req(
        "GET",
        "/saas_suscripciones",
        params={"usuario_id": f"eq.{usuario_id}", "order": "creado.desc", "limit": "1"}
    )
    return rows[0] if rows else None


def db_list_suscripciones():
    return _supabase_req(
        "GET",
        "/saas_suscripciones",
        params={"select": "*,saas_usuarios(email,nombre)", "order": "creado.desc"}
    )


def db_create_suscripcion(usuario_id, plan, fecha_inicio, fecha_fin, estado="activa"):
    data = {
        "usuario_id": usuario_id,
        "estado": estado,
        "plan": plan,
        "fecha_inicio": _iso(fecha_inicio),
        "fecha_fin": _iso(fecha_fin),
        "creado": _iso(datetime.now(timezone.utc)),
    }
    return _supabase_req("POST", "/saas_suscripciones", json_data=data)[0]


def db_update_suscripcion(suscripcion_id, updates):
    rows = _supabase_req("PATCH", f"/saas_suscripciones?id=eq.{suscripcion_id}", json_data=updates)
    return rows[0] if rows else {}


# ═══════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════

def _origins():
    raw = os.environ.get("CORS_ORIGINS", "")
    if not raw or raw.strip() == "":
        # En produccion, sin CORS_ORIGINS configurado, solo permitir origenes comunes
        return ["https://verceldeploy-pied.vercel.app", "https://mobiliti-saas.vercel.app"]
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="Mobiliti SaaS API", version="1.0.0")

origins = _origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True if origins != ["*"] else False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(SecurityHeadersMiddleware)


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
    # Rate limiting por IP
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    if _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Intenta de nuevo en 15 minutos.")

    email = body.get("email", "").lower().strip()
    password = body.get("password", "")

    if not email or not password:
        _record_attempt(client_ip)
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


# ─── VERSION / AUTO-UPDATE ────────────────────────────────────

@app.get("/version")
def get_latest_version():
    """Devuelve informacion de la ultima version disponible."""
    return CURRENT_VERSION

@app.get("/download/latest")
def download_latest():
    """Redirige a la descarga del .exe mas reciente."""
    return {"url": CURRENT_VERSION["download_url"]}


# ═══════════════════════════════════════════════════════════════
# HANDLER PARA VERCEL
# ═══════════════════════════════════════════════════════════════

handler = Mangum(app)
