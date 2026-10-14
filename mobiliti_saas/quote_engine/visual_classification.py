"""Clasifica con Claude, viendo la foto, los productos cuyo nombre no dice qué son.

Los nombres de una Quotation suelen ser apodos del proveedor ("lido privados ch")
o claves sin palabras ("DU82L.160155"). El diccionario no los reconoce y adivinar
por la descripción confunde un escritorio con pasacables con un multicontacto.
"""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
import time
from typing import Any, Mapping, Sequence

from PIL import Image


MODELOS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
LOTE = 40  # productos por solicitud; la API admite hasta 100 imágenes
LADO_MAXIMO = 512  # basta para reconocer el mueble y cuesta pocos tokens
PLAZO_SEGUNDOS = 60  # tiempo total de la consulta; pasado el plazo la cotización sigue con el diccionario
CATEGORIAS = {
    "Silla": "sillas operativas, ejecutivas, de visita, de capacitación o apilables",
    "Sillones": "sofás, sillones lounge, salas de espera, asientos modulares, puffs y otomanes tapizados",
    "Bancos": "bancas y bancos para sentarse sin respaldo, taburetes altos o de bar",
    "Escritorios-WorkStation": (
        "escritorios individuales, ejecutivos, en L o privados; estaciones de trabajo y bench "
        "para varias personas; recepciones y mostradores"
    ),
    "Mesas de Juntas": "mesas de reunión, juntas o conferencia, incluidas las mesas altas de reunión",
    "Mesas de Apoyo": "mesas de centro, laterales, auxiliares, ocasionales, personales o altas de café",
    "Librero - Locker - Gabinete": (
        "libreros, lockers, gabinetes, credenzas, armarios y muebles de guardado con puertas o entrepaños"
    ),
    "Archiveros Moviles y Fijos": (
        "archiveros y cajoneras (pedestales), móviles o fijos, de los que van junto o bajo el escritorio"
    ),
    "Phonebooths": "cabinas acústicas o phone booths",
    "Multicontactos": "multicontactos, módulos de energía, cables y accesorios eléctricos vendidos como producto aparte",
    "Cocineta": "muebles de cocineta o estación de café",
    "Pizarrones": "pizarrones y pintarrones",
    "Terminados": "pisos, alfombras, accesorios y cualquier cosa que no sea ninguna de las anteriores",
}
SISTEMA = (
    "Clasificas mobiliario de oficina para una cotización. De cada producto recibes el nombre que le puso el "
    "proveedor, su descripción, sus medidas y casi siempre su foto. Elige exactamente una categoría de la lista "
    "para cada uno.\n\n"
    "La foto y la descripción mandan sobre el nombre: muchos nombres son apodos internos (por ejemplo, 'lido' es "
    "una línea de escritorios, y 'bench para 4PX' es una estación de trabajo para cuatro personas, no un banco). "
    "Una descripción de escritorio que menciona pasacables, gabinete lateral o módulo eléctrico sigue siendo un "
    "escritorio: clasifica el mueble completo, no sus accesorios.\n\nCategorías:\n"
    + "\n".join(f"- {nombre}: {detalle}." for nombre, detalle in CATEGORIAS.items())
)


def _miniatura(datos: bytes) -> str | None:
    try:
        with Image.open(BytesIO(datos)) as original:
            imagen = original.convert("RGBA")
    except Exception:  # incluye DecompressionBombError, que no es OSError
        return None  # sin foto legible se clasifica solo con el texto
    fondo = Image.new("RGBA", imagen.size, "white")
    fondo.alpha_composite(imagen)
    fondo = fondo.convert("RGB")
    fondo.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    salida = BytesIO()
    fondo.save(salida, "JPEG", quality=80)
    return base64.standard_b64encode(salida.getvalue()).decode()


def _evento(**datos: Any) -> None:
    print(json.dumps({"event": "visual_classification", **datos}, ensure_ascii=True, separators=(",", ":")))


