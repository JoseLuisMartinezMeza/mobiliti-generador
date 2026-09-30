"""Redacción comercial a partir de hechos publicados por cada proveedor.

La fuente, los precios y las advertencias permanecen intactos. Las correcciones
editoriales exactas se aplican únicamente al texto que fue revisado; una nueva
descripción del proveedor no hereda correcciones por parecido de nombre o SKU.
Sunon y ALMA conservan el idioma de origen: la presentación elige la traducción.
"""
from __future__ import annotations

from functools import lru_cache
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from .sunon_descriptions import reconciliar_ficha_sunon


_MARCAS = frozenset({
    "tarkett", "cr-global", "sonara", "sunon", "alma", "lumbro", "jome",
    "lauco", "idelika", "conceptos", "labenze", "requiez",
})
_DATOS = Path(__file__).with_name("data") / "supplier_description_corrections.json"
_PALABRAS_CORREGIDAS = {
    "puertoo": "puerto", "ajustableen": "ajustable en", "ejectutivo": "ejecutivo",
    "electroestática": "electrostática", "electroestatica": "electrostática",
    "cudrada": "cuadrada", "recubirmiento": "recubrimiento", "cromodo": "cromado",
    "alumino": "aluminio", "descansapies": "descansapiés", "descansa pies": "descansapiés",
    "descansa brazos": "descansabrazos", "scocca": "carcasa",
    "inalambrica": "inalámbrica", "inalambrico": "inalámbrico",
    "vertébra": "vértebra", "vertebra": "vértebra", "mensula": "ménsula",
    "sofa": "sofá", "liston": "listón", "impresion": "impresión",
    "diametro": "diámetro", "dimension": "dimensión", "boton": "botón",
    "teka": "teca", "polyester": "poliéster", "whicker": "wicker",
}


def _texto(valor: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(valor or ""))).strip()


def _marca(item: dict) -> str:
    return _texto(item.get("catalog") or item.get("supplier")).casefold()


@lru_cache(maxsize=1)
def _correcciones() -> dict[tuple[str, str], dict]:
    datos = json.loads(_DATOS.read_text(encoding="utf-8"))
    return {(fila["supplier"], fila["original"]): fila for fila in datos["corrections"]}


def _ortografia(texto: str) -> str:
    for original, correcto in _PALABRAS_CORREGIDAS.items():
        def reemplazar(match: re.Match, destino: str = correcto) -> str:
            return destino[:1].upper() + destino[1:] if match.group()[:1].isupper() else destino
        texto = re.sub(rf"\b{re.escape(original)}\b", reemplazar, texto, flags=re.I)
    return texto


def _puntuacion(texto: str) -> str:
    texto = texto.replace("•", ". ")
    texto = re.sub(r"\s+([,.;:])", r"\1", texto)
    texto = re.sub(r"[,;:]\s*\.", ".", texto)
    texto = re.sub(r"\.\s*\.", ".", texto)
    texto = re.sub(r"(?<=[a-záéíóúñ])\.(?=[A-ZÁÉÍÓÚÑ])", ". ", texto)
    texto = re.sub(r"(?<=\d)\s*(?:mts|cms)\b\.?", lambda m: " m" if "mts" in m.group() else " cm", texto, flags=re.I)
    texto = re.sub(r"(?<=\d)(mm|cm|kg|m)(?=\b)", r" \1", texto, flags=re.I)
    texto = re.sub(r"(?<=\d)\s*[xX*]\s*(?=[.\d])", " × ", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" ,;.")
    if texto:
        texto = texto[0].upper() + texto[1:]
        if texto[-1] not in ".!?":
            texto += "."
    return texto


