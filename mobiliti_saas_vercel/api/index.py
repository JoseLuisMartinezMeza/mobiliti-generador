"""
Backend Mobiliti SaaS para Vercel (Serverless).
FastAPI + Supabase REST API.
TODO EN UN SOLO ARCHIVO para evitar problemas de imports en Vercel.
"""

import os
import httpx
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://amarztcyhgtszmwazxgl.supabase.co"
)
SUPABASE_SERVICE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "JWT_PLACEHOLDER"
)
JWT_SECRET_KEY = os.environ.get(
    "JWT_SECRET_KEY",
    "***REMOVED***"
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Cliente Supabase REST
HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

supabase = httpx.AsyncClient(
    base_url=f"{SUPABASE_URL}/rest/v1",
    headers=HEADERS,
    timeout=30.0
)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ═══════════════════════════════════════════════════════════════
# AUTH HELPERS
# ═══════════════════════════════════════════════════════════════

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


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

async def db_get_usuario_by_email(email: str):
    resp = await supabase.get("/saas_usuarios", params={"email": f"eq.{email}"})
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def db_get_usuario_by_id(user_id: int):
    resp = await supabase.get("/saas_usuarios", params={"id": f"eq.{user_id}"})
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def db_list_usuarios():
    resp = await supabase.get("/saas_usuarios", params={"select": "*", "order": "creado.desc"})
    resp.raise_for_status()
    return resp.json()


async def db_create_usuario(email, hashed_password, nombre, empresa, es_admin=False):
    data = {
        "email": email.lower().strip(),
        "hashed_password": hashed_password,
        "nombre": nombre,
        "empresa": empresa,
        "es_admin": es_admin,
        "activo": True,
        "creado": _iso(datetime.now(timezone.utc)),
    }
    resp = await supabase.post("/saas_usuarios", json=data)
    resp.raise_for_status()
    return resp.json()[0]


async def db_get_suscripcion_by_usuario(usuario_id: int):
    resp = await supabase.get(
        "/saas_suscripciones",
        params={"usuario_id": f"eq.{usuario_id}", "order": "creado.desc", "limit": "1"}
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def db_list_suscripciones():
    resp = await supabase.get(
        "/saas_suscripciones",
        params={"select": "*,saas_usuarios(email,nombre)", "order": "creado.desc"}
    )
    resp.raise_for_status()
    return resp.json()


async def db_create_suscripcion(usuario_id, plan, fecha_inicio, fecha_fin, estado="activa"):
    data = {
        "usuario_id": usuario_id,
        "estado": estado,
        "plan": plan,
        "fecha_inicio": _iso(fecha_inicio),
        "fecha_fin": _iso(fecha_fin),
        "creado": _iso(datetime.now(timezone.utc)),
    }
    resp = await supabase.post("/saas_suscripciones", json=data)
    resp.raise_for_status()
    return resp.json()[0]


async def db_update_suscripcion(suscripcion_id, updates):
    resp = await supabase.patch(f"/saas_suscripciones?id=eq.{suscripcion_id}", json=updates)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


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


async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")
    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token invalido")
    usuario = await db_get_usuario_by_id(int(user_id))
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if not usuario.get("activo"):
        raise HTTPException(status_code=403, detail="Usuario desactivado")
    return usuario


async def require_admin(current_user: dict = Depends(get_current_user)):
    if not current_user.get("es_admin"):
        raise HTTPException(status_code=403, detail="Se requieren permisos de administrador")
    return current_user


# ─── Health Check ─────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "service": "Mobiliti SaaS API"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ─── LOGIN ────────────────────────────────────────────────────

@app.post("/login")
async def login_endpoint(body: dict):
    email = body.get("email", "").lower().strip()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")

    usuario = await db_get_usuario_by_email(email)
    if not usuario:
        raise HTTPException(status_code=401, detail="Credenciales invalidas")

    if not verify_password(password, usuario["hashed_password"]):
        raise HTTPException(status_code=401, detail="Credenciales invalidas")

    suscripcion = await db_get_suscripcion_by_usuario(usuario["id"])
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
            "es_admin": usuario["es_admin"],
        },
        "suscripcion": {
            "id": suscripcion["id"],
            "estado": suscripcion["estado"],
            "plan": suscripcion["plan"],
            "fecha_inicio": suscripcion["fecha_inicio"],
            "fecha_fin": suscripcion["fecha_fin"],
        }
    }


