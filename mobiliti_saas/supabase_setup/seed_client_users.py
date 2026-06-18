"""Create or repair Mobiliti client users in Supabase.

Required env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
  MOBILITI_CLIENT_DEFAULT_PASSWORD or MOBILITI_CLIENT_PASSWORD_<ALIAS>

Existing users keep their password unless RESET_CLIENT_PASSWORDS=true.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urlencode
import urllib.error
import urllib.request

import bcrypt


CLIENT_USERS = [
    ("joel.meza@mobiliti.mx", "Joel Meza", "100"),
    ("karen.merin@mobiliti.mx", "Karen Merin", "200"),
    ("jl.martinez@mobiliti.mx", "JL Martinez", "300"),
    ("gabriela.zavala@mobiliti.mx", "Gabriela Zavala", "400"),
    ("susana@mobiliti.mx", "Susana", "500"),
    ("emiliano.quevedo@mobiliti.mx", "Emiliano Quevedo", "600"),
]
PASSWORD_ENV_BY_EMAIL = {
    "joel.meza@mobiliti.mx": "MOBILITI_CLIENT_PASSWORD_JOEL_MEZA",
    "karen.merin@mobiliti.mx": "MOBILITI_CLIENT_PASSWORD_KAREN_MERIN",
    "jl.martinez@mobiliti.mx": "MOBILITI_CLIENT_PASSWORD_JL_MARTINEZ",
    "gabriela.zavala@mobiliti.mx": "MOBILITI_CLIENT_PASSWORD_GABRIELA_ZAVALA",
    "susana@mobiliti.mx": "MOBILITI_CLIENT_PASSWORD_SUSANA",
    "emiliano.quevedo@mobiliti.mx": "MOBILITI_CLIENT_PASSWORD_EMILIANO_QUEVEDO",
}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return value


SUPABASE_URL = _required_env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = _required_env("SUPABASE_SERVICE_KEY")
DEFAULT_PASSWORD = _required_env("MOBILITI_CLIENT_DEFAULT_PASSWORD")
RESET_PASSWORDS = os.environ.get("RESET_CLIENT_PASSWORDS", "").lower() in {"1", "true", "yes"}


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _client_password(email: str) -> str:
    env_name = PASSWORD_ENV_BY_EMAIL.get(email.lower().strip())
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return DEFAULT_PASSWORD


def _supabase_req(method: str, table: str, *, params: dict | None = None, payload: dict | None = None):
    query = f"?{urlencode(params)}" if params else ""
    url = f"{SUPABASE_URL}/rest/v1/{table}{query}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase HTTP {exc.code}: {detail}") from exc
    return json.loads(content) if content else []


def _get_user(email: str) -> dict | None:
    rows = _supabase_req("GET", "saas_usuarios", params={"email": f"eq.{email}", "limit": "1"})
    return rows[0] if rows else None


def _upsert_user(email: str, name: str) -> dict:
    existing = _get_user(email)
    payload = {
        "email": email,
        "nombre": name,
        "empresa": "Mobiliti",
        "es_admin": False,
        "activo": True,
    }
    if not existing or RESET_PASSWORDS:
        payload["hashed_password"] = _hash_password(_client_password(email))

    if existing:
        rows = _supabase_req("PATCH", "saas_usuarios", params={"id": f"eq.{existing['id']}"}, payload=payload)
    else:
        rows = _supabase_req("POST", "saas_usuarios", payload=payload)
    return rows[0]


def _ensure_subscription(user_id: int) -> None:
    rows = _supabase_req(
        "GET",
        "saas_suscripciones",
        params={"usuario_id": f"eq.{user_id}", "order": "fecha_fin.desc", "limit": "1"},
    )
    fecha_fin = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
    payload = {
        "usuario_id": user_id,
        "estado": "activa",
        "plan": "cliente",
        "fecha_fin": fecha_fin,
    }
    if rows:
        _supabase_req("PATCH", "saas_suscripciones", params={"id": f"eq.{rows[0]['id']}"}, payload=payload)
    else:
        payload["fecha_inicio"] = datetime.now(timezone.utc).isoformat()
        _supabase_req("POST", "saas_suscripciones", payload=payload)


def main() -> None:
    for email, name, prefix in CLIENT_USERS:
        user = _upsert_user(email, name)
        _ensure_subscription(int(user["id"]))
        print(f"{email} listo con prefijo {prefix}")


if __name__ == "__main__":
    main()
