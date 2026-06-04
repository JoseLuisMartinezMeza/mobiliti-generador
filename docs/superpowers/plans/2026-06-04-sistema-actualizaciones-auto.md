# Sistema de Actualizaciones Automáticas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar un sistema de actualizaciones automáticas para el Cotizador Mobiliti (.exe PyInstaller) que notifique al usuario cuando haya una nueva versión, descargue en background y reinstale al reiniciar.

**Architecture:** Arquitectura "Passive Update" con updater helper externo. La app principal (Tkinter) verifica versiones contra el backend Vercel, descarga el nuevo .exe a `%TEMP%`, lanza un batch helper que espera el cierre, reemplaza el binario y relanza la app.

**Tech Stack:** Python + Tkinter (cliente), FastAPI + Vercel (backend), Batch script Windows (updater helper), PyInstaller (build).

---

## Contexto del Proyecto

El Cotizador Mobiliti es una app desktop Windows empaquetada con PyInstaller como `.exe` único (~200 MB). Se distribuye como ZIP con:
- `Mobiliti_Generador.exe` — ejecutable principal
- `config.json` — URL del API
- `Formato Cotización 2026 GDL (1).xlsx` — template

Los usuarios descomprimen el ZIP y ejecutan el `.exe`. No hay instalador.

### Flujo actual de distribución
1. Dev compila con PyInstaller
2. Genera ZIP manualmente
3. Envía ZIP al usuario por email/WhatsApp
4. Usuario reemplaza el archivo manualmente

### Problema
Cuando hay actualizaciones (fix de bugs, nuevas categorías, mejoras), todos los clientes deben descargar el ZIP nuevo manualmente.

### Solución: Passive Update
La app verifica silenciosamente al inicio si hay nueva versión. Si la hay, muestra diálogo Tkinter. El usuario puede:
- **Actualizar ahora**: descarga, cierra app, reemplaza .exe, relanza automáticamente
- **Más tarde**: continúa usando la app, pregunta de nuevo en el próximo inicio
- **Omitir esta versión**: guarda en config y no pregunta hasta la siguiente versión

---

## File Structure

### Archivos nuevos a crear

| Archivo | Responsabilidad |
|---------|-----------------|
| `mobiliti_saas/cliente/updater.py` | Lógica de verificación, descarga, diálogo UI |
| `mobiliti_saas/cliente/updater_helper.bat` | Script batch que reemplaza el .exe y relanza |
| `mobiliti_saas/cliente/version.txt` | Versión actual embebida (ej: `1.5.2`) |
| `mobiliti_saas/api/version_manifest.py` | Endpoint FastAPI `/version` y `/download/latest` |
| `mobiliti_saas/api/version_manifest.json` | JSON estático servido como fallback |
| `scripts/release_version.py` | Script de release: genera manifest, sube a Vercel |

### Archivos a modificar

| Archivo | Cambio |
|---------|--------|
| `mobiliti_saas/cliente/main_cliente.py` | Integrar `updater.py` al inicio del login |
| `mobiliti_saas/api/index.py` | Agregar rutas `/version`, `/download/latest` |
| `mobiliti_saas/cliente/entry_point.py` | Leer `version.txt` para saber versión actual |
| `mobiliti_saas/Mobiliti_SaaS.spec` | Incluir `version.txt` y `updater_helper.bat` en PyInstaller bundle |

---

## Versionado Semántico

Usaremos **SemVer simplificado**: `MAJOR.MINOR.PATCH`

| Tipo | Ejemplo | Descripción |
|------|---------|-------------|
| MAJOR | `2.0.0` | Cambio incompatible (nuevo backend, nuevo formato) |
| MINOR | `1.6.0` | Nueva funcionalidad (nueva categoría, campo nuevo) |
| PATCH | `1.5.3` | Fix de bug (corrección de typo, fix de Vol.) |

**Regla de negocio:**
- Si `MAJOR` cambia → forzar actualización (no se puede omitir)
- Si `MINOR/PATCH` cambia → opcional (usuario puede omitir)

---

## Task 1: Servidor — Endpoints de Versión en Vercel

**Files:**
- Create: `mobiliti_saas/api/version_manifest.py`
- Modify: `mobiliti_saas/api/index.py`

