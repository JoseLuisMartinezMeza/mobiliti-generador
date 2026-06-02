#!/usr/bin/env python3
"""
Generador de Cotizaciones Mobiliti - MODO INTERACTIVO
======================================================
Solo ejecuta este archivo y sigue las instrucciones.
"""

import os
import subprocess
import sys
from datetime import datetime

# Asegurar que podemos importar el script principal
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def limpiar_pantalla():
    os.system('cls' if os.name == 'nt' else 'clear')


def preguntar(mensaje, default=""):
    if default:
        respuesta = input(f"{mensaje} [{default}]: ").strip()
        return respuesta if respuesta else default
    return input(f"{mensaje}: ").strip()


def listar_excels():
    """Lista archivos .xlsx en la carpeta actual."""
    archivos = [f for f in os.listdir(SCRIPT_DIR) if f.endswith('.xlsx') and not f.startswith('~')]
    return archivos


def seleccionar_archivo(mensaje, filtro=None, default=None):
    """Muestra archivos Excel y permite seleccionar uno."""
    archivos = listar_excels()
    if filtro:
        archivos = [f for f in archivos if filtro.lower() in f.lower()]
    
    print(f"\n{mensaje}")
    print("-" * 50)
    
    if not archivos:
        print("  No se encontraron archivos .xlsx")
        return input("  Escribe la ruta manualmente: ").strip()
    
    for i, archivo in enumerate(archivos, 1):
        marker = " <-- SUGERIDO" if default and default in archivo else ""
        print(f"  {i}. {archivo}{marker}")
    
    print(f"  0. Escribir ruta manualmente")
    
    while True:
        try:
            opcion = input("\nSelecciona una opción: ").strip()
            if opcion == "0":
                return input("Escribe la ruta: ").strip()
            idx = int(opcion) - 1
            if 0 <= idx < len(archivos):
                return archivos[idx]
        except ValueError:
            pass
        print("Opción inválida. Intenta de nuevo.")


def main():
    limpiar_pantalla()
    
    print("=" * 60)
    print("  GENERADOR DE COTIZACIONES MOBILITI")
    print("  Modo Interactivo")
    print("=" * 60)
    print()
    print("Este asistente te guiará paso a paso.")
    print("Solo necesitas tu archivo de Quotation del proveedor.")
    print()
    
    # --- PASO 1: Seleccionar archivo fuente ---
    input("Presiona ENTER para comenzar...")
    limpiar_pantalla()
    
    print("=== PASO 1/6: Archivo fuente (Quotation) ===")
    print("Este es el archivo del proveedor con los productos.")
    print("Busca el que tiene la hoja 'Quotation' con los items.")
    print()
    
    source = seleccionar_archivo(
        "Archivos Excel disponibles:",
        filtro="quotation",
        default="Quotation"
    )
    print(f"\n[OK] Seleccionado: {source}")
    
    # --- PASO 2: Seleccionar template ---
    input("\nPresiona ENTER para continuar...")
    limpiar_pantalla()
    
    print("=== PASO 2/6: Plantilla Mobiliti ===")
    print("Este es el archivo 'Formato Cotización 2026 GDL'.")
    print("Debe tener las hojas: Cotizacion, Mobiliti, etc.")
    print()
    
    template = seleccionar_archivo(
        "Archivos Excel disponibles:",
        filtro="formato",
        default="Formato"
    )
    print(f"\n[OK] Seleccionado: {template}")
    
    # --- PASO 3: Nombre de salida ---
    input("\nPresiona ENTER para continuar...")
    limpiar_pantalla()
    
    print("=== PASO 3/6: Archivo de salida ===")
    default_output = f"Cotizacion_{datetime.now().strftime('%Y%m%d')}.xlsx"
    output = preguntar("Nombre del archivo generado", default_output)
    if not output.endswith('.xlsx'):
        output += '.xlsx'
    print(f"\n[OK] Se guardará como: {output}")
    
    # --- PASO 4: Datos del proyecto ---
    input("\nPresiona ENTER para continuar...")
    limpiar_pantalla()
    
    print("=== PASO 4/6: Datos del proyecto ===")
    cotizacion = preguntar("Número de cotización", "100-00000")
    proyecto = preguntar("Nombre del proyecto")
    
    # --- PASO 5: Datos del cliente ---
    input("\nPresiona ENTER para continuar...")
    limpiar_pantalla()
    
    print("=== PASO 5/6: Datos del cliente ===")
    cliente = preguntar("Nombre del cliente")
    correo = preguntar("Correo electrónico")
    telefono = preguntar("Teléfono")
    direccion = preguntar("Dirección")
    razon_social = preguntar("Razón social")
    
    # --- PASO 6: Confirmar y ejecutar ---
    input("\nPresiona ENTER para continuar...")
    limpiar_pantalla()
    
    print("=" * 60)
    print("  RESUMEN")
    print("=" * 60)
    print(f"  Fuente:      {source}")
    print(f"  Plantilla:   {template}")
    print(f"  Salida:      {output}")
    print(f"  Cotización:  {cotizacion}")
    print(f"  Proyecto:    {proyecto}")
    print(f"  Cliente:     {cliente}")
    print(f"  Correo:      {correo}")
    print(f"  Teléfono:    {telefono}")
    print(f"  Dirección:   {direccion}")
    print(f"  Razón Soc.:  {razon_social}")
    print("=" * 60)
    
    confirmar = input("\n¿Todo correcto? (s/n): ").strip().lower()
    if confirmar not in ('s', 'si', 'yes', 'y'):
        print("\nOperación cancelada.")
        sys.exit(0)
    
    # --- EJECUTAR ---
    print("\n" + "=" * 60)
    print("  GENERANDO COTIZACIÓN...")
    print("=" * 60)
    
    # Importar y ejecutar
    from generar_cotizacion_v2 import generar_cotizacion
    
    class Args:
        pass
    
    args = Args()
    args.source = source
    args.template = template
    args.output = output
    args.cotizacion = cotizacion
    args.proyecto = proyecto
    args.cliente = cliente
    args.correo = correo
    args.telefono = telefono
    args.direccion = direccion
    args.razon_social = razon_social
    
    try:
        generar_cotizacion(args)
        print(f"\n[OK] Archivo generado: {os.path.join(SCRIPT_DIR, output)}")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("  ¿Qué sigue?")
    print("=" * 60)
    print("  1. Abre el archivo en Excel")
    print("  2. Revisa la hoja 'Mobiliti' (ya está llena)")
    print("  3. Revisa la hoja 'Cotizacion' (fórmulas + imágenes)")
    print("  4. Ajusta descuentos en columna G si es necesario")
    print("  5. Guarda como PDF si lo necesitas")
    print("=" * 60)
    
    input("\nPresiona ENTER para salir...")


if __name__ == '__main__':
    main()
