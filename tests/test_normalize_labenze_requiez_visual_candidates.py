from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import shutil
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/normalize_labenze_requiez_visual_candidates.py"
BUILDER = ROOT / "scripts/build_verified_catalog_images.py"


def _load(path: Path, name: str):
    if not path.is_file():
        raise AssertionError(f"falta script: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def normalizer():
    return _load(SCRIPT, "normalize_visual_candidates")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "review.json").write_text('{"decision":"approved_for_normalization"}\n', encoding="utf-8")
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_image(path: Path, size: tuple[int, int], bbox: tuple[int, int, int, int]) -> None:
    image = Image.new("RGB", size, "white")
    left, top, right, bottom = bbox
    for y in range(top, bottom):
        for x in range(left, right):
            image.putpixel((x, y), (20, 80, 140))
    image.save(path, format="PNG", optimize=False)


def _save_formatted_image(
    path: Path,
    image_format: str,
    size: tuple[int, int] = (600, 850),
    bbox: tuple[int, int, int, int] = (50, 80, 550, 770),
) -> None:
    image = Image.new("RGB", size, "white")
    left, top, right, bottom = bbox
    for y in range(top, bottom):
        for x in range(left, right):
            image.putpixel((x, y), (20, 80, 140))
    options = {"lossless": True} if image_format == "WEBP" else {"quality": 95}
    image.save(path, format=image_format, **options)


def _entry(root: Path, source: Path, *, action: str = "validate_exact", **updates: object) -> dict:
    dimensions = updates.get("source_dimensions")
    if dimensions is None:
        with Image.open(source) as image:
            dimensions = {"width": image.width, "height": image.height}
    value = {
        "internal_id": "labenze:test-001",
        "supplier": "labenze",
        "sku": "TEST-001",
        "product_key": "test-001",
        "source_path": source.relative_to(root).as_posix(),
        "source_sha256": _sha(source),
        "source_dimensions": dimensions,
        "source_review_path": "review.json",
        "source_review_sha256": _sha(root / "review.json"),
        "action": action,
    }
    value.update(updates)
    return value


def _write_plan(root: Path, entries: list[dict], name: str = "plan.json") -> Path:
    path = root / name
    path.write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _receipt(root: Path, output: Path, manifest: dict, index: int = 0) -> dict:
    path = output / manifest["entries"][index]["receipt_path"]
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_hashes(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): _sha(item)
        for item in sorted(path.rglob("*"), key=lambda value: value.as_posix())
        if item.is_file()
    }


def test_validate_exact_is_content_addressed_deterministic_and_matches_builder_gate(
    normalizer, workspace: Path
):
    source = workspace / "exact.png"
    _save_image(source, (1024, 1024), (100, 200, 900, 800))
    source_before = source.read_bytes()
    review_before = (workspace / "review.json").read_bytes()
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output_a = workspace / "output-a"
    output_b = workspace / "output-b"

    manifest_a = normalizer.normalize_plan(plan, output_a, workspace)
    manifest_b = normalizer.normalize_plan(plan, output_b, workspace)
    receipt = _receipt(workspace, output_a, manifest_a)

    assert manifest_a["status"] == "PASS"
    assert manifest_a["summary"] == {"failed": 0, "passed": 1, "total": 1}
    assert _tree_hashes(output_a) == _tree_hashes(output_b)
    assert receipt["status"] == "PASS"
    assert receipt["action"] == "validate_exact"
    assert receipt["output"]["sha256"] == _sha(source)
    assert receipt["output"]["path"] == f"assets/{_sha(source)}.png"
    assert (output_a / receipt["output"]["path"]).read_bytes() == source_before
    assert receipt["geometry"]["before"] == receipt["geometry"]["after"]
    assert receipt["geometry"]["aspect_deformation"] == pytest.approx(0.0)
    assert receipt["transformation"] == {
        "canvas_padding": False,
        "crop": False,
        "reframe": False,
        "resize": False,
        "scale": 1.0,
    }
    assert receipt["approved"] is False
    assert receipt["promotion"]["allowed"] is False
    assert all(value is False for value in receipt["mutations"].values())
    assert source.read_bytes() == source_before
    assert (workspace / "review.json").read_bytes() == review_before

    builder = _load(BUILDER, "verified_builder")
    left, top, right, bottom, occupancy = builder._foreground_bbox(source, "test")
    ours = normalizer.inspect_foreground(Image.open(source))
    assert ours["bbox"] == {
        "left": left,
        "top": top,
        "width": right - left + 1,
        "height": bottom - top + 1,
    }
    assert ours["occupancy"] == pytest.approx(occupancy)


@pytest.mark.skipif(os.name != "nt", reason="regresión específica de rutas Win32")
def test_windows_long_output_creates_artifact_directories_and_publishes_pass(
    normalizer, workspace: Path
):
    source = workspace / "exact-long-path.png"
    _save_image(source, (1024, 1024), (100, 200, 900, 800))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    stage_name = f".normalized.staging-{_sha(plan)[:12]}"
    filler_length = 186 - len(str(workspace)) - len(stage_name) - 2
    assert 0 < filler_length < 240
    output_parent = workspace / ("x" * filler_length)
    output_parent.mkdir()
    output = output_parent / "normalized"
    expected_asset = output_parent / stage_name / "assets" / f"{_sha(source)}.png"
    assert len(str(expected_asset)) == 262

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert manifest["status"] == "PASS"
    assert (output / "assets" / f"{_sha(source)}.png").is_file()
    assert (output / "manifest.json").is_file()


