from __future__ import annotations

from html import unescape
from io import BytesIO
import json
from pathlib import Path
import hashlib
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image


SUNON_SEARCH_URL = "https://www.sunonglobal.com/"
SUNON_REST_PRODUCT_URL = "https://www.sunonglobal.com/wp-json/wp/v2/product"
SUNON_USER_AGENT = "MobilitiQuoteWorker/1.0 (+https://www.sunonglobal.com/)"
MAX_SUNON_IMAGE_BYTES = 12 * 1024 * 1024
SUNON_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "sunon_catalog.json"
PRODUCT_CODE_RE = re.compile(r"\b[A-Z]{1,8}[A-Z0-9]*(?:[-.][A-Z0-9]+)*\d[A-Z0-9]*(?:[-.][A-Z0-9]+)*\b", re.I)
IMG_SRC_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.I)
LI_RE = re.compile(r"<li\b[^>]*>.*?</li>", re.I | re.S)
TR_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
TD_RE = re.compile(r"<t[dh]\b[^>]*>.*?</t[dh]>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


class SunonImageLookupError(RuntimeError):
    pass


def normalize_sunon_code(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_product_code(product_name: str | None) -> str | None:
    text = str(product_name or "").strip()
    if not text:
        return None
    for match in PRODUCT_CODE_RE.finditer(text.upper()):
        raw = match.group(0).strip("-.")
        normalized = normalize_sunon_code(raw)
        if len(normalized) >= 4:
            return raw
    return None


def sunon_code_candidates(value: str | None) -> list[str]:
    code = extract_product_code(value) or str(value or "").strip()
    if not code:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        candidate = str(candidate or "").strip("-. ")
        normalized = normalize_sunon_code(candidate)
        if len(normalized) < 4 or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(candidate)

    add(code)
    parts = re.split(r"([-.])", code)
    while len(parts) > 1:
        parts = parts[:-2]
        candidate = "".join(parts)
        if _looks_like_base_code(candidate):
            add(candidate)
    return candidates


def _looks_like_base_code(value: str | None) -> bool:
    normalized = normalize_sunon_code(value)
    return (
        len(normalized) >= 4
        and bool(re.search(r"[A-Z]", normalized))
        and bool(re.search(r"\d", normalized))
    )


def fetch_sunon_product_image(
    product_name: str | None,
    output_dir: str | Path,
    *,
    timeout_seconds: int = 18,
) -> Path | None:
    code = extract_product_code(product_name)
    if not code:
        return None

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    image_url = find_sunon_image_url(code, product_name=product_name, timeout_seconds=timeout_seconds)
    if not image_url:
        return None
    return download_sunon_image(code, image_url, output_root, timeout_seconds=timeout_seconds)


def fetch_sunon_catalog_product_image(
    product_name: str | None,
    output_dir: str | Path,
    *,
    catalog_path: str | Path | None = None,
    live_lookup: bool | None = None,
    timeout_seconds: int = 18,
) -> Path | None:
    code = extract_product_code(product_name)
    if not code:
        return None

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    image_url = find_sunon_catalog_image_url(code, catalog_path=catalog_path)
    if not image_url and _sunon_catalog_live_lookup_enabled(live_lookup):
        image_url = find_sunon_exact_image_url(code, timeout_seconds=timeout_seconds)
    if not image_url:
        return None
    return download_sunon_image(code, image_url, output_root, timeout_seconds=timeout_seconds)


def find_sunon_catalog_image_url(code: str, *, catalog_path: str | Path | None = None) -> str | None:
    entry = find_sunon_catalog_entry(code, catalog_path=catalog_path)
    if not entry:
        return None
    image_url = str(entry.get("image_url") or "").strip()
    return image_url or None


def find_sunon_catalog_entry(code: str, *, catalog_path: str | Path | None = None) -> dict | None:
    match = find_sunon_catalog_match(code, catalog_path=catalog_path)
    return match[0]


def find_sunon_catalog_match(
    code: str,
    *,
    catalog_path: str | Path | None = None,
) -> tuple[dict | None, str | None, str | None]:
    catalog = load_sunon_catalog(catalog_path)
    for index, candidate in enumerate(sunon_code_candidates(code)):
        entry = catalog.get(normalize_sunon_code(candidate))
        if entry:
            match_type = "exact_code" if index == 0 else "base_code"
            return entry, candidate, match_type
    return None, None, None


def load_sunon_catalog(catalog_path: str | Path | None = None) -> dict[str, dict]:
    path = Path(catalog_path) if catalog_path else SUNON_CATALOG_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return {}

    catalog: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = normalize_sunon_code(entry.get("normalized_code") or entry.get("code"))
        image_url = str(entry.get("image_url") or "").strip()
        if not normalized or not image_url:
            continue
        clean = dict(entry)
        clean["normalized_code"] = normalized
        catalog[normalized] = clean
    return catalog


def find_sunon_image_url(
    code: str,
    *,
    product_name: str | None = None,
    timeout_seconds: int = 18,
) -> str | None:
    query = urllib.parse.urlencode({"s": code})
    search_url = f"{SUNON_SEARCH_URL}?{query}"
    html = _fetch_text(search_url, timeout_seconds=timeout_seconds)
    image_url = parse_sunon_image_url(html, code, base_url=search_url)
    if image_url:
        return image_url

    for search_term in _sunon_product_search_terms(code, product_name):
        image_url = _find_sunon_rest_product_image_url(
            search_term,
            code,
            timeout_seconds=timeout_seconds,
        )
        if image_url:
            return image_url
    return None


def find_sunon_exact_image_url(
    code: str,
    *,
    timeout_seconds: int = 18,
) -> str | None:
    query = urllib.parse.urlencode({"s": code})
    search_url = f"{SUNON_SEARCH_URL}?{query}"
    html = _fetch_text(search_url, timeout_seconds=timeout_seconds)
    image_url = parse_sunon_image_url(html, code, base_url=search_url)
    if image_url:
        return image_url
    return _find_sunon_rest_product_image_url(
        code,
        code,
        timeout_seconds=timeout_seconds,
        exact_code_only=True,
    )


def parse_sunon_image_url(html: str, code: str, *, base_url: str = SUNON_SEARCH_URL) -> str | None:
    normalized_code = normalize_sunon_code(code)
    if not normalized_code:
        return None

    for block in [*LI_RE.findall(html), *TR_RE.findall(html)]:
        if normalized_code not in normalize_sunon_code(_strip_tags(block)):
            continue
        image_url = _first_image_src(block, base_url)
        if image_url:
            return image_url
    return None


def parse_sunon_product_no_catalog_entries(
    html: str,
    *,
    product_url: str,
    product_title: str = "",
    last_seen: str = "",
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in TR_RE.findall(html):
        cells = TD_RE.findall(row)
        if len(cells) < 2:
            continue
        image_url = _first_image_src(row, product_url)
        if not image_url:
            continue
        for cell in cells[1:2]:
            for match in PRODUCT_CODE_RE.finditer(_strip_tags(cell).upper()):
                code = match.group(0).strip("-.")
                normalized = normalize_sunon_code(code)
                if len(normalized) < 4 or normalized in seen:
                    continue
                seen.add(normalized)
                entries.append(
                    {
                        "code": code,
                        "normalized_code": normalized,
                        "product_title": product_title,
                        "product_url": product_url,
                        "image_url": image_url,
                        "source_type": "product_no_table",
                        "confidence": "exact_code",
                        "last_seen": last_seen,
                    }
                )
    return entries


def download_sunon_image(
    code: str,
    image_url: str,
    output_dir: str | Path,
    *,
    timeout_seconds: int = 18,
) -> Path:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    data, content_type = _fetch_bytes(image_url, timeout_seconds=timeout_seconds)
    if not data:
        raise SunonImageLookupError(f"Sunon devolvio imagen vacia para {code}")
    _verify_image_bytes(code, data)
    suffix = _image_suffix(image_url, content_type)
    digest = hashlib.sha256((normalize_sunon_code(code) + image_url).encode("utf-8")).hexdigest()[:16]
    output = output_root / f"sunon_{normalize_sunon_code(code)}_{digest}{suffix}"
    output.write_bytes(data)
    return output


def _sunon_product_search_terms(code: str, product_name: str | None) -> list[str]:
    terms: list[str] = []

    def add(term: str | None) -> None:
        clean = re.sub(r"\s+", " ", str(term or "").strip())
        if clean and clean.lower() not in {item.lower() for item in terms}:
            terms.append(clean)

    add(code)
    name = str(product_name or "").strip()
    if name:
        without_code = re.sub(re.escape(str(code)), " ", name, flags=re.I)
        without_code = re.sub(r"\s+", " ", without_code).strip(" -.")
        add(without_code)
        for term in _product_family_terms(without_code):
            add(term)
        add(name)
    return terms


def _product_family_terms(value: str | None) -> list[str]:
    text = re.sub(r"[-_/]+", " ", str(value or ""))
    tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9]+", text)
        if token.lower()
        not in {
            "chair",
            "chairs",
            "table",
            "tables",
            "task",
            "office",
            "conference",
            "meeting",
            "stool",
            "stools",
            "seating",
            "lounge",
            "modular",
            "model",
            "sunon",
        }
    ]
    terms: list[str] = []
    if len(tokens) >= 2:
        terms.append(" ".join(tokens[:2]))
    if tokens:
        terms.append(tokens[0])
    return terms


def _find_sunon_rest_product_image_url(
    search_term: str,
    code: str,
    *,
    timeout_seconds: int,
    exact_code_only: bool = False,
) -> str | None:
    query = urllib.parse.urlencode({"search": search_term, "per_page": "6", "_embed": "1"})
    url = f"{SUNON_REST_PRODUCT_URL}?{query}"
    try:
        products = json.loads(_fetch_text(url, timeout_seconds=timeout_seconds))
    except (json.JSONDecodeError, SunonImageLookupError):
        return None
    if not isinstance(products, list):
        return None

    normalized_code = normalize_sunon_code(code)
    search_tokens = _meaningful_tokens(search_term)
    best: tuple[int, str] | None = None
    for product in products:
        if not isinstance(product, dict):
            continue
        if _is_document_like_product(product):
            continue
        image_url = _featured_media_url(product)
        if not image_url:
            continue
        haystack = _rest_product_text(product)
        has_exact_code = normalized_code and normalized_code in normalize_sunon_code(haystack)
        if exact_code_only and not has_exact_code:
            continue
        score = 0
        if has_exact_code:
            score += 100
        product_tokens = _meaningful_tokens(haystack)
        score += 10 * len(search_tokens & product_tokens)
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, image_url)
    return best[1] if best else None