def _lumbro(texto: str) -> str:
    # «Sistema» es un encabezado de agrupación capturado al final de la celda.
    texto = re.sub(r"\s+Sistema:\s*.*$", "", texto, flags=re.I)
    texto = re.sub(r"Multicontacto,\s*Modelo\s+", "Multicontacto modelo ", texto, flags=re.I)
    texto = re.sub(r"Pasacables,\s*Modelo\s+", "Pasacables modelo ", texto, flags=re.I)
    texto = re.sub(r"\b127\s*v\b", "127 V", texto, flags=re.I)
    texto = re.sub(r"\b1 puertos\b", "1 puerto", texto, flags=re.I)
    texto = re.sub(r"\b([2-9]) puerto\b", r"\1 puertos", texto, flags=re.I)
    texto = re.sub(r"\b1 cable de metro con clavija\b", "1 cable con clavija", texto, flags=re.I)
    texto = re.sub(r"\b1 \+ puerto\b", "1 puerto", texto, flags=re.I)
    texto = re.sub(r"tipo A\s+(?=1 puerto)", "tipo A, ", texto, flags=re.I)
    texto = re.sub(r"de carga\s+(?=1 pasacable)", "de carga, ", texto, flags=re.I)
    texto = re.sub(r"\bUSB\s*\(Datos\)", "USB de datos", texto, flags=re.I)
    texto = re.sub(r"\bUSB\s*\(Carga\)", "USB de carga", texto, flags=re.I)
    texto = re.sub(r"\bUSB doble\s*\(Carga\)", "USB doble de carga", texto, flags=re.I)
    texto = re.sub(r"\bpara canaleta COLOR\b", "para canaleta en color", texto)
    texto = re.sub(r"\bNo Regulados\b", "no regulados", texto, flags=re.I)
    texto = re.sub(r"\bPUERTO USB CARGA DOBLE\b", "puerto USB de carga doble", texto, flags=re.I)
    texto = re.sub(r"\bINTERCONECTAR\b", "interconectar", texto)
    return texto


def _jome(item: dict, texto: str) -> str:
    atributos = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    codigo = _texto(item.get("sku") or atributos.get("source_code"))
    if texto.casefold() == codigo.casefold() and codigo:
        bloque = _texto(atributos.get("block") or item.get("collection"))
        if bloque and bloque.casefold() not in {"sin-bloque", "accesorios", "jome"}:
            texto = _nombre_bloque_jome(bloque)
            medidas = _texto(atributos.get("dimensions"))
            if medidas:
                texto += f". Medidas: {medidas}"
    texto = re.sub(r"\s+Peso soportado\s*\.?$", "", texto, flags=re.I)
    texto = re.sub(r"\bBrazos Porta Monitores Doble\b", "Brazo doble para monitores", texto, flags=re.I)
    texto = re.sub(r"\bBrazos Porta Monitores Sencillo\b", "Brazo sencillo para monitor", texto, flags=re.I)
    texto = re.sub(r"\bRango altura\b", "Rango de altura:", texto, flags=re.I)
    texto = re.sub(r"\bDimensi[oó]n pantalla\b", "Tamaño de pantalla:", texto, flags=re.I)
    return texto


def _nombre_bloque_jome(bloque: str) -> str:
    bloque = re.sub(r"^Bases?\s+centrales?\b", "Base central", bloque, flags=re.I)
    bloque = re.sub(r"^Bases?\s+laterales?\b", "Base lateral", bloque, flags=re.I)
    bloque = re.sub(r"^Cubiertas\b", "Cubierta", bloque, flags=re.I)
    bloque = re.sub(r"^Escritorio Rectos\b", "Escritorio recto", bloque, flags=re.I)
    bloque = re.sub(r"\binvidual\b", "individual", bloque, flags=re.I)
    return _ortografia(bloque)


def _labenze(texto: str) -> str:
    # Los encabezados de color sueltos no describen la variante seleccionada.
    texto = re.sub(
        r"^(?:(?:Cromo|Negro|Gris|Blanco|Plata|Mineral|Aluminio|Colores especiales|"
        r"Colores varios|Colores|especiales|Madera Haya|Madera Natural|Madera Wengué|"
        r"Madera Nogal|Acabado Natural|Acabado Nogal)\s+)+",
        "", texto, flags=re.I,
    )
    texto = re.sub(r"\s+y externo\b(?:\s+Negro)?", "", texto, flags=re.I)
    texto = re.sub(r"^TRINE0\s+", "", texto)
    texto = re.sub(r"\bSilla 4 patas\b", "Silla de 4 patas", texto, flags=re.I)
    texto = re.sub(r"\bApilable a\s*(\d+)\s*pzs\.?", r"Apilable hasta \1 piezas", texto, flags=re.I)
    texto = re.sub(r"\bAcabados Materiales\b", "Materiales:", texto)
    texto = texto.replace("power coating", "pintura en polvo")
    texto = re.sub(r"\bSofá de 2 plazas\. Resistente y estable elaborada", "Sofá de 2 plazas, resistente y estable, elaborado", texto)
    return texto


