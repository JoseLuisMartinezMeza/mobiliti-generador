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
