"""Importador determinista de la lista oficial de precios Labenze B26.

El PDF tiene una retícula editorial estable: cada producto comienza con un
título a 13 pt y termina antes del siguiente título. Los códigos y precios se
vinculan únicamente dentro de ese rectángulo; nunca se arrastran entre fichas.
Los recortes publicados salen de la misma ficha PDF y conservan página/bbox.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

import fitz
from PIL import Image, ImageChops

from mobiliti_saas.quote_engine.supplier_catalog import PUBLIC_ITEM_FIELDS, load_supplier_catalog_data

from .common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    ImageAsset,
    _normalize_image,
    read_validated_source,
    source_ref,
)


_MIME = "application/pdf"
_FILENAME = "LP Labenze B26.pdf"
_LOGICAL_PATH = f"LABENZE/{_FILENAME}"
SUPPORTED_SHA256 = frozenset(
    {"c4fc2d2152b5e854f7c36c9106c71cd21853abb50efcde96ba2566cb72f1d6f3"}
)
_PDF_URL = (
    "https://mobiliti11-my.sharepoint.com/personal/joel_meza_mobiliti_mx/"
    "Documents/PROYECTOS%20CET%20-%202026/LISTAS%20DE%20PRECIOS%20"
    "PROVEEDORES/LABENZE/LP%20Labenze%20B26.pdf"
)
_CODE = re.compile(r"(?<![A-Z0-9])\d{2,4}-[A-Z0-9](?:[A-Z0-9./-]*[A-Z0-9])?", re.I)
_PRICE = re.compile(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)")
_TITLE_MIN_SIZE = 12.5
_TITLE_MAX_SIZE = 13.5
_PRODUCT_FIRST_PAGE = 4
_PRODUCT_LAST_PAGE = 48
_PAGE_BOTTOM = 676.0


@dataclass(frozen=True)
class _Line:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold: bool

    @property
    def x(self) -> float:
        return self.bbox[0]

    @property
    def y(self) -> float:
        return self.bbox[1]


@dataclass(frozen=True)
class _Code:
    value: str
    label: str
    line: _Line


@dataclass(frozen=True)
class _PriceGroup:
    y: float
    values: tuple[Decimal, ...]
    label: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class _Section:
    page_number: int
    title: str
    start: float
    end: float
    lines: tuple[_Line, ...]


@dataclass(frozen=True)
class _Record:
    page_number: int
    title: str
    collection: str
    code: str
    variant: str
    description: str
    direct_price: Decimal
    options: tuple[tuple[str, Decimal], ...]
    code_bbox: tuple[float, float, float, float]
    price_bboxes: tuple[tuple[float, float, float, float], ...]
    crop_bbox: tuple[float, float, float, float]
    image_match_status: str


def _clean(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    replacements = {
        "C�digo": "Código",
        "Descripci�n": "Descripción",
        "cat�logo": "catálogo",
        "Sill�n": "Sillón",
        "SILL�N": "SILLÓN",
        "Sof�": "Sofá",
        "SOF�": "SOFÁ",
        "Metr�polis": "Metrópolis",
        "METR�POLIS": "METRÓPOLIS",
        "ANDR�": "ANDRÉ",
        "uni�n": "unión",
        "opci�n": "opción",
        "est�": "está",
        "pol�mero": "polímero",
        "met�lica": "metálica",
        "tecnopol�mero": "tecnopolímero",
        "inyecci�n": "inyección",
        "hidr�fugo": "hidrófugo",
        "melam�nica": "melamínica",
        "dise�o": "diseño",
        "poli�ster": "poliéster",
        "cat�logo": "catálogo",
    }
    for broken, repaired in replacements.items():
        text = text.replace(broken, repaired)
    return text


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(value)).strip("-") or "sin-dato"


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001')):f}"


def _amount(value: str) -> Decimal:
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise ValueError("LABENZE_PRICE") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("LABENZE_PRICE")
    return amount


def _validated_document(files: object) -> tuple[object, bytes]:
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("LABENZE_BUNDLE") from None
    if len(rows) != 1:
        raise ValueError("LABENZE_BUNDLE")
    document = rows[0]
    path = getattr(document, "path", None)
    local_path = getattr(document, "local_path", None)
    declared_hash = getattr(document, "sha256", None)
    if (
        not isinstance(path, str)
        or path != _LOGICAL_PATH
        or getattr(document, "kind", None) != "price_list"
        or getattr(document, "mime_type", None) != _MIME
        or not isinstance(local_path, Path)
        or local_path.suffix.casefold() != ".pdf"
        or not isinstance(declared_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
    ):
        raise ValueError("LABENZE_BUNDLE")
    validated, data = read_validated_source(local_path, ".pdf")
    if validated.sha256 != declared_hash:
        raise ValueError("LABENZE_HASH")
    if validated.sha256 not in SUPPORTED_SHA256:
        raise ValueError("LABENZE_UNSUPPORTED_HASH")
    return document, data


def _page_lines(page: fitz.Page) -> tuple[_Line, ...]:
    result: list[_Line] = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if block.get("type") != 0:
            continue
        for raw_line in block.get("lines", ()):  # pragma: no branch - forma de PyMuPDF
            spans = raw_line.get("spans", ())
            text = _clean(" ".join(span.get("text", "") for span in spans))
            if not text:
                continue
            bbox = tuple(round(float(value), 3) for value in raw_line["bbox"])
            result.append(
                _Line(
                    text=text,
                    bbox=bbox,
                    size=max(float(span.get("size", 0)) for span in spans),
                    bold=any("bold" in str(span.get("font", "")).casefold() for span in spans),
                )
            )
    return tuple(sorted(result, key=lambda line: (line.y, line.x, line.text)))


def _sections(page: fitz.Page, page_number: int) -> tuple[_Section, ...]:
    lines = _page_lines(page)
    titles = [
        line
        for line in lines
        if line.bold and _TITLE_MIN_SIZE <= line.size <= _TITLE_MAX_SIZE and line.x >= 145
    ]
    result = []
    for index, title in enumerate(titles):
        end = titles[index + 1].y - 0.5 if index + 1 < len(titles) else _PAGE_BOTTOM
        scoped = tuple(line for line in lines if title.y - 0.5 <= line.y < end)
        result.append(_Section(page_number, title.text, title.y, end, scoped))
    return tuple(result)


def _codes(section: _Section) -> tuple[_Code, ...]:
    result: list[_Code] = []
    for line in section.lines:
        if line.x < 325:
            continue
        for match in _CODE.finditer(line.text.upper()):
            code = match.group(0).strip("./-")
            label = _clean((line.text[: match.start()] + " " + line.text[match.end() :]).strip(" -:/"))
            result.append(_Code(code, label, line))
    return tuple(sorted(result, key=lambda row: (row.line.y, row.line.x, row.value)))


def _price_groups(section: _Section) -> tuple[_PriceGroup, ...]:
    raw: list[tuple[float, float, Decimal, tuple[float, float, float, float]]] = []
    for line in section.lines:
        if line.x < 400:
            continue
        for position, match in enumerate(_PRICE.finditer(line.text)):
            raw.append((line.y, line.x + position / 100, _amount(match.group(1)), line.bbox))
    grouped: list[list[tuple[float, float, Decimal, tuple[float, float, float, float]]]] = []
    for record in sorted(raw):
        if not grouped or abs(grouped[-1][0][0] - record[0]) > 2.0:
            grouped.append([record])
        else:
            grouped[-1].append(record)
    result = []
    for group in grouped:
        y = group[0][0]
        label_candidates = [
            line
            for line in section.lines
            if line.x >= 395
            and "$" not in line.text
            and 0 < abs(y - line.y) <= 18
            and _fold(line.text) != "precio"
        ]
        label = min(label_candidates, key=lambda line: abs(y - line.y)).text if label_candidates else ""
        bboxes = [row[3] for row in group]
        bbox = (
            round(min(row[0] for row in bboxes), 3),
            round(min(row[1] for row in bboxes), 3),
            round(max(row[2] for row in bboxes), 3),
            round(max(row[3] for row in bboxes), 3),
        )
        result.append(
            _PriceGroup(
                y,
                tuple(value for _, _, value, _ in sorted(group, key=lambda row: row[1])),
                label,
                bbox,
            )
        )
    return tuple(result)


def _description(section: _Section) -> str:
    ignored = {
        "base", "cuerpo", "codigo", "precio", "descripcion", "cubierta",
        "asiento", "elementos", "grado 0", "grado a", "uso interno",
        "uso interno y externo",
    }
    pieces = []
    for line in section.lines:
        folded = _fold(line.text)
        if (
            line is section.lines[0]
            or line.x < 45
            or line.x >= 335
            or folded in ignored
            or _CODE.search(line.text.upper())
            or _PRICE.search(line.text)
            or re.fullmatch(r"[A-Z]{2,4}(?:\s+[A-Z]{2,4})*", line.text)
        ):
            continue
        pieces.append(line.text)
    return _clean(" ".join(dict.fromkeys(pieces)))[:4000]


def _configuration_label(section: _Section, code: _Code, group: _PriceGroup) -> str:
    """Obtiene sólo una etiqueta explícita de configuración de la misma fila."""

    if code.label:
        return code.label
    candidates = [
        line
        for line in section.lines
        if 325 <= line.x < 430
        and code.line.y - 1 <= line.y <= group.y + 3
        and _fold(line.text).startswith("base")
        and _CODE.search(line.text.upper()) is None
        and _PRICE.search(line.text) is None
    ]
    return min(candidates, key=lambda line: abs(group.y - line.y)).text if candidates else ""


def _collection(page_number: int) -> str:
    if page_number <= 22:
        return "Sillas"
    if page_number <= 32:
        return "Sillones y línea confort"
    if page_number <= 38:
        return "Bancos"
    if page_number <= 41:
        return "Bancas"
    return "Mesas, bases y cubiertas"


def _image_blocks(page: fitz.Page, section: _Section) -> tuple[fitz.Rect, ...]:
    result = []
    for block in page.get_text("dict")["blocks"]:
        if (
            block.get("type") == 1
            and block.get("width", 0) >= 60
            and block.get("height", 0) >= 60
            and block["bbox"][0] < 160
        ):
            rect = fitz.Rect(block["bbox"])
            if rect.y1 > section.start and rect.y0 < section.end:
                result.append(rect)
    return tuple(sorted(result, key=lambda rect: (rect.y0, rect.x0)))


def _clip_for_record(
    page: fitz.Page,
    section: _Section,
    code: _Code,
    price_group: _PriceGroup,
) -> tuple[float, float, float, float]:
    blocks = _image_blocks(page, section)
    if len(blocks) > 4:
        target = price_group.y
        block = min(blocks, key=lambda rect: abs((rect.y0 + rect.y1) / 2 - target))
        rect = fitz.Rect(max(20, block.x0 - 6), max(0, block.y0 - 6), min(155, block.x1 + 6), min(_PAGE_BOTTOM, block.y1 + 6))
    else:
        rect = fitz.Rect(25, max(0, section.start + 8), 150, min(_PAGE_BOTTOM, section.end - 5))
    return tuple(round(value, 1) for value in (rect.x0, rect.y0, rect.x1, rect.y1))


def _options_for_group(group: _PriceGroup, has_grades: bool) -> tuple[tuple[str, Decimal], ...]:
    if len(group.values) == 1:
        return ()
    if has_grades and len(group.values) == 2:
        return (("Grado 0", group.values[0]), ("Grado A", group.values[1]))
    return tuple((f"Precio publicado {index}", value) for index, value in enumerate(group.values, 1))


def _all_group_options(
    groups: Sequence[_PriceGroup],
    *,
    has_grades: bool,
) -> tuple[tuple[str, Decimal], ...]:
    options: list[tuple[str, Decimal]] = []
    for group in groups:
        expanded = _options_for_group(group, has_grades)
        if expanded:
            options.extend(expanded)
        else:
            options.append((group.label or f"Precio publicado {len(options) + 1}", group.values[0]))
    return tuple(options)


def _pricing_label(label: str) -> bool:
    folded = _fold(label)
    return any(token in folded for token in ("grado", "tela", "piel"))


def _records_from_section(page: fitz.Page, section: _Section) -> tuple[_Record, ...]:
    codes = _codes(section)
    groups = _price_groups(section)
    if not codes or not groups:
        return ()
    description = _description(section)
    image_blocks = _image_blocks(page, section)
    folded_lines = {_fold(line.text) for line in section.lines}
    has_grades = "grado 0" in folded_lines and "grado a" in folded_lines
    assignments: list[
        tuple[_Code, _PriceGroup, tuple[_PriceGroup, ...], tuple[tuple[str, Decimal], ...]]
    ] = []

    leather_codes = [code for code in codes if code.value.upper().endswith("P00")]
    labelled_shared = (
        len(groups) > 1
        and all(group.label and _pricing_label(group.label) for group in groups)
        and not leather_codes
    )
    if leather_codes and len(leather_codes) == 1 and any("piel" in _fold(group.label) for group in groups):
        leather = leather_codes[0]
        fabric_groups = tuple(group for group in groups if "piel" not in _fold(group.label))
        leather_groups = tuple(group for group in groups if "piel" in _fold(group.label))
        for code in codes:
            selected = leather_groups if code is leather else fabric_groups
            options = _all_group_options(selected, has_grades=has_grades)
            if len(options) == 1:
                assignments.append((code, selected[0], selected, ()))
            else:
                assignments.append((code, selected[0], selected, options))
    elif labelled_shared:
        shared = _all_group_options(groups, has_grades=has_grades)
        for code in codes:
            assignments.append((code, groups[0], groups, shared))
    elif len(groups) == 1:
        group = groups[0]
        options = _options_for_group(group, has_grades)
        for code in codes:
            assignments.append((code, group, (group,), options))
    elif len(groups) > len(codes):
        for code, group in zip(codes[:-1], groups):
            assignments.append((code, group, (group,), _options_for_group(group, has_grades)))
        last_groups = groups[len(codes) - 1 :]
        last_options = _all_group_options(last_groups, has_grades=has_grades)
        assignments.append(
            (codes[-1], last_groups[0], last_groups, last_options if len(last_options) > 1 else ())
        )
    else:
        for code, group in zip(codes, groups):
            assignments.append((code, group, (group,), _options_for_group(group, has_grades)))

        # En la ficha ARETA de la página 8, el bloque declara tres códigos
        # "Cuerpo + base mismo color" debajo de 106-002XX. El único importe
        # del subgrupo es $1,835; se comparte sólo dentro de esta ficha.
        if (
            section.page_number == 8
            and _fold(section.title).startswith("areta polipropileno")
            and len(codes) == 7
            and len(groups) == 4
        ):
            shared_group = groups[-1]
            for code in codes[len(groups) :]:
                assignments.append((code, shared_group, (shared_group,), ()))

    result = []
    for code, group, evidence_groups, options in assignments:
        direct = options[0][1] if options else group.values[0]
        result.append(
            _Record(
                page_number=section.page_number,
                title=section.title,
                collection=_collection(section.page_number),
                code=code.value,
                variant=_configuration_label(section, code, group),
                description=description,
                direct_price=direct,
                options=options,
                code_bbox=code.line.bbox,
                price_bboxes=tuple(evidence.bbox for evidence in evidence_groups),
                crop_bbox=_clip_for_record(page, section, code, group),
                image_match_status=(
                    "exact_pdf" if len(codes) == 1 and len(image_blocks) == 1 else "family_pdf"
                ),
            )
        )
    return tuple(result)


def _extract_records(document: fitz.Document) -> tuple[_Record, ...]:
    records = []
    last_page = min(len(document), _PRODUCT_LAST_PAGE)
    for page_number in range(_PRODUCT_FIRST_PAGE, last_page + 1):
        page = document[page_number - 1]
        for section in _sections(page, page_number):
            records.extend(_records_from_section(page, section))
    if not records:
        raise ValueError("LABENZE_EMPTY")
    return tuple(records)


def _pricing_signature(record: _Record) -> tuple[str, tuple[tuple[str, str], ...]]:
    return _money(record.direct_price), tuple((name, _money(value)) for name, value in record.options)


def _union_bbox(rows: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(row[0] for row in rows),
        min(row[1] for row in rows),
        max(row[2] for row in rows),
        max(row[3] for row in rows),
    )


def _merge_explicit_configurations(rows: Sequence[_Record]) -> _Record | None:
    """Colapsa un código repetido sólo si cada fila nombra su configuración."""

    if len(rows) < 2 or any(not row.variant for row in rows):
        return None
    variants = [_fold(row.variant) for row in rows]
    if len(set(variants)) != len(variants):
        return None
    options: list[tuple[str, Decimal]] = []
    for row in sorted(rows, key=lambda candidate: candidate.code_bbox[1]):
        if row.options:
            options.extend(
                (f"{row.variant} / {name}", price)
                for name, price in row.options
            )
        else:
            options.append((row.variant, row.direct_price))
    first = rows[0]
    return _Record(
        page_number=first.page_number,
        title=first.title,
        collection=first.collection,
        code=first.code,
        variant="",
        description=first.description,
        direct_price=options[0][1],
        options=tuple(options),
        code_bbox=_union_bbox([row.code_bbox for row in rows]),
        price_bboxes=tuple(bbox for row in rows for bbox in row.price_bboxes),
        crop_bbox=first.crop_bbox,
        image_match_status="family_pdf",
    )


def _deduplicated_records(records: Sequence[_Record]) -> tuple[tuple[_Record, bool], ...]:
    by_code: dict[str, list[_Record]] = {}
    for record in records:
        by_code.setdefault(record.code.casefold(), []).append(record)
    result: list[tuple[_Record, bool]] = []
    for rows in by_code.values():
        by_section: dict[tuple[int, str], list[_Record]] = {}
        for record in rows:
            by_section.setdefault((record.page_number, record.title), []).append(record)
        collapsed: list[tuple[_Record, bool]] = []
        for section_rows in by_section.values():
            merged = _merge_explicit_configurations(section_rows)
            if merged is not None:
                wildcard_code = merged.code.upper().endswith(("XX", "XXX"))
                collapsed.append((merged, wildcard_code))
                continue
            signatures = {_pricing_signature(record) for record in section_rows}
            if len(signatures) == 1:
                collapsed.append((section_rows[0], False))
            else:
                collapsed.extend((record, True) for record in section_rows)

        if len(by_section) == 1:
            result.extend(collapsed)
        elif len({_pricing_signature(record) for record, _ in collapsed}) == 1 and not any(
            needs_review for _, needs_review in collapsed
        ):
            result.append((collapsed[0][0], False))
        else:
            result.extend((record, True) for record, _ in collapsed)
    return tuple(sorted(result, key=lambda pair: (pair[0].code.casefold(), pair[0].page_number, pair[0].title)))


def _render_asset(page: fitz.Page, bbox: tuple[float, float, float, float]) -> ImageAsset | None:
    rect = fitz.Rect(bbox)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    png = pixmap.tobytes("png")
    with Image.open(io.BytesIO(png)) as image:
        rgb = image.convert("RGB")
        white = Image.new("RGB", rgb.size, "white")
        difference = ImageChops.difference(rgb, white)
        if difference.getbbox() is None:
            return None
        changed = difference.convert("L").point(lambda value: 255 if value > 8 else 0)
        if sum(changed.histogram()[1:]) < max(100, rgb.width * rgb.height // 500):
            return None
    return _normalize_image(png)


def _base_options(record: _Record) -> list[dict]:
    seen: Counter[str] = Counter()
    result = []
    for name, price in record.options:
        base = _slug(name)
        seen[base] += 1
        option_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        result.append({"id": option_id, "name": name, "price_net": _money(price), "available": True})
    return result


def _item(
    record: _Record,
    *,
    source_hash: str,
    duplicate_code: bool,
    asset: ImageAsset | None,
    code_reference: dict,
    price_references: tuple[dict, ...],
    image_reference: dict,
) -> dict:
    identity_material = f"{record.code}\0{record.title}\0{record.variant}\0{record.page_number}\0{record.code_bbox}"
    identity_hash = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:20]
    if duplicate_code:
        internal_id = f"labenze:review:{_slug(record.code)}:{identity_hash}"
        sku = ""
        code_status = "needs_review"
        warnings = ["Código publicado repetido con precios distintos; conservar evidencia y revisar al cotizar."]
    else:
        internal_id = f"labenze:{_slug(record.code)}"
        sku = record.code
        code_status = "verified"
        warnings = []
    name = record.title if not record.variant else f"{record.title} — {record.variant}"
    attributes = {
        "source_code": record.code,
        "variant": record.variant,
        "source_page": record.page_number,
        "price_basis": "MXN neto antes de IVA",
        "quotable": True,
        "source_sha256": source_hash,
        "evidence": {
            "code": code_reference,
            "prices": list(price_references),
            "image": image_reference,
        },
    }
    image_kind = "placeholder"
    if asset is not None:
        image_kind = "official"
        attributes["image_match"] = {
            "status": record.image_match_status,
            "asset_sha256": asset.sha256,
            "source_references": [image_reference],
        }
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": f"{asset.sha256}.png",
            "image_kind": "official",
            "label": (
                "Recorte oficial exacto de ficha PDF Labenze B26"
                if record.image_match_status == "exact_pdf"
                else "Imagen oficial de familia/modelo en ficha PDF Labenze B26"
            ),
            "approved": True,
        }
    item = {
        "internal_id": internal_id,
        "supplier": "labenze",
        "product_key": internal_id.removeprefix("labenze:"),
        "sku": sku,
        "code_status": code_status,
        "brand": "Labenze",
        "collection": record.collection,
        "name": name[:1000],
        "description": record.description,
        "unit": "PZA",
        "availability_type": "made_to_order",
        "stock": None,
        "lead_time": "Por confirmar",
        "base_price_options": _base_options(record),
        "add_on_options": [],
        "base_currency": "MXN",
        "price_net": _money(record.direct_price),
        "tax_rate": "0.160000",
        "attributes": attributes,
        "image_url": "",
        "image_kind": image_kind,
        "product_url": f"{_PDF_URL}#page={record.page_number}",
        "warnings": warnings,
        "source_reference": json.dumps(
            [code_reference, *price_references, image_reference],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    }
    if tuple(item) != tuple(PUBLIC_ITEM_FIELDS):
        raise ValueError("LABENZE_PUBLIC_FIELDS")
    return item


def _build(
    files: object,
    *,
    synced_at: datetime | None,
    include_assets: bool,
) -> CatalogSnapshotBuild:
    _document, data = _validated_document(files)
    source_hash = hashlib.sha256(data).hexdigest()
    pdf = fitz.open(stream=data, filetype="pdf")
    assets: dict[str, ImageAsset] = {}
    bindings: list[CatalogAssetBinding] = []
    items = []
    try:
        records = _deduplicated_records(_extract_records(pdf))
        for record, duplicate_code in records:
            page = pdf[record.page_number - 1]
            code_reference = source_ref(source_hash, record.page_number, record.code_bbox)
            price_references = tuple(
                source_ref(source_hash, record.page_number, bbox)
                for bbox in record.price_bboxes
            )
            image_reference = source_ref(source_hash, record.page_number, record.crop_bbox)
            asset = _render_asset(page, record.crop_bbox) if include_assets else None
            item = _item(
                record,
                source_hash=source_hash,
                duplicate_code=duplicate_code,
                asset=asset,
                code_reference=code_reference,
                price_references=price_references,
                image_reference=image_reference,
            )
            items.append(item)
            if asset is not None:
                assets.setdefault(asset.sha256, asset)
                bindings.append(
                    CatalogAssetBinding(
                        item["internal_id"],
                        asset.sha256,
                        f"{asset.sha256}.png",
                        "official",
                        record.image_match_status,
                        (image_reference,),
                    )
                )
    finally:
        pdf.close()
    items.sort(key=lambda item: item["internal_id"])
    bindings.sort(key=lambda binding: binding.internal_id)
    generated_at = synced_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    snapshot = {
        "supplier": "labenze",
        "source_hash": source_hash,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "items": items,
    }
    load_supplier_catalog_data(snapshot, expected_supplier="labenze")
    return CatalogSnapshotBuild(snapshot, assets, tuple(bindings))


def build_labenze_snapshot(
    files: object,
    *,
    synced_at: datetime | None = None,
) -> dict:
    """Construye el snapshot JSON sin rasterizar activos."""

    return _build(files, synced_at=synced_at, include_assets=False).snapshot


def build_labenze_snapshot_with_assets(
    files: object,
    *,
    synced_at: datetime | None = None,
) -> CatalogSnapshotBuild:
    """Construye snapshot, recortes oficiales y un binding por item cubierto."""

    return _build(files, synced_at=synced_at, include_assets=True)


__all__ = ("build_labenze_snapshot", "build_labenze_snapshot_with_assets")
