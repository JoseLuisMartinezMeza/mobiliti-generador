from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobiliti_saas.worker.catalog_sync import load_source_config
import mobiliti_saas.worker.catalog_sync.service as catalog_service
from mobiliti_saas.worker.catalog_sync.importers.jome import build_jome_snapshot_with_assets
from mobiliti_saas.worker.catalog_sync.importers.lauco import build_lauco_snapshot_with_assets


ROOT = Path("tmp/jome-lauco-source")
SOURCES = Path("mobiliti_saas/worker/catalog_sync/sources.json")
LOCAL_BY_NAME = {
    "Spec guide-Estructuras Jome-2026.xlsx": ROOT / "Spec guide-Estructuras Jome-2026.xlsx",
    "Spec guide-Laminado-2026.xlsx": ROOT / "Spec guide-Laminado-2026.xlsx",
    "Spec Guide Lauco-2026.xlsb": ROOT / "Spec Guide Lauco-2026.xlsb",
}
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSB_MIME = "application/vnd.ms-excel.sheet.binary.macroEnabled.12"


def _official_documents(supplier: str):
    configs = {row.supplier: row for row in load_source_config(SOURCES)}
    config = configs[supplier]
    missing = [LOCAL_BY_NAME[file.name] for file in config.files if not LOCAL_BY_NAME[file.name].exists()]
    if missing:
        pytest.skip("No están disponibles las fuentes oficiales: " + ", ".join(map(str, missing)))
    return tuple(
        SimpleNamespace(
            path=file.path,
            kind=file.kind,
            brand=file.brand,
            sha256=hashlib.sha256(LOCAL_BY_NAME[file.name].read_bytes()).hexdigest(),
            mime_type=XLSB_MIME if file.extension == ".xlsb" else XLSX_MIME,
            local_path=LOCAL_BY_NAME[file.name],
        )
        for file in config.files
    )


@pytest.mark.parametrize(
    ("supplier", "builder"),
    (
        ("jome", build_jome_snapshot_with_assets),
        ("lauco", build_lauco_snapshot_with_assets),
    ),
)
def test_official_builders_match_source_config_and_service_asset_contract(supplier, builder):
    build = builder(_official_documents(supplier))
    candidate = catalog_service._validate_snapshot(
        build.snapshot,
        expected_supplier=supplier,
    )
    metrics = catalog_service._asset_metrics(candidate, build)

    assert candidate["supplier"] == supplier
    assert build.bindings
    assert metrics["official_images_planned"] == len(build.bindings)
    assert metrics["unique_assets_planned"] == len(build.assets_by_sha256)
