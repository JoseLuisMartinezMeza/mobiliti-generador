import json
from pathlib import Path

import pytest

from mobiliti_saas.worker.catalog_sync import load_source_config


SOURCES_PATH = Path("mobiliti_saas/worker/catalog_sync/sources.json")
ROOT_PATH = "PROYECTOS CET - 2026/LISTAS DE PRECIOS PROVEEDORES"


def test_first_wave_source_config_is_explicit_and_safe():
    rows = load_source_config(Path("mobiliti_saas/worker/catalog_sync/sources.json"))
    assert [row.supplier for row in rows] == ["cr-global", "sonara", "sunon", "alma"]
    names = {file.name for row in rows for file in row.files}
    assert "FAST INVENTARIO SUNON 2026.cmdrw" not in names
    assert "NEW Order registration template-To Dealers (1).xlsx" not in names
    assert "CRG_LP_General_Dist_2026-04.pdf" in names
    assert "SPEC Guide-Alma-KUN.xlsx" in names
    assert "Spec guide-Alma-KUN Design.xlsx" in names
    assert sum(len(row.files) for row in rows) == 13
    assert all(file.extension in {".xlsx", ".pdf"} for row in rows for file in row.files)
    assert {row.root_path for row in rows} == {ROOT_PATH}


def _source(**changes):
    source = {
        "supplier": "sunon",
        "label": "Sunon",
        "adapter": "sunon",
        "root_path": ROOT_PATH,
        "files": [{"path": "SUNON/source.xlsx", "kind": "inventory"}],
    }
    source.update(changes)
    return source


@pytest.mark.parametrize(
    "entries",
    [
        [_source(), _source()],
        [_source(adapter="unknown")],
        [_source(files=[{"path": "/absolute.xlsx", "kind": "inventory"}])],
        [_source(files=[{"path": "folder/../source.xlsx", "kind": "inventory"}])],
        [_source(files=[{"path": "source.cmdrw", "kind": "inventory"}])],
        [_source(files=[{"path": "source.xlsx", "kind": "inventory"}] * 2)],
    ],
)
def test_source_config_rejects_untrusted_entries(tmp_path, entries):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(ValueError):
        load_source_config(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows[0]["files"].append({"path": "CR GLOBAL/extra.pdf", "kind": "catalog"}),
        lambda rows: rows.pop(),
        lambda rows: rows[0].update(label="Tampered CR Global"),
        lambda rows: rows.reverse(),
        lambda rows: rows[1]["files"][0].update(path="SONARA/replaced.pdf"),
        lambda rows: rows[0].update(root_path="ATTACK/" + ROOT_PATH),
    ],
    ids=["addition", "omission", "substitution", "supplier-reordering", "path-change", "root-prefix"],
)
def test_source_config_rejects_any_deviation_from_first_wave_allowlist(tmp_path, mutate):
    rows = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    mutate(rows)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        load_source_config(path)
