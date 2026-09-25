"""Pruebas sin red: no gastar otra vez al reutilizar o reanudar una imagen."""

import base64
import json
from io import BytesIO

import pytest
from PIL import Image

from mobiliti_saas.quote_engine import image_library as piloto
from scripts.quotation_image_trial import generar_galeria


def png(color, size=(150, 150)):
    salida = BytesIO()
    Image.new("RGB", size, color).save(salida, "PNG")
    return salida.getvalue()


def test_reutiliza_entre_proyectos_y_conserva_aprobacion(tmp_path, monkeypatch):
    ruta = tmp_path / "biblioteca.db"
    original = png("gray")
    mejorada = png("gray", (1024, 1024))
    llamadas = []

    def proveedor(url, clave, datos, tipo):
        llamadas.append(url)
        assert original in datos
        assert b"gpt-image-2.5-sunburst-2026-09-08" in datos
        return json.dumps({"data": [{"b64_json": base64.b64encode(mejorada).decode()}], "usage": {"output_tokens": 100}}).encode(), {"x-request-id": "prueba"}

    monkeypatch.setattr(piloto, "solicitar", proveedor)
    db = piloto.abrir_biblioteca(ruta)
    sha = piloto.registrar_original(db, original)
    version = piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "clave-de-prueba")
    assert piloto.obtener_aprobada(db, original) is None
    piloto.aprobar(db, version)
    db.close()
    # Otro proyecto/proceso utiliza los mismos bytes, sin depender del nombre de archivo.
    db = piloto.abrir_biblioteca(ruta)
    assert piloto.registrar_original(db, original) == sha
    assert piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "clave-de-prueba") == version
    assert piloto.obtener_aprobada(db, original) == mejorada
    assert len(llamadas) == 1
    assert db.execute("SELECT count(*) FROM originales").fetchone()[0] == 1
    db.close()


def test_solicitud_ambigua_no_repite_cobro(tmp_path, monkeypatch):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    sha = piloto.registrar_original(db, png("gray"))
    llamadas = []

    def interrumpido(*args):
        llamadas.append(1)
        raise TimeoutError("Respuesta perdida después del envío")

    monkeypatch.setattr(piloto, "solicitar", interrumpido)
    with pytest.raises(TimeoutError):
        piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "prueba")
    with pytest.raises(RuntimeError, match="sin resultado confirmado"):
        piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "prueba")
    assert len(llamadas) == 1
    db.close()


def test_reserva_unica_entre_conexiones_y_configuraciones_distintas(tmp_path):
    primera = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    segunda = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    sha = piloto.registrar_original(primera, png("gray"))
    config = piloto.CONFIGURACIONES["openai"]
    registro, nueva = piloto.reservar(primera, sha, "openai", config)
    assert nueva
    assert not piloto.reservar(segunda, sha, "openai", config)[1]
    otra, nueva = piloto.reservar(segunda, sha, "openai", {**config, "quality": "medium"})
    assert nueva and otra["id"] != registro["id"]
    with pytest.raises(ValueError, match="completa"):
        piloto.aprobar(primera, registro["id"])
    primera.close()
    segunda.close()


def test_seedvr2_reanuda_la_cola_sin_otro_post(tmp_path, monkeypatch):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    sha = piloto.registrar_original(db, png("gray"))
    configuracion = piloto.CONFIGURACIONES["seedvr2"]
    registro, _ = piloto.reservar(db, sha, "seedvr2", configuracion)
    metadata = {"request_id": "guardado", "status_url": "https://queue.fal.run/status", "response_url": "https://queue.fal.run/result"}
    with db:
        db.execute("UPDATE versiones SET estado='en_cola', metadata=? WHERE id=?", (json.dumps(metadata), registro["id"]))
    mejorada = png("gray", (1024, 1024))
    llamadas = []

    def consultar(url, clave, datos=None):
        assert datos is None
        llamadas.append(url)
        if url.endswith("status"):
            return {"status": "COMPLETED"}
        return {"image": {"url": "data:image/png;base64," + base64.b64encode(mejorada).decode()}, "seed": 20260924}

    monkeypatch.setattr(piloto, "consultar_json", consultar)
    assert piloto.ejecutar_version(db, sha, "seedvr2", configuracion, "prueba") == registro["id"]
    assert len(llamadas) == 2
    piloto.aprobar(db, registro["id"])
    assert piloto.obtener_aprobada(db, png("gray")) == mejorada
    db.close()


def test_corrupcion_no_se_reutiliza_ni_dispara_generacion(tmp_path, monkeypatch):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    original = png("gray")
    sha = piloto.registrar_original(db, original)
    version, _ = piloto.reservar(db, sha, "openai", piloto.CONFIGURACIONES["openai"])
    piloto.completar(db, version["id"], png("white"), {})
    piloto.aprobar(db, version["id"])
    with db:
        db.execute("UPDATE versiones SET imagen=? WHERE id=?", (b"corrupto", version["id"]))
    monkeypatch.setattr(piloto, "solicitar", lambda *args: pytest.fail("No debe llamar al proveedor"))
    with pytest.raises(ValueError, match="integridad"):
        piloto.obtener_aprobada(db, original)
    with pytest.raises(ValueError, match="corrupto"):
        piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "prueba")
    db.close()


