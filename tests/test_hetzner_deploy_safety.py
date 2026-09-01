import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = ROOT / "deploy" / "hetzner" / "preflight.py"


def _preflight_module():
    assert PREFLIGHT_PATH.is_file(), "missing catalog-sync deploy preflight"
    spec = importlib.util.spec_from_file_location("hetzner_preflight", PREFLIGHT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Certificate:
    def __init__(self, *, symlink=False, regular=True, size=1, uid=0, gid=10001, mode=0o440):
        self.symlink = symlink
        self.regular = regular
        self.size = size
        self.uid = uid
        self.gid = gid
        self.mode = mode

    def is_symlink(self):
        return self.symlink

    def is_file(self):
        return self.regular

    def stat(self):
        return os.stat_result((self.mode, 0, 0, 1, self.uid, self.gid, self.size, 0, 0, 0))


class _Directory:
    def __init__(self, *, symlink=False, directory=True, uid=0, gid=10001, mode=0o750):
        self.symlink = symlink
        self.directory = directory
        self.uid = uid
        self.gid = gid
        self.mode = mode

    def is_symlink(self):
        return self.symlink

    def is_dir(self):
        return self.directory

    def stat(self):
        return os.stat_result((self.mode, 0, 0, 1, self.uid, self.gid, 0, 0, 0, 0))


def _active_catalog_env():
    return {
        "CATALOG_SYNC_ENABLED": "true",
        "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
        "SUPABASE_SERVICE_KEY": "test-service-key",
        "CATALOG_ENABLED_SUPPLIERS": "sunon",
        "MS_GRAPH_TENANT_ID": "tenant-id",
        "MS_GRAPH_CLIENT_ID": "client-id",
        "MS_GRAPH_CERT_PATH": "/run/secrets/mobiliti-graph/client-cert.pem",
        "MS_GRAPH_CERT_THUMBPRINT": "a1b2c3",
        "SHAREPOINT_HOSTNAME": "contoso.sharepoint.com",
        "SHAREPOINT_SITE_PATH": "/sites/catalogs",
        "SHAREPOINT_DRIVE_NAME": "Documents",
        "SHAREPOINT_CATALOG_ROOT": "suppliers",
    }


def _active_catalog_r2_env():
    return _active_catalog_env() | {
        "CATALOG_ASSET_STORAGE_PROVIDER": "r2",
        "CATALOG_ASSET_R2_ACCOUNT_ID": "catalog-account",
        "CATALOG_ASSET_R2_ENDPOINT_URL": "https://catalog-account.r2.cloudflarestorage.com",
        "CATALOG_ASSET_R2_ACCESS_KEY_ID": "catalog-access",
        "CATALOG_ASSET_R2_SECRET_ACCESS_KEY": "catalog-secret",
        "CATALOG_ASSET_R2_BUCKET": "catalog-assets",
        "CATALOG_ASSET_R2_REGION": "auto",
        "CATALOG_ASSET_PUBLIC_BASE_URL": "https://assets.example.test",
    }


def _bash_path(path):
    if os.name != "nt":
        return str(path)
    drive, tail = os.path.splitdrive(str(path))
    return f"/{drive[0].lower()}{tail.replace(chr(92), '/') }"


def test_hetzner_deploy_uses_immutable_release_worktrees():
    deploy = (ROOT / "deploy" / "hetzner" / "deploy.sh").read_text(encoding="utf-8")

    assert 'worktree add --detach "${RELEASE_DIR}" "${TARGET_COMMIT}"' in deploy
    assert 'RELEASES_DIR="${RELEASES_DIR:-/opt/mobiliti-worker/releases}"' in deploy
    assert 'WORKER_IMAGE_TAG="${TARGET_COMMIT}"' in deploy
    assert 'docker rename "${ACTIVE_CONTAINER}" "${BACKUP_CONTAINER}"' in deploy
    assert "restore_previous_worker" in deploy
    assert 'docker rename "${BACKUP_CONTAINER}" "${ACTIVE_CONTAINER}"' in deploy
    assert "docker container rm" not in deploy
    assert "docker rm" not in deploy
    assert "git reset --hard" not in deploy
    assert "rm -rf" not in deploy


def test_hetzner_bootstrap_refuses_to_replace_existing_non_git_directory():
    bootstrap = (ROOT / "deploy" / "hetzner" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )

    assert "Refusing to replace non-git path" in bootstrap
    assert "git reset --hard" not in bootstrap
    assert "rm -rf" not in bootstrap


def test_hetzner_worker_mounts_graph_credentials_read_only():
    compose = (ROOT / "deploy" / "hetzner" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "/etc/mobiliti-worker/graph:/run/secrets/mobiliti-graph:ro" in compose


def test_hetzner_bootstrap_creates_graph_credentials_directory_without_a_certificate():
    bootstrap = (ROOT / "deploy" / "hetzner" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )

    assert 'install -d -o root -g 10001 -m 0750 "${ENV_DIR}/graph"' in bootstrap
    assert "client-cert.pem" not in bootstrap
    wrapper = bootstrap[bootstrap.index("cat >/usr/local/bin/mobiliti-worker-deploy") :]
    fetch = wrapper.index('git -C "${APP_DIR}" fetch')
    assert "preflight.py" not in wrapper[:fetch]


def test_worker_example_disables_catalog_sync_and_uses_container_certificate_path():
    example = (ROOT / "deploy" / "hetzner" / "worker.env.example").read_text(
        encoding="utf-8"
    )

    assert "MS_GRAPH_CERT_PATH=/run/secrets/mobiliti-graph/client-cert.pem" in example
    assert "SUPABASE_SERVICE_KEY=" in example
    assert "CATALOG_ASSET_PUBLIC_BASE_URL=" in example
    assert "CATALOG_ASSET_STORAGE_PROVIDER=supabase" in example
    assert "CATALOG_ASSET_R2_BUCKET=catalog-assets" in example
    assert "CATALOG_SYNC_ENABLED=false" in example


def test_catalog_r2_credentials_are_server_only_and_separate_from_quote_storage():
    server = (ROOT / "mobiliti_saas" / ".env.example").read_text(encoding="utf-8")
    web = (ROOT / "mobiliti_saas" / "web" / ".env.example").read_text(encoding="utf-8")
    worker = (ROOT / "deploy" / "hetzner" / "worker.env.example").read_text(encoding="utf-8")

    for contents in (server, worker):
        assert "CATALOG_ASSET_R2_ACCESS_KEY_ID=" in contents
        assert "CATALOG_ASSET_R2_SECRET_ACCESS_KEY=" in contents
        assert "CATALOG_ASSET_R2_BUCKET=catalog-assets" in contents
        assert "R2_BUCKET=quote-files" in contents
    assert "CATALOG_ASSET_STORAGE_PROVIDER=" in web
    assert "CATALOG_ASSET_PUBLIC_BASE_URL=" in web
    assert "CATALOG_ASSET_R2_ACCESS_KEY_ID" not in web
    assert "CATALOG_ASSET_R2_SECRET_ACCESS_KEY" not in web


def test_catalog_sync_preflight_allows_disabled_sync_without_graph_credentials():
    preflight = _preflight_module()

    for disabled in ("", "0", "false", "no"):
        preflight.validate_catalog_sync({"CATALOG_SYNC_ENABLED": disabled})


@pytest.mark.parametrize("invalid", ("enabled", "on", "2", "tru"))
def test_catalog_sync_preflight_rejects_unknown_enabled_value(invalid):
    preflight = _preflight_module()

    with pytest.raises(preflight.PreflightError, match="CATALOG_SYNC_ENABLED"):
        preflight.validate_catalog_sync({"CATALOG_SYNC_ENABLED": invalid})


@pytest.mark.parametrize(
    "missing",
    (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "CATALOG_ENABLED_SUPPLIERS",
        "MS_GRAPH_TENANT_ID",
        "MS_GRAPH_CLIENT_ID",
        "MS_GRAPH_CERT_PATH",
        "MS_GRAPH_CERT_THUMBPRINT",
        "SHAREPOINT_HOSTNAME",
        "SHAREPOINT_SITE_PATH",
        "SHAREPOINT_DRIVE_NAME",
        "SHAREPOINT_CATALOG_ROOT",
    ),
)
def test_catalog_sync_preflight_rejects_each_missing_active_setting(missing):
    preflight = _preflight_module()
    values = _active_catalog_env()
    values[missing] = ""

    with pytest.raises(preflight.PreflightError, match=missing):
        preflight.validate_catalog_sync(
            values, host_directory=_Directory(), certificate=_Certificate()
        )


@pytest.mark.parametrize(
    "certificate",
    (
        _Certificate(symlink=True),
        _Certificate(regular=False),
        _Certificate(size=0),
        _Certificate(uid=10001),
        _Certificate(gid=0),
        _Certificate(mode=0o444),
    ),
)
def test_catalog_sync_preflight_rejects_insecure_certificate(certificate):
    preflight = _preflight_module()

    with pytest.raises(preflight.PreflightError, match="certificate"):
        preflight.validate_catalog_sync(
            _active_catalog_env(), host_directory=_Directory(), certificate=certificate
        )


@pytest.mark.parametrize(
    "directory",
    (
        _Directory(symlink=True),
        _Directory(directory=False),
        _Directory(uid=10001),
        _Directory(gid=0),
        _Directory(mode=0o755),
    ),
)
def test_catalog_sync_preflight_rejects_insecure_host_directory(directory):
    preflight = _preflight_module()

    with pytest.raises(preflight.PreflightError, match="host directory"):
        preflight.validate_catalog_sync(
            _active_catalog_env(), host_directory=directory, certificate=_Certificate()
        )


def test_catalog_sync_preflight_rejects_invalid_or_duplicate_suppliers():
    preflight = _preflight_module()

    for suppliers in ("sunon,sunon", "sunon,unknown"):
        values = _active_catalog_env() | {"CATALOG_ENABLED_SUPPLIERS": suppliers}
        with pytest.raises(preflight.PreflightError, match="CATALOG_ENABLED_SUPPLIERS"):
            preflight.validate_catalog_sync(
                values, host_directory=_Directory(), certificate=_Certificate()
            )


def test_catalog_sync_preflight_accepts_valid_sunon_configuration():
    preflight = _preflight_module()

    preflight.validate_catalog_sync(
        _active_catalog_env(), host_directory=_Directory(), certificate=_Certificate()
    )


def test_catalog_sync_preflight_accepts_complete_catalog_r2_configuration():
    preflight = _preflight_module()

    preflight.validate_catalog_sync(
        _active_catalog_r2_env(), host_directory=_Directory(), certificate=_Certificate()
    )


@pytest.mark.parametrize(
    "missing",
    (
        "CATALOG_ASSET_R2_ACCOUNT_ID",
        "CATALOG_ASSET_R2_ENDPOINT_URL",
        "CATALOG_ASSET_R2_ACCESS_KEY_ID",
        "CATALOG_ASSET_R2_SECRET_ACCESS_KEY",
        "CATALOG_ASSET_R2_BUCKET",
        "CATALOG_ASSET_R2_REGION",
        "CATALOG_ASSET_PUBLIC_BASE_URL",
    ),
)
def test_catalog_sync_preflight_rejects_incomplete_catalog_r2_configuration(missing):
    preflight = _preflight_module()
    values = _active_catalog_r2_env()
    values[missing] = ""

    with pytest.raises(preflight.PreflightError, match=missing):
        preflight.validate_catalog_sync(
            values, host_directory=_Directory(), certificate=_Certificate()
        )


@pytest.mark.parametrize(
    "provider,public_base",
    (
        ("unknown", "https://assets.example.test"),
        ("r2", "https://assets.example.test/path"),
        ("r2", "https://catalog.r2.dev"),
    ),
)
def test_catalog_sync_preflight_rejects_unknown_provider_or_invalid_public_base(
    provider, public_base
):
    preflight = _preflight_module()
    values = _active_catalog_r2_env() | {
        "CATALOG_ASSET_STORAGE_PROVIDER": provider,
        "CATALOG_ASSET_PUBLIC_BASE_URL": public_base,
    }

    with pytest.raises(preflight.PreflightError, match="CATALOG_ASSET"):
        preflight.validate_catalog_sync(
            values, host_directory=_Directory(), certificate=_Certificate()
        )


def test_provision_passes_catalog_r2_settings_without_quote_fallback():
    provision = (ROOT / "deploy" / "hetzner" / "provision.ps1").read_text(encoding="utf-8")

    for name in (
        "CatalogAssetStorageProvider",
        "CatalogAssetR2AccountId",
        "CatalogAssetR2EndpointUrl",
        "CatalogAssetR2AccessKeyId",
        "CatalogAssetR2SecretAccessKey",
        "CatalogAssetPublicBaseUrl",
    ):
        assert f"${name}" in provision
    assert "CATALOG_ASSET_R2_BUCKET=catalog-assets" in provision
    assert "CATALOG_ASSET_R2_ACCESS_KEY_ID=$CatalogAssetR2AccessKeyId" in provision
    assert "CATALOG_ASSET_R2_SECRET_ACCESS_KEY=$CatalogAssetR2SecretAccessKey" in provision


@pytest.mark.parametrize(
    "contents",
    (
        "BROKEN",
        "export CATALOG_SYNC_ENABLED=false",
        "CATALOG_SYNC_ENABLED=false\nCATALOG_SYNC_ENABLED=true",
    ),
)
def test_catalog_sync_preflight_rejects_malformed_or_duplicate_env(tmp_path, contents):
    preflight = _preflight_module()
    env_file = tmp_path / "worker.env"
    env_file.write_text(contents, encoding="utf-8")

    with pytest.raises(preflight.PreflightError, match="worker.env"):
        preflight.read_env_file(env_file)


def test_deploy_runs_target_catalog_preflight_after_fetch_and_before_build():
    deploy = (ROOT / "deploy" / "hetzner" / "deploy.sh").read_text(encoding="utf-8")

    fetch = deploy.index('git -C "${APP_DIR}" fetch')
    target_preflight = deploy.index(':deploy/hetzner/preflight.py')
    worktree = deploy.index("worktree add")
    build = deploy.index("docker compose")

    assert fetch < target_preflight < worktree < build
    assert (
        'git -C "${APP_DIR}" show "${TARGET_COMMIT}:deploy/hetzner/preflight.py"'
        in deploy
    )


def test_deploy_ensures_exact_graph_host_directory_before_target_preflight():
    deploy = (ROOT / "deploy" / "hetzner" / "deploy.sh").read_text(encoding="utf-8")

    assert 'GRAPH_HOST_DIR="/etc/mobiliti-worker/graph"' in deploy
    ensure = deploy.index('install -d -o root -g 10001 -m 0750 "${GRAPH_HOST_DIR}"')
    preflight = deploy.index(':deploy/hetzner/preflight.py')
    assert ensure < preflight


def test_worker_container_has_read_only_runtime_hardening():
    compose = (ROOT / "deploy" / "hetzner" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "read_only: true" in compose
    assert "- ALL" in compose
    assert "- no-new-privileges:true" in compose


@pytest.mark.parametrize(
    ("preflight_exit", "expected_calls"),
    (
        (1, ["fetch", "show", "preflight"]),
        (0, ["fetch", "show", "preflight", "worktree"]),
    ),
)
def test_first_rollout_runs_target_preflight_after_fetch_without_build(
    tmp_path, preflight_exit, expected_calls
):
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if not bash:
        pytest.skip("bash is required to exercise the deploy preflight")

    app = tmp_path / "app"
    deploy_dir = app / "deploy" / "hetzner"
    deploy_dir.mkdir(parents=True)
    (app / ".git").mkdir()
    assert not (deploy_dir / "preflight.py").exists()
    deploy = (ROOT / "deploy" / "hetzner" / "deploy.sh").read_text(encoding="utf-8")
    root_guard = '''if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo mobiliti-worker-deploy" >&2
  exit 1
fi

'''
    assert root_guard in deploy
    script = deploy_dir / "deploy.sh"
    script.write_text(
        deploy.replace(root_guard, 'PATH="${DEPLOY_TEST_BIN}:${PATH}"\n'),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    for name, body in {
        "python3": (
            'payload="$(cat)"\n'
            '[[ "$payload" == "raise SystemExit(0)" ]] || exit 31\n'
            'printf "preflight\\n" >> "$CALL_LOG"\n'
            'exit "$PREFLIGHT_EXIT"\n'
        ),
        "git": (
            'case "$*" in\n'
            '  *fetch*) printf "fetch\\n" >> "$CALL_LOG"; exit 0;;\n'
            '  *"rev-parse FETCH_HEAD"*) printf "0123456789abcdef0123456789abcdef01234567\\n"; exit 0;;\n'
            '  *"show 0123456789abcdef0123456789abcdef01234567:deploy/hetzner/preflight.py"*) '
            'printf "show\\n" >> "$CALL_LOG"; printf "raise SystemExit(0)\\n"; exit 0;;\n'
            '  *"worktree add"*) printf "worktree\\n" >> "$CALL_LOG"; exit 23;;\n'
            'esac\nexit 24\n'
        ),
        "install": "exit 0\n",
        "docker": 'printf "docker\\n" >> "$CALL_LOG"\nexit 25\n',
    }.items():
        command = bin_dir / name
        command.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
        command.chmod(0o755)

    env_file = tmp_path / "worker.env"
    values = _active_catalog_env() | {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "MOBILITI_REST_SECRET": "test-rest-secret",
        "QUOTE_STORAGE_BUCKET": "quote-files",
    }
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")
    env = os.environ | {
        "APP_DIR": _bash_path(app),
        "ENV_FILE": _bash_path(env_file),
        "RELEASES_DIR": _bash_path(tmp_path / "releases"),
        "CALL_LOG": _bash_path(call_log),
        "DEPLOY_TEST_BIN": _bash_path(bin_dir),
        "PREFLIGHT_EXIT": str(preflight_exit),
    }

    result = subprocess.run(
        [bash, _bash_path(script)], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert call_log.read_text(encoding="utf-8").splitlines() == expected_calls, result.stderr
    assert "docker" not in call_log.read_text(encoding="utf-8")
