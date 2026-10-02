import json
import re
import time

from core import constants


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def ensure_dirs():
    for d in (constants.LOG_DIR, constants.SESSION_DIR, constants.PASTE_DIR, constants.OUTPUT_DIR, constants.NOTES_DIR, constants.CHECKPOINT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def log_event(record):
    ensure_dirs()
    with open(constants.EVENT_LOG, "a") as fh:
        fh.write(json.dumps({"ts": now_iso(), **record}, ensure_ascii=False) + "\n")


def session_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    return constants.SESSION_DIR / f"{safe}.json"


def load_session(session_id):
    try:
        return json.loads(session_path(session_id).read_text())
    except (OSError, ValueError):
        return {"pastes": [], "nudged_at": 0, "last_note_ts": 0, "prompts": 0, "turns": {}}


def save_session(session_id, state):
    ensure_dirs()
    session_path(session_id).write_text(json.dumps(state, ensure_ascii=False))
