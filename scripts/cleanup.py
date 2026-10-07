#!/usr/bin/env python3
"""Move Kiasi data files that no session uses any more to trash, and empty the trash later.

Safety rules:
- Only files whose names Kiasi writes (constants.CLEANUP_PATTERNS) are touched, only as regular
  files directly inside Kiasi's own folders, never through a symlink. Those are its data folder and
  the .kiasi/checkpoints folders in projects that its turn events named for a checklist.
- A file goes only when the file and its session have both been unused for CLEANUP_IDLE_DAYS
  (pastes: CLEANUP_PASTE_IDLE_DAYS). The current session and anything used in the last
  CLEANUP_RECENT_HOURS are never touched. A notes file goes only when its newest entry is older
  than CLEANUP_NOTE_DAYS. The event log is never rewritten.
- Files are moved to trash/ and deleted CLEANUP_TRASH_DAYS later; --restore puts them back, a project's
  checklists into their project folder.
- Mode auto (default) only reports for the first CLEANUP_REPORT_DAYS; KIASI_CLEANUP=report|trash|off
  overrides it. Over CLEANUP_MAX_BYTES the trash is emptied oldest first, then it only warns.
- Every move, delete and restore is written to cleanup.jsonl. Errors are recorded, never raised.

Usage: cleanup.py [--dry-run] [--if-due] [--session ID] [--quiet] | cleanup.py --restore [NAME ...]
"""
import argparse
import gzip
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import constants
from core.events import try_lock

DAY = 86400
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"


def stamp(t=None):
    return time.strftime(TS_FORMAT, time.localtime(t))


def parse_ts(text):
    try:
        return time.mktime(time.strptime(text, TS_FORMAT))
    except (TypeError, ValueError):
        return None


def folders():
    """Folder name -> (path, idle days) for the per-session folders."""
    return {
        "outputs": (constants.OUTPUT_DIR, constants.CLEANUP_IDLE_DAYS),
        "checkpoints": (constants.CHECKPOINT_DIR, constants.CLEANUP_IDLE_DAYS),
        "pastes": (constants.PASTE_DIR, constants.CLEANUP_PASTE_IDLE_DAYS),
        "sessions": (constants.SESSION_DIR, constants.CLEANUP_IDLE_DAYS),
    }


def load_state():
    try:
        return json.loads(constants.CLEANUP_STATE.read_text())
    except (OSError, ValueError):
        return {}


def record(action, **fields):
    with open(constants.CLEANUP_MANIFEST, "a") as fh:
        fh.write(json.dumps({"ts": stamp(), "action": action, **fields}) + "\n")


def own_files(name, folder):
    """Regular files in folder whose names Kiasi writes; symlinks and anything else are skipped."""
    pattern = re.compile(constants.CLEANUP_PATTERNS[name])
    if not folder.is_dir() or folder.is_symlink():
        return
    for path in folder.iterdir():
        if not pattern.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            yield path, path.stat()
        except OSError:
            continue


def last_used(stat):
    return max(stat.st_mtime, stat.st_atime)


def activity_index():
    """Session id -> last time its transcript or Kiasi's session state changed."""
    seen = {}
    places = [constants.TRANSCRIPT_ROOT.glob("*/*.jsonl") if constants.TRANSCRIPT_ROOT.is_dir() else [],
              constants.SESSION_DIR.glob("*.json") if constants.SESSION_DIR.is_dir() else []]
    for group in places:
        for path in group:
            try:
                seen[path.stem] = max(seen.get(path.stem, 0), path.stat().st_mtime)
            except OSError:
                continue
    return seen


def output_owners():
    """Saved output file name -> session id, from the cap events in the event log."""
    owners = {}
    try:
        with open(constants.EVENT_LOG, errors="replace") as fh:
            for line in fh:
                if '"saved_path"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("saved_path") and event.get("session_id"):
                    owners[Path(event["saved_path"]).name] = event["session_id"]
    except OSError:
        pass
    return owners


def project_checklist_folder(folder):
    """True for a real .kiasi/checkpoints folder in a project, never one reached through a symlink."""
    return (folder.parts[-2:] == constants.PROJECT_CHECKLIST_DIR.parts and folder.is_dir()
            and not folder.is_symlink() and not folder.parent.is_symlink())


