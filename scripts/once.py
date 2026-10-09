"""Run a UserPromptSubmit hook once per session.

Usage in settings.json, in place of the hook's own command:
    sh <kiasi>/scripts/run.sh once.py -- <the command as it was>

The wrapped command gets the hook's stdin and its output is passed through on the first prompt of a session. On every later prompt
nothing is printed, so text the hook would have repeated word for word on each request is attached once. Markers live under
DATA_DIR/once and are keyed by session and command; `python3 once.py --reset` removes them."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import constants  # noqa: E402

ONCE_DIR = constants.DATA_DIR / "once"


def marker(session, command):
    key = hashlib.sha1(" ".join(command).encode()).hexdigest()[:12]
    return ONCE_DIR / f"{session}-{key}"


def main(argv):
    if argv[:1] == ["--reset"]:
        for path in ONCE_DIR.glob("*"):
            path.unlink(missing_ok=True)
        return 0
    command = argv[1:] if argv[:1] == ["--"] else argv
    if not command:
        print("once.py: no command given after --", file=sys.stderr)
        return 0
    raw = sys.stdin.read()
    try:
        session = str(json.loads(raw or "{}").get("session_id") or "")
    except ValueError:
        session = ""
    session = "".join(c for c in session if c.isalnum() or c in "-_") or "no-session"
    mark = marker(session, command)
    if mark.exists():
        return 0
    try:
        ONCE_DIR.mkdir(parents=True, exist_ok=True)
        mark.touch()
    except OSError:
        pass
    done = subprocess.run(command, input=raw, text=True, capture_output=True, env=os.environ)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
