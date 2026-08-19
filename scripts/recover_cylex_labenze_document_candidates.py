"""Recupera sólo XObjects auditados del PDF Cylex fijado por hash.

Este carril es exclusivamente local. El PDF completo se considera no apto para
ingesta y nunca se copia al output: únicamente se decodifican los 75 XObjects
auditados, dentro de un subprocess con memoria y tiempo acotados.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import multiprocessing
import os
import re
import stat
import sys
import time
import warnings
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import fitz
from PIL import Image, ImageDraw, ImageFont

try:
    from mobiliti_saas.worker.catalog_sync.importers import common as _pdf_common
except ModuleNotFoundError as exc:  # Ejecución directa desde scripts/.
    if exc.name != "mobiliti_saas":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mobiliti_saas.worker.catalog_sync.importers import common as _pdf_common


CYLEX_DOCUMENT_URL = (
    "https://media.cylex.mx/companies/1203/5778/uploadedfiles/"
    "12035778_637885105004313129_SL_LABENZE_-_TENDENCE_MOBILI_-_SIN_PRECIO.pdf"
)
CYLEX_SOURCE_SHA256 = "dd8a856b0e10bd541abb2d60f3c470dd2e78bf2ea34315117f1a85c17cbffa34"
CYLEX_SOURCE_BYTES = 7_211_618
CYLEX_PAGE_COUNT = 121
CYLEX_XREF_LENGTH = 7_648
CANONICAL_NORMALIZED_RESEARCH_SHA256 = (
    "6fba55df0ba05237f7990d374b24773852bf77c0bcb6656e7968bfc8309ebcb8"
)

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_XOBJECT_RAW_BYTES = 8 * 1024 * 1024
MAX_XOBJECT_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_XOBJECT_PIXELS = 25_000_000
MAX_XOBJECT_DIMENSION = 8_192
MAX_SELECTED_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
MAX_WIRE_BYTES = 192 * 1024 * 1024
MAX_WORKER_MEMORY_BYTES = 1024 * 1024 * 1024
WORKER_TIMEOUT_SECONDS = 60
MAX_MASK_EXPANSION_RATIO = 1024

_SOURCE_NAME = "Tendence Mobili / media.cylex.mx"
_ALLOWED_OUTPUT_PARTS = (".superpowers", "sdd", "artifacts")
_EXPECTED_DISCARDED_ACTIONS = {"goto": 5, "uri": 131}
_EXPECTED_UNREACHABLE_XREFS = [7646]
_ACTIVE_TOKEN = re.compile(
    rb"/(?:JavaScript|JS|Launch|GoToR|SubmitForm|ImportData|OpenAction|AA|"
    rb"EmbeddedFiles|RichMedia|Filespec|AcroForm)\b"
)
_URI_TOKEN = re.compile(rb"/URI\b")
_GOTO_TOKEN = re.compile(rb"/GoTo\b")
_NEXT_TOKEN = re.compile(rb"/Next\b")
_XREF_REFERENCE_7646 = re.compile(rb"(?<![0-9])7646\s+0\s+R\b")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RecoveryError(ValueError):
    """Error estable y fail-closed del carril Cylex."""


@dataclass(frozen=True)
class AuditedSelection:
    source_code: str
    page_number: int
    xref: int
    bbox: tuple[float, float, float, float]
    width: int
    height: int

    @property
    def internal_id(self) -> str:
        return f"labenze:{self.source_code.casefold()}"


def _selection(
    source_code: str,
    page_number: int,
    xref: int,
    bbox: tuple[float, float, float, float],
    dimensions: tuple[int, int],
) -> AuditedSelection:
    return AuditedSelection(source_code, page_number, xref, bbox, *dimensions)


# Mapa congelado tras la auditoría visual del PDF exacto dd8a…ffa34. Cada
# XObject es único para una identidad; no hay reutilización implícita.
AUDITED_SELECTIONS = (
    _selection("106-00850", 25, 168, (88.2480, 485.5600, 226.9580, 679.6600), (375, 540)),
    _selection("106-01000", 39, 238, (373.4600, 460.3000, 541.6400, 680.9700), (378, 511)),
    _selection("107-00224", 108, 602, (424.7700, 417.8900, 508.3570, 545.9000), (313, 355)),
    _selection("107-01200", 109, 608, (338.5700, 475.7190, 527.3500, 711.8790), (408, 526)),
    _selection("108-02003", 7, 80, (122.5200, 188.0700, 547.7400, 499.7900), (850, 640)),
    _selection("108-02103", 8, 84, (71.0640, 152.6300, 453.8240, 525.1000), (600, 600)),
    _selection("108-02203", 9, 89, (211.5200, 394.8000, 416.3400, 507.3000), (498, 282)),
    _selection("155-08500", 30, 192, (71.0640, 500.3400, 222.0640, 671.4000), (449, 524)),
    _selection("155-08502", 32, 203, (36.6640, 472.2000, 285.7140, 687.9800), (482, 430)),
    _selection("155-08550", 31, 198, (374.2400, 532.1820, 506.5300, 701.7520), (494, 471)),
    _selection("155-10410-000", 71, 405, (53.8870, 237.3500, 294.0670, 523.9800), (441, 544)),
    _selection("155-10410-NGO", 71, 404, (304.1200, 239.6800, 544.7000, 521.8900), (431, 523)),
    _selection("155-10420-000", 72, 410, (55.2780, 251.0800, 284.4780, 526.9200), (591, 732)),
    _selection("155-10420-NGO", 72, 409, (305.9100, 250.8100, 530.9700, 525.7800), (446, 562)),
    _selection("155-10425-000", 73, 414, (73.5230, 243.4100, 291.2530, 499.5600), (355, 430)),
    _selection("155-10425-NGO", 73, 415, (311.6000, 227.8200, 539.9100, 511.2000), (306, 393)),
    _selection("155-10430-000", 74, 419, (142.6400, 163.9200, 460.2100, 531.0000), (441, 527)),
    _selection("155-10450-000", 75, 423, (145.5300, 188.9700, 463.0300, 531.9400), (395, 441)),
    _selection("155-10650", 106, 588, (121.3200, 423.4400, 215.1480, 547.5100), (421, 574)),
    _selection("155-10710-000", 47, 290, (119.8700, 420.0200, 202.1010, 527.7600), (228, 299)),
    _selection("155-10710-NGO", 47, 289, (383.1000, 420.9901, 471.6280, 524.9100), (245, 288)),
    _selection("155-10720-000", 48, 296, (56.5340, 224.9900, 289.2540, 502.8200), (412, 471)),
    _selection("155-10720-NGO", 48, 295, (305.7000, 221.8400, 531.8000, 502.0000), (382, 488)),
    _selection("155-10725-000", 49, 300, (60.4960, 231.3400, 279.4260, 483.6000), (359, 425)),
    _selection("155-10725-NGO", 49, 301, (329.4000, 234.4900, 520.5700, 488.3000), (266, 363)),
    _selection("155-10730-000", 50, 306, (351.4800, 490.6200, 540.6100, 593.2300), (359, 202)),
    _selection("155-10810-000", 53, 319, (119.2500, 400.4000, 240.0300, 535.4800), (455, 375)),
    _selection("155-10810-NGO", 53, 320, (366.0200, 395.6800, 482.0500, 536.1000), (424, 527)),
    _selection("155-10820-000", 54, 326, (64.3930, 243.8800, 291.3630, 525.9400), (372, 477)),
    _selection("155-10820-NGO", 54, 325, (312.3100, 244.7800, 545.6900, 524.7000), (383, 473)),
    _selection("155-10825-000", 55, 330, (46.0560, 230.4700, 288.9860, 518.5700), (397, 485)),
    _selection("155-10825-NGO", 55, 331, (335.9200, 242.8500, 528.5100, 516.1700), (276, 403)),
    _selection("155-10830-000", 56, 335, (148.2500, 171.5601, 469.2300, 519.4200), (378, 422)),
    _selection("155-10840-NGO", 59, 351, (303.5200, 229.7000, 559.8100, 479.1800), (420, 420)),
    _selection("155-10850-000", 57, 340, (353.1000, 503.1780, 519.5600, 699.0180), (415, 502)),
    _selection("155-10900-00", 81, 456, (374.7500, 528.5660, 506.4000, 699.8660), (369, 495)),
    _selection("155-10901-NAT", 82, 460, (142.6400, 187.8200, 462.1100, 530.7000), (327, 362)),
    _selection("155-14040-CRO", 85, 474, (153.2200, 165.2700, 503.5200, 504.1600), (420, 420)),
    _selection("155-14090-000", 84, 469, (72.5370, 234.1300, 291.7370, 516.4800), (357, 475)),
    _selection("155-14090-NGO", 84, 470, (301.7800, 263.8000, 559.5300, 513.1300), (420, 420)),
    _selection("155-18000-000", 86, 478, (153.2200, 170.5700, 459.2300, 540.3200), (406, 508)),
    _selection("155-18010-000", 87, 482, (53.3790, 213.6300, 294.0690, 522.5700), (418, 554)),
    _selection("155-18010-NGO", 87, 483, (299.9600, 217.1600, 550.2300, 519.9200), (421, 526)),
    _selection("155-18020-000", 88, 487, (60.0620, 206.6000, 295.9620, 519.5100), (384, 527)),
    _selection("155-18020-NGO", 88, 488, (303.6800, 213.6500, 547.2100, 520.3900), (397, 516)),
    _selection("155-18025-000", 89, 492, (161.8000, 198.5000, 448.6500, 526.5800), (467, 553)),
    _selection("155-18030-000", 90, 496, (167.5700, 210.9200, 450.6500, 530.6600), (380, 444)),
    _selection("155-18050-000", 91, 500, (168.4800, 211.7900, 444.8500, 531.2900), (409, 489)),
    _selection("155-19010-000", 63, 369, (58.0000, 224.1400, 290.6100, 530.2400), (381, 517)),
    _selection("155-19010-NGO", 63, 370, (311.3100, 223.3700, 543.0800, 532.9500), (380, 515)),
    _selection("155-19020-000", 64, 374, (45.1840, 233.6400, 299.9040, 525.3100), (418, 493)),
    _selection("155-19020-NGO", 64, 375, (301.7400, 233.5900, 550.8600, 524.0100), (423, 507)),
    _selection("155-19030-000", 66, 383, (166.9600, 140.1400, 445.7500, 489.4700), (321, 414)),
    _selection("155-19040-NGO", 68, 391, (56.3760, 261.3800, 313.0860, 510.1900), (420, 420)),
    _selection("155-19045-CRO", 68, 392, (299.5000, 266.1900, 556.1600, 514.9000), (420, 420)),
    _selection("155-19050-000", 67, 387, (141.6300, 162.6000, 462.8400, 511.7600), (466, 522)),
    _selection("155-20110-000", 40, 242, (115.1300, 374.6600, 209.4020, 498.6000), (284, 384)),
    _selection("155-20110-NGO", 41, 251, (386.3800, 392.9700, 491.8400, 537.6600), (380, 536)),
    _selection("155-20115-CRO", 44, 269, (121.6500, 409.0800, 224.5600, 528.7800), (447, 535)),
    _selection("155-20115-NGO", 44, 272, (382.2600, 400.2000, 492.6900, 525.4500), (445, 521)),
    _selection("155-20120-000", 41, 250, (113.5000, 399.6600, 219.0400, 535.2600), (285, 377)),
    _selection("155-20125-000", 42, 258, (62.0480, 238.3100, 284.0880, 519.3600), (324, 423)),
    _selection("155-20125-NGO", 42, 259, (310.5900, 238.5500, 525.3300, 522.3000), (404, 551)),
    _selection("155-20130-000", 43, 265, (132.1600, 178.9700, 482.0500, 506.9000), (416, 401)),
    _selection("155-20140-BCO", 45, 278, (81.6320, 238.6500, 290.6120, 519.1200), (403, 557)),
    _selection("155-20140-NGO", 45, 280, (324.5000, 241.6000, 546.2800, 519.6900), (421, 544)),
    _selection("155-20200-000", 77, 434, (365.1800, 477.6890, 526.4500, 693.7590), (394, 545)),
    _selection("155-20300-000", 80, 450, (107.9400, 415.3100, 250.0700, 547.0600), (386, 371)),
    _selection("155-20350-000", 80, 451, (309.7000, 414.4000, 539.1500, 547.0600), (536, 321)),
    _selection("155-20850-000", 52, 315, (44.8370, 465.8400, 235.1270, 691.5400), (406, 496)),
    _selection("155-30835-000", 58, 346, (385.6000, 407.5200, 482.8960, 535.2600), (270, 354)),
    _selection("155-30835-NGO", 58, 345, (109.4600, 407.6600, 240.8000, 535.6300), (420, 420)),
    _selection("155-30845-CRO", 59, 350, (62.8960, 212.2400, 319.9860, 461.7200), (420, 420)),
    _selection("157-09000", 119, 660, (377.3000, 470.1300, 541.0900, 709.4900), (367, 553)),
    _selection("157-10650", 117, 650, (134.9200, 418.9500, 218.5790, 547.0600), (232, 355)),
)

CYLEX_SEMANTICALLY_BLOCKED_CODES = frozenset(
    {
        "108-02004",
        "108-2003M",
        "108-2004M",
        "108-02102",
        "108-02104",
        "108-2103M",
        "108-2104M",
        "108-02202",
        "108-02204",
        "155-19025-000",
        "155-19025-NGO",
        "155-10950-000",
        "155-14050-000",
        "155-10600-TAP",
    }
)


def _fail(code: str) -> None:
    raise RecoveryError(code) from None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _io_path(path: Path) -> Path:
    """Use the Windows extended path form without changing manifest paths."""
    resolved = path.resolve()
    if os.name == "nt" and not str(resolved).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(resolved))
    return resolved


def _read_regular_file(path: Path, maximum: int) -> bytes:
    try:
        source = Path(path)
        before = os.lstat(source)
    except Exception:
        _fail("SOURCE_FILE")
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail("SOURCE_FILE")
    if not 0 < before.st_size <= maximum:
        _fail("SOURCE_SIZE")
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
        ):
            _fail("SOURCE_CHANGED")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or len(data) != opened.st_size
        ):
            _fail("SOURCE_CHANGED")
        if len(data) > maximum:
            _fail("SOURCE_SIZE")
        return data
    except RecoveryError:
        raise
    except Exception:
        _fail("SOURCE_FILE")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


def _read_exact_source(
    path: Path,
    *,
    expected_sha256: str = CYLEX_SOURCE_SHA256,
    expected_bytes: int = CYLEX_SOURCE_BYTES,
) -> bytes:
    if _HEX_SHA256.fullmatch(str(expected_sha256)) is None:
        _fail("SOURCE_HASH")
    data = _read_regular_file(path, MAX_INPUT_BYTES)
    if len(data) != expected_bytes:
        _fail("SOURCE_SIZE")
    if _sha256(data) != expected_sha256:
        _fail("SOURCE_HASH")
    if not data.startswith(b"%PDF-"):
        _fail("SOURCE_MAGIC")
    return data


def _json_loads(line: str) -> dict:
    try:
        value = json.loads(
            line,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except Exception:
        _fail("REPORT_JSON")
    if not isinstance(value, dict):
        _fail("REPORT_SCHEMA")
    return value


def _load_bbox_rows(
    path: Path,
    *,
    expected_sha256: str = CANONICAL_NORMALIZED_RESEARCH_SHA256,
) -> list[dict]:
    data = _read_regular_file(path, MAX_REPORT_BYTES)
    if _sha256(data) != expected_sha256:
        _fail("REPORT_HASH")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _fail("REPORT_ENCODING")
    rows = [_json_loads(line) for line in text.splitlines() if line]
    selections = {selection.internal_id: selection for selection in AUDITED_SELECTIONS}
    found: dict[str, dict] = {}
    blocked = set()
    cylex_count = 0
    for row in rows:
        candidate = row.get("candidate")
        if not isinstance(candidate, Mapping) or candidate.get("source_name") != _SOURCE_NAME:
            continue
        cylex_count += 1
        identity = row.get("canonical_identity")
        review = row.get("review")
        if (
            row.get("schema_version") != 1
            or row.get("supplier") != "labenze"
            or not isinstance(identity, Mapping)
            or not isinstance(review, Mapping)
            or review.get("approved") is not False
            or review.get("reviewer") != ""
            or review.get("reviewed_at") is not None
            or candidate.get("document_url") != CYLEX_DOCUMENT_URL
        ):
            _fail("REPORT_SCHEMA")
        internal_id = row.get("internal_id")
        source_code = identity.get("source_code")
        disposition = candidate.get("document_disposition")
        if disposition == "document_semantic_blocked":
            blocked.add(source_code)
            continue
        if disposition != "document_bbox_review" or internal_id not in selections:
            _fail("REPORT_DISPOSITION")
        selection = selections[str(internal_id)]
        if (
            source_code != selection.source_code
            or candidate.get("page_number") != selection.page_number
            or row.get("acquisition_kind") != "document_page"
            or row.get("terminal_status") != "found_candidate"
        ):
            _fail("REPORT_IDENTITY")
        if internal_id in found:
            _fail("REPORT_DUPLICATE")
        found[str(internal_id)] = json.loads(json.dumps(row))
    if cylex_count != 89 or blocked != CYLEX_SEMANTICALLY_BLOCKED_CODES:
        _fail("REPORT_COVERAGE")
    if set(found) != set(selections) or len(found) != 75:
        _fail("REPORT_COVERAGE")
    return [found[key] for key in sorted(found)]


def _bounded_flate_decode(
    raw: bytes,
    *,
    expected_size: int,
    max_expanded_bytes: int,
    max_ratio: int,
) -> bytes:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= MAX_XOBJECT_RAW_BYTES
        or type(expected_size) is not int
        or not 0 <= expected_size <= max_expanded_bytes <= MAX_XOBJECT_EXPANDED_BYTES
        or type(max_ratio) is not int
        or not 1 <= max_ratio <= MAX_MASK_EXPANSION_RATIO
    ):
        _fail("PDF_STREAM_ARGUMENT")
    decoder = zlib.decompressobj()
    output = bytearray()
    try:
        for offset in range(0, len(raw), 64 * 1024):
            pending = raw[offset : offset + 64 * 1024]
            while pending:
                chunk = decoder.decompress(
                    pending,
                    min(64 * 1024, max_expanded_bytes - len(output) + 1),
                )
                output.extend(chunk)
                if len(output) > max_expanded_bytes:
                    _fail("PDF_STREAM_LIMIT")
                remaining = decoder.unconsumed_tail
                if remaining == pending and not chunk:
                    _fail("PDF_STREAM_INVALID")
                pending = remaining
        if not decoder.eof:
            output.extend(decoder.flush(max_expanded_bytes - len(output) + 1))
    except RecoveryError:
        raise
    except Exception:
        _fail("PDF_STREAM_INVALID")
    if not decoder.eof or decoder.unused_data:
        _fail("PDF_STREAM_INVALID")
    if len(output) != expected_size:
        _fail("PDF_STREAM_SIZE")
    if output and len(output) / len(raw) > max_ratio:
        _fail("PDF_STREAM_RATIO")
    return bytes(output)


def _classify_discardable_object(raw_object: bytes) -> str | None:
    if type(raw_object) is not bytes or len(raw_object) > 64 * 1024:
        _fail("PDF_OBJECT_LIMIT")
    if _ACTIVE_TOKEN.search(raw_object) or _NEXT_TOKEN.search(raw_object):
        _fail("PDF_ACTIVE_CONTENT")
    has_uri = _URI_TOKEN.search(raw_object) is not None
    has_goto = _GOTO_TOKEN.search(raw_object) is not None
    if has_uri and has_goto:
        _fail("PDF_ACTIVE_CONTENT")
    if has_uri:
        if re.search(rb"/S\s*/URI\b", raw_object) is None:
            _fail("PDF_ACTIVE_CONTENT")
        return "discard_uri"
    if has_goto:
        if (
            re.search(rb"/Type\s*/Action\b", raw_object) is None
            or re.search(rb"/S\s*/GoTo\b", raw_object) is None
        ):
            _fail("PDF_ACTIVE_CONTENT")
        return "discard_goto"
    if re.search(rb"/Type\s*/Action\b", raw_object):
        _fail("PDF_ACTIVE_CONTENT")
    return None


def _pdf_integer(document, xref: int, key: str) -> int:
    value = _pdf_common._pdf_integer(document, xref, key)
    if value is None:
        _fail("PDF_XOBJECT_INVALID")
    return value


def _raw_stream(document, xref: int) -> bytes:
    if not 0 < xref < document.xref_length() or not document.xref_is_stream(xref):
        _fail("PDF_XOBJECT_INVALID")
    declared = _pdf_integer(document, xref, "Length")
    if not 0 < declared <= MAX_XOBJECT_RAW_BYTES:
        _fail("PDF_XOBJECT_LIMIT")
    try:
        raw = document.xref_stream_raw(xref)
    except Exception:
        _fail("PDF_XOBJECT_INVALID")
    if not isinstance(raw, bytes) or len(raw) != declared:
        _fail("PDF_XOBJECT_INVALID")
    return raw


def _object_bytes(document, xref: int) -> bytes:
    try:
        value = document.xref_object(xref, compressed=False)
    except Exception:
        _fail("PDF_XREF_INVALID")
    if not isinstance(value, str):
        _fail("PDF_XREF_INVALID")
    raw = value.encode("latin-1", "ignore")
    if _classify_discardable_object(raw) is not None:
        _fail("PDF_SELECTED_ACTION")
    return raw


def _parse_xref(value: tuple[str, str]) -> int | None:
    if value[0] == "null":
        return None
    match = re.fullmatch(r"([1-9][0-9]*) 0 R", value[1]) if value[0] == "xref" else None
    if match is None:
        _fail("PDF_XOBJECT_INVALID")
    return int(match.group(1))


def _validate_flate_auxiliary(
    document,
    xref: int,
    *,
    expected_size: int,
    max_ratio: int,
) -> dict:
    _object_bytes(document, xref)
    if _pdf_common._pdf_filters(document, xref) != ("FlateDecode",):
        _fail("PDF_XOBJECT_FILTER")
    raw = _raw_stream(document, xref)
    _bounded_flate_decode(
        raw,
        expected_size=expected_size,
        max_expanded_bytes=MAX_XOBJECT_EXPANDED_BYTES,
        max_ratio=max_ratio,
    )
    return {
        "xref": xref,
        "raw_bytes": len(raw),
        "raw_sha256": _sha256(raw),
        "decoded_bytes": expected_size,
    }


def _validate_selected_xobject(document, selection: AuditedSelection) -> dict:
    raw_object = _object_bytes(document, selection.xref)
    if document.xref_get_key(selection.xref, "Subtype") != ("name", "/Image"):
        _fail("PDF_XOBJECT_TYPE")
    width = _pdf_integer(document, selection.xref, "Width")
    height = _pdf_integer(document, selection.xref, "Height")
    bits = _pdf_integer(document, selection.xref, "BitsPerComponent")
    if (width, height) != (selection.width, selection.height) or bits != 8:
        _fail("PDF_XOBJECT_DIMENSIONS")
    if (
        width > MAX_XOBJECT_DIMENSION
        or height > MAX_XOBJECT_DIMENSION
        or width * height > MAX_XOBJECT_PIXELS
    ):
        _fail("PDF_XOBJECT_LIMIT")
    filters = _pdf_common._pdf_filters(document, selection.xref)
    if filters not in {("DCTDecode",), ("FlateDecode",)}:
        _fail("PDF_XOBJECT_FILTER")
    raw = _raw_stream(document, selection.xref)
    color_kind, color_value = document.xref_get_key(selection.xref, "ColorSpace")
    palette = None
    if color_kind == "name" and color_value == "/DeviceRGB":
        components = 3
        if filters == ("FlateDecode",):
            _bounded_flate_decode(
                raw,
                expected_size=width * height * components,
                max_expanded_bytes=MAX_XOBJECT_EXPANDED_BYTES,
                max_ratio=200,
            )
    elif color_kind == "array":
        match = re.fullmatch(
            r"\[\s*/Indexed\s*/DeviceRGB\s+([0-9]+)\s+([1-9][0-9]*)\s+0\s+R\s*\]",
            color_value,
        )
        if match is None or filters != ("FlateDecode",):
            _fail("PDF_XOBJECT_COLORSPACE")
        highest_index = int(match.group(1))
        palette_xref = int(match.group(2))
        if highest_index > 255:
            _fail("PDF_XOBJECT_COLORSPACE")
        palette = _validate_flate_auxiliary(
            document,
            palette_xref,
            expected_size=(highest_index + 1) * 3,
            max_ratio=200,
        )
        components = 1
        _bounded_flate_decode(
            raw,
            expected_size=width * height,
            max_expanded_bytes=MAX_XOBJECT_EXPANDED_BYTES,
            max_ratio=200,
        )
    else:
        _fail("PDF_XOBJECT_COLORSPACE")
    if filters == ("DCTDecode",):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if (
                        image.format != "JPEG"
                        or image.size != (width, height)
                        or getattr(image, "is_animated", False)
                    ):
                        _fail("PDF_XOBJECT_IMAGE")
                    image.verify()
        except RecoveryError:
            raise
        except Exception:
            _fail("PDF_XOBJECT_IMAGE")
    smask_xref = _parse_xref(document.xref_get_key(selection.xref, "SMask"))
    mask = None
    if smask_xref is not None:
        _object_bytes(document, smask_xref)
        if (
            document.xref_get_key(smask_xref, "Subtype") != ("name", "/Image")
            or document.xref_get_key(smask_xref, "ColorSpace") != ("name", "/DeviceGray")
            or _pdf_integer(document, smask_xref, "Width") != width
            or _pdf_integer(document, smask_xref, "Height") != height
        ):
            _fail("PDF_XOBJECT_MASK")
        mask_bits = _pdf_integer(document, smask_xref, "BitsPerComponent")
        if mask_bits not in {1, 8}:
            _fail("PDF_XOBJECT_MASK")
        expected_mask_size = ((width * mask_bits + 7) // 8) * height
        mask = _validate_flate_auxiliary(
            document,
            smask_xref,
            expected_size=expected_mask_size,
            max_ratio=MAX_MASK_EXPANSION_RATIO,
        )
        mask["bits_per_component"] = mask_bits
    return {
        "xref": selection.xref,
        "width": width,
        "height": height,
        "bits_per_component": bits,
        "components": components,
        "filters": list(filters),
        "raw_bytes": len(raw),
        "raw_sha256": _sha256(raw),
        "palette": palette,
        "smask": mask,
        "object_sha256": _sha256(raw_object),
    }


def _scan_document_structure(document, source: bytes) -> dict:
    if (
        not document.is_pdf
        or document.needs_pass
        or document.is_encrypted
        or document.page_count != CYLEX_PAGE_COUNT
        or document.xref_length() != CYLEX_XREF_LENGTH
        or document.embfile_names()
    ):
        _fail("PDF_STRUCTURE")
    discarded = {"goto": 0, "uri": 0}
    unreachable = []
    for xref in range(1, document.xref_length()):
        try:
            raw = document.xref_object(xref, compressed=False).encode("latin-1", "ignore")
        except Exception:
            if xref != 7646:
                _fail("PDF_XREF_INVALID")
            unreachable.append(xref)
            continue
        classification = _classify_discardable_object(raw)
        if classification == "discard_uri":
            discarded["uri"] += 1
        elif classification == "discard_goto":
            discarded["goto"] += 1
    if discarded != _EXPECTED_DISCARDED_ACTIONS:
        _fail("PDF_ACTION_PROFILE")
    if unreachable != _EXPECTED_UNREACHABLE_XREFS or _XREF_REFERENCE_7646.search(source):
        _fail("PDF_XREF_PROFILE")
    return {
        "discarded_actions": discarded,
        "discarded_unreachable_xrefs": unreachable,
    }


def _rect_close(left, right, tolerance: float = 0.02) -> bool:
    return len(left) == len(right) == 4 and all(
        math.isfinite(float(a)) and abs(float(a) - float(b)) <= tolerance
        for a, b in zip(left, right)
    )


def _validate_page_binding(document, selection: AuditedSelection) -> dict:
    page = document.load_page(selection.page_number - 1)
    try:
        hits = page.search_for(selection.source_code)
        page_xrefs = {int(image[0]) for image in page.get_images(full=True)}
        rects = page.get_image_rects(selection.xref, transform=False)
    except Exception:
        _fail("PDF_PAGE_PARSE")
    if len(hits) != 1 or selection.xref not in page_xrefs:
        _fail("PDF_PAGE_IDENTITY")
    matching_rects = [rect for rect in rects if _rect_close(tuple(rect), selection.bbox)]
    if len(matching_rects) != 1:
        _fail("PDF_BBOX_MISMATCH")
    return {
        "page_number": selection.page_number,
        "page_rect": [round(float(value), 4) for value in tuple(page.rect)],
        "code_bbox": [round(float(value), 4) for value in tuple(hits[0])],
        "bbox": [round(float(value), 4) for value in selection.bbox],
        "bbox_occurrences": len(rects),
    }


def _pixmap_png(document, xref: int, smask_xref: int | None) -> bytes:
    base = mask = combined = converted = None
    try:
        base = fitz.Pixmap(document, xref)
        if smask_xref is not None and not base.alpha:
            mask = fitz.Pixmap(document, smask_xref)
            if (base.width, base.height) != (mask.width, mask.height):
                _fail("PDF_XOBJECT_MASK")
            combined = fitz.Pixmap(base, mask)
        else:
            combined = base
        if combined.colorspace is None or combined.colorspace.n not in {1, 3}:
            converted = fitz.Pixmap(fitz.csRGB, combined)
            target = converted
        elif combined.colorspace.n == 1:
            converted = fitz.Pixmap(fitz.csRGB, combined)
            target = converted
        else:
            target = combined
        data = target.tobytes("png")
    except RecoveryError:
        raise
    except Exception:
        _fail("PDF_XOBJECT_DECODE")
    finally:
        converted = None
        combined = None
        mask = None
        base = None
    if not 0 < len(data) <= MAX_XOBJECT_RAW_BYTES:
        _fail("PDF_XOBJECT_OUTPUT_LIMIT")
    return data


def _validate_png(data: bytes, expected_dimensions: tuple[int, int]) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format != "PNG"
                    or image.size != expected_dimensions
                    or image.mode not in {"RGB", "RGBA"}
                    or getattr(image, "is_animated", False)
                ):
                    _fail("OUTPUT_IMAGE_INVALID")
                image.verify()
    except RecoveryError:
        raise
    except Exception:
        _fail("OUTPUT_IMAGE_INVALID")
    return expected_dimensions


def _recover_payload(source: bytes, rows: Sequence[Mapping[str, object]]) -> dict:
    if _sha256(source) != CYLEX_SOURCE_SHA256 or len(source) != CYLEX_SOURCE_BYTES:
        _fail("SOURCE_HASH")
    row_by_id = {str(row["internal_id"]): row for row in rows}
    if len(row_by_id) != 75 or set(row_by_id) != {item.internal_id for item in AUDITED_SELECTIONS}:
        _fail("REPORT_COVERAGE")
    document = None
    assets: dict[str, bytes] = {}
    candidates = []
    selected_expanded = 0
    try:
        document = fitz.open(stream=source, filetype="pdf")
        structure = _scan_document_structure(document, source)
        for selection in sorted(AUDITED_SELECTIONS, key=lambda item: item.internal_id):
            row = row_by_id[selection.internal_id]
            binding = _validate_page_binding(document, selection)
            xobject = _validate_selected_xobject(document, selection)
            selected_expanded += selection.width * selection.height * 4
            if selected_expanded > MAX_SELECTED_EXPANDED_BYTES:
                _fail("PDF_SELECTED_EXPANSION_LIMIT")
            smask = xobject.get("smask")
            smask_xref = int(smask["xref"]) if isinstance(smask, Mapping) else None
            png = _pixmap_png(document, selection.xref, smask_xref)
            _validate_png(png, (selection.width, selection.height))
            asset_sha256 = _sha256(png)
            if asset_sha256 in assets:
                _fail("OUTPUT_ASSET_SHARED")
            assets[asset_sha256] = png
            identity = row["canonical_identity"]
            assert isinstance(identity, Mapping)
            shortest = min(selection.width, selection.height)
            candidates.append(
                {
                    "schema_version": 1,
                    "supplier": "labenze",
                    "internal_id": selection.internal_id,
                    "source_code": selection.source_code,
                    "name": identity.get("name"),
                    "product_key": identity.get("product_key"),
                    "visual_signature_sha256": identity.get("visual_signature_sha256"),
                    "report_candidate_id": row.get("report_candidate_id"),
                    "document_url": CYLEX_DOCUMENT_URL,
                    "product_url": f"{CYLEX_DOCUMENT_URL}#page={selection.page_number}",
                    "image_source_url": None,
                    "source_kind": "catalog_pdf",
                    "document_sha256": CYLEX_SOURCE_SHA256,
                    "document_bytes": CYLEX_SOURCE_BYTES,
                    "page_number": selection.page_number,
                    "page_rect": binding["page_rect"],
                    "code_bbox": binding["code_bbox"],
                    "bbox": binding["bbox"],
                    "xref": selection.xref,
                    "bbox_occurrences": binding["bbox_occurrences"],
                    "extraction_mode": "selected_xobject",
                    "page_rendered": False,
                    "xobject": xobject,
                    "asset": {
                        "path": f"candidates/{asset_sha256}.png",
                        "sha256": asset_sha256,
                        "bytes": len(png),
                        "media_type": "image/png",
                        "width": selection.width,
                        "height": selection.height,
                        "source_shortest_side": shortest,
                        "source_shortest_side_512_plus": shortest >= 512,
                    },
                    "approved": False,
                    "reviewer": "",
                    "reviewed_at": None,
                    "checks": {
                        "page_identity_exact": None,
                        "bbox_is_product_only": None,
                        "full_product_visible": None,
                        "not_cropped": None,
                        "configuration_supported": None,
                        "clean_background": None,
                    },
                    "next_action": "manual_visual_and_quality_review",
                }
            )
    except RecoveryError:
        raise
    except Exception:
        _fail("PDF_INVALID")
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass
    if len(candidates) != 75 or len(assets) != 75:
        _fail("OUTPUT_COVERAGE")
    output_bytes = sum(len(value) for value in assets.values())
    if output_bytes > MAX_OUTPUT_BYTES:
        _fail("OUTPUT_BUDGET")
    return {
        "security": {
            "source_url": CYLEX_DOCUMENT_URL,
            "source_sha256": CYLEX_SOURCE_SHA256,
            "source_bytes": CYLEX_SOURCE_BYTES,
            "page_count": CYLEX_PAGE_COUNT,
            "xref_length": CYLEX_XREF_LENGTH,
            "input_document_accepted": False,
            "input_document_disposition": "rejected_as_whole_pdf",
            "common_preflight_observed_result": "PDF_UNSAFE",
            "extraction_mode": "selected_xobject_only",
            "page_assets_emitted": 0,
            "sanitized_pdf_emitted": False,
            "selected_xobject_count": 75,
            "selected_expanded_bytes_upper_bound": selected_expanded,
            **structure,
        },
        "candidates": candidates,
        "assets": assets,
    }


def _set_unix_worker_limit() -> None:
    if os.name == "nt":
        return
    try:
        import resource

        current = resource.getrlimit(resource.RLIMIT_AS)[1]
        hard = MAX_WORKER_MEMORY_BYTES if current == resource.RLIM_INFINITY else min(
            current, MAX_WORKER_MEMORY_BYTES
        )
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    except Exception:
        _fail("WORKER_MEMORY_LIMIT")


def _worker_entry(control, output, source: bytes, rows: list[dict]) -> None:
    try:
        _set_unix_worker_limit()
        if control.recv_bytes(1) != b"G":
            return
        payload = _recover_payload(source, rows)
        wire = {
            "ok": True,
            "pid": os.getpid(),
            "security": payload["security"],
            "candidates": payload["candidates"],
            "assets": {
                digest: base64.b64encode(data).decode("ascii")
                for digest, data in payload["assets"].items()
            },
        }
        encoded = json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_WIRE_BYTES:
            _fail("WORKER_OUTPUT_LIMIT")
        output.send_bytes(encoded)
    except RecoveryError as exc:
        output.send_bytes(
            json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")).encode("utf-8")
        )
    except BaseException:
        try:
            output.send_bytes(b'{"ok":false,"error":"WORKER_FAILURE"}')
        except Exception:
            pass
    finally:
        control.close()
        output.close()


def _stop_process(process) -> None:
    try:
        process.join(1)
    except Exception:
        pass
    if process.is_alive():
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.join(1)
        except Exception:
            pass
    if process.is_alive():
        killer = getattr(process, "kill", None)
        if callable(killer):
            try:
                killer()
            except Exception:
                pass
            try:
                process.join(1)
            except Exception:
                pass


def recover_in_subprocess(source: bytes, rows: Sequence[Mapping[str, object]]) -> dict:
    if type(source) is not bytes or _sha256(source) != CYLEX_SOURCE_SHA256:
        _fail("SOURCE_HASH")
    plain_rows = [json.loads(json.dumps(row)) for row in rows]
    context = multiprocessing.get_context("spawn")
    control_read, control_write = context.Pipe(duplex=False)
    output_read, output_write = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker_entry,
        args=(control_read, output_write, source, plain_rows),
    )
    process.daemon = True
    job = None
    message = None
    try:
        process.start()
        control_read.close()
        output_write.close()
        job = _pdf_common._windows_text_job(process)
        if job is None:
            _fail("WORKER_MEMORY_LIMIT")
        control_write.send_bytes(b"G")
        if not output_read.poll(WORKER_TIMEOUT_SECONDS):
            _fail("WORKER_TIMEOUT")
        try:
            message = output_read.recv_bytes(MAX_WIRE_BYTES)
        except Exception:
            _fail("WORKER_OUTPUT_LIMIT")
    finally:
        _stop_process(process)
        try:
            _pdf_common._close_windows_handle(job)
        except Exception:
            pass
        for connection in (control_read, control_write, output_read, output_write):
            try:
                connection.close()
            except Exception:
                pass
        try:
            process.close()
        except Exception:
            pass
    if message is None:
        _fail("WORKER_FAILURE")
    try:
        wire = json.loads(message.decode("utf-8"))
    except Exception:
        _fail("WORKER_PROTOCOL")
    if not isinstance(wire, dict) or wire.get("ok") is not True:
        _fail(str(wire.get("error") if isinstance(wire, dict) else "WORKER_PROTOCOL"))
    encoded_assets = wire.get("assets")
    if not isinstance(encoded_assets, dict) or len(encoded_assets) != 75:
        _fail("WORKER_PROTOCOL")
    assets: dict[str, bytes] = {}
    total = 0
    for digest, encoded in encoded_assets.items():
        if _HEX_SHA256.fullmatch(str(digest)) is None or not isinstance(encoded, str):
            _fail("WORKER_PROTOCOL")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            _fail("WORKER_PROTOCOL")
        if _sha256(data) != digest:
            _fail("WORKER_ASSET_HASH")
        total += len(data)
        if total > MAX_OUTPUT_BYTES:
            _fail("OUTPUT_BUDGET")
        assets[str(digest)] = data
    candidates = wire.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 75:
        _fail("WORKER_PROTOCOL")
    for row in candidates:
        if not isinstance(row, Mapping) or row.get("approved") is not False:
            _fail("WORKER_PROTOCOL")
        asset = row.get("asset")
        if not isinstance(asset, Mapping) or asset.get("sha256") not in assets:
            _fail("WORKER_PROTOCOL")
        _validate_png(
            assets[str(asset["sha256"])],
            (int(asset["width"]), int(asset["height"])),
        )
    return {
        "security": wire["security"],
        "worker": {
            "pid": wire["pid"],
            "memory_limit_bytes": MAX_WORKER_MEMORY_BYTES,
            "timeout_seconds": WORKER_TIMEOUT_SECONDS,
            "transport": "bounded_spawn_pipe",
        },
        "candidates": candidates,
        "assets": assets,
    }


def _validate_output_dir(output_dir: Path, workspace_root: Path) -> Path:
    workspace = Path(workspace_root).resolve()
    allowed_root = workspace.joinpath(*_ALLOWED_OUTPUT_PARTS).resolve()
    output = Path(output_dir).resolve()
    try:
        output.relative_to(allowed_root)
    except ValueError:
        _fail("OUTPUT_SCOPE")
    if output == allowed_root:
        _fail("OUTPUT_SCOPE")
    if output.exists():
        _fail("OUTPUT_EXISTS")
    return output


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_contact_sheets(output: Path, rows: Sequence[Mapping[str, object]]) -> list[dict]:
    sheet_dir = output / "contact-sheets"
    sheet_dir.mkdir(parents=True, exist_ok=False)
    font = ImageFont.load_default()
    index = []
    for sheet_number, start in enumerate(range(0, len(rows), 15), 1):
        chunk = rows[start : start + 15]
        canvas = Image.new("RGB", (1600, 1080), "white")
        draw = ImageDraw.Draw(canvas)
        sheet_rows = []
        for position, row in enumerate(chunk):
            column = position % 5
            line = position // 5
            left = column * 320
            top = line * 360
            asset = row["asset"]
            image_path = output / str(asset["path"])
            with Image.open(_io_path(image_path)) as source:
                preview = source.convert("RGBA")
                preview.thumbnail((286, 270), Image.Resampling.LANCZOS)
                background = Image.new("RGBA", preview.size, "white")
                background.alpha_composite(preview)
                x = left + (320 - preview.width) // 2
                y = top + 8 + (270 - preview.height) // 2
                canvas.paste(background.convert("RGB"), (x, y))
            draw.rectangle((left, top, left + 319, top + 359), outline="#b7c5d1", width=1)
            label = (
                f"{row['source_code']}  p.{row['page_number']}  X{row['xref']}\n"
                f"{asset['width']}x{asset['height']}  {str(asset['sha256'])[:12]}\n"
                f"512+: {'PASS' if asset['source_shortest_side_512_plus'] else 'FAIL'}  "
                "APROBADO: NO"
            )
            draw.multiline_text((left + 10, top + 287), label, fill="#10283c", font=font, spacing=4)
            sheet_rows.append(
                {
                    "internal_id": row["internal_id"],
                    "source_code": row["source_code"],
                    "position": position,
                }
            )
        name = f"cylex-candidates-{sheet_number:02d}.png"
        canvas.save(sheet_dir / name, format="PNG", optimize=False, compress_level=6)
        index.append({"path": f"contact-sheets/{name}", "rows": sheet_rows})
    return index


def _artifact_hashes(output: Path) -> dict:
    files = {}
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        if path.name == "artifact-hashes.json":
            continue
        data = _io_path(path).read_bytes()
        files[path.relative_to(output).as_posix()] = {
            "bytes": len(data),
            "sha256": _sha256(data),
        }
    return {"schema_version": 1, "files": files}


def write_recovery_output(
    payload: Mapping[str, object],
    output_dir: Path,
    *,
    workspace_root: Path,
) -> dict:
    output = _validate_output_dir(output_dir, workspace_root)
    rows = payload.get("candidates")
    assets = payload.get("assets")
    if not isinstance(rows, list) or len(rows) != 75 or not isinstance(assets, Mapping):
        _fail("OUTPUT_PAYLOAD")
    output.mkdir(parents=True, exist_ok=False)
    candidates_dir = output / "candidates"
    candidates_dir.mkdir(exist_ok=False)
    for digest, data in sorted(assets.items()):
        if not isinstance(data, bytes) or _sha256(data) != digest:
            _fail("OUTPUT_ASSET_HASH")
        _io_path(candidates_dir / f"{digest}.png").write_bytes(data)
    sorted_rows = sorted(rows, key=lambda row: str(row["internal_id"]))
    (output / "candidates.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in sorted_rows
        ),
        encoding="utf-8",
    )
    with (output / "candidates.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = [
            "internal_id",
            "source_code",
            "page_number",
            "xref",
            "bbox",
            "asset_sha256",
            "width",
            "height",
            "source_shortest_side_512_plus",
            "approved",
            "next_action",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted_rows:
            asset = row["asset"]
            writer.writerow(
                {
                    "internal_id": row["internal_id"],
                    "source_code": row["source_code"],
                    "page_number": row["page_number"],
                    "xref": row["xref"],
                    "bbox": json.dumps(row["bbox"], separators=(",", ":")),
                    "asset_sha256": asset["sha256"],
                    "width": asset["width"],
                    "height": asset["height"],
                    "source_shortest_side_512_plus": asset["source_shortest_side_512_plus"],
                    "approved": False,
                    "next_action": row["next_action"],
                }
            )
    receipts = [
        {
            "schema_version": 1,
            "internal_id": row["internal_id"],
            "source_code": row["source_code"],
            "status": "candidate_extracted_unapproved",
            "document_sha256": CYLEX_SOURCE_SHA256,
            "page_number": row["page_number"],
            "xref": row["xref"],
            "bbox": row["bbox"],
            "asset_sha256": row["asset"]["sha256"],
        }
        for row in sorted_rows
    ]
    (output / "receipts.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in receipts),
        encoding="utf-8",
    )
    security = {
        "schema_version": 1,
        **dict(payload["security"]),
        "worker": dict(payload["worker"]),
        "production_mutations": 0,
        "store_mutations": 0,
        "worker_importer_mutations": 0,
    }
    _write_json(output / "security-report.json", security)
    _write_json(
        output / "decisions.json",
        {
            "schema_version": 1,
            "approved": [],
            "pending_manual_review": [row["internal_id"] for row in sorted_rows],
        },
    )
    _write_json(output / "blocked.json", {"schema_version": 1, "rows": []})
    contact_index = _render_contact_sheets(output, sorted_rows)
    _write_json(output / "contact-sheet-index.json", contact_index)
    logical_sha256 = _sha256(
        json.dumps(
            {
                "candidates": sorted_rows,
                "asset_sha256": sorted(assets),
                "security": payload["security"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    summary = {
        "schema_version": 1,
        "status": "passed_unapproved_candidates",
        "counts": {
            "approved": 0,
            "blocked_during_recovery": 0,
            "candidate_rows": 75,
            "contact_sheets": len(contact_index),
            "unique_assets": len(assets),
        },
        "bytes": {
            "assets_total": sum(len(data) for data in assets.values()),
            "assets_max": max(len(data) for data in assets.values()),
        },
        "technical_resolution": {
            "source_shortest_side_512_plus": sum(
                bool(row["asset"]["source_shortest_side_512_plus"]) for row in sorted_rows
            ),
            "source_shortest_side_below_512": sum(
                not bool(row["asset"]["source_shortest_side_512_plus"]) for row in sorted_rows
            ),
        },
        "logical_recovery_sha256": logical_sha256,
        "generation_calls": 0,
        "promotion_calls": 0,
        "production_mutations": 0,
    }
    _write_json(output / "summary.json", summary)
    _write_json(output / "artifact-hashes.json", _artifact_hashes(output))
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--normalized-research", type=Path, required=True)
    parser.add_argument("--normalized-sha256", default=CANONICAL_NORMALIZED_RESEARCH_SHA256)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows = _load_bbox_rows(args.normalized_research, expected_sha256=args.normalized_sha256)
    source = _read_exact_source(args.source)
    payload = recover_in_subprocess(source, rows)
    summary = write_recovery_output(payload, args.output, workspace_root=args.workspace_root)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
