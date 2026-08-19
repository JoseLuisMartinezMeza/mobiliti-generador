"""Investiga y adquiere candidatos visuales exactos sin mutar el catálogo activo."""

from __future__ import annotations

import argparse
import hashlib
import base64
import csv
from email.utils import parsedate_to_datetime
import io
import ipaddress
import json
import re
import socket
import shutil
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import quote
from urllib.parse import parse_qsl, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError


CANONICAL_INVENTORY_SHA256 = "476013bf863552d4e622f510c39a019fc1549859714edbd1e8b76994d31a0812"
EXPECTED_SUPPLIER_COUNTS = {"labenze": 462, "requiez": 314}
MAX_ORIGINAL_BYTES = 8 * 1024 * 1024
MAX_IMAGE_SIDE = 8192
MAX_IMAGE_PIXELS = 25_000_000


@dataclass(frozen=True)
class IdentityCandidate:
    source_id: str
    code: str = ""
    short_code: str = ""
    name: str = ""
    payload: object = None


@dataclass(frozen=True)
class IdentityMatch:
    status: str
    candidate: IdentityCandidate | None
    matched_field: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ResearchCandidate:
    source_name: str
    source_kind: str
    source_id: str
    query: str
    matched_field: str
    product_url: str
    image_source_url: str
    evidence: dict
    approved: bool = False


@dataclass(frozen=True)
class CandidateEnumeration:
    status: str
    candidates: list[ResearchCandidate]
    reason: str


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class DownloadResult:
    sha256: str
    path: Path
    mime: str
    dimensions: dict[str, int]
    bytes: int
    requested_url: str
    final_url: str


class CachedHttpClient:
    """Cliente HTTP con cache de evidencia inmutable y replay offline."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        transport,
        offline: bool = False,
        sleeper=time.sleep,
        clock=lambda: datetime.now(timezone.utc),
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        allowed_hosts: Iterable[str] | None = None,
        resolver=None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser positivo")
        self.cache_dir = Path(cache_dir)
        self.transport = transport
        self.offline = bool(offline)
        self.sleeper = sleeper
        self.clock = clock
        self.max_attempts = max_attempts
        self.backoff_seconds = float(backoff_seconds)
        self.allowed_hosts = None if allowed_hosts is None else {str(host).lower().rstrip(".") for host in allowed_hosts}
        self.resolver = resolver

    def _path(self, url: str) -> Path:
        key = hashlib.sha256(str(url).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _read(self, path: Path, url: str) -> HttpResponse:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            body = base64.b64decode(entry["body_base64"], validate=True)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cache HTTP inválido: {path}") from exc
        if entry.get("request_url") != url or hashlib.sha256(body).hexdigest() != entry.get("body_sha256"):
            raise ValueError(f"Fallo de integridad en cache HTTP: {path}")
        return HttpResponse(
            status=int(entry["status"]),
            url=str(entry["response_url"]),
            headers={str(key): str(value) for key, value in (entry.get("headers") or {}).items()},
            body=body,
        )

    def _write_once(self, path: Path, url: str, response: HttpResponse) -> None:
        if path.exists():
            existing = self._read(path, url)
            if existing != response:
                raise ValueError(f"Cache HTTP inmutable ya contiene otra respuesta: {url}")
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": 1,
            "request_url": url,
            "response_url": response.url,
            "status": response.status,
            "headers": dict(sorted((str(key).casefold(), str(value)) for key, value in response.headers.items())),
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "body_base64": base64.b64encode(response.body).decode("ascii"),
            "fetched_at": self.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        payload = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        path.write_text(payload, encoding="utf-8", newline="\n")

    def get(self, url: str) -> HttpResponse:
        if self.allowed_hosts is not None:
            requested_host, _ = _url_host(url, "URL HTTP")
            if not _host_allowed(requested_host, self.allowed_hosts):
                raise ValueError(f"URL HTTP usa host no permitido: {requested_host}")
            if self.resolver is not None and not self.offline:
                _validate_public_host(requested_host, self.resolver)
        path = self._path(url)
        if path.is_file():
            response = self._read(path, url)
            self._validate_final_response_host(response)
            return response
        if self.offline:
            raise ValueError(f"Falta respuesta en cache offline: {url}")
        last_response: HttpResponse | None = None
        for attempt in range(1, self.max_attempts + 1):
            response = self.transport(url)
            if not isinstance(response, HttpResponse):
                raise TypeError("transport debe devolver HttpResponse")
            self._validate_final_response_host(response)
            last_response = response
            retryable = response.status == 429 or 500 <= response.status <= 599
            if not retryable:
                self._write_once(path, url, response)
                return response
            if attempt < self.max_attempts:
                headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
                retry_after = headers.get("retry-after", "").strip()
                try:
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            retry_at = parsedate_to_datetime(retry_after)
                            now = self.clock()
                            if now.tzinfo is None:
                                now = now.replace(tzinfo=timezone.utc)
                            delay = (retry_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()
                    else:
                        delay = self.backoff_seconds * (2 ** (attempt - 1))
                except (TypeError, ValueError, OverflowError):
                    delay = self.backoff_seconds * (2 ** (attempt - 1))
                self.sleeper(min(60.0, max(0.0, delay)))
        assert last_response is not None
        raise ValueError(f"HTTP agotó reintentos con status {last_response.status}: {url}")

    def _validate_final_response_host(self, response: HttpResponse) -> None:
        if self.allowed_hosts is None:
            return
        final_host, _ = _url_host(response.url, "redirect HTTP final")
        if not _host_allowed(final_host, self.allowed_hosts):
            raise ValueError(f"redirect HTTP usa host no permitido: {final_host}")
        if self.resolver is not None and not self.offline:
            _validate_public_host(final_host, self.resolver)

    def get_json(self, url: str) -> object:
        response = self.get(url)
        if response.status != 200:
            raise ValueError(f"status HTTP inesperado: {response.status}")
        try:
            return json.loads(response.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Respuesta JSON inválida: {url}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_inventory(
    path: Path,
    *,
    expected_sha256: str = CANONICAL_INVENTORY_SHA256,
) -> list[dict]:
    """Carga el inventario Task 5 y fija hash, cardinalidad e identidad."""

    path = Path(path)
    actual_sha256 = _sha256_file(path)
    if actual_sha256.lower() != str(expected_sha256).lower():
        raise ValueError(
            f"SHA-256 de inventario inesperado: esperado={expected_sha256}, actual={actual_sha256}"
        )
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONL inválido en línea {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Fila de inventario inválida en línea {line_number}")
        rows.append(row)
    expected_total = sum(EXPECTED_SUPPLIER_COUNTS.values())
    if len(rows) != expected_total:
        raise ValueError(f"El inventario debe contener exactamente {expected_total} filas; contiene {len(rows)}")
    ids: set[str] = set()
    counts = {supplier: 0 for supplier in EXPECTED_SUPPLIER_COUNTS}
    for row in rows:
        supplier = str(row.get("supplier") or "").strip().lower()
        if supplier not in counts:
            raise ValueError(f"supplier desconocido: {supplier!r}")
        internal_id = str(row.get("internal_id") or "").strip()
        if not internal_id or internal_id in ids:
            raise ValueError(f"internal_id ausente o duplicado: {internal_id!r}")
        ids.add(internal_id)
        counts[supplier] += 1
    if counts != EXPECTED_SUPPLIER_COUNTS:
        raise ValueError(f"Conteos por supplier inesperados: {counts}")
    return rows


def normalize_identity(value: object) -> str:
    """Normaliza sólo compatibilidad Unicode, mayúsculas y caracteres alfanuméricos."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).upper()
    return "".join(character for character in normalized if "A" <= character <= "Z" or "0" <= character <= "9")


