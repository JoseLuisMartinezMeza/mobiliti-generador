#!/usr/bin/env python3
"""
Script seguro para crear el usuario administrador inicial en Supabase.

TODAS las credenciales se leen de variables de entorno.
NUNCA hardcodees contraseñas en este archivo.

Uso:
    export SUPABASE_URL="https://tu-proyecto.supabase.co"
    export SUPABASE_SERVICE_KEY="eyJ..."
    export ADMIN_EMAIL="admin@tuempresa.com"
    export ADMIN_PASSWORD="TuContraseñaSegura123!"
    export ADMIN_NOMBRE="Administrador"
    export ADMIN_EMPRESA="Tu Empresa"
    python seed_admin_secure.py
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

import bcrypt


def get_env(var_name: str) -> str:
    """Obtiene una variable de entorno requerida."""
    value = os.environ.get(var_name)
    if not value:
        print(f"ERROR: La variable de entorno {var_name} es requerida.")
        sys.exit(1)
    return value


def _get_supabase_headers(supabase_key: str):
    return {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _supabase_req(supabase_url: str, supabase_key: str, method: str, path: str, params=None, json_data=None):
    url = f"{supabase_url}/rest/v1{path}"
    if params:
        query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{query}"

    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    for k, v in _get_supabase_headers(supabase_key).items():
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


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def main():
    print("=" * 60)
    print(" Mobiliti SaaS - Seed de Administrador Seguro")
    print("=" * 60)

    # Leer configuracion de variables de entorno
    SUPABASE_URL = get_env("SUPABASE_URL")
    SUPABASE_SERVICE_KEY = get_env("SUPABASE_SERVICE_KEY")
    ADMIN_EMAIL = get_env("ADMIN_EMAIL").lower().strip()
    ADMIN_PASSWORD = get_env("ADMIN_PASSWORD")
    ADMIN_NOMBRE = os.environ.get("ADMIN_NOMBRE", "Administrador")
    ADMIN_EMPRESA = os.environ.get("ADMIN_EMPRESA", "Mobiliti")

    # Validar fortaleza de contraseña
    if len(ADMIN_PASSWORD) < 8:
        print("ERROR: La contraseña del admin debe tener al menos 8 caracteres.")
        sys.exit(1)

    print(f"\nConectando a Supabase: {SUPABASE_URL}")
    print(f"Email del admin: {ADMIN_EMAIL}")

    # Verificar si el usuario ya existe
    try:
        existing = _supabase_req(
            SUPABASE_URL, SUPABASE_SERVICE_KEY,
            "GET", "/saas_usuarios",
            params={"email": f"eq.{ADMIN_EMAIL}"}
        )
    except RuntimeError as e:
        print(f"ERROR al consultar Supabase: {e}")
        sys.exit(1)

    if existing:
        usuario = existing[0]
        print(f"\n⚠️  El usuario ya existe (ID: {usuario['id']}).")
        print("   Verificando suscripcion...")

        # Verificar si tiene suscripcion
        try:
            suscripciones = _supabase_req(
                SUPABASE_URL, SUPABASE_SERVICE_KEY,
                "GET", "/saas_suscripciones",
                params={
                    "usuario_id": f"eq.{usuario['id']}",
                    "order": "creado.desc",
                    "limit": "1"
                }
            )
        except RuntimeError as e:
            print(f"ERROR al consultar suscripciones: {e}")
            sys.exit(1)

        if suscripciones:
            print(f"   ✅ Suscripcion encontrada (ID: {suscripciones[0]['id']}, Estado: {suscripciones[0]['estado']})")
        else:
            print("   ⚠️  No tiene suscripcion. Creando una nueva...")
            now = datetime.now(timezone.utc)
            fecha_fin = now + timedelta(days=365 * 10)  # 10 años
            try:
                nueva = _supabase_req(
                    SUPABASE_URL, SUPABASE_SERVICE_KEY,
                    "POST", "/saas_suscripciones",
                    json_data={
                        "usuario_id": usuario["id"],
                        "estado": "activa",
                        "plan": "anual",
                        "fecha_inicio": now.isoformat(),
                        "fecha_fin": fecha_fin.isoformat(),
                        "creado": now.isoformat(),
                    }
                )[0]
                print(f"   ✅ Suscripcion creada (ID: {nueva['id']})")
            except RuntimeError as e:
                print(f"ERROR al crear suscripcion: {e}")
                sys.exit(1)

        print("\n✅ Seed completado. El administrador ya estaba configurado.")
        return

    # Crear usuario admin
    print("\n🔧 Creando usuario administrador...")
    hashed_password = hash_password(ADMIN_PASSWORD)
    now = datetime.now(timezone.utc)

    try:
        nuevo_usuario = _supabase_req(
            SUPABASE_URL, SUPABASE_SERVICE_KEY,
            "POST", "/saas_usuarios",
            json_data={
                "email": ADMIN_EMAIL,
                "hashed_password": hashed_password,
                "nombre": ADMIN_NOMBRE,
                "empresa": ADMIN_EMPRESA,
                "es_admin": True,
                "activo": True,
                "creado": now.isoformat(),
            }
        )[0]
        print(f"   ✅ Usuario creado (ID: {nuevo_usuario['id']})")
    except RuntimeError as e:
        print(f"ERROR al crear usuario: {e}")
        sys.exit(1)

    # Crear suscripcion activa
    print("\n🔧 Creando suscripcion activa...")
    fecha_fin = now + timedelta(days=365 * 10)  # 10 años

    try:
        nueva_suscripcion = _supabase_req(
            SUPABASE_URL, SUPABASE_SERVICE_KEY,
            "POST", "/saas_suscripciones",
            json_data={
                "usuario_id": nuevo_usuario["id"],
                "estado": "activa",
                "plan": "anual",
                "fecha_inicio": now.isoformat(),
                "fecha_fin": fecha_fin.isoformat(),
                "creado": now.isoformat(),
            }
        )[0]
        print(f"   ✅ Suscripcion creada (ID: {nueva_suscripcion['id']})")
    except RuntimeError as e:
        print(f"ERROR al crear suscripcion: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(" ✅ SEED COMPLETADO EXITOSAMENTE")
    print("=" * 60)
    print(f"\n   Usuario:  {ADMIN_EMAIL}")
    print(f"   Nombre:   {ADMIN_NOMBRE}")
    print(f"   Empresa:  {ADMIN_EMPRESA}")
    print(f"   Plan:     Anual (10 años)")
    print(f"   Admin:    Sí")
    print("\n   El backend esta listo para usar.")
    print("=" * 60)


if __name__ == "__main__":
    main()
