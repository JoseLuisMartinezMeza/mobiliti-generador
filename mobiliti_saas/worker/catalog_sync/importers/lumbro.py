from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Mapping
from xml.etree import ElementTree

from . import common as _common
from .common import (
    CatalogAssetBinding,
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
    _GENERAL_PATH: 3,
    _NEW_PATH: 2,
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
    prices_by_identity: dict[str, set[Decimal]] = {}
    for record in records:
        if record.identity and record.net_price is not None:
            prices_by_identity.setdefault(record.identity, set()).add(record.net_price)
    conflicts = {
        identity for identity, prices in prices_by_identity.items() if len(prices) > 1
    }
    return tuple(
        replace(
            record,
            parse_status="needs_review",
            warnings=record.warnings + ("conflicting_price",),
        )
        if record.identity in conflicts and "conflicting_price" not in record.warnings
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
