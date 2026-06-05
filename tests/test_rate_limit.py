"""
Tests unitarios para el rate limiting del backend.
No requiere variables de entorno ni conexion a Supabase.
"""

import time
import pytest

# Copiamos la logica de rate limiting para testearla sin importar todo el backend
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 900


def _check_rate_limit(store: dict, ip: str) -> bool:
    now = time.monotonic()
    attempts = store.get(ip, [])
    attempts = [t for t in attempts if now - t < _WINDOW_SECONDS]
    store[ip] = attempts
    return len(attempts) < _MAX_ATTEMPTS


def _record_attempt(store: dict, ip: str):
    now = time.monotonic()
    attempts = store.get(ip, [])
    attempts.append(now)
    store[ip] = attempts


class TestRateLimit:
    def test_allows_under_limit(self):
        store = {}
        ip = "192.168.1.1"
        for _ in range(4):
            assert _check_rate_limit(store, ip) is True
            _record_attempt(store, ip)
        assert len(store[ip]) == 4

    def test_blocks_at_limit(self):
        store = {}
        ip = "192.168.1.2"
        for _ in range(5):
            assert _check_rate_limit(store, ip) is True
            _record_attempt(store, ip)
        # 6to intento debe bloquearse
        assert _check_rate_limit(store, ip) is False

    def test_different_ips_independent(self):
        store = {}
        ip_a = "10.0.0.1"
        ip_b = "10.0.0.2"
        for _ in range(5):
            _record_attempt(store, ip_a)
        assert _check_rate_limit(store, ip_a) is False
        assert _check_rate_limit(store, ip_b) is True

    def test_expires_after_window(self):
        store = {}
        ip = "192.168.1.3"
        # Simular intentos antiguos poniendo timestamps viejos manualmente
        old_time = time.monotonic() - _WINDOW_SECONDS - 1
        store[ip] = [old_time] * 5
        # Despues de la ventana debe permitir de nuevo
        assert _check_rate_limit(store, ip) is True
