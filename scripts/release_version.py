#!/usr/bin/env python3
"""
Script de release para Mobiliti Generador.

Uso:
    python scripts/release_version.py --version 1.5.4 --notes "Fix de bug X"

Pasos automatizados:
1. Actualiza version.txt
2. Actualiza CURRENT_VERSION en ambos backends (inline en index.py)
3. Ejecuta PyInstaller build
4. Genera ZIP de distribucion

Requiere: PyInstaller instalado, acceso al proyecto.
"""
import argparse
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CLIENT_DIR = PROJECT_ROOT / "mobiliti_saas" / "cliente"
API_DIR = PROJECT_ROOT / "mobiliti_saas" / "api"
DEPLOY_API_DIR = PROJECT_ROOT / "vercel_deploy" / "api"
DIST_DIR = PROJECT_ROOT / "mobiliti_saas" / "dist"
RELEASE_DIR = PROJECT_ROOT / "mobiliti_saas" / "release"


def update_version_txt(version: str):
    vf = CLIENT_DIR / "version.txt"
    vf.write_text(version + "\n", encoding="utf-8")
    print(f"[release] {vf} -> {version}")


def _make_manifest_block(version: str, notes: str, download_url: str, force: bool) -> str:
    release_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = version.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return f'''CURRENT_VERSION = {{
    "version": "{version}",
    "major": {major},
    "minor": {minor},
    "patch": {patch},
    "download_url": "{download_url}",
    "release_notes": """{notes}""",
    "release_date": "{release_date}",
    "force_update": {str(force).lower()},
    "min_version_required": None,
}}'''


def update_version_in_index(index_path: Path, version: str, notes: str, download_url: str, force: bool):
    content = index_path.read_text(encoding="utf-8")
    new_block = _make_manifest_block(version, notes, download_url, force)

    # Reemplazar el bloque CURRENT_VERSION existente
    pattern = r'CURRENT_VERSION = \{[^}]+\}'
    if re.search(pattern, content):
        content = re.sub(pattern, new_block, content, count=1)
    else:
        print(f"[release] ADVERTENCIA: no se encontro CURRENT_VERSION en {index_path}")
        return

    index_path.write_text(content, encoding="utf-8")
    print(f"[release] {index_path} actualizado")


def build_exe():
    print("[release] Ejecutando PyInstaller...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "Mobiliti_SaaS.spec", "--noconfirm"],
        cwd=PROJECT_ROOT / "mobiliti_saas",
        check=True,
    )


def create_zip(version: str) -> Path:
    zip_name = f"Mobiliti_Generador_Windows_v{version}.zip"
    zip_path = RELEASE_DIR / zip_name
    RELEASE_DIR.mkdir(exist_ok=True)

    files_to_zip = [
        (DIST_DIR / "Mobiliti_Generador.exe", "Mobiliti_Generador.exe"),
        (PROJECT_ROOT / "mobiliti_saas" / "config.json", "config.json"),
        (PROJECT_ROOT / "Formato Cotización 2026 GDL (1).xlsx", "Formato Cotización 2026 GDL (1).xlsx"),
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, dst in files_to_zip:
            if src.exists():
                zf.write(src, dst)
            else:
                print(f"[release] ADVERTENCIA: {src} no encontrado")

    print(f"[release] ZIP creado: {zip_path}")
    return zip_path


def main():
    parser = argparse.ArgumentParser(description="Release script para Mobiliti Generador")
    parser.add_argument("--version", required=True, help="Nueva version SemVer (ej: 1.5.4)")
    parser.add_argument("--notes", default="", help="Notas de release")
    parser.add_argument("--download-url", default="", help="URL de descarga del .exe")
    parser.add_argument("--force", action="store_true", help="Marcar como actualizacion forzosa")
    parser.add_argument("--skip-build", action="store_true", help="Saltar build de PyInstaller")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    download_url = args.download_url or f"https://github.com/tuusuario/mobiliti/releases/download/v{version}/Mobiliti_Generador.exe"

    update_version_txt(version)
    update_version_in_index(API_DIR / "index.py", version, args.notes, download_url, args.force)
    update_version_in_index(DEPLOY_API_DIR / "index.py", version, args.notes, download_url, args.force)

    if not args.skip_build:
        build_exe()

    zip_path = create_zip(version)

    print("\n" + "=" * 60)
    print("RELEASE COMPLETADO")
    print("=" * 60)
    print(f"Version:     {version}")
    print(f"ZIP:         {zip_path}")
    print(f"Descarga:    {download_url}")
    print("\nProximos pasos:")
    print("1. Subir el ZIP a GitHub Releases (o tu CDN)")
    print("2. Deploy del backend a Vercel: vercel --prod")
    print("3. Probar la actualizacion desde un cliente anterior")


if __name__ == "__main__":
    main()
