import importlib
import sys


MODULE = "mobiliti_saas.supabase_setup.seed_client_users"


def _load_seed_module(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setenv("MOBILITI_CLIENT_DEFAULT_PASSWORD", "fallback-password")
    sys.modules.pop(MODULE, None)
    return importlib.import_module(MODULE)


def test_seed_uses_user_specific_password_from_env(monkeypatch):
    module = _load_seed_module(monkeypatch)
    monkeypatch.setenv("MOBILITI_CLIENT_PASSWORD_JOEL_MEZA", "user-password")

    assert module._client_password("joel.meza@mobiliti.mx") == "user-password"


def test_seed_falls_back_to_default_password(monkeypatch):
    module = _load_seed_module(monkeypatch)

    assert module._client_password("karen.merin@mobiliti.mx") == "fallback-password"
