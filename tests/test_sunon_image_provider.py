from pathlib import Path
from io import BytesIO
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mobiliti_saas.quote_engine.sunon_image_provider import (  # noqa: E402
    extract_product_code,
    fetch_sunon_product_image,
    find_sunon_catalog_match,
    find_sunon_catalog_image_url,
    find_sunon_exact_image_url,
    find_sunon_image_url,
    parse_sunon_product_no_catalog_entries,
    parse_sunon_image_url,
    sunon_code_candidates,
)


class _FakeResponse:
    def __init__(self, data: bytes, content_type: str) -> None:
        self._data = data
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size=-1):
        return self._data


def test_extract_product_code_reads_first_sunon_style_token():
    assert extract_product_code("CHJ80SW H7 Task Chair") == "CHJ80SW"
    assert extract_product_code("CLG65SW Locke Task Chair") == "CLG65SW"
    assert extract_product_code("Sin codigo de producto") is None


def test_sunon_code_candidates_include_controlled_base_code():
    assert sunon_code_candidates("DV74-2.380148 I-Varna II Conference Table") == [
        "DV74-2.380148",
        "DV74-2",
        "DV74",
    ]
    assert sunon_code_candidates("DMC27.058040 MixCube Stools") == [
        "DMC27.058040",
        "DMC27",
    ]
    assert sunon_code_candidates("SD32.1.MR.M Ducky Stool") == [
        "SD32.1.MR.M",
        "SD32.1.MR",
        "SD32.1",
        "SD32",
    ]


def test_parse_sunon_search_result_image_for_code():
    html = """
    <ul>
      <li>
        <a href="/product-category/resource/3dmodel/?product_id=12013">
          <div class="pic"><img src="https://file.sunonglobal.com/wp-content/uploads/chair_CHJ80SW.jpg" alt=""></div>
          <div class="tit">H7-<span>CHJ80SW</span></div>
        </a>
      </li>
    </ul>
    """

    assert parse_sunon_image_url(html, "CHJ80SW") == "https://file.sunonglobal.com/wp-content/uploads/chair_CHJ80SW.jpg"


def test_parse_sunon_product_table_image_for_code():
    html = """
    <table><tbody>
      <tr>
        <td><div class="pic"><img src="/wp-content/uploads/chair_CHJ81SW.jpg" alt=""></div></td>
        <td>CHJ81SW</td>
      </tr>
    </tbody></table>
    """

    assert parse_sunon_image_url(html, "CHJ81SW", base_url="https://www.sunonglobal.com/product/h7/") == (
        "https://www.sunonglobal.com/wp-content/uploads/chair_CHJ81SW.jpg"
    )


def test_parse_sunon_product_no_catalog_entries_from_variant_rows():
    html = """
    <table><tbody>
      <tr>
        <td><div class="pic"><img src="/wp-content/uploads/chair_CHJ80SW.jpg" alt=""></div></td>
        <td>CHJ80SW</td>
        <td>620(mm)</td>
      </tr>
      <tr>
        <td><div class="pic"><img src="/wp-content/uploads/chair_CHJ81SW.jpg" alt=""></div></td>
        <td>CHJ81SW</td>
        <td>670(mm)</td>
      </tr>
    </tbody></table>
    """

    entries = parse_sunon_product_no_catalog_entries(
        html,
        product_url="https://www.sunonglobal.com/product/h7-task-office-chair/",
        product_title="H7",
        last_seen="2026-07-02",
    )

    assert [entry["code"] for entry in entries] == ["CHJ80SW", "CHJ81SW"]
    assert entries[0]["confidence"] == "exact_code"
    assert entries[0]["image_url"] == "https://www.sunonglobal.com/wp-content/uploads/chair_CHJ80SW.jpg"


