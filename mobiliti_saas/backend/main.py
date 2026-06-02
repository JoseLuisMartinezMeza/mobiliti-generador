"""
Mobiliti SaaS Backend - API con control de suscripciones
El cliente desktop requiere conexion a internet y login email/password.
Se verifica la suscripcion activa en cada request protegido.
"""
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from pydantic import BaseModel
import uuid
import os
import subprocess
import sys
import shutil
import json

from database import engine, get_db
from models import Base, Usuario, Suscripcion, SesionCliente
from auth import (
    verify_password, get_password_hash, create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES, get_user_by_email, verificar_suscripcion_activa,
    SECRET_KEY, ALGORITHM
)
from jose import jwt, JWTError

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mobiliti SaaS API")

# Directorios
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
HISTORIAL_DIR = os.path.join(os.path.dirname(__file__), "historial")
TEMPLATE_DEFAULT = "Formato Cotización 2026 GDL (1).xlsx"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(HISTORIAL_DIR, exist_ok=True)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

VERSION_ACTUAL = "1.0.0"


# --- Schemas ---
class UsuarioCreate(BaseModel):
    email: str
    password: str
    nombre: str
    empresa: str


class SuscripcionCreate(BaseModel):
    usuario_email: str
    plan: str = "mensual"  # mensual, anual
    dias: int = 30


class LoginRequest(BaseModel):
    email: str
    password: str
    hardware_id: str = ""


class VerificarToken(BaseModel):
    token: str


# --- Auth helpers ---
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalido",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user_by_email(db, email=email)
    if user is None:
        raise credentials_exception
    return user


def get_current_admin(current_user: Usuario = Depends(get_current_user)):
    if not current_user.es_admin:
        raise HTTPException(status_code=403, detail="Se requiere permiso de admin")
    return current_user


def usuario_con_suscripcion_activa(current_user: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    """Dependencia que verifica suscripcion activa antes de permitir acceso."""
    activa, mensaje = verificar_suscripcion_activa(db, current_user.id)
    if not activa:
        raise HTTPException(status_code=403, detail=mensaje)
    return current_user


# --- Health check ---
@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION_ACTUAL, "servicio": "online"}


# --- Registro ---
@app.post("/registro")
def registro(usuario: UsuarioCreate, db: Session = Depends(get_db)):
    if get_user_by_email(db, usuario.email):
        raise HTTPException(status_code=400, detail="Email ya registrado")
    db_user = Usuario(
        email=usuario.email,
        hashed_password=get_password_hash(usuario.password),
        nombre=usuario.nombre,
        empresa=usuario.empresa,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return {"mensaje": "Usuario registrado. Contacta al admin para activar tu suscripcion.", "email": db_user.email}


# --- Login para clientes desktop ---
@app.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = get_user_by_email(db, data.email)
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Credenciales incorrectas")
    if not user.activo:
        raise HTTPException(status_code=400, detail="Usuario desactivado")

    # Verificar suscripcion activa
    activa, mensaje = verificar_suscripcion_activa(db, user.id)
    if not activa:
        raise HTTPException(status_code=403, detail=mensaje)

    # Crear token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    # Registrar sesion del cliente
    sesion = SesionCliente(
        usuario_id=user.id,
        hardware_id=data.hardware_id,
        token=access_token,
    )
    db.add(sesion)
    db.commit()

    # Obtener info de suscripcion
    suscripcion = db.query(Suscripcion).filter(Suscripcion.usuario_id == user.id).first()

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "nombre": user.nombre,
        "email": user.email,
        "suscripcion": {
            "estado": suscripcion.estado if suscripcion else None,
            "plan": suscripcion.plan if suscripcion else None,
            "fecha_fin": suscripcion.fecha_fin.isoformat() if suscripcion and suscripcion.fecha_fin else None,
        }
    }


