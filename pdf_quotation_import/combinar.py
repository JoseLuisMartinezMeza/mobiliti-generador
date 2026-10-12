"""Combina varias cotizaciones de proveedor en una sola Quotation importable a Proyectos.

Un Proyecto admite una sola Quotation de origen. Aquí cada renglón conserva su proveedor y su
moneda en las columnas Supplier y Original Currency, que el importador de Proyectos ya entiende.
La primera fuente es la base y se conserva tal cual; las demás se agregan al final.

Uso:
    python -m pdf_quotation_import.combinar --salida combinada.xlsx --fuente fabrica.xlsx Sunon USD --fuente local.pdf "Proveedor local" MXN
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import posixpath
import re
import shutil
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string

from mobiliti_saas.quote_engine.images import _worksheet_path_for_sheet, extract_images
from mobiliti_saas.quote_engine.parser import Q_HEADER_ROW, col_index, detect_columns, read_items
from mobiliti_saas.quote_engine.quotation_import import ALLOWED_IMPORT_CURRENCIES, build_import_manifest

from .converter import add_product_image, apply_template_row, copy_cell_style
from .llm_fallback import MODELOS, convertir_con_llm, es_quotation_estandar


def combinar_quotations(
    fuentes: list[tuple[str | Path, str, str]],
    salida_xlsx: str | Path,
    modelo: str = "haiku",
    cliente=None,
) -> dict:
    """Escribe una Quotation con los renglones de todas las fuentes (archivo, proveedor, moneda)."""
    if not fuentes:
        raise ValueError("Se requiere al menos una fuente")
    salida = Path(salida_xlsx)
    resumen_fuentes: list[dict] = []
    advertencias: list[str] = []
    carpetas_fotos: list[str] = []
    with tempfile.TemporaryDirectory(prefix="combinar_quote_") as carpeta:
        estandar: list[tuple[Path, str, str]] = []
        for indice, (archivo, proveedor, moneda) in enumerate(fuentes):
            archivo, proveedor, moneda = Path(archivo), proveedor.strip(), moneda.strip().upper()
            # Sin proveedor ni moneda solo se anexan renglones: así importa la web, que guarda ambos por línea.
            if bool(proveedor) != bool(moneda) or (moneda and moneda not in ALLOWED_IMPORT_CURRENCIES):
                raise ValueError(f"Proveedor o moneda inválidos para {archivo.name}")
            ruta, costo = archivo, 0.0
            if not es_quotation_estandar(archivo):
                ruta = Path(carpeta) / f"fuente_{indice}.xlsx"
                uso = convertir_con_llm(archivo, ruta, modelo=modelo, cliente=cliente)
                costo = uso["costo_usd"]
                advertencias += [f"{archivo.name}: {texto}" for texto in uso["advertencias"]]
            estandar.append((ruta, proveedor, moneda))
            resumen_fuentes.append(
                {
                    "archivo": archivo.name,
                    "proveedor": proveedor,
                    "moneda": moneda,
                    "productos": sum(1 for item in read_items(ruta)[0] if item.tipo == "producto"),
                    "convertida_con_llm": ruta != archivo,
                    "costo_usd": costo,
                }
            )

        ruta_base, proveedor_base, moneda_base = estandar[0]
        libro = load_workbook(ruta_base)
        try:
            hoja = libro["Quotation"]
            columnas = detect_columns(hoja)
            # shortcut: sin etiquetas se descartan Supplier/Original Currency propios de lo anexado; conservarlos si se mezclan ambos usos.
            etiquetar = any(proveedor for _ruta, proveedor, _moneda in estandar)
            if etiquetar:
                col_proveedor = _columna_opcional(hoja, columnas, "proveedor", "Supplier")
                col_moneda = _columna_opcional(hoja, columnas, "moneda_original", "Original Currency")
            col_precio = col_index(columnas, "list_price", columnas.get("unit_price", "J"))
            col_cantidad = col_index(columnas, "cantidad", "G")
            # El parser usa la columna de medidas como volumen cuando la hoja no trae Vol.
            col_volumen = (
                col_index(columnas, "m3", "H") if columnas["m3"] != columnas.get("dimension", "E") else None
            )
            filas_producto = [c.row for c in hoja["A"] if _es_numero(c.value) and c.row > Q_HEADER_ROW]
            filas_categoria = [
                c.row for c in hoja["A"] if isinstance(c.value, str) and c.value.startswith("-")
            ]
            _fijar_resultados(ruta_base, hoja, filas_producto, [col_cantidad, col_precio, col_volumen])
            for fila in filas_producto if proveedor_base else ():
                _escribir_si_vacia(hoja.cell(fila, col_proveedor), proveedor_base)
                _escribir_si_vacia(hoja.cell(fila, col_moneda), moneda_base)

            estilo_producto = [copy_cell_style(hoja.cell(filas_producto[0], c)) for c in range(1, 13)]
            estilo_categoria = (
                [copy_cell_style(hoja.cell(filas_categoria[0], c)) for c in range(1, 13)]
                if filas_categoria
                else None
            )
            alto = hoja.row_dimensions[filas_producto[0]].height or 110
            # Las fotos dentro de la celda se pierden al guardar; se dejan como fotos normales del renglón.
            con_foto = {i.anchor._from.row + 1 for i in hoja._images if hasattr(i.anchor, "_from")}
            for fila_foto, (celda, datos) in _fotos_en_celda(ruta_base).items():
                if fila_foto in filas_producto and fila_foto not in con_foto:
                    hoja[celda].value = None
                    add_product_image(
                        hoja,
                        fila_foto,
                        _guardar_foto(Path(carpeta), f"base_{fila_foto}", datos),
                        hoja.row_dimensions[fila_foto].height or alto,
                    )
            fila = max(c.row for c in hoja["A"] if c.value is not None) + 1
            numero = len(filas_producto)
            filas_agregadas: list[int] = []
            for indice, (ruta, proveedor, moneda) in enumerate(estandar[1:], start=1):
                items, _columnas = read_items(ruta)
                fotos, carpeta_fotos = extract_images(ruta)
                carpetas_fotos.append(carpeta_fotos)
                for fila_foto, (_celda, datos) in _fotos_en_celda(ruta).items():
                    fotos.setdefault(fila_foto, _guardar_foto(Path(carpeta), f"f{indice}_{fila_foto}", datos))
                if items[0].tipo != "categoria":
                    apply_template_row(hoja, fila, estilo_categoria)
                    hoja.cell(fila, 1).value = f"- {proveedor or 'Importados'}"
                    fila += 1
                for item in items:
                    if item.tipo == "categoria":
                        apply_template_row(hoja, fila, estilo_categoria)
                        hoja.cell(fila, 1).value = f"- {item.nombre}"
                    else:
                        numero += 1
                        filas_agregadas.append(fila)
                        apply_template_row(hoja, fila, estilo_producto)
                        hoja.row_dimensions[fila].height = alto
                        hoja.cell(fila, 1).value = numero
                        hoja.cell(fila, 2).value = item.nombre
                        # El importador de Proyectos exige descripción en cada renglón.
                        hoja.cell(fila, col_index(columnas, "descripcion", "D")).value = (
                            item.descripcion or item.nombre
                        )
                        hoja.cell(fila, col_index(columnas, "dimension", "E")).value = item.dimension
                        hoja.cell(fila, col_cantidad).value = item.cantidad
                        hoja.cell(fila, col_precio).value = item.precio
                        if col_volumen and _es_numero(item.m3):
                            hoja.cell(fila, col_volumen).value = item.m3
                        if etiquetar:
                            hoja.cell(fila, col_proveedor).value = item.proveedor or proveedor or None
                            hoja.cell(fila, col_moneda).value = item.moneda_original or moneda or None
                        if item.row in fotos:
                            add_product_image(hoja, fila, Path(fotos[item.row]), alto)
                    fila += 1
            # openpyxl conserva el id de las fotos leídas y numera las nuevas por posición;
            # se igualan todos a su posición porque el motor exige ids únicos.
            for posicion, imagen in enumerate(hoja._images, start=len(hoja._charts) + 1):
                if getattr(imagen.anchor, "pic", None) is not None:
                    imagen.anchor.pic.nvPicPr.cNvPr.id = posicion
            # Se valida antes de entregar para no dejar un archivo que Proyectos rechace.
            temporal = Path(carpeta) / salida.name
            libro.save(temporal)
        finally:
            libro.close()
            for carpeta_fotos in carpetas_fotos:
                shutil.rmtree(carpeta_fotos, ignore_errors=True)
        try:
            manifiesto, fotos_importadas = build_import_manifest(
                temporal.read_bytes(), str(uuid.uuid4()), salida.name
            )
        except ValueError as exc:
            raise ValueError(f"La Quotation combinada no pasa el importador de Proyectos: {exc}") from exc
        shutil.copyfile(temporal, salida)

    return {
        "salida": str(salida),
        "productos": len(manifiesto["items"]),
        "secciones": len(manifiesto["sections"]),
        "fotos": len(fotos_importadas),
        "filas_agregadas": filas_agregadas,
        "fuentes": resumen_fuentes,
        "costo_usd": round(sum(fuente["costo_usd"] for fuente in resumen_fuentes), 6),
        "advertencias": advertencias,
    }


def _guardar_foto(carpeta: Path, nombre: str, datos: bytes) -> Path:
    ruta = carpeta / f"foto_{nombre}"
    ruta.write_bytes(datos)
    return ruta


def _fotos_en_celda(ruta: Path) -> dict[int, tuple[str, bytes]]:
    """Fotos que Excel guarda dentro de la celda ("Colocar en celda"), por fila: (celda, bytes).

    Ni openpyxl ni el extractor del motor las leen. La cadena es:
    celda vm -> metadata.xml -> rdrichvalue.xml -> richValueRel.xml -> su .rels -> xl/media.
    """
    partes = (
        "xl/metadata.xml",
        "xl/richData/rdrichvalue.xml",
        "xl/richData/richValueRel.xml",
        "xl/richData/_rels/richValueRel.xml.rels",
    )
    with zipfile.ZipFile(ruta) as paquete:
        if not set(partes) <= set(paquete.namelist()):
            return {}
        metadatos, valores, relaciones, destinos = (paquete.read(parte).decode("utf-8") for parte in partes)
        hoja = paquete.read(_worksheet_path_for_sheet(paquete, "Quotation")).decode("utf-8")
        de_valor = re.findall(r'<rc [^>]*\bv="(\d+)"', metadatos.partition("<valueMetadata")[2])
        de_futuro = re.findall(r'rvb i="(\d+)"', metadatos)
        de_rico = re.findall(r"<rv [^>]*>\s*<v>(\d+)</v>", valores)
        ids = re.findall(r'<rel r:id="([^"]+)"', relaciones)
        destino_por_id = {rel.get("Id"): rel.get("Target") for rel in ET.fromstring(destinos)}
        fotos: dict[int, tuple[str, bytes]] = {}
        for columna, fila, vm in re.findall(r'<c r="([A-Z]+)(\d+)"[^>]*\bvm="(\d+)"', hoja):
            try:
                indice = int(de_rico[int(de_futuro[int(de_valor[int(vm) - 1])])])
                destino = posixpath.normpath(posixpath.join("xl/richData", destino_por_id[ids[indice]]))
                fotos[int(fila)] = (f"{columna}{fila}", paquete.read(destino))
            except (IndexError, KeyError):
                continue
    return fotos


def _es_numero(valor: object) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _columna_opcional(hoja, columnas: dict[str, str], clave: str, titulo: str) -> int:
    """Índice de la columna; la crea al final si la Quotation no la trae."""
    if clave in columnas:
        return column_index_from_string(columnas[clave])
    indice = hoja.max_column + 1
    hoja.cell(Q_HEADER_ROW, indice).value = titulo
    return indice


def _escribir_si_vacia(celda, valor: str) -> None:
    if celda.value in (None, ""):
        celda.value = valor


def _fijar_resultados(ruta: Path, hoja, filas: list[int], columnas: list[int | None]) -> None:
    """openpyxl no conserva resultados de fórmulas al guardar y el importador exige valores."""
    resultados = None
    for fila in filas:
        for columna in columnas:
            if columna is None or hoja.cell(fila, columna).data_type != "f":
                continue
            if resultados is None:
                resultados = load_workbook(ruta, data_only=True)["Quotation"]
            resultado = resultados.cell(fila, columna).value
            if resultado is not None:
                hoja.cell(fila, columna).value = resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Combina cotizaciones (Quotation, PDF o Excel) en una Quotation para Proyectos."
    )
    parser.add_argument(
        "--fuente", "-f", nargs=3, action="append", required=True,
        metavar=("ARCHIVO", "PROVEEDOR", "MONEDA"),
        help="Repetible; la primera es la base. Moneda: " + ", ".join(sorted(ALLOWED_IMPORT_CURRENCIES)),
    )
    parser.add_argument("--salida", "-o", required=True, help="Ruta del XLSX combinado")
    parser.add_argument("--modelo", "-m", choices=sorted(MODELOS), default="haiku")
    args = parser.parse_args(argv)

    resumen = combinar_quotations([tuple(fuente) for fuente in args.fuente], args.salida, args.modelo)

    sys.stdout.reconfigure(errors="replace")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    print(f"[OK] Quotation combinada: {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
