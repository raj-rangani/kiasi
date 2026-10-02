import os
import sys

from core import constants
from core.events import log_event
from core.procs import detached_kwargs


def launch_cleanup(session_id):
    """Start cleanup.py detached so session start never waits on it; it runs at most once a day."""
    if constants.CLEANUP_MODE == "off":
        return
    try:
        import subprocess
        subprocess.Popen([sys.executable, str(constants.PLUGIN_ROOT / "scripts" / "cleanup.py"), "--if-due", "--quiet", "--session", session_id],
                         env={**os.environ, "CLAUDE_PLUGIN_DATA": str(constants.DATA_DIR)},  # same data folder as this hook, never another
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached_kwargs())
    except OSError as exc:
        sys.stderr.write(f"kiasi: cleanup not started: {exc}\n")


def ensure_dashboard(session_id):
    """Make sure a dashboard server is up so the pages and their 30-minute rebuild do not depend on anyone starting it."""
    if not constants.DASHBOARD_AUTOSTART:
        return ""
    try:
        import dashboard
        url, started = dashboard.ensure()
    except Exception as exc:  # noqa: BLE001  session start never fails on the dashboard
        sys.stderr.write(f"kiasi: dashboard not started: {exc}\n")
        return ""
    if not started:
        return ""
    log_event({"event": "dashboard_start", "session_id": session_id, "url": url, "ok": bool(url)})
    if not url:
        sys.stderr.write(f"kiasi: dashboard did not come up, see {constants.DASHBOARD_LOG}\n")
        return ""
    return f"Kiasi dashboard started: {url} (reports rebuild every {constants.DASHBOARD_REBUILD_SECONDS // 60} min)"
