"""Promueve una copia auditada de la plantilla oficial de cotizaciones."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mobiliti_saas.quote_engine.official_template import (  # noqa: E402
    load_template_contract,
    promote,
    verify_official_template,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    promote(args.source, args.destination, args.contract)
    inspection = verify_official_template(
        args.destination,
        load_template_contract(args.contract),
    )
    print(f"Plantilla oficial promovida: {inspection.sha256}")


if __name__ == "__main__":
    main()
