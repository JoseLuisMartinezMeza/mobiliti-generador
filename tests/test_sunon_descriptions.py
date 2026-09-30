"""Traducciones revisadas conservan especificaciones y restricciones Sunon."""
import json
from pathlib import Path
import re

import pytest

from mobiliti_saas.quote_engine.sunon_descriptions import redactar_texto_sunon, titulo_sunon
from mobiliti_saas.quote_engine.supplier_descriptions import (
    detectar_pendientes_descripcion,
    nombre_producto_proveedor,
    redactar_descripcion_proveedor,
)


DATOS = Path(__file__).parents[1] / "mobiliti_saas/quote_engine/data/sunon_description_translations.json"


def test_sunon_escritorio_con_gabinete_tiene_redaccion_natural():
    texto = redactar_texto_sunon("Custom Desk with Cabinet: one side with a side cabinet, one side with slanted insert legs")
    assert texto == "Escritorio con gabinete lateral en un extremo y patas inclinadas de inserción en el otro."
    assert "Custom" not in texto


def test_sunon_ficha_completa_sin_perder_ultima_restriccion():
    texto = redactar_texto_sunon(
        "Material Description: 1)Chair Body: PP + 20% fiberglass, molded "
        "2)Gas Cylinder: SAMHONGSA Class 3 gas lift cylinder, Stroke: 252 ±3mm "
        "3)5-Star Bases: 690mm diameter steel-plated 5-star base "
        "4)Chair Casters: PA casters Function Description: "
        "1)Chair Base Adjustment: BG Chair Base, only height adjustment, not tilt-capable"
    )
    assert "20% de fibra de vidrio" in texto
    assert "252 ± 3 mm" in texto and "690 mm" in texto
    assert "no permite reclinación" in texto
    assert "Function Description" not in texto


def test_sunon_tolerancias_capacidad_y_densidad_no_cambian():
    texto = redactar_texto_sunon("Seat Cushion: cut foam, density 31.5kg/m3±2, equipped with E1 grade bentwood board, PP seat shell, weight capacity 136kg")
    assert "31.5 ± 2 kg/m3" in texto
    assert "grado E1" in texto and "136 kg" in texto


def test_sunon_correccion_de_encabezado_con_errata_no_altera_hechos():
    texto = redactar_texto_sunon("Material Desctription: 1)Cable Trays: high-quality cold-rolled steel, 0.7mm, surface electrostatic powder coating")
    assert texto == "Charola pasacables de acero laminado en frío de alta calidad, de 0.7 mm, con recubrimiento electrostático en polvo."


@pytest.mark.parametrize("nombre,esperado", [
    ("Coupe II Task chair", "Silla operativa Coupe II"),
    ("Alien Task Chair", "Silla operativa Alien"),
    ("Maxim Private Offices Desk", "Escritorio para oficina privada Maxim"),
    ("SAMU Lounge Chairs Seating (Not for sale)", "Sillón de descanso SAMU (no disponible para venta)"),
    ("Wardrobe/Bookcase (right-door)", "Guardarropa con librero y puerta derecha"),
    ("Mandis Flip-top Training Table", "Mesa de capacitación abatible Mandis"),
])
def test_titulos_conservan_modelo_lado_y_restricciones(nombre, esperado):
    assert titulo_sunon(nombre) == esperado


def test_ficha_desconocida_no_recibe_especificaciones_de_un_modelo_parecido():
    fuente = "Custom Desk with Cabinet: unique new specifications 99mm"
    assert redactar_texto_sunon(fuente) == fuente
    assert titulo_sunon("Modelo exclusivo Z9") == "Modelo exclusivo Z9"


