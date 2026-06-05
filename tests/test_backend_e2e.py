"""
Test E2E del backend (endpoints publicos y rate limiting).
Requiere que esten instalados fastapi y starlette.
No requiere Supabase para los endpoints publicos.
"""

import pytest
from fastapi.testclient import TestClient

# Importamos solo la app del backend
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vercel_deploy", "api"))

# Mock variables de entorno antes de importar index
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import index


@pytest.fixture
def client():
    return TestClient(index.app)


class TestPublicEndpoints:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_version(self, client):
        resp = client.get("/version")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "download_url" in data
        assert data["version"] == "1.5.4"

    def test_download_latest(self, client):
        resp = client.get("/download/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data


class TestRateLimitE2E:
    def test_login_blocks_after_5_attempts(self, client, monkeypatch):
        # Mock DB para evitar conexion a Supabase
        def mock_get_user(email):
            return None

        monkeypatch.setattr(index, "db_get_usuario_by_email", mock_get_user)

        ip = "127.0.0.1"
        for i in range(5):
            resp = client.post("/login", json={"email": "a@b.com", "password": "x"}, headers={"X-Forwarded-For": ip})
            assert resp.status_code == 401

        # 6to intento debe bloquearse
        resp = client.post("/login", json={"email": "a@b.com", "password": "x"}, headers={"X-Forwarded-For": ip})
        assert resp.status_code == 429
        assert "Demasiados intentos" in resp.json()["detail"]
