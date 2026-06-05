"""Script para crear el primer usuario administrador en la base de datos.

TODAS las credenciales se leen de variables de entorno.
NUNCA hardcodees contrasenas en este archivo.

Uso:
    export ADMIN_EMAIL="admin@tuempresa.com"
    export ADMIN_PASSWORD="TuContrasenaSegura123!"
    export ADMIN_NOMBRE="REMOVED_PASSWORD Luis Martinez"
    python seed_admin.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.models import Usuario, Suscripcion
from backend.auth import get_password_hash

# Configuracion desde variables de entorno
DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./mobiliti_saas.db")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_NOMBRE = os.environ.get("ADMIN_NOMBRE", "Administrador")

if not ADMIN_EMAIL or not ADMIN_PASSWORD:
    print("ERROR: Debes configurar ADMIN_EMAIL y ADMIN_PASSWORD como variables de entorno.")
    print("Ejemplo:")
    print('  export ADMIN_EMAIL="admin@tuempresa.com"')
    print('  export ADMIN_PASSWORD="TuContrasenaSegura123!"')
    sys.exit(1)

if len(ADMIN_PASSWORD) < 8:
    print("ERROR: La contrasena debe tener al menos 8 caracteres.")
    sys.exit(1)


def crear_admin():
    print(f"Conectando a la base de datos...")
    engine = create_engine(DB_URL, echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        # Verificar si ya existe
        admin_existente = db.query(Usuario).filter(
            Usuario.email == ADMIN_EMAIL
        ).first()

        if admin_existente:
            print(f"El usuario {ADMIN_EMAIL} ya existe (ID: {admin_existente.id}).")

            # Asegurar que tenga el rol admin
            if admin_existente.rol != "admin":
                admin_existente.rol = "admin"
                db.commit()
                print("Rol actualizado a 'admin'.")

            # Asegurar que tenga suscripcion
            suscripcion = db.query(Suscripcion).filter(
                Suscripcion.usuario_id == admin_existente.id
            ).first()

            if not suscripcion:
                nueva_sus = Suscripcion(
                    usuario_id=admin_existente.id,
                    tipo="anual",
                    estado="activa",
                    fecha_fin=None  # Ilimitada
                )
                db.add(nueva_sus)
                db.commit()
                print("Suscripcion activa creada.")
            else:
                suscripcion.estado = "activa"
                db.commit()
                print("Suscripcion activa verificada.")

            return

        # Crear usuario admin
        admin = Usuario(
            email=ADMIN_EMAIL,
            nombre=ADMIN_NOMBRE,
            password_hash=get_password_hash(ADMIN_PASSWORD),
            rol="admin",
            activo=True
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(f"Usuario admin creado con ID: {admin.id}")

        # Crear suscripcion ilimitada
        suscripcion = Suscripcion(
            usuario_id=admin.id,
            tipo="anual",
            estado="activa",
            fecha_fin=None
        )
        db.add(suscripcion)
        db.commit()

        print(f"Suscripcion activa creada para admin.")
        print(f"\n=== ADMIN CREADO ===")
        print(f"Email: {ADMIN_EMAIL}")
        print(f"====================\n")
        print("IMPORTANTE: Guarda estas credenciales en un gestor de contrasenas.")

    except Exception as e:
        print(f"ERROR: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    crear_admin()