def test_cada_clausula_revisada_tiene_fuente_y_preserva_todas_las_cifras():
    filas = json.loads(DATOS.read_text(encoding="utf-8"))["translations"]
    assert len(filas) == 299
    for fila in filas:
        traducida = redactar_texto_sunon(fila["source"])
        assert traducida == fila["spanish"], fila["source"]
        assert fila["source_reference"]
        valores = lambda texto: set(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?", texto))
        assert valores(fila["source"]) <= valores(traducida), fila["source"]


@pytest.mark.parametrize("nombre,titulo", [
    ("Rect. Soft Corner Occasional Table", "Mesa auxiliar"),
    ("Rectangle Wire Base Occasional Table", "Mesa auxiliar con base de varilla"),
])
def test_mesa_con_formas_contradictorias_omite_ambas_y_conserva_estructura(nombre, titulo):
    ficha = {"supplier": "sunon", "name": nombre, "description": (
        "Material Description: 1)Square coffee table. 2)Tabletop: 18mm, surface uses "
        "impregnated melamine paper for excellent stability, superior layering, strong "
        "resistance to dirt and wear; E0 grade particle board "
        "3)Support Frame: round tube made of Q195 steel, disc made of SPHC, electrostatic powder coating"
    )}
    fuente_original = ficha["description"]
    cuerpo = redactar_texto_sunon(redactar_descripcion_proveedor(ficha))
    assert titulo_sunon(nombre_producto_proveedor(ficha)) == titulo
    assert cuerpo.startswith("Mesa de centro.")
    assert "cuadrada" not in cuerpo and "rectangular" not in cuerpo
    assert "tubo redondo" in cuerpo and "18 mm" in cuerpo and "grado E0" in cuerpo
    assert detectar_pendientes_descripcion(ficha) == ["geometria_contradictoria_entre_nombre_y_ficha_confirmar"]
    assert ficha["description"] == fuente_original


@pytest.mark.parametrize("nombre,puertas", [
    ("Double-Door Storage Cabinets Frame with Shelves", "Four"),
    ("Four-Door Storage Cabinets Frame with Shelves", "Eight"),
    ("Six-Door Storage Cabinets Frame with Shelves", "Twelve"),
])
def test_gabinete_con_cantidad_de_puertas_contradictoria_omite_ambas(nombre, puertas):
    ficha = {"supplier": "sunon", "name": nombre, "description": (
        f"Material Description: 1){puertas}-door file cabinet body "
        "2)Cabinet: the main body surface covered with impregnated melamine paper. E0 grade particleboard "
        "3)Edge Banding: the cabinet uses PVC edge banding strips, thickness 1mm"
    )}
    assert titulo_sunon(nombre_producto_proveedor(ficha)) == "Cuerpo de gabinete con estantes"
    cuerpo = redactar_texto_sunon(redactar_descripcion_proveedor(ficha))
    assert cuerpo.startswith("Cuerpo de archivero.")
    assert "puertas" not in cuerpo
    assert "1 mm" in cuerpo and "grado E0" in cuerpo
    assert detectar_pendientes_descripcion(ficha) == ["cantidad_de_puertas_contradictoria_entre_nombre_y_ficha_confirmar"]


def test_ficha_actualizada_compatible_y_tubos_redondos_no_producen_conflictos():
    ficha = {"supplier": "sunon", "name": "Rectangle Wire Base Occasional Table", "description": (
        "Material Description: 1)Rectangular coffee table 2)Support Frame: round tube made of Q195 steel"
    )}
    assert redactar_descripcion_proveedor(ficha) == ficha["description"]
    assert nombre_producto_proveedor(ficha) == ficha["name"]
    assert detectar_pendientes_descripcion(ficha) == []


def test_cajones_consistentes_se_conservan():
    ficha = {"supplier": "sunon", "name": "3 Drawer Lateral File", "description": "Material Description: 1)Fixed three-drawer cabinet. 2)Lock: ordinary front lock"}
    assert titulo_sunon(nombre_producto_proveedor(ficha)) == "Archivero lateral de 3 cajones"
    assert redactar_texto_sunon(redactar_descripcion_proveedor(ficha)).startswith("Archivero fijo de tres cajones.")
    assert detectar_pendientes_descripcion(ficha) == []
