"""Redacción fiel a hechos y variantes de los catálogos comerciales."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from mobiliti_saas.quote_engine.supplier_descriptions import (
    detectar_pendientes_descripcion,
    nombre_producto_proveedor,
    redactar_descripcion_proveedor,
)


DATOS = Path(__file__).parents[1] / "mobiliti_saas/quote_engine/data/supplier_description_corrections.json"


def _ficha_corregida(marca, inicio, **campos):
    filas = json.loads(DATOS.read_text(encoding="utf-8"))["corrections"]
    fila = next(f for f in filas if f["supplier"] == marca and f["original"].startswith(inicio))
    return {"supplier": marca, "description": fila["original"], **campos}


def test_jome_recupera_descripcion_del_bloque_sin_inventar_materiales():
    ficha = {
        "supplier": "jome", "sku": "CHCH-1300", "name": "CHCH-1300", "description": "CHCH-1300",
        "attributes": {"block": "Charola universal sencilla bench", "dimensions": "1.30 mts"},
    }
    assert redactar_descripcion_proveedor(ficha) == "Charola universal sencilla bench. Medidas: 1.30 m."
    assert detectar_pendientes_descripcion(ficha) == ["ficha_tecnica_incompleta_identidad_recuperada_del_bloque"]


def test_jome_no_inventa_capacidad_de_monitor_ausente():
    ficha = {
        "supplier": "jome",
        "description": "Brazos Porta Monitores Doble, Modelo Me-Work, Rango altura 160-410 mm Dimension pantalla 13”-27” Peso soportado",
    }
    texto = redactar_descripcion_proveedor(ficha)
    assert "Brazo doble para monitores" in texto
    assert "160-410 mm" in texto and "13”-27”" in texto
    assert "Peso soportado" not in texto
    assert "capacidad_de_carga_ausente" in detectar_pendientes_descripcion(ficha)


def test_lumbro_preserva_conexiones_y_no_adivina_longitud_cable():
    ficha = {"supplier": "lumbro", "description": "Multicontacto, Modelo Barcelona. Incluye: 3 puertos de corriente de 127v, 1 puertoo USB doble(Carga), 1 puerto RJ45, 1 puerto HDMI, 1 puerto VGA y 1 cable de metro con clavija"}
    texto = redactar_descripcion_proveedor(ficha)
    assert "3 puertos de corriente de 127 V" in texto
    assert "1 puerto USB doble de carga" in texto
    assert all(conector in texto for conector in ("1 puerto RJ45", "1 puerto HDMI", "1 puerto VGA"))
    assert "1 cable con clavija" in texto
    assert "1 metro" not in texto
    assert detectar_pendientes_descripcion(ficha) == ["longitud_de_cable_no_especificada"]


def test_lumbro_longitud_explicita_no_se_pierde():
    ficha = {"supplier": "lumbro", "description": "Multicontacto, Modelo Dublin. Incluye: 1 puerto USB de carga Tipo A 1 puerto USB de carga Tipo C y 1 cable de 1.5 mts con clavija Sistema: Splits"}
    texto = redactar_descripcion_proveedor(ficha)
    assert "tipo A, 1 puerto" in texto
    assert "1.5 m con clavija" in texto
    assert "Sistema:" not in texto


def test_sonara_redacta_sin_perder_exclusiones():
    ficha = _ficha_corregida("sonara", "Herrajes de sujeción a techo tipo parrilla de aluminio 1.20")
    texto = redactar_descripcion_proveedor(ficha)
    assert "1.20 × 1.20 m" in texto
    assert "No incluyen amarre a losa" in texto


def test_sonara_no_corrige_medida_ambigua_por_intuicion():
    ficha = _ficha_corregida("sonara", "Panel Sonara 1.20 x .240")
    assert "0.240 m" in redactar_descripcion_proveedor(ficha)
    assert "medida_0_240_m_publicada_requiere_confirmacion" in detectar_pendientes_descripcion(ficha)


def test_conceptos_completa_redaccion_sin_inventar_especie_madera():
    ficha = _ficha_corregida("conceptos", "Sillón aspetta")
    texto = redactar_descripcion_proveedor(ficha)
    assert texto == "Sillón Aspetta de 1 plaza, con estructura interna y brazos de madera."
    assert "pino" not in texto
    assert "texto_fuente_recortado_a_una_fila_se_conservan_solo_hechos_completos" in detectar_pendientes_descripcion(ficha)


def test_correccion_exacta_no_sobrescribe_nueva_ficha_mismo_modelo():
    ficha = _ficha_corregida("conceptos", "Sillón aspetta")
    ficha["description"] += " nogal, con acabado nuevo."
    texto = redactar_descripcion_proveedor(ficha)
    assert "nogal" in texto and "acabado nuevo" in texto


def test_labenze_base_cromada_seleccionada_no_arrastra_otras_bases():
    ficha = _ficha_corregida("labenze", "Cromo Madera Natural Consultar Negro Madera Wengué catálogo Blanco Madera Nogal de tapices. Aluminio L3 4 patas en madera de encino blanco en Natural/Wengué/Nogal. R3 Fija de 4 puntas de acero cromado o pintado. *Aplican colores especiales P3 Fija de 4 puntas tipo spider en acero cromado o pintado. Sillón de 1 plaza en cuerpo de poliuretano P20 Giratoria de 5 puntas nylon con rodajas color negro. inyectado y tapizado. R10", name="MEG — R3 Cromo")
    texto = redactar_descripcion_proveedor(ficha)
    assert "cuerpo de poliuretano inyectado y tapizado" in texto
    assert "Base fija de 4 puntas de acero cromado." in texto
    assert "nylon" not in texto and "L17" not in texto


def test_labenze_sin_variante_no_promete_una_base_especifica():
    ficha = _ficha_corregida("labenze", "Cromo Madera Natural Consultar Negro Madera Wengué catálogo Blanco Madera Nogal de tapices. Aluminio L3 4 patas en madera de encino blanco en Natural/Wengué/Nogal. R3 Fija de 4 puntas de acero cromado o pintado. *Aplican colores especiales P3 Fija de 4 puntas tipo spider en acero cromado o pintado. Sillón de 1 plaza en cuerpo de poliuretano P20 Giratoria de 5 puntas nylon con rodajas color negro. inyectado y tapizado. R10", name="MEG")
    assert redactar_descripcion_proveedor(ficha) == "Sillón de 1 plaza con cuerpo de poliuretano inyectado y tapizado."


def test_labenze_no_infiere_uso_exterior_de_fragmento_cortado():
    ficha = {"supplier": "labenze", "description": "Silla apilable en tecnopolímero. y externo"}
    assert redactar_descripcion_proveedor(ficha) == "Silla apilable en tecnopolímero."


def test_requiez_preserva_compatibilidades_y_corrige_erratas():
    ficha = {"supplier": "requiez", "description": "Cabecera ajustableen color negro. Modelos compatibles Julia."}
    assert redactar_descripcion_proveedor(ficha) == "Cabecera ajustable en color negro. Modelos compatibles: Julia."


def test_requiez_detecta_especificaciones_contradictorias():
    ficha = {"supplier": "requiez", "description": "autopesante con 3 posiciones de bloqueo. 4 posiciones de bloqueo y 4 niveles de tensión."}
    assert "especificaciones_mezcladas_entre_productos" in detectar_pendientes_descripcion(ficha)


def test_requiez_no_publica_bloqueos_contradictorios_de_fuente_revisada():
    ficha = _ficha_corregida("requiez", "autopesante con 3 posiciones")
    assert redactar_descripcion_proveedor(ficha) == "Elevación neumática."
    assert "mecanismo_y_numero_de_bloqueos_contradictorios_omitidos_hasta_verificar" in detectar_pendientes_descripcion(ficha)


def test_idelika_separa_mesa_del_loveseat_vecino():
    ficha = _ficha_corregida("idelika", "Madera Maciza Eucalipto 100*60*32 Sacaleta")
    texto = redactar_descripcion_proveedor(ficha)
    assert texto == "Mesa de madera maciza de eucalipto. Medidas: 100 × 60 × 32."
    assert "142" not in texto and "Love" not in texto


def test_idelika_no_atribuye_galvanizado_a_variante_ambigua():
    ficha = _ficha_corregida("idelika", "55*55*109 alto Acero con pintura")
    texto = redactar_descripcion_proveedor(ficha)
    assert "Estructura de acero con pintura electrostática y cuerda" in texto
    assert "galvanizado" not in texto and "loneta" not in texto
    assert "55 × 55 × 109" in texto


def test_idelika_retira_promesa_escolar_y_conserva_materiales():
    ficha = {"supplier": "idelika", "description": "Estructura en tuvo redondo, terminado en pintura epóxica. Tus alumnos con estos pupitres, seguro sacarán las notas más altas. Cómodos e inspiradores."}
    assert redactar_descripcion_proveedor(ficha) == "Estructura en tubo redondo, terminado en pintura epóxica."


def test_idelika_no_asigna_unidad_a_dimensiones_desconocidas():
    ficha = {"supplier": "idelika", "description": "Color a elegir 230*115*80 Precios disponibles en www.idelika.com"}
    texto = redactar_descripcion_proveedor(ficha)
    assert texto == "Color a elegir 230 × 115 × 80."
    assert "cm" not in texto


def test_lauco_legible_sin_perder_densidades_y_negaciones():
    ficha = {"supplier": "lauco", "description": "Sofa 1.5 plazas c/mesa. • Estructura interna madera sin nudos. • Acojinamiento densidad 27/28."}
    texto = redactar_descripcion_proveedor(ficha)
    assert "Sofá de 1.5 plazas con mesa" in texto
    assert "Estructura interna de madera sin nudos" in texto
    assert "27/28" in texto
    assert "•" not in texto


@pytest.mark.parametrize("marca", ["sunon", "alma"])
def test_no_traduce_prematuramente_catalogos_en_ingles(marca):
    fuente = "Dining armchair with teak frame. Load capacity 120 kg."
    assert redactar_descripcion_proveedor({"supplier": marca, "description": fuente}) == fuente


def test_tarkett_no_inventa_material_desde_el_nombre():
    ficha = {"supplier": "tarkett", "name": "Aurea Tech Cadiz 6.0mm"}
    assert redactar_descripcion_proveedor(ficha) == "Aurea Tech Cadiz 6.0 mm."
    assert detectar_pendientes_descripcion(ficha) == ["fuente_sin_descripcion_tecnica"]


def test_no_modifica_snapshot_y_no_aplica_a_otro_proveedor():
    ficha = {"supplier": "offiho", "description": "SILLA 1.20mts", "attributes": {"color": "Negro"}, "price_net": "1200"}
    antes = deepcopy(ficha)
    assert redactar_descripcion_proveedor(ficha) == "SILLA 1.20mts"
    assert ficha == antes


def test_todas_las_correcciones_tienen_fuente_y_clave_unica():
    filas = json.loads(DATOS.read_text(encoding="utf-8"))["corrections"]
    claves = {(fila["supplier"], fila["original"]) for fila in filas}
    assert len(claves) == len(filas)
    assert all(fila["source_reference"] for fila in filas)
    assert all(fila["revised"].strip() or "fuente_sin_descripcion_tecnica" in fila["pending"] for fila in filas)


def test_jome_omite_clausula_truncada_sin_inventar_canto():
    ficha = _ficha_corregida("jome", "Cubierta recta para lateral de laminado")
    assert redactar_descripcion_proveedor(ficha) == "Cubierta recta para lateral de laminado plástico de 28 mm."
    assert "texto_fuente_truncado_se_omiten_clausulas_incompletas" in detectar_pendientes_descripcion(ficha)


def test_idelika_fuente_solo_promocional_no_se_convierte_en_ficha():
    ficha = {"supplier": "idelika", "name": "Colchón memory foam", "description": "Precios disponibles en"}
    assert redactar_descripcion_proveedor(ficha) == ""
    assert "fuente_sin_descripcion_tecnica" in detectar_pendientes_descripcion(ficha)


def test_idelika_conserva_cojineria_por_separado_al_resolver_variantes():
    ficha = _ficha_corregida("idelika", "Acero con pintura electroestática cuerda de multifilamento Acero galvanizado y cuerda textilene Cojinería")
    assert "La cojinería se vende por separado." in redactar_descripcion_proveedor(ficha)


def test_idelika_pedido_minimo_ambiguo_permanece_como_condicion_visible():
    ficha = _ficha_corregida("idelika", "diseño sin precedentes, exclusiva")
    assert "Pedido mínimo y disponibilidad por confirmar." in redactar_descripcion_proveedor(ficha)


def test_lauco_no_deja_coma_antes_del_punto_de_vineta():
    ficha = {"supplier": "lauco", "description": "Sofa 1 plaza, base negro mate o gris oxford, • Estructura interna madera."}
    texto = redactar_descripcion_proveedor(ficha)
    assert "oxford. Estructura" in texto
    assert ",." not in texto


def test_nombre_conceptos_separa_identidad_de_ficha_truncada():
    ficha = _ficha_corregida("conceptos", "Sillón aspetta")
    ficha["name"] = ficha["description"]
    assert nombre_producto_proveedor(ficha) == "Sillón Aspetta de 1 plaza"


def test_nombre_jome_recupera_bloque_y_conserva_codigo():
    ficha = {"supplier": "jome", "sku": "CHCH-1300", "name": "CHCH-1300", "description": "CHCH-1300", "attributes": {"block": "Charola universal sencilla bench"}}
    assert nombre_producto_proveedor(ficha) == "Charola universal sencilla bench · CHCH-1300"


def test_nombre_correcto_y_nombre_comercial_ingles_se_conservan():
    assert nombre_producto_proveedor({"supplier": "lumbro", "name": "Barcelona Carga Negro", "description": "Multicontacto"}) == "Barcelona Carga Negro"
    nombre = "STRING Dining Side Chair Frame: Alu in powder coating S&B: PVC Rope"
    assert nombre_producto_proveedor({"supplier": "alma", "name": nombre, "description": nombre}) == "STRING Dining Side Chair"


@pytest.mark.parametrize("nombre,esperado", [
    ("RUGBY Lounge Armchair Arm and Rim: Polarwood (Faux Teak)", "RUGBY Lounge Armchair"),
    ("CLOGS Center Module Base: Alu in teaklook", "CLOGS Center Module"),
    ("LOTUS Round Coffee Table Ceramic Top:", "LOTUS Round Coffee Table"),
])
def test_nombre_alma_separa_etiqueta_completa(nombre, esperado):
    assert nombre_producto_proveedor({"supplier": "alma", "name": nombre, "description": nombre}) == esperado


def test_idelika_minimo_de_pedido_explicito_en_atributos_se_conserva():
    ficha = _ficha_corregida("idelika", "diseño sin precedentes, exclusiva", attributes={"minimum_order": "200.000000"})
    assert "Pedido mínimo: 200 piezas. Disponibilidad por confirmar." in redactar_descripcion_proveedor(ficha)


def test_labenze_gl_madera_se_recupera_sin_adivinar_codigo():
    ficha = _ficha_corregida("labenze", "Cromo Madera Natural Consultar Negro Madera Wengué catálogo Blanco Madera Nogal de tapices. Aluminio GL", name="NELL — GL Madera")
    assert "Base fija de 4 puntas de madera sólida" in redactar_descripcion_proveedor(ficha)


def test_labenze_paleta_muestra_solo_variante_seleccionada():
    ficha = _ficha_corregida("labenze", "Negro Consultar catálogo de tapices. R16", name="GALA EDU — Monolit")
    texto = redactar_descripcion_proveedor(ficha)
    assert "Monolit de 18 mm" in texto
    assert "Arauco" not in texto and "16 mm" not in texto
