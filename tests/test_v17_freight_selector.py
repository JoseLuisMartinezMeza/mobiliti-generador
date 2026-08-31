"""El flete v17 consulta el checkbox vigente y conserva el factor oficial."""

from xml.etree import ElementTree as ET

import pytest

from mobiliti_saas.quote_engine.official_composer import (
    LEGACY_COTIZACION_LAYOUT,
    V17_COTIZACION_LAYOUT,
    _shift_cotizacion_sidecars,
)


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
SIGNED_FORMULA = (
    'IF(Mobiliti!$C$13="Dólares",Cotizacion!H37*17.5,Cotizacion!H37)'
)


def _cell(formula=SIGNED_FORMULA):
    cell = ET.Element(f"{{{MAIN}}}c", {"r": "N37", "s": "21"})
    if formula is not None:
        ET.SubElement(cell, f"{{{MAIN}}}f").text = formula
    ET.SubElement(cell, f"{{{MAIN}}}v").text = "1750000"
    return cell


@pytest.mark.parametrize("delta", [-19, 0, 1000])
def test_v17_freight_follows_active_checkbox_after_rows_move(delta):
    source = _cell()
    before = ET.tostring(source)
    row = 37 + delta

    result = _shift_cotizacion_sidecars(
        {37: (source,)}, layout=V17_COTIZACION_LAYOUT, total_delta=delta
    )[row][0]

    assert result.findtext(f"{{{MAIN}}}f") == (
        f"IF(Mobiliti!$P$4=TRUE,Cotizacion!H{row}*17.5,Cotizacion!H{row})"
    )
    assert result.attrib == {"r": f"N{row}", "s": "21"}
    assert result.find(f"{{{MAIN}}}v") is None
    assert ET.tostring(source) == before


def test_legacy_freight_formula_keeps_its_currency_selector():
    result = _shift_cotizacion_sidecars(
        {37: (_cell(),)}, layout=LEGACY_COTIZACION_LAYOUT, total_delta=0
    )[37][0]
    assert result.findtext(f"{{{MAIN}}}f") == SIGNED_FORMULA


@pytest.mark.parametrize("formula", [None, SIGNED_FORMULA.replace("17.5", "18")])
def test_v17_freight_rejects_unsigned_source_formula(formula):
    with pytest.raises(ValueError, match="N37"):
        _shift_cotizacion_sidecars(
            {37: (_cell(formula),)},
            layout=V17_COTIZACION_LAYOUT,
            total_delta=1,
        )
