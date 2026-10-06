#!/usr/bin/env python3
import gzip
import json
import os
import sys
import time
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime
from math import ceil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import constants
from reports.budget import install_day, project_of, transcript_files

SAVING_KINDS = ("cap", "paste_refused", "delegated", "pruned", "read_skipped")
CONTEXT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
MARK_KINDS = {"compaction": "compaction", "pruned": "pruned", "reread_check": "check", "delegated": "check", "turn_stop": "stop"}


def epoch_local(ts):
    return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))


def epoch_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def local_day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def local_stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def iter_entries(path):
    for line in open(path, errors="replace"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("timestamp"):
            yield entry


def is_prompt(message):
    content = message.get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
    return any(isinstance(b, dict) and b.get("type") == "text" and not b.get("text", "").lstrip().startswith("<") for b in blocks)


def usage_entries(path):
    seen = set()
    for entry in iter_entries(path):
        message = entry.get("message") or {}
        if entry.get("type") != "assistant" or not message.get("usage"):
            yield entry, None
            continue
        request = entry.get("requestId") or entry.get("uuid")
        if request in seen:
            yield entry, None
            continue
        seen.add(request)
        yield entry, message["usage"]


SYNTHETIC_MODEL = "<synthetic>"


def recall_texts(message):
    content = message.get("content")
    if not isinstance(content, list):
        return []
    texts = (json.dumps(block.get("input") or {}, ensure_ascii=False) for block in content if isinstance(block, dict) and block.get("type") == "tool_use")
    return [text for text in texts if any(mark in text.replace("\\\\", "/") for mark in constants.LENS_READBACK_MARKS)]


def miss_cause(gap, compacted, switched):
    if compacted:
        return "compaction"
    if switched:
        return "model switch"
    if gap > constants.LENS_CACHE_IDLE_LONG:
        return "idle over 1h"
    if gap > constants.LENS_CACHE_IDLE_SHORT:
        return "idle over 5 min"
    return "other"


def cache_scan(days):
    """Cache reads against all input per day, and every main-session miss, over this window and the one before it."""
    main, subs = transcript_files(days * 2)
    cutoff = time.time() - days * 2 * 86400
    per_day = defaultdict(lambda: {"read": 0, "input": 0})
    misses = []
    for path in main + subs:
        is_main = path in main
        last_t, compacted, last_model = None, False, None
        for entry, usage in usage_entries(path):
            if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
                compacted = True
            if not usage:
                continue
            t = epoch_iso(entry["timestamp"])
            creation, read = usage.get("cache_creation_input_tokens", 0), usage.get("cache_read_input_tokens", 0)
            model = (entry.get("message") or {}).get("model")
            if t >= cutoff:
                day = per_day[local_day(t)]
                day["read"] += read
                day["input"] += read + creation + usage.get("input_tokens", 0)
                if is_main and last_t is not None and creation >= constants.LENS_CACHE_MISS_TOKENS and creation > read:
                    switched = bool(last_model and model and model != last_model and model != SYNTHETIC_MODEL)
                    misses.append({"t": t, "prev": last_t, "tokens": creation, "cause": miss_cause(t - last_t, compacted, switched),
                                   "session": Path(path).stem, "project": project_of(path)})
            compacted = False
            if model and model != SYNTHETIC_MODEL:
                last_model = model
            last_t = t
    return per_day, misses


def attribute_misses(misses, events):
    """Compaction when one ran between the two steps; for "other", the config group a prompt in between saw change."""
    compactions = [(t, record.get("session_id") or "") for t, record in events if record.get("event") in ("compact", "plugin_compact")]
    changes = [(t, record.get("session_id"), record["config_changed"]) for t, record in events if record.get("event") == "prompt" and record.get("config_changed")]
    for miss in misses:
        if miss["cause"] != "compaction" and any(miss["prev"] <= t <= miss["t"] and who in ("", miss["session"]) for t, who in compactions):
            miss["cause"] = "compaction"
        elif miss["cause"] == "other":
            changed = next((parts for t, who, parts in changes if who == miss["session"] and miss["prev"] <= t <= miss["t"]), None)
            if changed:
                miss["cause"] = f"{changed[0]} changed"


def extra_cost(tokens):
    return int(tokens * (constants.CACHE_WRITE_PRICE - constants.CACHE_READ_PRICE) / constants.CACHE_READ_PRICE)


def cache_report(days, per_day, misses, paid):
    """Overview 03: hit rate against the target, avoidable misses against the window before, causes by cost."""
    start = time.time() - days * 86400
    current = [m for m in misses if m["t"] >= start]
    prior = [m for m in misses if m["t"] < start]
    window = {day: v for day, v in per_day.items() if day >= local_day(start)}
    read, total = sum(v["read"] for v in window.values()), sum(v["input"] for v in window.values())
    causes = defaultdict(lambda: {"count": 0, "tokens": 0})
    for miss in current:
        causes[miss["cause"]]["count"] += 1
        causes[miss["cause"]]["tokens"] += miss["tokens"]
    rows = sorted(({"cause": cause, **row, "extra": extra_cost(row["tokens"])} for cause, row in causes.items()),
                  key=lambda row: (row["cause"] == "compaction", -row["extra"]))
    avoidable = [m for m in current if m["cause"] != "compaction"]
    had_prior = any(day < local_day(start) and v["input"] for day, v in per_day.items())
    extra = extra_cost(sum(m["tokens"] for m in avoidable))
    return {"hit_rate": round(read / total, 4) if total else None, "hit_target": constants.LENS_HIT_TARGET,
            "hit_days": [{"day": day, "rate": round(v["read"] / v["input"], 4)} for day, v in sorted(window.items()) if v["input"]],
            "misses": len(current), "avoidable": len(avoidable),
            "prior_avoidable": sum(1 for m in prior if m["cause"] != "compaction") if had_prior else None,
            "tokens": sum(m["tokens"] for m in current), "extra": extra, "extra_share": round(extra / paid, 4) if paid else None,
            "write_price": constants.CACHE_WRITE_PRICE, "read_price": constants.CACHE_READ_PRICE, "causes": rows}


def recall_rows(events, sessions):
    rows = defaultdict(lambda: {"cuts": 0, "recalled": 0})
    for t, record in events:
        if record.get("event") != "cap" or not record.get("saved_path"):
            continue
        name = Path(record["saved_path"]).name
        row = rows[record.get("kind") or "other"]
        row["cuts"] += 1
        recalls = sessions.get(record.get("session_id", ""), {}).get("recalls", [])
        if any(when > t and name in text for when, text in recalls):
            row["recalled"] += 1
    return sorted(({"kind": kind, **row} for kind, row in rows.items()), key=lambda row: -row["cuts"])


def scan_sessions(days):
    main, subs = transcript_files(days)
    sessions = {}
    bill = Counter()
    main_bill, prompts = Counter(), Counter()
    for path in main:
        info = {"session": Path(path).stem, "project": project_of(path), "steps": [], "contexts": [], "prompts": [], "reread": 0, "recalls": []}
        for entry, usage in usage_entries(path):
            t = epoch_iso(entry["timestamp"])
            message = entry.get("message") or {}
            if entry.get("type") == "assistant":
                info["recalls"].extend((t, text) for text in recall_texts(message))
            if usage:
                info["steps"].append(t)
                info["contexts"].append(sum(usage.get(k, 0) for k in CONTEXT_KEYS))
                info["reread"] += usage.get("cache_read_input_tokens", 0)
                bill[local_day(t)] += usage.get("cache_read_input_tokens", 0)
                main_bill[local_day(t)] += usage.get("cache_read_input_tokens", 0)
            elif entry.get("type") == "user" and is_prompt(entry.get("message") or {}):
                info["prompts"].append(t)
                prompts[local_day(t)] += 1
        if info["steps"]:
            sessions[info["session"]] = info
    for path in subs:
        for entry, usage in usage_entries(path):
            if usage:
                bill[local_day(epoch_iso(entry["timestamp"]))] += usage.get("cache_read_input_tokens", 0)
    return sessions, bill, main_bill, prompts


def count_after(times, t):
    return len(times) - bisect_right(times, t)


def steps_until_next_prompt(steps, prompts, t):
    index = bisect_right(prompts, t)
    end = prompts[index] if index < len(prompts) else float("inf")
    return bisect_right(steps, end) - bisect_right(steps, t)


def read_events(days):
    cutoff = time.time() - days * 86400
    events = []
    try:
        for line in open(constants.EVENT_LOG):
            try:
                record = json.loads(line)
                t = epoch_local(record["ts"])
            except (ValueError, KeyError):
                continue
            if t >= cutoff:
                events.append((t, record))
    except OSError:
        pass
    return events


def tokens(chars):
    return int(chars // constants.CHARS_PER_TOKEN)


def action(record, kind, label, kept_out=0, later=0, saved=0, formula=""):
    return {"ts": record["ts"], "session": record.get("session_id") or record.get("agent_id") or "", "kind": kind, "label": label,
            "kept_out": int(kept_out), "later_steps": int(later), "saved": int(saved), "formula": formula, "record": record}


def classify(t, record, sessions):
    session = record.get("session_id", "")
    info = sessions.get(session, {"steps": [], "prompts": []})
    event = record.get("event")
    later = count_after(info["steps"], t)
    if event == "cap":
        kept = tokens(record.get("chars", 0) - record.get("shown_chars", 0))
        return action(record, "cap", f"{record.get('tool_name')} {record.get('kind')}: {record.get('label', '')}", kept, later, kept * later,
                      f"{kept:,} tokens kept out × {later} later steps in the session")
    if event == "read_skipped":
        kept = tokens(record.get("chars", 0))
        return action(record, "read_skipped", f"unchanged re-read of {record.get('path', '')} skipped", kept, later, kept * later,
                      f"{kept:,} tokens not re-sent × {later} later steps in the session")
    if event == "read_retry":
        return action(record, "read_retry", f"skipped read of {record.get('path', '')} repeated and let through")
    if event == "routed":
        return action(record, "routed", f"{record.get('tool_name')} pointed at {record.get('target')}: {record.get('label', '')}")
    if event == "route_retry":
        return action(record, "route_retry", f"{record.get('tool_name')} repeated after the routing and let through: {record.get('label', '')}")
    if event == "prompt":
        check = record.get("reread_check") or {}
        if record.get("paste_blocked"):
            kept = tokens(record.get("prompt_chars", 0))
            return action(record, "paste_refused", f"prompt of {record.get('prompt_chars', 0):,} chars refused, saved to disk", kept, later, kept * later,
                          f"{kept:,} tokens kept out × {later} later steps in the session")
        if check.get("mode") == "delegate":
            saved = max(0, check.get("here", 0) - check.get("delegated", 0))
            return action(record, "delegated", f"delegation instructed at {record.get('context_tokens', 0) // 1000}k context", 0, check.get("steps", 0), saved,
                          f"{check.get('here', 0):,} estimated here − {check.get('delegated', 0):,} estimated in a subagent")
        if check:
            return action(record, "reread_check", f"re-read numbers shown at {record.get('context_tokens', 0) // 1000}k context, {check.get('steps', 0)} steps per prompt")
        if record.get("paste_saved"):
            return action(record, "paste_saved", f"prompt of {record.get('prompt_chars', 0):,} chars saved to disk")
        if record.get("nudge"):
            return action(record, "nudge", f"nudge at {record.get('context_tokens', 0) // 1000}k context")
        return None
    if event == "plugin_compact":
        before, after = record.get("tokens_before", 0), record.get("tokens_after", 0)
        if record.get("mode") == "prune":
            return action(record, "pruned", f"transcript pruned {before // 1000}k → {after // 1000}k tokens, level {record.get('level')}, {record.get('messages')} messages", before - after, 0, before,
                          f"one summariser request over {before:,} tokens avoided")
        return action(record, "summary", f"pruning could not reach the target ({before // 1000}k → {after // 1000}k), built-in summary used")
    if event == "compact":
        return action(record, "compaction", f"{record.get('trigger')} compaction at {record.get('context_tokens', 0) // 1000}k context")
    if event in ("turn_warn", "turn_stop") and record.get("subagent"):
        return None  # a subagent's budget is not the turn budget, and its stop is not followed by the next prompt
    if event == "turn_warn":
        return action(record, "turn_warn", f"warned at {record.get('steps')} steps, {record.get('reread', 0) / 1e6:.1f}M re-read")
    if event == "turn_stop" and record.get("first"):
        after = steps_until_next_prompt(info["steps"], info["prompts"], t)
        return action(record, "turn_stop", f"stopped at {record.get('steps')} steps, {after} more before the next prompt", 0, after, 0,
                      f"steps between the stop and the next prompt: {after} (complies under {constants.LENS_COMPLY_STEPS})")
    if event == "loop":
        return action(record, "loop", f"{record.get('label', '')} failed {record.get('count')} times in one turn, Claude told to stop retrying")
    if event == "state" and (record.get("task") or record.get("files") or record.get("failures") or record.get("outputs") or record.get("checklists")):
        return action(record, "state", f"state re-injected after compaction: {record.get('files', 0)} files, {record.get('failures', 0)} failing, {record.get('outputs', 0)} saved outputs, {record.get('checklists', 0)} checklists")
    if event == "agent" and record.get("model_set"):
        return action(record, "agent_model", f"{record.get('agent_type')} set to {record.get('model_set')}")
    if event == "agent" and record.get("decision") == "ask":
        return action(record, "review_asked", f"repeat review by {record.get('agent_type')} turned into a question")
    if event == "stop" and record.get("note_written"):
        return action(record, "note", "session note written")
    if event == "session_start" and record.get("note"):
        return action(record, "recall", f"last note recalled ({record.get('source')})")
    return None


def bucket_label(low, high):
    return f"{low}" if high == low else f"{low}–{high}" if high else f"{low}+"


def bucket_index(buckets, value):
    for index, (low, high) in enumerate(buckets):
        if value >= low and (high is None or value <= high):
            return index
    return len(buckets) - 1


def prompt_steps(info):
    steps, contexts, prompts = info["steps"], info["contexts"], info["prompts"]
    rows = []
    for index, start in enumerate(prompts):
        end = prompts[index + 1] if index + 1 < len(prompts) else float("inf")
        first, last = bisect_right(steps, start), bisect_right(steps, end)
        rows.append((last - first, sum(contexts[first:last])))
    return rows


def step_histogram(sessions):
    hist = [{"label": bucket_label(low, high), "low": low, "prompts": 0, "bill": 0} for low, high in constants.LENS_STEP_BUCKETS]
    for info in sessions.values():
        for count, bill in prompt_steps(info):
            if count:
                row = hist[bucket_index(constants.LENS_STEP_BUCKETS, count)]
                row["prompts"] += 1
                row["bill"] += bill
    return hist


def cap_summary(actions):
    by_tool = defaultdict(lambda: {"count": 0, "kept_out": 0, "saved": 0})
    sizes = [{"label": bucket_label(low // 1000, high // 1000 if high else None) + " k", "count": 0} for low, high in constants.LENS_CUT_BUCKETS]
    for a in actions:
        if a["kind"] != "cap":
            continue
        key = f"{a['record'].get('tool_name')} · {a['record'].get('kind')}"
        by_tool[key]["count"] += 1
        by_tool[key]["kept_out"] += a["kept_out"]
        by_tool[key]["saved"] += a["saved"]
        sizes[bucket_index(constants.LENS_CUT_BUCKETS, a["kept_out"])]["count"] += 1
    rows = [{"tool": key, **row} for key, row in by_tool.items()]
    rows.sort(key=lambda r: -r["kept_out"])
    return {"by_tool": rows, "sizes": sizes}


def compaction_rows(events):
    plugin = [(t, r) for t, r in events if r.get("event") == "plugin_compact"]
    used = set()
    rows = []
    for t, record in events:
        if record.get("event") != "compact":
            continue
        row = {"ts": record["ts"], "session": record.get("session_id", "")[:8], "trigger": record.get("trigger"), "context": record.get("context_tokens", 0),
               "mode": "", "level": None, "before": None, "after": None}
        for index, (pt, plug) in enumerate(plugin):
            same_session = not plug.get("session_id") or plug.get("session_id") == record.get("session_id")
            if index not in used and same_session and abs(pt - t) <= constants.LENS_COMPACT_JOIN_SECONDS:
                used.add(index)
                row.update({"mode": plug.get("mode"), "level": plug.get("level"), "before": plug.get("tokens_before"), "after": plug.get("tokens_after")})
                break
        rows.append(row)
    for index, (pt, plug) in enumerate(plugin):
        if index not in used:
            rows.append({"ts": plug["ts"], "session": (plug.get("session_id") or "")[:8], "trigger": plug.get("trigger"), "context": plug.get("tokens_before", 0), "mode": plug.get("mode"),
                         "level": plug.get("level"), "before": plug.get("tokens_before"), "after": plug.get("tokens_after")})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def series_points(info, marks):
    contexts = info["contexts"]
    count = len(contexts)
    stride = max(1, ceil(count / constants.LENS_SERIES_POINTS))
    prompt_steps_set = {min(count - 1, bisect_left(info["steps"], t)) for t in info["prompts"]}
    keep = set(range(0, count, stride)) | prompt_steps_set | set(marks) | {count - 1}
    return [{"i": i, "context": contexts[i], "prompt": i in prompt_steps_set, "marks": marks.get(i, [])} for i in sorted(keep)]


def prompt_rows(info):
    """One row per prompt: the step it started at, how many steps it ran, its context at the start and its cost (context summed over its steps)."""
    steps, contexts, prompts = info["steps"], info["contexts"], sorted(info["prompts"])
    rows = []
    for k, t in enumerate(prompts):
        start = min(len(steps) - 1, bisect_left(steps, t))
        end = len(steps) if k + 1 >= len(prompts) else min(len(steps), bisect_left(steps, prompts[k + 1]))
        end = max(end, start + 1)
        rows.append({"n": k + 1, "ts": local_stamp(t), "step": start, "steps": end - start, "context": contexts[start], "cost": sum(contexts[start:end])})
    return rows


def postmortem(info, acts, steps_per_prompt, mean_context):
    """Deterministic ranking of what cost this session the most, with the rule that avoids it."""
    contexts, steps = info["contexts"], info["steps"]
    findings = []
    for i in range(1, len(contexts)):
        jump = contexts[i] - contexts[i - 1]
        later = len(steps) - i - 1
        if jump >= constants.PM_JUMP_TOKENS and later > 0:
            findings.append({"label": f"step {i + 1} added {jump // 1000}k tokens of context in one step",
                             "cost": jump * later,
                             "fix": "read by section, or derive the answer out of context with mcp__kiasi__distill or mcp__kiasi__run"})
    if contexts and contexts[0] >= constants.PM_STARTUP_TOKENS:
        findings.append({"label": f"the session started at {contexts[0] // 1000}k tokens before any work",
                         "cost": contexts[0] * len(steps),
                         "fix": "every step re-sends this; trim always-on MCP servers, startup hooks and CLAUDE.md"})
    long_turns = [c for c in steps_per_prompt if c >= constants.TURN_STOP_STEPS]
    if long_turns:
        findings.append({"label": f"{len(long_turns)} turn(s) ran {constants.TURN_STOP_STEPS}+ steps",
                         "cost": sum(long_turns) * mean_context,
                         "fix": "checkpoint the remaining work to a file or hand it to one subagent at the warning"})
    compactions = [a for a in acts if a["kind"] == "compaction"]
    if compactions:
        findings.append({"label": f"compacted {len(compactions)} time(s) mid-task",
                         "cost": sum(a["record"].get("context_tokens", 0) for a in compactions),
                         "fix": "one task per session: /clear at the task switch instead of compacting mid-task"})
    for a in acts:
        if a["kind"] == "read_retry":
            findings.append({"label": f"whole read of {a['record'].get('path', '')} repeated after the skip and let through",
                             "cost": 0,
                             "fix": "the file was unchanged and its first read is still in context; search that instead"})
        elif a["kind"] == "loop":
            findings.append({"label": a["label"], "cost": 0,
                             "fix": "change approach after two identical failures instead of retrying"})
    bash_caps = [a for a in acts if a["kind"] == "cap" and a["record"].get("kind") in ("bash", "bulk", "test")]
    if len(bash_caps) >= constants.PM_BASH_CAPS:
        findings.append({"label": f"{len(bash_caps)} long command outputs were capped in this session",
                         "cost": 0,
                         "fix": "run scan-only commands through mcp__kiasi__run so only a digest ever enters the conversation"})
    findings.sort(key=lambda f: -f["cost"])
    return findings[: constants.PM_MAX_FINDINGS]


def session_records(sessions, actions):
    per_session = defaultdict(list)
    for a in actions:
        per_session[a["session"]].append(a)
    records = []
    for session, info in sessions.items():
        marks = defaultdict(list)
        for a in per_session.get(session, []):
            kind = MARK_KINDS.get(a["kind"])
            if kind:
                marks[min(len(info["steps"]) - 1, bisect_left(info["steps"], epoch_local(a["ts"])))].append(kind)
        steps_per_prompt = [count for count, _ in prompt_steps(info) if count]
        mean_context = int(sum(info["contexts"]) / len(info["contexts"]))
        records.append({
            "session": session, "short": session[:8], "project": info["project"], "day": local_day(info["steps"][0]), "start": local_stamp(info["steps"][0]),
            "prompts": len(info["prompts"]), "steps": len(info["steps"]), "mean_steps": round(sum(steps_per_prompt) / max(1, len(steps_per_prompt)), 1),
            "long_turns": sum(1 for c in steps_per_prompt if c >= constants.TURN_STOP_STEPS),
            "startup": info["contexts"][0],
            "mean_context": mean_context, "peak": max(info["contexts"]), "bill": info["reread"],
            "findings": postmortem(info, per_session.get(session, []), steps_per_prompt, mean_context),
            "compactions": max(sum(1 for a in per_session.get(session, []) if a["kind"] == "compaction"),
                               sum(1 for a in per_session.get(session, []) if a["kind"] in ("pruned", "summary"))),
            "actions": len(per_session.get(session, [])), "saved": sum(a["saved"] for a in per_session.get(session, [])),
            "series": series_points(info, marks),
            "prompt_rows": prompt_rows(info),
        })
    records.sort(key=lambda r: -r["bill"])
    return records[: constants.LENS_MAX_SESSIONS]


def period_metrics(rows):
    turns = sum(r.get("turns", 0) + r.get("sub_turns", 0) for r in rows)
    reread = sum((r.get("main") or {}).get("cache_read_input_tokens", 0) + (r.get("sub") or {}).get("cache_read_input_tokens", 0) for r in rows)
    main_turns = sum(r.get("turns", 0) for r in rows)
    return {
        "days": len(rows),
        "turns": turns,
        "reread": reread,
        "reread_per_turn": int(reread / turns) if turns else 0,
        "reread_per_day": int(reread / len(rows)) if rows else None,
        "mean_context": int(sum(r.get("mean_context", 0) * r.get("turns", 0) for r in rows) / main_turns) if main_turns else 0,
        "high_share": round(sum(r.get("high_share", 0) * r.get("turns", 0) for r in rows) / main_turns, 3) if main_turns else 0,
    }


def history_rows():
    """Per-day rows from HISTORY_FILE, which keeps every day budget.py ever built; budget.json's window when there is none yet."""
    for path in (constants.HISTORY_FILE, constants.BUDGET_FILE):
        try:
            return json.loads(path.read_text()).get("per_day") or []
        except (OSError, ValueError, AttributeError):
            continue
    return []


def merge_savings(actions, days):
    """Merge this build's per-day savings into SAVINGS_FILE and return the totals over every day ever kept.

    Days the window covers in full replace the stored row; the partly covered first day and
    days outside the window keep the stored row unless the new one counts more actions."""
    try:
        stored = {r["day"]: r for r in json.loads(constants.SAVINGS_FILE.read_text())["per_day"]}
    except (OSError, ValueError, KeyError, TypeError):
        stored = {}
    rows = {}
    for a in actions:
        row = rows.setdefault(a["ts"][:10], {"day": a["ts"][:10], "saved": 0, "kept_out": 0, "actions": 0, "caps": 0})
        row["saved"] += a["saved"]
        row["kept_out"] += a["kept_out"]
        row["actions"] += 1
        row["caps"] += a["kind"] == "cap"
    first_full_day = local_day(time.time() - (days - 1) * 86400)
    for day, row in rows.items():
        if day >= first_full_day or day not in stored or row["actions"] >= stored[day].get("actions", 0):
            stored[day] = row
    kept = [stored[day] for day in sorted(stored)]
    constants.SAVINGS_FILE.write_text(json.dumps({"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "per_day": kept}, indent=1))
    return {"first_day": kept[0]["day"] if kept else None, "days": len(kept),
            **{key: sum(r.get(key, 0) for r in kept) for key in ("saved", "kept_out", "actions", "caps")}}


def since_install():
    """Before-and-after comparison built from the per-day history, so the baseline
    survives the report window and Claude Code's transcript retention.

    Days before the install day are the baseline; the install day itself and today
    (partial) are left out of the per-day averages but today counts per turn."""
    day = install_day()
    rows = history_rows()
    if not rows:
        return {"install_day": day, "before": None, "after": None}
    today = local_day(time.time())
    if not day:
        return {"install_day": None, "before": period_metrics([r for r in rows if r["day"] < today]), "after": None}
    before = [r for r in rows if r["day"] < day]
    after_full = [r for r in rows if day < r["day"] < today]
    after_all = [r for r in rows if r["day"] > day]
    after = period_metrics(after_full)
    per_turn = period_metrics(after_all)
    after["reread_per_turn"], after["turns"], after["mean_context"], after["high_share"] = per_turn["reread_per_turn"], per_turn["turns"], per_turn["mean_context"], per_turn["high_share"]
    b = period_metrics(before)
    factor = round(b["reread_per_turn"] / after["reread_per_turn"], 1) if b["reread_per_turn"] and after["reread_per_turn"] else None
    return {"install_day": day, "before": b if before else None, "after": after if after_all else None, "factor": factor,
            "first_day": rows[0]["day"], "history_days": len(rows)}


def storage_growth(files, now):
    """Bytes of saved files still on disk, per day written, over the last STORAGE_DAYS days."""
    days = [local_day(now - (constants.STORAGE_DAYS - 1 - i) * 86400) for i in range(constants.STORAGE_DAYS)]
    added = Counter()
    for _, _, stat in files:
        added[local_day(stat.st_mtime)] += stat.st_size
    older = sum(size for day, size in added.items() if day < days[0])
    rows, total = [], older
    for day in days:
        total += added.get(day, 0)
        rows.append({"day": day, "added": added.get(day, 0), "total": total})
    return rows


def storage_folders(cleanup, recalls, now, report_end):
    """Per cleaned folder: last use, next cleanup, read-back counts and its files, largest first; plus what moves when report-only ends."""
    texts = [text for _, text in recalls]
    out, moves = {}, {"count": 0, "bytes": 0, "files": []}
    for name, path, size, last, due in cleanup.schedule(""):
        auto = name not in constants.STORAGE_READBACK_FOLDERS
        read = auto or any(path.name in text for text in texts)
        row = out.setdefault(name, {"auto": auto, "last": 0, "next_due": None, "soon": 0, "soon_bytes": 0, "read": 0, "unread": 0, "unread_bytes": 0, "files": []})
        row["last"] = max(row["last"], last)
        row["next_due"] = min(row["next_due"] or due, due)
        if due < now + constants.STORAGE_DAYS * 86400:
            row["soon"] += 1
            row["soon_bytes"] += size
        row["read" if read else "unread"] += 1
        row["unread_bytes"] += 0 if read else size
        item = {"name": path.name, "folder": name, "bytes": size, "last": cleanup.stamp(last)[:16], "due": cleanup.stamp(max(due, now))[:16], "read": read}
        row["files"].append(item)
        if report_end and due < report_end:
            moves["count"] += 1
            moves["bytes"] += size
            moves["files"].append(item)
    for row in out.values():
        row["files"] = sorted(row["files"], key=lambda r: -r["bytes"])[: constants.STORAGE_LIST_ROWS]
        row["last"] = cleanup.stamp(row["last"])[:16]
        row["next_due"] = cleanup.stamp(max(row["next_due"], now))[:10]
    moves["files"] = sorted(moves["files"], key=lambda r: -r["bytes"])[: constants.STORAGE_LIST_ROWS]
    return out, moves


def storage(recalls=()):
    """The Storage tab: size per folder, growth per day, and for each cleaned folder when it was used, when it is cleaned and what was read back."""
    import cleanup
    try:
        last = json.loads(constants.CLEANUP_STATE.read_text())
    except (OSError, ValueError):
        last = {}
    folders = defaultdict(lambda: {"bytes": 0, "files": 0})
    for path in constants.LOG_DIR.rglob("*"):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(constants.LOG_DIR).parts
            row = folders[rel[0] if len(rel) > 1 else path.name]
            row["bytes"] += path.stat().st_size
            row["files"] += 1
    now = time.time()
    pending = [{"name": path.name, "folder": name, "bytes": size, "reason": reason}
               for name, path, size, reason in cleanup.candidates(now, "")]
    trash = [{"name": cleanup.plain_name(path), "folder": path.parent.name, "bytes": stat.st_size, "moved": cleanup.stamp(stat.st_mtime),
              "delete_on": cleanup.stamp(stat.st_mtime + constants.CLEANUP_TRASH_DAYS * 86400)[:10]}
             for path, stat in sorted(cleanup.trash_files(), key=lambda row: row[1].st_mtime)]
    history, done = [], Counter()
    try:
        lines = [json.loads(line) for line in constants.CLEANUP_MANIFEST.read_text(errors="replace").splitlines() if line.strip()]
        history = lines[::-1][:constants.STORAGE_HISTORY_ROWS]
        for row in lines:
            done[row.get("action")] += 1
            done[f"{row.get('action')}_bytes"] += row.get("size") or 0
    except (OSError, ValueError):
        pass
    first = cleanup.parse_ts(last.get("first_run"))
    managed = [(name, path, stat) for name, (folder, _) in cleanup.folders().items() for path, stat in cleanup.own_files(name, folder)]
    managed += [("notes", path, stat) for path, stat in cleanup.own_files("notes", constants.NOTES_DIR)]
    report_end = first + constants.CLEANUP_REPORT_DAYS * 86400 if first and constants.CLEANUP_MODE == "auto" and first + constants.CLEANUP_REPORT_DAYS * 86400 > now else None
    cleaned, report_moves = storage_folders(cleanup, recalls, now, report_end)
    return {"growth": storage_growth(managed, now), "cleaned": cleaned, "report_moves": report_moves,
            "size": sum(row["bytes"] for row in folders.values()), "max": constants.CLEANUP_MAX_BYTES, "mode": constants.CLEANUP_MODE,
            "last": {k: v for k, v in last.items() if k != "pending"},
            "report_until": cleanup.stamp(first + constants.CLEANUP_REPORT_DAYS * 86400)[:10] if first else None,
            "folders": sorted(({"name": k, **v} for k, v in folders.items()), key=lambda row: -row["bytes"]),
            "pending": pending[: constants.STORAGE_LIST_ROWS], "pending_count": len(pending), "pending_bytes": sum(row["bytes"] for row in pending),
            "trash": trash[-constants.STORAGE_LIST_ROWS:], "trash_count": len(trash), "trash_bytes": sum(row["bytes"] for row in trash),
            "history": history, "done": dict(done), "idle_days": constants.CLEANUP_IDLE_DAYS, "paste_idle_days": constants.CLEANUP_PASTE_IDLE_DAYS,
            "note_days": constants.CLEANUP_NOTE_DAYS, "report_days": constants.CLEANUP_REPORT_DAYS, "trash_days": constants.CLEANUP_TRASH_DAYS}


def build(days):
    sessions, bill, main_bill, prompts = scan_sessions(days)
    events = read_events(days * 2)
    cache_days, misses_all = cache_scan(days)
    attribute_misses(misses_all, events)
    events = [(t, record) for t, record in events if t >= time.time() - days * 86400]
    actions = []
    per_day = defaultdict(Counter)
    by_kind = defaultdict(lambda: {"count": 0, "saved": 0, "kept_out": 0})
    checks, pastes, budget_rows = [], [], []
    for t, record in events:
        item = classify(t, record, sessions)
        if not item:
            continue
        actions.append(item)
        by_kind[item["kind"]]["count"] += 1
        by_kind[item["kind"]]["saved"] += item["saved"]
        by_kind[item["kind"]]["kept_out"] += item["kept_out"]
        per_day[item["ts"][:10]][item["kind"]] += item["saved"]
        if item["kind"] in ("reread_check", "delegated"):
            check = record.get("reread_check") or {}
            checks.append({"ts": item["ts"], "session": item["session"][:8], "context": record.get("context_tokens", 0), "steps": check.get("steps"),
                           "here": check.get("here", 0), "delegated": check.get("delegated", 0), "mode": check.get("mode", "shown")})
        if item["kind"] in ("paste_saved", "paste_refused"):
            pastes.append({"ts": item["ts"], "session": item["session"][:8], "chars": record.get("prompt_chars", 0), "blocked": item["kind"] == "paste_refused",
                           "path": record.get("paste_saved") or record.get("paste_blocked") or ""})
        if item["kind"] in ("turn_warn", "turn_stop"):
            budget_rows.append({"ts": item["ts"], "session": item["session"][:8], "kind": item["kind"], "steps": record.get("steps"), "reread": record.get("reread", 0),
                                "after": item["later_steps"] if item["kind"] == "turn_stop" else None})
    first_day = local_day(time.time() - days * 86400)
    days_seen = sorted(day for day in set(bill) | set(per_day) if day >= first_day)
    stops = [a for a in actions if a["kind"] == "turn_stop"]
    complied = sum(1 for a in stops if a["later_steps"] < constants.LENS_COMPLY_STEPS)
    session_rows = session_records(sessions, actions)
    total_prompts = sum(len(s["prompts"]) for s in sessions.values())
    total_steps = sum(len(s["steps"]) for s in sessions.values())
    startups = defaultdict(list)
    for info in sessions.values():
        startups[local_day(info["steps"][0])].append(info["contexts"][0])
    all_startups = [value for values in startups.values() for value in values]
    misses, miss_tokens = defaultdict(Counter), Counter()
    for miss in misses_all:
        misses[local_day(miss["t"])][miss["cause"]] += 1
        miss_tokens[local_day(miss["t"])] += miss["tokens"]
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    all_time = merge_savings(actions, days)
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "days": days,
        "totals": {"saved": sum(a["saved"] for a in actions), "actions": len(actions), "kept_out": sum(a["kept_out"] for a in actions), "paid": sum(bill.values()),
                   "pruned": by_kind["pruned"]["count"], "summaries": by_kind["summary"]["count"], "compactions": by_kind["compaction"]["count"],
                   "stops": len(stops), "stops_complied": complied, "mean_steps_after_stop": round(sum(a["later_steps"] for a in stops) / max(1, len(stops)), 1),
                   "sessions": len(sessions), "prompts": total_prompts, "steps": total_steps, "mean_steps": round(total_steps / max(1, total_prompts), 1),
                   "reread_per_prompt": int(sum(main_bill.values()) / max(1, total_prompts)), "startup_mean": int(sum(all_startups) / max(1, len(all_startups))),
                   "reads_skipped": by_kind["read_skipped"]["count"], "reads_retried": by_kind["read_retry"]["count"]},
        "by_kind": dict(by_kind),
        "since": since_install(),
        "all_time": all_time,
        "per_day": [{"day": day, "paid": bill.get(day, 0), "prompts": prompts.get(day, 0), "reread_per_prompt": int(main_bill.get(day, 0) / max(1, prompts.get(day, 0))),
                     "sessions": len(startups.get(day, [])), "startup": int(sum(startups.get(day, [])) / max(1, len(startups.get(day, [])))),
                     "cache_misses": dict(misses.get(day, {})), "cache_miss_tokens": miss_tokens.get(day, 0),
                     "hit_rate": round(cache_days[day]["read"] / cache_days[day]["input"], 4) if cache_days.get(day, {}).get("input") else None,
                     **{k: v for k, v in per_day[day].items() if v}} for day in days_seen],
        "sessions": session_rows,
        "steps_hist": step_histogram(sessions),
        "caps": cap_summary(actions),
        "recall": recall_rows(events, sessions),
        "cache": cache_report(days, cache_days, misses_all, sum(bill.values())),
        "storage": storage([r for info in sessions.values() for r in info["recalls"]]),
        "compactions": compaction_rows(events),
        "checks": list(reversed(checks)),
        "pastes": list(reversed(pastes)),
        "budget_rows": list(reversed(budget_rows)),
        "actions": list(reversed(actions))[: constants.LENS_MAX_ACTIONS],
        "settings": {"warn_tokens": constants.CONTEXT_WARN_TOKENS, "hard_tokens": constants.CONTEXT_HARD_TOKENS, "turn_warn_steps": constants.turn_warn_steps(),
                     "turn_stop_steps": constants.TURN_STOP_STEPS, "comply_steps": constants.LENS_COMPLY_STEPS, "saving_kinds": SAVING_KINDS},
    }
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    constants.LENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = constants.LENS_FILE.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(report, ensure_ascii=False).encode(), compresslevel=6, mtime=0))
    os.replace(tmp, constants.LENS_FILE)
    (constants.LOG_DIR / "lens.json").unlink(missing_ok=True)  # the uncompressed copy older versions wrote
    return report


def print_report(report):
    t = report["totals"]
    print(f"last {report['days']} days: {t['actions']} kiasi actions, {t['saved'] / 1e6:.1f}M re-read tokens avoided against {t['paid'] / 1e6:.0f}M paid, "
          f"{t['kept_out'] // 1000}k tokens kept out, {t['pruned']} compactions pruned / {t['summaries']} summarised, {t['stops']} turns stopped "
          f"({t['stops_complied']} complied, mean {t['mean_steps_after_stop']} steps after), {t['sessions']} sessions, {t['mean_steps']} steps per prompt")
    for kind, row in sorted(report["by_kind"].items(), key=lambda x: -x[1]["saved"]):
        print(f"  {kind:14} {row['count']:4}  saved {row['saved'] / 1e6:6.1f}M")


if __name__ == "__main__":
    print_report(build(int(sys.argv[1]) if len(sys.argv) > 1 else constants.BUDGET_DAYS))
