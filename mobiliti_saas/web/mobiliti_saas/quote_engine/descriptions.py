from __future__ import annotations

import re
from typing import Any


SPANISH_CATEGORY_LABELS = {
    "Silla": "Silla",
    "Mesas de Apoyo": "Mesa de apoyo",
    "Escritorios-WorkStation": "Escritorio workstation",
    "Sillones": "Sillon",
    "Mesas de Juntas": "Mesa de juntas",
    "Librero - Locker - Gabinete": "Librero, locker o gabinete",
    "Archiveros Moviles y Fijos": "Archivero movil o fijo",
    "Phonebooths": "Phonebooth",
    "Multicontactos": "Multicontacto",
    "Terminados": "Producto terminado",
    "Bancos": "Banco",
    "Cocineta": "Cocineta",
    "Pizarrones": "Pizarron",
}

ENGLISH_CATEGORY_LABELS = {
    "Silla": "Chair",
    "Mesas de Apoyo": "Occasional table",
    "Escritorios-WorkStation": "Workstation desk",
    "Sillones": "Lounge seating",
    "Mesas de Juntas": "Conference table",
    "Librero - Locker - Gabinete": "Bookcase, locker or cabinet",
    "Archiveros Moviles y Fijos": "Mobile or fixed file cabinet",
    "Phonebooths": "Phone booth",
    "Multicontactos": "Power module",
    "Terminados": "Finished product",
    "Bancos": "Stool",
    "Cocineta": "Kitchenette",
    "Pizarrones": "Whiteboard",
}