def match_exact_identity(query: object, candidates: Iterable[IdentityCandidate]) -> IdentityMatch:
    """Resuelve exclusivamente igualdad única de ``code`` o ``short_code``."""

    normalized_query = normalize_identity(query)
    if not normalized_query:
        return IdentityMatch("rejected", None, reason="empty_identity")
    matches: list[tuple[IdentityCandidate, str]] = []
    for candidate in candidates:
        for field, raw_value in (("code", candidate.code), ("short_code", candidate.short_code)):
            if raw_value and normalize_identity(raw_value) == normalized_query:
                matches.append((candidate, field))
                break
    unique = {candidate.source_id: (candidate, field) for candidate, field in matches}
    if len(unique) == 1:
        candidate, field = next(iter(unique.values()))
        return IdentityMatch("found_exact", candidate, matched_field=field, reason="unique_exact_identity")
    if len(unique) > 1:
        return IdentityMatch("rejected", None, reason="identity_collision")
    return IdentityMatch("rejected", None, reason="no_exact_identity")


def _row_identity(row: Mapping[str, object]) -> str:
    return str(row.get("sku") or row.get("source_code") or "").strip()


def enumerate_shopify_candidates(
    inventory_row: Mapping[str, object],
    products: Iterable[Mapping[str, object]],
    *,
    source_name: str,
    storefront_url: str,
) -> list[ResearchCandidate]:
    """Enumera sólo imágenes unidas explícitamente al variant SKU exacto."""

    query = _row_identity(inventory_row)
    normalized_query = normalize_identity(query)
    if not normalized_query:
        return []
    allow_kl_prefix = source_name.casefold().removeprefix("www.") == "3rin.com.mx"
    result: list[ResearchCandidate] = []
    for product in products:
        vendor = str(product.get("vendor") or "").strip()
        handle = str(product.get("handle") or "").strip().strip("/")
        if not handle:
            continue
        images = product.get("images")
        variants = product.get("variants")
        if not isinstance(images, list) or not isinstance(variants, list):
            continue
        for variant in variants:
            if not isinstance(variant, Mapping):
                continue
            raw_sku = str(variant.get("sku") or "").strip()
            comparison_sku = raw_sku
            prefix_removed = False
            if allow_kl_prefix and normalize_identity(vendor) == "LABENZE":
                prefixed = re.match(r"^\s*KL\s*-\s*(.+)$", raw_sku, flags=re.IGNORECASE)
                if prefixed:
                    comparison_sku = prefixed.group(1)
                    prefix_removed = True
            if normalize_identity(comparison_sku) != normalized_query:
                continue
            variant_id = variant.get("id")
            if variant_id is None:
                continue
            for image in images:
                if not isinstance(image, Mapping):
                    continue
                bound_ids = image.get("variant_ids")
                if not isinstance(bound_ids, list) or str(variant_id) not in {str(value) for value in bound_ids}:
                    continue
                image_url = str(image.get("src") or image.get("url") or "").strip()
                if not image_url:
                    continue
                result.append(
                    ResearchCandidate(
                        source_name=source_name,
                        source_kind="authorized_distributor",
                        source_id=str(product.get("id") or handle),
                        query=query,
                        matched_field="variant.sku",
                        product_url=f"{storefront_url.rstrip('/')}/products/{quote(handle)}?variant={quote(str(variant_id))}",
                        image_source_url=image_url,
                        evidence={
                            "product_id": product.get("id"),
                            "variant_id": variant_id,
                            "variant_sku": raw_sku,
                            "image_id": image.get("id"),
                            "image_variant_ids": list(bound_ids),
                            "declared_vendor": vendor,
                            "kl_prefix_removed": prefix_removed,
                        },
                    )
                )
    return result


def enumerate_explicit_visual_candidates(
    inventory_row: Mapping[str, object],
    records: Iterable[Mapping[str, object]],
    *,
    source_name: str,
    source_kind: str,
) -> list[ResearchCandidate]:
    """Consume el contrato intermedio explícito SKU→firma visual→imagen."""

    query = _row_identity(inventory_row)
    expected_signature = str((inventory_row.get("visual_signature") or {}).get("sha256") or "")
    if not query or not expected_signature:
        return []
    result: list[ResearchCandidate] = []
    for record in records:
        assignments = record.get("assignments")
        if not isinstance(assignments, list):
            continue
        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                continue
            assigned_sku = str(assignment.get("sku") or "")
            signature = str(assignment.get("visual_signature_sha256") or "")
            if normalize_identity(assigned_sku) != normalize_identity(query) or signature != expected_signature:
                continue
            product_url = str(assignment.get("product_url") or "")
            image_source_url = str(assignment.get("image_source_url") or "")
            if not product_url or not image_source_url:
                continue
            result.append(
                ResearchCandidate(
                    source_name=source_name,
                    source_kind=source_kind,
                    source_id=str(record.get("source_id") or record.get("id") or ""),
                    query=query,
                    matched_field="assignments[].sku+visual_signature_sha256",
                    product_url=product_url,
                    image_source_url=image_source_url,
                    evidence={
                        "assigned_sku": assigned_sku,
                        "visual_signature_sha256": signature,
                        "configuration": assignment.get("configuration"),
                    },
                )
            )
    return result


