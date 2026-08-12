import importlib.util
from datetime import datetime, timezone
from pathlib import Path
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
