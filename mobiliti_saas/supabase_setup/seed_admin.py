"""Script para crear o reparar el usuario administrador inicial."""

from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.auth import get_password_hash
from backend.models import Suscripcion, Usuario


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return value


def _load_settings() -> dict:
    return {
        "db_url": _required_env("DATABASE_URL"),
        "admin_email": _required_env("ADMIN_EMAIL").lower(),
        "admin_password": _required_env("ADMIN_PASSWORD"),
        "admin_nombre": os.environ.get("ADMIN_NOMBRE", "Administrador Mobiliti").strip() or "Administrador Mobiliti",
        "admin_empresa": os.environ.get("ADMIN_EMPRESA", "Mobiliti").strip() or "Mobiliti",
    }


def crear_admin():
    settings = _load_settings()
    print("Conectando a la base de datos...")
    engine = create_engine(settings["db_url"], echo=False)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_local()

    try:
        admin = db.query(Usuario).filter(Usuario.email == settings["admin_email"]).first()

        if not admin:
            admin = Usuario(
                email=settings["admin_email"],
                hashed_password=get_password_hash(settings["admin_password"]),
                nombre=settings["admin_nombre"],
                empresa=settings["admin_empresa"],
                es_admin=True,
                activo=True,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            print(f"Usuario admin creado con ID: {admin.id}.")
        else:
            admin.hashed_password = get_password_hash(settings["admin_password"])
            admin.nombre = settings["admin_nombre"]
            admin.empresa = settings["admin_empresa"]
            admin.es_admin = True
            admin.activo = True
            db.commit()
            print(f"Usuario admin actualizado (ID: {admin.id}).")

        suscripcion = db.query(Suscripcion).filter(Suscripcion.usuario_id == admin.id).first()
        fecha_fin = datetime.utcnow() + timedelta(days=3650)

        if not suscripcion:
            suscripcion = Suscripcion(
                usuario_id=admin.id,
                estado="activa",
                plan="anual",
                fecha_inicio=datetime.utcnow(),
                fecha_fin=fecha_fin,
            )
            db.add(suscripcion)
            db.commit()
            print("Suscripcion admin creada.")
        else:
            suscripcion.estado = "activa"
            suscripcion.plan = "anual"
            suscripcion.fecha_fin = fecha_fin
            db.commit()
            print("Suscripcion admin actualizada.")

        print("Admin listo.")
        print(f"Email: {settings['admin_email']}")
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    crear_admin()
