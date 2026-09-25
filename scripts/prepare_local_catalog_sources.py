"""Migra el índice de catálogos del almacén local antiguo, con respaldo."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.supplier_catalog import load_supplier_catalog_data


def preparar_fuentes(data: dict) -> bool:
    # Un índice existente, incluso vacío o deshabilitado, es una decisión vigente.
    if "catalog_sources" in data:
        return False
    fuentes = []
    for supplier, snapshot in data.get("catalog_published_snapshots", {}).items():
        if snapshot.get("status") != "published" or not snapshot.get("id"):
            continue
        load_supplier_catalog_data(snapshot["payload"], expected_supplier=supplier)
        fuentes.append({
            "supplier": supplier,
            "enabled": True,
            "published_version_id": snapshot["id"],
        })
    if not fuentes:
        return False
    data["catalog_sources"] = fuentes
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=ROOT / ".mobiliti_dev_store")
    args = parser.parse_args()
    path = args.store.resolve() / "db.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not preparar_fuentes(data):
        return
    backup = path.with_name("db.before-catalog-sources-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Índice local preparado: {len(data['catalog_sources'])} catálogos. Respaldo: {backup}")


if __name__ == "__main__":
    main()
