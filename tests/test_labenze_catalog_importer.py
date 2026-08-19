from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pytest
from PIL import Image

from mobiliti_saas.worker.catalog_sync.importers.common import CatalogSnapshotBuild
from mobiliti_saas.worker.catalog_sync.importers import labenze as labenze_module
from mobiliti_saas.worker.catalog_sync.importers.labenze import (
    build_labenze_snapshot,
    build_labenze_snapshot_with_assets,
)


_MIME = "application/pdf"
_REAL_PDF = Path(
    r"C:\Users\pepem\AppData\Local\Temp\mobiliti-catalog-discovery-20260818"
    r"\LP Labenze B26.pdf"
)
_REAL_SHA256 = "c4fc2d2152b5e854f7c36c9106c71cd21853abb50efcde96ba2566cb72f1d6f3"


@dataclass(frozen=True)
class SourceDocument:
    path: str
    kind: str
    sha256: str
    mime_type: str
    local_path: Path


def _source(path: Path, *, declared_hash: str | None = None) -> SourceDocument:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return SourceDocument(
        path="LABENZE/LP Labenze B26.pdf",
        kind="price_list",
        sha256=declared_hash or digest,
        mime_type=_MIME,
        local_path=path,
    )


@pytest.fixture()
def sample_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "LP Labenze B26.pdf"
    document = fitz.open()
    for _ in range(3):
        document.new_page(width=538.583, height=708.661)
    page = document.new_page(width=538.583, height=708.661)
    page.insert_text((156, 50), "LOLA", fontname="hebo", fontsize=13)
    page.insert_text((348, 70), "Codigo", fontname="hebo", fontsize=10)
    page.insert_text((430, 70), "Precio", fontname="hebo", fontsize=10)
    page.insert_text((348, 90), "162-4360T-NAT-000", fontsize=8)
    page.insert_text((348, 110), "162-4360T-NOG-000", fontsize=8)
    page.insert_text((430, 90), "Tela Grado 0", fontname="hebo", fontsize=8)
    page.insert_text((430, 101), "$5,700.00 + IVA", fontname="hebo", fontsize=8)
    page.insert_text((430, 120), "Tela Grado A", fontname="hebo", fontsize=8)
    page.insert_text((430, 131), "$6,105.00 + IVA", fontname="hebo", fontsize=8)
    page.insert_text((430, 150), "Piel Negra", fontname="hebo", fontsize=8)
    page.insert_text((430, 161), "$8,307.00 + IVA", fontname="hebo", fontsize=8)
    page.insert_text((430, 180), "Piel Color", fontname="hebo", fontsize=8)
    page.insert_text((430, 191), "$9,076.00 + IVA", fontname="hebo", fontsize=8)
    page.insert_text((158, 145), "Descripcion", fontname="hebo", fontsize=10)
    page.insert_text((158, 158), "Silla de madera tapizada.", fontsize=8)
    page.draw_rect(fitz.Rect(48, 58, 136, 182), color=(0, 0, 0), fill=(0.3, 0.6, 0.9))

    page.insert_text((156, 215), "SIN ARRASTRE", fontname="hebo", fontsize=13)
    page.insert_text((348, 238), "Codigo", fontname="hebo", fontsize=10)
    page.insert_text((430, 238), "Precio", fontname="hebo", fontsize=10)
    page.insert_text((348, 258), "155-00001", fontsize=8)
    page.insert_text((348, 278), "155-00002", fontsize=8)
    page.insert_text((430, 269), "$1,250.00 + IVA", fontname="hebo", fontsize=8)
    page.draw_rect(fitz.Rect(48, 233, 136, 338), color=(0, 0, 0), fill=(0.8, 0.4, 0.2))

    page.insert_text((156, 405), "EXACTO", fontname="hebo", fontsize=13)
    page.insert_text((348, 428), "Codigo", fontname="hebo", fontsize=10)
    page.insert_text((430, 428), "Precio", fontname="hebo", fontsize=10)
    page.insert_text((348, 448), "155-00003", fontsize=8)
    page.insert_text((430, 459), "$2,000.00 + IVA", fontname="hebo", fontsize=8)
    exact_image = io.BytesIO()
    Image.new("RGB", (80, 80), (102, 204, 76)).save(exact_image, format="PNG")
    page.insert_image(fitz.Rect(48, 420, 136, 505), stream=exact_image.getvalue())

    page.insert_text((156, 550), "SIN PRECIO", fontname="hebo", fontsize=13)
    page.insert_text((348, 573), "Codigo", fontname="hebo", fontsize=10)
    page.insert_text((430, 573), "Precio", fontname="hebo", fontsize=10)
    page.insert_text((348, 593), "155-99999", fontsize=8)
    page.draw_rect(fitz.Rect(48, 565, 136, 650), color=(0, 0, 0), fill=(0.5, 0.3, 0.7))

    page = document.new_page(width=538.583, height=708.661)
    page.insert_text((156, 50), "GRADOS SEPARADOS", fontname="hebo", fontsize=13)
    page.insert_text((348, 70), "Codigo", fontname="hebo", fontsize=10)
    page.insert_text((348, 90), "155-00004", fontsize=8)
    page.insert_text((430, 100), "Grado 0", fontname="hebo", fontsize=8)
    page.insert_text((430, 112), "$2,100.00 + IVA", fontname="hebo", fontsize=8)
    page.insert_text((430, 140), "Grado A", fontname="hebo", fontsize=8)
    page.insert_text((430, 152), "$2,300.00 + IVA", fontname="hebo", fontsize=8)
    page.draw_rect(fitz.Rect(48, 58, 136, 182), color=(0, 0, 0), fill=(0.2, 0.7, 0.4))
    document.save(path)
    document.close()
    monkeypatch.setattr(
        labenze_module,
        "SUPPORTED_SHA256",
        frozenset({hashlib.sha256(path.read_bytes()).hexdigest()}),
    )
    return path