PHRASE_TRANSLATIONS = [
    ("Material Description", "Descripcion de materiales"),
    ("Function Description", "Descripcion funcional"),
    ("Finishing", "Acabado"),
    ("four-door file cabinet with display area", "archivero de cuatro puertas con area de exhibicion"),
    ("E0 grade MDF/particle board", "tablero MDF/aglomerado grado E0"),
    ("E0 grade particleboard", "tablero aglomerado grado E0"),
    ("E0 grade particle board", "tablero aglomerado grado E0"),
    ("E0 grade MDF", "MDF grado E0"),
    ("natural wood veneer edge banding", "canto de chapa de madera natural"),
    ("natural wood veneer", "chapa de madera natural"),
    ("wood grain parts", "partes con veta de madera"),
    ("Hinges", "Bisagras"),
    ("concealed hinge", "bisagra oculta"),
    ("Lock", "Cerradura"),
    ("double-door lock", "cerradura de doble puerta"),
    ("Long conference table", "mesa de juntas alargada"),
    ("Long meeting table", "mesa de juntas alargada"),
    ("Rectangular conference table", "mesa de juntas rectangular"),
    ("round aluminum alloy base", "base redonda de aleacion de aluminio"),
    ("round aluminum base", "base redonda de aluminio"),
    ("aluminum alloy Sofa Legs", "patas de sofa de aleacion de aluminio"),
    ("aluminum alloy", "aleacion de aluminio"),
    ("the majority of the board is made of", "la mayor parte del tablero es de"),
    ("majority of the board is made of", "la mayor parte del tablero es de"),
    ("Table Legs", "patas de mesa"),
    ("angled aluminum alloy beveled legs", "patas biseladas anguladas de aleacion de aluminio"),
    ("45-degree aluminum alloy die-casting angled legs", "patas anguladas de aluminio fundido a 45 grados"),
    ("45° angled aluminum alloy beveled legs", "patas biseladas de aleacion de aluminio a 45 grados"),
    ("polished surface", "superficie pulida"),
    ("Straight employee desk", "estacion de trabajo recta"),
    ("Straight Desk for Staff", "escritorio recto para personal"),
    ("steel legs on both sides", "patas de acero en ambos lados"),
    ("impregnated melamine paper", "papel melaminico impregnado"),
    ("superior layering", "laminado superior"),
    ("PVC edge banding strips", "cantos de PVC"),
    ("PVC edge banding strios", "cantos de PVC"),
    ("PVC edge strips", "cantos de PVC"),
    ("PVC edge strios", "cantos de PVC"),
    ("ABS laser edge banding strips", "cantos laser de ABS"),
    ("the tabletop uses", "la cubierta usa"),
    ("tabletop uses", "la cubierta usa"),
    ("edge banding strips", "cantos"),
    ("edge banding strios", "cantos"),
    ("edge banding", "canto"),
    ("Middle Steel Legs", "patas centrales de acero"),
    ("Pull Rod", "tirante"),
    ("Beam", "Travesano"),
    ("made of high-quality cold-rolled steel", "fabricado en acero laminado en frio de alta calidad"),
    ("high-quality cold-rolled steel", "acero laminado en frio de alta calidad"),
    ("round tube made of Q195 steel", "tubo redondo de acero Q195"),
    ("surface electrostatic powder coating", "superficie con recubrimiento electrostatico en polvo"),
    ("disc made of SPHC", "disco de SPHC"),
    ("on the surface", "en la superficie"),
    ("Support Frame", "estructura de soporte"),
    ("Cable management", "gestion de cableado"),
    ("wiring hole", "orificio pasacables"),
    ("Steel edge side table with handle", "mesa auxiliar con borde de acero y jaladera"),
    ("T-shaped steel legs", "patas de acero tipo T"),
    ("electrostatic powder coating surface", "superficie con recubrimiento electrostatico en polvo"),
    ("water-based environmentally friendly paint", "pintura ecologica base agua"),
    ("Irregular coffee table", "mesa de cafe irregular"),
    ("Square negotiation table", "mesa cuadrada de negociacion"),
    ("Round coffee table", "mesa redonda de cafe"),
    ("wooden base round coffee table", "mesa redonda de cafe con base de madera"),
    ("stone slab on top", "cubierta de piedra en la parte superior"),
    ("C-shaped wooden legs", "patas de madera en forma de C"),
    ("beveled edges", "bordes biselados"),
    ("eco-friendly", "ecologico"),
    ("eco-friendly paint finish", "acabado ecologico de pintura"),
    ("paint finish", "acabado de pintura"),
    ("Portable Power", "modulo electrico portatil"),
    ("Round plate Base", "base de placa redonda"),
    ("Cover Plate", "placa de cubierta"),
    ("surface material is", "material de superficie"),
    ("impregnated film paper", "papel film impregnado"),
    ("particleboard", "tablero aglomerado"),
    ("adopt", "usa"),
    ("below", "inferior"),
    ("Chair Body", "cuerpo de la silla"),
    ("Fabric Combination", "combinacion de telas"),
    ("Inner seat with premium linen", "asiento interior con lino premium"),
    ("Outer back with PVC leather", "respaldo exterior con piel PVC"),
    ("fold endurance", "resistencia al doblado"),
    ("no cracks", "sin grietas"),
    ("Cut cotton", "algodon cortado"),
    ("PP integrated", "PP integrado"),
    ("backrest uses mesh", "respaldo en malla"),
    ("uses high-quality linen velvet", "con terciopelo de lino de alta calidad"),
    ("high-quality linen velvet", "terciopelo de lino de alta calidad"),
    ("high-quality linen fabric", "tela de lino de alta calidad"),
    ("linen fabric", "tela de lino"),
    ("high-quality mesh", "malla de alta calidad"),
    ("dry rubbing color fastness grade", "solidez del color al frote en seco grado"),
    ("dry rubbing fastness", "solidez al frote en seco"),
    ("wet rubbing color fastness", "solidez del color al frote humedo"),
    ("abrasion resistance", "resistencia a la abrasion"),
    ("water repellency", "repelencia al agua"),
    ("oil repellency", "repelencia al aceite"),
    ("stain resistance", "resistencia a manchas"),
    ("Chair Back", "respaldo"),
    ("Seat Cushion", "cojin del asiento"),
    ("Chair Bases", "base de la silla"),
    ("Gas Cylinder", "cilindro de gas"),
    ("Chair Casters", "rodajas"),
    ("Lumbar Support Adjustment", "ajuste de soporte lumbar"),
    ("4D Quick Installation Armrests", "descansabrazos 4D de instalacion rapida"),
    ("PP height-adjustable armrest", "descansabrazos de PP ajustable en altura"),
    ("height-adjustable armrest", "descansabrazos ajustable en altura"),
    ("Armrests", "descansabrazos"),
    ("5-Star Bases", "base de cinco puntas"),
    ("Chair Frame", "estructura de la silla"),
    ("nylon + 30% fiberglass integrated back frame", "marco de respaldo integrado de nylon con 30% de fibra de vidrio"),
    ("30% fiberglass integrated back frame", "marco de respaldo integrado con 30% de fibra de vidrio"),
    ("molded foam", "espuma moldeada"),
    ("cut foam", "espuma cortada"),
    ("density", "densidad"),
    ("equipped with", "equipado con"),
    ("grade bentwood board", "tablero curvado de grado E1"),
    ("E1 grade bentwood seat", "asiento de madera curvada grado E1"),
    ("bentwood seat", "asiento de madera curvada"),
    ("PP seat shell", "carcasa de asiento en PP"),
    ("weight capacity", "capacidad de carga"),
    ("Zhongtai mechanism", "mecanismo Zhongtai"),
    ("SAMHONGSA class 3 gas cylinder", "cilindro de gas SAMHONGSA clase 3"),
    ("diameter nylon 5-star base", "de diametro en nylon de cinco puntas"),
    ("PU casters", "rodajas de PU"),
    ("vertical adjustment", "ajuste vertical"),
    ("quick installation design", "diseno de instalacion rapida"),
    ("adjustable vertically", "ajustables verticalmente"),
    ("rotatable left and right", "rotacion izquierda y derecha"),
    ("forward and backward sliding", "deslizamiento hacia adelante y atras"),
    ("width adjustable", "ancho ajustable"),
    ("Tabletop", "cubierta"),
    ("Veneer", "Chapa"),
    ("thickness", "espesor"),
    ("the main body surface covered with", "la superficie del cuerpo principal recubierta con"),
    ("main body surface covered with", "superficie del cuerpo principal recubierta con"),
    ("main body surface covered", "superficie del cuerpo principal recubierta"),
    ("Cabinet", "gabinete"),
    ("four-door file cabinet", "archivero de cuatro puertas"),
    ("glass panel door with embedded glass", "puerta de panel de vidrio con vidrio embebido"),
    ("glass panel door", "puerta de panel de vidrio"),
    ("embedded glass", "vidrio embebido"),
    ("tempered glass", "vidrio templado"),
    ("glass doors", "puertas de vidrio"),
    ("Door Panel", "panel de puerta"),
    ("display area", "area de exhibicion"),
    ("shelves", "repisas"),
    ("round negotiation table", "mesa redonda de negociacion"),
    ("Square conference table", "mesa de juntas cuadrada"),
    ("meeting table", "mesa de juntas"),
    ("Q195 steel", "acero Q195"),
    ("surface uses", "superficie con"),
    ("wooden base", "base de madera"),
    ("steel frame", "estructura de acero"),
    ("electrostatic powder coating", "recubrimiento electrostatico en polvo"),
    ("melamine paper", "papel melaminico"),
    ("excellent stability", "excelente estabilidad"),
    ("strong resistance to dirt and wear", "alta resistencia a suciedad y desgaste"),
    ("door-shaped steel legs", "patas de acero tipo marco"),
    ("Leg nails", "regatones"),
    ("PP fixed nails", "regatones fijos de PP"),
    ("damping buffer hinge", "bisagra con cierre amortiguado"),
    ("Handles", "jaladeras"),
    ("seat density", "densidad del asiento"),
    ("seat shell", "carcasa de asiento"),
    ("backrest", "respaldo"),
    ("three-proof fabric", "tela repelente"),
    ("leather and fabric combination", "combinacion de piel y tela"),
    ("main areas use linen velvet fabric", "zonas principales con tela de lino aterciopelada"),
    ("linen velvet fabric", "tela de lino aterciopelada"),
    ("bottom part with ecological polyurethane (EPU) synthetic leather", "parte inferior con piel sintetica de poliuretano ecologico (EPU)"),
    ("bottom part uses ecological polyurethane (EPU) synthetic leather", "parte inferior con piel sintetica de poliuretano ecologico (EPU)"),
    ("ecological polyurethane (EPU) synthetic leather", "piel sintetica de poliuretano ecologico (EPU)"),
    ("bottom part uses", "parte inferior con"),
    ("bottom part", "parte inferior"),
    ("Foam", "Espuma"),
    ("high resilience foam", "espuma de alta resiliencia"),
    ("built-in welded steel frame", "estructura de acero soldada integrada"),
    ("other parts", "otras partes"),
    ("steel Sofa Legs", "patas de sofa de acero"),
    ("Sofa Legs", "patas de sofa"),
    ("hollow round tube", "tubo redondo hueco"),
    ("solid round bar", "barra redonda solida"),
    ("reinforced round steel", "acero redondo reforzado"),
    ("round tube", "tubo redondo"),
    ("made of", "fabricado en"),
    ("dry rub fastness", "solidez al frote en seco"),
    ("supports", "soporta"),
    ("includes", "incluye"),
    ("powder-coated", "con pintura electrostatica"),
    ("PA casters", "rodajas de PA"),
    ("Casters", "rodajas"),
    ("molded", "espuma moldeada"),
    ("Paint", "Pintura"),
    ("surface", "superficie"),
    ("diameter", "diametro"),
    ("uses", "con"),
    ("cycles", "ciclos"),
    ("times", "ciclos"),
    ("mesh", "malla"),
    ("Stroke", "recorrido"),
]

