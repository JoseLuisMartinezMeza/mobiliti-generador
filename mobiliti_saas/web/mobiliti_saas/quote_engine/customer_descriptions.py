"""Presentación comercial de catálogos sin modificar sus datos de origen."""

from __future__ import annotations

import re
import unicodedata

from .descriptions import _translate_part, normalize_description_language
from .alma_descriptions import redactar_texto_alma, titulo_alma
from .sunon_descriptions import redactar_texto_sunon, titulo_sunon


CATALOGOS_COMERCIALES = frozenset({
    "tarkett", "offiho", "cr-global", "sonara", "sunon", "alma", "lumbro",
    "jome", "lauco", "idelika", "conceptos", "labenze", "requiez",
})

# Sólo vocabulario técnico inequívoco; nombres comerciales y claves se conservan.
ACENTOS = {
    "descripcion": "descripción", "modulo": "módulo", "modulos": "módulos",
    "sillon": "sillón", "sofa": "sofá", "sofas": "sofás", "cojin": "cojín",
    "movil": "móvil", "moviles": "móviles", "poliester": "poliéster",
    "plastico": "plástico", "plastica": "plástica", "plasticos": "plásticos",
    "ergonomico": "ergonómico", "ergonomica": "ergonómica",
    "neumatico": "neumático", "neumatica": "neumática",
    "mecanico": "mecánico", "mecanica": "mecánica", "mecanicas": "mecánicas",
    "electrico": "eléctrico", "electrica": "eléctrica", "electrificacion": "electrificación",
    "electrostatico": "electrostático", "electrostatica": "electrostática",
    "solido": "sólido", "solida": "sólida", "sintetico": "sintético",
    "sintetica": "sintética", "ceramica": "cerámica", "ceramico": "cerámico",
    "acustico": "acústico", "acustica": "acústica", "diametro": "diámetro",
    "recepcion": "recepción", "reunion": "reunión", "reclinacion": "reclinación",
    "regulacion": "regulación", "rotacion": "rotación", "configuracion": "configuración",
    "inclinacion": "inclinación", "extension": "extensión", "version": "versión",
    "garantia": "garantía", "estandar": "estándar", "aluminio": "aluminio",
    "ventilacion": "ventilación", "abrasion": "abrasión", "humedo": "húmedo",
    "frio": "frío", "clasico": "clásico", "clasica": "clásica",
    "tecnica": "técnica", "tecnicas": "técnicas", "tecnicos": "técnicos",
    "lamina": "lámina", "laminas": "láminas", "melaminico": "melamínico",
    "polipropileno": "polipropileno", "algodon": "algodón", "laser": "láser",
    "cajon": "cajón", "pizarron": "pizarrón", "travesano": "travesaño",
    "aleacion": "aleación", "ecologico": "ecológico", "ecologica": "ecológica",
    "area": "área", "areas": "áreas", "alamo": "álamo",
}
_ACENTOS_RE = re.compile(r"\b(?:" + "|".join(ACENTOS) + r")\b", re.I)
_SIGLAS = re.compile(r"\b(?:abs|mdf|hpl|pvc|pu|pp|pa|usb|usb-c|ac|dc|e0|e1|bifma|ansi)\b", re.I)


def _clave(texto: str) -> str:
    return "".join(
        letra for letra in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(letra)
    ).strip(" .")


def _clave_deduplicacion(texto: str) -> str:
    """Compara textos completos sin cambiar modelos ni claves visibles."""
    texto = _clave(texto)
    texto = re.sub(r"\s*·\s*", " ", texto)
    texto = re.sub(r"(?<=\d)\s*(mm|cm|kg|m[23])\b", r" \1", texto)
    return " ".join(texto.split())


