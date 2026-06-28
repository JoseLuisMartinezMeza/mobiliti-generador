"""Safe Cloudflare R2 readiness checks for Mobiliti quote storage.

The script never prints token values, derived S3 secrets, signed URLs, or full
Authorization headers. It only reports readiness booleans and bucket/CORS
status so production can be switched to QUOTE_STORAGE_PROVIDER=r2 deliberately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


DEFAULT_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REQUIRED_CORS_METHODS = {"GET", "PUT", "HEAD"}
REQUIRED_CORS_HEADERS = {"content-type"}


def _load_kv_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    data: dict[str, str] = {}
    aliases = {
        "ACCOUNT ID": "R2_ACCOUNT_ID",
        "API TOKEN": "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE API TOKEN": "CLOUDFLARE_API_TOKEN",
        "ACCESS KEY ID": "R2_ACCESS_KEY_ID",
        "R2 ACCESS KEY ID": "R2_ACCESS_KEY_ID",
        "SECRET ACCESS KEY": "R2_SECRET_ACCESS_KEY",
        "R2 SECRET ACCESS KEY": "R2_SECRET_ACCESS_KEY",
        "ENDPOINT S3": "R2_ENDPOINT_URL",
        "S3 ENDPOINT": "R2_ENDPOINT_URL",
        "BUCKET": "R2_BUCKET",
    }
    env_keys = {
        "R2_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ENDPOINT_URL",
        "R2_BUCKET",
    }
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        sep = "=" if "=" in raw else ":" if ":" in raw else ""
        if not sep:
            continue
        left, right = raw.split(sep, 1)
        label = re.sub(r"\s+", " ", left.strip()).upper()
        key = aliases.get(label)
        if not key and label in env_keys:
            key = label
        if not key:
            continue
        value = right.strip().strip("\"'")
        if key == "CLOUDFLARE_API_TOKEN" and value.lower().startswith("bearer "):
            value = value.split(None, 1)[1].strip()
        if value:
            data[key] = value
    return data


def _env_or_file(name: str, file_values: dict[str, str], fallback: str = "") -> str:
    return os.environ.get(name, "").strip() or file_values.get(name, "").strip() or fallback


def _json_request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def verify_cloudflare_token(token: str) -> tuple[bool, str | None, str | None]:
    if not token:
        return False, None, "missing_token"
    try:
        data = _json_request("GET", "https://api.cloudflare.com/client/v4/user/tokens/verify", token)
    except urllib.error.HTTPError as exc:
        return False, None, f"http_{exc.code}"
    except Exception as exc:
        return False, None, exc.__class__.__name__
    result = data.get("result") or {}
    return bool(data.get("success")), result.get("id"), None


def derive_r2_s3_credentials(api_token: str, token_id: str | None) -> tuple[str, str] | None:
    if not api_token or not token_id:
        return None
    return token_id, hashlib.sha256(api_token.encode("utf-8")).hexdigest()


def _s3_client(account_id: str, access_key_id: str, secret_access_key: str, endpoint_url: str = ""):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("missing_boto3") from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def check_s3_bucket(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    endpoint_url: str = "",
) -> dict:
    if not all([account_id, access_key_id, secret_access_key, bucket]):
        return {"s3_ready": False, "error": "missing_s3_config"}
    try:
        client = _s3_client(account_id, access_key_id, secret_access_key, endpoint_url)
        client.head_bucket(Bucket=bucket)
        return {"s3_ready": True}
    except Exception as exc:
        return {"s3_ready": False, "error": exc.__class__.__name__}


def evaluate_cors_rules(rules: list[dict], origins: list[str]) -> dict:
    missing: list[str] = []
    if not origins:
        return {
            "cors_configured": bool(rules),
            "cors_rules_count": len(rules),
            "cors_ready": False,
            "cors_missing": ["origin_not_provided"],
        }

    for origin in origins:
        matched = False
        for rule in rules:
            allowed_origins = set(rule.get("AllowedOrigins") or [])
            allowed_methods = {str(method).upper() for method in (rule.get("AllowedMethods") or [])}
            allowed_headers = {str(header).lower() for header in (rule.get("AllowedHeaders") or [])}
            origin_ok = origin in allowed_origins or "*" in allowed_origins
            methods_ok = REQUIRED_CORS_METHODS.issubset(allowed_methods)
            headers_ok = "*" in allowed_headers or REQUIRED_CORS_HEADERS.issubset(allowed_headers)
            if origin_ok and methods_ok and headers_ok:
                matched = True
                break
        if not matched:
            missing.append(origin)

    return {
        "cors_configured": bool(rules),
        "cors_rules_count": len(rules),
        "cors_ready": not missing,
        "cors_missing": missing,
    }


def get_bucket_cors(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    origins: list[str],
    endpoint_url: str = "",
) -> dict:
    try:
        client = _s3_client(account_id, access_key_id, secret_access_key, endpoint_url)
        data = client.get_bucket_cors(Bucket=bucket)
        rules = data.get("CORSRules") or []
        return evaluate_cors_rules(rules, origins)
    except Exception as exc:
        return {"cors_configured": False, "cors_ready": False, "cors_error": exc.__class__.__name__}


def apply_bucket_cors(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    origins: list[str],
    endpoint_url: str = "",
) -> dict:
    if not origins:
        return {"cors_applied": False, "cors_error": "missing_origins"}
    rules = [
        {
            "AllowedOrigins": origins,
            "AllowedMethods": ["GET", "PUT", "HEAD"],
            "AllowedHeaders": ["Content-Type", "Authorization", "x-amz-content-sha256", "x-amz-date"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 3600,
        }
    ]
    try:
        client = _s3_client(account_id, access_key_id, secret_access_key, endpoint_url)
        client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
        return {"cors_applied": True, "cors_rules_count": len(rules)}
    except Exception as exc:
        return {"cors_applied": False, "cors_error": exc.__class__.__name__}


def probe_s3_object(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    endpoint_url: str = "",
) -> dict:
    key = f"_diagnostics/r2-doctor-{uuid.uuid4().hex}.txt"
    body = b"mobiliti-r2-doctor"
    try:
        client = _s3_client(account_id, access_key_id, secret_access_key, endpoint_url)
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")
        downloaded = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        client.delete_object(Bucket=bucket, Key=key)
        return {"probe_ready": downloaded == body, "probe_deleted": True}
    except Exception as exc:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        return {"probe_ready": False, "probe_error": exc.__class__.__name__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Cloudflare R2 readiness without printing secrets.")
    parser.add_argument("--cloudflare-env-file", type=Path, default=None)
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET") or os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files"))
    parser.add_argument("--origin", action="append", default=[])
    parser.add_argument("--apply-cors", action="store_true")
    parser.add_argument("--probe-object", action="store_true")
    args = parser.parse_args(argv)

    file_values = _load_kv_file(args.cloudflare_env_file) if args.cloudflare_env_file else {}
    account_id = _env_or_file("R2_ACCOUNT_ID", file_values)
    api_token = _env_or_file("CLOUDFLARE_API_TOKEN", file_values)
    access_key_id = _env_or_file("R2_ACCESS_KEY_ID", file_values)
    secret_access_key = _env_or_file("R2_SECRET_ACCESS_KEY", file_values)
    endpoint_url = _env_or_file("R2_ENDPOINT_URL", file_values)
    bucket = args.bucket or file_values.get("R2_BUCKET", "quote-files")

    token_ok, token_id, token_error = verify_cloudflare_token(api_token)
    derived = None
    if token_ok and (not access_key_id or not secret_access_key):
        derived = derive_r2_s3_credentials(api_token, token_id)
        if derived:
            access_key_id, secret_access_key = derived

    report = {
        "account_id_present": bool(account_id),
        "bucket": bucket,
        "endpoint_url_present": bool(endpoint_url),
        "cloudflare_token_present": bool(api_token),
        "cloudflare_token_valid": token_ok,
        "cloudflare_token_error": token_error,
        "token_id_present": bool(token_id),
        "s3_credentials_present": bool(access_key_id and secret_access_key),
        "s3_credentials_derived_from_token": bool(derived),
    }
    report.update(check_s3_bucket(account_id, access_key_id, secret_access_key, bucket, endpoint_url))
    if report.get("s3_ready"):
        if args.apply_cors:
            report.update(apply_bucket_cors(account_id, access_key_id, secret_access_key, bucket, args.origin, endpoint_url))
        report.update(get_bucket_cors(account_id, access_key_id, secret_access_key, bucket, args.origin, endpoint_url))
        if args.probe_object:
            report.update(probe_s3_object(account_id, access_key_id, secret_access_key, bucket, endpoint_url))

    print(json.dumps(report, sort_keys=True))
    ready = bool(report.get("s3_ready"))
    if args.origin:
        ready = ready and bool(report.get("cors_ready"))
    if args.probe_object:
        ready = ready and bool(report.get("probe_ready"))
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main())