def _image_url(image: object) -> str:
    if isinstance(image, str):
        return image.strip()
    if isinstance(image, Mapping):
        return str(image.get("url") or image.get("src") or image.get("img") or image.get("image") or "").strip()
    return ""


def _requiez_image_identity_evidence(query: str, image_url: str) -> tuple[bool, list[str]]:
    target = normalize_identity(query)
    path_segments = [segment for segment in urlsplit(image_url).path.split("/") if segment]
    normalized_segments = [normalize_identity(segment.rsplit(".", 1)[0]) for segment in path_segments]
    supported = target in normalized_segments
    if not supported and normalized_segments:
        filename = normalized_segments[-1]
        suffix = filename[len(target) :] if filename.startswith(target) else ""
        supported = bool(suffix and suffix in {"A", "B", "C", "D", "E", "F", "FRENTE", "TRASERA", "LATERAL"})
    return supported, normalized_segments


def enumerate_requiez_candidates(
    inventory_row: Mapping[str, object],
    listing: Iterable[Mapping[str, object]],
    details: Mapping[str, Mapping[str, object]],
    *,
    page_observations: Mapping[str, object] | None = None,
) -> CandidateEnumeration:
    """Une listado/detalle Requiez por identidad y evalúa todos los ``imgs[]``."""

    del page_observations  # Una respuesta de SPA nunca participa en la identidad.
    query = _row_identity(inventory_row)
    identity_candidates = [
        IdentityCandidate(
            source_id=str(product.get("id") or product.get("_id") or product.get("uuid") or product.get("code") or ""),
            code=str(product.get("code") or ""),
            short_code=str(product.get("shortCode") or product.get("short_code") or ""),
            name=str(product.get("name") or ""),
            payload=product,
        )
        for product in listing
        if isinstance(product, Mapping)
    ]
    match = match_exact_identity(query, identity_candidates)
    if match.status != "found_exact" or match.candidate is None:
        return CandidateEnumeration(match.status, [], match.reason)
    selected = match.candidate
    detail = details.get(selected.source_id)
    if not isinstance(detail, Mapping):
        return CandidateEnumeration("exhausted", [], "exact_detail_unavailable")
    detail_source_id = str(detail.get("id") or detail.get("_id") or detail.get("uuid") or "")
    if detail_source_id and detail_source_id != selected.source_id:
        return CandidateEnumeration("rejected", [], "detail_identity_mismatch")
    detail_match = match_exact_identity(
        query,
        [
            IdentityCandidate(
                source_id=selected.source_id,
                code=str(detail.get("code") or ""),
                short_code=str(detail.get("shortCode") or detail.get("short_code") or ""),
            )
        ],
    )
    if detail_match.status != "found_exact":
        return CandidateEnumeration("rejected", [], "detail_identity_mismatch")
    images = detail.get("imgs")
    if not isinstance(images, list):
        return CandidateEnumeration("exhausted", [], "detail_without_images")
    canonical_code = str(detail.get("code") or selected.code or query)
    product_url = f"https://requiez.com/producto/{quote(canonical_code, safe='')}"
    result: list[ResearchCandidate] = []
    for position, image in enumerate(images):
        image_url = _image_url(image)
        if not image_url:
            continue
        image_id = image.get("id") if isinstance(image, Mapping) else None
        image_supported, image_path_tokens = _requiez_image_identity_evidence(query, image_url)
        result.append(
            ResearchCandidate(
                source_name="api-productos.requiez.com",
                source_kind="manufacturer_official",
                source_id=selected.source_id,
                query=query,
                matched_field=match.matched_field,
                product_url=product_url,
                image_source_url=image_url,
                evidence={
                    "listing_code": selected.code,
                    "listing_short_code": selected.short_code,
                    "detail_code": detail.get("code"),
                    "detail_short_code": detail.get("shortCode") or detail.get("short_code"),
                    "image_index": position,
                    "image_id": image_id,
                    "image_name": image.get("name") if isinstance(image, Mapping) else None,
                    "image_priority": image.get("prioridad") if isinstance(image, Mapping) else None,
                    "image_product_id": image.get("idProduct") if isinstance(image, Mapping) else None,
                    "image_identity_supported": image_supported,
                    "image_path_tokens": image_path_tokens,
                },
            )
        )
    if not result:
        return CandidateEnumeration("exhausted", [], "detail_without_images")
    return CandidateEnumeration("found_exact", result, "unique_exact_identity")


class RequiezSource:
    """Adapter de listado+detalle oficial Requiez, totalmente cacheable."""

    listing_url = "https://api-productos.requiez.com/productos"
    detail_base_url = "https://api-productos.requiez.com/producto/code"

    def __init__(self, client: CachedHttpClient) -> None:
        self.client = client
        self._listing_value: list[Mapping[str, object]] | None = None

    def _listing(self) -> list[Mapping[str, object]]:
        if self._listing_value is None:
            payload = self.client.get_json(self.listing_url)
            if isinstance(payload, dict):
                payload = payload.get("productos") or payload.get("products") or payload.get("data")
            if not isinstance(payload, list):
                raise ValueError("Listado Requiez no contiene una lista de productos")
            self._listing_value = [value for value in payload if isinstance(value, Mapping)]
        return self._listing_value

    def research(self, row: Mapping[str, object]) -> CandidateEnumeration:
        if str(row.get("supplier") or "").casefold() != "requiez":
            return CandidateEnumeration("exhausted", [], "source_not_applicable")
        query = _row_identity(row)
        listing = self._listing()
        identities = [
            IdentityCandidate(
                source_id=str(product.get("id") or product.get("_id") or product.get("uuid") or product.get("code") or ""),
                code=str(product.get("code") or ""),
                short_code=str(product.get("shortCode") or product.get("short_code") or ""),
                payload=product,
            )
            for product in listing
        ]
        match = match_exact_identity(query, identities)
        if match.status != "found_exact" or match.candidate is None:
            terminal = "rejected" if match.reason == "identity_collision" else "exhausted"
            return CandidateEnumeration(terminal, [], match.reason)
        code = match.candidate.code or match.candidate.short_code
        detail_url = f"{self.detail_base_url}/{quote(code, safe='')}"
        detail_payload = self.client.get_json(detail_url)
        if isinstance(detail_payload, dict) and isinstance(detail_payload.get("producto"), dict):
            detail_payload = detail_payload["producto"]
        elif isinstance(detail_payload, dict) and isinstance(detail_payload.get("product"), dict):
            detail_payload = detail_payload["product"]
        elif isinstance(detail_payload, dict) and isinstance(detail_payload.get("data"), dict):
            detail_payload = detail_payload["data"]
        if not isinstance(detail_payload, Mapping):
            return CandidateEnumeration("rejected", [], "detail_payload_invalid")
        return enumerate_requiez_candidates(
            row,
            listing,
            {match.candidate.source_id: detail_payload},
        )


