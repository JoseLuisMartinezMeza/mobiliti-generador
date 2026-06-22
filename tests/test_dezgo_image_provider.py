from pathlib import Path
import urllib.error

import pytest
from PIL import Image

from mobiliti_saas.quote_engine.ai_image_provider import (
    DEFAULT_DEZGO_ENDPOINT,
    DEFAULT_DEZGO_NEGATIVE_PROMPT,
    DEFAULT_DEZGO_PROMPT,
    DEFAULT_DEZGO_TEXT_ENDPOINT,
    DezgoImageProviderConfig,
    ImageProviderError,
    dezgo_config_from_env,
    enhance_with_dezgo,
    generate_with_dezgo,
    normalize_image_provider,
)


def _png_bytes(path: Path, color=(255, 255, 255, 0)) -> bytes:
    Image.new("RGBA", (16, 16), color).save(path, "PNG")
    return path.read_bytes()


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def test_normalize_image_provider_defaults_to_pillow():
    assert normalize_image_provider(None) == "pillow"
    assert normalize_image_provider("") == "pillow"
    assert normalize_image_provider("local") == "pillow"
    assert normalize_image_provider("DEZGO") == "dezgo"


def test_default_dezgo_prompt_targets_realistic_ambient_catalog():
    assert DEFAULT_DEZGO_ENDPOINT == "https://api.dezgo.com/image2image_flux_2_pro"
    assert DEFAULT_DEZGO_TEXT_ENDPOINT == "https://api.dezgo.com/text2image_flux_2_pro"
    assert "photorealistic" in DEFAULT_DEZGO_PROMPT
    assert "clean pure white or transparent studio background" in DEFAULT_DEZGO_PROMPT
    assert "preserve the exact original product shape" in DEFAULT_DEZGO_PROMPT
    assert "changed product design" in DEFAULT_DEZGO_NEGATIVE_PROMPT


def test_dezgo_env_uses_flux_2_pro_even_if_legacy_endpoint_was_background_removal(monkeypatch):
    monkeypatch.setenv("DEZGO_ENDPOINT", "https://api.dezgo.com/remove-background")
    monkeypatch.delenv("DEZGO_ALLOW_NON_RETOUCH_ENDPOINT", raising=False)

    config = dezgo_config_from_env()

    assert config.endpoint == "https://api.dezgo.com/image2image_flux_2_pro"
    assert config.strength >= 0.5


def test_dezgo_env_upgrades_legacy_image2image_endpoint_to_flux_2_pro(monkeypatch):
    monkeypatch.setenv("DEZGO_ENDPOINT", "https://api.dezgo.com/image2image")
    monkeypatch.delenv("DEZGO_ALLOW_LEGACY_IMAGE_ENDPOINT", raising=False)

    config = dezgo_config_from_env()

    assert config.endpoint == "https://api.dezgo.com/image2image_flux_2_pro"


def test_dezgo_env_can_explicitly_allow_legacy_image_endpoint(monkeypatch):
    monkeypatch.setenv("DEZGO_ENDPOINT", "https://api.dezgo.com/image2image")
    monkeypatch.setenv("DEZGO_ALLOW_LEGACY_IMAGE_ENDPOINT", "1")

    config = dezgo_config_from_env()

    assert config.endpoint == "https://api.dezgo.com/image2image"


def test_dezgo_env_can_explicitly_allow_non_retouch_endpoint(monkeypatch):
    monkeypatch.setenv("DEZGO_ENDPOINT", "https://api.dezgo.com/remove-background")
    monkeypatch.setenv("DEZGO_ALLOW_NON_RETOUCH_ENDPOINT", "1")

    config = dezgo_config_from_env()

    assert config.endpoint == "https://api.dezgo.com/remove-background"