WORD_TRANSLATIONS = {
    "with": "con",
    "for": "para",
    "on": "en",
    "and": "y",
    "or": "o",
    "the": "el",
    "an": "un",
    "of": "de",
    "grade": "grado",
    "long": "de largo",
}


def normalize_description_language(value: Any) -> str:
    text = str(value or "es").strip().lower()
    if text in {"spanish", "espanol", "español", "spa"}:
        return "es"
    if text in {"english", "ingles", "inglés", "eng"}:
        return "en"
    return text if text in {"es", "en"} else "es"


def build_product_description(
    product_name: Any,
    source_description: Any,
    category: str,
    language: Any = "es",
) -> str:
    language = normalize_description_language(language)
    clean_name = _clean_text(product_name)
    display_name = _display_model_name(clean_name)
    clean_source = _clean_text(source_description)

    if language == "en":
        label = ENGLISH_CATEGORY_LABELS.get(category, category or "Product")
        body = _summarize_source(clean_source, max_parts=7)
        return _join_description(label, "model", display_name, body)

    label = SPANISH_CATEGORY_LABELS.get(category, category or "Producto")
    body = _translate_description_to_spanish(clean_source)
    return _join_description(label, "modelo", display_name, body)


def _join_description(label: str, model_word: str, product_name: str, body: str) -> str:
    title = f"{label} {model_word} {product_name}.".strip()
    if body:
        return f"{title}\n{body}"
    return title


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _display_model_name(product_name: str) -> str:
    """Remove only an initial SKU/code from the visible model title."""
    text = _clean_text(product_name)
    first_token, sep, remainder = text.partition(" ")
    if not sep:
        return text

    glued_match = re.match(r"^([A-Z0-9._/-]*\d[A-Z0-9._/-]*?)([A-Z][a-z].*)$", first_token)
    if glued_match:
        return f"{glued_match.group(2)} {remainder}".strip()

    if _looks_like_model_code(first_token):
        return remainder.strip()
    return text