class ShopifySource:
    """Adapter paginado Shopify que conserva sólo bindings variant→image."""

    def __init__(
        self,
        client: CachedHttpClient,
        *,
        source_name: str,
        storefront_url: str,
        max_pages: int = 100,
    ) -> None:
        self.client = client
        self.source_name = source_name
        self.storefront_url = storefront_url.rstrip("/")
        self.max_pages = max_pages
        self._products_value: list[Mapping[str, object]] | None = None

    def _products(self) -> list[Mapping[str, object]]:
        if self._products_value is not None:
            return self._products_value
        products: list[Mapping[str, object]] = []
        for page in range(1, self.max_pages + 1):
            url = f"{self.storefront_url}/products.json?limit=250&page={page}"
            payload = self.client.get_json(url)
            page_products = payload.get("products") if isinstance(payload, dict) else None
            if not isinstance(page_products, list):
                raise ValueError(f"Página Shopify inválida: {url}")
            if not page_products:
                self._products_value = products
                return products
            products.extend(value for value in page_products if isinstance(value, Mapping))
        raise ValueError(f"Shopify excedió el máximo de {self.max_pages} páginas")

    def research(self, row: Mapping[str, object]) -> CandidateEnumeration:
        if str(row.get("supplier") or "").casefold() != "labenze":
            return CandidateEnumeration("exhausted", [], "source_not_applicable")
        candidates = enumerate_shopify_candidates(
            row,
            self._products(),
            source_name=self.source_name,
            storefront_url=self.storefront_url,
        )
        if candidates:
            variant_ids = {str(candidate.evidence.get("variant_id")) for candidate in candidates}
            if len(variant_ids) > 1:
                return CandidateEnumeration("rejected", candidates, "variant_sku_collision")
            return CandidateEnumeration("found_exact", candidates, "unique_exact_variant_binding")
        return CandidateEnumeration("exhausted", [], "no_explicit_variant_image_binding")


class LabenzeLegacySource:
    """Enumera familias del API legacy sin inferir SKU/configuración por nombre."""

    listing_url = "https://test.diagrama.labenze.com/productos"

    def __init__(self, client: CachedHttpClient, *, enumerate_details: bool = True) -> None:
        self.client = client
        self.enumerate_details = enumerate_details
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        payload = self.client.get_json(self.listing_url)
        if not isinstance(payload, list) or any(not isinstance(value, Mapping) for value in payload):
            raise ValueError("Listado Labenze legacy inválido")
        if self.enumerate_details:
            for product in payload:
                source_id = str(product.get("id") or "")
                if not source_id:
                    raise ValueError("Familia Labenze legacy sin id")
                detail = self.client.get_json(f"{self.listing_url}/{quote(source_id, safe='')}")
                if not isinstance(detail, Mapping) or str(detail.get("id") or "") != source_id:
                    raise ValueError(f"Detalle Labenze legacy incompatible: {source_id}")
        self._loaded = True

    def research(self, row: Mapping[str, object]) -> CandidateEnumeration:
        if str(row.get("supplier") or "").casefold() != "labenze":
            return CandidateEnumeration("exhausted", [], "source_not_applicable")
        self._load()
        return CandidateEnumeration(
            "exhausted",
            [],
            "legacy_family_has_no_explicit_sku_configuration_binding",
        )


