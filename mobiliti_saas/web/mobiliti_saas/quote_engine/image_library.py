"""Biblioteca persistente de imágenes y clientes OpenAI/SeedVR2 compartidos."""
from __future__ import annotations
import base64
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from PIL import Image
from .ai_image_provider import _multipart_body

# Instrucción y calidad elegidas en la comparación del 2026-10-13 (output/comparativa-ia-20261013):
# medium se ve como high por un cuarto del costo y en unos 20 s en lugar de 30.
PROMPT = (
    "Recreate this low-resolution furniture product photo as a clean, sharp studio catalog image of the "
    "SAME product for a sales quotation. "
    "REBUILD SURFACES: the source is heavily pixelated and its flat surfaces (tabletops, desktops, panels, "
    "seats) show capture defects: blur, pixel blocks, banding and washed-out white glare or reflection "
    "patches. Do not preserve those defects. Re-render every surface as continuous, evenly lit material with "
    "crisp realistic detail; for wood or laminate show clear natural grain running in the same direction, the "
    "same color tone across the whole surface and a clean, defined edge thickness. Remove white glare, hot "
    "spots and reflections completely. "
    "KEEP THE PRODUCT IDENTICAL: same design, silhouette, proportions, camera angle, colors and materials; "
    "same number, position and thickness of legs, wires, casters, armrests, panels, drawers and cable ports. "
    "Do not redesign, add or remove parts, or add text, logos or props. If a structural detail is ambiguous, "
    "keep its original shape instead of inventing a new one. "
    "BACKGROUND: seamless pure white (#FFFFFF). If the source has a gray, tinted or gradient background, a "
    "floor plane, a backdrop or any cast shadow, remove it entirely. No floor, no shadow, no border. "
    "FRAMING: tight product-only crop. Center the product and scale it uniformly so it fills about 92% of the "
    "frame along its longest dimension, leaving only a thin even white margin. Never cut off any part of the "
    "product and never stretch it."
)
CONFIGURACIONES = {
    "openai": {
        "model": "gpt-image-2.5-sunburst-2026-09-08",
        "prompt": PROMPT, "size": "1024x1024", "quality": "medium", "background": "opaque",
        "output_format": "png", "n": 1,
    },
    "seedvr2": {
        "model": "fal-ai/seedvr/upscale/image", "upscale_mode": "factor",
        "upscale_factor": 1024 / 150, "noise_scale": 0.1,
        "output_format": "png", "seed": 20260924,
    },
}
MAX_BYTES = 32 * 1024 * 1024
# Un 429 con estos códigos es saldo agotado; cualquier otro 429 es límite de frecuencia.
CODIGOS_SALDO = frozenset({"credit_balance_exhausted", "billing_hard_limit_reached", "insufficient_quota"})