def test_publica_codigos_y_opciones_de_tapiz_solo_con_evidencia(sample_pdf: Path):
    build = build_labenze_snapshot_with_assets(
        (_source(sample_pdf),),
        synced_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
    )

    assert isinstance(build, CatalogSnapshotBuild)
    items = {item["sku"]: item for item in build.snapshot["items"]}
    assert set(items) == {
        "162-4360T-NAT-000",
        "162-4360T-NOG-000",
        "155-00001",
        "155-00002",
        "155-00003",
        "155-00004",
    }
    lola = items["162-4360T-NAT-000"]
    assert lola["name"] == "LOLA"
    assert lola["availability_type"] == "made_to_order"
    assert lola["base_currency"] == "MXN"
    assert lola["tax_rate"] == "0.160000"
    assert lola["price_net"] == "5700.000000"
    assert [option["name"] for option in lola["base_price_options"]] == [
        "Tela Grado 0",
        "Tela Grado A",
        "Piel Negra",
        "Piel Color",
    ]
    assert [option["price_net"] for option in lola["base_price_options"]] == [
        "5700.000000",
        "6105.000000",
        "8307.000000",
        "9076.000000",
    ]
    assert items["155-00001"]["price_net"] == "1250.000000"
    assert items["155-00002"]["price_net"] == "1250.000000"
    assert "155-99999" not in items
    assert [
        (option["name"], option["price_net"])
        for option in items["155-00004"]["base_price_options"]
    ] == [
        ("Grado 0", "2100.000000"),
        ("Grado A", "2300.000000"),
    ]


def test_assets_distinguen_recorte_exacto_de_imagen_familiar(sample_pdf: Path):
    build = build_labenze_snapshot_with_assets((_source(sample_pdf),))

    assert len(build.bindings) == len(build.snapshot["items"])
    assert build.assets_by_sha256
    family_binding = next(row for row in build.bindings if row.internal_id.endswith("162-4360t-nat-000"))
    assert family_binding.image_kind == "official"
    assert family_binding.match_status == "family_pdf"
    exact_binding = next(row for row in build.bindings if row.internal_id.endswith("155-00003"))
    assert exact_binding.match_status == "exact_pdf"
    reference = family_binding.source_references[0]
    assert reference["sheet_or_page"] == 4
    assert len(reference["cell_or_bbox"]) == 4
    assert reference["cell_or_bbox"][0] < reference["cell_or_bbox"][2]
    assert reference["cell_or_bbox"][1] < reference["cell_or_bbox"][3]

    item = next(row for row in build.snapshot["items"] if row["sku"] == "162-4360T-NAT-000")
    assert item["image_kind"] == "official"
    assert item["attributes"]["image_match"]["status"] == "family_pdf"
    assert item["product_url"].endswith("#page=4")
    evidence = json.loads(item["source_reference"])
    assert len(evidence) >= 3
    assert reference == evidence[-1]
    assert evidence[0]["cell_or_bbox"] != evidence[1]["cell_or_bbox"]
    assert item["attributes"]["evidence"]["code"] == evidence[0]
    assert item["attributes"]["evidence"]["image"] == reference


def test_snapshot_ligero_conserva_identidad_y_timestamp(sample_pdf: Path):
    moment = datetime(2026, 8, 18, 13, 14, 15, tzinfo=timezone.utc)
    first = build_labenze_snapshot((_source(sample_pdf),), synced_at=moment)
    second = build_labenze_snapshot((_source(sample_pdf),), synced_at=moment)

    assert first == second
    assert first["supplier"] == "labenze"
    assert first["generated_at"] == "2026-08-18T13:14:15+00:00"
    assert first["source_hash"] == hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    assert all(item["image_kind"] == "placeholder" for item in first["items"])


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"kind": "catalog"}, "LABENZE_BUNDLE"),
        ({"mime_type": "application/octet-stream"}, "LABENZE_BUNDLE"),
        ({"path": "LABENZE/otro.pdf"}, "LABENZE_BUNDLE"),
        ({"path": "EVIL/LP Labenze B26.pdf"}, "LABENZE_BUNDLE"),
        ({"sha256": "0" * 64}, "LABENZE_HASH"),
    ],
)
def test_rechaza_fuente_fuera_del_contrato(sample_pdf: Path, changes: dict, error: str):
    source = _source(sample_pdf)
    invalid = SourceDocument(
        path=changes.get("path", source.path),
        kind=changes.get("kind", source.kind),
        sha256=changes.get("sha256", source.sha256),
        mime_type=changes.get("mime_type", source.mime_type),
        local_path=source.local_path,
    )

    with pytest.raises(ValueError, match=error):
        build_labenze_snapshot_with_assets((invalid,))