def _idelika(texto: str) -> str:
    texto = re.sub(r"\s*Precios disponibles en\s+www\.idelika\.com\s*", " ", texto, flags=re.I)
    texto = re.sub(r"\s*Tus alumnos con estos pupitres,.*$", "", texto, flags=re.I)
    texto = re.sub(r"\s*Las aguas del océano Índico.*$", "", texto, flags=re.I)
    texto = re.sub(r"\ben tuvo redondo\b", "en tubo redondo", texto, flags=re.I)
    texto = re.sub(r"\s+A B C D\s*$", "", texto)
    # No se combinan versiones interior/exterior ni se adivinan unidades.
    return texto


def _base_seleccionada(item: dict, correccion: dict) -> str:
    bases = correccion.get("base_options", {})
    nombre = _texto(item.get("name"))
    # Solo se acepta el código de base en el segmento explícito de variante.
    variante = re.search(r"[—·]\s*(GL|[A-Z]\d+)\b(.*)$", nombre)
    if not variante or variante.group(1) not in bases:
        return ""
    base = bases[variante.group(1)]
    acabado = variante.group(2).strip().casefold()
    if acabado == "cromo":
        base = base.replace("cromado o pintado", "cromado")
    elif acabado in {"negro", "negra", "blanca", "blanco", "bca/esp"}:
        base = base.replace("cromado o pintado", "pintado")
    if acabado == "alum":
        base = base.replace("nylon o aluminio", "aluminio")
    # No trasladar el negro de una tabla de familia a una variante blanca.
    if acabado in {"blanca", "blanco", "bca/esp"}:
        base = base.replace("aluminio negro", "aluminio")
    return base


