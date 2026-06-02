"""Script para inicializar la base de datos local para tests."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.models import Base, Usuario, Suscripcion
from backend.auth import get_password_hash

# Base de datos SQLite para tests
DB_URL = "sqlite:///./test.db"


def init_db():
    print(f"Creando base de datos en {DB_URL}...")
    engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})

    # Crear tablas
    Base.metadata.create_all(bind=engine)
    print("Tablas creadas.")

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        # Crear usuario admin de prueba
        admin = Usuario(
            email="***REMOVED***",
            nombre="REMOVED_PASSWORD Luis Martinez",
            password_hash=get_password_hash("***REMOVED***"),
            rol="admin",
            activo=True
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        # Crear suscripcion
        suscripcion = Suscripcion(
            usuario_id=admin.id,
            tipo="anual",
            estado="activa",
            fecha_fin=None
        )
        db.add(suscripcion)
        db.commit()

        print(f"Usuario admin creado: ID {admin.id}")
        print("Base de datos inicializada.")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
