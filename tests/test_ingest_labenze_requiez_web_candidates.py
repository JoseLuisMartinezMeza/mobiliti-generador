import importlib
import base64
import hashlib
import io
import json
import ssl
import subprocess
import sys
import zlib
from collections import Counter
from pathlib import Path

import pytest
import fitz
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_DIR = ROOT / ".mobiliti_dev_store/visual-remediation/inventory-20260819T073817Z"
RESEARCH_DIR = ROOT / ".mobiliti_dev_store/visual-remediation/research-v5-security-20260819T091241Z"
REVIEW_DIR = ROOT / ".mobiliti_dev_store/visual-remediation/review-20260819T143000Z"
LABENZE_REPORT = ROOT / ".superpowers/sdd/2026-08-18-labenze-requiez-visual-remediation/task-6b-labenze-web-candidates.json"
REQUIEZ_REPORT = ROOT / ".superpowers/sdd/2026-08-18-labenze-requiez-visual-remediation/task-6b-requiez-web-candidates.json"
LABENZE_PDF = Path(
    r"C:\Users\pepem\AppData\Local\Temp\mobiliti-catalog-discovery-20260818\LP Labenze B26.pdf"
)
REQUIEZ_PDF = Path(
    r"C:\Users\pepem\AppData\Local\Temp\mobiliti-catalog-discovery-20260818\Lista de precios A-26.pdf"
)
JUN_PLACEHOLDER = ROOT / ".mobiliti_dev_store/visual-remediation/manual-probes-20260819-jun-m/RE-1063M.jpg"


def test_cycle_1_loader_module_exists():
    """Rompe si desaparece el loader seguro que adapta los dos reportes web."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    assert callable(module.load_normalized_inputs)


@pytest.fixture(scope="module")
def intake():
    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    return module.load_normalized_inputs(
        inventory_dir=INVENTORY_DIR,
        research_dir=RESEARCH_DIR,
        review_dir=REVIEW_DIR,
        labenze_pdf=LABENZE_PDF,
        requiez_pdf=REQUIEZ_PDF,
        labenze_report_path=LABENZE_REPORT,
        requiez_report_path=REQUIEZ_REPORT,
        expected_labenze_report_sha256="c4b9fdef321ea3fea1ecdc028b85ebbaeaa2429275a9e40ce811ba0c301f897b",
        expected_requiez_report_sha256="c67b882bb825ac16a0a632879e5e0cbe6581238d28322fe0b668e027fd8d99ab",
    )


def test_cycle_1_canonical_loader_emits_exact_residual_set_and_preserves_empty_skus(intake):
    """Rompe si el adaptador pierde IDs residuales o eleva códigos ambiguos a SKU verificado."""

    assert len(intake.normalized_rows) == 656
    assert {row["internal_id"] for row in intake.normalized_rows} == {
        row["internal_id"]
        for row in intake.research_rows
        if row["status"] != "found_exact"
    }
    ambiguous = [
        row for row in intake.normalized_rows if row["canonical_identity"]["code_status"] == "needs_review"
    ]
    assert len(ambiguous) == 8
    assert all(row["canonical_identity"]["sku"] == "" for row in ambiguous)
    assert all(row["report_identity"]["query_sku"] for row in ambiguous)


@pytest.mark.parametrize(
    "raw,error",
    [
        ('{"schema_version":1,"schema_version":1}', "duplicada"),
        ('{"schema_version":NaN}', "NaN/Infinity"),
        (json.dumps({"schema_version": 1, "bad": "x" + chr(1)}), "control"),
        ("[" * 40 + "0" + "]" * 40, "profundidad"),
    ],
)
def test_cycle_1_strict_json_rejects_adversarial_documents(tmp_path, raw, error):
    """Rompe si el parser vuelve a aceptar JSON ambiguo o datos de control hostiles."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    path = tmp_path / "report.json"
    path.write_text(raw, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=error):
        module.load_strict_json(path, expected_sha256=digest)


def test_cycle_1_strict_json_rejects_wrong_physical_sha(tmp_path):
    """Rompe si el loader confía en hashes declarados dentro del propio reporte."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    path = tmp_path / "report.json"
    path.write_text('{"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 físico"):
        module.load_strict_json(path, expected_sha256="0" * 64)


def test_cycle_1_report_schema_summary_and_identity_drift_are_blocking(intake):
    """Rompe si campos desconocidos, resumen divergente o identidad alterada pasan el gate."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    report = json.loads(json.dumps(intake.report_payloads["requiez"]))
    report["summary"]["found_candidate"] += 1
    with pytest.raises(ValueError, match="summary"):
        module.normalize_report_payloads(
            intake.inventory_rows,
            intake.research_rows,
            {"labenze": intake.report_payloads["labenze"], "requiez": report},
            intake.report_hashes,
        )
    report = json.loads(json.dumps(intake.report_payloads["requiez"]))
    report["rows"][0]["unknown_field"] = True
    with pytest.raises(ValueError, match="desconocid"):
        module.normalize_report_payloads(
            intake.inventory_rows,
            intake.research_rows,
            {"labenze": intake.report_payloads["labenze"], "requiez": report},
            intake.report_hashes,
        )
    report = json.loads(json.dumps(intake.report_payloads["requiez"]))
    report["rows"][0]["name"] += " drift"
    with pytest.raises(ValueError, match="identidad/configuración"):
        module.normalize_report_payloads(
            intake.inventory_rows,
            intake.research_rows,
            {"labenze": intake.report_payloads["labenze"], "requiez": report},
            intake.report_hashes,
        )


def test_cycle_2_routes_real_rows_to_direct_document_or_none(intake):
    """Rompe si un PDF se descarga como imagen o un no-encontrado entra a la red."""

    counts = {
        kind: sum(row["acquisition_kind"] == kind for row in intake.normalized_rows)
        for kind in ("direct_image", "document_page", "none")
    }
    assert counts == {"direct_image": 235, "document_page": 94, "none": 327}
    documents = [row for row in intake.normalized_rows if row["acquisition_kind"] == "document_page"]
    assert all(row["candidate"]["image_source_url"] is None for row in documents)
    assert all("#" not in row["candidate"]["document_url"] for row in documents)
    assert all(row["candidate"]["page_number"] > 0 for row in documents)
    assert all(row["candidate"] is None for row in intake.normalized_rows if row["acquisition_kind"] == "none")


