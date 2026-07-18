from __future__ import annotations

import hashlib
from io import BytesIO
import json
import re
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from PIL import Image


_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_MONDECASA_IMAGES_MANIFEST_PATH = _DATA_DIR / "mondecasa_images.v1.json"
DEFAULT_MONDECASA_IMAGES_ARCHIVE_PATH = _DATA_DIR / "mondecasa_images.v1.zip"

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_ASSET_BYTES = 1 * 1024 * 1024
MAX_PAGES = 60
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 25_000_000
MAX_ASSIGNMENTS_PER_PAGE = 256
MAX_REFERENCES_PER_PAGE = 256
MAX_COMPRESSION_RATIO = 100

_ROOT_FIELDS = {
    "schema_version",
    "artifact_kind",
    "generated_on",
    "official_sources",
    "provenance",
    "counts",
    "pages",
}
_SOURCE_FIELDS = {"product_page_origin", "image_origin"}
_COUNT_FIELDS = {"pages", "assignments", "unique_assets", "total_asset_bytes"}
_PAGE_FIELDS = {
    "page_url",
    "gallery_image_url",
    "chosen_image_url",
    "reference_numbers",
    "assignments",
    "asset",
}
_ASSIGNMENT_FIELDS = {"source_code", "match_status"}
_ASSET_FIELDS = {
    "archive_name",
    "sha256",
    "size_bytes",
    "media_type",
    "width",
    "height",
}
_PAGE_ORIGIN = "https://www.mondecasa.com.sg"
_IMAGE_ORIGIN = "https://cdn.prod.website-files.com"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[A-Z0-9]{4,256}")
_PAGE_PATH = re.compile(r"/all-products/[a-z0-9]+(?:-[a-z0-9]+)*")
_IMAGE_PATH_PREFIX = "/66fcf3946613a3f9499ea3b8/"
_IMAGE_SEGMENT = re.compile(r"(?:[A-Za-z0-9._~-]|%20)+")
_ARCHIVE_NAME = re.compile(r"images/([0-9a-f]{64})\.jpg")


class MondecasaImageResourceError(ValueError):
    """El paquete offline de imágenes Mondecasa incumple su contrato cerrado."""


@dataclass(frozen=True)
class MondecasaImageResolution:
    match_status: Literal["exact_web", "model_web"]
    asset_bytes: bytes
    page_url: str
    image_url: str
    reference_numbers: tuple[str, ...]
    original_sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class MondecasaImageIndex:
    resource_fingerprint: str
    resolutions_by_code: Mapping[str, MondecasaImageResolution]


@dataclass(frozen=True)
class _AssetSpec:
    archive_name: str
    sha256: str
    size_bytes: int
    width: int
    height: int


@dataclass(frozen=True)
class _PageSpec:
    page_url: str
    chosen_image_url: str
    reference_numbers: tuple[str, ...]
    assignments: tuple[tuple[str, Literal["exact_web", "model_web"]], ...]
    asset: _AssetSpec


def _fail(code: str):
    raise MondecasaImageResourceError(code) from None


