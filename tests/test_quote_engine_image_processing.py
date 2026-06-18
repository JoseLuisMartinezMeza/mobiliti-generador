from pathlib import Path
import inspect
import sys

import pytest
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.image_processing import improve_image_map, improve_product_image  # noqa: E402


def test_pillow_cleanup_defaults_to_normal_profile():
    assert inspect.signature(improve_product_image).parameters["cleanup_strength"].default == "normal"
    assert inspect.signature(improve_image_map).parameters["cleanup_strength"].default == "normal"


def _sample_product_image(path: Path) -> None:
    img = Image.new("RGB", (80, 60), (232, 232, 232))
    draw = ImageDraw.Draw(img)
    draw.ellipse((24, 14, 56, 50), fill=(35, 35, 35))
    img.save(path, "JPEG")


def test_improve_product_image_outputs_transparent_png_without_gray_border(tmp_path):
    source = tmp_path / "product.jpg"
    _sample_product_image(source)

    output = improve_product_image(source, tmp_path / "out", background="transparent", min_size=160)

    assert output.suffix.lower() == ".png"
    with Image.open(output) as result:
        assert result.mode == "RGBA"
        assert min(result.size) >= 160
        assert result.getpixel((0, 0))[3] == 0
        center = result.getpixel((result.width // 2, result.height // 2))
        assert center[3] == 255
        assert center[:3] == (35, 35, 35)


def test_improve_product_image_can_flatten_to_white_background(tmp_path):
    source = tmp_path / "product.jpg"
    _sample_product_image(source)

    output = improve_product_image(source, tmp_path / "out", background="white", min_size=160)

    with Image.open(output) as result:
        assert result.mode == "RGB"
        assert result.getpixel((0, 0)) == (255, 255, 255)
        assert min(result.size) >= 160


def test_improve_image_map_preserves_rows_and_returns_pngs(tmp_path):
    source = tmp_path / "product.jpg"
    _sample_product_image(source)
    image_map = {9: str(source)}

    result = improve_image_map(image_map, tmp_path, background="transparent", min_size=120)

    assert set(result) == {9}
    assert Path(result[9]).suffix.lower() == ".png"
    assert Path(result[9]).exists()


def test_dezgo_image_provider_raises_without_key_when_explicit(monkeypatch, tmp_path):
    monkeypatch.delenv("DEZGO_API_KEY", raising=False)
    source = tmp_path / "product.jpg"
    _sample_product_image(source)

    with pytest.raises(Exception):
        improve_product_image(
            source,
            tmp_path / "out",
            background="transparent",
            min_size=120,
            image_provider="dezgo",
        )


def test_dezgo_image_provider_uses_separate_cache_from_pillow(monkeypatch, tmp_path):
    monkeypatch.setenv("DEZGO_API_KEY", "fake-key")
    source = tmp_path / "product.jpg"
    _sample_product_image(source)

    def fake_enhance_with_dezgo(_source_path, output_path, _config=None):
        Image.new("RGBA", (24, 24), (255, 0, 0, 255)).save(output_path, "PNG")
        return Path(output_path)

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.image_processing.enhance_with_dezgo",
        fake_enhance_with_dezgo,
    )

    pillow_output = improve_product_image(source, tmp_path / "out", min_size=120)
    dezgo_output = improve_product_image(source, tmp_path / "out", min_size=120, image_provider="dezgo")

    assert pillow_output != dezgo_output
    with Image.open(dezgo_output) as result:
        assert result.getpixel((12, 12)) == (255, 0, 0, 255)


def test_dezgo_image_provider_uses_user_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("DEZGO_API_KEY", "fake-key")
    source = tmp_path / "product.jpg"
    _sample_product_image(source)
    prompts = []

    def fake_enhance_with_dezgo(_source_path, output_path, config=None):
        prompts.append(config.prompt)
        Image.new("RGBA", (24, 24), (255, 0, 0, 255)).save(output_path, "PNG")
        return Path(output_path)

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.image_processing.enhance_with_dezgo",
        fake_enhance_with_dezgo,
    )

    output = improve_product_image(
        source,
        tmp_path / "out",
        min_size=120,
        image_provider="dezgo",
        image_prompt="Mejora la calidad de imagen y que este en fondo blanco",
    )

    assert output.exists()
    assert prompts and prompts[0].startswith("Mejora la calidad de imagen y que este en fondo blanco")
    assert "preserve the exact original product shape" in prompts[0]


def test_dezgo_invalid_image_response_raises_when_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("DEZGO_API_KEY", "fake-key")
    source = tmp_path / "product.jpg"
    _sample_product_image(source)

    def fake_enhance_with_dezgo(_source_path, output_path, _config=None):
        Path(output_path).write_bytes(b"not an image")
        return Path(output_path)

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.image_processing.enhance_with_dezgo",
        fake_enhance_with_dezgo,
    )

    with pytest.raises(Exception):
        improve_product_image(source, tmp_path / "out", min_size=120, image_provider="dezgo")


