"""El motor consulta a Claude solo por los productos importados cuyo nombre no reconoce."""

from __future__ import annotations

from mobiliti_saas.quote_engine import engine
from mobiliti_saas.quote_engine.parser import QuoteItem

ESCRITORIO = "Material Description:\n1)Tabletop: 25mm\n7)Beam with cable management"


def _producto(fila: int, nombre: str, descripcion: str = "", **extra) -> QuoteItem:
    return QuoteItem(tipo="producto", row=fila, nombre=nombre, descripcion=descripcion, cantidad=1, precio=100, **extra)


def _categorias(items, metadata=None, imagenes=None) -> dict[int, str]:
    lineas, _ = engine._official_presentation_lines(items, metadata or {"tipo_cambio": "20"}, imagenes or {})
    return {linea.item.row: linea.category for linea in lineas}


def test_claude_decide_solo_los_nombres_que_el_diccionario_no_reconoce(monkeypatch):
    consultados = []

    def clasificar(productos, imagenes, categorias):
        consultados.extend((producto.row, imagenes.get(producto.row)) for producto in productos)
        assert "Escritorios-WorkStation" in categorias
        return {producto.row: "Escritorios-WorkStation" for producto in productos}

    monkeypatch.setattr(engine, "clasificar_productos", clasificar)
    foto = (b"foto", "image/png")
    items = [
        _producto(9, "lido privados ch", ESCRITORIO),
        _producto(10, "CLG67SW Locke Task Chair", "Chair with cable management tray"),
        _producto(11, "BENCH LIDO PARA 2PX", "Straight employee desk"),
    ]

    assert _categorias(items, imagenes={9: foto}) == {
        9: "Escritorios-WorkStation",
        10: "Silla",
        11: "Escritorios-WorkStation",
    }
    assert consultados == [(9, foto)]


def test_la_descripcion_de_la_cotizacion_usa_la_categoria_revisada(monkeypatch):
    monkeypatch.setattr(engine, "clasificar_productos", lambda productos, *_: {9: "Escritorios-WorkStation"})
    lineas, _ = engine._official_presentation_lines(
        [_producto(9, "lido privados ch", ESCRITORIO)], {"tipo_cambio": "20"}, {}
    )

    # Antes decía "Power module model lido privados ch." en la cotización.
    assert engine._official_cotizacion_description(lineas[0], "en").startswith("Workstation desk model")


def test_sin_respuesta_de_claude_se_conserva_el_diccionario(monkeypatch):
    monkeypatch.setattr(engine, "clasificar_productos", lambda *_: {})

    # Comportamiento anterior, con su error conocido: la descripción decide.
    assert _categorias([_producto(9, "lido privados ch", ESCRITORIO)]) == {9: "Multicontactos"}


def test_en_un_carrito_mixto_solo_se_consulta_lo_importado(monkeypatch):
    consultados = []

    def clasificar(productos, imagenes, categorias):
        consultados.extend(producto.row for producto in productos)
        return {}

    monkeypatch.setattr(engine, "clasificar_productos", clasificar)
    precio = dict(moneda_original="MXN", precio_original=100, tipo_cambio_congelado=1)
    items = [
        _producto(9, "ZX-900 clave de catalogo", proveedor="Offiho", modo_precio="net", **precio),
        _producto(10, "DU82L.160155 clone 1", ESCRITORIO, proveedor="Sunon Inc", modo_precio="imported", **precio),
    ]

    _categorias(items, metadata={"catalog_price_mode": "mixed_catalog_converted", "quote_currency": "MXN"})

    assert consultados == [10]


def test_un_carrito_de_un_solo_catalogo_no_se_consulta(monkeypatch):
    def clasificar(productos, imagenes, categorias):
        assert not productos
        return {}

    monkeypatch.setattr(engine, "clasificar_productos", clasificar)

    _categorias(
        [_producto(9, "ZX-900 clave de catalogo")],
        metadata={"source_type": "offiho_cart", "catalog_supplier": "offiho", "catalog_supplier_label": "Offiho"},
    )
