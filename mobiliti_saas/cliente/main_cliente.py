"""
Cliente Desktop Mobiliti - Generador de Cotizaciones
Requiere conexion a internet y login email/password.
Se verifica la suscripcion activa online antes de cada uso.
"""
import sys
import os
import json
import ctypes
import urllib.request
import urllib.error
import ssl
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
import threading
from datetime import datetime

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Configuracion
VERSION = "1.0.0"


def get_base_dir():
    """Obtiene el directorio base ya sea en desarrollo o como ejecutable."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_api_url():
    """Lee la URL del API desde config.json o usa el default de Vercel."""
    config_path = os.path.join(get_base_dir(), "config.json")
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                return cfg.get("api_url", "https://mobiliti-saas.vercel.app")
    except Exception:
        pass
    return "https://mobiliti-saas.vercel.app"


API_URL = get_api_url()


def get_resource_path(filename):
    """Obtiene la ruta absoluta de un recurso.
    
    Orden de busqueda:
    1. Directorio del .exe (permite archivos personalizados externos)
    2. Directorio temporal de PyInstaller (sys._MEIPASS, archivos empaquetados)
    """
    # 1. Primero buscar en el directorio del .exe (archivos externos/personalizados)
    external_path = os.path.join(get_base_dir(), filename)
    if os.path.exists(external_path):
        return external_path
    
    # 2. Fallback al bundle interno de PyInstaller
    if getattr(sys, '_MEIPASS', None):
        meipass_path = os.path.join(sys._MEIPASS, filename)
        if os.path.exists(meipass_path):
            return meipass_path
    
    # 3. Si no existe en ningun lado, devolver la ruta externa de todos modos
    # (para que el error sea claro: "archivo no encontrado en X")
    return external_path


def get_hardware_id():
    """Obtiene un ID unico del hardware (para tracking, no para bloqueo)."""
    try:
        import wmi
        c = wmi.WMI()
        cpu = c.Win32_Processor()[0].ProcessorId.strip()
        board = c.Win32_BaseBoard()[0].SerialNumber.strip()
        disk = c.Win32_DiskDrive()[0].SerialNumber.strip()
        return f"{cpu}-{board}-{disk}"
    except Exception:
        import uuid
        return str(uuid.getnode())


def api_post(endpoint, data, token=None):
    """Hace un POST al backend."""
    url = f"{API_URL}{endpoint}"
    body = json.dumps(data).encode('utf-8')
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
            return json.loads(response.read().decode('utf-8')), response.status
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        try:
            err = json.loads(error_body)
            return err, e.code
        except:
            return {"detail": f"Error HTTP {e.code}"}, e.code
    except Exception as e:
        return {"detail": f"Error de conexion: {str(e)}"}, 0


def api_get(endpoint, token):
    """Hace un GET al backend."""
    url = f"{API_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
            return json.loads(response.read().decode('utf-8')), response.status
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        try:
            err = json.loads(error_body)
            return err, e.code
        except:
            return {"detail": f"Error HTTP {e.code}"}, e.code
    except Exception as e:
        return {"detail": f"Error de conexion: {str(e)}"}, 0


def get_templates_from_api():
    """Obtiene templates disponibles."""
    try:
        req = urllib.request.Request(f"{API_URL}/templates", method="GET")
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get("templates", ["Formato Cotización 2026 GDL (1).xlsx"])
    except Exception:
        return ["Formato Cotización 2026 GDL (1).xlsx"]


def verificar_servicio_online():
    """Verifica que el backend este online. Maneja SSL correctamente en Windows."""
    try:
        req = urllib.request.Request(f"{API_URL}/health", method="GET")
        # Contexto SSL que funciona en .exe empaquetado
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


class MobilitiClient:
    def __init__(self, root):
        self.root = root
        self.root.title("Mobiliti - Generador de Cotizaciones")
        self.root.geometry("750x650")
        self.root.resizable(True, True)

        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Paleta corporativa Mobiliti
        self.COLORS = {
            'primary': '#1a237e',
            'primary_light': '#3949ab',
            'accent': '#00acc1',
            'bg': '#f8f9fa',
            'surface': '#ffffff',
            'text': '#212529',
            'text_secondary': '#6c757d',
            'border': '#dee2e6',
            'success': '#2e7d32',
            'warning': '#ed6c02',
            'error': '#c62828',
        }
        
        # Estilos globales
        self.style.configure('.', font=('Segoe UI', 10), background=self.COLORS['bg'])
        self.style.configure('TFrame', background=self.COLORS['bg'])
        self.style.configure('TLabel', font=('Segoe UI', 10), background=self.COLORS['bg'], foreground=self.COLORS['text'])
        self.style.configure('TEntry', font=('Segoe UI', 10), padding=6)
        self.style.configure('TButton', font=('Segoe UI', 10, 'bold'), padding=(20, 8))
        
        # Boton primario
        self.style.configure('Primary.TButton',
            font=('Segoe UI', 11, 'bold'),
            foreground='white',
            background=self.COLORS['primary_light'],
            padding=(24, 10))
        self.style.map('Primary.TButton',
            background=[('active', self.COLORS['primary']), ('pressed', self.COLORS['primary'])],
            foreground=[('active', 'white'), ('pressed', 'white')])
        
        # Boton secundario
        self.style.configure('Secondary.TButton',
            font=('Segoe UI', 10),
            foreground=self.COLORS['text_secondary'],
            background=self.COLORS['surface'],
            padding=(16, 6))
        self.style.map('Secondary.TButton',
            background=[('active', '#e9ecef'), ('pressed', '#dee2e6')])
        
        # LabelFrame estilizado
        self.style.configure('Card.TLabelframe',
            background=self.COLORS['surface'],
            borderwidth=1,
            relief='solid')
        self.style.configure('Card.TLabelframe.Label',
            font=('Segoe UI', 12, 'bold'),
            background=self.COLORS['surface'],
            foreground=self.COLORS['primary'])
        
        # Combobox
        self.style.configure('TCombobox', font=('Segoe UI', 10), padding=4)
        
        # Progressbar
        self.style.configure('TProgressbar',
            thickness=4,
            background=self.COLORS['accent'],
            troughcolor=self.COLORS['border'])
        
        self.root.configure(bg=self.COLORS['bg'])

        self.token = None
        self.user_info = None
        self.suscripcion_info = None
        self.templates = get_templates_from_api()

        # Verificar que el servicio este online
        if not verificar_servicio_online():
            self.show_offline_screen()
            return

        self.show_login_screen()

    def show_offline_screen(self):
        """Pantalla cuando no hay conexion al servidor. Permite reintentar."""
        self.clear_window()

        frame = ttk.Frame(self.root, padding="40")
        frame.pack(expand=True, fill='both')

        self._add_logo(frame)
        
        ttk.Label(frame, text="Sin conexion", font=('Segoe UI', 20, 'bold'), 
                  foreground=self.COLORS['error']).pack(pady=(30, 10))
        ttk.Label(frame, text="No se pudo conectar al servidor Mobiliti.", 
                  font=('Segoe UI', 12)).pack(pady=(0, 5))
        ttk.Label(frame, text="Verifica tu conexion a internet y que el servidor este activo.", 
                  font=('Segoe UI', 10), foreground=self.COLORS['text_secondary']).pack(pady=(0, 5))
        ttk.Label(frame, text=f"URL: {API_URL}", 
                  font=('Segoe UI', 9), foreground=self.COLORS['text_secondary']).pack(pady=(0, 25))

        ttk.Button(frame, text="Reintentar", command=self.retry_connection, style='Primary.TButton').pack(pady=(10, 5))
        ttk.Button(frame, text="Salir", command=self.root.quit, style='Secondary.TButton').pack(pady=(10, 0))

    def retry_connection(self):
        """Reintentar conexion al servidor."""
        if verificar_servicio_online():
            self.show_login_screen()
        else:
            messagebox.showerror("Sin conexion", "Aun no se puede conectar al servidor.\n\nVerifica tu conexion a internet.")

    def _add_logo(self, parent, max_height=80):
        """Agrega el logo de Mobiliti si esta disponible."""
        if not HAS_PIL:
            ttk.Label(parent, text="MOBILITI", font=('Segoe UI', 28, 'bold'),
                     foreground=self.COLORS['primary']).pack(pady=(0, 5))
            return
        try:
            logo_path = get_resource_path("LOGO.png")
            if os.path.exists(logo_path):
                img = Image.open(logo_path)
                ratio = img.width / img.height
                new_height = max_height
                new_width = int(new_height * ratio)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                lbl = ttk.Label(parent, image=photo, background=self.COLORS['bg'])
                lbl.image = photo  # keep reference
                lbl.pack(pady=(0, 10))
            else:
                ttk.Label(parent, text="MOBILITI", font=('Segoe UI', 28, 'bold'),
                         foreground=self.COLORS['primary']).pack(pady=(0, 5))
        except Exception:
            ttk.Label(parent, text="MOBILITI", font=('Segoe UI', 28, 'bold'),
                     foreground=self.COLORS['primary']).pack(pady=(0, 5))

    def _add_footer(self, parent):
        """Agrega un footer sutil con version."""
        footer = ttk.Frame(parent)
        footer.pack(fill='x', side='bottom', pady=(20, 0))
        ttk.Label(footer, text=f"Mobiliti Generador v{VERSION}  2026",
                  font=('Segoe UI', 8), foreground=self.COLORS['text_secondary']).pack(side='right')

    def show_login_screen(self):
        """Pantalla de login con email/password."""
        self.clear_window()

        frame = ttk.Frame(self.root, padding="40")
        frame.pack(expand=True, fill='both')

        self._add_logo(frame)
        ttk.Label(frame, text="Generador de Cotizaciones", 
                  font=('Segoe UI', 14), foreground=self.COLORS['text_secondary']).pack(pady=(0, 30))

        # Tarjeta de login
        card = ttk.LabelFrame(frame, text="  Iniciar Sesion  ", padding="25", style='Card.TLabelframe')
        card.pack(fill='x', padx=20, pady=(0, 10))

        ttk.Label(card, text="Email", font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 4))
        self.email_entry = ttk.Entry(card, width=40, font=('Segoe UI', 11))
        self.email_entry.pack(fill='x', pady=(0, 14))
        self.email_entry.focus()

        ttk.Label(card, text="Contraseña", font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 4))
        self.password_entry = ttk.Entry(card, width=40, font=('Segoe UI', 11), show="*")
        self.password_entry.pack(fill='x', pady=(0, 10))
        
        # Vincular Enter con login
        self.password_entry.bind('<Return>', lambda e: self.do_login())

        self.status_label = ttk.Label(card, text="", font=('Segoe UI', 10))
        self.status_label.pack(pady=(5, 0))

        ttk.Button(card, text="Entrar", command=self.do_login, style='Primary.TButton').pack(fill='x', pady=(15, 0))

        self._add_footer(frame)

        # Cargar credenciales guardadas
        saved = self.load_saved_credentials()
        if saved:
            self.email_entry.insert(0, saved.get("email", ""))

    def do_login(self):
        email = self.email_entry.get().strip()
        password = self.password_entry.get()

        if not email or not password:
            self.status_label.config(text="Ingresa email y contraseña", foreground='#e65100')
            return

        self.status_label.config(text="Conectando...", foreground='#1565c0')
        self.root.update()

        def login():
            data = {
                "email": email,
                "password": password,
                "hardware_id": get_hardware_id()
            }
            result, status = api_post("/login", data)
            self.root.after(0, lambda: self.on_login_result(result, status, email, password))

        threading.Thread(target=login, daemon=True).start()

    def on_login_result(self, result, status, email, password):
        if status == 200:
            self.token = result.get("access_token")
            usuario = result.get("usuario", {})
            self.user_info = {
                "nombre": usuario.get("nombre", ""),
                "email": usuario.get("email", ""),
            }
            self.suscripcion_info = result.get("suscripcion", {})
            self.save_credentials(email, password)
            self.status_label.config(text=f"Bienvenido, {self.user_info['nombre']}!", foreground='#2e7d32')
            self.root.after(1000, self.show_main_screen)
        else:
            msg = result.get("detail", "Error de login")
            self.status_label.config(text=msg, foreground='#c62828')

    def show_main_screen(self):
        """Pantalla principal del generador."""
        self.clear_window()

        # Menu
        menubar = tk.Menu(self.root, bg=self.COLORS['surface'], fg=self.COLORS['text'],
                         activebackground=self.COLORS['primary_light'], activeforeground='white')
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0, bg=self.COLORS['surface'], fg=self.COLORS['text'],
                           activebackground=self.COLORS['primary_light'], activeforeground='white')
        file_menu.add_command(label="Nueva Cotizacion", command=self.reset_form)
        file_menu.add_separator()
        file_menu.add_command(label="Abrir carpeta de historial", command=self.open_history)
        file_menu.add_separator()
        file_menu.add_command(label="Cerrar sesion", command=self.logout)
        file_menu.add_command(label="Salir", command=self.root.quit)
        menubar.add_cascade(label="Archivo", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0, bg=self.COLORS['surface'], fg=self.COLORS['text'],
                           activebackground=self.COLORS['primary_light'], activeforeground='white')
        help_menu.add_command(label="Verificar actualizaciones", command=self.check_updates)
        help_menu.add_command(label="Acerca de", command=self.show_about)
        menubar.add_cascade(label="Ayuda", menu=help_menu)

        # Scrollable canvas para contenido
        container = ttk.Frame(self.root)
        container.pack(fill='both', expand=True)
        
        canvas = tk.Canvas(container, bg=self.COLORS['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient='vertical', command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding="30")
        
        scrollable_frame.bind(
            '<Configure>',
            lambda e: canvas.configure(scrollregion=canvas.bbox('all'))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor='nw', width=750)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), 'units')
        canvas.bind_all('<MouseWheel>', _on_mousewheel)

        # Header visual
        header = ttk.Frame(scrollable_frame)
        header.pack(fill='x', pady=(0, 20))
        
        nombre = self.user_info.get("nombre", "Usuario") if self.user_info else "Usuario"
        ttk.Label(header, text=f"Hola, {nombre}", 
                  font=('Segoe UI', 14, 'bold')).pack(side='left')

        if self.suscripcion_info:
            estado = self.suscripcion_info.get("estado", "")
            fecha_fin = self.suscripcion_info.get("fecha_fin", "")
            color = self.COLORS['success'] if estado == 'activa' else self.COLORS['error']
            badge_frame = ttk.Frame(header)
            badge_frame.pack(side='right')
            ttk.Label(badge_frame, text=f"  {estado.upper()}  ", 
                      font=('Segoe UI', 9, 'bold'), foreground='white',
                      background=color).pack(side='left', padx=(0, 8))
            ttk.Label(badge_frame, text=f"Vence: {fecha_fin[:10] if fecha_fin else 'N/A'}", 
                      font=('Segoe UI', 9), foreground=self.COLORS['text_secondary']).pack(side='left')

        # Separador
        ttk.Separator(scrollable_frame, orient='horizontal').pack(fill='x', pady=(0, 20))

        ttk.Label(scrollable_frame, text="Generar Nueva Cotizacion", 
                  font=('Segoe UI', 18, 'bold'), foreground=self.COLORS['primary']).pack(anchor='w', pady=(0, 20))

        # Formulario en tarjeta
        form_frame = ttk.LabelFrame(scrollable_frame, text="  Datos del Proyecto  ", 
                                     padding="20", style='Card.TLabelframe')
        form_frame.pack(fill='x', pady=(0, 20))

        # Archivo fuente
        ttk.Label(form_frame, text="Quotation del proveedor *", font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky='w', pady=5)
        source_frame = ttk.Frame(form_frame)
        source_frame.grid(row=0, column=1, sticky='ew', pady=5, padx=(15, 0))
        source_frame.columnconfigure(0, weight=1)

        self.source_var = tk.StringVar()
        ttk.Entry(source_frame, textvariable=self.source_var).grid(row=0, column=0, sticky='ew')
        ttk.Button(source_frame, text="Examinar...", command=self.browse_source, style='Secondary.TButton').grid(row=0, column=1, padx=(8, 0))

        # Template
        ttk.Label(form_frame, text="Template", font=('Segoe UI', 10, 'bold')).grid(row=1, column=0, sticky='w', pady=5)
        self.template_var = tk.StringVar(value=self.templates[0] if self.templates else "")
        template_combo = ttk.Combobox(form_frame, textvariable=self.template_var,
                                       values=self.templates, state='readonly', width=45)
        template_combo.grid(row=1, column=1, sticky='w', pady=5, padx=(15, 0))

        # Numero de cotizacion
        ttk.Label(form_frame, text="No. Cotizacion", font=('Segoe UI', 10, 'bold')).grid(row=2, column=0, sticky='w', pady=5)
        self.cot_var = tk.StringVar(value="100-00000")
        ttk.Entry(form_frame, textvariable=self.cot_var, width=25).grid(row=2, column=1, sticky='w', pady=5, padx=(15, 0))

        # Descuento %
        ttk.Label(form_frame, text="Descuento %", font=('Segoe UI', 10, 'bold')).grid(row=3, column=0, sticky='w', pady=5)
        self.descuento_var = tk.StringVar(value="30")
        desc_frame = ttk.Frame(form_frame)
        desc_frame.grid(row=3, column=1, sticky='w', pady=5, padx=(15, 0))
        ttk.Entry(desc_frame, textvariable=self.descuento_var, width=10).pack(side='left')
        ttk.Label(desc_frame, text="%  (ej: 30 = 30% descuento)", font=('Segoe UI', 9), foreground=self.COLORS['text_secondary']).pack(side='left', padx=(8, 0))

        # Proyecto
        ttk.Label(form_frame, text="Proyecto", font=('Segoe UI', 10, 'bold')).grid(row=4, column=0, sticky='w', pady=5)
        self.proy_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.proy_var, width=50).grid(row=4, column=1, sticky='ew', pady=5, padx=(15, 0))

        # Cliente
        ttk.Label(form_frame, text="Cliente", font=('Segoe UI', 10, 'bold')).grid(row=5, column=0, sticky='w', pady=5)
        self.cliente_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.cliente_var, width=50).grid(row=5, column=1, sticky='ew', pady=5, padx=(15, 0))

        # Correo
        ttk.Label(form_frame, text="Correo", font=('Segoe UI', 10, 'bold')).grid(row=6, column=0, sticky='w', pady=5)
        self.correo_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.correo_var, width=50).grid(row=6, column=1, sticky='ew', pady=5, padx=(15, 0))

        # Telefono
        ttk.Label(form_frame, text="Telefono", font=('Segoe UI', 10, 'bold')).grid(row=7, column=0, sticky='w', pady=5)
        self.tel_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.tel_var, width=30).grid(row=7, column=1, sticky='w', pady=5, padx=(15, 0))

        # Direccion
        ttk.Label(form_frame, text="Direccion", font=('Segoe UI', 10, 'bold')).grid(row=8, column=0, sticky='w', pady=5)
        self.dir_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.dir_var, width=50).grid(row=8, column=1, sticky='ew', pady=5, padx=(15, 0))

        # Razon social
        ttk.Label(form_frame, text="Razon Social", font=('Segoe UI', 10, 'bold')).grid(row=9, column=0, sticky='w', pady=5)
        self.razon_var = tk.StringVar()
        ttk.Entry(form_frame, textvariable=self.razon_var, width=50).grid(row=9, column=1, sticky='ew', pady=5, padx=(15, 0))

        form_frame.columnconfigure(1, weight=1)

        # Boton generar prominente
        btn_frame = ttk.Frame(scrollable_frame)
        btn_frame.pack(fill='x', pady=(0, 20))
        
        gen_btn = ttk.Button(btn_frame, text="  Generar Cotizacion  ", command=self.generate_quote, style='Primary.TButton')
        gen_btn.pack(side='left')
        
        ttk.Button(btn_frame, text="Limpiar", command=self.reset_form, style='Secondary.TButton').pack(side='left', padx=(10, 0))

        # Barra de progreso y log
        self.progress = ttk.Progressbar(scrollable_frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 10))
        self.progress.stop()
        self.progress.pack_forget()

        log_frame = ttk.LabelFrame(scrollable_frame, text="  Progreso  ", padding="10", style='Card.TLabelframe')
        log_frame.pack(fill='both', expand=True, pady=(0, 10))
        
        self.log_text = tk.Text(log_frame, height=10, wrap='word', font=('Consolas', 9), 
                                 bg='#f8f9fa', fg=self.COLORS['text'], relief='flat',
                                 highlightthickness=1, highlightbackground=self.COLORS['border'])
        self.log_text.pack(fill='both', expand=True, side='left')

        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side='right', fill='y')
        self.log_text.config(yscrollcommand=log_scroll.set)

        self.log(f"Cliente Mobiliti iniciado correctamente")
        self.log(f"Usuario: {self.user_info.get('email', '')}")
        if self.suscripcion_info:
            self.log(f"Suscripcion: {self.suscripcion_info.get('estado', '')} | Plan: {self.suscripcion_info.get('plan', '')}")
        
        self._add_footer(scrollable_frame)

    def browse_source(self):
        filename = filedialog.askopenfilename(
            title="Seleccionar Quotation",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
        )
        if filename:
            self.source_var.set(filename)

    def generate_quote(self):
        source = self.source_var.get()
        if not source or not os.path.exists(source):
            messagebox.showerror("Error", "Selecciona un archivo quotation valido")
            return

        # Verificar suscripcion online antes de generar
        self.log("Verificando suscripcion online...")
        result, status = api_post("/verificar-sesion", {}, token=self.token)
        if status != 200:
            msg = result.get("detail", "Error de verificacion")
            self.log(f"ERROR: {msg}")
            messagebox.showerror("Acceso denegado", f"{msg}\n\nContacta al administrador.")
            return

        template = self.template_var.get()
        cot = self.cot_var.get()
        proy = self.proy_var.get()
        cliente = self.cliente_var.get()
        correo = self.correo_var.get()
        tel = self.tel_var.get()
        direc = self.dir_var.get()
        razon = self.razon_var.get()
        descuento = self.descuento_var.get().strip()

        self.progress.pack(fill='x', pady=(10, 5))
        self.progress.start()
        self.log("\n" + "="*50)
        self.log("Iniciando generacion de cotizacion...")

        def run():
            try:
                base_dir = get_base_dir()
                script = get_resource_path("generar_cotizacion_v5_xlwings.py")
                template_path = get_resource_path(template)

                if not os.path.exists(template_path):
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    template_path = os.path.join(os.path.dirname(os.path.dirname(script_dir)), template)

                output_name = f"Cotizacion_{proy.replace(' ', '_') if proy else 'Proyecto'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                output_dir = os.path.join(base_dir, "historial")
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, output_name)

                self.log(f"Source: {source}")
                self.log(f"Template: {template_path}")
                self.log(f"Output: {output_path}")

                if getattr(sys, 'frozen', False):
                    # Modo ejecutable: usar el mismo .exe con --generate para crear un proceso limpio
                    import json
                    args_dict = {
                        "source": source,
                        "template": template_path,
                        "output": output_path,
                        "cotizacion": cot,
                        "proyecto": proy,
                        "cliente": cliente,
                        "correo": correo,
                        "telefono": tel,
                        "direccion": direc,
                        "razon_social": razon,
                        "descuento": descuento,
                    }
                    cmd = [sys.executable, "--generate", json.dumps(args_dict)]
                else:
                    # Modo desarrollo: usar subprocess con python
                    cmd = [
                        sys.executable, script,
                        "--source", source,
                        "--template", template_path,
                        "--output", output_path,
                        "--cotizacion", cot,
                        "--proyecto", proy,
                        "--cliente", cliente,
                        "--correo", correo,
                        "--telefono", tel,
                        "--direccion", direc,
                        "--razon_social", razon,
                        "--descuento", descuento,
                    ]

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=base_dir
                )

                for line in process.stdout:
                    self.root.after(0, lambda l=line: self.log(l.rstrip()))

                process.wait()

                if process.returncode == 0 and os.path.exists(output_path):
                    self.root.after(0, lambda: self.on_generation_success(output_path))
                else:
                    self.root.after(0, lambda: self.on_generation_error("Error en la generacion"))

            except Exception as e:
                self.root.after(0, lambda: self.on_generation_error(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def on_generation_success(self, output_path):
        self.progress.stop()
        self.progress.pack_forget()
        self.log(f"\nCotizacion generada exitosamente!")
        self.log(f"{output_path}")

        if messagebox.askyesno("Exito", f"Cotizacion generada.\n\nAbrir el archivo?"):
            os.startfile(output_path)

    def on_generation_error(self, error):
        self.progress.stop()
        self.progress.pack_forget()
        self.log(f"\nError: {error}")
        messagebox.showerror("Error", f"Error al generar cotizacion:\n{error}")

    def log(self, message):
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')

    def reset_form(self):
        self.source_var.set("")
        self.proy_var.set("")
        self.cliente_var.set("")
        self.correo_var.set("")
        self.tel_var.set("")
        self.dir_var.set("")
        self.razon_var.set("")
        self.log_text.delete(1.0, 'end')
        self.log("Formulario reiniciado")

    def open_history(self):
        history_dir = os.path.join(get_base_dir(), "historial")
        os.makedirs(history_dir, exist_ok=True)
        os.startfile(history_dir)

    def logout(self):
        self.token = None
        self.user_info = None
        self.suscripcion_info = None
        self.show_login_screen()

    def check_updates(self):
        try:
            req = urllib.request.Request(f"{API_URL}/version")
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get("version") != VERSION:
                    messagebox.showinfo("Actualizacion",
                        f"Nueva version disponible: {data.get('version')}\n"
                        f"Contacta al administrador para descargar.")
                else:
                    messagebox.showinfo("Actualizacion", "Tienes la ultima version.")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo verificar actualizaciones:\n{str(e)}")

    def show_about(self):
        nombre = self.user_info.get("nombre", "") if self.user_info else ""
        email = self.user_info.get("email", "") if self.user_info else ""
        suscripcion = self.suscripcion_info.get("estado", "") if self.suscripcion_info else ""
        messagebox.showinfo("Acerca de",
            f"Mobiliti - Generador de Cotizaciones\n"
            f"Version: {VERSION}\n"
            f"Usuario: {nombre}\n"
            f"Email: {email}\n"
            f"Suscripcion: {suscripcion}\n"
            f"2026 Mobiliti")

    def clear_window(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def load_saved_credentials(self):
        try:
            cred_file = os.path.join(get_base_dir(), "credentials.json")
            if os.path.exists(cred_file):
                with open(cred_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return None

    def save_credentials(self, email, password):
        try:
            cred_file = os.path.join(get_base_dir(), "credentials.json")
            with open(cred_file, 'w') as f:
                json.dump({"email": email, "password": password}, f)
        except:
            pass


def main():
    root = tk.Tk()

    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

    app = MobilitiClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
