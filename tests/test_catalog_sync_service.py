import os
import hashlib
import io
import subprocess
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image
import mobiliti_saas.worker.catalog_sync.service as catalog_service

from mobiliti_saas.worker.catalog_sync.importers.alma import (
    AlmaAssetBinding,
    AlmaSnapshotBuild,
)
from mobiliti_saas.worker.catalog_sync.importers.sunon import (
    SunonAssetBinding,
    SunonSnapshotBuild,
)
from mobiliti_saas.worker.catalog_sync.importers.common import (
    CatalogAssetBinding,
    CatalogSnapshotBuild,
    ImageAsset,
)
from mobiliti_saas.worker.catalog_sync.graph import (
    DeltaExpiredError,
    DeltaResult,
    DownloadedFile,
    GraphError,
    GraphItem,
)
from mobiliti_saas.worker.catalog_sync.repository import (
    RunRecord,
    SnapshotRecord,
    SourceFileRecord,
    SourceRecord,
)
from mobiliti_saas.worker.catalog_sync.service import (
    ADAPTERS,
    CATALOG_EXIT_DISABLED,
    CATALOG_EXIT_NO_WORK,
    CatalogDiff,
    SyncResult,
    main,
    classify_snapshot_diff,
    run_supplier_sync,
    run_due_once,
)


SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")
RUN_ID = UUID("22222222-2222-2222-2222-222222222222")
CANDIDATE_ID = UUID("33333333-3333-3333-3333-333333333333")
SNAPSHOT_ID = UUID("44444444-4444-4444-4444-444444444444")
FILE_ONE_ID = UUID("55555555-5555-5555-5555-555555555555")
ALMA_ONE = "SPEC Guide-Alma-KUN.xlsx"
ALMA_TWO = "SPEC GUIDES 2026/ALMA/Spec guide-Alma-KUN Design.xlsx"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DELTA = "https://graph.microsoft.com/v1.0/delta?$deltatoken=opaque-secret"
ROOT_PATH = "PROYECTOS CET - 2026/LISTAS DE PRECIOS PROVEEDORES"


def item(**changes):
    row = {
        "internal_id": "alma:kun:chair-1",
        "supplier": "alma",
        "product_key": "chair-1",
        "sku": "CHAIR-1",
        "code_status": "verified",
        "brand": "KUN",
        "collection": "KUN",
        "name": "Chair One",
        "description": "Complete description",
        "unit": "piece",
        "availability_type": "stocked",
        "stock": "5",
        "lead_time": "2 weeks",
        "base_price_options": [],
        "add_on_options": [],
        "base_currency": "USD",
        "price_net": "100.000000",
        "tax_rate": "0.160000",
        "attributes": {"color": "black"},
        "image_url": "https://example.test/chair-1.webp",
        "image_kind": "official",
        "product_url": "https://example.test/chair-1",
        "warnings": [],
        "source_reference": "spec:E9",
    }
    row.update(changes)
    return row


def snapshot(*, source_hash="a" * 64, generated_at="2026-07-16T12:00:00Z", items=None):
    return {
        "supplier": "alma",
        "source_hash": source_hash,
        "generated_at": generated_at,
        "items": deepcopy(items if items is not None else [item()]),
    }


def sunon_item(**changes):
    row = item(
        internal_id="sunon:office:chair-1",
        supplier="sunon",
        product_key="chair-1",
        sku="SUNON-CHAIR-1",
        image_url="",
        image_kind="placeholder",
    )
    row.update(changes)
    return row


def sunon_snapshot(*, source_hash="a" * 64, items=None):
    return {
        "supplier": "sunon",
        "source_hash": source_hash,
        "generated_at": "2026-07-16T12:00:00Z",
        "items": deepcopy(items if items is not None else [sunon_item()]),
    }


def source():
    return SourceRecord(
        SOURCE_ID, "alma", "ALMA", "alma", "drive-1", "root-1", DELTA, True, SNAPSHOT_ID
    )


def run(status="requested"):
    return RunRecord(RUN_ID, SOURCE_ID, "manual", status, 7, None, {}, None)


def source_file(
    *, file_id=FILE_ONE_ID, drive_item_id="graph-1", path=ALMA_ONE,
    e_tag='"etag-1"', sha256="b" * 64, deleted=False,
):
    return SourceFileRecord(
        file_id, SOURCE_ID, drive_item_id, path, e_tag, None, 3, sha256, MIME_XLSX,
        f"catalog-sources/{sha256}.xlsx", "valid", {}, RUN_ID, deleted,
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc) if deleted else None,
        RUN_ID if deleted else None,
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )


def published(payload=None):
    payload = deepcopy(payload or snapshot())
    return SnapshotRecord(
        SNAPSHOT_ID, "alma", payload["source_hash"],
        datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00")),
        "published", payload,
    )


def graph_item(*, item_id="graph-1", name="SPEC Guide-Alma-KUN.xlsx",
               parent=f"/drives/drive-1/root:/{ROOT_PATH}",
               e_tag='"etag-1"', deleted=None):
    return GraphItem(
        item_id, name, parent, None if deleted else 3, None if deleted else e_tag, None,
        None if deleted else MIME_XLSX, False, deleted,
    )


class FakeRepository:
    def __init__(self, *, active=(), history=(), published_snapshot=None, start=True):
        self.source = source()
        self.active = {row.drive_item_id: row for row in active}
        self.history = {(row.drive_item_id, row.e_tag): row for row in (*active, *history)}
        self.published_snapshot = published_snapshot
        self.start = start
        self.calls = []
        self.next_file_id = 100

    def get_source(self, supplier):
        self.calls.append(("get_source", supplier))
        return self.source

    def start_run(self, source_id, trigger, requested_by):
        self.calls.append(("start_run", source_id, trigger, requested_by))
        return RUN_ID if self.start else None

    def create_run(self, source_id, trigger, requested_by):
        self.calls.append(("create_run", source_id, trigger, requested_by))
        return run()

    def claim_run(self, run_id):
        self.calls.append(("claim_run", run_id))
        return run("running")

    def get_published_snapshot(self, source_record):
        self.calls.append(("get_published_snapshot", source_record.id))
        return self.published_snapshot

    def find_file(self, source_id, drive_item_id, e_tag):
        self.calls.append(("find_file", source_id, drive_item_id, e_tag))
        return self.history.get((drive_item_id, e_tag))

    def list_latest_files(self, source_id, allowed_paths):
        self.calls.append(("list_latest_files", source_id, tuple(allowed_paths)))
        return tuple(sorted(self.active.values(), key=lambda row: row.path))

    def store_raw_if_absent(self, local_path, sha256, extension, mime_type):
        self.calls.append(("store_raw_if_absent", sha256, extension, mime_type))
        return f"catalog-sources/{sha256}.{extension.removeprefix('.')}"

    def store_catalog_asset_if_absent(self, object_name, content, content_type):
        self.calls.append(("store_catalog_asset_if_absent", object_name, content, content_type))
        return object_name

    def materialize_raw_if_present(self, row, destination):
        self.calls.append(("materialize_raw_if_present", row.id, destination))
        return DownloadedFile(Path(destination), row.size_bytes, row.sha256)

    def record_source_file(self, source_id, graph_row, downloaded, object_path, run_id, validation):
        self.calls.append(("record_source_file", graph_row.drive_item_id if hasattr(graph_row, "drive_item_id") else graph_row.id,
                           graph_row.path, downloaded.sha256, object_path, validation))
        self.next_file_id += 1
        row = source_file(
            file_id=UUID(int=self.next_file_id), drive_item_id=graph_row.id,
            path=graph_row.path, e_tag=graph_row.e_tag, sha256=downloaded.sha256,
        )
        self.active[graph_row.id] = row
        return row

    def mark_file_deleted(self, source_id, drive_item_id, run_id):
        self.calls.append(("mark_file_deleted", source_id, drive_item_id, run_id))
        self.active.pop(drive_item_id, None)

    def stage_candidate(self, run_id, candidate, metrics, delta_link):
        self.calls.append(("stage_candidate", run_id, candidate, dict(metrics), delta_link))
        return CANDIDATE_ID

    def auto_publish_candidate(self, candidate_id):
        self.calls.append(("auto_publish_candidate", candidate_id))
        return candidate_id

    def finish_no_changes(self, run_id, metrics, delta_link):
        self.calls.append(("finish_no_changes", run_id, dict(metrics), delta_link))

    def finish_failed(self, run_id, error_code, metrics):
        self.calls.append(("finish_failed", run_id, error_code, dict(metrics)))


