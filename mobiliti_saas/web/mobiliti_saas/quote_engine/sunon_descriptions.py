"""Traducciones revisadas de las fichas Sunon, sin sustitución palabra a palabra."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata


_DATOS = Path(__file__).with_name("data") / "sunon_description_translations.json"
_ENCABEZADOS = re.compile(r"\b(?:Material|Function|Process)\s+Desc(?:r|tr)iption\s*:\s*\.?", re.I)


def _clave(texto: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(texto or ""))).strip(" .,:;-“”").casefold()


@lru_cache(maxsize=1)
def _traducciones() -> dict[str, str]:
    datos = json.loads(_DATOS.read_text(encoding="utf-8"))
    return {_clave(fila["source"]): fila["spanish"] for fila in datos["translations"]}


def redactar_texto_sunon(texto: object) -> str:
    """Admite una cláusula aislada o la ficha con enumeraciones y encabezados."""
    original = re.sub(r"\s+", " ", str(texto or "")).strip()
    neutros = {
        "coffee table": "Mesa de centro.",
        "file cabinet body": "Cuerpo de archivero.",
    }
    if _clave(original) in neutros:
        return neutros[_clave(original)]
    tabla = _traducciones()
    if _clave(original) in tabla:
        return tabla[_clave(original)]
    limpio = _ENCABEZADOS.sub("\n", original)
    partes = re.split(r"\s*\d+\)\s*|[\r\n]+|\s*\|\s*", limpio)
    resultado = []
    for parte in partes:
        if not _clave(parte):
            continue
        revisada = tabla.get(_clave(parte), neutros.get(_clave(parte), parte.strip()))
        if revisada:
            resultado.append(revisada)
    return "\n".join(resultado)


_TITULOS = {
    "2 Drawer Freestanding Credenza": "Credenza independiente de 2 cajones",
    "2 Drawer Freestanding Credenza (low)": "Credenza baja independiente de 2 cajones",
    "3 Drawer Freestanding Credenza": "Credenza independiente de 3 cajones",
    "3 Drawer Lateral File": "Archivero lateral de 3 cajones",
    "Aluminum frame glass door": "Puerta de vidrio con marco de aluminio",
    "Bar Height Table": "Mesa alta",
    "Bar Height Table w/ CableManagement Box": "Mesa alta con caja pasacables",
    "Bookcase-3": "Librero de 3 niveles",
    "Bookcase-4": "Librero de 4 niveles",
    "Bookcase-5": "Librero de 5 niveles",
    "Continuation Storage Cabinets Frame": "Cuerpo de gabinete de ampliación",
    "Door-shape steel leg of single desk": "Pata de acero tipo marco para escritorio individual",
    "Double-Door Storage Cabinets Frame with Shelves": "Cuerpo de gabinete de dos puertas con estantes",
    "Double-Sided Angled Steel Leg": "Pata de acero inclinada para estación doble",
    "Double-Sided Extension Steel Leg": "Pata de extensión de acero para estación doble",
    "Double-Sided Post Steel Leg": "Pata de acero tipo poste para estación doble",
    "Extended lockers": "Extensión de lockers",
    "Four-Door Storage Cabinets Frame with Shelves": "Cuerpo de gabinete de cuatro puertas con estantes",
    "Freestanding Open Shelf Storage": "Mueble independiente de estantes abiertos",
    "Freestanding Pedestal F/F": "Archivero independiente F/F",
    "DUXHGB Flip-top cable box": "Caja pasacables abatible DUXHGB",
    "Laminate frame glass door": "Puerta de vidrio con marco de laminado",
    "Laminate lower door": "Puerta inferior de laminado",
    "Laminate upper door": "Puerta superior de laminado",
    "M cabinet Bar Height Table with Shelves": "Mesa alta con estantes M Cabinet",
    "M cabinet Bar-height table w/shelves": "Mesa alta con estantes M Cabinet",
    "Mall II Conference": "Mesa de conferencias Mall II",
    "Mall II Height-Adjustable": "Mesa de altura ajustable Mall II",
    "Mall II Private Offices": "Escritorio Mall II para oficina privada",
    "Manager Desk": "Escritorio gerencial",
    "Manager desk with left return": "Escritorio gerencial con extensión izquierda",
    "Manager desk with right return": "Escritorio gerencial con extensión derecha",
    "Mobile Pedestal B/B/F": "Archivero móvil B/B/F",
    "Mobile Pedestal B/F": "Archivero móvil B/F",
    "Modesty Panel": "Faldón",
    "Negotiation table cabinet": "Gabinete para mesa de reunión",
    "Occasional table": "Mesa auxiliar",
    "Wire Base Occasional Table": "Mesa auxiliar con base de varilla",
    "Storage Cabinets Frame with Shelves": "Cuerpo de gabinete con estantes",
    "Personal Table": "Mesa individual",
    "Privacy Screen Bracket": "Soporte para mampara de privacidad",
    "Reception Desk": "Mostrador de recepción",
    "Reception Desk Return": "Extensión para mostrador de recepción",
    "Rect. Soft Corner Occasional Table": "Mesa auxiliar rectangular de esquinas redondeadas",
    "Rectangle Wire Base Occasional Table": "Mesa auxiliar rectangular con base de varilla",
    "Return": "Extensión lateral",
    "Round Bar-Height Table": "Mesa alta redonda",
    "Round Cafe Table": "Mesa redonda de cafetería",
    "Round Occasional Table": "Mesa auxiliar redonda",
    "Round Wire Base Occasional Table": "Mesa auxiliar redonda con base de varilla",
    "SAMU Lounge Chairs Seating (Not for sale)": "Sillón de descanso SAMU (no disponible para venta)",
    "Single-Sided Extension Steel Leg": "Pata de extensión de acero para estación individual",
    "Single-Sided Post Steel Leg": "Pata de acero tipo poste para estación individual",
    "Six-Door Storage Cabinets Frame with Shelves": "Cuerpo de gabinete de seis puertas con estantes",
    "Slanting steel leg of single desk": "Pata de acero inclinada para escritorio individual",
    "Spine[floor]": "Organizador de cables tipo columna para piso",
    "Spine[hanging]": "Organizador de cables tipo columna colgante",
    "Square Occasional Table": "Mesa auxiliar cuadrada",
    "Staff Chair": "Silla operativa",
    "Storage Cabinet": "Gabinete de almacenamiento",
    "Storage Cabinet-Lateral File Combination Cabinets Frame": "Cuerpo de gabinete combinado con archivero lateral",
    "Storage Cabinets": "Gabinete de almacenamiento",
    "Storage Cabinets Frame": "Cuerpo de gabinete de almacenamiento",
    "Tea cabinet": "Gabinete de servicio",
    "Thin screen clip": "Clip para mampara delgada",
    "Tower (left-door)": "Gabinete tipo torre con puerta izquierda",
    "Tower (right-door)": "Gabinete tipo torre con puerta derecha",
    "Training Chair": "Silla de capacitación",
    "U shaped desk w/ 2 drawer credenza": "Escritorio en U con credenza de 2 cajones",
    "U shaped desk w/ 3 drawer credenza": "Escritorio en U con credenza de 3 cajones",
    "UP1 Height-Adjustable Table Base": "Base de mesa de altura ajustable UP1",
    "UP1 Height-Adjustable Table middle screen for dual workstation": "Mampara central para estación doble de altura ajustable UP1",
    "UP1 L-shape Height-Adjustable Table": "Escritorio en L de altura ajustable UP1",
    "Wall-Mounted Overhead Storage": "Gabinete superior de montaje en pared",
    "Wardrobe (left-door)": "Guardarropa con puerta izquierda",
    "Wardrobe (right door)": "Guardarropa con puerta derecha",
    "Wardrobe/Bookcase (left-door)": "Guardarropa con librero y puerta izquierda",
    "Wardrobe/Bookcase (right-door)": "Guardarropa con librero y puerta derecha",
    "Worksurface": "Cubierta de trabajo",
    "lockers": "Lockers",
    "mounted socket[America]": "Contacto eléctrico de montaje, estándar estadounidense",
    "resting top w/ surpporting credenza": "Cubierta con credenza de soporte",
    "wire management tray": "Charola pasacables",
}
_SUFIJOS = (
    ("Flip-top Training Table", "Mesa de capacitación abatible"),
    ("Private Offices Desk", "Escritorio para oficina privada"),
    ("Lounge Modular Seating", "Asiento modular de descanso"),
    ("Lounge Sofas Seating", "Sofá de descanso"),
    ("Lounge Chairs Seating", "Sillón de descanso"),
    ("Lounge Chairs", "Sillón de descanso"),
    ("Lounge Chair", "Sillón de descanso"),
    ("Occasional Tables", "Mesa auxiliar"),
    ("Occasional Table", "Mesa auxiliar"),
    ("Conference Table", "Mesa de conferencias"),
    ("Training Seating", "Asiento de capacitación"),
    ("Training Chair", "Silla de capacitación"),
    ("Stackable Chair", "Silla apilable"),
    ("Guest Chair", "Silla de visita"),
    ("Task Chair", "Silla operativa"),
    ("Multipurpose", "Mobiliario multiusos"),
    ("Divisions", "Divisiones"),
    ("Chair", "Silla"),
    ("Stool", "Banco"),
)


def titulo_sunon(texto: object) -> str:
    """Traduce el tipo de producto y conserva íntegro el nombre del modelo."""
    titulo = re.sub(r"\s+", " ", str(texto or "")).strip(" .")
    if titulo in _TITULOS:
        return _TITULOS[titulo]
    for sufijo, tipo in _SUFIJOS:
        marca = re.match(r"^(.*?)\s+" + re.escape(sufijo) + r"$", titulo, flags=re.I)
        if marca:
            return f"{tipo} {marca.group(1)}"
    return titulo


def reconciliar_ficha_sunon(item: dict) -> tuple[str, str, list[str]]:
    """Omite datos incompatibles en pares exactos de nombre y primera cláusula.

    Las formas de tubos, patas o bases no se comparan con la forma de la cubierta.
    Tampoco se deducen puertas, cajones o plazas desde el código del fabricante.
    """
    nombre = str(item.get("name") or "").strip()
    descripcion = str(item.get("description") or "").strip()
    conflictos = {
        "Rect. Soft Corner Occasional Table": (
            "Square coffee table", "Occasional table", "Coffee table",
            "geometria_contradictoria_entre_nombre_y_ficha_confirmar",
        ),
        "Rectangle Wire Base Occasional Table": (
            "Square coffee table", "Wire Base Occasional Table", "Coffee table",
            "geometria_contradictoria_entre_nombre_y_ficha_confirmar",
        ),
        "Double-Door Storage Cabinets Frame with Shelves": (
            "Four-door file cabinet body", "Storage Cabinets Frame with Shelves", "File cabinet body",
            "cantidad_de_puertas_contradictoria_entre_nombre_y_ficha_confirmar",
        ),
        "Four-Door Storage Cabinets Frame with Shelves": (
            "Eight-door file cabinet body", "Storage Cabinets Frame with Shelves", "File cabinet body",
            "cantidad_de_puertas_contradictoria_entre_nombre_y_ficha_confirmar",
        ),
        "Six-Door Storage Cabinets Frame with Shelves": (
            "Twelve-door file cabinet body", "Storage Cabinets Frame with Shelves", "File cabinet body",
            "cantidad_de_puertas_contradictoria_entre_nombre_y_ficha_confirmar",
        ),
    }
    conflicto = conflictos.get(nombre)
    if not conflicto:
        return nombre, descripcion, []
    fuente, titulo, neutro, pendiente = conflicto
    # Limitar a la primera característica completa: no alterar una exclusión o
    # una comparación con otro mueble añadida en una revisión futura de la ficha.
    patron = re.compile(r"(\b1\)\s*)" + re.escape(fuente) + r"\s*\.?\s*(?=2\))", re.I)
    if not patron.search(descripcion):
        return nombre, descripcion, []
    revisada = patron.sub(lambda match: match.group(1) + neutro + " ", descripcion, count=1)
    return titulo, revisada, [pendiente]