def clasificar_productos(
    productos: Sequence[Any],
    imagenes: Mapping[int, tuple[bytes, str]],
    categorias: Sequence[str],
    *,
    cliente: Any = None,
) -> dict[int, str]:
    """Devuelve ``{fila: categoría}`` de lo que Claude clasificó; vacío si está apagado o falla.

    ``productos`` tienen ``row``, ``nombre``, ``descripcion`` y ``dimension``. Quien llama
    conserva su propia clasificación para las filas que no vengan en el resultado.
    """

    modelo = MODELOS.get(os.environ.get("QUOTE_LLM_FALLBACK_MODEL", "").strip().lower())
    if not modelo or not productos:
        return {}

    # Los productos repetidos (mismo texto y misma foto) se preguntan una sola vez.
    filas_por_producto: dict[tuple[str, str, str, bytes | None], list[int]] = {}
    for producto in productos:
        foto = imagenes.get(producto.row)
        clave = (
            str(producto.nombre or "").strip(),
            str(producto.descripcion or "").strip()[:400],
            str(producto.dimension or "").strip(),
            foto[0] if foto else None,
        )
        filas_por_producto.setdefault(clave, []).append(producto.row)
    unicos = list(filas_por_producto)
    permitidas = set(categorias) & set(CATEGORIAS)
    resultado: dict[int, str] = {}
    entrada = salida = 0
    inicio = time.monotonic()
    try:
        from pydantic import create_model
        from typing import Literal

        fila_respuesta = create_model("Producto", indice=(int, ...), categoria=(Literal[tuple(CATEGORIAS)], ...))
        formato = create_model("Clasificacion", productos=(list[fila_respuesta], ...))
        if cliente is None:
            import anthropic

            cliente = anthropic.Anthropic(max_retries=0)
        for desde in range(0, len(unicos), LOTE):
            lote = unicos[desde:desde + LOTE]
            contenido: list[dict[str, Any]] = []
            for indice, (nombre, descripcion, dimension, foto) in enumerate(lote, start=1):
                contenido.append({
                    "type": "text",
                    "text": (
                        f"Producto {indice}\nNombre: {nombre}\nMedidas: {dimension or 'sin dato'}\n"
                        f"Descripción: {descripcion or 'sin dato'}"
                    ),
                })
                miniatura = _miniatura(foto) if foto else None
                if miniatura:
                    contenido.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": miniatura},
                    })
            for intento in range(2):  # un reintento por si el fallo fue pasajero
                restante = PLAZO_SEGUNDOS - (time.monotonic() - inicio)
                if restante <= 0:
                    raise TimeoutError("sin tiempo para consultar a Claude")
                try:
                    respuesta = cliente.messages.parse(
                        model=modelo,
                        max_tokens=16000,
                        system=SISTEMA,
                        messages=[{"role": "user", "content": contenido}],
                        output_format=formato,
                        output_config={"effort": "high"},
                        timeout=restante,
                    )
                    break
                except Exception:
                    if intento:
                        raise
            entrada += respuesta.usage.input_tokens
            salida += respuesta.usage.output_tokens
            if respuesta.parsed_output is None:
                raise ValueError("respuesta sin clasificación")
            for fila in respuesta.parsed_output.productos:
                if 1 <= fila.indice <= len(lote) and fila.categoria in permitidas:
                    for numero in filas_por_producto[lote[fila.indice - 1]]:
                        resultado[numero] = fila.categoria
    except Exception as error:  # la cotización sigue con el diccionario
        _evento(status="failed", error=type(error).__name__, products=len(unicos), classified=len(resultado))
        return resultado
    _evento(
        status="ok", model=modelo, products=len(unicos), classified=len(resultado),
        input_tokens=entrada, output_tokens=salida, seconds=round(time.monotonic() - inicio, 1),
    )
    return resultado
