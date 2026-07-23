from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mobiliti_saas.web.api import index
from mobiliti_saas.quote_engine.quotation_import import build_import_manifest
from project_fixtures import valid_project_payload
from quotation_import_fixtures import write_import_fixture


IMPORT_ID = "22222222-2222-4222-8222-222222222222"


def _auth_headers(user_id: int = 7) -> dict[str, str]:
    token = index.create_access_token({"sub": str(user_id), "email": "cliente@example.com"})
    return {"Authorization": f"Bearer {token}"}


def _preview_png(size: tuple[int, int] = (4, 3), *, image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (23, 97, 151)).save(output, format=image_format)
    return output.getvalue()


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
        preview_path: _preview_png(),
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
    def create(path, content, content_type="application/octet-stream"):
        if path in storage:
            raise index._StorageObjectAlreadyExists(path)
        storage[path] = content

    monkeypatch.setattr(index, "_storage_create_bytes_if_absent", create)
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
    monkeypatch.setattr(index, "_storage_create_bytes_if_absent", lambda *args: uploads.append(args))

    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=headers,
    )

    assert response.status_code == 413
    assert uploads == []


@pytest.mark.parametrize(
    ("preview", "status"),
    [
        (b"", 409),
        (b"not-a-png", 409),
        (b"x" * (8 * 1024 * 1024 + 1), 413),
        (_preview_png((8193, 1)), 409),
        (_preview_png(image_format="JPEG"), 409),
    ],
    ids=["empty", "malformed", "oversized", "oversized-dimensions", "jpeg"],
)
def test_import_promotion_preflights_all_preview_images_before_first_upload(
    monkeypatch, tmp_path, preview, status
):
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    preview_path = next(iter(job["metadata"]["import_preview_paths"].values()))
    storage[preview_path] = preview
    uploads = []

    def upload(path, content, content_type="application/octet-stream"):
        if path in storage:
            raise index._StorageObjectAlreadyExists(path)
        uploads.append((path, content, content_type))
        storage[path] = content

    monkeypatch.setattr(index, "_storage_create_bytes_if_absent", upload)
    response = client.post(
        f"/projects/{project['id']}/imports/{job['id']}", headers=headers,
    )

    assert response.status_code == status
    assert uploads == []
    assert not [path for path in storage if path.startswith("projects/")]


def test_import_promotion_retry_is_byte_identical_and_does_not_reupload(monkeypatch, tmp_path):
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    uploads = []

    def upload(path, content, content_type="application/octet-stream"):
        if path in storage:
            raise index._StorageObjectAlreadyExists(path)
        uploads.append((path, content, content_type))
        storage[path] = content

    monkeypatch.setattr(index, "_storage_create_bytes_if_absent", upload)
    first = client.post(f"/projects/{project['id']}/imports/{job['id']}", headers=headers)
    first_bytes = {path: content for path, content, _mime in uploads}
    uploads.clear()
    second = client.post(f"/projects/{project['id']}/imports/{job['id']}", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert uploads == []
    assert {path: storage[path] for path in first_bytes} == first_bytes
    assert job["status"] == "draft"


def test_import_promotion_uses_real_dev_storage_for_first_copy_and_retry(monkeypatch, tmp_path):
    real_download = index._storage_download_bytes
    real_upload = index._storage_upload_bytes
    real_create = index._storage_create_bytes_if_absent
    monkeypatch.setattr(index, "DEV_STORE_DIR", tmp_path / "dev-store")
    client, headers, project, job, storage = project_with_import_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(index, "_storage_download_bytes", real_download)
    monkeypatch.setattr(index, "_storage_upload_bytes", real_upload)
    monkeypatch.setattr(index, "_storage_create_bytes_if_absent", real_create)

    for path, content in storage.items():
        if path.startswith("users/"):
            real_upload(path, content, "image/png" if path.endswith(".png") else "application/octet-stream")

    first = client.post(f"/projects/{project['id']}/imports/{job['id']}", headers=headers)
    second = client.post(f"/projects/{project['id']}/imports/{job['id']}", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert job["status"] == "draft"


class _R2Error(Exception):
    def __init__(self, code: str, status: int | None = None):
        response = {"Error": {"Code": code}}
        if status is not None:
            response["ResponseMetadata"] = {"HTTPStatusCode": status}
        self.response = response


def test_r2_missing_object_and_conditional_conflict_are_explicit(monkeypatch):
    class R2:
        def get_object(self, **_kwargs):
            raise _R2Error("NoSuchKey")

        def put_object(self, **kwargs):
            assert kwargs["IfNoneMatch"] == "*"
            raise _R2Error("PreconditionFailed", 412)

    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_r2_storage", lambda: True)
    monkeypatch.setattr(index, "_r2_client", lambda: R2())

    with pytest.raises(index._StorageObjectNotFound):
        index._storage_download_bytes("projects/7/example/missing.png")
    with pytest.raises(index._StorageObjectAlreadyExists):
        index._storage_create_bytes_if_absent("projects/7/example/asset.png", b"content", "image/png")


def test_r2_transport_error_is_not_treated_as_absence(monkeypatch):
    class R2:
        def get_object(self, **_kwargs):
            raise _R2Error("AccessDenied", 403)

    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_r2_storage", lambda: True)
    monkeypatch.setattr(index, "_r2_client", lambda: R2())

    with pytest.raises(RuntimeError) as error:
        index._storage_download_bytes("projects/7/example/asset.png")
    assert not isinstance(error.value, index._StorageObjectNotFound)


def test_supabase_conditional_conflict_uses_non_upsert_write(monkeypatch):
    requests = []

    def conflict(request, timeout):
        requests.append(request)
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", {}, BytesIO(b'{"message":"exists"}')
        )

    monkeypatch.setattr(index, "DEV_MODE", False)
    monkeypatch.setattr(index, "_use_r2_storage", lambda: False)
    monkeypatch.setattr(index, "SUPABASE_URL", "https://example.invalid")
    monkeypatch.setattr(index, "SUPABASE_SERVICE_KEY", "test-key")
    monkeypatch.setattr(index.urllib.request, "urlopen", conflict)

    with pytest.raises(index._StorageObjectAlreadyExists):
        index._storage_create_bytes_if_absent("projects/7/example/asset.png", b"content", "image/png")
    assert requests[0].get_header("X-upsert") == "false"