def test_padding_uses_smallest_feasible_square_and_copies_pixels_without_resize(
    normalizer, workspace: Path
):
    source = workspace / "needs-padding.png"
    _save_image(source, (1024, 1024), (30, 100, 994, 924))
    plan = _write_plan(
        workspace,
        [_entry(workspace, source, action="centered_canvas_padding_no_scale")],
    )
    output = workspace / "output"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipt = _receipt(workspace, output, manifest)
    final_path = output / receipt["output"]["path"]

    assert receipt["status"] == "PASS"
    assert receipt["geometry"]["after"]["canvas"] == {"width": 1048, "height": 1048}
    assert receipt["geometry"]["after"]["minimum_margin"] >= 0.04
    assert max(receipt["geometry"]["after"]["bbox_axis_ratios"].values()) <= 0.92
    assert 0.12 <= receipt["geometry"]["after"]["occupancy"] <= 0.80
    assert receipt["geometry"]["aspect_deformation"] == pytest.approx(0.0)
    assert receipt["transformation"]["paste_offset"] == {"x": 12, "y": 12}
    assert receipt["transformation"]["resize"] is False
    assert receipt["pixel_identity"]["preserved"] is True
    with Image.open(source) as before, Image.open(final_path) as after:
        crop = after.convert("RGB").crop((12, 12, 1036, 1036))
        assert crop.tobytes() == before.convert("RGB").tobytes()


@pytest.mark.parametrize(
    ("suffix", "image_format"),
    [(".webp", "WEBP"), (".jpg", "JPEG")],
)
def test_validate_exact_non_square_source_is_png_canvas_normalized_without_scaling(
    normalizer, workspace: Path, suffix: str, image_format: str
):
    source = workspace / f"exact-non-square{suffix}"
    _save_formatted_image(source, image_format)
    source_before = source.read_bytes()
    plan = _write_plan(workspace, [_entry(workspace, source, action="validate_exact")])
    output = workspace / f"output-{image_format.casefold()}"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipt = _receipt(workspace, output, manifest)
    final_path = output / receipt["output"]["path"]

    assert receipt["status"] == "PASS"
    assert receipt["action"] == "validate_exact"
    assert receipt["output"]["mime"] == "image/png"
    assert receipt["geometry"]["after"]["canvas"] == {"width": 1024, "height": 1024}
    assert receipt["transformation"]["canvas_padding"] is True
    assert receipt["transformation"]["resize"] is False
    assert receipt["transformation"]["scale"] == pytest.approx(1.0)
    offset = receipt["transformation"]["paste_offset"]
    with Image.open(source) as before, Image.open(final_path) as after:
        crop = after.convert("RGB").crop(
            (offset["x"], offset["y"], offset["x"] + before.width, offset["y"] + before.height)
        )
        assert crop.tobytes() == before.convert("RGB").tobytes()
    assert source.read_bytes() == source_before


def test_transparent_png_with_opaque_white_product_preserves_rgba_and_builder_gate(
    normalizer, workspace: Path
):
    source = workspace / "white-product-transparent.png"
    image = Image.new("RGBA", (600, 850), (255, 255, 255, 0))
    for y in range(80, 770):
        for x in range(50, 550):
            image.putpixel((x, y), (255, 255, 255, 255))
    image.save(source, format="PNG")
    plan = _write_plan(
        workspace,
        [_entry(workspace, source, action="centered_canvas_padding_no_scale")],
    )
    output = workspace / "output-transparent"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipt = _receipt(workspace, output, manifest)
    final_path = output / receipt["output"]["path"]
    offset = receipt["transformation"]["paste_offset"]

    assert receipt["status"] == "PASS"
    assert receipt["geometry"]["after"]["transparent_canvas"] is True
    assert receipt["pixel_identity"]["mode"] == "RGBA"
    assert receipt["pixel_identity"]["preserved"] is True
    with Image.open(source) as before, Image.open(final_path) as after:
        assert after.mode == "RGBA"
        assert after.getpixel((0, 0))[3] == 0
        crop = after.convert("RGBA").crop(
            (offset["x"], offset["y"], offset["x"] + before.width, offset["y"] + before.height)
        )
        assert crop.tobytes() == before.convert("RGBA").tobytes()

    builder = _load(BUILDER, "verified_builder_transparent")
    left, top, right, bottom, occupancy = builder._foreground_bbox(final_path, "transparent")
    assert receipt["geometry"]["after"]["bbox"] == {
        "left": left,
        "top": top,
        "width": right - left + 1,
        "height": bottom - top + 1,
    }
    assert receipt["geometry"]["after"]["occupancy"] == pytest.approx(occupancy)


@pytest.mark.parametrize(
    ("size", "bbox", "expected_code"),
    [
        ((511, 700), (50, 50, 450, 650), "SOURCE_SHORTEST_SIDE_BELOW_512"),
        ((1024, 1024), (462, 462, 562, 562), "NO_FEASIBLE_CANVAS"),
        ((1024, 1024), (0, 100, 700, 900), "SOURCE_FOREGROUND_TOUCHES_BORDER"),
    ],
)
def test_lowres_low_occupancy_and_cropped_sources_fail_closed_with_receipt(
    normalizer, workspace: Path, size, bbox, expected_code
):
    source = workspace / f"bad-{expected_code}.png"
    _save_image(source, size, bbox)
    plan = _write_plan(
        workspace,
        [_entry(workspace, source, action="centered_canvas_padding_no_scale")],
    )
    output = workspace / f"out-{expected_code}"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipt = _receipt(workspace, output, manifest)

    assert manifest["status"] == "FAILED"
    assert receipt["status"] == "FAILED"
    assert receipt["failure"]["code"] == expected_code
    assert receipt["approved"] is False
    assert receipt["promotion"]["allowed"] is False
    assert not (output / "assets").exists() or not list((output / "assets").iterdir())


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"source_sha256": "0" * 64}, "SOURCE_SHA256_MISMATCH"),
        ({"source_dimensions": {"width": 1023, "height": 1024}}, "SOURCE_DIMENSIONS_MISMATCH"),
        ({"source_review_sha256": "f" * 64}, "SOURCE_REVIEW_SHA256_MISMATCH"),
    ],
)
def test_hash_dimensions_and_review_binding_mismatches_fail_closed(
    normalizer, workspace: Path, change, expected_code
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source, **change)])
    output = workspace / f"out-{expected_code}"

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert _receipt(workspace, output, manifest)["failure"]["code"] == expected_code


