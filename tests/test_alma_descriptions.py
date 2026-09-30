"""Traducciones ALMA: fidelidad de modelos, componentes, variantes y acabados."""

import pytest

from mobiliti_saas.quote_engine.alma_descriptions import redactar_texto_alma, titulo_alma


@pytest.mark.parametrize("fuente,esperado", [
    ("STRING Dining Side Chair Frame: Alu in powder coating S&B: PVC Rope",
     "STRING silla de comedor sin brazos. Estructura: aluminio con pintura en polvo. Asiento y respaldo: cuerda de PVC"),
    ("RUGBY Lounge Armchair Arm and Rim: Polarwood (Faux Teak)",
     "RUGBY sillón de descanso. Brazos y borde: Polarwood (imitación teca)"),
    ("PILLOW Coffee Table Top: Alu in teaklook",
     "PILLOW mesa de centro. Cubierta: aluminio con acabado imitación teca"),
    ("LOOP Dining Armchair S&B: 7mm PVC Rope",
     "LOOP silla de comedor con brazos. Asiento y respaldo: cuerda de PVC de 7 mm"),
    ("Decorative Pillow", "Cojín decorativo"),
    ("Lumbar Pillow", "Cojín lumbar"),
    ("BANYAN TREE Footstool", "BANYAN TREE reposapiés"),
    ("JUMBO Sunlounger, without wheels", "JUMBO camastro, sin ruedas"),
    ("LOTUS Round Coffee Table Ceramic Top:", "LOTUS mesa de centro redonda. Cubierta cerámica"),
])
def test_redaccion_de_tipos_y_materiales(fuente, esperado):
    assert redactar_texto_alma(fuente) == esperado


def test_titulo_no_queda_cortado_a_mitad_de_etiqueta():
    assert titulo_alma("RUGBY Lounge Armchair Arm and Rim: Teak") == "RUGBY sillón de descanso"
    assert titulo_alma("PILLOW Sunlounger with Wheels Rim and Wheels: Alu in teaklook") == "PILLOW camastro con ruedas"


def test_aluminio_imitacion_no_se_convierte_en_madera():
    texto = redactar_texto_alma("Palmer lounge armchair Frame: aluminium in TEAK look Back: strap")
    assert "aluminio con acabado imitación teca" in texto
    assert "teca natural" not in texto


def test_conserva_acabados_comerciales_literalmente():
    texto = redactar_texto_alma("BAGEL Rectangular Dining Table (S) Ceramic Top: Jenny Grey / Feather White / Black / Sharo Blue")
    for valor in ("BAGEL", "(S)", "Jenny Grey", "Feather White", "Sharo Blue"):
        assert valor in texto
    assert "mesa de comedor rectangular" in texto


def test_conserva_codigos_tecnicos_de_tapizado():
    texto = redactar_texto_alma("PILLOW Sunlounger with Wheels Seat Support: BATYLINE 7407-5001 / 5005 / 5015")
    assert "soporte del asiento" in texto.casefold()
    assert "BATYLINE 7407-5001 / 5005 / 5015" in texto


def test_teca_real_y_sintetica_se_distinguen():
    natural = redactar_texto_alma("VATICAN II dining armchair Legs: Real teak")
    sintetica = redactar_texto_alma("Vatican dining armchair Back: synthetic wood slats")
    assert "teca natural" in natural
    assert "listones de madera sintética" in sintetica


def test_cinta_conserva_espesor_y_lateralidad():
    texto = redactar_texto_alma("Marrakech Meridienne Right Short Arm,PVC big strap 45mm")
    assert "brazo corto derecho" in texto
    assert "45 mm" in texto and "PVC" in texto
    assert "izquierdo" not in texto


def test_traduccion_no_agrega_caracteristicas_comerciales():
    texto = redactar_texto_alma("STRING Lounge Chair")
    assert texto == "STRING silla de descanso"
    assert not any(v in texto for v in ("ergonómica", "resistente", "exterior", "aluminio"))


def test_correccion_de_frase_pegada_no_pierde_material():
    texto = redactar_texto_alma("ENZO bar bart with wheelsand ceramic topThe middle divider andbottom are alu in heattransfer teak color")
    assert "carrito de bar" in texto and "ruedas" in texto
    assert "divisor intermedio" in texto.casefold() and "parte inferior" in texto
    assert "aluminio" in texto and "transferencia térmica" in texto


def test_traducir_texto_vacio_no_inventa_descripcion():
    assert redactar_texto_alma("") == ""
