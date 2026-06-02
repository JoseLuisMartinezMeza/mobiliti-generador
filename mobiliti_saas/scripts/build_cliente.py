#!/usr/bin/env python3
"""
Script para compilar el cliente desktop Mobiliti SaaS con PyInstaller.
El backend ahora esta en Vercel; este .exe solo contiene el generador local + login.
"""

import os
import sys
import shutil
import subprocess

# Directorios
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAAS_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.dirname(SAAS_DIR)
SPECFILE = os.path.join(SAAS_DIR, "Mobiliti_SaaS.spec")
DIST_DIR = os.path.join(SAAS_DIR, "dist")
BUILD_DIR = os.path.join(SAAS_DIR, "build")

def main():
    print("=" * 60)
    print("Mobiliti SaaS - Build Cliente Desktop")
    print("=" * 60)

    # Verificar PyInstaller
    try:
        import PyInstaller
        print(f"[OK] PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("[ERROR] PyInstaller no instalado. Ejecuta:")
        print("        pip install pyinstaller")
        sys.exit(1)

    # Limpiar builds anteriores
    print("\n[1/4] Limpiando builds anteriores...")
    for d in [DIST_DIR, BUILD_DIR]:
        if os.path.exists(d):
            shutil.rmtree(d)
            print(f"      Eliminado: {d}")

    # Compilar
    print("\n[2/4] Compilando con PyInstaller...")
    print(f"      Spec: {SPECFILE}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        SPECFILE,
        "--clean",
        "--noconfirm",
    ]

    result = subprocess.run(cmd, cwd=SAAS_DIR)
    if result.returncode != 0:
        print("[ERROR] Fallo la compilacion")
        sys.exit(1)

    # Verificar salida
    print("\n[3/4] Verificando salida...")
    exe_name = "Mobiliti_Generador.exe"
    exe_path = os.path.join(DIST_DIR, exe_name)

    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"[OK] {exe_name} generado correctamente")
        print(f"     Tamano: {size_mb:.1f} MB")
        print(f"     Ruta: {exe_path}")
    else:
        print(f"[ERROR] No se encontro {exe_name}")
        sys.exit(1)

    # Copiar a release
    print("\n[4/4] Copiando a carpeta release...")
    release_dir = os.path.join(SAAS_DIR, "release")
    os.makedirs(release_dir, exist_ok=True)

    release_exe = os.path.join(release_dir, exe_name)
    shutil.copy2(exe_path, release_exe)
    print(f"[OK] Copiado a: {release_exe}")

    print("\n" + "=" * 60)
    print("BUILD COMPLETADO")
    print("=" * 60)
    print("\nArchivos generados:")
    print(f"  - {exe_path}")
    print(f"  - {release_exe}")
    print("\nPara distribuir:")
    print("  1. Copia Mobiliti_Generador.exe + config.json")
    print("  2. Edita config.json con la URL de tu API en Vercel")
    print("  3. El usuario ejecuta el .exe e inicia sesion con email/password")


if __name__ == "__main__":
    main()