class WooCommerceSource:
    """Adapter Arterio que exige variación Labenze, SKU único e imagen propia."""

    base_url = "https://arterio.mx/wp-json/wc/store/v1/products"

    def __init__(self, client: CachedHttpClient, *, max_pages: int = 100) -> None:
        self.client = client
        self.max_pages = max_pages
        self._parents: list[Mapping[str, object]] | None = None
        self._variations: list[Mapping[str, object]] | None = None

    def _paginate(self, query: str = "") -> list[Mapping[str, object]]:
        result: list[Mapping[str, object]] = []
        separator = "&" if query else "?"
        prefix = f"{self.base_url}{query}"
        for page in range(1, self.max_pages + 1):
            url = f"{prefix}{separator}per_page=100&page={page}"
            response = self.client.get(url)
            if response.status != 200:
                raise ValueError(f"status HTTP inesperado: {response.status}")
            try:
                payload = json.loads(response.body.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Página WooCommerce JSON inválida: {url}") from exc
            if not isinstance(payload, list):
                raise ValueError(f"Página WooCommerce inválida: {url}")
            if not payload:
                return result
            result.extend(value for value in payload if isinstance(value, Mapping))
            headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
            total_pages = headers.get("x-wp-totalpages", "").strip()
            if total_pages.isdigit() and page >= int(total_pages):
                return result
        raise ValueError(f"WooCommerce excedió el máximo de {self.max_pages} páginas")

    def _load(self) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
        if self._parents is None:
            self._parents = self._paginate()
        if self._variations is None:
            self._variations = self._paginate("?type=variation")
        return self._parents, self._variations

    def research(self, row: Mapping[str, object]) -> CandidateEnumeration:
        if str(row.get("supplier") or "").casefold() != "labenze":
            return CandidateEnumeration("exhausted", [], "source_not_applicable")
        query = _row_identity(row)
        parents, variations = self._load()
        labenze_parent_ids = {
            str(product.get("id"))
            for product in parents
            if any(
                isinstance(brand, Mapping) and normalize_identity(brand.get("name")) == "LABENZE"
                for brand in (product.get("brands") or [])
            )
        }
        exact = [
            variation
            for variation in variations
            if str(variation.get("parent")) in labenze_parent_ids
            and normalize_identity(variation.get("sku")) == normalize_identity(query)
        ]
        unique_ids = {str(variation.get("id") or "") for variation in exact}
        if len(unique_ids) > 1:
            return CandidateEnumeration("rejected", [], "variation_sku_collision")
        if not exact:
            return CandidateEnumeration("exhausted", [], "no_exact_labenze_variation_sku")
        variation = exact[0]
        product_url = str(variation.get("permalink") or "").strip()
        images = variation.get("images")
        if not product_url or not isinstance(images, list):
            return CandidateEnumeration("exhausted", [], "variation_without_explicit_image")
        candidates = []
        for image in images:
            image_url = _image_url(image)
            if not image_url:
                continue
            candidates.append(
                ResearchCandidate(
                    source_name="arterio.mx",
                    source_kind="authorized_distributor",
                    source_id=str(variation.get("id") or ""),
                    query=query,
                    matched_field="variation.sku",
                    product_url=product_url,
                    image_source_url=image_url,
                    evidence={
                        "variation_id": variation.get("id"),
                        "parent_id": variation.get("parent"),
                        "variation_sku": variation.get("sku"),
                        "variation": variation.get("variation"),
                        "image_id": image.get("id") if isinstance(image, Mapping) else None,
                    },
                )
            )
        if not candidates:
            return CandidateEnumeration("exhausted", [], "variation_without_explicit_image")
        return CandidateEnumeration("found_exact", candidates, "unique_exact_variation_image")


class InfinitiSource:
    """Adapter Infiniti gobernado por bindings externos de configuración exacta."""

    wp_base_url = "https://www.infinitidesign.it/wp-json/wp/v2/product"
    woo_detail_base_url = "https://www.infinitidesign.it/wp-json/wc/store/v1/products"

    def __init__(
        self,
        client: CachedHttpClient,
        *,
        bindings: Iterable[Mapping[str, object]],
        max_pages: int = 20,
    ) -> None:
        self.client = client
        self.bindings = [value for value in bindings if isinstance(value, Mapping)]
        self.max_pages = max_pages
        self._products_value: dict[str, Mapping[str, object]] | None = None

    def _products(self) -> dict[str, Mapping[str, object]]:
        if self._products_value is not None:
            return self._products_value
        products: dict[str, Mapping[str, object]] = {}
        for page in range(1, self.max_pages + 1):
            url = f"{self.wp_base_url}?lang=en&per_page=100&page={page}"
            payload = self.client.get_json(url)
            if not isinstance(payload, list):
                raise ValueError(f"Página Infiniti WP inválida: {url}")
            for value in payload:
                if isinstance(value, Mapping) and value.get("id") is not None:
                    products[str(value["id"])] = value
            if len(payload) < 100:
                self._products_value = products
                return products
        raise ValueError(f"Infiniti excedió el máximo de {self.max_pages} páginas")

    def research(self, row: Mapping[str, object]) -> CandidateEnumeration:
        if str(row.get("supplier") or "").casefold() != "labenze":
            return CandidateEnumeration("exhausted", [], "source_not_applicable")
        products = self._products()
        query = _row_identity(row)
        signature = str((row.get("visual_signature") or {}).get("sha256") or "")
        exact_bindings = [
            binding
            for binding in self.bindings
            if str(binding.get("internal_id") or "") == str(row.get("internal_id") or "")
            and normalize_identity(binding.get("sku")) == normalize_identity(query)
            and str(binding.get("visual_signature_sha256") or "") == signature
            and binding.get("wp_product_id") is not None
        ]
        product_ids = {str(binding["wp_product_id"]) for binding in exact_bindings}
        if not product_ids:
            return CandidateEnumeration("exhausted", [], "no_curated_infiniti_configuration_binding")
        if len(product_ids) > 1:
            return CandidateEnumeration("rejected", [], "infiniti_binding_collision")
        binding = exact_bindings[0]
        product_id = next(iter(product_ids))
        wp_product = products.get(product_id)
        if not isinstance(wp_product, Mapping) or str(wp_product.get("lang") or "en").casefold() != "en":
            return CandidateEnumeration("rejected", [], "infiniti_wp_product_missing")
        detail_url = f"{self.woo_detail_base_url}/{quote(product_id)}"
        detail = self.client.get_json(detail_url)
        if not isinstance(detail, Mapping) or str(detail.get("id") or "") != product_id:
            return CandidateEnumeration("rejected", [], "infiniti_detail_identity_mismatch")
        product_url = str(detail.get("permalink") or wp_product.get("link") or "").strip()
        images = detail.get("images")
        if not product_url or not isinstance(images, list):
            return CandidateEnumeration("exhausted", [], "infiniti_binding_without_gallery")
        candidates = []
        for image in images:
            image_url = _image_url(image)
            if not image_url:
                continue
            image_host, parsed_image = _url_host(image_url, "image_source_url")
            if image_host not in {"infinitidesign.it", "www.infinitidesign.it"} or not parsed_image.path.startswith(
                "/wp-content/uploads/"
            ):
                continue
            candidates.append(
                ResearchCandidate(
                    source_name="infinitidesign.it",
                    source_kind="manufacturer_official",
                    source_id=product_id,
                    query=query,
                    matched_field="curated_binding.internal_id+sku+visual_signature+wp_product_id",
                    product_url=product_url,
                    image_source_url=image_url,
                    evidence={
                        "wp_product_id": int(product_id) if product_id.isdigit() else product_id,
                        "binding_internal_id": binding.get("internal_id"),
                        "binding_sku": binding.get("sku"),
                        "binding_visual_signature_sha256": binding.get("visual_signature_sha256"),
                        "image_id": image.get("id") if isinstance(image, Mapping) else None,
                        "image_alt": image.get("alt") if isinstance(image, Mapping) else None,
                    },
                )
            )
        if not candidates:
            return CandidateEnumeration("exhausted", [], "infiniti_binding_without_gallery")
        return CandidateEnumeration("found_exact", candidates, "curated_exact_configuration_binding")


def _url_host(value: object, field: str) -> tuple[str, object]:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field} debe ser una URL HTTPS segura")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} tiene puerto inválido") from exc
    if port not in {None, 443}:
        raise ValueError(f"{field} tiene puerto no permitido")
    return parsed.hostname.lower().rstrip("."), parsed


def _host_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    canonical = host.lower().rstrip(".")
    return canonical in {str(value).lower().rstrip(".") for value in allowed_hosts}