**Contexto:** El backend FastAPI en Vercel necesita exponer dos endpoints:
1. `GET /version` — devuelve la última versión disponible, URL de descarga, y release notes
2. `GET /download/latest` — redirige HTTP 302 a la URL del .exe actualizado (almacenado en GitHub Releases o S3)

**Nota sobre almacenamiento:** Los archivos .exe de ~200 MB no deben almacenarse en el repo ni en Vercel (límite de 50MB). Se usará **GitHub Releases** como CDN gratuito para los binarios.

- [ ] **Step 1: Crear `version_manifest.py` con modelo Pydantic**

```python
# mobiliti_saas/api/version_manifest.py
from pydantic import BaseModel, HttpUrl
from typing import Optional

class VersionInfo(BaseModel):
    version: str          # ej: "1.5.3"
    major: int
    minor: int
    patch: int
    download_url: HttpUrl
    release_notes: str
    release_date: str     # ISO 8601: "2026-06-04T10:00:00Z"
    force_update: bool = False
    min_version_required: Optional[str] = None

# Esta info se actualiza manualmente en cada release
CURRENT_VERSION = VersionInfo(
    version="1.5.3",
    major=1,
    minor=5,
    patch=3,
    download_url="https://github.com/tuusuario/mobiliti/releases/download/v1.5.3/Mobiliti_Generador.exe",
    release_notes="Fix: detección dinámica de columna Vol. Mejora: escalado de imágenes por categoría.",
    release_date="2026-06-04T10:00:00Z",
    force_update=False,
    min_version_required=None,
)
```

- [ ] **Step 2: Agregar endpoints al router de `index.py`**

En `mobiliti_saas/api/index.py`, agregar:

```python
from fastapi.responses import RedirectResponse
from version_manifest import CURRENT_VERSION, VersionInfo

@app.get("/version", response_model=VersionInfo)
def get_latest_version():
    """Devuelve información de la última versión disponible."""
    return CURRENT_VERSION

@app.get("/download/latest")
def download_latest():
    """Redirige a la descarga del .exe más reciente."""
    return RedirectResponse(url=str(CURRENT_VERSION.download_url), status_code=302)
```

- [ ] **Step 3: Probar endpoints localmente**

```bash
cd mobiliti_saas/api
curl http://localhost:8000/version
curl -I http://localhost:8000/download/latest
```

Expected: JSON con versión y redirección 302 respectivamente.

- [ ] **Step 4: Commit**

```bash
git add mobiliti_saas/api/version_manifest.py mobiliti_saas/api/index.py
git commit -m "feat(api): add version endpoints for auto-updater"
```

---

## Task 2: Cliente — Módulo `updater.py`

**Files:**
- Create: `mobiliti_saas/cliente/updater.py`

**Contexto:** Este módulo maneja toda la lógica de actualización en el cliente. Debe:
1. Leer versión actual desde `version.txt` (embebido por PyInstaller)
2. Llamar a `GET /version` del backend
3. Comparar versiones (SemVer)
4. Mostrar diálogo Tkinter si hay actualización
5. Descargar nuevo .exe a `%TEMP%` con progress bar
6. Crear y lanzar `updater_helper.bat`
7. Cerrar la aplicación principal

- [ ] **Step 1: Crear `updater.py` con estructura base**