# ─── VERIFICAR SESIÓN ─────────────────────────────────────────

@app.post("/verificar-sesion")
async def verificar_sesion(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")

    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")

    user_id = payload.get("sub")
    usuario = await db_get_usuario_by_id(int(user_id))
    if not usuario or not usuario.get("activo"):
        raise HTTPException(status_code=403, detail="Usuario no encontrado o inactivo")

    suscripcion = await db_get_suscripcion_by_usuario(usuario["id"])
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
async def admin_list_usuarios(user: dict = Depends(require_admin)):
    return await db_list_usuarios()


@app.post("/admin/usuarios")
async def admin_create_usuario(body: dict, user: dict = Depends(require_admin)):
    email = body.get("email", "").lower().strip()
    password = body.get("password", "")
    nombre = body.get("nombre", "")
    empresa = body.get("empresa", "")
    es_admin = body.get("es_admin", False)

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")

    existing = await db_get_usuario_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="El email ya esta registrado")

    hashed = get_password_hash(password)
    nuevo = await db_create_usuario(email, hashed, nombre, empresa, es_admin)
    return {"mensaje": "Usuario creado", "usuario": nuevo}


# ─── ADMIN: SUSCRIPCIONES ─────────────────────────────────────

@app.get("/admin/suscripciones")
async def admin_list_suscripciones(user: dict = Depends(require_admin)):
    return await db_list_suscripciones()


@app.post("/admin/suscripciones")
async def admin_create_suscripcion(body: dict, user: dict = Depends(require_admin)):
    usuario_id = body.get("usuario_id")
    plan = body.get("plan", "mensual")
    dias = body.get("dias", 30)
    estado = body.get("estado", "activa")

    if not usuario_id:
        raise HTTPException(status_code=400, detail="usuario_id requerido")

    now = datetime.now(timezone.utc)
    fecha_fin = now + timedelta(days=dias)
    nueva = await db_create_suscripcion(int(usuario_id), plan, now, fecha_fin, estado)
    return {"mensaje": "Suscripcion creada", "suscripcion": nueva}


@app.patch("/admin/suscripciones/{suscripcion_id}")
async def admin_update_suscripcion(suscripcion_id: int, body: dict, user: dict = Depends(require_admin)):
    allowed = {"estado", "plan", "fecha_fin"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No hay campos validos para actualizar")
    updated = await db_update_suscripcion(suscripcion_id, updates)
    return {"mensaje": "Suscripcion actualizada", "suscripcion": updated}


# ─── GENERAR COTIZACIÓN ───────────────────────────────────────

@app.post("/generar-cotizacion")
async def generar_cotizacion(body: dict, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token no proporcionado")

    token = authorization.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token invalido")

    user_id = payload.get("sub")
    usuario = await db_get_usuario_by_id(int(user_id))
    if not usuario or not usuario.get("activo"):
        raise HTTPException(status_code=403, detail="Usuario inactivo")

    suscripcion = await db_get_suscripcion_by_usuario(usuario["id"])
    now = datetime.now(timezone.utc)

    if not suscripcion:
        raise HTTPException(status_code=403, detail="Sin suscripcion")

    fecha_fin = datetime.fromisoformat(suscripcion["fecha_fin"].replace("Z", "+00:00"))
    if suscripcion["estado"] != "activa" or fecha_fin < now:
        raise HTTPException(status_code=403, detail="Suscripcion no activa")

    return {"autorizado": True, "mensaje": "Puedes generar la cotizacion localmente"}


# ═══════════════════════════════════════════════════════════════
# HANDLER PARA VERCEL
# ═══════════════════════════════════════════════════════════════

handler = Mangum(app)
