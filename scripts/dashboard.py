#!/usr/bin/env python3
"""Stdlib-only local dashboard for kiasi: serves dashboard/ at /, report JSON
from the data dir at /reports/<file>, subscription usage limits at /limits, and
runs a sync on POST /sync. Requests whose Host is not a loopback name are
refused (DNS rebinding), and POST /sync only from the dashboard's own origin.

Usage: python3 dashboard.py [port]   (default port 8787, tries the next ports if
that one is taken)
       python3 dashboard.py --ensure  (print the URL of the running dashboard, starting
one detached first if none answers; what the SessionStart hook and /kiasi:dashboard do)

Where it listens goes to dashboard.json in the data dir, errors to dashboard.log. On
start the reports are rebuilt when missing or older than the rebuild interval, then
every 30 minutes.
"""
import gzip
import http.client
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from core import constants
import limits_data

PLUGIN_ROOT = constants.PLUGIN_ROOT
DASHBOARD_DIR = PLUGIN_ROOT / "dashboard"
DATA_DIR = constants.DATA_DIR
SYNC_SCRIPT = PLUGIN_ROOT / "scripts" / "reports" / "sync.py"
REBUILD_SECONDS = constants.DASHBOARD_REBUILD_SECONDS

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
        try:
            run_sync_background()
        except Exception as exc:  # noqa: BLE001  the timer must survive one bad run
            sys.stderr.write(f"dashboard: rebuild not started: {exc}\n")


def reports_stale():
    """True when the lens report is missing or older than one rebuild interval."""
    try:
        return time.time() - constants.LENS_FILE.stat().st_mtime > REBUILD_SECONDS
    except OSError:
        return True


def write_state(port):
    constants.DATA_DIR.mkdir(parents=True, exist_ok=True)
    constants.DASHBOARD_STATE.write_text(json.dumps({
        "pid": os.getpid(), "port": port, "url": f"http://127.0.0.1:{port}/",
        "started": datetime.now().replace(microsecond=0).isoformat()}, indent=1) + "\n")


def answers(port):
    """True when a kiasi dashboard answers GET /limits on this port (its own Server header, or the JSON the old build sent)."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=constants.DASHBOARD_PROBE_SECONDS)
        conn.request("GET", "/limits", headers={"Host": "127.0.0.1"})
        res = conn.getresponse()
        res.read()
        conn.close()
    except (OSError, http.client.HTTPException):
        return False
    server = res.getheader("Server") or ""
    return res.status == 200 and (server.startswith(constants.DASHBOARD_SERVER) or "json" in (res.getheader("Content-Type") or ""))


def running_url():
    """URL of the dashboard already listening (recorded port first, then the default), or None."""
    ports = []
    try:
        ports.append(int(json.loads(constants.DASHBOARD_STATE.read_text())["port"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ports.append(constants.DASHBOARD_PORT)
    for port in dict.fromkeys(ports):
        if answers(port):
            return f"http://127.0.0.1:{port}/"
    return None


def start_detached():
    """Start dashboard.py in its own session with no terminal, so it outlives the Claude Code process that asked for it."""
    constants.DATA_DIR.mkdir(parents=True, exist_ok=True)
    mode = "ab"
    try:
        if constants.DASHBOARD_LOG.stat().st_size > constants.DASHBOARD_LOG_MAX_BYTES:
            mode = "wb"
    except OSError:
        pass
    with open(constants.DASHBOARD_LOG, mode) as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(constants.DASHBOARD_PORT)],
                         env={**os.environ, "CLAUDE_PLUGIN_DATA": str(constants.DATA_DIR)},
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)


def ensure():
    """(url, started): the URL of a dashboard that answers, starting one first when none does. url is None if it did not come up."""
    url = running_url()
    if url:
        return url, False
    start_detached()
    deadline = time.monotonic() + constants.DASHBOARD_START_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.1)
        url = running_url()
        if url:
            return url, True
    return None, True


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
    server_version = constants.DASHBOARD_SERVER

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def log_request(self, code="-", size="-"):
        try:
            ok = 200 <= int(code) < 400
        except (TypeError, ValueError):
            ok = False
        if not ok:  # only failures go to the log, which lives in the data dir when started detached
            super().log_request(code, size)

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
            data = json.dumps(limits_data.get_limits()).encode()
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
    if "--ensure" in sys.argv[1:]:
        url, started = ensure()
        if not url:
            sys.stderr.write(f"dashboard: did not come up, see {constants.DASHBOARD_LOG}\n")
            sys.exit(1)
        print(f"kiasi dashboard {'started' if started else 'already running'} on {url}", flush=True)
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else constants.DASHBOARD_PORT
    server = None
    for candidate in range(port, port + constants.DASHBOARD_PORT_TRIES):
        try:
            server = ThreadingServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        sys.stderr.write("dashboard: no free port found\n")
        sys.exit(1)
    write_state(port)
    if reports_stale():
        run_sync_background()
    threading.Thread(target=periodic_rebuild, daemon=True).start()
    print(f"kiasi dashboard on http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
