from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XlsxImage
from PIL import Image
import pymupdf
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.join(ROOT, "mobiliti_saas", "worker"))

import quote_worker  # noqa: E402
from mobiliti_saas.quote_engine.images import extract_images  # noqa: E402
from mobiliti_saas.quote_engine.parser import read_items  # noqa: E402
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest  # noqa: E402
from pdf_quotation_import.llm_fallback import (  # noqa: E402
    CotizacionExtraida,
    ProductoExtraido,
    convertir_con_llm,
)


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 40), color).save(buffer, "PNG")
    return buffer.getvalue()


def _producto(**cambios) -> ProductoExtraido:
    base = {
        "categoria": "",
        "codigo": "",
        "nombre": "BENCH P/8PX",
        "descripcion": "cada cubierta",
        "dimension": "1.80x.80x.75",
        "color": "",
        "cantidad": 15,
        "precio_unitario": 37957.0,
        "hoja": "",
        "posicion": 1,
        "nota_interna": "",
    }
    return ProductoExtraido(**{**base, **cambios})


def _cotizacion(productos: list[ProductoExtraido], subtotal: float = 0) -> CotizacionExtraida:
    return CotizacionExtraida(
        proveedor="SUNON",
        referencia="OCT-01",
        fecha="2026-10-01",
        cliente="GABY",
        moneda="MXN",
        subtotal_documento=subtotal,
        productos=productos,
    )


class ClienteFalso:
    """Sustituye solo la red: devuelve la extracción indicada y guarda la petición."""

    def __init__(self, extraida, stop_reason="end_turn", entrada=1000, salida=500):
        self.peticion: dict = {}
        self.messages = SimpleNamespace(parse=self._parse)
        self._respuesta = SimpleNamespace(
            parsed_output=extraida,
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=entrada, output_tokens=salida),
        )

    def _parse(self, **peticion):
        self.peticion = peticion
        return self._respuesta


def _pdf_con_dos_fotos(ruta: Path) -> None:
    doc = pymupdf.open()
    pagina = doc.new_page(width=612, height=792)
    pagina.insert_text((150, 60), "DESCRIPCION CANTIDAD MEDIDAS PRECIO", fontsize=9)
    # Se insertan fuera de orden para comprobar que se asignan de arriba hacia abajo.
    pagina.insert_image(pymupdf.Rect(30, 300, 110, 360), stream=_png((200, 10, 10)))
    pagina.insert_image(pymupdf.Rect(30, 100, 110, 160), stream=_png((10, 10, 200)))
    doc.save(ruta)
    doc.close()


