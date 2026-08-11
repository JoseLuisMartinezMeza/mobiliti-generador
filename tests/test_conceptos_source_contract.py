import json
from pathlib import Path

import pytest

from mobiliti_saas.worker.catalog_sync import load_source_config


SOURCES_PATH = Path("mobiliti_saas/worker/catalog_sync/sources.json")


def test_conceptos_sofas_official_xlsx_source_is_pinned():
    conceptos = {source.supplier: source for source in load_source_config(SOURCES_PATH)}["conceptos"]

    assert conceptos.label == "Conceptos"
    assert conceptos.adapter == "conceptos"
    assert {
        (file.path, file.kind, file.drive_item_id, file.extension, file.mime_type, file.name)
        for file in conceptos.files
    } == {
        (
            "SPEC GUIDES 2026/CONCEPTOS/Spec guide - Conceptos - Sofas - CdMx - Gdl - Qro - 2021.xlsx",
            "spec_guide",
            "01DHXXN76XWGQOWSKX2RDL5YG6GTS355BO",
            ".xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Spec guide - Conceptos - Sofas - CdMx - Gdl - Qro - 2021.xlsx",
        ),
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows[-1]["files"][0].pop("mime_type"),
        lambda rows: rows[-1]["files"][0].update(mime_type="application/pdf"),
    ],
    ids=["mime-omitted", "mime-incorrect"],
)
def test_conceptos_source_requires_a_matching_explicit_mime_type(tmp_path, mutate):
    rows = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    mutate(rows)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        load_source_config(path)
