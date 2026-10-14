"""OpenAI hasta el presupuesto autorizado; SeedVR2 después, con biblioteca privada."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops

from . import image_library as biblioteca


ERRORES_SALDO = biblioteca.CODIGOS_SALDO
# Segundos de espera antes de cada reintento cuando el proveedor responde 429 por frecuencia.
ESPERAS_POR_FRECUENCIA = (8, 20)
# Segundos que una cotización espera, pasado su tope, a lo que ya estaba en curso.
GRACIA_EN_CURSO = 45.0
# Reserva conservadora para una entrada <=1024 y una salida 1024 de calidad high o menor, n=1.
RESERVA_MICRO_USD = 100_000
# Entrada a OpenAI comparada el 2026-10-13: producto al 92 % de un cuadro blanco de 512 px.
LLENADO_ENTRADA = 0.92
LADO_ENTRADA = 512
# Margen que se deja alrededor del producto al recortar el blanco sobrante del resultado.
MARGEN_SALIDA = 0.03


def _entrada_openai(datos: bytes) -> bytes | None:
    """Centra el producto según su transparencia; una foto sin transparencia se envía tal cual."""
    with Image.open(BytesIO(datos)) as imagen:
        rgba = imagen.convert("RGBA")
    alfa = rgba.getchannel("A")
    caja = alfa.point(lambda p: 255 if p else 0).getbbox()
    if alfa.getextrema()[0] == 255 or not caja:
        return None
    x0, y0, x1, y1 = caja
    lado = round(max(x1 - x0, y1 - y0) / LLENADO_ENTRADA)
    blanca = Image.new("RGBA", rgba.size, "white")
    blanca.alpha_composite(rgba)
    lienzo = Image.new("RGB", (lado, lado), "white")
    lienzo.paste(blanca.convert("RGB"), (-((x0 + x1 - lado) // 2), -((y0 + y1 - lado) // 2)))
    salida = BytesIO()
    lienzo.resize((LADO_ENTRADA, LADO_ENTRADA), Image.LANCZOS).save(salida, "PNG")
    return salida.getvalue()


def _recortar_margen(datos: bytes) -> bytes:
    """Quita el blanco sobrante alrededor del producto; nunca agrega píxeles."""
    with Image.open(BytesIO(datos)) as imagen:
        imagen.load()
    blanca = Image.new("RGBA", imagen.size, "white")
    blanca.alpha_composite(imagen.convert("RGBA"))
    diferencia = ImageChops.difference(blanca.convert("RGB"), Image.new("RGB", imagen.size, "white")).convert("L")
    caja = diferencia.point(lambda p: 255 if p > 8 else 0).getbbox()
    if not caja:
        return datos
    x0, y0, x1, y1 = caja
    margen = round(max(x1 - x0, y1 - y0) * MARGEN_SALIDA)
    ventana = (max(x0 - margen, 0), max(y0 - margen, 0), min(x1 + margen, imagen.width), min(y1 + margen, imagen.height))
    if ventana == (0, 0, *imagen.size):
        return datos
    salida = BytesIO()
    imagen.crop(ventana).save(salida, "PNG")
    return salida.getvalue()


class PlazoVencido(RuntimeError):
    """La foto no está en la biblioteca y ya no hay tiempo para pedirla al proveedor."""


def politica_activa() -> bool:
    valor = os.environ.get("QUOTATION_IMAGE_POLICY", "off")
    if valor not in {"off", "openai_then_seedvr2", "seedvr2"}:
        raise RuntimeError("QUOTATION_IMAGE_POLICY inválida")
    return valor != "off"


def _micro_usd(nombre: str) -> int:
    valor = Decimal(os.environ[nombre])
    if not valor.is_finite() or valor < 0:
        raise ValueError(f"{nombre} debe ser un importe no negativo")
    return int(valor * 1_000_000)


def _abrir_presupuesto(raiz: Path) -> sqlite3.Connection:
    db = sqlite3.connect(raiz / "presupuesto.db", timeout=30)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS politica (
            id INTEGER PRIMARY KEY CHECK(id=1), limite INTEGER NOT NULL,
            previo INTEGER NOT NULL, proveedor TEXT NOT NULL, motivo TEXT);
        CREATE TABLE IF NOT EXISTS consumos (
            id TEXT PRIMARY KEY, micro_usd INTEGER NOT NULL);
    """)
    if not db.execute("SELECT 1 FROM politica WHERE id=1").fetchone():
        limite = _micro_usd("QUOTATION_OPENAI_BUDGET_USD")
        previo = _micro_usd("QUOTATION_OPENAI_PREVIOUS_USD")
        with db:
            db.execute("INSERT OR IGNORE INTO politica VALUES (1, ?, ?, 'openai', NULL)", (limite, previo))
    return db


