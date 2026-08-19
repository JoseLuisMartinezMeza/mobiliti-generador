from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scripts import audit_labenze_requiez_visuals as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_labenze_requiez_visuals.py"
LABENZE_SHA = "a" * 64
REQUIEZ_SHA = "b" * 64


def _write_png(path: Path, *, size=(1024, 1024), bbox=(128, 128, 895, 895)) -> str:
    image = Image.new("RGBA", size, (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle(bbox, fill=(32, 80, 140, 255))
    image.save(path, format="PNG")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = path.with_name(f"{digest}.png")
    path.rename(target)
    return target.name


def _item(
    supplier: str,
    index: int,
    *,
    status: str,
    asset_name: str | None,
    source_code: str | None = None,
    needs_review: bool = False,
) -> dict:
    code = source_code or f"{'LAB' if supplier == 'labenze' else 'REQ'}-{index:04d}"
    slug = code.casefold().replace("/", "-")
    internal_id = (
        f"labenze:review:{slug}:{index:04d}"
        if supplier == "labenze" and needs_review
        else f"{supplier}:{slug}"
    )
    source_hash = LABENZE_SHA if supplier == "labenze" else REQUIEZ_SHA
    attributes = {
        "source_code": code,
        "source_page": index % 113 + 1,
        "variant": "Base negra · sin brazos" if supplier == "labenze" else "",
    }
    if supplier == "labenze":
        attributes["source_sha256"] = source_hash
    else:
        attributes["source_file_sha256"] = source_hash
    if status != "placeholder":
        attributes["image_match"] = {
            "status": status,
            "asset_sha256": Path(asset_name).stem,
            "source_references": [
                {
                    "file_id": source_hash,
                    "sheet_or_page": attributes["source_page"],
                    "cell_or_bbox": [10.0, 20.0, 110.0, 180.0],
                }
            ],
        }
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": asset_name,
            "image_kind": "official",
            "approved": True,
        }
    return {
        "internal_id": internal_id,
        "supplier": supplier,
        "product_key": internal_id.removeprefix(f"{supplier}:"),
        "sku": "" if needs_review else code,
        "code_status": "needs_review" if needs_review else "verified",
        "collection": "Sillas",
        "name": "ZELIG MODULAR" if code.startswith("155-191") else f"Producto {code}",
        "description": f"Configuración publicada {code}",
        "base_price_options": [],
        "add_on_options": [],
        "attributes": attributes,
        "image_kind": "placeholder" if status == "placeholder" else "official",
        "product_url": f"https://example.test/{supplier}.pdf#page={attributes['source_page']}",
        "warnings": [],
    }


def _synthetic_inputs(tmp_path: Path) -> tuple[dict, dict, Path]:
    assets = tmp_path / "assets"
    assets.mkdir()
    valid_asset = _write_png(assets / "valid.png")

    lab_focals = [
        "106-00603-BAT",
        "155-19100-000",
        "155-19110-000",
        "155-19120-000",
        "155-19130",
        "155-19140",
        "155-19150",
        "155-19160",
        "155-19170-NAT",
    ]
    req_focals = [
        "RM-9025N/NG",
        "RE-1063M",
        "RE-1064M",
        "RE-1073M",
        "RM-9100/GR",
        "RM-9100/NG",
        "RM-9101/GR",
        "RE-822/PU/MP",
        "RE-828/PU/PN",
        "RA-28",
    ]
    lab_items = [
        _item(
            "labenze",
            index,
            status="exact_pdf" if index < 46 else "family_pdf",
            asset_name=valid_asset,
            source_code=(lab_focals[index - 8] if 8 <= index < 8 + len(lab_focals) else None),
            needs_review=index < 8,
        )
        for index in range(462)
    ]
    req_items = [
        _item(
            "requiez",
            index,
            status="exact_pdf" if index < 157 else "family_pdf" if index == 157 else "placeholder",
            asset_name=valid_asset if index <= 157 else None,
            source_code=req_focals[index] if index < len(req_focals) else None,
        )
        for index in range(314)
    ]
    rebuilt = {
        "labenze": {
            "supplier": "labenze",
            "source_hash": LABENZE_SHA,
            "items": json.loads(json.dumps(lab_items)),
        },
        "requiez": {
            "supplier": "requiez",
            "source_hash": "derived-requiez-snapshot-hash",
            "items": json.loads(json.dumps(req_items)),
        },
    }
    store = {
        "catalog_published_snapshots": {
            "labenze": {
                "supplier": "labenze",
                "source_hash": LABENZE_SHA,
                "payload": {
                    "supplier": "labenze",
                    "source_hash": LABENZE_SHA,
                    "items": lab_items,
                },
            },
            "requiez": {
                "supplier": "requiez",
                "source_hash": "derived-requiez-snapshot-hash",
                "payload": {
                    "supplier": "requiez",
                    "source_hash": "derived-requiez-snapshot-hash",
                    "items": req_items,
                },
            },
        }
    }
    return rebuilt, store, assets


def test_cli_se_puede_invocar_directamente_con_python_314():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--labenze-pdf" in completed.stdout
    assert "--requiez-pdf" in completed.stdout
    assert "--store" in completed.stdout
    assert "--assets-dir" in completed.stdout
    assert "--output-dir" in completed.stdout


def test_comando_reproducible_cita_rutas_windows_con_espacios():
    command = audit.build_reproducible_command(
        ["C:\\Python314\\python.exe", "scripts/audit.py", "--labenze-pdf", "C:\\Fuentes\\LP Labenze B26.pdf"]
    )

    assert '"C:\\Fuentes\\LP Labenze B26.pdf"' in command
    assert command.startswith("C:\\Python314\\python.exe scripts/audit.py")


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("labenze_cardinality", "AUDIT_CARDINALITY:labenze"),
        ("requiez_cardinality", "AUDIT_CARDINALITY:requiez"),
        ("duplicate_identity", "AUDIT_DUPLICATE_ID"),
        ("identity_mismatch", "AUDIT_IDENTITY_MISMATCH"),
        ("source_mismatch", "AUDIT_SOURCE_HASH"),
        ("snapshot_missing", "AUDIT_SNAPSHOT_MISSING"),
    ],
)
def test_rechaza_snapshot_hash_identidad_o_cardinalidad_incorrectos(
    tmp_path: Path, mutation: str, error: str
):
    rebuilt, store, assets = _synthetic_inputs(tmp_path)
    if mutation == "labenze_cardinality":
        rebuilt["labenze"]["items"].pop()
    elif mutation == "requiez_cardinality":
        store["catalog_published_snapshots"]["requiez"]["payload"]["items"].pop()
    elif mutation == "duplicate_identity":
        rebuilt["labenze"]["items"][1]["internal_id"] = rebuilt["labenze"]["items"][0]["internal_id"]
    elif mutation == "identity_mismatch":
        store["catalog_published_snapshots"]["labenze"]["payload"]["items"][20]["sku"] = "OTRO"
    elif mutation == "source_mismatch":
        store["catalog_published_snapshots"]["requiez"]["payload"]["items"][20]["attributes"]["source_file_sha256"] = "c" * 64
    elif mutation == "snapshot_missing":
        store["catalog_published_snapshots"].pop("labenze")

    with pytest.raises(ValueError, match=error):
        audit.audit_snapshots(
            rebuilt,
            store,
            assets,
            tmp_path / "output",
            input_hashes={"labenze": LABENZE_SHA, "requiez": REQUIEZ_SHA, "store": "d" * 64},
            input_paths={"labenze": "labenze.pdf", "requiez": "requiez.pdf", "store": "db.json"},
            reproducible_command="python scripts/audit_labenze_requiez_visuals.py ...",
        )


