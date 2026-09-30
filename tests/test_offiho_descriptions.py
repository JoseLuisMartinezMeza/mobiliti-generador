"""Regresiones de redacción con recortes reales del catálogo Offiho."""

import copy
import json
from pathlib import Path
import re

import pytest

from mobiliti_saas.quote_engine.offiho_descriptions import (
    nombre_producto_offiho,
    redactar_descripcion_offiho,
)


@pytest.fixture(scope="module")
def articulos():
    ruta = Path(__file__).resolve().parents[1] / "mobiliti_saas/quote_engine/data/offiho_catalog.json"
    return json.loads(ruta.read_text(encoding="utf-8"))["items"]


def _buscar(articulos, clave):
    return next(item for item in articulos if item["inventory_key"] == clave)


@pytest.mark.parametrize("clave,esperado", [
    ("VESPER 1", "VESPER 1"),
    ("TORRE W BLANCO", "TORRE W"),
    ("BRAZOS 86 NEGRO", "BRAZOS 86"),
    ("CABECERA OHE-112 GRIS", "Cabecera OHE-112"),
    ("OHE-305 NEGRO CIANI *", "CIANI"),
])
def test_nombre_conserva_modelo_y_tipo_del_inventario(articulos, clave, esperado):
    assert nombre_producto_offiho(_buscar(articulos, clave)) == esperado


def test_pdf_vanto_no_convierte_capacidades_vecinas_en_caracteristicas(articulos):
    item = _buscar(articulos, "OHV-78 NEGRO VANTO *")
    texto = redactar_descripcion_offiho(item)
    assert texto == "VANTO de Offiho."
    assert not re.search(r"120|140|ODLAPSER|CAJA|º", texto)


def test_cabecera_no_hereda_resistencia_de_silla(articulos):
    item = _buscar(articulos, "CABECERA OHE-112 GRIS")
    texto = redactar_descripcion_offiho(item)
    assert texto == "Cabecera OHE-112 de Offiho."
    assert not re.search(r"160|180|brazos|kg", texto, re.I)


def test_vesper_conserva_materiales_restriccion_exterior_y_peso(articulos):
    texto = redactar_descripcion_offiho(_buscar(articulos, "VESPER 1"))
    assert "acero tubular de 20 mm" in texto
    assert "espesor 1.5 mm" in texto
    assert "estructura cromada no aplica para exterior" in texto
    assert "Asiento y respaldo de una sola pieza en polipropileno" in texto
    assert "120 kg" in texto
    assert not re.search(r"544|545|597|anja|465|dimensiones en mm", texto)


def test_detecta_recorte_pdf_sin_campo_de_procedencia(articulos):
    item = dict(_buscar(articulos, "VESPER 2"))
    item.pop("description_source")
    texto = redactar_descripcion_offiho(item)
    assert "acero tubular" in texto
    assert "544" not in texto and "anja" not in texto


def test_ficha_oficial_conserva_datos_y_no_afirma_piel_natural(articulos):
    texto = redactar_descripcion_offiho(_buscar(articulos, "OHE-305 NEGRO CIANI *"))
    assert "soft revolving leather" in texto
    assert "piel natural" not in texto and "piel genuina" not in texto
    assert "50 kg/m³" in texto and "160 kg" in texto
    assert "Altura del asiento ajustable mediante pistón neumático" in texto
    assert "CIANI *" not in texto


def test_presentacion_final_conserva_denominacion_comercial_del_tapizado(articulos):
    from mobiliti_saas.quote_engine.customer_descriptions import construir_descripcion_catalogo

    item = _buscar(articulos, "OHE-305 NEGRO CIANI *")
    texto = construir_descripcion_catalogo(nombre_producto_offiho(item), redactar_descripcion_offiho(item), catalogo="offiho")
    assert "soft revolving leather" in texto
    assert "soft revolving Piel" not in texto


def test_texto_tecnico_de_accesorio_no_se_sustituye_sin_motivo():
    item = {"code": "BRAZO-40", "name": "", "description": "Brazo ajustable de polipropileno. Precio por par."}
    assert redactar_descripcion_offiho(item) == item["description"]