def test_rechaza_pdf_fuera_del_hash_oficial_aunque_declarado_coincida(
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(labenze_module, "SUPPORTED_SHA256", frozenset({_REAL_SHA256}))

    with pytest.raises(ValueError, match="LABENZE_UNSUPPORTED_HASH"):
        build_labenze_snapshot_with_assets((_source(sample_pdf),))


@pytest.mark.skipif(not _REAL_PDF.is_file(), reason="PDF B26 no disponible en este entorno")
def test_pdf_b26_real_tiene_cobertura_total_y_hash_confirmado():
    assert hashlib.sha256(_REAL_PDF.read_bytes()).hexdigest() == _REAL_SHA256

    build = build_labenze_snapshot_with_assets((_source(_REAL_PDF),))
    items = build.snapshot["items"]

    assert len(items) == 462
    assert len({item["internal_id"] for item in items}) == len(items)
    assert len(build.bindings) == len(items)
    assert all(item["price_net"] for item in items)
    assert all(
        item["sku"]
        or (item["code_status"] == "needs_review" and item["attributes"]["source_code"])
        for item in items
    )
    assert all(item["image_kind"] == "official" for item in items)
    assert all(item["product_url"].startswith("https://") for item in items)
    assert sum(item["code_status"] == "needs_review" for item in items) == 8
    assert any(item["sku"] == "155-20400" and item["price_net"] == "2720.000000" for item in items)
    assert any(item["sku"] == "160-S1170" and item["price_net"] == "2305.000000" for item in items)

    by_sku = {item["sku"]: item for item in items if item["sku"]}
    for sku in ("110-52410", "110-52430", "110-52470", "110-52460"):
        assert [option["price_net"] for option in by_sku[sku]["base_price_options"]] == [
            "5275.000000",
            "5423.000000",
        ]
    assert by_sku["112-33200-000"]["price_net"] == "22590.000000"
    assert [
        (option["name"], option["price_net"])
        for option in by_sku["112-33200-000"]["base_price_options"]
    ] == [
        ("Grado 0", "22590.000000"),
        ("Grado A", "23082.000000"),
    ]
    assert [
        (option["name"], option["price_net"])
        for option in by_sku["112-75000"]["base_price_options"]
    ] == [
        ("Grado 0", "35189.000000"),
        ("Grado A", "36461.000000"),
        ("Piel", "42890.000000"),
    ]
    assert by_sku["112-33200-P00"]["price_net"] == "27093.000000"
    assert by_sku["112-33200-P00"]["base_price_options"] == []
    assert by_sku["1113-3430I"]["price_net"] == "3784.000000"
    assert by_sku["13-5900I"]["price_net"] == "3024.000000"
    for sku in ("106-00240-ROJ", "106-00250-ANT", "106-00260-NJA"):
        assert by_sku[sku]["price_net"] == "1835.000000"
        assert by_sku[sku]["attributes"]["source_page"] == 8

    assert [
        (option["name"], option["price_net"])
        for option in by_sku["160-08900"]["base_price_options"]
    ] == [
        ("Base de H.40 NGO - GRIS", "410.000000"),
        ("Base de H.40 BCO - DGY - ANT", "615.000000"),
    ]
    assert [
        (option["name"], option["price_net"])
        for option in by_sku["160-08950"]["base_price_options"]
    ] == [
        ("Base de H.45 NGO - GRIS", "465.000000"),
        ("Base de H.45 BCO - DGY - ANT", "700.000000"),
    ]
    assert [
        (option["name"], option["price_net"])
        for option in by_sku["107-00620"]["base_price_options"]
    ] == [
        ("Base Cromada", "3115.000000"),
        ("Base Colores especiales", "2865.000000"),
    ]

    curly = [
        item
        for item in items
        if item["attributes"]["source_code"] == "160-090XX"
        and item["name"].startswith("CURLY BASE")
    ]
    assert len(curly) == 1
    assert curly[0]["code_status"] == "needs_review"
    assert curly[0]["sku"] == ""
    assert [
        (option["name"], option["price_net"])
        for option in curly[0]["base_price_options"]
    ] == [
        ("Base de H.40 / Colores especiales", "505.000000"),
        ("Base de H.45 / Colores especiales", "590.000000"),
    ]

    cross_title = [
        item for item in items if item["attributes"]["source_code"] == "155-22700-000"
    ]
    assert len(cross_title) == 3
    assert len({item["name"].split(" — ")[0] for item in cross_title}) == 3
    assert all(item["code_status"] == "needs_review" for item in cross_title)
