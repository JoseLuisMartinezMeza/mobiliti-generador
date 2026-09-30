"""Regresiones de redacción de catálogos en la cotización entregable."""

from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import hashlib
import re

from openpyxl import load_workbook
import pytest

from mobiliti_saas.quote_engine import generate_quote
from mobiliti_saas.quote_engine import catalog_cart
from mobiliti_saas.quote_engine.mixed_catalog import (
    MIXED_CATALOG_ORDER,
    build_mixed_catalog_cart_payload,
    create_mixed_catalog_quotation_workbook,
)
from mobiliti_saas.quote_engine.offiho_catalog import (
    build_offiho_cart_payload,
    create_offiho_quotation_workbook,
    load_offiho_catalog,
)
from mobiliti_saas.quote_engine.quotation_sheets import quotation_data_rows
from mobiliti_saas.quote_engine.parser import read_items
from mobiliti_saas.quote_engine.supplier_catalog import (
    build_supplier_cart_payload,
    create_supplier_quotation_workbook,
    load_supplier_catalog_data,
)
from mobiliti_saas.quote_engine.tarkett_catalog import TarkettCatalogItem


RAIZ = Path(__file__).resolve().parents[1]
PLANTILLA = RAIZ / "mobiliti_saas/worker/templates/Formato Cotizacion 2026 Oficial.xlsx"


@pytest.fixture(autouse=True)
def _imagenes_aisladas(monkeypatch):
    def sin_descargas(*args, **kwargs):
        raise AssertionError("Estos casos de redacción no deben descargar imágenes")

    monkeypatch.setattr(catalog_cart, "_download_catalog_image", sin_descargas)


def _descripciones_vinculadas(libro):
    filas = libro["Quotation"].iter_rows(min_row=7, values_only=True)
    encabezados = next(filas)
    columna_cantidad = encabezados.index("Qty")
    columna_precio = encabezados.index("List Price")
    fuentes = dict(enumerate(filas, start=8))
    resultado = {}
    for fila in libro["Cotizacion"].iter_rows(min_col=3, max_col=3):
        coincidencia = re.fullmatch(r"=Quotation!D([1-9][0-9]*)", str(fila[0].value or ""))
        if coincidencia:
            origen = fuentes[int(coincidencia.group(1))]
            resultado[origen[1]] = (
                origen[3],
                origen[columna_cantidad],
                origen[columna_precio],
            )
    return resultado


def _catalogo_offiho_local(claves):
    fuente = load_offiho_catalog()
    productos = [
        replace(
            fuente["by_inventory_key"][clave], unit_price=Decimal("100"),
            available_quantity=Decimal("10"), image_url="", image_kind="placeholder",
        )
        for clave in claves
    ]
    return {
        "source_hash": "a" * 64,
        "items": productos,
        "by_inventory_key": {item.inventory_key: item for item in productos},
    }


def test_offiho_real_llega_al_cliente_sin_ocr_y_con_especificaciones_conservadas(tmp_path):
    claves = (
        "OHE-405 NEGRO ALUFSEN", "OHV-78 NEGRO VANTO *", "BRAZO 86S AL DER BLANCO",
    )
    catalogo = _catalogo_offiho_local(claves)
    carrito = build_offiho_cart_payload(
        [{"inventory_key": clave, "quantity": "2"} for clave in claves], catalog=catalogo,
    )
    congelado = deepcopy(carrito)
    fuente = create_offiho_quotation_workbook(carrito, tmp_path / "offiho-fuente.xlsx")
    nombres_comerciales = {
        item.nombre: item.nombre_comercial
        for item in read_items(fuente)[0] if item.tipo == "producto"
    }
    salida = generate_quote(
        fuente, tmp_path / "offiho-cliente.xlsx",
        {"source_type": "offiho_cart", "description_language": "es", "tipo_cambio": "20"},
        PLANTILLA,
    )
    assert carrito == congelado
    with closing(load_workbook(salida, read_only=True, data_only=False)) as libro:
        descripciones = _descripciones_vinculadas(libro)
        assert len(descripciones) == 3
        alufsen = next(texto for nombre, (texto, _, _) in descripciones.items() if "ALUFSEN" in nombre)
        assert "aluminio" in alufsen.lower()
        assert "pistón neumático" in alufsen.lower()
        assert "50 kg/m³" in alufsen
        assert "160 kg" in alufsen
        assert "synchro" not in alufsen.lower()
        assert "anti-shock" not in alufsen.lower()
        for nombre, (texto, cantidad, precio) in descripciones.items():
            assert texto.startswith(nombres_comerciales[nombre] + ".")
            assert cantidad == 2
            assert precio == 100
            for ruido in ("ODLAPSER", "OTNEISA", "ºº", "POR CAJA", "Fuente:", "URL:", "Clave:"):
                assert ruido not in texto
            if "VANTO" in nombre or "BRAZO" in nombre:
                assert "120" not in texto and "140" not in texto
                assert "SHELL-37" not in texto


