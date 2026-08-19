from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

MODULE_SPEC = importlib.util.find_spec("scripts.review_labenze_requiez_image_candidates")
assert MODULE_SPEC is not None, "falta la herramienta de revisión Task 6B"
review = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(review)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _tree_fingerprint(path: Path) -> dict:
    if path.is_file():
        candidates = [path]
        root = path.parent
    else:
        candidates = sorted(
            (entry for entry in path.rglob("*") if entry.is_file()),
            key=lambda entry: entry.as_posix(),
        )
        root = path
    files = [
        {
            "path": entry.relative_to(root).as_posix(),
            "bytes": entry.stat().st_size,
            "sha256": _sha256_file(entry),
        }
        for entry in candidates
    ]
    material = json.dumps(
        files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "files": len(files),
        "bytes": sum(value["bytes"] for value in files),
        "sha256": hashlib.sha256(material).hexdigest(),
    }


def _write_manifest(root: Path, *, nested: bool) -> None:
    hashes = {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(root.rglob("*"), key=lambda entry: entry.as_posix())
        if path.is_file() and path.name != "artifact-hashes.json"
    }
    payload = {"sha256": hashes} if nested else hashes
    (root / "artifact-hashes.json").write_bytes(_json_bytes(payload))


def _logical_research_sha(rows: list[dict]) -> str:
    logical_rows = [
        {key: value for key, value in row.items() if key != "researched_at"}
        for row in rows
    ]
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for row in logical_rows
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_original(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> dict:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    margin_x = max(1, round(size[0] * 0.10))
    margin_y = max(1, round(size[1] * 0.10))
    draw.rectangle(
        (margin_x, margin_y, size[0] - margin_x - 1, size[1] - margin_y - 1),
        fill=color,
    )
    image.save(path, format="PNG")
    digest = _sha256_file(path)
    destination = path.with_name(f"{digest}.png")
    path.replace(destination)
    return {
        "path": destination,
        "sha256": digest,
        "object_name": destination.name,
        "bytes": destination.stat().st_size,
        "dimensions": {"width": size[0], "height": size[1]},
        "mime": "image/png",
    }


def _candidate(
    *,
    source_name: str,
    source_kind: str,
    source_id: str,
    product_url: str,
    image_url: str,
    original: dict,
) -> dict:
    return {
        "approved": False,
        "evidence": {"binding": "exact_sku"},
        "image_source_url": image_url,
        "matched_field": "code",
        "product_url": product_url,
        "query": source_id,
        "source_id": source_id,
        "source_kind": source_kind,
        "source_name": source_name,
        "download": {
            "status": "downloaded",
            "sha256": original["sha256"],
            "object_name": original["object_name"],
            "mime": original["mime"],
            "bytes": original["bytes"],
            "dimensions": original["dimensions"],
            "requested_url": image_url,
            "final_url": image_url,
        },
    }


def _inventory_row(
    supplier: str,
    internal_id: str,
    code: str,
    source_hash: str,
    name: str,
) -> dict:
    return {
        "supplier": supplier,
        "internal_id": internal_id,
        "product_key": internal_id,
        "sku": code,
        "source_code": code,
        "source_hash": source_hash,
        "name": name,
        "description": f"Descripción {name}",
        "collection": "Colección de prueba",
        "source_page": 1,
        "product_url": "https://sharepoint.example/catalogo.pdf#page=1",
        "visual_signature": {
            "sha256": hashlib.sha256(internal_id.encode()).hexdigest(),
            "fields": {
                "model": name,
                "variant": code,
                "base_options": [],
                "add_on_options": [],
            },
        },
    }


def _research_row(row: dict, status: str, reason: str, candidates: list[dict]) -> dict:
    return {
        "schema_version": 1,
        **{
            key: row[key]
            for key in (
                "supplier",
                "internal_id",
                "product_key",
                "sku",
                "source_code",
                "name",
                "description",
                "collection",
                "source_hash",
                "source_page",
                "visual_signature",
            )
        },
        "fallback": {"product_url": row["product_url"], "source_page": 1},
        "query": {"raw": row["sku"], "normalized": row["sku"]},
        "status": status,
        "reason": reason,
        "source_kind": (
            candidates[0]["source_kind"] if len(candidates) == 1 else "multiple"
        ),
        "candidate": candidates[0] if len(candidates) == 1 else None,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "evidence": [candidate["evidence"] for candidate in candidates],
        "researched_at": "2026-08-19T09:12:43Z",
        "review": {
            "approved": False,
            "reviewer": "",
            "reviewed_at": None,
            "checks": {
                "full_product_visible": None,
                "not_cropped": None,
                "configuration_supported": None,
            },
        },
    }


def _rewrite_research(inputs: dict, rows: list[dict]) -> str:
    research_dir = inputs["research_dir"]
    (research_dir / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    summary = json.loads((research_dir / "summary.json").read_text(encoding="utf-8"))
    summary["rows"] = len(rows)
    summary["counts"] = {
        status: sum(row["status"] == status for row in rows)
        for status in ("found_exact", "rejected", "exhausted")
    }
    summary["downloaded_candidates"] = sum(
        candidate.get("download", {}).get("status") == "downloaded"
        for row in rows
        for candidate in row["candidates"]
    )
    logical_sha = _logical_research_sha(rows)
    summary["logical_candidates_sha256"] = logical_sha
    (research_dir / "summary.json").write_bytes(_json_bytes(summary))
    _write_manifest(research_dir, nested=False)
    return logical_sha


def _build_inputs(tmp_path: Path) -> dict:
    labenze_pdf = tmp_path / "Labenze.pdf"
    requiez_pdf = tmp_path / "Requiez.pdf"
    store_path = tmp_path / "db.json"
    assets_dir = tmp_path / "catalog-assets"
    inventory_dir = tmp_path / "inventory"
    research_dir = tmp_path / "research"
    originals_dir = research_dir / "originals"
    labenze_pdf.write_bytes(b"catalogo labenze")
    requiez_pdf.write_bytes(b"catalogo requiez")
    store_path.write_text('{"catalog_published_snapshots":{}}\n', encoding="utf-8")
    assets_dir.mkdir()
    (assets_dir / "asset.txt").write_text("activo", encoding="utf-8")
    inventory_dir.mkdir()
    originals_dir.mkdir(parents=True)

    labenze_hash = _sha256_file(labenze_pdf)
    requiez_hash = _sha256_file(requiez_pdf)
    rows = [
        _inventory_row("labenze", "labenze:3rin-low", "101-LOW", labenze_hash, "ARETA"),
        _inventory_row(
            "labenze", "labenze:collision-a", "155-22700-000", labenze_hash, "AMITHA"
        ),
        _inventory_row(
            "labenze", "labenze:collision-b", "155-22700-000", labenze_hash, "AMITHA BANCO"
        ),
        _inventory_row(
            "requiez", "requiez:rm-9025n-ng", "RM-9025N/NG", requiez_hash, "SKATE"
        ),
        _inventory_row("requiez", "requiez:ri-50", "RI-50", requiez_hash, "RI-50"),
        _inventory_row(
            "requiez", "requiez:re-1063m", "RE-1063M", requiez_hash, "JUN MESA"
        ),
    ]
    inventory_payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    (inventory_dir / "inventory.jsonl").write_text(
        inventory_payload, encoding="utf-8", newline="\n"
    )
    with (inventory_dir / "inventory.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(("supplier", "internal_id"))
        writer.writerows((row["supplier"], row["internal_id"]) for row in rows)
    inventory_summary = {
        "schema_version": 1,
        "counts": {"total": 6, "suppliers": {"labenze": 3, "requiez": 3}},
        "input_hashes": {
            "labenze": labenze_hash,
            "requiez": requiez_hash,
            "store": _sha256_file(store_path),
        },
        "input_paths": {
            "labenze": str(labenze_pdf.resolve()),
            "requiez": str(requiez_pdf.resolve()),
            "store": str(store_path.resolve()),
            "assets": str(assets_dir.resolve()),
        },
    }
    (inventory_dir / "summary.json").write_bytes(_json_bytes(inventory_summary))
    _write_manifest(inventory_dir, nested=True)
    inventory_sha = _sha256_file(inventory_dir / "inventory.jsonl")

    low = _write_original(originals_dir / "low.png", (500, 700), (30, 80, 160))
    high = _write_original(originals_dir / "high.png", (640, 640), (40, 130, 70))
    shared = _write_original(originals_dir / "shared.png", (900, 700), (150, 50, 80))
    lab_candidate = _candidate(
        source_name="3rin.com.mx",
        source_kind="authorized_distributor",
        source_id="101-LOW",
        product_url="https://3rin.com.mx/products/areta",
        image_url="https://cdn.shopify.com/s/files/areta.png",
        original=low,
    )
    rm_candidates = [
        _candidate(
            source_name="api-productos.requiez.com",
            source_kind="manufacturer_official",
            source_id="RM-9025N-NG-a",
            product_url="https://requiez.com/producto/RM-9025N-NG",
            image_url="https://requiez.com/img/products/skate/frente.png",
            original=high,
        ),
        _candidate(
            source_name="api-productos.requiez.com",
            source_kind="manufacturer_official",
            source_id="RM-9025N-NG-b",
            product_url="https://requiez.com/producto/RM-9025N-NG",
            image_url="https://requiez.com/img/products/skate/lateral.png",
            original=shared,
        ),
    ]
    ri_candidate = _candidate(
        source_name="api-productos.requiez.com",
        source_kind="manufacturer_official",
        source_id="RI-50",
        product_url="https://requiez.com/producto/RI-50",
        image_url="https://requiez.com/img/products/ri-50/RI-50.png",
        original=shared,
    )
    research_rows = [
        _research_row(rows[0], "found_exact", "exact_identity_candidates_found", [lab_candidate]),
        _research_row(rows[1], "rejected", "inventory_identity_collision", []),
        _research_row(rows[2], "rejected", "inventory_identity_collision", []),
        _research_row(rows[3], "found_exact", "exact_identity_candidates_found", rm_candidates),
        _research_row(rows[4], "found_exact", "exact_identity_candidates_found", [ri_candidate]),
        _research_row(rows[5], "exhausted", "no_exact_identity", []),
    ]
    research_rows.sort(key=lambda row: (row["supplier"], row["internal_id"]))
    (research_dir / "candidates.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in research_rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    (research_dir / "candidates.csv").write_text("fixture\n", encoding="utf-8")
    logical_sha = _logical_research_sha(research_rows)
    research_summary = {
        "schema_version": 1,
        "status": "passed",
        "inventory_sha256": inventory_sha,
        "rows": 6,
        "counts": {"found_exact": 3, "rejected": 2, "exhausted": 1},
        "downloaded_candidates": 4,
        "logical_candidates_sha256": logical_sha,
        "inputs_before": {
            "inventory": _tree_fingerprint(inventory_dir / "inventory.jsonl"),
            "store": _tree_fingerprint(store_path),
            "assets": _tree_fingerprint(assets_dir),
        },
        "inputs_after": {
            "inventory": _tree_fingerprint(inventory_dir / "inventory.jsonl"),
            "store": _tree_fingerprint(store_path),
            "assets": _tree_fingerprint(assets_dir),
        },
        "inputs_unchanged": True,
        "researched_at": "2026-08-19T09:12:43Z",
    }
    (research_dir / "summary.json").write_bytes(_json_bytes(research_summary))
    _write_manifest(research_dir, nested=False)
    return {
        "inventory_dir": inventory_dir,
        "research_dir": research_dir,
        "labenze_pdf": labenze_pdf,
        "requiez_pdf": requiez_pdf,
        "store_path": store_path,
        "assets_dir": assets_dir,
        "inventory_sha": inventory_sha,
        "research_sha": logical_sha,
        "rows": rows,
        "research_rows": research_rows,
    }


def _small_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review, "EXPECTED_SUPPLIER_COUNTS", {"labenze": 3, "requiez": 3})
    monkeypatch.setattr(
        review,
        "EXPECTED_RESEARCH_COUNTS",
        {"found_exact": 3, "rejected": 2, "exhausted": 1},
    )
    monkeypatch.setattr(review, "EXPECTED_DOWNLOADED_CANDIDATES", 4)
    monkeypatch.setattr(review, "EXPECTED_UNIQUE_ORIGINALS", 3)


def _run(inputs: dict, output_dir: Path, *, reviewed_at: str = "2026-08-19T12:00:00Z"):
    return review.run_review(
        inventory_dir=inputs["inventory_dir"],
        research_dir=inputs["research_dir"],
        labenze_pdf=inputs["labenze_pdf"],
        requiez_pdf=inputs["requiez_pdf"],
        store_path=inputs["store_path"],
        assets_dir=inputs["assets_dir"],
        output_dir=output_dir,
        expected_inventory_sha256=inputs["inventory_sha"],
        expected_research_logical_sha256=inputs["research_sha"],
        reviewed_at=reviewed_at,
    )


def test_candidate_id_is_stable_and_uses_identity_source_urls_and_sha():
    """Romper la composición del ID o usar un índice debe cambiar este resultado."""

    row = {
        "supplier": "requiez",
        "internal_id": "requiez:rm-9025n-ng",
        "product_key": "requiez:rm-9025n-ng",
        "sku": "RM-9025N/NG",
        "source_code": "RM-9025N/NG",
    }
    candidate = {
        "source_name": "api-productos.requiez.com",
        "source_kind": "manufacturer_official",
        "source_id": "9025",
        "product_url": "https://requiez.com/producto/RM-9025N-NG",
        "image_source_url": "https://requiez.com/img/products/skate/Skate_RM-9025_frente.webp",
    }

    assert review.build_candidate_id(row, candidate, "4" * 64) == (
        "18b03cd0e7765dae01e883ebe163e8188569f4afd595972600d5279ee4bc51e3"
    )


def test_review_exports_pending_candidates_search_queue_and_contain_sheets(
    tmp_path, monkeypatch
):
    """Perder candidatos, aprobar solos, recortar tiles o declarar exhausted debe fallar."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    output = tmp_path / "review"

    summary = _run(inputs, output)

    candidates = [
        json.loads(line)
        for line in (output / "candidate-review.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    search_rows = [
        json.loads(line)
        for line in (output / "search-queue.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_id = {row["internal_id"]: row for row in search_rows}
    assert summary["counts"] == {
        "candidates": 4,
        "identities": 6,
        "sheets": 2,
        "technical_gate_passed": 3,
        "unique_originals": 3,
    }
    assert len(candidates) == 4
    assert len({row["candidate_id"] for row in candidates}) == 4
    assert len(search_rows) == 6
    assert all(row["internet_exhausted"] is False for row in search_rows)
    assert by_id["labenze:3rin-low"]["next_action"] == "additional_web_search"
    assert by_id["labenze:collision-a"]["next_action"] == (
        "additional_web_search_collision"
    )
    assert by_id["labenze:collision-b"]["next_action"] == (
        "additional_web_search_collision"
    )
    assert by_id["requiez:re-1063m"]["sku"] == "RE-1063M"
    assert by_id["requiez:re-1063m"]["next_action"] == "additional_web_search"
    assert by_id["requiez:rm-9025n-ng"]["candidate_count"] == 2
    assert by_id["requiez:rm-9025n-ng"]["next_action"] == "human_review_candidates"
    assert by_id["requiez:ri-50"]["next_action"] == "human_review_candidates"
    low = next(row for row in candidates if row["source_name"] == "3rin.com.mx")
    assert low["metrics"]["min_dimension"] == 500
    assert low["automatic_gate"]["passed"] is False
    assert "source_shortest_side_below_512" in low["automatic_gate"]["reasons"]
    ri50 = next(row for row in candidates if row["internal_id"] == "requiez:ri-50")
    assert ri50["source_kind"] == "manufacturer_official"
    assert all(row["review"]["approved"] is False for row in candidates)
    assert all(row["review"]["reviewer"] == "" for row in candidates)
    assert all(row["review"]["reviewed_at"] is None for row in candidates)
    expected_checks = {
        "identity_exact",
        "configuration_supported",
        "full_product_visible",
        "not_cropped",
        "correct_base",
        "correct_arms",
        "correct_seats_table",
        "correct_finish",
        "clean_background",
    }
    assert all(set(row["review"]["checks"]) == expected_checks for row in candidates)
    assert all(
        all(value is None for value in row["review"]["checks"].values())
        for row in candidates
    )
    decisions = json.loads((output / "decisions.json").read_text(encoding="utf-8"))
    assert decisions == {"schema_version": 1, "decisions": []}
    sheet_index = json.loads(
        (output / "contact-sheet-index.json").read_text(encoding="utf-8")
    )
    assert len(sheet_index["tiles"]) == 4
    assert {tile["candidate_id"] for tile in sheet_index["tiles"]} == {
        row["candidate_id"] for row in candidates
    }
    assert all(tile["fit"] == "contain" for tile in sheet_index["tiles"])
    assert all(tile["image_bbox"][2] > tile["image_bbox"][0] for tile in sheet_index["tiles"])
    assert (output / "candidate-review.csv").is_file()
    assert (output / "search-queue.csv").is_file()
    assert (output / "artifact-hashes.json").is_file()


def test_review_is_logically_reproducible_and_does_not_change_inputs(tmp_path, monkeypatch):
    """Introducir timestamps o rutas de salida en el hash lógico debe fallar."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    protected = {
        name: _tree_fingerprint(inputs[name])
        for name in (
            "inventory_dir",
            "research_dir",
            "labenze_pdf",
            "requiez_pdf",
            "store_path",
            "assets_dir",
        )
    }

    first = _run(inputs, tmp_path / "review-a", reviewed_at="2026-08-19T12:00:00Z")
    second = _run(inputs, tmp_path / "review-b", reviewed_at="2026-08-20T12:00:00Z")

    assert first["logical_review_sha256"] == second["logical_review_sha256"]
    assert first["reviewed_at"] != second["reviewed_at"]
    assert first["inputs_unchanged"] is True
    assert second["inputs_unchanged"] is True
    assert protected == {
        name: _tree_fingerprint(inputs[name])
        for name in protected
    }


def test_review_rejects_duplicate_research_identity_even_with_refreshed_hashes(
    tmp_path, monkeypatch
):
    """Repetir una identidad y perder otra no puede ocultarse tras hashes válidos."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    rows[-1] = json.loads(json.dumps(rows[0]))
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="duplicad|identidad"):
        _run(inputs, tmp_path / "review")


@pytest.mark.parametrize("field", ["sku", "product_key", "source_hash"])
def test_review_rejects_identity_divergence_from_inventory(
    tmp_path, monkeypatch, field
):
    """Cambiar identidad o source hash en Task 6A debe detener la revisión."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    rows[0][field] = "divergente"
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="identidad|source_hash"):
        _run(inputs, tmp_path / "review")


def test_review_accepts_empty_sku_when_source_code_and_both_manifests_match(
    tmp_path, monkeypatch
):
    """Exigir SKU no vacío rompe identidades canónicas needs_review de Labenze."""

    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    inventory_path = inputs["inventory_dir"] / "inventory.jsonl"
    inventory_rows = [
        json.loads(line)
        for line in inventory_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    inventory_rows[0]["sku"] = ""
    inventory_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in inventory_rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    _write_manifest(inputs["inventory_dir"], nested=True)
    inputs["inventory_sha"] = _sha256_file(inventory_path)
    research_rows = inputs["research_rows"]
    matching = next(
        row for row in research_rows if row["internal_id"] == inventory_rows[0]["internal_id"]
    )
    matching["sku"] = ""
    summary_path = inputs["research_dir"] / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["inventory_sha256"] = inputs["inventory_sha"]
    summary_path.write_bytes(_json_bytes(summary))
    inputs["research_sha"] = _rewrite_research(inputs, research_rows)

    result = _run(inputs, tmp_path / "review")

    assert result["status"] == "passed"


def test_review_rejects_manifest_or_logical_hash_mismatch(tmp_path, monkeypatch):
    """Alterar un artefacto o el hash lógico debe fallar antes de exportar revisión."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    summary_path = inputs["research_dir"] / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["logical_candidates_sha256"] = "0" * 64
    summary_path.write_bytes(_json_bytes(summary))
    _write_manifest(inputs["research_dir"], nested=False)

    with pytest.raises(ValueError, match="lógico|logico"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_download_count_divergent_in_research_summary(
    tmp_path, monkeypatch
):
    """Un summary que no reconcilia los candidatos descargados debe bloquearse."""

    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    summary_path = inputs["research_dir"] / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["downloaded_candidates"] = 999
    summary_path.write_bytes(_json_bytes(summary))
    _write_manifest(inputs["research_dir"], nested=False)

    with pytest.raises(ValueError, match="descargad|download"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_candidate_preapproved_by_untrusted_research(
    tmp_path, monkeypatch
):
    """Datos fuente no confiables nunca pueden inyectar una aprobación."""

    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    found = next(row for row in rows if row["status"] == "found_exact")
    found["candidates"][0]["approved"] = True
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="aprob"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_original_traversal_before_opening_it(tmp_path, monkeypatch):
    """Un object_name con traversal nunca puede escapar de originals/."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    found = next(row for row in rows if row["status"] == "found_exact")
    found["candidates"][0]["download"]["object_name"] = "../escape.png"
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="path|ruta|object_name|traversal"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_undeclared_original_even_when_manifest_declares_it(
    tmp_path, monkeypatch
):
    """Un original sin candidato es un artefacto adicional y debe bloquearse."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    extra = _write_original(
        inputs["research_dir"] / "originals" / "extra.png",
        (640, 640),
        (10, 20, 30),
    )
    assert extra["path"].is_file()
    _write_manifest(inputs["research_dir"], nested=False)

    with pytest.raises(ValueError, match="adicional|declarad"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_existing_output_without_overwriting(tmp_path, monkeypatch):
    """Una salida existente conserva sus bytes y no se reutiliza."""

    assert hasattr(review, "run_review"), "falta el orquestador Task 6B"
    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    output = tmp_path / "review"
    output.mkdir()
    sentinel = output / "sentinel.txt"
    sentinel.write_text("preservar", encoding="utf-8")

    with pytest.raises(ValueError, match="existe"):
        _run(inputs, output)

    assert sentinel.read_text(encoding="utf-8") == "preservar"
    assert sorted(path.name for path in output.iterdir()) == ["sentinel.txt"]


def test_original_validator_rejects_symlink_or_hardlink_alias(tmp_path):
    """Un alias de filesystem no se considera original regular independiente."""

    assert hasattr(review, "inspect_original"), "falta el validador de originales"
    original = _write_original(tmp_path / "source.png", (640, 640), (20, 30, 40))
    alias = tmp_path / f"{'a' * 64}.png"
    try:
        os.link(original["path"], alias)
    except OSError:
        pytest.skip("el filesystem no permite hardlinks")

    with pytest.raises(ValueError, match="alias|enlace|regular"):
        review.inspect_original(
            alias,
            expected_sha256=_sha256_file(alias),
            expected_bytes=alias.stat().st_size,
            expected_mime="image/png",
            expected_dimensions={"width": 640, "height": 640},
        )


def test_contact_sheet_wraps_complete_labels_without_clipping(tmp_path):
    """Abreviar con elipsis u ocultar texto fuera del tile debe fallar."""

    originals = tmp_path / "originals"
    originals.mkdir()
    original = _write_original(originals / "candidate.png", (640, 640), (40, 60, 80))
    configuration = (
        "ADA RE-810 Tapiz personalizable Consulta muestrario "
        "TELA GRADO A APOLO MILAN Y FRED"
    )
    candidate = {
        "index": 1,
        "candidate_id": "b" * 64,
        "candidate_id_short": "b" * 12,
        "supplier": "requiez",
        "sku": "RE-810",
        "configuration": configuration,
        "source_name": "api-productos.requiez.com",
        "original": {
            "object_name": original["object_name"],
            "dimensions": original["dimensions"],
        },
        "automatic_gate": {"passed": True},
    }
    output = tmp_path / "review"
    output.mkdir()

    _inventory, index = review._render_contact_sheets(
        output, [candidate], originals
    )

    tile = index["tiles"][0]
    assert "labels" in tile, "el índice debe permitir auditar cada label renderizado"
    texts = [label["text"] for label in tile["labels"]]
    assert "…" not in "".join(texts)
    config_text = " ".join(
        text.removeprefix("Config: ") for text in texts if text.startswith("Config: ") or text in configuration
    )
    assert config_text == configuration
    tile_left, tile_top, tile_right, tile_bottom = tile["bbox"]
    for label in tile["labels"]:
        left, top, right, bottom = label["bbox"]
        assert tile_left <= left < right <= tile_right
        assert tile_top <= top < bottom <= tile_bottom


def test_csv_neutralizes_formula_prefixes_in_all_string_cells_only(tmp_path):
    """Excel nunca debe interpretar nombre, URL o descripción como fórmula."""

    row = {
        "name": "  =HYPERLINK(\"https://evil.example\")",
        "product_url": "\t+WEBSERVICE(\"https://evil.example\")",
        "description": "\r@SUM(1,1)",
        "source_code": "-2+3",
        "nested": {
            "formula": "=1+1",
            "tab": "\t@payload",
        },
        "safe": "https://requiez.com/producto/RI-50",
    }
    csv_path = tmp_path / "review.csv"
    jsonl_path = tmp_path / "review.jsonl"

    review._write_csv(csv_path, [row])
    review._write_jsonl(jsonl_path, [row])

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        exported = next(csv.DictReader(handle))
    assert exported["name"].startswith("'")
    assert exported["product_url"].startswith("'")
    assert exported["description"].startswith("'")
    assert exported["source_code"].startswith("'")
    assert exported["safe"] == row["safe"]
    assert json.loads(exported["nested"]) == row["nested"]
    for value in exported.values():
        if value.startswith("'"):
            continue
        assert value.lstrip(" \t\r\n")[:1] not in {"=", "+", "-", "@"}
        assert value[:1] not in {"\t", "\r"}
    assert json.loads(jsonl_path.read_text(encoding="utf-8")) == row


@pytest.mark.parametrize(
    ("target", "bad_url"),
    [
        ("product", "file:///tmp/producto"),
        ("product", "javascript:alert(1)"),
        ("product", "https://requiez.com.evil.example/producto/RM-9025N-NG"),
        ("product", "https://user:pass@requiez.com/producto/RM-9025N-NG"),
        ("product", "\thttps://requiez.com/producto/RM-9025N-NG"),
        (
            "product",
            "https://requiez.com/producto/RM-9025N-NG?variant=123%0AInjected",
        ),
        ("image", "https://evil.example/img/products/skate/frente.png"),
        ("image", "https://requiez.com/producto/not-an-image.png"),
        ("final", "https://evil.example/img/products/skate/frente.png"),
    ],
)
def test_review_rejects_unsafe_or_off_policy_candidate_urls(
    tmp_path, monkeypatch, target, bad_url
):
    """Esquema, userinfo, controles, host engañoso o ruta ajena deben bloquearse."""

    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    found = next(row for row in rows if row["internal_id"] == "requiez:rm-9025n-ng")
    candidate = found["candidates"][0]
    if target == "product":
        candidate["product_url"] = bad_url
    elif target == "image":
        candidate["image_source_url"] = bad_url
        candidate["download"]["requested_url"] = bad_url
        candidate["download"]["final_url"] = bad_url
    else:
        candidate["download"]["final_url"] = bad_url
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="URL|HTTPS|host|ruta|política|control"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_source_kind_incoherent_with_source_name(tmp_path, monkeypatch):
    """Una fuente oficial no puede reclasificarse como distribuidor."""

    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    found = next(row for row in rows if row["internal_id"] == "requiez:rm-9025n-ng")
    found["candidates"][0]["source_kind"] = "authorized_distributor"
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="source_kind|fuente"):
        _run(inputs, tmp_path / "review")


def test_review_rejects_unproven_allowed_redirect(tmp_path, monkeypatch):
    """Incluso un redirect al host permitido requiere evidencia exacta de Task 6A."""

    _small_contract(monkeypatch)
    inputs = _build_inputs(tmp_path)
    rows = inputs["research_rows"]
    found = next(row for row in rows if row["internal_id"] == "requiez:rm-9025n-ng")
    found["candidates"][0]["download"]["final_url"] = (
        "https://requiez.com/img/products/skate/redirected.png"
    )
    inputs["research_sha"] = _rewrite_research(inputs, rows)

    with pytest.raises(ValueError, match="redirect|evidencia|cache"):
        _run(inputs, tmp_path / "review")


def test_script_direct_cli_can_load_task6a_url_policy():
    """Ejecutar `python scripts/...py` debe poder cargar la policy local sin red."""

    script = Path("scripts/review_labenze_requiez_image_candidates.py").resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--research-dir" in result.stdout
