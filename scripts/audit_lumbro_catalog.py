from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mobiliti_saas.worker.catalog_sync.importers import (
    build_lumbro_snapshot_with_assets,
)


PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SOURCE_FILES = {
    "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf": (
        "LISTA DE PRECIOS MULTICONTACTOS 2026.pdf",
        "price_list",
        PDF_MIME,
    ),
    "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf": (
        "LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf",
        "price_list",
        PDF_MIME,
    ),
    "LUMBRO/LP/Precios Interconexión Sunón act.xlsx": (
        "Precios Interconexion Sunon act.xlsx",
        "price_list",
        XLSX_MIME,
    ),
    "SPEC GUIDES 2026/LUMBRO/Spec guide-Lumbro-2026.xlsx": (
        "Spec guide-Lumbro-2026.xlsx",
        "spec_guide",
        XLSX_MIME,
    ),
    "LUMBRO/CATALOGO/CATALOGO LUMBRO 2024 DIGITAL (1).pdf": (
        "CATALOGO LUMBRO 2024 DIGITAL (1).pdf",
        "catalog",
        PDF_MIME,
    ),
}
EXPECTED_SOURCE_HASHES = {
    "LUMBRO/CATALOGO/CATALOGO LUMBRO 2024 DIGITAL (1).pdf": "bbd810ebab20336d2a6bdc61123955bd062c5a64d57d4359556fcf6aef57e053",
    "LUMBRO/LP/LISTA DE PRECIOS MULTICONTACTOS 2026.pdf": "83319649f387ba14107854e39e2cf9c70a03d0a121e71080efcec1d46e1654d5",
    "LUMBRO/LP/LISTA DE PRECIOS NUEVOS PRODUCTOS LUMBRO 2025.pdf": "19d38a8aa98df2d4f77f229cc94813b26f13d1809f0231c73b3fa9b64d4f1a29",
    "LUMBRO/LP/Precios Interconexión Sunón act.xlsx": "48376c65038c65ce07c658f3570c741dac70c9cdf676f171dda3674a9925551b",
    "SPEC GUIDES 2026/LUMBRO/Spec guide-Lumbro-2026.xlsx": "fce1a47fa719300fd3b6be5edf934f6c9a082676427ea6b5fa8d20bd06b8f3d1",
}


@dataclass(frozen=True)
class AuditSourceFile:
    path: str
    kind: str
    brand: None
    sha256: str
    mime_type: str
    local_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_bundle(source_dir: Path) -> tuple[AuditSourceFile, ...]:
    if not isinstance(source_dir, Path) or not source_dir.is_dir():
        raise ValueError("LUMBRO_AUDIT_SOURCE_DIR")
    rows = []
    hashes = {}
    for logical_path, (filename, kind, mime_type) in SOURCE_FILES.items():
        local_path = source_dir / filename
        if not local_path.is_file():
            raise ValueError(f"LUMBRO_AUDIT_MISSING:{filename}")
        digest = _sha256(local_path)
        hashes[logical_path] = digest
        rows.append(
            AuditSourceFile(
                logical_path,
                kind,
                None,
                digest,
                mime_type,
                local_path,
            )
        )
    if dict(sorted(hashes.items())) != EXPECTED_SOURCE_HASHES:
        raise ValueError("LUMBRO_AUDIT_HASH_MISMATCH")
    return tuple(rows)


def audit_lumbro_catalog(source_dir: Path, output: Path) -> dict:
    source_dir = Path(source_dir)
    output = Path(output)
    bundle = _source_bundle(source_dir)
    build = build_lumbro_snapshot_with_assets(bundle)
    coverage = dict(build.snapshot["metadata"]["coverage"])
    coverage.update(
        supplier="lumbro",
        source_hash=build.snapshot["source_hash"],
        source_hashes=dict(sorted((row.path, row.sha256) for row in bundle)),
    )
    if coverage["parsed_price_rows"] != (
        coverage["imported_rows"]
        + coverage["reconciled_rows"]
        + coverage["excluded_rows"]
    ):
        raise ValueError("LUMBRO_AUDIT_UNBALANCED")
    if len(coverage["exclusions"]) != coverage["excluded_rows"] or any(
        not row.get("reason") for row in coverage["exclusions"]
    ):
        raise ValueError("LUMBRO_AUDIT_EXCLUSIONS")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            coverage,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audita offline las cinco fuentes oficiales Lumbro 2026."
    )
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    coverage = audit_lumbro_catalog(arguments.source_dir, arguments.output)
    print(
        json.dumps(
            {
                "source_hash": coverage["source_hash"],
                "items": coverage["items"],
                "verified_items": coverage["verified_items"],
                "needs_review_items": coverage["needs_review_items"],
                "assets": coverage["assets"],
                "bindings": coverage["bindings"],
                "excluded_rows": coverage["excluded_rows"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