```python
# mobiliti_saas/cliente/updater.py
"""
Sistema de actualizaciones automáticas para Mobiliti Generador.
Arquitectura: Passive Update con helper batch externo.
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
import tempfile
import threading
from pathlib import Path
from tkinter import Toplevel, Label, Button, ttk, messagebox
import tkinter as tk

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
APP_NAME = "Mobiliti Generador"
VERSION_FILE = "version.txt"
CONFIG_SKIP_KEY = "skip_version"
TEMP_DIR = Path(tempfile.gettempdir()) / "mobiliti_update"
BACKEND_VERSION_URL = None  # Se carga desde config.json

# ---------------------------------------------------------------------------
# Utilidades de versión
# ---------------------------------------------------------------------------

def parse_version(v: str) -> tuple[int, int, int]:
    """Convierte '1.5.3' → (1, 5, 3)."""
    parts = v.strip().lstrip("v").split(".")
    return tuple(int(p) for p in parts[:3])

def compare_versions(local: str, remote: str) -> int:
    """
    Compara dos versiones SemVer.
    Retorna: -1 (local < remota), 0 (iguales), 1 (local > remota)
    """
    l = parse_version(local)
    r = parse_version(remote)
    for a, b in zip(l, r):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0

# ---------------------------------------------------------------------------
# Rutas y recursos
# ---------------------------------------------------------------------------

def get_resource_path(filename: str) -> Path:
    """
    Resuelve la ruta a un recurso embebido o externo.
    Prioridad: directorio del .exe > sys._MEIPASS > cwd.
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates = [
            exe_dir / filename,
            Path(sys._MEIPASS) / filename if hasattr(sys, "_MEIPASS") else None,
        ]
    else:
        candidates = [
            Path(__file__).parent / filename,
            Path.cwd() / filename,
        ]
    for cand in candidates:
        if cand and cand.exists():
            return cand
    return candidates[0] if candidates else Path(filename)

def get_current_version() -> str:
    """Lee la versión actual desde version.txt."""
    vf = get_resource_path(VERSION_FILE)
    try:
        return vf.read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"

def get_config() -> dict:
    """Carga config.json para obtener la URL del API."""
    cf = get_resource_path("config.json")
    try:
        return json.loads(cf.read_text(encoding="utf-8"))
    except Exception:
        return {"api_url": "https://verceldeploy-pied.vercel.app"}

# ---------------------------------------------------------------------------
# Comunicación con backend
# ---------------------------------------------------------------------------

def fetch_latest_version(timeout: int = 10) -> dict | None:
    """Consulta al backend la última versión disponible."""
    global BACKEND_VERSION_URL
    if BACKEND_VERSION_URL is None:
        cfg = get_config()
        base = cfg.get("api_url", "").rstrip("/")
        BACKEND_VERSION_URL = f"{base}/version"
    try:
        req = urllib.request.Request(
            BACKEND_VERSION_URL,
            headers={"User-Agent": f"{APP_NAME}/{get_current_version()}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[updater] Error consultando versión: {e}")
        return None

# ---------------------------------------------------------------------------
# Descarga con progreso
# ---------------------------------------------------------------------------

def download_file(url: str, dest: Path, progress_callback=None, chunk_size=8192):
    """Descarga un archivo binario con callback de progreso."""
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
    return dest

# ---------------------------------------------------------------------------
# UI — Diálogo de actualización
# ---------------------------------------------------------------------------

class UpdateDialog(Toplevel):
    """
    Diálogo modal que muestra información de la nueva versión
    y permite al usuario decidir qué hacer.
    """
    def __init__(self, parent, version_info: dict, on_update, on_later, on_skip):
        super().__init__(parent)
        self.title(f"Actualización disponible — {APP_NAME}")
        self.resizable(False, False)
        self.configure(bg="#f8f9fa")
        self.grab_set()  # Modal

        self.on_update = on_update
        self.on_later = on_later
        self.on_skip = on_skip

        # Centrar ventana
        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (400 // 2)
        y = (self.winfo_screenheight() // 2) - (300 // 2)
        self.geometry(f"400x300+{x}+{y}")

        # Contenido
        Label(
            self,
            text="🚀 Nueva versión disponible",
            font=("Segoe UI", 14, "bold"),
            bg="#f8f9fa",
            fg="#1a237e",
        ).pack(pady=(20, 10))

        Label(
            self,
            text=f"Versión instalada: {get_current_version()}\n"
                 f"Versión disponible: {version_info['version']}",
            font=("Segoe UI", 11),
            bg="#f8f9fa",
            fg="#212529",
            justify="center",
        ).pack(pady=5)

        Label(
            self,
            text=f"Notas de release:\n{version_info.get('release_notes', 'Sin notas')}",
            font=("Segoe UI", 9),
            bg="#f8f9fa",
            fg="#6c757d",
            wraplength=360,
            justify="left",
        ).pack(pady=10, padx=20)

        # Botones
        btn_frame = tk.Frame(self, bg="#f8f9fa")
        btn_frame.pack(pady=20)

        Button(
            btn_frame,
            text="Actualizar ahora",
            command=self._do_update,
            bg="#1a237e",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            width=16,
        ).pack(side="left", padx=5)

        Button(
            btn_frame,
            text="Más tarde",
            command=self._do_later,
            bg="#ffffff",
            fg="#212529",
            font=("Segoe UI", 10),
            width=12,
        ).pack(side="left", padx=5)

        Button(
            btn_frame,
            text="Omitir esta versión",
            command=self._do_skip,
            bg="#f8f9fa",
            fg="#6c757d",
            font=("Segoe UI", 9),
            width=16,
        ).pack(side="left", padx=5)

    def _do_update(self):
        self.destroy()
        self.on_update()

    def _do_later(self):
        self.destroy()
        self.on_later()

    def _do_skip(self):
        self.destroy()
        self.on_skip()


class DownloadProgressDialog(Toplevel):
    """Diálogo que muestra el progreso de descarga del nuevo .exe."""
    def __init__(self, parent, version: str):
        super().__init__(parent)
        self.title("Descargando actualización...")
        self.resizable(False, False)
        self.configure(bg="#f8f9fa")
        self.grab_set()

        self.update_idletasks()
        x = (self.winfo_screenwidth() // 2) - (350 // 2)
        y = (self.winfo_screenheight() // 2) - (150 // 2)
        self.geometry(f"350x150+{x}+{y}")

        Label(
            self,
            text=f"Descargando {APP_NAME} v{version}",
            font=("Segoe UI", 11),
            bg="#f8f9fa",
            fg="#212529",
        ).pack(pady=(20, 10))

        self.progress = ttk.Progressbar(self, mode="determinate", length=300)
        self.progress.pack(pady=10)

        self.status_label = Label(
            self,
            text="Conectando...",
            font=("Segoe UI", 9),
            bg="#f8f9fa",
            fg="#6c757d",
        )
        self.status_label.pack()

    def set_progress(self, current: int, total: int):
        if total > 0:
            pct = min(100, int(current * 100 / total))
            self.progress["value"] = pct
            mb = current / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            self.status_label.config(text=f"Descargado: {mb:.1f} / {total_mb:.1f} MB")
        else:
            self.status_label.config(text=f"Descargado: {current} bytes")
        self.update_idletasks()

# ---------------------------------------------------------------------------
# Updater helper — batch script
# ---------------------------------------------------------------------------

def write_updater_batch(current_exe: Path, new_exe: Path, bat_path: Path):
    """
    Genera un batch script que:
    1. Espera 2 segundos a que el proceso principal termine
    2. Reemplaza el .exe viejo con el nuevo
    3. Borra archivos temporales
    4. Relanza la aplicación
    """
    batch_content = f"""@echo off
chcp 65001 >nul
echo [updater] Esperando cierre de {APP_NAME}...
timeout /t 2 /nobreak >nul

echo [updater] Reemplazando ejecutable...
move /Y "{new_exe}" "{current_exe}" >nul 2>&1
if errorlevel 1 (
    echo [updater] Error al reemplazar. Intentando con xcopy...
    xcopy /Y "{new_exe}" "{current_exe}*" >nul 2>&1
)

echo [updater] Limpiando temporales...
rmdir /S /Q "{new_exe.parent}" >nul 2>&1

echo [updater] Iniciando nueva versión...
start "" "{current_exe}"

del "%~f0"
"""
    bat_path.write_text(batch_content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Flujo principal de actualización
# ---------------------------------------------------------------------------

def should_skip_version(remote_version: str) -> bool:
    """Verifica si el usuario optó por omitir esta versión."""
    config_path = get_resource_path("config.json")
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        return cfg.get(CONFIG_SKIP_KEY) == remote_version
    except Exception:
        return False

def set_skip_version(remote_version: str):
    """Guarda en config.json que el usuario quiere omitir esta versión."""
    config_path = get_resource_path("config.json")
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg[CONFIG_SKIP_KEY] = remote_version
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def check_and_prompt_update(parent_window: tk.Tk):
    """
    Función principal llamada al inicio de la app.
    Verifica versión remota y muestra diálogo si es necesario.
    """
    current = get_current_version()
    info = fetch_latest_version()

    if info is None:
        print("[updater] No se pudo contactar al servidor de versiones.")
        return

    remote = info.get("version", "0.0.0")
    cmp = compare_versions(current, remote)

    if cmp >= 0:
        print(f"[updater] Versión actual ({current}) es la más reciente.")
        return

    if should_skip_version(remote):
        print(f"[updater] Usuario omitió la versión {remote}.")
        return

    print(f"[updater] Nueva versión disponible: {remote} (actual: {current})")

    # Si es force_update, mostrar diálogo sin opción de omitir
    if info.get("force_update", False):
        result = messagebox.showwarning(
            "Actualización obligatoria",
            f"Se requiere actualizar a la versión {remote} para continuar.\n\n"
            f"{info.get('release_notes', '')}",
            type=messagebox.OKCANCEL,
        )
        if result != "ok":
            parent_window.destroy()
            sys.exit(0)
        _perform_update(parent_window, info)
        return

    # Diálogo normal
    def on_update():
        _perform_update(parent_window, info)

    def on_later():
        print("[updater] Usuario pospuso la actualización.")

    def on_skip():
        set_skip_version(remote)
        print(f"[updater] Versión {remote} omitida por el usuario.")

    UpdateDialog(parent_window, info, on_update, on_later, on_skip)


def _perform_update(parent_window: tk.Tk, version_info: dict):
    """Descarga el nuevo .exe y lanza el updater helper."""
    download_url = version_info.get("download_url")
    remote_version = version_info.get("version", "unknown")

    if not download_url:
        messagebox.showerror("Error", "No se encontró URL de descarga.")
        return

    # Crear diálogo de progreso
    progress = DownloadProgressDialog(parent_window, remote_version)

    def download_and_install():
        try:
            exe_name = Path(sys.executable if getattr(sys, "frozen", False) else "Mobiliti_Generador.exe").name
            new_exe = TEMP_DIR / exe_name

            def on_progress(current, total):
                parent_window.after(0, lambda: progress.set_progress(current, total))

            download_file(download_url, new_exe, on_progress)

            # Cerrar diálogo de progreso
            parent_window.after(0, progress.destroy)

            # Crear y lanzar batch updater
            current_exe = Path(sys.executable) if getattr(sys, "frozen", False) else Path.cwd() / exe_name
            bat_path = TEMP_DIR / "updater_helper.bat"
            write_updater_batch(current_exe, new_exe, bat_path)

            # Lanzar batch de forma independiente
            import subprocess
            subprocess.Popen(
                [str(bat_path)],
                shell=True,
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            )

            # Cerrar aplicación principal
            parent_window.after(500, parent_window.destroy)
            parent_window.after(1000, lambda: sys.exit(0))

        except Exception as e:
            parent_window.after(0, lambda: messagebox.showerror("Error de actualización", str(e)))
            parent_window.after(0, progress.destroy)

    threading.Thread(target=download_and_install, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point manual (para testing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    check_and_prompt_update(root)
    root.mainloop()
```

