import importlib.util
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from uuid import UUID


SCRIPT = Path("scripts/promote_validated_catalogs.py")


def _module():
    spec = importlib.util.spec_from_file_location("promote_validated_catalogs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_local_promotion_stages_an_absent_cursor_without_manual_marker(monkeypatch):
    module = _module()
    source_id = UUID("11111111-1111-1111-1111-111111111111")
    candidate_id = UUID("22222222-2222-2222-2222-222222222222")
    calls = []

    class Source:
        id = source_id

    class Snapshot:
        id = candidate_id
        source_hash = "a" * 64
        payload = {"items": []}

    class Repository:
        def get_source(self, supplier):
            return Source()

        def get_published_snapshot(self, source):
            calls.append(("get_published_snapshot", source.id))
            return None if len(calls) == 1 else Snapshot()

        def start_run(self, source, trigger, reviewed_by):
            return source_id

        def stage_candidate(self, run_id, snapshot, metrics, delta_link):
            calls.append(("stage_candidate", run_id, snapshot, metrics, delta_link))
            return candidate_id

        def publish_candidate(self, candidate, reviewed_by, note):
            calls.append(("publish_candidate", candidate, reviewed_by, note))
            return candidate

    monkeypatch.setattr(module.CatalogRepository, "from_environment", lambda: Repository())
    payload = {
        "source_hash": "a" * 64,
        "generated_at": "2026-08-12T12:00:00Z",
        "items": [],
    }

    result = module._promote({"sunon": payload}, 7, "validated")

    assert result == [{"supplier": "sunon", "status": "published", "items": 0, "source_hash": "a" * 64}]
    staged = next(call for call in calls if call[0] == "stage_candidate")
    assert staged[-1] is None
    assert all("manual://" not in repr(call) for call in calls)


def test_asset_upload_with_anon_key_sends_authorized_rest_secret(monkeypatch, tmp_path):
    module = _module()
    content = b"catalog-image"
    object_name = f"{hashlib.sha256(content).hexdigest()}.png"
    source = tmp_path / object_name
    source.write_bytes(content)
    captured = {}

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b""

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "publishable-key")
    monkeypatch.setenv("MOBILITI_REST_SECRET", "deployment-secret")
    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    assert module._upload_asset(object_name, source) == object_name
    assert captured["timeout"] == 30
    assert captured["request"].get_header("X-mobiliti-rest-secret") == "deployment-secret"


def test_existing_public_asset_is_verified_by_content_hash(monkeypatch, tmp_path):
    module = _module()
    content = b"existing-catalog-image"
    object_name = f"{hashlib.sha256(content).hexdigest()}.webp"
    source = tmp_path / object_name
    source.write_bytes(content)
    urls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return content

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        if len(urls) == 1:
            raise HTTPError(request.full_url, 409, "Conflict", {}, None)
        assert timeout == 30
        return Response()

    monkeypatch.setenv("SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "publishable-key")
    monkeypatch.setenv("MOBILITI_REST_SECRET", "deployment-secret")
    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    assert module._upload_asset(object_name, source) == object_name
    assert urls[1].endswith(f"/storage/v1/object/public/catalog-assets/{object_name}")