def redactar_descripcion_proveedor(item: dict) -> str:
    """Devuelve el texto comercial, sin modificar el registro ni sus atributos."""
    texto = _texto(item.get("description"))
    marca = _marca(item)
    if marca not in _MARCAS:
        return texto
    correccion = _correcciones().get((marca, texto))
    if correccion is not None:
        texto = correccion["revised"]
        variante = _texto(item.get("name")).split(" — ")[-1]
        texto = correccion.get("variant_descriptions", {}).get(variante, texto)
        base = _base_seleccionada(item, correccion)
        if base:
            texto += " " + base
    if marca == "idelika" and "Pedido mínimo y disponibilidad por confirmar." in texto:
        atributos = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        try:
            minimo = Decimal(str(atributos.get("minimum_order") or ""))
        except InvalidOperation:
            minimo = Decimal("0")
        if minimo.is_finite() and minimo > 0:
            cantidad = format(minimo.normalize(), "f")
            texto = texto.replace("Pedido mínimo y disponibilidad por confirmar.", f"Pedido mínimo: {cantidad} piezas. Disponibilidad por confirmar.")
    if marca in {"sunon", "alma"}:
        return reconciliar_ficha_sunon(item)[1] if marca == "sunon" else texto
    if marca == "tarkett" and not texto:
        # La lista local contiene identidad, espesor y unidad, sin ficha técnica.
        texto = _texto(item.get("name"))
    texto = _ortografia(texto)
    if marca == "lumbro":
        texto = _lumbro(texto)
    elif marca == "jome":
        texto = _jome(item, texto)
    elif marca == "labenze":
        texto = _labenze(texto)
    elif marca == "idelika":
        texto = _idelika(texto)
    elif marca == "requiez":
        texto = re.sub(r"\bmesh\b", "malla", texto, flags=re.I)
        texto = re.sub(r"\bModelos compatibles\s+(?!:)", "Modelos compatibles: ", texto, flags=re.I)
        texto = re.sub(r"\bSillón ejecutivo, respaldo alto tapizado en tela estructura", "Sillón ejecutivo con respaldo alto tapizado en tela y estructura", texto, flags=re.I)
    elif marca == "cr-global":
        texto = re.sub(r"^Vértebra pasacables Fabricada", "Vértebra pasacables fabricada", texto, flags=re.I)
        texto = re.sub(r"\banti[- ]colisi[oó]n\b", "anticolisión", texto, flags=re.I)
        texto = re.sub(r"\bMEDIDA SUGERIDA DE CUBIERTA\s*:?\s*", "Medida sugerida de cubierta: ", texto)
    elif marca == "lauco":
        texto = re.sub(r"\b(sofá|sillón)\s+(?=\d)", r"\1 de ", texto, flags=re.I)
        texto = re.sub(r"\bc/mesa\b", "con mesa", texto, flags=re.I)
        texto = re.sub(r"\bEstructura interna madera\b", "Estructura interna de madera", texto, flags=re.I)
        texto = re.sub(r"\bSoporte asiento\b", "Soporte de asiento", texto, flags=re.I)
        texto = re.sub(r"\bPatas acero\b", "Patas de acero", texto, flags=re.I)
        texto = re.sub(r"\bademás delcrón\b", "además de delcrón", texto, flags=re.I)
        texto = re.sub(r"\b(plazas?) brazos y respaldo (bajo|alto) base\b", r"\1 con brazos y respaldo \2; base", texto, flags=re.I)
        texto = re.sub(r"\bpino de primera (?=\d)", "pino de primera de ", texto, flags=re.I)
        texto = re.sub(r"\bpuntos críticos,? paneles\b", "puntos críticos. Paneles", texto, flags=re.I)
        texto = re.sub(r"\b(Paneles sólidos en listón de \d+\s*mm), armada con\b", r"\1. Estructura armada con", texto, flags=re.I)
        texto = re.sub(r"\bAcojinamiento en asiento, poliuretano flexible densidad\b", "Acojinamiento del asiento en poliuretano flexible de densidad", texto, flags=re.I)
        texto = re.sub(r", respaldo densidad\b", "; respaldo de densidad", texto, flags=re.I)
        texto = re.sub(r", brazos y costados densidad\b", "; brazos y costados de densidad", texto, flags=re.I)
        texto = re.sub(r"\bSoporte de asiento triplay\b", "Soporte del asiento en triplay", texto, flags=re.I)
        texto = re.sub(r"\btriplay de pino (?=\d)", "triplay de pino de ", texto, flags=re.I)
        texto = re.sub(r"\bo MDF (?=\d)", "o MDF de ", texto)
        texto = re.sub(r"\bgrapa de acero (?=\d)", "grapa de acero de ", texto, flags=re.I)
        texto = re.sub(r"\bcal\. (\d+) acabado pintura\b", r"cal. \1, con acabado de pintura", texto, flags=re.I)
    return _puntuacion(texto)