def test_dezgo_remove_background_posts_image_with_auth_header(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    expected = _png_bytes(source)
    output = tmp_path / "out.png"
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["auth"] = request.get_header("X-dezgo-key")
        captured["content_type"] = request.get_header("Content-type")
        captured["body"] = request.data
        return _FakeResponse(expected)

    monkeypatch.setattr("mobiliti_saas.quote_engine.ai_image_provider.urllib.request.urlopen", fake_urlopen)

    result = enhance_with_dezgo(
        source,
        output,
        DezgoImageProviderConfig(api_key="fake-key", endpoint="https://api.dezgo.com/remove-background"),
    )

    assert result == output
    assert output.read_bytes() == expected
    assert captured["url"] == "https://api.dezgo.com/remove-background"
    assert captured["timeout"] == 120
    assert captured["auth"] == "fake-key"
    assert "multipart/form-data" in captured["content_type"]
    assert b'name="image"; filename="source.png"' in captured["body"]
    assert b'name="mode"' in captured["body"]
    assert b"transparent" in captured["body"]


def test_dezgo_image2image_includes_flux_model_and_prompt(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    expected = _png_bytes(source, color=(20, 20, 20, 255))
    output = tmp_path / "out.png"
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = request.data
        return _FakeResponse(expected)

    monkeypatch.setattr("mobiliti_saas.quote_engine.ai_image_provider.urllib.request.urlopen", fake_urlopen)

    enhance_with_dezgo(
        source,
        output,
        DezgoImageProviderConfig(
            api_key="fake-key",
            endpoint="https://api.dezgo.com/image2image_flux_2_pro",
            model="flux_2_pro",
            prompt="clean furniture product photo on a pure white background",
        ),
    )

    assert b'name="init_image"; filename="source.png"' in captured["body"]
    assert b'name="model"' not in captured["body"]
    assert b'name="prompt"' in captured["body"]
    assert b"pure white background" in captured["body"]
    assert b'name="negative_prompt"' in captured["body"]
    assert b'name="strength"' not in captured["body"]


def test_dezgo_legacy_image2image_replaces_flux_model_with_valid_endpoint_model(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    expected = _png_bytes(source, color=(20, 20, 20, 255))
    output = tmp_path / "out.png"
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = request.data
        return _FakeResponse(expected)

    monkeypatch.setattr("mobiliti_saas.quote_engine.ai_image_provider.urllib.request.urlopen", fake_urlopen)

    enhance_with_dezgo(
        source,
        output,
        DezgoImageProviderConfig(
            api_key="fake-key",
            endpoint="https://api.dezgo.com/image2image",
            model="flux_2",
            prompt="clean furniture product photo on a pure white background",
        ),
    )

    assert b"realistic_vision_5_1" in captured["body"]
    assert b"flux_2" not in captured["body"]


def test_dezgo_text2image_flux_generates_from_prompt(monkeypatch, tmp_path):
    expected = _png_bytes(tmp_path / "expected.png", color=(30, 30, 30, 255))
    output = tmp_path / "generated.png"
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        return _FakeResponse(expected)

    monkeypatch.setattr("mobiliti_saas.quote_engine.ai_image_provider.urllib.request.urlopen", fake_urlopen)

    generate_with_dezgo(
        "realistic workstation render",
        output,
        DezgoImageProviderConfig(
            api_key="fake-key",
            text_endpoint="https://api.dezgo.com/text2image_flux_2_pro",
            text_width=1024,
            text_height=1024,
            text_steps=8,
        ),
    )

    assert output.read_bytes() == expected
    assert captured["url"] == "https://api.dezgo.com/text2image_flux_2_pro"
    assert captured["timeout"] == 120
    assert b'name="prompt"' in captured["body"]
    assert b"realistic workstation render" in captured["body"]
    assert b'name="format"' in captured["body"]
    assert b'name="width"' not in captured["body"]
    assert b'name="transparent_background"' not in captured["body"]


def test_dezgo_missing_key_raises_clear_error(tmp_path):
    source = tmp_path / "source.png"
    _png_bytes(source)

    with pytest.raises(ImageProviderError, match="DEZGO_API_KEY"):
        enhance_with_dezgo(source, tmp_path / "out.png", DezgoImageProviderConfig(api_key=None))


def test_dezgo_http_error_does_not_expose_secret(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    _png_bytes(source)

    def fake_urlopen(_request, timeout):
        assert timeout == 120
        raise urllib.error.HTTPError(
            "https://api.dezgo.com/remove-background",
            402,
            "Payment Required",
            {},
            None,
        )

    monkeypatch.setattr("mobiliti_saas.quote_engine.ai_image_provider.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ImageProviderError) as exc:
        enhance_with_dezgo(source, tmp_path / "out.png", DezgoImageProviderConfig(api_key="fake-secret"))

    assert "fake-secret" not in str(exc.value)