def validate_candidate_urls(
    candidate: ResearchCandidate,
    *,
    allowed_product_hosts: Iterable[str],
    allowed_image_hosts: Iterable[str],
) -> ResearchCandidate:
    """Separa una ficha individual exacta de su URL de bytes de imagen."""

    product_host, product = _url_host(candidate.product_url, "product_url")
    image_host, image = _url_host(candidate.image_source_url, "image_source_url")
    if not _host_allowed(product_host, allowed_product_hosts):
        raise ValueError(f"product_url usa host no permitido: {product_host}")
    if not _host_allowed(image_host, allowed_image_hosts):
        raise ValueError(f"image_source_url usa host no permitido: {image_host}")
    product_path = product.path.lower().rstrip("/")
    forbidden = ("/search", "/buscar", "/collections", "/collection", "/category", "/categoria", "/family", "/familia")
    image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif")
    query_keys = {key.casefold() for key, _ in parse_qsl(product.query, keep_blank_values=True)}
    if (
        not product_path
        or product_path == "/"
        or any(segment in product_path for segment in forbidden)
        or product_path.endswith(image_extensions)
        or query_keys & {"q", "query", "search", "s", "buscar", "term", "keyword"}
    ):
        raise ValueError("product_url no es una página individual exacta")
    if product_host == image_host and product.path.rstrip("/") == image.path.rstrip("/"):
        raise ValueError("product_url no puede confundirse con image_source_url")
    return candidate


def _default_resolver(host: str) -> list[str]:
    return sorted({result[4][0] for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def _validate_public_host(host: str, resolver) -> None:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(value) for value in resolver(host)]
        except (OSError, ValueError) as exc:
            raise ValueError(f"No se pudo resolver host de imagen: {host}") from exc
    if not addresses:
        raise ValueError(f"No se pudo resolver host de imagen: {host}")
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise ValueError(f"URL de imagen resuelve a red privada: {host}")


def _detect_image_type(body: bytes) -> tuple[str, str]:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValueError("magic de imagen no reconocido")


def download_original(
    candidate: ResearchCandidate,
    output_dir: Path,
    *,
    allowed_image_hosts: Iterable[str],
    fetcher,
    resolver=_default_resolver,
    check_dns: bool = True,
) -> DownloadResult:
    """Descarga bytes originales después de validar red, respuesta e imagen real."""

    requested_host, _ = _url_host(candidate.image_source_url, "image_source_url")
    if not _host_allowed(requested_host, allowed_image_hosts):
        raise ValueError(f"image_source_url usa host no permitido: {requested_host}")
    if check_dns:
        _validate_public_host(requested_host, resolver)
    response = fetcher(candidate.image_source_url)
    if not isinstance(response, HttpResponse):
        raise TypeError("fetcher debe devolver HttpResponse")
    final_host, _ = _url_host(response.url, "redirect final")
    if not _host_allowed(final_host, allowed_image_hosts):
        raise ValueError(f"redirect final usa host no permitido: {final_host}")
    if check_dns:
        _validate_public_host(final_host, resolver)
    if response.status != 200:
        raise ValueError(f"status HTTP inesperado: {response.status}")
    body = bytes(response.body)
    if len(body) > MAX_ORIGINAL_BYTES:
        raise ValueError("Original supera 8 MiB")
    headers = {str(key).casefold(): str(value) for key, value in response.headers.items()}
    declared_mime = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if declared_mime in {"image/svg+xml", "text/svg"} or response.url.lower().split("?", 1)[0].endswith(".svg"):
        raise ValueError("SVG no permitido")
    if declared_mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError(f"MIME de imagen no permitido: {declared_mime!r}")
    actual_mime, extension = _detect_image_type(body)
    if actual_mime != declared_mime:
        raise ValueError(f"MIME no coincide con magic: declarado={declared_mime}, real={actual_mime}")
    try:
        with Image.open(io.BytesIO(body)) as image:
            width, height = image.size
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                raise ValueError("Original supera 8192 px por lado")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Original supera 25 Mpx")
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Formato de imagen real no permitido")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Imagen original inválida") from exc
    digest = hashlib.sha256(body).hexdigest()
    output_dir = Path(output_dir)
    destination = output_dir / f"{digest}{extension}"
    if destination.exists():
        if destination.read_bytes() != body:
            raise ValueError(f"Colisión en original content-addressed: {destination.name}")
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
    return DownloadResult(
        sha256=digest,
        path=destination,
        mime=actual_mime,
        dimensions={"width": width, "height": height},
        bytes=len(body),
        requested_url=candidate.image_source_url,
        final_url=response.url,
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left in right.parents or right in left.parents


def validate_output_path(output_path: Path, *, protected_paths: Sequence[Path]) -> Path:
    """Exige una salida nueva y ajena a inventario, store y assets."""

    output = Path(output_path).resolve()
    for protected_path in protected_paths:
        protected = Path(protected_path).resolve()
        if _paths_overlap(output, protected):
            raise ValueError(f"La salida se solapa con una entrada protegida: {protected}")
    if output.exists():
        raise ValueError(f"La salida ya existe: {output}")
    return output


def _tree_fingerprint(path: Path) -> dict:
    path = Path(path)
    files = []
    total_bytes = 0
    if path.is_file():
        candidates = [path]
        root = path.parent
    elif path.is_dir():
        candidates = sorted((entry for entry in path.rglob("*") if entry.is_file()), key=lambda item: item.as_posix())
        root = path
    else:
        raise ValueError(f"Entrada protegida ausente: {path}")
    for entry in candidates:
        size = entry.stat().st_size
        total_bytes += size
        files.append(
            {
                "path": entry.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": _sha256_file(entry),
            }
        )
    material = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "files": len(files),
        "bytes": total_bytes,
        "sha256": hashlib.sha256(material).hexdigest(),
    }


def _copy_cache(source: Path | None, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    if source is None:
        return
    source = Path(source)
    if not source.is_dir():
        raise ValueError(f"Cache fuente ausente: {source}")
    for entry in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if not entry.is_file():
            continue
        relative = entry.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry, target)
        if _sha256_file(entry) != _sha256_file(target):
            raise RuntimeError(f"Cache copiada no coincide: {relative}")


def _candidate_dict(candidate: ResearchCandidate) -> dict:
    value = asdict(candidate)
    value["approved"] = False
    return value


