from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import quote_worker


PORT = int(os.environ.get("PORT", "10000"))
POLL_SECONDS = int(os.environ.get("WORKER_POLL_SECONDS", "10"))
ISOLATE_JOBS = os.environ.get("WORKER_ISOLATE_JOBS", "1").strip().lower() not in {"0", "false", "no"}
JOB_TIMEOUT_SECONDS = int(os.environ.get("WORKER_JOB_TIMEOUT_SECONDS", "0") or "0")
CATALOG_SYNC_ENABLED = os.environ.get("CATALOG_SYNC_ENABLED", "").strip().lower() in {
    "1", "true", "yes",
}
CATALOG_SYNC_LEASE_SECONDS = 45 * 60
CATALOG_EXIT_WORKED = 0
CATALOG_EXIT_FAILED = 1
CATALOG_EXIT_NO_WORK = 2
CATALOG_EXIT_DISABLED = 3
RATE_SYNC_INTERVAL_SECONDS = 6 * 60 * 60
RATE_SYNC_RETRY_SECONDS = 15 * 60
RATE_SYNC_TIMEOUT_SECONDS = 30
_RATE_LAST_SYNC_ATTEMPT = 0.0


def _catalog_sync_timeout(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 1800
    if parsed < 60:
        return 1800
    return min(parsed, CATALOG_SYNC_LEASE_SECONDS - 60)


CATALOG_SYNC_TIMEOUT_SECONDS = _catalog_sync_timeout(
    os.environ.get("CATALOG_SYNC_TIMEOUT_SECONDS", "1800") or "1800"
)
WORKER_SCRIPT = Path(__file__).resolve().with_name("quote_worker.py")
PROJECT_ROOT = Path(__file__).resolve().parents[2]

stop_event = threading.Event()
state_lock = threading.Lock()
state = {
    "status": "starting",
    "processed": 0,
    "last_run_at": None,
    "last_error": None,
    "last_catalog_sync_at": None,
    "last_catalog_sync_status": "disabled" if not CATALOG_SYNC_ENABLED else "never",
    "last_rate_sync_at": None,
    "last_rate_sync_status": "disabled" if not CATALOG_SYNC_ENABLED else "never",
    "isolated_jobs": ISOLATE_JOBS,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_state(**updates):
    with state_lock:
        state.update(updates)


def _build_client():
    if quote_worker.DEV_MODE:
        return quote_worker.LocalDevClient()
    if quote_worker.DATABASE_URL:
        return quote_worker.PostgresClient()
    return quote_worker.SupabaseClient()


def _has_pending_job() -> bool:
    client = _build_client()
    quote_worker.recover_stale_jobs(client)
    return quote_worker.fetch_next_job(client) is not None


def _run_catalog_sync_isolated() -> bool:
    if not CATALOG_SYNC_ENABLED:
        with state_lock:
            if state.get("last_catalog_sync_status") not in {"failed", "timeout"}:
                state["last_catalog_sync_status"] = "disabled"
        return False
    cmd = [sys.executable, "-m", "mobiliti_saas.worker.catalog_sync.service", "--due"]
    kwargs = {
        "cwd": str(PROJECT_ROOT),
        "check": False,
        "timeout": CATALOG_SYNC_TIMEOUT_SECONDS,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    try:
        result = subprocess.run(cmd, **kwargs)
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        returncode = None
    except Exception:
        returncode = CATALOG_EXIT_FAILED
    if returncode == CATALOG_EXIT_WORKED:
        _set_state(
            status="running", last_error=None, last_catalog_sync_at=_now(),
            last_catalog_sync_status="succeeded",
        )
        return True
    if returncode in {CATALOG_EXIT_NO_WORK, CATALOG_EXIT_DISABLED}:
        next_status = "no_work" if returncode == CATALOG_EXIT_NO_WORK else "misconfigured"
        with state_lock:
            failed_before = (
                state.get("last_catalog_sync_status") in {"failed", "timeout"}
                or state.get("last_error") == "catalog_sync_failed"
            )
            if not failed_before:
                state["last_catalog_sync_status"] = next_status
                if next_status == "misconfigured":
                    state["status"] = "degraded"
                    state["last_error"] = "catalog_sync_failed"
        return False
    status = "timeout" if returncode is None else "failed"
    _set_state(
        status="degraded", last_error="catalog_sync_failed",
        last_catalog_sync_at=_now(), last_catalog_sync_status=status,
    )
    return False


def _rate_sync_due(now=None) -> bool:
    now = time.monotonic() if now is None else now
    with state_lock:
        status = state.get("last_rate_sync_status")
    interval = (
        RATE_SYNC_RETRY_SECONDS
        if status in {"misconfigured", "failed", "timeout"}
        else RATE_SYNC_INTERVAL_SECONDS
    )
    return _RATE_LAST_SYNC_ATTEMPT == 0.0 or now - _RATE_LAST_SYNC_ATTEMPT >= interval


def _run_rate_sync_isolated() -> bool:
    global _RATE_LAST_SYNC_ATTEMPT
    if not CATALOG_SYNC_ENABLED:
        _set_state(last_rate_sync_status="disabled")
        return False
    now = time.monotonic()
    if not _rate_sync_due(now):
        return False
    _RATE_LAST_SYNC_ATTEMPT = now
    cmd = [sys.executable, "-m", "mobiliti_saas.worker.catalog_sync.rate_service"]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            check=False,
            timeout=RATE_SYNC_TIMEOUT_SECONDS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        returncode = None
    except Exception:
        returncode = CATALOG_EXIT_FAILED
    if returncode == CATALOG_EXIT_WORKED:
        _set_state(last_rate_sync_at=_now(), last_rate_sync_status="succeeded")
        return True
    if returncode in {CATALOG_EXIT_NO_WORK, CATALOG_EXIT_DISABLED}:
        _set_state(
            last_rate_sync_status=(
                "no_work" if returncode == CATALOG_EXIT_NO_WORK else "misconfigured"
            )
        )
        return False
    _set_state(
        last_rate_sync_at=_now(),
        last_rate_sync_status="timeout" if returncode is None else "failed",
    )
    return False


def _run_once_isolated() -> bool:
    if not _has_pending_job():
        if quote_worker.sync_tarkett_catalog_if_due(_build_client()):
            return True
        if _run_rate_sync_isolated():
            return True
        did_work = _run_catalog_sync_isolated()
        print("Sin jobs pendientes.")
        return did_work

    cmd = [sys.executable, str(WORKER_SCRIPT), "--once"]
    kwargs = {"cwd": str(PROJECT_ROOT), "check": False}
    if JOB_TIMEOUT_SECONDS > 0:
        kwargs["timeout"] = JOB_TIMEOUT_SECONDS

    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"quote_worker --once termino con codigo {result.returncode}")
    return True


def worker_loop():
    _set_state(status="running", last_error=None)
    while not stop_event.is_set():
        try:
            did_work = _run_once_isolated() if ISOLATE_JOBS else quote_worker.run_once()
            with state_lock:
                state["last_run_at"] = _now()
                if did_work:
                    state["processed"] += 1
                    if state["last_error"] == "worker_cycle_failed":
                        state["last_error"] = None
                        state["status"] = "running"
        except Exception:
            _set_state(status="degraded", last_error="worker_cycle_failed", last_run_at=_now())
        stop_event.wait(POLL_SECONDS)
    _set_state(status="stopping")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        payload = _health_payload()

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200 if payload["ok"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _health_payload():
    with state_lock:
        current = dict(state)
    status = current.get("status")
    catalog_status = current.get("last_catalog_sync_status")
    rate_status = current.get("last_rate_sync_status")
    if status not in {"starting", "running", "degraded", "stopping"}:
        status = "degraded"
    if catalog_status not in {
        "disabled", "never", "no_work", "misconfigured", "succeeded", "failed", "timeout",
    }:
        catalog_status = "failed"
    if rate_status not in {
        "disabled", "never", "no_work", "misconfigured", "succeeded", "failed", "timeout",
    }:
        rate_status = "failed"
    return {
        "ok": status in {"running", "degraded"},
        "status": status,
        "processed": int(current.get("processed", 0)),
        "last_run_at": current.get("last_run_at"),
        "last_error": current.get("last_error") if current.get("last_error") in {
            None, "catalog_sync_failed", "worker_cycle_failed",
        } else "worker_cycle_failed",
        "isolated_jobs": bool(current.get("isolated_jobs")),
        "last_catalog_sync_at": current.get("last_catalog_sync_at"),
        "last_catalog_sync_status": catalog_status,
        "last_rate_sync_at": current.get("last_rate_sync_at"),
        "last_rate_sync_status": rate_status,
    }


def main():
    worker = threading.Thread(target=worker_loop, name="mobiliti-worker", daemon=True)
    worker.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)

    def shutdown(_signum, _frame):
        stop_event.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    finally:
        stop_event.set()
        worker.join(timeout=30)


if __name__ == "__main__":
    main()