class FakeGraph:
    def __init__(self, items=(), *, sha256="c" * 64, error=None):
        self.result = DeltaResult(tuple(items), DELTA)
        self.sha256 = sha256
        self.error = error
        self.calls = []

    def iter_delta(self, drive_id, root_id, delta_link=None):
        self.calls.append(("iter_delta", drive_id, root_id, delta_link))
        return self.result

    def download_content(self, drive_id, graph_row, destination, max_bytes):
        self.calls.append(("download_content", drive_id, graph_row.id, max_bytes))
        if self.error:
            raise self.error
        return DownloadedFile(Path(destination), 3, self.sha256)


class SequenceDeltaGraph(FakeGraph):
    def __init__(self, responses):
        super().__init__()
        self.responses = iter(responses)

    def iter_delta(self, drive_id, root_id, delta_link=None):
        self.calls.append(("iter_delta", drive_id, root_id, delta_link))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def call(repo, graph, adapter, *, dry_run=False):
    return run_supplier_sync(
        "alma", "manual", 7, dry_run,
        repository=repo, graph_client=graph, adapters={"alma": adapter},
    )


def call_sunon(repo, graph, adapter):
    return run_supplier_sync(
        "sunon", "manual", 7, False,
        repository=repo, graph_client=graph, adapters={"sunon": adapter},
    )


def call_names(repo):
    return [entry[0] for entry in repo.calls]


def test_exact_legacy_local_cursor_starts_a_full_crawl():
    repo = FakeRepository(published_snapshot=published())
    legacy = f"manual://validated-local-snapshot/alma/{'a' * 64}"
    repo.source = SourceRecord(
        SOURCE_ID, "alma", "ALMA", "alma", "drive-1", "root-1", legacy, True, SNAPSHOT_ID
    )
    graph = SequenceDeltaGraph((DeltaResult((), DELTA),))

    result = call(repo, graph, lambda files: snapshot())

    assert result.status == "no_changes"
    assert graph.calls == [("iter_delta", "drive-1", "root-1", None)]


def test_malformed_legacy_cursor_is_passed_to_graph_and_fails_closed():
    repo = FakeRepository()
    malformed = f"manual://validated-local-snapshot/alma/{'A' * 64}"
    repo.source = SourceRecord(
        SOURCE_ID, "alma", "ALMA", "alma", "drive-1", "root-1", malformed, True, SNAPSHOT_ID
    )
    graph = SequenceDeltaGraph((GraphError("invalid cursor"),))

    result = call(repo, graph, lambda files: snapshot())

    assert result.status == "failed" and result.error_code == "graph_failed"
    assert graph.calls == [("iter_delta", "drive-1", "root-1", malformed)]


def test_expired_delta_retries_once_without_a_cursor():
    repo = FakeRepository(published_snapshot=published())
    graph = SequenceDeltaGraph((DeltaExpiredError("expired"), DeltaResult((), DELTA)))

    result = call(repo, graph, lambda files: snapshot())

    assert result.status == "no_changes"
    assert graph.calls == [
        ("iter_delta", "drive-1", "root-1", DELTA),
        ("iter_delta", "drive-1", "root-1", None),
    ]


def test_second_expired_delta_finishes_as_graph_failed():
    repo = FakeRepository()
    graph = SequenceDeltaGraph((DeltaExpiredError("expired"), DeltaExpiredError("expired again")))

    result = call(repo, graph, lambda files: snapshot())

    assert result.status == "failed" and result.error_code == "graph_failed"
    assert graph.calls == [
        ("iter_delta", "drive-1", "root-1", DELTA),
        ("iter_delta", "drive-1", "root-1", None),
    ]


@pytest.mark.parametrize(
    "cursor",
    (None, f"manual://validated-local-snapshot/alma/{'a' * 64}"),
    ids=("initial-none", "legacy-marker"),
)
def test_full_crawl_deletes_active_file_missing_from_live_enumeration(cursor):
    old = source_file(drive_item_id="old-graph-1")
    repo = FakeRepository(active=(old,))
    repo.source = SourceRecord(
        SOURCE_ID,
        "alma",
        "ALMA",
        "alma",
        "drive-1",
        "root-1",
        cursor,
        True,
        SNAPSHOT_ID,
    )
    graph = SequenceDeltaGraph((DeltaResult((), DELTA),))
    captured = []

    result = call(repo, graph, lambda files: captured.append(files) or snapshot())

    assert result.status == "awaiting_approval"
    assert ("mark_file_deleted", SOURCE_ID, "old-graph-1", RUN_ID) in repo.calls
    assert metrics(result)["tombstones"] == 1
    assert captured == [()]
    assert not any(
        call[0] == "materialize_raw_if_present" and call[1] == old.id
        for call in repo.calls
    )


def test_expired_delta_full_crawl_replaces_old_id_before_staging_candidate():
    old = source_file(drive_item_id="old-graph-1", sha256="b" * 64)
    repo = FakeRepository(active=(old,))
    graph = SequenceDeltaGraph((
        DeltaExpiredError("expired"),
        DeltaResult((graph_item(item_id="new-graph-1"),), DELTA),
    ))
    captured = []

    result = call(repo, graph, lambda files: captured.append(files) or snapshot())

    assert result.status == "awaiting_approval"
    assert graph.calls[:2] == [
        ("iter_delta", "drive-1", "root-1", DELTA),
        ("iter_delta", "drive-1", "root-1", None),
    ]
    assert ("mark_file_deleted", SOURCE_ID, "old-graph-1", RUN_ID) in repo.calls
    assert captured[0][0].path == ALMA_ONE
    assert captured[0][0].sha256 == "c" * 64
    assert len(captured[0]) == 1
    assert any(call[0] == "stage_candidate" for call in repo.calls)
    assert not any(
        call[0] == "materialize_raw_if_present" and call[1] == old.id
        for call in repo.calls
    )


def _track_run_directories(monkeypatch, tmp_path):
    real_temporary_directory = catalog_service.tempfile.TemporaryDirectory
    created = []

    def temporary_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        manager = real_temporary_directory(*args, **kwargs)
        created.append(Path(manager.name))
        return manager

    monkeypatch.setattr(catalog_service.tempfile, "TemporaryDirectory", temporary_directory)
    return created


class DestinationGraph(FakeGraph):
    def __init__(self, *args, fail_after_destination=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.destinations = []
        self.fail_after_destination = fail_after_destination

    def download_content(self, drive_id, graph_row, destination, max_bytes):
        self.destinations.append(Path(destination))
        if self.fail_after_destination:
            raise RuntimeError("download failed")
        return super().download_content(drive_id, graph_row, destination, max_bytes)


class DestinationRepository(FakeRepository):
    def __init__(self, *args, fail_after_materialization=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.destinations = []
        self.fail_after_materialization = fail_after_materialization

    def materialize_raw_if_present(self, row, destination):
        self.destinations.append(Path(destination))
        return super().materialize_raw_if_present(row, destination)

    def get_published_snapshot(self, source_record):
        if self.fail_after_materialization:
            raise RuntimeError("repository failed")
        return super().get_published_snapshot(source_record)


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("published", "published"),
        ("candidate", "awaiting_approval"),
        ("no_changes", "no_changes"),
        ("dry_run", "dry_run"),
        ("adapter_error", "failed"),
        ("repository_error", "failed"),
        ("graph_error", "failed"),
    ],
)
def test_run_temp_directory_contains_all_materializations_and_is_always_cleaned(
    monkeypatch, tmp_path, scenario, expected_status,
):
    created = _track_run_directories(monkeypatch, tmp_path)
    repo = DestinationRepository(
        active=(source_file(),),
        published_snapshot=published(),
        fail_after_materialization=scenario == "repository_error",
    )
    graph = DestinationGraph(
        (graph_item(e_tag='"etag-2"'),) if scenario in {"candidate", "graph_error"} else (),
        sha256="c" * 64,
        fail_after_destination=scenario == "graph_error",
    )

    def adapter(files):
        if scenario == "adapter_error":
            raise RuntimeError("adapter failed")
        if scenario == "candidate":
            return snapshot(source_hash="c" * 64, items=[item(price_net="101.000000")])
        if scenario == "published":
            return snapshot(items=[item(stock="9")])
        return snapshot()

    result = call(repo, graph, adapter, dry_run=scenario == "dry_run")

    assert result.status == expected_status
    assert len(created) == 1
    destinations = repo.destinations + graph.destinations
    assert destinations
    assert all(path.parent == created[0] for path in destinations)
    assert not created[0].exists()


