"""Traducción editorial de las fichas ALMA con nombres comerciales intactos.

El vocabulario describe tipos, componentes y acabados explícitos de la fuente.
No deduce ergonomía, resistencia, uso exterior ni materiales a partir de fotos.
"""

from __future__ import annotations

import re


_MODELOS = (
    "BANYAN TREE", "DINOSAUR EGG", "LONG BEACH", "KEY WEST", "ST.VINCENT",
    "STRING", "BALANCE", "LOOP", "JUMBO", "RUGBY", "BLOCK", "PILLOW",
    "SHELL", "CLOGS", "LOTUS", "NEST", "CHIC", "BAGEL", "BERGEN", "ROOT",
    "PIPE", "LANTERN", "PAVILION", "FLOW", "RIBBON", "OVAL", "MOONCAKE",
    "SOHO", "VATICAN", "IBIZA", "MARRAKECH", "CAYENNE", "RHODES",
    "SPAGHETTI", "PACIFIC", "LOFT", "CHIENGMAI", "KUTA", "PALMER",
    "MEDITERRANEAN", "ENZO", "MANHATTAN", "VASE", "LILLE", "LOIRE",
    "OASI", "MONTANA", "SAMUI", "DUBLIN", "CORE", "SEOUL", "CLOVER",
    "UDINE", "LAGOON", "BREEZE", "RIO",
)
_ACABADOS = (
    "Travertino Romano", "Calacata Gold", "Calcata Gold", "Jenny Grey",
    "Jenny Gray", "Feather White", "White Romano", "Sharo Blue", "Snow White",
    "Yaoyan Black", "Polarwood", "Polar wood", "Sunproof", "BATYLINE",
    "Saffron", "KUN", "JK",
)

