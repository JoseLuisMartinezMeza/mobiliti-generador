#!/usr/bin/env python3
"""
test_clasificador.py
====================
Tests unitarios para el clasificador de productos Mobiliti.
Categorias exactas del template + nuevas (Bancos, Cocineta, Pizarrones).
"""

import pytest
from clasificador import cargar_diccionario, clasificar_producto, normalizar_texto

DICCIONARIO = cargar_diccionario("diccionario_categorias.json")


# --- Tests de normalizacion ---
def test_normalizar_acentos():
    assert normalizar_texto("Sillón") == "sillon"
    assert normalizar_texto("PIZARRÓN") == "pizarron"
    assert normalizar_texto("Recepción") == "recepcion"


def test_normalizar_espacios():
    assert normalizar_texto("  sala  de   juntas  ") == "sala de juntas"
    assert normalizar_texto("SF51.1.MR\nF80 Lounge Sofas") == "sf51.1.mr f80 lounge sofas"


# --- Tests de clasificacion: productos reales KIVO ---
@pytest.mark.parametrize("producto,esperado", [
    # Mesas de Juntas
    ("SALA DE JUNTAS TETRIS 3.30X2.70", "Mesas de Juntas"),
    ("SAJA JUNTAS 3.60X1.40 LIDO", "Mesas de Juntas"),

    # Sillones
    ("SF51.1.MR\nF80 Lounge Sofas", "Sillones"),

    # Mesas de Apoyo
    ("T197\nMoji Round Wood Occasional Table", "Mesas de Apoyo"),
    ("T196\nMoji Round Wood Occasional Table", "Mesas de Apoyo"),
    ("EN70-2\nVarna Occasional Table", "Mesas de Apoyo"),
    ("EW70\nJason Occasional  Table", "Mesas de Apoyo"),

    # Silla
    ("CAZ83SW\nAveza Task Chair", "Silla"),
    ("CDK19GS \nNegotiation Chair ", "Silla"),

    # Escritorios
    ("ESCRITORIO LIDO 1.20X.60", "Escritorios-WorkStation"),
    ("TJ58\nI-Key Veneer Reception Desk", "Escritorios-WorkStation"),

    # Librero - Locker - Gabinete
    ("DG65-2\nStorage Cabinets", "Librero - Locker - Gabinete"),

    # Pizarrones (CORREGIDO: antes Terminados)
    ("DM24\nModit Whiteboard", "Pizarrones"),

    # Bancos (CORREGIDO: antes Terminados)
    ("SD32.1.MR.M\nDucky Stool", "Bancos"),

    # Mesas de Apoyo (Barra -> Mesas de Apoyo por contexto)
    ("BARRA 3.20*1.00*1.10", "Mesas de Apoyo"),

    # Cocineta (CORREGIDO: antes Terminados)
    ("cocinetas sunon", "Cocineta"),
])
def test_clasificar_productos_reales(producto, esperado):
    resultado = clasificar_producto(producto, DICCIONARIO)
    assert resultado == esperado, f"'{producto}' -> esperado '{esperado}', obtuvo '{resultado}'"


# --- Tests de correcciones especificas ---
def test_sala_de_estar_es_sillones():
    """Sala de estar debe clasificar como Sillones, no Mesas de Juntas."""
    assert clasificar_producto("SALA DE ESTAR", DICCIONARIO) == "Sillones"
    assert clasificar_producto("sala estar", DICCIONARIO) == "Sillones"
    assert clasificar_producto("living room sofa", DICCIONARIO) == "Sillones"


# --- Tests de robustez: typos y variantes ---
@pytest.mark.parametrize("producto,esperado", [
    ("sala de junta", "Mesas de Juntas"),
    ("sillla operativa", "Silla"),
    ("sofá lounge", "Sillones"),
    ("escrittorio", "Escritorios-WorkStation"),
    ("mesita de apoyo", "Mesas de Apoyo"),
    ("cocinetta", "Cocineta"),
    ("archivero movil", "Archiveros Moviles y Fijos"),
    ("phone booth", "Phonebooths"),
    ("power strip", "Multicontactos"),
    ("locker metalico", "Librero - Locker - Gabinete"),
    ("pizarron blanco", "Pizarrones"),
    ("taburete de bar", "Bancos"),
    ("mini cocina", "Cocineta"),
    ("white board", "Pizarrones"),
    ("kitchenette unit", "Cocineta"),
])
def test_clasificar_typos(producto, esperado):
    resultado = clasificar_producto(producto, DICCIONARIO)
    assert resultado == esperado, f"'{producto}' -> esperado '{esperado}', obtuvo '{resultado}'"


# --- Tests de fallback ---
def test_producto_desconocido():
    resultado = clasificar_producto("XYZ123 Producto Inexistente", DICCIONARIO)
    assert resultado == "Terminados"


def test_producto_vacio():
    assert clasificar_producto("", DICCIONARIO) == "Terminados"
    assert clasificar_producto(None, DICCIONARIO) == "Terminados"


# --- Verificar que todas las categorias del template + nuevas estan presentes ---
def test_categorias_presentes():
    categorias_esperadas = [
        "Silla", "Mesas de Apoyo", "Escritorios-WorkStation", "Sillones",
        "Mesas de Juntas", "Librero - Locker - Gabinete",
        "Archiveros Moviles y Fijos", "Phonebooths",
        "Multicontactos", "Terminados",
        "Bancos", "Cocineta", "Pizarrones"
    ]
    categorias_dict = list(DICCIONARIO.get("categorias", {}).keys())
    for cat in categorias_esperadas:
        assert cat in categorias_dict, f"Categoria '{cat}' no encontrada en diccionario"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
