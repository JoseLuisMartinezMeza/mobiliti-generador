from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
import re
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

from openpyxl import load_workbook
from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.images import extract_images  # noqa: E402
from mobiliti_saas.quote_engine.official_composer import _next_picture_id  # noqa: E402
from mobiliti_saas.quote_engine.parser import read_items  # noqa: E402
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest  # noqa: E402
from pdf_quotation_import import combinar  # noqa: E402
from pdf_quotation_import.combinar import combinar_quotations  # noqa: E402
from quotation_import_fixtures import write_import_fixture  # noqa: E402
from test_llm_fallback import ClienteFalso, _cotizacion, _pdf_con_dos_fotos, _producto  # noqa: E402


def _manifiesto(ruta: Path) -> tuple[dict, dict]:
    """El mismo validador que usa Proyectos al importar una Quotation."""
    return build_import_manifest(ruta.read_bytes(), str(uuid.uuid4()), ruta.name)


def _sin_modelo(monkeypatch) -> None:
    monkeypatch.setattr(combinar, "convertir_con_llm", lambda *_a, **_k: pytest.fail("no debe llamar al modelo"))


def test_combina_fabrica_y_pdf_local_en_una_quotation_que_proyectos_importa(tmp_path: Path) -> None:
    fabrica = write_import_fixture(tmp_path / "fabrica.xlsx")
    local = tmp_path / "local.pdf"
    _pdf_con_dos_fotos(local)
    salida = tmp_path / "combinada.xlsx"
    cliente = ClienteFalso(
        _cotizacion([_producto(), _producto(nombre="LOCKER", cantidad=32, precio_unitario=3790.0)])
    )

    resumen = combinar_quotations(
        [(fabrica, "Sunon", "USD"), (local, "Proveedor local", "MXN")], salida, cliente=cliente
    )

    original, _fotos_original = _manifiesto(fabrica)
    manifiesto, fotos = _manifiesto(salida)
    items = manifiesto["items"]
    assert [(item["provider"], item["source_currency"]) for item in items] == (
        [("Sunon", "USD")] * 7 + [("Proveedor local", "MXN")] * 2
    )
    assert manifiesto["currency_status"] == "detected"
    assert [seccion["title"] for seccion in manifiesto["sections"]] == [
        "SALA DE JUNTAS SECUNDARIO", "MUESTRAS", "CONCEJO", "PRODUCTOS",
    ]
    # Los renglones de fábrica no cambian de fila ni de contenido.
    campos = ("source_row", "name", "description", "dimension", "quantity", "unit_price")
    assert [[item[c] for c in campos] for item in items[:7]] == [
        [item[c] for c in campos] for item in original["items"]
    ]
    assert [(item["name"], Decimal(item["quantity"]), Decimal(item["unit_price"])) for item in items[7:]] == [
        ("BENCH P/8PX", 15, 37957),
        ("LOCKER", 32, 3790),
    ]
    assert len(fotos) == 9
    volumenes = [item.m3 for item in read_items(salida)[0] if item.tipo == "producto"]
    assert volumenes == [0.25] * 7 + [0, 0]

    assert resumen["productos"] == 9 and resumen["secciones"] == 4 and resumen["fotos"] == 9
    assert [(f["archivo"], f["productos"], f["convertida_con_llm"]) for f in resumen["fuentes"]] == [
        ("fabrica.xlsx", 7, False),
        ("local.pdf", 2, True),
    ]
    assert resumen["costo_usd"] == pytest.approx(0.00035)