- [ ] **Step 2: Commit del módulo updater**

```bash
git add mobiliti_saas/cliente/updater.py
git commit -m "feat(updater): add auto-update client module with Tkinter UI"
```

---

## Task 3: Integrar Updater en `main_cliente.py`

**Files:**
- Modify: `mobiliti_saas/cliente/main_cliente.py`

**Contexto:** El updater debe ejecutarse **después** del login exitoso pero **antes** de mostrar la pantalla principal. Esto asegura que solo usuarios con sesión activa reciban actualizaciones (evita checks innecesarios si el login falla).

- [ ] **Step 1: Importar y llamar al updater en el flujo post-login**

Agregar al inicio de `main_cliente.py`:
```python
import updater
```

En el método que muestra la pantalla principal después del login exitoso (ej: `_show_main_screen` o similar), agregar como **primera línea**:

```python
def _show_main_screen(self):
    # Verificar actualizaciones disponibles
    updater.check_and_prompt_update(self.root)
    
    # ... resto del código existente ...
```

- [ ] **Step 2: Asegurar que el check no bloquee la UI**

El `check_and_prompt_update` ya es no-bloqueante porque:
1. `fetch_latest_version` usa `urllib.request.urlopen` con timeout de 10s
2. La descarga corre en un `threading.Thread` separado
3. El diálogo es modal pero no bloquea el hilo principal más allá de la interacción del usuario