def es_aviso_interno(texto: str) -> bool:
    """Reconoce únicamente avisos administrativos conocidos, no restricciones de venta."""
    texto = _clave(texto)
    return bool(re.search(
        r"^(?:codigo (?:oficial )?(?:por verificar|no es unico)|"
        r"codigo de proveedor faltante\b|"
        r"codigo [^:;]+ requiere revision en pagina \d+\b|"
        r"modelo no encontrado en spec guide|revision documental local|"
        r"missing_code\b|price_pending\b|variant_not_quotable\b)", texto,
    )) or "variante no cotizable" in texto


def _acentuar(texto: str) -> str:
    def sustituir(match: re.Match) -> str:
        original = match.group()
        corregido = ACENTOS[original.lower()]
        return corregido.capitalize() if original[:1].isupper() else corregido
    return _ACENTOS_RE.sub(sustituir, texto)


def _redactar_segmento(texto: str, idioma: str, catalogo: str) -> str:
    texto = re.sub(r"\s+", " ", texto).strip(" .|;•")
    if not texto:
        return ""
    if idioma == "es":
        if catalogo == "alma":
            texto = redactar_texto_alma(texto)
        elif catalogo == "sunon":
            revisado = redactar_texto_sunon(texto)
            texto = _translate_part(texto) if revisado == texto else revisado
        elif not catalogo:
            # Offiho y los demás proveedores ya redactan en español. Traducir
            # palabras sueltas puede convertir nombres de tapices en materiales.
            texto = _translate_part(texto)
        # Un bloque en mayúsculas es una ficha, no un nombre de modelo.
        letras = [letra for letra in texto if letra.isalpha()]
        if letras and sum(letra.isupper() for letra in letras) / len(letras) > .8:
            texto = texto.lower()
        texto = _acentuar(texto)
        texto = re.sub(r"^Notas:\s*nota:\s*", "Notas: ", texto, flags=re.I)
        texto = re.sub(
            r"^Garantía:\s*(?:Garantía\s+(?:de\s+)?)?(\d+)\s+años\b",
            r"Garantía: \1 años", texto, flags=re.I,
        )
        if catalogo == "sunon":
            colores = {"black": "negro", "white": "blanco", "gray": "gris", "grey": "gris",
                       "red": "rojo", "green": "verde", "blue": "azul", "orange": "naranja",
                       "yellow": "amarillo"}
            color = re.fullmatch(r"Color:\s*(\w+)", texto, flags=re.I)
            if color and color.group(1).casefold() in colores:
                texto = "Color: " + colores[color.group(1).casefold()]
        elif catalogo == "alma":
            texto = re.sub(
                r"^Sin cojín Aluminio:\s*Recubrimiento en polvo$",
                "Sin cojín; aluminio con recubrimiento en polvo", texto, flags=re.I,
            )
        elif catalogo == "requiez":
            texto = re.sub(r"\bng\s*/\s*gr\b", "NG/GR", texto, flags=re.I)
        texto = re.sub(r"\bEntrega:\s*Entrega\s+", "Entrega: ", texto, flags=re.I)
        texto = re.sub(r"\bVariante:\s*", "Configuración: ", texto, flags=re.I)
        texto = re.sub(
            r"^Configuración:\s*((?:NEGR[OA]|BLANC[OA]|GRIS|AZUL|ROJO|VERDE|NARANJA|AMARILLO|CAMELLO|BEIGE|OXFORD))$",
            r"Color: \1", texto, flags=re.I,
        )
        if re.match(r"^Configuración:", texto, flags=re.I):
            texto = re.sub(
                r"\b(?:CON|SIN)\s+(?:BRAZOS|CABECERA)\b",
                lambda match: match.group().lower(), texto, flags=re.I,
            )
        texto = re.sub(r"\bStandard\b", "Estándar", texto, flags=re.I)
        texto = re.sub(r"\b(?:Color|Acabado):\s*NO\s+APLICA\b", "", texto, flags=re.I)
        texto = re.sub(
            r"\b(?:NEGR[OA]|BLANC[OA]|GRIS|AZUL|ROJO|VERDE|NARANJA|AMARILLO|CAMELLO|BEIGE|OXFORD)\b",
            lambda match: match.group().lower(), texto,
        )
    texto = re.sub(r"(?<=\d)\s*(mm|cm|kg|m³|m²)\b", r" \1", texto, flags=re.I)
    texto = re.sub(r"\s+([,;])", r"\1", texto)
    texto = re.sub(r"\s*:\s*", ": ", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    texto = _SIGLAS.sub(lambda match: match.group().upper(), texto)
    texto = re.sub(r"(?<=[.!?])\s+([a-záéíóúñ])", lambda match: " " + match.group(1).upper(), texto)
    return texto[:1].upper() + texto[1:]


def _normalizar_unidades_titulo(texto: str, catalogo: str) -> str:
    # Los espesores y longitudes figuran en estos títulos de catálogo. No
    # aplicar esta regla a modelos de otras marcas, como «SERIE20MM».
    if catalogo not in {"tarkett", "sonara"}:
        return texto
    texto = re.sub(r"(?<=\d)\s*mts\b\.?", " m", texto, flags=re.I)
    return re.sub(r"(?<=\d)\s*(mm|cm|m)\b", lambda m: " " + m.group(1).lower(), texto, flags=re.I)


def construir_descripcion_catalogo(
    nombre: object, descripcion: object, idioma: object = "es", *, catalogo: str = "",
) -> str:
    """Separa especificaciones y condiciones; nunca resume cortando características."""
    idioma = normalize_description_language(idioma)
    titulo = re.sub(r"\s+", " ", str(nombre or "")).strip(" .*")
    titulo = _normalizar_unidades_titulo(titulo, catalogo)
    if idioma == "es" and catalogo == "cr-global":
        titulo = re.sub(r"^Vertebra\b", "Vértebra", titulo, flags=re.I)
    titulo_origen = titulo
    if idioma == "es" and catalogo == "alma":
        titulo = titulo_alma(titulo)
    elif idioma == "es" and catalogo == "sunon":
        titulo = titulo_sunon(titulo)
    fuente = str(descripcion or "")
    fuente = re.sub(r"\b(?:Material|Function|Process)\s+Description\s*:", "", fuente, flags=re.I)
    segmentos: list[str] = []
    vistos = {_clave_deduplicacion(titulo), _clave_deduplicacion(titulo_origen)}
    for parte in re.split(r"\s*\|\s*|[\r\n]+|\s*\d+\)\s*", fuente):
        parte = parte.strip()
        if not parte or re.match(
            r"^(?:fuente|hash\s+(?:fuente|fila)|url|clave|sku|colecci[oó]n|unidad)\s*:",
            parte, flags=re.I,
        ) or es_aviso_interno(parte):
            continue
        if _clave(parte) in {"standard", "estandar"}:
            continue
        parte = _normalizar_unidades_titulo(parte, catalogo)
        # El nombre ya figura en el encabezado; no repetirlo al inicio de la ficha.
        if titulo_origen and re.match(re.escape(titulo_origen) + r"(?:\s|[.:;]|$)", parte, re.I):
            resto = re.sub(r"^" + re.escape(titulo_origen) + r"\b[\s.:;]*", "", parte, flags=re.I)
            if not re.match(r"^(?:de|con|en|para|sin|y)\b", resto, re.I) or _clave(resto) == "de offiho":
                parte = resto
        texto = _redactar_segmento(parte, idioma, catalogo)
        clave = _clave_deduplicacion(texto)
        if not clave or clave in vistos or clave == "de offiho":
            continue
        vistos.add(clave)
        segmentos.append(texto + ("" if texto.endswith((".", "!", "?")) else "."))
    # Los modelos se conservan literalmente; la categoría logística no es una ficha comercial.
    return "\n".join([titulo + "." if titulo else "", *segmentos]).strip()