def test_exporta_776_decisiones_matriz_compartida_y_contact_sheets_sin_aprobar(
    tmp_path: Path,
):
    rebuilt, store, assets = _synthetic_inputs(tmp_path)
    output = tmp_path / "inventory-20260819T120000Z"
    store_path = tmp_path / "db.json"
    store_path.write_text(json.dumps(store), encoding="utf-8")
    before_store = hashlib.sha256(store_path.read_bytes()).hexdigest()
    before_assets = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in assets.iterdir()
    }

    summary = audit.audit_snapshots(
        rebuilt,
        store,
        assets,
        output,
        input_hashes={"labenze": LABENZE_SHA, "requiez": REQUIEZ_SHA, "store": before_store},
        input_paths={"labenze": "labenze.pdf", "requiez": "requiez.pdf", "store": str(store_path)},
        reproducible_command="python scripts/audit_labenze_requiez_visuals.py --offline",
    )

    assert summary["counts"] == {
        "total": 776,
        "suppliers": {"labenze": 462, "requiez": 314},
        "match_status": {"exact_pdf": 203, "family_pdf": 417, "placeholder": 156},
        "decisions": {
            "re_audit_current": 158,
            "replace_or_rebuild": 462,
            "search_exact": 156,
        },
    }
    rows = [json.loads(line) for line in (output / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len({row["internal_id"] for row in rows}) == 776
    required = {
        "supplier", "internal_id", "product_key", "sku", "source_code", "collection",
        "name", "description", "source_page", "source_bbox", "current_asset",
        "image_kind", "match_status", "product_url", "visual_signature",
        "initial_decision", "reasons", "review",
    }
    assert required <= rows[0].keys()
    assert all(row["review"] == {
        "approved": False,
        "reviewer": "",
        "checks": {
            "full_product_visible": None,
            "not_cropped": None,
            "configuration_supported": None,
        },
        "status": "pending_human_review",
    } for row in rows)
    assert all(row["initial_decision"] == "replace_or_rebuild" for row in rows if row["supplier"] == "labenze")
    assert all(row["initial_decision"] == "search_exact" for row in rows if row["supplier"] == "requiez" and row["match_status"] == "placeholder")

    with (output / "inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 776
    shared = json.loads((output / "shared-visual-matrix.json").read_text(encoding="utf-8"))
    assert shared["groups"]
    assert all(group["equivalence_proven"] is False for group in shared["groups"])
    assert all(group["assigned_internal_ids"] and group["assigned_skus"] for group in shared["groups"])
    assert len(summary["contact_sheets"]) == 39
    assert all((output / relative).is_file() for relative in summary["contact_sheets"])
    with Image.open(output / summary["contact_sheets"][0]) as sheet:
        assert sheet.format == "PNG"
        assert sheet.width >= 1200 and sheet.height >= 800
    assert set(summary["artifact_hashes"]) >= {
        "inventory.jsonl", "inventory.csv", "shared-visual-matrix.json", "summary.json"
    }
    assert summary["input_hashes"]["store"] == before_store
    assert summary["reproducible_command"].endswith("--offline")
    assert hashlib.sha256(store_path.read_bytes()).hexdigest() == before_store
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in assets.iterdir()
    } == before_assets

    with pytest.raises(FileExistsError, match="AUDIT_OUTPUT_EXISTS"):
        audit.audit_snapshots(
            rebuilt,
            store,
            assets,
            output,
            input_hashes={"labenze": LABENZE_SHA, "requiez": REQUIEZ_SHA, "store": before_store},
            input_paths={},
            reproducible_command="repeat",
        )


def test_inspecciona_png_hash_bbox_margenes_reglas_y_excepciones_explicitamente(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    valid_name = _write_png(assets / "valid.png")
    border_name = _write_png(
        assets / "border.png",
        size=(320, 200),
        bbox=(0, 0, 319, 199),
    )

    valid = audit.inspect_asset(assets, valid_name)
    invalid = audit.inspect_asset(assets, border_name)
    missing = audit.inspect_asset(assets, "f" * 64 + ".png")

    assert valid["sha256_valid"] is True
    assert valid["mime"] == "image/png"
    assert valid["dimensions"] == {"width": 1024, "height": 1024}
    assert valid["quality_checks"] == {
        "square_1024_plus": True,
        "source_shortest_side_512_plus": True,
        "margin_4pct_plus": True,
        "bbox_92pct_or_less": True,
        "occupancy_12_to_80pct": True,
        "aspect_deformation_1pct_or_less": None,
    }
    assert valid["quality_exception"] == "source_aspect_reference_unavailable"
    assert invalid["quality_checks"]["square_1024_plus"] is False
    assert invalid["quality_checks"]["source_shortest_side_512_plus"] is False
    assert invalid["quality_checks"]["margin_4pct_plus"] is False
    assert invalid["border_or_rule_signal"] is True
    assert missing["status"] == "missing"
    assert "asset_missing" in missing["reasons"]


def test_marca_como_aproximacion_los_rotulos_incrustados_en_el_activo(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    path = assets / "label.png"
    image = Image.new("RGB", (1024, 1024), "white")
    draw = ImageDraw.Draw(image)
    draw.text((120, 120), "CODIGO SKU 1234", fill="black")
    draw.text((120, 150), "DESCRIPCION DEL PRODUCTO", fill="black")
    image.save(path, format="PNG")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = assets / f"{digest}.png"
    path.rename(target)

    inspected = audit.inspect_asset(assets, target.name)

    assert inspected["text_like_signal"] is True
    assert "text_like_signal" in inspected["reasons"]


def test_summary_exige_y_enumera_todos_los_casos_focales(tmp_path: Path):
    rebuilt, store, assets = _synthetic_inputs(tmp_path)

    summary = audit.audit_snapshots(
        rebuilt,
        store,
        assets,
        tmp_path / "inventory-focal",
        input_hashes={"labenze": LABENZE_SHA, "requiez": REQUIEZ_SHA, "store": "d" * 64},
        input_paths={},
        reproducible_command="synthetic",
    )

    focal = summary["focal_cases"]
    assert set(focal["requiez"]) == {
        "RM-9025N/NG", "RE-1063M", "RE-1064M", "RE-1073M", "RM-9100/GR",
        "RM-9100/NG", "RM-9101/GR", "RE-822/PU/MP", "RE-828/PU/PN", "RA-28",
    }
    assert focal["labenze"]["106-00603-BAT"]["found"] is True
    assert len(focal["labenze"]["ZELIG"]["internal_ids"]) == 8
    assert len(focal["labenze"]["needs_review"]["internal_ids"]) == 8
