"""
Worker para procesar cotizaciones web.

Default/final: QUOTE_ENGINE=python, sin Microsoft Excel.
"""

from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BUCKET = os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files")
STORAGE_PROVIDER = os.environ.get("QUOTE_STORAGE_PROVIDER", "supabase").strip().lower()
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "").strip().rstrip("/")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.environ.get("R2_BUCKET", BUCKET).strip() or BUCKET
R2_REGION = os.environ.get("R2_REGION", "auto").strip() or "auto"
POLL_SECONDS = int(os.environ.get("WORKER_POLL_SECONDS", "10"))
STALE_MINUTES = int(os.environ.get("WORKER_STALE_MINUTES", "30"))
MAX_QUOTE_OUTPUT_MB = int(os.environ.get("MAX_QUOTE_OUTPUT_MB", "100"))
QUOTE_ENGINE = os.environ.get("QUOTE_ENGINE", "python").strip().lower()
DATABASE_URL = os.environ.get("DATABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")
MOBILITI_REST_SECRET = os.environ.get("MOBILITI_REST_SECRET")
MOBILITI_API_URL = os.environ.get("MOBILITI_API_URL", "https://web-lemon-one-45.vercel.app").strip().rstrip("/")
DEV_MODE = os.environ.get("MOBILITI_DEV_MODE", "").lower() in {"1", "true", "yes"}
TARKETT_CART_SOURCE_TYPE = "tarkett_cart"
OFFIHO_CART_SOURCE_TYPE = "offiho_cart"
SUPPLIER_CART_SOURCE_TYPE = "supplier_cart"
JSON_CART_SOURCE_TYPES = frozenset(
    {TARKETT_CART_SOURCE_TYPE, OFFIHO_CART_SOURCE_TYPE, SUPPLIER_CART_SOURCE_TYPE}
)
SUPPLIER_LABELS = {
    "cr-global": "CR Global",
    "sonara": "Sonara",
    "sunon": "Sunon",
    "alma": "ALMA",
    "lumbro": "Lumbro",
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEV_STORE_DIR = Path(os.environ.get("MOBILITI_DEV_STORE_DIR", PROJECT_ROOT / ".mobiliti_dev_store")).resolve()
TARKETT_SYNC_ENABLED = os.environ.get("TARKETT_SYNC_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
TARKETT_SYNC_INTERVAL_SECONDS = max(900, int(os.environ.get("TARKETT_SYNC_INTERVAL_SECONDS", "21600")))
TARKETTNET_EMAIL = os.environ.get("TARKETTNET_EMAIL", "").strip()
TARKETTNET_PASSWORD = os.environ.get("TARKETTNET_PASSWORD", "")
TARKETT_CATALOG_FALLBACK_PATH = PROJECT_ROOT / "mobiliti_saas" / "quote_engine" / "data" / "tarkett_catalog.json"
_TARKETT_LAST_SYNC_ATTEMPT = 0.0

from mobiliti_saas.quote_engine.tarkettnet_catalog import sync_catalog_from_tarkettnet  # noqa: E402


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta variable de entorno requerida: {name}")
    return value


def _format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def _validate_output_size(source: Path) -> None:
    max_bytes = MAX_QUOTE_OUTPUT_MB * 1024 * 1024
    size = source.stat().st_size
    if size > max_bytes:
        raise RuntimeError(
            "Cotizacion generada pesa "
            f"{_format_size_mb(size)} y supera el limite de Storage de {MAX_QUOTE_OUTPUT_MB} MB"
        )


_R2_CLIENT = None


def _use_r2_storage() -> bool:
    return STORAGE_PROVIDER in {"r2", "cloudflare-r2", "cloudflare"}


def _provider_uses_r2(provider: str | None) -> bool:
    return str(provider or "").strip().lower() in {"r2", "cloudflare-r2", "cloudflare"}


def _r2_endpoint_url() -> str:
    if R2_ENDPOINT_URL:
        return R2_ENDPOINT_URL
    if R2_ACCOUNT_ID:
        return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return ""


def _r2_configured() -> bool:
    return bool(_r2_endpoint_url() and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET)


def _r2_client():
    global _R2_CLIENT
    if _R2_CLIENT is not None:
        return _R2_CLIENT
    if not _r2_configured():
        raise RuntimeError(
            "Cloudflare R2 no configurado: define R2_ACCOUNT_ID/R2_ENDPOINT_URL, "
            "R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY y R2_BUCKET"
        )
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("Falta dependencia boto3 para Cloudflare R2") from exc
    _R2_CLIENT = boto3.client(
        "s3",
        endpoint_url=_r2_endpoint_url(),
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name=R2_REGION,
        config=Config(signature_version="s3v4"),
    )
    return _R2_CLIENT


def _quote_object_content_type(path: str) -> str:
    return (
        "application/pdf"
        if str(path or "").lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


class SupabaseClient:
    def __init__(self) -> None:
        self.base_url = _required_env("SUPABASE_URL").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_KEY") or SUPABASE_ANON_KEY
        if not self.key:
            raise RuntimeError("Falta SUPABASE_SERVICE_KEY o SUPABASE_ANON_KEY")

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": content_type,
            "Prefer": "return=representation",
            **({"x-mobiliti-rest-secret": MOBILITI_REST_SECRET} if MOBILITI_REST_SECRET else {}),
        }

    def rest(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        url = f"{self.base_url}/rest/v1{path}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=payload, method=method)
        for key, value in self._headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Supabase REST {exc.code}: {body}") from exc

    def catalog_snapshot_get(self, supplier: str) -> dict | None:
        if not os.environ.get("SUPABASE_SERVICE_KEY") and MOBILITI_REST_SECRET:
            return self._catalog_api_request("GET")
        rows = self.rest(
            "GET",
            "/saas_supplier_catalog_snapshots",
            params={
                "supplier": f"eq.{supplier}",
                "select": "supplier,source_hash,generated_at,payload,updated_at",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def catalog_snapshot_upsert(self, supplier: str, payload: dict) -> dict:
        if not os.environ.get("SUPABASE_SERVICE_KEY") and MOBILITI_REST_SECRET:
            return self._catalog_api_request("PUT", {"payload": payload})
        url = f"{self.base_url}/rest/v1/saas_supplier_catalog_snapshots?on_conflict=supplier"
        row = {
            "supplier": supplier,
            "source_hash": payload["source_hash"],
            "generated_at": payload["generated_at"],
            "payload": payload,
            "updated_at": _utc_now(),
        }
        req = urllib.request.Request(url, data=json.dumps(row).encode("utf-8"), method="POST")
        for key, value in self._headers().items():
            req.add_header(key, value)
        req.add_header("Prefer", "resolution=merge-duplicates,return=representation")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                rows = json.loads(body) if body else []
                return rows[0] if isinstance(rows, list) and rows else row
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Supabase catalog snapshot {exc.code}: {body}") from exc

    def _catalog_api_request(self, method: str, payload: dict | None = None) -> dict | None:
        parsed = urllib.parse.urlparse(MOBILITI_API_URL)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("MOBILITI_API_URL invalida")
        url = f"{MOBILITI_API_URL}/internal/catalogs/tarkett"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("x-mobiliti-rest-secret", MOBILITI_REST_SECRET or "")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = resp.read().decode("utf-8")
                return json.loads(content) if content else None
        except urllib.error.HTTPError as exc:
            exc.read()
            raise RuntimeError(f"Mobiliti catalog API {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Mobiliti catalog API connection error: {exc.reason}") from exc

    def storage_download(self, object_path: str, dest: Path) -> None:
        self.storage_download_from_provider(object_path, dest, STORAGE_PROVIDER)

    def storage_download_from_provider(self, object_path: str, dest: Path, provider: str | None) -> None:
        if _provider_uses_r2(provider):
            try:
                obj = _r2_client().get_object(Bucket=R2_BUCKET, Key=str(object_path).strip("/"))
                dest.write_bytes(obj["Body"].read())
            except Exception as exc:
                raise RuntimeError(f"R2 download error: {exc.__class__.__name__}") from exc
            return

        encoded = urllib.parse.quote(object_path, safe="/")
        url = f"{self.base_url}/storage/v1/object/{BUCKET}/{encoded}"
        req = urllib.request.Request(url, method="GET")
        for key, value in self._headers("application/octet-stream").items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                dest.write_bytes(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Storage download {exc.code}: {body}") from exc

    def storage_upload(self, object_path: str, source: Path) -> None:
        if _use_r2_storage():
            try:
                _r2_client().put_object(
                    Bucket=R2_BUCKET,
                    Key=str(object_path).strip("/"),
                    Body=source.read_bytes(),
                    ContentType=_quote_object_content_type(object_path),
                )
            except Exception as exc:
                raise RuntimeError(f"R2 upload error: {exc.__class__.__name__}") from exc
            return

        encoded = urllib.parse.quote(object_path, safe="/")
        url = f"{self.base_url}/storage/v1/object/{BUCKET}/{encoded}"
        req = urllib.request.Request(url, data=source.read_bytes(), method="PUT")
        for key, value in self._headers("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").items():
            req.add_header(key, value)
        req.add_header("x-upsert", "true")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Storage upload {exc.code}: {body}") from exc

    def storage_delete(self, object_path: str) -> None:
        self.storage_delete_from_provider(object_path, STORAGE_PROVIDER)

    def storage_delete_from_provider(self, object_path: str, provider: str | None) -> None:
        clean_path = str(object_path or "").strip().lstrip("/")
        if not clean_path:
            return
        if _provider_uses_r2(provider):
            try:
                _r2_client().delete_object(Bucket=R2_BUCKET, Key=clean_path)
            except Exception as exc:
                raise RuntimeError(f"R2 delete error: {exc.__class__.__name__}") from exc
            return

        url = f"{self.base_url}/storage/v1/object/{BUCKET}"
        req = urllib.request.Request(url, data=json.dumps({"prefixes": [clean_path]}).encode("utf-8"), method="DELETE")
        for key, value in self._headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            raise RuntimeError(f"Storage delete {exc.code}: {body}") from exc


class PostgresClient(SupabaseClient):
    def __init__(self) -> None:
        super().__init__()
        self.db_url = _required_env("DATABASE_URL")

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [_jsonable_row(row) for row in rows]

    def _write(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Falta dependencia psycopg para DATABASE_URL") from exc
        adapted = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, adapted)
                rows = cur.fetchall()
            conn.commit()
        return [_jsonable_row(row) for row in rows]

    def rest(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        params = params or {}
        data = data or {}
        if method == "GET" and path == "/saas_quote_jobs":
            limit = int(params.get("limit", "1") or 1)
            status = str(params.get("status", "eq.queued")).split(".", 1)[1]
            return self._rows(
                "SELECT * FROM saas_quote_jobs WHERE status = %s ORDER BY created_at ASC LIMIT %s",
                (status, limit),
            )

        if method == "PATCH" and path == "/saas_quote_jobs":
            status = str(params.get("status", "eq.processing")).split(".", 1)[1]
            updated_raw = str(params.get("updated_at", ""))
            if not updated_raw.startswith("lt."):
                raise RuntimeError("PATCH saas_quote_jobs requiere updated_at lt.")
            cutoff = updated_raw.split(".", 1)[1]
            return self._update_jobs(
                data,
                "status = %s AND updated_at < %s",
                (status, cutoff),
            )

        if method == "PATCH" and path.startswith("/saas_quote_jobs?id=eq."):
            filters = _parse_eq_filters(path)
            where = ["id = %s"]
            values = [filters["id"]]
            if "status" in filters:
                where.append("status = %s")
                values.append(filters["status"])
            return self._update_jobs(data, " AND ".join(where), tuple(values))

        raise RuntimeError(f"Operacion Postgres no soportada: {method} {path}")

    def catalog_snapshot_get(self, supplier: str) -> dict | None:
        rows = self._rows(
            """
            SELECT supplier, source_hash, generated_at, payload, updated_at
            FROM saas_supplier_catalog_snapshots
            WHERE supplier = %s
            LIMIT 1
            """,
            (supplier,),
        )
        return rows[0] if rows else None

    def catalog_snapshot_upsert(self, supplier: str, payload: dict) -> dict:
        rows = self._write(
            """
            INSERT INTO saas_supplier_catalog_snapshots
                (supplier, source_hash, generated_at, payload, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (supplier) DO UPDATE SET
                source_hash = EXCLUDED.source_hash,
                generated_at = EXCLUDED.generated_at,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING supplier, source_hash, generated_at, payload, updated_at
            """,
            (supplier, payload["source_hash"], payload["generated_at"], payload),
        )
        return rows[0]

    def _update_jobs(self, data: dict, where_sql: str, where_params: tuple) -> list[dict]:
        payload = dict(data)
        if not payload:
            return []
        set_clause = ", ".join(f"{key} = %s" for key in payload.keys())
        return self._write(
            f"UPDATE saas_quote_jobs SET {set_clause} WHERE {where_sql} RETURNING *",
            tuple(payload.values()) + where_params,
        )


def _jsonable_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    clean = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            clean[key] = value.isoformat()
        else:
            clean[key] = value
    return clean


def _parse_eq_filters(path: str) -> dict[str, str]:
    query = path.split("?", 1)[0]
    if "?" in path:
        query = path.split("?", 1)[1]
    elif "id=eq." in path:
        query = path.split("/saas_quote_jobs?", 1)[-1]
    filters: dict[str, str] = {}
    for part in query.split("&"):
        if "=eq." in part:
            key, value = part.split("=eq.", 1)
            filters[key] = value
    if "id" not in filters and "id=eq." in path:
        filters["id"] = path.split("id=eq.", 1)[1].split("&", 1)[0]
    return filters


class LocalDevClient:
    def __init__(self) -> None:
        self.db_path = DEV_STORE_DIR / "db.json"
        self.storage_root = DEV_STORE_DIR / "storage" / BUCKET

    def _load(self) -> dict:
        if not self.db_path.exists():
            raise RuntimeError("Store dev no existe. Inicia backend con MOBILITI_DEV_MODE=1 y haz login/upload primero.")
        return json.loads(self.db_path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        DEV_STORE_DIR.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def rest(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        store = self._load()
        if path == "/saas_quote_jobs" and method == "GET":
            rows = list(store.get("quote_jobs", []))
            status_filter = (params or {}).get("status")
            if isinstance(status_filter, str) and status_filter.startswith("eq."):
                wanted = status_filter.split(".", 1)[1]
                rows = [row for row in rows if row.get("status") == wanted]
            rows.sort(key=lambda row: row.get("created_at", ""))
            limit = int((params or {}).get("limit", len(rows)) or len(rows))
            return rows[:limit]

        if path == "/saas_quote_jobs" and method == "PATCH":
            params = params or {}
            rows = []
            for row in store.get("quote_jobs", []):
                if params.get("status") and row.get("status") != params["status"].split(".", 1)[1]:
                    continue
                updated_filter = params.get("updated_at")
                if isinstance(updated_filter, str) and updated_filter.startswith("lt."):
                    cutoff = datetime.fromisoformat(updated_filter.split(".", 1)[1])
                    updated_at = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))
                    if updated_at >= cutoff:
                        continue
                row.update(data or {})
                rows.append(dict(row))
            if rows:
                self._save(store)
            return rows

        if path.startswith("/saas_quote_jobs?id=eq.") and method == "PATCH":
            filters = {}
            for part in path.split("?", 1)[0].split("&"):
                if "=eq." in part:
                    key, value = part.split("=eq.", 1)
                    filters[key.rsplit("?", 1)[-1]] = value
            job_id = filters.get("id") or path.split("id=eq.", 1)[1].split("&", 1)[0]
            for row in store.get("quote_jobs", []):
                if row["id"] == job_id and all(str(row.get(k)) == str(v) for k, v in filters.items() if k != "id"):
                    row.update(data or {})
                    self._save(store)
                    return [{"id": job_id, **row}]
            return []

        raise RuntimeError(f"Operacion dev no soportada: {method} {path}")

    def catalog_snapshot_get(self, supplier: str) -> dict | None:
        return self._load().get("supplier_catalog_snapshots", {}).get(supplier)

    def catalog_snapshot_upsert(self, supplier: str, payload: dict) -> dict:
        store = self._load()
        row = {
            "supplier": supplier,
            "source_hash": payload["source_hash"],
            "generated_at": payload["generated_at"],
            "payload": payload,
            "updated_at": _utc_now(),
        }
        store.setdefault("supplier_catalog_snapshots", {})[supplier] = row
        self._save(store)
        return row

    def _storage_file(self, object_path: str) -> Path:
        safe_path = object_path.replace("\\", "/").lstrip("/")
        if ".." in safe_path.split("/"):
            raise RuntimeError("Ruta de storage invalida")
        return self.storage_root / safe_path

    def storage_download(self, object_path: str, dest: Path) -> None:
        source = self._storage_file(object_path)
        if not source.exists():
            raise RuntimeError(f"Archivo no existe en storage dev: {object_path}")
        dest.write_bytes(source.read_bytes())

    def storage_download_from_provider(self, object_path: str, dest: Path, provider: str | None) -> None:
        self.storage_download(object_path, dest)

    def storage_upload(self, object_path: str, source: Path) -> None:
        dest = self._storage_file(object_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())

    def storage_delete(self, object_path: str) -> None:
        self._storage_file(object_path).unlink(missing_ok=True)

    def storage_delete_from_provider(self, object_path: str, provider: str | None) -> None:
        self.storage_delete(object_path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_project_root() -> Path:
    history_dir = PROJECT_ROOT / "versiones historial" / "HISTORIAL DE VERSIONES" / "Mobiliti_Generador_Windows"
    return history_dir if history_dir.exists() else PROJECT_ROOT


def _default_template() -> Path:
    root = _resolve_project_root()
    candidates = [
        root / "Formato Cotización 2026 GDL (1).xlsx",
        root / "Formato Cotizacion 2026 GDL (1).xlsx",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _template_path() -> str:
    return os.environ.get("TEMPLATE_PATH") or str(_default_template())


def _json_job_source_type(job: dict) -> str | None:
    metadata = job.get("metadata") or {}
    value = metadata.get("source_type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _has_supported_json_cart_source_type(job: dict) -> bool:
    return _json_job_source_type(job) in JSON_CART_SOURCE_TYPES


def _has_json_input_hint(job: dict) -> bool:
    input_path = str(job.get("input_path") or "").lower()
    metadata = job.get("metadata") or {}
    original_filename = str(metadata.get("original_filename") or "").lower()
    metadata_extension = str(metadata.get("input_extension") or "").lower()
    return input_path.endswith(".json") or original_filename.endswith(".json") or metadata_extension == ".json"


def _input_extension_for_job(job: dict) -> str:
    input_path = str(job.get("input_path") or "").lower()
    metadata = job.get("metadata") or {}
    original_filename = str(metadata.get("original_filename") or "").lower()
    if _has_json_input_hint(job) or _has_supported_json_cart_source_type(job):
        return ".json"
    if input_path.endswith(".pdf") or original_filename.endswith(".pdf"):
        return ".pdf"
    return ".xlsx"


def _convert_pdf_to_quotation(source_pdf: Path, output_xlsx: Path, reference_xlsx: str | Path) -> None:
    from pdf_quotation_import import convert_pdf_to_quotation

    convert_pdf_to_quotation(source_pdf, output_xlsx, reference_xlsx=reference_xlsx)


def _convert_tarkett_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.tarkett_catalog import create_tarkett_quotation_workbook

    create_tarkett_quotation_workbook(payload, output_xlsx)


def _convert_offiho_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.offiho_catalog import create_offiho_quotation_workbook

    create_offiho_quotation_workbook(payload, output_xlsx)


def _convert_supplier_cart_to_quotation(source_json: Path, output_xlsx: Path, payload: dict) -> None:
    from mobiliti_saas.quote_engine.supplier_catalog import create_supplier_quotation_workbook

    create_supplier_quotation_workbook(payload, output_xlsx)


def _read_cart_payload(source_json: Path) -> dict:
    try:
        payload = json.loads(source_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("JSON de entrada invalido") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("JSON de entrada debe ser un objeto")
    return payload


def _is_json_cart_job(job: dict) -> bool:
    metadata = job.get("metadata") or {}
    return _has_json_input_hint(job) or _has_supported_json_cart_source_type(job) or "source_type" in metadata


def _prepare_generator_input(job: dict, local_input: Path, tmp_dir: Path) -> Path:
    input_extension = _input_extension_for_job(job)
    if _is_json_cart_job(job):
        payload = _read_cart_payload(local_input)
        source_type = payload.get("source_type")
        if not isinstance(source_type, str) or not source_type.strip():
            raise RuntimeError("JSON de entrada sin source_type")
        source_type = source_type.strip()

        metadata = job.get("metadata") or {}
        metadata_source_type = _json_job_source_type(job)
        if metadata_source_type is None:
            raise RuntimeError("source_type de metadata ausente para JSON de entrada")
        if metadata_source_type != source_type:
            raise RuntimeError("source_type de metadata no coincide con JSON de entrada")

        conversions = {
            TARKETT_CART_SOURCE_TYPE: (
                "quotation_from_tarkett.xlsx",
                _convert_tarkett_cart_to_quotation,
                "tarkett_converted",
            ),
            OFFIHO_CART_SOURCE_TYPE: (
                "quotation_from_offiho.xlsx",
                _convert_offiho_cart_to_quotation,
                "offiho_converted",
            ),
            SUPPLIER_CART_SOURCE_TYPE: (
                "quotation_from_supplier.xlsx",
                _convert_supplier_cart_to_quotation,
                "supplier_converted",
            ),
        }
        conversion = conversions.get(source_type)
        if conversion is None:
            raise RuntimeError("Tipo de fuente JSON no soportado")

        output_name, converter, conversion_flag = conversion
        converted_input = tmp_dir / output_name
        converter(local_input, converted_input, payload)
        metadata["input_extension"] = ".json"
        metadata[conversion_flag] = True
        if source_type == SUPPLIER_CART_SOURCE_TYPE:
            supplier = str(payload.get("supplier") or "").strip().lower()
            supplier_label = SUPPLIER_LABELS.get(supplier)
            if supplier_label is None:
                raise RuntimeError("Proveedor de catalogo no soportado")
            metadata.update(
                {
                    "catalog_supplier": supplier,
                    "catalog_supplier_label": supplier_label,
                    "catalog_price_mode": "list_price_net",
                    "base_currency": payload.get("base_currency"),
                    "quote_currency": payload.get("quote_currency"),
                    "exchange_rate": payload.get("exchange_rate"),
                    "rate_source": payload.get("rate_source"),
                    "rate_effective_date": payload.get("rate_effective_date"),
                    "rate_retrieved_at": payload.get("rate_retrieved_at"),
                }
            )
        job["metadata"] = metadata
        return converted_input
    if input_extension != ".pdf":
        return local_input

    converted_input = tmp_dir / "quotation_from_pdf.xlsx"
    _convert_pdf_to_quotation(local_input, converted_input, _template_path())
    metadata = job.get("metadata") or {}
    metadata["input_extension"] = ".pdf"
    metadata["pdf_converted"] = True
    job["metadata"] = metadata
    return converted_input


def _job_input_storage_provider(job: dict) -> str:
    metadata = job.get("metadata") or {}
    return str(
        metadata.get("resolved_input_storage_provider")
        or metadata.get("input_storage_provider")
        or metadata.get("storage_provider")
        or metadata.get("quote_storage_provider")
        or STORAGE_PROVIDER
    ).strip().lower()


def _job_input_storage_provider_candidates(job: dict) -> list[str]:
    metadata = job.get("metadata") or {}
    explicit = (
        metadata.get("resolved_input_storage_provider")
        or metadata.get("input_storage_provider")
        or metadata.get("storage_provider")
        or metadata.get("quote_storage_provider")
    )
    primary = str(explicit or STORAGE_PROVIDER).strip().lower()
    candidates = [primary]
    if not explicit:
        if _provider_uses_r2(primary):
            candidates.append("supabase")
        elif _r2_configured():
            candidates.append("r2")

    unique: list[str] = []
    seen: set[str] = set()
    for provider in candidates:
        if provider and provider not in seen:
            seen.add(provider)
            unique.append(provider)
    return unique or [STORAGE_PROVIDER]


def _set_resolved_input_storage_provider(job: dict, provider: str) -> None:
    metadata = job.get("metadata") or {}
    metadata["resolved_input_storage_provider"] = provider
    job["metadata"] = metadata


def _download_job_input(client: SupabaseClient, job: dict, dest: Path) -> None:
    if hasattr(client, "storage_download_from_provider"):
        errors: list[str] = []
        for provider in _job_input_storage_provider_candidates(job):
            try:
                client.storage_download_from_provider(job["input_path"], dest, provider)
                _set_resolved_input_storage_provider(job, provider)
                return
            except Exception as exc:
                errors.append(f"{provider}: {exc.__class__.__name__}")
        raise RuntimeError(f"No se pudo descargar input desde storage providers: {', '.join(errors)}")
    client.storage_download(job["input_path"], dest)
    _set_resolved_input_storage_provider(job, _job_input_storage_provider(job))


def _delete_job_input(client: SupabaseClient, job: dict) -> None:
    provider = _job_input_storage_provider(job)
    if hasattr(client, "storage_delete_from_provider"):
        client.storage_delete_from_provider(job.get("input_path") or "", provider)
        return
    client.storage_delete(job.get("input_path") or "")


def _run_generator(job: dict, input_path: Path, output_path: Path) -> None:
    metadata = job.get("metadata") or {}
    job["metadata"] = metadata
    engine = QUOTE_ENGINE
    if engine == "auto":
        engine = "python"

    if engine in {"python", "openpyxl", "online"}:
        from online_quote_generator import generate_online_quote

        generate_online_quote(input_path, output_path, metadata, _template_path())
        return
    raise RuntimeError(
        f"QUOTE_ENGINE invalido: {QUOTE_ENGINE}. "
        "La version final SaaS usa QUOTE_ENGINE=python; xlwings quedo archivado en historial."
    )


def fetch_next_job(client: SupabaseClient) -> dict | None:
    rows = client.rest(
        "GET",
        "/saas_quote_jobs",
        params={"status": "eq.queued", "select": "*", "order": "created_at.asc", "limit": "1"},
    )
    return rows[0] if rows else None


def recover_stale_jobs(client: SupabaseClient) -> int:
    if STALE_MINUTES <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES)).isoformat()
    rows = client.rest(
        "PATCH",
        "/saas_quote_jobs",
        params={"status": "eq.processing", "updated_at": f"lt.{cutoff}"},
        data={
            "status": "queued",
            "error_message": "Reintentado automaticamente: worker anterior quedo stale",
            "updated_at": _utc_now(),
        },
    )
    if rows:
        print(f"Jobs stale reencolados: {len(rows)}")
    return len(rows)


def claim_job(client: SupabaseClient, job: dict) -> dict | None:
    job_id = job["id"]
    rows = client.rest(
        "PATCH",
        f"/saas_quote_jobs?id=eq.{job_id}&status=eq.queued",
        data={"status": "processing", "updated_at": _utc_now()},
    )
    return rows[0] if rows else None


def update_progress(client: SupabaseClient, job: dict, percent: int) -> None:
    metadata = {**(job.get("metadata") or {}), "progress_percent": percent}
    rows = client.rest(
        "PATCH",
        f"/saas_quote_jobs?id=eq.{job['id']}",
        data={"metadata": metadata, "updated_at": _utc_now()},
    )
    if rows:
        job["metadata"] = rows[0].get("metadata") or metadata


def process_job(client: SupabaseClient, job: dict) -> dict | None:
    claimed = claim_job(client, job)
    if not claimed:
        print(f"Job {job['id']} ya fue tomado por otro worker.")
        return None

    job = {**job, **claimed}
    job_id = job["id"]
    output_path = f"users/{job['usuario_id']}/jobs/{job_id}/output.xlsx"

    with tempfile.TemporaryDirectory(prefix="mobiliti-quote-") as tmp:
        tmp_dir = Path(tmp)
        local_input = tmp_dir / f"input{_input_extension_for_job(job)}"
        local_output = tmp_dir / "output.xlsx"
        started_at = time.perf_counter()
        try:
            update_progress(client, job, 45)
            _download_job_input(client, job, local_input)
            update_progress(client, job, 55)
            generator_input = _prepare_generator_input(job, local_input, tmp_dir)
            _run_generator(job, generator_input, local_output)
            generation_seconds = round(time.perf_counter() - started_at, 1)
            update_progress(client, job, 90)
            _validate_output_size(local_output)
            client.storage_upload(output_path, local_output)
            input_deleted = False
            try:
                _delete_job_input(client, job)
                input_deleted = True
            except Exception as exc:
                print(f"WARN: no se pudo borrar input de job {job_id}: {exc}")
            metadata = {
                **(job.get("metadata") or {}),
                "progress_percent": 100,
                "generation_seconds": generation_seconds,
            }
            return client.rest(
                "PATCH",
                f"/saas_quote_jobs?id=eq.{job_id}",
                data={
                    "status": "completed",
                    "input_path": None if input_deleted else job.get("input_path"),
                    "output_path": output_path,
                    "metadata": metadata,
                    "error_message": None,
                    "updated_at": _utc_now(),
                    "completed_at": _utc_now(),
                },
            )
        except Exception as exc:
            generation_seconds = round(time.perf_counter() - started_at, 1)
            try:
                update_progress(client, job, 100)
            except Exception:
                pass
            client.rest(
                "PATCH",
                f"/saas_quote_jobs?id=eq.{job_id}",
                data={
                    "status": "failed",
                    "metadata": {
                        **(job.get("metadata") or {}),
                        "progress_percent": 100,
                        "generation_seconds": generation_seconds,
                    },
                    "error_message": str(exc)[:1000],
                    "updated_at": _utc_now(),
                },
            )
            raise


def _fallback_tarkett_catalog_payload() -> dict:
    try:
        payload = json.loads(TARKETT_CATALOG_FALLBACK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Catalogo Tarkett de respaldo no disponible") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or not payload["items"]:
        raise RuntimeError("Catalogo Tarkett de respaldo invalido")
    return payload


def sync_tarkett_catalog_if_due(client, *, force: bool = False) -> bool:
    global _TARKETT_LAST_SYNC_ATTEMPT
    if not TARKETT_SYNC_ENABLED:
        return False
    now = time.monotonic()
    if not force and now - _TARKETT_LAST_SYNC_ATTEMPT < TARKETT_SYNC_INTERVAL_SECONDS:
        return False
    _TARKETT_LAST_SYNC_ATTEMPT = now
    if not TARKETTNET_EMAIL or not TARKETTNET_PASSWORD:
        print("WARN: sincronizacion Tarkett habilitada sin credenciales Tarkettnet.")
        return False

    snapshot = client.catalog_snapshot_get("tarkett")
    base_payload = snapshot.get("payload") if isinstance(snapshot, dict) else None
    if not isinstance(base_payload, dict):
        base_payload = _fallback_tarkett_catalog_payload()
    enriched = sync_catalog_from_tarkettnet(
        base_payload,
        email=TARKETTNET_EMAIL,
        password=TARKETTNET_PASSWORD,
    )
    if str(enriched.get("source_hash")) == str((snapshot or {}).get("source_hash")):
        print("Catalogo Tarkett sin cambios.")
        return False
    client.catalog_snapshot_upsert("tarkett", enriched)
    print(
        "Catalogo Tarkett actualizado: "
        f"{enriched.get('tarkettnet_matches', 0)} coincidencias, "
        f"{enriched.get('tarkettnet_price_matches', 0)} precios."
    )
    return True


def run_once() -> bool:
    client = LocalDevClient() if DEV_MODE else (PostgresClient() if DATABASE_URL else SupabaseClient())
    recover_stale_jobs(client)
    job = fetch_next_job(client)
    if not job:
        sync_tarkett_catalog_if_due(client)
        print("Sin jobs pendientes.")
        return False
    print(f"Procesando job {job['id']}...")
    process_job(client, job)
    print(f"Job {job['id']} completado.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker de cotizaciones Mobiliti")
    parser.add_argument("--once", action="store_true", help="Procesa un solo job y termina")
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    print("Worker Mobiliti iniciado.")
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"ERROR: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
