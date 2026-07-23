from __future__ import annotations

from copy import deepcopy
import json

from fastapi.testclient import TestClient

from mobiliti_saas.web.api import index
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from project_fixtures import valid_project_payload
from quotation_import_fixtures import write_import_fixture


IMPORT_ID = "22222222-2222-4222-8222-222222222222"


def _auth_headers(user_id: int = 7) -> dict[str, str]:
    token = index.create_access_token({"sub": str(user_id), "email": "cliente@example.com"})
    return {"Authorization": f"Bearer {token}"}


def project_with_import_fixture(monkeypatch, tmp_path):
    state = {"projects": []}
    storage: dict[str, bytes] = {}
    monkeypatch.setattr(index, "JWT_SECRET_KEY", "project-import-assets-test-secret")
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

    source = write_import_fixture(tmp_path / "quotation-import.xlsx")
    source_bytes = source.read_bytes()
    manifest, _images = build_import_manifest(source_bytes, IMPORT_ID, source.name)
    prefix = f"users/7/jobs/{IMPORT_ID}/"
    manifest_path = f"{prefix}preview/{manifest['source_hash'][:16]}/manifest.json"
    preview_path = f"{prefix}preview/{manifest['source_hash'][:16]}/row-11.png"
    storage.update({
        f"{prefix}input.xlsx": source_bytes,
        preview_path: b"preview-png-bytes",
        manifest_path: json.dumps({
            **manifest, "preview_image_paths": {"11": preview_path},
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    })
    job = {
        "id": IMPORT_ID,
        "usuario_id": 7,
        "status": "draft",
        "input_path": f"{prefix}input.xlsx",
        "metadata": {
            "original_filename": source.name,
            "import_manifest_path": manifest_path,
            "import_preview_paths": {"11": preview_path},
            "import_source_hash": manifest["source_hash"],
            "import_item_count": len(manifest["items"]),
        },
    }
    monkeypatch.setattr(index, "db_get_quote_job", lambda job_id: job if job_id == IMPORT_ID else None)
    monkeypatch.setattr(index, "_storage_download_bytes", lambda path: storage[path])

    def upload(path, content, content_type="application/octet-stream"):
        storage[path] = content

    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    client = TestClient(index.app)
    created = client.post(
        "/projects", headers=_auth_headers(),
        json={"name": "Oficinas", "payload": valid_project_payload()},
    )
    assert created.status_code == 201, created.json()
    return client, _auth_headers(), created.json()["project"], job, storage


def test_import_promotion_copies_source_manifest_and_images_without_consuming_job(
    monkeypatch, tmp_path
):
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)

    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    prefix = f"projects/7/{project['id']}/"
    assert body["source_asset_key"].startswith(prefix + "sources/")
    assert all(path.startswith(prefix + "images/") for path in body["image_asset_keys"].values())
    assert job["status"] == "draft"
    assert storage[body["source_asset_key"]] == storage[job["input_path"]]


def test_import_promotion_rejects_project_owned_by_another_user(monkeypatch, tmp_path):
    client, _headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)

    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=_auth_headers(8),
    )

    assert response.status_code == 404
    assert not [path for path in storage if path.startswith("projects/")]


def test_import_promotion_rejects_archived_project(monkeypatch, tmp_path):
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    archived = client.post(
        f"/projects/{project['id']}/archive", headers=headers,
        json={
            "expected_revision": project["revision"],
            "operation_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        },
    )
    assert archived.status_code == 200, archived.json()

    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=headers,
    )

    assert response.status_code == 409
    assert not [path for path in storage if path.startswith("projects/")]


def test_import_promotion_rejects_oversized_source_before_upload(monkeypatch, tmp_path):
    client, headers, project, job, _storage = project_with_import_fixture(monkeypatch, tmp_path)
    uploads = []
    monkeypatch.setattr(
        index,
        "_validated_import_source",
        lambda *_args: ({"source_hash": "a" * 64}, job, b"x" * (index.MAX_QUOTE_REQUEST_BYTES + 1)),
    )
    monkeypatch.setattr(index, "_storage_upload_bytes", lambda *args: uploads.append(args))

    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=headers,
    )

    assert response.status_code == 413
    assert uploads == []


def test_import_promotion_preflights_all_preview_sizes_before_first_upload(monkeypatch, tmp_path):
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    preview_path = next(iter(job["metadata"]["import_preview_paths"].values()))
    storage[preview_path] = b"x" * (index.IMPORT_PREVIEW_IMAGE_MAX_BYTES + 1)
    uploads = []

    def upload(path, content, content_type="application/octet-stream"):
        uploads.append((path, content, content_type))
        storage[path] = content

    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=headers,
    )

    assert response.status_code == 413
    assert uploads == []
    assert not [path for path in storage if path.startswith("projects/")]


def test_import_promotion_retry_is_byte_identical_and_does_not_reupload(monkeypatch, tmp_path):
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    uploads = []

    def upload(path, content, content_type="application/octet-stream"):
        uploads.append((path, content, content_type))
        storage[path] = content

    monkeypatch.setattr(index, "_storage_upload_bytes", upload)
    first = client.post(f"/projects/{project['id']}/imports/{job['id']}", headers=headers)
    first_bytes = {path: content for path, content, _mime in uploads}
    uploads.clear()
    second = client.post(f"/projects/{project['id']}/imports/{job['id']}", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert uploads == []
    assert {path: storage[path] for path in first_bytes} == first_bytes
    assert job["status"] == "draft"
