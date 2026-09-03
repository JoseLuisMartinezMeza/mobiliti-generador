"""Compatibility wrapper for the Python-only Mobiliti quote engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mobiliti_saas.quote_engine import generate_quote, read_items  # noqa: E402


_ARGUMENT_OMITTED = object()


def generate_online_quote(
    source_path: str | Path,
    output_path: str | Path,
    metadata: dict[str, Any] | None = None,
    template_path: str | Path | None = None,
    *,
    original_quotation_path: object = _ARGUMENT_OMITTED,
    quotation_data_rows: object = _ARGUMENT_OMITTED,
) -> Path:
    explicit_sources = {}
    if original_quotation_path is not _ARGUMENT_OMITTED:
        explicit_sources["original_quotation_path"] = original_quotation_path
    if quotation_data_rows is not _ARGUMENT_OMITTED:
        explicit_sources["quotation_data_rows"] = quotation_data_rows
    return generate_quote(
        source_path,
        output_path,
        metadata,
        template_path,
        **explicit_sources,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera cotizacion XLSX sin Microsoft Excel")
    parser.add_argument("--source", "-s", required=True, help="Archivo Quotation.xlsx")
    parser.add_argument("--output", "-o", required=True, help="Archivo XLSX de salida")
    parser.add_argument("--template", "-t", default=None, help="Plantilla XLSX de Mobiliti")
    parser.add_argument("--metadata-json", default="{}", help="Metadata JSON para encabezado")
    parser.add_argument("--cotizacion", default="")
    parser.add_argument("--proyecto", default="")
    parser.add_argument("--cliente", default="")
    parser.add_argument("--correo", default="")
    parser.add_argument("--telefono", default="")
    parser.add_argument("--direccion", default="")
    parser.add_argument("--razon-social", dest="razon_social", default="")
    parser.add_argument("--descuento", default="")
    args = parser.parse_args()

    try:
        metadata = json.loads(args.metadata_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"metadata-json invalido: {exc}") from exc
    for key in ["cotizacion", "proyecto", "cliente", "correo", "telefono", "direccion", "razon_social", "descuento"]:
        value = getattr(args, key)
        if value:
            metadata[key] = value

    generate_online_quote(args.source, args.output, metadata, args.template)
    print(f"Cotizacion generada: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