def test_find_sunon_catalog_image_url_uses_exact_normalized_code(tmp_path):
    catalog = tmp_path / "sunon_catalog.json"
    catalog.write_text(
        """
        {
          "entries": [
            {
              "code": "DMC27.058040",
              "normalized_code": "DMC27058040",
              "image_url": "https://file.sunonglobal.com/dmc27.jpg"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    assert find_sunon_catalog_image_url("DMC27.058040", catalog_path=catalog) == "https://file.sunonglobal.com/dmc27.jpg"
    assert find_sunon_catalog_image_url("DMC27", catalog_path=catalog) is None


def test_find_sunon_catalog_image_url_uses_official_base_code_for_extended_variant(tmp_path):
    catalog = tmp_path / "sunon_catalog.json"
    catalog.write_text(
        """
        {
          "entries": [
            {
              "code": "DV74",
              "normalized_code": "DV74",
              "image_url": "https://file.sunonglobal.com/dv74.png"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    assert find_sunon_catalog_image_url(
        "DV74-2.380148 I-Varna II Conference Table",
        catalog_path=catalog,
    ) == "https://file.sunonglobal.com/dv74.png"
    entry, matched_code, match_type = find_sunon_catalog_match(
        "DV74-2.380148 I-Varna II Conference Table",
        catalog_path=catalog,
    )
    assert entry is not None
    assert matched_code == "DV74"
    assert match_type == "base_code"


def test_find_sunon_catalog_image_url_prefers_full_code_over_base_code(tmp_path):
    catalog = tmp_path / "sunon_catalog.json"
    catalog.write_text(
        """
        {
          "entries": [
            {
              "code": "DMC27",
              "normalized_code": "DMC27",
              "image_url": "https://file.sunonglobal.com/base.jpg"
            },
            {
              "code": "DMC27.058040",
              "normalized_code": "DMC27058040",
              "image_url": "https://file.sunonglobal.com/full.jpg"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    assert find_sunon_catalog_image_url("DMC27.058040 MixCube Stools", catalog_path=catalog) == (
        "https://file.sunonglobal.com/full.jpg"
    )


def test_fetch_sunon_product_image_downloads_search_match(monkeypatch, tmp_path):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (20, 20, 20)).save(buffer, "JPEG")
    image_bytes = buffer.getvalue()
    search_html = """
    <li>
      <div class="pic"><img src="https://file.sunonglobal.com/wp-content/uploads/143100011_单体图_CHJ80SW_侧面45°.jpg" alt=""></div>
      <div class="tit">H7-<span>CHJ80SW</span></div>
    </li>
    """.encode("utf-8")

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "s=CHJ80SW" in url:
            return _FakeResponse(search_html, "text/html; charset=utf-8")
        if "%E5%8D%95%E4%BD%93%E5%9B%BE_CHJ80SW" in url:
            return _FakeResponse(image_bytes, "image/jpeg")
        raise AssertionError(url)

    monkeypatch.setattr("mobiliti_saas.quote_engine.sunon_image_provider.urllib.request.urlopen", fake_urlopen)

    output = fetch_sunon_product_image("CHJ80SW H7 Task Chair", tmp_path)

    assert output is not None
    assert output.suffix == ".jpg"
    assert output.read_bytes() == image_bytes


def test_find_sunon_image_url_uses_rest_product_fallback(monkeypatch):
    search_html = b"""
    <html>
      <head><title>CLG65SW | Sunon Office Furniture</title></head>
      <body><img src="/wp-content/themes/logo.png"></body>
    </html>
    """
    rest_json = b"""
    [{
      "id": 3689,
      "link": "https://www.sunonglobal.com/product/locke-ergonomic-task-chair/",
      "title": {"rendered": "Locke"},
      "content": {"rendered": "<p>Product Product No. CLG65SW</p>"},
      "_embedded": {
        "wp:featuredmedia": [{
          "source_url": "https://file.sunonglobal.com/wp-content/uploads/locke.png",
          "media_details": {
            "sizes": {
              "large": {"source_url": "https://file.sunonglobal.com/wp-content/uploads/locke-large.png"}
            }
          }
        }]
      }
    }]
    """

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "s=CLG65SW" in url:
            return _FakeResponse(search_html, "text/html; charset=utf-8")
        if "wp-json/wp/v2/product" in url and "search=CLG65SW" in url:
            return _FakeResponse(rest_json, "application/json; charset=utf-8")
        raise AssertionError(url)

    monkeypatch.setattr("mobiliti_saas.quote_engine.sunon_image_provider.urllib.request.urlopen", fake_urlopen)

    assert find_sunon_image_url("CLG65SW", product_name="CLG65SW Locke Task Chair") == (
        "https://file.sunonglobal.com/wp-content/uploads/locke-large.png"
    )


def test_find_sunon_image_url_uses_product_name_when_code_search_has_no_rest_match(monkeypatch):
    search_html = b"<html><head><title>CHT85SW | Sunon Office Furniture</title></head></html>"
    empty_json = b"[]"
    h2_json = b"""
    [{
      "id": 3769,
      "link": "https://www.sunonglobal.com/product/h2-ergonomic-office-chair/",
      "title": {"rendered": "H2"},
      "content": {"rendered": "<p>H2 ergonomic office chair</p>"},
      "_embedded": {
        "wp:featuredmedia": [{
          "source_url": "https://file.sunonglobal.com/wp-content/uploads/h2.png"
        }]
      }
    }]
    """

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "s=CHT85SW" in url:
            return _FakeResponse(search_html, "text/html; charset=utf-8")
        if "wp-json/wp/v2/product" in url and "search=CHT85SW" in url:
            return _FakeResponse(empty_json, "application/json; charset=utf-8")
        if "wp-json/wp/v2/product" in url and "search=H2+Task+Chair" in url:
            return _FakeResponse(h2_json, "application/json; charset=utf-8")
        raise AssertionError(url)

    monkeypatch.setattr("mobiliti_saas.quote_engine.sunon_image_provider.urllib.request.urlopen", fake_urlopen)

    assert find_sunon_image_url("CHT85SW", product_name="CHT85SW H2 Task Chair") == (
        "https://file.sunonglobal.com/wp-content/uploads/h2.png"
    )


def test_find_sunon_image_url_uses_product_family_terms(monkeypatch):
    search_html = b"<html><head><title>DV74-2.380148 | Sunon Office Furniture</title></head></html>"
    empty_json = b"[]"
    varna_json = b"""
    [{
      "id": 4310,
      "link": "https://www.sunonglobal.com/product/varna-ii-stylish-conference-table/",
      "slug": "varna-ii-stylish-conference-table",
      "title": {"rendered": "Varna II"},
      "content": {"rendered": "<p>Stylish conference table</p>"},
      "_embedded": {
        "wp:featuredmedia": [{
          "source_url": "https://file.sunonglobal.com/wp-content/uploads/varna-ii.png"
        }]
      }
    }]
    """

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "s=DV74-2.380148" in url:
            return _FakeResponse(search_html, "text/html; charset=utf-8")
        if "wp-json/wp/v2/product" in url and "search=Varna+II" in url:
            return _FakeResponse(varna_json, "application/json; charset=utf-8")
        if "wp-json/wp/v2/product" in url:
            return _FakeResponse(empty_json, "application/json; charset=utf-8")
        raise AssertionError(url)

    monkeypatch.setattr("mobiliti_saas.quote_engine.sunon_image_provider.urllib.request.urlopen", fake_urlopen)

    assert find_sunon_image_url(
        "DV74-2.380148",
        product_name="DV74-2.380148 I-Varna II Conference Table",
    ) == "https://file.sunonglobal.com/wp-content/uploads/varna-ii.png"


def test_find_sunon_exact_image_url_rejects_family_only_rest_match(monkeypatch):
    search_html = b"<html><head><title>DV74-2.380148 | Sunon Office Furniture</title></head></html>"
    varna_json = b"""
    [{
      "id": 4310,
      "link": "https://www.sunonglobal.com/product/varna-ii-stylish-conference-table/",
      "slug": "varna-ii-stylish-conference-table",
      "title": {"rendered": "Varna II"},
      "content": {"rendered": "<p>Stylish conference table without exact code</p>"},
      "_embedded": {
        "wp:featuredmedia": [{
          "source_url": "https://file.sunonglobal.com/wp-content/uploads/varna-ii.png"
        }]
      }
    }]
    """

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "s=DV74-2.380148" in url:
            return _FakeResponse(search_html, "text/html; charset=utf-8")
        if "wp-json/wp/v2/product" in url and "search=DV74-2.380148" in url:
            return _FakeResponse(varna_json, "application/json; charset=utf-8")
        raise AssertionError(url)

    monkeypatch.setattr("mobiliti_saas.quote_engine.sunon_image_provider.urllib.request.urlopen", fake_urlopen)

    assert find_sunon_exact_image_url("DV74-2.380148") is None


def test_find_sunon_image_url_skips_document_like_products(monkeypatch):
    search_html = b"<html><head><title>Tetris | Sunon Office Furniture</title></head></html>"
    tetris_json = b"""
    [{
      "id": 1809,
      "link": "https://www.sunonglobal.com/product/tetris-brochure/",
      "slug": "tetris-brochure",
      "title": {"rendered": "Tetris Brochure"},
      "content": {"rendered": "<p>Tetris</p>"},
      "_embedded": {
        "wp:featuredmedia": [{
          "source_url": "https://file.sunonglobal.com/wp-content/uploads/tetris-brochure.png"
        }]
      }
    }]
    """

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "s=ST90.NA.MR" in url:
            return _FakeResponse(search_html, "text/html; charset=utf-8")
        if "wp-json/wp/v2/product" in url and "search=Tetris" in url:
            return _FakeResponse(tetris_json, "application/json; charset=utf-8")
        if "wp-json/wp/v2/product" in url:
            return _FakeResponse(b"[]", "application/json; charset=utf-8")
        raise AssertionError(url)

    monkeypatch.setattr("mobiliti_saas.quote_engine.sunon_image_provider.urllib.request.urlopen", fake_urlopen)

    assert find_sunon_image_url(
        "ST90.NA.MR",
        product_name="ST90.NA.MR Tetris Lounge Modular Seating",
    ) is None
