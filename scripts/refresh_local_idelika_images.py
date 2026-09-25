"""Verifica y publica imágenes individuales de IDÉLIKA en el dev-store local.

La fuente visual primaria es la tienda oficial de IDÉLIKA. Un producto solo
recibe imagen cuando el nombre/modelo y el tipo de mueble son compatibles. Los
casos ambiguos pierden el recorte PDF anterior y quedan marcados para revisión,
evitando mostrar logos, textos o muebles de otra ficha.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mobiliti_saas.quote_engine.image_processing import (  # noqa: E402
    improve_product_image_bytes,
)
from mobiliti_saas.worker.catalog_sync.importers.common import (  # noqa: E402
    ImageAsset,
    _normalize_image,
)


OFFICIAL_ORIGIN = "https://idelika.com"
OFFICIAL_HOSTS = {"idelika.com", "www.idelika.com"}
MAX_SHOP_HTML_BYTES = 3 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 8 * 1024 * 1024
ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.png$")
SKU_PREFIX_RE = re.compile(r"^\s*\[[^]]+\]\s*")
TEMPLATE_IMAGE_RE = re.compile(
    r"^/web/image/product\.template/(?P<template_id>\d+)/image_(?:512|1024|1920)(?:/|$)"
)


@dataclass(frozen=True)
class ShopCandidate:
    name: str
    product_url: str
    image_url: str
    template_id: int
    match_status: str = "exact_web"


@dataclass(frozen=True)
class AuditedPdfCandidate:
    name: str
    source_file: str
    page: int
    xref: int
    crop: tuple[float, float, float, float] | None
    edit_method: str


@dataclass(frozen=True)
class ResolvedProductImage:
    candidate: ShopCandidate | AuditedPdfCandidate
    asset_sha256: str
    width: int
    height: int


_FOLD_REPLACEMENTS = {
    "sofacama": "sofa cama",
    "st mitchel": "saint mitchel",
    "wabba": "wabi",
    "sonsaura": "sonsuara",
    "saurasonsuara": "sonsuara",
    "pichilinge": "pichilingue",
    "picnic": "pic nic",
    "arm sombrilla": "sombrilla brazo",
}

_PRODUCT_ALIASES = {
    "pupitre 360 grados": "partner pupitre",
}

_TYPE_GROUPS = {
    "silla": "chair",
    "sillon": "armchair",
    "sofa": "sofa",
    "seccional": "sofa",
    "sala": "sofa",
    "love": "sofa",
    "banco": "stool",
    "taburete": "stool",
    "banca": "bench",
    "mesa": "table",
    "base": "table-base",
    "camastro": "lounger",
    "daybed": "lounger",
    "columpio": "swing",
    "mecedora": "swing",
    "pupitre": "school-desk",
    "escritorio": "desk",
    "pantalla": "screen-or-shade",
    "tapete": "rug",
    "alfombra": "rug",
    "recamara": "bedroom",
    "cama": "bedroom",
    "cabecera": "bedroom",
    "sombrilla": "umbrella",
    "ottoman": "ottoman",
    "librero": "storage",
    "archivero": "storage",
    "estante": "storage",
    "rack": "storage",
}

_IDENTITY_STOPWORDS = {
    *_TYPE_GROUPS,
    "a",
    "al",
    "alta",
    "alto",
    "b",
    "baja",
    "bajo",
    "beige",
    "blanca",
    "blanco",
    "c",
    "cafe",
    "centro",
    "coleccion",
    "color",
    "comedor",
    "con",
    "counter",
    "d",
    "de",
    "del",
    "doble",
    "en",
    "exterior",
    "fabricacion",
    "grande",
    "grados",
    "gris",
    "individual",
    "interior",
    "lateral",
    "metal",
    "minimo",
    "negra",
    "negro",
    "o",
    "para",
    "pedido",
    "plaza",
    "plazas",
    "pzs",
    "set",
    "techado",
    "y",
}

_QUALIFIERS = {
    "100",
    "aluminio",
    "apilable",
    "alta",
    "baja",
    "beige",
    "blanca",
    "blanco",
    "brazo",
    "cafe",
    "counter",
    "doble",
    "exterior",
    "galvanizado",
    "gris",
    "individual",
    "interior",
    "loneta",
    "lounge",
    "madera",
    "metal",
    "negra",
    "negro",
    "techado",
    "textilene",
}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())
    for source, target in _FOLD_REPLACEMENTS.items():
        text = re.sub(rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])", target, text)
    return " ".join(text.split())


def _canonical_product(value: object) -> str:
    folded = _fold(value)
    return _PRODUCT_ALIASES.get(folded, folded)


def _tokens(value: object) -> tuple[str, ...]:
    return tuple(_fold(value).split())


def _identity_tokens(product: object) -> tuple[str, ...]:
    found: list[str] = []
    for token in _canonical_product(product).split():
        if token in _IDENTITY_STOPWORDS or token.isdigit() or len(token) <= 1:
            continue
        if token not in found:
            found.append(token)
    return tuple(found)


def _type_groups(value: object) -> frozenset[str]:
    return frozenset(
        _TYPE_GROUPS[token]
        for token in _canonical_product(value).split()
        if token in _TYPE_GROUPS
    )


def _candidate_score(
    product: str,
    variant: str,
    description: str,
    candidate: ShopCandidate,
) -> float | None:
    target = _canonical_product(product)
    candidate_text = _canonical_product(candidate.name)
    target_tokens = set(target.split())
    candidate_tokens = set(candidate_text.split())
    identities = set(_identity_tokens(product))
    if not identities or not identities <= candidate_tokens:
        return None

    target_groups = _type_groups(product)
    candidate_groups = _type_groups(candidate.name)
    if target_groups and not target_groups <= candidate_groups:
        return None

    variant_tokens = set(_canonical_product(variant).split())
    if "techado" in variant_tokens and {"exterior", "100"} <= candidate_tokens:
        return None
    if {"exterior", "100"} <= variant_tokens and "techado" in candidate_tokens:
        return None
    if "1" in target_tokens and "2" in candidate_tokens:
        return None
    if "2" in target_tokens and "individual" in candidate_tokens:
        return None

    # Un grupo o colección sin tipo concreto no autoriza tomar un integrante
    # cualquiera de la familia como si fuera el producto completo.
    if {"coleccion", "set"} & target_tokens:
        if not ({"coleccion", "set", "sala"} & candidate_tokens):
            return None

    shared = len(target_tokens & candidate_tokens)
    coverage = shared / max(1, len(target_tokens))
    context_tokens = set(_canonical_product(f"{variant} {description}").split())
    qualifier_overlap = len((context_tokens & candidate_tokens) & _QUALIFIERS)
    qualifier_conflicts = len(
        (candidate_tokens & {"apilable", "doble", "individual", "counter", "alta", "baja", "brazo"})
        - context_tokens
    )
    phrase_bonus = 18 if target in candidate_text else 0
    type_bonus = 14 * len(target_groups)
    extra_tokens = max(0, len(candidate_tokens - target_tokens) - 6)
    return (
        90
        + coverage * 35
        + phrase_bonus
        + type_bonus
        + qualifier_overlap * 7
        - qualifier_conflicts * 18
        - extra_tokens * 0.25
    )


def select_shop_candidate(
    product: str,
    *,
    variant: str,
    description: str,
    candidates: tuple[ShopCandidate, ...],
) -> ShopCandidate | None:
    """Elige una foto oficial solo con identidad y tipo de producto compatibles."""

    scored: list[tuple[float, ShopCandidate]] = []
    for candidate in candidates:
        score = _candidate_score(product, variant, description, candidate)
        if score is not None:
            scored.append((score, candidate))
    if not scored:
        return None
    scored.sort(
        key=lambda row: (
            -row[0],
            len(_canonical_product(row[1].name)),
            row[1].template_id,
            row[1].name,
        )
    )
    return scored[0][1]


class _ShopCandidateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._alt = ""
        self._src = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "a":
            classes = set(str(values.get("class") or "").split())
            if "oe_product_image_link" in classes:
                self._href = str(values.get("href") or "")
                self._alt = ""
                self._src = ""
            return
        if tag == "img" and self._href is not None:
            self._alt = str(values.get("alt") or "")
            self._src = str(values.get("src") or values.get("data-src") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        if self._alt and self._src:
            self.candidates.append((self._href, self._alt, self._src))
        self._href = None
        self._alt = ""
        self._src = ""


def _official_url(value: str, *, expected_path_prefix: str) -> str:
    absolute = urljoin(f"{OFFICIAL_ORIGIN}/", html.unescape(str(value or "").strip()))
    parsed = urlsplit(absolute)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in OFFICIAL_HOSTS
        or not parsed.path.startswith(expected_path_prefix)
    ):
        raise ValueError("URL oficial IDÉLIKA inválida")
    return urlunsplit(("https", "idelika.com", parsed.path, parsed.query, ""))


def parse_shop_candidates(content: str) -> tuple[ShopCandidate, ...]:
    parser = _ShopCandidateParser()
    parser.feed(content)
    found: dict[tuple[int, str], ShopCandidate] = {}
    for raw_href, raw_alt, raw_src in parser.candidates:
        try:
            product_url = _official_url(raw_href.split("?", 1)[0], expected_path_prefix="/shop/")
            image_url = _official_url(raw_src, expected_path_prefix="/web/image/product.template/")
        except ValueError:
            continue
        parsed_image = urlsplit(image_url)
        match = TEMPLATE_IMAGE_RE.match(parsed_image.path)
        if match is None:
            continue
        template_id = int(match.group("template_id"))
        image_path = re.sub(r"/image_(?:512|1024|1920)(?=/|$)", "/image_1024", parsed_image.path, count=1)
        image_url = urlunsplit(("https", "idelika.com", image_path, parsed_image.query, ""))
        name = SKU_PREFIX_RE.sub("", html.unescape(raw_alt)).strip()
        if not name:
            continue
        found[(template_id, name)] = ShopCandidate(
            name=name,
            product_url=product_url,
            image_url=image_url,
            template_id=template_id,
        )
    return tuple(sorted(found.values(), key=lambda row: (row.template_id, row.name)))


def search_queries(product: str) -> tuple[str, ...]:
    canonical = _canonical_product(product)
    identities = _identity_tokens(product)
    groups = _type_groups(product)
    preferred_type = next(
        (
            token
            for token in canonical.split()
            if token in _TYPE_GROUPS and _TYPE_GROUPS[token] in groups
        ),
        "",
    )
    raw = [product]
    if identities:
        model = " ".join(identities[:2])
        if preferred_type:
            raw.append(f"{model} {preferred_type}")
        raw.append(model)
    unique: list[str] = []
    seen: set[str] = set()
    for query in raw:
        clean = " ".join(str(query or "").split())
        key = _fold(clean)
        if clean and key not in seen:
            seen.add(key)
            unique.append(clean)
    return tuple(unique)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_response_bytes(response: requests.Response, maximum: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared:
        try:
            if int(declared) > maximum:
                raise ValueError("Respuesta IDÉLIKA excede el límite")
        except ValueError as exc:
            if "excede" in str(exc):
                raise
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > maximum:
            raise ValueError("Respuesta IDÉLIKA excede el límite")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_final_official_response(response: requests.Response) -> None:
    parsed = urlsplit(response.url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in OFFICIAL_HOSTS:
        raise ValueError("Redirección IDÉLIKA no confiable")
    response.raise_for_status()


def _fetch_shop_candidates(
    session: requests.Session,
    query: str,
    cache_dir: Path,
) -> tuple[ShopCandidate, ...]:
    key = _sha256(_fold(query).encode("utf-8"))
    cache_path = cache_dir / "search" / f"{key}.html"
    if cache_path.is_file():
        data = cache_path.read_bytes()
    else:
        response = session.get(
            f"{OFFICIAL_ORIGIN}/shop",
            params={"search": query},
            timeout=(10, 45),
            stream=True,
        )
        _validate_final_official_response(response)
        data = _bounded_response_bytes(response, MAX_SHOP_HTML_BYTES)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    return parse_shop_candidates(data.decode("utf-8", errors="replace"))


def _candidate_pool(
    session: requests.Session,
    product: str,
    cache_dir: Path,
) -> tuple[ShopCandidate, ...]:
    found: dict[tuple[int, str], ShopCandidate] = {}
    for query in search_queries(product):
        for candidate in _fetch_shop_candidates(session, query, cache_dir):
            found[(candidate.template_id, candidate.name)] = candidate
    return tuple(sorted(found.values(), key=lambda row: (row.template_id, row.name)))


def _download_official_image(
    session: requests.Session,
    candidate: ShopCandidate,
    cache_dir: Path,
) -> tuple[bytes, str]:
    key = _sha256(candidate.image_url.encode("utf-8"))
    cache_path = cache_dir / "source-images" / f"{key}.bin"
    metadata_path = cache_path.with_suffix(".json")
    if cache_path.is_file() and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        data = cache_path.read_bytes()
        if metadata.get("sha256") != _sha256(data):
            raise ValueError("Cache de imagen IDÉLIKA alterado")
        return data, str(metadata.get("content_type") or "")

    response = session.get(candidate.image_url, timeout=(10, 60), stream=True)
    _validate_final_official_response(response)
    data = _bounded_response_bytes(response, MAX_SOURCE_IMAGE_BYTES)
    raw_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
    if raw_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("Formato de imagen oficial IDÉLIKA no permitido")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    metadata_path.write_text(
        json.dumps(
            {"sha256": _sha256(data), "content_type": raw_type, "url": candidate.image_url},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return data, raw_type


def _light_border_ratio(image: Image.Image) -> float:
    rgba = image.convert("RGBA")
    rgba.thumbnail((256, 256), Image.Resampling.LANCZOS)
    width, height = rgba.size
    band = max(1, min(width, height) // 12)
    pixels = rgba.load()
    light = total = 0
    for y in range(height):
        for x in range(width):
            if x >= band and x < width - band and y >= band and y < height - band:
                continue
            red, green, blue, alpha = pixels[x, y]
            total += 1
            if alpha <= 12 or min(red, green, blue) >= 232:
                light += 1
    return light / max(1, total)


def _without_png_ancillary_metadata(content: bytes) -> bytes:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return content
    output = bytearray(content[:8])
    offset = 8
    found_end = False
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset : offset + 4], "big")
        end = offset + 12 + length
        if end > len(content):
            raise ValueError("PNG oficial IDÉLIKA truncado")
        chunk_type = content[offset + 4 : offset + 8]
        if chunk_type not in {b"tEXt", b"zTXt", b"iTXt", b"iCCP"}:
            output.extend(content[offset:end])
        offset = end
        if chunk_type == b"IEND":
            found_end = True
            break
    if not found_end:
        raise ValueError("PNG oficial IDÉLIKA sin IEND")
    return bytes(output)


def _bounded_source_image(content: bytes, content_type: str) -> tuple[bytes, str, bool]:
    if content_type == "image/png":
        content = _without_png_ancillary_metadata(content)
    with Image.open(BytesIO(content)) as source:
        source.load()
        if source.width <= 0 or source.height <= 0 or source.width * source.height > 40_000_000:
            raise ValueError("Dimensiones de imagen IDÉLIKA inválidas")
        light_background = _light_border_ratio(source) >= 0.58
        image = source.convert("RGBA")
        if max(image.size) > 1024:
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, "PNG", compress_level=4)
    return output.getvalue(), "image/png", light_background


def _pad_transparent_product(
    content: bytes,
    min_size: int,
    *,
    allow_full_rectangle: bool = False,
) -> bytes:
    with Image.open(BytesIO(content)) as source:
        rgba = source.convert("RGBA")
        alpha = rgba.getchannel("A")
        bbox = alpha.getbbox()
        if bbox is None:
            raise ValueError("La imagen oficial no contiene producto visible")
        visible = sum(alpha.histogram()[9:])
        ratio = visible / max(1, rgba.width * rgba.height)
        if ratio < 0.006 or (ratio > 0.94 and not allow_full_rectangle):
            raise ValueError("Máscara de producto oficial insegura")
        left, top, right, bottom = bbox
        # La canalización compartida recorta los bordes transparentes antes de
        # devolver el PNG, por lo que aquí el alfa puede tocar los cuatro lados
        # aunque el original tuviera margen suficiente. El padding se repone
        # después de aislar el bbox visible.
        product = rgba.crop(bbox)
        padding = max(16, math.ceil(max(product.size) * 0.055))
        canvas = Image.new(
            "RGBA",
            (product.width + padding * 2, product.height + padding * 2),
            (255, 255, 255, 0),
        )
        canvas.alpha_composite(product, (padding, padding))
        if min(canvas.size) < min_size:
            scale = min_size / min(canvas.size)
            canvas = canvas.resize(
                (math.ceil(canvas.width * scale), math.ceil(canvas.height * scale)),
                Image.Resampling.LANCZOS,
            )
        output = BytesIO()
        canvas.save(output, "PNG", optimize=True)
        return output.getvalue()


def prepare_official_product_asset(
    content: bytes,
    content_type: str,
    *,
    min_size: int = 900,
) -> ImageAsset:
    """Quita el fondo y valida que el producto permanezca completo."""

    bounded, bounded_type, light_background = _bounded_source_image(content, content_type)
    cleaned, _ = improve_product_image_bytes(
        bounded,
        bounded_type,
        background="transparent",
        min_size=max(1, min_size),
        cleanup_strength="balanced",
        remove_shadow=not light_background,
    )
    try:
        padded = _pad_transparent_product(cleaned, max(1, min_size))
    except ValueError:
        if not light_background:
            raise
        cleaned, _ = improve_product_image_bytes(
            bounded,
            bounded_type,
            background="transparent",
            min_size=max(1, min_size),
            cleanup_strength="normal",
            remove_shadow=False,
        )
        with Image.open(BytesIO(bounded)) as before, Image.open(BytesIO(cleaned)) as after:
            if before.size == after.size and after.convert("RGBA").getchannel("A").getextrema()[0] >= 245:
                raise ValueError("La limpieza conservadora no removió el fondo")
        padded = _pad_transparent_product(
            cleaned,
            max(1, min_size),
            allow_full_rectangle=True,
        )
    return _normalize_image(padded)


def _idelika_items(data: dict) -> list[dict]:
    try:
        items = data["catalog_published_snapshots"]["idelika"]["payload"]["items"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Catálogo IDÉLIKA local ausente") from exc
    if not isinstance(items, list):
        raise ValueError("Items IDÉLIKA locales inválidos")
    ids: set[str] = set()
    for item in items:
        internal_id = str(item.get("internal_id") or "") if isinstance(item, dict) else ""
        if not internal_id or internal_id in ids:
            raise ValueError(f"internal_id IDÉLIKA ausente o duplicado: {internal_id!r}")
        ids.add(internal_id)
    return items


def merge_idelika_visuals(
    active: dict,
    resolved: dict[str, ResolvedProductImage],
) -> tuple[dict, dict[str, int]]:
    """Reemplaza únicamente metadatos visuales y falla cerrado en ambiguos."""

    merged = copy.deepcopy(active)
    items = _idelika_items(merged)
    valid_ids = {str(item["internal_id"]) for item in items}
    unexpected = set(resolved) - valid_ids
    if unexpected:
        raise ValueError(f"Resoluciones IDÉLIKA desconocidas: {sorted(unexpected)[:3]}")

    resolved_count = 0
    needs_review = 0
    visual_keys = {
        "approved_asset",
        "image_match",
        "image_reference",
        "image_sha256",
        "image_width",
        "image_height",
        "source_image_url",
        "web_image_quality",
    }
    for item in items:
        internal_id = str(item["internal_id"])
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
            item["attributes"] = attributes
        match = resolved.get(internal_id)
        item["image_url"] = ""
        if match is None:
            for key in visual_keys:
                attributes.pop(key, None)
            item["image_kind"] = "placeholder"
            item["product_url"] = ""
            attributes["image_review_status"] = "needs_review"
            needs_review += 1
            continue

        candidate = match.candidate
        object_name = f"{match.asset_sha256}.png"
        if not ASSET_NAME_RE.fullmatch(object_name):
            raise ValueError(f"SHA-256 visual IDÉLIKA inválido: {internal_id}")
        item["image_kind"] = "official"
        attributes["approved_asset"] = {
            "bucket": "catalog-assets",
            "path": object_name,
            "image_kind": "official",
            "label": "Producto IDÉLIKA auditado sin fondo",
            "approved": True,
        }
        if isinstance(candidate, ShopCandidate):
            item["product_url"] = candidate.product_url
            source_reference = {
                "url": candidate.image_url,
                "product_url": candidate.product_url,
                "product": candidate.name,
                "template_id": candidate.template_id,
            }
            match_status = candidate.match_status
            quality = (
                "official_product_complete_background_removed"
                if match_status == "exact_web"
                else "official_model_complete_background_removed_visual_audit"
            )
            attributes["source_image_url"] = candidate.image_url
        else:
            item["product_url"] = ""
            source_reference = {
                "catalog_file": candidate.source_file,
                "page": candidate.page,
                "xref": candidate.xref,
                "crop": list(candidate.crop) if candidate.crop is not None else None,
                "product": candidate.name,
                "edit_method": candidate.edit_method,
            }
            match_status = "exact_pdf_visual_audit"
            quality = "official_pdf_product_complete_background_removed"
            attributes.pop("source_image_url", None)
        attributes["image_match"] = {
            "status": match_status,
            "asset_sha256": match.asset_sha256,
            "source_references": [source_reference],
        }
        attributes["image_reference"] = source_reference
        attributes["web_image_quality"] = quality
        attributes["image_sha256"] = match.asset_sha256
        attributes["image_width"] = match.width
        attributes["image_height"] = match.height
        attributes["image_review_status"] = "approved"
        resolved_count += 1

    return merged, {
        "resolved": resolved_count,
        "needs_review": needs_review,
        "items": len(items),
    }


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON raíz inválido: {path}")
    return value


def _audited_transparent_asset(data: bytes) -> ImageAsset:
    if len(data) > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("Asset visual auditado excede el límite")
    asset = _normalize_image(data)
    with Image.open(BytesIO(asset.data)) as source:
        alpha = source.convert("RGBA").getchannel("A")
        minimum, maximum = alpha.getextrema()
        visible = sum(alpha.histogram()[9:])
        visible_ratio = visible / max(1, source.width * source.height)
    if minimum >= 245 or maximum <= 12 or not 0.006 <= visible_ratio <= 0.94:
        raise ValueError("Asset visual auditado no contiene un producto aislado seguro")
    return asset


def load_audited_image_overrides(
    manifest_path: Path,
    *,
    valid_ids: set[str],
) -> tuple[
    dict[str, ShopCandidate],
    dict[str, tuple[ShopCandidate | AuditedPdfCandidate, ImageAsset]],
    dict[str, int],
]:
    """Carga resoluciones visuales revisadas y falla cerrado ante datos alterados."""

    manifest_path = Path(manifest_path).resolve()
    manifest = _read_json(manifest_path)
    if manifest.get("version") != 1 or manifest.get("catalog") != "idelika":
        raise ValueError("Manifiesto visual IDÉLIKA incompatible")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifiesto visual IDÉLIKA sin entradas")

    manifest_root = manifest_path.parent.resolve()
    seen: set[str] = set()
    web: dict[str, ShopCandidate] = {}
    assets: dict[str, tuple[ShopCandidate | AuditedPdfCandidate, ImageAsset]] = {}
    loaded_assets: dict[Path, ImageAsset] = {}
    pdf_override_count = 0
    web_asset_override_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Entrada visual IDÉLIKA inválida")
        internal_ids = entry.get("internal_ids")
        if (
            not isinstance(internal_ids, list)
            or not internal_ids
            or any(not isinstance(value, str) or not value for value in internal_ids)
        ):
            raise ValueError("internal_ids visuales IDÉLIKA inválidos")
        if len(set(internal_ids)) != len(internal_ids):
            raise ValueError("internal_ids visuales IDÉLIKA duplicados en una entrada")
        overlap = seen & set(internal_ids)
        unknown = set(internal_ids) - valid_ids
        if overlap or unknown:
            problem = sorted(overlap or unknown)
            raise ValueError(f"Resoluciones visuales IDÉLIKA inválidas: {problem[:3]}")
        seen.update(internal_ids)

        source = entry.get("source")
        if not isinstance(source, dict):
            raise ValueError("Fuente visual IDÉLIKA inválida")
        source_kind = source.get("kind")
        name = str(source.get("product") or "").strip()
        if not name:
            raise ValueError("Producto de fuente visual IDÉLIKA ausente")

        candidate: ShopCandidate | AuditedPdfCandidate
        if source_kind in {"official_web", "official_web_asset"}:
            product_url = _official_url(
                str(source.get("product_url") or ""),
                expected_path_prefix="/shop/",
            )
            image_url = _official_url(
                str(source.get("image_url") or ""),
                expected_path_prefix="/web/image/product.template/",
            )
            match = TEMPLATE_IMAGE_RE.match(urlsplit(image_url).path)
            template_id = source.get("template_id")
            if (
                not isinstance(template_id, int)
                or match is None
                or int(match.group("template_id")) != template_id
            ):
                raise ValueError("template_id visual IDÉLIKA inconsistente")
            match_status = str(source.get("match_status") or "exact_web")
            if match_status not in {"exact_web", "model_web"}:
                raise ValueError("Estado de coincidencia web IDÉLIKA no permitido")
            candidate = ShopCandidate(
                name=name,
                product_url=product_url,
                image_url=image_url,
                template_id=template_id,
                match_status=match_status,
            )
            if source_kind == "official_web":
                for internal_id in internal_ids:
                    web[internal_id] = candidate
                continue
            web_asset_override_count += len(internal_ids)
        elif source_kind == "official_pdf":
            source_file = str(source.get("file") or "").strip()
            page = source.get("page")
            xref = source.get("xref")
            if (
                not source_file
                or Path(source_file).name != source_file
                or Path(source_file).suffix.lower() != ".pdf"
                or not isinstance(page, int)
                or page <= 0
                or not isinstance(xref, int)
                or xref <= 0
            ):
                raise ValueError("Referencia PDF visual IDÉLIKA inválida")
            raw_crop = source.get("crop")
            crop: tuple[float, float, float, float] | None = None
            if raw_crop is not None:
                if (
                    not isinstance(raw_crop, list)
                    or len(raw_crop) != 4
                    or any(not isinstance(value, (int, float)) for value in raw_crop)
                ):
                    raise ValueError("Recorte PDF visual IDÉLIKA inválido")
                crop = tuple(float(value) for value in raw_crop)
                left, top, right, bottom = crop
                if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                    raise ValueError("Recorte PDF visual IDÉLIKA fuera de rango")
            candidate = AuditedPdfCandidate(
                name=name,
                source_file=source_file,
                page=page,
                xref=xref,
                crop=crop,
                edit_method="strict_background_extraction",
            )
            pdf_override_count += len(internal_ids)
        else:
            raise ValueError("Tipo de fuente visual IDÉLIKA no permitido")
        edit_method = str(entry.get("edit_method") or "").strip()
        if edit_method != "strict_background_extraction":
            raise ValueError("Método de edición visual IDÉLIKA no permitido")
        relative_asset = Path(str(entry.get("asset") or ""))
        if relative_asset.is_absolute() or not relative_asset.parts:
            raise ValueError("Ruta de asset visual IDÉLIKA inválida")
        asset_path = (manifest_root / relative_asset).resolve()
        try:
            asset_path.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError("Asset visual IDÉLIKA fuera del manifiesto") from exc
        expected_sha256 = str(entry.get("asset_sha256") or "").lower()
        data = asset_path.read_bytes()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or _sha256(data) != expected_sha256:
            raise ValueError("Asset visual IDÉLIKA alterado")
        asset = loaded_assets.get(asset_path)
        if asset is None:
            asset = _audited_transparent_asset(data)
            loaded_assets[asset_path] = asset
        for internal_id in internal_ids:
            assets[internal_id] = (candidate, asset)

    return web, assets, {
        "audited_manifest_entries": len(entries),
        "audited_web_overrides": len(web),
        "audited_web_asset_overrides": web_asset_override_count,
        "audited_pdf_overrides": pdf_override_count,
    }


def refresh_local_idelika_images(
    *,
    db_path: Path,
    assets_dir: Path,
    cache_dir: Path,
    backup_path: Path,
    staged_path: Path,
    report_path: Path,
    audited_manifest_path: Path | None = None,
    expected_db_sha256: str | None = None,
) -> dict:
    db_path = Path(db_path)
    assets_dir = Path(assets_dir)
    cache_dir = Path(cache_dir)
    backup_path = Path(backup_path)
    staged_path = Path(staged_path)
    report_path = Path(report_path)
    active_bytes = db_path.read_bytes()
    before_sha256 = _sha256(active_bytes)
    if expected_db_sha256 and before_sha256 != expected_db_sha256.lower():
        raise ValueError(
            f"El dev-store cambió: esperado {expected_db_sha256.lower()}, actual {before_sha256}"
        )
    for target in (backup_path, staged_path, report_path):
        if target.exists():
            raise ValueError(f"El artefacto ya existe: {target}")

    active = json.loads(active_bytes.decode("utf-8"))
    items = _idelika_items(active)
    audited_report = {
        "audited_manifest_entries": 0,
        "audited_web_overrides": 0,
        "audited_web_asset_overrides": 0,
        "audited_pdf_overrides": 0,
    }
    audited_web: dict[str, ShopCandidate] = {}
    audited_assets: dict[
        str,
        tuple[ShopCandidate | AuditedPdfCandidate, ImageAsset],
    ] = {}
    if audited_manifest_path is not None:
        audited_web, audited_assets, audited_report = load_audited_image_overrides(
            audited_manifest_path,
            valid_ids={str(item["internal_id"]) for item in items},
        )
    session = requests.Session()
    session.headers.update({"User-Agent": "Mobiliti-local-idelika-image-verifier/1.0"})

    pools: dict[str, tuple[ShopCandidate, ...]] = {}
    search_errors: dict[str, str] = {}
    for index, product in enumerate(sorted({str(item.get("name") or "") for item in items}), 1):
        try:
            pools[product] = _candidate_pool(session, product, cache_dir)
        except Exception as error:
            pools[product] = ()
            search_errors[product] = f"{type(error).__name__}:{error}"
        if index % 20 == 0:
            print(f"Búsqueda oficial IDÉLIKA: {index}/{len(set(item.get('name') for item in items))}", flush=True)

    selected: dict[str, ShopCandidate] = {}
    for item in items:
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        candidate = select_shop_candidate(
            str(item.get("name") or ""),
            variant=str(attributes.get("variant") or ""),
            description=str(item.get("description") or ""),
            candidates=pools.get(str(item.get("name") or ""), ()),
        )
        if candidate is not None:
            selected[str(item["internal_id"])] = candidate
    selected.update(audited_web)

    assets_by_url: dict[str, ImageAsset] = {}
    processing_errors: dict[str, str] = {}
    unique_candidates = {
        candidate.image_url: candidate for candidate in selected.values()
    }
    for index, candidate in enumerate(unique_candidates.values(), 1):
        try:
            source, content_type = _download_official_image(session, candidate, cache_dir)
            source_sha = _sha256(source)
            processed_index = cache_dir / "processed" / f"{source_sha}.json"
            if processed_index.is_file():
                metadata = json.loads(processed_index.read_text(encoding="utf-8"))
                object_name = str(metadata.get("object_name") or "")
                processed_path = cache_dir / "processed" / object_name
                if not ASSET_NAME_RE.fullmatch(object_name) or not processed_path.is_file():
                    raise ValueError("Índice procesado IDÉLIKA inválido")
                data = processed_path.read_bytes()
                if _sha256(data) != Path(object_name).stem:
                    raise ValueError("Asset procesado IDÉLIKA alterado")
                asset = _normalize_image(data)
            else:
                asset = prepare_official_product_asset(source, content_type)
                object_name = f"{asset.sha256}.png"
                processed_path = cache_dir / "processed" / object_name
                processed_path.parent.mkdir(parents=True, exist_ok=True)
                if processed_path.exists():
                    if _sha256(processed_path.read_bytes()) != asset.sha256:
                        raise ValueError("Colisión de asset procesado IDÉLIKA")
                else:
                    processed_path.write_bytes(asset.data)
                processed_index.write_text(
                    json.dumps(
                        {
                            "object_name": object_name,
                            "source_sha256": source_sha,
                            "source_url": candidate.image_url,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            assets_by_url[candidate.image_url] = asset
        except Exception as error:
            processing_errors[candidate.image_url] = f"{type(error).__name__}:{error}"
        if index % 10 == 0:
            print(f"Limpieza de fondo IDÉLIKA: {index}/{len(unique_candidates)}", flush=True)

    resolved: dict[str, ResolvedProductImage] = {}
    for internal_id, candidate in selected.items():
        asset = assets_by_url.get(candidate.image_url)
        if asset is None:
            continue
        resolved[internal_id] = ResolvedProductImage(
            candidate=candidate,
            asset_sha256=asset.sha256,
            width=asset.width,
            height=asset.height,
        )

    for internal_id, (candidate, asset) in audited_assets.items():
        object_name = f"{asset.sha256}.png"
        processed_path = cache_dir / "processed" / object_name
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        if processed_path.exists():
            if not processed_path.is_file() or _sha256(processed_path.read_bytes()) != asset.sha256:
                raise ValueError("Asset visual auditado IDÉLIKA en conflicto")
        else:
            processed_path.write_bytes(asset.data)
        resolved[internal_id] = ResolvedProductImage(
            candidate=candidate,
            asset_sha256=asset.sha256,
            width=asset.width,
            height=asset.height,
        )

    merged, visual_report = merge_idelika_visuals(active, resolved)
    referenced = {f"{row.asset_sha256}.png" for row in resolved.values()}
    assets_dir.mkdir(parents=True, exist_ok=True)
    copied = existing = 0
    for object_name in sorted(referenced):
        source = cache_dir / "processed" / object_name
        if not source.is_file() or _sha256(source.read_bytes()) != Path(object_name).stem:
            raise ValueError(f"Asset IDÉLIKA preparado ausente: {object_name}")
        target = assets_dir / object_name
        if target.exists():
            if not target.is_file() or _sha256(target.read_bytes()) != Path(object_name).stem:
                raise ValueError(f"Asset IDÉLIKA destino inválido: {target}")
            existing += 1
        else:
            shutil.copy2(source, target)
            copied += 1

    merged_bytes = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(active_bytes)
    if backup_path.read_bytes() != active_bytes:
        raise RuntimeError("El respaldo IDÉLIKA no coincide byte a byte")
    staged_path.write_bytes(merged_bytes)
    if _sha256(staged_path.read_bytes()) != _sha256(merged_bytes):
        raise RuntimeError("El staging IDÉLIKA no coincide")
    shutil.copyfile(staged_path, db_path)
    if _sha256(db_path.read_bytes()) != _sha256(merged_bytes):
        raise RuntimeError("El dev-store IDÉLIKA no coincide con el staging")

    report = {
        "status": "passed",
        "before_sha256": before_sha256,
        "after_sha256": _sha256(merged_bytes),
        **visual_report,
        **audited_report,
        "unique_selected_images": len(unique_candidates),
        "unique_published_assets": len(referenced),
        "assets_copied": copied,
        "assets_existing": existing,
        "search_errors": search_errors,
        "processing_errors": processing_errors,
        "backup": str(backup_path),
        "staged": str(staged_path),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / ".mobiliti_dev_store" / "db.json",
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=PROJECT_ROOT / ".mobiliti_dev_store" / "catalog-assets",
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--audited-manifest", type=Path)
    parser.add_argument("--expected-db-sha256")
    return parser


def main() -> None:
    args = _parser().parse_args()
    report = refresh_local_idelika_images(
        db_path=args.db,
        assets_dir=args.assets,
        cache_dir=args.cache,
        backup_path=args.backup,
        staged_path=args.staged,
        report_path=args.report,
        audited_manifest_path=args.audited_manifest,
        expected_db_sha256=args.expected_db_sha256,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