def test_consecutive_syncs_use_distinct_cleaned_temp_directories(monkeypatch, tmp_path):
    created = _track_run_directories(monkeypatch, tmp_path)

    for _ in range(3):
        repo = DestinationRepository(active=(source_file(),), published_snapshot=published())
        assert call(repo, DestinationGraph(), lambda files: snapshot()).status == "no_changes"

    assert len(created) == 3
    assert len(set(created)) == 3
    assert all(not path.exists() for path in created)


def test_claimed_run_is_executed_without_starting_a_second_run():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    result = run_supplier_sync(
        "alma", "manual", 7, False,
        repository=repo,
        graph_client=FakeGraph(),
        adapters={"alma": lambda files: snapshot()},
        claimed_run_id=RUN_ID,
    )

    assert result.status == "no_changes"
    assert "start_run" not in call_names(repo)
    assert ("finish_no_changes", RUN_ID, dict(result.metrics), DELTA) in repo.calls


def test_due_runner_is_disabled_and_empty_by_default(monkeypatch):
    monkeypatch.delenv("CATALOG_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("CATALOG_ENABLED_SUPPLIERS", raising=False)
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.CatalogRepository.from_environment",
        lambda: pytest.fail("repository must not be opened"),
    )

    assert run_due_once() == "disabled"
    assert main(["--due"]) == CATALOG_EXIT_DISABLED


def test_due_cli_runs_from_repository_root_without_network():
    env = dict(os.environ)
    env.pop("CATALOG_SYNC_ENABLED", None)
    env.pop("CATALOG_ENABLED_SUPPLIERS", None)

    result = subprocess.run(
        [sys.executable, "-m", "mobiliti_saas.worker.catalog_sync.service", "--due"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == CATALOG_EXIT_DISABLED
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(("status", "exit_code"), [
    ("published", catalog_service.CATALOG_EXIT_WORKED),
    ("no_changes", catalog_service.CATALOG_EXIT_WORKED),
    ("awaiting_approval", catalog_service.CATALOG_EXIT_WORKED),
    ("no_work", CATALOG_EXIT_NO_WORK),
    ("disabled", CATALOG_EXIT_DISABLED),
    ("failed", catalog_service.CATALOG_EXIT_FAILED),
])
def test_due_cli_uses_bounded_exit_protocol(monkeypatch, status, exit_code):
    monkeypatch.setattr(catalog_service, "run_due_once", lambda: status)

    assert main(["--due"]) == exit_code


def test_due_runner_claims_at_most_one_run_and_uses_explicit_registry(monkeypatch):
    seen = []

    class DueRepository:
        def recover_stale_syncs(self, suppliers):
            seen.append(("recover", suppliers))
            return 0

        def claim_next_sync(self, suppliers):
            seen.append(("claim", suppliers))
            return type("Claim", (), {
                "run_id": RUN_ID,
                "supplier": "alma",
                "trigger_type": "manual",
                "requested_by": 7,
            })()

    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", "alma,sunon")
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.CatalogRepository.from_environment",
        DueRepository,
    )
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.GraphCatalogClient.from_environment",
        lambda: object(),
    )
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.run_supplier_sync",
        lambda supplier, trigger, requested_by, dry_run, **kwargs: (
            seen.append((supplier, trigger, requested_by, dry_run, kwargs)),
            SyncResult("no_changes", RUN_ID, None, None, (), None, False),
        )[1],
    )

    assert set(ADAPTERS) == {
        "cr_global",
        "sonara",
        "sunon",
        "alma",
        "lumbro",
        "jome",
        "lauco",
        "idelika",
        "conceptos",
    }
    assert ADAPTERS["cr_global"].__name__ == "build_cr_global_snapshot_with_assets"
    assert ADAPTERS["sonara"].__name__ == "build_sonara_snapshot_with_assets"
    assert ADAPTERS["lumbro"].__name__ == "build_lumbro_snapshot_with_assets"
    assert ADAPTERS["jome"].__name__ == "build_jome_snapshot_with_assets"
    assert ADAPTERS["lauco"].__name__ == "build_lauco_snapshot_with_assets"
    assert ADAPTERS["idelika"].__name__ == "build_idelika_snapshot_with_assets"
    assert ADAPTERS["conceptos"].__name__ == "build_conceptos_snapshot_with_assets"
    assert run_due_once() == "no_changes"
    assert seen[0] == ("recover", ("alma", "sunon"))
    assert seen[1] == ("claim", ("alma", "sunon"))
    assert seen[2][0:4] == ("alma", "manual", 7, False)
    assert seen[2][4]["claimed_run_id"] == RUN_ID
    assert seen[2][4]["adapters"] is ADAPTERS


@pytest.mark.parametrize("supplier", ["lumbro", "idelika", "conceptos"])
def test_due_scheduler_accepts_generic_supplier_allowlist(monkeypatch, supplier):
    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", supplier)

    assert catalog_service._enabled_suppliers() == (supplier,)


def test_lumbro_task6_snapshot_metadata_is_valid_preserved_and_deterministic():
    lumbro_item = item(
        internal_id="lumbro:variant:barcelona",
        supplier="lumbro",
        product_key="barcelona",
        sku="BARCELONA",
        brand="Lumbro",
        collection="Empotrables",
        name="Barcelona",
        unit="PZA",
        base_currency="MXN",
        price_net="2824.000000",
        tax_rate="0.160000",
    )
    coverage = {
        "parsed_price_rows": 1,
        "imported_rows": 1,
        "reconciled_rows": 0,
        "excluded_rows": 0,
        "exclusions": [],
    }
    payload = {
        "supplier": "lumbro",
        "source_hash": "b" * 64,
        "generated_at": "2026-07-18T12:00:00Z",
        "items": [lumbro_item],
        "metadata": {
            "sources": [{"path": "LUMBRO/LP/lista.pdf", "sha256": "c" * 64}],
            "link_manifest_fingerprint": "d" * 64,
            "coverage": coverage,
        },
    }

    loaded = catalog_service._validate_snapshot(payload, expected_supplier="lumbro")

    assert loaded["metadata"]["coverage"] == coverage
    assert loaded["metadata"] is not payload["metadata"]
    reordered = deepcopy(payload)
    reordered["metadata"] = dict(reversed(tuple(reordered["metadata"].items())))
    assert catalog_service._identity(payload) == catalog_service._identity(reordered)
    changed = deepcopy(payload)
    changed["metadata"]["coverage"]["parsed_price_rows"] = 2
    assert catalog_service._identity(payload) != catalog_service._identity(changed)


def test_snapshot_rejects_invalid_unbounded_metadata_and_unknown_root_keys():
    base = snapshot()
    invalid_metadata = deepcopy(base)
    invalid_metadata["metadata"] = []
    unbounded_metadata = deepcopy(base)
    unbounded_metadata["metadata"] = {"oversized": "x" * 300_000}
    unknown_root = deepcopy(base)
    unknown_root["unexpected"] = True

    for payload in (invalid_metadata, unbounded_metadata, unknown_root):
        with pytest.raises(ValueError, match="Invalid snapshot"):
            catalog_service._validate_snapshot(payload)