def _looks_like_model_code(token: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]", "", token)
    has_code_separator = bool(re.search(r"[._/-]", token))
    return (len(normalized) >= 5 or has_code_separator) and any(char.isdigit() for char in normalized)


def _summarize_source(text: str, max_parts: int = 6) -> str:
    parts = _split_description_parts(text)
    summary = ". ".join(parts[:max_parts]).strip()
    if summary and not summary.endswith("."):
        summary += "."
    return summary


def _translate_description_to_spanish(text: str) -> str:
    translated_parts = []
    for part in _split_description_parts(text):
        translated = _translate_part(part)
        if translated:
            translated_parts.append(translated)
        if len(translated_parts) >= 6:
            break
    summary = ". ".join(translated_parts).strip()
    if summary and not summary.endswith("."):
        summary += "."
    return summary


def _split_description_parts(text: str) -> list[str]:
    text = _clean_text(text)
    text = re.sub(r"\b(?:Material|Function)\s+Description\s*:", " ", text, flags=re.I)
    chunks = re.split(r"\s*(?:\d+\)|;)\s*", text)
    parts = []
    for chunk in chunks:
        chunk = chunk.strip(" .:-")
        if not chunk or chunk.lower() in {"material description", "function description"}:
            continue
        parts.append(chunk)
    return parts


def _translate_part(text: str) -> str:
    translated = _normalize_symbols(text)
    for source, target in sorted(PHRASE_TRANSLATIONS, key=lambda item: len(item[0]), reverse=True):
        translated = re.sub(re.escape(source), target, translated, flags=re.I)
    for source, target in WORD_TRANSLATIONS.items():
        translated = re.sub(rf"\b{re.escape(source)}\b", target, translated, flags=re.I)
    translated = _normalize_symbols(translated)
    translated = translated.replace("mm", " mm")
    translated = re.sub(r"\bkg/m3\b", "kg/m3", translated, flags=re.I)
    translated = re.sub(r"\bE1 tablero curvado de grado E1\b", "tablero curvado de grado E1", translated)
    translated = re.sub(r"\s*:\s*", ": ", translated)
    translated = re.sub(r"\s+", " ", translated).strip(" .")
    translated = translated[:1].upper() + translated[1:] if translated else translated
    return translated


def _normalize_symbols(text: str) -> str:
    text = text.replace("≥", " mayor o igual a ")
    text = text.replace("â‰¥", " mayor o igual a ")
    text = text.replace(">=", " mayor o igual a ")
    text = text.replace("≤", " menor o igual a ")
    text = text.replace("â‰¤", " menor o igual a ")
    text = text.replace("<=", " menor o igual a ")
    return text