def checklist_folders():
    """The project checklist folders that turn events named, each once however its path was spelled."""
    named = set()
    try:
        with open(constants.EVENT_LOG, errors="replace") as fh:
            for line in fh:
                if '"checkpoint"' not in line:
                    continue
                try:
                    path = json.loads(line).get("checkpoint")
                except (ValueError, AttributeError):
                    continue
                if isinstance(path, str) and path:
                    named.add(Path(path).parent)
    except OSError:
        pass
    found = {}
    for folder in sorted(named):
        if project_checklist_folder(folder):
            found.setdefault(folder.resolve(), folder)
    return list(found.values())


def managed_folders():
    """(folder name, path, idle days) for every per-session folder, the project checklist folders included."""
    rows = [(name, folder, days) for name, (folder, days) in folders().items()]
    return rows + [("checkpoints", folder, constants.CLEANUP_IDLE_DAYS) for folder in [*checklist_folders(), constants.TEMP_CHECKLIST_DIR]]


def owner(name, path, owners):
    if name == "outputs":
        match = re.match(r"compact-([0-9a-f]{8})-", path.name)
        return match.group(1) if match else owners.get(path.name)
    if name == "checkpoints":
        return path.name[:8]
    if name == "pastes":
        return path.name[:36]
    return path.stem


def session_seen(session, seen):
    if not session:
        return 0
    if len(session) == 8:
        return max((t for sid, t in seen.items() if sid.startswith(session)), default=0)
    return seen.get(session, 0)


def same_session(a, b):
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def newest_note(path):
    try:
        lines = [l for l in path.read_text(errors="replace").splitlines() if l.strip()]
        return parse_ts(json.loads(lines[-1]).get("ts")) if lines else None
    except (OSError, ValueError, AttributeError):
        return None


