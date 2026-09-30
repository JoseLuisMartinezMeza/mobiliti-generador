import pytest

from mobiliti_saas.quote_engine.customer_descriptions import construir_descripcion_catalogo


def test_catalogo_conserva_identidad_y_oculta_metadatos_internos():
    texto = construir_descripcion_catalogo(
        "VESPER 1", "VESPER 1 de Offiho. | SKU: 999 | Clave: 999 | URL: https://example.test"
        " | Fuente: archivo.xlsx | Hash fila: 123 | Colección: Colección 2026"
        " | Standard | Codigo por verificar | Modelo no encontrado en spec guide; verificar"
        " | Variante: NEGRO", "es",
    )
    assert texto == "VESPER 1.\nColor: negro."


def test_catalogo_conserva_todas_las_especificaciones_y_condiciones():
    texto = construir_descripcion_catalogo(
        "Silla Uno",
        "1)Respaldo en malla 2)Asiento en tela 3)Base en aluminio 4)Brazos ajustables"
        " 5)Rodajas de PU 6)Altura ajustable 7)No incluye cabecera"
        " | Garantia: 5 años | Entrega: Entrega inmediata | PRECIO POR CONFIRMAR"
        " | ADVERTENCIA: EXISTENCIA INSUFICIENTE - SOLICITADO 8 - DISPONIBLE 2"
        " | Imagen de referencia", "es",
    )
    assert "No incluye cabecera." in texto
    assert "Garantía: 5 años." in texto
    assert "Entrega: inmediata." in texto
    assert "Precio por confirmar." in texto
    assert "Existencia insuficiente" in texto or "existencia insuficiente" in texto
    assert "Imagen de referencia." in texto
    assert "|" not in texto
    assert not texto.startswith("Silla modelo")


def test_catalogo_acentua_traduccion_sin_cambiar_especificaciones():
    texto = construir_descripcion_catalogo(
        "LOIRE", "LOIRE LOUNGE ARMCHAIR WITH STRAP 45mm | Base: aluminium", "es",
    )
    assert "Sillón de descanso con correa 45 mm." in texto
    assert "Base: aluminio." in texto


def test_catalogo_no_corta_decimales_ni_porcentajes():
    texto = construir_descripcion_catalogo(
        "Módulo A", "MDF de 18.5mm; 30% fibra de vidrio; densidad 55kg/m3. No incluye cubierta.",
    )
    assert "18.5 mm" in texto
    assert "30%" in texto
    assert "55 kg/m3" in texto
    assert "No incluye cubierta" in texto


def test_catalogo_ingles_conserva_especificaciones_fuente():
    texto = construir_descripcion_catalogo(
        "Dinosaur egg", "Dinosaur egg sling dining armchair with loose seat cushion"
        " | SKU: KUN-01 | Full aluminium legs", "en",
    )
    assert texto.startswith("Dinosaur egg.\nSling dining armchair")
    assert "Full aluminium legs." in texto
    assert "SKU" not in texto


def test_catalogo_no_inventa_atributos_cuando_solo_hay_nombre():
    assert construir_descripcion_catalogo("VANTO", "VANTO de Offiho.") == "VANTO."


def test_offiho_no_convierte_nombre_de_tapiz_en_promesa_de_piel():
    texto = construir_descripcion_catalogo(
        "CIANI", "Tapizado: soft revolving leather.", catalogo="offiho",
    )
    assert "soft revolving leather" in texto
    assert "piel" not in texto.lower()


def test_catalogo_preserva_restricciones_y_avisos_de_color():
    texto = construir_descripcion_catalogo(
        "Silla A", "No apta para exterior. | Color de referencia; el tono puede variar."
        " | Notas: No incluye brazos; venta por par. | Producto agotado",
    )
    assert "No apta para exterior" in texto
    assert "el tono puede variar" in texto
    assert "No incluye brazos" in texto
    assert "venta por par" in texto
    assert "Producto agotado" in texto


@pytest.mark.parametrize(
    "titulo,cuerpo,catalogo,titulo_final",
    [
        ("ROOT · 160-05535", "Root 160-05535", "requiez", "ROOT · 160-05535"),
        ("Aurea Tech Cadiz 6.0mm", "Aurea Tech Cadiz 6.0 mm", "tarkett", "Aurea Tech Cadiz 6.0 mm"),
    ],
)
def test_catalogo_omite_repeticion_completa_sin_cambiar_identidad(titulo, cuerpo, catalogo, titulo_final):
    assert construir_descripcion_catalogo(titulo, cuerpo, catalogo=catalogo) == titulo_final + "."


@pytest.mark.parametrize(
    "titulo,cuerpo,catalogo,conservado",
    [
        ("ROOT · 160-05535", "Root 160-05535 con base de aluminio", "requiez", "con base de aluminio"),
        ("ROOT · 160-05535", "Root 160-05536", "requiez", "160-05536"),
        ("Aurea Tech Cadiz 6.0mm", "Aurea Tech Cadiz 6.5 mm", "tarkett", "6.5 mm"),
    ],
)
def test_catalogo_deduplicacion_no_omite_detalles_ni_modelos_distintos(titulo, cuerpo, catalogo, conservado):
    texto = construir_descripcion_catalogo(titulo, cuerpo, catalogo=catalogo)
    assert texto.startswith(titulo.replace("6.0mm", "6.0 mm") + ".\n")
    assert conservado in texto.split("\n", 1)[1]


@pytest.mark.parametrize("color", ["NEGRA", "BLANCA"])
def test_catalogo_variante_de_color_femenino_es_color(color):
    assert construir_descripcion_catalogo("Silla A", "Variante: " + color) == (
        "Silla A.\nColor: " + color.lower() + "."
    )


def test_catalogo_variante_compuesta_sigue_siendo_configuracion():
    assert construir_descripcion_catalogo("Silla A", "Variante: NEGRA CON BRAZOS") == (
        "Silla A.\nConfiguración: negra con brazos."
    )


@pytest.mark.parametrize("componente", ["CON BRAZOS", "SIN BRAZOS", "CON CABECERA", "SIN CABECERA"])
def test_catalogo_componentes_de_configuracion_conservan_modelos(componente):
    texto = construir_descripcion_catalogo(
        "Silla A", "Variante: NEGRA " + componente + " ABC-123", catalogo="offiho",
    )
    assert texto == "Silla A.\nConfiguración: negra " + componente.lower() + " ABC-123."
