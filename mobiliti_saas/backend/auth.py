from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from models import Usuario, Suscripcion

import os

SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY no configurada. Establece la variable de entorno.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 horas

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_user_by_email(db: Session, email: str):
    return db.query(Usuario).filter(Usuario.email == email).first()


def verificar_suscripcion_activa(db: Session, usuario_id: int) -> tuple[bool, str]:
    """Verifica si el usuario tiene una suscripcion activa y vigente."""
    suscripcion = db.query(Suscripcion).filter(
        Suscripcion.usuario_id == usuario_id
    ).first()

    if not suscripcion:
        return False, "No tienes una suscripcion activa. Contacta al administrador."

    if suscripcion.estado == "suspendida":
        return False, "Tu suscripcion ha sido suspendida. Contacta al administrador."

    if suscripcion.estado == "cancelada":
        return False, "Tu suscripcion fue cancelada. Contacta al administrador."

    if suscripcion.estado == "vencida":
        return False, "Tu suscripcion ha vencido. Renueva para continuar."

    if suscripcion.fecha_fin and suscripcion.fecha_fin < datetime.utcnow():
        # Auto-marcar como vencida
        suscripcion.estado = "vencida"
        db.commit()
        return False, "Tu suscripcion ha vencido. Renueva para continuar."

    return True, "OK"