def test_pdf_no_estandar_se_convierte_a_quotation_que_el_parser_lee(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.pdf"
    salida = tmp_path / "quotation.xlsx"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(
        _cotizacion(
            [
                _producto(categoria="OPERATIVO"),
                _producto(categoria="OPERATIVO", nombre="LOCKER", codigo="LK-1", cantidad=32, precio_unitario=3790.0),
            ]
        )
    )

    uso = convertir_con_llm(fuente, salida, modelo="haiku", cliente=cliente)

    items, _columnas = read_items(salida)
    productos = [item for item in items if item.tipo == "producto"]
    assert [item.nombre for item in items if item.tipo == "categoria"] == ["OPERATIVO"]
    assert [(p.nombre, p.dimension, p.cantidad, p.precio) for p in productos] == [
        ("BENCH P/8PX", "1.80x.80x.75", 15, 37957.0),
        ("LK-1\nLOCKER", "1.80x.80x.75", 32, 3790.0),
    ]
    assert productos[0].descripcion == "cada cubierta"

    fotos, _carpeta = extract_images(salida)
    assert sorted(fotos) == [9, 10]
    assert Image.open(fotos[9]).convert("RGB").getpixel((5, 5))[2] > 150  # la foto azul es la de arriba

    documento = cliente.peticion["messages"][0]["content"][0]
    assert documento["type"] == "document"
    assert documento["source"]["media_type"] == "application/pdf"
    assert cliente.peticion["model"] == "claude-haiku-5-5"
    assert cliente.peticion["output_format"] is CotizacionExtraida
    assert uso["modelo"] == "claude-haiku-5-5"
    assert uso["productos"] == 2 and uso["fotos"] == 2
    assert uso["moneda"] == "MXN"
    assert (uso["tokens_entrada"], uso["tokens_salida"]) == (1000, 500)
    assert uso["costo_usd"] == pytest.approx(0.00035)  # 1000*0.10/1M + 500*0.50/1M
    assert uso["advertencias"] == []


def test_costo_usa_la_tarifa_del_modelo_elegido(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.pdf"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(_cotizacion([_producto()]), entrada=20_000, salida=3_000)

    uso = convertir_con_llm(fuente, tmp_path / "q.xlsx", modelo="sonnet", cliente=cliente)

    assert cliente.peticion["model"] == "claude-sonnet-5-5"
    assert uso["costo_usd"] == pytest.approx(0.07)  # 20000*2/1M + 3000*10/1M


def test_no_asigna_fotos_si_no_coinciden_con_los_productos_de_la_pagina(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.pdf"
    salida = tmp_path / "quotation.xlsx"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(_cotizacion([_producto()]))  # un producto, dos fotos en la página

    uso = convertir_con_llm(fuente, salida, cliente=cliente)

    assert uso["fotos"] == 0
    assert extract_images(salida)[0] == {}


def test_excel_no_estandar_envia_celdas_y_asigna_foto_por_fila(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.xlsx"
    salida = tmp_path / "quotation.xlsx"
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Hoja1"
    hoja.append(["Concepto", "Cant", "P.U."])
    hoja.append(["Silla ejecutiva", 4, 1250.5])
    hoja.add_image(XlsxImage(BytesIO(_png((10, 200, 10)))), "E2")
    libro.save(fuente)
    libro.close()
    cliente = ClienteFalso(
        _cotizacion(
            [_producto(nombre="Silla ejecutiva", cantidad=4, precio_unitario=1250.5, hoja="Hoja1", posicion=2)]
        )
    )

    uso = convertir_con_llm(fuente, salida, cliente=cliente)

    enviado = cliente.peticion["messages"][0]["content"][0]["text"]
    assert "Hoja: Hoja1" in enviado
    assert "fila 2: A=Silla ejecutiva | B=4 | C=1250.5" in enviado
    productos = [item for item in read_items(salida)[0] if item.tipo == "producto"]
    assert [(p.nombre, p.cantidad, p.precio) for p in productos] == [("Silla ejecutiva", 4, 1250.5)]
    assert sorted(extract_images(salida)[0]) == [9]
    assert uso["fotos"] == 1


def test_la_quotation_del_respaldo_se_puede_importar_a_proyectos(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.pdf"
    salida = tmp_path / "quotation.xlsx"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(
        _cotizacion(
            [
                _producto(descripcion=""),
                _producto(nombre="LOCKER", descripcion="- Frente opaco\n- Candado", dimension="=1x1"),
            ]
        )
    )

    convertir_con_llm(fuente, salida, cliente=cliente)

    # El importador de Proyectos exige descripción y rechaza textos que empiezan con = + - @.
    manifiesto, fotos = build_import_manifest(salida.read_bytes(), str(uuid.uuid4()), "quotation.xlsx")
    assert [item["description"] for item in manifiesto["items"]] == ["BENCH P/8PX", "Frente opaco - Candado"]
    assert manifiesto["items"][1]["dimension"] == "1x1"
    assert manifiesto["provider"] == "SUNON"
    assert manifiesto["currency_status"] == "required"
    assert sorted(fotos) == [9, 10]


def test_nota_interna_del_proveedor_se_advierte_y_no_llega_a_la_descripcion(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.pdf"
    salida = tmp_path / "quotation.xlsx"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(_cotizacion([_producto(nombre="LOCKER", nota_interna="$4125 cal 22")]))

    uso = convertir_con_llm(fuente, salida, cliente=cliente)

    producto = next(item for item in read_items(salida)[0] if item.tipo == "producto")
    assert producto.descripcion == "cada cubierta"
    assert uso["advertencias"] == ["Producto 1 (LOCKER) trae una nota fuera de la tabla: $4125 cal 22"]


def test_productos_de_precio_distinto_no_comparten_nombre(tmp_path: Path) -> None:
    # La plantilla da un solo precio a los renglones con el mismo nombre (el de mayor cantidad).
    fuente = tmp_path / "proveedor.pdf"
    salida = tmp_path / "quotation.xlsx"
    _pdf_con_dos_fotos(fuente)
    locker = {"nombre": "LOCKER C/CANDADO", "posicion": 2}
    cliente = ClienteFalso(
        _cotizacion(
            [
                _producto(**locker, descripcion="", dimension=".90x.45x1.00", cantidad=32, precio_unitario=3790.0),
                _producto(**locker, descripcion="Frente acrilico\nCon chapa", cantidad=89, precio_unitario=0),
                _producto(**locker, descripcion="Frente opaco", cantidad=15, precio_unitario=5424.0),
                _producto(nombre="SILLA", cantidad=2, precio_unitario=100.0),
                _producto(nombre="SILLA", cantidad=5, precio_unitario=100.0),
            ]
        )
    )

    convertir_con_llm(fuente, salida, cliente=cliente)

    nombres = [item.nombre for item in read_items(salida)[0] if item.tipo == "producto"]
    assert nombres == [
        "LOCKER C/CANDADO - .90x.45x1.00",
        "LOCKER C/CANDADO - Frente acrilico",
        "LOCKER C/CANDADO - Frente opaco",
        "SILLA",
        "SILLA",
    ]


@pytest.mark.parametrize(
    "productos",
    [
        [],
        [_producto(cantidad=0)],
        [_producto(precio_unitario=-1)],
        [_producto(nombre=" ", codigo="")],
    ],
)
def test_rechaza_extraccion_invalida_sin_escribir_quotation(tmp_path: Path, productos) -> None:
    fuente = tmp_path / "proveedor.pdf"
    salida = tmp_path / "quotation.xlsx"
    _pdf_con_dos_fotos(fuente)

    with pytest.raises(ValueError):
        convertir_con_llm(fuente, salida, cliente=ClienteFalso(_cotizacion(productos)))

    assert not salida.exists()


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_rechaza_respuesta_incompleta_del_modelo(tmp_path: Path, stop_reason: str) -> None:
    fuente = tmp_path / "proveedor.pdf"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(_cotizacion([_producto()]), stop_reason=stop_reason)

    with pytest.raises(ValueError, match=stop_reason):
        convertir_con_llm(fuente, tmp_path / "q.xlsx", cliente=cliente)


def test_advierte_precio_faltante_y_subtotal_distinto(tmp_path: Path) -> None:
    fuente = tmp_path / "proveedor.pdf"
    _pdf_con_dos_fotos(fuente)
    cliente = ClienteFalso(
        _cotizacion(
            [_producto(cantidad=2, precio_unitario=100.0), _producto(nombre="LOCKER ACRILICO", precio_unitario=0)],
            subtotal=500.0,
        )
    )

    uso = convertir_con_llm(fuente, tmp_path / "q.xlsx", cliente=cliente)

    assert len(uso["advertencias"]) == 2
    assert "LOCKER ACRILICO" in uso["advertencias"][0]
    assert "500.00" in uso["advertencias"][1]


def _job(fuente: Path, extension: str) -> dict:
    return {
        "id": "job-llm",
        "usuario_id": 7,
        "input_path": f"users/7/jobs/job-llm/input{extension}",
        "metadata": {"input_extension": extension, "file_size": fuente.stat().st_size},
    }


def _excel_sin_hoja_quotation(ruta: Path) -> Path:
    libro = Workbook()
    libro.active.title = "Hoja1"
    libro.active.append(["Silla", 4, 1250.5])
    libro.save(ruta)
    libro.close()
    return ruta


def _respaldo_falso(vistos: dict):
    def convertir(fuente, salida_xlsx, _referencia):
        vistos["fuente"] = Path(fuente).name
        libro = Workbook()
        libro.active.title = "Quotation"
        libro.save(salida_xlsx)
        libro.close()
        return {"modelo": "claude-haiku-5-5", "costo_usd": 0.00035}

    return convertir


def test_worker_sin_bandera_conserva_el_comportamiento_actual(monkeypatch, tmp_path: Path) -> None:
    fuente = _excel_sin_hoja_quotation(tmp_path / "input.xlsx")
    monkeypatch.setattr(quote_worker, "QUOTE_LLM_FALLBACK_MODEL", "")
    monkeypatch.setattr(quote_worker, "_convertir_con_llm", lambda *_a: pytest.fail("no debe llamar al modelo"))
    job = _job(fuente, ".xlsx")

    preparado = quote_worker._prepare_generator_input(job, fuente, tmp_path)

    assert preparado.parser_source == fuente
    assert "llm_fallback" not in job["metadata"]


def test_worker_con_bandera_no_llama_al_modelo_si_la_quotation_es_estandar(monkeypatch, tmp_path: Path) -> None:
    fuente = tmp_path / "input.xlsx"
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Quotation"
    for columna, texto in enumerate(["No.", "Item Name", "Photo", "Description", "Dimension"], start=1):
        hoja.cell(row=7, column=columna).value = texto
    hoja.cell(row=8, column=1).value = 1
    hoja.cell(row=8, column=2).value = "Silla"
    libro.save(fuente)
    libro.close()
    monkeypatch.setattr(quote_worker, "QUOTE_LLM_FALLBACK_MODEL", "haiku")
    monkeypatch.setattr(quote_worker, "_convertir_con_llm", lambda *_a: pytest.fail("no debe llamar al modelo"))

    preparado = quote_worker._prepare_generator_input(_job(fuente, ".xlsx"), fuente, tmp_path)

    assert preparado.parser_source == fuente


def test_worker_con_bandera_usa_el_modelo_si_el_excel_no_es_quotation(monkeypatch, tmp_path: Path) -> None:
    fuente = _excel_sin_hoja_quotation(tmp_path / "input.xlsx")
    vistos: dict = {}
    monkeypatch.setattr(quote_worker, "QUOTE_LLM_FALLBACK_MODEL", "haiku")
    monkeypatch.setattr(quote_worker, "_convertir_con_llm", _respaldo_falso(vistos))
    job = _job(fuente, ".xlsx")

    preparado = quote_worker._prepare_generator_input(job, fuente, tmp_path)

    assert vistos["fuente"] == "input.xlsx"
    assert preparado.parser_source.name == "quotation_from_llm.xlsx"
    assert preparado.original_quotation == preparado.parser_source
    assert job["metadata"]["llm_fallback"] == {"modelo": "claude-haiku-5-5", "costo_usd": 0.00035}


def test_worker_pdf_que_falla_solo_usa_el_modelo_con_bandera(monkeypatch, tmp_path: Path) -> None:
    fuente = tmp_path / "input.pdf"
    _pdf_con_dos_fotos(fuente)

    def convertidor_falla(*_a):
        raise ValueError("No se detectaron productos en el PDF")

    vistos: dict = {}
    monkeypatch.setattr(quote_worker, "_convert_pdf_to_quotation", convertidor_falla)
    monkeypatch.setattr(quote_worker, "_template_path_for_job", lambda _job: str(tmp_path / "plantilla.xlsx"))
    monkeypatch.setattr(quote_worker, "_convertir_con_llm", _respaldo_falso(vistos))

    monkeypatch.setattr(quote_worker, "QUOTE_LLM_FALLBACK_MODEL", "")
    with pytest.raises(ValueError, match="No se detectaron productos"):
        quote_worker._prepare_generator_input(_job(fuente, ".pdf"), fuente, tmp_path)
    assert vistos == {}

    monkeypatch.setattr(quote_worker, "QUOTE_LLM_FALLBACK_MODEL", "sonnet")
    job = _job(fuente, ".pdf")
    preparado = quote_worker._prepare_generator_input(job, fuente, tmp_path)

    assert vistos["fuente"] == "input.pdf"
    assert preparado.parser_source.name == "quotation_from_llm.xlsx"
    assert job["metadata"]["pdf_converted"] is True
    assert job["metadata"]["llm_fallback"]["modelo"] == "claude-haiku-5-5"