def detectar_pendientes_descripcion(item: dict) -> list[str]:
    """Señala carencias de evidencia para la auditoría, nunca para el cliente."""
    texto = _texto(item.get("description"))
    marca = _marca(item)
    pendientes: list[str] = []
    if marca == "sunon":
        pendientes.extend(reconciliar_ficha_sunon(item)[2])
    correccion = _correcciones().get((marca, texto))
    if correccion:
        pendientes.extend(correccion.get("pending", []))
    codigo = _texto(item.get("sku") or item.get("code"))
    if not texto:
        pendientes.append("fuente_sin_descripcion_tecnica")
    if codigo and texto.casefold() == codigo.casefold():
        atributos = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        if marca != "jome" or not _texto(atributos.get("block") or item.get("collection")):
            pendientes.append("fuente_solo_codigo")
        else:
            pendientes.append("ficha_tecnica_incompleta_identidad_recuperada_del_bloque")
    if marca == "lumbro" and "cable de metro" in texto.casefold():
        pendientes.append("longitud_de_cable_no_especificada")
    if marca == "jome" and re.search(r"Peso soportado\s*\.?$", texto, flags=re.I):
        pendientes.append("capacidad_de_carga_ausente")
    if marca == "requiez":
        nombre = _texto(item.get("name")).replace(" · ", " ")
        if texto == nombre or texto in {"Estructura Respaldo", "Reclinamiento sincrónico"}:
            pendientes.append("fuente_sin_ficha_completa")
        if "3 posiciones de bloqueo" in texto and "4 posiciones de bloqueo" in texto:
            pendientes.append("especificaciones_mezcladas_entre_productos")
    if marca == "idelika" and not correccion:
        if "Acero galvanizado" in texto and "Acero con pintura" in texto:
            pendientes.append("variantes_interior_exterior_mezcladas")
        if any(nombre in texto for nombre in ("Sacaleta Love", "Majahuitas Holbox", "Variante Monte Alban", "Humanity 360")):
            pendientes.append("posibles_productos_vecinos_en_extraccion_pdf")
        if re.search(r"\bPróximamente\b", texto, flags=re.I):
            pendientes.append("disponibilidad_futura_sin_actualizar")
        if re.fullmatch(r"(?:Medidas:\s*)?[\d\s*×/.,]+", texto, flags=re.I):
            pendientes.append("fuente_solo_medidas_sin_materiales")
    if marca == "labenze" and re.search(r"\bL\d+\b.*\bR\d+\b.*\bP\d+\b", texto) and not correccion:
        pendientes.append("columnas_de_bases_y_cuerpo_mezcladas")
    if re.search(r"\b(?:y|de|en|con|por)\s*[,.]?$", texto, flags=re.I):
        pendientes.append("frase_truncada_en_fuente")
    return list(dict.fromkeys(pendientes))


def nombre_producto_proveedor(item: dict) -> str:
    """Acorta nombres que contienen una ficha completa sin crear otra identidad."""
    nombre = _texto(item.get("name"))
    fuente = _texto(item.get("description"))
    marca = _marca(item)
    atributos = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    codigo = _texto(item.get("sku") or atributos.get("source_code"))
    if marca == "sunon":
        return reconciliar_ficha_sunon(item)[0]
    if marca == "jome" and codigo and nombre == codigo:
        bloque = _texto(atributos.get("block") or item.get("collection"))
        if bloque and bloque.casefold() not in {"sin-bloque", "accesorios", "jome"}:
            return f"{_nombre_bloque_jome(bloque)} · {codigo}"
    if not nombre or nombre != fuente:
        return nombre
    if marca == "alma":
        # Separadores explícitos del fabricante; no se deduce el modelo.
        return re.split(r"\s+(?:Arm and Rim|Rim and Wheels|Seat Support|Ceramic Top|Removable Top|Frame|S&B|Top|Rim|Arm|Arms|Armrests|Base)\s*:", nombre, maxsplit=1, flags=re.I)[0].strip()
    if marca not in {"conceptos", "jome"} or len(nombre) < 55:
        return nombre
    texto = redactar_descripcion_proveedor(item).rstrip(".")
    if marca == "conceptos":
        if texto.startswith("Estructura en escuadra") and codigo:
            return f"Modelo {codigo}"
        return texto.split(", con ", 1)[0].split(". ", 1)[0]
    if re.match(r"^(?:\d|plástico\b|faldón\b)", nombre, flags=re.I):
        bloque = _texto(atributos.get("block"))
        if bloque and codigo:
            return f"{_nombre_bloque_jome(bloque)} · {codigo}"
    modelo = re.search(r"\bModelo\s+([^,.;]+)", texto, flags=re.I)
    titulo = re.split(
        r"[,;]|\b(?:fabricad[oa]s?|elaborad[oa]s?|terminado|acabado)\b|"
        r"\s+con\s+(?:base|cubierta|asiento|estructura)\b",
        texto, maxsplit=1, flags=re.I,
    )[0].strip(" .")
    if len(titulo) >= 10 and len(titulo) < len(nombre):
        if modelo and modelo.group(1).casefold() not in titulo.casefold():
            titulo += " " + modelo.group(1).strip()
        return titulo
    return nombre
