"""Ingiere evidencia web Labenze/Requiez sin aprobar ni mutar el catálogo."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import http.client
import json
import math
import re
import stat
import sys
import unicodedata
import zlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import fitz
from PIL import Image

try:
    from mobiliti_saas.worker.catalog_sync.importers import common as _pdf_common
    from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError, _pdf_pages
except ModuleNotFoundError as exc:  # ``python scripts/<archivo>.py`` no incluye la raíz.
    if exc.name != "mobiliti_saas":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mobiliti_saas.worker.catalog_sync.importers import common as _pdf_common
    from mobiliti_saas.worker.catalog_sync.importers.common import SourceSafetyError, _pdf_pages

try:
    from scripts.research_labenze_requiez_images import (
        IMAGE_HOSTS,
        MAX_ORIGINAL_BYTES,
        PRODUCT_PAGE_HOSTS,
        SOURCE_HTTP_HOSTS,
        CachedHttpClient,
        HttpResponse,
        ResearchCandidate,
        UrllibTransport,
        _canonical_source_name,
        _copy_cache,
        download_original,
        validate_candidate_source_policy,
        validate_candidate_urls,
        validate_source_resource_url,
    )
except ModuleNotFoundError as exc:  # Permite ejecutar el script desde scripts/.
    if not str(exc.name or "").startswith("scripts"):
        raise
    from research_labenze_requiez_images import (  # type: ignore[no-redef]
        IMAGE_HOSTS,
        MAX_ORIGINAL_BYTES,
        PRODUCT_PAGE_HOSTS,
        SOURCE_HTTP_HOSTS,
        CachedHttpClient,
        HttpResponse,
        ResearchCandidate,
        UrllibTransport,
        _canonical_source_name,
        _copy_cache,
        download_original,
        validate_candidate_source_policy,
        validate_candidate_urls,
        validate_source_resource_url,
    )

try:
    from scripts.review_labenze_requiez_image_candidates import (
        _load_json,
        _load_jsonl,
        _configuration_text,
        _csv_cell,
        _render_contact_sheets,
        _tree_fingerprint,
        _write_csv,
        _write_json,
        _write_jsonl,
        build_candidate_id,
        inspect_original,
        _validate_artifact_manifest,
        _validate_inventory,
        _validate_research,
    )
except ModuleNotFoundError as exc:  # Permite ejecutar el script desde scripts/.
    if not str(exc.name or "").startswith("scripts"):
        raise
    from review_labenze_requiez_image_candidates import (  # type: ignore[no-redef]
        _load_json,
        _load_jsonl,
        _configuration_text,
        _csv_cell,
        _render_contact_sheets,
        _tree_fingerprint,
        _write_csv,
        _write_json,
        _write_jsonl,
        build_candidate_id,
        inspect_original,
        _validate_artifact_manifest,
        _validate_inventory,
        _validate_research,
    )


CANONICAL_INVENTORY_SHA256 = "476013bf863552d4e622f510c39a019fc1549859714edbd1e8b76994d31a0812"
CANONICAL_RESEARCH_JSONL_SHA256 = "1282b014fc08aac67ffe46ab3fad51d72030387065e8499d288d69ccbc24c06c"
CANONICAL_RESEARCH_LOGICAL_SHA256 = "7bf193a76086c610212eb0ad4f724c149d46e491f51bc4b6c83c2166fe0165f2"
CANONICAL_REVIEW_LOGICAL_SHA256 = "9bdfc8a05e70c841b7773cd567d8f489339243cc920daafc6ad590e3a62674ad"
CANONICAL_PDF_SHA256 = {
    "labenze": "c4fc2d2152b5e854f7c36c9106c71cd21853abb50efcde96ba2566cb72f1d6f3",
    "requiez": "7f3281d1965c67a234bac55112800067019ad471f835de59ff758e759eca56ba",
}
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
DOCUMENT_PREFLIGHT_PROFILES = {
    "https://www.segomuebles.com/archivos/labenze.pdf": {
        "sha256": "6fbef668374ea03aa06bda01abf1e681b5e2da8e3decf1d311225d7139a3390c",
        "page_count": 39,
        "max_stream_expanded_bytes": 384 * 1024 * 1024,
        "max_stream_ratio": 256,
        "ascii85_flate_only": True,
    },
    "https://umbral-comex.labenze.com/Catalogo_Coleccion_Umbral_ComexLabenze.pdf": {
        "sha256": "f7f63160281cbd087dea8bbcd723872a076f0a78da89def0d5360b90359f6fcb",
        "page_count": 19,
        "max_stream_expanded_bytes": 384 * 1024 * 1024,
    }
}
JUN_PLACEHOLDER_SHA256 = "7d2a5ffae5940a5b6ab0386ed982e47dd0465b89823e4ea84d28c3b0ed909424"
SEMANTIC_CONFLICT_IDS = {
    "requiez:ra-06",
    "requiez:ra-11",
    "requiez:ra-20",
    "requiez:rm-9101-gr",
}
ATANA_SHARED_IDS = frozenset({"labenze:160-0910p", "labenze:160-0910p-ngo"})
DOCUMENT_AUDIT_SHA256 = "dbb9d13ad51f1107bc2f716b9b011be881b22a6b6e3e04363bfb34658d5aad02"
ZERO_BASED_DOCUMENT_SOURCES = {
    "Tendence Mobili / media.cylex.mx",
    "officenter.com.mx",
    "segomuebles.com",
}
DOCUMENT_BLOCK_REASONS = {
    "108-02004": "visual_3_places_but_identity_4",
    "108-2003M": "visual_without_required_table",
    "108-2004M": "visual_without_required_table",
    "108-02102": "visual_3_places_but_identity_2",
    "108-02104": "visual_3_places_but_identity_4",
    "108-2103M": "visual_without_required_table",
    "108-2104M": "visual_without_required_table",
    "108-02202": "visual_3_places_but_identity_2",
    "108-02204": "visual_3_places_but_identity_4",
    "155-19025-000": "family_scene_not_isolated_product",
    "155-19025-NGO": "family_scene_wrong_base_not_isolated",
    "155-10950-000": "historical_code_reused_puf_prestige_not_nora",
    "155-14050-000": "puf_queen_not_high_back_armchair",
    "155-10600-TAP": "component_only_missing_required_base",
    "108-06002": "visual_3_places_but_identity_2",
    "108-06202": "visual_3_places_but_identity_2",
}
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 24
DIRECT_SOURCE_KINDS = {"manufacturer_official", "authorized_distributor", "third_party_exact"}

_TOP_KEYS = {
    "schema_version",
    "supplier",
    "researched_at",
    "input_hashes",
    "summary",
    "research_context",
    "rows",
}
_INPUT_HASH_KEYS = {"candidates_jsonl", "canonical_inventory_jsonl"}
_INPUT_FILE_KEYS = {"path", "sha256", "bytes"}
_LABENZE_RESEARCH_CONTEXT_KEYS = {
    "queries",
    "domains_queried",
    "product_host_allowlist",
    "image_host_allowlist",
    "exact_sku_preservation",
    "identity_field_policy",
    "auto_promotion_allowed",
    "visual_approval_performed",
}
_REQUIEZ_RESEARCH_CONTEXT_KEYS = {
    "domains_queried",
    "product_host_allowlist",
    "image_host_allowlist",
    "exact_sku_preservation",
    "auto_promotion_allowed",
}
_QUERY_KEYS = {"Q1", "Q2", "Q3", "Q4", "Q5"}
_IDENTITY_POLICY_KEYS = {
    "sku",
    "query_sku",
    "source_code",
    "needs_review_never_elevated_to_verified",
}
_CONFIG_KEYS = {"collection", "model", "variant", "description", "base_options", "add_on_options"}
_SIGNATURE_KEYS = {"fields", "sha256"}
_SIGNATURE_FIELD_KEYS = _CONFIG_KEYS
_EVIDENCE_KEYS = {
    "binding",
    "byte_length",
    "dimensions",
    "dimensions_source",
    "domains_scope",
    "full_product_visible",
    "identity_resolution_required",
    "image_sha256",
    "match_method",
    "not_cropped",
    "notes",
    "placeholder_source_image",
    "product_link_verified",
    "query",
    "raw_variant_sku",
    "review_required",
    "shared_image_identity_equivalence",
    "variant_id",
}
_LABENZE_ROW_KEYS = {
    "approved",
    "code_status",
    "config",
    "disposition",
    "evidence",
    "image_source_url",
    "input_reason",
    "input_status",
    "internal_id",
    "name",
    "product_key",
    "product_url",
    "query_sku",
    "reason",
    "sku",
    "source_code",
    "source_hash",
    "source_kind",
    "source_name",
    "source_page",
    "status",
    "supplier",
    "visual_signature",
}
_REQUIEZ_ROW_KEYS = {
    "approved",
    "config",
    "evidence",
    "image_source_url",
    "internal_id",
    "name",
    "product_key",
    "product_url",
    "rejection_reason",
    "sku",
    "source_kind",
    "source_name",
    "status",
    "supplier",
}
_REPORT_STATUSES = {
    "found_candidate",
    "no_indexed_exact_after_search",
    "collision_requires_signature",
}
_REVIEW_CHECKS = (
    "identity_exact",
    "configuration_supported",
    "full_product_visible",
    "not_cropped",
    "correct_base",
    "correct_arms",
    "correct_seats_table",
    "correct_finish",
    "clean_background",
)


@dataclass(frozen=True)
class LoadedInputs:
    inventory_rows: list[dict]
    research_rows: list[dict]
    review_search_rows: list[dict]
    review_candidate_rows: list[dict]
    report_payloads: dict[str, dict]
    report_hashes: dict[str, str]
    normalized_rows: list[dict]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_report_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"Reporte ausente: {path}") from exc
    reparse = bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    if path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Reporte no es archivo regular: {path}")
    if getattr(metadata, "st_nlink", 1) != 1:
        raise ValueError(f"Reporte no puede ser hardlink: {path}")
    if not 0 < metadata.st_size <= MAX_REPORT_BYTES:
        raise ValueError(f"Reporte excede 4 MiB o está vacío: {path}")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Clave JSON duplicada: {key}")
        value[key] = item
    return value


def _reject_constant(value: str):
    raise ValueError(f"NaN/Infinity no permitido: {value}")


def _validate_json_tree(value: object, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError("JSON excede profundidad permitida")
    if isinstance(value, str):
        if any(unicodedata.category(character).startswith("C") for character in value):
            raise ValueError("JSON contiene carácter de control")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN/Infinity no permitido")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_json_tree(key, depth + 1)
            _validate_json_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json_tree(item, depth + 1)


def load_strict_json(path: Path, *, expected_sha256: str) -> dict:
    """Lee un reporte JSON fijado por hash, sin ambigüedades del parser."""

    path = Path(path)
    _assert_report_file(path)
    actual = _sha256_file(path)
    if actual != str(expected_sha256).casefold():
        raise ValueError(f"SHA-256 físico inesperado: esperado={expected_sha256}, actual={actual}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Reporte JSON inválido: {path}") from exc
    _validate_json_tree(payload)
    if not isinstance(payload, dict):
        raise ValueError("Reporte debe ser un objeto JSON")
    return payload


def _require_keys(value: Mapping[str, object], allowed: set[str], label: str, required: set[str] | None = None) -> None:
    unknown = set(value) - allowed
    missing = (allowed if required is None else required) - set(value)
    if unknown:
        raise ValueError(f"{label} contiene campos desconocidos: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label} carece de campos requeridos: {sorted(missing)}")


def _expected_summary(supplier: str, rows: Sequence[Mapping[str, object]]) -> dict:
    statuses = Counter(str(row.get("status") or "") for row in rows)
    result: dict[str, object] = {
        "total": len(rows),
        "found_candidate": statuses["found_candidate"],
        "no_indexed_exact_after_search": statuses["no_indexed_exact_after_search"],
    }
    if supplier == "labenze":
        result["collision_requires_signature"] = statuses["collision_requires_signature"]
        result["dispositions"] = dict(
            Counter(str(row.get("disposition") or "") for row in rows)
        )
        found = [row for row in rows if row.get("status") == "found_candidate"]
        result["found_candidate_evidence_classes"] = {
            "direct_image": sum(not _is_document_reference(row) for row in found),
            "document_evidence_only": sum(_is_document_reference(row) for row in found),
        }
    result["approved"] = sum(row.get("approved") is True for row in rows)
    return result


def _is_document_reference(row: Mapping[str, object]) -> bool:
    product_url = str(row.get("product_url") or "")
    image_url = str(row.get("image_source_url") or "")
    return bool(re.search(r"\.pdf#page=[1-9][0-9]*$", product_url, re.IGNORECASE)) and product_url == image_url


def _pending_review() -> dict[str, object]:
    return {
        "approved": False,
        "reviewer": "",
        "reviewed_at": None,
        "checks": {check: None for check in _REVIEW_CHECKS},
    }


def _canonical_url_without_fragment(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _report_candidate_id(
    report_sha256: str,
    inventory_row: Mapping[str, object],
    acquisition_kind: str,
    candidate: Mapping[str, object] | None,
) -> str:
    material = {
        "report_sha256": report_sha256,
        "identity": {
            key: inventory_row.get(key)
            for key in ("supplier", "internal_id", "product_key", "sku", "source_code")
        },
        "acquisition_kind": acquisition_kind,
        "candidate": candidate,
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _identity_matches(report_row: Mapping[str, object], inventory_row: Mapping[str, object]) -> bool:
    for field in ("supplier", "internal_id", "product_key", "sku", "name"):
        if report_row.get(field) != inventory_row.get(field):
            return False
    expected_config = (inventory_row.get("visual_signature") or {}).get("fields")
    if report_row.get("config") != expected_config:
        return False
    if str(inventory_row.get("supplier")) == "labenze":
        for field in ("source_code", "source_hash", "source_page", "code_status", "visual_signature"):
            if report_row.get(field) != inventory_row.get(field):
                return False
    return True


def _validate_report_metadata(supplier: str, payload: Mapping[str, object]) -> None:
    input_hashes = payload.get("input_hashes")
    if not isinstance(input_hashes, dict):
        raise ValueError(f"input_hashes inválido en reporte {supplier}")
    _require_keys(input_hashes, _INPUT_HASH_KEYS, f"input_hashes {supplier}")
    for name, descriptor in input_hashes.items():
        if not isinstance(descriptor, dict):
            raise ValueError(f"input_hashes.{name} inválido en reporte {supplier}")
        required = {"path", "sha256", "bytes"} if supplier == "labenze" else {"path", "sha256"}
        _require_keys(
            descriptor,
            _INPUT_FILE_KEYS,
            f"input_hashes.{name} {supplier}",
            required=required,
        )
        if not isinstance(descriptor.get("path"), str) or not descriptor["path"]:
            raise ValueError(f"input_hashes.{name}.path inválido")
        sha256 = descriptor.get("sha256")
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ValueError(f"input_hashes.{name}.sha256 inválido")
        if "bytes" in descriptor and (
            isinstance(descriptor["bytes"], bool)
            or not isinstance(descriptor["bytes"], int)
            or descriptor["bytes"] <= 0
        ):
            raise ValueError(f"input_hashes.{name}.bytes inválido")

    context = payload.get("research_context")
    if not isinstance(context, dict):
        raise ValueError(f"research_context inválido en reporte {supplier}")
    context_keys = (
        _LABENZE_RESEARCH_CONTEXT_KEYS
        if supplier == "labenze"
        else _REQUIEZ_RESEARCH_CONTEXT_KEYS
    )
    _require_keys(context, context_keys, f"research_context {supplier}")
    for name in ("domains_queried", "product_host_allowlist", "image_host_allowlist"):
        values = context.get(name)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError(f"research_context.{name} inválido en reporte {supplier}")
    if context.get("exact_sku_preservation") is not True:
        raise ValueError(f"research_context exact_sku_preservation inválido en reporte {supplier}")
    if context.get("auto_promotion_allowed") is not False:
        raise ValueError(f"research_context auto_promotion_allowed inválido en reporte {supplier}")
    if supplier == "labenze":
        if context.get("visual_approval_performed") is not False:
            raise ValueError("research_context visual_approval_performed inválido en reporte labenze")
        queries = context.get("queries")
        policy = context.get("identity_field_policy")
        if not isinstance(queries, dict) or not isinstance(policy, dict):
            raise ValueError("research_context anidado inválido en reporte labenze")
        _require_keys(queries, _QUERY_KEYS, "research_context.queries labenze")
        _require_keys(policy, _IDENTITY_POLICY_KEYS, "research_context.identity_field_policy labenze")
        if any(not isinstance(value, str) or not value for value in queries.values()):
            raise ValueError("research_context.queries inválido en reporte labenze")
        if any(
            not isinstance(policy.get(name), str) or not policy[name]
            for name in ("sku", "query_sku", "source_code")
        ) or policy.get("needs_review_never_elevated_to_verified") is not True:
            raise ValueError("research_context.identity_field_policy inválido en reporte labenze")


def _validate_report_schema(supplier: str, payload: Mapping[str, object]) -> list[dict]:
    _require_keys(payload, _TOP_KEYS, f"reporte {supplier}")
    if payload.get("schema_version") != 1 or payload.get("supplier") != supplier:
        raise ValueError(f"schema/supplier desconocido en reporte {supplier}")
    if not isinstance(payload.get("researched_at"), str) or not payload["researched_at"]:
        raise ValueError(f"researched_at inválido en reporte {supplier}")
    _validate_report_metadata(supplier, payload)
    rows = payload.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"rows inválido en reporte {supplier}")
    row_keys = _LABENZE_ROW_KEYS if supplier == "labenze" else _REQUIEZ_ROW_KEYS
    seen = set()
    for row in rows:
        _require_keys(row, row_keys, f"fila {supplier}")
        status = row.get("status")
        if status not in _REPORT_STATUSES or (supplier == "requiez" and status == "collision_requires_signature"):
            raise ValueError(f"estado desconocido en reporte {supplier}: {status!r}")
        internal_id = str(row.get("internal_id") or "")
        if not internal_id or internal_id in seen:
            raise ValueError(f"ID duplicado/ausente en reporte {supplier}: {internal_id!r}")
        seen.add(internal_id)
        if row.get("approved") is not False:
            raise ValueError(f"Candidato preaprobado no permitido: {internal_id}")
        config = row.get("config")
        evidence = row.get("evidence")
        if not isinstance(config, dict) or not isinstance(evidence, dict):
            raise ValueError(f"config/evidence inválido: {internal_id}")
        _require_keys(config, _CONFIG_KEYS, f"config {internal_id}")
        _require_keys(evidence, _EVIDENCE_KEYS, f"evidence {internal_id}", required=set())
        if supplier == "labenze":
            signature = row.get("visual_signature")
            if not isinstance(signature, dict):
                raise ValueError(f"visual_signature inválida: {internal_id}")
            _require_keys(signature, _SIGNATURE_KEYS, f"visual_signature {internal_id}")
            fields = signature.get("fields")
            if not isinstance(fields, dict):
                raise ValueError(f"visual_signature.fields inválido: {internal_id}")
            _require_keys(fields, _SIGNATURE_FIELD_KEYS, f"visual_signature.fields {internal_id}")
    expected_summary = _expected_summary(supplier, rows)
    if payload.get("summary") != expected_summary:
        raise ValueError(f"summary divergente en reporte {supplier}")
    return rows


def _normalize_row(
    report_row: Mapping[str, object],
    inventory_row: Mapping[str, object],
    report_sha256: str,
) -> dict:
    status = str(report_row["status"])
    evidence = report_row["evidence"]
    assert isinstance(evidence, dict)
    candidate: dict | None = None
    if status == "found_candidate" and _is_document_reference(report_row):
        acquisition_kind = "document_page"
        raw_url = str(report_row["product_url"])
        reported_page_number = int(raw_url.rsplit("#page=", 1)[1])
        page_number = reported_page_number + int(
            str(report_row.get("source_name") or "") in ZERO_BASED_DOCUMENT_SOURCES
        )
        source_code = str(inventory_row.get("source_code") or "")
        block_reason = DOCUMENT_BLOCK_REASONS.get(source_code)
        candidate = {
            "source_name": str(report_row.get("source_name") or ""),
            "source_kind": "catalog_pdf",
            "source_id": hashlib.sha256(_canonical_url_without_fragment(raw_url).encode()).hexdigest(),
            "product_url": None,
            "image_source_url": None,
            "document_url": _canonical_url_without_fragment(raw_url),
            "page_number": page_number,
            "reported_page_number": reported_page_number,
            "document_disposition": (
                "document_semantic_blocked" if block_reason else "document_bbox_review"
            ),
            "document_block_reason": block_reason,
            "binding": evidence.get("binding"),
            "variant_id": None,
            "product_link_verified": False,
            "evidence": evidence,
        }
    elif status == "found_candidate" and evidence.get("placeholder_source_image") is not True:
        acquisition_kind = "direct_image"
        product_url = str(report_row.get("product_url") or "")
        image_url = str(report_row.get("image_source_url") or "")
        variant_values = [
            value
            for key, value in parse_qsl(urlsplit(product_url).query, keep_blank_values=True)
            if key.casefold() == "variant"
        ]
        variant_id = variant_values[0] if len(variant_values) == 1 and variant_values[0].isdigit() else None
        candidate = {
            "source_name": str(report_row.get("source_name") or ""),
            "source_kind": str(report_row.get("source_kind") or ""),
            "source_id": hashlib.sha256(product_url.encode()).hexdigest(),
            "product_url": product_url,
            "image_source_url": image_url,
            "document_url": None,
            "page_number": None,
            "binding": evidence.get("binding"),
            "variant_id": variant_id or evidence.get("variant_id"),
            "product_link_verified": bool(variant_id) or evidence.get("product_link_verified") is True,
            "evidence": evidence,
        }
    else:
        acquisition_kind = "none"
    query_sku = str(report_row.get("query_sku") or inventory_row.get("source_code") or "")
    result = {
        "schema_version": 1,
        "supplier": inventory_row["supplier"],
        "internal_id": inventory_row["internal_id"],
        "canonical_identity": {
            "product_key": inventory_row["product_key"],
            "sku": inventory_row["sku"],
            "source_code": inventory_row["source_code"],
            "source_hash": inventory_row["source_hash"],
            "name": inventory_row.get("name", ""),
            "configuration": (inventory_row.get("visual_signature") or {}).get("fields"),
            "visual_signature_sha256": (inventory_row.get("visual_signature") or {}).get("sha256"),
            "code_status": inventory_row.get("code_status"),
        },
        "report_identity": {
            "query_sku": query_sku,
            "report_sha256": report_sha256,
            "input_status": report_row.get("input_status"),
        },
        "terminal_status": status,
        "acquisition_kind": acquisition_kind,
        "candidate": candidate,
        "review": _pending_review(),
    }
    result["report_candidate_id"] = _report_candidate_id(
        report_sha256, inventory_row, acquisition_kind, candidate
    )
    return result


def validate_normalized_routing(rows: Sequence[Mapping[str, object]]) -> None:
    """Cierra la frontera entre bytes directos, documentos y filas sin red."""

    for row in rows:
        internal_id = str(row.get("internal_id") or "")
        kind = row.get("acquisition_kind")
        candidate = row.get("candidate")
        if kind == "none":
            if candidate is not None:
                raise ValueError(f"none no puede conservar solicitud de red: {internal_id}")
            continue
        if not isinstance(candidate, Mapping):
            raise ValueError(f"Ruta sin candidato: {internal_id}")
        if kind == "document_page":
            document_url = str(candidate.get("document_url") or "")
            if (
                candidate.get("source_kind") != "catalog_pdf"
                or
                candidate.get("image_source_url") is not None
                or candidate.get("product_url") is not None
                or "#" in document_url
                or urlsplit(document_url).scheme != "https"
                or type(candidate.get("page_number")) is not int
                or int(candidate["page_number"]) <= 0
            ):
                raise ValueError(f"document_page inválido: {internal_id}")
            continue
        if kind != "direct_image":
            raise ValueError(f"Tipo de adquisición desconocido: {kind!r}")
        if candidate.get("source_kind") not in DIRECT_SOURCE_KINDS:
            raise ValueError(f"source_kind directo desconocido: {internal_id}")
        product_url = str(candidate.get("product_url") or "")
        image_url = str(candidate.get("image_source_url") or "")
        if not product_url or not image_url or product_url == image_url:
            raise ValueError(f"direct_image exige URLs HTTPS distintas: {internal_id}")
        if urlsplit(product_url).scheme != "https" or urlsplit(image_url).scheme != "https":
            raise ValueError(f"direct_image exige URLs HTTPS distintas: {internal_id}")
        evidence = candidate.get("evidence")
        if isinstance(evidence, Mapping) and evidence.get("placeholder_source_image") is True:
            raise ValueError(f"placeholder no puede entrar al downloader: {internal_id}")
        variant_values = [
            value
            for key, value in parse_qsl(urlsplit(product_url).query, keep_blank_values=True)
            if key.casefold() == "variant"
        ]
        declared_variant = candidate.get("variant_id")
        requires_variant = (
            declared_variant not in {None, ""}
            or candidate.get("product_link_verified") is True
        )
        if (requires_variant or variant_values) and (
            len(variant_values) != 1
            or not variant_values[0].isdigit()
            or str(declared_variant or "") != variant_values[0]
            or candidate.get("product_link_verified") is not True
        ):
            raise ValueError(f"product_link_unverified: {internal_id}")


def build_request_plan(rows: Sequence[Mapping[str, object]]) -> dict[str, list[dict]]:
    """Devuelve sólo asociaciones autorizadas a tocar red; ``none`` no aparece."""

    validate_normalized_routing(rows)
    return {
        "direct_images": [
            {"internal_id": row["internal_id"], "candidate": row["candidate"]}
            for row in rows
            if row["acquisition_kind"] == "direct_image"
        ],
        "document_pages": [
            {"internal_id": row["internal_id"], "candidate": row["candidate"]}
            for row in rows
            if row["acquisition_kind"] == "document_page"
        ],
    }


def _as_research_candidate(candidate: Mapping[str, object]) -> ResearchCandidate:
    return ResearchCandidate(
        source_name=str(candidate.get("source_name") or ""),
        source_kind=str(candidate.get("source_kind") or ""),
        source_id=str(candidate.get("source_id") or ""),
        query="",
        matched_field=str(candidate.get("binding") or ""),
        product_url=str(candidate.get("product_url") or ""),
        image_source_url=str(candidate.get("image_source_url") or ""),
        evidence=dict(candidate.get("evidence") or {}),
        approved=False,
    )


def _client_get(client, url: str, **kwargs) -> object:
    """Convierte sólo fallos esperados de transporte en un motivo replay-estable."""

    try:
        return client.get(url, **kwargs)
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise ValueError("transport_error") from exc
    except ValueError as exc:
        message = str(exc)
        dns_transport_failure = (
            message.startswith("No se pudo resolver host público:")
            and isinstance(exc.__cause__, (OSError, TimeoutError))
        )
        if (
            message.startswith("Falta respuesta en cache offline:")
            or message.startswith("HTTP agotó reintentos con status ")
            or dns_transport_failure
        ):
            raise ValueError("transport_error") from exc
        raise


def _probe_product_page(candidate: Mapping[str, object], client) -> HttpResponse:
    research_candidate = _as_research_candidate(candidate)
    validate_candidate_source_policy(research_candidate)
    validate_candidate_urls(
        research_candidate,
        allowed_product_hosts=PRODUCT_PAGE_HOSTS,
        allowed_image_hosts=IMAGE_HOSTS,
    )
    source = _canonical_source_name(candidate.get("source_name"))
    response = _client_get(
        client,
        research_candidate.product_url,
        source_name=source,
        resource_kind="product",
        max_response_bytes=MAX_ORIGINAL_BYTES,
    )
    if not isinstance(response, HttpResponse):
        raise TypeError("client.get debe devolver HttpResponse")
    if response.status != 200:
        raise ValueError(f"product_page_http_{response.status}")
    try:
        validate_source_resource_url(response.url, source_name=source, resource_kind="product")
    except ValueError as exc:
        raise ValueError("product_page_redirect_invalid") from exc
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].casefold()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise ValueError("product_page_not_html")
    requested_variants = [
        value
        for key, value in parse_qsl(urlsplit(research_candidate.product_url).query)
        if key.casefold() == "variant"
    ]
    final_variants = [
        value
        for key, value in parse_qsl(urlsplit(response.url).query)
        if key.casefold() == "variant"
    ]
    if requested_variants and requested_variants != final_variants:
        raise ValueError("product_page_redirect_invalid")
    return response


def _candidate_review_row(
    row: Mapping[str, object],
    download,
    metrics: Mapping[str, object],
) -> dict:
    candidate = row["candidate"]
    assert isinstance(candidate, Mapping)
    identity = row["canonical_identity"]
    assert isinstance(identity, Mapping)
    inventory_shape = {
        "supplier": row["supplier"],
        "internal_id": row["internal_id"],
        "product_key": identity["product_key"],
        "sku": identity["sku"],
        "source_code": identity["source_code"],
        "source_hash": identity["source_hash"],
        "name": identity["name"],
        "visual_signature": {
            "sha256": identity["visual_signature_sha256"],
            "fields": identity["configuration"],
        },
    }
    candidate_id = build_candidate_id(inventory_shape, candidate, download.sha256)
    bbox = metrics.get("foreground_bbox")
    occupancy = float(metrics.get("occupancy") or 0.0)
    margins = metrics.get("margins")
    source_width = int(download.dimensions["width"])
    source_height = int(download.dimensions["height"])
    requires_upscale = max(source_width, source_height) < 1024
    margin_contract = bool(
        isinstance(margins, Mapping)
        and all(
            isinstance(margins.get(side), (int, float))
            and math.isfinite(float(margins[side]))
            and float(margins[side]) >= 0.04
            for side in ("left", "top", "right", "bottom")
        )
    )
    bbox_contract = bool(
        isinstance(bbox, Mapping)
        and isinstance(bbox.get("width"), (int, float))
        and isinstance(bbox.get("height"), (int, float))
        and math.isfinite(float(bbox["width"]))
        and math.isfinite(float(bbox["height"]))
        and 0 < float(bbox["width"]) <= source_width * 0.92
        and 0 < float(bbox["height"]) <= source_height * 0.92
    )
    occupancy_contract = math.isfinite(occupancy) and 0.12 <= occupancy <= 0.80
    feasible = bool(
        not requires_upscale
        and margin_contract
        and bbox_contract
        and occupancy_contract
    )
    return {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "candidate_id_short": candidate_id[:12],
        "report_candidate_id": row["report_candidate_id"],
        "supplier": row["supplier"],
        "internal_id": row["internal_id"],
        "product_key": identity["product_key"],
        "sku": identity["sku"],
        "source_code": identity["source_code"],
        "source_hash": identity["source_hash"],
        "name": identity["name"],
        "visual_signature": inventory_shape["visual_signature"],
        "configuration": _configuration_text(inventory_shape),
        "product_url": candidate["product_url"],
        "image_source_url": candidate["image_source_url"],
        "source_kind": candidate["source_kind"],
        "source_name": candidate["source_name"],
        "source_id": candidate["source_id"],
        "binding": candidate["binding"],
        "evidence": candidate["evidence"],
        "original": {
            "path": f"originals/{download.path.name}",
            "object_name": download.path.name,
            "sha256": download.sha256,
            "bytes": download.bytes,
            "mime": download.mime,
            "dimensions": download.dimensions,
            "mode": metrics["mode"],
        },
        "metrics": {
            key: metrics[key]
            for key in (
                "min_dimension",
                "max_dimension",
                "aspect_ratio",
                "has_alpha",
                "foreground_bbox",
                "occupancy",
                "margins",
            )
        },
        "automatic_gate": metrics["automatic_gate"],
        "normalization_feasibility": {
            "informational_only": True,
            "target_square_px": 1024,
            "requires_upscale": requires_upscale,
            "could_meet_contain_contract_without_semantic_edit": feasible,
            "contract": {
                "margin_4pct_plus": margin_contract,
                "bbox_92pct_or_less": bbox_contract,
                "occupancy_12_to_80pct": occupancy_contract,
            },
        },
        "normalized_asset_path": None,
        "identity_configuration_conflict": row["internal_id"] in SEMANTIC_CONFLICT_IDS,
        "global_gate_blocked": row["internal_id"] in SEMANTIC_CONFLICT_IDS,
        "review": _pending_review(),
    }


def acquire_direct_images(
    rows: Sequence[Mapping[str, object]],
    client,
    originals_dir: Path,
    *,
    max_total_bytes: int = 256 * 1024 * 1024,
) -> tuple[list[dict], list[dict]]:
    """Prueba fichas y descarga cada URL directa una sola vez, siempre pendiente."""

    direct_rows = [row for row in rows if row.get("acquisition_kind") == "direct_image"]
    validate_normalized_routing(direct_rows)
    originals_dir = Path(originals_dir)
    image_cache: dict[str, object] = {}
    product_cache: dict[str, object] = {}
    total_bytes = 0
    receipts: list[dict] = []
    output: list[dict] = []
    for row in direct_rows:
        candidate = row["candidate"]
        assert isinstance(candidate, Mapping)
        receipt = {
            "schema_version": 1,
            "report_candidate_id": row["report_candidate_id"],
            "supplier": row["supplier"],
            "internal_id": row["internal_id"],
            "acquisition_kind": "direct_image",
            "status": "rejected",
            "reason": "",
            "requested_product_url": candidate["product_url"],
            "final_product_url": None,
            "requested_url": candidate["image_source_url"],
            "final_url": None,
            "original": None,
        }
        try:
            association = _as_research_candidate(candidate)
            validate_candidate_source_policy(association)
            validate_candidate_urls(
                association,
                allowed_product_hosts=PRODUCT_PAGE_HOSTS,
                allowed_image_hosts=IMAGE_HOSTS,
            )
            product_url = str(candidate["product_url"])
            page = product_cache.get(product_url)
            if isinstance(page, ValueError):
                raise page
            if page is None:
                try:
                    page = _probe_product_page(candidate, client)
                except ValueError as exc:
                    product_cache[product_url] = exc
                    raise
                product_cache[product_url] = page
            receipt["final_product_url"] = page.url
            image_url = str(candidate["image_source_url"])
            cached = image_cache.get(image_url)
            if isinstance(cached, ValueError):
                raise cached
            if cached is None:
                research_candidate = _as_research_candidate(candidate)

                def fetch(url: str):
                    response = _client_get(
                        client,
                        url,
                        source_name=_canonical_source_name(candidate.get("source_name")),
                        resource_kind="image",
                        max_response_bytes=MAX_ORIGINAL_BYTES,
                    )
                    if not isinstance(response, HttpResponse):
                        raise TypeError("client.get debe devolver HttpResponse")
                    if hashlib.sha256(response.body).hexdigest() == JUN_PLACEHOLDER_SHA256:
                        raise ValueError("known_placeholder_sha256")
                    if total_bytes + len(response.body) > max_total_bytes:
                        raise ValueError("direct_image_budget_exceeded")
                    return response

                try:
                    transport_dns_bound = (
                        type(client) is CachedHttpClient
                        and type(getattr(client, "transport", None)) is UrllibTransport
                    )
                    cached = download_original(
                        research_candidate,
                        originals_dir,
                        allowed_image_hosts=IMAGE_HOSTS,
                        fetcher=fetch,
                        check_dns=(
                            not bool(getattr(client, "offline", False))
                            and not transport_dns_bound
                        ),
                    )
                except ValueError as exc:
                    image_cache[image_url] = exc
                    raise
                image_cache[image_url] = cached
                total_bytes += cached.bytes
            download = cached
            receipt["final_url"] = download.final_url
            receipt["original"] = {
                "object_name": download.path.name,
                "sha256": download.sha256,
                "bytes": download.bytes,
                "mime": download.mime,
                "dimensions": download.dimensions,
            }
            declared = candidate.get("evidence", {}).get("dimensions")
            if isinstance(declared, Mapping) and declared:
                if download.dimensions != {
                    "width": int(declared.get("width", 0)),
                    "height": int(declared.get("height", 0)),
                }:
                    raise ValueError("declared_dimensions_mismatch")
            metrics = inspect_original(
                download.path,
                expected_sha256=download.sha256,
                expected_bytes=download.bytes,
                expected_mime=download.mime,
                expected_dimensions=download.dimensions,
            )
            receipt.update(
                {
                    "status": "downloaded",
                    "reason": "",
                }
            )
            output.append(_candidate_review_row(row, download, metrics))
        except ValueError as exc:
            receipt["reason"] = str(exc)
        receipts.append(receipt)
    output.sort(key=lambda value: (str(value["supplier"]), str(value["internal_id"]), str(value["candidate_id"])))
    return receipts, output


def _difference_hash(path: Path) -> str:
    with Image.open(path) as source:
        source.load()
        image = source.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(image.get_flattened_data())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return f"{bits:016x}"


def analyze_duplicate_clusters(
    candidates: Sequence[dict],
    originals_dir: Path,
) -> dict[str, object]:
    """Expone duplicados exactos y perceptuales sin inferir aprobación o reuse."""

    by_sha: dict[str, list[dict]] = {}
    by_dhash: dict[str, list[dict]] = {}
    originals_dir = Path(originals_dir)
    for candidate in candidates:
        sha256 = str(candidate["original"]["sha256"])
        by_sha.setdefault(sha256, []).append(candidate)
        dhash = _difference_hash(originals_dir / candidate["original"]["object_name"])
        candidate["perceptual_hash"] = dhash
        by_dhash.setdefault(dhash, []).append(candidate)
    exact = []
    for sha256, values in sorted(by_sha.items()):
        identities = sorted({str(value["internal_id"]) for value in values})
        if len(identities) < 2:
            continue
        signatures = sorted({str(value["visual_signature"]["sha256"]) for value in values})
        conflict = len(signatures) > 1
        potential = len(signatures) == 1
        declared_evidence = frozenset(identities) == ATANA_SHARED_IDS
        for value in values:
            value["duplicate_conflict"] = conflict
            value["potential_shared_visual"] = potential
            value["declared_shared_visual_evidence"] = declared_evidence
            value["shared_visual_group"] = None
            if conflict or potential:
                value["global_gate_blocked"] = True
        exact.append(
            {
                "sha256": sha256,
                "internal_ids": identities,
                "visual_signature_sha256s": signatures,
                "duplicate_conflict": conflict,
                "potential_shared_visual": potential,
                "declared_shared_visual_evidence": declared_evidence,
                "shared_visual_group": None,
            }
        )
    perceptual = [
        {
            "dhash": digest,
            "candidate_ids": sorted(str(value["candidate_id"]) for value in values),
            "internal_ids": sorted({str(value["internal_id"]) for value in values}),
            "decision_effect": "inspection_only",
        }
        for digest, values in sorted(by_dhash.items())
        if len(values) > 1
    ]
    return {"schema_version": 1, "exact": exact, "perceptual": perceptual}


def render_candidate_contact_sheets(
    output_dir: Path,
    candidates: Sequence[dict],
    originals_dir: Path,
) -> tuple[dict, dict]:
    """Reutiliza las láminas 6B: contain y etiquetas fuera del área visual."""

    ordered = sorted(
        candidates,
        key=lambda value: (str(value["supplier"]), str(value["internal_id"]), str(value["candidate_id"])),
    )
    for index, candidate in enumerate(ordered, 1):
        candidate["index"] = index
    return _render_contact_sheets(Path(output_dir), ordered, Path(originals_dir))


def _normalized_document_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).upper()
    return "".join(character for character in normalized if character.isalnum())


def _render_page_preview(data: bytes, page_number: int, destination: Path) -> None:
    document = fitz.open(stream=data, filetype="pdf")
    try:
        page = document.load_page(page_number - 1)
        width = max(1.0, float(page.rect.width))
        height = max(1.0, float(page.rect.height))
        scale = min(2.0, 2048.0 / max(width, height), math.sqrt(4_000_000 / (width * height)))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        payload = pixmap.tobytes("png")
    finally:
        document.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ValueError(f"Preview inmutable divergente: {destination.name}")
    else:
        destination.write_bytes(payload)


def _pdf_flate_size_bounded(raw: bytes, *, max_stream_ratio: int) -> int:
    """Cuenta Flate sin materializar más de 64 MiB y aplica el ratio del perfil."""

    if type(max_stream_ratio) is not int or not 1 <= max_stream_ratio <= 256:
        raise SourceSafetyError("PDF_LIMIT")
    decoder = zlib.decompressobj()
    total = 0
    invalid = False
    try:
        for offset in range(0, len(raw), 64 * 1024):
            pending = raw[offset : offset + 64 * 1024]
            while pending:
                output = decoder.decompress(
                    pending,
                    min(
                        64 * 1024,
                        _pdf_common.MAX_PDF_STREAM_DECODED_BYTES - total + 1,
                    ),
                )
                total += len(output)
                if total > _pdf_common.MAX_PDF_STREAM_DECODED_BYTES:
                    raise SourceSafetyError("PDF_LIMIT")
                remaining = decoder.unconsumed_tail
                if remaining == pending and not output:
                    invalid = True
                    break
                pending = remaining
            if invalid:
                break
        if not invalid and not decoder.eof:
            output = decoder.flush(
                _pdf_common.MAX_PDF_STREAM_DECODED_BYTES - total + 1
            )
            total += len(output)
    except SourceSafetyError:
        raise
    except (MemoryError, OverflowError, zlib.error):
        invalid = True
    if invalid or not decoder.eof or decoder.unused_data:
        raise SourceSafetyError("PDF_INVALID")
    if total > _pdf_common.MAX_PDF_STREAM_DECODED_BYTES or (
        total and (not raw or total / len(raw) > max_stream_ratio)
    ):
        raise SourceSafetyError("PDF_LIMIT")
    return total


def _pdf_ascii85_flate_size(raw: bytes, *, max_stream_ratio: int) -> int:
    """Decodifica exclusivamente ASCII85→Flate con límites antes de expandir."""

    if not 0 < len(raw) <= _pdf_common.MAX_PDF_STREAM_RAW_BYTES:
        raise SourceSafetyError("PDF_LIMIT")
    compact = b"".join(raw.split())
    if len(compact) < 3 or not compact.endswith(b"~>") or compact.startswith(b"<~"):
        raise SourceSafetyError("PDF_INVALID")
    try:
        compressed = base64.a85decode(
            compact[:-2],
            foldspaces=False,
            adobe=False,
            ignorechars=b"",
        )
    except (ValueError, OverflowError) as exc:
        raise SourceSafetyError("PDF_INVALID") from exc
    if not 0 < len(compressed) <= _pdf_common.MAX_PDF_STREAM_RAW_BYTES:
        raise SourceSafetyError("PDF_LIMIT")
    return _pdf_flate_size_bounded(
        compressed,
        max_stream_ratio=max_stream_ratio,
    )


def _sego_pdf_preflight(
    document,
    *,
    max_stream_expanded_bytes: int,
    max_stream_ratio: int,
) -> dict[int, int]:
    """Replica el gate común y añade un único filtro auditado para el SHA Sego."""

    xref_count = document.xref_length()
    if not 1 < xref_count <= _pdf_common.MAX_PDF_XREFS:
        raise SourceSafetyError("PDF_LIMIT")
    stream_count = 0
    total_raw = 0
    total_expanded = 0
    raw_sizes: dict[int, int] = {}
    for xref in range(1, xref_count):
        keys = set(document.xref_get_keys(xref))
        passive_open_action = (
            "OpenAction" in keys
            and _pdf_common._pdf_passive_internal_open_action(document, xref)
        )
        if "AA" in keys or ("OpenAction" in keys and not passive_open_action):
            raise SourceSafetyError("PDF_UNSAFE")
        type_value = document.xref_get_key(xref, "Type")[1]
        action_value = document.xref_get_key(xref, "S")[1]
        passive_uri = _pdf_common._pdf_passive_uri_action(document, xref)
        passive_link = _pdf_common._pdf_passive_uri_action(document, xref, "A/")
        chained_action = (
            document.xref_get_key(xref, "Next")[0] != "null"
            and type_value == "/Action"
        ) or document.xref_get_key(xref, "A/Next")[0] != "null"
        if chained_action:
            raise SourceSafetyError("PDF_UNSAFE")
        if (type_value == "/Action" and not passive_uri) or (
            action_value
            in {
                "/GoToR",
                "/ImportData",
                "/JavaScript",
                "/Launch",
                "/Named",
                "/SubmitForm",
                "/URI",
            }
            and not passive_uri
        ):
            raise SourceSafetyError("PDF_UNSAFE")
        raw_object = document.xref_object(xref, compressed=False).encode(
            "latin-1", "ignore"
        )
        active_tokens = {
            match.group(0) for match in _pdf_common._ACTIVE_PDF_TOKEN.finditer(raw_object)
        }
        allowed_active_tokens = (
            active_tokens == {b"/URI"} and (passive_uri or passive_link)
        ) or (active_tokens == {b"/OpenAction"} and passive_open_action)
        if active_tokens and not allowed_active_tokens:
            raise SourceSafetyError("PDF_UNSAFE")
        if not document.xref_is_stream(xref):
            continue

        stream_count += 1
        declared = _pdf_common._pdf_integer(document, xref, "Length")
        if declared is None:
            raise SourceSafetyError("PDF_INVALID")
        if declared > _pdf_common.MAX_PDF_STREAM_RAW_BYTES:
            raise SourceSafetyError("PDF_LIMIT")
        total_raw += declared
        if (
            stream_count > _pdf_common.MAX_PDF_STREAMS
            or total_raw > _pdf_common.MAX_FILE_BYTES
        ):
            raise SourceSafetyError("PDF_LIMIT")
        raw = document.xref_stream_raw(xref)
        if not isinstance(raw, bytes) or len(raw) != declared:
            raise SourceSafetyError("PDF_INVALID")
        raw_sizes[xref] = len(raw)
        filters = _pdf_common._pdf_filters(document, xref)
        subtype = document.xref_get_key(xref, "Subtype")[1]
        if (
            subtype == "/Image"
            and filters
            and set(filters) <= _pdf_common._PDF_IMAGE_FILTERS
        ):
            decoded = _pdf_common._pdf_image_decoded_size(document, xref)
        elif filters == ():
            decoded = len(raw)
        elif filters == ("FlateDecode",):
            decoded = _pdf_flate_size_bounded(
                raw,
                max_stream_ratio=max_stream_ratio,
            )
        elif filters == ("ASCII85Decode", "FlateDecode"):
            decoded = _pdf_ascii85_flate_size(
                raw,
                max_stream_ratio=max_stream_ratio,
            )
        else:
            raise SourceSafetyError("PDF_UNSAFE")
        total_expanded += decoded
        if total_expanded > max_stream_expanded_bytes:
            raise SourceSafetyError("PDF_LIMIT")
    return raw_sizes


def _sego_pdf_pages(
    data: bytes,
    *,
    max_stream_expanded_bytes: int,
    max_stream_ratio: int,
    max_pages: int,
) -> tuple:
    """Extrae texto Sego tras el preflight local exact-hash y los gates de página comunes."""

    document = None
    try:
        document = fitz.open(stream=data, filetype="pdf")
        if not document.is_pdf or document.needs_pass or document.is_encrypted:
            raise SourceSafetyError("PDF_UNSAFE")
        if not 0 < document.page_count <= max_pages:
            raise SourceSafetyError("PDF_LIMIT")
        if document.embfile_names():
            raise SourceSafetyError("PDF_UNSAFE")
        raw_sizes = _sego_pdf_preflight(
            document,
            max_stream_expanded_bytes=max_stream_expanded_bytes,
            max_stream_ratio=max_stream_ratio,
        )
        total_images = 0
        total_image_bytes = 0
        page_image_counts = []
        for index in range(document.page_count):
            page = document.load_page(index)
            image_count, image_bytes = _pdf_common._pdf_page_preflight(
                document, page, raw_sizes
            )
            page_image_counts.append(image_count)
            total_images += image_count
            total_image_bytes += image_bytes
            if (
                total_images > _pdf_common.MAX_PDF_IMAGES
                or total_image_bytes > _pdf_common.MAX_PDF_IMAGE_BYTES
            ):
                raise SourceSafetyError("PDF_LIMIT")
        texts = _pdf_common._pdf_text_isolated(data, document.page_count)
        return tuple(
            _pdf_common.PdfPage(index + 1, text, page_image_counts[index])
            for index, text in enumerate(texts)
        )
    except SourceSafetyError:
        raise
    except Exception as exc:
        raise SourceSafetyError("PDF_INVALID") from exc
    finally:
        if document is not None:
            try:
                document.close()
            except Exception as exc:
                raise SourceSafetyError("PDF_INVALID") from exc


def _preflight_document(url: str, body: bytes) -> tuple[str, tuple]:
    """Aplica el gate común o un perfil local fijado por URL+SHA."""

    digest = hashlib.sha256(body).hexdigest()
    profile = DOCUMENT_PREFLIGHT_PROFILES.get(url)
    kwargs = {}
    if profile is not None:
        if digest != profile["sha256"]:
            raise ValueError("document_hash_mismatch")
        if profile.get("ascii85_flate_only") is True:
            pages = _sego_pdf_pages(
                body,
                max_pages=profile["page_count"],
                max_stream_expanded_bytes=profile["max_stream_expanded_bytes"],
                max_stream_ratio=profile["max_stream_ratio"],
            )
        else:
            kwargs["max_stream_expanded_bytes"] = profile["max_stream_expanded_bytes"]
            pages = tuple(_pdf_pages(body, **kwargs))
    else:
        pages = tuple(_pdf_pages(body, **kwargs))
    if profile is not None and len(pages) != profile["page_count"]:
        raise ValueError("document_page_count_mismatch")
    return digest, pages


def acquire_document_pages(
    rows: Sequence[Mapping[str, object]],
    client,
    documents_dir: Path,
    previews_dir: Path,
    *,
    max_total_bytes: int = 128 * 1024 * 1024,
) -> tuple[list[dict], list[dict]]:
    """Prevalida PDF exactos y emite sólo una subcola de página/bbox pendiente."""

    document_rows = [row for row in rows if row.get("acquisition_kind") == "document_page"]
    validate_normalized_routing(document_rows)
    documents_dir = Path(documents_dir)
    previews_dir = Path(previews_dir)
    document_cache: dict[str, dict] = {}
    preview_cache: dict[tuple[str, int], str] = {}
    total_bytes = 0
    receipts: list[dict] = []
    queue: list[dict] = []
    for row in document_rows:
        candidate = row["candidate"]
        identity = row["canonical_identity"]
        assert isinstance(candidate, Mapping) and isinstance(identity, Mapping)
        url = str(candidate["document_url"])
        receipt = {
            "schema_version": 1,
            "report_candidate_id": row["report_candidate_id"],
            "supplier": row["supplier"],
            "internal_id": row["internal_id"],
            "acquisition_kind": "document_page",
            "status": "document_fetch_failed",
            "reason": "",
            "requested_url": url,
            "final_url": None,
            "page_number": candidate["page_number"],
            "document": None,
            "preview_path": None,
            "crop_path": None,
        }
        source = _canonical_source_name(candidate.get("source_name"))
        try:
            validate_source_resource_url(
                url,
                source_name=source,
                resource_kind="document",
            )
        except ValueError as exc:
            receipt["reason"] = str(exc)
            receipts.append(receipt)
            continue
        if candidate.get("document_disposition") == "document_semantic_blocked":
            receipt["status"] = "document_semantic_blocked"
            receipt["reason"] = str(candidate.get("document_block_reason") or "document_semantic_blocked")
            receipts.append(receipt)
            continue
        cached = document_cache.get(url)
        if cached is None:
            validate_source_resource_url(url, source_name=source, resource_kind="document")
            try:
                response = _client_get(
                    client,
                    url,
                    source_name=source,
                    resource_kind="document",
                    max_response_bytes=MAX_DOCUMENT_BYTES,
                )
                if not isinstance(response, HttpResponse):
                    raise TypeError("client.get debe devolver HttpResponse")
            except ValueError as exc:
                cached = {"status": "document_fetch_failed", "reason": str(exc)}
            else:
                if response.status != 200:
                    cached = {
                        "status": "document_fetch_failed",
                        "reason": f"document_http_{response.status}",
                        "final_url": response.url,
                    }
                else:
                    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].casefold()
                    if content_type != "application/pdf":
                        cached = {
                            "status": "document_mime_invalid",
                            "reason": f"document_mime_invalid:{content_type}",
                            "final_url": response.url,
                        }
                    elif not response.body.startswith(b"%PDF-"):
                        cached = {
                            "status": "document_magic_invalid",
                            "reason": "document_magic_invalid",
                            "final_url": response.url,
                        }
                    elif len(response.body) > MAX_DOCUMENT_BYTES:
                        cached = {
                            "status": "document_limit_exceeded",
                            "reason": "document_exceeds_64_mib",
                            "final_url": response.url,
                        }
                    elif total_bytes + len(response.body) > max_total_bytes:
                        raise ValueError("document_aggregate_budget_exceeded")
                    else:
                        try:
                            digest, pages = _preflight_document(url, bytes(response.body))
                        except SourceSafetyError as exc:
                            cached = {
                                "status": "document_preflight_failed",
                                "reason": f"document_preflight_failed:{exc}",
                                "final_url": response.url,
                            }
                        except ValueError as exc:
                            if str(exc) not in {
                                "document_hash_mismatch",
                                "document_page_count_mismatch",
                            }:
                                raise
                            cached = {
                                "status": "document_profile_mismatch",
                                "reason": str(exc),
                                "final_url": response.url,
                            }
                        else:
                            validate_source_resource_url(
                                response.url,
                                source_name=source,
                                resource_kind="document",
                            )
                            destination = documents_dir / f"{digest}.pdf"
                            documents_dir.mkdir(parents=True, exist_ok=True)
                            if destination.exists():
                                if destination.read_bytes() != response.body:
                                    raise ValueError(f"Documento content-addressed divergente: {destination.name}")
                            else:
                                destination.write_bytes(response.body)
                            total_bytes += len(response.body)
                            cached = {
                                "status": "downloaded",
                                "reason": "",
                                "final_url": response.url,
                                "sha256": digest,
                                "bytes": len(response.body),
                                "path": destination,
                                "pages": pages,
                                "body": bytes(response.body),
                            }
            document_cache[url] = cached
        receipt["status"] = cached["status"]
        receipt["reason"] = cached.get("reason", "")
        receipt["final_url"] = cached.get("final_url")
        if cached["status"] != "downloaded":
            receipts.append(receipt)
            continue
        receipt["document"] = {
            "path": f"documents/{cached['path'].name}",
            "object_name": cached["path"].name,
            "sha256": cached["sha256"],
            "bytes": cached["bytes"],
        }
        page_number = int(candidate["page_number"])
        pages = cached["pages"]
        source_code = str(identity["source_code"])
        if page_number > len(pages) or _normalized_document_text(source_code) not in _normalized_document_text(pages[page_number - 1].text):
            receipt["status"] = "document_identity_unverified"
            receipt["reason"] = "document_page_missing_or_code_absent"
            receipts.append(receipt)
            continue
        preview_key = (cached["sha256"], page_number)
        preview_relative = preview_cache.get(preview_key)
        if preview_relative is None:
            preview_name = f"{cached['sha256']}-p{page_number:04d}.png"
            _render_page_preview(cached["body"], page_number, previews_dir / preview_name)
            preview_relative = f"page-previews/{preview_name}"
            preview_cache[preview_key] = preview_relative
        receipt["status"] = "document_page_ready"
        receipt["preview_path"] = preview_relative
        queue.append(
            {
                "schema_version": 1,
                "supplier": row["supplier"],
                "internal_id": row["internal_id"],
                "report_candidate_id": row["report_candidate_id"],
                "source_code": source_code,
                "document_url": url,
                "document": receipt["document"],
                "page_number": page_number,
                "preview_path": preview_relative,
                "bbox": None,
                "crop_path": None,
                "bbox_review": {
                    "approved": False,
                    "reviewer": "",
                    "reviewed_at": None,
                    "checks": {"page_identity_exact": None, "bbox_is_product_only": None},
                },
            }
        )
        receipts.append(receipt)
    queue.sort(key=lambda value: (str(value["supplier"]), str(value["internal_id"])))
    return receipts, queue


def build_global_search_queue(
    loaded: LoadedInputs,
    candidate_rows: Sequence[Mapping[str, object]],
    document_queue: Sequence[Mapping[str, object]],
    receipts: Sequence[Mapping[str, object]],
) -> list[dict]:
    """Superpone 6C sobre las 776 identidades sin sustituir resultados 6B."""

    queue_by_id = {
        str(row["internal_id"]): json.loads(json.dumps(row))
        for row in loaded.review_search_rows
    }
    failed_6b = {
        str(row["internal_id"])
        for row in loaded.review_candidate_rows
        if row.get("automatic_gate", {}).get("passed") is False
    }
    new_candidates: dict[str, list[Mapping[str, object]]] = {}
    for candidate in candidate_rows:
        new_candidates.setdefault(str(candidate["internal_id"]), []).append(candidate)
    documents = {str(row["internal_id"]): row for row in document_queue}
    receipts_by_id = {str(row["internal_id"]): row for row in receipts}
    normalized = {str(row["internal_id"]): row for row in loaded.normalized_rows}
    for internal_id, row in queue_by_id.items():
        row["task6b_technical_fail_preserved"] = internal_id in failed_6b
        row["task6c_overlay_applied"] = internal_id in normalized
        row["task6c_candidate_ids"] = sorted(
            str(candidate["candidate_id"])
            for candidate in new_candidates.get(internal_id, [])
        )
        receipt = receipts_by_id.get(internal_id)
        row["task6c_receipt_status"] = receipt.get("status") if receipt else None
        if internal_id not in normalized:
            continue
        candidates = new_candidates.get(internal_id, [])
        if candidates:
            if any(candidate.get("identity_configuration_conflict") for candidate in candidates):
                row["next_action"] = "identity_configuration_conflict"
            elif any(candidate.get("global_gate_blocked") for candidate in candidates):
                row["next_action"] = "human_review_quality_exception_or_search"
            elif any(candidate.get("automatic_gate", {}).get("passed") for candidate in candidates):
                row["next_action"] = "human_review_candidates"
            else:
                row["next_action"] = "human_review_quality_exception_or_search"
        elif internal_id in documents:
            row["next_action"] = "document_bbox_review"
        elif receipt and receipt.get("status") == "document_semantic_blocked":
            row["next_action"] = "additional_web_search"
        elif receipt and receipt.get("reason") == "product_link_unverified":
            row["next_action"] = "product_link_unverified"
        elif normalized[internal_id].get("terminal_status") == "collision_requires_signature":
            row["next_action"] = "additional_web_search_collision"
        else:
            row["next_action"] = "generation_reference_prep_pending_exhaustion_review"
        row["internet_exhausted"] = False
    result = sorted(queue_by_id.values(), key=lambda value: (str(value["supplier"]), str(value["internal_id"])))
    if len(result) != len(loaded.inventory_rows) or len({row["internal_id"] for row in result}) != len(result):
        raise ValueError("Cola global debe contener exactamente una fila por identidad")
    if not failed_6b <= {row["internal_id"] for row in result if row["task6b_technical_fail_preserved"]}:
        raise ValueError("Overlay perdió FAIL técnicos de Task 6B")
    return result


_OPERATIONAL_LOGICAL_KEYS = {
    "completed_at",
    "created_at",
    "fetched_at",
    "researched_at",
    "reviewed_at",
    "started_at",
}


def _logical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _logical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in _OPERATIONAL_LOGICAL_KEYS
        }
    if isinstance(value, list):
        return [_logical_value(item) for item in value]
    return value


def logical_intake_sha256(material: Mapping[str, object]) -> str:
    payload = json.dumps(
        _logical_value(material),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_declared_originals(
    originals_dir: Path,
    candidate_rows: Sequence[Mapping[str, object]],
    receipts: Sequence[Mapping[str, object]],
) -> None:
    declared = {
        str(row["original"]["object_name"])
        for row in candidate_rows
        if isinstance(row.get("original"), Mapping)
    }
    declared.update(
        str(row["original"]["object_name"])
        for row in receipts
        if isinstance(row.get("original"), Mapping)
    )
    originals_dir = Path(originals_dir)
    actual = {path.name for path in originals_dir.glob("*") if path.is_file()} if originals_dir.is_dir() else set()
    if actual != declared:
        raise ValueError(
            f"Original faltante/adicional no declarado: faltantes={sorted(declared-actual)}, "
            f"adicionales={sorted(actual-declared)}"
        )


def validate_no_approvals(values: Sequence[Mapping[str, object]]) -> None:
    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            if value.get("approved") is True:
                raise ValueError("approved=true no permitido en Task 6C")
            if "reviewer" in value and value.get("reviewer") not in {"", None}:
                raise ValueError("reviewer no puede asignarse en Task 6C")
            if "reviewed_at" in value and value.get("reviewed_at") is not None:
                raise ValueError("reviewed_at no puede asignarse en Task 6C")
            if "approved" in value and "checks" in value:
                checks = value.get("checks")
                if not isinstance(checks, Mapping) or any(
                    check_value is not None for check_value in checks.values()
                ):
                    raise ValueError("checks humanos deben permanecer null en Task 6C")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(list(values))


def validate_new_output(output_dir: Path, protected_paths: Sequence[Path]) -> Path:
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError(f"La salida ya existe: {output}")
    for path in protected_paths:
        protected = Path(path).resolve()
        if output == protected or output in protected.parents or protected in output.parents:
            raise ValueError(f"La salida se solapa con entrada protegida: {protected}")
    return output


def validate_cache_isolation(output_dir: Path, cache_from: Path | None) -> Path | None:
    """Normaliza la fuente de replay y rechaza cualquier solape con la salida."""

    if cache_from is None:
        return None
    output = Path(output_dir).resolve()
    declared_source = Path(cache_from).resolve()
    source = (
        declared_source / "http-cache"
        if (declared_source / "http-cache").is_dir()
        else declared_source
    )
    if any(
        output == candidate or output in candidate.parents or candidate in output.parents
        for candidate in (declared_source, source)
    ):
        raise ValueError("La fuente de cache se solapa con la salida")
    return source


def _write_union_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({str(key) for row in rows for key in row})
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field)) for field in fields})


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(output_dir).as_posix(): _sha256_file(path)
        for path in sorted(Path(output_dir).rglob("*"), key=lambda value: value.as_posix())
        if path.is_file() and path.name not in {"artifact-hashes.json", "FAILED.json"}
    }


def _reference_duplicate_clusters(
    candidates: Sequence[dict],
    loaded: LoadedInputs,
) -> list[dict]:
    records: list[dict] = []
    for candidate in candidates:
        records.append(
            {
                "batch": "task6c",
                "internal_id": candidate["internal_id"],
                "sha256": candidate["original"]["sha256"],
                "visual_signature_sha256": candidate["visual_signature"]["sha256"],
                "candidate": candidate,
            }
        )
    for candidate in loaded.review_candidate_rows:
        records.append(
            {
                "batch": "task6a",
                "internal_id": candidate["internal_id"],
                "sha256": candidate["original"]["sha256"],
                "visual_signature_sha256": candidate["visual_signature"]["sha256"],
                "candidate": None,
            }
        )
    for row in loaded.inventory_rows:
        asset = row.get("current_asset")
        if not isinstance(asset, Mapping):
            continue
        sha256 = str(asset.get("actual_sha256") or asset.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            continue
        records.append(
            {
                "batch": "task5_active",
                "internal_id": row["internal_id"],
                "sha256": sha256,
                "visual_signature_sha256": (row.get("visual_signature") or {}).get("sha256"),
                "candidate": None,
            }
        )
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(str(record["sha256"]), []).append(record)
    result = []
    for sha256, values in sorted(grouped.items()):
        identities = sorted({str(value["internal_id"]) for value in values})
        if len(identities) < 2:
            continue
        signatures = sorted({str(value["visual_signature_sha256"]) for value in values})
        conflict = len(signatures) > 1
        potential = len(signatures) == 1
        declared_evidence = frozenset(identities) == ATANA_SHARED_IDS
        for value in values:
            candidate = value["candidate"]
            if candidate is not None and (conflict or potential):
                candidate["duplicate_conflict"] = conflict
                candidate["potential_shared_visual"] = potential
                candidate["declared_shared_visual_evidence"] = declared_evidence
                candidate["shared_visual_group"] = None
                candidate["global_gate_blocked"] = True
        result.append(
            {
                "sha256": sha256,
                "internal_ids": identities,
                "visual_signature_sha256s": signatures,
                "batches": sorted({str(value["batch"]) for value in values}),
                "duplicate_conflict": conflict,
                "potential_shared_visual": potential,
                "declared_shared_visual_evidence": declared_evidence,
                "shared_visual_group": None,
            }
        )
    return result


def _verified_reference_image(root: Path, relative_path: object, expected_sha256: object) -> Path:
    """Resuelve una imagen declarada dentro de su raíz y verifica su contenido físico."""

    root = Path(root).resolve(strict=True)
    relative = Path(str(relative_path or ""))
    expected = str(expected_sha256 or "").casefold()
    if (
        not str(relative_path or "")
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise ValueError("Ruta/SHA de referencia perceptual inválida")
    try:
        path = (root / relative).resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"Imagen de referencia perceptual ausente: {relative.as_posix()}") from exc
    reparse = bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    if path == root or root not in path.parents or path.is_symlink() or reparse or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Ruta de referencia perceptual no confinada: {relative.as_posix()}")
    if _sha256_file(path) != expected:
        raise ValueError(f"SHA de referencia perceptual divergente: {relative.as_posix()}")
    return path


def _reference_perceptual_clusters(
    candidates: Sequence[Mapping[str, object]],
    loaded: LoadedInputs,
    *,
    originals_dir: Path,
    research_dir: Path,
    assets_dir: Path,
) -> list[dict]:
    """Compara dHash de 6C, 6A y activos Task 5 sin decidir bloqueo ni reuse."""

    declared: list[tuple[str, str, object, object, object, Path]] = []
    for candidate in candidates:
        original = candidate.get("original")
        signature = candidate.get("visual_signature")
        if not isinstance(original, Mapping) or not isinstance(signature, Mapping):
            raise ValueError("Candidato 6C inválido para comparación perceptual")
        declared.append(
            (
                "task6c",
                str(candidate.get("internal_id") or ""),
                original.get("object_name"),
                original.get("sha256"),
                signature.get("sha256"),
                Path(originals_dir),
            )
        )
    for candidate in loaded.review_candidate_rows:
        original = candidate.get("original")
        signature = candidate.get("visual_signature")
        if not isinstance(original, Mapping) or not isinstance(signature, Mapping):
            raise ValueError("Candidato 6A inválido para comparación perceptual")
        declared.append(
            (
                "task6a",
                str(candidate.get("internal_id") or ""),
                original.get("path"),
                original.get("sha256"),
                signature.get("sha256"),
                Path(research_dir),
            )
        )
    for row in loaded.inventory_rows:
        asset = row.get("current_asset")
        signature = row.get("visual_signature")
        if not isinstance(asset, Mapping) or not asset.get("path"):
            continue
        if not isinstance(signature, Mapping):
            raise ValueError("Firma de activo Task 5 inválida para comparación perceptual")
        declared.append(
            (
                "task5_active",
                str(row.get("internal_id") or ""),
                asset.get("path"),
                asset.get("actual_sha256") or asset.get("sha256"),
                signature.get("sha256"),
                Path(assets_dir),
            )
        )

    hash_cache: dict[tuple[str, str], str] = {}
    grouped: dict[str, list[dict]] = {}
    for batch, internal_id, relative, sha256, signature_sha256, root in declared:
        path = _verified_reference_image(root, relative, sha256)
        cache_key = (str(path), str(sha256))
        dhash = hash_cache.get(cache_key)
        if dhash is None:
            dhash = _difference_hash(path)
            hash_cache[cache_key] = dhash
        grouped.setdefault(dhash, []).append(
            {
                "batch": batch,
                "internal_id": internal_id,
                "sha256": str(sha256),
                "visual_signature_sha256": str(signature_sha256),
            }
        )

    clusters = []
    for dhash, values in sorted(grouped.items()):
        internal_ids = sorted({value["internal_id"] for value in values})
        if len(internal_ids) < 2:
            continue
        clusters.append(
            {
                "dhash": dhash,
                "internal_ids": internal_ids,
                "sha256s": sorted({value["sha256"] for value in values}),
                "visual_signature_sha256s": sorted(
                    {value["visual_signature_sha256"] for value in values}
                ),
                "batches": sorted({value["batch"] for value in values}),
                "records": sorted(
                    values,
                    key=lambda value: (value["batch"], value["internal_id"], value["sha256"]),
                ),
                "decision_effect": "inspection_only",
            }
        )
    return clusters


def _skip_receipts(rows: Sequence[Mapping[str, object]]) -> list[dict]:
    return [
        {
            "schema_version": 1,
            "report_candidate_id": row["report_candidate_id"],
            "supplier": row["supplier"],
            "internal_id": row["internal_id"],
            "acquisition_kind": "none",
            "status": "skipped_no_network",
            "reason": row["terminal_status"],
            "requested_product_url": None,
            "final_product_url": None,
            "requested_url": None,
            "final_url": None,
            "page_number": None,
            "document": None,
            "preview_path": None,
            "crop_path": None,
            "original": None,
        }
        for row in rows
        if row["acquisition_kind"] == "none"
    ]


def _percentile_95(values: Sequence[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _validate_document_outputs(
    documents_dir: Path,
    previews_dir: Path,
    receipts: Sequence[Mapping[str, object]],
    queue: Sequence[Mapping[str, object]],
) -> None:
    declared_documents = {
        str(row["document"]["object_name"])
        for row in receipts
        if isinstance(row.get("document"), Mapping)
    }
    actual_documents = {path.name for path in documents_dir.glob("*.pdf")}
    if actual_documents != declared_documents:
        raise ValueError("Documento faltante/adicional no declarado")
    declared_previews = {Path(str(row["preview_path"])).name for row in queue}
    actual_previews = {path.name for path in previews_dir.glob("*.png")}
    if actual_previews != declared_previews:
        raise ValueError("Preview faltante/adicional no declarado")


def _failure_receipt(output_dir: Path, stage: str, exc: Exception, started_at: str) -> None:
    try:
        if output_dir.is_dir() and not (output_dir / "FAILED.json").exists():
            _write_json(
                output_dir / "FAILED.json",
                {
                    "schema_version": 1,
                    "status": "failed",
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "started_at": started_at,
                },
            )
    except OSError:
        return


def run_intake(
    *,
    inventory_dir: Path,
    research_dir: Path,
    review_dir: Path,
    labenze_pdf: Path,
    requiez_pdf: Path,
    store_path: Path,
    assets_dir: Path,
    labenze_report_path: Path,
    requiez_report_path: Path,
    document_audit_path: Path,
    output_dir: Path,
    expected_labenze_report_sha256: str,
    expected_requiez_report_sha256: str,
    expected_inventory_sha256: str = CANONICAL_INVENTORY_SHA256,
    expected_research_logical_sha256: str = CANONICAL_RESEARCH_LOGICAL_SHA256,
    expected_review_logical_sha256: str = CANONICAL_REVIEW_LOGICAL_SHA256,
    offline: bool = False,
    cache_from: Path | None = None,
    started_at: str | None = None,
    http_timeout: float = 30.0,
) -> dict[str, object]:
    """Ejecuta el overlay local write-once y deja evidencia reproducible."""

    paths = {
        "assets": Path(assets_dir).resolve(),
        "document_audit": Path(document_audit_path).resolve(),
        "inventory": Path(inventory_dir).resolve(),
        "labenze_pdf": Path(labenze_pdf).resolve(),
        # Preservar la ruta léxica: load_strict_json debe hacer lstat al enlace,
        # no al target que produciría Path.resolve().
        "labenze_report": Path(labenze_report_path).absolute(),
        "requiez_pdf": Path(requiez_pdf).resolve(),
        "requiez_report": Path(requiez_report_path).absolute(),
        "research": Path(research_dir).resolve(),
        "review": Path(review_dir).resolve(),
        "store": Path(store_path).resolve(),
    }
    output_dir = validate_new_output(output_dir, list(paths.values()))
    cache_source = validate_cache_isolation(output_dir, cache_from)
    timestamp = started_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    stage = "validate_document_audit"
    if _sha256_file(paths["document_audit"]) != DOCUMENT_AUDIT_SHA256:
        raise ValueError("SHA-256 de auditoría documental divergente")
    before = {name: _tree_fingerprint(path) for name, path in sorted(paths.items())}
    stage = "load_inputs"
    loaded = load_normalized_inputs(
        inventory_dir=paths["inventory"],
        research_dir=paths["research"],
        review_dir=paths["review"],
        labenze_pdf=paths["labenze_pdf"],
        requiez_pdf=paths["requiez_pdf"],
        labenze_report_path=paths["labenze_report"],
        requiez_report_path=paths["requiez_report"],
        expected_labenze_report_sha256=expected_labenze_report_sha256,
        expected_requiez_report_sha256=expected_requiez_report_sha256,
        expected_inventory_sha256=expected_inventory_sha256,
        expected_research_logical_sha256=expected_research_logical_sha256,
        expected_review_logical_sha256=expected_review_logical_sha256,
    )
    try:
        stage = "create_output"
        output_dir.mkdir(parents=True, exist_ok=False)
        originals_dir = output_dir / "originals"
        documents_dir = output_dir / "documents"
        previews_dir = output_dir / "page-previews"
        originals_dir.mkdir()
        documents_dir.mkdir()
        previews_dir.mkdir()
        stage = "copy_http_cache"
        if offline and cache_source is None:
            raise ValueError("Replay offline exige --cache-from")
        _copy_cache(cache_source, output_dir / "http-cache")
        client = CachedHttpClient(
            output_dir / "http-cache",
            transport=UrllibTransport(timeout=http_timeout),
            offline=offline,
            allowed_hosts=SOURCE_HTTP_HOSTS,
            max_attempts=4,
            backoff_seconds=2,
        )
        stage = "acquire_direct_images"
        direct_receipts, candidates = acquire_direct_images(
            loaded.normalized_rows,
            client,
            originals_dir,
        )
        stage = "acquire_documents"
        document_receipts, document_queue = acquire_document_pages(
            loaded.normalized_rows,
            client,
            documents_dir,
            previews_dir,
        )
        receipts = direct_receipts + document_receipts + _skip_receipts(loaded.normalized_rows)
        receipts.sort(key=lambda value: (str(value["supplier"]), str(value["internal_id"])))
        if len(receipts) != len(loaded.normalized_rows):
            raise ValueError("Cada residual debe tener exactamente un receipt terminal")
        stage = "duplicates_and_global_queue"
        duplicate_clusters = analyze_duplicate_clusters(candidates, originals_dir)
        duplicate_clusters["reference_exact"] = _reference_duplicate_clusters(candidates, loaded)
        duplicate_clusters["reference_perceptual"] = _reference_perceptual_clusters(
            candidates,
            loaded,
            originals_dir=originals_dir,
            research_dir=paths["research"],
            assets_dir=paths["assets"],
        )
        global_queue = build_global_search_queue(
            loaded,
            candidates,
            document_queue,
            receipts,
        )
        stage = "reconcile_assets"
        validate_declared_originals(originals_dir, candidates, receipts)
        _validate_document_outputs(documents_dir, previews_dir, receipts, document_queue)
        validate_no_approvals(
            list(loaded.normalized_rows) + list(candidates) + list(document_queue) + list(global_queue)
        )
        stage = "render_contact_sheets"
        contact_sheets, contact_index = render_candidate_contact_sheets(
            output_dir,
            candidates,
            originals_dir,
        )
        decisions = {"schema_version": 1, "decisions": []}
        stage = "write_outputs"
        _write_jsonl(output_dir / "normalized-research.jsonl", loaded.normalized_rows)
        _write_union_csv(output_dir / "normalized-research.csv", loaded.normalized_rows)
        _write_jsonl(output_dir / "acquisition-receipts.jsonl", receipts)
        _write_union_csv(output_dir / "acquisition-receipts.csv", receipts)
        _write_jsonl(output_dir / "candidate-review.jsonl", candidates)
        _write_union_csv(output_dir / "candidate-review.csv", candidates)
        _write_jsonl(output_dir / "global-search-queue.jsonl", global_queue)
        _write_union_csv(output_dir / "global-search-queue.csv", global_queue)
        _write_jsonl(output_dir / "document-extraction-queue.jsonl", document_queue)
        _write_union_csv(output_dir / "document-extraction-queue.csv", document_queue)
        _write_json(output_dir / "duplicate-clusters.json", duplicate_clusters)
        _write_json(output_dir / "decisions.json", decisions)
        _write_json(output_dir / "contact-sheets.json", contact_sheets)
        _write_json(output_dir / "contact-sheet-index.json", contact_index)
        logical_material = {
            "normalized_research": loaded.normalized_rows,
            "acquisition_receipts": receipts,
            "candidate_review": candidates,
            "global_search_queue": global_queue,
            "document_extraction_queue": document_queue,
            "duplicate_clusters": duplicate_clusters,
            "decisions": decisions,
            "contact_sheets": contact_sheets,
            "contact_sheet_index": contact_index,
        }
        logical_sha = logical_intake_sha256(logical_material)
        stage = "snapshot_inputs_after"
        after = {name: _tree_fingerprint(path) for name, path in sorted(paths.items())}
        unchanged = before == after
        image_sizes = sorted(int(path.stat().st_size) for path in originals_dir.glob("*") if path.is_file())
        document_sizes = sorted(
            int(path.stat().st_size) for path in documents_dir.glob("*.pdf") if path.is_file()
        )
        routing = dict(sorted(Counter(row["acquisition_kind"] for row in loaded.normalized_rows).items()))
        receipt_statuses = dict(sorted(Counter(row["status"] for row in receipts).items()))
        document_dispositions = dict(
            sorted(
                Counter(
                    row["candidate"]["document_disposition"]
                    for row in loaded.normalized_rows
                    if row["acquisition_kind"] == "document_page"
                ).items()
            )
        )
        summary = {
            "schema_version": 1,
            "status": "passed" if unchanged else "failed",
            "offline": bool(offline),
            "started_at": timestamp,
            "logical_intake_sha256": logical_sha,
            "counts": {
                "normalized_residuals": len(loaded.normalized_rows),
                "global_queue": len(global_queue),
                "direct_candidates": len(candidates),
                "direct_technical_passed": sum(
                    candidate["automatic_gate"]["passed"] is True for candidate in candidates
                ),
                "unique_originals": len(list(originals_dir.glob("*"))),
                "document_queue": len(document_queue),
                "unique_documents": len(list(documents_dir.glob("*.pdf"))),
                "page_previews": len(list(previews_dir.glob("*.png"))),
                "contact_sheets": len(contact_sheets["sheets"]),
                "task6b_technical_fails_preserved": sum(
                    row["task6b_technical_fail_preserved"] for row in global_queue
                ),
                "approved": 0,
            },
            "routing": routing,
            "receipt_statuses": receipt_statuses,
            "document_audit_dispositions": document_dispositions,
            "bytes": {
                "originals_total": sum(image_sizes),
                "originals_max": max(image_sizes, default=0),
                "originals_p95": _percentile_95(image_sizes),
                "documents_total": sum(document_sizes),
                "documents_max": max(document_sizes, default=0),
                "documents_p95": _percentile_95(document_sizes),
            },
            "input_hashes": {
                "inventory_jsonl": expected_inventory_sha256,
                "research_logical": expected_research_logical_sha256,
                "review_logical": expected_review_logical_sha256,
                "labenze_report": expected_labenze_report_sha256,
                "requiez_report": expected_requiez_report_sha256,
                "document_audit": DOCUMENT_AUDIT_SHA256,
            },
            "inputs_before": before,
            "inputs_after": after,
            "inputs_unchanged": unchanged,
            "production_mutations": 0,
            "generation_calls": 0,
            "promotion_calls": 0,
        }
        _write_json(output_dir / "summary.json", summary)
        _write_json(output_dir / "artifact-hashes.json", _artifact_hashes(output_dir))
        if not unchanged:
            raise RuntimeError("PDFs/DB/assets/inventario/research/review/reportes cambiaron")
        return summary
    except Exception as exc:
        _failure_receipt(output_dir, stage, exc, timestamp)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-dir", type=Path, required=True)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--labenze-pdf", type=Path, required=True)
    parser.add_argument("--requiez-pdf", type=Path, required=True)
    parser.add_argument("--store", dest="store_path", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--labenze-report", dest="labenze_report_path", type=Path, required=True)
    parser.add_argument("--requiez-report", dest="requiez_report_path", type=Path, required=True)
    parser.add_argument("--document-audit", dest="document_audit_path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--labenze-report-sha256", required=True)
    parser.add_argument("--requiez-report-sha256", required=True)
    parser.add_argument("--expected-inventory-sha256", default=CANONICAL_INVENTORY_SHA256)
    parser.add_argument("--expected-research-logical-sha256", default=CANONICAL_RESEARCH_LOGICAL_SHA256)
    parser.add_argument("--expected-review-logical-sha256", default=CANONICAL_REVIEW_LOGICAL_SHA256)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cache-from", type=Path)
    parser.add_argument("--http-timeout", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = run_intake(
        inventory_dir=args.inventory_dir,
        research_dir=args.research_dir,
        review_dir=args.review_dir,
        labenze_pdf=args.labenze_pdf,
        requiez_pdf=args.requiez_pdf,
        store_path=args.store_path,
        assets_dir=args.assets_dir,
        labenze_report_path=args.labenze_report_path,
        requiez_report_path=args.requiez_report_path,
        document_audit_path=args.document_audit_path,
        output_dir=args.output_dir,
        expected_labenze_report_sha256=args.labenze_report_sha256,
        expected_requiez_report_sha256=args.requiez_report_sha256,
        expected_inventory_sha256=args.expected_inventory_sha256,
        expected_research_logical_sha256=args.expected_research_logical_sha256,
        expected_review_logical_sha256=args.expected_review_logical_sha256,
        offline=args.offline,
        cache_from=args.cache_from,
        http_timeout=args.http_timeout,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def normalize_report_payloads(
    inventory_rows: Sequence[Mapping[str, object]],
    research_rows: Sequence[Mapping[str, object]],
    report_payloads: Mapping[str, Mapping[str, object]],
    report_hashes: Mapping[str, str],
) -> list[dict]:
    """Adapta ambos reportes usando exclusivamente la identidad del inventario."""

    inventory_by_id = {str(row["internal_id"]): row for row in inventory_rows}
    research_by_id = {str(row["internal_id"]): row for row in research_rows}
    residual_by_supplier = {
        supplier: {
            str(row["internal_id"])
            for row in research_rows
            if row.get("supplier") == supplier and row.get("status") != "found_exact"
        }
        for supplier in ("labenze", "requiez")
    }
    normalized = []
    for supplier in ("labenze", "requiez"):
        payload = report_payloads.get(supplier)
        if not isinstance(payload, Mapping):
            raise ValueError(f"Reporte faltante: {supplier}")
        rows = _validate_report_schema(supplier, payload)
        report_ids = {str(row["internal_id"]) for row in rows}
        if report_ids != residual_by_supplier[supplier]:
            missing = sorted(residual_by_supplier[supplier] - report_ids)
            extra = sorted(report_ids - residual_by_supplier[supplier])
            raise ValueError(f"IDs faltantes/extra en reporte {supplier}: faltantes={missing}, extra={extra}")
        input_hashes = payload.get("input_hashes")
        if not isinstance(input_hashes, dict):
            raise ValueError(f"input_hashes inválido en reporte {supplier}")
        declared_inventory = (input_hashes.get("canonical_inventory_jsonl") or {}).get("sha256")
        declared_research = (input_hashes.get("candidates_jsonl") or {}).get("sha256")
        if declared_inventory != CANONICAL_INVENTORY_SHA256 or declared_research != CANONICAL_RESEARCH_JSONL_SHA256:
            raise ValueError(f"input_hashes divergentes en reporte {supplier}")
        for report_row in rows:
            internal_id = str(report_row["internal_id"])
            inventory_row = inventory_by_id.get(internal_id)
            research_row = research_by_id.get(internal_id)
            if inventory_row is None or research_row is None or not _identity_matches(report_row, inventory_row):
                raise ValueError(f"identidad/configuración divergente: {internal_id}")
            if inventory_row.get("code_status") == "verified":
                query_sku = str(report_row.get("query_sku") or inventory_row.get("source_code") or "")
                normalized_query = "".join(character for character in query_sku.upper() if character.isalnum())
                normalized_source = "".join(character for character in str(inventory_row["source_code"]).upper() if character.isalnum())
                if normalized_query != normalized_source:
                    raise ValueError(f"query_sku no coincide con identidad verificada: {internal_id}")
            elif inventory_row.get("sku") != "":
                raise ValueError(f"needs_review elevó SKU canónico: {internal_id}")
            normalized.append(
                _normalize_row(report_row, inventory_row, str(report_hashes[supplier]))
            )
    normalized.sort(key=lambda row: (str(row["supplier"]), str(row["internal_id"])))
    validate_normalized_routing(normalized)
    return normalized


def _validate_review(
    review_dir: Path,
    inventory_rows: Sequence[Mapping[str, object]],
    *,
    expected_review_logical_sha256: str,
) -> tuple[list[dict], list[dict]]:
    _validate_artifact_manifest(review_dir, nested=False)
    summary = _load_json(review_dir / "summary.json", "summary Task 6B")
    if (
        summary.get("status") != "passed"
        or summary.get("inputs_unchanged") is not True
        or summary.get("logical_review_sha256") != expected_review_logical_sha256
    ):
        raise ValueError("Summary/hash lógico Task 6B divergente")
    search_rows = _load_jsonl(review_dir / "search-queue.jsonl", "cola Task 6B")
    candidate_rows = _load_jsonl(review_dir / "candidate-review.jsonl", "candidatos Task 6B")
    inventory_ids = {str(row["internal_id"]) for row in inventory_rows}
    if len(search_rows) != len(inventory_ids) or {str(row.get("internal_id")) for row in search_rows} != inventory_ids:
        raise ValueError("Task 6B no cubre exactamente el inventario")
    for row in candidate_rows:
        review = row.get("review")
        if not isinstance(review, dict) or review.get("approved") is not False:
            raise ValueError("Task 6B contiene candidato preaprobado")
    return search_rows, candidate_rows


def load_normalized_inputs(
    *,
    inventory_dir: Path,
    research_dir: Path,
    review_dir: Path,
    labenze_pdf: Path,
    requiez_pdf: Path,
    labenze_report_path: Path,
    requiez_report_path: Path,
    expected_labenze_report_sha256: str,
    expected_requiez_report_sha256: str,
    expected_inventory_sha256: str = CANONICAL_INVENTORY_SHA256,
    expected_research_logical_sha256: str = CANONICAL_RESEARCH_LOGICAL_SHA256,
    expected_review_logical_sha256: str = CANONICAL_REVIEW_LOGICAL_SHA256,
) -> LoadedInputs:
    """Valida artefactos 5/6A/6B y devuelve las 656 filas normalizadas."""

    inventory_dir = Path(inventory_dir).resolve()
    research_dir = Path(research_dir).resolve()
    review_dir = Path(review_dir).resolve()
    pdf_paths = {"labenze": Path(labenze_pdf).resolve(), "requiez": Path(requiez_pdf).resolve()}
    pdf_hashes = {supplier: _sha256_file(path) for supplier, path in pdf_paths.items()}
    if pdf_hashes != CANONICAL_PDF_SHA256:
        raise ValueError(f"SHA de PDFs canónicos divergente: {pdf_hashes}")
    inventory_rows, _ = _validate_inventory(
        inventory_dir,
        expected_inventory_sha256=expected_inventory_sha256,
        pdf_hashes=pdf_hashes,
    )
    if _sha256_file(research_dir / "candidates.jsonl") != CANONICAL_RESEARCH_JSONL_SHA256:
        raise ValueError("SHA físico candidates.jsonl Task 6A divergente")
    research_rows, _ = _validate_research(
        research_dir,
        inventory_rows,
        expected_inventory_sha256=expected_inventory_sha256,
        expected_research_logical_sha256=expected_research_logical_sha256,
    )
    review_search_rows, review_candidate_rows = _validate_review(
        review_dir,
        inventory_rows,
        expected_review_logical_sha256=expected_review_logical_sha256,
    )
    report_paths = {
        "labenze": Path(labenze_report_path).absolute(),
        "requiez": Path(requiez_report_path).absolute(),
    }
    report_hashes = {
        "labenze": str(expected_labenze_report_sha256).casefold(),
        "requiez": str(expected_requiez_report_sha256).casefold(),
    }
    report_payloads = {
        supplier: load_strict_json(path, expected_sha256=report_hashes[supplier])
        for supplier, path in report_paths.items()
    }
    normalized_rows = normalize_report_payloads(
        inventory_rows,
        research_rows,
        report_payloads,
        report_hashes,
    )
    return LoadedInputs(
        inventory_rows=inventory_rows,
        research_rows=research_rows,
        review_search_rows=review_search_rows,
        review_candidate_rows=review_candidate_rows,
        report_payloads=report_payloads,
        report_hashes=report_hashes,
        normalized_rows=normalized_rows,
    )


if __name__ == "__main__":
    raise SystemExit(main())
