import hashlib
from copy import deepcopy

from mobiliti_saas.api import index as api_index
from scripts import apply_canonical_review_overlay as overlay_script


build_canonical_review_overlay = overlay_script.build_canonical_review_overlay


def _item(internal_id: str) -> dict:
    return {
        "internal_id": internal_id,
        "image_url": "https://old.example.test/image.png",
        "image_kind": "official",
        "attributes": {
            "commercial_field": "preservar",
            "approved_asset": {
                "bucket": "catalog-assets",
                "path": f"{'a' * 64}.png",
                "image_kind": "official",
                "approved": True,
            },
        },
    }


def _row(
    internal_id: str,
    *,
    classification: str,
    relative_path: str = "",
    digest: str = "",
    migration_from: str | None = None,
    declared_hash: bool = True,
) -> dict:
    exists = bool(relative_path)
    return {
        "internal_id": internal_id,
        "supplier": "labenze",
        "identity": {"name": internal_id},
        "migration": {"from_internal_id": migration_from} if migration_from else None,
        "classification": classification,
        "has_valid_image": classification in {
            "valid_exact_reviewed",
            "candidate_qa_pass_unapproved",
        },
        "asset_or_candidate": {
            "path": relative_path,
            "declared_sha256": digest if declared_hash else None,
            "actual_sha256": digest,
            "exists": True,
            "hash_match": True if declared_hash else None,
        } if exists else None,
        "selected": False,
        "approved": False,
        "promoted": False,
    }


def test_builds_visual_only_r10_overlay_and_maps_migrated_identity(tmp_path):
    exact_bytes = b"exact-reviewed-image"
    candidate_bytes = b"pending-candidate-image"
    exact_sha = hashlib.sha256(exact_bytes).hexdigest()
    candidate_sha = hashlib.sha256(candidate_bytes).hexdigest()
    exact_source = tmp_path / "visual" / "exact.png"
    candidate_source = tmp_path / "visual" / "candidate.webp"
    exact_source.parent.mkdir()
    exact_source.write_bytes(exact_bytes)
    candidate_source.write_bytes(candidate_bytes)

    active = {
        "users": [{"id": 7, "email": "preservar@example.test"}],
        "catalog_published_snapshots": {
            "labenze": {
                "id": "snapshot-estable",
                "payload": {
                    "items": [
                        _item("labenze:exact"),
                        _item("labenze:legacy"),
                        _item("labenze:no-asset"),
                    ]
                },
            }
        },
    }
    canonical = {
        "status": "NO_GO_PENDING_REFRESH",
        "rows": [
            _row(
                "labenze:exact",
                classification="valid_exact_reviewed",
                relative_path="visual/exact.png",
                digest=exact_sha,
            ),
            _row(
                "labenze:migrated",
                classification="candidate_pending_qa",
                relative_path="visual/candidate.webp",
                digest=candidate_sha,
                migration_from="labenze:legacy",
                declared_hash=False,
            ),
            _row(
                "labenze:no-asset",
                classification="blocked_no_deterministic_asset",
            ),
        ],
    }
    before = deepcopy(active)

    updated, assets, summary = build_canonical_review_overlay(
        active,
        canonical,
        workspace=tmp_path,
        bundle="R10/v8",
        suppliers=("labenze",),
    )

    assert active == before
    assert updated["users"] == before["users"]
    assert updated["catalog_published_snapshots"]["labenze"]["id"] == "snapshot-estable"
    items = {
        item["internal_id"]: item
        for item in updated["catalog_published_snapshots"]["labenze"]["payload"]["items"]
    }
    exact = items["labenze:exact"]
    migrated = items["labenze:legacy"]
    absent = items["labenze:no-asset"]

    assert exact["attributes"]["commercial_field"] == "preservar"
    assert exact["attributes"]["approved_asset"] == before["catalog_published_snapshots"]["labenze"]["payload"]["items"][0]["attributes"]["approved_asset"]
    assert exact["image_url"] == ""
    assert exact["image_kind"] == "official"
    assert exact["attributes"]["canonical_review_overlay"]["asset"] == {
        "bucket": "catalog-assets",
        "path": f"{exact_sha}.png",
        "image_kind": "official",
        "review_only": True,
    }
    assert migrated["image_kind"] == "generated_reference"
    assert migrated["attributes"]["canonical_review_overlay"]["source_internal_id"] == "labenze:migrated"
    assert migrated["attributes"]["canonical_review_overlay"]["approved"] is False
    assert migrated["attributes"]["canonical_review_overlay"]["promoted"] is False
    assert absent["image_kind"] == "placeholder"
    assert absent["attributes"]["canonical_review_overlay"]["asset"] is None
    assert set(assets) == {f"{exact_sha}.png", f"{candidate_sha}.webp"}
    assert assets[f"{exact_sha}.png"] == exact_source.resolve()
    assert summary == {
        "rows": 3,
        "with_asset": 2,
        "without_asset": 1,
        "official": 1,
        "generated_reference": 1,
        "placeholder": 1,
        "unique_assets": 2,
        "asset_bytes": len(exact_bytes) + len(candidate_bytes),
        "by_classification": {
            "blocked_no_deterministic_asset": 1,
            "candidate_pending_qa": 1,
            "valid_exact_reviewed": 1,
        },
        "by_supplier": {"labenze": 3},
    }


