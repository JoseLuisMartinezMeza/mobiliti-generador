"""
Auto-update module for Mobiliti Desktop Client.
Self-contained; uses stdlib + tkinter only.
"""
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Constants / style
# ---------------------------------------------------------------------------
_PRIMARY = '#1a237e'
_BG = '#f8f9fa'
_TEXT = '#212529'
_FONT = 'Segoe UI'
_UPDATE_DIR = os.path.join(tempfile.gettempdir(), 'mobiliti_update')


# ---------------------------------------------------------------------------
# Path helpers (mirrored from main_cliente so this module stays self-contained)
# ---------------------------------------------------------------------------
def _get_base_dir():
    """Return the directory of the .exe or the script."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_resource_path(filename):
    """Resolve a resource: exe dir -> _MEIPASS -> cwd."""
    base = _get_base_dir()
    external = os.path.join(base, filename)
    if os.path.exists(external):
        return external
    if getattr(sys, '_MEIPASS', None):
        bundled = os.path.join(sys._MEIPASS, filename)
        if os.path.exists(bundled):
            return bundled
    return external


# ---------------------------------------------------------------------------
# Config / version helpers
# ---------------------------------------------------------------------------
def _load_config():
    """Load config.json from the base dir."""
    path = os.path.join(_get_base_dir(), 'config.json')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg):
    """Save config.json to the base dir."""
    path = os.path.join(_get_base_dir(), 'config.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _read_local_version():
    """Read version string from version.txt next to the executable."""
    path = _get_resource_path('version.txt')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            pass
    return '0.0.0'


# ---------------------------------------------------------------------------
# SemVer
# ---------------------------------------------------------------------------
def _parse_version(v):
    """Parse '1.5.3' or 'v1.5.3' -> (1, 5, 3). Missing segments default to 0."""
    v = v.strip().lstrip('v').lstrip('V')
    parts = v.split('.')
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _compare_version(a, b):
    """Return -1 if a < b, 0 if equal, 1 if a > b."""
    ta = _parse_version(a)
    tb = _parse_version(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def _fetch_json(url, timeout=10):
    """GET a JSON endpoint via urllib. Returns dict or raises on failure."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _download_file(url, dest_path, progress_callback=None, chunk_size=65536, timeout=30):
    """Download url to dest_path in chunks. progress_callback(bytes_read, total)."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        total = int(resp.headers.get('Content-Length', 0))
        read = 0
        with open(dest_path, 'wb') as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if progress_callback:
                    progress_callback(read, total)
        return read


# ---------------------------------------------------------------------------
# UI Dialogs
# ---------------------------------------------------------------------------
class UpdateDialog(tk.Toplevel):
    """Modal dialog: current vs available version + release notes + 3 actions."""

    def __init__(self, parent, current, available, notes, on_update, on_later, on_skip):
        super().__init__(parent)
        self.title('Actualización disponible')
        self.resizable(False, False)
        self.configure(bg=_BG)

        self.on_update = on_update
        self.on_later = on_later
        self.on_skip = on_skip

        self.transient(parent)
        self.grab_set()

        # Header
        tk.Label(
            self, text='Hay una nueva versión disponible',
            font=(_FONT, 14, 'bold'), fg=_PRIMARY, bg=_BG
        ).pack(pady=(16, 8), padx=24)

        # Version info
        info = f'Versión actual: {current}\nVersión disponible: {available}'
        tk.Label(self, text=info, font=(_FONT, 11), fg=_TEXT, bg=_BG, justify='left').pack(padx=24)

        # Release notes
        if notes:
            tk.Label(self, text='Notas de la versión:', font=(_FONT, 10, 'bold'),
                     fg=_TEXT, bg=_BG).pack(anchor='w', padx=24, pady=(12, 4))
            text = tk.Text(self, height=6, width=50, font=(_FONT, 10),
                           bg='white', fg=_TEXT, relief='solid', borderwidth=1)
            text.insert('1.0', notes)
            text.config(state='disabled')
            text.pack(padx=24, pady=(0, 8), fill='both', expand=False)

        # Buttons
        btn_frame = tk.Frame(self, bg=_BG)
        btn_frame.pack(pady=(8, 16), padx=24)

        ttk.Button(btn_frame, text='Actualizar ahora', command=self._update).pack(side='left', padx=4)
        ttk.Button(btn_frame, text='Más tarde', command=self._later).pack(side='left', padx=4)
        ttk.Button(btn_frame, text='Omitir esta versión', command=self._skip).pack(side='left', padx=4)

        self.update_idletasks()
        self._center_on_parent(parent)

    def _center_on_parent(self, parent):
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        w, h = self.winfo_width(), self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f'+{x}+{y}')

    def _update(self):
        self.destroy()
        self.on_update()

    def _later(self):
        self.destroy()
        self.on_later()

    def _skip(self):
        self.destroy()
        self.on_skip()


class DownloadProgressDialog(tk.Toplevel):
    """Modal dialog with a progress bar and MB counters."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Descargando actualización')
        self.resizable(False, False)
        self.configure(bg=_BG)
        self.transient(parent)
        self.grab_set()

        tk.Label(self, text='Descargando nueva versión…',
                 font=(_FONT, 12, 'bold'), fg=_PRIMARY, bg=_BG).pack(pady=(16, 8), padx=24)

        self._label = tk.Label(self, text='0.00 MB / 0.00 MB',
                               font=(_FONT, 10), fg=_TEXT, bg=_BG)
        self._label.pack(padx=24)

        self._progress = ttk.Progressbar(self, mode='determinate', length=320)
        self._progress.pack(padx=24, pady=(8, 16), fill='x')

        self.update_idletasks()
        self._center_on_parent(parent)

    def _center_on_parent(self, parent):
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        w, h = self.winfo_width(), self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f'+{x}+{y}')

    def update_progress(self, read, total):
        """Call from the download thread; schedules UI update on main thread."""
        def _set():
            self._label.config(text=f'{read / 1048576:.2f} MB / {total / 1048576:.2f} MB')
            if total > 0:
                self._progress['value'] = (read / total) * 100
            else:
                self._progress['value'] = 0
        self.after(0, _set)

    def close(self):
        self.destroy()