def test_no_modifica_ficha_fuente(articulos):
    item = copy.deepcopy(_buscar(articulos, "VESPER 1"))
    anterior = copy.deepcopy(item)
    redactar_descripcion_offiho(item)
    nombre_producto_offiho(item)
    assert item == anterior


def test_todas_las_fichas_quedan_sin_ruido_grafico(articulos):
    ruido = re.compile(r"ºº|\b(?:ODLAPSER|OTNEISA|SOZARB|SELBATSUJA|ARUTLA)\b|\b\d{3} \d{3} \d{3}\b|medidas: dimensiones", re.I)
    defectos = [(item["inventory_key"], texto) for item in articulos if ruido.search(texto := redactar_descripcion_offiho(item))]
    assert defectos == []


def test_no_agrega_kit_cajero_desde_un_recorte_compartido(articulos):
    texto = redactar_descripcion_offiho(_buscar(articulos, "OHE-100 BLANCO WHALE"))
    assert "kit" not in texto.casefold()
    assert "pistón" not in texto.casefold()


def test_cotas_sueltas_no_recortan_espesores_decimales(articulos):
    texto = redactar_descripcion_offiho(_buscar(articulos, "VILLA 1"))
    assert "espesor 1.5 mm" in texto


@pytest.mark.parametrize("clave,frase_ajena", [
    ("VESPER 3T", "Base: estrella"),
    ("SPLIT 3A", "Estructura de acero"),
])
def test_no_agrega_estructura_contradictoria_de_ficha_vecina(articulos, clave, frase_ajena):
    texto = redactar_descripcion_offiho(_buscar(articulos, clave))
    assert frase_ajena not in texto


def test_detecta_ruido_en_payload_real_sin_procedencia(articulos):
    from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload, load_offiho_catalog

    catalogo = load_offiho_catalog()
    diferencias = []
    for original in articulos:
        item = build_offiho_cart_payload(
            [{"inventory_key": original["inventory_key"], "quantity": 1}], catalog=catalogo,
        )["items"][0]
        assert "description_source" not in item
        texto = redactar_descripcion_offiho(item)
        # El payload sustituye nombres vacíos por la identidad de inventario.
        # Comparamos esa misma identidad con/sin procedencia de la fuente.
        esperado = redactar_descripcion_offiho({**original, "name": item["name"]})
        assert not re.search(r"ºº|ODLAPSER|OTNEISA|SOZARB|SELBATSUJA|medidas: dimensiones|\b\d{3} \d{3} \d{3}\b", texto, re.I)
        if texto != esperado:
            diferencias.append((original["inventory_key"], esperado, texto))
    assert diferencias == []


def test_alufsen_aclara_cabecera_y_conserva_tapizado_de_brazos(articulos):
    item = copy.deepcopy(_buscar(articulos, "OHE-405 NEGRO ALUFSEN"))
    anterior = copy.deepcopy(item)
    texto = redactar_descripcion_offiho(item)
    assert "Altura del asiento ajustable mediante pistón neumático." in texto
    assert "que permite ajustar la altura" not in texto
    assert "Respaldo alto con cabecera. Asiento y respaldo con espuma laminada flexible" in texto
    assert "asiento y respaldo con cabecera" not in texto.lower()
    assert (
        "Brazos de aluminio fundido con acabado natural pulido, "
        "acojinados y tapizados en soft revolving leather color negro."
    ) in texto
    assert "Tapizado: soft revolving leather de color negro." in texto
    assert "50 kg/m³" in texto and "160 kg" in texto
    assert ". Acojinados" not in texto
    assert item == anterior


def test_pulido_de_alufsen_no_inventa_cabecera_ni_cambia_ajuste_distinto():
    texto = redactar_descripcion_offiho({
        "name": "Silla de prueba",
        "description": (
            "elevación: por medio de pistón neumático con recorrido de 100 mm. "
            "asiento y respaldo: de respaldo alto, espuma de 30 kg/m3."
        ),
    })
    assert "Ajuste de altura mediante pistón neumático con recorrido de 100 mm" in texto
    assert "cabecera" not in texto
    assert "30 kg/m³" in texto