# Casos editoriales de los proveedores; los precios y las existencias son sintéticos.
CASOS = {
    "cr-global": ("Vértebra pasacables", "Vertébra pasacables Fabricada en plastico.", ("Vértebra", "plástico")),
    "sonara": ("Canal U", "Canal U de 2.40mts", ("2.40 m",)),
    "sunon": ("Locke", "Finishing: high-quality mesh. Seat Cushion: molded foam, density 55kg/m3.", ("malla de alta calidad", "55")),
    "alma": ("Dinosaur egg", "Dinosaur egg sling dining armchair with loose seat cushion, full aluminium legs", ("silla de comedor con brazos", "cojín", "aluminio")),
    "lumbro": ("Lisboa", "Multicontacto, Modelo Lisboa. Incluye 1 puertoo USB (Carga). Sistema: Standard", ("Multicontacto", "1 puerto USB de carga")),
    "jome": ("Brazo doble", "Brazos Porta Monitores Doble. Peso soportado", ("Brazo doble para monitores",)),
    "lauco": ("Sofá", "Estructura interna madera. Soporte asiento.", ("Estructura interna de madera", "Soporte de asiento")),
    "idelika": ("Banco", "Estructura en tuvo redondo. Precios disponibles en www.idelika.com", ("tubo redondo",)),
    "conceptos": ("Aspetta", "Sillón aspetta de 1 plaza fabricado en estructura interna y brazos en madera de", ("Sillón Aspetta de 1 plaza", "brazos de madera")),
    "labenze": ("Silla multiperforada", "Cromo Accesorios: Base tapiz y brazos. Silla de polipropileno con respaldo multiperforado y base de 5 puntas cromada con rodajas.", ("respaldo multiperforado", "como accesorios")),
    "requiez": ("Sillón ejecutivo", "Sillón ejecutivo, respaldo alto tapizado en tela estructura", ("respaldo alto tapizado en tela y estructura",)),
}


def _catalogos_y_seleccion():
    tarkett = TarkettCatalogItem(
        code="TARK-REDACCION", name="Piso Tarkett", unit="M2",
        available_quantity=Decimal("10"), unit_price=Decimal("100"), price_source="catalog",
    )
    catalogos = {
        "tarkett": {"source_hash": "b" * 64, "items": [tarkett], "by_code": {tarkett.code: tarkett}},
        "offiho": _catalogo_offiho_local(("OHE-405 NEGRO ALUFSEN",)),
    }
    seleccion = [
        {"catalog": "tarkett", "code": tarkett.code, "quantity": "2"},
        {"catalog": "offiho", "inventory_key": "OHE-405 NEGRO ALUFSEN", "quantity": "2"},
    ]
    for proveedor, (nombre, descripcion, _) in CASOS.items():
        identidad = proveedor + ":redaccion"
        producto = {
            "internal_id": identidad, "supplier": proveedor, "product_key": identidad,
            "sku": proveedor.upper() + "-PRUEBA", "code_status": "verified",
            "brand": proveedor, "collection": "", "name": nombre, "description": descripcion,
            "unit": "PZA", "availability_type": "made_to_order", "stock": None,
            "lead_time": "4-6 semanas", "base_price_options": [], "add_on_options": [],
            "base_currency": "USD" if proveedor in {"sunon", "alma"} else "MXN",
            "price_net": "100.000000", "tax_rate": "0.160000",
            "attributes": {"dimensions": "60 × 50 × 80 cm", "warranty": "5 años", "product_notes": ["No incluye instalación"]},
            "image_url": "", "image_kind": "placeholder", "product_url": "",
            "warnings": [], "source_reference": proveedor + ":fuente-congelada",
        }
        catalogos[proveedor] = {
            "supplier": proveedor, "source_hash": hashlib.sha256(proveedor.encode()).hexdigest(),
            "generated_at": "2026-09-29T12:00:00Z", "items": [producto],
        }
        seleccion.append({"catalog": proveedor, "internal_id": identidad, "quantity": "2"})
    return catalogos, seleccion


