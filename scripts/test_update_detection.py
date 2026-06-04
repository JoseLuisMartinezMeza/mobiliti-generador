#!/usr/bin/env python3
"""
Prueba de deteccion de actualizacion con version simulada.
Cambia temporalmente version.txt a una version vieja para probar
que el updater detecta correctamente la nueva version.

Uso:
    python scripts/test_update_detection.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mobiliti_saas', 'cliente'))
import updater

API_URL = "https://verceldeploy-pied.vercel.app"
VERSION_FILE = os.path.join(os.path.dirname(__file__), '..', 'mobiliti_saas', 'cliente', 'version.txt')

def main():
    print("=" * 60)
    print("TEST: Deteccion de actualizacion (simulacion)")
    print("=" * 60)

    # Guardar version original
    with open(VERSION_FILE, 'r') as f:
        original_version = f.read().strip()
    print(f"\nVersion real: {original_version}")

    # Simular version vieja
    fake_version = "1.5.1"
    print(f"Simulando version: {fake_version}")
    
    with open(VERSION_FILE, 'w') as f:
        f.write(fake_version)

    try:
        # Forzar lectura de version
        updater._local_version_cache = None
        local = updater._read_local_version()
        print(f"Version leida del archivo: {local}")

        # Consultar backend
        print(f"\nConsultando {API_URL}/version ...")
        remote = updater._fetch_json(f"{API_URL}/version")
        print(f"Version remota: {remote['version']}")

        # Comparar
        cmp = updater._compare_version(local, remote['version'])
        if cmp < 0:
            print(f"\n[OK] DETECCION CORRECTA: Hay actualizacion disponible!")
            print(f"   {local} -> {remote['version']}")
            print(f"   URL: {remote['download_url']}")
            print(f"   Notas: {remote['release_notes'][:60]}...")
            return 0
        elif cmp == 0:
            print(f"\n[WARN] Igual version (no deberia pasar con simulacion)")
            return 1
        else:
            print(f"\n[WARN] Local es mas nueva (inesperado)")
            return 1
    finally:
        # Restaurar version original
        with open(VERSION_FILE, 'w') as f:
            f.write(original_version)
        updater._local_version_cache = None
        print(f"\nVersion restaurada a: {original_version}")

if __name__ == '__main__':
    sys.exit(main())
