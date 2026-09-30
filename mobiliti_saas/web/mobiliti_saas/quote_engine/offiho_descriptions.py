"""Redacción de presentación Offiho sin alterar las fichas fuente ni sus precios.

Los recortes del PDF mezclan cotas, precios y fichas vecinas. Sólo las cláusulas
técnicas identificables se convierten en texto para el cliente. Una identidad
breve es preferible a presentar especificaciones que el recorte no respalda.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_ETIQUETAS = (
    "asiento, respaldo y patas", "respaldo, asiento y patas", "patas, asiento y respaldo",
    "respaldo, brazos, asiento y patas", "asiento, respaldo y brazos",
    "respaldo, asiento y brazos", "asiento y respaldo", "respaldo y asiento",
    "sistema de elevación", "estructura color", "color respaldo",
    "características", "caracteristicas", "elevación", "elevacion", "resistencia",
    "estructura", "mecanismo", "cabecera", "respaldo", "asiento", "cubierta",
    "regatones", "rodajas", "parrilla", "brazos", "paleta", "patas", "tapiz",
    "base", "color", "medidas", "nota", "mesa",
)
_PATRON_ETIQUETAS = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(v) for v in _ETIQUETAS) + r")\s*:", re.I
)
_RUIDO_PDF = re.compile(
    r"ºº|\b(?:ODLAPSER|OTNEISA|SOZARB|SELBATSUJA|ARUTLA|YLATI|EDAM)\b"
    r"|\bmedidas\s*:\s*dimensiones\s+en\s+mm"
    r"|\b\d{3,4}\s+\d{3,4}\s+\d{2,4}\b",
    re.I,
)
_PATRON_CODIGO = re.compile(r"\bOH[A-Z]-\d+[A-Z0-9/-]*", re.I)


def _texto(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sin_acentos(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value) if not unicodedata.combining(c)).casefold()


def _limpiar_nombre(value: Any) -> str:
    nombre = _texto(value).replace("*", "").strip()
    # Marcadores repetidos por la extracción, no números de modelo distintos.
    nombre = re.sub(r"\b([A-Za-zÁÉÍÓÚÑáéíóúñ]+)\s+\1\b", r"\1", nombre, flags=re.I)
    return _texto(nombre)


def nombre_producto_offiho(item: dict[str, Any]) -> str:
    """Conserva la familia cuando el inventario separa VESPER / 1 o BRAZOS / 86."""
    codigo = _limpiar_nombre(item.get("code"))
    nombre = _limpiar_nombre(item.get("name"))
    if nombre and _sin_acentos(nombre) == "cabecera":
        return f"Cabecera {codigo}".strip()
    if not nombre:
        return codigo or _limpiar_nombre(item.get("inventory_key"))
    if re.match(r"^OH[A-Z]-\d", codigo, re.I):
        return nombre
    if not codigo or nombre.casefold().startswith(codigo.casefold()):
        return nombre
    if "/" in codigo and nombre.casefold().startswith(codigo.split("/", 1)[0].casefold() + " "):
        return nombre
    if codigo.casefold() in nombre.casefold().split():
        return nombre
    return f"{codigo} {nombre}".strip()


def _es_accesorio(item: dict[str, Any]) -> bool:
    identidad = _sin_acentos(" ".join(_texto(item.get(k)) for k in ("code", "name", "inventory_key")))
    return bool(re.search(r"\b(?:cabecera|brazos?|piston(?:cajero)?|conector|regaton|rodaja|aro)\b", identidad)) and not bool(
        re.search(r"\b(?:con|sin) brazos\b", identidad)
    )


def _identidad_minima(item: dict[str, Any]) -> str:
    nombre = nombre_producto_offiho(item)
    return f"{nombre} de Offiho." if nombre else "Producto Offiho."


def _normalizar_frase(value: str, *, pdf: bool) -> str:
    texto = _texto(value).strip(" .;*º")
    if pdf:
        # Cotas y pesos de empaque que quedan después de una oración completa.
        texto = re.sub(r"[.]\s+(?:\d[\d.,\s]*(?:Pzs?|Kgs?)?\s*)+$", ".", texto, flags=re.I)
        texto = re.sub(r"[.]\s+[A-Za-z]{1,4}\s+\d[\d.,\s]*$", ".", texto)
        texto = re.sub(r"[.]\s+(?:[Øø]?\d[\d.,]*(?:\s*(?:mm|cm|kg|pzs?))?\s*)+$", ".", texto, flags=re.I)
    sustituciones = (
        (r"\bde el\b", "del"),
        (r"\bde medio de\b", "por medio de"),
        (r"\btermidado\b", "terminado"),
        (r"\ben base a esp[aá]rrago rascado\b", "con espárrago roscado"),
        (r"\bhule espuma laminad[oa] flexible\b", "espuma laminada flexible"),
        (r"\bhule espuma de poliuretano inyectad[oa]\b", "espuma de poliuretano inyectada"),
        (r"\balto hule espuma\b", "alto con espuma"),
        (r"\bsemi-r[ií]gido\b", "semirrígido"),
        (r"\bal color de su elecci[oó]n de (?:toda )?nuestra colecci[oó]n de tapices\b", "en los colores de la colección de tapices"),
        (r"\bal color de su elecci[oó]n\b", "en color a elegir"),
        (r"\bde toda nuestra colecci[oó]n de tapices\b", "de la colección de tapices"),
        (r"\bun s[oó]lo\b", "un solo"),
        (r"\bdrene de l[ií]quidos\b", "drenaje de líquidos"),
        (r"\bPOLIESTER\b", "poliéster"),
        (r"\bsynchro\b", "sincronizado"),
        (r"\banti-shock\b", "antirretorno"),
        (r"\bslider\b", "deslizamiento del asiento"),
        (r"\bpad de\b", "almohadilla de"),
    )
    for patron, reemplazo in sustituciones:
        texto = re.sub(patron, reemplazo, texto, flags=re.I)
    texto = re.sub(r"(?<=\d)\s*kgs?\s*/\s*m[³3]\b", " kg/m³", texto, flags=re.I)
    texto = re.sub(r"(?<=\d)\s*kgs?\b", " kg", texto, flags=re.I)
    texto = re.sub(r"(?<=\d)\s*mm\b", " mm", texto, flags=re.I)
    texto = re.sub(r"(?<=\d)\s*cm\b", " cm", texto, flags=re.I)
    # Sólo bajar mayúsculas de frases completas; nombres comerciales permanecen.
    texto = re.sub(r"\b(?:[A-ZÁÉÍÓÚÑ]+[ ,]+){3,}[A-ZÁÉÍÓÚÑ]+\b", lambda m: m.group().lower(), texto)
    return texto.strip(" .;*")


def _redactar_clausula(etiqueta: str, contenido: str, *, pdf: bool) -> str:
    contenido = _normalizar_frase(contenido, pdf=pdf)
    if not contenido or _RUIDO_PDF.search(contenido):
        return ""
    clave = _sin_acentos(etiqueta)
    if clave in {"medidas", "estructura color", "color", "color respaldo"} and pdf:
        return ""
    if clave == "medidas":
        return f"Medidas: {contenido}"
    if clave == "resistencia":
        peso = re.fullmatch(r"peso sugerido\s+(\d+(?:[.,]\d+)?)\s*kg", contenido, flags=re.I)
        return f"Peso sugerido de uso: {peso.group(1)} kg" if peso else f"Resistencia: {contenido}"
    if clave in {"caracteristicas", "nota"}:
        if contenido.lower().startswith("de una sola pieza"):
            contenido = "Diseño " + contenido
        return contenido[:1].upper() + contenido[1:]
    if clave in {"elevacion", "sistema de elevacion"}:
        contenido = re.sub(r"^(?:por medio de|de medio de)\s+", "mediante ", contenido, flags=re.I)
        if re.fullmatch(
            r"mediante pistón neumático que permite ajustar la altura del asiento",
            contenido, flags=re.I,
        ):
            return "Altura del asiento ajustable mediante pistón neumático"
        return f"Ajuste de altura {contenido}"
    if clave == "tapiz":
        contenido = re.sub(r"^en\s+", "", contenido, flags=re.I)
        return f"Tapizado: {contenido}"
    if clave in {"color", "color respaldo", "estructura color"}:
        return f"{etiqueta.capitalize()}: {contenido}"
    if clave == "respaldo" and contenido.lower().startswith("respaldo "):
        return contenido[:1].upper() + contenido[1:]
    if clave == "brazos":
        tapizado = re.fullmatch(
            r"de aluminio fundido y pulidos con terminado natural\.\s+"
            r"Acojinados y tapizados en (.+)",
            contenido, flags=re.I,
        )
        if tapizado:
            return (
                "Brazos de aluminio fundido con acabado natural pulido, "
                f"acojinados y tapizados en {tapizado.group(1)}"
            )
    if clave == "asiento y respaldo":
        cabecera = re.fullmatch(r"de respaldo alto con cabecera,\s*(.+)", contenido, flags=re.I)
        if cabecera:
            acojinamiento = re.sub(r"^con\s+", "", cabecera.group(1), flags=re.I)
            return f"Respaldo alto con cabecera. Asiento y respaldo con {acojinamiento}"
    if clave == "asiento y respaldo" and re.match(r"^de respaldo (alto|bajo)\b", contenido, re.I):
        contenido = re.sub(r"^de respaldo (alto|bajo)\s*,?\s*", r"Respaldo \1; asiento y respaldo con ", contenido, flags=re.I)
        return contenido.replace("con con ", "con ")
    # Cuando el original arranca con un sustantivo, dos puntos evitan construir
    # «Asiento hule espuma» o «Base estrella» como una oración incorrecta.
    union = " " if re.match(r"^(?:de|en|con|tipo|alto|bajo|medio|ajustables?|fijos?)\b", contenido, re.I) else ": "
    return etiqueta[:1].upper() + etiqueta[1:].lower() + union + contenido


def redactar_descripcion_offiho(item: dict[str, Any]) -> str:
    """Devuelve una ficha legible usando sólo los hechos del registro recibido."""
    original = _texto(item.get("description"))
    fuente = _texto(item.get("description_source"))
    if not original or fuente == "inventory_label" or original.startswith("Producto Offiho"):
        return _identidad_minima(item)
    pdf = fuente == "pdf_catalog" or bool(_RUIDO_PDF.search(original))
    etiquetas = list(_PATRON_ETIQUETAS.finditer(original))
    if pdf and not etiquetas:
        return _identidad_minima(item)
    # Los accesorios no heredan el peso, base o mecanismo de la silla vecina.
    if _es_accesorio(item) and any(_sin_acentos(m.group(1)) in {"base", "resistencia", "asiento", "mecanismo"} for m in etiquetas):
        return _identidad_minima(item)
    codigo = _texto(item.get("code")).upper()
    codigos = {m.group().upper() for m in _PATRON_CODIGO.finditer(original)}
    if pdf and any(not otro.startswith(codigo.replace(" ", "")) for otro in codigos):
        return _identidad_minima(item)
    if not etiquetas:
        frase = _normalizar_frase(original, pdf=False)
        return frase[:1].upper() + frase[1:] + ("" if frase.endswith((".", "!", "?")) else ".")
    frases = []
    etiquetas_anteriores: set[str] = set()
    tras_medidas = False
    for indice, etiqueta in enumerate(etiquetas):
        clave_etiqueta = _sin_acentos(etiqueta.group(1))
        if clave_etiqueta == "medidas":
            tras_medidas = True
        # Algunos recortes empiezan la ficha vecina después de las cotas. Una
        # base alternativa no puede añadirse a una estructura ya descrita, ni
        # patas metálicas a un conjunto de asiento/respaldo/patas de una pieza.
        if pdf and tras_medidas and (
            (clave_etiqueta == "base" and "estructura" in etiquetas_anteriores)
            or (clave_etiqueta == "estructura" and any("patas" in v for v in etiquetas_anteriores))
        ):
            continue
        fin = etiquetas[indice + 1].start() if indice + 1 < len(etiquetas) else len(original)
        contenido = original[etiqueta.end():fin].strip()
        frase = _redactar_clausula(etiqueta.group(1), contenido, pdf=pdf)
        if frase and frase.casefold() not in {v.casefold() for v in frases}:
            frases.append(frase)
            etiquetas_anteriores.add(clave_etiqueta)
    return ". ".join(frases) + "." if frases else _identidad_minima(item)
