from copy import deepcopy

import pytest

from mobiliti_saas.web.api import index
from project_fixtures import valid_project_payload


def test_dev_project_save_is_revision_safe_and_idempotent(monkeypatch):
    state = {"projects": []}
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: deepcopy(state))
    monkeypatch.setattr(
        index,
        "_dev_save",
        lambda data: (state.clear(), state.update(deepcopy(data))),
    )
    created = index.db_create_project(7, "Oficinas", valid_project_payload())
    saved = index.db_save_project(
        created["id"],
        7,
        "Oficinas GDL",
        valid_project_payload(),
        expected_revision=0,
        operation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    retried = index.db_save_project(
        created["id"],
        7,
        "Oficinas GDL",
        valid_project_payload(),
        expected_revision=0,
        operation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    stale = index.db_save_project(
        created["id"],
        7,
        "Viejo",
        valid_project_payload(),
        expected_revision=0,
        operation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )

    assert saved["revision"] == 1
    assert retried == saved
    assert stale == {}
    assert index.db_get_project(created["id"], 8) is None


def test_dev_project_list_status_and_return_values_are_isolated(monkeypatch):
    state = {"projects": []}
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: deepcopy(state))
    monkeypatch.setattr(
        index,
        "_dev_save",
        lambda data: (state.clear(), state.update(deepcopy(data))),
    )
    created = index.db_create_project(7, "Oficinas", valid_project_payload())
    listed = index.db_list_projects(7, "active")
    listed[0]["payload"]["quote_fields"]["proyecto"] = "Mutado"
    archived = index.db_set_project_status(
        created["id"],
        7,
        "archived",
        expected_revision=0,
        operation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )

    assert set(archived) >= {
        "id", "usuario_id", "name", "status", "revision", "schema_version",
        "payload", "last_operation_id", "created_at", "updated_at", "archived_at",
        "summary",
    }
    assert archived["status"] == "archived"
    assert archived["archived_at"]
    assert archived["summary"] == {"sections": 1, "principals": 1, "complements": 1}
    assert index.db_get_project(created["id"], 7)["payload"]["quote_fields"]["proyecto"] == "Oficinas"
    assert index.db_list_projects(7, "active") == []
    assert index.db_list_projects(7, "archived")[0]["id"] == created["id"]
    with pytest.raises(ValueError):
        index.db_set_project_status(
            created["id"], 7, "deleted", expected_revision=1,
            operation_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )


def test_postgres_save_uses_revision_compare_and_returns_idempotent_retry(monkeypatch):
    payload = valid_project_payload()
    persisted = {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "usuario_id": 7,
        "name": "Oficinas",
        "status": "active",
        "revision": 1,
        "schema_version": 1,
        "payload": payload,
        "last_operation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:01:00+00:00",
        "archived_at": None,
    }
    seen = {}
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_postgres", lambda: True)
    monkeypatch.setattr(index, "_pg_write", lambda sql, params: seen.update(sql=sql, params=params))
    monkeypatch.setattr(index, "db_get_project", lambda *_args: deepcopy(persisted))

    result = index.db_save_project(
        persisted["id"], 7, "Oficinas GDL", payload,
        expected_revision=0,
        operation_id=persisted["last_operation_id"],
    )

    assert """UPDATE saas_projects
SET name = %s,
    payload = %s,
    revision = revision + 1,
    last_operation_id = %s,
    updated_at = %s
WHERE id = %s
  AND usuario_id = %s
  AND revision = %s
RETURNING *""" in seen["sql"]
    assert result["last_operation_id"] == persisted["last_operation_id"]
    assert result["summary"] == {"sections": 1, "principals": 1, "complements": 1}


def test_supabase_save_filters_ownership_revision_then_rereads_idempotency(monkeypatch):
    payload = valid_project_payload()
    project_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    operation_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    persisted = {
        "id": project_id,
        "usuario_id": 7,
        "name": "Oficinas",
        "status": "active",
        "revision": 1,
        "schema_version": 1,
        "payload": payload,
        "last_operation_id": operation_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:01:00+00:00",
        "archived_at": None,
    }
    seen = []
    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_postgres", lambda: False)
    monkeypatch.setattr(
        index,
        "_supabase_req",
        lambda method, path, params=None, json_data=None: seen.append((method, path, json_data)) or [],
    )
    monkeypatch.setattr(index, "db_get_project", lambda *_args: deepcopy(persisted))

    result = index.db_save_project(
        project_id, 7, "Oficinas GDL", payload,
        expected_revision=0, operation_id=operation_id,
    )

    assert seen[0][0] == "PATCH"
    assert seen[0][1] == (
        f"/saas_projects?id=eq.{project_id}&usuario_id=eq.7&revision=eq.0"
    )
    assert result["id"] == project_id
