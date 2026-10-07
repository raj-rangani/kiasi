"""Rebuild logs/budget.json and logs/lens.json.gz and record the outcome in logs/sync.json.

Run directly (`python3 reports/sync.py [days]`), by the dashboard server (every 30 minutes, or on POST /sync), by the /kiasi:sync
command, or by anything else that wants the pages fresh. Never raises: the outcome, including
a failure, goes to sync.json so the pages can show it.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import constants


def now():
    return datetime.now().replace(microsecond=0).isoformat()


def write_status(**fields):
    tmp = constants.SYNC_STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(fields, indent=1) + "\n")
    os.replace(tmp, constants.SYNC_STATUS)


def main(days):
    trigger = "manual"
    if constants.SYNC_REQUEST.exists():
        try:
            trigger = constants.SYNC_REQUEST.read_text().strip() or "request"
        except OSError:
            trigger = "request"
        constants.SYNC_REQUEST.unlink()
    started = now()
    write_status(state="running", trigger=trigger, started=started, finished=None, days=days)
    t0 = time.monotonic()
    error = None
    for script in constants.SYNC_SCRIPTS:
        try:
            run = subprocess.run([sys.executable, str(constants.PLUGIN_ROOT / "scripts" / script), str(days)], cwd=constants.PLUGIN_ROOT / "scripts",
                                 capture_output=True, text=True, timeout=constants.SYNC_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            error = f"{script}: no result after {constants.SYNC_TIMEOUT_SECONDS}s"
            break
        except OSError as exc:  # never leave the status at "running"
            error = f"{script}: {exc}"
            break
        if run.returncode != 0:
            error = f"{script}: " + (run.stderr.strip().splitlines() or [f"exit {run.returncode}"])[-1]
            break
    try:  # retention runs at most once a day and never fails the sync
        subprocess.run([sys.executable, str(constants.PLUGIN_ROOT / "scripts" / "cleanup.py"), "--if-due", "--quiet"],
                       cwd=constants.PLUGIN_ROOT / "scripts", timeout=constants.CLEANUP_TIMEOUT_SECONDS + 10,
                       env={**os.environ, "CLAUDE_PLUGIN_DATA": str(constants.DATA_DIR)})
    except subprocess.TimeoutExpired:
        pass
    seconds = round(time.monotonic() - t0, 1)
    write_status(state="failed" if error else "done", trigger=trigger, started=started, finished=now(),
                 days=days, seconds=seconds, error=error)
    print(f"sync {'failed' if error else 'done'} in {seconds}s ({trigger})" + (f": {error}" if error else ""))
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else constants.BUDGET_DAYS))