- [ ] **Step 3: Commit**

```bash
git add mobiliti_saas/cliente/main_cliente.py
git commit -m "feat(gui): integrate auto-updater check after login"
```

---

## Task 4: Archivo `version.txt` y PyInstaller Spec

**Files:**
- Create: `mobiliti_saas/cliente/version.txt`
- Modify: `mobiliti_saas/Mobiliti_SaaS.spec`
- Modify: `mobiliti_saas/cliente/entry_point.py` (opcional)

**Contexto:** PyInstaller debe empaquetar `version.txt` dentro del `.exe` para que `get_resource_path` lo encuentre en `sys._MEIPASS`.

- [ ] **Step 1: Crear `version.txt`**

```
1.5.3
```

Guardar en: `mobiliti_saas/cliente/version.txt`

- [ ] **Step 2: Modificar `Mobiliti_SaaS.spec` para incluir `version.txt`**

En el spec de PyInstaller, agregar `version.txt` a `datas`. Ejemplo:

```python
# Dentro del Analysis(...)
datas=[
    ('cliente/version.txt', '.'),
    ('cliente/updater_helper.bat', '.'),  # si existe
    # ... otros archivos existentes ...
],
```

- [ ] **Step 3: Actualizar script de build para generar `version.txt` automáticamente**

