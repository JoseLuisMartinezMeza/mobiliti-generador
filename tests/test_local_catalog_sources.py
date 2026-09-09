from copy import deepcopy

from scripts.prepare_local_catalog_sources import preparar_fuentes


def test_migration_keeps_snapshots_and_existing_publication_decisions(monkeypatch):
    monkeypatch.setattr("scripts.prepare_local_catalog_sources.load_supplier_catalog_data", lambda payload, expected_supplier: payload)
    data = {"catalog_published_snapshots": {
        "labenze": {"id": "reviewed-1", "status": "published", "payload": {"items": [1]}},
        "alma": {"id": "draft-1", "status": "candidate", "payload": {}},
    }, "projects": [{"id": 123}]}
    before = deepcopy(data)
    assert preparar_fuentes(data)
    assert data["catalog_sources"] == [{"supplier": "labenze", "enabled": True, "published_version_id": "reviewed-1"}]
    assert {k: data[k] for k in before} == before
    data["catalog_sources"][0]["enabled"] = False
    assert not preparar_fuentes(data)
    assert data["catalog_sources"][0]["enabled"] is False
    data["catalog_sources"] = []
    assert not preparar_fuentes(data)
