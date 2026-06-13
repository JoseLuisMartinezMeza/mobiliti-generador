from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid


DEFAULT_DEZGO_ENDPOINT = "https://api.dezgo.com/remove-background"
DEFAULT_DEZGO_MODEL = "flux_2"
DEFAULT_DEZGO_PROMPT = (
    "clean professional furniture product photo, preserve the original object, "
    "sharp details, pure white studio background"
)


class ImageProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class DezgoImageProviderConfig:
    api_key: str | None = None
    endpoint: str = DEFAULT_DEZGO_ENDPOINT
    model: str = DEFAULT_DEZGO_MODEL
    prompt: str = DEFAULT_DEZGO_PROMPT
    mode: str = "transparent"
    output_format: str = "png"
    strength: float = 0.35
    timeout_seconds: int = 120


def normalize_image_provider(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "pillow", "local", "gratis", "free"}:
        return "pillow"
    if text in {"dezgo", "flux", "flux2", "flux_2", "ia", "ai"}:
        return "dezgo"
    return "pillow"


def dezgo_config_from_env() -> DezgoImageProviderConfig:
    return DezgoImageProviderConfig(
        api_key=os.environ.get("DEZGO_API_KEY"),
        endpoint=os.environ.get("DEZGO_ENDPOINT", DEFAULT_DEZGO_ENDPOINT),
        model=os.environ.get("DEZGO_MODEL", DEFAULT_DEZGO_MODEL),
        prompt=os.environ.get("DEZGO_PROMPT", DEFAULT_DEZGO_PROMPT),
        mode=os.environ.get("DEZGO_REMOVE_BACKGROUND_MODE", "transparent"),
        output_format=os.environ.get("DEZGO_OUTPUT_FORMAT", "png"),
        strength=_float_env("DEZGO_IMAGE_STRENGTH", 0.35),
        timeout_seconds=int(_float_env("DEZGO_TIMEOUT_SECONDS", 120)),
    )


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
            output.write_bytes(response.read())
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
    if config.model:
        fields["model"] = config.model
    if "image2image" in path:
        fields["strength"] = str(config.strength)
    return fields


def _multipart_body(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
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
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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
