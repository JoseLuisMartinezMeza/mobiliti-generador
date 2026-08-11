import json
from pathlib import Path

import pytest

from mobiliti_saas.worker.catalog_sync import load_source_config


SOURCES_PATH = Path("mobiliti_saas/worker/catalog_sync/sources.json")


def test_idelika_official_pdf_sources_are_pinned_for_traceability():
    idelika = {source.supplier: source for source in load_source_config(SOURCES_PATH)}["idelika"]

    assert idelika.label == "IDÉLIKA"
    assert idelika.adapter == "idelika"
    assert {
        (file.path, file.kind, file.drive_item_id, file.extension, file.mime_type, file.name)
        for file in idelika.files
    } == {
        (
            "IDELIKA/1 CATALOGO FABRICACION 2026B.pdf",
            "catalog",
            "01DHXXN7YJMCJUVPBWNJEJPJIH7B4OTAUR",
            ".pdf",
            "application/pdf",
            "1 CATALOGO FABRICACION 2026B.pdf",
        ),
        (
            "IDELIKA/2 CATALOGO STOCK 2026.pdf",
            "inventory",
            "01DHXXN7YASXKBZPOLSBHIX2N2T3PB4G2R",
            ".pdf",
            "application/pdf",
            "2 CATALOGO STOCK 2026.pdf",
        ),
        (
            "IDELIKA/4 SCHOOL SERIES 2026.pdf",
            "catalog",
            "01DHXXN7YTQLPUZXRUN5E3J62UE2JQUWNC",
            ".pdf",
            "application/pdf",
            "4 SCHOOL SERIES 2026.pdf",
        ),
    }
    assert all(file.extension == ".pdf" for file in idelika.files)
    assert "TEQUILA LOVE.pdf" not in {file.name for file in idelika.files}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows[-2]["files"][0].pop("mime_type"),
        lambda rows: rows[-2]["files"][1].update(mime_type="application/pdfx"),
    ],
    ids=["mime-omitted", "mime-incorrect"],
)
def test_idelika_sources_require_a_matching_explicit_mime_type(tmp_path, mutate):
    rows = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    mutate(rows)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        load_source_config(path)
