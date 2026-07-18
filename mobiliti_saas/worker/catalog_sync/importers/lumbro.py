from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from .common import iter_pdf_pages, validate_source_file


_GENERAL_PATH = "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf"
_NEW_PATH = "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf"
_EXPECTED_PDFS = {
    _GENERAL_PATH: 3,
    _NEW_PATH: 2,
}
_PDF_MIME = "application/pdf"
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