def _research_record(row: dict, sources: Sequence[object], researched_at: str) -> dict:
    candidates: list[ResearchCandidate] = []
    rejected_reasons: list[str] = []
    exhausted_reasons: list[str] = []
    for source in sources:
        result = source.research(row)
        if not isinstance(result, CandidateEnumeration):
            raise TypeError("Un adapter de fuente debe devolver CandidateEnumeration")
        if result.status not in {"found_exact", "rejected", "exhausted"}:
            raise ValueError(f"Estado de fuente inválido: {result.status!r}")
        if result.status == "found_exact":
            candidates.extend(result.candidates)
        elif result.status == "rejected":
            rejected_reasons.append(result.reason)
            candidates.extend(result.candidates)
        else:
            exhausted_reasons.append(result.reason)
    candidate_sources = {candidate.source_name for candidate in candidates}
    if rejected_reasons:
        status = "rejected"
        reason = ";".join(dict.fromkeys(rejected_reasons))
    elif len(candidate_sources) > 1:
        status = "rejected"
        reason = "cross_source_exact_candidate_collision"
    elif candidates:
        status = "found_exact"
        reason = "exact_identity_candidates_found"
    else:
        status = "exhausted"
        reason = ";".join(dict.fromkeys(exhausted_reasons)) or "approved_sources_exhausted"
    serialized = [_candidate_dict(candidate) for candidate in candidates]
    source_kinds = sorted({candidate.source_kind for candidate in candidates})
    return {
        "schema_version": 1,
        "supplier": row["supplier"],
        "internal_id": row["internal_id"],
        "product_key": row.get("product_key", ""),
        "sku": row.get("sku", ""),
        "source_code": row.get("source_code", ""),
        "name": row.get("name", ""),
        "description": row.get("description", ""),
        "collection": row.get("collection", ""),
        "source_hash": row.get("source_hash", ""),
        "source_page": row.get("source_page"),
        "visual_signature": row.get("visual_signature"),
        "fallback": {
            "product_url": row.get("product_url", ""),
            "source_page": row.get("source_page"),
            "source_locator": f"Página {row.get('source_page')}, código {_row_identity(row)}",
        },
        "query": {"raw": _row_identity(row), "normalized": normalize_identity(_row_identity(row))},
        "status": status,
        "reason": reason,
        "source_kind": source_kinds[0] if len(source_kinds) == 1 else ("multiple" if source_kinds else ""),
        "candidate": serialized[0] if len(serialized) == 1 else None,
        "candidates": serialized,
        "candidate_count": len(serialized),
        "evidence": [value["evidence"] for value in serialized],
        "researched_at": researched_at,
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


def _write_records_csv(path: Path, records: list[dict]) -> None:
    fields = [
        "supplier", "internal_id", "product_key", "sku", "source_code", "name", "description",
        "collection", "source_hash", "source_page", "status", "reason",
        "source_kind", "candidate_count", "query", "candidate", "candidates", "evidence",
        "researched_at", "review", "fallback", "visual_signature",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    key: json.dumps(record[key], ensure_ascii=False, sort_keys=True)
                    if isinstance(record[key], (dict, list))
                    else record[key]
                    for key in fields
                }
            )


