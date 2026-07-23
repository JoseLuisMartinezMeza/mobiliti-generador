import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mobiliti_saas.web.api import index
from project_fixtures import valid_project_payload
from quotation_import_fixtures import write_import_fixture


def _auth_headers(user_id=7):
    token = index.create_access_token(
        {"sub": str(user_id), "email": "cliente@example.com"}
    )
    return {"Authorization": f"Bearer {token}"}


def _sunon_catalog():
    return {
        "supplier": "sunon",
        "source_hash": hashlib.sha256(b"sunon-project-test").hexdigest(),
        "generated_at": "2026-07-22T00:00:00+00:00",
        "items": [{
            "internal_id": "sunon:chair-1",
            "supplier": "sunon",
            "product_key": "chair-1",
            "sku": "CHAIR-1",
            "code_status": "verified",
            "brand": "Sunon",
            "collection": "Work",
            "name": "Silla",
            "description": "Silla operativa",
            "unit": "pieza",
            "availability_type": "made_to_order",
            "stock": None,
            "lead_time": "6 semanas",
            "base_price_options": [],
            "add_on_options": [],
            "base_currency": "USD",
            "price_net": "100.000000",
            "tax_rate": "0.160000",
            "attributes": {},
            "image_url": "",
            "image_kind": "placeholder",
            "product_url": "",
            "warnings": [],
            "source_reference": "sunon:test",
        }],
    }


