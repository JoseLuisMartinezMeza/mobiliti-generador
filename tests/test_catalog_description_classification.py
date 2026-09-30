"""La redacción para clientes no cambia categorías ni precios de cálculo.

Casos reales del inventario Offiho y del XLSX JOME (COSTO LAMINADO 2026,
fila 626). La clasificación histórica se conserva incluso cuando es discutible:
corregir tarifas de instalación requiere una tarea distinta de redacción.
"""

from copy import deepcopy

from openpyxl import load_workbook

from mobiliti_saas.quote_engine.classification import classify_product_name, load_category_dictionary
from mobiliti_saas.quote_engine.engine import _official_cotizacion_description, _official_presentation_lines
from mobiliti_saas.quote_engine.offiho_catalog import (
    build_offiho_cart_payload, create_offiho_quotation_workbook, load_offiho_catalog,
)
from mobiliti_saas.quote_engine.parser import read_items
from mobiliti_saas.quote_engine.supplier_catalog import (
    build_supplier_cart_payload, create_supplier_quotation_workbook, load_supplier_catalog_data,
)


def _sin_imagenes(payload):
    copia = deepcopy(payload)
    for item in copia["items"]:
        item["image_url"] = ""
        item["image_references"] = []
    return copia


def _categorias(items, proveedor):
    diccionario = load_category_dictionary([item["name"] for item in items])
    return [classify_product_name(
        item["name"], diccionario, description=item["description"],
        source_category=proveedor, supplier=proveedor,
    ) for item in items]


def test_vesper_y_banca_conservan_alias_logistico_pero_muestran_familia(tmp_path, monkeypatch):
    monkeypatch.setenv("MOBILITI_DEV_MODE", "1")
    catalogo = load_offiho_catalog()
    payload = _sin_imagenes(build_offiho_cart_payload([
        {"inventory_key": "VESPER 1", "quantity": 1},
        {"inventory_key": "VESPER 1 BANCA 4P", "quantity": 1},
    ], catalog=catalogo))
    originales = deepcopy(payload["items"])
    categorias_antes = _categorias(originales, "Offiho")
    assert categorias_antes == ["Terminados", "Bancos"]

    ruta = create_offiho_quotation_workbook(payload, tmp_path / "vesper.xlsx", image_dir=tmp_path / "imagenes")
    items, _ = read_items(ruta)
    productos = [item for item in items if item.tipo == "producto"]
    assert [item.nombre for item in productos] == [item["name"] for item in originales]
    for producto, original in zip(productos, originales):
        assert producto.descripcion.startswith(original["description"])
        assert producto.precio == original["unit_price"]
    lineas, _ = _official_presentation_lines(items, {
        "source_type": "offiho_cart", "catalog_supplier": "offiho",
        "catalog_supplier_label": "Offiho", "quote_currency": "MXN",
    }, {})
    assert [linea.category for linea in lineas] == categorias_antes
    texto = _official_cotizacion_description(lineas[0], "es")
    assert texto.startswith("VESPER 1.")
    assert "acero tubular de 20 mm" in texto
    assert "544 545 597" not in texto
    assert payload["items"] == originales
    libro = load_workbook(ruta, read_only=True)
    try:
        assert libro["_Catalogo_textos"].sheet_state in {"hidden", "veryHidden"}
        assert libro["Quotation"]["B9"].value == "1"
    finally:
        libro.close()


def _catalogo_jome_lrep():
    original = "Libero para recepción recta, 3 entrepaños fijos para lefort, estructura fabricada en"
    item = {
        "internal_id": "jome-lrep-8050", "supplier": "jome", "product_key": "lrep-8050",
        "sku": "LREP-8050", "code_status": "verified", "brand": "JOME",
        "collection": "Recepciones Modulares", "name": original, "description": original,
        "unit": "pieza", "availability_type": "made_to_order", "stock": None,
        "lead_time": "", "base_price_options": [], "add_on_options": [],
        "base_currency": "MXN", "price_net": "4540.000000", "tax_rate": "0.160000",
        "attributes": {"block": "Recepciones Modulares", "source_code": "LREP-8050", "dimensions": ".80x.50x1.28"},
        "image_url": "", "image_kind": "placeholder", "product_url": "", "warnings": [],
        "source_reference": "Spec guide-Laminado-2026.xlsx:COSTO LAMINADO 2026:E626",
    }
    return load_supplier_catalog_data({
        "supplier": "jome", "source_hash": "a" * 64,
        "generated_at": "2026-09-30T00:00:00Z", "items": [item],
    })


def test_jome_corrige_libero_solo_en_presentacion_sin_reclasificar(tmp_path, monkeypatch):
    monkeypatch.setenv("MOBILITI_DEV_MODE", "1")
    payload = build_supplier_cart_payload([
        {"internal_id": "jome-lrep-8050", "quantity": "1", "add_on_option_ids": []},
    ], catalog=_catalogo_jome_lrep(), quote_currency="MXN", rate_rows=[])
    originales = deepcopy(payload["items"])
    categorias_antes = _categorias(originales, "Jome")
    assert categorias_antes == ["Terminados"]

    ruta = create_supplier_quotation_workbook(payload, tmp_path / "lrep.xlsx")
    items, _ = read_items(ruta)
    producto = next(item for item in items if item.tipo == "producto")
    assert producto.nombre == originales[0]["name"]
    assert producto.descripcion.startswith(originales[0]["description"])
    assert producto.precio == 4540
    lineas, _ = _official_presentation_lines(items, {
        "source_type": "supplier_cart", "catalog_supplier": "jome",
        "catalog_supplier_label": "Jome", "quote_currency": "MXN",
    }, {})
    assert [linea.category for linea in lineas] == categorias_antes
    texto = _official_cotizacion_description(lineas[0], "es")
    assert texto.startswith("Librero para recepción recta")
    assert "Libero" not in texto
    assert "3 entrepaños fijos" in texto
    assert payload["items"] == originales
