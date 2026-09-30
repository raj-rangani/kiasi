#!/usr/bin/env python3
"""Stdlib-only local dashboard for kiasi: serves dashboard/ at /, report JSON
from the data dir at /reports/<file>, subscription usage limits at /limits, and
runs a sync on POST /sync. Requests whose Host is not a loopback name are
refused (DNS rebinding), and POST /sync only from the dashboard's own origin.

Usage: python3 dashboard.py [port]   (default port 8787, falls back to a free
ephemeral port if that one is taken)
"""
import gzip
import http.server
import json
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import constants
import usage_limits

PLUGIN_ROOT = constants.PLUGIN_ROOT
DASHBOARD_DIR = PLUGIN_ROOT / "dashboard"
DATA_DIR = constants.DATA_DIR
SYNC_SCRIPT = PLUGIN_ROOT / "scripts" / "sync.py"
REBUILD_SECONDS = 30 * 60

_sync_lock = threading.Lock()
_sync_thread = None


def run_sync_background():
    global _sync_thread
    with _sync_lock:
        if _sync_thread and _sync_thread.is_alive():
            return False
        _sync_thread = threading.Thread(target=_run_sync, daemon=True)
        _sync_thread.start()
        return True


def _run_sync():
    try:
        subprocess.run([sys.executable, str(SYNC_SCRIPT), "7"], check=False, timeout=180)
    except Exception as exc:  # noqa: BLE001
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "sync.json").write_text(json.dumps({"state": "failed", "error": str(exc)}))


def periodic_rebuild():
    while True:
        time.sleep(REBUILD_SECONDS)
        run_sync_background()


def host_allowed(headers):
    host = (headers.get("Host") or "").rsplit(":", 1)[0]
    return host in constants.DASHBOARD_HOSTS


def same_origin(headers):
    expected = f"http://{headers.get('Host')}"
    origin = headers.get("Origin")
    if origin:
        return origin == expected
    referer = headers.get("Referer") or ""
    return referer == expected or referer.startswith(expected + "/")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("dashboard: " + (fmt % args) + "\n")

    def refuse_foreign_host(self):
        if host_allowed(self.headers):
            return False
        self.send_error(403, "the dashboard answers only on 127.0.0.1 or localhost")
        return True

    def do_HEAD(self):  # noqa: N802
        if not self.refuse_foreign_host():
            super().do_HEAD()

    def do_GET(self):  # noqa: N802
        if self.refuse_foreign_host():
            return
        if self.path == "/":
            self.path = "/index.html"
        if self.path.split("?")[0].rstrip("/") == "/limits":
            data = json.dumps(usage_limits.get_limits()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/reports/"):
            name = self.path[len("/reports/"):].split("?")[0]
            if "/" in name or ".." in name:
                self.send_error(404)
                return
            path = DATA_DIR / name
            packed = DATA_DIR / f"{name}.gz"
            if not path.is_file() and not packed.is_file():
                self.send_error(404, "report not found")
                return
            gzipped = not path.is_file()
            data = packed.read_bytes() if gzipped else path.read_bytes()
            if gzipped and "gzip" not in self.headers.get("Accept-Encoding", ""):
                data, gzipped = gzip.decompress(data), False
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):  # noqa: N802
        if self.refuse_foreign_host():
            return
        if self.path.rstrip("/") == "/sync" and not same_origin(self.headers):
            self.send_error(403, "sync is accepted only from the dashboard page")
            return
        if self.path.rstrip("/") == "/sync":
            started = run_sync_background()
            body = json.dumps({"requested": True, "started": started}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    server = None
    for candidate in range(port, port + 20):
        try:
            server = ThreadingServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        sys.stderr.write("dashboard: no free port found\n")
        sys.exit(1)
    threading.Thread(target=periodic_rebuild, daemon=True).start()
    print(f"kiasi dashboard on http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
