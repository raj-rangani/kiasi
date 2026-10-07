"""Claude Code statusLine wrapper installed by /kiasi:limits setup.

Claude Code pipes session JSON to the status line on every update. This saves
its rate_limits (5-hour and weekly use, Pro and Max plans) for the dashboard
and draws the Kiasi line: model, context, steps of the turn budget, the plan
limits with the weekly pace, and tokens Kiasi kept out of this session. When
the user had a status line command before, it still runs on the same JSON and
the Kiasi part is added after its output. The installer copies this file next
to statusline-chain.json, so it must not import anything from the plugin.
"""
import json
import os
from contextlib import contextmanager
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHAIN_FILE = HERE / "statusline-chain.json"
POINTER_FILE = HERE / "data-dir"
RATE_LIMITS_NAME = "rate-limits.json"
SESSION_DIR_NAME = "sessions"
TIMEOUT_SECONDS = 10
GIT_TIMEOUT_SECONDS = 1
WEEK_SECONDS = 7 * 86400
MID_PERCENT = 50
WARN_PERCENT = 75
BAD_PERCENT = 90
HISTORY_NAME = "limits-history.jsonl"
HISTORY_MAX_BYTES = 256_000
HISTORY_KEEP_SECONDS = 14 * 86400
FORECAST_MIN_SPAN_SECONDS = 4 * 3600
PACE_SLACK = 5
APPEND_SEPARATOR = "  ‖ "
SEPARATOR = " │ "
BAR_WIDTH = 10
BAR_FULL = "█"
BAR_EMPTY = "░"
COLORS = {
    "ok": "\033[38;2;110;215;140m",
    "mid": "\033[38;2;235;205;100m",
    "warn": "\033[38;2;245;160;90m",
    "bad": "\033[38;2;245;110;110m",
    "model": "\033[38;2;235;235;230m",
    "folder": "\033[38;2;130;180;235m",
    "branch": "\033[38;2;200;150;220m",
    "label": "\033[38;2;160;160;160m",
    "turn": "\033[38;2;190;165;245m",
    "time": "\033[38;2;130;185;190m",
    "kiasi": "\033[38;2;240;190;100m",
    "dim": "\033[38;2;105;105;105m",
    "reset": "\033[0m",
}


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def data_dir(chain):
    candidates = [os.environ.get("CLAUDE_PLUGIN_DATA")]
    try:
        candidates.append(POINTER_FILE.read_text().strip())
    except OSError:
        pass
    candidates.append(chain.get("data_dir"))
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return HERE


def append_history(limits, target_dir, now):
    old = (read_json(target_dir / RATE_LIMITS_NAME) or {}).get("rate_limits") or {}
    lines = []
    for key, window in limits.items():
        used = (window or {}).get("used_percentage")
        if used is None:
            continue
        before = old.get(key) or {}
        if before.get("used_percentage") == used and before.get("resets_at") == window.get("resets_at"):
            continue
        lines.append(json.dumps({"ts": now, "key": key, "used": used,
                                 "resets_at": window.get("resets_at")}))
    if not lines:
        return
    path = target_dir / HISTORY_NAME
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    if path.stat().st_size > HISTORY_MAX_BYTES:
        prune_history(path, now)


def prune_history(path, now):
    keep = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                if json.loads(line).get("ts", 0) >= now - HISTORY_KEEP_SECONDS:
                    keep.append(line)
            except ValueError:
                continue
    fd, tmp = tempfile.mkstemp(prefix=".limits-history.", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.writelines(keep)
    os.replace(tmp, path)


@contextmanager
def limits_lock(target_dir):
    """One status line at a time reads the last limits, appends what changed and replaces the file, or two sessions log
    the same reading twice. Waits at most a second, and runs unlocked where the file system cannot lock."""
    handle = None
    try:
        import fcntl
        handle = open(target_dir / ".limits.lock", "a")
        deadline = time.monotonic() + 1
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
    except (ImportError, OSError):
        pass
    try:
        yield
    finally:
        if handle:
            handle.close()


def save_rate_limits(payload, target_dir):
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict) or not limits:
        return
    now = int(time.time())
    with limits_lock(target_dir):
        try:
            append_history(limits, target_dir, now)
        except (OSError, TypeError, ValueError):
            pass
        fd, tmp = tempfile.mkstemp(prefix=".rate-limits.", dir=target_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"updated": now, "rate_limits": limits}, handle)
        os.replace(tmp, target_dir / RATE_LIMITS_NAME)


def paint(text, tone):
    if not tone or os.environ.get("NO_COLOR"):
        return text
    return f"{COLORS[tone]}{text}{COLORS['reset']}"


def tone_for(value, warn, bad, mid=None):
    if value >= bad:
        return "bad"
    if value >= warn:
        return "warn"
    return "mid" if mid is not None and value >= mid else "ok"


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{round(n / 1000)}k" if n >= 1000 else str(int(n))


