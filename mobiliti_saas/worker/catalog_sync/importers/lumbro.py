from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Mapping
from xml.etree import ElementTree

from mobiliti_saas.worker.catalog_sync.lumbro_links import (
    load_lumbro_link_index,
    resolve_lumbro_link,
)

from . import common as _common
from .common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    CellRef,
    ImageAsset,
    extract_xlsx_images,
    iter_pdf_pages,
    neutralize_spreadsheet_text,
    open_xlsx_data_only,
    open_xlsx_data_only_from_bytes,
    read_validated_source,
    source_ref,
    validate_source_file,
)


_GENERAL_PATH = "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf"
_NEW_PATH = "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf"
_EXPECTED_PDFS = {
    _GENERAL_PATH: 2,
    _NEW_PATH: 3,
}
_PDF_MIME = "application/pdf"
_SPEC_PATH = "SPEC GUIDES 2026/LUMBRO/Spec guide-Lumbro-2026.xlsx"
_SPEC_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SPEC_SHEET = "SPEC-GUIDE-LUMBRO"
_SPEC_FIRST_ROW = 8
_SPEC_LAST_ROW = 520
_INTERCONNECTION_PATH = "LUMBRO/LP/Precios Interconexión Sunón act.xlsx"
_INTERCONNECTION_SHEET = "2026"
_INTERCONNECTION_HEADER = "PRECIOS UNITARIOS MENOS EL 10% DESCUENTO MAS IVA"
_INTERCONNECTION_AUTHORITY = 4
_CATALOG_PATH = "LUMBRO/CATALOGO/CATALOGO LUMBRO 2024 DIGITAL (1).pdf"
_CATALOG_MIME = "application/pdf"
_CATALOG_SHA256 = "bbd810ebab20336d2a6bdc61123955bd062c5a64d57d4359556fcf6aef57e053"
_CATALOG_PDF_PROFILE = {"pdf_profile": "lumbro_catalog_2024"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MONEY = re.compile(
    r"^\s*\$?\s*((?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{2})?)\s*$"
)
_MONEY_LIKE = re.compile(r"(?:\$|\b[A-Z]{3}\b).*[0-9]|[0-9].*(?:,|\.)", re.IGNORECASE)


LumbroParseStatus = Literal["parsed", "needs_review"]


@dataclass(frozen=True)
class LumbroPriceSource:
    path: str
    file_id: str
    page: int


@dataclass(frozen=True)
class LumbroPriceRecord:
    identity: str
    model: str
    configuration: str
    net_price: Decimal | None
    currency: Literal["MXN"]
    tax_rate: Decimal
    source: LumbroPriceSource
    authority_rank: int
    parse_status: LumbroParseStatus
    warnings: tuple[str, ...] = ()
    color: str = ""


@dataclass(frozen=True)
class LumbroSpecSource:
    path: str
    file_id: str
    sheet: str
    heading_row: int | None
    row: int


@dataclass(frozen=True)
class LumbroSpecRecord:
    internal_id: str
    identity: str
    price_identity: str
    model: str
    configuration: str
    color: str
    code: str
    description: str
    dimensions: str
    mounting: str
    notes: tuple[str, ...]
    currency: str
    spec_price_evidence: object | None
    source: LumbroSpecSource
    provenance: Mapping[str, tuple[dict, ...]]
    image_sha256: str | None = None
    image_warning: str | None = None
    net_price: Decimal | None = None
    price_source: LumbroPriceSource | LumbroInterconnectionSource | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LumbroSpecBuild:
    records: tuple[LumbroSpecRecord, ...]
    assets_by_sha256: Mapping[str, ImageAsset]
    bindings: tuple[CatalogAssetBinding, ...]


@dataclass(frozen=True)
class LumbroInterconnectionSource:
    path: str
    file_id: str
    sheet: str
    row: int
    description_cell: str
    price_cell: str


@dataclass(frozen=True)
class LumbroInterconnectionRecord:
    internal_id: str
    identity: str
    code: str
    model: str
    configuration: str
    description: str
    net_price: Decimal | None
    currency: Literal["MXN"]
    tax_rate: Decimal
    source: LumbroInterconnectionSource
    provenance: Mapping[str, tuple[dict, ...]]
    authority_rank: int
    parse_status: LumbroParseStatus
    warnings: tuple[str, ...] = ()
    color: str = ""


LumbroImageEvidenceStatus = Literal["bound", "excluded"]


@dataclass(frozen=True)
class LumbroInterconnectionImageEvidence:
    source: CellRef
    asset_sha256: str
    status: LumbroImageEvidenceStatus
    internal_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class LumbroInterconnectionBuild:
    records: tuple[LumbroInterconnectionRecord, ...]
    assets_by_sha256: Mapping[str, ImageAsset]
    bindings: tuple[CatalogAssetBinding, ...]
    image_evidence: tuple[LumbroInterconnectionImageEvidence, ...]


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    plain = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", plain.casefold()))


def _designation(model: str, configuration: str = "") -> tuple[str, str]:
    return model, configuration


_PUBLISHED_DESIGNATIONS = {
    _fold(label): _designation(model, configuration)
    for label, model, configuration in (
        ("BARCELONA", "Barcelona", ""),
        ("BARCELONA CARGA", "Barcelona", "Carga"),
        ("BARCELONA BOX", "Barcelona", "Box"),
        ("BARCELONA BOX/HDMI INALÁMBRICO", "Barcelona", "Box/HDMI Inalámbrico"),
        ("LISBOA", "Lisboa", ""),
        ("LISBOA CARGA", "Lisboa", "Carga"),
        ("PRAGA", "Praga", ""),
        ("AMBERES", "Amberes", ""),
        ("AMBERES DE CARGA", "Amberes", "Carga"),
        ("IBIZA", "Ibiza", ""),
        ("IBIZA DE CARGA", "Ibiza", "Carga"),
        ("IBIZA DE CARGA A+C", "Ibiza", "Carga A+C"),
        ("ATENAS", "Atenas", ""),
        ("BARI", "Bari", ""),
        ("BARI-G", "Bari-G", ""),
        ("BARI PASACABLE", "Bari", "Pasacable"),
        ("VENECIA", "Venecia", ""),
        ("VENECIA INALÁMBRICO", "Venecia", "Inalámbrico"),
        ("PISA", "Pisa", ""),
        ("SPLIT", "Split", ""),
        ("SPLIT MINI", "Split", "Mini"),
        ("SPLIT MINI PUERTOS", "Split", "Mini Puertos"),
        ("SPLIT MINI A+C", "Split", "Mini A+C"),
        ("SPLIT NANO 1", "Split", "Nano 1"),
        ("SPLIT NANO 2", "Split", "Nano 2"),
        ("SPLIT G", "Split", "G"),
        ("SPLIT G DE CARGA", "Split", "G Carga"),
        ("SPLIT G A+C", "Split", "G A+C"),
        ("MARSELLA", "Marsella", ""),
        ("DUBLIN", "Dublin", ""),
        ("DUBLIN G", "Dublin", "G"),
        ("NAPOLI", "Napoli", ""),
        ("AMSTERDAM", "Amsterdam", ""),
        ("LIVERPOOL", "Liverpool", ""),
        ("MAUI", "Maui", ""),
        ("VANCOUVER", "Vancouver", ""),
        ("TORRE HEXA", "Torre Hexa", ""),
        ("TORRE OCTA", "Torre Octa", ""),
        ("HAMBURGO", "Hamburgo", ""),
        ("MONACO", "Monaco", ""),
        ("MONACO G", "Monaco", "G"),
        ("CABLE HDMI LUMBRO 4K 0.5 M", "Cable HDMI Lumbro 4K", "0.5 m"),
        ("CABLE HDMI LUMBRO 4K 5 M", "Cable HDMI Lumbro 4K", "5 m"),
        ("CABLE HDMI LUMBRO 4K 10 M", "Cable HDMI Lumbro 4K", "10 m"),
        (
            "CABLE HDMI LUMBRO 4K FIBRA OPTICA 5 M",
            "Cable HDMI Lumbro 4K",
            "Fibra óptica 5 m",
        ),
        (
            "CABLE HDMI LUMBRO 4K FIBRA OPTICA 10 M",
            "Cable HDMI Lumbro 4K",
            "Fibra óptica 10 m",
        ),
    )
}

_SPEC_DESIGNATION_ALIASES = {
    _fold(label): _designation(model, configuration)
    for label, model, configuration in (
        ("AMBERES CARGA", "Amberes", "Carga"),
        ("IBIZA CARGA", "Ibiza", "Carga"),
        ("SPLIT G CARGA", "Split", "G Carga"),
        ("MONACO-G", "Monaco", "G"),
        ("BARCELONA BOX HDMI", "Barcelona", "Box/HDMI Inalámbrico"),
        ("BARCELONA BOX IN", "Barcelona", "Box/HDMI Inalámbrico"),
    )
}


def _identity(model: str, configuration: str) -> str:
    return _fold(" ".join(value for value in (model, configuration) if value))


def _validated_pdf_bundle(files) -> tuple[object, ...]:
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("LUMBRO_PDF_BUNDLE") from None
    if len(rows) != len(_EXPECTED_PDFS):
        raise ValueError("LUMBRO_PDF_BUNDLE")

    by_path = {}
    for row in rows:
        logical_path = getattr(row, "path", None)
        local_path = getattr(row, "local_path", None)
        declared_hash = getattr(row, "sha256", None)
        if (
            logical_path not in _EXPECTED_PDFS
            or logical_path in by_path
            or getattr(row, "kind", None) != "price_list"
            or getattr(row, "brand", None) is not None
            or getattr(row, "mime_type", None) != _PDF_MIME
            or not isinstance(local_path, Path)
            or local_path.suffix.casefold() != ".pdf"
            or not isinstance(declared_hash, str)
            or _HASH.fullmatch(declared_hash) is None
        ):
            raise ValueError("LUMBRO_PDF_BUNDLE")
        validated = validate_source_file(local_path, ".pdf")
        if validated.sha256 != declared_hash:
            raise ValueError("LUMBRO_PDF_HASH")
        by_path[logical_path] = row

    if set(by_path) != set(_EXPECTED_PDFS):
        raise ValueError("LUMBRO_PDF_BUNDLE")
    return tuple(by_path[path] for path in _EXPECTED_PDFS)


def _designation_at(lines: tuple[str, ...], index: int):
    if not _fold(lines[index]):
        return None, 0
    for width in range(min(3, len(lines) - index), 0, -1):
        key = _fold(" ".join(lines[index : index + width]))
        designation = _PUBLISHED_DESIGNATIONS.get(key)
        if designation is not None:
            return designation, width
    return None, 0


def _record(row, page: int, model: str, configuration: str, price, status, warnings):
    return LumbroPriceRecord(
        identity=_identity(model, configuration),
        model=model,
        configuration=configuration,
        net_price=price,
        currency="MXN",
        tax_rate=Decimal("0.16"),
        source=LumbroPriceSource(row.path, row.sha256, page),
        authority_rank=_EXPECTED_PDFS[row.path],
        parse_status=status,
        warnings=tuple(warnings),
    )


def _parse_page(row, page) -> list[LumbroPriceRecord]:
    lines = tuple(line.strip() for line in page.text.splitlines() if line.strip())
    records = []
    pending = None
    pending_line = None
    orphan_prices = []
    index = 0
    while index < len(lines):
        designation, width = _designation_at(lines, index)
        if designation is not None:
            if pending is not None:
                records.append(
                    _record(
                        row,
                        page.number,
                        *pending,
                        None,
                        "needs_review",
                        ("missing_price",),
                    )
                )
            pending = designation
            pending_line = index
            index += width
            continue

        money = _MONEY.fullmatch(lines[index])
        if money is not None:
            try:
                price = Decimal(money.group(1).replace(",", ""))
            except InvalidOperation:
                price = None
            if price is None or not price.is_finite() or price <= 0:
                if pending is not None:
                    records.append(
                        _record(
                            row,
                            page.number,
                            *pending,
                            None,
                            "needs_review",
                            ("malformed_currency",),
                        )
                    )
                    pending = pending_line = None
            elif pending is None:
                orphan_prices.append((index, price))
            else:
                records.append(
                    _record(row, page.number, *pending, price, "parsed", ())
                )
                pending = pending_line = None
            index += 1
            continue

        if pending is not None and _MONEY_LIKE.search(lines[index]):
            records.append(
                _record(
                    row,
                    page.number,
                    *pending,
                    None,
                    "needs_review",
                    ("malformed_currency",),
                )
            )
            pending = pending_line = None
        index += 1

    if pending is not None:
        eligible = [(line, price) for line, price in orphan_prices if line < pending_line]
        if len(eligible) == 1 and len(orphan_prices) == 1:
            records.append(
                _record(row, page.number, *pending, eligible[0][1], "parsed", ())
            )
            orphan_prices.clear()
        else:
            records.append(
                _record(
                    row,
                    page.number,
                    *pending,
                    None,
                    "needs_review",
                    ("missing_price",),
                )
            )

    for _, price in orphan_prices:
        records.append(
            _record(
                row,
                page.number,
                "",
                "",
                price,
                "needs_review",
                ("missing_identity",),
            )
        )
    return records


def _mark_conflicting_prices(records: list[LumbroPriceRecord]) -> tuple[LumbroPriceRecord, ...]:
    prices_by_identity: dict[tuple[str, str, int], set[Decimal]] = {}
    for record in records:
        if record.identity and record.net_price is not None:
            key = (record.identity, _fold(record.color), record.authority_rank)
            prices_by_identity.setdefault(key, set()).add(record.net_price)
    conflicts = {
        key for key, prices in prices_by_identity.items() if len(prices) > 1
    }
    return tuple(
        replace(
            record,
            parse_status="needs_review",
            warnings=record.warnings + ("conflicting_price",),
        )
        if (record.identity, _fold(record.color), record.authority_rank) in conflicts
        and "conflicting_price" not in record.warnings
        else record
        for record in records
    )


def parse_lumbro_pdf_prices(files) -> tuple[LumbroPriceRecord, ...]:
    """Construye evidencia tipada de las dos listas PDF comerciales de Lumbro."""

    records = []
    for row in _validated_pdf_bundle(files):
        for page in iter_pdf_pages(row.local_path):
            records.extend(_parse_page(row, page))
    return _mark_conflicting_prices(records)


def _plain(value: object) -> str:
    return " ".join(neutralize_spreadsheet_text(value).split())


_INTERCONNECTION_CODES_BY_DESCRIPTION = {
    _plain(description): (code, model, _plain(description))
    for code, model, description in (
        (
            "MULT-LIDO-INT",
            "LIDO",
            "Multicontacto Especial LINEA LIDO PARA INTERCONECTAR 4 Puertos AC , "
            "1 Puerto USB DE CARGA DOBLE TIPO A, CON ENTRADAS PARA ARNES DE AMBOS "
            "LADOS, MEDIDAS DE 42 X 16 CM ",
        ),
        (
            "LIDO.OP-INT",
            "LIDO",
            "Multicontacto LIDO para canaleta COLOR GRIS OXFORD con 3 puertos AC "
            "No Regulados y 1 PUERTO USB CARGA DOBLE, para INTERCONECTAR",
        ),
        (
            "JUMP-1.5M",
            "JUMPER",
            "Cable de interconexión o JUMPER CON SALIDA PARA ARNES POR AMBOS LADOS "
            "de 1.5 metros para Carga No Regula ",
        ),
        (
            "CAJA-FUS",
            "CAJA DE FUSIBLE",
            "Caja de Fusible, PARA CARGA NO REGULADA, con entrada para ARNES de un "
            "costado y del otro cable cal 14 con clavija de 2.5 m de longitud",
        ),
    )
}


def _validated_interconnection_source(source):
    local_path = getattr(source, "local_path", None)
    declared_hash = getattr(source, "sha256", None)
    if (
        getattr(source, "path", None) != _INTERCONNECTION_PATH
        or getattr(source, "kind", None) != "price_list"
        or getattr(source, "brand", None) is not None
        or getattr(source, "mime_type", None) != _SPEC_MIME
        or not isinstance(local_path, Path)
        or local_path.suffix.casefold() != ".xlsx"
        or not isinstance(declared_hash, str)
        or _HASH.fullmatch(declared_hash) is None
    ):
        raise ValueError("LUMBRO_INTERCONNECTION_SOURCE")
    validated = validate_source_file(local_path, ".xlsx")
    if validated.sha256 != declared_hash:
        raise ValueError("LUMBRO_INTERCONNECTION_HASH")
    return source


def _validated_interconnection_package(source):
    source = _validated_interconnection_source(source)
    validated, data = read_validated_source(source.local_path, ".xlsx")
    if validated.sha256 != source.sha256:
        raise ValueError("LUMBRO_INTERCONNECTION_HASH")
    parts, sheets, _, content_types = _common._validate_xlsx(data)
    return source, parts, sheets, content_types


def _data_only_package_for_sheet(parts, sheets, selected_sheet: str) -> bytes:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cleaned = dict(parts)
    for sheet_name, part_name in sheets.items():
        if sheet_name == selected_sheet:
            continue
        try:
            root = ElementTree.fromstring(cleaned[part_name])
        except Exception:
            raise ValueError("LUMBRO_INTERCONNECTION_SHEET_XML") from None
        root[:] = [
            child for child in root if child.tag != f"{{{namespace}}}drawing"
        ]
        cleaned[part_name] = ElementTree.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        relationships_name = _common._sheet_relationship_name(part_name)
        if relationships_name in cleaned:
            try:
                relationships = ElementTree.fromstring(cleaned[relationships_name])
            except Exception:
                raise ValueError("LUMBRO_INTERCONNECTION_SHEET_XML") from None
            relationships[:] = [
                relation
                for relation in relationships
                if not relation.get("Type", "").endswith("/drawing")
            ]
            cleaned[relationships_name] = ElementTree.tostring(
                relationships, encoding="utf-8", xml_declaration=True
            )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(cleaned):
            archive.writestr(name, cleaned[name])
    return output.getvalue()


def _cached_formula_prices(parts, sheets) -> dict[str, str]:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    part_name = sheets.get(_INTERCONNECTION_SHEET)
    if part_name is None:
        return {}
    try:
        root = ElementTree.fromstring(parts[part_name])
    except Exception:
        raise ValueError("LUMBRO_INTERCONNECTION_SHEET_XML") from None

    cached = {}
    for cell in root.iter(f"{{{namespace}}}c"):
        coordinate = cell.get("r", "").upper()
        price_match = re.fullmatch(r"H([1-9][0-9]*)", coordinate)
        if coordinate != "P4" and (
            price_match is None or int(price_match.group(1)) < 4
        ):
            continue
        formula = cell.find(f"{{{namespace}}}f")
        value = cell.find(f"{{{namespace}}}v")
        if formula is None or value is None or cell.get("t") not in {None, "n"}:
            continue
        raw = (value.text or "").strip()
        try:
            numeric = Decimal(raw)
        except InvalidOperation:
            continue
        if numeric.is_finite() and numeric > 0:
            cached[coordinate] = raw
    return cached


def _sheet_image_gallery(parts, sheets, content_types):
    part_name = sheets.get(_INTERCONNECTION_SHEET)
    if part_name is None:
        return ()
    found = _common._drawing_images(
        parts,
        {_INTERCONNECTION_SHEET: part_name},
        content_types,
        keep_ambiguous=True,
    )
    return tuple(_common._normalized_xlsx_images(parts, found))


def _interconnection_price(value: object) -> tuple[Decimal | None, tuple[str, ...]]:
    if value is None:
        return None, ("missing_price",)
    if isinstance(value, bool):
        return None, ("malformed_price",)
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, ("malformed_price",)
    if not price.is_finite() or price <= 0:
        return None, ("malformed_price",)
    return price, ()


def _interconnection_internal_id(code: str, description: str, cell: str) -> str:
    material = "\0".join((code, description, cell))
    return "lumbro:interconnection:" + hashlib.sha256(material.encode()).hexdigest()[:20]


def _interconnection_record(
    source,
    sheet,
    row: int,
    description_column: str,
    price_column: str,
    cached_formula_prices,
):
    description_cell = f"{description_column}{row}"
    price_cell = f"{price_column}{row}"
    description = _plain(sheet[description_cell].value)
    raw_price = cached_formula_prices.get(price_cell, sheet[price_cell].value)
    if not description and raw_price is None:
        return None
    if description.startswith("*"):
        return None

    designation = _INTERCONNECTION_CODES_BY_DESCRIPTION.get(description)
    code, model, configuration = designation or ("", "", "")
    price, price_warnings = _interconnection_price(raw_price)
    warnings = (() if code else ("missing_code",)) + price_warnings
    status: LumbroParseStatus = "parsed" if not warnings else "needs_review"
    source_evidence = LumbroInterconnectionSource(
        source.path,
        source.sha256,
        _INTERCONNECTION_SHEET,
        row,
        description_cell,
        price_cell,
    )
    internal_id = _interconnection_internal_id(code, description, description_cell)
    return LumbroInterconnectionRecord(
        internal_id=internal_id,
        identity=_identity(model, configuration) if code else _fold(description),
        code=code,
        model=model,
        configuration=configuration,
        description=description,
        net_price=price,
        currency="MXN",
        tax_rate=Decimal("0.16"),
        source=source_evidence,
        provenance={
            "description": (source_ref(source.sha256, _INTERCONNECTION_SHEET, description_cell),),
            "price": (source_ref(source.sha256, _INTERCONNECTION_SHEET, price_cell),),
        },
        authority_rank=_INTERCONNECTION_AUTHORITY,
        parse_status=status,
        warnings=warnings,
    )


def _image_anchor_row(reference: CellRef) -> int | None:
    match = re.fullmatch(r"[A-Z]{1,3}([1-9][0-9]{0,6})", reference.cell, re.IGNORECASE)
    return int(match.group(1)) if match is not None else None


def _bind_interconnection_images(source, records, images):
    records_by_row: dict[int, list[LumbroInterconnectionRecord]] = {}
    for record in records:
        records_by_row.setdefault(record.source.row, []).append(record)
    images_by_row: dict[int, list[tuple[CellRef, ImageAsset]]] = {}
    for reference, asset in images:
        row = _image_anchor_row(reference)
        if reference.sheet == _INTERCONNECTION_SHEET and row is not None:
            images_by_row.setdefault(row, []).append((reference, asset))

    assets = {}
    bindings = []
    evidence = []
    image_warning_by_row = {}
    bound_rows = set()
    for row in sorted(images_by_row):
        row_images = sorted(images_by_row[row], key=lambda candidate: candidate[0].cell)
        row_records = records_by_row.get(row, ())
        can_bind = len(row_records) == 1 and len(row_images) == 1
        if not row_records:
            reason = "no_product_row"
        elif len(row_records) > 1:
            reason = "ambiguous_product_row"
        elif len(row_images) > 1:
            reason = "ambiguous_images"
        else:
            reason = None

        if can_bind:
            record = row_records[0]
            reference, asset = row_images[0]
            bound_rows.add(row)
            assets[asset.sha256] = asset
            bindings.append(
                CatalogAssetBinding(
                    internal_id=record.internal_id,
                    asset_sha256=asset.sha256,
                    object_name=f"lumbro/{asset.sha256}.png",
                    image_kind="official",
                    match_status="exact_xlsx",
                    source_references=(
                        source_ref(source.sha256, _INTERCONNECTION_SHEET, reference.cell),
                    ),
                )
            )
            evidence.append(
                LumbroInterconnectionImageEvidence(
                    reference, asset.sha256, "bound", record.internal_id
                )
            )
        else:
            if row_records:
                image_warning_by_row[row] = "ambiguous_image"
            for reference, asset in row_images:
                evidence.append(
                    LumbroInterconnectionImageEvidence(
                        reference, asset.sha256, "excluded", reason=reason
                    )
                )

    enriched = []
    for record in records:
        warning = image_warning_by_row.get(record.source.row)
        if warning is None and record.source.row not in bound_rows:
            warning = "missing_image"
        enriched.append(
            replace(record, warnings=record.warnings + (warning,)) if warning else record
        )
    return tuple(enriched), assets, tuple(bindings), tuple(evidence)


def parse_lumbro_interconnection(source) -> LumbroInterconnectionBuild:
    """Lee solo la hoja activa 2026 y conserva precios netos/celdas exactas."""

    source, parts, sheets, content_types = _validated_interconnection_package(source)
    passive_data = _data_only_package_for_sheet(parts, sheets, _INTERCONNECTION_SHEET)
    workbook = open_xlsx_data_only_from_bytes(passive_data)
    try:
        if workbook.active.title != _INTERCONNECTION_SHEET:
            raise ValueError("LUMBRO_INTERCONNECTION_ACTIVE_SHEET")
        sheet = workbook.active
        if sheet["H3"].value != _INTERCONNECTION_HEADER:
            raise ValueError("LUMBRO_INTERCONNECTION_HEADER")
        cached_prices = _cached_formula_prices(parts, sheets)
        records = []
        for row in range(4, sheet.max_row + 1):
            record = _interconnection_record(
                source, sheet, row, "G", "H", cached_prices
            )
            if record is not None:
                records.append(record)
        alternate = _interconnection_record(
            source, sheet, 4, "O", "P", cached_prices
        )
        if alternate is not None:
            records.append(alternate)
    finally:
        workbook.close()

    images = _sheet_image_gallery(parts, sheets, content_types)
    records, assets, bindings, evidence = _bind_interconnection_images(
        source, tuple(records), images
    )
    return LumbroInterconnectionBuild(records, assets, bindings, evidence)


def _validated_spec_source(source):
    local_path = getattr(source, "local_path", None)
    declared_hash = getattr(source, "sha256", None)
    if (
        getattr(source, "path", None) != _SPEC_PATH
        or getattr(source, "kind", None) != "spec_guide"
        or getattr(source, "brand", None) is not None
        or getattr(source, "mime_type", None) != _SPEC_MIME
        or not isinstance(local_path, Path)
        or local_path.suffix.casefold() != ".xlsx"
        or not isinstance(declared_hash, str)
        or _HASH.fullmatch(declared_hash) is None
    ):
        raise ValueError("LUMBRO_SPEC_SOURCE")
    validated = validate_source_file(local_path, ".xlsx")
    if validated.sha256 != declared_hash:
        raise ValueError("LUMBRO_SPEC_HASH")
    return source


def _detail_kind(value: str) -> str | None:
    folded = _fold(value)
    if folded.startswith("color "):
        return "color"
    if folded.startswith("su montaje"):
        return "mounting"
    if folded.startswith("nota"):
        return "note"
    return None


def _heading_candidate(value: str) -> bool:
    folded = _fold(value)
    return bool(value) and _detail_kind(value) is None and not folded.startswith(
        ("linea ", "famila ", "familia ", "sistema ")
    )


def _heading_rows(sheet, coded_rows: tuple[int, ...]) -> dict[int, int | None]:
    headings = {}
    previous = _SPEC_FIRST_ROW
    for row in coded_rows:
        candidates = [
            current
            for current in range(previous + 1, row)
            if _heading_candidate(_plain(sheet.cell(current, 3).value))
        ]
        headings[row] = candidates[-1] if candidates else None
        previous = row
    return headings


def _designation_from_spec(
    heading: str, code: str
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    designations = tuple(
        _SPEC_DESIGNATION_ALIASES.get(_fold(value))
        or _PUBLISHED_DESIGNATIONS.get(_fold(value))
        for value in (heading, code)
    )
    selected = None
    for designation in designations:
        if designation is not None and designation[1]:
            selected = designation
            break
    if selected is None:
        selected = next((value for value in designations if value is not None), None)
    if selected is None:
        source = "heading" if heading else "code"
        return heading or code, "", (source,), ()

    model, configuration = selected
    model_sources = tuple(
        source
        for source, designation in zip(("heading", "code"), designations)
        if designation is not None and designation[0] == model
    )
    configuration_sources = tuple(
        source
        for source, designation in zip(("heading", "code"), designations)
        if configuration and designation is not None and designation[1] == configuration
    )
    return model, configuration, model_sources, configuration_sources


def _colors(value: str) -> tuple[str, ...]:
    text = value.split(":", 1)[1] if ":" in value else value
    colors = []
    for part in re.split(r"\s*(?:,|;|\by\b)\s*", text, flags=re.IGNORECASE):
        clean = _plain(part)
        if clean and clean not in colors:
            colors.append(clean)
    return tuple(colors)


def _cell_references(file_id: str, coordinates) -> tuple[dict, ...]:
    return tuple(source_ref(file_id, _SPEC_SHEET, coordinate) for coordinate in coordinates)


def _internal_id(code: str, model: str, configuration: str, color: str) -> str:
    material = "\0".join((_fold(code), _fold(model), _fold(configuration), _fold(color)))
    return "lumbro:variant:" + hashlib.sha256(material.encode()).hexdigest()[:20]


def _image_rows(images) -> dict[int, tuple[object, ImageAsset]]:
    rows = {}
    for reference, asset in images.items():
        match = re.fullmatch(r"B([0-9]+)", reference.cell, re.IGNORECASE)
        if (
            reference.sheet == _SPEC_SHEET
            and match is not None
            and _SPEC_FIRST_ROW <= int(match.group(1)) <= _SPEC_LAST_ROW
        ):
            rows[int(match.group(1))] = (reference, asset)
    return rows


def _parse_lumbro_spec_workbook(source, workbook) -> LumbroSpecBuild:
    if workbook.sheetnames.count(_SPEC_SHEET) != 1:
        raise ValueError("LUMBRO_SPEC_SHEET")
    sheet = workbook[_SPEC_SHEET]
    headings = tuple(_fold(sheet.cell(_SPEC_FIRST_ROW, column).value).strip(". ") for column in range(1, 7))
    if headings != ("cod", "imagen", "descripcion", "medida unidad", "p unitario", "moneda"):
        raise ValueError("LUMBRO_SPEC_HEADER")

    coded_rows = tuple(
        row
        for row in range(_SPEC_FIRST_ROW + 1, _SPEC_LAST_ROW + 1)
        if _plain(sheet.cell(row, 1).value)
    )
    coded_row_set = set(coded_rows)
    heading_rows = _heading_rows(sheet, coded_rows)
    raw_images = {
        reference: asset
        for reference, asset in extract_xlsx_images(source.local_path).items()
        if reference.sheet == _SPEC_SHEET
    }
    exact_images = {
        row: candidate
        for row, candidate in _image_rows(raw_images).items()
        if row in coded_row_set
    }
    assets_by_sha256 = {asset.sha256: asset for _, asset in exact_images.values()}

    blocks = []
    for index, row in enumerate(coded_rows):
        heading_row = heading_rows[row]
        heading = _plain(sheet.cell(heading_row, 3).value) if heading_row else ""
        code = _plain(sheet.cell(row, 1).value)
        model, configuration, model_sources, configuration_sources = (
            _designation_from_spec(heading, code)
        )
        next_row = coded_rows[index + 1] if index + 1 < len(coded_rows) else _SPEC_LAST_ROW + 1
        next_heading = heading_rows.get(next_row)
        block_end = (next_heading - 1) if next_heading else next_row - 1

        description_parts = []
        description_coordinates = []
        color_values = []
        color_coordinates = []
        mounting_parts = []
        mounting_coordinates = []
        notes = []
        note_coordinates = []
        for current in range(row, block_end + 1):
            text = _plain(sheet.cell(current, 3).value)
            if not text:
                continue
            kind = _detail_kind(text)
            coordinate = f"C{current}"
            if kind == "color":
                color_values.extend(value for value in _colors(text) if value not in color_values)
                color_coordinates.append(coordinate)
            elif kind == "mounting":
                mounting_parts.append(text)
                mounting_coordinates.append(coordinate)
            elif kind == "note":
                notes.append(text)
                note_coordinates.append(coordinate)
            else:
                description_parts.append(text)
                description_coordinates.append(coordinate)

        dimensions = _plain(sheet.cell(row, 4).value)
        currency = _plain(sheet.cell(row, 6).value)
        evidence = sheet.cell(row, 5).value
        identity_coordinates = {
            "heading": f"C{heading_row}" if heading_row else None,
            "code": f"A{row}",
        }
        provenance = {
            "code": _cell_references(source.sha256, (f"A{row}",)),
            "model": _cell_references(
                source.sha256,
                tuple(identity_coordinates[value] for value in model_sources),
            ),
            "configuration": _cell_references(
                source.sha256,
                tuple(identity_coordinates[value] for value in configuration_sources),
            ),
            "description": _cell_references(source.sha256, description_coordinates),
            "dimensions": _cell_references(source.sha256, (f"D{row}",)) if dimensions else (),
            "color": _cell_references(source.sha256, color_coordinates),
            "mounting": _cell_references(source.sha256, mounting_coordinates),
            "notes": _cell_references(source.sha256, note_coordinates),
            "spec_price_evidence": _cell_references(source.sha256, (f"E{row}",)) if evidence is not None else (),
            "currency": _cell_references(source.sha256, (f"F{row}",)) if currency else (),
        }
        blocks.append(
            {
                "row": row,
                "heading_row": heading_row,
                "code": code,
                "model": model,
                "configuration": configuration,
                "colors": tuple(color_values) or ("",),
                "description": " ".join(description_parts),
                "dimensions": dimensions,
                "mounting": " ".join(mounting_parts),
                "notes": tuple(notes),
                "currency": currency,
                "evidence": evidence,
                "provenance": provenance,
            }
        )
    family_images = {}
    for block in blocks:
        exact = exact_images.get(block["row"])
        if exact is not None:
            family_images.setdefault(_fold(block["model"]), []).append(exact)

    records = []
    bindings = []
    for block in blocks:
        exact = exact_images.get(block["row"])
        selected = exact
        borrowed = False
        if selected is None:
            candidates = family_images.get(_fold(block["model"]), ())
            unique = {asset.sha256: (reference, asset) for reference, asset in candidates}
            if len(unique) == 1 and block["configuration"]:
                selected = next(iter(unique.values()))
                borrowed = True
        for color in block["colors"]:
            internal_id = _internal_id(
                block["code"], block["model"], block["configuration"], color
            )
            family_binding = selected is not None and (bool(color) or borrowed)
            warning = "El color puede variar" if family_binding else None
            record = LumbroSpecRecord(
                internal_id=internal_id,
                identity=_identity(block["model"], " ".join(value for value in (block["configuration"], color) if value)),
                price_identity=_identity(block["model"], block["configuration"]),
                model=block["model"],
                configuration=block["configuration"],
                color=color,
                code=block["code"],
                description=block["description"],
                dimensions=block["dimensions"],
                mounting=block["mounting"],
                notes=block["notes"],
                currency=block["currency"],
                spec_price_evidence=block["evidence"],
                source=LumbroSpecSource(
                    source.path,
                    source.sha256,
                    _SPEC_SHEET,
                    block["heading_row"],
                    block["row"],
                ),
                provenance=block["provenance"],
                image_sha256=selected[1].sha256 if selected else None,
                image_warning=warning,
                warnings=(warning,) if warning else (),
            )
            records.append(record)
            if selected is not None:
                reference, asset = selected
                bindings.append(
                    CatalogAssetBinding(
                        internal_id=internal_id,
                        asset_sha256=asset.sha256,
                        object_name=f"lumbro/{asset.sha256}.png",
                        image_kind="official",
                        match_status="family_xlsx" if family_binding else "exact_xlsx",
                        source_references=(
                            source_ref(source.sha256, _SPEC_SHEET, reference.cell),
                        ),
                    )
                )
    return LumbroSpecBuild(tuple(records), assets_by_sha256, tuple(bindings))


def parse_lumbro_spec_guide(source) -> LumbroSpecBuild:
    """Extrae evidencia de identidad del spec guide sin otorgarle autoridad de precio."""

    source = _validated_spec_source(source)
    workbook = open_xlsx_data_only(source.local_path)
    try:
        return _parse_lumbro_spec_workbook(source, workbook)
    finally:
        workbook.close()


def reconcile_lumbro_spec_prices(spec_records, price_records) -> tuple[LumbroSpecRecord, ...]:
    """Añade precios comerciales inequívocos; E permanece como diagnóstico."""

    prices_by_identity = {}
    prices_by_code = {}
    for price in price_records:
        if price.net_price is not None and price.parse_status == "parsed":
            code = getattr(price, "code", "")
            if code:
                prices_by_code.setdefault(code, []).append(price)
            else:
                prices_by_identity.setdefault(price.identity, []).append(price)

    enriched = []
    for record in spec_records:
        matches = (
            *prices_by_code.get(record.code, ()),
            *prices_by_identity.get(record.price_identity, ()),
        )
        if not matches:
            enriched.append(record)
            continue
        authority = max(match.authority_rank for match in matches)
        authoritative = [match for match in matches if match.authority_rank == authority]
        values = {match.net_price for match in authoritative}
        if len(values) != 1:
            enriched.append(record)
            continue
        selected = authoritative[0]
        enriched.append(
            replace(record, net_price=selected.net_price, price_source=selected.source)
        )
    return tuple(enriched)


_SOURCE_CONTRACT = {
    _GENERAL_PATH: ("price_list", _PDF_MIME, ".pdf"),
    _NEW_PATH: ("price_list", _PDF_MIME, ".pdf"),
    _INTERCONNECTION_PATH: ("price_list", _SPEC_MIME, ".xlsx"),
    _SPEC_PATH: ("spec_guide", _SPEC_MIME, ".xlsx"),
    _CATALOG_PATH: ("catalog", _CATALOG_MIME, ".pdf"),
}
_CATALOG_CATEGORIES = {
    "empotrables": "Empotrables",
    "splits": "Splits",
    "sobreponer": "Sobreponer",
    "pasacables": "Pasacables",
}
_CATALOG_MEASUREMENT = re.compile(r"(?i)\b[0-9]+(?:[.,][0-9]+)?\s*mm\b")


def _catalog_pdf_profile(source) -> dict[str, int]:
    local_path = getattr(source, "local_path", None)
    if (
        getattr(source, "path", None) == _CATALOG_PATH
        and getattr(source, "kind", None) == "catalog"
        and getattr(source, "brand", None) is None
        and getattr(source, "mime_type", None) == _CATALOG_MIME
        and getattr(source, "sha256", None) == _CATALOG_SHA256
        and isinstance(local_path, Path)
        and local_path.suffix.casefold() == ".pdf"
    ):
        return dict(_CATALOG_PDF_PROFILE)
    return {}


def _validated_lumbro_bundle(files) -> dict[str, object]:
    try:
        rows = tuple(files)
    except TypeError:
        raise ValueError("LUMBRO_BUNDLE") from None
    if len(rows) != len(_SOURCE_CONTRACT):
        raise ValueError("LUMBRO_BUNDLE")
    by_path = {}
    for row in rows:
        logical_path = getattr(row, "path", None)
        contract = _SOURCE_CONTRACT.get(logical_path)
        local_path = getattr(row, "local_path", None)
        declared_hash = getattr(row, "sha256", None)
        if contract is None:
            raise ValueError("LUMBRO_BUNDLE")
        kind, mime_type, extension = contract
        if (
            logical_path in by_path
            or getattr(row, "kind", None) != kind
            or getattr(row, "brand", None) is not None
            or getattr(row, "mime_type", None) != mime_type
            or not isinstance(local_path, Path)
            or local_path.suffix.casefold() != extension
            or not isinstance(declared_hash, str)
            or _HASH.fullmatch(declared_hash) is None
        ):
            raise ValueError("LUMBRO_BUNDLE")
        profile = _catalog_pdf_profile(row)
        if profile:
            raw_validated, _ = _common._read_source(
                local_path, extension, _common.MAX_FILE_BYTES
            )
            if raw_validated.sha256 != declared_hash:
                raise ValueError("LUMBRO_HASH")
        validated = validate_source_file(local_path, extension, **profile)
        if validated.sha256 != declared_hash:
            raise ValueError("LUMBRO_HASH")
        by_path[logical_path] = row
    if set(by_path) != set(_SOURCE_CONTRACT):
        raise ValueError("LUMBRO_BUNDLE")
    return by_path


def _source_descriptors(files) -> list[dict]:
    descriptors = [
        {
            "path": row.path,
            "kind": row.kind,
            "brand": row.brand,
            "sha256": row.sha256,
            "mime_type": row.mime_type,
        }
        for row in files
    ]
    return sorted(descriptors, key=lambda row: row["path"])


def _source_hash(files) -> str:
    index = load_lumbro_link_index()
    material = {
        "link_manifest_fingerprint": index.resource_fingerprint,
        "sources": _source_descriptors(files),
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_catalog_source(source):
    if (
        getattr(source, "path", None) != _CATALOG_PATH
        or getattr(source, "kind", None) != "catalog"
        or getattr(source, "brand", None) is not None
        or getattr(source, "mime_type", None) != _CATALOG_MIME
        or not isinstance(getattr(source, "local_path", None), Path)
        or source.local_path.suffix.casefold() != ".pdf"
        or not isinstance(getattr(source, "sha256", None), str)
        or _HASH.fullmatch(source.sha256) is None
    ):
        raise ValueError("LUMBRO_CATALOG_SOURCE")
    profile = _catalog_pdf_profile(source)
    if profile:
        raw_validated, _ = _common._read_source(
            source.local_path, ".pdf", _common.MAX_FILE_BYTES
        )
        if raw_validated.sha256 != source.sha256:
            raise ValueError("LUMBRO_CATALOG_HASH")
    validated = validate_source_file(source.local_path, ".pdf", **profile)
    if validated.sha256 != source.sha256:
        raise ValueError("LUMBRO_CATALOG_HASH")
    return source


def _catalog_designation(lines: tuple[str, ...], index: int):
    designation, width = _designation_at(lines, index)
    if designation is not None:
        return designation, width
    aliases = {
        "split mini a c": ("Split", "Mini A+C"),
        "split mini puertos": ("Split", "Mini Puertos"),
        "split g de carga": ("Split", "G Carga"),
        "split g a c": ("Split", "G A+C"),
        "nano 1": ("Split", "Nano 1"),
        "nano 2": ("Split", "Nano 2"),
        "bari pasacables": ("Bari", "Pasacable"),
    }
    for width in range(min(3, len(lines) - index), 0, -1):
        designation = aliases.get(_fold(" ".join(lines[index : index + width])))
        if designation is not None:
            return designation, width
    return None, 0


def _parse_lumbro_catalog(source) -> dict[str, dict]:
    """Indexa solo encabezados exactos y medidas publicadas del PDF técnico."""

    source = _validated_catalog_source(source)
    category = ""
    current_identity = ""
    current_model = ""
    current_configuration = ""
    records: dict[str, dict] = {}
    for page in iter_pdf_pages(
        source.local_path, **_catalog_pdf_profile(source)
    ):
        lines = tuple(
            " ".join(line.split())
            for line in page.text.splitlines()
            if " ".join(line.split())
        )
        for line in lines:
            found = _CATALOG_CATEGORIES.get(_fold(line))
            if found is not None:
                category = found
                break
        index = 0
        page_designation = None
        while index < len(lines):
            designation, width = _catalog_designation(lines, index)
            if designation is not None:
                page_designation = designation
                index += width
            else:
                index += 1
        if page_designation is not None:
            current_model, current_configuration = page_designation
            current_identity = _identity(current_model, current_configuration)

        measurements = []
        for line in lines:
            for match in _CATALOG_MEASUREMENT.findall(line):
                clean = " ".join(match.replace(",", ".").split())
                if clean not in measurements:
                    measurements.append(clean)
        if current_identity and (measurements or page_designation is not None):
            record = records.setdefault(
                current_identity,
                {
                    "model": current_model,
                    "configuration": current_configuration,
                    "category": category,
                    "measurements": [],
                    "references": [],
                },
            )
            if category and not record["category"]:
                record["category"] = category
            for measurement in measurements:
                if measurement not in record["measurements"]:
                    record["measurements"].append(measurement)
            reference = source_ref(source.sha256, page.number, (0, 0, 0, 0))
            if reference not in record["references"]:
                record["references"].append(reference)
    return records


def _canonical_references(*groups) -> list[dict]:
    unique = {}
    for group in groups:
        for reference in group or ():
            encoded = json.dumps(
                reference, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            unique[encoded] = reference
    return [unique[key] for key in sorted(unique)]


def _price_reference(record) -> dict:
    source = record.source
    if isinstance(source, LumbroInterconnectionSource):
        return source_ref(source.file_id, source.sheet, source.price_cell)
    return source_ref(source.file_id, source.page, (0, 0, 0, 0))


def _row_audit_reference(record) -> dict:
    source = record.source
    if isinstance(source, LumbroInterconnectionSource):
        return {
            "path": source.path,
            "sheet_or_page": source.sheet,
            "cell_or_bbox": source.price_cell,
        }
    return {
        "path": source.path,
        "sheet_or_page": source.page,
        "cell_or_bbox": [0, 0, 0, 0],
    }


def _price_source_metadata(record) -> dict:
    source = record.source
    if isinstance(source, LumbroInterconnectionSource):
        return {
            "authority_rank": record.authority_rank,
            "cell": source.price_cell,
            "path": source.path,
            "sheet": source.sheet,
        }
    return {
        "authority_rank": record.authority_rank,
        "page": source.page,
        "path": source.path,
    }


def _slug(value: object) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", _fold(value)))


def _json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _direct_internal_id(record) -> str:
    if isinstance(record, LumbroInterconnectionRecord):
        return record.internal_id
    material = "\0".join(
        (
            record.identity,
            _fold(getattr(record, "color", "")),
            format(record.net_price, "f") if record.net_price is not None else "",
            record.source.path,
            str(record.source.page),
        )
    )
    return "lumbro:price:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _item(
    *,
    internal_id: str,
    source_code: str,
    model: str,
    configuration: str,
    color: str,
    description: str,
    dimensions: str,
    mounting: str,
    notes,
    spec_price_evidence,
    price_record,
    technical,
    warnings,
    references,
) -> dict:
    price = price_record.net_price if price_record is not None else None
    item_warnings = list(dict.fromkeys(warnings))
    if not source_code:
        item_warnings.append("Código oficial por verificar; variante no cotizable.")
    if price is None:
        item_warnings.append("Precio comercial por verificar; variante no cotizable.")
    name = " ".join(value for value in (model, configuration, color) if value).strip()
    if not name:
        name = description or "Producto Lumbro por revisar"
    collection = technical.get("category", "") if technical else ""
    link = resolve_lumbro_link(model, collection)
    link_metadata = dict(link.metadata)
    link_metadata["label"] = (
        "Ver producto" if link.status == "exact_index" else "Ver catálogo Lumbro"
    )
    attributes = {
        "source_code": source_code,
        "model": model,
        "configuration": configuration,
        "color": color,
        "dimensions": dimensions,
        "mounting": mounting,
        "product_notes": list(notes),
        "spec_price_evidence": _json_value(spec_price_evidence),
        "catalog_measurements": list(technical.get("measurements", ())) if technical else [],
        "price_source": _price_source_metadata(price_record) if price_record else {},
        "product_url_match": link_metadata,
    }
    product_key_material = source_code or _identity(model, configuration) or internal_id
    return {
        "internal_id": internal_id,
        "supplier": "lumbro",
        "product_key": f"lumbro:{_slug(product_key_material) or internal_id[-20:]}",
        "sku": "",
        "code_status": "needs_review",
        "brand": "Lumbro",
        "collection": collection,
        "name": name,
        "description": description,
        "unit": "PZA",
        "availability_type": "unknown",
        "stock": None,
        "lead_time": "",
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": "MXN",
        "price_net": f"{(price or Decimal(0)):.6f}",
        "tax_rate": "0.160000",
        "attributes": attributes,
        "image_url": "",
        "image_kind": "placeholder",
        "product_url": link.url,
        "warnings": item_warnings,
        "source_reference": json.dumps(
            _canonical_references(references, technical.get("references", ()) if technical else ()),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    }


def _selection(entries):
    valid = [
        entry
        for entry in entries
        if entry["record"].parse_status == "parsed"
        and entry["record"].net_price is not None
    ]
    if not valid:
        return None, (), ()
    authority = max(entry["record"].authority_rank for entry in valid)
    authoritative = [
        entry for entry in valid if entry["record"].authority_rank == authority
    ]
    values = {entry["record"].net_price for entry in authoritative}
    if len(values) != 1:
        return None, (), tuple(valid)
    selected = sorted(
        authoritative,
        key=lambda entry: json.dumps(
            _row_audit_reference(entry["record"]), sort_keys=True, separators=(",", ":")
        ),
    )[0]
    rejected = tuple(entry for entry in valid if entry is not selected)
    return selected, tuple(authoritative), rejected


def _compatible_variant(spec_record, price_record) -> bool:
    if _fold(spec_record.model) != _fold(price_record.model):
        return False
    if _fold(spec_record.configuration) != _fold(price_record.configuration):
        return False
    price_color = _fold(getattr(price_record, "color", ""))
    return not price_color or price_color == _fold(spec_record.color)


def _finalize_official_skus(items) -> None:
    code_counts = defaultdict(int)
    for item in items:
        code = _plain(item["attributes"]["source_code"])
        if code:
            code_counts[_fold(code)] += 1

    for item in items:
        code = _plain(item["attributes"]["source_code"])
        has_price = Decimal(item["price_net"]) > 0
        if code and has_price and code_counts[_fold(code)] == 1:
            item["sku"] = code
            item["code_status"] = "verified"
        elif code and code_counts[_fold(code)] > 1:
            item["warnings"].append(
                "C\u00f3digo oficial no es \u00fanico entre variantes; variante no cotizable."
            )


def _spec_references(record: LumbroSpecRecord) -> list[dict]:
    return _canonical_references(
        *(record.provenance[key] for key in sorted(record.provenance))
    )


def _build_lumbro(files, *, include_assets: bool):
    bundle = _validated_lumbro_bundle(files)
    ordered_files = tuple(bundle[path] for path in _SOURCE_CONTRACT)
    pdf_records = parse_lumbro_pdf_prices(
        (bundle[_GENERAL_PATH], bundle[_NEW_PATH])
    )
    spec_build = parse_lumbro_spec_guide(bundle[_SPEC_PATH])
    interconnection_build = parse_lumbro_interconnection(
        bundle[_INTERCONNECTION_PATH]
    )
    technical_by_identity = _parse_lumbro_catalog(bundle[_CATALOG_PATH])
    commercial = [
        {"record": record, "kind": "pdf", "disposition": None, "final_ids": []}
        for record in pdf_records
    ] + [
        {
            "record": record,
            "kind": "interconnection",
            "disposition": None,
            "final_ids": [],
        }
        for record in interconnection_build.records
    ]

    pdf_entries_by_identity = defaultdict(list)
    inter_entries_by_code = defaultdict(list)
    for entry in commercial:
        record = entry["record"]
        if entry["kind"] == "pdf" and record.identity:
            pdf_entries_by_identity[record.identity].append(entry)
        if entry["kind"] == "interconnection" and record.code:
            inter_entries_by_code[record.code].append(entry)

    resolution_cache = {}
    items = []
    item_by_id = {}
    for record in spec_build.records:
        resolution_key = (record.code, record.price_identity, _fold(record.color))
        if resolution_key not in resolution_cache:
            candidates = [
                *inter_entries_by_code.get(record.code, ()),
                *pdf_entries_by_identity.get(record.price_identity, ()),
            ]
            candidates = [
                entry
                for entry in candidates
                if _compatible_variant(record, entry["record"])
            ]
            selected, _, rejected = _selection(candidates)
            resolution_cache[resolution_key] = selected
            if selected is not None and selected["disposition"] is None:
                selected["disposition"] = ("reconciled", "exact_compatible_identity")
            for rejected_entry in rejected:
                if rejected_entry["disposition"] is None:
                    reason = (
                        "lower_authority"
                        if selected is not None
                        and rejected_entry["record"].authority_rank
                        < selected["record"].authority_rank
                        else "redundant_same_authority"
                    )
                    rejected_entry["disposition"] = ("excluded", reason)
        selected = resolution_cache[resolution_key]
        if selected is not None:
            selected["final_ids"].append(record.internal_id)
        technical = technical_by_identity.get(
            _identity(record.model, record.configuration), {}
        )
        references = _spec_references(record)
        if selected is not None:
            references.append(_price_reference(selected["record"]))
        item = _item(
            internal_id=record.internal_id,
            source_code=record.code,
            model=record.model,
            configuration=record.configuration,
            color=record.color,
            description=record.description,
            dimensions=record.dimensions,
            mounting=record.mounting,
            notes=record.notes,
            spec_price_evidence=record.spec_price_evidence,
            price_record=selected["record"] if selected else None,
            technical=technical,
            warnings=record.warnings,
            references=references,
        )
        items.append(item)
        item_by_id[item["internal_id"]] = item

    remaining_groups = defaultdict(list)
    for entry in commercial:
        if entry["disposition"] is not None:
            continue
        record = entry["record"]
        if record.parse_status == "needs_review":
            internal_id = _direct_internal_id(record)
            technical = technical_by_identity.get(record.identity, {})
            references = [
                _price_reference(record),
                *(
                    reference
                    for values in getattr(record, "provenance", {}).values()
                    for reference in values
                ),
            ]
            item = _item(
                internal_id=internal_id,
                source_code="",
                model=record.model,
                configuration=record.configuration,
                color=getattr(record, "color", ""),
                description=getattr(record, "description", ""),
                dimensions="",
                mounting="",
                notes=(),
                spec_price_evidence=None,
                price_record=record if record.net_price is not None else None,
                technical=technical,
                warnings=record.warnings,
                references=references,
            )
            items.append(item)
            item_by_id[internal_id] = item
            entry["disposition"] = ("imported", "visible_needs_review")
            entry["final_ids"].append(internal_id)
            continue
        key = (
            f"code:{record.code}"
            if getattr(record, "code", "")
            else f"identity:{record.identity}"
        )
        key = (key, _fold(getattr(record, "color", "")))
        remaining_groups[key].append(entry)

    for entries in remaining_groups.values():
        selected, _, rejected = _selection(entries)
        if selected is None:
            for entry in entries:
                entry["disposition"] = ("excluded", "ambiguous_price")
            continue
        record = selected["record"]
        internal_id = _direct_internal_id(record)
        technical = technical_by_identity.get(record.identity, {})
        references = [
            _price_reference(record),
            *(
                reference
                for values in getattr(record, "provenance", {}).values()
                for reference in values
            ),
        ]
        item = _item(
            internal_id=internal_id,
            source_code=getattr(record, "code", ""),
            model=record.model,
            configuration=record.configuration,
            color=getattr(record, "color", ""),
            description=getattr(record, "description", ""),
            dimensions="",
            mounting="",
            notes=(),
            spec_price_evidence=None,
            price_record=record,
            technical=technical,
            warnings=record.warnings,
            references=references,
        )
        items.append(item)
        item_by_id[internal_id] = item
        selected["disposition"] = ("imported", "standalone_commercial_row")
        selected["final_ids"].append(internal_id)
        for entry in rejected:
            if entry["disposition"] is None:
                reason = (
                    "lower_authority"
                    if entry["record"].authority_rank < record.authority_rank
                    else "redundant_same_authority"
                )
                entry["disposition"] = ("excluded", reason)

    for entry in commercial:
        if entry["disposition"] is None:
            entry["disposition"] = ("excluded", "unresolved_commercial_row")

    _finalize_official_skus(items)

    bindings_by_id = defaultdict(list)
    for binding in spec_build.bindings:
        if binding.internal_id in item_by_id:
            bindings_by_id[binding.internal_id].append(
                (binding, spec_build.assets_by_sha256.get(binding.asset_sha256))
            )
    inter_entry_by_id = {
        entry["record"].internal_id: entry
        for entry in commercial
        if entry["kind"] == "interconnection"
    }
    for binding in interconnection_build.bindings:
        entry = inter_entry_by_id.get(binding.internal_id)
        if entry is None:
            continue
        targets = tuple(dict.fromkeys(entry["final_ids"]))
        if len(targets) != 1:
            continue
        target = targets[0]
        if target in item_by_id:
            bindings_by_id[target].append(
                (
                    replace(binding, internal_id=target),
                    interconnection_build.assets_by_sha256.get(binding.asset_sha256),
                )
            )

    final_assets = {}
    final_bindings = []
    if include_assets:
        for internal_id in sorted(bindings_by_id):
            candidates = bindings_by_id[internal_id]
            by_sha256 = defaultdict(list)
            for binding, asset in candidates:
                if asset is not None:
                    by_sha256[binding.asset_sha256].append((binding, asset))
            if len(by_sha256) != 1:
                if len(by_sha256) > 1:
                    item = item_by_id[internal_id]
                    item["warnings"].append(
                        "Conflicto de imágenes oficiales; se usa placeholder."
                    )
                    item["attributes"]["image_conflict"] = sorted(by_sha256)
                continue
            asset_sha256, same_asset = next(iter(by_sha256.items()))
            binding, asset = sorted(
                same_asset,
                key=lambda pair: (pair[0].match_status == "family_xlsx", pair[0].match_status),
            )[0]
            references = _canonical_references(
                *(candidate.source_references for candidate, _ in same_asset)
            )
            final_binding = CatalogAssetBinding(
                internal_id=internal_id,
                asset_sha256=asset_sha256,
                object_name=f"{asset_sha256}.png",
                image_kind="official",
                match_status=binding.match_status,
                source_references=tuple(references),
            )
            final_assets[asset_sha256] = asset
            final_bindings.append(final_binding)
            item = item_by_id[internal_id]
            item["image_kind"] = "official"
            item["attributes"]["image_match"] = {
                "status": final_binding.match_status,
                "asset_sha256": asset_sha256,
                "source_references": references,
            }
            item["attributes"]["approved_asset"] = {
                "bucket": "catalog-assets",
                "path": final_binding.object_name,
                "image_kind": "official",
                "label": "Imagen oficial de fuente Lumbro verificada",
                "approved": True,
            }

    exclusions = []
    counts = {"imported": 0, "reconciled": 0, "excluded": 0}
    for entry in commercial:
        status, reason = entry["disposition"]
        counts[status] += 1
        if status == "excluded":
            record = entry["record"]
            exclusions.append(
                {
                    **_row_audit_reference(record),
                    "identity": record.identity,
                    "model": record.model,
                    "configuration": record.configuration,
                    "color": getattr(record, "color", ""),
                    "price_net": (
                        f"{record.net_price:.6f}"
                        if record.net_price is not None
                        else ""
                    ),
                    "parse_status": record.parse_status,
                    "reason": reason,
                }
            )
    exclusions.sort(
        key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
    )
    items.sort(key=lambda item: item["internal_id"])
    final_bindings.sort(key=lambda binding: binding.internal_id)
    coverage = {
        "parsed_price_rows": len(commercial),
        "imported_rows": counts["imported"],
        "reconciled_rows": counts["reconciled"],
        "excluded_rows": counts["excluded"],
        "exclusions": exclusions,
        "items": len(items),
        "verified_items": sum(item["code_status"] == "verified" for item in items),
        "needs_review_items": sum(item["code_status"] == "needs_review" for item in items),
        "priced_items": sum(Decimal(item["price_net"]) > 0 for item in items),
        "assets": len(final_assets),
        "bindings": len(final_bindings),
        "catalog_enriched_items": sum(bool(item["collection"]) for item in items),
    }
    link_index = load_lumbro_link_index()
    snapshot = {
        "supplier": "lumbro",
        "source_hash": _source_hash(ordered_files),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
        "metadata": {
            "sources": _source_descriptors(ordered_files),
            "link_manifest_fingerprint": link_index.resource_fingerprint,
            "coverage": coverage,
        },
    }
    if include_assets:
        return CatalogSnapshotBuild(snapshot, final_assets, tuple(final_bindings))
    return snapshot


def build_lumbro_snapshot(files) -> dict:
    return _build_lumbro(files, include_assets=False)


def build_lumbro_snapshot_with_assets(files) -> CatalogSnapshotBuild:
    return _build_lumbro(files, include_assets=True)
