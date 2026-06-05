#!/usr/bin/env python3
"""
Script para rotar (cambiar) el JWT_SECRET_KEY en Vercel.

Uso:
    python scripts/rotate_jwt_secret.py
    
O con un valor personalizado:
    python scripts/rotate_jwt_secret.py --secret mi-nuevo-secreto-custom

Esto:
    1. Genera un nuevo JWT_SECRET_KEY seguro (256 bits)
    2. Muestra instrucciones para actualizarlo en Vercel
    3. Invalida todos los tokens existentes (los usuarios deberan volver a loguearse)
"""

import secrets
import sys
import argparse


def generate_secure_secret(length=64):
    """Genera un secret seguro usando secrets.token_urlsafe."""
    return secrets.token_urlsafe(length)


def main():
    parser = argparse.ArgumentParser(description="Rota el JWT_SECRET_KEY del backend Mobiliti")
    parser.add_argument(
        "--secret",
        help="Usa un secret personalizado en lugar de generar uno aleatorio",
        default=None,
    )
    parser.add_argument(
        "--length",
        type=int,
        default=64,
        help="Longitud del secret generado (default: 64 caracteres)",
    )
    args = parser.parse_args()

    if args.secret:
        new_secret = args.secret
        print("[INFO] Usando secret proporcionado por el usuario.")
    else:
        new_secret = generate_secure_secret(args.length)
        print("[OK] Nuevo JWT_SECRET_KEY generado aleatoriamente.")

    print()
    print("=" * 70)
    print("  ROTACION DE JWT_SECRET_KEY - Mobiliti SaaS")
    print("=" * 70)
    print()
    print("NUEVO SECRET:")
    print(f"  {new_secret}")
    print()
    print("LONGITUD:")
    print(f"  {len(new_secret)} caracteres")
    print()
    print("=" * 70)
    print("  INSTRUCCIONES PARA APLICAR EL CAMBIO")
    print("=" * 70)
    print()
    print("1. Actualiza la variable en Vercel:")
    print("   cd vercel_deploy")
    print("   vercel env rm JWT_SECRET_KEY")
    print("   vercel env add JWT_SECRET_KEY")
    print(f"   (pega el valor: {new_secret[:20]}...)")
    print()
    print("2. Re-deploya el backend:")
    print("   vercel --prod")
    print()
    print("3. Aviso a usuarios:")
    print("   Todos los tokens JWT existentes quedaran INVALIDOS.")
    print("   Los usuarios deberan volver a iniciar sesion.")
    print()
    print("4. Si usas el backend local (backend/auth.py):")
    print("   export JWT_SECRET_KEY='" + new_secret + "'")
    print()
    print("=" * 70)
    print("  IMPORTANTE")
    print("=" * 70)
    print()
    print("- Guarda este secret en un gestor de contrasenas (1Password, Bitwarden)")
    print("- NUNCA commitees este secret al repositorio")
    print("- El secret anterior queda expuesto en el historial de Git")
    print("  Considera limpiar el historial con: scripts/clean_git_history.sh")
    print()

    # Guardar en archivo temporal para facilitar copiar-pegar
    temp_file = "/tmp/mobiliti_new_jwt_secret.txt"
    try:
        with open(temp_file, "w") as f:
            f.write(new_secret)
        print(f"[INFO] Secret tambien guardado temporalmente en: {temp_file}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