@pytest.fixture
def project_client(monkeypatch):
    payload = valid_project_payload()
    payload["lines"] = [payload["lines"][0]]
    project = {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "usuario_id": 7,
        "name": "Oficinas",
        "status": "active",
        "revision": 3,
        "schema_version": 1,
        "payload": payload,
        "last_operation_id": None,
        "created_at": "2026-07-22T00:00:00+00:00",
        "updated_at": "2026-07-22T00:00:00+00:00",
        "archived_at": None,
    }
    storage = {}
    jobs = []
    events = []

    monkeypatch.setattr(index, "JWT_SECRET_KEY", "project-quote-test-secret")
    monkeypatch.setattr(
        index,
        "db_get_usuario_by_id",
        lambda user_id: {
            "id": int(user_id),
            "email": "cliente@example.com",
            "activo": True,
            "es_admin": False,
        },
    )
    monkeypatch.setattr(index, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(index, "db_get_project", lambda project_id, user_id: (
        deepcopy(project)
        if project_id == project["id"] and int(user_id) == project["usuario_id"]
        else None
    ))
    monkeypatch.setattr(index, "_require_enabled_catalog_supplier", lambda value: value)
    monkeypatch.setattr(index, "_load_supplier_catalog_cached", lambda supplier: (
        _sunon_catalog()
        if supplier == "sunon"
        else (_ for _ in ()).throw(AssertionError(f"catalogo inesperado: {supplier}"))
    ))
    effective = (date.today() - timedelta(days=1)).isoformat()
    monkeypatch.setattr(index, "db_list_exchange_rates", lambda: [{
        "currency": "USD",
        "effective_date": effective,
        "mxn_per_unit": "18.500000",
        "retrieved_at": f"{effective}T20:00:00Z",
    }])
    monkeypatch.setattr(index, "_next_quote_number_for_user", lambda _user: None)
    monkeypatch.setattr(index, "_enforce_active_quote_limit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(index, "_storage_provider_name", lambda: "test")

    def create_job(user_id, template, metadata, input_path, job_id=None):
        events.append("create_job")
        job = {
            "id": job_id,
            "usuario_id": user_id,
            "status": "draft",
            "template": template,
            "metadata": deepcopy(metadata),
            "input_path": input_path,
        }
        jobs.append(job)
        return deepcopy(job)

    def reserve(_user_id, _job_id, groups):
        events.append("reserve")
        assert groups == []
        return []

    def upload(path, content, content_type="application/octet-stream"):
        events.append("upload")
        storage[path] = bytes(content)

    def queue(job_id, metadata):
        events.append("queue")
        job = next(row for row in jobs if row["id"] == job_id)
        job.update(status="queued", metadata=deepcopy(metadata))
        return deepcopy(job)

    monkeypatch.setattr(index, "db_create_quote_job", create_job)
    monkeypatch.setattr(index, "db_reserve_mixed_cart", reserve)
    monkeypatch.setattr(index, "db_queue_mixed_quote_job", queue)
    monkeypatch.setattr(index, "db_release_mixed_cart", lambda _job_id: events.append("release"))
    monkeypatch.setattr(index, "db_delete_quote_job", lambda _job_id: events.append("delete_job"))
    monkeypatch.setattr(index, "_delete_storage_paths", lambda paths: [
        storage.pop(path, None) for path in paths
    ])
    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    monkeypatch.setattr(index, "_storage_download_bytes", lambda path: storage[path])
    monkeypatch.setattr(index, "_wake_worker", lambda: events.append("wake"))

    client = TestClient(index.app)
    return client, _auth_headers(), project, storage, jobs, events


def test_project_quote_uses_saved_revision_and_does_not_mutate_project(project_client):
    client, headers, project, storage, jobs, events = project_client

    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )

    assert response.status_code == 202, response.json()
    job = response.json()["job"]
    frozen = json.loads(storage[job["input_path"]])
    assert job["metadata"]["project_id"] == project["id"]
    assert job["metadata"]["project_revision"] == project["revision"]
    assert frozen["project_context"]["project_revision"] == project["revision"]
    assert frozen["groups"][0]["items"][0]["line_id"] == (
        project["payload"]["lines"][0]["line_id"]
    )
    assert events == ["create_job", "reserve", "upload", "queue", "wake"]
    assert len(jobs) == 1
    assert client.get(
        f"/projects/{project['id']}", headers=headers
    ).json()["project"]["revision"] == project["revision"]


def test_project_quote_rejects_stale_revision_before_creating_job(project_client):
    client, headers, project, storage, jobs, events = project_client

    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"] + 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "project_revision_conflict"
    assert jobs == []
    assert storage == {}
    assert events == []


def test_project_quote_requires_active_owned_project_and_exact_body(project_client):
    client, headers, project, _storage, jobs, _events = project_client

    assert client.post(
        f"/projects/{project['id']}/quote",
        headers=_auth_headers(8),
        json={"expected_revision": project["revision"]},
    ).status_code == 404
    assert client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"], "extra": True},
    ).status_code == 400
    project["status"] = "archived"
    archived = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )
    assert archived.status_code == 409
    assert jobs == []


def test_project_quote_uses_official_template_contract_hash(project_client):
    client, headers, project, _storage, _jobs, _events = project_client

    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )

    contract = json.loads(Path(index.OFFICIAL_TEMPLATE_CONTRACT_PATH).read_text("utf-8"))
    assert response.status_code == 202, response.json()
    assert response.json()["job"]["metadata"]["template_contract_hash"] == contract["sha256"]


def test_project_quote_resolves_each_occurrence_once_with_physical_quantities(
    project_client,
    monkeypatch,
):
    client, headers, project, storage, _jobs, _events = project_client
    child = deepcopy(project["payload"]["lines"][0])
    child.update({
        "line_id": "22222222-2222-4222-8222-222222222222",
        "role": "complement",
        "section_id": None,
        "parent_line_id": project["payload"]["lines"][0]["line_id"],
        "position": 0,
        "quantity": "2",
        "quantity_mode": "per_parent_unit",
    })
    project["payload"]["lines"].append(child)
    seen = []
    original = index.build_mixed_catalog_cart_payload

    def tracked(*args, **kwargs):
        seen.append((deepcopy(args), deepcopy(kwargs)))
        return original(*args, **kwargs)

    monkeypatch.setattr(index, "build_mixed_catalog_cart_payload", tracked)

    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )

    assert response.status_code == 202, response.json()
    assert len(seen) == 1
    payload = json.loads(storage[response.json()["job"]["input_path"]])
    assert [
        item["quantity"] for item in payload["groups"][0]["items"]
    ] == ["10.000000", "20.000000"]
    assert payload["sections"][0]["line_ids"] == [
        project["payload"]["lines"][0]["line_id"],
        child["line_id"],
    ]