@pytest.mark.parametrize("proveedor", ["conceptos", "labenze"])
def test_catalogo_individual_aplica_edicion_antes_de_anadir_trazabilidad(tmp_path, proveedor):
    catalogos, _ = _catalogos_y_seleccion()
    carrito = build_supplier_cart_payload(
        [{"internal_id": proveedor + ":redaccion", "quantity": "2"}],
        load_supplier_catalog_data(catalogos[proveedor], expected_supplier=proveedor),
        "MXN", [], today=date(2026, 9, 29),
    )
    congelado = deepcopy(carrito)
    salida = create_supplier_quotation_workbook(carrito, tmp_path / (proveedor + "-fuente.xlsx"))
    producto = next(item for item in read_items(salida)[0] if item.tipo == "producto")
    texto = producto.descripcion_comercial
    for esperado in CASOS[proveedor][2]:
        assert esperado.casefold() in texto.casefold(), texto
    assert CASOS[proveedor][1] in producto.descripcion
    assert "Fuente:" in producto.descripcion
    assert "Unidad:" in producto.descripcion
    assert carrito == congelado


@pytest.mark.parametrize("con_trazabilidad", [False, True], ids=["fuente-xlsx", "con-trazabilidad"])
def test_trece_catalogos_conservan_redaccion_y_datos_en_cotizacion_mixta(tmp_path, con_trazabilidad):
    catalogos, seleccion = _catalogos_y_seleccion()
    originales = deepcopy(catalogos)
    carrito = build_mixed_catalog_cart_payload(
        seleccion, catalogs=catalogos, quote_currency="MXN", commercial_discount_percent="40",
        rate_rows=[{"currency": "USD", "effective_date": "2026-09-29", "mxn_per_unit": "20.000000", "retrieved_at": "2026-09-29T12:00:00Z"}],
        today=date(2026, 9, 29),
    )
    congelado = deepcopy(carrito)
    fuente = create_mixed_catalog_quotation_workbook(carrito, tmp_path / "mixta-fuente.xlsx")
    argumentos = {"quotation_data_rows": quotation_data_rows(carrito)} if con_trazabilidad else {}
    salida = generate_quote(
        fuente, tmp_path / "mixta-cliente.xlsx",
        {"source_type": "mixed_catalog_cart", "description_language": "es",
         "catalog_price_mode": "mixed_catalog_converted", "quote_currency": "MXN",
         "catalog_source_hashes": {grupo["catalog"]: grupo["catalog_source_hash"] for grupo in carrito["groups"]},
         "rate_summary": carrito["rate_summary"], "auto_electrification_rate": carrito["auto_electrification_rate"]},
        PLANTILLA, **argumentos,
    )
    assert catalogos == originales
    assert carrito == congelado
    with closing(load_workbook(salida, read_only=True, data_only=False)) as libro:
        descripciones = _descripciones_vinculadas(libro)
        assert len(descripciones) == len(MIXED_CATALOG_ORDER) == 13
        errores = []
        for proveedor, (nombre, _, esperados) in CASOS.items():
            texto, cantidad, precio = descripciones[nombre]
            assert texto.startswith(nombre + "."), (proveedor, texto)
            for esperado in (*esperados, "4-6 semanas", "5 años", "No incluye instalación"):
                if esperado.casefold() not in texto.casefold():
                    errores.append((proveedor, "falta " + esperado, texto))
            assert cantidad == 2
            assert precio == (2000 if proveedor in {"sunon", "alma"} else 100)
            for ruido in ("Fuente:", "Hash fuente:", "SKU:", "URL:", "Clave:", "Unidad:", " | ", "puertoo", "Peso soportado", "www.idelika.com"):
                if ruido in texto:
                    errores.append((proveedor, "sobra " + ruido, texto))
        assert not errores, errores