def _is_document_like_product(product: dict) -> bool:
    title = product.get("title")
    title_text = title.get("rendered") if isinstance(title, dict) else title
    text = " ".join(
        str(value or "")
        for value in (title_text, product.get("link"), product.get("slug"))
    ).lower()
    return any(
        word in text
        for word in (
            "brochure",
            "manual",
            "specification",
            "installation",
            "download",
            "documents",
        )
    )


def _featured_media_url(product: dict) -> str | None:
    embedded = product.get("_embedded")
    if not isinstance(embedded, dict):
        return None
    media_items = embedded.get("wp:featuredmedia")
    if not isinstance(media_items, list):
        return None
    for media in media_items:
        if not isinstance(media, dict):
            continue
        media_details = media.get("media_details")
        if isinstance(media_details, dict):
            sizes = media_details.get("sizes")
            if isinstance(sizes, dict):
                for size_name in ("full", "large", "medium"):
                    size = sizes.get(size_name)
                    if isinstance(size, dict) and size.get("source_url"):
                        return str(size["source_url"])
        if media.get("source_url"):
            return str(media["source_url"])
    return None


def _rest_product_text(product: dict) -> str:
    values: list[str] = []
    for key in ("link", "slug"):
        values.append(str(product.get(key) or ""))
    for key in ("title", "content", "excerpt"):
        value = product.get(key)
        if isinstance(value, dict):
            values.append(_strip_tags(str(value.get("rendered") or "")))
        else:
            values.append(_strip_tags(str(value or "")))
    return " ".join(values)