# Frases largas primero para no traducir «teaklook» como madera de teca real.
_FRASES = {
    "aluminium in regular powder coating": "aluminio con pintura en polvo",
    "aluminium in teak look": "aluminio con acabado imitación teca",
    "aluminum in teak look": "aluminio con acabado imitación teca",
    "alu in teaklook": "aluminio con acabado imitación teca",
    "alu in heat transfer teak color": "aluminio con acabado color teca por transferencia térmica",
    "aluminium legs with teak or polar wood decoration": "patas de aluminio con detalles de teca o Polar wood",
    "alu legs with teak or polar wood decoration": "patas de aluminio con detalles de teca o Polar wood",
    "aluminium with teak rims": "aluminio con bordes de teca",
    "legs with real teak decoration": "patas con detalles de teca natural",
    "synthetic wood armrests": "descansabrazos de madera sintética",
    "synthetic wood slats": "listones de madera sintética",
    "heat-transfer teak finish": "acabado imitación teca por transferencia térmica",
    "heat transfer teak color": "acabado color teca por transferencia térmica",
    "teak color(heat transfer)": "color teca por transferencia térmica",
    "in powder coating": "con pintura en polvo",
    "with powder coating": "con pintura en polvo",
    "powder coating": "pintura en polvo",
    "powder coat": "pintura en polvo",
    "faux teak": "imitación teca",
    "real teak": "teca natural",
    "special-shape back cushion": "cojín de respaldo con forma especial",
    "regular back cushion": "cojín de respaldo estándar",
    "loose seat cushion": "cojín de asiento suelto",
    "full aluminium legs": "patas íntegramente de aluminio",
    "full alu legs": "patas íntegramente de aluminio",
    "full aluminium": "íntegramente de aluminio",
    "swivel dining armchair": "silla de comedor giratoria con brazos",
    "swivel lounge armchair": "sillón de descanso giratorio",
    "high back lounge armchair": "sillón de descanso con respaldo alto",
    "highback lounge armchair": "sillón de descanso con respaldo alto",
    "highback lounge chair": "silla de descanso con respaldo alto",
    "sling dining armchair": "silla de comedor con brazos y tejido tensado",
    "sling dining side chair": "silla de comedor sin brazos con tejido tensado",
    "sling lounge sidechair": "silla de descanso sin brazos con tejido tensado",
    "sling foot stool": "reposapiés con tejido tensado",
    "sling 2 seater sidechair": "asiento de 2 plazas sin brazos con tejido tensado",
    "dining side chair": "silla de comedor sin brazos",
    "dining sidechair": "silla de comedor sin brazos",
    "lounge side chair": "silla de descanso sin brazos",
    "lounge sidechair": "silla de descanso sin brazos",
    "dining armchair": "silla de comedor con brazos",
    "lounge armchair": "sillón de descanso",
    "lounge chair": "silla de descanso",
    "dining chair": "silla de comedor",
    "rocking armchair": "mecedora con brazos",
    "rocking chair": "mecedora",
    "deluxe folding armchair": "silla plegable Deluxe con brazos",
    "folding chair": "silla plegable",
    "square round dining table": "mesa de comedor cuadrada/redonda",
    "round dining table": "mesa de comedor redonda",
    "square dining table": "mesa de comedor cuadrada",
    "rectangular dining table": "mesa de comedor rectangular",
    "extendable dining table": "mesa de comedor extensible",
    "fixed dining table": "mesa de comedor fija",
    "long dining table": "mesa de comedor alargada",
    "dining table": "mesa de comedor",
    "round coffee table": "mesa de centro redonda",
    "rectangular coffee table": "mesa de centro rectangular",
    "lounge height coffee table": "mesa de centro a altura de descanso",
    "coffee table": "mesa de centro",
    "square side table": "mesa lateral cuadrada",
    "round side table": "mesa lateral redonda",
    "side table": "mesa lateral",
    "end table": "mesa lateral",
    "picnic table": "mesa de pícnic",
    "bar table": "mesa alta",
    "extendable table": "mesa extensible",
    "bar chair": "silla alta",
    "barstool": "banco alto",
    "counter height": "altura de mostrador",
    "bar height": "altura de barra",
    "lounge height": "altura de descanso",
    "single sofa": "sofá de 1 plaza",
    "two seater sofa": "sofá de 2 plazas",
    "two-seater sofa": "sofá de 2 plazas",
    "three seater sofa": "sofá de 3 plazas",
    "2 seater side sofa": "sofá lateral de 2 plazas",
    "lounge left arm module": "módulo de descanso con brazo izquierdo",
    "lounge right arm module": "módulo de descanso con brazo derecho",
    "left arm module": "módulo con brazo izquierdo",
    "right arm module": "módulo con brazo derecho",
    "left short arm": "brazo corto izquierdo",
    "right short arm": "brazo corto derecho",
    "left right module": "módulo izquierdo/derecho",
    "left arm": "brazo izquierdo",
    "right arm": "brazo derecho",
    "with right back": "con respaldo a la derecha",
    "with left back": "con respaldo a la izquierda",
    "low armrest": "descansabrazos bajo",
    "organic corner module": "módulo esquinero de forma orgánica",
    "corner module": "módulo esquinero",
    "center module": "módulo central",
    "extension module single": "módulo individual de extensión",
    "single module": "módulo individual",
    "single seat base module": "módulo de base para 1 plaza",
    "two-seater base module": "módulo de base para 2 plazas",
    "back rest module": "módulo de respaldo",
    "lounge corner chair": "asiento esquinero de descanso",
    "center chair": "asiento central",
    "foot stool": "reposapiés",
    "footstool": "reposapiés",
    "footsool": "reposapiés",
    "sunlounger": "camastro",
    "sunbed": "camastro",
    "day bed": "cama de descanso",
    "daybed": "cama de descanso",
    "meridienne": "chaise longue",
    "floor lamp": "lámpara de pie",
    "foor lamp": "lámpara de pie",
    "hand lamp": "lámpara portátil",
    "solar lamp": "lámpara solar",
    "solar floor lamp": "lámpara solar de pie",
    "solar led": "LED solar",
    "led light strip": "tira de luz LED",
    "decorative pillow": "cojín decorativo",
    "lumbar pillow": "cojín lumbar",
    "pillow for": "cojín para",
    "series sofa": "sofá de la serie",
    "ceramic shelving": "entrepaño cerámico",
    "bbq grill/brazier": "parrilla/asador",
    "barcart": "carrito de bar",
    "bar cart": "carrito de bar",
    "bar bart": "carrito de bar",
    "with u-shaped legs": "con patas en forma de U",
    "hanging chair": "silla colgante",
    "aluminium legs": "patas de aluminio",
    "alu legs": "patas de aluminio",
    "strap back": "respaldo de cintas",
    "aluminium back": "respaldo de aluminio",
    "sunproof strap": "cinta Sunproof",
    "flat strap": "cinta plana",
    "chinese hpl top": "cubierta de HPL de origen chino",
    "chinese sling": "tejido tensado de origen chino",
    "hpl top": "cubierta de HPL",
    "round pole": "columna redonda",
    "removable top": "cubierta desmontable",
    "ceramic top": "cubierta cerámica",
    "teak top": "cubierta de teca",
    "teak base": "base de teca",
    "hpl / ceramic / teak top": "cubierta de HPL, cerámica o teca",
    "jk top": "cubierta JK",
    "alu top": "cubierta de aluminio",
    "aluminium top": "cubierta de aluminio",
    "aluminum top": "cubierta de aluminio",
    "teak slats": "listones de teca",
    "teak decoration": "detalles de teca",
    "teak deocration": "detalles de teca",
    "flat wicker": "tejido wicker plano",
    "pvc big strap": "cinta ancha de PVC",
    "pvc strap": "cinta de PVC",
    "pvc rope": "cuerda de PVC",
    "padded in": "acolchado con",
    "regular sling": "tejido tensado estándar",
    "single sling": "tejido tensado sencillo",
    "double v weaving style": "tejido en doble V",
    "v weaving style": "tejido en V",
    "strand weaving": "tejido de cordones",
    "square weaving": "tejido cuadrado",
    "diamond weaving": "tejido en rombos",
    "dense weaving": "tejido denso",
    "vertical weaving": "tejido vertical",
    "without wheels": "sin ruedas",
    "with wheels": "con ruedas",
    "with arms": "con brazos",
    "with arm": "con brazo",
    "with sunshade": "con parasol",
    "with adjustable shade": "con sombra ajustable",
    "adjustable back": "respaldo ajustable",
    "curved armrest": "descansabrazos curvo",
    "stainless steel": "acero inoxidable",
    "aluminium frame": "estructura de aluminio",
    "alu frame": "estructura de aluminio",
    "aluminium framing": "marco de aluminio",
    "seat and back": "asiento y respaldo",
    "seat & back": "asiento y respaldo",
    "arm and rim": "brazos y borde",
    "rim and wheels": "borde y ruedas",
    "legs and arms": "patas y brazos",
    "roof, s&b": "techo, asiento y respaldo",
    "seat support": "soporte del asiento",
    "seat rim": "borde del asiento",
    "middle divider": "divisor intermedio",
    "low version": "versión baja",
    "high version": "versión alta",
    "all kun ceramic options": "todas las opciones cerámicas KUN",
    "all available options": "todas las opciones disponibles",
    "horizontal alu panel": "panel horizontal de aluminio",
    "sliding sling roof": "techo corredizo de tejido tensado",
    "fixed alu slats roof": "techo de listones fijos de aluminio",
    "electric alu slats": "listones de aluminio eléctricos",
    "left/right": "izquierda/derecha",
    "rolling blinds": "persianas enrollables",
    "aluminium slats": "listones de aluminio",
    "back panel": "panel posterior",
    "back: horizontal alu panel": "panel posterior: aluminio horizontal",
    "2x left/right electric pvc sling rolling blinds": "2 persianas enrollables eléctricas de tejido tensado de PVC, izquierda/derecha",
    "width belt": "de ancho de cinta",
    "big handle": "asa grande",
}
_PALABRAS = {
    "aluminium": "aluminio", "aluminum": "aluminio", "alu": "aluminio",
    "teak": "teca", "teaklook": "acabado imitación teca", "ceramic": "cerámica",
    "frame": "estructura", "structure": "estructura", "arms": "brazos",
    "armrests": "descansabrazos", "armrest": "descansabrazos", "rim": "borde",
    "seat": "asiento", "back": "respaldo", "legs": "patas", "wheels": "ruedas",
    "top": "cubierta", "bottom": "parte inferior", "pole": "columna",
    "roof": "techo", "shading": "protección solar", "fabric": "tela",
    "rope": "cuerda", "straps": "cintas", "strap": "cinta", "sling": "tejido tensado",
    "wicker": "tejido wicker", "planter": "macetero", "lid": "tapa",
    "stool": "taburete", "sofa": "sofá", "sof": "sofá", "corner": "esquinero",
    "pavilion": "pérgola", "cabana": "cabaña", "swing": "columpio",
    "single chair": "asiento individual", "double chair": "asiento doble",
    "with": "con", "without": "sin", "and": "y", "or": "o", "for": "para",
    "in": "en", "available": "disponibles", "extensions": "extensiones",
    "basic": "versión básica", "colourful": "multicolor", "regular": "estándar",
    "stand": "soporte", "chinese": "de origen chino", "sides": "lados",
    "electric": "eléctrico", "version": "versión", "spring": "resortes",
    "black": "negro", "white": "blanco", "table": "mesa",
    "balcony": "balcón", "decoration": "detalles", "are": "son",
    "all": "todas", "options": "opciones", "light": "luz",
}
_LABELS = re.compile(
    r"\b(?:Roof,\s*S&B|Arm and Rim|Rim and Wheels|Legs and arms|Seat\s*&\s*back|"
    r"Seat and back|Seat Support|Removable Top|Ceramic Top|Frame|Structure|S&B|"
    r"Armrests|Arms|Arm|Rim|Base|Top|Seat|Back|Wheels|Legs|Roof|Shading|Pole|Bottom)\s*:", re.I,
)