def test_configured_path_rejects_same_name_lumbro_discovery_with_wrong_graph_id():
    from mobiliti_saas.worker.catalog_sync import load_source_config

    config = next(
        row for row in load_source_config(Path("mobiliti_saas/worker/catalog_sync/sources.json"))
        if row.supplier == "lumbro"
    )
    discovered = graph_item(
        item_id="01DHXXN73PQIV3NEC74BFIAXGF7HN3S3NF",
        name="LISTA DE PRECIOS MULTICONTACTOS 2026.pdf",
        parent=f"/drives/drive-1/root:/{ROOT_PATH}/LUMBRO/LP",
    )

    assert catalog_service._configured_path(discovered, config, "drive-1") is None


def test_due_runner_claims_before_graph_initialization(monkeypatch):
    calls = []

    class DueRepository:
        def recover_stale_syncs(self, suppliers):
            calls.append(("recover", suppliers))
            return 1

        def claim_next_sync(self, suppliers):
            calls.append(("claim", suppliers))
            return None

    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", "alma")
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.CatalogRepository.from_environment",
        DueRepository,
    )
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.GraphCatalogClient.from_environment",
        lambda: pytest.fail("Graph must not be initialized without a claim"),
    )

    assert run_due_once() == "no_work"
    assert main(["--due"]) == CATALOG_EXIT_NO_WORK
    assert calls == [
        ("recover", ("alma",)), ("claim", ("alma",)),
        ("recover", ("alma",)), ("claim", ("alma",)),
    ]


def test_due_runner_closes_claim_when_dependency_preparation_fails(monkeypatch):
    calls = []

    class DueRepository:
        def recover_stale_syncs(self, _suppliers):
            return 0

        def claim_next_sync(self, _suppliers):
            return type("Claim", (), {
                "run_id": RUN_ID,
                "supplier": "alma",
                "trigger_type": "manual",
                "requested_by": 7,
            })()

        def finish_failed(self, run_id, error_code, metrics):
            calls.append((run_id, error_code, metrics))

    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", "alma")
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.CatalogRepository.from_environment",
        DueRepository,
    )
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.GraphCatalogClient.from_environment",
        lambda: object(),
    )
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.run_supplier_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )

    assert run_due_once() == "failed"
    assert calls == [(RUN_ID, "dependency_failed", {})]


@pytest.mark.parametrize("value", ["", "sunon,unknown", "sunon,sunon", "cr_global"])
def test_due_runner_rejects_invalid_or_ambiguous_supplier_env(monkeypatch, value):
    monkeypatch.setenv("CATALOG_SYNC_ENABLED", "1")
    monkeypatch.setenv("CATALOG_ENABLED_SUPPLIERS", value)
    monkeypatch.setattr(
        "mobiliti_saas.worker.catalog_sync.service.CatalogRepository.from_environment",
        lambda: pytest.fail("repository must not be opened"),
    )

    assert run_due_once() == "disabled"


def metrics(result):
    return dict(result.metrics)


def test_diff_is_frozen_deterministic_and_ignores_generated_timestamp():
    previous = snapshot(generated_at="2026-07-15T00:00:00Z")
    candidate = snapshot(generated_at="2026-07-16T00:00:00Z")
    result = classify_snapshot_diff(previous, candidate)
    assert result == CatalogDiff(0, 0, 0, 0, 0, (), False)
    with pytest.raises(FrozenInstanceError):
        result.changed_count = 1


def test_diff_stock_and_lead_time_only_is_auto_publishable():
    candidate = snapshot(items=[item(stock="8", lead_time="3 weeks")])
    result = classify_snapshot_diff(snapshot(), candidate)
    assert result.changed_count == 1
    assert result.operational_count == 1
    assert result.material_count == 0
    assert result.changed_fields == ("lead_time", "stock")
    assert result.auto_publishable is True


def _approved_sunon_reference(**changes):
    asset = {
        "bucket": "catalog-assets",
        "path": f"{'f' * 64}.png",
        "image_kind": "generated_reference",
        "label": "Imagen de referencia",
        "approved": True,
    }
    asset.update(changes)
    return asset


def test_sunon_preserves_valid_approved_generated_reference_canonically():
    previous = sunon_snapshot(items=[sunon_item(
        image_kind="generated_reference",
        attributes={
            "color": "black",
            "approved_asset": _approved_sunon_reference(label="Referencia anterior no confiable"),
        },
    )])
    candidate = sunon_snapshot(items=[sunon_item(attributes={"color": "blue"})])

    result = catalog_service._preserve_curated_visuals(previous, candidate)

    row = result["items"][0]
    assert row["image_kind"] == "generated_reference"
    assert row["image_url"] == ""
    assert row["attributes"] == {
        "color": "blue",
        "approved_asset": {
            "bucket": "catalog-assets",
            "path": f"{'f' * 64}.png",
            "image_kind": "generated_reference",
            "label": "Imagen de referencia",
            "approved": True,
        },
    }


def test_sunon_preserves_legacy_approved_reference_without_nested_kind():
    previous = sunon_snapshot(items=[sunon_item(
        image_kind="generated_reference",
        attributes={"approved_asset": _approved_sunon_reference(image_kind=None)},
    )])
    del previous["items"][0]["attributes"]["approved_asset"]["image_kind"]
    candidate = sunon_snapshot()

    result = catalog_service._preserve_curated_visuals(previous, candidate)

    assert result["items"][0]["attributes"]["approved_asset"]["image_kind"] == "generated_reference"


@pytest.mark.parametrize(
    "asset_changes,previous_changes,candidate_changes",
    [
        ({"bucket": "untrusted-assets"}, {}, {}),
        ({"path": "F" * 64 + ".png"}, {}, {}),
        ({"approved": False}, {}, {}),
        ({"image_kind": "official"}, {}, {}),
        ({}, {"supplier": "alma"}, {}),
        ({}, {"product_key": "other-chair"}, {}),
        ({}, {}, {"supplier": "alma"}),
        ({}, {}, {"product_key": "other-chair"}),
    ],
    ids=(
        "bucket", "path", "approved", "nested-kind", "previous-supplier",
        "previous-product-key", "candidate-supplier", "candidate-product-key",
    ),
)
def test_sunon_does_not_inherit_invalid_or_mismatched_reference(
    asset_changes, previous_changes, candidate_changes,
):
    previous_row = sunon_item(
        image_kind="generated_reference",
        attributes={"approved_asset": _approved_sunon_reference(**asset_changes)},
    )
    previous_row.update(previous_changes)
    candidate_row = sunon_item(attributes={"color": "blue"})
    candidate_row.update(candidate_changes)
    previous = sunon_snapshot(items=[previous_row])
    candidate = sunon_snapshot(items=[candidate_row])

    result = catalog_service._preserve_curated_visuals(previous, candidate)

    row = result["items"][0]
    assert row["image_kind"] == "placeholder"
    assert row["image_url"] == ""
    assert row["attributes"] == {"color": "blue"}


def test_sunon_never_replaces_candidate_official_image():
    previous = sunon_snapshot(items=[sunon_item(
        image_kind="generated_reference",
        attributes={"approved_asset": _approved_sunon_reference()},
    )])
    candidate = sunon_snapshot(items=[sunon_item(
        image_kind="official",
        image_url="https://example.test/live-sunon.png",
        attributes={"image_match": {"status": "exact_xlsx"}},
    )])

    result = catalog_service._preserve_curated_visuals(previous, candidate)

    assert result["items"][0]["image_kind"] == "official"
    assert result["items"][0]["image_url"] == "https://example.test/live-sunon.png"
    assert result["items"][0]["attributes"] == {"image_match": {"status": "exact_xlsx"}}