def _meaningful_tokens(value: str | None) -> set[str]:
    generic = {
        "chair",
        "chairs",
        "table",
        "tables",
        "task",
        "office",
        "conference",
        "meeting",
        "stool",
        "stools",
        "model",
        "sunon",
    }
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9]+", str(value or ""))
        if len(token) >= 2 and token.lower() not in generic
    }


def _fetch_text(url: str, *, timeout_seconds: int) -> str:
    data, content_type = _fetch_bytes(url, timeout_seconds=timeout_seconds, max_bytes=4 * 1024 * 1024)
    charset = "utf-8"
    match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    if match:
        charset = match.group(1).strip("\"'")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _fetch_bytes(
    url: str,
    *,
    timeout_seconds: int,
    max_bytes: int = MAX_SUNON_IMAGE_BYTES,
) -> tuple[bytes, str]:
    request = urllib.request.Request(_ascii_url(url), headers={"User-Agent": SUNON_USER_AGENT})
    last_error: BaseException | None = None
    for _attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise SunonImageLookupError(f"Respuesta Sunon supera {max_bytes} bytes")
                return data, str(response.headers.get("Content-Type", ""))
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
    raise SunonImageLookupError(f"No se pudo consultar Sunon: {last_error}") from last_error


def _ascii_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%")
    query = urllib.parse.quote(parts.query, safe="=&%:+,;/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _first_image_src(html: str, base_url: str) -> str | None:
    match = IMG_SRC_RE.search(html)
    if not match:
        return None
    url = unescape(match.group(1).strip())
    if not url:
        return None
    return urllib.parse.urljoin(base_url, url)


def _strip_tags(html: str) -> str:
    return unescape(TAG_RE.sub(" ", html))


def _verify_image_bytes(code: str, data: bytes) -> None:
    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise SunonImageLookupError(f"Sunon no devolvio una imagen valida para {code}") from exc


def _image_suffix(image_url: str, content_type: str) -> str:
    path_suffix = Path(urllib.parse.urlparse(image_url).path).suffix.lower()
    if path_suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return ".jpg" if path_suffix == ".jpeg" else path_suffix
    content_type = content_type.lower()
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def sunon_lookup_enabled() -> bool:
    return os.environ.get("SUNON_IMAGE_LOOKUP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _sunon_catalog_live_lookup_enabled(value: bool | None = None) -> bool:
    if value is not None:
        return bool(value)
    return os.environ.get("SUNON_CATALOG_LIVE_LOOKUP_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
