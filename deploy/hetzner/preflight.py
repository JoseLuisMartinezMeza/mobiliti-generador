"""Validacion acotada de catalogos para el despliegue del worker."""

import argparse
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_CERTIFICATE_PATH = "/run/secrets/mobiliti-graph/client-cert.pem"
HOST_DIRECTORY = Path("/etc/mobiliti-worker/graph")
HOST_CERTIFICATE = Path("/etc/mobiliti-worker/graph/client-cert.pem")
_ENABLED = {"1", "true", "yes"}
_DISABLED = {"", "0", "false", "no"}
_REQUIRED = (
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
)
_CATALOG_R2_REQUIRED = (
    "CATALOG_ASSET_R2_ACCOUNT_ID",
    "CATALOG_ASSET_R2_ENDPOINT_URL",
    "CATALOG_ASSET_R2_ACCESS_KEY_ID",
    "CATALOG_ASSET_R2_SECRET_ACCESS_KEY",
    "CATALOG_ASSET_R2_BUCKET",
    "CATALOG_ASSET_R2_REGION",
    "CATALOG_ASSET_PUBLIC_BASE_URL",
)
_SUPPLIERS = {
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
    "idelika", "conceptos",
    "labenze", "requiez",
}
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class PreflightError(ValueError):
    """Configuracion insegura o incompleta antes de activar catalogos."""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not _KEY.fullmatch(key) or key in values:
            raise PreflightError("invalid worker.env assignment")
        values[key] = value.strip()
    return values


def _validate_certificate(certificate: Path) -> None:
    if certificate.is_symlink() or not certificate.is_file():
        raise PreflightError("certificate must be a regular file")
    details = certificate.stat()
    if details.st_size <= 0:
        raise PreflightError("certificate must not be empty")
    if details.st_uid != 0 or details.st_gid != 10001:
        raise PreflightError("certificate owner or group is invalid")
    if stat.S_IMODE(details.st_mode) != 0o440:
        raise PreflightError("certificate mode is invalid")


def _validate_host_directory(directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise PreflightError("host directory must be a directory, not a symlink")
    details = directory.stat()
    if details.st_uid != 0 or details.st_gid != 10001:
        raise PreflightError("host directory owner or group is invalid")
    if stat.S_IMODE(details.st_mode) != 0o750:
        raise PreflightError("host directory mode is invalid")


def _validate_https_origin(value: str, name: str, *, reject_r2_dev=False) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise PreflightError(f"{name} is invalid") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not host
        or host != host.lower()
        or parsed.netloc != host
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (reject_r2_dev and (host == "r2.dev" or host.endswith(".r2.dev")))
    ):
        raise PreflightError(f"{name} is invalid")


def validate_catalog_sync(
    values: dict[str, str],
    host_directory: Path | None = None,
    certificate: Path | None = None,
) -> None:
    enabled = values.get("CATALOG_SYNC_ENABLED", "").strip().lower()
    if enabled not in _ENABLED | _DISABLED:
        raise PreflightError("CATALOG_SYNC_ENABLED is invalid")
    if enabled in _DISABLED:
        return

    for key in _REQUIRED:
        if not values.get(key, "").strip():
            raise PreflightError(f"missing {key}")
    _validate_https_origin(values["SUPABASE_URL"], "SUPABASE_URL")
    provider = values.get("CATALOG_ASSET_STORAGE_PROVIDER", "supabase").strip().lower()
    if provider not in {"supabase", "r2"}:
        raise PreflightError("CATALOG_ASSET_STORAGE_PROVIDER is invalid")
    public_base = values.get("CATALOG_ASSET_PUBLIC_BASE_URL", "").strip()
    if public_base:
        _validate_https_origin(
            public_base, "CATALOG_ASSET_PUBLIC_BASE_URL", reject_r2_dev=True
        )
    if provider == "r2":
        for key in _CATALOG_R2_REQUIRED:
            if not values.get(key, "").strip():
                raise PreflightError(f"missing {key}")
        if values["CATALOG_ASSET_R2_BUCKET"] != "catalog-assets":
            raise PreflightError("CATALOG_ASSET_R2_BUCKET is invalid")
        _validate_https_origin(
            values["CATALOG_ASSET_R2_ENDPOINT_URL"],
            "CATALOG_ASSET_R2_ENDPOINT_URL",
        )
    if values["MS_GRAPH_CERT_PATH"] != EXPECTED_CERTIFICATE_PATH:
        raise PreflightError("MS_GRAPH_CERT_PATH is invalid")

    suppliers = values["CATALOG_ENABLED_SUPPLIERS"].split(",")
    if (
        not suppliers
        or any(not supplier or supplier.strip() != supplier or supplier not in _SUPPLIERS for supplier in suppliers)
        or len(set(suppliers)) != len(suppliers)
    ):
        raise PreflightError("CATALOG_ENABLED_SUPPLIERS is invalid")

    _validate_host_directory(host_directory or HOST_DIRECTORY)
    _validate_certificate(certificate or HOST_CERTIFICATE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--host-directory", type=Path, default=HOST_DIRECTORY)
    parser.add_argument("--certificate", type=Path, default=HOST_CERTIFICATE)
    args = parser.parse_args()
    try:
        validate_catalog_sync(
            read_env_file(args.env_file),
            host_directory=args.host_directory,
            certificate=args.certificate,
        )
    except (OSError, PreflightError) as error:
        print(f"Catalog sync preflight failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
