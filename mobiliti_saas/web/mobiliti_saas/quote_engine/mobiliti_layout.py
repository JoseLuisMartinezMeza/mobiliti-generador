"""Planificación pura de las filas dinámicas de la hoja Mobiliti."""

from dataclasses import dataclass
from typing import Mapping, Sequence


BASE_SECTION_COUNT = 16
BASE_PRODUCT_CAPACITY = 33
BASE_FIRST_SECTION_ROW = 13
BASE_BLOCK_HEIGHT = 35
XLSX_MAX_ROWS = 1_048_576
RESERVED_ROWS_AFTER_TOTAL = 64


@dataclass(frozen=True)
class SectionNeed:
    """Productos que se deben reservar para una categoría de Mobiliti."""

    id: str
    title: str
    item_count: int

    def __post_init__(self) -> None:
        if type(self.item_count) is not int:
            raise TypeError("La cantidad de productos debe ser un entero")
        if self.item_count < 0:
            raise ValueError("La cantidad de productos no puede ser negativa")


@dataclass(frozen=True)
class SectionLayout:
    """Ubicación y capacidad calculada para una sección de Mobiliti."""

    id: str
    title: str
    section_row: int
    product_start: int
    capacity: int
    item_count: int
    subtotal_row: int


@dataclass(frozen=True)
class MobilitiRowMap:
    """Mapa de filas que usarán los pasos OOXML posteriores."""

    sections: tuple[SectionLayout, ...]
    total_row: int
    last_product_row: int
    product_ranges: tuple[tuple[int, int], ...]
    subtotal_rows: tuple[int, ...]
    row_translation: Mapping[int, tuple[int, ...]]

    @property
    def item_rows(self) -> tuple[int, ...]:
        """Filas usadas, en el mismo orden que los ítems de entrada."""

        return tuple(
            row
            for section in self.sections
            for row in range(section.product_start, section.product_start + section.item_count)
        )

    @classmethod
    def from_sections(
        cls, sections: Sequence[SectionLayout], total_row: int
    ) -> "MobilitiRowMap":
        frozen = tuple(sections)
        return cls(
            sections=frozen,
            total_row=total_row,
            last_product_row=max(
                section.product_start + section.capacity - 1 for section in frozen
            ),
            product_ranges=tuple(
                (section.product_start, section.capacity) for section in frozen
            ),
            subtotal_rows=tuple(section.subtotal_row for section in frozen),
            row_translation=build_row_translation(frozen, total_row),
        )


def build_row_translation(
    sections: Sequence[SectionLayout], total_row: int
) -> Mapping[int, tuple[int, ...]]:
    """Relaciona las filas modelo oficiales con cada fila creada."""

    product_rows = tuple(
        row
        for section in sections
        for row in range(section.product_start, section.product_start + section.capacity)
    )
    return {
        13: tuple(section.section_row for section in sections),
        14: product_rows,
        47: tuple(section.subtotal_row for section in sections),
        573: (total_row,),
    }


def plan_mobiliti_layout(needs: Sequence[SectionNeed]) -> MobilitiRowMap:
    """Calcula un layout sin topes comerciales; solo respeta XLSX físico."""

    visible_count = max(BASE_SECTION_COUNT, len(needs))
    minimum_total_row = BASE_FIRST_SECTION_ROW + visible_count * (
        BASE_PRODUCT_CAPACITY + 2
    )
    if minimum_total_row + RESERVED_ROWS_AFTER_TOTAL > XLSX_MAX_ROWS:
        raise ValueError("La cotizacion excede la capacidad física de XLSX")

    cursor = BASE_FIRST_SECTION_ROW
    sections: list[SectionLayout] = []

    for index in range(visible_count):
        need = (
            needs[index]
            if index < len(needs)
            else SectionNeed(f"unused-{index}", "NOMBRE", 0)
        )
        capacity = max(BASE_PRODUCT_CAPACITY, need.item_count)
        product_start = cursor + 1
        subtotal_row = product_start + capacity
        next_cursor = subtotal_row + 1
        if next_cursor + RESERVED_ROWS_AFTER_TOTAL > XLSX_MAX_ROWS:
            raise ValueError("La cotizacion excede la capacidad física de XLSX")
        sections.append(
            SectionLayout(
                id=need.id,
                title=need.title,
                section_row=cursor,
                product_start=product_start,
                capacity=capacity,
                item_count=need.item_count,
                subtotal_row=subtotal_row,
            )
        )
        cursor = next_cursor

    total_row = cursor
    if total_row + RESERVED_ROWS_AFTER_TOTAL > XLSX_MAX_ROWS:
        raise ValueError("La cotizacion excede la capacidad física de XLSX")

    return MobilitiRowMap.from_sections(sections, total_row=total_row)