# ---------------------------------------------------------------------------
# Install helper
# ---------------------------------------------------------------------------
def _write_bat_and_launch(current_exe, new_exe, bat_path):
    """Write a temporary batch script that swaps the exe and relaunches."""
    bat_content = (
        '@echo off\n'
        'timeout /t 2 /nobreak >nul\n'
        f'move /Y "{new_exe}" "{current_exe}" >nul 2>&1\n'
        f'start "" "{current_exe}"\n'
        f'del /F /Q "{bat_path}" >nul 2>&1\n'
    )
    with open(bat_path, 'w', encoding='utf-8') as f:
        f.write(bat_content)

    subprocess.Popen(
        f'"{bat_path}"',
        shell=True,
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------
def check_and_prompt_update(parent_window):
    """Check for update and prompt user. Call after login."""
    cfg = _load_config()
    api_url = cfg.get('api_url', 'https://mobiliti-saas.vercel.app').rstrip('/')

    # 1. Fetch remote version
    try:
        remote = _fetch_json(f'{api_url}/version')
    except Exception:
        return  # silent on network failure

    remote_version = remote.get('version', '')
    download_url = remote.get('download_url', '')
    release_notes = remote.get('release_notes', '')

    if not remote_version or not download_url:
        return

    # 2. Compare
    local_version = _read_local_version()
    if _compare_version(remote_version, local_version) <= 0:
        return  # up to date

    # 3. Respect skip
    skip_version = cfg.get('skip_version', '')
    if _compare_version(remote_version, skip_version) == 0:
        return

    # 4. Prompt
    def do_update():
        _start_download(parent_window, download_url)

    def do_later():
        pass

    def do_skip():
        cfg['skip_version'] = remote_version
        _save_config(cfg)

    UpdateDialog(parent_window, local_version, remote_version, release_notes,
                 do_update, do_later, do_skip)


def _start_download(parent_window, download_url):
    """Start background download and show progress dialog."""
    os.makedirs(_UPDATE_DIR, exist_ok=True)
    dest = os.path.join(_UPDATE_DIR, 'Mobiliti_Generador_new.exe')

    dlg = DownloadProgressDialog(parent_window)

    def on_done(success, msg_or_path):
        def _finish():
            dlg.close()
            if success:
                _install_and_restart(msg_or_path)
            else:
                messagebox.showerror('Error', f'No se pudo descargar la actualización:\n{msg_or_path}')
        parent_window.after(0, _finish)

    def _download_thread():
        try:
            def prog(read, total):
                dlg.update_progress(read, total)

            _download_file(download_url, dest, progress_callback=prog)
            on_done(True, dest)
        except Exception as e:
            on_done(False, str(e))

    threading.Thread(target=_download_thread, daemon=True).start()


def _install_and_restart(new_exe):
    """Write batch helper, launch it, and exit the current process."""
    current_exe = os.path.join(_get_base_dir(), 'Mobiliti_Generador.exe')
    bat_path = os.path.join(_UPDATE_DIR, 'update_helper.bat')

    _write_bat_and_launch(current_exe, new_exe, bat_path)
    sys.exit(0)