def _require_object(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _require_text(value: object, code: str, *, maximum: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(code)
    return value


def _require_integer(
    value: object,
    code: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        _fail(code)
    return value


def _normalize_code(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return "".join(re.findall(r"[A-Z0-9]", text.upper()))


def _canonical_url(value: object, *, page: bool) -> str:
    url = _require_text(value, "MONDECASA_IMAGE_URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        _fail("MONDECASA_IMAGE_URL")
    expected_host = "www.mondecasa.com.sg" if page else "cdn.prod.website-files.com"
    path_valid = _PAGE_PATH.fullmatch(parsed.path) is not None if page else _image_path(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.netloc != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not path_valid
    ):
        _fail("MONDECASA_IMAGE_URL")
    return url


def _image_path(path: str) -> bool:
    if not path.startswith(_IMAGE_PATH_PREFIX):
        return False
    segments = path[len(_IMAGE_PATH_PREFIX) :].split("/")
    return bool(
        segments
        and all(
            segment not in {"", ".", ".."}
            and _IMAGE_SEGMENT.fullmatch(segment) is not None
            for segment in segments
        )
        and segments[-1].endswith((".jpg", ".jpeg"))
    )


def _metadata(root: dict) -> None:
    generated_on = root["generated_on"]
    if (
        root["schema_version"] != "1.0"
        or root["artifact_kind"] != "official_product_image_archive"
        or not isinstance(generated_on, str)
    ):
        _fail("MONDECASA_IMAGE_METADATA")
    try:
        parsed_date = date.fromisoformat(generated_on)
    except ValueError:
        _fail("MONDECASA_IMAGE_METADATA")
    if parsed_date.isoformat() != generated_on:
        _fail("MONDECASA_IMAGE_METADATA")

    sources = _require_object(
        root["official_sources"], _SOURCE_FIELDS, "MONDECASA_IMAGE_SOURCES"
    )
    if (
        sources["product_page_origin"] != _PAGE_ORIGIN
        or sources["image_origin"] != _IMAGE_ORIGIN
    ):
        _fail("MONDECASA_IMAGE_SOURCES")

    provenance = root["provenance"]
    if (
        not isinstance(provenance, dict)
        or not provenance
        or len(provenance) > 32
        or any(
            not isinstance(key, str)
            or not key
            or key != key.strip()
            or len(key) > 64
            or not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 2048
            or any(ord(character) < 32 or ord(character) == 127 for character in key + value)
            for key, value in provenance.items()
        )
    ):
        _fail("MONDECASA_IMAGE_PROVENANCE")


def _asset_spec(value: object) -> _AssetSpec:
    asset = _require_object(value, _ASSET_FIELDS, "MONDECASA_IMAGE_ASSET")
    sha256 = _require_text(asset["sha256"], "MONDECASA_IMAGE_ASSET", maximum=64)
    archive_name = _require_text(
        asset["archive_name"], "MONDECASA_IMAGE_ASSET", maximum=80
    )
    size_bytes = _require_integer(
        asset["size_bytes"],
        "MONDECASA_IMAGE_ASSET",
        minimum=1,
        maximum=MAX_ASSET_BYTES,
    )
    width = _require_integer(
        asset["width"],
        "MONDECASA_IMAGE_ASSET",
        minimum=1,
        maximum=MAX_IMAGE_DIMENSION,
    )
    height = _require_integer(
        asset["height"],
        "MONDECASA_IMAGE_ASSET",
        minimum=1,
        maximum=MAX_IMAGE_DIMENSION,
    )
    match = _ARCHIVE_NAME.fullmatch(archive_name)
    if (
        _SHA256.fullmatch(sha256) is None
        or match is None
        or match.group(1) != sha256
        or asset["media_type"] != "image/jpeg"
        or width * height > MAX_IMAGE_PIXELS
    ):
        _fail("MONDECASA_IMAGE_ASSET")
    return _AssetSpec(archive_name, sha256, size_bytes, width, height)


def _normalized_values(
    value: object,
    code: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        _fail(code)
    result = []
    for raw in value:
        if not isinstance(raw, str):
            _fail(code)
        normalized = _normalize_code(raw)
        if raw != normalized or _CODE.fullmatch(normalized) is None:
            _fail(code)
        result.append(normalized)
    if len(result) != len(set(result)):
        _fail(code)
    return tuple(result)


def _page_specs(root: dict) -> tuple[tuple[_PageSpec, ...], dict[str, _AssetSpec]]:
    pages = root["pages"]
    if not isinstance(pages, list) or not pages:
        _fail("MONDECASA_IMAGE_PAGE")
    if len(pages) > MAX_PAGES:
        _fail("MONDECASA_IMAGE_PAGE_LIMIT")

    result = []
    assets_by_sha256: dict[str, _AssetSpec] = {}
    page_urls: set[str] = set()
    reference_pages: dict[str, str] = {}
    assigned_codes: set[str] = set()
    for raw_page in pages:
        page = _require_object(raw_page, _PAGE_FIELDS, "MONDECASA_IMAGE_PAGE")
        page_url = _canonical_url(page["page_url"], page=True)
        _canonical_url(page["gallery_image_url"], page=False)
        chosen_image_url = _canonical_url(page["chosen_image_url"], page=False)
        if page_url in page_urls:
            _fail("MONDECASA_IMAGE_PAGE")
        page_urls.add(page_url)

        references = _normalized_values(
            page["reference_numbers"],
            "MONDECASA_IMAGE_REFERENCE",
            maximum=MAX_REFERENCES_PER_PAGE,
        )
        for reference in references:
            prior = reference_pages.setdefault(reference, page_url)
            if prior != page_url:
                _fail("MONDECASA_IMAGE_REFERENCE")

        raw_assignments = page["assignments"]
        if (
            not isinstance(raw_assignments, list)
            or not raw_assignments
            or len(raw_assignments) > MAX_ASSIGNMENTS_PER_PAGE
        ):
            _fail("MONDECASA_IMAGE_ASSIGNMENT")
        assignments = []
        for raw_assignment in raw_assignments:
            assignment = _require_object(
                raw_assignment,
                _ASSIGNMENT_FIELDS,
                "MONDECASA_IMAGE_ASSIGNMENT",
            )
            source_code = assignment["source_code"]
            normalized = _normalize_code(source_code)
            status = assignment["match_status"]
            if (
                not isinstance(source_code, str)
                or source_code != normalized
                or _CODE.fullmatch(normalized) is None
                or normalized not in references
                or status not in {"exact_web", "model_web"}
                or normalized in assigned_codes
            ):
                _fail("MONDECASA_IMAGE_ASSIGNMENT")
            assigned_codes.add(normalized)
            assignments.append((normalized, status))
        statuses = {status for _, status in assignments}
        if len(statuses) != 1 or ("exact_web" in statuses and len(assignments) != 1):
            _fail("MONDECASA_IMAGE_ASSIGNMENT")

        asset = _asset_spec(page["asset"])
        prior_asset = assets_by_sha256.setdefault(asset.sha256, asset)
        if prior_asset != asset:
            _fail("MONDECASA_IMAGE_ASSET")
        result.append(
            _PageSpec(
                page_url,
                chosen_image_url,
                references,
                tuple(assignments),
                asset,
            )
        )
    return tuple(result), assets_by_sha256


def _validate_counts(
    value: object,
    pages: tuple[_PageSpec, ...],
    assets_by_sha256: dict[str, _AssetSpec],
) -> None:
    counts = _require_object(value, _COUNT_FIELDS, "MONDECASA_IMAGE_COUNTS")
    expected = {
        "pages": len(pages),
        "assignments": sum(len(page.assignments) for page in pages),
        "unique_assets": len(assets_by_sha256),
        "total_asset_bytes": sum(asset.size_bytes for asset in assets_by_sha256.values()),
    }
    if any(
        isinstance(counts[field], bool)
        or not isinstance(counts[field], int)
        or counts[field] != expected[field]
        for field in _COUNT_FIELDS
    ):
        _fail("MONDECASA_IMAGE_COUNTS")


def _safe_jpeg(data: bytes, spec: _AssetSpec) -> None:
    failed = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                if (
                    probe.format != "JPEG"
                    or getattr(probe, "is_animated", False)
                    or probe.size != (spec.width, spec.height)
                ):
                    failed = True
                probe.verify()
            if not failed:
                with Image.open(BytesIO(data)) as image:
                    image.load()
                    if image.format != "JPEG" or image.size != (spec.width, spec.height):
                        failed = True
    except Exception:
        failed = True
    if failed:
        _fail("MONDECASA_IMAGE_JPEG")


def _archive_assets(
    archive_bytes: bytes,
    assets_by_sha256: dict[str, _AssetSpec],
) -> dict[str, bytes]:
    if type(archive_bytes) is not bytes or not 0 < len(archive_bytes) <= MAX_ARCHIVE_BYTES:
        _fail("MONDECASA_IMAGE_ZIP_LIMIT")
    expected_names = {asset.archive_name for asset in assets_by_sha256.values()}
    result = {}
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if (
                len(names) != len(set(names))
                or set(names) != expected_names
                or any(_ARCHIVE_NAME.fullmatch(name) is None for name in names)
            ):
                _fail("MONDECASA_IMAGE_ZIP")
            for info in infos:
                sha256 = _ARCHIVE_NAME.fullmatch(info.filename).group(1)
                spec = assets_by_sha256.get(sha256)
                unix_mode = (info.external_attr >> 16) & 0o170000
                if (
                    spec is None
                    or info.is_dir()
                    or info.flag_bits & 0x1
                    or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                    or info.file_size != spec.size_bytes
                    or info.file_size > MAX_ASSET_BYTES
                    or unix_mode == 0o120000
                    or info.file_size > max(1024, info.compress_size * MAX_COMPRESSION_RATIO)
                ):
                    _fail("MONDECASA_IMAGE_ZIP")
                with archive.open(info, "r") as stream:
                    data = stream.read(MAX_ASSET_BYTES + 1)
                if (
                    len(data) != spec.size_bytes
                    or len(data) > MAX_ASSET_BYTES
                    or hashlib.sha256(data).hexdigest() != spec.sha256
                ):
                    _fail("MONDECASA_IMAGE_ASSET")
                _safe_jpeg(data, spec)
                result[sha256] = data
    except MondecasaImageResourceError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError, ValueError):
        _fail("MONDECASA_IMAGE_ZIP")
    if set(result) != set(assets_by_sha256):
        _fail("MONDECASA_IMAGE_ZIP")
    return result


def build_mondecasa_image_index(
    resource: object,
    archive_bytes: bytes,
) -> MondecasaImageIndex:
    root = _require_object(resource, _ROOT_FIELDS, "MONDECASA_IMAGE_RESOURCE")
    _metadata(root)
    pages, assets_by_sha256 = _page_specs(root)
    _validate_counts(root["counts"], pages, assets_by_sha256)
    asset_bytes = _archive_assets(archive_bytes, assets_by_sha256)

    canonical = json.dumps(
        root,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    resolutions = {}
    for page in pages:
        data = asset_bytes[page.asset.sha256]
        for source_code, status in page.assignments:
            resolutions[source_code] = MondecasaImageResolution(
                status,
                data,
                page.page_url,
                page.chosen_image_url,
                page.reference_numbers,
                page.asset.sha256,
                page.asset.width,
                page.asset.height,
            )
    return MondecasaImageIndex(
        fingerprint,
        MappingProxyType(dict(sorted(resolutions.items()))),
    )


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("MONDECASA_IMAGE_MANIFEST")
        result[key] = value
    return result


def _read_bounded(path: Path, maximum: int, code: str) -> bytes:
    try:
        with Path(path).open("rb") as source:
            data = source.read(maximum + 1)
    except (OSError, TypeError, ValueError):
        _fail(code)
    if not 0 < len(data) <= maximum:
        _fail(code)
    return data


def load_mondecasa_image_index(
    manifest_path: Path = DEFAULT_MONDECASA_IMAGES_MANIFEST_PATH,
    archive_path: Path = DEFAULT_MONDECASA_IMAGES_ARCHIVE_PATH,
) -> MondecasaImageIndex:
    manifest_bytes = _read_bounded(
        manifest_path, MAX_MANIFEST_BYTES, "MONDECASA_IMAGE_MANIFEST"
    )
    archive_bytes = _read_bounded(
        archive_path, MAX_ARCHIVE_BYTES, "MONDECASA_IMAGE_ZIP_LIMIT"
    )
    try:
        resource = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except MondecasaImageResourceError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail("MONDECASA_IMAGE_MANIFEST")
    return build_mondecasa_image_index(resource, archive_bytes)


def resolve_mondecasa_image(
    source_code: object,
    index: MondecasaImageIndex,
) -> MondecasaImageResolution | None:
    if not isinstance(index, MondecasaImageIndex):
        _fail("MONDECASA_IMAGE_INDEX")
    if isinstance(source_code, str) and len(source_code) > 2048:
        return None
    normalized = _normalize_code(source_code)
    if _CODE.fullmatch(normalized) is None:
        return None
    return index.resolutions_by_code.get(normalized)