def _reservar_gasto(db: sqlite3.Connection, identificador: str) -> bool:
    # ponytail: presupuesto SQLite compartido en un host; DB central si se añaden hosts.
    with db:
        db.execute("BEGIN IMMEDIATE")
        limite, previo, proveedor = db.execute("SELECT limite, previo, proveedor FROM politica WHERE id=1").fetchone()
        if proveedor == "seedvr2":
            return False
        if db.execute("SELECT 1 FROM consumos WHERE id=?", (identificador,)).fetchone():
            return True  # La biblioteca impide reenviar una solicitud ambigua.
        gasto = db.execute("SELECT COALESCE(SUM(micro_usd),0) FROM consumos").fetchone()[0]
        if previo + gasto + RESERVA_MICRO_USD > limite:
            db.execute("UPDATE politica SET proveedor='seedvr2', motivo='presupuesto' WHERE id=1")
            return False
        db.execute("INSERT INTO consumos VALUES (?, ?)", (identificador, RESERVA_MICRO_USD))
        return True


def _costo_reportado(metadata: dict) -> int:
    uso = metadata.get("usage") or {}
    detalle = uso.get("input_tokens_details") or {}
    valores = [detalle.get("image_tokens"), detalle.get("text_tokens"), uso.get("output_tokens")]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in valores):
        return RESERVA_MICRO_USD  # No liberar reserva sin contabilidad completa.
    imagen, texto, salida = valores
    return imagen * 8 + texto * 5 + salida * 30


def _raiz_biblioteca(cuenta: str) -> Path:
    if not cuenta:
        raise RuntimeError("Falta la cuenta autenticada para la biblioteca de imágenes")
    ruta = os.environ.get("QUOTATION_IMAGE_LIBRARY_DIR", "")
    if not ruta or not Path(ruta).is_absolute():
        raise RuntimeError("Se requiere QUOTATION_IMAGE_LIBRARY_DIR absoluta y persistente")
    return Path(ruta)


def verificar_configuracion(cuenta: str) -> None:
    """Falla pronto por lo que afectaría a todas las fotos de la cotización, no solo a una."""
    raiz = _raiz_biblioteca(cuenta)
    proveedor = "seedvr2"
    try:
        with closing(biblioteca.abrir_biblioteca(raiz / biblioteca.huella(cuenta.encode()) / "biblioteca.db")):
            pass
        if os.environ.get("QUOTATION_IMAGE_POLICY") == "openai_then_seedvr2":
            with closing(_abrir_presupuesto(raiz)) as presupuesto:
                proveedor = presupuesto.execute("SELECT proveedor FROM politica WHERE id=1").fetchone()[0]
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError("La biblioteca de imágenes no se puede abrir o escribir") from error
    except (KeyError, ArithmeticError, ValueError) as error:
        raise RuntimeError("Falta configurar el presupuesto de OpenAI") from error
    # Agotado el saldo de OpenAI su clave deja de hacer falta; SeedVR2 es siempre el respaldo.
    for nombre in (("OPENAI_API_KEY",) if proveedor == "openai" else ()) + ("FAL_KEY",):
        if not os.environ.get(nombre):
            raise RuntimeError(f"Falta {nombre} para mejorar las imágenes")


def _ejecutar_version(limite: float | None, *argumentos) -> str:
    hasta = None if limite is None else limite + GRACIA_EN_CURSO
    for espera in (*ESPERAS_POR_FRECUENCIA, None):
        try:
            return biblioteca.ejecutar_version(*argumentos, hasta=hasta)
        except biblioteca.ErrorHTTPProveedor as exc:
            if exc.estado != 429 or exc.codigo in ERRORES_SALDO or espera is None:
                raise
            if limite is not None and time.monotonic() + espera > limite:
                raise  # queda reenviable; se pedirá en la siguiente cotización
            time.sleep(espera)


