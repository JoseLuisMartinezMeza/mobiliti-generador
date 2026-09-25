"""Presupuesto y cambio persistente de proveedor: sin llamadas ni cargos reales."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO

import pytest
from PIL import Image

from mobiliti_saas.quote_engine import quotation_image_policy as politica
from mobiliti_saas.quote_engine import image_library as biblioteca


def foto(color="gray", formato="PNG"):
    salida = BytesIO()
    Image.new("RGB", (150, 150), color).save(salida, formato)
    return salida.getvalue()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    for nombre, valor in {
        "QUOTATION_IMAGE_POLICY": "openai_then_seedvr2",
        "QUOTATION_IMAGE_LIBRARY_DIR": str(tmp_path),
        "QUOTATION_OPENAI_BUDGET_USD": "30",
        "QUOTATION_OPENAI_PREVIOUS_USD": "0.40",
        "OPENAI_API_KEY": "prueba", "FAL_KEY": "prueba",
    }.items():
        monkeypatch.setenv(nombre, valor)
    llamadas = []
    def solicitar(url, clave="", datos=None, tipo=None):
        llamadas.append(url)
        if "openai.com" in url:
            return json.dumps({"data": [{"b64_json": base64.b64encode(foto("white")).decode()}],
                "usage": {"input_tokens_details": {"image_tokens": 100, "text_tokens": 118}, "output_tokens": 1756}}).encode(), {}
        if url.endswith('/status'):
            return b'{"status":"COMPLETED"}', {}
        if url.endswith('/result'):
            return json.dumps({"image": {"url": "data:image/png;base64," + base64.b64encode(foto("blue")).decode()}}).encode(), {}
        return b'{"request_id":"test","status_url":"https://queue.fal.run/status","response_url":"https://queue.fal.run/result"}', {}
    monkeypatch.setattr(biblioteca, "solicitar", solicitar)
    return tmp_path, llamadas, solicitar


def test_prioridad_openai_cache_entre_proyectos_y_aislamiento(entorno):
    raiz, llamadas, _ = entorno
    for _ in range(2):
        assert politica.mejorar_imagen_cotizacion(foto(), "karen") == (foto("white"), "image/png")
    assert len(llamadas) == 1
    politica.mejorar_imagen_cotizacion(foto(), "otra-cuenta")
    assert len(llamadas) == 2
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT SUM(micro_usd) FROM consumos').fetchone()[0] == 108140


@pytest.mark.parametrize("codigo", sorted(politica.ERRORES_SALDO))
def test_saldo_agotado_cambia_misma_imagen_y_persiste(entorno, monkeypatch, codigo):
    raiz, llamadas, original = entorno
    def sin_saldo(url, *args):
        if "openai.com" in url:
            llamadas.append(url)
            raise biblioteca.ErrorHTTPProveedor(429, codigo)
        return original(url, *args)
    monkeypatch.setattr(biblioteca, "solicitar", sin_saldo)
    assert politica.mejorar_imagen_cotizacion(foto(), "karen")[0] == foto("blue")
    politica.mejorar_imagen_cotizacion(foto("red"), "otra-cuenta")
    assert sum('openai.com' in url for url in llamadas) == 1
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT proveedor FROM politica').fetchone()[0] == 'seedvr2'


def test_tope_incluye_consumo_previo_y_conserva_imagen_openai(entorno, monkeypatch):
    raiz, llamadas, _ = entorno
    monkeypatch.setenv('QUOTATION_OPENAI_BUDGET_USD', '0.51')
    politica.mejorar_imagen_cotizacion(foto(), 'karen')
    assert politica.mejorar_imagen_cotizacion(foto('red'), 'karen')[0] == foto('blue')
    assert politica.mejorar_imagen_cotizacion(foto(), 'karen')[0] == foto('white')
    # Cambiar la variable no reinicia un presupuesto ya persistido.
    monkeypatch.setenv('QUOTATION_OPENAI_BUDGET_USD', '30')
    politica.mejorar_imagen_cotizacion(foto('green'), 'karen')
    assert sum('openai.com' in url for url in llamadas) == 1


@pytest.mark.parametrize('error', [TimeoutError('sin respuesta'), biblioteca.ErrorHTTPProveedor(429, 'rate_limit_exceeded'), biblioteca.ErrorHTTPProveedor(401, 'invalid_api_key')])
def test_error_temporal_o_credencial_no_cambia_ni_reenvia(entorno, monkeypatch, error):
    raiz, llamadas, _ = entorno
    def fallo(*args):
        llamadas.append(1)
        raise error
    monkeypatch.setattr(biblioteca, 'solicitar', fallo)
    with pytest.raises(type(error)):
        politica.mejorar_imagen_cotizacion(foto(), 'karen')
    with pytest.raises(RuntimeError, match='sin resultado confirmado'):
        politica.mejorar_imagen_cotizacion(foto(), 'karen')
    assert llamadas == [1]
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT proveedor FROM politica').fetchone()[0] == 'openai'
        assert db.execute('SELECT SUM(micro_usd) FROM consumos').fetchone()[0] == 100000


def test_reserva_atomica_entre_workers(entorno, monkeypatch):
    raiz, _, _ = entorno
    monkeypatch.setenv('QUOTATION_OPENAI_BUDGET_USD', '0.50')
    with closing(politica._abrir_presupuesto(raiz)):
        pass
    def reservar(numero):
        with closing(politica._abrir_presupuesto(raiz)) as db:
            return politica._reservar_gasto(db, str(numero))
    with ThreadPoolExecutor(max_workers=4) as pool:
        resultados = list(pool.map(reservar, range(4)))
    assert sum(resultados) == 1


def test_tarifa_inesperada_detiene_nuevas_solicitudes_openai(entorno, monkeypatch):
    _, llamadas, original = entorno
    def caro(url, *args):
        respuesta, headers = original(url, *args)
        if 'openai.com' in url:
            payload = json.loads(respuesta)
            payload['usage']['output_tokens'] = 4000
            respuesta = json.dumps(payload).encode()
        return respuesta, headers
    monkeypatch.setattr(biblioteca, 'solicitar', caro)
    politica.mejorar_imagen_cotizacion(foto(), 'karen')
    assert politica.mejorar_imagen_cotizacion(foto('red'), 'karen')[0] == foto('blue')
    assert sum('openai.com' in url for url in llamadas) == 1
    assert politica._costo_reportado({}) == politica.RESERVA_MICRO_USD


def test_jpeg_sin_modificar_entrada_y_sin_cuenta_no_envia(entorno, monkeypatch):
    _, llamadas, original = entorno
    datos = foto(formato='JPEG')
    def comprobar(url, clave='', body=None, tipo=None):
        if 'openai.com' in url:
            assert datos in body and b'image/jpeg' in body
        return original(url, clave, body, tipo)
    monkeypatch.setattr(biblioteca, 'solicitar', comprobar)
    with pytest.raises(RuntimeError, match='autenticada'):
        politica.mejorar_imagen_cotizacion(datos, '')
    assert not llamadas
    politica.mejorar_imagen_cotizacion(datos, 'karen')
    assert len(llamadas) == 1


def test_excel_general_y_cdmx_reutilizan_biblioteca_y_preservan_quotation(entorno, monkeypatch):
    from openpyxl import load_workbook
    from mobiliti_saas.quote_engine import engine
    from mobiliti_saas.quote_engine.template_profiles import resolve_template_profile
    from quotation_import_fixtures import write_import_fixture

    raiz, llamadas, _ = entorno
    monkeypatch.setenv('QUOTATION_OPENAI_BUDGET_USD', '0.51')
    fuente = write_import_fixture(raiz / 'quotation.xlsx', currency='USD')
    original_sha = biblioteca.huella(fuente.read_bytes())
    imagenes_originales = {biblioteca.huella(datos) for datos, _ in engine._source_product_images(engine._normalized_quotation_source(fuente)).values()}
    cantidad_llamadas = None
    for perfil in ('official_2026_gdl', 'sunon_cdmx_v1c'):
        salida = raiz / (perfil + '.xlsx')
        engine.generate_quote(fuente, salida, {'_image_library_account': 'karen', 'exchange_rate': '18.5'},
                              resolve_template_profile(perfil).template_path)
        assert biblioteca.huella(fuente.read_bytes()) == original_sha
        preservadas = {biblioteca.huella(datos) for datos, _ in engine._source_product_images(salida).values()}
        assert imagenes_originales <= preservadas
        libro = load_workbook(salida, data_only=False)
        try:
            imagenes_cotizacion = {biblioteca.huella(imagen._data()) for imagen in libro['Cotizacion']._images}
            assert biblioteca.huella(foto('white')) in imagenes_cotizacion
            assert biblioteca.huella(foto('blue')) in imagenes_cotizacion
            assert any(c.data_type == 'f' for row in libro['Cotizacion'] for c in row)
        finally:
            libro.close()
        if cantidad_llamadas is not None:
            assert len(llamadas) == cantidad_llamadas
        cantidad_llamadas = len(llamadas)
    assert sum('openai.com' in url for url in llamadas) == 1
