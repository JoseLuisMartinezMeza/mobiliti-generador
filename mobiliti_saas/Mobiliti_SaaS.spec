# -*- mode: python ; coding: utf-8 -*-
"""
Spec file para compilar el cliente desktop Mobiliti SaaS con PyInstaller.
El backend ahora esta en Vercel; este .exe solo contiene el generador local + login.
"""
import sys
import os

block_cipher = None

# Directorio base = donde esta este spec file (mobiliti_saas/)
base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
project_root = os.path.dirname(base_dir)

a = Analysis(
    ['cliente\\entry_point.py'],
    pathex=[base_dir, project_root],
    binaries=[],
    datas=[
        # Archivos de configuracion y recursos
        ('config.json', '.'),
        # Archivos del generador (estan en la raiz del proyecto)
        (os.path.join(project_root, 'diccionario_categorias.json'), '.'),
        (os.path.join(project_root, 'LOGO.png'), '.'),
        (os.path.join(project_root, 'Formato Cotización 2026 GDL (1).xlsx'), '.'),
        # Clasificador e imagenes
        (os.path.join(project_root, 'insertar_imagenes.py'), '.'),
        (os.path.join(project_root, 'mejorador_imagenes.py'), '.'),
        (os.path.join(project_root, 'clasificador.py'), '.'),
        (os.path.join(project_root, 'generar_cotizacion_v5_xlwings.py'), '.'),
        # GUI del cliente
        ('cliente\\main_cliente.py', '.'),
        ('cliente\\verificador.py', '.'),
    ],
    hiddenimports=[
        'wmi',
        'openpyxl',
        'xlwings',
        'rapidfuzz',
        'PIL',
        'clasificador',
        'insertar_imagenes',
        'mejorador_imagenes',
        'generar_cotizacion_v5_xlwings',
        'main_cliente',
        'verificador',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.ttk',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Mobiliti_Generador',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
