#!/usr/bin/env python3
"""
clasificador.py
===============
Clasificador de productos Mobiliti basado en diccionario JSON + fuzzy matching.

Uso:
    from clasificador import cargar_diccionario, clasificar_producto
    diccionario = cargar_diccionario("diccionario_categorias.json")
    categoria = clasificar_producto("Aveza Task Chair", diccionario)
    # -> "SILLA OPERATIVA"
"""

import json
import unicodedata
from pathlib import Path

try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_DISPONIBLE = True
except ImportError:
    RAPIDFUZZ_DISPONIBLE = False


def _quitar_acentos(texto: str) -> str:
    """Normaliza acentos: 'sillón' -> 'sillon'."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )


def normalizar_texto(texto: str, quitar_acentos: bool = True) -> str:
    """
    Normaliza texto para comparación:
    - Minúsculas
    - Quita acentos (opcional)
    - Quita espacios extra
    - Quita saltos de línea
    """
    if not isinstance(texto, str):
        texto = str(texto) if texto is not None else ""
    texto = texto.lower().strip()
    texto = texto.replace('\n', ' ').replace('\r', ' ')
    # Colapsar espacios múltiples
    while '  ' in texto:
        texto = texto.replace('  ', ' ')
    if quitar_acentos:
        texto = _quitar_acentos(texto)
    return texto


def cargar_diccionario(path: str | Path) -> dict:
    """Carga el diccionario de categorías desde un archivo JSON."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Diccionario no encontrado: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


def _preparar_terminos_planos(diccionario: dict) -> tuple:
    """
    Prepara una lista plana de (término, categoría) para fuzzy matching.
    Se ejecuta una sola vez por clasificación batch.
    """
    terminos_planos = []
    for cat_nombre, cat_data in diccionario.get("categorias", {}).items():
        for termino in cat_data.get("terminos", []):
            terminos_planos.append((normalizar_texto(termino), cat_nombre))
    return terminos_planos


def clasificar_producto(nombre_producto: str, diccionario: dict) -> str:
    """
    Clasifica un nombre de producto en una categoría del diccionario.

    Estrategia:
        1. Match exacto por substring (término contenido en nombre).
        2. Si no hay match exacto y rapidfuzz está disponible,
           usar fuzzy matching con umbral configurable.
        3. Si nada coincide, devolver categoría default ("OTRO").
    """
    if not nombre_producto:
        return diccionario.get("config", {}).get("default_category", "OTRO")

    config = diccionario.get("config", {})
    umbral = config.get("umbral_fuzzy", 75)
    case_sensitive = config.get("case_sensitive", False)
    normalizar_acentos = config.get("normalizar_acentos", True)
    priorizar_exacto = config.get("priorizar_match_exacto", True)
    default = config.get("default_category", "OTRO")

    nombre_norm = normalizar_texto(nombre_producto, quitar_acentos=normalizar_acentos)
    if not case_sensitive:
        nombre_norm = nombre_norm.lower()

    categorias = diccionario.get("categorias", {})

    # --- PASO 1: Match exacto por substring ---
    # Ordenar terminos de mas largo a mas corto para priorizar matches especificos
    # (ej: "reception desk" antes que "desk")
    if priorizar_exacto:
        terminos_ordenados = []
        for cat_nombre, cat_data in categorias.items():
            for termino in cat_data.get("terminos", []):
                term_norm = normalizar_texto(termino, quitar_acentos=normalizar_acentos)
                if not case_sensitive:
                    term_norm = term_norm.lower()
                terminos_ordenados.append((len(term_norm), term_norm, cat_nombre))
        terminos_ordenados.sort(reverse=True)  # Mayor longitud primero

        for _, term_norm, cat_nombre in terminos_ordenados:
            if term_norm in nombre_norm:
                return cat_nombre

    # --- PASO 2: Fuzzy matching (fallback) ---
    if RAPIDFUZZ_DISPONIBLE:
        terminos_planos = _preparar_terminos_planos(diccionario)
        if not terminos_planos:
            return default

        # Extraemos solo los términos normalizados para rapidfuzz
        choices = [t[0] for t in terminos_planos]
        resultado = process.extractOne(nombre_norm, choices, scorer=fuzz.WRatio)

        if resultado is not None:
            match_texto, score, idx = resultado
            if score >= umbral:
                return terminos_planos[idx][1]

    return default


def clasificar_batch(nombres: list[str], diccionario: dict) -> list[str]:
    """Clasifica una lista de nombres de productos de forma eficiente."""
    return [clasificar_producto(n, diccionario) for n in nombres]


# --- CLI básico para pruebas manuales ---
if __name__ == "__main__":
    import sys
    dic = cargar_diccionario("diccionario_categorias.json")
    if len(sys.argv) > 1:
        nombre = " ".join(sys.argv[1:])
        cat = clasificar_producto(nombre, dic)
        print(f"Producto: {nombre}")
        print(f"Categoria: {cat}")
    else:
        print("Uso: python clasificador.py 'nombre del producto'")