def redactar_texto_alma(texto: str) -> str:
    """Traduce términos documentados y conserva modelos, acabados y medidas."""
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not texto:
        return ""
    # Reparaciones de palabras pegadas presentes en el documento del proveedor.
    for antes, despues in (("wheelsand", "wheels and"), ("topThe", "top. The"), ("andbottom", "and bottom"), ("heattransfer", "heat transfer"), ("right t arm", "right arm")):
        texto = texto.replace(antes, despues)
    texto = re.sub(r"\bw/\s*", "with ", texto, flags=re.I)
    texto = re.sub(r"\bW\s+(?=CALCATA)", "with ", texto)
    texto = re.sub(r"(?<=\w)\s+Aluminium(?=$|\s+with Ceramic Top)", ". Material: aluminium", texto, flags=re.I)
    texto = re.sub(r"\bsling 2 seater sidechair\b", "asiento de 2 plazas sin brazos con tejido tensado", texto, flags=re.I)
    texto = re.sub(r"\b(?:aluminium|alu) legs with teak or polar wood decoration\b", "patas de aluminio con detalles de teca o Polar wood", texto, flags=re.I)
    texto = re.sub(r"\b([23])\s*-?\s*(?:seat(?:er)?|seater)\s+(sofa|sof|chair|module)\b", lambda m: {"sofa": "sofá", "sof": "sofá", "chair": "asiento", "module": "módulo"}[m.group(2).lower()] + " de " + m.group(1) + " plazas", texto, flags=re.I)
    texto = re.sub(r"\b([23])\s*-?\s*seater\b", r"de \1 plazas", texto, flags=re.I)
    protegidos: dict[str, str] = {}

    def proteger(match: re.Match) -> str:
        clave = f"ZZPROTEGIDO{len(protegidos)}ZZ"
        protegidos[clave] = match.group()
        return clave

    # Un modelo sólo se protege al comienzo o cuando la fuente lo escribe en
    # mayúsculas; «decorative pillow» y «Samui pavilion» son nombres genéricos.
    modelos = "|".join(re.escape(v) for v in sorted(_MODELOS, key=len, reverse=True))
    texto = re.sub(r"^(?:" + modelos + r")\b(?!\s+for\b)", proteger, texto, flags=re.I)
    texto = re.sub(r"\b(?:" + modelos + r")\b", proteger, texto)
    # Valores comerciales permanecen literales, aun si contienen colores.
    texto = re.sub(r"\b(?:" + "|".join(re.escape(v) for v in sorted(_ACABADOS, key=len, reverse=True)) + r")\b", proteger, texto, flags=re.I)
    texto = _LABELS.sub(lambda m: (". " if m.start() else "") + m.group(), texto)
    texto = re.sub(r"\bS&B\b", "asiento y respaldo", texto)
    diccionario = {**_PALABRAS, **_FRASES}
    patron = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(v) for v in sorted(diccionario, key=len, reverse=True)) + r")(?!\w)", re.I)
    texto = patron.sub(lambda m: diccionario[m.group().lower()], texto)
    for clave, valor in reversed(list(protegidos.items())):
        texto = texto.replace(clave, valor)
    nombres_acabado = "|".join(re.escape(v) for v in sorted(_ACABADOS, key=len, reverse=True))
    texto = re.sub(r"\bcon (" + nombres_acabado + r") cubierta cerámica\b", r"con cubierta cerámica \1", texto, flags=re.I)
    texto = re.sub(r"\btodas KUN cerámica opciones\b", "todas las opciones cerámicas KUN", texto, flags=re.I)
    texto = re.sub(r"(?<![.:;,])\s+(estructura de aluminio|brazos con detalles|asiento y respaldo con|descansabrazos de madera|base de teca)\b", r". \1", texto, flags=re.I)
    texto = re.sub(r"\bcon brazos con\b", "con brazos y", texto)
    texto = re.sub(r"\bson aluminio\b", "son de aluminio", texto)
    texto = re.sub(r"\b(cuerda|cinta) (\d+(?:[.]\d+)?)\s*mm\b", r"\1 de \2 mm", texto)
    texto = re.sub(r"(?<=\d)\s*(mm|cm)\b", r" \1", texto, flags=re.I)
    texto = re.sub(r"\s+([,;.])", r"\1", texto)
    texto = re.sub(r"[,;]\s*[.]", ".", texto)
    texto = re.sub(r",(?=\S)", ", ", texto)
    texto = re.sub(r"(?<=\w)\(", " (", texto)
    texto = re.sub(r"[.]\s*[.]", ".", texto)
    texto = re.sub(r"\s*:\s*", ": ", texto)
    texto = re.sub(r":\s*(?=[.]|$)", "", texto)
    texto = re.sub(r"\bThe\s+", "", texto)
    texto = re.sub(r"\bSunproof cinta\b", "cinta Sunproof", texto, flags=re.I)
    texto = re.sub(r"\bBATYLINE tejido tensado\b", "tejido tensado BATYLINE", texto, flags=re.I)
    texto = re.sub(r"(:\s*)(\d+(?:[.]\d+)?)\s*mm\s+(cuerda de PVC|cuerda|cinta)", r"\1\3 de \2 mm", texto, flags=re.I)
    texto = re.sub(r"\bcon (\d+) (patas)\b", r"con \1 \2", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .;,")
    texto = re.sub(r"(?<=\.)\s+([a-záéíóúñ])", lambda m: " " + m.group(1).upper(), texto)
    return texto[:1].upper() + texto[1:]


def titulo_alma(texto: str) -> str:
    """Conserva la identidad anterior a una ficha de componentes etiquetada."""
    texto = str(texto or "").strip()
    match = _LABELS.search(texto)
    return redactar_texto_alma(texto[:match.start()] if match else texto)
