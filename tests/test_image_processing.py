from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

import mobiliti_saas.quote_engine.image_processing as image_processing
from mobiliti_saas.quote_engine.image_processing import (
    MAX_MONTAGE_BYTES,
    MAX_MONTAGE_IMAGES,
    compose_product_montage,
)


def solid_png(
    color: tuple[int, int, int],
    size: tuple[int, int] = (64, 64),
) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def test_product_montage_keeps_main_dominant_and_orders_thumbnails() -> None:
    main = solid_png((255, 0, 0), (800, 800))
    blue = solid_png((0, 0, 255), (300, 300))
    green = solid_png((0, 255, 0), (300, 300))

    payload = compose_product_montage(main, [blue, green])

    assert payload is not None
    with Image.open(BytesIO(payload)) as image:
        result = image.convert("RGB")
        assert result.size == (1200, 900)
        assert result.getpixel((350, 450))[0] > 200
        assert result.getpixel((970, 250))[2] > 200
        assert result.getpixel((970, 650))[1] > 200
    assert len(payload) <= MAX_MONTAGE_BYTES


def test_product_montage_returns_none_when_no_image_exists() -> None:
    assert compose_product_montage(None, []) is None


def test_product_montage_is_deterministic_and_never_upscales() -> None:
    source = solid_png((180, 20, 40), (20, 10))

    first = compose_product_montage(source, [])
    second = compose_product_montage(source, [])

    assert first == second
    assert first is not None
    with Image.open(BytesIO(first)) as image:
        result = image.convert("RGB")
        assert result.getpixel((600, 450)) == (180, 20, 40)
        assert result.getpixel((589, 450)) == (255, 255, 255)
        assert result.getpixel((610, 450)) == (255, 255, 255)


def test_product_montage_rejects_more_than_nine_images() -> None:
    source = solid_png((10, 20, 30))

    with pytest.raises(ValueError, match="m[aá]ximo de im[aá]genes"):
        compose_product_montage(source, [source] * MAX_MONTAGE_IMAGES)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "Tama"),
        (b"not-an-image", "Formato"),
    ],
)
def test_product_montage_rejects_empty_or_unsupported_sources(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        compose_product_montage(payload, [])


def test_product_montage_rejects_oversized_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = solid_png((10, 20, 30))
    monkeypatch.setattr(
        image_processing,
        "MAX_MONTAGE_SOURCE_BYTES",
        len(source) - 1,
    )

    with pytest.raises(ValueError, match="Tama"):
        compose_product_montage(source, [])


def test_product_montage_rejects_animated_png() -> None:
    output = BytesIO()
    Image.new("RGB", (20, 20), (255, 0, 0)).save(
        output,
        "PNG",
        save_all=True,
        append_images=[Image.new("RGB", (20, 20), (0, 0, 255))],
        duration=100,
    )

    with pytest.raises(ValueError, match="animada"):
        compose_product_montage(output.getvalue(), [])


def test_product_montage_rejects_decompression_bomb_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = solid_png((10, 20, 30), (11, 10))
    monkeypatch.setattr(image_processing, "MAX_MONTAGE_IMAGE_PIXELS", 100)

    with pytest.raises(ValueError, match="Dimensiones"):
        compose_product_montage(source, [])
