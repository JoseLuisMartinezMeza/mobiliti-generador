"""Supabase Storage retention for Mobiliti quote files.

Dry-run by default. Requires SUPABASE_URL and SUPABASE_SERVICE_KEY in env.
This script intentionally logs only counts and sizes, never signed URLs,
headers, tokens, or full object paths.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath


def _env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required env: {name}")
    return value.rstrip("/") if name == "SUPABASE_URL" else value


def _json_request(method: str, url: str, service_key: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", service_key)
    req.add_header("Authorization", f"Bearer {service_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(_safe_http_error("Supabase Storage", exc.code, detail)) from exc


def _safe_http_error(service: str, code: int, body: str) -> str:
    raw = str(body or "")
    for reason in ("exceed_storage_size_quota", "Payload too large", "InvalidRequest", "Unauthorized", "Forbidden"):
        if reason in raw:
            return f"{service} HTTP {code}: {reason}"
    return f"{service} HTTP {code}"


def _parse_dt(value: object) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.fromtimestamp(0, timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)


def _object_size_mb(obj: dict) -> float:
    metadata = obj.get("metadata") or {}
    try:
        return float(metadata.get("size") or 0) / 1024.0 / 1024.0
    except (TypeError, ValueError):
        return 0.0


def _list_prefix(base_url: str, service_key: str, bucket: str, prefix: str) -> list[dict]:
    url = f"{base_url}/storage/v1/object/list/{bucket}"
    offset = 0
    rows: list[dict] = []
    while True:
        batch = _json_request(
            "POST",
            url,
            service_key,
            {
                "prefix": prefix,
                "limit": 1000,
                "offset": offset,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        if not isinstance(batch, list):
            raise RuntimeError("Storage list did not return a list")
        if not batch:
            break
        for item in batch:
            name = str(item.get("name") or "").strip("/")
            if not name:
                continue
            full_name = str(PurePosixPath(prefix) / name) if prefix else name
            item["_full_name"] = full_name
            rows.append(item)
        if len(batch) < 1000:
            break
        offset += len(batch)
    return rows


def _list_objects_recursive(base_url: str, service_key: str, bucket: str, prefix: str) -> list[dict]:
    found: list[dict] = []
    pending = [prefix.strip("/")]
    seen_prefixes: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen_prefixes:
            continue
        seen_prefixes.add(current)
        for item in _list_prefix(base_url, service_key, bucket, current):
            full_name = item["_full_name"]
            if item.get("id"):
                found.append(item)
            else:
                pending.append(full_name)
    return found


def _job_dir(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if len(parts) >= 4 and parts[0] == "users" and parts[2] == "jobs":
        return "/".join(parts[:4])
    return None


def _delete_objects(base_url: str, service_key: str, bucket: str, paths: list[str]) -> None:
    if not paths:
        return
    url = f"{base_url}/storage/v1/object/{bucket}"
    _json_request("DELETE", url, service_key, {"prefixes": paths})


def build_plan(
    objects: list[dict],
    max_outputs_per_user: int,
    min_age_days: int = 1,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, int(min_age_days)))
    by_job: dict[str, dict] = defaultdict(dict)
    for obj in objects:
        path = str(obj.get("_full_name") or "")
        job_dir = _job_dir(path)
        if not job_dir:
            continue
        leaf = PurePosixPath(path).name.lower()
        if leaf.startswith("output") and leaf.endswith(".xlsx"):
            by_job[job_dir]["output"] = obj
        elif leaf.startswith("input") and (leaf.endswith(".xlsx") or leaf.endswith(".pdf")):
            by_job[job_dir]["input"] = obj

    by_user: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for job_dir, files in by_job.items():
        output = files.get("output")
        if not output:
            continue
        user_id = PurePosixPath(job_dir).parts[1]
        sort_date = _parse_dt(output.get("updated_at") or output.get("created_at"))
        by_user[user_id].append((job_dir, {"files": files, "sort_date": sort_date}))

    delete_paths: list[str] = []
    summary = {
        "users_reviewed": len(by_user),
        "jobs_with_outputs": sum(len(items) for items in by_user.values()),
        "old_jobs_deleted": 0,
        "completed_inputs_deleted": 0,
        "recent_jobs_skipped": 0,
        "recent_inputs_skipped": 0,
        "objects_planned": 0,
        "estimated_mb": 0.0,
    }

    for jobs in by_user.values():
        jobs.sort(key=lambda row: row[1]["sort_date"], reverse=True)
        for index, (job_dir, data) in enumerate(jobs):
            files = data["files"]
            old_enough = data["sort_date"] <= cutoff
            input_obj = files.get("input")
            output_obj = files.get("output")
            if index >= max_outputs_per_user:
                if not old_enough:
                    summary["recent_jobs_skipped"] += 1
                    continue
                for obj in (input_obj, output_obj):
                    if obj:
                        delete_paths.append(obj["_full_name"])
                        summary["estimated_mb"] += _object_size_mb(obj)
                summary["old_jobs_deleted"] += 1
            elif input_obj:
                if not old_enough:
                    summary["recent_inputs_skipped"] += 1
                    continue
                delete_paths.append(input_obj["_full_name"])
                summary["estimated_mb"] += _object_size_mb(input_obj)
                summary["completed_inputs_deleted"] += 1

    summary["objects_planned"] = len(delete_paths)
    summary["estimated_mb"] = round(summary["estimated_mb"], 2)
    return {"summary": summary, "delete_paths": list(dict.fromkeys(delete_paths))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Mobiliti quote Storage retention.")
    parser.add_argument("--bucket", default=os.environ.get("QUOTE_STORAGE_BUCKET", "quote-files"))
    parser.add_argument("--prefix", default="users")
    parser.add_argument("--max-per-user", type=int, default=int(os.environ.get("MAX_QUOTE_HISTORY_PER_USER", "3")))
    parser.add_argument("--min-age-days", type=int, default=int(os.environ.get("QUOTE_STORAGE_RETENTION_MIN_AGE_DAYS", "1")))
    parser.add_argument("--apply", action="store_true", help="Actually delete planned objects. Default is dry-run.")
    args = parser.parse_args()

    base_url = _env_required("SUPABASE_URL")
    service_key = _env_required("SUPABASE_SERVICE_KEY")

    objects = _list_objects_recursive(base_url, service_key, args.bucket, args.prefix)
    plan = build_plan(objects, args.max_per_user, min_age_days=args.min_age_days)
    summary = {
        **plan["summary"],
        "bucket": args.bucket,
        "dry_run": not args.apply,
        "min_age_days": args.min_age_days,
    }
    print(json.dumps(summary, sort_keys=True))

    if args.apply:
        _delete_objects(base_url, service_key, args.bucket, plan["delete_paths"])
        print(json.dumps({"deleted_objects": len(plan["delete_paths"]), "dry_run": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