def load_session(target_dir, session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    return read_json(target_dir / SESSION_DIR_NAME / f"{safe}.json")


def bar(percent, tone):
    filled = max(0, min(BAR_WIDTH, round(percent * BAR_WIDTH / 100)))
    return paint(BAR_FULL * filled, tone) + paint(BAR_EMPTY * (BAR_WIDTH - filled), "dim")


def gauge(label, percent, tone, suffix=""):
    return f"{paint(label, 'label')} {bar(percent, tone)} {paint(f'{round(percent)}%', tone)}{suffix}"


def reset_text(resets):
    if not resets:
        return ""
    when = time.localtime(float(resets))
    clock = time.strftime("%I:%M%p", when).lstrip("0").lower()
    same_day = time.strftime("%Y%m%d", when) == time.strftime("%Y%m%d")
    return paint(f" ⟳ {clock if same_day else time.strftime('%a ', when) + clock}", "time")


def folder_part(payload):
    folder = (payload.get("workspace") or {}).get("current_dir") or payload.get("cwd")
    return Path(folder).name if folder else None


def branch_part(payload):
    folder = (payload.get("workspace") or {}).get("current_dir") or payload.get("cwd")
    if not folder:
        return None
    for args in (["branch", "--show-current"], ["rev-parse", "--short", "HEAD"]):
        try:
            result = subprocess.run(["git", "-C", folder, *args], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        name = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0 and name:
            return f"{paint('git', 'label')} {paint(name, 'branch')}"
    return None


def context_part(payload):
    window = payload.get("context_window") or {}
    usage = window.get("current_usage") or {}
    used = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    size = int(window.get("context_window_size") or 0)
    if not used:
        return None
    if not size:
        return f"ctx {fmt_tokens(used)}"
    percent = used * 100 / size
    return gauge("ctx", percent, tone_for(percent, WARN_PERCENT, BAD_PERCENT, MID_PERCENT), paint(f" {fmt_tokens(used)}", "dim"))


def turn_part(payload, session):
    key = Path(payload.get("transcript_path") or "main").stem
    turn = (session.get("turns") or {}).get(key) or {}
    budget = session.get("turn_budget") or {}
    steps, stop = int(turn.get("steps") or 0), int(budget.get("stop") or 0)
    if not steps or not stop or budget.get("mode") == "off":
        return None
    tone = tone_for(steps, int(budget.get("warn") or stop), stop)
    return f"{paint('turn', 'label')} {paint(f'{steps}/{stop}', 'turn' if tone == 'ok' else tone)}"


def weekly_run_out(target_dir, window):
    used = (window or {}).get("used_percentage")
    resets = (window or {}).get("resets_at")
    if used is None or not resets:
        return ""
    start = float(resets) - WEEK_SECONDS
    first = last = None
    try:
        with open(target_dir / HISTORY_NAME, encoding="utf-8") as fh:
            for line in fh:
                try:
                    point = json.loads(line)
                except ValueError:
                    continue
                if point.get("key") != "seven_day" or point.get("used") is None or point.get("ts", 0) < start:
                    continue
                first = first or point
                last = point
    except OSError:
        return ""
    if not first or last is first or last["ts"] - first["ts"] < FORECAST_MIN_SPAN_SECONDS:
        return ""
    climb = last["used"] - first["used"]
    if climb <= 0:
        return ""
    run_out = last["ts"] + (100 - last["used"]) * (last["ts"] - first["ts"]) / climb
    if run_out >= float(resets):
        return ""
    when = time.localtime(run_out)
    clock = time.strftime("%I%p", when).lstrip("0").lower()
    same_day = time.strftime("%Y%m%d", when) == time.strftime("%Y%m%d")
    return paint(f" ✖ {clock if same_day else time.strftime('%a ', when) + clock}", "bad")


def limit_part(label, window, span=None, extra=""):
    used = (window or {}).get("used_percentage")
    if used is None:
        return None
    used = float(used)
    resets = (window or {}).get("resets_at")
    pace = ""
    if span and resets:
        gone = 100 * (1 - (float(resets) - time.time()) / span)
        if used - gone > PACE_SLACK:
            pace = paint(f" ▲{round(used - gone)}", "warn")
    return gauge(label, used, tone_for(used, WARN_PERCENT, BAD_PERCENT, MID_PERCENT), pace + extra + reset_text(resets))


def kept_part(session):
    kept = int(session.get("kept_tokens") or 0)
    return paint(f"kiasi −{fmt_tokens(kept)}", "kiasi") if kept else None


def kiasi_line(payload, session, standalone, target_dir):
    limits = payload.get("rate_limits") or {}
    parts = [
        paint((payload.get("model") or {}).get("display_name") or "Claude", "model") if standalone else None,
        paint(folder_part(payload), "folder") if standalone and folder_part(payload) else None,
        branch_part(payload) if standalone else None,
        context_part(payload),
        turn_part(payload, session),
        limit_part("5h", limits.get("five_hour")),
        limit_part("wk", limits.get("seven_day"), WEEK_SECONDS,
                   weekly_run_out(target_dir, limits.get("seven_day"))),
        kept_part(session),
    ]
    return paint(SEPARATOR, "dim").join(p for p in parts if p)


def main():
    raw = sys.stdin.buffer.read()
    chain = read_json(CHAIN_FILE)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    target_dir = data_dir(chain)
    try:
        save_rate_limits(payload, target_dir)
    except (OSError, TypeError, ValueError):
        pass
    session = load_session(target_dir, payload.get("session_id"))
    command = ((chain.get("previous") or {}).get("command") or "").strip()
    if not command:
        sys.stdout.write(kiasi_line(payload, session, True, target_dir))
        return 0
    try:
        result = subprocess.run(command, shell=True, input=raw, stdout=subprocess.PIPE,
                                timeout=TIMEOUT_SECONDS, check=False)
        own = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
    except (OSError, subprocess.SubprocessError):
        own = ""
    ours = kiasi_line(payload, session, not own, target_dir)
    sys.stdout.write(f"{own}{paint(APPEND_SEPARATOR, 'dim')}{ours}" if own and ours else own or ours)
    return 0


if __name__ == "__main__":
    sys.exit(main())
