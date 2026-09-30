"""La redacción comercial no altera identidad, clasificación ni costos fuente."""

from dataclasses import replace
from decimal import Decimal
from contextlib import closing
from copy import deepcopy

from openpyxl import Workbook, load_workbook
import pytest

from mobiliti_saas.quote_engine import engine
from mobiliti_saas.quote_engine.catalog_cart import create_catalog_quotation_workbook
from mobiliti_saas.quote_engine.local_files import save_quotation_workbook
from mobiliti_saas.quote_engine.parser import read_items


def _libro_con_textos():
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Quotation"
    for columna, encabezado in {1: "No.", 2: "Item", 4: "Description", 5: "Dimension", 7: "Qty", 10: "List Price"}.items():
        hoja.cell(7, columna, encabezado)
    hoja["A8"] = "- Offiho"
    for fila, nombre, descripcion in (
        (9, "1", "Asiento y respaldo de polipropileno. Clave: VESPER"),
        (10, "1 BANCA 4P", "Estructura de acero y asiento de polipropileno. Clave: VESPER"),
    ):
        for columna, valor in {1: fila - 8, 2: nombre, 4: descripcion, 5: "PZA", 7: 2, 10: 100}.items():
            hoja.cell(fila, columna, valor)
    textos = libro.create_sheet("_Catalogo_textos")
    textos.sheet_state = "hidden"
    textos.append(["fila", "nombre", "descripcion"])
    textos.append([9, "VESPER 1", "Asiento y respaldo de polipropileno."])
    textos.append([10, "VESPER 1 BANCA 4P", "Estructura de acero y asiento de polipropileno."])
    return libro


def _guardar(libro, ruta):
    try:
        save_quotation_workbook(libro, ruta)
    finally:
        libro.close()
    return ruta


def test_textos_comerciales_separados_conservan_clasificacion_y_costos_del_lote(tmp_path):
    fuente = _guardar(_libro_con_textos(), tmp_path / "identidad-separada.xlsx")
    productos = [item for item in read_items(fuente)[0] if item.tipo == "producto"]
    assert [item.nombre for item in productos] == ["1", "1 BANCA 4P"]
    assert [item.nombre_comercial for item in productos] == ["VESPER 1", "VESPER 1 BANCA 4P"]
    assert all("Clave: VESPER" in item.descripcion for item in productos)
    assert all("Clave:" not in item.descripcion_comercial for item in productos)
    sin_redaccion = [replace(item, nombre_comercial=None, descripcion_comercial=None) for item in productos]
    metadata = {"source_type": "offiho_cart", "tipo_cambio": "20"}
    originales, _ = engine._official_presentation_lines(sin_redaccion, metadata, {})
    redactadas, _ = engine._official_presentation_lines(productos, metadata, {})
    for original, redactada in zip(originales, redactadas, strict=True):
        assert (redactada.category, redactada.name, redactada.quantity, redactada.original_currency,
                redactada.original_cost, redactada.frozen_rate, redactada.converted_cost) == (
            original.category, original.name, original.quantity, original.original_currency,
            original.original_cost, original.frozen_rate, original.converted_cost,
        )
        assert redactada.original_cost == Decimal("100")
        descripcion = engine._official_cotizacion_description(redactada, "es")
        assert descripcion.startswith(redactada.item.nombre_comercial + ".")
        assert "Clave:" not in descripcion


def test_redaccion_comercial_no_sustituye_textos_de_un_producto_importado(tmp_path):
    fuente = _guardar(_libro_con_textos(), tmp_path / "importado.xlsx")
    producto = next(item for item in read_items(fuente)[0] if item.tipo == "producto")
    lineas, _ = engine._official_presentation_lines([producto], {"tipo_cambio": "20"}, {})
    linea = replace(lineas[0], origin="imported")
    esperado = engine._official_cotizacion_description(
        replace(linea, item=replace(producto, nombre_comercial=None, descripcion_comercial=None)), "es",
    )
    assert engine._official_cotizacion_description(linea, "es") == esperado


def test_escritor_importado_conserva_espacios_y_orden_sin_metadata_comercial(tmp_path):
    descripcion = (
        "Respaldo   negro.\n  Tapiz elegido por el cliente."
        " | Unidad: especial\nsegún ficha | Detalle: costura doble."
        " | Fuente: anotación del cliente | Asiento    fijo."
    )
    payload = {
        "source_type": "imported_quotation",
        "items": [{
            "name": "Silla personalizada", "description": descripcion,
            "supplier": "offiho", "unit": "PZA", "quantity": "2",
            "unit_price": "100", "warnings": [],
        }],
    }
    original = deepcopy(payload)
    fuente = create_catalog_quotation_workbook(
        payload, tmp_path / "fuente-importada.xlsx",
        source_type="imported_quotation", category_label="Importados",
    )
    with closing(load_workbook(fuente, read_only=True)) as libro:
        assert libro.sheetnames == ["Quotation"]
        assert libro["Quotation"]["D9"].value == descripcion
    producto = next(item for item in read_items(fuente)[0] if item.tipo == "producto")
    assert producto.descripcion == descripcion
    assert producto.nombre_comercial is None
    assert producto.descripcion_comercial is None
    assert producto.cantidad == 2 and producto.precio == 100
    assert payload == original


@pytest.mark.parametrize(
    "cambio",
    ["fila_duplicada", "fila_fuera", "fila_categoria", "nombre_numerico", "fila_booleana", "encabezados", "visible"],
)
def test_parser_rechaza_metadata_comercial_inconsistente(tmp_path, cambio):
    libro = _libro_con_textos()
    hoja = libro["_Catalogo_textos"]
    if cambio == "fila_duplicada":
        hoja["A3"] = 9
    elif cambio == "fila_fuera":
        hoja["A2"] = 11
    elif cambio == "fila_categoria":
        hoja["A2"] = 8
    elif cambio == "nombre_numerico":
        hoja["B2"] = 123
    elif cambio == "fila_booleana":
        hoja["A2"] = True
    elif cambio == "encabezados":
        hoja["C1"] = "precio"
    elif cambio == "visible":
        hoja.sheet_state = "visible"
    fuente = _guardar(libro, tmp_path / (cambio + ".xlsx"))
    with pytest.raises(ValueError):
        read_items(fuente)
