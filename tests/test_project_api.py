from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mobiliti_saas.web.api import index
from project_fixtures import valid_project_payload


def _auth_headers(user_id=7):
    token = index.create_access_token({"sub": str(user_id), "email": "cliente@example.com"})
    return {"Authorization": f"Bearer {token}"}


def _project_client(monkeypatch):
    state = {"projects": []}
    monkeypatch.setattr(index, "JWT_SECRET_KEY", "project-api-test-secret")
    monkeypatch.setattr(index, "DEV_MODE", True)
    monkeypatch.setattr(index, "_dev_load", lambda: deepcopy(state))
    monkeypatch.setattr(
        index,
        "_dev_save",
        lambda data: (state.clear(), state.update(deepcopy(data))),
    )
    monkeypatch.setattr(
        index,
        "db_get_usuario_by_id",
        lambda user_id: {"id": int(user_id), "activo": True, "es_admin": False},
    )
    monkeypatch.setattr(index, "_require_active_subscription", lambda _user_id: None)
    return TestClient(index.app)


def _created_project(monkeypatch):
    client = _project_client(monkeypatch)
    response = client.post(
        "/projects",
        headers=_auth_headers(7),
        json={"name": "Oficinas", "payload": valid_project_payload()},
    )
    assert response.status_code == 201, response.json()
    return client, response.json()["project"]


def test_catalog_search_requires_authentication_subscription_and_valid_query(monkeypatch):
    client = _project_client(monkeypatch)
    calls = []

    def snapshots(usuario_id, supplier):
        calls.append((usuario_id, supplier))
        return {
            "sunon": {"items": [{
                "internal_id": "sunon:olive",
                "sku": "OLIVE-II",
                "name": "Olive II Chair",
                "image_url": "https://assets.example/olive.png",
                "availability_type": "made_to_order",
                "lead_time": "6 semanas",
            }]}
        }

    monkeypatch.setattr(index, "_catalog_search_snapshots", snapshots, raising=False)

    assert client.get("/catalogs/search?q=olive").status_code == 401
    for params in (
        {"q": "olive", "supplier": "cliente"},
        {"q": "olive\x01"},
        {"q": "x" * 161},
        {"q": "olive", "offset": "true"},
        {"q": "olive", "offset": "-1"},
        {"q": "olive", "limit": "true"},
        {"q": "olive", "limit": "0"},
        {"q": "olive", "limit": "51"},
    ):
        assert client.get("/catalogs/search", params=params, headers=_auth_headers(7)).status_code == 400
    assert calls == []

    response = client.get(
        "/catalogs/search",
        params={"q": "OLÍVE", "supplier": "sunon", "offset": "0", "limit": "1"},
        headers=_auth_headers(7),
    )
    assert response.status_code == 200, response.json()
    assert calls == [(7, "sunon")]
    assert response.json()["items"][0]["identity"] == {
        "internal_id": "sunon:olive",
        "base_option_id": "",
        "add_on_option_ids": [],
    }
    assert response.json()["items"][0]["snapshot"]["availability"] == "6 semanas"

    monkeypatch.setattr(index, "_require_active_subscription", lambda _usuario_id: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get("/catalogs/search?q=olive", headers=_auth_headers(7)).status_code == 503


def test_project_routes_are_user_scoped_and_archive_without_delete(monkeypatch):
    client, project = _created_project(monkeypatch)

    listed = client.get("/projects", headers=_auth_headers(7))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["projects"]] == [project["id"]]
    assert client.get("/projects", headers=_auth_headers(8)).json()["projects"] == []
    assert client.get(f"/projects/{project['id']}", headers=_auth_headers(8)).status_code == 404

    archived = client.post(
        f"/projects/{project['id']}/archive",
        headers=_auth_headers(7),
        json={
            "expected_revision": project["revision"],
            "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    )
    assert archived.status_code == 200
    assert archived.json()["project"]["status"] == "archived"

    restored = client.post(
        f"/projects/{project['id']}/restore",
        headers=_auth_headers(7),
        json={
            "expected_revision": archived.json()["project"]["revision"],
            "operation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
    )
    assert restored.status_code == 200
    duplicate = client.post(
        f"/projects/{project['id']}/duplicate",
        headers=_auth_headers(7),
        json={},
    )
    assert duplicate.status_code == 201
    copied = duplicate.json()["project"]
    assert copied["id"] != project["id"]
    assert copied["name"] == "Oficinas (copia)"
    assert copied["status"] == "active"
    assert copied["revision"] == 0
    assert copied["payload"] == project["payload"]
    assert all("DELETE" not in route.methods for route in index.app.routes if route.path.startswith("/projects"))


def test_project_patch_returns_current_revision_on_conflict_and_rejects_invalid_contract(monkeypatch):
    client, project = _created_project(monkeypatch)
    response = client.patch(
        f"/projects/{project['id']}",
        headers=_auth_headers(7),
        json={
            "name": "Cambio",
            "payload": valid_project_payload(),
            "expected_revision": 99,
            "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_revision_conflict"
    assert response.json()["detail"]["project"]["revision"] == 0

    assert client.get("/projects?status=deleted", headers=_auth_headers(7)).status_code == 400
    assert client.get("/projects/not-a-uuid", headers=_auth_headers(7)).status_code == 400
    assert client.post(
        "/projects",
        headers=_auth_headers(7),
        json={"name": "Oficinas", "payload": valid_project_payload(), "extra": True},
    ).status_code == 400


def test_project_patch_updates_only_owned_project(monkeypatch):
    client, project = _created_project(monkeypatch)
    updated = client.patch(
        f"/projects/{project['id']}",
        headers=_auth_headers(7),
        json={
            "name": "Oficinas GDL",
            "payload": valid_project_payload(),
            "expected_revision": 0,
            "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["project"]["name"] == "Oficinas GDL"
    assert updated.json()["project"]["revision"] == 1
    assert client.patch(
        f"/projects/{project['id']}",
        headers=_auth_headers(8),
        json={
            "name": "Ajeno",
            "payload": valid_project_payload(),
            "expected_revision": 1,
            "operation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
    ).status_code == 404


def test_project_name_http_boundary_matches_schema_and_rejects_long_duplicate(monkeypatch):
    client = _project_client(monkeypatch)
    accepted_name = "P" * 120
    rejected_name = "P" * 121
    created = client.post(
        "/projects",
        headers=_auth_headers(7),
        json={"name": accepted_name, "payload": valid_project_payload()},
    )
    assert created.status_code == 201
    project = created.json()["project"]
    assert client.post(
        "/projects",
        headers=_auth_headers(7),
        json={"name": rejected_name, "payload": valid_project_payload()},
    ).status_code == 400

    updated = client.patch(
        f"/projects/{project['id']}",
        headers=_auth_headers(7),
        json={
            "name": accepted_name,
            "payload": valid_project_payload(),
            "expected_revision": project["revision"],
            "operation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        },
    )
    assert updated.status_code == 200
    assert client.patch(
        f"/projects/{project['id']}",
        headers=_auth_headers(7),
        json={
            "name": rejected_name,
            "payload": valid_project_payload(),
            "expected_revision": updated.json()["project"]["revision"],
            "operation_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        },
    ).status_code == 400

    duplicated = client.post(
        f"/projects/{project['id']}/duplicate",
        headers=_auth_headers(7),
        json={},
    )
    assert duplicated.status_code == 400
    assert len(client.get("/projects", headers=_auth_headers(7)).json()["projects"]) == 1


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
