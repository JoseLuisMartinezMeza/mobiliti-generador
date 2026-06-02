"""
Entry point para PyInstaller.
Configura sys.path para encontrar los modulos empaquetados.
Soporta modo GUI (default) y modo generador (--generate).
"""
import sys
import os

if hasattr(sys, '_MEIPASS'):
    # Estamos corriendo como .exe empaquetado
    sys.path.insert(0, sys._MEIPASS)
else:
    # Desarrollo
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, base)
    sys.path.insert(0, os.path.dirname(base))  # raiz del proyecto

if len(sys.argv) > 1 and sys.argv[1] == "--generate":
    # Modo generador: ejecutar directamente sin GUI
    import json
    from types import SimpleNamespace
    from generar_cotizacion_v5_xlwings import generar_cotizacion

    args_dict = json.loads(sys.argv[2])
    args = SimpleNamespace(**args_dict)
    generar_cotizacion(args)
else:
    # Modo GUI normal
    from main_cliente import main

    if __name__ == "__main__":
        main()