def test_sunon_sync_stages_preserved_reference_without_image_regression():
    previous_payload = sunon_snapshot(items=[sunon_item(
        image_kind="generated_reference",
        attributes={"approved_asset": _approved_sunon_reference()},
    )])
    candidate = sunon_snapshot(
        source_hash="c" * 64,
        items=[sunon_item(price_net="101.000000", attributes={})],
    )
    repo = FakeRepository(published_snapshot=published(previous_payload))
    repo.source = SourceRecord(
        SOURCE_ID, "sunon", "Sunon", "sunon", "drive-1", "root-1", DELTA,
        True, SNAPSHOT_ID,
    )

    result = call_sunon(repo, FakeGraph(), lambda files: candidate)

    assert result.status == "awaiting_approval"
    assert result.diff.changed_fields == ("price_net",)
    staged = next(entry[2] for entry in repo.calls if entry[0] == "stage_candidate")
    assert staged["items"][0]["image_kind"] == "generated_reference"
    assert staged["items"][0]["attributes"]["approved_asset"] == {
        "bucket": "catalog-assets",
        "path": f"{'f' * 64}.png",
        "image_kind": "generated_reference",
        "label": "Imagen de referencia",
        "approved": True,
    }


def _curated_item(supplier, *, image_kind="generated_reference", **changes):
    sha256 = "e" * 64
    row = item(
        internal_id=f"{supplier}:chair-1",
        supplier=supplier,
        product_key="chair-1",
        sku=f"{supplier.upper()}-CHAIR-1",
        name="Silla Uno",
        description="Silla ejecutiva con brazos",
        stock="5",
        base_price_options=[{
            "id": "black",
            "name": "Negro",
            "price_net": "100.000000",
            "available": True,
        }],
        add_on_options=[{
            "id": "headrest",
            "name": "Cabecera",
            "price_net": "20.000000",
            "available": True,
            "compatible_base_option_ids": ["black"],
        }],
        attributes={
            "variant": "Respaldo alto",
            "dimensions": "60 x 60 x 110 cm",
            "commercial_note": "anterior",
            "approved_asset": {
                "bucket": "catalog-assets",
                "path": f"{sha256}.png",
                "image_kind": image_kind,
                "label": "Etiqueta anterior",
                "approved": True,
            },
            "image_reference": {
                "asset_sha256": sha256,
                "approved": True,
                "configuration_supported": True,
                "full_product_visible": True,
                "not_cropped": True,
                "direct_product_reference": True,
                "image_source_url": "https://media.example.test/chair-1.png",
            },
            "source_image_url": "https://media.example.test/chair-1.png",
            "web_image_quality": {"sha256": sha256, "width": 1200, "height": 1200},
        },
        image_url="",
        image_kind=image_kind,
        product_url="https://supplier.example.test/products/chair-1",
    )
    row.update(changes)
    return row


def _curated_snapshot(supplier, row):
    return {
        "supplier": supplier,
        "source_hash": "a" * 64,
        "generated_at": "2026-08-18T12:00:00Z",
        "items": [deepcopy(row)],
    }


def _refresh_item(supplier, *, family=False):
    row = _curated_item(
        supplier,
        image_kind="official" if family else "placeholder",
        price_net="150.000000",
        stock="9",
        product_url="https://supplier.example.test/catalog.pdf#page=7",
    )
    row["base_price_options"][0].update(price_net="175.000000", available=False)
    row["add_on_options"][0].update(price_net="30.000000", available=False)
    row["attributes"] = {
        "variant": "Respaldo alto",
        "dimensions": "60 x 60 x 110 cm",
        "commercial_note": "nueva",
    }
    if family:
        row["attributes"].update({
            "image_match": {"status": "family_pdf"},
            "approved_asset": {
                "bucket": "catalog-assets",
                "path": f"{'d' * 64}.png",
                "image_kind": "official",
                "label": "Familia PDF",
                "approved": True,
            },
        })
    return row


@pytest.mark.parametrize(
    "supplier,family",
    [("labenze", False), ("labenze", True), ("requiez", False), ("requiez", True)],
)
def test_curated_visual_survives_lower_priority_refresh_without_stale_commercial_data(
    supplier, family,
):
    previous_row = _curated_item(supplier)
    candidate_row = _refresh_item(supplier, family=family)

    result = catalog_service._preserve_curated_visuals(
        _curated_snapshot(supplier, previous_row),
        _curated_snapshot(supplier, candidate_row),
    )

    row = result["items"][0]
    assert row["image_kind"] == "generated_reference"
    assert row["image_url"] == ""
    assert row["product_url"] == "https://supplier.example.test/products/chair-1"
    assert row["price_net"] == "150.000000"
    assert row["stock"] == "9"
    assert row["base_price_options"][0] == {
        "id": "black",
        "name": "Negro",
        "price_net": "175.000000",
        "available": False,
    }
    assert row["attributes"]["commercial_note"] == "nueva"
    assert "image_match" not in row["attributes"]
    assert row["attributes"]["approved_asset"] == {
        "bucket": "catalog-assets",
        "path": f"{'e' * 64}.png",
        "image_kind": "generated_reference",
        "label": "Imagen de referencia",
        "approved": True,
    }
    assert row["attributes"]["image_reference"] == previous_row["attributes"]["image_reference"]
    assert row["attributes"]["source_image_url"] == previous_row["attributes"]["source_image_url"]
    assert row["attributes"]["web_image_quality"] == previous_row["attributes"]["web_image_quality"]


def _exact_refresh_item(supplier, status, *, approved=True):
    row = _refresh_item(supplier, family=True)
    sha256 = "d" * 64
    row["attributes"]["image_match"] = {
        "status": status,
        "asset_sha256": sha256,
    }
    row["attributes"]["approved_asset"]["approved"] = approved
    row["attributes"]["approved_asset"]["label"] = "Exacta nueva"
    return row


@pytest.mark.parametrize(
    "supplier,status",
    [("labenze", "exact_pdf"), ("requiez", "exact_xlsx"), ("requiez", "exact_web")],
)
def test_approved_exact_official_refresh_always_replaces_prior_curated_visual(
    supplier, status,
):
    previous = _curated_snapshot(supplier, _curated_item(supplier))
    candidate_row = _exact_refresh_item(supplier, status)
    candidate = _curated_snapshot(supplier, candidate_row)

    result = catalog_service._preserve_curated_visuals(previous, candidate)

    assert result["items"][0] == candidate_row


def test_unapproved_exact_status_cannot_displace_prior_curated_visual():
    supplier = "labenze"
    previous = _curated_snapshot(supplier, _curated_item(supplier))
    candidate = _curated_snapshot(
        supplier,
        _exact_refresh_item(supplier, "exact_web", approved=False),
    )

    result = catalog_service._preserve_curated_visuals(previous, candidate)

    assert result["items"][0]["image_kind"] == "generated_reference"
    assert result["items"][0]["attributes"]["approved_asset"]["path"] == f"{'e' * 64}.png"


@pytest.mark.parametrize("supplier", ["labenze", "requiez"])
def test_prior_exact_official_visual_never_degrades_to_generated_reference(supplier):
    previous_row = _curated_item(supplier, image_kind="official")
    candidate_row = _refresh_item(supplier)
    candidate_row["image_kind"] = "generated_reference"
    candidate_row["attributes"]["approved_asset"] = {
        "bucket": "catalog-assets",
        "path": f"{'c' * 64}.png",
        "image_kind": "generated_reference",
        "label": "Imagen de referencia nueva",
        "approved": True,
    }

    result = catalog_service._preserve_curated_visuals(
        _curated_snapshot(supplier, previous_row),
        _curated_snapshot(supplier, candidate_row),
    )

    assert result["items"][0]["image_kind"] == "official"
    assert result["items"][0]["attributes"]["approved_asset"]["path"] == f"{'e' * 64}.png"