def test_bibliotecas_separadas_y_originales_sin_sobrescribir(tmp_path):
    archivo = tmp_path / "original.png"
    original = png("gray")
    piloto.exportar_archivo(archivo, original)
    with pytest.raises(ValueError, match="conserva"):
        piloto.exportar_archivo(archivo, png("white"))
    assert archivo.read_bytes() == original
    primera = piloto.abrir_biblioteca(tmp_path / "cuenta-a.db")
    segunda = piloto.abrir_biblioteca(tmp_path / "cuenta-b.db")
    sha = piloto.registrar_original(primera, original)
    version, _ = piloto.reservar(primera, sha, "openai", piloto.CONFIGURACIONES["openai"])
    piloto.completar(primera, version["id"], png("white"), {})
    piloto.aprobar(primera, version["id"])
    assert piloto.obtener_aprobada(segunda, original) is None
    primera.close()
    segunda.close()


def test_envio_seedvr2_usa_original_y_parametros_y_guarda_cola(tmp_path, monkeypatch):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    original = png("gray")
    sha = piloto.registrar_original(db, original)
    envios = []

    def consultar(url, clave, datos=None):
        if datos is not None:
            envios.append(datos)
            assert base64.b64decode(datos["image_url"].split(",", 1)[1]) == original
            assert datos["upscale_factor"] == 1024 / 150
            assert "model" not in datos
            return {"request_id": "nuevo", "status_url": "https://queue.fal.run/status", "response_url": "https://queue.fal.run/result"}
        if url.endswith("status"):
            fila = db.execute("SELECT * FROM versiones").fetchone()
            assert fila["estado"] == "en_cola"
            assert json.loads(fila["metadata"])["request_id"] == "nuevo"
            return {"status": "COMPLETED"}
        return {"image": {"url": "data:image/png;base64," + base64.b64encode(png("gray", (1024, 1024))).decode()}, "seed": 20260924}

    monkeypatch.setattr(piloto, "consultar_json", consultar)
    piloto.ejecutar_version(db, sha, "seedvr2", piloto.CONFIGURACIONES["seedvr2"], "prueba")
    assert len(envios) == 1
    db.close()


@pytest.mark.parametrize("url", ["http://queue.fal.run/test", "https://otro-servidor.invalid/test"])
def test_no_envia_credenciales_a_destinos_inseguros(url):
    with pytest.raises(ValueError):
        piloto.solicitar(url, "secreto-de-prueba")


def test_galeria_pendiente_no_simula_resultados(tmp_path):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    sha = piloto.registrar_original(db, png("gray"))
    manifiesto = {"imagenes": [{"sha256": sha, "producto": "Mesa <A>", "filas": [1, 3]}], "configuraciones": piloto.CONFIGURACIONES}
    ruta = generar_galeria(db, manifiesto, tmp_path / "galeria")
    contenido = ruta.read_text(encoding="utf-8")
    assert "0/14 resultados generados" in contenido
    assert contenido.count("pendiente de generar") == 2
    assert "Mesa &lt;A&gt;" in contenido
    assert db.execute("SELECT count(*) FROM versiones").fetchone()[0] == 0
    db.close()


def test_facturacion_rechazada_se_registra_y_no_se_reintenta(tmp_path, monkeypatch):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    sha = piloto.registrar_original(db, png("gray"))
    llamadas = []

    def rechazar(*args):
        llamadas.append(1)
        raise piloto.ErrorHTTPProveedor(400, "billing_hard_limit_reached")

    monkeypatch.setattr(piloto, "solicitar", rechazar)
    with pytest.raises(piloto.ErrorHTTPProveedor, match="billing_hard_limit_reached"):
        piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "prueba")
    fila = db.execute("SELECT * FROM versiones").fetchone()
    assert fila["estado"] == "rechazada"
    assert json.loads(fila["metadata"])["code"] == "billing_hard_limit_reached"
    with pytest.raises(RuntimeError, match="sin resultado confirmado"):
        piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "prueba")
    assert len(llamadas) == 1
    db.close()


def test_reintento_autorizado_conserva_historial_y_no_duplica(tmp_path, monkeypatch):
    db = piloto.abrir_biblioteca(tmp_path / "biblioteca.db")
    original = png("gray")
    sha = piloto.registrar_original(db, original)
    version, _ = piloto.reservar(db, sha, "openai", piloto.CONFIGURACIONES["openai"])
    historial = [{"http": 400, "code": "billing_hard_limit_reached"}]
    with db:
        db.execute("UPDATE versiones SET estado='reintento_autorizado',metadata=? WHERE id=?",
                   (json.dumps({"historial": historial}), version["id"]))
    llamadas = []
    def proveedor(*args):
        llamadas.append(1)
        return json.dumps({"data": [{"b64_json": base64.b64encode(original).decode()}]}).encode(), {}
    monkeypatch.setattr(piloto, "solicitar", proveedor)
    for _ in range(2):
        piloto.ejecutar_version(db, sha, "openai", piloto.CONFIGURACIONES["openai"], "prueba")
    assert len(llamadas) == 1
    assert json.loads(db.execute("SELECT metadata FROM versiones").fetchone()[0])["historial"] == historial
    db.close()
