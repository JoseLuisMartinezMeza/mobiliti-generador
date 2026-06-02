#!/usr/bin/env python3
"""
verificador.py
==============
Cliente de verificacion de licencia Mobiliti SaaS.
Se ejecuta antes de lanzar el generador de cotizaciones.
"""

import hashlib
import json
import os
import sys
import subprocess
import urllib.request
import urllib.error

# CONFIGURACION (cambiar en produccion)
API_URL = "http://localhost:8000"
LICENCIA_FILE = os.path.join(os.path.expanduser("~"), ".mobiliti_license.json")


def get_hardware_id():
    """Genera un ID unico basado en hardware de la PC."""
    import uuid
    # Combina varios identificadores de hardware
    mac = uuid.getnode()
    machine = os.environ.get('COMPUTERNAME', '')
    user = os.environ.get('USERNAME', '')
    raw = f"{mac}-{machine}-{user}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def guardar_licencia(clave):
    with open(LICENCIA_FILE, 'w') as f:
        json.dump({"clave": clave}, f)


def cargar_licencia():
    if os.path.exists(LICENCIA_FILE):
        with open(LICENCIA_FILE, 'r') as f:
            return json.load(f).get("clave")
    return None


def verificar_licencia_online(clave, hwid):
    """Verifica la licencia contra el servidor SaaS."""
    data = json.dumps({"clave": clave, "hardware_id": hwid}).encode()
    req = urllib.request.Request(
        f"{API_URL}/verificar-licencia",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            return True, result.get("expiracion", "N/A")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
            return False, err.get("detail", "Error desconocido")
        except:
            return False, f"Error HTTP {e.code}"
    except Exception as e:
        return False, f"Error de conexion: {str(e)}"


def main():
    print("=" * 60)
    print("MOBILITI - Verificacion de Licencia")
    print("=" * 60)
    
    hwid = get_hardware_id()
    
    # Intentar cargar licencia guardada
    clave = cargar_licencia()
    
    if not clave:
        clave = input("\nIntroduce tu clave de licencia: ").strip()
        if not clave:
            print("ERROR: Se requiere una clave de licencia.")
            sys.exit(1)
        guardar_licencia(clave)
    
    print(f"\nVerificando licencia...")
    valida, mensaje = verificar_licencia_online(clave, hwid)
    
    if valida:
        print(f"✅ Licencia valida. Expira: {mensaje}")
        print("\nLanzando Generador de Cotizaciones Mobiliti...\n")
        
        # Importar y ejecutar el generador
        try:
            import generar_cotizacion_v5_xlwings as gen
            import argparse
            
            # Parsear argumentos desde sys.argv
            parser = argparse.ArgumentParser()
            parser.add_argument('--source', '-s', required=True)
            parser.add_argument('--template', '-t', default='Formato Cotización 2026 GDL (1).xlsx')
            parser.add_argument('--output', '-o', default=None)
            parser.add_argument('--cotizacion', '-n', default='100-00000')
            parser.add_argument('--proyecto', '-p', default='')
            parser.add_argument('--cliente', '-c', default='')
            parser.add_argument('--correo', '-e', default='')
            parser.add_argument('--telefono', '-tel', default='')
            parser.add_argument('--direccion', '-d', default='')
            parser.add_argument('--razon_social', '-r', default='')
            
            args = parser.parse_args()
            gen.generar_cotizacion(args)
        except Exception as e:
            print(f"ERROR al ejecutar el generador: {e}")
            sys.exit(1)
    else:
        print(f"❌ LICENCIA INVALIDA: {mensaje}")
        print("\nContacta a Mobiliti para renovar tu suscripcion.")
        print(f"Hardware ID: {hwid}")
        sys.exit(1)


if __name__ == "__main__":
    main()