def test_dezgo_image_map_raises_when_explicit_provider_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("DEZGO_API_KEY", "fake-key")
    source = tmp_path / "product.jpg"
    _sample_product_image(source)

    def fake_enhance_with_dezgo(_source_path, _output_path, _config=None):
        raise RuntimeError("dezgo unavailable")

    monkeypatch.setattr(
        "mobiliti_saas.quote_engine.image_processing.enhance_with_dezgo",
        fake_enhance_with_dezgo,
    )

    with pytest.raises(RuntimeError, match="dezgo unavailable"):
        improve_image_map({9: str(source)}, tmp_path, min_size=120, image_provider="dezgo")


def test_aggressive_cleanup_removes_gray_shadow_but_keeps_enclosed_gray_detail(tmp_path):
    source = tmp_path / "shadowed_product.png"
    img = Image.new("RGB", (120, 90), (238, 238, 238))
    draw = ImageDraw.Draw(img)
    draw.ellipse((-18, 52, 138, 100), fill=(176, 176, 176))
    draw.rounded_rectangle((34, 14, 86, 72), radius=8, fill=(24, 24, 24))
    draw.rounded_rectangle((46, 26, 74, 44), radius=4, fill=(158, 158, 158))
    img.save(source)

    output = improve_product_image(
        source,
        tmp_path / "out",
        background="transparent",
        cleanup_strength="aggressive",
        min_size=1,
    )

    with Image.open(output) as result:
        assert result.mode == "RGBA"
        assert result.width <= 60
        assert result.height <= 65
        assert result.getpixel((2, result.height - 2))[3] == 0
        assert result.getpixel((result.width - 3, result.height - 2))[3] == 0
        enclosed_gray = result.getpixel((result.width // 2, result.height // 3))
        assert enclosed_gray[3] == 255
        assert enclosed_gray[:3] == (158, 158, 158)


def test_balanced_cleanup_preserves_more_product_texture_than_aggressive(tmp_path):
    source = tmp_path / "textured_product.png"
    img = Image.new("RGB", (120, 90), (238, 238, 238))
    draw = ImageDraw.Draw(img)
    draw.ellipse((-18, 52, 138, 100), fill=(176, 176, 176))
    draw.rounded_rectangle((34, 14, 86, 72), radius=8, fill=(24, 24, 24))
    for x in range(38, 84, 4):
        draw.line((x, 18, x + 12, 68), fill=(120, 120, 120), width=1)
    img.save(source)

    balanced = improve_product_image(
        source,
        tmp_path / "balanced",
        background="transparent",
        cleanup_strength="balanced",
        min_size=1,
    )
    aggressive = improve_product_image(
        source,
        tmp_path / "aggressive",
        background="transparent",
        cleanup_strength="aggressive",
        min_size=1,
    )

    def visible_mid_gray_count(path: Path) -> int:
        with Image.open(path) as result:
            rgba = result.convert("RGBA")
            pixels = rgba.get_flattened_data() if hasattr(rgba, "get_flattened_data") else rgba.getdata()
            return sum(
                1
                for r, g, b, a in pixels
                if a > 0 and 95 <= r <= 150 and 95 <= g <= 150 and 95 <= b <= 150
            )

    assert visible_mid_gray_count(balanced) > visible_mid_gray_count(aggressive)


def test_balanced_cleanup_preserves_textured_white_and_gray_product_surfaces(tmp_path):
    source = tmp_path / "white_gray_product.png"
    img = Image.new("RGB", (140, 100), (244, 244, 244))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 8, 116, 54), radius=12, fill=(218, 218, 212))
    draw.rectangle((18, 42, 122, 70), fill=(232, 232, 226))
    for x in range(24, 116, 5):
        draw.line((x, 12, x + 12, 66), fill=(198, 198, 192), width=1)
    draw.line((24, 70, 116, 70), fill=(45, 45, 45), width=3)
    draw.line((34, 70, 18, 96), fill=(70, 70, 70), width=2)
    draw.line((106, 70, 122, 96), fill=(70, 70, 70), width=2)
    img.save(source)

    output = improve_product_image(
        source,
        tmp_path / "out",
        background="transparent",
        cleanup_strength="balanced",
        min_size=1,
    )

    with Image.open(output) as result:
        rgba = result.convert("RGBA")
        visible_pixels = list(rgba.get_flattened_data() if hasattr(rgba, "get_flattened_data") else rgba.getdata())
    preserved_light_pixels = sum(
        1
        for r, g, b, a in visible_pixels
        if a > 0 and 185 <= r <= 238 and 185 <= g <= 238 and 180 <= b <= 235
    )
    dark_pixels = sum(1 for r, g, b, a in visible_pixels if a > 0 and r < 90 and g < 90 and b < 90)

    assert preserved_light_pixels > 2500
    assert dark_pixels > 100


