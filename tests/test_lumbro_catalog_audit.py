import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_lumbro_catalog import EXPECTED_SOURCE_HASHES, audit_lumbro_catalog


REAL_SOURCE_DIR = Path(
    ".cache/catalog_sources/lumbro/sharepoint_2026-07-26"
)


def test_cli_can_be_invoked_directly_from_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/audit_lumbro_catalog.py", "--help"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.skipif(
    not REAL_SOURCE_DIR.is_dir(), reason="Fuentes Lumbro reales ignoradas no disponibles"
)
def test_real_source_audit_hashes_and_balances_every_commercial_row(tmp_path):
    output = tmp_path / "coverage.json"

    coverage = audit_lumbro_catalog(REAL_SOURCE_DIR, output)

    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8")) == coverage
    assert coverage["source_hashes"] == EXPECTED_SOURCE_HASHES
    assert coverage["parsed_price_rows"] == (
        coverage["imported_rows"]
        + coverage["reconciled_rows"]
        + coverage["excluded_rows"]
    )
    assert len(coverage["exclusions"]) == coverage["excluded_rows"]
    assert all(row["reason"] for row in coverage["exclusions"])
    assert coverage["price_authority"] == "COSTO LUMBRO !E"
    assert coverage["official_product_rows"] == 49
    assert coverage["product_groups"] == 49
