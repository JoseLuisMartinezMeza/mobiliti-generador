from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import mobiliti_saas.api.index as api_index


class StreamingRequest:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def oversize_chunks():
    chunk = b"x" * (1024 * 1024)
    return [chunk] * 26


def test_supplier_quote_rejects_declared_oversize_before_catalog_loading(monkeypatch):
    loaded = []
    api_index.app.dependency_overrides[api_index.get_current_user] = lambda: {"id": 7}
    monkeypatch.setattr(api_index, "_require_enabled_catalog_supplier", lambda value: value)
    monkeypatch.setattr(api_index, "_require_active_subscription", lambda _user_id: None)
    monkeypatch.setattr(
        api_index,
        "_load_supplier_catalog_cached",
        lambda supplier: loaded.append(supplier) or (_ for _ in ()).throw(
            RuntimeError("catalog should not load")
        ),
    )
    try:
        response = TestClient(api_index.app).post(
            "/catalogs/alma/quote",
            headers={"content-length": str(api_index.MAX_QUOTE_REQUEST_BYTES + 1)},
            content=b"{}",
        )
    finally:
        api_index.app.dependency_overrides.clear()

    assert response.status_code == 413
    assert loaded == []
    assert "bytes" in response.json()["detail"].lower()


@pytest.mark.parametrize("headers", ({}, {"content-length": "2"}))
def test_supplier_reader_rejects_stream_over_limit_with_absent_or_false_length(headers):
    request = StreamingRequest(oversize_chunks(), headers=headers)

    with pytest.raises(api_index.HTTPException) as exc:
        asyncio.run(api_index._read_supplier_quote_body(request))

    assert exc.value.status_code == 413
    assert "bytes" in exc.value.detail.lower()


def test_supplier_reader_accepts_request_at_exact_25_mib_limit():
    padding = b" " * (api_index.MAX_QUOTE_REQUEST_BYTES - 2)
    request = StreamingRequest(
        [b"{}", padding],
        headers={"content-length": str(api_index.MAX_QUOTE_REQUEST_BYTES)},
    )

    assert asyncio.run(api_index._read_supplier_quote_body(request)) == {}