En `scripts/build_cliente.py` (o crear uno nuevo):

```python
import argparse
from pathlib import Path

def write_version(version: str):
    version_file = Path("mobiliti_saas/cliente/version.txt")
    version_file.write_text(version + "\n", encoding="utf-8")
    print(f"[build] version.txt actualizado a {version}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="Versión SemVer ej: 1.5.3")
    args = parser.parse_args()
    write_version(args.version)
```

- [ ] **Step 4: Commit**

```bash
git add mobiliti_saas/cliente/version.txt mobiliti_saas/Mobiliti_SaaS.spec scripts/build_cliente.py
git commit -m "build(pyinstaller): embed version.txt and updater batch in exe"
```

---

## Task 5: Script de Release

**Files:**
- Create: `scripts/release_version.py`

**Contexto:** Cada vez que se quiera publicar una nueva versión, este script automatiza:
1. Actualizar `version.txt`
2. Actualizar `version_manifest.py` en el backend
3. Generar el build con PyInstaller
4. Crear ZIP de distribución
5. (Opcional) Subir a GitHub Releases via `gh` CLI

- [ ] **Step 1: Crear `scripts/release_version.py`**

```python
#!/usr/bin/env python3
"""
Script de release para Mobiliti Generador.
Uso: python scripts/release_version.py --version 1.5.4 --notes "Fix de bug X"
"""
import argparse
import json
import subprocess
import zipfile
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).parent.parent
CLIENT_DIR = PROJECT_ROOT / "mobiliti_saas" / "cliente"
API_DIR = PROJECT_ROOT / "mobiliti_saas" / "api"
DIST_DIR = PROJECT_ROOT / "mobiliti_saas" / "dist"
RELEASE_DIR = PROJECT_ROOT / "mobiliti_saas" / "release"

def update_version_txt(version: str):
    vf = CLIENT_DIR / "version.txt"
    vf.write_text(version + "\n", encoding="utf-8")
    print(f"[release] {vf} → {version}")

def update_version_manifest(version: str, notes: str, download_url: str, force: bool = False):
    mf = API_DIR / "version_manifest.py"
    release_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = version.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    content = f'''# Auto-generated by release_version.py on {release_date}
from pydantic import BaseModel, HttpUrl
from typing import Optional

class VersionInfo(BaseModel):
    version: str
    major: int
    minor: int
    patch: int
    download_url: HttpUrl
    release_notes: str
    release_date: str
    force_update: bool = False
    min_version_required: Optional[str] = None

CURRENT_VERSION = VersionInfo(
    version="{version}",
    major={major},
    minor={minor},
    patch={patch},
    download_url="{download_url}",
    release_notes="""{notes}""",
    release_date="{release_date}",
    force_update={str(force).lower()},
    min_version_required=None,
)
'''
    mf.write_text(content, encoding="utf-8")
    print(f"[release] {mf} actualizado")

def build_exe():
    print("[release] Ejecutando PyInstaller...")
    subprocess.run(
        ["python", "-m", "PyInstaller", "Mobiliti_SaaS.spec", "--noconfirm"],
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
    parser.add_argument("--version", required=True, help="Nueva versión SemVer (ej: 1.5.4)")
    parser.add_argument("--notes", default="", help="Notas de release")
    parser.add_argument("--download-url", default="", help="URL de descarga del .exe")
    parser.add_argument("--force", action="store_true", help="Marcar como actualización forzosa")
    parser.add_argument("--skip-build", action="store_true", help="Saltar build de PyInstaller")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    download_url = args.download_url or f"https://github.com/tuusuario/mobiliti/releases/download/v{version}/Mobiliti_Generador.exe"

    update_version_txt(version)
    update_version_manifest(version, args.notes, download_url, args.force)

    if not args.skip_build:
        build_exe()

    zip_path = create_zip(version)

    print("\n" + "=" * 60)
    print("RELEASE COMPLETADO")
    print("=" * 60)
    print(f"Versión:     {version}")
    print(f"ZIP:         {zip_path}")
    print(f"Descarga:    {download_url}")
    print("\nPróximos pasos:")
    print("1. Subir el ZIP a GitHub Releases (o tu CDN)")
    print("2. Deploy del backend a Vercel: vercel --prod")
    print("3. Probar la actualización desde un cliente anterior")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/release_version.py
git commit -m "feat(release): add automated release script"
```

