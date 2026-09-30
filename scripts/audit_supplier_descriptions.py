"""Audita el texto local completo; no publica ni modifica snapshots del catálogo."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.customer_descriptions import construir_descripcion_catalogo
from mobiliti_saas.quote_engine.supplier_descriptions import (
    detectar_pendientes_descripcion,
    nombre_producto_proveedor,
    redactar_descripcion_proveedor,
)


def auditar(database: Path) -> dict:
    base = json.loads(database.read_text(encoding="utf-8-sig"))
    registros = []
    resumen = []
    catalogos = {
        marca: snapshot["payload"]["items"]
        for marca, snapshot in base.get("catalog_published_snapshots", {}).items()
        if marca != "offiho"
    }
    tarkett = json.loads((ROOT / "mobiliti_saas/quote_engine/data/tarkett_catalog.json").read_text(encoding="utf-8"))
    catalogos["tarkett"] = tarkett["items"]
    for marca, items in catalogos.items():
        filas_marca = []
        for registro in items:
            item = dict(registro, supplier=marca)
            original = str(item.get("description") or "")
            revisada = redactar_descripcion_proveedor(item)
            nombre_final = nombre_producto_proveedor(item)
            cliente = construir_descripcion_catalogo(nombre_final, revisada, "es", catalogo=marca)
            fila = {
                "supplier": marca,
                "internal_id": item.get("internal_id") or item.get("code"),
                "sku": item.get("sku") or item.get("code") or "",
                "name": item.get("name") or "",
                "revised_name": nombre_final,
                "original": original,
                "revised": revisada,
                "customer_text": cliente,
                "changed": original != revisada,
                "pending": detectar_pendientes_descripcion(item),
                "source_reference": item.get("source_reference") or tarkett.get("source_file", "") if marca == "tarkett" else item.get("source_reference", ""),
            }
            registros.append(fila)
            filas_marca.append(fila)
        motivos = Counter(motivo for fila in filas_marca for motivo in fila["pending"])
        resumen.append({
            "supplier": marca,
            "products": len(items),
            "unique_source_texts": len({fila["original"] for fila in filas_marca}),
            "source_texts_changed": sum(fila["changed"] for fila in filas_marca),
            "products_pending_source": sum(bool(fila["pending"]) for fila in filas_marca),
            "pending_reasons": dict(motivos),
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(database.resolve()),
        "scope": "Inventario local completo de 12 proveedores. Offiho se audita por separado. No verifica publicación o inventario remoto vigente.",
        "method": "Comparación del texto original completo con reglas editoriales acotadas y correcciones exactas con procedencia. La vista cliente aplica el formateador español. Las alertas señalan datos ausentes o ambiguos de la fuente, no características inventadas.",
        "summary": resumen,
        "records": registros,
    }


def resumen_markdown(informe: dict) -> str:
    lineas = [
        "# Revisión editorial de descripciones de proveedores", "",
        informe["scope"], "", informe["method"], "",
        "| Proveedor | Productos | Textos únicos | Textos de producto corregidos | Con pendiente de fuente |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for marca in informe["summary"]:
        lineas.append(f"| {marca['supplier']} | {marca['products']} | {marca['unique_source_texts']} | {marca['source_texts_changed']} | {marca['products_pending_source']} |")
    lineas += [
        "", "Los cambios de cuerpo se cuentan por producto. Sunon y ALMA conservan su idioma en el cuerpo fuente y se traducen en la presentación final; el conteo de cuerpo no mide esa traducción.",
        "", "## Limitaciones concretas", "",
        "- Conceptos: la extracción local conserva fragmentos de una sola fila; la nueva redacción utiliza únicamente hechos completos y marca la ficha para su recuperación posterior.",
        "- JOME: se recuperan las identidades publicadas en el bloque y las medidas cuando el texto solo era un SKU; no se inventan materiales ni capacidades.",
        "- Idélika: se separan los vecinos identificados en la extracción y se conservan hechos compartidos cuando las versiones interior/exterior se mezclan.",
        "- Requiez: los números contradictorios de posiciones de bloqueo se omiten hasta verificarlos; quedan los hechos no contradictorios.",
        "- Lumbro: una longitud incompleta como «cable de metro» no se convierte arbitrariamente en un cable de 1 m.",
        "- Tarkett: el catálogo local aporta nombres y espesores, pero no fichas técnicas completas. No se infieren materiales desde el nombre comercial.",
        "", "## Ejemplos por proveedor", "",
    ]
    for marca in informe["summary"]:
        filas = [fila for fila in informe["records"] if fila["supplier"] == marca["supplier"]]
        ejemplo = next((fila for fila in filas if fila["changed"]), filas[0])
        lineas += [f"### {marca['supplier']} · {ejemplo['sku'] or ejemplo['name']}", "", "Origen: " + (ejemplo["original"] or "Sin descripción técnica."), "", "Redacción: " + ejemplo["revised"], ""]
    return "\n".join(lineas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / ".mobiliti_dev_store/db.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output/descripciones-20260930/auditoria-proveedores.json")
    args = parser.parse_args()
    informe = auditar(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(resumen_markdown(informe), encoding="utf-8")
    print(json.dumps({"products": len(informe["records"]), "summary": informe["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