@pytest.mark.parametrize(
    "mismatch",
    (
        "supplier", "internal_id", "product_key", "sku", "name", "description",
        "variant", "dimensions", "base_option", "add_on_structure",
    ),
)
def test_curated_visual_requires_exact_identity_and_visual_configuration(mismatch):
    supplier = "labenze"
    previous_row = _curated_item(supplier)
    candidate_supplier = supplier
    candidate_row = _refresh_item(supplier)
    if mismatch == "supplier":
        candidate_supplier = "requiez"
        candidate_row["supplier"] = "requiez"
    elif mismatch == "internal_id":
        candidate_row["internal_id"] = "labenze:chair-2"
    elif mismatch == "product_key":
        candidate_row["product_key"] = "chair-2"
    elif mismatch == "sku":
        candidate_row["sku"] = "LABENZE-CHAIR-2"
    elif mismatch in {"name", "description"}:
        candidate_row[mismatch] += " diferente"
    elif mismatch in {"variant", "dimensions"}:
        candidate_row["attributes"][mismatch] += " diferente"
    elif mismatch == "base_option":
        candidate_row["base_price_options"][0]["name"] = "Gris"
    else:
        candidate_row["add_on_options"][0]["compatible_base_option_ids"] = []
    expected = deepcopy(candidate_row)

    result = catalog_service._preserve_curated_visuals(
        _curated_snapshot(supplier, previous_row),
        _curated_snapshot(candidate_supplier, candidate_row),
    )

    assert result["items"][0] == expected


@pytest.mark.parametrize(
    "unsafe",
    (
        "missing_reference", "reference_hash", "review_flag", "source_url",
        "approved_asset", "product_url",
    ),
)
def test_incomplete_or_unsafe_prior_visual_metadata_blocks_inheritance(unsafe):
    supplier = "requiez"
    previous_row = _curated_item(supplier)
    if unsafe == "missing_reference":
        previous_row["attributes"].pop("image_reference")
    elif unsafe == "reference_hash":
        previous_row["attributes"]["image_reference"]["asset_sha256"] = "d" * 64
    elif unsafe == "review_flag":
        previous_row["attributes"]["image_reference"]["not_cropped"] = False
    elif unsafe == "source_url":
        previous_row["attributes"]["image_reference"]["image_source_url"] = "http://unsafe.test/a.png"
    elif unsafe == "approved_asset":
        previous_row["attributes"]["approved_asset"]["bucket"] = "untrusted-assets"
    else:
        previous_row["product_url"] = ""
    candidate_row = _refresh_item(supplier)
    expected = deepcopy(candidate_row)

    result = catalog_service._preserve_curated_visuals(
        _curated_snapshot(supplier, previous_row),
        _curated_snapshot(supplier, candidate_row),
    )

    assert result["items"][0] == expected


@pytest.mark.parametrize(
    "previous,candidate,field",
    [
        (snapshot(), snapshot(items=[item(price_net="101.000000")]), "price_net"),
        (snapshot(), snapshot(items=[item(code_status="needs_review", sku="")]), "code_status"),
        (snapshot(), snapshot(items=[item(image_url="https://example.test/new.webp")]), "image_url"),
        (snapshot(), snapshot(items=[item(image_kind="generated_reference")]), "image_kind"),
        (snapshot(), snapshot(items=[item(), item(internal_id="alma:2", product_key="2", sku="CHAIR-2")]), "added"),
        (snapshot(items=[item(), item(internal_id="alma:2", product_key="2", sku="CHAIR-2")]), snapshot(), "pending_removal"),
    ],
)
def test_material_diff_never_auto_publishes(previous, candidate, field):
    result = classify_snapshot_diff(previous, candidate)
    assert field in result.changed_fields
    assert result.material_count >= 1
    assert result.auto_publishable is False


def test_diff_rejects_duplicate_ids_even_when_identical():
    duplicate = item()
    with pytest.raises(ValueError, match="snapshot") as caught:
        classify_snapshot_diff(snapshot(), snapshot(items=[item(), duplicate]))
    assert "internal_id" not in str(caught.value)


def test_same_etag_and_identity_finishes_without_download_or_candidate():
    active = source_file()
    repo = FakeRepository(active=(active,), published_snapshot=published())
    graph = FakeGraph((graph_item(),))
    captured = []
    result = call(repo, graph, lambda files: captured.append(files) or snapshot())
    assert result.status == "no_changes"
    assert len(captured[0]) == 1 and captured[0][0].path == ALMA_ONE
    assert captured[0][0].local_path is not None
    assert graph.calls == [("iter_delta", "drive-1", "root-1", DELTA)]
    assert call_names(repo).count("materialize_raw_if_present") == 1
    assert "finish_no_changes" in call_names(repo)
    assert not {"record_source_file", "store_raw_if_absent", "stage_candidate"} & set(call_names(repo))


def test_new_etag_same_sha_records_metadata_without_raw_or_candidate():
    active = source_file(e_tag='"etag-1"', sha256="b" * 64)
    repo = FakeRepository(active=(active,), published_snapshot=published())
    graph = FakeGraph((graph_item(e_tag='"etag-2"'),), sha256="b" * 64)
    result = call(repo, graph, lambda files: snapshot())
    assert result.status == "no_changes"
    assert [name for name in call_names(repo) if name == "record_source_file"] == ["record_source_file"]
    assert "store_raw_if_absent" not in call_names(repo)
    assert len([entry for entry in graph.calls if entry[0] == "download_content"]) == 1


def test_new_hash_stores_once_and_adapter_receives_complete_active_set():
    first = source_file()
    second = source_file(
        file_id=UUID(int=88), drive_item_id="graph-2", path=ALMA_TWO,
        e_tag='"etag-9"', sha256="d" * 64,
    )
    repo = FakeRepository(active=(first, second), published_snapshot=published())
    graph = FakeGraph((graph_item(e_tag='"etag-2"'),), sha256="c" * 64)
    captured = []
    candidate = snapshot(source_hash="c" * 64, items=[item(price_net="101.000000")])
    result = call(repo, graph, lambda files: captured.append(files) or candidate)
    assert result.status == "awaiting_approval"
    assert [row.path for row in captured[0]] == [ALMA_ONE, ALMA_TWO]
    assert all(row.local_path is not None for row in captured[0])
    assert call_names(repo).count("materialize_raw_if_present") == 1
    assert call_names(repo).count("store_raw_if_absent") == 1
    assert call_names(repo).count("record_source_file") == 1
    assert call_names(repo).count("stage_candidate") == 1
    assert "auto_publish_candidate" not in call_names(repo)


def test_stock_only_change_stages_then_requests_sql_auto_publication():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    graph = FakeGraph()
    result = call(repo, graph, lambda files: snapshot(items=[item(stock="9")]))
    assert result.status == "published"
    assert result.auto_publish_attempted is True
    assert call_names(repo)[-2:] == ["stage_candidate", "auto_publish_candidate"]


def test_sql_auto_publication_rejection_keeps_candidate_awaiting_approval():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    def reject(candidate_id):
        repo.calls.append(("auto_publish_candidate", candidate_id))
        raise RuntimeError(f"SECRET {DELTA}")

    repo.auto_publish_candidate = reject
    result = call(repo, FakeGraph(), lambda files: snapshot(items=[item(stock="9")]))
    assert result.status == "awaiting_approval"
    assert result.candidate_id == CANDIDATE_ID
    assert result.auto_publish_attempted is True
    assert "finish_failed" not in call_names(repo)
    assert "SECRET" not in repr(result) and "opaque" not in repr(result)


def test_file_failure_records_one_stable_error_without_leaks():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    graph = FakeGraph((graph_item(e_tag='"etag-2"'),), error=RuntimeError(f"SECRET {DELTA} {ALMA_ONE}"))
    result = call(repo, graph, lambda files: snapshot())
    assert result.status == "failed" and result.error_code == "file_failed"
    assert call_names(repo).count("finish_failed") == 1
    assert "SECRET" not in repr(result) and "opaque" not in repr(result) and "SPEC Guide" not in repr(result)
    assert repo.published_snapshot.payload == snapshot()