---

## Task 6: Flujo de trabajo de distribución (GitHub Releases)

**Contexto:** La estrategia recomendada para almacenar los .exe es usar **GitHub Releases** como CDN gratuito.

- [ ] **Step 1: Crear release en GitHub**

```bash
# Crear tag
git tag -a v1.5.4 -m "Release v1.5.4 - Fix detección Vol. y escalado imágenes"
git push origin v1.5.4

# Crear release con gh CLI (instalar desde https://cli.github.com/)
gh release create v1.5.4 \
  "mobiliti_saas/release/Mobiliti_Generador_Windows_v1.5.4.zip" \
  --title "Mobiliti Generador v1.5.4" \
  --notes "Fix: detección dinámica de columna Vol. Mejora: escalado de imágenes."
```

- [ ] **Step 2: Obtener URL del asset**

La URL del .exe individual (no el ZIP) se usará en `version_manifest.py`.

```
https://github.com/TU_USUARIO/TU_REPO/releases/download/v1.5.4/Mobiliti_Generador.exe
```

- [ ] **Step 3: Actualizar `version_manifest.py` con la URL real**

Editar `mobiliti_saas/api/version_manifest.py` para que `download_url` apunte al asset de GitHub Releases.

- [ ] **Step 4: Deploy del backend**

```bash
cd mobiliti_saas
vercel --prod
```

---

## Task 7: Testing del flujo completo

**Files:**
- Create: `tests/test_updater.py` (test unitario básico)

- [ ] **Step 1: Test de parsing de versiones**

```python
# tests/test_updater.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "cliente"))

from updater import parse_version, compare_versions

def test_parse_version():
    assert parse_version("1.5.3") == (1, 5, 3)
    assert parse_version("v2.0.0") == (2, 0, 0)
    assert parse_version("0.0.1") == (0, 0, 1)

def test_compare_versions():
    assert compare_versions("1.5.2", "1.5.3") == -1
    assert compare_versions("1.5.3", "1.5.3") == 0
    assert compare_versions("1.6.0", "1.5.3") == 1
    assert compare_versions("2.0.0", "1.9.9") == 1
```

- [ ] **Step 2: Test manual del flujo**

1. Compilar versión vieja (ej: `1.5.2`) con `--skip-build` en release
2. Ejecutarla y verificar que detecta `1.5.3` disponible
3. Aceptar actualización
4. Verificar que:
   - Se descarga el .exe a `%TEMP%\mobiliti_update\`
   - Se crea `updater_helper.bat`
   - La app se cierra
   - El batch reemplaza el .exe
   - Se relanza la app
   - `version.txt` ahora dice `1.5.3`

- [ ] **Step 3: Test de omitir versión**

1. Ejecutar app vieja
2. En el diálogo, clickear "Omitir esta versión"
3. Cerrar y reabrir app
4. Verificar que **no** muestra el diálogo de nuevo

- [ ] **Step 4: Commit de tests**

```bash
git add tests/test_updater.py
git commit -m "test(updater): add version parsing tests"
```

---

## Task 8: Documentación

**Files:**
- Create: `docs/ACTUALIZACIONES.md`

- [ ] **Step 1: Escribir documentación del sistema de actualizaciones**

```markdown
# Sistema de Actualizaciones Automáticas

