"""
Production readiness checks for Mobiliti SaaS.

Does not print secret values. Uses env vars already present in the shell.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REQUIRED_PROD_ENV = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "JWT_SECRET_KEY",
    "CORS_ORIGINS",
]

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_FILES = [
    ROOT / "vercel_deploy" / "vercel.json",
    ROOT / "vercel_deploy" / "requirements.txt",
    ROOT / "vercel_deploy" / "api" / "index.py",
    ROOT / "mobiliti_saas" / "web" / "vercel.json",
    ROOT / "mobiliti_saas" / "web" / "package.json",
    ROOT / "mobiliti_saas" / "worker" / "Dockerfile",
    ROOT / "mobiliti_saas" / "worker" / "requirements.txt",
    ROOT / "mobiliti_saas" / "supabase_setup" / "create_tables.sql",
]
LEGACY_ROOT_VERCEL = ROOT / "mobiliti_saas" / "vercel.json"


def _redact(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _result(name: str, status: str, message: str) -> dict:
    return {"name": name, "status": status, "message": message}


def check_env(env: dict[str, str]) -> list[dict]:
    results = []
    for name in REQUIRED_PROD_ENV:
        value = env.get(name, "").strip()
        if not value:
            results.append(_result(name, "fail", "faltante"))
            continue
        if "YOUR_" in value or "[PROJECT_REF]" in value or "xxxxxxxx" in value:
            results.append(_result(name, "fail", "placeholder no valido"))
            continue
        if name == "JWT_SECRET_KEY" and len(value) < 24:
            results.append(_result(name, "fail", "muy corto"))
            continue
        if name == "SUPABASE_URL" and not value.startswith("https://"):
            results.append(_result(name, "warn", "debe ser https en produccion"))
            continue
        if name == "CORS_ORIGINS" and "*" in {item.strip() for item in value.split(",")}:
            results.append(_result(name, "fail", "no uses * en produccion"))
            continue
        results.append(_result(name, "ok", f"configurado ({_redact(value)})"))

    if env.get("QUOTE_ENGINE", "python").strip().lower() not in {"python", "openpyxl", "online", "auto"}:
        results.append(_result("QUOTE_ENGINE", "fail", "valor invalido"))
    else:
        results.append(_result("QUOTE_ENGINE", "ok", env.get("QUOTE_ENGINE", "python")))

    return results


def _request_json(url: str, headers: dict | None = None, timeout: int = 20):
    req = urllib.request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def check_api(api_url: str, expected_storage_provider: str | None = None) -> dict:
    try:
        data = _request_json(f"{api_url.rstrip('/')}/health")
        if data.get("status") != "ok":
            return _result("API /health", "fail", f"respuesta inesperada: {data}")
        provider = data.get("storage_provider")
        configured = data.get("storage_configured")
        message = f"{api_url} storage_provider={provider or 'unknown'} storage_configured={configured}"
        if expected_storage_provider and provider != expected_storage_provider:
            return _result("API /health", "fail", f"esperado {expected_storage_provider}; {message}")
        if configured is False:
            return _result("API /health", "fail", message)
        return _result("API /health", "ok", message)
    except Exception as exc:
        return _result("API /health", "fail", str(exc))


def check_deploy_files() -> list[dict]:
    results = []
    missing = [str(path.relative_to(ROOT)) for path in DEPLOY_FILES if not path.exists()]
    if missing:
        results.append(_result("deploy files", "fail", "faltan: " + ", ".join(missing)))
    else:
        results.append(_result("deploy files", "ok", f"{len(DEPLOY_FILES)} archivos requeridos"))

    web_vercel = ROOT / "mobiliti_saas" / "web" / "vercel.json"
    try:
        data = json.loads(web_vercel.read_text(encoding="utf-8"))
        if data.get("outputDirectory") != "dist" or data.get("framework") != "vite":
            results.append(_result("web vercel.json", "fail", "framework/outputDirectory invalidos"))
        else:
            results.append(_result("web vercel.json", "ok", "vite -> dist"))
    except Exception as exc:
        results.append(_result("web vercel.json", "fail", str(exc)))

    if LEGACY_ROOT_VERCEL.exists():
        try:
            data = json.loads(LEGACY_ROOT_VERCEL.read_text(encoding="utf-8"))
            rewrites = data.get("rewrites", [])
            destinations = [str(row.get("destination", "")) for row in rewrites if isinstance(row, dict)]
            if any(destination.startswith("/api/") for destination in destinations):
                results.append(
                    _result(
                        "legacy mobiliti_saas/vercel.json",
                        "warn",
                        "no desplegar mobiliti_saas root; usa vercel_deploy para API y mobiliti_saas/web para frontend",
                    )
                )
        except Exception as exc:
            results.append(_result("legacy mobiliti_saas/vercel.json", "warn", f"no se pudo leer: {exc}"))

    return results


def check_supabase(env: dict[str, str], bucket: str = "quote-files") -> list[dict]:
    url = env.get("SUPABASE_URL", "").rstrip("/")
    key = env.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return [_result("Supabase", "skip", "faltan SUPABASE_URL/SUPABASE_SERVICE_KEY")]

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    results = []
    try:
        table_url = f"{url}/rest/v1/saas_quote_jobs?select=id&limit=1"
        _request_json(table_url, headers=headers)
        results.append(_result("Supabase table saas_quote_jobs", "ok", "REST accesible"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        results.append(_result("Supabase table saas_quote_jobs", "fail", f"HTTP {exc.code}: {body[:200]}"))
    except Exception as exc:
        results.append(_result("Supabase table saas_quote_jobs", "fail", str(exc)))

    try:
        bucket_id = urllib.parse.quote(bucket, safe="")
        bucket_url = f"{url}/storage/v1/bucket/{bucket_id}"
        data = _request_json(bucket_url, headers=headers)
        public = bool(data.get("public"))
        status = "fail" if public else "ok"
        msg = "bucket privado" if not public else "bucket publico; debe ser privado"
        results.append(_result(f"Storage bucket {bucket}", status, msg))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        results.append(_result(f"Storage bucket {bucket}", "fail", f"HTTP {exc.code}: {body[:200]}"))
    except Exception as exc:
        results.append(_result(f"Storage bucket {bucket}", "fail", str(exc)))

    return results


def exit_code(results: list[dict]) -> int:
    return 1 if any(row["status"] == "fail" for row in results) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verifica readiness de Mobiliti SaaS")
    parser.add_argument("--api-url", default=os.environ.get("MOBILITI_SMOKE_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--bucket", default=os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files"))
    parser.add_argument("--expect-storage-provider", choices=["supabase", "r2"], default=None)
    parser.add_argument("--dev", action="store_true", help="Modo local: no exige env vars de Supabase/JWT")
    parser.add_argument("--skip-supabase", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    results = []
    results.extend(check_deploy_files())
    if args.dev:
        results.append(_result("dev mode", "ok", "checks de produccion omitidos"))
    else:
        results.extend(check_env(os.environ))
    results.append(check_api(args.api_url, args.expect_storage_provider))
    if not args.skip_supabase:
        results.extend(check_supabase(os.environ, args.bucket))

    if args.as_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for row in results:
            print(f"[{row['status'].upper()}] {row['name']}: {row['message']}")

    sys.exit(exit_code(results))


if __name__ == "__main__":
    main()
