"""Presupuesto y cambio persistente de proveedor: sin llamadas ni cargos reales."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO

import pytest
from PIL import Image, ImageChops

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


@pytest.mark.parametrize('error', [TimeoutError('sin respuesta'), biblioteca.ErrorHTTPProveedor(401, 'invalid_api_key')])
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


def test_limite_de_frecuencia_reintenta_y_cobra_una_sola_vez(entorno, monkeypatch):
    raiz, llamadas, original = entorno
    monkeypatch.setattr(politica, 'ESPERAS_POR_FRECUENCIA', (0, 0))
    rechazos = []
    def saturado_una_vez(url, *args):
        if 'openai.com' in url and not rechazos:
            rechazos.append(url)
            raise biblioteca.ErrorHTTPProveedor(429, 'rate_limit_exceeded')
        return original(url, *args)
    monkeypatch.setattr(biblioteca, 'solicitar', saturado_una_vez)
    # Un 429 por frecuencia no generó nada: se reenvía en la misma cotización.
    assert politica.mejorar_imagen_cotizacion(foto(), 'karen') == (foto('white'), 'image/png')
    assert len(rechazos) == 1 and sum('openai.com' in url for url in llamadas) == 1
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT proveedor FROM politica').fetchone()[0] == 'openai'
        assert db.execute('SELECT SUM(micro_usd) FROM consumos').fetchone()[0] == 54070


def test_limite_de_frecuencia_persistente_no_bloquea_la_imagen(entorno, monkeypatch):
    raiz, llamadas, original = entorno
    monkeypatch.setattr(politica, 'ESPERAS_POR_FRECUENCIA', (0, 0))
    saturado = [True]
    def proveedor(url, *args):
        if 'openai.com' in url and saturado[0]:
            llamadas.append('saturado')
            raise biblioteca.ErrorHTTPProveedor(429, 'rate_limit_exceeded')
        return original(url, *args)
    monkeypatch.setattr(biblioteca, 'solicitar', proveedor)
    with pytest.raises(biblioteca.ErrorHTTPProveedor):
        politica.mejorar_imagen_cotizacion(foto(), 'karen')
    assert llamadas == ['saturado'] * 3  # el intento y sus dos reintentos
    saturado[0] = False
    # La siguiente cotización vuelve a enviarla sin intervención manual.
    assert politica.mejorar_imagen_cotizacion(foto(), 'karen')[0] == foto('white')
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT proveedor FROM politica').fetchone()[0] == 'openai'


def test_sondeo_seedvr2_saturado_conserva_la_solicitud_en_cola(entorno, monkeypatch):
    _, llamadas, original = entorno
    monkeypatch.setenv('QUOTATION_IMAGE_POLICY', 'seedvr2')
    monkeypatch.setattr(politica, 'ESPERAS_POR_FRECUENCIA', (0, 0))
    rechazos = []
    def proveedor(url, *args):
        if url.endswith('/status') and not rechazos:
            rechazos.append(url)
            raise biblioteca.ErrorHTTPProveedor(429, 'sin_codigo')
        return original(url, *args)
    monkeypatch.setattr(biblioteca, 'solicitar', proveedor)
    assert politica.mejorar_imagen_cotizacion(foto(), 'karen')[0] == foto('blue')
    # La solicitud ya encolada se consulta de nuevo; no se envía ni se cobra otra.
    assert sum(url.endswith('/fal-ai/seedvr/upscale/image') for url in llamadas) == 1


@pytest.mark.parametrize('tamano', [(101, 101), (100, 900)])
def test_seedvr2_recibe_las_fotos_pequenas_ya_agrandadas(entorno, monkeypatch, tamano):
    _, _, original = entorno
    monkeypatch.setenv('QUOTATION_IMAGE_POLICY', 'seedvr2')
    envios = []
    def proveedor(url, clave='', datos=None, tipo=None):
        if url.endswith('/fal-ai/seedvr/upscale/image'):
            cuerpo = json.loads(datos)
            imagen = Image.open(BytesIO(base64.b64decode(cuerpo['image_url'].split(',', 1)[1])))
            envios.append(imagen.size)
            # Validación de fal: al menos 128 px por lado y un factor de 1 a 10; si no, HTTP 422.
            if min(imagen.size) < 128 or not 1 <= cuerpo['upscale_factor'] <= 10:
                raise biblioteca.ErrorHTTPProveedor(422, 'sin_codigo')
        return original(url, clave, datos, tipo)
    monkeypatch.setattr(biblioteca, 'solicitar', proveedor)
    pequena = BytesIO()
    Image.new('RGB', tamano, 'gray').save(pequena, 'PNG')
    for _ in range(2):  # la segunda vez sale de la biblioteca
        assert politica.mejorar_imagen_cotizacion(pequena.getvalue(), 'karen')[0] == foto('blue')
    assert len(envios) == 1 and min(envios[0]) == 128


def test_tope_vencido_entrega_lo_guardado_y_no_pide_fotos_nuevas(entorno):
    import time

    raiz, llamadas, _ = entorno
    politica.mejorar_imagen_cotizacion(foto(), 'karen')
    vencido = time.monotonic() - 1
    # Una foto que ya está en la biblioteca no depende del tiempo que quede.
    assert politica.mejorar_imagen_cotizacion(foto(), 'karen', limite=vencido) == (foto('white'), 'image/png')
    with pytest.raises(politica.PlazoVencido):
        politica.mejorar_imagen_cotizacion(foto('red'), 'karen', limite=vencido)
    assert len(llamadas) == 1
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT COUNT(*) FROM consumos').fetchone()[0] == 1
    # Con tiempo de sobra se pide normalmente.
    assert politica.mejorar_imagen_cotizacion(foto('red'), 'karen', limite=time.monotonic() + 60)[1] == 'image/png'
    assert len(llamadas) == 2


def test_seis_hilos_comparten_biblioteca_y_presupuesto(entorno, monkeypatch):
    import time

    raiz, llamadas, original = entorno
    def lento(url, *args):
        time.sleep(0.05)  # deja que las llamadas se encimen como con el proveedor real
        return original(url, *args)
    monkeypatch.setattr(biblioteca, 'solicitar', lento)
    colores = ['red', 'green', 'blue', 'gray', 'black', 'orange', 'purple', 'brown', 'pink', 'olive', 'navy', 'teal']
    with ThreadPoolExecutor(max_workers=6) as pool:
        resultados = list(pool.map(lambda color: politica.mejorar_imagen_cotizacion(foto(color), 'karen'), colores))
    assert resultados == [(foto('white'), 'image/png')] * len(colores)
    assert len(llamadas) == len(colores)
    with closing(politica._abrir_presupuesto(raiz)) as db:
        assert db.execute('SELECT COUNT(*), SUM(micro_usd) FROM consumos').fetchone() == (12, 12 * 54070)
    # En otra cotización todas salen de la biblioteca, sin llamadas nuevas.
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda color: politica.mejorar_imagen_cotizacion(foto(color), 'karen'), colores))
    assert len(llamadas) == len(colores)


def test_configuracion_incompleta_se_detecta_antes_de_pedir_fotos(entorno, monkeypatch):
    raiz, llamadas, _ = entorno
    politica.verificar_configuracion('karen')
    with pytest.raises(RuntimeError, match='autenticada'):
        politica.verificar_configuracion('')
    monkeypatch.setenv('QUOTATION_IMAGE_LIBRARY_DIR', 'relativa')
    with pytest.raises(RuntimeError, match='QUOTATION_IMAGE_LIBRARY_DIR'):
        politica.verificar_configuracion('karen')
    # Una carpeta que no se puede crear: la biblioteca no podría guardar nada.
    (raiz / 'archivo').write_bytes(b'x')
    monkeypatch.setenv('QUOTATION_IMAGE_LIBRARY_DIR', str(raiz / 'archivo' / 'biblioteca'))
    with pytest.raises(RuntimeError, match='biblioteca'):
        politica.verificar_configuracion('karen')
    assert llamadas == []


def test_configuracion_exige_las_claves_del_proveedor_que_toca(entorno, monkeypatch):
    raiz, _, _ = entorno
    monkeypatch.setenv('FAL_KEY', '')
    with pytest.raises(RuntimeError, match='FAL_KEY'):
        politica.verificar_configuracion('karen')
    monkeypatch.setenv('FAL_KEY', 'prueba')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    with pytest.raises(RuntimeError, match='OPENAI_API_KEY'):
        politica.verificar_configuracion('karen')
    # Agotado el saldo de OpenAI, su clave deja de hacer falta.
    with closing(politica._abrir_presupuesto(raiz)) as db, db:
        db.execute("UPDATE politica SET proveedor='seedvr2' WHERE id=1")
    politica.verificar_configuracion('karen')


def test_configuracion_exige_el_presupuesto_solo_con_openai(tmp_path, monkeypatch):
    monkeypatch.setenv('QUOTATION_IMAGE_LIBRARY_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', 'prueba')
    monkeypatch.setenv('FAL_KEY', 'prueba')
    monkeypatch.delenv('QUOTATION_OPENAI_BUDGET_USD', raising=False)
    monkeypatch.delenv('QUOTATION_OPENAI_PREVIOUS_USD', raising=False)
    monkeypatch.setenv('QUOTATION_IMAGE_POLICY', 'openai_then_seedvr2')
    with pytest.raises(RuntimeError, match='presupuesto'):
        politica.verificar_configuracion('karen')
    monkeypatch.setenv('QUOTATION_IMAGE_POLICY', 'seedvr2')
    politica.verificar_configuracion('karen')


def test_reintento_por_frecuencia_no_espera_mas_alla_del_tope(entorno, monkeypatch):
    import time

    _, llamadas, _ = entorno
    monkeypatch.setattr(politica, 'ESPERAS_POR_FRECUENCIA', (30, 30))
    monkeypatch.setattr(politica.time, 'sleep', lambda segundos: pytest.fail('no debe esperar'))
    def saturado(url, *args):
        llamadas.append('saturado')
        raise biblioteca.ErrorHTTPProveedor(429, 'rate_limit_exceeded')
    monkeypatch.setattr(biblioteca, 'solicitar', saturado)
    with pytest.raises(biblioteca.ErrorHTTPProveedor):
        politica.mejorar_imagen_cotizacion(foto(), 'karen', limite=time.monotonic() + 5)
    assert llamadas == ['saturado']


def test_sondeo_seedvr2_se_detiene_tras_el_tope_y_se_reanuda_despues(entorno, monkeypatch):
    import time

    _, llamadas, original = entorno
    monkeypatch.setenv('QUOTATION_IMAGE_POLICY', 'seedvr2')
    monkeypatch.setattr(politica, 'GRACIA_EN_CURSO', 0.05)
    dormir = time.sleep
    monkeypatch.setattr(biblioteca.time, 'sleep', lambda segundos: dormir(0.02))
    pendiente = [True]
    def proveedor(url, *args):
        if url.endswith('/status') and pendiente[0]:
            llamadas.append(url)
            return b'{"status":"IN_PROGRESS"}', {}
        return original(url, *args)
    monkeypatch.setattr(biblioteca, 'solicitar', proveedor)
    inicio = time.monotonic()
    with pytest.raises(RuntimeError, match='sigue pendiente'):
        politica.mejorar_imagen_cotizacion(foto(), 'karen', limite=inicio + 0.05)
    assert time.monotonic() - inicio < 2
    pendiente[0] = False
    # La siguiente cotización retoma la misma solicitud: no se envía ni se cobra otra.
    assert politica.mejorar_imagen_cotizacion(foto(), 'karen')[0] == foto('blue')
    assert sum(url.endswith('/fal-ai/seedvr/upscale/image') for url in llamadas) == 1


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


def test_openai_recibe_el_producto_centrado_y_se_entrega_sin_blanco_sobrante(entorno, monkeypatch):
    _, llamadas, original = entorno
    enviadas = []
    def proveedor(url, clave='', datos=None, tipo=None):
        if 'openai.com' not in url:
            return original(url, clave, datos, tipo)
        llamadas.append(url)
        assert b'name="quality"\r\n\r\nmedium\r\n' in datos and b'name="background"\r\n\r\nopaque\r\n' in datos
        enviadas.append(Image.open(BytesIO(datos[datos.index(b'\x89PNG'):])).convert('RGB'))
        resultado = Image.new('RGB', (1024, 1024), 'white')
        resultado.paste((40, 40, 40), (300, 200, 700, 800))
        salida = BytesIO()
        resultado.save(salida, 'PNG')
        return json.dumps({"data": [{"b64_json": base64.b64encode(salida.getvalue()).decode()}],
            "usage": {"input_tokens_details": {"image_tokens": 300, "text_tokens": 330}, "output_tokens": 439}}).encode(), {}
    monkeypatch.setattr(biblioteca, 'solicitar', proveedor)
    silla = Image.new('RGBA', (150, 150), (0, 0, 0, 0))
    silla.paste((90, 90, 90, 255), (50, 40, 80, 110))  # 30x70 px de producto y mucho margen transparente
    datos = BytesIO()
    silla.save(datos, 'PNG')
    for _ in range(2):  # la segunda vez sale de la biblioteca, igual de recortada
        entregada = Image.open(BytesIO(politica.mejorar_imagen_cotizacion(datos.getvalue(), 'karen')[0]))
        assert entregada.size == (436, 636)  # 400x600 de producto más 3 % por lado
    assert len(llamadas) == 1
    entrada = enviadas[0]
    assert entrada.size == (512, 512) and entrada.getpixel((5, 5)) == (255, 255, 255)
    x0, y0, x1, y1 = ImageChops.difference(entrada, Image.new('RGB', entrada.size, 'white')).convert('L').point(lambda p: 255 if p > 30 else 0).getbbox()
    assert abs((y1 - y0) / 512 - politica.LLENADO_ENTRADA) < 0.02 and abs((x0 + x1) / 2 - 256) < 3


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
