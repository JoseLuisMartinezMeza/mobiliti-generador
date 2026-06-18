import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("smoke_saas", Path("scripts/smoke-saas.py"))
smoke_saas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_saas)


def test_signed_storage_upload_uses_signed_endpoint(monkeypatch, tmp_path):
    source = tmp_path / "q.xlsx"
    source.write_bytes(b"xlsx")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"Key":"quote-files/users/1/jobs/job/input.xlsx"}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return FakeResponse()

    monkeypatch.setattr(smoke_saas.urllib.request, "urlopen", fake_urlopen)

    result = smoke_saas._signed_storage_upload(
        "https://example.supabase.co",
        "anon-key",
        "quote-files",
        "users/1/jobs/job/input.xlsx",
        "signed-token",
        source,
    )

    assert captured["method"] == "PUT"
    assert captured["url"] == "https://example.supabase.co/storage/v1/object/upload/sign/quote-files/users/1/jobs/job/input.xlsx?token=signed-token"
    assert captured["headers"]["Apikey"] == "anon-key"
    assert captured["data"] == b"xlsx"
    assert result["Key"].endswith("input.xlsx")