# --- Token para web admin ---
@app.post("/token")
def login_web(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = get_user_by_email(db, form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Credenciales incorrectas")
    if not user.activo:
        raise HTTPException(status_code=400, detail="Usuario desactivado")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


# --- Verificar token (usado por cliente desktop periodicamente) ---
@app.post("/verificar-token")
def verificar_token(data: VerificarToken, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(data.token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Token invalido")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expirado o invalido")

    user = get_user_by_email(db, email=email)
    if not user or not user.activo:
        raise HTTPException(status_code=401, detail="Usuario no activo")

    # Verificar suscripcion
    activa, mensaje = verificar_suscripcion_activa(db, user.id)
    if not activa:
        raise HTTPException(status_code=403, detail=mensaje)

    # Actualizar ultimo acceso de sesion
    sesion = db.query(SesionCliente).filter(SesionCliente.token == data.token).first()
    if sesion:
        sesion.ultimo_acceso = datetime.utcnow()
        db.commit()

    suscripcion = db.query(Suscripcion).filter(Suscripcion.usuario_id == user.id).first()

    return {
        "valido": True,
        "email": user.email,
        "nombre": user.nombre,
        "suscripcion": {
            "estado": suscripcion.estado if suscripcion else None,
            "plan": suscripcion.plan if suscripcion else None,
            "fecha_fin": suscripcion.fecha_fin.isoformat() if suscripcion and suscripcion.fecha_fin else None,
        }
    }


# --- Admin: Suscripciones ---
@app.post("/admin/suscripciones")
def crear_suscripcion(data: SuscripcionCreate, admin: Usuario = Depends(get_current_admin), db: Session = Depends(get_db)):
    user = get_user_by_email(db, data.usuario_email)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Eliminar suscripcion previa si existe
    db.query(Suscripcion).filter(Suscripcion.usuario_id == user.id).delete()

    fecha_fin = datetime.utcnow() + timedelta(days=data.dias)
    suscripcion = Suscripcion(
        usuario_id=user.id,
        estado="activa",
        plan=data.plan,
        fecha_inicio=datetime.utcnow(),
        fecha_fin=fecha_fin,
    )
    db.add(suscripcion)
    db.commit()
    db.refresh(suscripcion)

    return {
        "mensaje": "Suscripcion creada",
        "usuario": user.email,
        "plan": data.plan,
        "fecha_fin": fecha_fin.isoformat(),
        "dias": data.dias,
    }


@app.get("/admin/suscripciones")
def listar_suscripciones(admin: Usuario = Depends(get_current_admin), db: Session = Depends(get_db)):
    suscripciones = db.query(Suscripcion).all()
    resultado = []
    for s in suscripciones:
        user = db.query(Usuario).filter(Usuario.id == s.usuario_id).first()
        resultado.append({
            "id": s.id,
            "usuario_email": user.email if user else "",
            "usuario_nombre": user.nombre if user else "",
            "estado": s.estado,
            "plan": s.plan,
            "fecha_inicio": s.fecha_inicio.isoformat() if s.fecha_inicio else None,
            "fecha_fin": s.fecha_fin.isoformat() if s.fecha_fin else None,
        })
    return resultado


@app.patch("/admin/suscripciones/{suscripcion_id}")
def actualizar_suscripcion(suscripcion_id: int, estado: str = Form(...), admin: Usuario = Depends(get_current_admin), db: Session = Depends(get_db)):
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripcion no encontrada")
    suscripcion.estado = estado
    db.commit()
    return {"id": suscripcion_id, "estado": estado}


@app.patch("/admin/usuarios/{user_id}")
def toggle_usuario(user_id: int, admin: Usuario = Depends(get_current_admin), db: Session = Depends(get_db)):
    user = db.query(Usuario).filter(Usuario.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    user.activo = not user.activo
    db.commit()
    return {"id": user_id, "activo": user.activo}


@app.get("/admin/usuarios")
def listar_usuarios(admin: Usuario = Depends(get_current_admin), db: Session = Depends(get_db)):
    usuarios = db.query(Usuario).all()
    resultado = []
    for u in usuarios:
        suscripcion = db.query(Suscripcion).filter(Suscripcion.usuario_id == u.id).first()
        resultado.append({
            "id": u.id,
            "email": u.email,
            "nombre": u.nombre,
            "empresa": u.empresa,
            "es_admin": u.es_admin,
            "activo": u.activo,
            "suscripcion": {
                "estado": suscripcion.estado if suscripcion else "sin suscripcion",
                "plan": suscripcion.plan if suscripcion else None,
                "fecha_fin": suscripcion.fecha_fin.isoformat() if suscripcion and suscripcion.fecha_fin else None,
            }
        })
    return resultado


# --- Actualizaciones ---
@app.get("/version")
def version():
    return {"version": VERSION_ACTUAL}


# --- Cotizaciones (protegidas por suscripcion activa) ---
@app.post("/upload-quotation")
def upload_quotation(
    file: UploadFile = File(...),
    current_user: Usuario = Depends(usuario_con_suscripcion_activa)
):
    filename = f"{current_user.id}_{uuid.uuid4().hex[:8]}_{file.filename}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"filename": filename, "mensaje": "Quotation subido correctamente"}


@app.get("/templates")
def listar_templates(current_user: Usuario = Depends(usuario_con_suscripcion_activa)):
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    templates = []
    for f in os.listdir(root):
        if f.endswith(".xlsx") and ("Formato" in f or "Template" in f or "template" in f.lower()):
            templates.append(f)
    return {"templates": templates}


@app.post("/generar-cotizacion")
def generar_cotizacion_api(
    source_filename: str = Form(...),
    template: str = Form(TEMPLATE_DEFAULT),
    cotizacion: str = Form("100-00000"),
    proyecto: str = Form(""),
    cliente: str = Form(""),
    correo: str = Form(""),
    telefono: str = Form(""),
    direccion: str = Form(""),
    razon_social: str = Form(""),
    current_user: Usuario = Depends(usuario_con_suscripcion_activa)
):
    source_path = os.path.join(UPLOAD_DIR, source_filename)
    if not os.path.exists(source_path):
        raise HTTPException(status_code=404, detail="Quotation no encontrado")

    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    template_path = os.path.join(root, template)
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail="Template no encontrado")

    output_name = f"Cotizacion_{proyecto.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    output_path = os.path.join(HISTORIAL_DIR, output_name)

    script_path = os.path.join(root, "generar_cotizacion_v5_xlwings.py")

    cmd = [
        sys.executable, script_path,
        "--source", source_path,
        "--template", template_path,
        "--output", output_path,
        "--cotizacion", cotizacion,
        "--proyecto", proyecto,
        "--cliente", cliente,
        "--correo", correo,
        "--telefono", telefono,
        "--direccion", direccion,
        "--razon_social", razon_social,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 and not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail=f"Error al generar: {result.stderr[:500]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

    return {
        "mensaje": "Cotizacion generada",
        "filename": output_name,
        "download_url": f"/download/{output_name}"
    }


@app.get("/historial")
def historial(current_user: Usuario = Depends(usuario_con_suscripcion_activa)):
    files = []
    for f in sorted(os.listdir(HISTORIAL_DIR), reverse=True):
        filepath = os.path.join(HISTORIAL_DIR, f)
        stat = os.stat(filepath)
        files.append({
            "filename": f,
            "fecha": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "size_kb": round(stat.st_size / 1024, 1),
            "download_url": f"/download/{f}"
        })
    return {"cotizaciones": files}


@app.get("/download/{filename}")
def download(filename: str, current_user: Usuario = Depends(usuario_con_suscripcion_activa)):
    filepath = os.path.join(HISTORIAL_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(filepath, filename=filename)