def _download_record_candidates(record: dict, candidate_downloader, originals_dir: Path) -> int:
    if record["status"] != "found_exact":
        return 0
    downloaded = 0
    for value in record["candidates"]:
        candidate_fields = {field: value[field] for field in ResearchCandidate.__dataclass_fields__}
        candidate = ResearchCandidate(**candidate_fields)
        try:
            result = candidate_downloader(candidate, originals_dir)
            if not isinstance(result, DownloadResult):
                raise TypeError("candidate_downloader debe devolver DownloadResult")
            value["download"] = {
                "status": "downloaded",
                "sha256": result.sha256,
                "object_name": result.path.name,
                "mime": result.mime,
                "bytes": result.bytes,
                "dimensions": result.dimensions,
                "requested_url": result.requested_url,
                "final_url": result.final_url,
            }
            downloaded += 1
        except ValueError as exc:
            value["download"] = {"status": "rejected", "reason": str(exc)}
    if downloaded == 0:
        record["status"] = "rejected"
        record["reason"] = "all_exact_candidates_failed_download"
        record["source_kind"] = ""
    record["candidate"] = record["candidates"][0] if len(record["candidates"]) == 1 else None
    record["evidence"] = [value["evidence"] for value in record["candidates"]]
    return downloaded


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    result = {}
    for path in sorted(output_dir.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path.name != "artifact-hashes.json":
            result[path.relative_to(output_dir).as_posix()] = _sha256_file(path)
    return result


def run_research(
    *,
    inventory_path: Path,
    store_path: Path,
    assets_dir: Path,
    output_dir: Path,
    sources: Sequence[object],
    expected_inventory_sha256: str = CANONICAL_INVENTORY_SHA256,
    researched_at: str | None = None,
    offline: bool = False,
    cache_from: Path | None = None,
    candidate_downloader=None,
) -> dict:
    """Ejecuta investigación reproducible sin promover, normalizar ni aprobar visuales."""

    inventory_path = Path(inventory_path).resolve()
    store_path = Path(store_path).resolve()
    assets_dir = Path(assets_dir).resolve()
    output_dir = validate_output_path(
        output_dir,
        protected_paths=[inventory_path, store_path, assets_dir],
    )
    rows = load_inventory(inventory_path, expected_sha256=expected_inventory_sha256)
    before = {
        "inventory": _tree_fingerprint(inventory_path),
        "store": _tree_fingerprint(store_path),
        "assets": _tree_fingerprint(assets_dir),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _copy_cache(cache_from, output_dir / "http-cache")
    (output_dir / "originals").mkdir()
    timestamp = researched_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        identity_counts = Counter(
            (str(row["supplier"]).casefold(), normalize_identity(_row_identity(row)))
            for row in rows
            if normalize_identity(_row_identity(row))
        )
        collision_keys = {key for key, count in identity_counts.items() if count > 1}
        records = []
        for row in rows:
            key = (str(row["supplier"]).casefold(), normalize_identity(_row_identity(row)))
            if key in collision_keys:
                record = _research_record(row, [], timestamp)
                record["status"] = "rejected"
                record["reason"] = "inventory_identity_collision"
                records.append(record)
            else:
                records.append(_research_record(row, sources, timestamp))
        records.sort(key=lambda value: (value["supplier"], value["internal_id"]))
        downloaded_candidates = 0
        if candidate_downloader is not None:
            downloaded_candidates = sum(
                _download_record_candidates(record, candidate_downloader, output_dir / "originals")
                for record in records
            )
        terminal_counts = Counter(record["status"] for record in records)
        counts = {status: terminal_counts.get(status, 0) for status in ("found_exact", "rejected", "exhausted")}
        if sum(counts.values()) != len(rows):
            raise RuntimeError("No todas las identidades terminaron en un estado terminal")
        logical_records = [{key: value for key, value in record.items() if key != "researched_at"} for record in records]
        logical_bytes = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for record in logical_records
        ).encode("utf-8")
        jsonl_bytes = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
        ).encode("utf-8")
        (output_dir / "candidates.jsonl").write_bytes(jsonl_bytes)
        _write_records_csv(output_dir / "candidates.csv", records)
        after = {
            "inventory": _tree_fingerprint(inventory_path),
            "store": _tree_fingerprint(store_path),
            "assets": _tree_fingerprint(assets_dir),
        }
        unchanged = before == after
        summary = {
            "schema_version": 1,
            "status": "passed" if unchanged else "failed",
            "offline": bool(offline),
            "researched_at": timestamp,
            "inventory_sha256": expected_inventory_sha256,
            "rows": len(records),
            "counts": counts,
            "downloaded_candidates": downloaded_candidates,
            "logical_candidates_sha256": hashlib.sha256(logical_bytes).hexdigest(),
            "inputs_before": before,
            "inputs_after": after,
            "inputs_unchanged": unchanged,
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        hashes = _artifact_hashes(output_dir)
        (output_dir / "artifact-hashes.json").write_text(
            json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if not unchanged:
            raise RuntimeError("Inventario/store/assets cambiaron durante la investigación")
        return summary
    except Exception as exc:
        failure = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "researched_at": timestamp,
        }
        (output_dir / "FAILED.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        raise


SOURCE_HTTP_HOSTS = {
    "api-productos.requiez.com",
    "requiez.com",
    "www.requiez.com",
    "test.diagrama.labenze.com",
    "test.labenze.com",
    "labenze.com",
    "www.labenze.com",
    "nogalbeat.com",
    "www.nogalbeat.com",
    "nogalbeatstore.com",
    "www.nogalbeatstore.com",
    "3rin.com.mx",
    "www.3rin.com.mx",
    "cdn.shopify.com",
    "arterio.mx",
    "www.arterio.mx",
    "infinitidesign.it",
    "www.infinitidesign.it",
}

PRODUCT_PAGE_HOSTS = {
    "requiez.com",
    "www.requiez.com",
    "test.labenze.com",
    "nogalbeat.com",
    "www.nogalbeat.com",
    "nogalbeatstore.com",
    "www.nogalbeatstore.com",
    "3rin.com.mx",
    "www.3rin.com.mx",
    "arterio.mx",
    "www.arterio.mx",
    "infinitidesign.it",
    "www.infinitidesign.it",
}

IMAGE_HOSTS = SOURCE_HTTP_HOSTS - {"test.labenze.com"}


class UrllibTransport:
    """Transporte HTTPS mínimo; los redirects se validan fuera de urllib."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def __call__(self, url: str) -> HttpResponse:
        request = Request(
            url,
            headers={
                "Accept": "application/json,image/png,image/jpeg,image/webp,*/*;q=0.1",
                "User-Agent": "MobilitiVisualResearch/1.0 (+read-only; exact-identity)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_ORIGINAL_BYTES + 1)
                return HttpResponse(
                    status=int(response.status),
                    url=str(response.geturl()),
                    headers=dict(response.headers.items()),
                    body=body,
                )
        except HTTPError as exc:
            body = exc.read(MAX_ORIGINAL_BYTES + 1)
            return HttpResponse(
                status=int(exc.code),
                url=str(exc.geturl()),
                headers=dict(exc.headers.items()),
                body=body,
            )


def _read_bindings(path: Path | None) -> list[Mapping[str, object]]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(value, Mapping) for value in payload):
        raise ValueError("Bindings Infiniti deben ser una lista JSON")
    return payload


def build_default_sources(client: CachedHttpClient, *, infiniti_bindings: Iterable[Mapping[str, object]] = ()) -> list[object]:
    """Construye adapters aprobados sin incluir resultados ni asociaciones web."""

    return [
        RequiezSource(client),
        LabenzeLegacySource(client),
        ShopifySource(client, source_name="nogalbeat.com", storefront_url="https://nogalbeat.com"),
        ShopifySource(
            client,
            source_name="nogalbeatstore.com",
            storefront_url="https://nogalbeatstore.com",
        ),
        ShopifySource(client, source_name="3rin.com.mx", storefront_url="https://3rin.com.mx"),
        WooCommerceSource(client),
        InfinitiSource(client, bindings=infiniti_bindings),
    ]


def should_download_candidate(candidate: ResearchCandidate) -> bool:
    """Autoriza adquirir evidencia exacta sin confundirla con aprobación humana."""

    return bool(
        candidate.matched_field
        and candidate.product_url
        and candidate.image_source_url
        and candidate.approved is False
    )


def _default_downloader(client: CachedHttpClient):
    def acquire(candidate: ResearchCandidate, originals_dir: Path) -> DownloadResult:
        if not should_download_candidate(candidate):
            raise ValueError("candidato sin binding explícito descargable")
        validate_candidate_urls(
            candidate,
            allowed_product_hosts=PRODUCT_PAGE_HOSTS,
            allowed_image_hosts=IMAGE_HOSTS,
        )
        return download_original(
            candidate,
            originals_dir,
            allowed_image_hosts=IMAGE_HOSTS,
            fetcher=client.get,
            resolver=_default_resolver,
            check_dns=not client.offline,
        )

    return acquire


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cache-from", type=Path)
    parser.add_argument("--infiniti-bindings", type=Path)
    parser.add_argument("--inventory-sha256", default=CANONICAL_INVENTORY_SHA256)
    parser.add_argument("--http-timeout", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = Path(args.output).resolve()
    client = CachedHttpClient(
        output_dir / "http-cache",
        transport=UrllibTransport(timeout=args.http_timeout),
        offline=args.offline,
        allowed_hosts=SOURCE_HTTP_HOSTS,
        resolver=_default_resolver,
        max_attempts=4,
        backoff_seconds=2,
    )
    sources = build_default_sources(client, infiniti_bindings=_read_bindings(args.infiniti_bindings))
    summary = run_research(
        inventory_path=args.inventory,
        store_path=args.store,
        assets_dir=args.assets,
        output_dir=output_dir,
        sources=sources,
        expected_inventory_sha256=args.inventory_sha256,
        offline=args.offline,
        cache_from=args.cache_from,
        candidate_downloader=_default_downloader(client),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
