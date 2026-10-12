"""Respaldo con Claude para cotizaciones de proveedor que no traen el layout Quotation.

El modelo solo extrae los datos; la hoja la escribe el mismo `write_workbook` del importador PDF.

Uso:
    python -m pdf_quotation_import.llm_fallback "cotizacion.pdf" --modelo haiku
"""

from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile

from openpyxl import load_workbook
from PIL import Image
from pydantic import BaseModel
import pymupdf

from mobiliti_saas.quote_engine.parser import read_items

from .converter import ParsedProduct, load_style_source, write_workbook


# alias -> (id del modelo, USD por millón de tokens de entrada, de salida). Tarifas de 2026-10.
MODELOS = {
    "haiku": ("claude-haiku-5-5", 0.10, 0.50),
    "sonnet": ("claude-sonnet-5-5", 2.00, 10.00),
}
MAX_CARACTERES_XLSX = 300_000

INSTRUCCIONES = """Extrae los productos de la cotización de proveedor recibida para rellenar una hoja Quotation.

- Un elemento por renglón de producto, en el orden del documento. No inventes ni omitas productos, y no incluyas subtotales, fletes, impuestos ni notas sueltas.
- Copia textos, medidas y números tal como aparecen, sin traducir. Si un dato no aparece usa cadena vacía, o 0 si es numérico.
- nombre: nombre completo del producto tal como aparece en su renglón, con la variante que lo distingue (por ejemplo "LOCKER. FRENTE OPACO"). Dos productos distintos nunca llevan el mismo nombre.
- descripcion: especificaciones, materiales y acabados del renglón, copiados tal cual; vacía si el renglón no trae más texto. Nunca incluyas precios ni importes: la descripción la lee el cliente final.
- nota_interna: texto suelto junto al renglón que no describe el producto, como un precio alterno o una anotación fuera de las columnas; vacía si no hay.
- codigo: clave o modelo del proveedor, si existe.
- categoria: encabezado de sección bajo el que aparece el producto, si existe.
- dimension: medidas como aparecen, por ejemplo 1.80x.80x.75.
- precio_unitario: precio por unidad antes de impuestos y descuentos. Si el renglón solo trae importe total, divídelo entre la cantidad. Si no trae precio, 0.
- hoja y posicion: en Excel, el nombre de la hoja y el número de fila del producto. En PDF, hoja vacía y el número de página.
- moneda: código ISO si el documento la indica; vacío si no.
- subtotal_documento: suma de los productos antes de impuestos tal como viene impresa; 0 si no aparece."""


class ProductoExtraido(BaseModel):
    categoria: str
    codigo: str
    nombre: str
    descripcion: str
    dimension: str
    color: str
    cantidad: float
    precio_unitario: float
    hoja: str
    posicion: int
    nota_interna: str


class CotizacionExtraida(BaseModel):
    proveedor: str
    referencia: str
    fecha: str
    cliente: str
    moneda: str
    subtotal_documento: float
    productos: list[ProductoExtraido]


def convertir_con_llm(
    fuente: str | Path,
    salida_xlsx: str | Path,
    referencia_xlsx: str | Path | None = None,
    modelo: str = "haiku",
    cliente=None,
) -> dict:
    """Extrae la cotización con Claude, escribe la Quotation estándar y devuelve tokens y costo."""
    fuente = Path(fuente)
    id_modelo, usd_entrada, usd_salida = MODELOS[modelo]
    es_pdf = fuente.suffix.lower() == ".pdf"
    if es_pdf:
        fotos = _fotos_pdf(fuente)
        contenido = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(fuente.read_bytes()).decode("ascii"),
                },
            },
            {"type": "text", "text": "Extrae esta cotización."},
        ]
    else:
        volcado, fotos = _volcar_xlsx(fuente)
        contenido = [{"type": "text", "text": volcado}]

    if cliente is None:
        import anthropic

        cliente = anthropic.Anthropic()
    # shortcut: una sola llamada con 16k tokens de salida (~80 productos); streaming o por páginas si llegan más grandes.
    respuesta = cliente.messages.parse(
        model=id_modelo,
        max_tokens=16000,
        system=INSTRUCCIONES,
        messages=[{"role": "user", "content": contenido}],
        output_format=CotizacionExtraida,
    )
    if respuesta.stop_reason != "end_turn" or respuesta.parsed_output is None:
        raise ValueError(f"El modelo no completó la extracción ({respuesta.stop_reason})")
    extraida = respuesta.parsed_output
    advertencias = _validar(extraida)

    with tempfile.TemporaryDirectory(prefix="llm_quote_") as carpeta:
        productos = _a_productos(extraida, fotos, es_pdf, Path(carpeta))
        write_workbook(
            productos,
            {
                "seller": _texto_seguro(extraida.proveedor) or fuente.stem,
                "project": extraida.referencia,
                "buyer": extraida.cliente,
                "invoice": extraida.referencia,
                "date": extraida.fecha,
            },
            Path(salida_xlsx),
            load_style_source(Path(referencia_xlsx) if referencia_xlsx else None),
        )

    entrada, salida = respuesta.usage.input_tokens, respuesta.usage.output_tokens
    # Haiku 5.5 cobra 5x ($0.50/$2.50) cuando el prompt pasa de 100K tokens.
    factor = 5 if modelo == "haiku" and entrada > 100_000 else 1
    return {
        "modelo": id_modelo,
        "productos": len(productos),
        "fotos": sum(1 for producto in productos if producto.image_path),
        "moneda": extraida.moneda,
        "tokens_entrada": entrada,
        "tokens_salida": salida,
        "costo_usd": round((entrada * usd_entrada + salida * usd_salida) * factor / 1_000_000, 6),
        "advertencias": advertencias,
    }


def es_quotation_estandar(ruta: str | Path) -> bool:
    """True si el parser del motor ya lee la hoja Quotation con al menos un producto."""
    # shortcut: solo detecta hoja ausente o cero productos; validar columnas si llegan layouts que parsean basura.
    try:
        items, _columnas = read_items(ruta)
    except Exception:
        return False
    return any(item.tipo == "producto" for item in items)


def _fotos_pdf(ruta: Path) -> dict[tuple[str, int], list[bytes]]:
    """Fotos de cada página, de arriba hacia abajo."""
    fotos: dict[tuple[str, int], list[bytes]] = {}
    with pymupdf.open(ruta) as doc:
        for numero, pagina in enumerate(doc, start=1):
            bloques = [b for b in pagina.get_text("dict")["blocks"] if b["type"] == 1 and b.get("image")]
            bloques.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
            fotos[("", numero)] = [b["image"] for b in bloques]
    return fotos


def _volcar_xlsx(ruta: Path) -> tuple[str, dict[tuple[str, int], list[bytes]]]:
    """Celdas no vacías con su fila, y las fotos ancladas a cada fila."""
    lineas: list[str] = []
    fotos: dict[tuple[str, int], list[bytes]] = {}
    libro = load_workbook(ruta, data_only=True)
    try:
        for hoja in libro.worksheets:
            lineas.append(f"## Hoja: {hoja.title}")
            for fila in hoja.iter_rows():
                celdas = [f"{c.column_letter}={c.value}" for c in fila if c.value is not None]
                if celdas:
                    lineas.append(f"fila {fila[0].row}: " + " | ".join(celdas))
            for imagen in hoja._images:
                ancla = getattr(imagen.anchor, "_from", None)
                if ancla is not None:
                    fotos.setdefault((hoja.title, ancla.row + 1), []).append(imagen._data())
    finally:
        libro.close()
    volcado = "\n".join(lineas)
    if len(volcado) > MAX_CARACTERES_XLSX:
        raise ValueError("El Excel es demasiado grande para el respaldo con LLM")
    return volcado, fotos


def _validar(extraida: CotizacionExtraida) -> list[str]:
    """La salida del modelo no es confiable: se rechaza lo inválido y se advierte lo dudoso."""
    if not extraida.productos:
        raise ValueError("El modelo no encontró productos en el archivo")
    advertencias: list[str] = []
    for numero, producto in enumerate(extraida.productos, start=1):
        if (
            not (_texto_seguro(producto.nombre) or _texto_seguro(producto.codigo))
            or producto.cantidad <= 0
            or producto.precio_unitario < 0
        ):
            raise ValueError(f"El modelo extrajo datos inválidos en el producto {numero}")
        if producto.precio_unitario == 0:
            advertencias.append(f"Producto {numero} sin precio: {producto.nombre}")
        if producto.nota_interna.strip():
            advertencias.append(
                f"Producto {numero} ({producto.nombre}) trae una nota fuera de la tabla: {producto.nota_interna.strip()}"
            )
    suma = sum(p.cantidad * p.precio_unitario for p in extraida.productos)
    subtotal = extraida.subtotal_documento
    if subtotal > 0 and abs(suma - subtotal) > 0.01 * subtotal:
        advertencias.append(
            f"La suma extraída ({suma:,.2f}) no coincide con el subtotal del documento ({subtotal:,.2f})"
        )
    return advertencias


def _a_productos(
    extraida: CotizacionExtraida,
    fotos: dict[tuple[str, int], list[bytes]],
    es_pdf: bool,
    carpeta: Path,
) -> list[ParsedProduct]:
    productos: list[ParsedProduct] = []
    por_ubicacion: dict[tuple[str, int], list[ParsedProduct]] = {}
    nombres = _nombres_distintos(extraida.productos)
    for numero, extraido in enumerate(extraida.productos, start=1):
        cantidad = extraido.cantidad
        codigo, nombre = _texto_seguro(extraido.codigo), nombres[numero - 1]
        producto = ParsedProduct(
            number=numero,
            code=codigo,
            category=_texto_seguro(extraido.categoria) or "PRODUCTOS",
            quantity=int(cantidad) if cantidad == int(cantidad) else cantidad,
            unit_price=extraido.precio_unitario,
            total_price=cantidad * extraido.precio_unitario,
            detail_lines=[],
            name=nombre,
            # El importador de Proyectos exige descripción en cada renglón.
            description=_texto_seguro(extraido.descripcion) or nombre or codigo,
            dimension=_texto_seguro(extraido.dimension),
            color=extraido.color,
        )
        productos.append(producto)
        por_ubicacion.setdefault(("" if es_pdf else extraido.hoja, extraido.posicion), []).append(producto)

    # shortcut: solo se asignan fotos si la página o fila trae tantas como productos; pedir coordenadas al modelo si no alcanza.
    for ubicacion, grupo in por_ubicacion.items():
        candidatas = fotos.get(ubicacion, [])
        if len(candidatas) != len(grupo):
            continue
        for producto, datos in zip(grupo, candidatas):
            if _es_imagen(datos):
                producto.image_path = carpeta / f"foto_{producto.number}"
                producto.image_path.write_bytes(datos)
    return productos


def _nombres_distintos(extraidos: list[ProductoExtraido]) -> list[str]:
    """La plantilla da un solo precio a los renglones con el mismo nombre (el de mayor cantidad),
    así que dos productos de precio distinto no pueden compartirlo."""
    def detalle(extraido: ProductoExtraido) -> str:
        texto = _texto_seguro(extraido.descripcion) or _texto_seguro(extraido.dimension)
        return texto.splitlines()[0][:80] if texto else ""

    nombres = [_texto_seguro(extraido.nombre) for extraido in extraidos]
    detalles = [detalle(extraido) for extraido in extraidos]
    grupos: dict[tuple[str, str], list[int]] = {}
    for indice, extraido in enumerate(extraidos):
        grupos.setdefault((_texto_seguro(extraido.codigo), nombres[indice]), []).append(indice)
    for indices in grupos.values():
        if len({extraidos[i].precio_unitario for i in indices}) < 2:
            continue
        for orden, i in enumerate(indices, start=1):
            unico = detalles[i] and sum(1 for j in indices if detalles[j] == detalles[i]) == 1
            nombres[i] = f"{nombres[i]} - {detalles[i]}" if unico else f"{nombres[i]} ({orden})"
    return nombres


def _texto_seguro(valor: str) -> str:
    """Quita el arranque con = + - @, que el importador de Proyectos rechaza por parecer fórmula."""
    return valor.strip().lstrip("=+-@ ").strip()


def _es_imagen(datos: bytes) -> bool:
    try:
        Image.open(BytesIO(datos)).verify()
    except Exception:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convierte una cotización no estándar a Quotation con Claude.")
    parser.add_argument("fuente", help="PDF o XLSX del proveedor")
    parser.add_argument("--modelo", "-m", choices=sorted(MODELOS), default="haiku")
    parser.add_argument("--salida", "-o", help="Ruta del XLSX Quotation generado")
    parser.add_argument("--referencia", "-r", help="XLSX de ejemplo para copiar formato base")
    args = parser.parse_args(argv)

    fuente = Path(args.fuente)
    salida = Path(args.salida) if args.salida else fuente.with_name(f"{fuente.stem}-Quotation-{args.modelo}.xlsx")
    uso = convertir_con_llm(fuente, salida, args.referencia, args.modelo)

    sys.stdout.reconfigure(errors="replace")
    print(json.dumps(uso, ensure_ascii=False, indent=2))
    print(f"[OK] Quotation generada: {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
