"""Clasificación con Claude de productos cuyo nombre no dice qué son: sin llamadas reales."""

from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from mobiliti_saas.quote_engine import visual_classification as visual

CATEGORIAS = tuple(visual.CATEGORIAS)


def _foto(lado: int = 900, color: str = "gray") -> bytes:
    salida = BytesIO()
    Image.new("RGBA", (lado, lado // 2), color).save(salida, "PNG")
    return salida.getvalue()


def _producto(fila: int, nombre: str, descripcion: str = "Tabletop: 25mm", dimension: str = "") -> SimpleNamespace:
    return SimpleNamespace(row=fila, nombre=nombre, descripcion=descripcion, dimension=dimension)


class ClienteFalso:
    """Responde por turnos; cada turno es una lista de (indice, categoria) o una excepción."""

    def __init__(self, *turnos):
        self.turnos = list(turnos)
        self.solicitudes = []
        self.messages = self

    def parse(self, **solicitud):
        self.solicitudes.append(solicitud)
        turno = self.turnos.pop(0)
        if isinstance(turno, Exception):
            raise turno
        salida = None if turno is None else SimpleNamespace(
            productos=[SimpleNamespace(indice=i, categoria=c) for i, c in turno]
        )
        return SimpleNamespace(parsed_output=salida, usage=SimpleNamespace(input_tokens=100, output_tokens=20))


@pytest.fixture(autouse=True)
def modelo_activo(monkeypatch):
    monkeypatch.setenv("QUOTE_LLM_FALLBACK_MODEL", "haiku")


def test_apagado_no_consulta_a_nadie(monkeypatch):
    monkeypatch.setenv("QUOTE_LLM_FALLBACK_MODEL", "")
    cliente = ClienteFalso()

    assert visual.clasificar_productos([_producto(9, "lido privados")], {}, CATEGORIAS, cliente=cliente) == {}
    assert cliente.solicitudes == []


def test_envia_texto_y_miniatura_y_devuelve_la_categoria_por_fila():
    cliente = ClienteFalso([(1, "Escritorios-WorkStation"), (2, "Mesas de Juntas")])
    productos = [
        _producto(16, "lido privados ch", "1)Tabletop: 25mm\n2)Cabinet", "1,600*1,550*750 mm"),
        _producto(42, "DU72.320140"),
    ]

    resultado = visual.clasificar_productos(productos, {16: (_foto(), "image/png")}, CATEGORIAS, cliente=cliente)

    assert resultado == {16: "Escritorios-WorkStation", 42: "Mesas de Juntas"}
    (solicitud,) = cliente.solicitudes
    assert solicitud["model"] == "claude-haiku-5-5"
    assert solicitud["output_config"] == {"effort": "high"}
    assert "Escritorios-WorkStation" in solicitud["system"]
    contenido = solicitud["messages"][0]["content"]
    assert [bloque["type"] for bloque in contenido] == ["text", "image", "text"]
    assert "Producto 1" in contenido[0]["text"] and "lido privados ch" in contenido[0]["text"]
    assert "1,600*1,550*750 mm" in contenido[0]["text"] and "Tabletop" in contenido[0]["text"]
    assert "Producto 2" in contenido[2]["text"]
    # La foto viaja reducida y sin transparencia para gastar pocos tokens.
    assert contenido[1]["source"]["media_type"] == "image/jpeg"
    with Image.open(BytesIO(base64.standard_b64decode(contenido[1]["source"]["data"]))) as miniatura:
        assert miniatura.format == "JPEG" and max(miniatura.size) == visual.LADO_MAXIMO
    # El esquema de respuesta solo admite las categorías conocidas.
    esquema = solicitud["output_format"].model_json_schema()
    assert sorted(CATEGORIAS) == sorted(esquema["$defs"]["Producto"]["properties"]["categoria"]["enum"])


def test_productos_repetidos_se_preguntan_una_vez():
    cliente = ClienteFalso([(1, "Escritorios-WorkStation"), (2, "Silla")])
    foto = (_foto(), "image/png")
    productos = [_producto(16, "lido privados"), _producto(18, "lido privados"), _producto(20, "otro")]

    resultado = visual.clasificar_productos(productos, {16: foto, 18: foto}, CATEGORIAS, cliente=cliente)

    assert resultado == {16: "Escritorios-WorkStation", 18: "Escritorios-WorkStation", 20: "Silla"}
    assert sum(bloque["type"] == "text" for bloque in cliente.solicitudes[0]["messages"][0]["content"]) == 2


def test_ignora_categorias_ajenas_e_indices_desconocidos():
    cliente = ClienteFalso([(1, "Silla"), (2, "Escritorios-WorkStation"), (7, "Bancos")])
    permitidas = tuple(c for c in CATEGORIAS if c != "Silla")

    resultado = visual.clasificar_productos(
        [_producto(9, "uno"), _producto(10, "dos")], {}, permitidas, cliente=cliente
    )

    assert resultado == {10: "Escritorios-WorkStation"}


@pytest.mark.parametrize("turno", [RuntimeError("sin red"), None])
def test_un_fallo_no_tumba_la_cotizacion(turno, capsys):
    cliente = ClienteFalso(turno)

    assert visual.clasificar_productos([_producto(9, "lido privados")], {}, CATEGORIAS, cliente=cliente) == {}
    assert '"event":"visual_classification"' in capsys.readouterr().out


def test_foto_ilegible_se_clasifica_solo_con_el_texto():
    cliente = ClienteFalso([(1, "Escritorios-WorkStation")])

    resultado = visual.clasificar_productos(
        [_producto(9, "lido privados")], {9: (b"no es una imagen", "image/png")}, CATEGORIAS, cliente=cliente
    )

    assert resultado == {9: "Escritorios-WorkStation"}
    assert [b["type"] for b in cliente.solicitudes[0]["messages"][0]["content"]] == ["text"]


def test_muchos_productos_se_reparten_en_lotes():
    productos = [_producto(fila, f"producto {fila}") for fila in range(1, visual.LOTE + 6)]
    cliente = ClienteFalso(
        [(i, "Silla") for i in range(1, visual.LOTE + 1)],
        [(i, "Bancos") for i in range(1, 6)],
    )

    resultado = visual.clasificar_productos(productos, {}, CATEGORIAS, cliente=cliente)

    assert len(cliente.solicitudes) == 2
    assert resultado[1] == "Silla" and resultado[visual.LOTE] == "Silla"
    assert resultado[visual.LOTE + 1] == "Bancos" and len(resultado) == visual.LOTE + 5


def test_si_falla_el_segundo_lote_conserva_el_primero():
    productos = [_producto(fila, f"producto {fila}") for fila in range(1, visual.LOTE + 3)]
    cliente = ClienteFalso([(i, "Silla") for i in range(1, visual.LOTE + 1)], TimeoutError("lento"))

    resultado = visual.clasificar_productos(productos, {}, CATEGORIAS, cliente=cliente)

    assert len(resultado) == visual.LOTE and set(resultado.values()) == {"Silla"}
