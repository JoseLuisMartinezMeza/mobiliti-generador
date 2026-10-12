"""Correcciones a dos fórmulas de la plantilla que el motor aplica al componer la cotización."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys

from openpyxl import load_workbook
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.path.join(ROOT, "mobiliti_saas", "worker"))

from online_quote_generator import generate_online_quote  # noqa: E402
from quotation_import_fixtures import write_import_fixture  # noqa: E402


@pytest.fixture(scope="module")
def cotizacion(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("formulas")
    salida = carpeta / "cotizacion.xlsx"
    generate_online_quote(
        write_import_fixture(carpeta / "quotation.xlsx"),
        salida,
        {"cotizacion": "PRUEBA-1", "proyecto": "Formulas", "cliente": "Cliente", "descuento": 40},
    )
    libro = load_workbook(salida, data_only=False)
    yield libro
    libro.close()


def test_la_caratula_usa_el_descuento_de_cada_partida_y_no_el_solicitado_para_todas(cotizacion) -> None:
    hoja = cotizacion["Cotizacion"]
    partidas = [
        (fila, re.fullmatch(r"=Mobiliti!AA(\d+)", str(hoja.cell(fila, 6).value)))
        for fila in range(15, hoja.max_row + 1)
    ]
    partidas = [(fila, int(coincide.group(1))) for fila, coincide in partidas if coincide]

    assert len(partidas) == 7
    for fila, mobiliti in partidas:
        # AE es el descuento por unidad en Mobiliti, que respeta el tope de cada proveedor.
        assert hoja.cell(fila, 7).value == f"=IFERROR(ROUND((Mobiliti!AE{mobiliti})/F{fila},2),0)"
        assert hoja.cell(fila, 8).value == f"=F{fila}*G{fila}"
    formulas = [str(celda.value) for fila in hoja.iter_rows() for celda in fila if celda.value is not None]
    assert not any("$AD$14" in formula for formula in formulas)


def test_la_columna_de_margen_marca_error_si_no_hay_margen_calculable(cotizacion) -> None:
    hoja = cotizacion["Mobiliti"]
    productos = [
        fila for fila in range(15, hoja.max_row + 1)
        if str(hoja.cell(fila, 4).value or "").startswith("=Quotation!")
    ]
    reserva = next(
        fila for fila in range(15, hoja.max_row + 1)
        if not hoja.cell(fila, 6).value and str(hoja.cell(fila, 40).value or "").startswith("=")
    )

    assert len(productos) == 7
    for fila in (*productos, reserva):
        # Antes: IF(AK<AM,"ERROR","OK"), que da OK cuando AK queda vacío por un error (texto > número).
        assert hoja.cell(fila, 40).value == (
            f'=IF(F{fila}="","",IF(OR(NOT(ISNUMBER(AK{fila})),AK{fila}<AM{fila}),"ERROR","OK"))'
        )