def test_balanced_cleanup_preserves_lower_gray_furniture_body(tmp_path):
    source = tmp_path / "gray_white_desk_with_dark_base.png"
    img = Image.new("RGBA", (170, 150), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((18, 12, 152, 34), fill=(196, 196, 188, 255))
    draw.rounded_rectangle((38, 30, 146, 118), radius=5, fill=(146, 150, 142, 255))
    draw.rectangle((16, 102, 128, 128), fill=(36, 36, 34, 255))
    draw.line((30, 128, 18, 144), fill=(35, 35, 35, 255), width=4)
    draw.line((118, 128, 150, 142), fill=(35, 35, 35, 255), width=4)
    for x in range(58, 116, 8):
        for y in range(78, 112, 8):
            draw.ellipse((x, y, x + 2, y + 2), fill=(242, 242, 242, 255))
    img.save(source)
    source_lower_gray_pixels = _lower_neutral_gray_pixels(source)

    output = improve_product_image(
        source,
        tmp_path / "out",
        background="transparent",
        cleanup_strength="balanced",
        min_size=1,
    )

    lower_gray_pixels = _lower_neutral_gray_pixels(output)

    assert lower_gray_pixels >= source_lower_gray_pixels * 0.8


def _lower_neutral_gray_pixels(path: Path) -> int:
    with Image.open(path) as result:
        rgba = result.convert("RGBA")
        pixels = rgba.load()
        lower_gray_pixels = 0
        for y in range(int(rgba.height * 0.58), rgba.height):
            for x in range(rgba.width):
                r, g, b, a = pixels[x, y]
                brightness = (r + g + b) / 3
                neutral = max(r, g, b) - min(r, g, b) <= 18
                if a > 0 and neutral and 125 <= brightness <= 190:
                    lower_gray_pixels += 1
        return lower_gray_pixels


def test_balanced_cleanup_removes_low_contrast_floor_shadow(tmp_path):
    source = tmp_path / "chair_with_floor_shadow.png"
    img = Image.new("RGB", (160, 140), (246, 246, 246))
    draw = ImageDraw.Draw(img)
    draw.ellipse((16, 82, 144, 132), fill=(205, 205, 205))
    draw.rounded_rectangle((56, 14, 104, 74), radius=8, fill=(58, 58, 58))
    draw.ellipse((46, 60, 114, 90), fill=(24, 24, 24))
    draw.line((80, 90, 80, 116), fill=(35, 35, 35), width=4)
    for end_x in (42, 64, 96, 118):
        draw.line((80, 112, end_x, 128), fill=(35, 35, 35), width=4)
        draw.ellipse((end_x - 5, 124, end_x + 5, 134), fill=(24, 24, 24))
    img.save(source)

    output = improve_product_image(
        source,
        tmp_path / "out",
        background="transparent",
        cleanup_strength="balanced",
        min_size=1,
    )

    with Image.open(output) as result:
        rgba = result.convert("RGBA")
        pixels = rgba.load()
        lower_floor_pixels = 0
        for y in range(int(rgba.height * 0.58), rgba.height):
            for x in range(rgba.width):
                r, g, b, a = pixels[x, y]
                if a > 0 and 155 <= r <= 230 and 155 <= g <= 230 and 155 <= b <= 230:
                    lower_floor_pixels += 1

    assert lower_floor_pixels < 120


def test_balanced_cleanup_preserves_real_light_furniture_lower_structure(tmp_path):
    source = Path(r"C:\Users\pepem\Downloads\OBJETIVO REFENCIA.png")
    if not source.exists():
        return

    output = improve_product_image(
        source,
        tmp_path / "out",
        background="transparent",
        cleanup_strength="balanced",
        min_size=900,
    )

    with Image.open(output) as result:
        rgba = result.convert("RGBA")
        pixels = rgba.load()
        lower_light_pixels = 0
        for y in range(int(rgba.height * 0.66), rgba.height):
            for x in range(rgba.width):
                r, g, b, a = pixels[x, y]
                brightness = (r + g + b) / 3
                neutral = max(r, g, b) - min(r, g, b) <= 35
                if a > 0 and neutral and 130 <= brightness <= 245:
                    lower_light_pixels += 1

    assert lower_light_pixels > 15000


def test_cleanup_trims_outer_empty_canvas_around_long_products(tmp_path):
    source = tmp_path / "long_workstation_with_wide_shadow.png"
    img = Image.new("RGB", (900, 600), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 320, 880, 520), fill=(221, 221, 221))
    draw.polygon([(130, 250), (760, 230), (820, 270), (180, 300)], fill=(170, 170, 165))
    for x in (165, 330, 505, 735):
        draw.line((x, 295, x, 390), fill=(50, 50, 50), width=8)
    img.save(source, "PNG")

    output = improve_product_image(
        source,
        tmp_path / "out",
        background="transparent",
        cleanup_strength="normal",
        min_size=1,
    )

    with Image.open(output) as result:
        rgba = result.convert("RGBA")
        dark_pixels = sum(
            1
            for r, g, b, a in rgba.getdata()
            if a > 0 and (r + g + b) / 3 < 90
        )

    assert rgba.width < 780
    assert rgba.height < 360
    assert dark_pixels > 250
