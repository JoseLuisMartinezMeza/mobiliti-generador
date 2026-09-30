"""Casos observados en la descarga real de 13 catálogos 200-00038."""

from copy import deepcopy
import re

import pytest

from mobiliti_saas.quote_engine.catalog_cart import catalog_quotation_item_text
from mobiliti_saas.quote_engine.customer_descriptions import construir_descripcion_catalogo
from mobiliti_saas.quote_engine.supplier_descriptions import redactar_descripcion_proveedor


@pytest.mark.parametrize("marca,aviso", [
    ("sonara", "Codigo de proveedor faltante; verificar antes de cotizar."),
    ("requiez", "Codigo ROOT requiere revision en pagina 108: el encabezado y el pie publican grafias distintas."),
])
def test_avisos_documentales_no_llegan_al_cliente_ni_ocultan_condiciones(marca, aviso):
    texto = construir_descripcion_catalogo(
        "Modelo X", aviso + " | No incluye cojinería | Pedido mínimo: 200 piezas"
        " | Imagen de referencia | Disponibilidad: por confirmar", catalogo=marca,
    )
    assert aviso not in texto
    assert "Codigo" not in texto
    assert "No incluye cojinería." in texto
    assert "Pedido mínimo: 200 piezas." in texto
    assert "Imagen de referencia." in texto
    assert "Disponibilidad: por confirmar." in texto


@pytest.mark.parametrize("marca,titulo,fuente,esperado", [
    ("cr-global", "Vertebra Pasacable", "Garantia: GARANTÍA DE 5 AÑOS", "Vértebra Pasacable.\nGarantía: 5 años."),
    ("sunon", "Personal Table", "Color: black", "Mesa individual.\nColor: negro."),
    ("alma", "STRING", "Sin cojín Aluminio: Recubrimiento en polvo", "STRING.\nSin cojín; aluminio con recubrimiento en polvo."),
    ("requiez", "ROOT · 160-05535", "RESINA FENÓLICA NG/GR", "ROOT · 160-05535.\nResina fenólica NG/GR."),
    ("lumbro", "LIDO.OP-INT", "Notas: NOTA: SE PUEDEN MODIFICAR LAS CONEXIONES CON PRECIO ESPECIAL", "LIDO.OP-INT.\nNotas: se pueden modificar las conexiones con precio especial."),
])
def test_opciones_reales_tienen_redaccion_natural_y_conservan_condiciones(marca, titulo, fuente, esperado):
    assert construir_descripcion_catalogo(titulo, fuente, catalogo=marca) == esperado


@pytest.mark.parametrize("marca,nombre,fuente,esperado", [
    ("tarkett", "Aurea Tech Cadiz 6.0mm", "Aurea Tech Cadiz 6.0 mm", "Aurea Tech Cadiz 6.0 mm."),
    ("sonara", "Canal U de 2.40mts", "Canal U de 2.40 m", "Canal U de 2.40 m."),
    ("sonara", "Herraje de aluminio - Canal U de 2.40mts", "Canal U de 2.40 m", "Herraje de aluminio - Canal U de 2.40 m.\nCanal U de 2.40 m."),
])
def test_normalizar_unidades_antes_de_comparar_titulo_y_ficha(marca, nombre, fuente, esperado):
    assert construir_descripcion_catalogo(nombre, fuente, catalogo=marca) == esperado


@pytest.mark.parametrize("modelo", ["SERIE20MM", "ABC-123", "BLACK ROOT 20MM", "LIDO.OP-INT"])
def test_modelos_comerciales_conservan_grafia_y_claves(modelo):
    assert construir_descripcion_catalogo(modelo, "Base de acero.", catalogo="offiho") == modelo + ".\nBase de acero."


def test_color_comercial_sunon_no_se_traduce_por_fragmentos():
    assert "Color: Black Oak." in construir_descripcion_catalogo("Modelo", "Color: Black Oak", catalogo="sunon")
    assert "Color: black." in construir_descripcion_catalogo("Personal Table", "Color: black", "en", catalogo="sunon")


def test_configuracion_conceptos_duplicada_no_reintroduce_ficha_cruda():
    fuente = "Cushion con base, para archivero, estructura fabricados con un bastidor de madera"
    item = {"catalog": "conceptos", "name": fuente, "description": fuente,
            "configuration": fuente, "quantity": "1", "unit_price": "990"}
    congelado = deepcopy(item)
    revisada, _, _ = catalog_quotation_item_text(item, index=1, source_type="supplier_cart")
    original, _, _ = catalog_quotation_item_text(item, index=1, source_type="supplier_cart", redactar=False)
    cliente = construir_descripcion_catalogo("Cojín con base para archivero", revisada, catalogo="conceptos")
    assert "Cushion" not in cliente and "estructura fabricados" not in cliente
    assert cliente.count("con bastidor de madera") == 1
    assert original.count(fuente) == 2
    assert item == congelado


def test_configuracion_distinta_de_ficha_conserva_material_seleccionado():
    item = {"catalog": "conceptos", "description": "Base de acero.", "configuration": "Tapiz Grado 1",
            "quantity": "1", "unit_price": "990"}
    texto, _, _ = catalog_quotation_item_text(item, index=1, source_type="supplier_cart")
    assert "Tapiz Grado 1" in texto


def test_lido_conserva_modelo_y_conexiones_sin_mayusculas_sueltas():
    fuente = "Multicontacto LIDO para canaleta COLOR GRIS OXFORD con 3 puertos AC No Regulados y 1 PUERTO USB CARGA DOBLE, para INTERCONECTAR"
    revisado = redactar_descripcion_proveedor({"supplier": "lumbro", "description": fuente})
    texto = construir_descripcion_catalogo("LIDO.OP-INT", revisado, catalogo="lumbro")
    assert texto == ("LIDO.OP-INT.\nMulticontacto LIDO para canaleta en color gris oxford con 3 puertos AC no regulados "
                     "y 1 puerto USB de carga doble, para interconectar.")


def test_lauco_conectores_mejorados_conservan_todos_los_valores():
    fuente = (
        "Sofa 1 plaza brazos y respaldo bajo base negro mate o gris oxford, • Estructura interna madera de pino de primera 22 mm "
        "sin nudos en puntos críticos paneles sólidos en liston de 18mm, armada con pegamento blanco y grapa de acero 38 mm "
        "y ensamble con tornillo pavonado 8 x 2”. • Acojinamiento en asiento, poliuretano flexible densidad 27/28, "
        "respaldo densidad 20/35, brazos y costados densidad 17/45, además delcrón laminado cal. 150 para mayor confort. "
        "• Soporte asiento triplay de pino 19 mm o MDF 18 mm con refuerzos.• Patas acero tubular ovalado 20 x 40 mm cal. 16 acabado pintura horneada."
    )
    revisado = redactar_descripcion_proveedor({"supplier": "lauco", "description": fuente})
    assert revisado.startswith("Sofá de 1 plaza con brazos y respaldo bajo; base negro mate o gris oxford.")
    for fragmento in ("pino de primera de 22 mm", "puntos críticos. Paneles", "Soporte del asiento en triplay",
                      "cal. 16, con acabado de pintura horneada", "18 mm. Estructura armada con",
                      "poliuretano flexible de densidad 27/28", "respaldo de densidad 20/35", "brazos y costados de densidad 17/45"):
        assert fragmento in revisado
    assert re.findall(r"\d+(?:/\d+)?", fuente) == re.findall(r"\d+(?:/\d+)?", revisado)
