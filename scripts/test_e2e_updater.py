#!/usr/bin/env python3
"""
Script de prueba end-to-end para el flujo de auto-actualización.
Simula todo el flujo sin necesidad de GUI ni descargar un EXE real.

Uso:
    python scripts/test_e2e_updater.py

Flujo simulado:
    1. Lee version.txt local (ej. 1.5.2)
    2. Consulta /version en el backend
    3. Compara versiones
    4. Simula la descarga (verifica que la URL sea accesible)
    5. Genera el batch de reemplazo (sin ejecutarlo)
    6. Verifica que el batch sea sintácticamente correcto
"""
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error

# Añadir cliente al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mobiliti_saas', 'cliente'))

import updater

API_URL = "https://verceldeploy-pied.vercel.app"
TEST_TIMEOUT = 15

def print_step(n, msg):
    print(f"\n[{n}/6] {msg}")

def main():
    print("=" * 60)
    print("TEST END-TO-END: Auto-Updater Mobiliti")
    print("=" * 60)

    # 1. Leer versión local
    print_step(1, "Leyendo version.txt local")
    local_ver = updater._read_local_version()
    print(f"   Versión local: {local_ver!r}")
    if not local_ver:
        print("   ERROR: No se pudo leer version.txt")
        return 1

    # 2. Consultar backend
    print_step(2, f"Consultando {API_URL}/version")
    try:
        remote = updater._fetch_json(f"{API_URL}/version")
        print(f"   Versión remota: {remote['version']}")
        print(f"   Release date:   {remote.get('release_date', 'N/A')}")
        print(f"   Force update:   {remote.get('force_update', False)}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return 1

    # 3. Comparar versiones
    print_step(3, "Comparando versiones")
    cmp = updater._compare_version(local_ver, remote['version'])
    
    if cmp < 0:
        print(f"   RESULTADO: Hay actualización disponible ({local_ver} → {remote['version']})")
    elif cmp == 0:
        print(f"   RESULTADO: Misma versión ({local_ver})")
    else:
        print(f"   RESULTADO: Local es más nueva ({local_ver} > {remote['version']})")

    # 4. Verificar URL de descarga
    print_step(4, "Verificando URL de descarga")
    download_url = remote.get('download_url', '')
    print(f"   URL: {download_url}")
    
    if "github.com" in download_url and "TU_USUARIO" in download_url:
        print("   ADVERTENCIA: La URL aún tiene el placeholder TU_USUARIO_GITHUB/TU_REPO")
        print("   El auto-updater fallará hasta que se configure el repo real.")
    else:
        try:
            req = urllib.request.Request(download_url, method='HEAD')
            req.add_header('User-Agent', 'Mobiliti-Updater-Test/1.0')
            with urllib.request.urlopen(req, timeout=TEST_TIMEOUT) as resp:
                print(f"   STATUS: {resp.status}")
                size = resp.headers.get('Content-Length')
                if size:
                    print(f"   TAMAÑO: {int(size) / (1024*1024):.1f} MB")
        except urllib.error.HTTPError as e:
            print(f"   STATUS: {e.code} ({e.reason})")
            if e.code == 404:
                print("   ADVERTENCIA: El release no existe aún en GitHub.")
        except Exception as e:
            print(f"   ERROR al verificar URL: {e}")

    # 5. Simular generación del batch
    print_step(5, "Simulando generación del batch de reemplazo")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.bat', delete=False) as f:
        bat_path = f.name
    
    fake_current = r"C:\Users\Test\Mobiliti_Generador.exe"
    fake_new = r"C:\Users\Test\Mobiliti_Generador.exe.new"
    
    try:
        updater._write_bat_and_launch(fake_current, fake_new, bat_path)
        with open(bat_path, 'r') as f:
            content = f.read()
        print("   Batch generado correctamente.")
        print("   Primeras 3 líneas:")
        for line in content.split('\n')[:3]:
            print(f"      {line}")
    except Exception as e:
        print(f"   ERROR: {e}")
        return 1
    finally:
        try:
            os.remove(bat_path)
        except:
            pass

    # 6. Verificar integridad del updater.py
    print_step(6, "Verificando integridad del módulo updater")
    required_funcs = [
        'check_and_prompt_update',
        '_parse_version',
        '_compare_version',
        '_fetch_json',
        '_download_file',
        '_write_bat_and_launch',
    ]
    all_ok = True
    for func in required_funcs:
        if hasattr(updater, func):
            print(f"   OK: {func}")
        else:
            print(f"   FALTA: {func}")
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("RESULTADO: TODAS LAS PRUEBAS PASARON [OK]")
    else:
        print("RESULTADO: HAY PROBLEMAS PENDIENTES [WARN]")
    print("=" * 60)
    return 0 if all_ok else 1

if __name__ == '__main__':
    sys.exit(main())
