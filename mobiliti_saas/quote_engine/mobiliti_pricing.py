"""Entradas numÃ©ricas de precios para la hoja oficial ``Mobiliti``.

Este mÃ³dulo no convierte costos canÃ³nicos por segunda vez. El producto entre
precio original y tipo congelado se calcula Ãºnicamente para comprobar el
invariante; la celda ``J`` recibe siempre ``QuotationDataRow.converted_cost``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import unicodedata
from typing import Sequence

from .mobiliti_layout import MobilitiRowMap
from .ooxml_worksheet import MobilitiCellWrite, WorksheetEditor
from .quotation_sheets import QuotationDataRow


CENT = Decimal("0.01")
MAX_NUMERIC_SCALE = 6
MAX_NUMERIC_INTEGRAL_DIGITS = 12
MAX_EXCEL_CELL_TEXT_LENGTH = 32_767
QUOTE_CURRENCIES = frozenset(("MXN", "USD", "EUR"))


def build_mobiliti_pricing_writes(
    rows: Sequence[QuotationDataRow],
    row_map: MobilitiRowMap,
) -> tuple[MobilitiCellWrite, ...]:
    """Mapea cada costo canÃ³nico, una sola vez y en orden, a ``Mobiliti!J``."""

    if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
        raise TypeError("Las filas canÃ³nicas de precios deben ser una secuencia")
    if not isinstance(row_map, MobilitiRowMap):
        raise TypeError("El mapa de filas Mobiliti es invÃ¡lido")

    item_rows = row_map.item_rows
    if len(rows) != len(item_rows):
        raise ValueError(
            "La cantidad de costos canÃ³nicos no coincide con las filas Mobiliti"
        )
    if len(item_rows) != len(set(item_rows)):
        raise ValueError("El mapa contiene filas Mobiliti duplicadas")

    seen_keys: set[str] = set()
    writes: list[MobilitiCellWrite] = []
    for position, (canonical, target_row) in enumerate(
        zip(rows, item_rows, strict=True), start=1
    ):
        if not isinstance(canonical, QuotationDataRow):
            raise TypeError("Fila canÃ³nica de precio invÃ¡lida")
        if canonical.position != position:
            raise ValueError("Orden de costos canÃ³nicos inconsistente")
        if not isinstance(canonical.item_key, str) or not canonical.item_key:
            raise ValueError("Clave canÃ³nica de precio invÃ¡lida")
        if canonical.item_key in seen_keys:
            raise ValueError("Clave canÃ³nica de precio duplicada")
        seen_keys.add(canonical.item_key)

        original = _numeric_18_6(
            canonical.original_cost,
            "original_cost",
            positive=False,
        )
        rate = _numeric_18_6(
            canonical.frozen_rate,
            "frozen_rate",
            positive=True,
        )
        converted = _numeric_18_6(
            canonical.converted_cost,
            "converted_cost",
            positive=False,
        )
        expected = _converted_cost(original, rate)
        _numeric_18_6(expected, "converted_cost", positive=False)
        if converted != expected:
            raise ValueError("Costo convertido canÃ³nico inconsistente")

        # No se usa ``expected`` como salida: converted_cost ya estÃ¡ congelado.
        writes.append(MobilitiCellWrite(f"J{target_row}", "number", converted))

    return tuple(writes)


def lumbro_frozen_cost(
    original_mxn: Decimal | None,
    frozen_rate: Decimal,
    *,
    missing_price_is_zero: bool = False,
) -> Decimal:
    """Congela en Python un accesorio Lumbro; nunca produce una fÃ³rmula Excel.

    La ausencia de precio falla cerrada. El llamador puede declarar de manera
    explÃ­cita que su contrato representa esa ausencia como cero.
    """

    if type(missing_price_is_zero) is not bool:
        raise TypeError("El contrato de precio cero Lumbro debe ser booleano")
    if original_mxn is None:
        if not missing_price_is_zero:
            raise ValueError("Precio Lumbro ausente")
        original_mxn = Decimal("0")

    original = _numeric_18_6(original_mxn, "precio Lumbro", positive=False)
    rate = _numeric_18_6(frozen_rate, "tipo congelado Lumbro", positive=True)
    result = _converted_cost(original, rate)
    _numeric_18_6(result, "costo congelado Lumbro", positive=False)
    return result


def write_official_currency_selector(
    editor: WorksheetEditor,
    quote_currency: str,
    delivery_place: str,
) -> None:
    """Escribe exclusivamente el selector oficial ``K4`` y el texto ``K8``."""

    if not isinstance(editor, WorksheetEditor):
        raise TypeError("Editor Mobiliti invÃ¡lido")
    if not isinstance(quote_currency, str):
        raise TypeError("Moneda de cotizacion invÃ¡lida")
    if quote_currency not in QUOTE_CURRENCIES:
        raise ValueError("Moneda de cotizacion invÃ¡lida")
    safe_place = _safe_k8_text(delivery_place)

    # Toda la validaciÃ³n ocurre antes de la primera mutaciÃ³n.
    editor.set_boolean("K4", quote_currency != "MXN")
    editor.set_inline_string("K8", safe_place)


def _numeric_18_6(
    value: object,
    field_name: str,
    *,
    positive: bool,
) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{field_name} debe ser Decimal y no bool")
    if not value.is_finite():
        raise ValueError(f"{field_name} debe ser finito")
    if (positive and value <= 0) or (not positive and value < 0):
        comparison = "mayor que cero" if positive else "no negativo"
        raise ValueError(f"{field_name} debe ser {comparison}")

    _sign, digits, exponent = value.as_tuple()
    scale = max(-exponent, 0)
    integral_digits = max(value.adjusted() + 1, 1) if not value.is_zero() else 1
    if (
        scale > MAX_NUMERIC_SCALE
        or integral_digits > MAX_NUMERIC_INTEGRAL_DIGITS
        or len(digits) > MAX_NUMERIC_SCALE + MAX_NUMERIC_INTEGRAL_DIGITS
    ):
        raise ValueError(f"{field_name} excede NUMERIC(18,6)")
    return value


def _converted_cost(original: Decimal, rate: Decimal) -> Decimal:
    try:
        with localcontext() as context:
            context.prec = 64
            return (original * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError("Costo convertido excede NUMERIC(18,6)") from error


def _safe_k8_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("K8 requiere texto")
    if any(not _is_xml_10_character(ord(character)) for character in value):
        raise ValueError("K8 contiene caracteres invÃ¡lidos para XML 1.0")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    if text.lstrip()[:1] in {"=", "+", "-", "@"}:
        text = "'" + text
    if len(text) > MAX_EXCEL_CELL_TEXT_LENGTH:
        raise ValueError("K8 excede el lÃ­mite de 32767 caracteres de Excel")
    return text


def _is_xml_10_character(codepoint: int) -> bool:
    return (
        codepoint in {0x9, 0xA, 0xD}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )
