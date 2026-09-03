from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit


MAX_COLLECTION_LENGTH = 120
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_GENERIC_SOFA_COLLECTIONS = frozenset({"sofa", "sofas"})


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    clean = " ".join(value.split())
    if (
        not clean
        or len(clean) > MAX_COLLECTION_LENGTH
        or any(unicodedata.category(character) in _CONTROL_CATEGORIES for character in clean)
    ):
        return ""
    return clean


def _fold(value: object) -> str:
    return " ".join(
        "".join(
            character
            for character in unicodedata.normalize("NFKD", str(value or ""))
            if not unicodedata.combining(character)
        ).casefold().split()
    )


def _evidence(item: dict) -> str:
    values = [
        item.get("product_url"),
        item.get("image_url"),
        item.get("match_status"),
        item.get("description_source"),
    ]
    references = item.get("image_references")
    if isinstance(references, (list, tuple)):
        values.extend(references)
    return " ".join(str(value or "") for value in values).casefold()


def _pdf_page(evidence: str, filename: str) -> int | None:
    match = re.search(rf"{re.escape(filename.casefold())}#page=(\d+)", evidence)
    return int(match.group(1)) if match else None


def _offiho_collection(item: dict) -> str:
    explicit = _text(item.get("collection"))
    explicit_labels = {
        "offiho": "Offiho",
        "offiho black": "Offiho Black",
        "colos": "Colos",
        "econosillas": "Econosillas",
        "econo sillas": "Econosillas",
    }
    if _fold(explicit) in explicit_labels:
        return explicit_labels[_fold(explicit)]

    evidence = _evidence(item)
    if "colos.it" in evidence or "/colos/" in evidence or "official_colos" in evidence:
        return "Colos"
    if "offihoblack.com" in evidence:
        return "Offiho Black"

    black_colos_page = _pdf_page(evidence, "lp-black-colos-jul2026.pdf")
    if black_colos_page is not None:
        return "Colos" if black_colos_page >= 14 else "Offiho Black"

    offiho_econo_page = _pdf_page(evidence, "lp-offiho-econo-sillas-jul2026.pdf")
    if offiho_econo_page is not None:
        return "Econosillas" if offiho_econo_page >= 16 else "Offiho"

    if (
        "/econosillas/" in evidence
        or "folletoeconosillas" in evidence
        or "econosillas-" in evidence
    ):
        return "Econosillas"
    return "Offiho"


def _tarkett_collection(item: dict) -> str:
    explicit = _text(item.get("collection"))
    if explicit:
        return explicit
    path = unquote(urlsplit(str(item.get("product_url") or "")).path).casefold()
    match = re.search(r"coleccion-c\d+-([^/]+)", _fold(path).replace(" ", "-"))
    if match:
        return " ".join(part.capitalize() for part in match.group(1).split("-") if part)

    name = _fold(item.get("name"))
    families = (
        ("ambienta stone", "Ambienta Stone"),
        ("ambienta series", "Ambienta Series"),
        ("ambienta", "Ambienta"),
        ("aurea tech", "Aurea Tech"),
        ("aurea click", "Aurea Click"),
        ("aurea pro", "Aurea Pro"),
        ("desso grezzo bloom", "Desso Grezzo Bloom"),
        ("desso grezzo vivid", "Desso Grezzo Vivid"),
        ("desso grezzo", "Desso Grezzo"),
        ("desso ess strct", "Desso Essence Structure"),
        ("desso defend", "Desso Defend"),
        ("desso grain", "Desso Grain"),
        ("desso", "Desso"),
        ("inspiration", "Inspiration"),
        ("injoy", "Injoy"),
        ("linha iq", "iQ"),
        ("loseta 3.1mm", "Loseta 3.1 mm"),
        ("loseta tc", "Loseta vinílica"),
        ("square set acoustic", "Square Set Acoustic"),
        ("ultrabond", "Adhesivos"),
    )
    for marker, collection in families:
        if marker in name:
            return collection
    if name.startswith(("cb 20 ", "tdc 50 ")):
        return "Zoclos y perfiles"
    if name.startswith("mw "):
        return "Accesorios"
    return "Otros Tarkett"


