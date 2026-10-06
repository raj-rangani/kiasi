import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from core import constants

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def project_slug(cwd):
    return re.sub(r"[^A-Za-z0-9]+", "-", cwd or "unknown").strip("-") or "unknown"


def checklist_folder(cwd):
    """Where a turn's checklist goes: the project's .kiasi/checkpoints when the project folder is writable, else the data folder."""
    if cwd and os.path.isdir(cwd) and os.access(cwd, os.W_OK):
        return Path(cwd) / constants.PROJECT_CHECKLIST_DIR
    return constants.CHECKPOINT_DIR


def make_checklist_folder(path):
    """Create a checklist's folder before Claude is told its path. In a project, Kiasi's folder ignores itself in git."""
    folder = Path(path).parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ignore = folder.parent / ".gitignore"
        if folder.parts[-2:] == constants.PROJECT_CHECKLIST_DIR.parts and not ignore.exists():
            ignore.write_text("*\n")
    except OSError:
        pass


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


def try_lock(handle):
    """Take an exclusive lock on an open file without waiting; raises OSError when another handle holds it."""
    if fcntl:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def unlock(handle):
    if fcntl:
        fcntl.flock(handle, fcntl.LOCK_UN)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def wait_lock(handle, seconds):
    """True once handle holds its lock; False after `seconds` of waiting, or at once when the file system cannot lock."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            try_lock(handle)
            return True
        except (BlockingIOError, PermissionError):  # held elsewhere: BlockingIOError on POSIX, PermissionError on Windows
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        except OSError:
            return False


@contextmanager
def session_lock(session_id):
    """Run one hook's changes to its session's state alone. A session's hooks run at the same time (parallel tool
    calls, subagents) and each loads the state, changes it and saves it, so without the lock one save dropped the
    calls another hook had just counted. A lock still busy after SESSION_LOCK_WAIT_SECONDS is given up: a hook must
    never hang a tool call. Yields whether the lock is held."""
    try:
        ensure_dirs()
        handle = open(session_path(session_id).with_suffix(".lock"), "a")
    except OSError:
        yield False
        return
    with handle:
        locked = wait_lock(handle, constants.SESSION_LOCK_WAIT_SECONDS)
        try:
            yield locked
        finally:
            if locked:
                unlock(handle)
