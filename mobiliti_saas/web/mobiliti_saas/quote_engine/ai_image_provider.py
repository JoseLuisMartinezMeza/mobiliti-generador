from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


DEFAULT_DEZGO_ENDPOINT = "https://api.dezgo.com/image2image_flux_2_pro"
DEFAULT_DEZGO_TEXT_ENDPOINT = "https://api.dezgo.com/text2image_flux_2_pro"
DEFAULT_DEZGO_MODEL = "flux_2_pro"
LEGACY_DEZGO_SD_MODEL = "realistic_vision_5_1"
DEFAULT_DEZGO_PROMPT = (
    "photorealistic premium office furniture product image, preserve the exact original product shape "
    "and identity, geometry, materials, wood grain, metal legs, color and proportions, centered full "
    "product visible, isolated on a clean pure white or transparent studio background, soft natural "
    "catalog shadow only, crisp edges, high resolution, sharp commercial catalog quality, remove dirty "
    "gray background artifacts, no text, no logos, no people"
)
DEFAULT_DEZGO_NEGATIVE_PROMPT = (
    "distorted geometry, changed product design, extra furniture, people, hands, text, watermark, "
    "logo, cropped product, blurry, low resolution, cartoon, illustration, oversaturated colors"
)


class ImageProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class DezgoImageProviderConfig:
    api_key: str | None = None
    endpoint: str = DEFAULT_DEZGO_ENDPOINT
    text_endpoint: str = DEFAULT_DEZGO_TEXT_ENDPOINT
    model: str = DEFAULT_DEZGO_MODEL
    prompt: str = DEFAULT_DEZGO_PROMPT
    negative_prompt: str = DEFAULT_DEZGO_NEGATIVE_PROMPT
    mode: str = "transparent"
    output_format: str = "png"
    strength: float = 0.58
    timeout_seconds: int = 120
    text_width: int = 1024
    text_height: int = 1024
    text_steps: int = 8


def normalize_image_provider(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "pillow", "local", "gratis", "free"}:
        return "pillow"
    if text in {"dezgo", "flux", "flux2", "flux_2", "ia", "ai"}:
        return "dezgo"
    if text in {"sunon_catalog", "sunon-catalog", "catalogo_sunon", "sunon_precise", "sunon-precise"}:
        return "sunon_catalog"
    if text in {"sunon", "sunon_web", "sunon-web", "web_sunon"}:
        return "sunon_web"
    return "pillow"


def dezgo_config_from_env() -> DezgoImageProviderConfig:
    return DezgoImageProviderConfig(
        api_key=os.environ.get("DEZGO_API_KEY"),
        endpoint=_retouch_endpoint_from_env(),
        text_endpoint=os.environ.get("DEZGO_TEXT_ENDPOINT", DEFAULT_DEZGO_TEXT_ENDPOINT),
        model=os.environ.get("DEZGO_MODEL", DEFAULT_DEZGO_MODEL),
        prompt=os.environ.get("DEZGO_PROMPT", DEFAULT_DEZGO_PROMPT),
        negative_prompt=os.environ.get("DEZGO_NEGATIVE_PROMPT", DEFAULT_DEZGO_NEGATIVE_PROMPT),
        mode=os.environ.get("DEZGO_REMOVE_BACKGROUND_MODE", "transparent"),
        output_format=os.environ.get("DEZGO_OUTPUT_FORMAT", "png"),
        strength=_float_env("DEZGO_IMAGE_STRENGTH", 0.58),
        timeout_seconds=int(_float_env("DEZGO_TIMEOUT_SECONDS", 120)),
        text_width=int(_float_env("DEZGO_TEXT_WIDTH", 1024)),
        text_height=int(_float_env("DEZGO_TEXT_HEIGHT", 1024)),
        text_steps=int(_float_env("DEZGO_TEXT_STEPS", 8)),
    )


def _retouch_endpoint_from_env() -> str:
    endpoint = os.environ.get("DEZGO_IMAGE2IMAGE_ENDPOINT") or os.environ.get("DEZGO_ENDPOINT", DEFAULT_DEZGO_ENDPOINT)
    if _is_non_retouch_endpoint(endpoint) and not _truthy_env("DEZGO_ALLOW_NON_RETOUCH_ENDPOINT"):
        return DEFAULT_DEZGO_ENDPOINT
    if _is_legacy_image_endpoint(endpoint) and not _truthy_env("DEZGO_ALLOW_LEGACY_IMAGE_ENDPOINT"):
        return DEFAULT_DEZGO_ENDPOINT
    return endpoint


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_non_retouch_endpoint(endpoint: str | None) -> bool:
    path = urllib.parse.urlparse(str(endpoint or "")).path.lower()
    return "remove-background" in path or "upscale" in path


