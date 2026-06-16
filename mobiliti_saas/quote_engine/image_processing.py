from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Any
from pathlib import Path
import hashlib
import math
import os

from PIL import Image, ImageChops, ImageFilter

from .ai_image_provider import DEFAULT_DEZGO_PROMPT, dezgo_config_from_env, enhance_with_dezgo, normalize_image_provider


class _ProtectionMask:
    def __init__(self, image: Image.Image | None = None) -> None:
        self.image = image
        self.pixels = image.load() if image is not None and image.getbbox() else None

    def __contains__(self, point: tuple[int, int]) -> bool:
        if self.pixels is None:
            return False
        x, y = point
        return self.pixels[x, y] > 0


@dataclass(frozen=True)
class ImageProcessingOptions:
    background: str = "transparent"
    min_size: int = 900
    cleanup_strength: str = "normal"
    tolerance: int = 32
    min_brightness: int = 185
    neutral_delta: int = 18
    white_threshold: int = 238
    fringe_brightness: int = 220
    floor_fringe_brightness: int = 220


MAX_WORKING_IMAGE_EDGE = 1800


def improve_product_image(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    background: str = "transparent",
    min_size: int = 900,
    cleanup_strength: str = "normal",
    image_provider: str | None = None,
    image_prompt: str | None = None,
    stats: dict[str, Any] | None = None,
) -> Path:
    source = Path(source_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    options = _build_options(background, min_size, cleanup_strength)
    provider = normalize_image_provider(image_provider or os.environ.get("IMAGE_PROVIDER"))
    provider_signature = _provider_signature(provider, image_prompt)
    output = output_root / f"{_cache_key(source, options, provider_signature)}.png"
    if output.exists():
        if provider == "dezgo":
            _bump_stat(stats, "image_ai_cached_count")
        return output

    if provider == "dezgo":
        _bump_stat(stats, "image_ai_attempted_count")
        try:
            config = dezgo_config_from_env()
            if str(image_prompt or "").strip():
                config = replace(
                    config,
                    prompt=_compose_dezgo_retouch_prompt(str(image_prompt).strip(), config.prompt),
                )
            enhance_with_dezgo(source, output, config)
            _normalize_provider_output(output, options)
            _bump_stat(stats, "image_ai_enhanced_count")
            return output
        except Exception:
            _bump_stat(stats, "image_ai_failed_count")
            if _strict_dezgo_requested(image_provider):
                raise

    with Image.open(source) as img:
        processed = _process_image(img, options)
        if options.background == "white":
            processed = _flatten_to_white(processed)
        processed.save(output, "PNG", optimize=True)
    return output


def improve_image_map(
    image_map: dict[int, str],
    temp_dir: str | Path,
    *,
    background: str = "transparent",
    min_size: int = 900,
    cleanup_strength: str = "normal",
    image_provider: str | None = None,
    image_prompt: str | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[int, str]:
    output_dir = Path(temp_dir) / "mejoradas"
    if stats is not None:
        stats["image_provider"] = normalize_image_provider(image_provider or os.environ.get("IMAGE_PROVIDER"))
        stats["image_source_count"] = len(image_map)
    improved: dict[int, str] = {}
    for row, image_path in image_map.items():
        try:
            improved[row] = str(
                improve_product_image(
                    image_path,
                    output_dir,
                    background=background,
                    min_size=min_size,
                    cleanup_strength=cleanup_strength,
                    image_provider=image_provider,
                    image_prompt=image_prompt,
                    stats=stats,
                )
            )
        except Exception:
            if _strict_dezgo_requested(image_provider):
                raise
            improved[row] = image_path
    return improved


def _normalize_provider_output(output: Path, options: ImageProcessingOptions) -> None:
    with Image.open(output) as img:
        processed = img.convert("RGBA")
        if processed.getchannel("A").getbbox():
            processed = _trim_transparent_edges(processed)
        processed = _trim_outer_background_padding(processed)
        processed = _upscale_if_needed(processed, options.min_size)
        if options.background == "white":
            processed = _flatten_to_white(processed)
        processed.save(output, "PNG", optimize=True)


def _compose_dezgo_retouch_prompt(user_prompt: str, base_prompt: str) -> str:
    user = " ".join(str(user_prompt or "").split())
    base = " ".join(str(base_prompt or DEFAULT_DEZGO_PROMPT).split())
    if not user:
        return base[:1000].rstrip()
    if user.lower() in base.lower():
        return base[:1000].rstrip()
    return f"{user}. {base}"[:1000].rstrip()


def _bump_stat(stats: dict[str, Any] | None, key: str, amount: int = 1) -> None:
    if stats is not None:
        stats[key] = int(stats.get(key, 0) or 0) + amount


def _process_image(img: Image.Image, options: ImageProcessingOptions) -> Image.Image:
    rgba = img.convert("RGBA")
    rgba = _downscale_if_needed(rgba, MAX_WORKING_IMAGE_EDGE)
    if _should_use_light_product_safe(rgba, options):
        return _process_light_product_safe(rgba, options)

    rgba = _remove_light_edge_background(rgba, options)
    rgba = _trim_transparent_edges(rgba)
    rgba = _upscale_if_needed(rgba, options.min_size)
    rgba = _sharpen_rgb_preserve_alpha(rgba)
    rgba = _cleanup_edge_fringe(rgba, options)
    rgba = _upscale_if_needed(rgba, math.ceil(options.min_size * 1.04))
    rgba = _drop_faint_residue(rgba, options)
    return _trim_outer_background_padding(rgba)


def _should_use_light_product_safe(img: Image.Image, options: ImageProcessingOptions) -> bool:
    if options.cleanup_strength != "balanced":
        return False

    rgba = img.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    margin_x = max(1, int(width * 0.06))
    y_start = max(0, int(height * 0.03))
    y_end = min(height, int(height * 0.97))
    total = 0
    light_neutral = 0
    dark = 0

    for y in range(y_start, y_end):
        for x in range(margin_x, width - margin_x):
            r, g, b, a = pixels[x, y]
            if a <= 8:
                continue
            total += 1
            brightness = (r + g + b) / 3
            neutral = max(r, g, b) - min(r, g, b) <= 35
            if neutral and 135 <= brightness <= 245:
                light_neutral += 1
            if brightness < 95:
                dark += 1

    if total == 0:
        return False
    return (light_neutral / total) >= 0.18 and (dark / total) <= 0.08


def _process_light_product_safe(img: Image.Image, options: ImageProcessingOptions) -> Image.Image:
    rgba = _matte_light_background_to_white(img)
    rgba = _trim_outer_background_padding(rgba)
    rgba = _upscale_if_needed(rgba, options.min_size)
    return _sharpen_rgb_preserve_alpha(rgba)


def _matte_light_background_to_white(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    rgb = _flatten_to_white(rgba).convert("RGBA")
    pixels = rgb.load()
    width, height = rgb.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            brightness = (r + g + b) / 3
            neutral = max(r, g, b) - min(r, g, b) <= 18
            if neutral and brightness >= 246:
                pixels[x, y] = (255, 255, 255, 255)
            else:
                pixels[x, y] = (r, g, b, 255)
    return rgb


def _remove_light_edge_background(img: Image.Image, options: ImageProcessingOptions) -> Image.Image:
    pixels = img.load()
    width, height = img.size
    bg = _estimate_background_color(img)
    protected = _foreground_protection_mask(img, options)
    visited: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited:
            continue
        visited.add((x, y))
        if (x, y) in protected:
            continue
        r, g, b, a = pixels[x, y]
        if a == 0 or _is_background_pixel((r, g, b), bg, options):
            pixels[x, y] = (255, 255, 255, 0)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if (
                    0 <= nx < width
                    and 0 <= ny < height
                    and (nx, ny) not in visited
                    and (nx, ny) not in protected
                ):
                    queue.append((nx, ny))
    return img


def _foreground_protection_mask(img: Image.Image, options: ImageProcessingOptions) -> _ProtectionMask:
    if options.cleanup_strength != "balanced":
        return _ProtectionMask()

    gray = img.convert("L")
    width, height = gray.size
    pixels = gray.load()
    dark_mask = Image.new("L", gray.size, 0)
    light_mask = Image.new("L", gray.size, 0)
    dark_pixels = dark_mask.load()
    light_pixels = light_mask.load()
    light_product = _has_light_product_surface(pixels, width, height)
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            value = pixels[x, y]
            if value < 95:
                dark_pixels[x, y] = 255
            elif light_product and y < height * 0.58 and 120 <= value <= 235 and _local_contrast(pixels, x, y) >= 14:
                light_pixels[x, y] = 255

    protected = Image.new("L", gray.size, 0)
    if dark_mask.getbbox():
        protected = ImageChops.lighter(protected, dark_mask.filter(ImageFilter.MaxFilter(3)))
    if light_mask.getbbox():
        protected = ImageChops.lighter(protected, light_mask.filter(ImageFilter.MaxFilter(37)))
    return _ProtectionMask(protected)


def _local_contrast(pixels, x: int, y: int) -> int:
    values = [
        pixels[x, y],
        pixels[x - 1, y],
        pixels[x + 1, y],
        pixels[x, y - 1],
        pixels[x, y + 1],
    ]
    return max(values) - min(values)


def _has_light_product_surface(pixels, width: int, height: int) -> bool:
    hits = 0
    y_limit = max(2, int(height * 0.48))
    margin_x = max(2, int(width * 0.08))
    for y in range(1, y_limit):
        for x in range(margin_x, width - margin_x):
            value = pixels[x, y]
            if 135 <= value <= 238 and _local_contrast(pixels, x, y) >= 14:
                hits += 1
    return hits >= max(24, int(width * height * 0.008))


def _estimate_background_color(img: Image.Image) -> tuple[int, int, int]:
    width, height = img.size
    samples = []
    sample_points = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    ]
    for x, y in sample_points:
        r, g, b, _ = img.getpixel((x, y))
        samples.append((r, g, b))
    return tuple(sorted(channel)[len(channel) // 2] for channel in zip(*samples))


def _is_background_pixel(
    color: tuple[int, int, int],
    background: tuple[int, int, int],
    options: ImageProcessingOptions,
) -> bool:
    brightness = sum(color) / 3
    neutral = max(color) - min(color) <= options.neutral_delta
    if min(color) >= options.white_threshold:
        return True
    if brightness < options.min_brightness:
        return False
    distance = math.sqrt(sum((color[index] - background[index]) ** 2 for index in range(3)))
    if distance <= options.tolerance:
        return True
    return options.cleanup_strength == "aggressive" and neutral


def _trim_transparent_edges(img: Image.Image) -> Image.Image:
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return img
    return img.crop(bbox)


def _trim_outer_background_padding(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    visible = alpha.point(lambda value: 255 if value > 8 else 0)
    if not visible.getbbox():
        return rgba

    background = _estimate_visible_background_color(rgba)
    diff = ImageChops.difference(rgba.convert("RGB"), Image.new("RGB", rgba.size, background)).convert("L")
    gray = rgba.convert("L")
    dark = gray.point(lambda value: 255 if value < 150 else 0)

    bbox = None
    min_width = max(12, int(rgba.width * 0.12))
    min_height = max(12, int(rgba.height * 0.12))
    for threshold in (70, 42, 24):
        different = diff.point(lambda value, threshold=threshold: 255 if value > threshold else 0)
        content = ImageChops.lighter(different, dark)
        content = ImageChops.multiply(content, visible)
        candidate = content.getbbox()
        if not candidate:
            continue
        left, top, right, bottom = candidate
        if right - left >= min_width and bottom - top >= min_height:
            bbox = candidate
            break

    if bbox is None:
        bbox = visible.getbbox()
    if not bbox:
        return rgba

    padded = _pad_bbox(bbox, rgba.size, max(8, int(max(rgba.size) * 0.035)))
    left, top, right, bottom = padded
    removes_horizontal = left > 2 or right < rgba.width - 2
    removes_vertical = top > 2 or bottom < rgba.height - 2
    if not (removes_horizontal or removes_vertical):
        return rgba
    return rgba.crop(padded)


def _estimate_visible_background_color(img: Image.Image) -> tuple[int, int, int]:
    rgba = img.convert("RGBA")
    width, height = rgba.size
    samples: list[tuple[int, int, int]] = []
    points = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    ]
    for x, y in points:
        r, g, b, a = rgba.getpixel((x, y))
        if a > 8:
            samples.append((r, g, b))
    if not samples:
        return (255, 255, 255)
    return tuple(sorted(channel)[len(channel) // 2] for channel in zip(*samples))


def _pad_bbox(
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
    margin: int,
) -> tuple[int, int, int, int]:
    width, height = size
    left, top, right, bottom = bbox
    return (
        max(0, left - margin),
        max(0, top - margin),
        min(width, right + margin),
        min(height, bottom + margin),
    )


def _upscale_if_needed(img: Image.Image, min_size: int) -> Image.Image:
    short_edge = min(img.size)
    if short_edge >= min_size:
        return img
    scale = min_size / short_edge
    size = (round(img.width * scale), round(img.height * scale))
    return _resize_rgba_premultiplied(img, size)


def _downscale_if_needed(img: Image.Image, max_edge: int) -> Image.Image:
    long_edge = max(img.size)
    if long_edge <= max_edge:
        return img
    scale = max_edge / long_edge
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return _resize_rgba_premultiplied(img, size)


def _cleanup_edge_fringe(img: Image.Image, options: ImageProcessingOptions) -> Image.Image:
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    protected = _foreground_protection_mask(rgba, options)

    alpha = rgba.getchannel("A")
    transparent = alpha.point(lambda value: 255 if value <= 8 else 0)
    candidate = Image.new("L", rgba.size, 0)
    candidate_pixels = candidate.load()

    for y in range(height):
        for x in range(width):
            if (x, y) in protected:
                continue
            if _is_fringe_pixel(pixels[x, y], y, height, options):
                candidate_pixels[x, y] = 255

    if not candidate.getbbox():
        data = [(0, 0, 0, 0) if a <= 8 else (r, g, b, a) for r, g, b, a in _flat_channel_data(rgba)]
        rgba.putdata(data)
        return _trim_transparent_edges(rgba)

    connected = ImageChops.multiply(candidate, transparent.filter(ImageFilter.MaxFilter(3)))
    max_expansions = {"normal": 10, "balanced": 18, "aggressive": 32}.get(options.cleanup_strength, 12)
    for _ in range(max_expansions):
        expanded = ImageChops.multiply(candidate, connected.filter(ImageFilter.MaxFilter(3)))
        delta = ImageChops.subtract(expanded, connected)
        if not delta.getbbox():
            break
        connected = ImageChops.lighter(connected, expanded)

    removal = _flat_channel_data(connected)
    data = []
    for pixel, remove in zip(_flat_channel_data(rgba), removal):
        r, g, b, a = pixel
        if a <= 8 or remove:
            data.append((0, 0, 0, 0))
        else:
            data.append((r, g, b, a))
    rgba.putdata(data)
    return _trim_transparent_edges(rgba)


def _drop_faint_residue(img: Image.Image, options: ImageProcessingOptions) -> Image.Image:
    if options.cleanup_strength not in {"balanced", "aggressive"}:
        return img

    rgba = img.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            brightness = (r + g + b) / 3
            neutral = max(r, g, b) - min(r, g, b) <= options.neutral_delta
            if a <= 3 or (a <= 128 and neutral and brightness >= 238):
                pixels[x, y] = (0, 0, 0, 0)
    return _trim_transparent_edges(rgba)


def _neighbors(width: int, height: int, x: int, y: int):
    for ny in range(max(0, y - 1), min(height, y + 2)):
        for nx in range(max(0, x - 1), min(width, x + 2)):
            if nx == x and ny == y:
                continue
            yield nx, ny


def _is_fringe_pixel(
    pixel: tuple[int, int, int, int],
    y: int,
    height: int,
    options: ImageProcessingOptions,
) -> bool:
    r, g, b, a = pixel
    if a <= 8:
        return False
    brightness = (r + g + b) / 3
    neutral = max(r, g, b) - min(r, g, b) <= options.neutral_delta
    if options.cleanup_strength == "balanced":
        if y >= height * 0.58:
            return neutral and brightness >= options.floor_fringe_brightness
        return neutral and brightness >= options.fringe_brightness
    threshold = options.fringe_brightness
    if options.cleanup_strength == "aggressive" and y >= height * 0.58:
        threshold = min(threshold, options.floor_fringe_brightness)
    return neutral and brightness >= threshold


def _sharpen_rgb_preserve_alpha(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    rgb = rgba.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=3))
    r, g, b = rgb.split()
    return Image.merge("RGBA", (r, g, b, rgba.getchannel("A")))


def _resize_rgba_premultiplied(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return img.convert("RGBa").resize(size, Image.Resampling.LANCZOS).convert("RGBA")


def _flat_channel_data(channel: Image.Image):
    if hasattr(channel, "get_flattened_data"):
        return channel.get_flattened_data()
    return channel.getdata()


def _flatten_to_white(img: Image.Image) -> Image.Image:
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, img.convert("RGBA")).convert("RGB")


def _normalize_background(value: str) -> str:
    text = str(value or "transparent").strip().lower()
    if text in {"white", "blanco"}:
        return "white"
    return "transparent"


def _normalize_cleanup_strength(value: str) -> str:
    text = str(value or "normal").strip().lower()
    if text in {"normal", "conservative", "suave"}:
        return "normal"
    if text in {"aggressive", "agresivo"}:
        return "aggressive"
    return "balanced"


def _build_options(background: str, min_size: int, cleanup_strength: str) -> ImageProcessingOptions:
    strength = _normalize_cleanup_strength(cleanup_strength)
    if strength == "normal":
        return ImageProcessingOptions(
            background=_normalize_background(background),
            min_size=max(1, int(min_size or 1)),
            cleanup_strength=strength,
            tolerance=32,
            min_brightness=185,
            neutral_delta=18,
            white_threshold=238,
            fringe_brightness=220,
            floor_fringe_brightness=220,
        )
    if strength == "balanced":
        return ImageProcessingOptions(
            background=_normalize_background(background),
            min_size=max(1, int(min_size or 1)),
            cleanup_strength=strength,
            tolerance=78,
            min_brightness=165,
            neutral_delta=40,
            white_threshold=230,
            fringe_brightness=240,
            floor_fringe_brightness=205,
        )
    return ImageProcessingOptions(
        background=_normalize_background(background),
        min_size=max(1, int(min_size or 1)),
        cleanup_strength=strength,
        tolerance=118,
        min_brightness=145,
        neutral_delta=70,
        white_threshold=222,
        fringe_brightness=145,
        floor_fringe_brightness=70,
    )


def _provider_signature(provider: str, image_prompt: str | None = None) -> str:
    if provider != "dezgo":
        return "pillow"
    config = dezgo_config_from_env()
    prompt = str(image_prompt or "").strip() or config.prompt
    return "|".join(
        [
            "dezgo",
            config.endpoint,
            config.model,
            config.mode,
            config.output_format,
            str(config.strength),
            prompt,
        ]
    )


def _cache_key(source: Path, options: ImageProcessingOptions, provider_signature: str = "pillow") -> str:
    digest = hashlib.sha256()
    digest.update(source.read_bytes())
    digest.update(repr(options).encode("utf-8"))
    digest.update(provider_signature.encode("utf-8"))
    return digest.hexdigest()


def _strict_dezgo_requested(image_provider: str | None) -> bool:
    return normalize_image_provider(image_provider) == "dezgo"