def test_incompatible_download_record_fails_before_storage_or_metadata():
    class IncompatibleGraph(FakeGraph):
        def download_content(self, drive_id, graph_row, destination, max_bytes):
            self.calls.append(("download_content", drive_id, graph_row.id, max_bytes))
            return DownloadedFile(Path("SECRET/other.xlsx"), 4, "z" * 64)

    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    result = call(repo, IncompatibleGraph((graph_item(e_tag='"etag-2"'),)), lambda files: snapshot())
    assert result.status == "failed" and result.error_code == "file_failed"
    assert "store_raw_if_absent" not in call_names(repo)
    assert "record_source_file" not in call_names(repo)
    assert "SECRET" not in repr(result)


def test_replayed_tombstone_skips_rpc_and_can_advance_delta():
    repo = FakeRepository(active=(), published_snapshot=published())
    tombstone = graph_item(deleted={"state": "deleted"})
    graph = FakeGraph((tombstone,))
    result = call(repo, graph, lambda files: snapshot())
    assert result.status == "no_changes"
    assert "mark_file_deleted" not in call_names(repo)
    assert "finish_no_changes" in call_names(repo)


def test_deleted_observation_reappearing_is_downloaded_and_recorded_again():
    deleted = source_file(deleted=True)
    repo = FakeRepository(history=(deleted,), published_snapshot=published())
    graph = FakeGraph((graph_item(),), sha256="c" * 64)
    result = call(repo, graph, lambda files: snapshot(source_hash="c" * 64))
    assert result.status == "awaiting_approval"
    assert call_names(repo).count("find_file") == 1
    assert call_names(repo).count("store_raw_if_absent") == 1
    assert call_names(repo).count("record_source_file") == 1


def test_lost_start_stops_before_graph_or_further_repository_mutation():
    repo = FakeRepository(start=False)
    graph = FakeGraph()
    result = call(repo, graph, lambda files: snapshot())
    assert result.status == "lost_claim"
    assert graph.calls == []
    assert result.run_id is None
    assert call_names(repo) == ["get_source", "start_run"]


def test_service_dependency_contract_does_not_require_legacy_run_methods():
    repo = FakeRepository(start=False)
    repo.create_run = None
    repo.claim_run = None
    result = call(repo, FakeGraph(), lambda files: snapshot())
    assert result.status == "lost_claim"
    assert call_names(repo) == ["get_source", "start_run"]


def test_unknown_path_is_ignored_but_duplicate_graph_ids_fail_closed():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    unknown = graph_item(name="attacker.xlsx", parent="OTHER/SECRET")
    result = call(repo, FakeGraph((unknown,)), lambda files: snapshot())
    assert result.status == "no_changes"
    assert "attacker" not in repr(repo.calls)

    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    duplicate = graph_item()
    result = call(repo, FakeGraph((duplicate, duplicate)), lambda files: snapshot())
    assert result.status == "failed" and result.error_code == "graph_invalid"
    assert call_names(repo).count("finish_failed") == 1


@pytest.mark.parametrize("parent", [
    f"/attacker/drives/drive-1/root:/{ROOT_PATH}/SPEC GUIDES 2026/ALMA",
    f"/drives/attacker/root:/{ROOT_PATH}/SPEC GUIDES 2026/ALMA",
    f"/drives/drive-1/root:/ATTACK/{ROOT_PATH}/SPEC GUIDES 2026/ALMA",
])
def test_graph_parent_requires_exact_drive_prefix_and_canonical_root(parent):
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    attacker = graph_item(parent=parent)
    result = call(repo, FakeGraph((attacker,)), lambda files: snapshot())
    assert result.status == "no_changes"
    assert "find_file" not in call_names(repo)


def test_metrics_cover_code_price_image_description_and_availability():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    result = call(repo, FakeGraph(), lambda files: snapshot())
    values = metrics(result)
    assert values["code_verified"] == 1
    assert values["priced_items"] == 1
    assert values["official_images"] == 1
    assert values["described_items"] == 1
    assert values["availability_stocked"] == 1
    assert len(values) <= 24


def test_dry_run_never_creates_or_claims_run_and_only_reads_repository():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    graph = FakeGraph((graph_item(e_tag='"etag-2"'),), sha256="c" * 64)
    result = call(repo, graph, lambda files: snapshot(source_hash="c" * 64), dry_run=True)
    assert result.status == "dry_run" and result.run_id is None
    forbidden = {
        "start_run", "create_run", "claim_run", "store_raw_if_absent", "record_source_file",
        "mark_file_deleted", "stage_candidate",
        "auto_publish_candidate", "finish_no_changes", "finish_failed",
    }
    assert not forbidden & set(call_names(repo))
    assert any(entry[0] == "download_content" for entry in graph.calls)


def _png_asset_bytes(width=1, height=1, color=(12, 34, 56)):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), color).save(stream, format="PNG")
    return stream.getvalue()


def _alma_sidecar(payload, *, bound_brands=("KUN",), status_by_brand=None):
    payload = deepcopy(payload)
    content = _png_asset_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    asset = ImageAsset(content, "image/png", 1, 1, sha256)
    bindings = tuple(
        AlmaAssetBinding(
            row["internal_id"],
            sha256,
            f"{sha256}.png",
            "official",
            (status_by_brand or {}).get(
                row["brand"], "exact_xlsx" if index == 0 else "family_xlsx"
            ),
            (),
        )
        for index, row in enumerate(payload["items"])
        if row["brand"] in bound_brands
    )
    for binding in bindings:
        row = next(item for item in payload["items"] if item["internal_id"] == binding.internal_id)
        row["image_url"] = ""
        row["image_kind"] = "official"
        row["attributes"]["image_match"] = {
            "status": binding.match_status,
            "asset_sha256": sha256,
            "source_references": [],
        }
        row["attributes"]["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": binding.object_name,
            "image_kind": "official",
            "label": "Imagen oficial del XLSX ALMA",
            "approved": True,
        }
    return AlmaSnapshotBuild(payload, {sha256: asset}, bindings)


def _generic_sidecar(
    payload,
    *,
    match_status="exact_pdf",
    asset_data=None,
    asset_width=2,
    asset_height=3,
):
    payload = deepcopy(payload)
    if asset_data is None:
        asset_data = _png_asset_bytes(2, 3)
    row = payload["items"][0]
    sha256 = hashlib.sha256(asset_data).hexdigest()
    references = ({"sha256": "a" * 64, "page": 3, "bbox": [1, 2, 3, 4]},)
    row["image_url"] = ""
    row["image_kind"] = "official"
    row["attributes"]["image_match"] = {
        "status": match_status,
        "asset_sha256": sha256,
        "source_references": list(references),
    }
    row["attributes"]["approved_asset"] = {
        "bucket": "catalog-assets",
        "path": f"{sha256}.png",
        "image_kind": "official",
        "label": "Imagen oficial exacta",
        "approved": True,
    }
    asset = ImageAsset(asset_data, "image/png", asset_width, asset_height, sha256)
    binding = CatalogAssetBinding(
        row["internal_id"], sha256, f"{sha256}.png", "official", match_status, references
    )
    return CatalogSnapshotBuild(payload, {sha256: asset}, (binding,))


def test_generic_exact_pdf_sidecar_uploads_before_stage():
    build = _generic_sidecar(snapshot(source_hash="c" * 64))
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    result = call(repo, FakeGraph(), lambda files: build)

    assert result.status == "awaiting_approval"
    names = call_names(repo)
    assert names.index("store_catalog_asset_if_absent") < names.index("stage_candidate")
    assert metrics(result)["image_exact_pdf"] == 1