def huella(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def dimensiones(datos: bytes, *, original: bool = False) -> tuple[int, int]:
    if not datos or len(datos) > MAX_BYTES:
        raise ValueError("Imagen vacía o demasiado grande")
    with Image.open(BytesIO(datos)) as imagen:
        if imagen.format not in ({"PNG", "JPEG"} if original else {"PNG"}) or imagen.width * imagen.height > 25_000_000:
            raise ValueError("Se requiere PNG de hasta 25 megapíxeles")
        imagen.verify()
        return imagen.size


def abrir_biblioteca(ruta: Path) -> sqlite3.Connection:
    # ponytail: SQLite local por cuenta; almacenamiento compartido al desplegar varios workers.
    ruta.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ruta, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS originales (
            sha TEXT PRIMARY KEY, imagen BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS versiones (
            id TEXT PRIMARY KEY, original TEXT NOT NULL, proveedor TEXT NOT NULL,
            configuracion TEXT NOT NULL, estado TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}', imagen BLOB, sha TEXT,
            creado TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS aprobadas (
            original TEXT PRIMARY KEY, version TEXT NOT NULL);
    """)
    return db


def registrar_original(db: sqlite3.Connection, datos: bytes) -> str:
    dimensiones(datos, original=True)
    sha = huella(datos)
    with db:
        db.execute("INSERT OR IGNORE INTO originales VALUES (?, ?)", (sha, datos))
    return sha


def clave_version(original: str, proveedor: str, configuracion: dict) -> str:
    return huella(json.dumps([original, proveedor, configuracion], sort_keys=True).encode())


def reservar(db: sqlite3.Connection, original: str, proveedor: str, configuracion: dict) -> tuple[dict, bool]:
    identificador = clave_version(original, proveedor, configuracion)
    with db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO versiones (id, original, proveedor, configuracion, estado) "
            "VALUES (?, ?, ?, ?, 'enviando')",
            (identificador, original, proveedor, json.dumps(configuracion, sort_keys=True)),
        )
    return dict(db.execute("SELECT * FROM versiones WHERE id=?", (identificador,)).fetchone()), cursor.rowcount == 1


def completar(db: sqlite3.Connection, identificador: str, datos: bytes, metadata: dict) -> None:
    ancho, alto = dimensiones(datos)
    anterior = db.execute("SELECT metadata FROM versiones WHERE id=?", (identificador,)).fetchone()
    historial = json.loads(anterior[0]).get("historial", []) if anterior else []
    if historial:
        metadata = {**metadata, "historial": historial}
    metadata = {**metadata, "dimensiones": [ancho, alto]}
    with db:
        db.execute(
            "UPDATE versiones SET estado='completada', imagen=?, sha=?, metadata=? WHERE id=?",
            (datos, huella(datos), json.dumps(metadata), identificador),
        )


def aprobar(db: sqlite3.Connection, identificador: str) -> None:
    version = db.execute("SELECT * FROM versiones WHERE id=?", (identificador,)).fetchone()
    if not version or version["estado"] != "completada" or huella(version["imagen"]) != version["sha"]:
        raise ValueError("Solo se puede aprobar una imagen completa e íntegra")
    with db:
        db.execute("INSERT INTO aprobadas VALUES (?, ?) ON CONFLICT(original) DO UPDATE SET version=excluded.version",
                   (version["original"], identificador))


def obtener_aprobada(db: sqlite3.Connection, original: bytes) -> bytes | None:
    fila = db.execute(
        "SELECT v.imagen, v.sha FROM aprobadas a JOIN versiones v ON a.version=v.id "
        "WHERE a.original=? AND v.estado='completada'", (huella(original),),
    ).fetchone()
    if fila is None:
        return None
    if huella(fila["imagen"]) != fila["sha"]:
        raise ValueError("La versión aprobada no conserva su integridad")
    return fila["imagen"]


class SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # No reenviar las credenciales a otro destino.


class ErrorHTTPProveedor(RuntimeError):
    def __init__(self, estado: int, codigo: str):
        self.estado = estado
        self.codigo = codigo
        super().__init__(f"Proveedor respondió HTTP {estado} ({codigo})")


def solicitar(url: str, clave: str = "", datos: bytes | None = None, tipo: str = "application/json") -> tuple[bytes, dict]:
    encabezados = {"Content-Type": tipo}
    if clave:
        destino = urllib.parse.urlsplit(url)
        if destino.scheme != "https":
            raise ValueError("La autenticación requiere HTTPS")
        dominio = destino.netloc
        if dominio == "api.openai.com":
            encabezados["Authorization"] = "Bearer " + clave
        elif dominio == "queue.fal.run":
            encabezados["Authorization"] = "Key " + clave
        else:
            raise ValueError("Destino de autenticación no permitido")
    peticion = urllib.request.Request(url, data=datos, headers=encabezados)
    try:
        with urllib.request.build_opener(SinRedireccion).open(peticion, timeout=240) as respuesta:
            contenido = respuesta.read(MAX_BYTES + 1)
            if len(contenido) > MAX_BYTES:
                raise ValueError("Respuesta demasiado grande")
            return contenido, dict(respuesta.headers)
    except urllib.error.HTTPError as error:
        codigo = "sin_codigo"
        try:
            detalle = json.loads(error.read(65536)).get("error", {})
            candidato = detalle.get("code") if isinstance(detalle, dict) else None
            if isinstance(candidato, str) and re.fullmatch(r"[a-z_]{1,80}", candidato):
                codigo = candidato
        except (ValueError, AttributeError):
            pass
        raise ErrorHTTPProveedor(error.code, codigo) from None


def consultar_json(url: str, clave: str, datos: dict | None = None) -> dict:
    respuesta, _ = solicitar(url, clave, json.dumps(datos).encode() if datos is not None else None)
    return json.loads(respuesta)


def ejecutar_version(db: sqlite3.Connection, original: str, proveedor: str, configuracion: dict, clave: str,
                     *, hasta: float | None = None) -> str:
    """``hasta`` (instante de ``time.monotonic()``) corta el sondeo de la cola; la solicitud queda reanudable."""
    try:
        return _ejecutar_version(db, original, proveedor, configuracion, clave, hasta)
    except ErrorHTTPProveedor as error:
        # Solo registrar rechazo inequívoco; un fallo 5xx puede ocurrir tras generar.
        if error.estado in {400, 401, 403, 404, 422, 429}:
            identificador = clave_version(original, proveedor, configuracion)
            # Un 429 por frecuencia no generó nada: se puede reenviar, y lo ya encolado sigue vivo.
            por_frecuencia = error.estado == 429 and error.codigo not in CODIGOS_SALDO
            with db:
                fila = db.execute("SELECT metadata FROM versiones WHERE id=?", (identificador,)).fetchone()
                meta = json.loads(fila[0]) if fila else {}
                meta.update(http=error.estado, code=error.codigo)
                if por_frecuencia:
                    db.execute("UPDATE versiones SET estado='reintento_autorizado', metadata=? WHERE id=? AND estado='enviando'",
                               (json.dumps(meta), identificador))
                else:
                    db.execute("UPDATE versiones SET estado='rechazada', metadata=? WHERE id=? AND estado IN ('enviando', 'en_cola')",
                               (json.dumps(meta), identificador))
        raise


def _ejecutar_version(db: sqlite3.Connection, original: str, proveedor: str, configuracion: dict, clave: str,
                      hasta: float | None = None) -> str:
    if not clave:
        raise ValueError("Falta la clave del proveedor")
    version, nueva = reservar(db, original, proveedor, configuracion)
    identificador = version["id"]
    if version["estado"] == "completada":
        if huella(version["imagen"]) != version["sha"]:
            raise ValueError("Resultado guardado corrupto; no se volverá a cobrar automáticamente")
        return identificador
    if version["estado"] == "reintento_autorizado":
        with db:
            nueva = db.execute("UPDATE versiones SET estado='enviando' WHERE id=? AND estado='reintento_autorizado'",
                               (identificador,)).rowcount == 1
    if not nueva and version["estado"] != "en_cola":
        raise RuntimeError("Solicitud anterior sin resultado confirmado. Revisar al proveedor antes de repetir el cobro")
    metadata = json.loads(version["metadata"])
    inicio = time.monotonic()
    if proveedor == "openai":
        original_bytes = db.execute("SELECT imagen FROM originales WHERE sha=?", (original,)).fetchone()[0]
        mime = "image/jpeg" if original_bytes.startswith(b"\xff\xd8") else "image/png"
        cuerpo, tipo = _multipart_body(configuracion, "image", "original.jpg" if mime == "image/jpeg" else "original.png", original_bytes, mime)
        respuesta, encabezados = solicitar("https://api.openai.com/v1/images/edits", clave, cuerpo, tipo)
        contenido = json.loads(respuesta)
        datos = base64.b64decode(contenido["data"][0]["b64_json"], validate=True)
        metadata = {"request_id": next((v for k, v in encabezados.items() if k.lower() == "x-request-id"), None),
                    "usage": contenido.get("usage"), "revised_prompt": contenido["data"][0].get("revised_prompt")}
    else:
        if nueva:
            original_bytes = db.execute("SELECT imagen FROM originales WHERE sha=?", (original,)).fetchone()[0]
            payload = {k: v for k, v in configuracion.items() if k != "model"}
            mime = "image/jpeg" if original_bytes.startswith(b"\xff\xd8") else "image/png"
            payload["image_url"] = "data:" + mime + ";base64," + base64.b64encode(original_bytes).decode()
            respuesta = consultar_json("https://queue.fal.run/" + configuracion["model"], clave, payload)
            metadata = {k: respuesta[k] for k in ("request_id", "status_url", "response_url")}
            with db:
                db.execute("UPDATE versiones SET estado='en_cola', metadata=? WHERE id=?", (json.dumps(metadata), identificador))
        while time.monotonic() - inicio < 600 and (hasta is None or time.monotonic() < hasta):
            estado = consultar_json(metadata["status_url"], clave)
            if estado["status"] == "COMPLETED":
                if estado.get("error"):
                    raise RuntimeError("SeedVR2 terminó con error; revisar su consola")
                metadata["metrics"] = estado.get("metrics")
                break
            time.sleep(5)
        else:
            raise RuntimeError("SeedVR2 sigue pendiente; reanudar recuperará la misma solicitud")
        resultado = consultar_json(metadata["response_url"], clave)
        url = resultado["image"]["url"]
        if url.startswith("data:image/png;base64,"):
            datos = base64.b64decode(url.split(",", 1)[1], validate=True)
        else:
            destino = urllib.parse.urlsplit(url)
            if destino.scheme != "https" or not (destino.hostname or "").endswith(".fal.media"):
                raise ValueError("SeedVR2 devolvió un destino de imagen inesperado")
            datos, _ = solicitar(url)
        metadata["seed"] = resultado.get("seed")
    metadata["segundos_esta_ejecucion"] = round(time.monotonic() - inicio, 2)
    completar(db, identificador, datos, metadata)
    return identificador


def exportar_archivo(ruta: Path, datos: bytes) -> None:
    if ruta.exists():
        if ruta.read_bytes() != datos:
            raise ValueError(f"Se conserva el archivo previo diferente: {ruta}")
    else:
        with ruta.open("xb") as archivo:
            archivo.write(datos)


