"""
Auto-updater para Mobiliti Generador.
Self-contained: usa solo stdlib + tkinter.
"""

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.request
from tkinter import ttk, messagebox

if sys.platform == "win32":
    import tkinter as tk
else:
    import tkinter as tk

_UPDATE_DIR = os.path.join(tempfile.gettempdir(), "mobiliti_update")
_PRIMARY = "#1a237e"
_BG = "#f8f9fa"
_FONT = ("Segoe UI", 10)


def _load_config():
    try:
        cfg_path = os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__), "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"api_url": "https://verceldeploy-pied.vercel.app"}


def _read_local_version():
    try:
        base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
        vfile = os.path.join(base, "version.txt")
        with open(vfile, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _parse_version(v):
    parts = v.strip().lstrip("v").split(".")
    nums = [int(p) for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _compare_version(a, b):
    av = _parse_version(a)
    bv = _parse_version(b)
    if av < bv:
        return -1
    elif av > bv:
        return 1
    return 0


def _fetch_json(url, timeout=10):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "MobilitiUpdater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download_file(url, dest, progress_callback=None, chunk_size=65536):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "MobilitiUpdater/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
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


def _write_bat_and_launch(current_exe, new_exe, bat_path):
    bat = (
        "@echo off\n"
        "timeout /t 2 /nobreak >nul\n"
        f'move /Y "{new_exe}" "{current_exe}" >nul 2>&1\n'
        f'start "" "{current_exe}"\n'
        f'del /F /Q "{bat_path}" >nul 2>&1\n'
    )
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat)
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
    sys.exit(0)


class UpdateDialog:
    def __init__(self, parent, current, remote, notes):
        self.result = None
        self.win = tk.Toplevel(parent)
        self.win.title("Actualizacion disponible")
        self.win.configure(bg=_BG)
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        tk.Label(self.win, text="Hay una nueva version de Mobiliti",
                 font=("Segoe UI", 12, "bold"), bg=_BG, fg=_PRIMARY).pack(pady=(16, 4))
        tk.Label(self.win, text=f"Version actual: {current}", bg=_BG, font=_FONT).pack()
        tk.Label(self.win, text=f"Nueva version: {remote}", bg=_BG, font=_FONT).pack()

        if notes:
            text = tk.Text(self.win, height=4, width=50, font=("Segoe UI", 9), wrap=tk.WORD)
            text.insert(tk.END, notes)
            text.config(state=tk.DISABLED)
            text.pack(padx=16, pady=8)

        btn_frame = tk.Frame(self.win, bg=_BG)
        btn_frame.pack(pady=12)

        ttk.Button(btn_frame, text="Actualizar ahora",
                   command=self._on_update).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Mas tarde",
                   command=self._on_later).pack(side=tk.LEFT, padx=4)

        self.win.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.win.winfo_height()) // 2
        self.win.geometry(f"+{x}+{y}")

        parent.wait_window(self.win)

    def _on_update(self):
        self.result = "update"
        self.win.destroy()

    def _on_later(self):
        self.result = "later"
        self.win.destroy()


class DownloadProgressDialog:
    def __init__(self, parent):
        self.win = tk.Toplevel(parent)
        self.win.title("Descargando actualizacion...")
        self.win.configure(bg=_BG)
        self.win.resizable(False, False)
        self.win.transient(parent)

        self.label = tk.Label(self.win, text="Descargando...", bg=_BG, font=_FONT)
        self.label.pack(pady=(16, 4))

        self.progress = ttk.Progressbar(self.win, length=300, mode="determinate")
        self.progress.pack(padx=16, pady=8)

        self.win.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.win.winfo_height()) // 2
        self.win.geometry(f"+{x}+{y}")

    def update(self, downloaded, total):
        if total > 0:
            pct = int(downloaded / total * 100)
            self.progress["value"] = pct
            mb = downloaded / (1024 * 1024)
            self.label.config(text=f"Descargando... {mb:.1f} MB")
        self.win.update_idletasks()

    def close(self):
        self.win.destroy()


def check_and_prompt_update(parent_window):
    """Entry point. Consulta version remota y muestra dialogo si hay update."""
    cfg = _load_config()
    api_url = cfg.get("api_url", "https://verceldeploy-pied.vercel.app").rstrip("/")

    local_version = _read_local_version()

    try:
        remote = _fetch_json(f"{api_url}/version")
    except Exception as e:
        return

    remote_version = remote.get("version", "0.0.0")
    if _compare_version(local_version, remote_version) >= 0:
        return

    dlg = UpdateDialog(
        parent_window,
        current=local_version,
        remote=remote_version,
        notes=remote.get("release_notes", ""),
    )

    if dlg.result != "update":
        return

    download_url = remote.get("download_url", "")
    if not download_url:
        messagebox.showerror("Error", "No se encontro URL de descarga.", parent=parent_window)
        return

    os.makedirs(_UPDATE_DIR, exist_ok=True)
    current_exe = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    new_exe = os.path.join(_UPDATE_DIR, "Mobiliti_Generador.exe.new")
    bat_path = os.path.join(_UPDATE_DIR, "update.bat")

    progress = DownloadProgressDialog(parent_window)

    def _download():
        try:
            _download_file(download_url, new_exe, progress_callback=progress.update)
            progress.close()
            _write_bat_and_launch(current_exe, new_exe, bat_path)
        except Exception as e:
            progress.close()
            messagebox.showerror("Error", f"Fallo la descarga:\n{e}", parent=parent_window)

    threading.Thread(target=_download, daemon=True).start()
