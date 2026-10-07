import glob
import json
import os
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import constants
from core.events import unlock, wait_lock

SYNTHETIC_MODEL = "<synthetic>"
SKIPPED = Counter()  # what a build could not read: files that vanished, JSON lines that are no event, unreadable timestamps
_SKIPPED_KEYS = set()  # a build reads a file more than once; each unreadable thing counts once


def skip(kind, key):
    if (kind, key) not in _SKIPPED_KEYS:
        _SKIPPED_KEYS.add((kind, key))
        SKIPPED[kind] += 1


def reset_skips():
    SKIPPED.clear()
    _SKIPPED_KEYS.clear()


def read_lines(path):
    """The JSON objects of a transcript. A file deleted since the glob, a line that is no JSON object and a blank
    line are skipped; all but the blank lines are counted (skip), so the build never fails on them."""
    try:
        handle = open(path, errors="replace")
    except OSError:
        skip("files", str(path))
        return
    with handle:
        for number, line in enumerate(handle):
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if isinstance(entry, dict):
                yield entry
            elif line.strip():
                skip("lines", (str(path), number))


def write_atomic(path, text):
    """Write beside the file, then replace it: a reader sees the old file or the new one, never half of it."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


@contextmanager
def file_lock(path, seconds=120):
    """Hold path's lock file while a build reads, merges and writes path, so two builds (the dashboard thread and Sync) cannot lose each other's rows."""
    try:
        handle = open(path.with_name(path.name + ".lock"), "a")
    except OSError:
        yield
        return
    with handle:
        held = wait_lock(handle, seconds)
        try:
            yield
        finally:
            if held:
                unlock(handle)


def local_day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def window_start(days):
    """Epoch of local midnight `days` days ago: every report counts whole local days from here."""
    return time.mktime(time.strptime(local_day(time.time() - days * 86400), "%Y-%m-%d"))


def first_ask(entry, asked):
    """False for an entry that is no new prompt: the summary of a built-in compaction, or a prompt already counted.
    A pruned compaction writes the kept messages back into the transcript: the prompts under their old promptId,
    the answers as zero-usage entries of the synthetic model."""
    if entry.get("isCompactSummary"):
        return False
    prompt = entry.get("promptId")
    if prompt is None:
        return True
    if prompt in asked:
        return False
    asked.add(prompt)
    return True

USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def transcript_files(days):
    cutoff = window_start(days)
    root = str(constants.TRANSCRIPT_ROOT)

    def fresh(path):
        try:
            return os.path.getmtime(path) > cutoff
        except OSError:  # deleted since the glob
            skip("files", path)
            return False
    main = [f for f in glob.glob(f"{root}/*/*.jsonl") if fresh(f)]
    subs = [f for f in glob.glob(f"{root}/*/*/subagents/*.jsonl") + glob.glob(f"{root}/*/subagents/*.jsonl") if fresh(f)]
    return main, subs


def install_day():
    """The day of the first event ever logged: the day Kiasi was switched on."""
    try:
        with open(constants.EVENT_LOG) as fh:
            for line in fh:
                if line.strip():
                    return json.loads(line).get("ts", "")[:10] or None
    except (OSError, ValueError, AttributeError):
        return None
    return None


HISTORY_COUNTING = 3  # 2: the messages a pruned compaction writes back are not steps; 3: days are local days, not UTC days
HISTORY_RECOUNT_DAYS = 45


def day_rows(per_day):
    rows = []
    for day in sorted(per_day):
        b = per_day[day]
        rows.append({"day": day, "turns": b["turns"], "sub_turns": b["sub_turns"], "mean_context": b["context_sum"] // max(1, b["turns"]), "high_share": round(b["high_turns"] / max(1, b["turns"]), 2),
                         "main": dict(b["main"]), "sub": dict(b["sub"])})
    return rows


def merge_history(rows, days):
    """Merge this build's per-day rows into HISTORY_FILE and return every row ever kept, oldest first.

    Days the window covers in full replace the stored row; the partly covered first day and
    days outside the window keep the stored row unless the new one counts more steps. A file written
    under an older HISTORY_COUNTING is recounted once, from the transcripts that still exist: it replaces every
    stored day from the oldest surviving transcript day on; older days, whose transcripts are gone, keep their
    stored row as it was (UTC-keyed, replays counted)."""
    with file_lock(constants.HISTORY_FILE):
        return _merge_history(rows, days)


def _merge_history(rows, days):
    try:
        stored_file = json.loads(constants.HISTORY_FILE.read_text())
        history = {r["day"]: r for r in stored_file["per_day"]}
    except (OSError, ValueError, KeyError, TypeError):
        stored_file, history = {}, {}
    if history and stored_file.get("counting") != HISTORY_COUNTING:
        rows, days = day_rows(scan(HISTORY_RECOUNT_DAYS)[0]), HISTORY_RECOUNT_DAYS
        if rows:
            history = {day: row for day, row in history.items() if day < rows[0]["day"]}
    first_full_day = local_day(time.time() - (days - 1) * 86400)
    for row in rows:
        stored = history.get(row["day"])
        steps = row["turns"] + row["sub_turns"]
        if row["day"] >= first_full_day or stored is None or steps >= stored.get("turns", 0) + stored.get("sub_turns", 0):
            history[row["day"]] = row
    kept = [history[day] for day in sorted(history)]
    write_atomic(constants.HISTORY_FILE, json.dumps({"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "counting": HISTORY_COUNTING, "per_day": kept}, indent=1))
    return kept


def project_of(path):
    return Path(path).relative_to(constants.TRANSCRIPT_ROOT).parts[0]


def day_of(entry):
    """The local day of an entry's timestamp (the day the user saw it on); "" for none or an unreadable one, which is counted."""
    stamp = entry.get("timestamp")
    if not stamp:
        return ""
    try:
        return local_day(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        skip("timestamps", stamp)
        return ""


def blank_day():
    return {"main": Counter(), "sub": Counter(), "turns": 0, "sub_turns": 0, "context_sum": 0, "high_turns": 0}


def scan(days):
    main, subs = transcript_files(days)
    min_day = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    per_day = defaultdict(blank_day)
    sessions = []
    projects = defaultdict(Counter)
    big_outputs = []
    pastes = 0
    paste_chars = 0
    compactions = 0
    tool_names = {}
    prompt_count = 0
    long_turns = 0
    long_turn_reread = 0
    for path in main:
        seen = set()
        turns = 0
        peak = 0
        context_sum = 0
        prev = 0
        session_compactions = 0
        session_pastes = 0
        session_prompts = 0
        turn_steps = 0
        turn_reread = 0
        first_day = None
        asked, results = set(), set()
        for entry in read_lines(path):
            kind = entry.get("type")
            message = entry.get("message") or {}
            if kind == "assistant":
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_names[block.get("id")] = (block.get("name"), block.get("input") or {})
                usage = message.get("usage") or {}
                request = entry.get("requestId") or entry.get("uuid")
                if not usage or request in seen or message.get("model") == SYNTHETIC_MODEL:
                    continue
                seen.add(request)
                day = day_of(entry)
                if day < min_day:
                    continue
                first_day = first_day or day
                context = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
                if prev > 150_000 and context < prev * 0.5:
                    session_compactions += 1
                prev = context
                turns += 1
                turn_steps += 1
                turn_reread += context
                peak = max(peak, context)
                context_sum += context
                bucket = per_day[day]
                bucket["turns"] += 1
                bucket["context_sum"] += context
                bucket["high_turns"] += 1 if context >= constants.BUDGET_HIGH_CONTEXT_TOKENS else 0
                projects[project_of(path)]["turns"] += 1
                for key in USAGE_KEYS:
                    bucket["main"][key] += usage.get(key) or 0
                    projects[project_of(path)][key] += usage.get(key) or 0
            elif kind == "user":
                if day_of(entry) < min_day:
                    continue
                content = message.get("content")
                blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
                typed = any(isinstance(b, dict) and b.get("type") == "text" and not b.get("text", "").lstrip().startswith("<") for b in blocks)
                if typed and not first_ask(entry, asked):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and not block.get("text", "").lstrip().startswith("<"):
                        session_prompts += 1
                        long_turns += 1 if turn_steps >= constants.TURN_STOP_STEPS else 0
                        long_turn_reread += turn_reread if turn_steps >= constants.TURN_STOP_STEPS else 0
                        turn_steps = 0
                        turn_reread = 0
                    if block.get("type") == "text" and len(block.get("text", "")) >= constants.PASTE_MIN_CHARS and not block["text"].lstrip().startswith("<"):
                        pastes += 1
                        session_pastes += 1
                        paste_chars += len(block["text"])
                    elif block.get("type") == "tool_result":
                        if block.get("tool_use_id") in results:
                            continue
                        results.add(block.get("tool_use_id"))
                        raw = block.get("content")
                        text = raw if isinstance(raw, str) else " ".join(x.get("text", "") for x in raw or [] if isinstance(x, dict))
                        if len(text) >= constants.BUDGET_BIG_OUTPUT_CHARS:
                            name, tool_input = tool_names.get(block.get("tool_use_id"), ("?", {}))
                            label = str(tool_input.get("command") or tool_input.get("file_path") or tool_input.get("url") or tool_input.get("pattern") or "")[:90]
                            big_outputs.append({"chars": len(text), "tool": name, "label": label, "session": Path(path).stem[:8]})
        compactions += session_compactions
        long_turns += 1 if turn_steps >= constants.TURN_STOP_STEPS else 0
        long_turn_reread += turn_reread if turn_steps >= constants.TURN_STOP_STEPS else 0
        prompt_count += session_prompts
        if turns:
            sessions.append({"session": Path(path).stem[:8], "project": project_of(path)[:40], "day": first_day, "turns": turns, "peak": peak, "mean": context_sum // turns, "compactions": session_compactions, "pastes": session_pastes,
                             "steps_per_prompt": round(turns / max(1, session_prompts), 1)})
    for path in subs:
        seen = set()
        for entry in read_lines(path):
            if entry.get("type") != "assistant":
                continue
            usage = (entry.get("message") or {}).get("usage") or {}
            request = entry.get("requestId") or entry.get("uuid")
            if not usage or request in seen or day_of(entry) < min_day or entry["message"].get("model") == SYNTHETIC_MODEL:
                continue
            seen.add(request)
            bucket = per_day[day_of(entry)]
            bucket["sub_turns"] += 1
            for key in USAGE_KEYS:
                bucket["sub"][key] += usage.get(key) or 0
    return per_day, sessions, projects, big_outputs, pastes, paste_chars, compactions, {"prompts": prompt_count, "long_turns": long_turns, "long_turn_reread": long_turn_reread}


def kiasi_actions(days):
    cutoff = window_start(days)
    actions = Counter()
    saved_chars = 0
    try:
        for line in open(constants.EVENT_LOG):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            try:
                if time.mktime(time.strptime(record["ts"], "%Y-%m-%dT%H:%M:%S")) < cutoff:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            event = record.get("event")
            if event == "cap":
                actions["outputs capped"] += 1
                saved_chars += record.get("chars", 0) - record.get("shown_chars", 0)
            elif event == "prompt" and record.get("nudge"):
                actions["nudges shown"] += 1
            elif event == "prompt" and record.get("paste_saved"):
                actions["pastes saved"] += 1
            elif event == "prompt" and record.get("paste_blocked"):
                actions["pastes refused"] += 1
            elif event == "prompt" and (record.get("reread_check") or {}).get("mode") == "delegate":
                actions["delegations instructed"] += 1
            elif event == "turn_warn":
                actions["subagent budget warnings" if record.get("subagent") else "turn budget warnings"] += 1
            elif event == "turn_stop" and record.get("first"):
                actions["subagents paused" if record.get("subagent") else "turns paused"] += 1
            elif event == "turn_over":
                actions["subagents over budget" if record.get("subagent") else "turns over budget"] += 1
            elif event == "turn_resume":
                actions["pauses resumed" if record.get("mode") == "resume" else "pauses skipped"] += 1
            elif event == "agent" and record.get("decision") == "ask":  # logged before the outcome, and with a model set as well
                actions["repeat reviews questioned"] += 1
            elif event == "agent" and record.get("model_set"):
                actions["subagent models set"] += 1
            elif event == "compact":
                actions["compactions instructed"] += 1
            elif event == "stop" and record.get("note_written"):
                actions["notes written"] += 1
    except OSError:
        pass
    return {"counts": dict(actions), "chars_kept_out": saved_chars}


def build(days):
    reset_skips()
    per_day, sessions, projects, big_outputs, pastes, paste_chars, compactions, steps = scan(days)
    days_out = day_rows(per_day)
    total_main = Counter()
    total_sub = Counter()
    for b in per_day.values():
        total_main.update(b["main"])
        total_sub.update(b["sub"])
    turns = sum(b["turns"] for b in per_day.values())
    context_sum = sum(b["context_sum"] for b in per_day.values())
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "days": days,
        "install_day": install_day(),
        "totals": {"main": dict(total_main), "sub": dict(total_sub), "turns": turns, "sub_turns": sum(b["sub_turns"] for b in per_day.values()),
                   "mean_context": context_sum // max(1, turns), "high_share": round(sum(b["high_turns"] for b in per_day.values()) / max(1, turns), 2),
                   "compactions": compactions, "pastes": pastes, "paste_chars": paste_chars, "sessions": len(sessions),
                   "prompts": steps["prompts"], "mean_steps": round(turns / max(1, steps["prompts"]), 1), "long_turns": steps["long_turns"], "long_turn_reread": steps["long_turn_reread"]},
        "per_day": days_out,
        "sessions": sorted(sessions, key=lambda s: -s["turns"] * s["mean"])[: constants.BUDGET_TOP_SESSIONS],
        "projects": [{"project": p, **dict(c)} for p, c in sorted(projects.items(), key=lambda x: -x[1]["cache_read_input_tokens"])],
        "big_outputs": sorted(big_outputs, key=lambda o: -o["chars"])[: constants.BUDGET_TOP_OUTPUTS],
        "kiasi": kiasi_actions(days),
        "settings": {"warn_tokens": constants.CONTEXT_WARN_TOKENS, "hard_tokens": constants.CONTEXT_HARD_TOKENS, "high_context_tokens": constants.BUDGET_HIGH_CONTEXT_TOKENS,
                     "turn_budget_mode": constants.TURN_BUDGET_MODE, "turn_stop_steps": constants.TURN_STOP_STEPS, "turn_stop_tokens": constants.TURN_STOP_TOKENS, "big_output_chars": constants.BUDGET_BIG_OUTPUT_CHARS},
    }
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    report["history_days"] = len(merge_history(days_out, days))
    report["skipped"] = dict(SKIPPED)
    write_atomic(constants.BUDGET_FILE, json.dumps(report, indent=1))
    return report