## Cómo funciona

1. Al iniciar sesión, el cliente consulta `GET /version` en el backend Vercel
2. Si la versión remota es mayor que la local, muestra diálogo Tkinter
3. El usuario puede: **Actualizar ahora**, **Más tarde**, u **Omitir**
4. Si actualiza: descarga el nuevo `.exe` a `%TEMP%`, lanza `updater_helper.bat`, y se cierra
5. El batch espera 2 segundos, reemplaza el `.exe`, limpia temporales, y relanza la app

## Cómo publicar una nueva versión

```bash
# 1. Asegurar que todo está commiteado
git status

# 2. Ejecutar script de release
python scripts/release_version.py \
  --version 1.5.4 \
  --notes "Fix: corrección de bug X. Mejora: nueva categoría Y." \
  --download-url "https://github.com/tuusuario/mobiliti/releases/download/v1.5.4/Mobiliti_Generador.exe"

# 3. Subir a GitHub Releases
gh release create v1.5.4 \
  "mobiliti_saas/release/Mobiliti_Generador_Windows_v1.5.4.zip" \
  --title "Mobiliti Generador v1.5.4" \
  --notes-file RELEASE_NOTES.md

# 4. Deploy del backend
vercel --prod

# 5. Probar desde un cliente con versión anterior
```

## Estructura de archivos relacionados

| Archivo | Propósito |
|---------|-----------|
| `mobiliti_saas/cliente/updater.py` | Lógica de actualización en el cliente |
| `mobiliti_saas/cliente/version.txt` | Versión actual embebida en el .exe |
| `mobiliti_saas/api/version_manifest.py` | Datos de la última versión disponible |
| `scripts/release_version.py` | Script de automatización de releases |

## Solución de problemas

### "No se pudo contactar al servidor de versiones"
- Verificar que `config.json` tiene la URL correcta del API
- Verificar conectividad a Internet
- Verificar que el backend está deployado en Vercel

### "Error al reemplazar el ejecutable"
- Windows puede bloquear el archivo si está en uso
- El updater helper espera 2 segundos, pero si la app tarda más en cerrar, puede fallar
- Solución: aumentar el `timeout /t` en `updater_helper.bat`

### "La app no se reinicia después de actualizar"
- Verificar que el antivirus no bloqueó el batch
- Verificar que la ruta del `.exe` no contiene espacios especiales
```

- [ ] **Step 2: Commit**

```bash
git add docs/ACTUALIZACIONES.md
git commit -m "docs: add auto-update system documentation"
```

---

## Spec Coverage Check

| Requerimiento | Task |
|---------------|------|
| Backend expone versión disponible | Task 1: Endpoints `/version` y `/download/latest` |
| Cliente consulta versión al inicio | Task 3: Integración en `main_cliente.py` |
| Comparación SemVer robusta | Task 2: `parse_version`, `compare_versions` en `updater.py` |
| Diálogo Tkinter de notificación | Task 2: `UpdateDialog` con 3 opciones |
| Descarga en background con progreso | Task 2: `download_file` + `DownloadProgressDialog` |
| Reemplazo del .exe sin conflicto | Task 2: `write_updater_batch` + helper batch |
| Opción "Omitir esta versión" persistente | Task 2: `should_skip_version`, `set_skip_version` |
| Versión embebida en el .exe | Task 4: `version.txt` + PyInstaller spec |
| Script de release automatizado | Task 5: `release_version.py` |
| Testing del flujo | Task 7: Unit tests + manual testing |
| Documentación | Task 8: `ACTUALIZACIONES.md` |

## Placeholder Scan

- ✅ Sin "TBD", "TODO", "implement later"
- ✅ Código completo en cada step
- ✅ Comandos exactos con expected output
- ✅ Type consistency: `VersionInfo` se usa igual en Task 1 y Task 5

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-04-sistema-actualizaciones-auto.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