def test_fuentes_estandar_se_combinan_sin_llamar_al_modelo(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    primera = write_import_fixture(tmp_path / "a" / "sunon.xlsx")
    segunda = write_import_fixture(tmp_path / "b" / "otra.xlsx")
    salida = tmp_path / "combinada.xlsx"
    _sin_modelo(monkeypatch)

    resumen = combinar_quotations([(primera, "Sunon", "USD"), (segunda, "Otra fabrica", "EUR")], salida)

    manifiesto, fotos = _manifiesto(salida)
    assert [(item["provider"], item["source_currency"]) for item in manifiesto["items"]] == (
        [("Sunon", "USD")] * 7 + [("Otra fabrica", "EUR")] * 7
    )
    assert len(manifiesto["sections"]) == 6
    assert len(fotos) == 14
    assert resumen["costo_usd"] == 0


def test_una_sola_fuente_no_estandar_queda_con_proveedor_y_moneda(tmp_path: Path) -> None:
    local = tmp_path / "local.pdf"
    _pdf_con_dos_fotos(local)
    salida = tmp_path / "local.xlsx"

    combinar_quotations([(local, "Proveedor local", "MXN")], salida, cliente=ClienteFalso(_cotizacion([_producto()])))

    manifiesto, _fotos = _manifiesto(salida)
    assert [(item["provider"], item["source_currency"]) for item in manifiesto["items"]] == [("Proveedor local", "MXN")]
    assert manifiesto["currency_status"] == "detected"


def test_respeta_la_moneda_que_el_renglon_ya_trae(monkeypatch, tmp_path: Path) -> None:
    fabrica = write_import_fixture(tmp_path / "fabrica.xlsx", currency="EUR")
    salida = tmp_path / "combinada.xlsx"
    _sin_modelo(monkeypatch)

    combinar_quotations([(fabrica, "Sunon", "USD")], salida)

    manifiesto, _fotos = _manifiesto(salida)
    assert {item["source_currency"] for item in manifiesto["items"]} == {"EUR"}
    encabezados = [celda.value for celda in load_workbook(salida)["Quotation"][7]]
    assert encabezados.count("Original Currency") == 1 and encabezados.count("Supplier") == 1


def test_precio_con_formula_conserva_su_resultado(monkeypatch, tmp_path: Path) -> None:
    fabrica = write_import_fixture(tmp_path / "fabrica.xlsx")
    libro = load_workbook(fabrica)
    libro["Quotation"]["J9"] = "=50*2+23"
    libro.save(fabrica)
    _guardar_resultado_de_formula(fabrica, "J9", 123)
    salida = tmp_path / "combinada.xlsx"
    _sin_modelo(monkeypatch)

    combinar_quotations([(fabrica, "Sunon", "USD")], salida)

    manifiesto, _fotos = _manifiesto(salida)
    assert Decimal(manifiesto["items"][0]["unit_price"]) == 123


def test_las_fotos_agregadas_no_repiten_el_id_de_las_de_la_base(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    fabrica = write_import_fixture(tmp_path / "fabrica.xlsx")
    # Una Quotation real trae ids salteados; openpyxl los conserva y numera las fotos nuevas por posición.
    libro = load_workbook(fabrica)
    for posicion, imagen in enumerate(libro["Quotation"]._images, start=1):
        imagen.anchor.pic.nvPicPr.cNvPr.id = posicion * 2
    libro.save(fabrica)
    segunda = write_import_fixture(tmp_path / "b" / "otra.xlsx")
    salida = tmp_path / "combinada.xlsx"
    _sin_modelo(monkeypatch)

    combinar_quotations([(fabrica, "Sunon", "USD"), (segunda, "Otra fabrica", "EUR")], salida)

    with zipfile.ZipFile(salida) as paquete:
        dibujo = ET.fromstring(paquete.read("xl/drawings/drawing1.xml"))
    # La misma regla con la que el motor rechaza la Quotation de un Proyecto.
    assert _next_picture_id(dibujo) == 15


def test_foto_guardada_dentro_de_la_celda_queda_como_foto_del_renglon(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    fabrica = write_import_fixture(tmp_path / "fabrica.xlsx")
    _pasar_foto_a_la_celda(fabrica, fila=9, color=(250, 10, 10))
    agregada = write_import_fixture(tmp_path / "b" / "otra.xlsx")
    _pasar_foto_a_la_celda(agregada, fila=9, color=(10, 250, 10))
    assert 9 not in extract_images(fabrica)[0]  # el motor no lee fotos dentro de la celda
    salida = tmp_path / "combinada.xlsx"
    _sin_modelo(monkeypatch)

    combinar_quotations([(fabrica, "Sunon", "USD"), (agregada, "Otra fabrica", "EUR")], salida)

    fotos = extract_images(salida)[0]
    assert len(fotos) == 14
    assert Image.open(fotos[9]).convert("RGB").getpixel((5, 5)) == (250, 10, 10)
    # La segunda fuente empieza en la fila 18; su producto de la fila 9 queda en la 19.
    assert Image.open(fotos[19]).convert("RGB").getpixel((5, 5)) == (10, 250, 10)
    assert load_workbook(salida)["Quotation"]["C9"].value is None  # sin el #VALUE! de la celda


def test_sin_proveedor_ni_moneda_solo_agrega_renglones_e_informa_cuales(monkeypatch, tmp_path: Path) -> None:
    # Así anexa la web: el proveedor y la moneda viven en las líneas del Proyecto, no en el archivo.
    (tmp_path / "b").mkdir()
    base = write_import_fixture(tmp_path / "base.xlsx")
    otra = write_import_fixture(tmp_path / "b" / "otra.xlsx")
    salida = tmp_path / "combinada.xlsx"
    _sin_modelo(monkeypatch)

    resumen = combinar_quotations([(base, "", ""), (otra, "", "")], salida)

    manifiesto, _fotos = _manifiesto(salida)
    assert len(manifiesto["items"]) == 14
    assert manifiesto["currency_status"] == "required"
    encabezados = [celda.value for celda in load_workbook(salida)["Quotation"][7]]
    assert "Supplier" not in encabezados and "Original Currency" not in encabezados
    assert resumen["filas_agregadas"] == [item["source_row"] for item in manifiesto["items"][7:]]
    assert resumen["filas_agregadas"][0] == 19


@pytest.mark.parametrize("proveedor, moneda", [("Sunon", "JPY"), (" ", "USD"), ("Sunon", "")])
def test_rechaza_proveedor_o_moneda_invalidos(tmp_path: Path, proveedor: str, moneda: str) -> None:
    fabrica = write_import_fixture(tmp_path / "fabrica.xlsx")
    salida = tmp_path / "combinada.xlsx"

    with pytest.raises(ValueError):
        combinar_quotations([(fabrica, proveedor, moneda)], salida)

    assert not salida.exists()


def _pasar_foto_a_la_celda(ruta: Path, fila: int, color: tuple[int, int, int]) -> None:
    """Quita la foto flotante del renglón y la deja como la guarda Excel con "Colocar en celda"."""
    libro = load_workbook(ruta)
    hoja = libro["Quotation"]
    hoja._images = [imagen for imagen in hoja._images if imagen.anchor._from.row + 1 != fila]
    libro.save(ruta)
    with zipfile.ZipFile(ruta) as paquete:
        partes = {nombre: paquete.read(nombre) for nombre in paquete.namelist()}
    xml = partes["xl/worksheets/sheet1.xml"].decode("utf-8")
    xml, cambios = re.subn(
        rf'(<c r="B{fila}".*?</c>)', rf'\g<1><c r="C{fila}" t="e" vm="1"><v>#VALUE!</v></c>', xml, flags=re.S
    )
    assert cambios == 1
    partes["xl/worksheets/sheet1.xml"] = xml.encode("utf-8")
    foto = BytesIO()
    Image.new("RGB", (40, 30), color).save(foto, "PNG")
    richdata = "http://schemas.microsoft.com/office/spreadsheetml/2017/richdata"
    # Las partes no se registran en [Content_Types].xml; basta para quien las lee por su ruta.
    partes.update(
        {
            "xl/media/encelda.png": foto.getvalue(),
            "xl/metadata.xml": (
                '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                f'xmlns:xlrd="{richdata}"><futureMetadata name="XLRICHVALUE" count="1"><bk><extLst><ext>'
                '<xlrd:rvb i="0"/></ext></extLst></bk></futureMetadata>'
                '<valueMetadata count="1"><bk><rc t="1" v="0"/></bk></valueMetadata></metadata>'
            ).encode(),
            "xl/richData/rdrichvalue.xml": (
                f'<rvData xmlns="{richdata}" count="1"><rv s="0"><v>0</v><v>5</v></rv></rvData>'
            ).encode(),
            "xl/richData/richValueRel.xml": (
                '<richValueRels xmlns="http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<rel r:id="rId1"/></richValueRels>'
            ).encode(),
            "xl/richData/_rels/richValueRel.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/image" Target="../media/encelda.png"/></Relationships>'
            ).encode(),
        }
    )
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as paquete:
        for nombre, contenido in partes.items():
            paquete.writestr(nombre, contenido)


def _guardar_resultado_de_formula(ruta: Path, celda: str, resultado: int) -> None:
    """openpyxl no escribe resultados de fórmulas; se agrega como lo dejaría Excel al guardar."""
    with zipfile.ZipFile(ruta) as paquete:
        partes = {nombre: paquete.read(nombre) for nombre in paquete.namelist()}
    hoja = partes["xl/worksheets/sheet1.xml"].decode("utf-8")
    hoja, cambios = re.subn(
        rf'(<c r="{celda}"[^>]*>\s*<f>[^<]*</f>)\s*<v\s*/?>(?:</v>)?',
        rf"\g<1><v>{resultado}</v>",
        hoja,
    )
    assert cambios == 1
    partes["xl/worksheets/sheet1.xml"] = hoja.encode("utf-8")
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as paquete:
        for nombre, contenido in partes.items():
            paquete.writestr(nombre, contenido)