def fmt_m(tokens):
    return f"{tokens / 1e6:6.1f}M"


def print_report(report):
    t = report["totals"]
    print(f"last {report['days']} days: {t['sessions']} sessions, {t['turns']} turns (+{t['sub_turns']} subagent), mean context {t['mean_context'] // 1000}k, {int(t['high_share'] * 100)}% of turns over {constants.BUDGET_HIGH_CONTEXT_TOKENS // 1000}k, {t['compactions']} compactions, {t['pastes']} pastes, {t['mean_steps']} steps per prompt, {t['long_turns']} turns of {constants.TURN_STOP_STEPS}+ steps re-read {fmt_m(t['long_turn_reread'])}")
    print(f"  re-read (cache read)  main {fmt_m(t['main'].get('cache_read_input_tokens', 0))}  subagents {fmt_m(t['sub'].get('cache_read_input_tokens', 0))}")
    print(f"  written (cache write) main {fmt_m(t['main'].get('cache_creation_input_tokens', 0))}  subagents {fmt_m(t['sub'].get('cache_creation_input_tokens', 0))}")
    print(f"  output                main {fmt_m(t['main'].get('output_tokens', 0))}  subagents {fmt_m(t['sub'].get('output_tokens', 0))}")
    print("  per day: " + "  ".join(f"{d['day'][5:]} {d['main'].get('cache_read_input_tokens', 0) / 1e6:.0f}M/{d['mean_context'] // 1000}k" for d in report["per_day"]))
    print("  kiasi: " + (", ".join(f"{k} {v}" for k, v in report["kiasi"]["counts"].items()) or "no actions yet") + f", {report['kiasi']['chars_kept_out'] // 1000}k chars kept out")


if __name__ == "__main__":
    print_report(build(int(sys.argv[1]) if len(sys.argv) > 1 else constants.BUDGET_DAYS))
