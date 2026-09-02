"""Entradas numéricas de precios para la hoja oficial ``Mobiliti``.

Este módulo no convierte costos canónicos por segunda vez. El producto entre
precio original y tipo congelado se calcula únicamente para comprobar el
invariante; la celda ``J`` recibe siempre ``QuotationDataRow.converted_cost``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import unicodedata
from typing import Sequence

from .mobiliti_layout import MobilitiRowMap
from .ooxml_worksheet import MobilitiCellWrite, WorksheetEditor
from .quotation_sheets import QuotationDataRow


CENT = Decimal("0.01")
NUMERIC_18_6_SCALE = 6
NUMERIC_18_6_INTEGRAL_DIGITS = 12
NUMERIC_18_2_SCALE = 2
NUMERIC_18_2_INTEGRAL_DIGITS = 16
MAX_EXCEL_CELL_TEXT_LENGTH = 32_767
QUOTE_CURRENCIES = frozenset(("MXN", "USD", "EUR"))


@dataclass(frozen=True)
class PricingRowBinding:
    """Identidad autoritativa esperada para una fila destino de ``Mobiliti``."""

    item_key: str
    section_id: str
    position: int
    target_row: int
    quotation_row: int | None = None
    quotation_rate: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not isinstance(self.item_key, str) or not self.item_key:
            raise ValueError("item_key de binding de precio inválido")
        if not isinstance(self.section_id, str) or not self.section_id:
            raise ValueError("section_id de binding de precio inválido")
        if type(self.position) is not int or self.position < 1:
            raise ValueError("position de binding de precio inválida")
        if type(self.target_row) is not int or self.target_row < 1:
            raise ValueError("target_row de binding de precio inválida")
        if self.quotation_row is not None and (
            type(self.quotation_row) is not int or self.quotation_row < 1
        ):
            raise ValueError("quotation_row de binding de precio invalida")
        if (
            isinstance(self.quotation_rate, bool)
            or not isinstance(self.quotation_rate, Decimal)
            or not self.quotation_rate.is_finite()
            or self.quotation_rate <= 0
        ):
            raise ValueError("quotation_rate de binding de precio invalida")


def build_mobiliti_pricing_writes(
    rows: Sequence[QuotationDataRow],
    row_map: MobilitiRowMap,
    *,
    bindings: Sequence[PricingRowBinding],
) -> tuple[MobilitiCellWrite, ...]:
    """Mapea cada costo canónico validando una identidad externa obligatoria."""

    if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
        raise TypeError("Las filas canónicas de precios deben ser una secuencia")
    if not isinstance(row_map, MobilitiRowMap):
        raise TypeError("El mapa de filas Mobiliti es inválido")
    if isinstance(bindings, (str, bytes, bytearray)) or not isinstance(
        bindings, Sequence
    ):
        raise TypeError("Los bindings autoritativos de precios deben ser una secuencia")

    layout_identities = _layout_identities(row_map)
    item_rows = tuple(target_row for _section_id, target_row in layout_identities)
    if len(rows) != len(item_rows) or len(bindings) != len(item_rows):
        raise ValueError(
            "La cantidad de costos canónicos, bindings y filas Mobiliti no coincide"
        )
    if len(item_rows) != len(set(item_rows)):
        raise ValueError("El mapa contiene filas Mobiliti duplicadas")

    seen_keys: set[str] = set()
    seen_binding_identities: set[tuple[str, str, int]] = set()
    writes: list[MobilitiCellWrite] = []
    for position, (canonical, binding, layout_identity) in enumerate(
        zip(rows, bindings, layout_identities, strict=True), start=1
    ):
        if not isinstance(canonical, QuotationDataRow):
            raise TypeError("Fila canónica de precio inválida")
        if not isinstance(binding, PricingRowBinding):
            raise TypeError("Binding autoritativo de precio inválido")
        expected_section_id, target_row = layout_identity
        if binding.target_row != target_row:
            raise ValueError("Identidad de precio inconsistente: target_row")
        if binding.section_id != expected_section_id:
            raise ValueError("Identidad de precio inconsistente: section_id del layout")
        if binding.position != position:
            raise ValueError("Identidad de precio inconsistente: position del binding")
        binding_identity = (
            binding.item_key,
            binding.section_id,
            binding.target_row,
        )
        if binding_identity in seen_binding_identities:
            raise ValueError("Identidad de binding de precio duplicada")
        seen_binding_identities.add(binding_identity)
        if canonical.position != position:
            raise ValueError("Identidad de precio inconsistente: position canónica")
        if not isinstance(canonical.item_key, str) or not canonical.item_key:
            raise ValueError("Clave canónica de precio inválida")
        if canonical.item_key in seen_keys:
            raise ValueError("Clave canónica de precio duplicada")
        seen_keys.add(canonical.item_key)
        if canonical.item_key != binding.item_key:
            raise ValueError("Identidad de precio inconsistente: item_key")
        if canonical.section_id != binding.section_id:
            raise ValueError("Identidad de precio inconsistente: section_id canónico")

        pending_values = (
            canonical.original_cost,
            canonical.frozen_rate,
            canonical.converted_cost,
        )
        pending_price = all(value is None for value in pending_values)
        if any(value is None for value in pending_values) and not pending_price:
            raise ValueError("Estado de precio canónico inconsistente")
        if pending_price:
            writes.append(
                MobilitiCellWrite(f"J{target_row}", "text", "Por confirmar")
            )
            continue

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
        converted = _numeric_18_2(
            canonical.converted_cost,
            "converted_cost",
            positive=False,
        )
        expected = _converted_cost(original, rate)
        _numeric_18_2(expected, "converted_cost", positive=False)
        if converted != expected:
            raise ValueError("Costo convertido canónico inconsistente")

        # No se usa ``expected`` como salida: converted_cost ya está congelado.
        if binding.quotation_row is None:
            writes.append(MobilitiCellWrite(f"J{target_row}", "number", converted))
        else:
            formula = f"=Quotation!K{binding.quotation_row}"
            if binding.quotation_rate != Decimal("1"):
                formula = (
                    f"=ROUND(Quotation!K{binding.quotation_row}"
                    f"*{format(binding.quotation_rate, 'f')},2)"
                )
            writes.append(
                MobilitiCellWrite(
                    f"J{target_row}",
                    "formula",
                    formula,
                )
            )

    return tuple(writes)


def lumbro_frozen_cost(
    original_mxn: Decimal,
    frozen_rate: Decimal,
) -> Decimal:
    """Congela en Python un accesorio Lumbro; nunca produce una fórmula Excel.

    La ausencia o ambigüedad siempre falla cerrada. Un cero legítimo se expresa
    únicamente como ``Decimal("0")``.
    """

    original = _numeric_18_6(original_mxn, "precio Lumbro", positive=False)
    rate = _numeric_18_6(frozen_rate, "tipo congelado Lumbro", positive=True)
    result = _converted_cost(original, rate)
    _numeric_18_2(result, "costo congelado Lumbro", positive=False)
    return result


def write_official_currency_selector(
    editor: WorksheetEditor,
    quote_currency: str,
    delivery_place: str,
    discount: Decimal | None = None,
    *,
    composer_variant: str = "official",
) -> None:
    """Escribe únicamente los selectores firmados del layout activo."""

    if not isinstance(editor, WorksheetEditor):
        raise TypeError("Editor Mobiliti inválido")
    if not isinstance(quote_currency, str):
        raise TypeError("Moneda de cotización inválida")
    if quote_currency not in QUOTE_CURRENCIES:
        raise ValueError("Moneda de cotización inválida")
    if composer_variant not in {
        "official",
        "official_v17",
        "sunon_cdmx_v1c",
    }:
        raise ValueError("Variante de compositor Mobiliti inválida")
    safe_place = _safe_delivery_text(delivery_place)
    if editor.layout.id in {"v17", "v18"}:
        writes = [
            MobilitiCellWrite("P4", "boolean", quote_currency != "MXN"),
        ]
        if composer_variant == "sunon_cdmx_v1c":
            writes.append(MobilitiCellWrite("P8", "text", safe_place))
        if discount is not None:
            # El porcentaje admite seis decimales; su fracción necesita ocho.
            validated_discount = _numeric_contract(
                discount,
                "descuento global",
                positive=False,
                scale_limit=NUMERIC_18_6_SCALE + 2,
                integral_digits_limit=NUMERIC_18_6_INTEGRAL_DIGITS,
            )
            if validated_discount > 1:
                raise ValueError("descuento global debe estar entre cero y uno")
            writes.append(
                MobilitiCellWrite(
                    f"AD{editor.layout.first_section_row}",
                    "number",
                    validated_discount,
                )
            )
        editor.set_typed_values(tuple(writes))
        return

    editor.set_typed_values(
        (
            MobilitiCellWrite("K4", "boolean", quote_currency != "MXN"),
            MobilitiCellWrite("K8", "text", safe_place),
        )
    )


def _layout_identities(row_map: MobilitiRowMap) -> tuple[tuple[str, int], ...]:
    identities = tuple(
        (section.id, target_row)
        for section in row_map.sections
        for target_row in range(
            section.product_start,
            section.product_start + section.item_count,
        )
    )
    if tuple(target_row for _section_id, target_row in identities) != row_map.item_rows:
        raise ValueError("Identidad de filas Mobiliti inconsistente")
    return identities


def _numeric_18_6(
    value: object,
    field_name: str,
    *,
    positive: bool,
) -> Decimal:
    return _numeric_contract(
        value,
        field_name,
        positive=positive,
        scale_limit=NUMERIC_18_6_SCALE,
        integral_digits_limit=NUMERIC_18_6_INTEGRAL_DIGITS,
    )


def _numeric_18_2(
    value: object,
    field_name: str,
    *,
    positive: bool,
) -> Decimal:
    return _numeric_contract(
        value,
        field_name,
        positive=positive,
        scale_limit=NUMERIC_18_2_SCALE,
        integral_digits_limit=NUMERIC_18_2_INTEGRAL_DIGITS,
    )


def _numeric_contract(
    value: object,
    field_name: str,
    *,
    positive: bool,
    scale_limit: int,
    integral_digits_limit: int,
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
        scale > scale_limit
        or integral_digits > integral_digits_limit
        or len(digits) > scale_limit + integral_digits_limit
    ):
        raise ValueError(
            f"{field_name} excede NUMERIC(18,{scale_limit})"
        )
    return value


def _converted_cost(original: Decimal, rate: Decimal) -> Decimal:
    try:
        with localcontext() as context:
            context.prec = 64
            return (original * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError("Costo convertido excede NUMERIC(18,2)") from error


def _safe_delivery_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Lugar de entrega requiere texto")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    if any(unicodedata.category(character) == "Cf" for character in text):
        raise ValueError(
            "Lugar de entrega contiene caracteres invisibles o de formato inseguros"
        )
    if text.lstrip()[:1] in {"=", "+", "-", "@"}:
        text = "'" + text
    if any(not _is_xml_10_character(ord(character)) for character in text):
        raise ValueError("Lugar de entrega contiene caracteres inválidos para XML 1.0")
    if len(text) > MAX_EXCEL_CELL_TEXT_LENGTH:
        raise ValueError("Lugar de entrega excede el límite de 32767 caracteres de Excel")
    return text


def _is_xml_10_character(codepoint: int) -> bool:
    return (
        codepoint in {0x9, 0xA, 0xD}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )
