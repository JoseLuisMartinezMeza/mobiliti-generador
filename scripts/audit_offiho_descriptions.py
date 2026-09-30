"""Compara las fichas locales Offiho con la presentación del carrito real."""
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.catalog_cart import catalog_quotation_item_text
from mobiliti_saas.quote_engine.customer_descriptions import construir_descripcion_catalogo
from mobiliti_saas.quote_engine.offiho_catalog import build_offiho_cart_payload, load_offiho_catalog
from mobiliti_saas.quote_engine.offiho_descriptions import nombre_producto_offiho, redactar_descripcion_offiho


def auditar() -> dict:
    catalogo = load_offiho_catalog()
    articulos = []
    for item in catalogo["items"]:
        carrito = build_offiho_cart_payload(
            [{"inventory_key": item.inventory_key, "quantity": "1"}], catalog=catalogo,
        )
        linea = carrito["items"][0]
        ficha = redactar_descripcion_offiho(linea)
        nombre = nombre_producto_offiho(linea)
        descripcion, _, _ = catalog_quotation_item_text(linea, index=1, source_type="offiho_cart")
        pendiente = ficha in {f"{nombre} de Offiho.", "Producto Offiho."}
        articulos.append({
            "inventory_key": item.inventory_key, "code": item.code, "name": nombre,
            "variant": item.variant, "description_source": item.description_source,
            "antes": item.description,
            "despues": construir_descripcion_catalogo(nombre, descripcion, "es", catalogo="offiho"),
            "detalle_tecnico": not pendiente, "pendiente_ficha_tecnica": pendiente,
        })
    return {
        "resumen": {
            "articulos": len(articulos),
            "fuentes": dict(Counter(item.description_source for item in catalogo["items"])),
            "textos_fuente_unicos": len({item.description for item in catalogo["items"]}),
            "con_detalle_tecnico": sum(fila["detalle_tecnico"] for fila in articulos),
            "identidad_y_acabado_sin_especificaciones_confiables": sum(fila["pendiente_ficha_tecnica"] for fila in articulos),
            "ruido_ocr_residual": sum(bool(re.search(r"ºº|ODLAPSER|OTNEISA|SOZARB|SELBATSUJA", fila["despues"])) for fila in articulos),
            "notas": [
                "Payload real del carrito sin description_source; no se consulta un catálogo actual al generar en el worker.",
                "Disponibilidad del snapshot para una unidad; no verifica inventario remoto vigente.",
                "Se preservan los archivos fuente, precios, existencias y claves.",
            ],
        },
        "articulos": articulos,
    }


if __name__ == "__main__":
    salida = ROOT / "output/descripciones-20260930/offiho-auditoria.json"
    informe = auditar()
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(informe["resumen"], ensure_ascii=False))
