"""The session receipt: what a session sent, what it would have sent without Kiasi, and the one thing that cost the most.

Written at SessionEnd, shown as a desktop notification then, and as one line at the next start in the same project.
The counterfactual is the same sum the report uses: every token a cap kept out would have sat in the context on
every later step of that session, so the avoided re-read is kept tokens times the steps that followed."""
import json
import os
import re
import time
from bisect import bisect_right
from pathlib import Path

from core import constants
from core.events import load_session, log_event, now_iso
from core.notify import notify_desktop
from core.transcript import entry_epoch


def fmt_tokens(n):
    """Tokens as a developer reads them: 1.8 k, 104 k, 6.9 M, 373 M, 2.84 B."""
    n = n or 0
    if n >= 1e9:
        return f"{n / 1e9:.2f} B"
    if n >= 1e8:
        return f"{n / 1e6:.0f} M"
    if n >= 1e6:
        return f"{n / 1e6:.1f} M"
    if n >= 1e4:
        return f"{n / 1e3:.0f} k"
    if n >= 1e3:
        return f"{n / 1e3:.1f} k"
    return str(int(n))


def assistant_times(transcript_path):
    """Epoch of every assistant step in the transcript, oldest first. A whole-file scan, parsing only assistant lines."""
    times = []
    if not transcript_path:
        return times
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") == "assistant":
                    times.append(entry_epoch(entry))
    except OSError:
        pass
    times.sort()
    return times


def cap_records(session_id):
    out = []
    try:
        with open(constants.EVENT_LOG, encoding="utf-8") as fh:
            for line in fh:
                if '"cap"' not in line or session_id not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("event") == "cap" and record.get("session_id") == session_id:
                    out.append(record)
    except OSError:
        pass
    return out


def cap_epoch(ts):
    """Event-log stamps are local time with no zone (now_iso); transcript stamps carry a Z or an offset."""
    if not ts:
        return 0
    if ts.endswith("Z") or re.search(r"[+-]\d\d:?\d\d$", ts):
        return entry_epoch({"timestamp": ts})
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0


def build_receipt(session_id, transcript_path, cwd, state):
    turns = (state or {}).get("turns") or {}
    sent = sum(int(t.get("reread") or 0) for t in turns.values())
    steps = sum(int(t.get("steps") or 0) for t in turns.values())
    times = assistant_times(transcript_path)
    kept, avoided, top = 0, 0, None
    for record in cap_records(session_id):
        tokens = max(0, (int(record.get("chars") or 0) - int(record.get("shown_chars") or 0)) // constants.CHARS_PER_TOKEN)
        later = len(times) - bisect_right(times, cap_epoch(record.get("ts", ""))) if times else 0
        kept += tokens
        avoided += tokens * later
        if top is None or tokens > top["tokens"]:
            top = {"tool": record.get("tool_name") or "", "label": (record.get("label") or "")[:80], "tokens": tokens}
    return {"ts": "", "session": session_id, "short": session_id[:8],
            "project": os.path.basename(cwd.rstrip("/\\")) if cwd else "", "cwd": cwd, "prompts": len(turns), "steps": steps,
            "sent": sent, "kept_out": kept, "avoided": avoided, "would_have": sent + avoided, "top": top}


def receipt_text(r):
    """Two or three short lines a developer can read in a notification."""
    if not r["sent"] and not r["kept_out"]:
        return ""
    head = f"Session {r['short']} sent {fmt_tokens(r['sent'])} of context across {r['steps']} steps."
    if r["kept_out"]:
        body = f"Without Kiasi about {fmt_tokens(r['would_have'])}: {fmt_tokens(r['kept_out'])} kept out, {fmt_tokens(r['avoided'])} of re-reads avoided."
    else:
        body = "Kiasi had nothing to cut in it."
    lines = [head, body]
    if r.get("top") and r["top"]["tokens"]:
        what = f"{r['top']['tool']} {r['top']['label']}".strip()
        lines.append(f"Costliest output: {what} ({fmt_tokens(r['top']['tokens'])} tokens cut).")
    return "\n".join(lines)


def write_receipt(r):
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    rows = read_receipts()
    rows.append(r)
    rows = rows[-constants.RECEIPTS_KEEP:]
    tmp = Path(str(constants.RECEIPTS_FILE) + ".tmp")
    tmp.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    os.replace(tmp, constants.RECEIPTS_FILE)


def read_receipts():
    rows = []
    try:
        for line in constants.RECEIPTS_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def last_receipt(cwd, exclude_session=""):
    """The newest receipt for this project from another session, or None."""
    for row in reversed(read_receipts()):
        if row.get("cwd") == cwd and row.get("session") != exclude_session:
            return row
    return None


def handle_session_end(payload):
    if payload.get("agent_id"):
        return None
    session_id = payload.get("session_id", "")
    receipt = build_receipt(session_id, payload.get("transcript_path"), payload.get("cwd") or "", load_session(session_id))
    receipt["ts"] = now_iso()
    text = receipt_text(receipt)
    if not text:
        return None
    write_receipt(receipt)
    log_event({"event": "receipt", "session_id": session_id, "sent": receipt["sent"], "kept_out": receipt["kept_out"], "avoided": receipt["avoided"]})
    notify_desktop("Kiasi receipt", text)
    return None
