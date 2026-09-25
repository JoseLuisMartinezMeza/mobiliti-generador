"""OpenAI hasta el presupuesto autorizado; SeedVR2 después, con biblioteca privada."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import image_library as biblioteca


ERRORES_SALDO = {"credit_balance_exhausted", "billing_hard_limit_reached", "insufficient_quota"}
# Reserva conservadora para una entrada <=1024 y una salida high 1024, n=1.
RESERVA_MICRO_USD = 100_000


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


def mejorar_imagen_cotizacion(datos: bytes, cuenta: str) -> tuple[bytes, str]:
    """Reutiliza por cuenta y contenido; los fallos no cambian silenciosamente de proveedor."""
    if not cuenta:
        raise RuntimeError("Falta la cuenta autenticada para la biblioteca de imágenes")
    ruta = os.environ.get("QUOTATION_IMAGE_LIBRARY_DIR", "")
    if not ruta or not Path(ruta).is_absolute():
        raise RuntimeError("Se requiere QUOTATION_IMAGE_LIBRARY_DIR absoluta y persistente")
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
    raiz = Path(ruta)
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
            return fila['imagen'], "image/png"

        proveedor = "seedvr2"
        config = dict(biblioteca.CONFIGURACIONES["openai"])
        version_openai = biblioteca.clave_version(original, "openai", config)
        cargo = cuenta_hash + ":" + version_openai
        if os.environ.get("QUOTATION_IMAGE_POLICY") == "openai_then_seedvr2":
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
                        version = biblioteca.ejecutar_version(db, original, "openai", config, os.environ.get("OPENAI_API_KEY", ""))
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
            config = {**biblioteca.CONFIGURACIONES["seedvr2"], "upscale_factor": 1024 / max(ancho, alto)}
            version = biblioteca.ejecutar_version(db, original, proveedor, config, os.environ.get("FAL_KEY", ""))
        fila = db.execute("SELECT imagen, sha, estado FROM versiones WHERE id=?", (version,)).fetchone()
        if fila['estado'] != 'completada' or biblioteca.huella(fila['imagen']) != fila['sha']:
            raise RuntimeError("Resultado de imagen inválido")
        with db:
            db.execute("INSERT OR IGNORE INTO seleccionadas VALUES (?, ?)", (original, version))
        return fila['imagen'], "image/png"