def test_dev_hydration_prefers_review_overlay_without_claiming_approval(monkeypatch):
    reviewed_name = f"{'b' * 64}.webp"
    old_name = f"{'a' * 64}.png"
    payload = {
        "items": [
            {
                "image_url": "",
                "image_kind": "placeholder",
                "attributes": {
                    "approved_asset": {
                        "bucket": "catalog-assets",
                        "path": old_name,
                        "image_kind": "official",
                        "approved": True,
                    },
                    "canonical_review_overlay": {
                        "bundle": "R10/v8",
                        "classification": "candidate_pending_qa",
                        "selected": False,
                        "approved": False,
                        "promoted": False,
                        "asset": {
                            "bucket": "catalog-assets",
                            "path": reviewed_name,
                            "image_kind": "generated_reference",
                            "review_only": True,
                        },
                    },
                },
            },
            {
                "image_url": "https://old.example.test/image.png",
                "image_kind": "official",
                "attributes": {
                    "approved_asset": {
                        "bucket": "catalog-assets",
                        "path": old_name,
                        "image_kind": "official",
                        "approved": True,
                    },
                    "canonical_review_overlay": {
                        "bundle": "R10/v8",
                        "classification": "blocked_no_deterministic_asset",
                        "selected": False,
                        "approved": False,
                        "promoted": False,
                        "asset": None,
                    },
                },
            },
        ]
    }
    monkeypatch.setattr(api_index, "DEV_MODE", True)
    monkeypatch.setattr(api_index, "DEV_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

    hydrated = api_index._hydrate_catalog_asset_urls(payload)

    assert hydrated["items"][0]["image_url"] == f"http://127.0.0.1:8000/dev/catalog-assets/{reviewed_name}"
    assert hydrated["items"][0]["image_kind"] == "generated_reference"
    assert hydrated["items"][1]["image_url"] == ""
    assert hydrated["items"][1]["image_kind"] == "placeholder"
    for item in hydrated["items"]:
        assert "approved_asset" not in item["attributes"]
        assert "canonical_review_overlay" not in item["attributes"]


def test_explicit_approval_materializes_overlay_assets_without_changing_commercial_fields():
    active = {
        "users": [{"id": 7}],
        "catalog_published_snapshots": {
            "labenze": {
                "payload": {
                    "items": [
                        {
                            "internal_id": "labenze:approved",
                            "image_url": "",
                            "image_kind": "generated_reference",
                            "price_net": "123.450000",
                            "attributes": {
                                "commercial_field": "preservar",
                                "canonical_review_overlay": {
                                    "bundle": "R10/v8+generated-references-20260822",
                                    "classification": "candidate_qa_pass_unapproved",
                                    "selected": False,
                                    "approved": False,
                                    "promoted": False,
                                    "asset": {
                                        "bucket": "catalog-assets",
                                        "path": f"{'b' * 64}.png",
                                        "image_kind": "generated_reference",
                                        "review_only": True,
                                    },
                                },
                            },
                        }
                    ]
                }
            }
        },
    }
    before = deepcopy(active)

    approved, summary = overlay_script.build_approved_catalog_overlay(
        active,
        suppliers=("labenze",),
        approved_by="usuario",
        approval_note="Cambios aprobados para produccion",
        approved_at="2026-08-24T18:00:00Z",
    )

    assert active == before
    assert approved["users"] == before["users"]
    item = approved["catalog_published_snapshots"]["labenze"]["payload"]["items"][0]
    assert item["price_net"] == "123.450000"
    assert item["attributes"]["commercial_field"] == "preservar"
    assert "canonical_review_overlay" not in item["attributes"]
    assert item["attributes"]["approved_asset"] == {
        "bucket": "catalog-assets",
        "path": f"{'b' * 64}.png",
        "image_kind": "generated_reference",
        "label": "Imagen de referencia aprobada",
        "approved": True,
        "approved_by": "usuario",
        "approved_at": "2026-08-24T18:00:00Z",
        "approval_note": "Cambios aprobados para produccion",
        "source_bundle": "R10/v8+generated-references-20260822",
        "source_classification": "candidate_qa_pass_unapproved",
    }
    assert item["image_url"] == ""
    assert item["image_kind"] == "generated_reference"
    assert summary == {
        "items": 1,
        "official": 0,
        "generated_reference": 1,
        "by_supplier": {"labenze": 1},
    }