@pytest.mark.parametrize(
    "failure",
    ("hash_mismatch", "orphan_asset", "invalid_png", "dimension_mismatch"),
)
def test_generic_asset_sidecar_rejects_untrusted_or_orphan_assets(failure):
    build = _generic_sidecar(
        snapshot(source_hash="c" * 64),
        asset_data=b"not-a-png" if failure == "invalid_png" else None,
    )
    assets = dict(build.assets_by_sha256)
    if failure == "hash_mismatch":
        sha256, asset = next(iter(assets.items()))
        assets[sha256] = ImageAsset(b"tampered", asset.media_type, asset.width, asset.height, sha256)
    else:
        if failure == "orphan_asset":
            orphan_data = b"orphan-png"
            orphan_sha = hashlib.sha256(orphan_data).hexdigest()
            assets[orphan_sha] = ImageAsset(orphan_data, "image/png", 1, 1, orphan_sha)
        elif failure == "dimension_mismatch":
            sha256, asset = next(iter(assets.items()))
            assets[sha256] = ImageAsset(
                asset.data, asset.media_type, asset.width + 1, asset.height, sha256
            )
    invalid = CatalogSnapshotBuild(build.snapshot, assets, build.bindings)
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    result = call(repo, FakeGraph(), lambda files: invalid)

    assert result.status == "failed"
    assert result.error_code == "snapshot_invalid"
    assert "store_catalog_asset_if_absent" not in call_names(repo)
    assert "stage_candidate" not in call_names(repo)


def test_alma_sidecar_accepts_safe_mondecasa_bindings_alongside_complete_kun_coverage():
    mondecasa = item(
        internal_id="alma:mondecasa:chair-1",
        product_key="mondecasa:chair-1",
        sku="MONDECASA-CHAIR-1",
        brand="Mondecasa",
        collection="Vatican",
        name="Vatican dining armchair",
    )
    build = _alma_sidecar(
        snapshot(items=[item(), mondecasa]),
        bound_brands=("KUN", "Mondecasa"),
        status_by_brand={"Mondecasa": "exact_web"},
    )
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    result = call(repo, FakeGraph(), lambda files: build, dry_run=True)

    assert result.status == "dry_run"
    assert metrics(result)["official_images_planned"] == 2
    assert metrics(result)["image_exact_xlsx"] == 1
    assert metrics(result)["image_exact_web"] == 1


def test_alma_sidecar_dry_run_plans_assets_without_any_mutation():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    build = _alma_sidecar(snapshot())

    result = call(repo, FakeGraph(), lambda files: build, dry_run=True)

    assert result.status == "dry_run"
    assert metrics(result)["official_images_planned"] == 1
    assert metrics(result)["unique_assets_planned"] == 1
    assert not {
        "store_catalog_asset_if_absent",
        "stage_candidate",
        "auto_publish_candidate",
        "finish_no_changes",
        "finish_failed",
    } & set(call_names(repo))


def test_alma_sidecar_uploads_unique_assets_before_no_changes():
    second = item(
        internal_id="alma:kun:chair-2",
        product_key="chair-2",
        sku="CHAIR-2",
    )
    candidate = snapshot(items=[item(), second])
    build = _alma_sidecar(candidate)
    repo = FakeRepository(
        active=(source_file(),), published_snapshot=published(build.snapshot)
    )

    result = call(repo, FakeGraph(), lambda files: build)

    assert result.status == "no_changes"
    names = call_names(repo)
    assert names.count("store_catalog_asset_if_absent") == 1
    assert names.index("store_catalog_asset_if_absent") < names.index("finish_no_changes")
    assert metrics(result)["official_images_planned"] == 2
    assert metrics(result)["unique_assets_planned"] == 1
    assert metrics(result)["image_exact_xlsx"] == 1
    assert metrics(result)["image_family_xlsx"] == 1


def test_alma_sidecar_uploads_before_stage_with_asset_metadata_attached():
    build = _alma_sidecar(snapshot(source_hash="c" * 64))
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    result = call(repo, FakeGraph(), lambda files: build)

    assert result.status == "awaiting_approval"
    names = call_names(repo)
    assert names.index("store_catalog_asset_if_absent") < names.index("stage_candidate")
    staged = next(entry[2] for entry in repo.calls if entry[0] == "stage_candidate")
    staged_item = staged["items"][0]
    assert staged_item["attributes"]["approved_asset"]["path"].endswith(".png")
    assert staged_item["attributes"]["image_match"]["status"] == "exact_xlsx"


def test_sunon_sidecar_uploads_before_staging_with_idempotent_metadata():
    payload = snapshot(source_hash="c" * 64)
    row = payload["items"][0]
    row["image_url"] = ""
    row["image_kind"] = "official"
    content = _png_asset_bytes(color=(78, 90, 123))
    sha256 = hashlib.sha256(content).hexdigest()
    row["attributes"]["image_match"] = {
        "status": "exact_xlsx",
        "asset_sha256": sha256,
        "source_references": [],
    }
    row["attributes"]["approved_asset"] = {
        "bucket": "catalog-assets",
        "path": f"{sha256}.png",
        "image_kind": "official",
        "label": "Imagen oficial del XLSX SUNON",
        "approved": True,
    }
    build = SunonSnapshotBuild(
        payload,
        {sha256: ImageAsset(content, "image/png", 1, 1, sha256)},
        (SunonAssetBinding(row["internal_id"], sha256, f"{sha256}.png", "official", "exact_xlsx", ()),),
    )
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())

    result = call(repo, FakeGraph(), lambda files: build)

    assert result.status == "awaiting_approval"
    names = call_names(repo)
    assert names.count("store_catalog_asset_if_absent") == 1
    assert names.index("store_catalog_asset_if_absent") < names.index("stage_candidate")
    staged = next(entry[2] for entry in repo.calls if entry[0] == "stage_candidate")
    assert staged["items"][0]["attributes"]["approved_asset"] == row["attributes"]["approved_asset"]
    assert staged["items"][0]["attributes"]["image_match"] == row["attributes"]["image_match"]
    assert metrics(result)["official_images_planned"] == 1
    assert metrics(result)["image_exact_xlsx"] == 1


def test_empty_delta_restart_materializes_every_active_file_before_adapter():
    rows = (
        source_file(),
        source_file(file_id=UUID(int=88), drive_item_id="graph-2", path=ALMA_TWO,
                    e_tag='"etag-2"', sha256="d" * 64),
    )
    repo = FakeRepository(active=rows, published_snapshot=published())
    captured = []
    result = call(repo, FakeGraph(), lambda files: captured.append(files) or snapshot())
    assert result.status == "no_changes"
    assert [row.path for row in captured[0]] == [ALMA_ONE, ALMA_TWO]
    assert all(row.local_path is not None for row in captured[0])
    assert call_names(repo).count("materialize_raw_if_present") == 2


def test_stage_retries_once_after_commit_then_timeout_and_uses_same_payload():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    attempts = []

    def commit_then_timeout(run_id, candidate, staged_metrics, delta_link):
        attempts.append((run_id, deepcopy(candidate), dict(staged_metrics), delta_link))
        repo.calls.append(("stage_candidate", run_id, candidate, dict(staged_metrics), delta_link))
        if len(attempts) == 1:
            raise TimeoutError("response lost")
        return CANDIDATE_ID

    repo.stage_candidate = commit_then_timeout
    candidate = snapshot(items=[item(price_net="101.000000")])
    result = call(repo, FakeGraph(), lambda files: candidate)
    assert result.status == "awaiting_approval" and result.candidate_id == CANDIDATE_ID
    assert len(attempts) == 2 and attempts[0] == attempts[1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"supplier": "unknown"},
        {"trigger": "retry"},
        {"requested_by": 0},
        {"dry_run": "yes"},
        {"adapters": {}},
    ],
)
def test_inputs_and_dependencies_fail_before_side_effects(kwargs):
    repo = FakeRepository()
    arguments = {
        "supplier": "alma", "trigger": "manual", "requested_by": 7, "dry_run": False,
        "repository": repo, "graph_client": FakeGraph(), "adapters": {"alma": lambda files: snapshot()},
    }
    arguments.update(kwargs)
    with pytest.raises(ValueError, match="Invalid sync request"):
        run_supplier_sync(**arguments)
    assert repo.calls == []


def test_public_result_is_frozen_bounded_and_contains_no_dependency_exception():
    repo = FakeRepository(active=(source_file(),), published_snapshot=published())
    result = call(repo, FakeGraph(error=RuntimeError("SECRET")), lambda files: snapshot())
    assert isinstance(result, SyncResult)
    with pytest.raises(FrozenInstanceError):
        result.status = "changed"
    assert len(result.status) <= 32
    assert result.error_code is None
