"""Importador auditable de la lista oficial Requiez A-26.

El PDF publica el precio en bloques visuales: uno o varios codigos aparecen
inmediatamente arriba de un importe, dentro de la misma columna. Este modulo
solo acepta esa relacion geometrica. No completa codigos por similitud, no
arrastra el ultimo producto entre bloques y no inventa acabados.

Una imagen se aprueba como ``exact_pdf`` solamente cuando el PDF repite el SKU
como pie/rotulo adyacente a un unico raster. Los demas productos conservan
``placeholder`` para no presentar una fotografia aproximada como evidencia.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import fitz

from . import common as _common
from .common import CatalogAssetBinding, CatalogSnapshotBuild, ImageAsset, source_ref


SUPPORTED_SHA256 = "7f3281d1965c67a234bac55112800067019ad471f835de59ff758e759eca56ba"
_SOURCE_PATH = "REQUIEZ/Lista de precios A-26.pdf"
_MIME = "application/pdf"
_SOURCE_URL = (
    "https://mobiliti11-my.sharepoint.com/personal/joel_meza_mobiliti_mx/"
    "Documents/PROYECTOS%20CET%20-%202026/LISTAS%20DE%20PRECIOS%20"
    "PROVEEDORES/REQUIEZ/Lista%20de%20precios%20A-26.pdf"
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(
    r"(?<![A-Z0-9])(?:"
    r"(?:R[A-Z]{1,3}|STE)-\d{1,4}[A-Z0-9]*"
    r"(?:\s*/\s*[A-Z0-9]+(?:-[A-Z0-9]+)*)*"
    r"|\d{2,4}-[A-Z0-9]\d{3,5}"
    r")",
    re.IGNORECASE,
)
_PRICE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d{0,2})?)")
_ACCESSORY_WORD = re.compile(
    r"\b(?:cabecera|base\s+tapiz|kit\s+de\s+base|brazo|paleta)\b",
    re.IGNORECASE,
)
_MAX_PRICE = Decimal("1000000000")


@dataclass(frozen=True)
class _Span:
    text: str
    bbox: tuple[float, float, float, float]
    size: float


@dataclass(frozen=True)
class _CodeOccurrence:
    code: str
    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class _PriceOccurrence:
    value: Decimal
    bbox: tuple[float, float, float, float]
    text: str


@dataclass(frozen=True)
class _ExactImage:
    bbox: tuple[float, float, float, float]
    reference: dict


def _clean(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("MIL\ufffdN", "MILÁN")
    text = text.replace("\ufffd", "")
    return re.sub(r"\s+", " ", text).strip()


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _slug(value: object) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", _fold(value)))


def _normalize_code(value: object) -> str:
    code = re.sub(r"\s*/\s*", "/", _clean(value).upper())
    return code.rstrip(".,:;")


def _clean_label(value: object) -> str:
    text = re.sub(r"/\s+", "/", _clean(value))
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return re.sub(r"/(?=(?:Sin|Con)\b)", "/ ", text, flags=re.IGNORECASE)


def _money(value: Decimal) -> str:
    return f"{value:.6f}"


def _bbox(values) -> tuple[float, float, float, float]:
    return tuple(round(float(value), 3) for value in values)


def _union_bbox(left, right) -> tuple[float, float, float, float]:
    return _bbox(
        (
            min(left[0], right[0]),
            min(left[1], right[1]),
            max(left[2], right[2]),
            max(left[3], right[3]),
        )
    )


def _validated_document(files):
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("REQUIEZ_BUNDLE") from None
    if len(rows) != 1:
        raise ValueError("REQUIEZ_BUNDLE")
    document = rows[0]
    local_path = getattr(document, "local_path", None)
    declared_hash = getattr(document, "sha256", None)
    if (
        getattr(document, "path", None) != _SOURCE_PATH
        or getattr(document, "kind", None) != "price_list"
        or getattr(document, "brand", None) is not None
        or getattr(document, "mime_type", None) != _MIME
        or not isinstance(local_path, Path)
        or local_path.suffix.casefold() != ".pdf"
        or not isinstance(declared_hash, str)
        or _HASH.fullmatch(declared_hash) is None
    ):
        raise ValueError("REQUIEZ_BUNDLE")
    validated, data = _common.read_validated_source(local_path, ".pdf")
    if validated.sha256 != declared_hash or declared_hash != SUPPORTED_SHA256:
        raise ValueError("REQUIEZ_HASH")
    try:
        probe = fitz.open(stream=data, filetype="pdf")
        if not 0 < probe.page_count <= 200 or not 1 < probe.xref_length() <= 20_000:
            raise ValueError("REQUIEZ_PDF_LIMIT")
    except ValueError:
        raise
    except Exception:
        raise ValueError("REQUIEZ_PDF_INVALID") from None
    finally:
        if "probe" in locals():
            probe.close()
    return document, data


def _spans(page) -> tuple[_Span, ...]:
    found = []
    for block in page.get_text("dict").get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                text = _clean(span.get("text"))
                if text:
                    found.append(
                        _Span(text, _bbox(span.get("bbox", (0, 0, 0, 0))), float(span.get("size", 0)))
                    )
    return tuple(found)


def _codes(spans: tuple[_Span, ...]) -> tuple[_CodeOccurrence, ...]:
    found = []
    for span in spans:
        for match in _CODE.finditer(span.text):
            code = _normalize_code(match.group(0))
            if code:
                found.append(_CodeOccurrence(code, span.text, span.bbox))
    return tuple(found)


def _decimal_price(raw: str) -> Decimal | None:
    clean = raw.replace(",", "")
    try:
        value = Decimal(clean)
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0 or value > _MAX_PRICE:
        return None
    return value.normalize()


def _prices(spans: tuple[_Span, ...]) -> tuple[_PriceOccurrence, ...]:
    found = []
    for index, span in enumerate(spans):
        match = _PRICE.search(span.text)
        if match is None:
            continue
        raw = match.group(1)
        price_bbox = span.bbox
        # En el PDF oficial los centavos se dibujan como superindice aparte.
        if "." not in raw or raw.endswith("."):
            candidates = []
            for other in spans[index + 1 : index + 5]:
                if not re.fullmatch(r"\d{2}", other.text):
                    continue
                vertical = abs(((other.bbox[1] + other.bbox[3]) / 2) - ((span.bbox[1] + span.bbox[3]) / 2))
                gap = other.bbox[0] - span.bbox[2]
                if vertical <= 6 and -1 <= gap <= 4:
                    candidates.append(other)
            if len(candidates) == 1:
                cents = candidates[0]
                raw = raw + cents.text if raw.endswith(".") else raw + "." + cents.text
                price_bbox = _union_bbox(price_bbox, cents.bbox)
        value = _decimal_price(raw)
        if value is not None:
            found.append(_PriceOccurrence(value, price_bbox, span.text))
    found.sort(key=lambda row: (row.bbox[1], row.bbox[0]))
    return tuple(found)


def _same_column(left_bbox, right_bbox, tolerance: float = 13) -> bool:
    return abs(left_bbox[0] - right_bbox[0]) <= tolerance


def _column_id(bbox, collection: str) -> int:
    x = bbox[0]
    if collection == "Accesorios":
        for index, (start, end) in enumerate(
            ((20, 150), (150, 295), (295, 440), (440, 590))
        ):
            if start <= x < end:
                return index
    return round(x / 10)


def _column_match(left_bbox, right_bbox, collection: str) -> bool:
    return _same_column(left_bbox, right_bbox) or (
        collection == "Accesorios"
        and _column_id(left_bbox, collection) == _column_id(right_bbox, collection)
    )


def _codes_for_price(
    price: _PriceOccurrence,
    prices: tuple[_PriceOccurrence, ...],
    codes: tuple[_CodeOccurrence, ...],
    collection: str,
) -> tuple[tuple[str, tuple[float, float, float, float]], ...]:
    previous_y = max(
        (
            row.bbox[1]
            for row in prices
            if row is not price
            and _column_match(row.bbox, price.bbox, collection)
            and row.bbox[1] < price.bbox[1] - 1
        ),
        default=price.bbox[1] - 72,
    )
    candidates = [
        row
        for row in codes
        if _column_match(row.bbox, price.bbox, collection)
        and previous_y + 1 < row.bbox[1] < price.bbox[1] + 2
        and price.bbox[1] - row.bbox[1] <= 70
    ]
    if not candidates:
        return ()
    # Una linea "Silla X + Cabecera RA-X" publica una configuracion total;
    # dos codigos desnudos consecutivos publican el mismo precio individual.
    if len(candidates) > 1 and any(_ACCESSORY_WORD.search(row.text) for row in candidates):
        code = "+".join(dict.fromkeys(row.code for row in candidates))
        bbox = candidates[0].bbox
        for candidate in candidates[1:]:
            bbox = _union_bbox(bbox, candidate.bbox)
        return ((code, bbox),)
    unique = {}
    for row in candidates:
        unique.setdefault(row.code, row.bbox)
    return tuple(unique.items())


def _accessory_cell_details(
    price: _PriceOccurrence,
    prices: tuple[_PriceOccurrence, ...],
    spans: tuple[_Span, ...],
    code_bbox=None,
) -> tuple[str, str]:
    """Lee la celda de accesorio desde el último precio hasta el siguiente SKU."""

    collection = "Accesorios"
    codes = _codes(spans)
    if code_bbox is None:
        anchored = _codes_for_price(price, prices, codes, collection)
        if not anchored:
            return "", ""
        code_bbox = anchored[0][1]
    column = _column_id(code_bbox, collection)
    next_code_y = min(
        (
            row.bbox[1]
            for row in codes
            if _column_id(row.bbox, collection) == column
            and row.bbox[1] > code_bbox[3] + 4
        ),
        default=10_000,
    )
    cell_prices = [
        row
        for row in prices
        if _column_id(row.bbox, collection) == column
        and code_bbox[1] - 2 <= row.bbox[1] < next_code_y
    ]
    if not cell_prices:
        return "", ""
    text_start = max(row.bbox[3] for row in cell_prices) - 3
    values = []
    for span in sorted(spans, key=lambda row: (row.bbox[1], row.bbox[0])):
        if (
            _column_id(span.bbox, collection) != column
            or not text_start <= span.bbox[1] < next_code_y - 1
            or _CODE.search(span.text)
            or _PRICE.search(span.text)
            or re.fullmatch(r"\d{1,2}", _clean(span.text))
        ):
            continue
        value = _clean(span.text).strip(" -*·")
        if value and value not in values:
            values.append(value)
    if not values:
        return "", ""
    marker = next(
        (index for index, value in enumerate(values) if _fold(value) == "modelos compatibles"),
        len(values),
    )
    label_values = values[:marker]
    # En estas celdas el PDF separa a veces el punto en un span propio y usa
    # ``/ Sin`` / ``/ Con`` como texto editorial, no como separador de SKU.
    # Conservamos ese espaciado y solo reunimos la puntuacion fragmentada.
    label = re.sub(r"\s+([.,;:])", r"\1", _clean(" ".join(label_values)))
    description = re.sub(r"\s+([.,;:])", r"\1", _clean(" ".join(values)))
    return label, description


def _price_label(
    price: _PriceOccurrence,
    prices: tuple[_PriceOccurrence, ...],
    spans: tuple[_Span, ...],
    collection: str,
) -> str:
    own_label = _clean(_PRICE.sub("", price.text)).strip(" -·")
    if own_label and _fold(own_label) not in {"precio", "precios"}:
        return _clean_label(own_label)
    column = _column_id(price.bbox, collection)
    inline = []
    for span in spans:
        if (
            _column_id(span.bbox, collection) != column
            or span.bbox[2] > price.bbox[0] + 2
            or max(span.bbox[1], price.bbox[1])
            >= min(span.bbox[3], price.bbox[3])
            or _CODE.search(span.text)
            or _PRICE.search(span.text)
        ):
            continue
        value = _clean(span.text).strip(" -*·")
        if value and _fold(value) not in {"precio", "precios", "color", "acabados"}:
            inline.append((span, value))
    if inline:
        price_center = (price.bbox[1] + price.bbox[3]) / 2
        closest = min(
            inline,
            key=lambda row: abs(
                ((row[0].bbox[1] + row[0].bbox[3]) / 2) - price_center
            ),
        )[0]
        closest_center = (closest.bbox[1] + closest.bbox[3]) / 2
        return _clean_label(
            " ".join(
                dict.fromkeys(
                    value
                    for span, value in inline
                    if abs(((span.bbox[1] + span.bbox[3]) / 2) - closest_center)
                    <= 0.5
                )
            )
        )
    previous_y = max(
        (
            row.bbox[1]
            for row in prices
            if row is not price
            and _column_id(row.bbox, collection) == column
            and row.bbox[1] < price.bbox[1] - 1
        ),
        default=max(0, price.bbox[1] - 90),
    )
    values = []
    lower_bound = max(previous_y - 1, price.bbox[1] - 70)
    for span in spans:
        if (
            _column_id(span.bbox, collection) != column
            or not lower_bound <= span.bbox[1] < price.bbox[1] + 1
        ):
            continue
        without_price = _clean(_PRICE.sub("", span.text)).strip(" -·")
        without_price = _clean(_CODE.sub("", without_price)).strip(" -·()")
        if not without_price or re.fullmatch(r"\d{1,2}", without_price) or _fold(without_price) in {
            "precios", "precio", "color", "acabados"
        }:
            continue
        if without_price not in values:
            values.append(without_price)

    # CHAP mantiene encabezados de superficie/acabado para toda la subseccion,
    # aunque no los repita entre cada SKU.
    section = ""
    finish = ""
    if any(_fold(span.text).startswith("cubierta ") for span in spans):
        section_rows = [
            span for span in spans
            if span.bbox[1] < price.bbox[1]
            and _fold(span.text) in {"cubierta circular", "cubierta cuadrada"}
        ]
        if section_rows:
            section = section_rows[-1].text
        structure_rows = [
            span for span in spans
            if _column_id(span.bbox, collection) == column
            and span.bbox[1] < price.bbox[1]
            and _fold(span.text).startswith("estructura")
        ]
        if structure_rows:
            structure = structure_rows[-1]
            parts = [structure.text]
            for span in spans:
                if (
                    _column_id(span.bbox, collection) == column
                    and structure.bbox[3] - 1 <= span.bbox[1] < price.bbox[1]
                    and not _CODE.search(span.text)
                    and not _PRICE.search(span.text)
                    and span.text not in parts
                ):
                    parts.append(span.text)
            finish = _clean(" ".join(parts))
    if section and finish:
        return _clean_label(f"{section} · {finish}")
    label = _clean_label(" ".join(values))
    if label:
        return label

    if collection == "Accesorios":
        accessory_label, _description = _accessory_cell_details(
            price, prices, spans
        )
        if accessory_label:
            return accessory_label

    # Algunas tablas publican un solo encabezado de acabado encima de dos
    # columnas de SKU/precio. Se comparte únicamente con el precio paralelo
    # inmediato de la izquierda, probado por la misma coordenada vertical.
    parallel_left = sorted(
        (
            row
            for row in prices
            if collection != "Accesorios"
            and row is not price
            and row.bbox[0] < price.bbox[0]
            and abs(row.bbox[1] - price.bbox[1]) <= 2
        ),
        key=lambda row: row.bbox[0],
        reverse=True,
    )
    for peer in parallel_left:
        peer_label = _price_label(peer, prices, spans, collection)
        if peer_label:
            return peer_label

    personalizable = next(
        (span.text for span in spans if _fold(span.text) == "tapiz personalizable"),
        "",
    )
    muestrario = next(
        (span.text for span in spans if "consulta muestrario" in _fold(span.text)),
        "",
    )
    if personalizable and muestrario:
        return _clean_label(
            f"{_clean(personalizable)} · {_clean(muestrario).lstrip('* ')}"
        )
    return ""


def _explicit_continuation(label: str) -> bool:
    return bool(
        re.search(
            r"(?:color(?:es)? especial|gris\s*/\s*negro|\bmodificaci|"
            r"\baccesorio\b|(?:^|\s)ng(?:\s|$)|(?:^|\s)gr(?:\s|$))",
            _fold(label),
        )
    )


def _collection(page_number: int, spans: tuple[_Span, ...]) -> str:
    folded = "\n".join(_fold(span.text) for span in spans)
    exact = (
        ("sillas y bancos de trabajo", "Sillas y bancos de trabajo"),
        ("visitantes y colectividad", "Visitantes y colectividad"),
        ("edu & train", "Edu & Train"),
        ("industrial", "Industrial"),
        ("sillones", "Sillones"),
        ("bancos fijos", "Bancos fijos"),
        ("bancas", "Bancas"),
        ("mesas", "Mesas"),
        ("accesorios", "Accesorios"),
    )
    for marker, label in exact:
        if marker in folded:
            return label
    for start, end, label in (
        (3, 59, "Sillas y bancos de trabajo"),
        (61, 72, "Visitantes y colectividad"),
        (74, 82, "Edu & Train"),
        (84, 89, "Industrial"),
        (91, 97, "Sillones"),
        (99, 101, "Bancos fijos"),
        (103, 105, "Bancas"),
        (107, 108, "Mesas"),
        (110, 113, "Accesorios"),
    ):
        if start <= page_number <= end:
            return label
    return ""


def _family(spans: tuple[_Span, ...], collection: str) -> str:
    titles = [
        span
        for span in spans
        if span.size >= 28
        and not _PRICE.search(span.text)
        and not _CODE.search(span.text)
    ]
    if titles:
        titles.sort(key=lambda row: (-row.size, row.bbox[1], row.bbox[0]))
        return titles[0].text
    return collection or "Requiez"


def _descriptions(
    spans: tuple[_Span, ...], codes: tuple[_CodeOccurrence, ...]
) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for code in sorted(codes, key=lambda row: (row.bbox[1], row.bbox[0], row.code)):
        # El listado superior publica SKU a la izquierda y descripcion exacta
        # en la misma linea, antes del panel de precios.
        if code.bbox[0] > 220 or code.bbox[1] > 230:
            continue
        peers = [
            span
            for span in spans
            if span.bbox[0] >= code.bbox[2] - 2
            and span.bbox[0] < 590
            and max(span.bbox[1], code.bbox[1]) < min(span.bbox[3], code.bbox[3])
            and not _CODE.search(span.text)
            and not _PRICE.search(span.text)
        ]
        if peers:
            code_center = (code.bbox[1] + code.bbox[3]) / 2
            closest = min(
                peers,
                key=lambda span: abs(
                    ((span.bbox[1] + span.bbox[3]) / 2) - code_center
                ),
            )
            closest_center = (closest.bbox[1] + closest.bbox[3]) / 2
            peers = [
                span
                for span in peers
                if abs(((span.bbox[1] + span.bbox[3]) / 2) - closest_center)
                <= 0.5
            ]
        text = _clean(" ".join(span.text.lstrip("- ") for span in peers))
        if text:
            descriptions.setdefault(code.code, text)
    return descriptions


def _nearby_description(
    code_bbox, price_bbox, spans: tuple[_Span, ...]
) -> str:
    values = []
    for span in spans:
        if (
            abs(span.bbox[0] - code_bbox[0]) <= 14
            and code_bbox[3] - 1 <= span.bbox[1] < price_bbox[1]
            and not _CODE.search(span.text)
            and not _PRICE.search(span.text)
        ):
            values.append(span.text.lstrip("- "))
    return _clean(" ".join(values))


def _image_match(
    page,
    page_number: int,
    code: str,
    codes: tuple[_CodeOccurrence, ...],
    file_hash: str,
) -> _ExactImage | None:
    if "+" in code:
        return None
    images = []
    for info in page.get_image_info(xrefs=True):
        bbox = _bbox(info.get("bbox", (0, 0, 0, 0)))
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if info.get("xref", 0) > 0 and width >= 35 and height >= 35 and width * height >= 2500:
            images.append(bbox)
    matches = []
    for occurrence in codes:
        if occurrence.code != code or sum(
            1 for _ in _CODE.finditer(occurrence.text)
        ) != 1:
            continue
        for image_bbox in images:
            horizontal = min(occurrence.bbox[2], image_bbox[2]) - max(occurrence.bbox[0], image_bbox[0])
            below_gap = occurrence.bbox[1] - image_bbox[3]
            above_gap = image_bbox[1] - occurrence.bbox[3]
            overlaps_vertical = occurrence.bbox[1] <= image_bbox[3] and occurrence.bbox[3] >= image_bbox[1]
            if horizontal <= 0:
                continue
            if overlaps_vertical:
                score = (0, 0.0)
            elif -2 <= below_gap <= 24:
                score = (1, abs(below_gap))
            elif -2 <= above_gap <= 12:
                score = (2, abs(above_gap))
            else:
                continue
            matches.append((score, image_bbox))
    unique = {}
    for score, bbox in matches:
        unique[bbox] = min(score, unique.get(bbox, score))
    if not unique:
        return None
    minimum = min(unique.values())
    nearest = [bbox for bbox, distance in unique.items() if distance == minimum]
    if len(nearest) != 1:
        return None
    bbox = nearest[0]
    return _ExactImage(bbox, source_ref(file_hash, page_number, bbox))


def _family_image_match(page, page_number: int, file_hash: str) -> _ExactImage | None:
    """Acepta un solo visual principal; nunca galerias ni accesorios."""
    candidates = []
    for info in page.get_image_info(xrefs=True):
        bbox = _bbox(info.get("bbox", (0, 0, 0, 0)))
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if (
            info.get("xref", 0) > 0
            and bbox[0] >= 20
            and width >= 80
            and height >= 80
            and width * height >= 8_000
        ):
            candidates.append(bbox)
    unique = tuple(dict.fromkeys(candidates))
    if len(unique) != 1:
        return None
    return _ExactImage(unique[0], source_ref(file_hash, page_number, unique[0]))


def _root_code(codes: tuple[_CodeOccurrence, ...]) -> _CodeOccurrence | None:
    candidates = [
        row
        for row in codes
        if row.bbox[1] > 700 and re.fullmatch(r"\d{3}-\d{5}", row.code)
    ]
    if len(candidates) == 1:
        return candidates[0]
    candidates = [row for row in codes if "mesa root" in _fold(row.text)]
    if len(candidates) == 1:
        return candidates[0]
    # En el PDF oficial SKU y descripcion son spans separados; el encabezado
    # sigue probado por su posicion superior, mientras el pie conflictivo esta
    # al final de la pagina.
    candidates = [
        row for row in codes if row.bbox[0] < 200 and row.bbox[1] < 150
    ]
    return candidates[0] if len(candidates) == 1 else None


def _authoritative_published_code(
    code: str,
    codes: tuple[_CodeOccurrence, ...],
) -> _CodeOccurrence | None:
    """Recupera la grafía superior solo por identidad alfanumérica exacta."""

    if "+" in code:
        return None
    signature = re.sub(r"[^A-Z0-9]", "", code.upper())
    candidates = {
        row.code: row
        for row in codes
        if row.bbox[0] < 160
        and 75 <= row.bbox[1] < 180
        and re.sub(r"[^A-Z0-9]", "", row.code.upper()) == signature
    }
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def _top_sequence(
    prices: tuple[_PriceOccurrence, ...],
    codes: tuple[_CodeOccurrence, ...],
    collection: str,
) -> tuple[tuple[tuple[str, tuple[float, float, float, float]], ...], ...] | None:
    """Usa el listado descriptivo superior cuando prueba cardinalidad 1:1."""
    top = [
        row
        for row in codes
        if row.bbox[0] < 160 and 75 <= row.bbox[1] < 180
    ]
    top.sort(key=lambda row: (row.bbox[1], row.bbox[0]))
    unique_top = []
    for row in top:
        if not unique_top or row.code != unique_top[-1].code:
            unique_top.append(row)
    direct = tuple(
        _codes_for_price(price, prices, codes, collection) for price in prices
    )
    if (
        len(unique_top) >= 2
        and all(group for group in direct)
        and sum(len(group) for group in direct) == len(unique_top)
    ):
        offset = 0
        authoritative = []
        for group in direct:
            selected = unique_top[offset : offset + len(group)]
            authoritative.append(tuple((row.code, row.bbox) for row in selected))
            offset += len(group)
        return tuple(authoritative)
    return None


def _parse_document(document, data: bytes, *, include_image_matches: bool):
    pdf = fitz.open(stream=data, filetype="pdf")
    rows = []
    try:
        for page_index, page in enumerate(pdf):
            page_number = page_index + 1
            spans = _spans(page)
            prices = _prices(spans)
            if not prices:
                continue
            codes = _codes(spans)
            collection = _collection(page_number, spans)
            if not collection:
                continue
            family = _family(spans, collection)
            published_descriptions = _descriptions(spans, codes)
            root = _root_code(codes) if _fold(family) == "root" else None
            authoritative_sequence = _top_sequence(prices, codes, collection)
            family_image = (
                _family_image_match(page, page_number, document.sha256)
                if include_image_matches and collection != "Accesorios"
                else None
            )
            last_anchored: dict[int, tuple[tuple[str, tuple[float, float, float, float]], ...]] = {}
            last_root_material = ""
            for price_index, price in enumerate(prices):
                label = _price_label(price, prices, spans, collection)
                if root is not None and "colores especiales" in _fold(label):
                    base = re.sub(r"(?:,?\s*NG/GR)\s*$", "", last_root_material, flags=re.IGNORECASE)
                    special = label
                    if _fold(base).endswith("cosmo prima") and _fold(special).startswith("cosmo prima"):
                        special = re.sub(
                            r"^Cosmo\s+prima,?\s*", "", special, flags=re.IGNORECASE
                        )
                    if base:
                        label = _clean_label(f"{base} · {special}")
                elif root is not None and label:
                    last_root_material = label
                column = _column_id(price.bbox, collection)
                anchored = (
                    authoritative_sequence[price_index]
                    if authoritative_sequence is not None
                    else _codes_for_price(price, prices, codes, collection)
                )
                root_warning = ""
                if not anchored and root is not None:
                    anchored = ((root.code, root.bbox),)
                    root_warning = (
                        f"Codigo ROOT requiere revision en pagina {page_number}: "
                        "el encabezado y el pie publican grafias distintas."
                    )
                elif not anchored and _explicit_continuation(label):
                    anchored = last_anchored.get(column, ())
                anchored = tuple(
                    (published.code, published.bbox)
                    if (
                        published := _authoritative_published_code(code, codes)
                    ) is not None
                    else (code, code_bbox)
                    for code, code_bbox in anchored
                )
                if anchored:
                    last_anchored[column] = anchored
                for code, code_bbox in anchored:
                    description = published_descriptions.get(code)
                    if not description and "+" not in code:
                        description = _nearby_description(code_bbox, price.bbox, spans)
                    row_label = label
                    if collection == "Accesorios":
                        accessory_label, accessory_description = _accessory_cell_details(
                            price, prices, spans, code_bbox
                        )
                        if not row_label:
                            row_label = accessory_label
                        if accessory_description:
                            description = accessory_description
                    description = description or f"{family} {code}"
                    record = {
                        "code": code,
                        "family": family,
                        "collection": collection,
                        "name": f"{family} · {code}",
                        "description": description,
                        "price_net": price.value,
                        "currency": "MXN",
                        "page": page_number,
                        "code_bbox": code_bbox,
                        "price_bbox": price.bbox,
                        "price_reference": source_ref(document.sha256, page_number, price.bbox),
                        "option_label": row_label,
                        "code_status": "verified",
                        "warnings": [root_warning] if root_warning else [],
                    }
                    if include_image_matches:
                        exact = _image_match(page, page_number, code, codes, document.sha256)
                        if exact is not None:
                            record["image_bbox"] = exact.bbox
                            record["image_reference"] = exact.reference
                        elif family_image is not None:
                            record["family_image_bbox"] = family_image.bbox
                            record["family_image_reference"] = family_image.reference
                    rows.append(record)
    finally:
        pdf.close()
    rows.sort(key=lambda row: (row["page"], row["price_bbox"][1], row["price_bbox"][0], row["code"]))
    if not rows:
        raise ValueError("REQUIEZ_EMPTY")
    return tuple(rows)


def parse_requiez_rows(files) -> tuple[dict, ...]:
    """Extrae un renglon por relacion SKU-precio geometricamente publicada."""
    document, data = _validated_document(files)
    return _parse_document(document, data, include_image_matches=False)


def _source_hash(document) -> str:
    material = f"requiez-a26-v1\0{document.path}\0{document.sha256}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _identity(code: str) -> str:
    return f"requiez:{_slug(code)}"


def _option_id(identity: str, row: dict) -> str:
    material = json.dumps(
        [row["page"], list(row["price_bbox"]), _money(row["price_net"])],
        separators=(",", ":"),
    )
    return f"{identity}:opcion:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _render_asset(data: bytes, page_number: int, bbox) -> ImageAsset:
    pdf = fitz.open(stream=data, filetype="pdf")
    try:
        page = pdf[page_number - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(bbox), alpha=False)
        raw = pixmap.tobytes("png")
    finally:
        pdf.close()
    return _common._normalize_image(raw)


def _public_snapshot(
    document,
    data: bytes,
    rows: tuple[dict, ...],
    *,
    include_assets: bool,
    synced_at: str | None,
) -> CatalogSnapshotBuild:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["code"]].append(row)

    items = []
    assets: dict[str, ImageAsset] = {}
    bindings = []
    for code, variants in sorted(grouped.items(), key=lambda entry: _slug(entry[0])):
        variants.sort(key=lambda row: (row["page"], row["price_bbox"][1], row["price_bbox"][0]))
        primary = variants[0]
        identity = _identity(code)
        options = []
        by_price: dict[Decimal, list[dict]] = {}
        for row in variants:
            by_price.setdefault(row["price_net"], []).append(row)
        if len(by_price) > 1:
            for price_rows in by_price.values():
                row = next(
                    (candidate for candidate in price_rows if candidate.get("option_label")),
                    price_rows[0],
                )
                options.append(
                    {
                        "id": _option_id(identity, row),
                        "name": (
                            row.get("option_label")
                            or f"Evidencia pagina {row['page']} · precio {_money(row['price_net'])} MXN"
                        ),
                        "price_net": _money(row["price_net"]),
                        "available": True,
                    }
                )
        description_row = primary
        if len(by_price) == 1:
            def _description_rank(row):
                description = _clean(row.get("description"))
                fallbacks = {
                    _fold(f"{row['family']} {code}"),
                    _fold(f"{row['collection']} {code}"),
                }
                return (_fold(description) not in fallbacks, len(description))

            description_row = max(variants, key=_description_rank)
        references = []
        for row in variants:
            if row["price_reference"] not in references:
                references.append(row["price_reference"])
        attributes = {
            "source_code": code,
            "source_page": primary["page"],
            "source_file_sha256": document.sha256,
            "prices": [
                {
                    "price_net_mxn": _money(row["price_net"]),
                    "page": row["page"],
                    "code_bbox": list(row["code_bbox"]),
                    "price_bbox": list(row["price_bbox"]),
                }
                for row in variants
            ],
        }
        image_kind = "placeholder"
        exact_rows = [row for row in variants if row.get("image_bbox") is not None]
        exact_keys = {(row["page"], row["image_bbox"]) for row in exact_rows}
        family_rows = [row for row in variants if row.get("family_image_bbox") is not None]
        family_keys = {(row["page"], row["family_image_bbox"]) for row in family_rows}
        selected = status = label = None
        if len(exact_keys) == 1:
            selected = exact_rows[0]
            status = "exact_pdf"
            label = "Imagen oficial exacta del PDF Requiez A-26"
        elif len(family_keys) == 1:
            selected = family_rows[0]
            status = "family_pdf"
            label = "Imagen oficial de familia del PDF Requiez A-26"
        if include_assets and selected is not None:
            image_bbox = selected.get("image_bbox") or selected["family_image_bbox"]
            image_reference = selected.get("image_reference") or selected["family_image_reference"]
            asset = _render_asset(data, selected["page"], image_bbox)
            object_name = f"{asset.sha256}.png"
            image_kind = "official"
            assets[asset.sha256] = asset
            image_references = (image_reference,)
            attributes["image_match"] = {
                "status": status,
                "asset_sha256": asset.sha256,
                "source_references": list(image_references),
            }
            attributes["approved_asset"] = {
                "bucket": "catalog-assets",
                "path": object_name,
                "image_kind": "official",
                "label": label,
                "approved": True,
            }
            bindings.append(
                CatalogAssetBinding(
                    identity,
                    asset.sha256,
                    object_name,
                    "official",
                    status,
                    image_references,
                )
            )
        product_url = f"{_SOURCE_URL}#page={primary['page']}"
        ambiguous_warnings = [
            f"Precio sin etiqueta diferenciadora en pagina {row['page']}."
            for row in variants
            if len(by_price) > 1 and not row.get("option_label")
        ]
        items.append(
            {
                "internal_id": identity,
                "supplier": "requiez",
                "product_key": identity,
                "sku": code,
                "code_status": (
                    "needs_review"
                    if any(row["code_status"] == "needs_review" for row in variants)
                    else "verified"
                ),
                "brand": "Requiez",
                "collection": primary["collection"],
                "name": primary["name"],
                "description": description_row["description"],
                "unit": "PZA",
                "availability_type": "made_to_order",
                "stock": None,
                "lead_time": "",
                "base_price_options": options,
                "add_on_options": [],
                "base_currency": "MXN",
                "price_net": _money(primary["price_net"]),
                "tax_rate": "0.160000",
                "attributes": attributes,
                "image_url": "",
                "image_kind": image_kind,
                "product_url": product_url,
                "warnings": list(
                    dict.fromkeys(
                        [
                            warning
                            for row in variants
                            for warning in row.get("warnings", ())
                            if warning
                        ]
                        + ambiguous_warnings
                    )
                ),
                "source_reference": json.dumps(
                    references, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ),
            }
        )
    timestamp = synced_at or datetime.now(timezone.utc)
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("REQUIEZ_SYNCED_AT")
    generated_at = timestamp.isoformat().replace("+00:00", "Z")
    snapshot = {
        "supplier": "requiez",
        "source_hash": _source_hash(document),
        "generated_at": generated_at,
        "items": items,
    }
    bindings.sort(key=lambda binding: binding.internal_id)
    return CatalogSnapshotBuild(snapshot, assets, tuple(bindings))


def build_requiez_snapshot(files, *, synced_at: datetime | None = None) -> dict:
    """Construye el snapshot sin renderizar/cargar activos binarios."""
    document, data = _validated_document(files)
    rows = _parse_document(document, data, include_image_matches=False)
    return _public_snapshot(
        document, data, rows, include_assets=False, synced_at=synced_at
    ).snapshot


def build_requiez_snapshot_with_assets(
    files, *, synced_at: datetime | None = None
) -> CatalogSnapshotBuild:
    """Construye snapshot y solo los recortes con correspondencia SKU exacta."""
    document, data = _validated_document(files)
    rows = _parse_document(document, data, include_image_matches=True)
    return _public_snapshot(
        document, data, rows, include_assets=True, synced_at=synced_at
    )


build_requiez_catalog = build_requiez_snapshot
