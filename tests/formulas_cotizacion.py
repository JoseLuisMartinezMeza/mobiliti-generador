"""Expectativas compartidas sobre fórmulas que el motor escribe en la hoja Cotizacion."""

from __future__ import annotations


def descuento_de_partida(precio: object, fila: int) -> str:
    """Fórmula esperada en ``Cotizacion!G{fila}`` dado el contenido de ``F{fila}``.

    La partida usa el descuento que calcula Mobiliti (AE por unidad, con las mismas
    proporciones del precio), no el solicitado para todas (``Mobiliti!$AD$14``).
    """

    if not str(precio).startswith("="):
        # Precio por confirmar: la partida conserva la fórmula de la plantilla.
        return "=ROUND(Mobiliti!$AD$14,2)"
    descuento = str(precio).removeprefix("=").replace("Mobiliti!AA", "Mobiliti!AE")
    return f"=IFERROR(ROUND(({descuento})/F{fila},2),0)"
