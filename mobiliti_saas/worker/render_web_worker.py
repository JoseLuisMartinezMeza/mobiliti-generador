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
WORKER_SCRIPT = Path(__file__).resolve().with_name("quote_worker.py")
PROJECT_ROOT = Path(__file__).resolve().parents[2]

stop_event = threading.Event()
state_lock = threading.Lock()
state = {
    "status": "starting",
    "processed": 0,
    "last_run_at": None,
    "last_error": None,
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


def _run_once_isolated() -> bool:
    if not _has_pending_job():
        quote_worker.sync_tarkett_catalog_if_due(_build_client())
        print("Sin jobs pendientes.")
        return False

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
                state["last_error"] = None
                if did_work:
                    state["processed"] += 1
        except Exception as exc:
            _set_state(status="degraded", last_error=str(exc), last_run_at=_now())
        stop_event.wait(POLL_SECONDS)
    _set_state(status="stopping")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        with state_lock:
            payload = dict(state)
        payload["ok"] = payload["status"] in {"running", "degraded"}

        body = json.dumps(payload).encode("utf-8")
        self.send_response(200 if payload["ok"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


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
