"""Planificación pura de las filas dinámicas de la hoja Mobiliti."""

from dataclasses import dataclass
from typing import Mapping, Sequence


BASE_SECTION_COUNT = 16
BASE_PRODUCT_CAPACITY = 33
BASE_FIRST_SECTION_ROW = 13
BASE_BLOCK_HEIGHT = 35
CANONICAL_AUXILIARY_ROW_COUNT = 37
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
    canonical_auxiliary_row_count: int

    @property
    def item_rows(self) -> tuple[int, ...]:
        """Filas usadas, en el mismo orden que los ítems de entrada."""

        return tuple(
            row
            for section in self.sections
            for row in range(section.product_start, section.product_start + section.item_count)
        )

    @property
    def canonical_first_section_row(self) -> int:
        return self.sections[0].section_row

    @property
    def canonical_first_product_row(self) -> int:
        return self.canonical_first_section_row + 1

    @property
    def canonical_first_subtotal_row(self) -> int:
        return self.canonical_first_product_row + BASE_PRODUCT_CAPACITY

    @property
    def canonical_total_row(self) -> int:
        return self.canonical_first_section_row + BASE_SECTION_COUNT * BASE_BLOCK_HEIGHT

    @property
    def canonical_last_product_row(self) -> int:
        return self.canonical_total_row - 2

    @property
    def canonical_subtotal_rows(self) -> tuple[int, ...]:
        return tuple(
            range(
                self.canonical_first_subtotal_row,
                self.canonical_total_row,
                BASE_BLOCK_HEIGHT,
            )
        )

    @property
    def canonical_auxiliary_start(self) -> int:
        return self.canonical_total_row + 1

    @property
    def canonical_auxiliary_end(self) -> int:
        return self.canonical_total_row + self.canonical_auxiliary_row_count

    @classmethod
    def from_sections(
        cls,
        sections: Sequence[SectionLayout],
        total_row: int,
        canonical_auxiliary_row_count: int = CANONICAL_AUXILIARY_ROW_COUNT,
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
            canonical_auxiliary_row_count=canonical_auxiliary_row_count,
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
    first_section_row = sections[0].section_row
    first_product_row = first_section_row + 1
    first_subtotal_row = first_product_row + BASE_PRODUCT_CAPACITY
    canonical_total_row = first_section_row + BASE_SECTION_COUNT * BASE_BLOCK_HEIGHT
    return {
        first_section_row: tuple(section.section_row for section in sections),
        first_product_row: product_rows,
        first_subtotal_row: tuple(section.subtotal_row for section in sections),
        canonical_total_row: (total_row,),
    }


def plan_mobiliti_layout(
    needs: Sequence[SectionNeed],
    *,
    first_section_row: int = BASE_FIRST_SECTION_ROW,
    canonical_auxiliary_row_count: int = CANONICAL_AUXILIARY_ROW_COUNT,
) -> MobilitiRowMap:
    """Calcula un layout sin topes comerciales; solo respeta XLSX físico."""

    if type(first_section_row) is not int or first_section_row < 1:
        raise ValueError("La primera fila de sección Mobiliti es inválida")
    if (
        type(canonical_auxiliary_row_count) is not int
        or canonical_auxiliary_row_count < 0
    ):
        raise ValueError("La cantidad de filas auxiliares Mobiliti es inválida")

    visible_count = max(BASE_SECTION_COUNT, len(needs))
    minimum_total_row = first_section_row + visible_count * (
        BASE_PRODUCT_CAPACITY + 2
    )
    if minimum_total_row + RESERVED_ROWS_AFTER_TOTAL > XLSX_MAX_ROWS:
        raise ValueError("La cotizacion excede la capacidad física de XLSX")

    cursor = first_section_row
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

    return MobilitiRowMap.from_sections(
        sections,
        total_row=total_row,
        canonical_auxiliary_row_count=canonical_auxiliary_row_count,
    )