def _sonara_collection(item: dict) -> str:
    haystack = _fold(f"{item.get('name', '')} {item.get('description', '')}")
    if "shapes" in haystack:
        return "Sonara Shapes"
    if "custom" in haystack:
        return "Sonara Custom"
    if "flex" in haystack:
        return "Sonara Flex"
    if "suspendid" in haystack or "sacc" in haystack:
        return "Paneles suspendidos"
    if any(term in haystack for term in ("herraje", "tubo", "canal", "perfil", "kit ")):
        return "Herrajes y perfiles"
    if any(term in haystack for term in ("adh", "adhes", "pegamento", "aislante", "ecomute", "cubeta")):
        return "Adhesivos y aislantes"
    if "panel" in haystack or "lambrin" in haystack:
        return "Paneles y lambrines"
    return "Otros Sonara"


def _sunon_collection(item: dict) -> str:
    name = _fold(item.get("name"))
    if any(term in name for term in ("task chair", "office chair", "executive chair", "manager chair")):
        return "Sillas operativas"
    if any(term in name for term in ("training", "multipurpose", "stackable", "school chair")):
        return "Sillas de capacitación"
    if any(term in name for term in ("guest chair", "visitor chair")):
        return "Sillas visitantes"
    if any(term in name for term in ("stool", "bar chair", "bench")):
        return "Bancos"
    if any(term in name for term in ("sofa", "lounge", "seating", "ottoman", "armchair")):
        return "Salas y lounge"
    if "chair" in name:
        return "Sillas"
    if any(term in name for term in ("cabinet", "storage", "locker", "pedestal", "credenza", "bookcase", "shelf", "filing", " file", "tower", "wardrobe", " door")):
        return "Almacenamiento"
    if any(term in name for term in ("desk", "workstation", "private office", "height-adjustable", "worksurface", "return", "steel leg")):
        return "Escritorios"
    if any(term in name for term in ("table", "coffee", "occasional", "conference")):
        return "Mesas"
    if any(term in name for term in ("screen", "spine", "cable", "power", "accessory", "bracket", "monitor", "panel", "socket", "division", "wire management")):
        return "Accesorios"
    return "Otros Sunon"


def _lumbro_collection(item: dict) -> str:
    explicit = _text(item.get("collection"))
    if explicit:
        return explicit
    haystack = _fold(f"{item.get('name', '')} {item.get('description', '')}")
    if any(term in haystack for term in ("interconex", "jumper", "arnes")):
        return "Interconexión"
    return "Multicontactos"


def _lauco_collection(item: dict) -> str:
    name = _text(item.get("name"))
    match = re.match(r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)", name)
    if not match:
        return "Otros Lauco"
    token = match.group(1)
    return token if token.isupper() else token.capitalize()


def _conceptos_collection(item: dict) -> str:
    name = _fold(item.get("name"))
    match = re.match(r"(?:sillon|loveseat|sofa|taburete|ottoman)\s+([a-z0-9]+)", name)
    if match and match.group(1) not in {
        "individual", "esquinero", "curvo", "conector", "dos", "sin",
    }:
        return match.group(1).capitalize()
    if "cushion" in name or "cojin" in name:
        return "Cojines"
    if any(term in name for term in ("modular", "ottoman", "esquinero", "conector", "sin brazos", "peninsula")):
        return "Modulares"
    return "Otros Conceptos"


def resolve_catalog_collection(supplier: object, item: object) -> str:
    """Devuelve la colección visible sin alterar la identidad comercial del producto."""
    clean_supplier = _fold(supplier).replace(" ", "-")
    clean_item = item if isinstance(item, dict) else {}
    explicit = _text(clean_item.get("collection"))

    if clean_supplier == "offiho":
        return _offiho_collection(clean_item)
    if clean_supplier == "tarkett":
        return _tarkett_collection(clean_item)
    if clean_supplier == "sonara" and not explicit:
        return _sonara_collection(clean_item)
    if clean_supplier == "sunon" and not explicit:
        return _sunon_collection(clean_item)
    if clean_supplier == "lumbro":
        return _lumbro_collection(clean_item)
    if clean_supplier == "lauco" and (
        not explicit or _fold(explicit) in _GENERIC_SOFA_COLLECTIONS
    ):
        return _lauco_collection(clean_item)
    if clean_supplier == "conceptos" and (
        not explicit or _fold(explicit) in _GENERIC_SOFA_COLLECTIONS
    ):
        return _conceptos_collection(clean_item)
    return explicit or "Otros"