def live_pauses(now):
    """(session ids, checklist paths) of the pause records a resume can still use. Their state and checklist stay as long as the record."""
    sessions, checklists = set(), set()
    for path in constants.NOTES_DIR.glob("*.paused*.json"):
        try:
            record = json.loads(path.read_text())
            at = parse_ts(record["at"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if at and now - at <= constants.NOTE_MAX_AGE_DAYS * DAY:
            sessions.add(record.get("session_id") or "")
            checklists.add(record.get("checkpoint"))
    return sessions, checklists


def schedule(current):
    """(folder name, path, size, last used, due time) for every file cleanup manages, outside the current session."""
    seen = activity_index()
    paused_sessions, paused_checklists = live_pauses(time.time())
    owners = output_owners()
    recent = constants.CLEANUP_RECENT_HOURS * 3600
    rows = []
    for name, folder, days in managed_folders():
        for path, stat in own_files(name, folder):
            session = owner(name, path, owners)
            if same_session(session, current) or str(path) in paused_checklists or any(same_session(session, paused) for paused in paused_sessions):
                continue
            last = max(last_used(stat), session_seen(session, seen))
            rows.append((name, path, stat.st_size, last, last + max(days * DAY, recent)))
    for path, stat in own_files("notes", constants.NOTES_DIR):
        newest = newest_note(path)
        if newest:
            rows.append(("notes", path, stat.st_size, newest, max(newest, stat.st_mtime) + max(constants.CLEANUP_NOTE_DAYS * DAY, recent)))
    for path, stat in own_files("paused", constants.NOTES_DIR):
        try:
            at = parse_ts(json.loads(path.read_text())["at"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue  # unreadable records are left alone
        if at:
            rows.append(("paused", path, stat.st_size, at, at + constants.NOTE_MAX_AGE_DAYS * DAY))
    return rows


def candidates(now, current):
    """(folder name, path, size, reason) for every file safe to move to trash."""
    found = []
    for name, path, size, last, due in schedule(current):
        if due < now:
            age = int((now - last) / DAY)
            found.append((name, path, size, f"newest note {age}d old" if name == "notes" else f"pause record {age}d old" if name == "paused" else f"unused {age}d"))
    return found


def trash_files():
    """(path, stat) for every file in trash/<folder>/."""
    if not constants.TRASH_DIR.is_dir():
        return []
    rows = []
    for sub in constants.TRASH_DIR.iterdir():
        if sub.is_dir() and not sub.is_symlink():
            for path in sub.iterdir():
                if path.is_file() and not path.is_symlink():
                    rows.append((path, path.stat()))
    return rows


def plain_name(path):
    """The file's own name: trash stores it gzipped as <name>.gz."""
    return path.name[:-3] if path.name.endswith(".gz") else path.name


def move_to_trash(name, path, size, reason):
    """Gzip the file into trash/<folder>/<name>.gz, then remove the original; the trash clock starts now."""
    dest_dir = constants.TRASH_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{path.name}.gz"
    if dest.exists():
        dest = dest_dir / f"{path.stem}.{int(time.time())}{path.suffix}.gz"
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(gzip.compress(path.read_bytes(), compresslevel=constants.CLEANUP_GZIP_LEVEL))
    os.replace(tmp, dest)
    path.unlink()
    record("trash", path=str(path), to=str(dest), size=size, stored=dest.stat().st_size, reason=reason)


def purge(path, size, reason):
    path.unlink(missing_ok=True)
    record("delete", path=str(path), size=size, reason=reason)


def folder_bytes():
    total = 0
    for path in constants.LOG_DIR.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def drop_old_index():
    """Remove search.db, the index older versions kept; search.py now reads the files directly."""
    freed = 0
    for path in (constants.SEARCH_DB, Path(f"{constants.SEARCH_DB}-wal"), Path(f"{constants.SEARCH_DB}-shm")):
        if path.is_file() and not path.is_symlink():
            size = path.stat().st_size
            path.unlink()
            record("delete", path=str(path), size=size, reason="search index no longer used")
            freed += size
    return freed


def resolve_mode(state, now):
    mode = constants.CLEANUP_MODE
    if mode in ("off", "report", "trash"):
        return mode
    first = parse_ts(state.get("first_run")) or now
    return "report" if now - first < constants.CLEANUP_REPORT_DAYS * DAY else "trash"


def run(current, dry_run):
    now = time.time()
    state = load_state()
    mode = "report" if dry_run else resolve_mode(state, now)
    found = candidates(now, current)
    if not dry_run and mode != "off":
        drop_old_index()
    result = {"first_run": state.get("first_run") or stamp(now), "ts": stamp(now), "mode": mode,
              "candidates": len(found), "candidate_bytes": sum(row[2] for row in found),
              "moved": 0, "purged": 0, "purged_bytes": 0, "over_cap": False}
    if mode == "trash":
        for name, path, size, reason in found:
            move_to_trash(name, path, size, reason)
            result["moved"] += 1
        for path, stat in sorted(trash_files(), key=lambda row: row[1].st_mtime):
            if now - stat.st_mtime > constants.CLEANUP_TRASH_DAYS * DAY:
                purge(path, stat.st_size, f"in trash {constants.CLEANUP_TRASH_DAYS}d")
                result["purged"] += 1
                result["purged_bytes"] += stat.st_size
        size = folder_bytes()
        for path, stat in sorted(trash_files(), key=lambda row: row[1].st_mtime):
            if size <= constants.CLEANUP_MAX_BYTES:
                break
            purge(path, stat.st_size, "size cap")
            result["purged"] += 1
            result["purged_bytes"] += stat.st_size
            size -= stat.st_size
    result["size"] = folder_bytes()
    result["trash_bytes"] = sum(stat.st_size for _, stat in trash_files())
    result["over_cap"] = result["size"] > constants.CLEANUP_MAX_BYTES
    result["pending"] = [{"path": str(path), "size": size, "reason": reason}
                         for _, path, size, reason in found[: constants.CLEANUP_REPORT_FILES]] if mode == "report" else []
    return result, found


def trashed_from():
    """Trash path -> the path its file was moved from, from the manifest."""
    origins = {}
    try:
        with open(constants.CLEANUP_MANIFEST, errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("action") == "trash" and row.get("to"):
                    origins[row["to"]] = row.get("path") or ""
    except OSError:
        pass
    return origins


def restore_path(path, origins):
    """Where a trashed file goes back: the project checklist folder it came from while that folder is there, else Kiasi's own folder."""
    origin = Path(origins.get(str(path)) or "")
    if project_checklist_folder(origin.parent):
        return origin
    return constants.LOG_DIR / path.parent.name / plain_name(path)


def restore(names):
    restored = 0
    origins = trashed_from()
    for path, stat in trash_files():
        if path.name.endswith(".tmp") or (names and path.name not in names and plain_name(path) not in names):
            continue
        dest = restore_path(path, origins)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.name.endswith(".gz"):
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(gzip.decompress(path.read_bytes()))
            os.replace(tmp, dest)
            path.unlink()
        else:
            os.replace(path, dest)
        os.utime(dest)  # restored files count as just used, so they are not moved again for a while
        record("restore", path=str(dest), size=stat.st_size)
        restored += 1
    return restored


def due():
    last = parse_ts(load_state().get("ts"))
    return last is None or time.time() - last >= constants.CLEANUP_INTERVAL_HOURS * 3600


def timed_out(signum, frame):
    raise TimeoutError(f"no result after {constants.CLEANUP_TIMEOUT_SECONDS}s")


def arm_timeout():
    """Give up after CLEANUP_TIMEOUT_SECONDS: an alarm signal where there is one, a timer thread on Windows."""
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(constants.CLEANUP_TIMEOUT_SECONDS)
        return
    def bail():
        sys.stderr.write(f"cleanup: no result after {constants.CLEANUP_TIMEOUT_SECONDS}s\n")
        os._exit(1)
    timer = threading.Timer(constants.CLEANUP_TIMEOUT_SECONDS, bail)
    timer.daemon = True
    timer.start()


def main():
    parser = argparse.ArgumentParser(description="Move Kiasi data files no session uses any more to trash.")
    parser.add_argument("--dry-run", action="store_true", help="list what would be moved, change nothing")
    parser.add_argument("--if-due", action="store_true", help=f"skip if the last run was under {constants.CLEANUP_INTERVAL_HOURS}h ago")
    parser.add_argument("--session", default="", help="the running session; its files are never touched")
    parser.add_argument("--restore", nargs="*", metavar="NAME", help="move files back from trash (all, or the names given)")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args()
    if not constants.LOG_DIR.is_dir():
        return 0
    if args.restore is None and not args.dry_run and (constants.CLEANUP_MODE == "off" or (args.if_due and not due())):
        return 0
    arm_timeout()
    with open(constants.CLEANUP_LOCK, "w") as lock:
        try:
            try_lock(lock)
        except OSError:
            return 0
        if args.restore is not None:
            count = restore(set(args.restore))
            if not args.quiet:
                print(f"restored {count} files from {constants.TRASH_DIR}")
            return 0
        try:
            result, found = run(args.session, args.dry_run)
        except Exception as exc:
            result, found = {**load_state(), "ts": stamp(), "error": str(exc)}, []
        if not args.dry_run:
            constants.CLEANUP_STATE.write_text(json.dumps(result, indent=1) + "\n")
            with open(constants.EVENT_LOG, "a") as fh:
                fh.write(json.dumps({"ts": result["ts"], "event": constants.CLEANUP_EVENT_NAME,
                                     **{k: result.get(k) for k in ("mode", "candidates", "moved", "purged", "size", "error") if k in result}}) + "\n")
    if "error" in result:
        print(f"cleanup failed: {result['error']}", file=sys.stderr if args.quiet else sys.stdout)
        return 1
    if args.quiet:
        return 0
    for name, path, size, reason in found:
        print(f"{'would move' if result['mode'] == 'report' else 'moved'}  {size:>9,}  {reason:<22}  {path}")
    print(f"mode {result['mode']}: {result['candidates']} files ({result['candidate_bytes'] / 1e6:.2f} MB) "
          f"{'would go to trash' if result['mode'] == 'report' else 'moved to trash'}, {result['purged']} deleted from trash; "
          f"folder {result['size'] / 1e6:.1f} MB, trash {result['trash_bytes'] / 1e6:.1f} MB"
          + ("; over the size cap" if result["over_cap"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