def test_project_quote_copies_but_does_not_consume_promoted_import(
    project_client,
    monkeypatch,
    tmp_path,
):
    client, headers, project, storage, jobs, events = project_client
    source = write_import_fixture(tmp_path / "quotation.xlsx").read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    source_key = (
        f"projects/7/{project['id']}/sources/{source_hash}.xlsx"
    )
    imported = deepcopy(valid_project_payload()["lines"][1])
    imported.update({
        "role": "principal",
        "section_id": "section-1",
        "parent_line_id": None,
        "position": 0,
        "source_row": 14,
        "source_currency": "USD",
        "source_asset_key": source_key,
        "image_asset_key": "",
        "unit_price": "20",
    })
    imported.pop("quantity_mode")
    project["payload"]["lines"] = [imported]
    storage[source_key] = source
    monkeypatch.setattr(
        index,
        "_consume_import_draft",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("un activo de Proyecto no se consume")
        ),
    )

    response = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )

    assert response.status_code == 202, response.json()
    job = response.json()["job"]
    frozen = json.loads(storage[job["input_path"]])
    copied = job["metadata"]["import_source_path"]
    assert storage[copied] == source
    assert frozen["imported_source"]["source_path"] == copied
    assert frozen["imported_source"]["items"][0]["line_id"] == imported["line_id"]
    assert source_key in storage
    assert jobs[0]["status"] == "queued"
    assert events == [
        "create_job", "upload", "reserve", "upload", "queue", "wake",
    ]


def test_project_quote_rejects_multiple_or_tampered_import_sources_before_job(
    project_client,
):
    client, headers, project, storage, jobs, events = project_client
    first = deepcopy(valid_project_payload()["lines"][1])
    first.update({
        "role": "principal",
        "section_id": "section-1",
        "parent_line_id": None,
        "position": 0,
        "source_asset_key": (
            f"projects/7/{project['id']}/sources/{'a' * 64}.xlsx"
        ),
    })
    first.pop("quantity_mode")
    second = deepcopy(first)
    second.update({
        "line_id": "44444444-4444-4444-8444-444444444444",
        "position": 1,
        "source_asset_key": (
            f"projects/7/{project['id']}/sources/{'b' * 64}.xlsx"
        ),
    })
    project["payload"]["lines"] = [first, second]

    multiple = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )
    assert multiple.status_code == 400
    assert "mas de una Quotation" in multiple.json()["detail"]
    assert jobs == []
    assert events == []

    project["payload"]["lines"] = [first]
    storage[first["source_asset_key"]] = b"tampered"
    tampered = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )
    assert tampered.status_code == 400
    assert jobs == []
    assert events == []


def test_project_quote_validates_metadata_and_capacity_before_job(
    project_client,
    monkeypatch,
):
    client, headers, project, _storage, jobs, events = project_client
    project["payload"]["quote_fields"]["cliente"] = ""
    missing = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )
    assert missing.status_code == 400
    assert jobs == []
    assert events == []

    project["payload"]["quote_fields"]["cliente"] = "Cliente"
    real_validate = index.validate_quote_size

    def capacity(*, section_counts, encoded_bytes):
        if encoded_bytes:
            raise ValueError("capacidad fisica excedida")
        return real_validate(
            section_counts=section_counts,
            encoded_bytes=encoded_bytes,
        )

    monkeypatch.setattr(index, "validate_quote_size", capacity)
    too_large = client.post(
        f"/projects/{project['id']}/quote",
        headers=headers,
        json={"expected_revision": project["revision"]},
    )
    assert too_large.status_code == 400
    assert jobs == []
    assert events == []