def mejorar_imagen_cotizacion(datos: bytes, cuenta: str, *, limite: float | None = None) -> tuple[bytes, str]:
    """Reutiliza por cuenta y contenido; los fallos no cambian silenciosamente de proveedor.

    ``limite`` es un instante de ``time.monotonic()``: pasado ese momento solo se
    entrega lo que ya está en la biblioteca y lo demás lanza ``PlazoVencido``.
    """
    raiz = _raiz_biblioteca(cuenta)
    if len(datos) > biblioteca.MAX_BYTES:
        raise RuntimeError("Imagen demasiado grande para la biblioteca")
    with Image.open(BytesIO(datos)) as imagen:
        if imagen.format not in {"PNG", "JPEG"} or imagen.width * imagen.height > 25_000_000:
            raise RuntimeError("Imagen de cotización no admitida")
        ancho, alto = imagen.size
        tipo = "image/png" if imagen.format == "PNG" else "image/jpeg"
        imagen.verify()
    # Las fotografías que ya tienen suficiente resolución no necesitan otro cobro.
    if max(ancho, alto) >= 1024:
        return datos, tipo
    cuenta_hash = biblioteca.huella(cuenta.encode())
    with closing(biblioteca.abrir_biblioteca(raiz / cuenta_hash / "biblioteca.db")) as db:
        original = biblioteca.registrar_original(db, datos)
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS seleccionadas (original TEXT PRIMARY KEY, version TEXT NOT NULL)")
        seleccion = db.execute("SELECT version FROM seleccionadas WHERE original=?", (original,)).fetchone()
        if seleccion:
            fila = db.execute("SELECT * FROM versiones WHERE id=?", seleccion).fetchone()
            if not fila or fila['estado'] != 'completada' or biblioteca.huella(fila['imagen']) != fila['sha']:
                raise RuntimeError("La imagen guardada no conserva su integridad")
            return _recortar_margen(fila['imagen']), "image/png"
        if limite is not None and time.monotonic() >= limite:
            raise PlazoVencido("Sin tiempo para mejorar esta imagen en la cotización")

        proveedor = "seedvr2"
        if os.environ.get("QUOTATION_IMAGE_POLICY") == "openai_then_seedvr2":
            config = dict(biblioteca.CONFIGURACIONES["openai"])
            centrada = _entrada_openai(datos)
            # La selección se sigue guardando con la huella de la foto original.
            entrada_openai = biblioteca.registrar_original(db, centrada) if centrada else original
            version_openai = biblioteca.clave_version(entrada_openai, "openai", config)
            cargo = cuenta_hash + ":" + version_openai
            with closing(_abrir_presupuesto(raiz)) as presupuesto:
                # Recuperar un resultado previo a un reinicio antes de hacer otra reserva.
                anterior = db.execute("SELECT estado, metadata FROM versiones WHERE id=?", (version_openai,)).fetchone()
                if anterior and anterior[0] == 'rechazada' and json.loads(anterior[1]).get('code') in ERRORES_SALDO:
                    with presupuesto:
                        presupuesto.execute("UPDATE politica SET proveedor='seedvr2', motivo='saldo' WHERE id=1")
                if anterior and anterior[0] == 'completada':
                    proveedor = "openai"
                    version = version_openai
                elif _reservar_gasto(presupuesto, cargo):
                    try:
                        version = _ejecutar_version(limite, db, entrada_openai, "openai", config, os.environ.get("OPENAI_API_KEY", ""))
                        proveedor = "openai"
                    except biblioteca.ErrorHTTPProveedor as exc:
                        if exc.codigo not in ERRORES_SALDO:
                            raise
                        with presupuesto:
                            presupuesto.execute("UPDATE consumos SET micro_usd=0 WHERE id=?", (cargo,))
                            presupuesto.execute("UPDATE politica SET proveedor='seedvr2', motivo=? WHERE id=1", (exc.codigo,))
                if proveedor == "openai":
                    meta = json.loads(db.execute("SELECT metadata FROM versiones WHERE id=?", (version,)).fetchone()[0])
                    costo = _costo_reportado(meta)
                    with presupuesto:
                        presupuesto.execute("UPDATE consumos SET micro_usd=? WHERE id=?", (costo, cargo))
                        if costo > RESERVA_MICRO_USD:
                            presupuesto.execute("UPDATE politica SET proveedor='seedvr2', motivo='revisar_tarifa' WHERE id=1")
        if proveedor == "seedvr2":
            entrada = original
            # fal responde HTTP 422 si un lado mide menos de 128 px o el factor sale de 1 a 10.
            if min(ancho, alto) < 128:
                escala = 128 / min(ancho, alto)
                ancho, alto = round(ancho * escala), round(alto * escala)
                agrandada = BytesIO()
                with Image.open(BytesIO(datos)) as imagen:
                    imagen.convert("RGBA").resize((ancho, alto), Image.LANCZOS).save(agrandada, "PNG")
                entrada = biblioteca.registrar_original(db, agrandada.getvalue())
            config = {**biblioteca.CONFIGURACIONES["seedvr2"], "upscale_factor": max(1024 / max(ancho, alto), 1)}
            version = _ejecutar_version(limite, db, entrada, proveedor, config, os.environ.get("FAL_KEY", ""))
        fila = db.execute("SELECT imagen, sha, estado FROM versiones WHERE id=?", (version,)).fetchone()
        if fila['estado'] != 'completada' or biblioteca.huella(fila['imagen']) != fila['sha']:
            raise RuntimeError("Resultado de imagen inválido")
        with db:
            db.execute("INSERT OR IGNORE INTO seleccionadas VALUES (?, ?)", (original, version))
        return _recortar_margen(fila['imagen']), "image/png"