@pytest.mark.parametrize("unsafe", ["../outside.png", "/absolute.png", "folder\\image.png"])
def test_unsafe_source_paths_reject_the_whole_plan_without_output(
    normalizer, workspace: Path, unsafe: str
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    entry = _entry(workspace, source)
    entry["source_path"] = unsafe
    plan = _write_plan(workspace, [entry])
    output = workspace / "output"

    with pytest.raises(normalizer.PlanError, match="ruta insegura"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()


def test_final_plan_alias_is_checked_lexically_before_resolve(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    target = _write_plan(workspace, [_entry(workspace, source)], "target-plan.json")
    alias = workspace / "alias-plan.json"
    alias.write_bytes(target.read_bytes())
    original_resolve = Path.resolve
    original_reparse_check = normalizer._has_reparse_flag

    def simulated_resolve(path: Path, strict: bool = False) -> Path:
        if path.absolute() == alias.absolute():
            return target
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", simulated_resolve)
    monkeypatch.setattr(
        normalizer,
        "_has_reparse_flag",
        lambda path: Path(path).absolute() == alias.absolute() or original_reparse_check(path),
    )
    output = workspace / "output-plan-alias"

    with pytest.raises(normalizer.PlanError, match="enlace|reparse|alias"):
        normalizer.normalize_plan(alias, output, workspace)
    assert not output.exists()


def test_workspace_root_alias_is_checked_lexically_before_resolve(
    normalizer, workspace: Path, tmp_path: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    alias = tmp_path / "workspace-alias"
    alias.mkdir()
    original_resolve = Path.resolve
    original_reparse_check = normalizer._has_reparse_flag

    def simulated_resolve(path: Path, strict: bool = False) -> Path:
        if path.absolute() == alias.absolute():
            return workspace
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", simulated_resolve)
    monkeypatch.setattr(
        normalizer,
        "_has_reparse_flag",
        lambda path: Path(path).absolute() == alias.absolute() or original_reparse_check(path),
    )
    output = workspace / "output-workspace-alias"

    with pytest.raises(normalizer.PlanError, match="workspace.*enlace|workspace.*reparse|alias"):
        normalizer.normalize_plan(plan, output, alias)
    assert not output.exists()


def test_every_output_parent_ancestor_is_checked_for_reparse(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    simulated_reparse = workspace / "simulated-reparse"
    parent = simulated_reparse / "child"
    parent.mkdir(parents=True)
    original_reparse_check = normalizer._has_reparse_flag
    monkeypatch.setattr(
        normalizer,
        "_has_reparse_flag",
        lambda path: Path(path) == simulated_reparse or original_reparse_check(path),
    )
    output = parent / "output"

    with pytest.raises(normalizer.PlanError, match="ancestro|reparse"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()


def test_bindings_drift_at_stage_mkdir_blocks_publication_and_preserves_failed_stage(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    replacement = workspace / "replacement.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    _save_image(replacement, (1024, 1024), (120, 120, 880, 880))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    review = workspace / "review.json"
    output_parent = workspace / "boundary-parent"
    output_parent.mkdir()
    output = output_parent / "output"
    original_mkdir = Path.mkdir
    original_reparse_check = normalizer._has_reparse_flag
    boundary = {"fired": False, "parent_reparse": False}

    def mutate_after_stage_mkdir(path: Path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path.name.startswith(".output.staging-") and not boundary["fired"]:
            boundary["fired"] = True
            plan.write_text('{"schema_version":1,"entries":[]}\n', encoding="utf-8")
            review.write_text('{"decision":"changed"}\n', encoding="utf-8")
            hardlink = workspace / "source-hardlink.png"
            os.link(source, hardlink)
            source.write_bytes(replacement.read_bytes())
            boundary["parent_reparse"] = True
        return result

    monkeypatch.setattr(Path, "mkdir", mutate_after_stage_mkdir)
    monkeypatch.setattr(
        normalizer,
        "_has_reparse_flag",
        lambda path: (
            boundary["parent_reparse"] and Path(path) == output_parent
        )
        or original_reparse_check(path),
    )

    with pytest.raises(normalizer.PlanError, match="drift|binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()
    stages = list(output_parent.glob(".output.staging-*"))
    assert len(stages) == 1
    assert (stages[0] / "FAILED.json").is_file()


def test_parent_reparse_drift_during_stage_write_blocks_publication(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output_parent = workspace / "write-boundary"
    output_parent.mkdir()
    output = output_parent / "output"
    original_write_new = normalizer._write_new
    original_reparse_check = normalizer._has_reparse_flag
    boundary = {"fired": False, "parent_reparse": False}

    def mutate_after_stage_write(path: Path, payload: bytes):
        result = original_write_new(path, payload)
        if not boundary["fired"]:
            boundary["fired"] = True
            boundary["parent_reparse"] = True
        return result

    monkeypatch.setattr(normalizer, "_write_new", mutate_after_stage_write)
    monkeypatch.setattr(
        normalizer,
        "_has_reparse_flag",
        lambda path: (
            boundary["parent_reparse"] and Path(path) == output_parent
        )
        or original_reparse_check(path),
    )

    with pytest.raises(normalizer.PlanError, match="drift|binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()
    stages = list(output_parent.glob(".output.staging-*"))
    assert len(stages) == 1
    assert (stages[0] / "FAILED.json").is_file()


def test_drift_inside_rename_never_publishes_pass_manifest(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    replacement = workspace / "replacement.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    _save_image(replacement, (1024, 1024), (130, 130, 870, 870))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    review = workspace / "review.json"
    output_parent = workspace / "rename-boundary"
    output_parent.mkdir()
    output = output_parent / "output"
    original_rename = normalizer.os.rename
    original_reparse_check = normalizer._has_reparse_flag
    boundary = {"fired": False, "parent_reparse": False}

    def mutate_inside_rename(source_path, destination_path):
        if not boundary["fired"]:
            boundary["fired"] = True
            review.write_text('{"decision":"changed-at-rename"}\n', encoding="utf-8")
            os.link(source, workspace / "rename-hardlink.png")
            source.write_bytes(replacement.read_bytes())
            boundary["parent_reparse"] = True
        return original_rename(source_path, destination_path)

    monkeypatch.setattr(normalizer.os, "rename", mutate_inside_rename)
    monkeypatch.setattr(
        normalizer,
        "_has_reparse_flag",
        lambda path: (
            boundary["parent_reparse"] and Path(path) == output_parent
        )
        or original_reparse_check(path),
    )

    with pytest.raises(normalizer.PlanError, match="drift|binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)
    assert output.is_dir()
    assert not (output / "manifest.json").exists()
    assert (output / "FAILED.json").is_file()


def test_rename_that_moves_then_raises_quarantines_only_original_stage_identity(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-rename-raised"
    original_rename = normalizer.os.rename
    foreign_manifest = b'{"status":"FOREIGN"}\n'
    injected = {"stage": None}

    def move_then_recreate_stage_and_raise(source_path, destination_path):
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        result = original_rename(source_path, destination_path)
        if destination_path == output:
            source_path.mkdir()
            (source_path / "manifest.json").write_bytes(foreign_manifest)
            injected["stage"] = source_path
            raise OSError("fallo sintético posterior al rename efectivo")
        return result

    monkeypatch.setattr(normalizer.os, "rename", move_then_recreate_stage_and_raise)

    with pytest.raises(normalizer.PlanError, match="escritura|publicación"):
        normalizer.normalize_plan(plan, output, workspace)

    foreign_stage = injected["stage"]
    assert foreign_stage is not None
    assert (foreign_stage / "manifest.json").read_bytes() == foreign_manifest
    assert not (foreign_stage / "INVALIDATED_PASS_MANIFEST.json").exists()
    assert not (foreign_stage / "FAILED.json").exists()
    assert output.is_dir()
    assert not (output / "manifest.json").exists()
    assert (output / "INVALIDATED_PASS_MANIFEST.json").is_file()
    failed = json.loads((output / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["failure"]["code"] == "TRANSACTION_WRITE_FAILED"


def test_drift_during_final_manifest_write_quarantines_pass_manifest(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    review = workspace / "review.json"
    output = workspace / "output-manifest-boundary"
    original_write_new = normalizer._write_new
    boundary = {"fired": False}

    def mutate_after_manifest_write(path: Path, payload: bytes):
        result = original_write_new(path, payload)
        if path.name == "manifest.json" and not boundary["fired"]:
            boundary["fired"] = True
            review.write_text('{"decision":"changed-after-manifest"}\n', encoding="utf-8")
        return result

    monkeypatch.setattr(normalizer, "_write_new", mutate_after_manifest_write)

    with pytest.raises(normalizer.PlanError, match="drift|binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()
    stages = list(workspace.glob(".output-manifest-boundary.staging-*"))
    assert len(stages) == 1
    assert not (stages[0] / "manifest.json").exists()
    assert (stages[0] / "INVALIDATED_PASS_MANIFEST.json").is_file()
    assert (stages[0] / "FAILED.json").is_file()


def test_unexpected_error_after_manifest_write_preserves_auditable_failed_stage(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-unexpected-write-error"
    original_write_new = normalizer._write_new

    def fail_after_manifest_write(path: Path, payload: bytes):
        result = original_write_new(path, payload)
        if path.name == "manifest.json":
            raise OSError("fallo sintético después de escribir manifest")
        return result

    monkeypatch.setattr(normalizer, "_write_new", fail_after_manifest_write)

    with pytest.raises(normalizer.PlanError, match="escritura|publicación"):
        normalizer.normalize_plan(plan, output, workspace)

    assert not output.exists()
    stages = list(workspace.glob(".output-unexpected-write-error.staging-*"))
    assert len(stages) == 1
    stage = stages[0]
    assert (stage / "TRANSACTION_PREPARED.json").is_file()
    assert not (stage / "manifest.json").exists()
    assert (stage / "INVALIDATED_PASS_MANIFEST.json").is_file()
    failed = json.loads((stage / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["failure"]["code"] == "TRANSACTION_WRITE_FAILED"
    assert failed["approved"] is False
    assert failed["promotion"]["allowed"] is False


def test_atomic_rename_observes_complete_manifest_and_receipts_in_stage(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-atomic-complete"
    original_rename = normalizer.os.rename
    observed = {"complete": False}

    def assert_complete_before_rename(source_path, destination_path):
        source_path = Path(source_path)
        if source_path.name.startswith(".output-atomic-complete.staging-"):
            manifest_path = source_path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert all((source_path / row["receipt_path"]).is_file() for row in manifest["entries"])
            observed["complete"] = True
        return original_rename(source_path, destination_path)

    monkeypatch.setattr(normalizer.os, "rename", assert_complete_before_rename)

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert manifest["status"] == "PASS"
    assert observed["complete"] is True
    assert (output / "manifest.json").is_file()


def test_complete_staged_tree_is_sealed_once_before_publish(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-staged-seal"
    original_write_new = normalizer._write_new
    injected = {"done": False}

    def add_unexpected_artifact_after_manifest(path: Path, payload: bytes):
        result = original_write_new(path, payload)
        if path.name == "manifest.json" and not injected["done"]:
            injected["done"] = True
            (path.parent / "unexpected-evidence.bin").write_bytes(b"unexpected")
        return result

    monkeypatch.setattr(normalizer, "_write_new", add_unexpected_artifact_after_manifest)

    with pytest.raises(normalizer.PlanError, match="artifact|árbol|tree|binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)

    assert not output.exists()
    stages = list(workspace.glob(".output-staged-seal.staging-*"))
    assert len(stages) == 1
    assert (stages[0] / "unexpected-evidence.bin").read_bytes() == b"unexpected"
    assert (stages[0] / "INVALIDATED_PASS_MANIFEST.json").is_file()
    assert (stages[0] / "INVALIDATED.json").is_file()
    assert (stages[0] / "FAILED.json").is_file()


@pytest.mark.parametrize(
    "mutation",
    ["move_receipt", "replace_png", "hardlink_png", "swap_output"],
)
def test_tree_corruption_inside_final_rename_respects_transaction_identity(
    normalizer, workspace: Path, monkeypatch, mutation: str
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / f"output-tree-{mutation}"
    preserved = workspace / f"preserved-stage-{mutation}"
    original_rename = normalizer.os.rename
    boundary = {"done": False}

    def corrupt_inside_rename(source_path, destination_path):
        staged = Path(source_path)
        destination = Path(destination_path)
        if destination == output and not boundary["done"]:
            boundary["done"] = True
            if mutation == "move_receipt":
                receipt = next((staged / "receipts").glob("*.json"))
                original_rename(receipt, staged / "moved-receipt-evidence.json")
            elif mutation == "replace_png":
                asset = next((staged / "assets").glob("*.png"))
                asset.write_bytes(b"not-a-png")
            elif mutation == "hardlink_png":
                asset = next((staged / "assets").glob("*.png"))
                os.link(asset, staged / "hardlinked-asset-evidence.png")
            else:
                shutil.copytree(staged, destination)
                original_rename(staged, preserved)
                return None
        return original_rename(source_path, destination_path)

    monkeypatch.setattr(normalizer.os, "rename", corrupt_inside_rename)

    with pytest.raises(normalizer.PlanError, match="artifact|árbol|tree|binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)

    assert output.is_dir()
    if mutation == "swap_output":
        assert (preserved / "manifest.json").is_file()
        assert (output / "manifest.json").is_file()
        assert not (output / "INVALIDATED_PASS_MANIFEST.json").exists()
        assert not (output / "INVALIDATED.json").exists()
        assert not (output / "FAILED.json").exists()
    else:
        assert not (output / "manifest.json").exists()
        assert (output / "INVALIDATED_PASS_MANIFEST.json").is_file()
        assert (output / "INVALIDATED.json").is_file()
        assert (output / "FAILED.json").is_file()


@pytest.mark.parametrize("changed_input", ["source", "review"])
def test_failed_candidate_bindings_are_revalidated_post_publish(
    normalizer, workspace: Path, monkeypatch, changed_input: str
):
    source = workspace / "low-resolution.png"
    _save_image(source, (400, 700), (50, 50, 350, 650))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    review = workspace / "review.json"
    output = workspace / f"output-failed-binding-{changed_input}"
    original_rename = normalizer.os.rename
    boundary = {"done": False}

    def mutate_failed_input_inside_rename(source_path, destination_path):
        if Path(destination_path) == output and not boundary["done"]:
            boundary["done"] = True
            target = source if changed_input == "source" else review
            target.write_bytes(target.read_bytes() + b"\nchanged")
        return original_rename(source_path, destination_path)

    monkeypatch.setattr(normalizer.os, "rename", mutate_failed_input_inside_rename)

    with pytest.raises(normalizer.PlanError, match="binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)

    assert output.is_dir()
    assert not (output / "manifest.json").exists()
    assert (output / "FAILED.json").is_file()
    invalidated_receipt = next((output / "INVALIDATED_RECEIPTS").glob("*.json"))
    receipt = json.loads(invalidated_receipt.read_text(encoding="utf-8"))
    assert receipt["source"]["sha256"] == receipt["source"]["declared_sha256"]
    assert receipt["source_review"]["sha256"] == receipt["source_review"]["declared_sha256"]


@pytest.mark.parametrize("mismatched_input", ["source", "review"])
def test_failed_sha_mismatch_still_binds_existing_file_for_post_publish_revalidation(
    normalizer, workspace: Path, monkeypatch, mismatched_input: str
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    entry = _entry(workspace, source)
    declared_field = (
        "source_sha256" if mismatched_input == "source" else "source_review_sha256"
    )
    entry[declared_field] = "0" * 64
    plan = _write_plan(workspace, [entry])
    review = workspace / "review.json"
    output = workspace / f"output-sha-mismatch-{mismatched_input}"
    original_rename = normalizer.os.rename

    def mutate_mismatched_file_inside_rename(source_path, destination_path):
        if Path(destination_path) == output:
            target = source if mismatched_input == "source" else review
            target.write_bytes(target.read_bytes() + b"\nchanged")
        return original_rename(source_path, destination_path)

    monkeypatch.setattr(normalizer.os, "rename", mutate_mismatched_file_inside_rename)

    with pytest.raises(normalizer.PlanError, match="binding|cambió"):
        normalizer.normalize_plan(plan, output, workspace)

    invalidated_receipt = next((output / "INVALIDATED_RECEIPTS").glob("*.json"))
    receipt = json.loads(invalidated_receipt.read_text(encoding="utf-8"))
    receipt_field = "source" if mismatched_input == "source" else "source_review"
    assert receipt[receipt_field]["sha256"] != receipt[receipt_field]["declared_sha256"]
    assert (output / "FAILED.json").is_file()


def test_complete_artifact_tree_is_validated_exactly_pre_and_post_rename(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-tree-checkpoints"
    original = getattr(normalizer, "_assert_artifact_tree", None)
    checked: list[Path] = []

    def trace_tree_check(*args, **kwargs):
        checked.append(Path(args[0]))
        if original is not None:
            return original(*args, **kwargs)
        return None

    monkeypatch.setattr(normalizer, "_assert_artifact_tree", trace_tree_check, raising=False)

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert manifest["status"] == "PASS"
    assert len(checked) == 2
    assert checked[0].name.startswith(".output-tree-checkpoints.staging-")
    assert checked[1] == output


def test_input_hash_reads_remain_linear_for_hundreds_of_rows(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "shared-scale.png"
    image = Image.new("RGB", (512, 512), "white")
    image.paste((20, 80, 140), (50, 50, 462, 462))
    image.save(source, format="PNG")
    row_count = 200
    internal_ids = [f"labenze:scale-{index:03d}" for index in range(row_count)]
    evidence = _shared_visual_evidence(*internal_ids, group_id="linear-scale-group")
    entries = [
        _entry(
            workspace,
            source,
            action="centered_canvas_padding_no_scale",
            internal_id=internal_id,
            sku=f"SCALE-{index:03d}",
            product_key=f"scale-{index:03d}",
            shared_visual_evidence=dict(evidence),
        )
        for index, internal_id in enumerate(internal_ids)
    ]
    plan = _write_plan(workspace, entries)
    output = workspace / "output-linear-scale"
    original_read_bound_file = normalizer._read_bound_file
    reads = {"count": 0}

    def count_bound_reads(*args, **kwargs):
        reads["count"] += 1
        return original_read_bound_file(*args, **kwargs)

    monkeypatch.setattr(normalizer, "_read_bound_file", count_bound_reads)

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert manifest["status"] == "PASS"
    assert manifest["summary"]["total"] == row_count
    assert reads["count"] == row_count * 2 + 10


def test_hardlink_source_is_rejected(normalizer, workspace: Path):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))

    hardlink = workspace / "hardlink.png"
    os.link(source, hardlink)
    hard_plan = _write_plan(workspace, [_entry(workspace, hardlink)], "hard-plan.json")
    hard_out = workspace / "hard-out"
    hard_manifest = normalizer.normalize_plan(hard_plan, hard_out, workspace)
    assert _receipt(workspace, hard_out, hard_manifest)["failure"]["code"] == "SOURCE_ALIAS_FORBIDDEN"


def test_symlink_or_reparse_source_is_rejected(normalizer, workspace: Path, monkeypatch):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    symlink = workspace / "symlink.png"
    try:
        symlink.symlink_to(source)
    except OSError:
        original_reparse_check = normalizer._has_reparse_flag
        monkeypatch.setattr(
            normalizer,
            "_has_reparse_flag",
            lambda path: Path(path) == source or original_reparse_check(path),
        )
        symlink = source
    symlink_plan = _write_plan(workspace, [_entry(workspace, symlink)], "symlink-plan.json")
    symlink_out = workspace / "symlink-out"
    symlink_manifest = normalizer.normalize_plan(symlink_plan, symlink_out, workspace)
    assert _receipt(workspace, symlink_out, symlink_manifest)["failure"]["code"] == "SOURCE_ALIAS_FORBIDDEN"


def test_duplicate_identity_or_source_is_structurally_rejected(normalizer, workspace: Path):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    first = _entry(workspace, source)
    duplicate = dict(first)
    duplicate["internal_id"] = "labenze:test-002"
    duplicate["product_key"] = "test-002"
    duplicate["sku"] = "TEST-002"
    plan = _write_plan(workspace, [first, duplicate])
    output = workspace / "output"

    with pytest.raises(normalizer.PlanError, match="duplicado"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()


def _shared_visual_evidence(*internal_ids: str, group_id: str = "family-seat-blue") -> dict:
    return {
        "group_id": group_id,
        "assigned_internal_ids": sorted(internal_ids),
        "evidence_url": "https://example.test/catalog.pdf#page=7",
        "reason": "El catálogo oficial asigna la misma toma y configuración a ambos códigos.",
        "visual_signature": "seat-blue-front-v1",
        "configuration_equivalence": "same-model-color-components",
    }


def test_duplicate_source_hash_is_allowed_only_with_symmetric_explicit_visual_evidence(
    normalizer, workspace: Path
):
    source = workspace / "shared.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    first_id = "labenze:test-001"
    second_id = "labenze:test-002"
    evidence = _shared_visual_evidence(first_id, second_id)
    first = _entry(workspace, source, shared_visual_evidence=evidence)
    second = _entry(
        workspace,
        source,
        internal_id=second_id,
        product_key="test-002",
        sku="TEST-002",
        shared_visual_evidence=dict(evidence),
    )
    plan = _write_plan(workspace, [first, second])
    output = workspace / "output-shared"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipts = [_receipt(workspace, output, manifest, index) for index in range(2)]

    assert manifest["status"] == "PASS"
    assert manifest["summary"] == {"failed": 0, "passed": 2, "total": 2}
    assert receipts[0]["output"]["sha256"] == receipts[1]["output"]["sha256"]
    assert receipts[0]["shared_visual_evidence"] == evidence
    assert receipts[1]["shared_visual_evidence"] == evidence
    assert len(list((output / "assets").iterdir())) == 1


@pytest.mark.parametrize(
    "corrupt",
    [
        "missing",
        "asymmetric_ids",
        "different_group",
        "different_signature",
        "invalid_url",
        "malformed_url",
    ],
)
def test_duplicate_hash_evidence_group_is_rejected_atomically_when_missing_or_asymmetric(
    normalizer, workspace: Path, corrupt: str
):
    source = workspace / "shared.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    first_id = "labenze:test-001"
    second_id = "labenze:test-002"
    evidence = _shared_visual_evidence(first_id, second_id)
    first = _entry(workspace, source, shared_visual_evidence=evidence)
    second_evidence = dict(evidence)
    if corrupt == "missing":
        second = _entry(
            workspace,
            source,
            internal_id=second_id,
            product_key="test-002",
            sku="TEST-002",
        )
    else:
        if corrupt == "asymmetric_ids":
            second_evidence["assigned_internal_ids"] = [second_id]
        elif corrupt == "different_group":
            second_evidence["group_id"] = "different-group"
        elif corrupt == "different_signature":
            second_evidence["visual_signature"] = "different-signature"
        elif corrupt == "invalid_url":
            second_evidence["evidence_url"] = "catalog.pdf#page=7"
        elif corrupt == "malformed_url":
            second_evidence["evidence_url"] = "https://[invalid"
        second = _entry(
            workspace,
            source,
            internal_id=second_id,
            product_key="test-002",
            sku="TEST-002",
            shared_visual_evidence=second_evidence,
        )
    plan = _write_plan(workspace, [first, second])
    output = workspace / f"output-{corrupt}"

    with pytest.raises(normalizer.PlanError, match="evidencia visual compartida"):
        normalizer.normalize_plan(plan, output, workspace)
    assert not output.exists()


def test_shared_visual_group_is_atomic_when_one_member_fails_candidate_validation(
    normalizer, workspace: Path
):
    source = workspace / "shared.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    first_id = "labenze:test-001"
    second_id = "labenze:test-002"
    evidence = _shared_visual_evidence(first_id, second_id)
    entries = [
        _entry(workspace, source, shared_visual_evidence=evidence),
        _entry(
            workspace,
            source,
            internal_id=second_id,
            product_key="test-002",
            sku="TEST-002",
            source_review_sha256="0" * 64,
            shared_visual_evidence=dict(evidence),
        ),
    ]
    plan = _write_plan(workspace, entries)
    output = workspace / "output-shared-member-failure"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipts = [_receipt(workspace, output, manifest, index) for index in range(2)]

    assert manifest["status"] == "FAILED"
    assert manifest["summary"] == {"failed": 2, "passed": 0, "total": 2}
    assert {receipt["status"] for receipt in receipts} == {"FAILED"}
    assert receipts[0]["failure"]["code"] == "SHARED_VISUAL_GROUP_MEMBER_FAILED"
    assert receipts[1]["failure"]["code"] == "SOURCE_REVIEW_SHA256_MISMATCH"
    assert not (output / "assets").exists() or not list((output / "assets").iterdir())


def test_wide_source_that_requires_over_25_mpx_square_fails_closed(
    normalizer, workspace: Path
):
    source = workspace / "wide.png"
    _save_image(source, (6000, 512), (500, 60, 5500, 452))
    plan = _write_plan(
        workspace,
        [_entry(workspace, source, action="centered_canvas_padding_no_scale")],
    )
    output = workspace / "output"

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert _receipt(workspace, output, manifest)["failure"]["code"] == "NO_FEASIBLE_CANVAS"


def test_final_png_over_8_mib_is_rejected(normalizer, workspace: Path):
    source = workspace / "noise.png"
    image = Image.new("RGB", (2048, 2048), "white")
    noise = random.Random(7).randbytes(1700 * 1700 * 3)
    patch = Image.frombytes("RGB", (1700, 1700), noise)
    image.paste(patch, (174, 174))
    image.save(source, format="PNG", compress_level=0)
    assert source.stat().st_size > 8 * 1024 * 1024
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output"

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert _receipt(workspace, output, manifest)["failure"]["code"] == "FINAL_BYTES_OVER_8_MIB"


def test_aggregate_asset_memory_budget_fails_closed_before_unbounded_accumulation(
    normalizer, workspace: Path, monkeypatch
):
    first = workspace / "first.png"
    second = workspace / "second.png"
    _save_image(first, (1024, 1024), (100, 100, 900, 900))
    _save_image(second, (1024, 1024), (120, 100, 920, 900))
    entries = [
        _entry(workspace, first),
        _entry(
            workspace,
            second,
            internal_id="requiez:test-002",
            supplier="requiez",
            sku="TEST-002",
            product_key="test-002",
        ),
    ]
    plan = _write_plan(workspace, entries)
    output = workspace / "output-budget"
    monkeypatch.setattr(normalizer, "MAX_AGGREGATE_ASSET_BYTES", 1, raising=False)

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipts = [_receipt(workspace, output, manifest, index) for index in range(2)]

    assert manifest["status"] == "FAILED"
    assert manifest["summary"] == {"failed": 2, "passed": 0, "total": 2}
    assert {receipt["failure"]["code"] for receipt in receipts} == {
        "AGGREGATE_ASSET_MEMORY_BUDGET_EXCEEDED"
    }
    assert not (output / "assets").exists() or not list((output / "assets").iterdir())


def test_malformed_plan_duplicate_json_keys_and_output_overlap_write_nothing(
    normalizer, workspace: Path
):
    malformed = workspace / "malformed.json"
    malformed.write_text('{"schema_version":1,"entries":[}', encoding="utf-8")
    duplicate_keys = workspace / "duplicate-keys.json"
    duplicate_keys.write_text('{"schema_version":1,"schema_version":1,"entries":[]}', encoding="utf-8")

    for plan in (malformed, duplicate_keys):
        output = workspace / f"out-{plan.stem}"
        with pytest.raises(normalizer.PlanError):
            normalizer.normalize_plan(plan, output, workspace)
        assert not output.exists()

    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    with pytest.raises(normalizer.PlanError, match="solapa"):
        normalizer.normalize_plan(plan, source, workspace)


def test_existing_output_is_never_overwritten(normalizer, workspace: Path):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(normalizer.PlanError, match="salida ya existe"):
        normalizer.normalize_plan(plan, output, workspace)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_mime_magic_animation_and_corrupt_images_fail_closed(normalizer, workspace: Path):
    png_as_jpg = workspace / "wrong.jpg"
    _save_image(png_as_jpg, (1024, 1024), (100, 100, 900, 900))
    corrupt = workspace / "corrupt.png"
    corrupt.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    animated = workspace / "animated.png"
    frames = [Image.new("RGB", (512, 512), color) for color in ("white", "black")]
    frames[0].save(animated, format="PNG", save_all=True, append_images=frames[1:], duration=100)

    expected = [
        (png_as_jpg, "SOURCE_MIME_EXTENSION_MISMATCH"),
        (corrupt, "SOURCE_IMAGE_INVALID"),
        (animated, "SOURCE_ANIMATED"),
    ]
    for index, (source, code) in enumerate(expected):
        updates = {"source_dimensions": {"width": 1024, "height": 1024}} if source == corrupt else {}
        plan = _write_plan(
            workspace,
            [_entry(workspace, source, **updates)],
            f"plan-{index}.json",
        )
        output = workspace / f"output-{index}"
        manifest = normalizer.normalize_plan(plan, output, workspace)
        assert _receipt(workspace, output, manifest)["failure"]["code"] == code


def test_nontrivial_exif_orientation_is_rejected_without_implicit_rotation(
    normalizer, workspace: Path
):
    source = workspace / "oriented.jpg"
    image = Image.new("RGB", (600, 850), "white")
    for y in range(80, 770):
        for x in range(50, 550):
            image.putpixel((x, y), (20, 80, 140))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, format="JPEG", quality=95, exif=exif)
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-oriented"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipt = _receipt(workspace, output, manifest)

    assert manifest["status"] == "FAILED"
    assert receipt["failure"]["code"] == "SOURCE_EXIF_ORIENTATION_UNSUPPORTED"
    assert not (output / "assets").exists() or not list((output / "assets").iterdir())


def test_decompression_bomb_error_becomes_failed_receipt_instead_of_aborting_batch(
    normalizer, workspace: Path, monkeypatch
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-bomb"

    def raise_bomb(*_args, **_kwargs):
        raise Image.DecompressionBombError("synthetic bomb probe")

    monkeypatch.setattr(normalizer.Image, "open", raise_bomb)
    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert manifest["status"] == "FAILED"
    assert _receipt(workspace, output, manifest)["failure"]["code"] == "SOURCE_IMAGE_INVALID"


def test_plan_schema_is_explicit_and_unknown_action_is_rejected(normalizer, workspace: Path):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    entry = _entry(workspace, source)
    entry["action"] = "auto_fix"
    plan = _write_plan(workspace, [entry])

    with pytest.raises(normalizer.PlanError, match="action"):
        normalizer.normalize_plan(plan, workspace / "output", workspace)


def test_failed_and_pass_receipts_and_manifest_are_deterministic_and_never_approve(
    normalizer, workspace: Path
):
    good = workspace / "good.png"
    bad = workspace / "bad.png"
    _save_image(good, (1024, 1024), (100, 100, 900, 900))
    _save_image(bad, (400, 700), (50, 50, 350, 650))
    entries = [
        _entry(workspace, good),
        _entry(
            workspace,
            bad,
            internal_id="requiez:test-002",
            supplier="requiez",
            sku="TEST-002",
            product_key="test-002",
            action="centered_canvas_padding_no_scale",
        ),
    ]
    plan = _write_plan(workspace, entries)
    output = workspace / "output"

    manifest = normalizer.normalize_plan(plan, output, workspace)

    assert manifest["status"] == "FAILED"
    assert manifest["summary"] == {"failed": 1, "passed": 1, "total": 2}
    assert manifest["approved"] is False
    assert manifest["promotion"]["allowed"] is False
    assert all(value is False for value in manifest["mutations"].values())
    for index in range(2):
        receipt = _receipt(workspace, output, manifest, index)
        assert receipt["approved"] is False
        assert receipt["promotion"]["allowed"] is False
        assert all(value is False for value in receipt["mutations"].values())


def test_receipts_and_manifest_bind_algorithm_script_and_runtime_provenance(
    normalizer, workspace: Path
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    output = workspace / "output-provenance"

    manifest = normalizer.normalize_plan(plan, output, workspace)
    receipt = _receipt(workspace, output, manifest)
    provenance = manifest["provenance"]

    assert receipt["provenance"] == provenance
    assert provenance["algorithm"] == {
        "name": "labenze_requiez_visual_candidate_normalizer",
        "schema_version": 1,
        "version": "1.0.0",
        "foreground_gate": "builder_alpha16_corner_delta20_v1",
    }
    assert provenance["implementation"]["script_sha256"] == _sha(SCRIPT)
    assert set(provenance["runtime"]) == {"pillow", "python", "zlib", "zlib_runtime"}
    assert all(provenance["runtime"].values())
    assert provenance["limits"] == {
        "aggregate_asset_memory_bytes": normalizer.MAX_AGGREGATE_ASSET_BYTES
    }


def test_cli_prints_absolute_manifest_path_under_workspace_root(
    normalizer, workspace: Path, tmp_path: Path, monkeypatch, capsys
):
    source = workspace / "source.png"
    _save_image(source, (1024, 1024), (100, 100, 900, 900))
    plan = _write_plan(workspace, [_entry(workspace, source)])
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    result = normalizer.main(
        [
            "--plan",
            str(plan),
            "--output-dir",
            "cli-output",
            "--workspace-root",
            str(workspace),
        ]
    )

    assert result == 0
    printed = Path(capsys.readouterr().out.strip())
    assert printed == (workspace / "cli-output" / "manifest.json").resolve(strict=True)
