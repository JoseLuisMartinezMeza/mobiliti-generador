import pytest

from mobiliti_saas.quote_engine.mobiliti_layout import (
    BASE_BLOCK_HEIGHT,
    SectionNeed,
    plan_mobiliti_layout,
)


def test_official_base_block_height_is_preserved_as_layout_metadata():
    assert BASE_BLOCK_HEIGHT == 35


@pytest.mark.parametrize(
    ("counts", "expected_sections", "expected_first_subtotal"),
    [([34], 16, 48), ([100], 16, 114), ([1] * 17, 17, 47), ([40] * 20, 20, 54)],
)
def test_layout_expands_without_business_caps(
    counts, expected_sections, expected_first_subtotal
):
    layout = plan_mobiliti_layout(
        [SectionNeed(f"s-{index}", f"S{index}", count) for index, count in enumerate(counts)]
    )

    assert len(layout.sections) == expected_sections
    assert layout.sections[0].subtotal_row == expected_first_subtotal
    assert sum(section.item_count for section in layout.sections[: len(counts)]) == sum(counts)
    assert layout.total_row < 1_048_576


def test_layout_rejects_only_real_xlsx_overflow():
    with pytest.raises(ValueError, match="capacidad f.sica de XLSX"):
        plan_mobiliti_layout([SectionNeed("huge", "Huge", 1_048_576)])


def test_empty_needs_keep_the_official_sixteen_section_baseline():
    layout = plan_mobiliti_layout([])

    assert len(layout.sections) == 16
    assert layout.total_row == 573


@pytest.mark.parametrize("invalid_count", [-1, True, 1.0])
def test_section_need_rejects_negative_or_non_integer_item_counts(invalid_count):
    with pytest.raises((TypeError, ValueError)):
        SectionNeed("invalid", "Inválida", invalid_count)


class _TooManySections:
    def __len__(self):
        return 1_048_576

    def __getitem__(self, index):
        raise AssertionError("No debe materializar secciones después del preflight")


def test_layout_rejects_many_sections_before_building_a_massive_map():
    with pytest.raises(ValueError, match="capacidad f.sica de XLSX"):
        plan_mobiliti_layout(_TooManySections())


def test_layout_exposes_rows_for_later_official_template_steps():
    layout = plan_mobiliti_layout([SectionNeed("chairs", "Sillas", 34)])

    assert layout.item_rows == tuple(range(14, 48))
    assert layout.product_ranges[0] == (14, 34)
    assert layout.subtotal_rows[0] == 48
    assert layout.last_product_row == layout.sections[-1].subtotal_row - 1
    assert layout.row_translation[13][0] == 13
    assert layout.row_translation[14][:2] == (14, 15)
    assert layout.row_translation[47][0] == 48
    assert layout.row_translation[573] == (layout.total_row,)