def _is_legacy_image_endpoint(endpoint: str | None) -> bool:
    path = urllib.parse.urlparse(str(endpoint or "")).path.lower().rstrip("/")
    return path == "/image2image" or path == "image2image"


def enhance_with_dezgo(
    source_path: str | Path,
    output_path: str | Path,
    config: DezgoImageProviderConfig | None = None,
) -> Path:
    config = config or dezgo_config_from_env()
    if not config.api_key:
        raise ImageProviderError("Falta configurar DEZGO_API_KEY")

    source = Path(source_path)
    output = Path(output_path)
    endpoint = _normalize_endpoint(config.endpoint)
    fields = _fields_for_endpoint(endpoint, config)
    file_field = _file_field_for_endpoint(endpoint)
    body, content_type = _multipart_body(
        fields,
        file_field,
        source.name,
        source.read_bytes(),
        _content_type_for(source),
    )
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "X-Dezgo-Key": config.api_key,
            "Content-Type": content_type,
            "User-Agent": "MobilitiQuoteWorker/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            output.parent.mkdir(parents=True, exist_ok=True)
            _write_dezgo_response_output(response, output, config)
    except urllib.error.HTTPError as exc:
        raise ImageProviderError(_safe_http_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise ImageProviderError(f"No se pudo conectar con Dezgo: {exc.reason}") from exc
    return output


def generate_with_dezgo(
    prompt: str,
    output_path: str | Path,
    config: DezgoImageProviderConfig | None = None,
) -> Path:
    config = config or dezgo_config_from_env()
    if not config.api_key:
        raise ImageProviderError("Falta configurar DEZGO_API_KEY")

    output = Path(output_path)
    fields = _fields_for_text_endpoint(config, prompt)
    body, content_type = _multipart_body(fields, "unused", "unused.txt", b"", "text/plain", include_file=False)
    request = urllib.request.Request(
        _normalize_endpoint(config.text_endpoint),
        data=body,
        method="POST",
        headers={
            "X-Dezgo-Key": config.api_key,
            "Content-Type": content_type,
            "User-Agent": "MobilitiQuoteWorker/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            output.parent.mkdir(parents=True, exist_ok=True)
            _write_dezgo_response_output(response, output, config)
    except urllib.error.HTTPError as exc:
        raise ImageProviderError(_safe_http_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise ImageProviderError(f"No se pudo conectar con Dezgo: {exc.reason}") from exc
    return output


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = str(endpoint or DEFAULT_DEZGO_ENDPOINT).strip()
    if endpoint.startswith("/"):
        return f"https://api.dezgo.com{endpoint}"
    return endpoint


def _file_field_for_endpoint(endpoint: str) -> str:
    path = urllib.parse.urlparse(endpoint).path.lower()
    if "remove-background" in path or "upscale" in path:
        return "image"
    return "init_image"


def _fields_for_endpoint(endpoint: str, config: DezgoImageProviderConfig) -> dict[str, str]:
    path = urllib.parse.urlparse(endpoint).path.lower()
    if "remove-background" in path:
        return {"mode": config.mode}
    if "upscale" in path:
        return {"format": config.output_format}

    fields = {
        "prompt": config.prompt,
        "format": config.output_format,
    }
    if config.negative_prompt:
        fields["negative_prompt"] = config.negative_prompt
    model = _model_for_endpoint(path, config.model)
    if model:
        fields["model"] = model
    if "image2image" in path and "flux_2" not in path:
        fields["strength"] = str(config.strength)
    return fields


def _fields_for_text_endpoint(config: DezgoImageProviderConfig, prompt: str) -> dict[str, str]:
    endpoint = _normalize_endpoint(config.text_endpoint)
    path = urllib.parse.urlparse(endpoint).path.lower()
    fields = {
        "prompt": _limit_prompt(prompt),
        "format": config.output_format,
    }
    if "text2image_flux" in path:
        fields.update(
            {
                "width": str(_clamp_multiple(config.text_width, 512, 1536)),
                "height": str(_clamp_multiple(config.text_height, 512, 1536)),
                "steps": str(max(2, min(20, int(config.text_steps or 8)))),
                "transparent_background": "false",
            }
        )
    else:
        fields.update(
            {
                "width": str(_clamp_multiple(config.text_width, 320, 1024)),
                "height": str(_clamp_multiple(config.text_height, 320, 1024)),
                "negative_prompt": config.negative_prompt,
            }
        )
        if config.model:
            fields["model"] = config.model
    return fields


def _model_for_endpoint(path: str, model: str) -> str:
    text = str(model or "").strip()
    if "flux_2" in path:
        return ""
    if "image2image" in path and text.startswith("flux_"):
        return LEGACY_DEZGO_SD_MODEL
    return text


def _write_dezgo_response_output(response, output: Path, config: DezgoImageProviderConfig) -> None:
    body = response.read()
    content_type = str(response.headers.get("content-type", "")).lower()
    if _is_image_response(body, content_type):
        output.write_bytes(body)
        return

    payload = _json_payload(body)
    if payload is None:
        raise ImageProviderError("Dezgo no devolvio una imagen valida")

    tx = payload.get("tx") if isinstance(payload, dict) else None
    tx_id = tx.get("_id") if isinstance(tx, dict) else None
    if not tx_id:
        raise ImageProviderError("Dezgo no devolvio una imagen ni un job valido")

    output.write_bytes(_wait_for_dezgo_job_output(str(tx_id), config))


def _is_image_response(body: bytes, content_type: str) -> bool:
    if content_type.startswith("image/"):
        return True
    return body.startswith(b"\x89PNG\r\n\x1a\n") or body.startswith(b"\xff\xd8\xff") or body.startswith(b"RIFF")


def _json_payload(body: bytes) -> dict | None:
    if body[:1] not in {b"{", b"["}:
        return None
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _wait_for_dezgo_job_output(tx_id: str, config: DezgoImageProviderConfig) -> bytes:
    deadline = time.monotonic() + max(30, int(config.timeout_seconds or 120))
    last_status = ""
    while time.monotonic() < deadline:
        job = _fetch_dezgo_job(tx_id, config)
        last_status = str(job.get("status") or "")
        files = job.get("files") if isinstance(job, dict) else None
        output_file = files.get("output") if isinstance(files, dict) else None
        if isinstance(output_file, dict) and output_file.get("url"):
            return _download_dezgo_output(str(output_file["url"]), config)
        if job.get("final"):
            break
        time.sleep(1.5)

    suffix = f" ({last_status})" if last_status else ""
    raise ImageProviderError(f"Dezgo no termino el job de imagen a tiempo{suffix}")


def _fetch_dezgo_job(tx_id: str, config: DezgoImageProviderConfig) -> dict:
    job_url = f"https://api.dezgo.com/unstable/job/{urllib.parse.quote(tx_id, safe='')}"
    request = urllib.request.Request(
        job_url,
        headers={
            "X-Dezgo-Key": config.api_key or "",
            "Accept": "application/json",
            "User-Agent": "MobilitiQuoteWorker/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ImageProviderError("Dezgo devolvio un job invalido")
    return payload


def _download_dezgo_output(url: str, config: DezgoImageProviderConfig) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MobilitiQuoteWorker/1.0"})
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        body = response.read()
        content_type = str(response.headers.get("content-type", "")).lower()
    if not _is_image_response(body, content_type):
        raise ImageProviderError("Dezgo devolvio una salida que no es imagen")
    return body


def _multipart_body(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
    include_file: bool = True,
) -> tuple[bytes, str]:
    boundary = f"----MobilitiDezgo{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    if include_file:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                file_bytes,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _limit_prompt(prompt: str, limit: int = 1000) -> str:
    clean = " ".join(str(prompt or "").split())
    return clean[:limit].rstrip()


def _clamp_multiple(value: int, min_value: int, max_value: int) -> int:
    value = max(min_value, min(max_value, int(value or min_value)))
    return max(min_value, value - (value % 8))


def _content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _safe_http_error_message(exc: urllib.error.HTTPError) -> str:
    detail = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        detail = payload.get("message") or payload.get("detail") or payload.get("title") or ""
    except Exception:
        detail = ""
    suffix = f": {detail}" if detail else ""
    return f"Dezgo HTTP {exc.code} {exc.reason}{suffix}"
