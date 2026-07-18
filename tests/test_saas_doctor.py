import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("saas_doctor", Path("scripts/saas_doctor.py"))
saas_doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(saas_doctor)


def test_check_env_rejects_missing_and_placeholders():
    results = saas_doctor.check_env(
        {
            "SUPABASE_URL": "https://[PROJECT_REF].supabase.co",
            "SUPABASE_SERVICE_KEY": "",
            "JWT_SECRET_KEY": "short",
            "CORS_ORIGINS": "*",
        }
    )

    statuses = {row["name"]: row["status"] for row in results}
    assert statuses["SUPABASE_URL"] == "fail"
    assert statuses["SUPABASE_SERVICE_KEY"] == "fail"
    assert statuses["JWT_SECRET_KEY"] == "fail"
    assert statuses["CORS_ORIGINS"] == "fail"


def test_check_env_accepts_valid_shape():
    results = saas_doctor.check_env(
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "service-role-key-example-long-value",
            "JWT_SECRET_KEY": "long-secret-value-at-least-24",
            "CORS_ORIGINS": "https://web.example.com",
            "QUOTE_ENGINE": "python",
        }
    )

    assert all(row["status"] == "ok" for row in results)


def test_exit_code_fails_on_fail_status():
    assert saas_doctor.exit_code([{"status": "ok"}, {"status": "warn"}]) == 0
    assert saas_doctor.exit_code([{"status": "ok"}, {"status": "fail"}]) == 1


def test_check_deploy_files_has_required_files():
    results = saas_doctor.check_deploy_files()
    statuses = {row["name"]: row["status"] for row in results}

    assert statuses["deploy files"] == "ok"
    assert statuses["docker build context"] == "ok"
    assert statuses["web vercel.json"] == "ok"


def test_check_deploy_files_warns_on_legacy_root_vercel():
    results = saas_doctor.check_deploy_files()
    statuses = {row["name"]: row["status"] for row in results}

    if saas_doctor.LEGACY_ROOT_VERCEL.exists():
        assert statuses["legacy mobiliti_saas/vercel.json"] == "warn"


def test_worker_dockerfile_runs_http_worker_with_matching_healthcheck():
    dockerfile = (saas_doctor.ROOT / "mobiliti_saas" / "worker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert 'CMD ["python", "mobiliti_saas/worker/render_web_worker.py"]' in dockerfile
    assert "os.environ.get('PORT', '10000')" in dockerfile
    assert "http://127.0.0.1:{port}/health" in dockerfile


def test_worker_dockerfile_uses_pinned_alpine_runtime_as_non_root():
    dockerfile = (saas_doctor.ROOT / "mobiliti_saas" / "worker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    lock = saas_doctor.ROOT / "mobiliti_saas" / "worker" / "requirements.lock"

    assert dockerfile.startswith("FROM python:3.12-alpine@sha256:")
    assert "apk add --no-cache libstdc++=" in dockerfile
    assert "python -m pip install --upgrade pip==26.1.2" in dockerfile
    assert "requirements.lock /app/requirements.lock" in dockerfile
    assert "pip install -r /app/requirements.lock" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert lock.exists()
    assert all(
        not line or line.startswith("#") or "==" in line
        for line in lock.read_text(encoding="utf-8").splitlines()
    )


def test_worker_docker_context_is_allowlisted():
    patterns = {
        line.strip()
        for line in (saas_doctor.ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "*" in patterns
    assert "!mobiliti_saas/worker/**" in patterns
    assert "!mobiliti_saas/quote_engine/**" in patterns
    assert "!pdf_quotation_import/**" in patterns
    assert "!.git/**" not in patterns