def test_cycle_2_direct_urls_are_distinct_and_shopify_variants_are_bound(intake):
    """Rompe si ficha/bytes se confunden o se pierde el enlace de variante exacta."""

    direct = [row["candidate"] for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image"]
    assert all(candidate["product_url"] != candidate["image_source_url"] for candidate in direct)
    variants = [
        row["candidate"]
        for row in intake.normalized_rows
        if row["supplier"] == "labenze"
        and row["acquisition_kind"] == "direct_image"
        and "variant=" in row["candidate"]["product_url"]
    ]
    assert len(variants) == 181
    # El reporte v5 contiene 181 asociaciones totales, no contradice las 75
    # correcciones del reporte previo: aquí se cuentan todas las filas asociadas.
    binding_counts = Counter(candidate["binding"] for candidate in variants)
    assert binding_counts == {
        "variant.featured_image": 71,
        "variant_sku_and_image_variant_ids": 110,
    }
    assert all(str(candidate["variant_id"]).isdigit() for candidate in variants)
    assert all(candidate["product_link_verified"] is True for candidate in variants)


def test_cycle_2_routing_validator_rejects_equal_direct_urls(intake):
    """Rompe si una página/PDF puede volver a colarse en el downloader de imágenes."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    row = json.loads(json.dumps(next(row for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image")))
    row["candidate"]["image_source_url"] = row["candidate"]["product_url"]
    with pytest.raises(ValueError, match="distintas"):
        module.validate_normalized_routing([row])


def test_cycle_2_request_plan_excludes_none_and_splits_documents(intake):
    """Rompe si un placeholder/no-indexed provoca una solicitud de red."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    plan = module.build_request_plan(intake.normalized_rows)
    assert len(plan["direct_images"]) == 235
    assert len(plan["document_pages"]) == 94
    assert {request["internal_id"] for request in plan["direct_images"]}.isdisjoint(
        row["internal_id"] for row in intake.normalized_rows if row["acquisition_kind"] == "none"
    )


def test_cycle_3_static_policy_accepts_nexus_and_only_four_exact_documents():
    """Rompe si falta una fuente aprobada o si una allowlist inyectada amplía la red."""

    research = importlib.import_module("scripts.research_labenze_requiez_images")
    assert research.validate_source_resource_url(
        "https://nexus-flex.com/products/cubierta-compact?variant=47220313325813",
        source_name="nexus-flex.com",
        resource_kind="product",
    )
    assert research.validate_source_resource_url(
        "https://cdn.shopify.com/s/files/1/0708/1132/0565/files/compact.jpg?v=1761023331",
        source_name="nexus-flex.com",
        resource_kind="image",
    )
    documents = {
        "tendence mobili / media.cylex.mx": "https://media.cylex.mx/companies/1203/5778/uploadedfiles/12035778_637885105004313129_SL_LABENZE_-_TENDENCE_MOBILI_-_SIN_PRECIO.pdf",
        "officenter.com.mx": "https://www.officenter.com.mx/wp-content/uploads/2019/01/LABENZE-CATALOGO-2018-BAJA-ilovepdf-compressed.pdf",
        "segomuebles.com": "https://www.segomuebles.com/archivos/labenze.pdf",
        "labenze / umbral-comex.labenze.com": "https://umbral-comex.labenze.com/Catalogo_Coleccion_Umbral_ComexLabenze.pdf",
    }
    for source_name, url in documents.items():
        assert research.validate_source_resource_url(
            url, source_name=source_name, resource_kind="document"
        )
        with pytest.raises(ValueError, match="permitid"):
            research.validate_source_resource_url(
                url.replace(".pdf", "-otro.pdf"),
                source_name=source_name,
                resource_kind="document",
            )
    with pytest.raises(ValueError, match="fragment"):
        research.validate_source_resource_url(
            documents["segomuebles.com"] + "#page=4",
            source_name="segomuebles.com",
            resource_kind="document",
        )
    with pytest.raises(ValueError, match="permitid"):
        research.validate_source_resource_url(
            "https://evil.example/products/cubierta-compact?variant=47220313325813",
            source_name="nexus-flex.com",
            resource_kind="product",
        )


class _Cycle3Response:
    status = 200
    headers = {"content-type": "application/octet-stream"}

    def __init__(self, body):
        self.body = body

    def read(self, limit):
        return self.body[:limit]


class _Cycle3Connection:
    peer_ip = "151.101.1.12"

    def __init__(self, body):
        self.response = _Cycle3Response(body)

    def request(self, method, target, *, headers):
        self.requested = (method, target, headers)

    def getresponse(self):
        return self.response

    def close(self):
        pass


def test_cycle_3_transport_enforces_per_request_response_limit():
    """Rompe si una respuesta puede superar el presupuesto antes de entrar al cache."""

    research = importlib.import_module("scripts.research_labenze_requiez_images")
    transport = research.UrllibTransport(
        resolver=lambda host: ["151.101.1.12"],
        connector=lambda host, ip, port, timeout, context: _Cycle3Connection(b"123456"),
        ssl_context=ssl.create_default_context(),
    )
    with pytest.raises(ValueError, match="límite|l.mite"):
        transport.fetch(
            "https://www.segomuebles.com/archivos/labenze.pdf",
            source_name="segomuebles.com",
            resource_kind="document",
            max_response_bytes=5,
        )


def _cycle_4_image_bytes(size=(640, 640), *, animated=False):
    stream = io.BytesIO()
    first = Image.new("RGB", size, "white")
    if animated:
        second = Image.new("RGB", size, "black")
        first.save(stream, format="WEBP", save_all=True, append_images=[second], duration=100, loop=0)
    else:
        first.save(stream, format="PNG")
    return stream.getvalue()


class _Cycle4Client:
    offline = True

    def __init__(self, research, *, image_body, image_mime="image/png", product_status=200, product_mime="text/html", product_final=None):
        self.research = research
        self.image_body = image_body
        self.image_mime = image_mime
        self.product_status = product_status
        self.product_mime = product_mime
        self.product_final = product_final
        self.calls = []

    def get(self, url, *, source_name, resource_kind, max_response_bytes):
        self.calls.append((url, source_name, resource_kind, max_response_bytes))
        if resource_kind == "product":
            return self.research.HttpResponse(
                self.product_status,
                self.product_final or url,
                {"content-type": self.product_mime},
                b"<!doctype html><title>Producto exacto</title>",
            )
        return self.research.HttpResponse(
            200,
            url,
            {"content-type": self.image_mime},
            self.image_body,
        )


def _cycle_4_direct_row(intake, dimensions):
    row = json.loads(json.dumps(next(row for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image")))
    row["candidate"]["evidence"]["dimensions"] = {"width": dimensions[0], "height": dimensions[1]}
    return row


def test_cycle_4_exact_page_and_image_create_content_addressed_pending_candidate(tmp_path, intake):
    """Rompe si un original válido se transforma, aprueba o pierde recibo/QA técnico."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    row = _cycle_4_direct_row(intake, (640, 640))
    client = _Cycle4Client(research, image_body=_cycle_4_image_bytes())
    receipts, candidates = module.acquire_direct_images([row], client, tmp_path / "originals")
    assert [call[2] for call in client.calls] == ["product", "image"]
    assert receipts[0]["status"] == "downloaded"
    assert receipts[0]["requested_url"] == row["candidate"]["image_source_url"]
    assert receipts[0]["final_url"] == row["candidate"]["image_source_url"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["review"]["approved"] is False
    assert set(candidate["review"]["checks"].values()) == {None}
    assert candidate["normalization_feasibility"]["informational_only"] is True
    assert candidate["normalized_asset_path"] is None
    original = tmp_path / candidate["original"]["path"]
    assert original.read_bytes() == client.image_body
    assert original.stem == hashlib.sha256(client.image_body).hexdigest()


def test_cycle_4_low_resolution_remains_fail_without_upscale(tmp_path, intake):
    """Rompe si una fuente menor de 512 px se reescala o obtiene PASS automático."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    body = _cycle_4_image_bytes((400, 400))
    row = _cycle_4_direct_row(intake, (400, 400))
    receipts, candidates = module.acquire_direct_images(
        [row], _Cycle4Client(research, image_body=body), tmp_path / "originals"
    )
    assert receipts[0]["status"] == "downloaded"
    assert candidates[0]["original"]["dimensions"] == {"width": 400, "height": 400}
    assert candidates[0]["automatic_gate"]["passed"] is False
    assert "source_shortest_side_below_512" in candidates[0]["automatic_gate"]["reasons"]


@pytest.mark.parametrize(
    "client_overrides,error",
    [
        ({"product_status": 404}, "product_page_http_404"),
        ({"product_mime": "application/json"}, "product_page_not_html"),
        ({"product_final": "https://nogalbeat.com/"}, "product_page_redirect_invalid"),
        ({"image_body": b"<html>no image</html>", "image_mime": "text/html"}, "MIME"),
    ],
)
def test_cycle_4_invalid_page_or_image_yields_terminal_receipt_without_candidate(
    tmp_path, intake, client_overrides, error
):
    """Rompe si home/404/HTML se acepta como evidencia visual o queda sin recibo."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    row = _cycle_4_direct_row(intake, (640, 640))
    defaults = {"image_body": _cycle_4_image_bytes()}
    defaults.update(client_overrides)
    receipts, candidates = module.acquire_direct_images(
        [row], _Cycle4Client(research, **defaults), tmp_path / "originals"
    )
    assert candidates == []
    assert receipts[0]["status"] == "rejected"
    assert error.casefold() in receipts[0]["reason"].casefold()


def test_cycle_4_metadata_change_and_animation_block_association_before_review(tmp_path, intake):
    """Rompe si metadata divergente o WEBP animado llega a revisión como candidato."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    row = _cycle_4_direct_row(intake, (999, 999))
    receipts, candidates = module.acquire_direct_images(
        [row],
        _Cycle4Client(research, image_body=_cycle_4_image_bytes((640, 640))),
        tmp_path / "metadata" / "originals",
    )
    assert candidates == []
    assert receipts[0]["reason"] == "declared_dimensions_mismatch"
    row = _cycle_4_direct_row(intake, (640, 640))
    receipts, candidates = module.acquire_direct_images(
        [row],
        _Cycle4Client(
            research,
            image_body=_cycle_4_image_bytes((640, 640), animated=True),
            image_mime="image/webp",
        ),
        tmp_path / "animated" / "originals",
    )
    assert candidates == []
    assert "animad" in receipts[0]["reason"].casefold()
    assert list((tmp_path / "animated" / "originals").glob("*")) == []


def _cycle_5_pdf_bytes(page_texts, *, active=False):
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_text((72, 72), text)
        if active:
            page.insert_link(
                {
                    "kind": fitz.LINK_URI,
                    "from": fitz.Rect(72, 80, 180, 100),
                    "uri": "https://example.com/active",
                }
            )
    data = document.tobytes()
    document.close()
    if active:
        data = data.replace(b"/URI", b"/JS ", 1)
    return data


class _Cycle5Client:
    offline = True

    def __init__(self, research, body, *, status=200, mime="application/pdf"):
        self.research = research
        self.body = body
        self.status = status
        self.mime = mime
        self.calls = []

    def get(self, url, *, source_name, resource_kind, max_response_bytes):
        self.calls.append((url, source_name, resource_kind, max_response_bytes))
        return self.research.HttpResponse(
            self.status,
            url,
            {"content-type": self.mime},
            self.body,
        )


def _cycle_5_document_row(intake, *, code="DOC-123", page=1):
    row = json.loads(
        json.dumps(
            next(
                row
                for row in intake.normalized_rows
                if row["acquisition_kind"] == "document_page"
                and row["candidate"]["source_name"] == "Tendence Mobili / media.cylex.mx"
            )
        )
    )
    row["canonical_identity"]["source_code"] = code
    row["candidate"]["page_number"] = page
    return row


def test_cycle_5_document_is_fetched_once_preflighted_and_queued_without_crop(tmp_path, intake):
    """Rompe si PDF+página se convierte en imagen de producto o se descarga por asociación."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    first = _cycle_5_document_row(intake)
    second = json.loads(json.dumps(first))
    second["internal_id"] += ":alias"
    second["report_candidate_id"] = "f" * 64
    client = _Cycle5Client(research, _cycle_5_pdf_bytes(["Catálogo DOC-123", "otra página"]))
    receipts, queue = module.acquire_document_pages(
        [first, second], client, tmp_path / "documents", tmp_path / "page-previews"
    )
    assert len(client.calls) == 1
    assert len(receipts) == len(queue) == 2
    assert {receipt["status"] for receipt in receipts} == {"document_page_ready"}
    assert all(item["bbox"] is None for item in queue)
    assert all(item["bbox_review"]["approved"] is False for item in queue)
    assert all(item["crop_path"] is None for item in queue)
    assert len(list((tmp_path / "documents").glob("*.pdf"))) == 1
    assert len(list((tmp_path / "page-previews").glob("*.png"))) == 1
    assert not (tmp_path / "originals").exists()


def test_cycle_5_document_cache_revalidates_every_source_association(tmp_path, intake):
    """Una URL cacheada no permite que otra source_name omita su política host+ruta."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    first = _cycle_5_document_row(intake)
    second = json.loads(json.dumps(first))
    second["internal_id"] += ":wrong-source"
    second["report_candidate_id"] = "9" * 64
    second["candidate"]["source_name"] = "officenter.com.mx"
    client = _Cycle5Client(research, _cycle_5_pdf_bytes(["Catálogo DOC-123"]))
    receipts, queue = module.acquire_document_pages(
        [first, second], client, tmp_path / "documents", tmp_path / "page-previews"
    )
    assert len(client.calls) == 1
    assert len(queue) == 1
    assert receipts[0]["status"] == "document_page_ready"
    assert receipts[1]["status"] == "document_fetch_failed"
    assert "no permit" in receipts[1]["reason"]


def test_cycle_5_canonical_document_audit_corrects_pages_and_blocks_16(intake):
    """Rompe si se usa el fragmento zero-based o una incompatibilidad semántica entra a bbox."""

    documents = [row for row in intake.normalized_rows if row["acquisition_kind"] == "document_page"]
    corrected = [
        row
        for row in documents
        if row["candidate"]["page_number"] == row["candidate"]["reported_page_number"] + 1
    ]
    unchanged = [
        row
        for row in documents
        if row["candidate"]["page_number"] == row["candidate"]["reported_page_number"]
    ]
    assert len(documents) == 94
    assert len(corrected) == 93
    assert len(unchanged) == 1
    assert unchanged[0]["canonical_identity"]["source_code"] == "155-22900-BAS"
    dispositions = {
        value: sum(row["candidate"]["document_disposition"] == value for row in documents)
        for value in ("document_bbox_review", "document_semantic_blocked")
    }
    assert dispositions == {"document_bbox_review": 78, "document_semantic_blocked": 16}


@pytest.mark.parametrize(
    "status,mime,body,expected",
    [
        (403, "text/html", b"forbidden", "document_fetch_failed"),
        (200, "text/html", b"%PDF-fake", "document_mime_invalid"),
        (200, "application/pdf", b"<html>fake</html>", "document_magic_invalid"),
    ],
)
def test_cycle_5_failed_document_fetch_is_honest_and_writes_no_asset(
    tmp_path, intake, status, mime, body, expected
):
    """Rompe si Cylex/HTML disfrazado produce documento, preview o candidato."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    row = _cycle_5_document_row(intake)
    receipts, queue = module.acquire_document_pages(
        [row],
        _Cycle5Client(research, body, status=status, mime=mime),
        tmp_path / "documents",
        tmp_path / "page-previews",
    )
    assert queue == []
    assert receipts[0]["status"] == expected
    assert list((tmp_path / "documents").glob("*")) == []
    assert list((tmp_path / "page-previews").glob("*")) == []


def test_cycle_5_unsafe_pdf_invalid_page_or_missing_code_never_creates_crop(tmp_path, intake):
    """Rompe si un PDF activo o una página sin identidad llega a la subcola bbox."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    unsafe = _cycle_5_document_row(intake)
    receipts, queue = module.acquire_document_pages(
        [unsafe],
        _Cycle5Client(research, _cycle_5_pdf_bytes(["DOC-123"], active=True)),
        tmp_path / "unsafe-documents",
        tmp_path / "unsafe-previews",
    )
    assert queue == []
    assert receipts[0]["status"] == "document_preflight_failed"
    invalid_page = _cycle_5_document_row(intake, page=3)
    missing_code = _cycle_5_document_row(intake, code="MISSING")
    receipts, queue = module.acquire_document_pages(
        [invalid_page, missing_code],
        _Cycle5Client(research, _cycle_5_pdf_bytes(["DOC-123"])),
        tmp_path / "identity-documents",
        tmp_path / "identity-previews",
    )
    assert queue == []
    assert {receipt["status"] for receipt in receipts} == {"document_identity_unverified"}
    assert all(receipt["crop_path"] is None for receipt in receipts)


def test_cycle_5_comex_expansion_override_is_local_and_exact_hash_pinned(monkeypatch):
    """Sólo el Comex auditado puede usar 384 MiB; otro byte falla cerrado."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    url = "https://umbral-comex.labenze.com/Catalogo_Coleccion_Umbral_ComexLabenze.pdf"
    profile = module.DOCUMENT_PREFLIGHT_PROFILES[url]
    assert profile == {
        "sha256": "f7f63160281cbd087dea8bbcd723872a076f0a78da89def0d5360b90359f6fcb",
        "page_count": 19,
        "max_stream_expanded_bytes": 384 * 1024 * 1024,
    }
    body = b"%PDF-1.7 profile-test"
    monkeypatch.setitem(
        module.DOCUMENT_PREFLIGHT_PROFILES,
        url,
        {
            "sha256": hashlib.sha256(body).hexdigest(),
            "page_count": 1,
            "max_stream_expanded_bytes": 384 * 1024 * 1024,
        },
    )
    called = {}

    def fake_pages(data, **kwargs):
        called.update(kwargs)
        return (object(),)

    monkeypatch.setattr(module, "_pdf_pages", fake_pages)
    digest, pages = module._preflight_document(url, body)
    assert digest == hashlib.sha256(body).hexdigest()
    assert len(pages) == 1
    assert called == {"max_stream_expanded_bytes": 384 * 1024 * 1024}
    with pytest.raises(ValueError, match="document_hash_mismatch"):
        module._preflight_document(url, body + b"changed")


def test_cycle_5_segomuebles_profile_is_hash_pinned_and_bounded():
    """El PDF Sego auditado admite sólo ASCII85→Flate acotado en su perfil local."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    url = "https://www.segomuebles.com/archivos/labenze.pdf"
    profile = module.DOCUMENT_PREFLIGHT_PROFILES[url]
    assert profile == {
        "sha256": "6fbef668374ea03aa06bda01abf1e681b5e2da8e3decf1d311225d7139a3390c",
        "page_count": 39,
        "max_stream_expanded_bytes": 384 * 1024 * 1024,
        "max_stream_ratio": 256,
        "ascii85_flate_only": True,
    }
    cache_entry = json.loads(
        (
            ROOT
            / ".mobiliti_dev_store/visual-remediation/web-intake-20260819T120000Z/http-cache/4fd97c168a54cda34f7c30ff899a4db99233a4988d0ee4bff1e3e656d8731b6c.json"
        ).read_text(encoding="utf-8")
    )
    body = base64.b64decode(cache_entry["body_base64"], validate=True)
    digest, pages = module._preflight_document(url, body)
    assert digest == profile["sha256"]
    assert len(pages) == 39
    assert "16007055" in module._normalized_document_text(pages[6].text)
    compressed = zlib.compress(b"A" * 7_140)
    encoded = base64.a85encode(compressed) + b"~>"
    assert module._pdf_ascii85_flate_size(encoded, max_stream_ratio=256) == 7_140
    with pytest.raises(module.SourceSafetyError, match="PDF_LIMIT"):
        module._pdf_ascii85_flate_size(encoded, max_stream_ratio=1)
    with pytest.raises(ValueError, match="document_hash_mismatch"):
        module._preflight_document(url, body + b"changed")


def test_cycle_6_jun_placeholder_hash_is_blocked_even_when_flag_is_false(tmp_path, intake):
    """Rompe si el placeholder JUN conocido reaparece sin flag y entra a originals/review."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    row = _cycle_4_direct_row(intake, (360, 363))
    row["candidate"]["evidence"]["placeholder_source_image"] = False
    receipts, candidates = module.acquire_direct_images(
        [row],
        _Cycle4Client(
            research,
            image_body=JUN_PLACEHOLDER.read_bytes(),
            image_mime="image/jpeg",
        ),
        tmp_path / "originals",
    )
    assert candidates == []
    assert receipts[0]["reason"] == "known_placeholder_sha256"
    assert list((tmp_path / "originals").glob("*")) == []


def test_cycle_6_repeated_product_and_image_urls_are_fetched_once_with_fanout(tmp_path, intake):
    """Rompe si dos asociaciones idénticas disparan dos GET de la misma URL."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    first = _cycle_4_direct_row(intake, (640, 640))
    second = json.loads(json.dumps(first))
    second["internal_id"] += ":alias"
    second["canonical_identity"]["product_key"] += ":alias"
    second["report_candidate_id"] = "e" * 64
    client = _Cycle4Client(research, image_body=_cycle_4_image_bytes())
    receipts, candidates = module.acquire_direct_images(
        [first, second], client, tmp_path / "originals"
    )
    assert len(receipts) == len(candidates) == 2
    assert [call[2] for call in client.calls].count("product") == 1
    assert [call[2] for call in client.calls].count("image") == 1
    assert len({candidate["candidate_id"] for candidate in candidates}) == 2


def test_cycle_6_direct_cache_revalidates_every_source_association(tmp_path, intake):
    """Deduplicar bytes no autoriza reutilizar una URL bajo una fuente incompatible."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    first = _cycle_4_direct_row(intake, (640, 640))
    second = json.loads(json.dumps(first))
    second["internal_id"] += ":wrong-source"
    second["canonical_identity"]["product_key"] += ":wrong-source"
    second["report_candidate_id"] = "8" * 64
    second["candidate"]["source_name"] = "3rin.com.mx"
    client = _Cycle4Client(research, image_body=_cycle_4_image_bytes())
    receipts, candidates = module.acquire_direct_images(
        [first, second], client, tmp_path / "originals"
    )
    assert [call[2] for call in client.calls].count("product") == 1
    assert [call[2] for call in client.calls].count("image") == 1
    assert len(candidates) == 1
    assert receipts[0]["status"] == "downloaded"
    assert receipts[1]["status"] == "rejected"
    assert "no permit" in receipts[1]["reason"]


def test_cycle_6_duplicate_conflicts_and_same_signature_reuse_are_blocked(tmp_path, intake):
    """Todo SHA+firma compartido es potencial reuse bloqueado; nunca aprobación."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    first = _cycle_4_direct_row(intake, (640, 640))
    second = json.loads(json.dumps(first))
    second["internal_id"] += ":other"
    second["canonical_identity"]["product_key"] += ":other"
    second["canonical_identity"]["visual_signature_sha256"] = "b" * 64
    second["report_candidate_id"] = "d" * 64
    _, candidates = module.acquire_direct_images(
        [first, second],
        _Cycle4Client(research, image_body=_cycle_4_image_bytes()),
        tmp_path / "originals",
    )
    clusters = module.analyze_duplicate_clusters(candidates, tmp_path / "originals")
    exact = clusters["exact"][0]
    assert exact["duplicate_conflict"] is True
    assert exact["potential_shared_visual"] is False
    assert all(candidate["global_gate_blocked"] is True for candidate in candidates)
    same_signature = json.loads(json.dumps(candidates))
    for candidate in same_signature:
        candidate["visual_signature"]["sha256"] = "c" * 64
        candidate["global_gate_blocked"] = False
    exact = module.analyze_duplicate_clusters(
        same_signature, tmp_path / "originals"
    )["exact"][0]
    assert exact["duplicate_conflict"] is False
    assert exact["potential_shared_visual"] is True
    assert all(candidate["global_gate_blocked"] is True for candidate in same_signature)
    atana = json.loads(json.dumps(candidates))
    for candidate, internal_id in zip(atana, ("labenze:160-0910p", "labenze:160-0910p-ngo")):
        candidate["internal_id"] = internal_id
        candidate["visual_signature"]["sha256"] = "a" * 64
    exact = module.analyze_duplicate_clusters(atana, tmp_path / "originals")["exact"][0]
    assert exact["duplicate_conflict"] is False
    assert exact["potential_shared_visual"] is True
    assert exact["shared_visual_group"] is None


def test_cycle_6_perceptual_references_compare_task6c_6a_and_active_assets(tmp_path):
    """dHash referencia los tres lotes con efecto sólo informativo y SHA verificado."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    originals = tmp_path / "task6c-originals"
    research_dir = tmp_path / "research"
    assets = tmp_path / "assets"
    (research_dir / "originals").mkdir(parents=True)
    originals.mkdir()
    assets.mkdir()

    def write_image(path, color):
        stream = io.BytesIO()
        Image.new("RGB", (640, 640), color).save(stream, format="PNG")
        body = stream.getvalue()
        path.write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    task6c_sha = write_image(originals / "task6c.png", "white")
    task6a_sha = write_image(research_dir / "originals" / "task6a.png", "gray")
    active_sha = write_image(assets / "active.png", "black")
    candidates = [
        {
            "internal_id": "labenze:new",
            "original": {"object_name": "task6c.png", "sha256": task6c_sha},
            "visual_signature": {"sha256": "1" * 64},
        }
    ]
    loaded = type(
        "Loaded",
        (),
        {
            "review_candidate_rows": [
                {
                    "internal_id": "labenze:6a",
                    "original": {
                        "path": "originals/task6a.png",
                        "sha256": task6a_sha,
                    },
                    "visual_signature": {"sha256": "2" * 64},
                }
            ],
            "inventory_rows": [
                {
                    "internal_id": "labenze:active",
                    "current_asset": {
                        "path": "active.png",
                        "actual_sha256": active_sha,
                    },
                    "visual_signature": {"sha256": "3" * 64},
                }
            ],
        },
    )()
    clusters = module._reference_perceptual_clusters(
        candidates,
        loaded,
        originals_dir=originals,
        research_dir=research_dir,
        assets_dir=assets,
    )
    assert len(clusters) == 1
    assert clusters[0]["internal_ids"] == ["labenze:6a", "labenze:active", "labenze:new"]
    assert clusters[0]["batches"] == ["task5_active", "task6a", "task6c"]
    assert clusters[0]["decision_effect"] == "inspection_only"
    assert "path" not in json.dumps(clusters[0])

    candidates[0]["original"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA"):
        module._reference_perceptual_clusters(
            candidates,
            loaded,
            originals_dir=originals,
            research_dir=research_dir,
            assets_dir=assets,
        )


def test_cycle_6_contact_sheet_uses_contain_and_labels_outside_image(tmp_path, intake):
    """Rompe si la lámina vuelve a recortar el producto o superpone etiquetas."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")
    row = _cycle_4_direct_row(intake, (640, 640))
    _, candidates = module.acquire_direct_images(
        [row], _Cycle4Client(research, image_body=_cycle_4_image_bytes()), tmp_path / "originals"
    )
    sheets, index = module.render_candidate_contact_sheets(
        tmp_path, candidates, tmp_path / "originals"
    )
    tile = index["tiles"][0]
    assert tile["fit"] == "contain"
    assert tile["image_bbox"][3] <= tile["image_area_bbox"][3]
    assert all(label["bbox"][1] >= tile["image_area_bbox"][3] for label in tile["labels"])
    assert sheets["sheets"][0]["candidate_count"] == 1


def test_cycle_7_overlay_keeps_776_and_all_task6b_technical_fails(intake):
    """Rompe si el overlay reemplaza 6B, pierde sus FAIL o duplica una identidad."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    queue = module.build_global_search_queue(intake, [], [], [])
    assert len(queue) == 776
    assert len({row["internal_id"] for row in queue}) == 776
    failed_ids = {
        row["internal_id"]
        for row in intake.review_candidate_rows
        if row["automatic_gate"]["passed"] is False
    }
    assert len(failed_ids) == 12
    assert sum(internal_id.startswith("labenze:") for internal_id in failed_ids) == 11
    preserved = {row["internal_id"] for row in queue if row["task6b_technical_fail_preserved"]}
    assert preserved == failed_ids


def test_cycle_7_logical_hash_ignores_operational_timestamps_but_not_decisions():
    """Rompe si online/replay difieren por reloj o si una decisión deja de afectar el hash."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    first = {
        "normalized": [{"internal_id": "x"}],
        "receipts": [{"status": "ok", "fetched_at": "2026-08-19T01:00:00Z"}],
        "decisions": {"decisions": []},
    }
    second = json.loads(json.dumps(first))
    second["receipts"][0]["fetched_at"] = "2026-08-20T02:00:00Z"
    assert module.logical_intake_sha256(first) == module.logical_intake_sha256(second)
    second["decisions"]["decisions"].append({"internal_id": "x", "approved": False})
    assert module.logical_intake_sha256(first) != module.logical_intake_sha256(second)


def test_cycle_7_original_reconciliation_rejects_extra_and_approval_gate_rejects_true(tmp_path):
    """Rompe si un archivo no declarado o approved=true puede cerrar la corrida."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    originals = tmp_path / "originals"
    originals.mkdir()
    (originals / ("a" * 64 + ".png")).write_bytes(_cycle_4_image_bytes())
    with pytest.raises(ValueError, match="adicional"):
        module.validate_declared_originals(originals, [], [])
    with pytest.raises(ValueError, match="approved"):
        module.validate_no_approvals(
            [{"review": {"approved": True, "reviewer": "x", "reviewed_at": "now", "checks": {}}}]
        )
    for key in ("review", "bbox_review"):
        with pytest.raises(ValueError, match="checks"):
            module.validate_no_approvals(
                [
                    {
                        key: {
                            "approved": False,
                            "reviewer": "",
                            "reviewed_at": None,
                            "checks": {"identity_exact": True},
                        }
                    }
                ]
            )


def test_cycle_7_existing_output_fails_before_loading_or_network(tmp_path):
    """Rompe si una corrida puede sobreescribir un output anterior."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="ya existe"):
        module.validate_new_output(output, [tmp_path / "protected"])


def test_cycle_7_cache_source_must_not_overlap_output(tmp_path):
    """Rompe si el replay puede autocopiar output/http-cache dentro de su fuente."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    source = tmp_path / "source"
    (source / "http-cache").mkdir(parents=True)
    output_inside_source = source / "nested-output"
    with pytest.raises(ValueError, match="cache.*solapa"):
        module.validate_cache_isolation(output_inside_source, source)
    with pytest.raises(ValueError, match="cache.*solapa"):
        module.validate_cache_isolation(source, source)

    external = tmp_path / "external-output"
    assert module.validate_cache_isolation(external, source) == (source / "http-cache").resolve()


def test_cycle_7_run_checks_existing_output_before_any_input_or_network(tmp_path):
    """Rompe si el orquestador toca entradas/red antes del gate write-once."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    output = tmp_path / "existing-run"
    output.mkdir()
    missing = tmp_path / "must-not-be-read"
    with pytest.raises(ValueError, match="ya existe"):
        module.run_intake(
            inventory_dir=missing,
            research_dir=missing,
            review_dir=missing,
            labenze_pdf=missing,
            requiez_pdf=missing,
            store_path=missing,
            assets_dir=missing,
            labenze_report_path=missing,
            requiez_report_path=missing,
            document_audit_path=missing,
            output_dir=output,
            expected_labenze_report_sha256="0" * 64,
            expected_requiez_report_sha256="0" * 64,
        )


def test_cycle_7_cli_guard_runs_only_after_all_definitions():
    """Evita ejecutar el orquestador antes de que existan todos sus helpers."""

    source = (ROOT / "scripts/ingest_labenze_requiez_web_candidates.py").read_text(
        encoding="utf-8"
    )
    assert source.rfind('if __name__ == "__main__":') > source.index(
        "def load_normalized_inputs("
    )


def test_cycle_7_product_probe_uses_static_allowlist(monkeypatch):
    """La allowlist del probe no puede derivarse del hostname entregado por el reporte."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    captured = {}

    def reject_before_network(candidate, *, allowed_product_hosts, allowed_image_hosts):
        captured["product_hosts"] = set(allowed_product_hosts)
        raise ValueError("stop")

    monkeypatch.setattr(module, "validate_candidate_urls", reject_before_network)
    candidate = {
        "source_name": "nexus-flex.com",
        "source_kind": "authorized_distributor",
        "source_id": "nexus-flex.com",
        "binding": "variant_sku_and_image_variant_ids",
        "product_url": "https://nexus-flex.com/products/falso?variant=1",
        "image_source_url": "https://cdn.shopify.com/s/files/1/falso.jpg?v=1",
        "evidence": {},
    }
    with pytest.raises(ValueError, match="stop"):
        module._probe_product_page(candidate, object())
    assert captured["product_hosts"] == module.PRODUCT_PAGE_HOSTS


@pytest.mark.parametrize(
    ("margins", "bbox", "occupancy", "dimensions", "expected"),
    [
        ({"left": 0.04, "top": 0.05, "right": 0.04, "bottom": 0.05},
         {"left": 48, "top": 60, "width": 1104, "height": 1080}, 0.50,
         {"width": 1200, "height": 1200}, (True, True, True, True)),
        ({"left": 0.03, "top": 0.05, "right": 0.04, "bottom": 0.05},
         {"left": 36, "top": 60, "width": 1116, "height": 1080}, 0.50,
         {"width": 1200, "height": 1200}, (False, False, True, False)),
        ({"left": 0.05, "top": 0.05, "right": 0.05, "bottom": 0.05},
         {"left": 60, "top": 60, "width": 1080, "height": 1080}, 0.81,
         {"width": 1200, "height": 1200}, (True, True, False, False)),
        ({"left": 0.05, "top": 0.05, "right": 0.05, "bottom": 0.05},
         {"left": 40, "top": 40, "width": 720, "height": 720}, 0.50,
         {"width": 800, "height": 800}, (True, True, True, False)),
    ],
)
def test_cycle_7_normalization_feasibility_is_calculated_not_constant(
    intake, margins, bbox, occupancy, dimensions, expected
):
    """El informe calcula cada contrato y nunca considera viable un upscale."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    row = next(row for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image")
    download = type(
        "Download",
        (),
        {
            "sha256": "a" * 64,
            "dimensions": dimensions,
            "path": Path("original.png"),
            "bytes": 123,
            "mime": "image/png",
        },
    )()
    metrics = {
        "mode": "RGBA",
        "min_dimension": min(dimensions.values()),
        "max_dimension": max(dimensions.values()),
        "aspect_ratio": dimensions["width"] / dimensions["height"],
        "has_alpha": True,
        "foreground_bbox": bbox,
        "occupancy": occupancy,
        "margins": margins,
        "automatic_gate": {"passed": True, "reasons": []},
    }
    feasibility = module._candidate_review_row(row, download, metrics)[
        "normalization_feasibility"
    ]
    assert (
        feasibility["contract"]["margin_4pct_plus"],
        feasibility["contract"]["bbox_92pct_or_less"],
        feasibility["contract"]["occupancy_12_to_80pct"],
        feasibility["could_meet_contain_contract_without_semantic_edit"],
    ) == expected


@pytest.mark.parametrize(
    "target",
    ["research_context", "input_hashes", "identity_field_policy"],
)
def test_cycle_1_report_schema_rejects_unknown_nested_fields(intake, target):
    """Las allowlists/metadatos declarados nunca amplían el contrato cerrado."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    payloads = json.loads(json.dumps(intake.report_payloads))
    if target == "research_context":
        payloads["labenze"]["research_context"]["allowlist_inyectada"] = ["evil.example"]
    elif target == "input_hashes":
        payloads["labenze"]["input_hashes"]["candidates_jsonl"]["host_allowlist"] = [
            "evil.example"
        ]
    else:
        payloads["labenze"]["research_context"]["identity_field_policy"][
            "override"
        ] = True
    with pytest.raises(ValueError, match="campos desconocidos"):
        module.normalize_report_payloads(
            intake.inventory_rows,
            intake.research_rows,
            payloads,
            intake.report_hashes,
        )


def test_cycle_7_direct_cli_starts_with_project_imports_available():
    """La CLI real desde scripts/ debe poder resolver el paquete del proyecto."""

    result = subprocess.run(
        [sys.executable, "scripts/ingest_labenze_requiez_web_candidates.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "--inventory-dir" in result.stdout


def test_cycle_2_routing_rejects_unknown_direct_source_kind(intake):
    """Un source_kind re-firmado no puede avanzar hacia la red."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    row = json.loads(
        json.dumps(next(row for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image"))
    )
    row["candidate"]["source_kind"] = "evil"
    with pytest.raises(ValueError, match="source_kind"):
        module.validate_normalized_routing([row])


def test_cycle_2_shopify_variant_bindings_require_exact_variant_query(intake):
    """Un binding de variante nunca puede degradarse silenciosamente a ficha genérica."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    row = json.loads(
        json.dumps(
            next(
                row
                for row in intake.normalized_rows
                if row["acquisition_kind"] == "direct_image"
                and row["candidate"]["binding"]
                in {"variant_sku_and_image_variant_ids", "variant.featured_image"}
            )
        )
    )
    row["candidate"]["product_url"] = row["candidate"]["product_url"].split("?", 1)[0]
    with pytest.raises(ValueError, match="product_link_unverified"):
        module.validate_normalized_routing([row])

    requiez = json.loads(
        json.dumps(
            next(
                row
                for row in intake.normalized_rows
                if row["supplier"] == "requiez"
                and row["acquisition_kind"] == "direct_image"
                and row["candidate"].get("variant_id")
            )
        )
    )
    requiez["candidate"]["product_url"] = requiez["candidate"]["product_url"].split("?", 1)[0]
    with pytest.raises(ValueError, match="product_link_unverified"):
        module.validate_normalized_routing([requiez])

    non_variants = [
        row
        for row in intake.normalized_rows
        if row["acquisition_kind"] == "direct_image"
        and "variant=" not in row["candidate"]["product_url"]
    ]
    assert len(non_variants) == 6
    assert all(row["candidate"].get("variant_id") is None for row in non_variants)
    assert all(row["candidate"].get("product_link_verified") is False for row in non_variants)
    module.validate_normalized_routing(non_variants)


@pytest.mark.parametrize("error", [OSError("socket down"), TimeoutError("timed out")])
def test_cycle_4_expected_transport_errors_become_terminal_direct_receipts(
    tmp_path, intake, error
):
    """Un fallo de red esperado no aborta ni oculta un bug de programación."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")

    class FailingClient:
        offline = False

        def get(self, *args, **kwargs):
            raise error

    row = next(row for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image")
    receipts, candidates = module.acquire_direct_images(
        [row], FailingClient(), tmp_path / "originals"
    )
    assert candidates == []
    assert receipts[0]["status"] == "rejected"
    assert receipts[0]["reason"] == "transport_error"


@pytest.mark.parametrize("failure", ["dns", "offline_miss", "http_exhausted"])
def test_cycle_4_real_http_client_expected_failures_become_terminal_receipts(
    tmp_path, intake, failure
):
    """Ejercita DNS/cache/reintentos reales sin convertir fallos de política en transporte."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")
    research = importlib.import_module("scripts.research_labenze_requiez_images")

    if failure == "dns":
        def resolver(_host):
            raise OSError("dns down")

        transport = research.UrllibTransport(resolver=resolver)
        offline = False
    elif failure == "offline_miss":
        transport = lambda _url: None
        offline = True
    else:
        class ExhaustedTransport:
            def fetch(self, url, **_kwargs):
                return research.HttpResponse(503, url, {}, b"")

        transport = ExhaustedTransport()
        offline = False

    client = research.CachedHttpClient(
        tmp_path / f"cache-{failure}",
        transport=transport,
        offline=offline,
        allowed_hosts=research.SOURCE_HTTP_HOSTS,
        max_attempts=1,
    )
    row = next(row for row in intake.normalized_rows if row["acquisition_kind"] == "direct_image")
    receipts, candidates = module.acquire_direct_images(
        [row], client, tmp_path / f"originals-{failure}"
    )
    assert candidates == []
    assert receipts[0]["status"] == "rejected"
    assert receipts[0]["reason"] == "transport_error"


@pytest.mark.parametrize(
    "error", [OSError("tls failure"), TimeoutError("timed out"), ValueError("Falta respuesta en cache offline: x")]
)
def test_cycle_5_expected_transport_errors_become_replay_stable_document_receipts(
    tmp_path, intake, error
):
    """Documento inaccesible queda auditable con la misma razón online/offline."""

    module = importlib.import_module("scripts.ingest_labenze_requiez_web_candidates")

    class FailingClient:
        offline = isinstance(error, ValueError)

        def get(self, *args, **kwargs):
            raise error

    row = next(
        row
        for row in intake.normalized_rows
        if row["acquisition_kind"] == "document_page"
        and row["candidate"]["document_disposition"] == "document_bbox_review"
    )
    receipts, queue = module.acquire_document_pages(
        [row], FailingClient(), tmp_path / "documents", tmp_path / "previews"
    )
    assert queue == []
    assert receipts[0]["status"] == "document_fetch_failed"
    assert receipts[0]["reason"] == "transport_error"
