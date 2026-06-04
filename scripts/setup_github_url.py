#!/usr/bin/env python3
"""
Configura tu usuario y repo de GitHub para el sistema de actualizaciones.
Uso:
    python scripts/setup_github_url.py --user miusuario --repo mobiliti-generador

Esto reemplaza el placeholder TU_USUARIO_GITHUB/TU_REPO en todos los archivos.
"""
import argparse
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
FILES_TO_UPDATE = [
    PROJECT_ROOT / "vercel_deploy" / "api" / "index.py",
    PROJECT_ROOT / "mobiliti_saas" / "api" / "index.py",
    PROJECT_ROOT / "scripts" / "release_version.py",
]


def update_file(path: Path, user: str, repo: str):
    content = path.read_text(encoding="utf-8")
    original = content

    # Reemplazar el placeholder en URLs de GitHub Releases
    content = re.sub(
        r"github\.com/TU_USUARIO_GITHUB/TU_REPO",
        f"github.com/{user}/{repo}",
        content,
    )

    if content != original:
        path.write_text(content, encoding="utf-8")
        print(f"  [OK] {path}")
        return True
    else:
        print(f"  [SKIP] {path} (no se encontro placeholder)")
        return False


def main():
    parser = argparse.ArgumentParser(description="Configura tu URL de GitHub Releases")
    parser.add_argument("--user", required=True, help="Tu usuario de GitHub")
    parser.add_argument("--repo", required=True, help="Nombre del repo (ej: mobiliti-generador)")
    args = parser.parse_args()

    print(f"Configurando GitHub URL: github.com/{args.user}/{args.repo}")
    print("-" * 50)

    updated = 0
    for f in FILES_TO_UPDATE:
        if f.exists():
            if update_file(f, args.user, args.repo):
                updated += 1
        else:
            print(f"  [ERROR] No existe: {f}")

    print("-" * 50)
    if updated > 0:
        print(f"Listo! {updated} archivo(s) actualizado(s).")
        print("\nAhora puedes publicar tu release:")
        print(f'  python scripts/release_version.py --version 1.5.4 --notes "Tu mensaje"')
    else:
        print("No se actualizo ningun archivo. Verifica que el placeholder exista.")


if __name__ == "__main__":
    main()
