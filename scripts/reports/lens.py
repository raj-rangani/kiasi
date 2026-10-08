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
from reports.budget import SKIPPED, SYNTHETIC_MODEL, file_lock, first_ask, install_day, project_of, read_lines, reset_skips, skip, transcript_files, window_start, write_atomic

SAVING_KINDS = ("cap", "paste_refused", "delegated", "pruned", "read_skipped")
CONTEXT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
PROBLEM_KINDS = {
    "pause_record": "A pause could not be saved, so a later 'continue' has nothing to resume.",
    "pause_no_task": "A pause was saved without the task, because the transcript could not be read.",
    "paused_file": "A saved pause could not be read back.",
    "lock_timeout": "State was written without the lock after waiting for it.",
    "bad_setting": "A value in .kiasi.json was ignored.",
    "checklist_folder": "The checklist folder could not be created.",
}
RUNAWAY_TOKENS = 200_000
AGENT_TOOLS = ("Agent", "Task")
WINDOW_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
PREFIX_PIECES = 12
PREFIX_KINDS = ("CLAUDE.md files", "plugins and skills", "MCP tool schemas", "agents list", "memory", "other")
ATTACHMENT_KINDS = {"skill_listing": "plugins and skills", "deferred_tools_delta": "MCP tool schemas", "mcp_instructions_delta": "MCP tool schemas",
                    "agent_listing_delta": "agents list", "instructions": "CLAUDE.md files"}
REMINDER_HINTS = (("MEMORY.md", "memory"), ("CLAUDE.md", "CLAUDE.md files"), ("skills are available", "plugins and skills"), ("mcp__", "MCP tool schemas"),
                  ("deferred tools", "MCP tool schemas"), ("agent types", "agents list"))
MARK_KINDS = {"compaction": "compaction", "pruned": "pruned", "reread_check": "check", "delegated": "check", "turn_stop": "stop"}


def synthetic_kind(entry, message):
    """What a <synthetic> assistant entry is. Replays are the bulk: Claude Code re-writes the loaded history in one burst on resume. The rest are placeholders for a turn that never produced an answer."""
    content = message.get("content")
    text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict))
    text = text.strip()
    if entry.get("isApiErrorMessage") or text.startswith("API Error"):
        return "api_error"
    if text.startswith("[Request interrupted"):
        return "interrupted"
    if text.startswith("No response requested"):
        return "no_response"
    return "replayed"


def epoch_local(ts):
    return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))


def epoch_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def local_day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def local_stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def iter_entries(path):
    """The entries of a transcript that carry a readable timestamp; the rest is skipped, the unreadable ones counted."""
    for entry in read_lines(path):
        if not entry.get("timestamp"):
            continue
        try:
            epoch_iso(entry["timestamp"])
        except (ValueError, TypeError, AttributeError):
            skip("timestamps", entry["timestamp"])
            continue
        yield entry


def is_prompt(message):
    content = message.get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
    return any(isinstance(b, dict) and b.get("type") == "text" and not b.get("text", "").lstrip().startswith("<") for b in blocks)


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, ceil(q * len(ordered)) - 1)] if ordered else 0


def window_in_effect():
    """The auto-compact window this process sees: the env var if it is a number, else unset (the model's own window, 200k when unknown)."""
    raw = os.environ.get(WINDOW_ENV, "").strip()
    tokens = int(raw) if raw.isdigit() and int(raw) > 0 else None
    return {"env": WINDOW_ENV, "tokens": tokens, "effective": tokens or RUNAWAY_TOKENS, "recommend": tokens is None or tokens > RUNAWAY_TOKENS}


def text_size(value):
    return len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False)) if value else 0


def piece(kind, chars, name=None):
    return {"kind": kind, "chars": chars, **({"name": name} if name else {})}


def prefix_pieces(entry):
    """What one pre-first-reply entry adds to the fixed prefix, sized in chars; what cannot be attributed is 'other'."""
    out = []
    if entry.get("type") == "attachment":
        a = entry.get("attachment") or {}
        kind = ATTACHMENT_KINDS.get(a.get("type"))
        if a.get("type") == "instructions":
            for f in a.get("files") or []:
                path = str(f.get("path", "")) if isinstance(f, dict) else ""
                out.append(piece("memory" if "memory" in path.lower() else "CLAUDE.md files", text_size(f), path or None))
        elif kind:
            body = {k: v for k, v in a.items() if k not in ("type", "isInitial", "skillCount")}
            out.append(piece(kind, text_size(body), a.get("type")))
        elif a.get("type") in ("hook_additional_context", "session_context", "prompt_snapshot"):
            out.append(piece("other", text_size(a.get("content") or a.get("context") or a.get("systemPrompt")), a.get("hookName") or a.get("type")))
    elif entry.get("type") == "user":
        content = (entry.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
        for b in blocks:
            text = b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else ""
            if text.lstrip().startswith("<system-reminder>"):
                kind = next((k for hint, k in REMINDER_HINTS if hint in text), "other")
                out.append(piece(kind, len(text)))
    return out


def prefix_report(sessions):
    """Per project: the median floor (first reply's context of sessions started in the window) and the pieces of its latest such session."""
    by_project = defaultdict(list)
    for info in sessions.values():
        if info["new"] and info["pieces"]:
            by_project[info["project"]].append(info)
    report = {}
    for project, infos in by_project.items():
        infos.sort(key=lambda i: i["steps"][0])
        merged = Counter()
        for p in infos[-1]["pieces"]:
            merged[(p["kind"], p.get("name"))] += p["chars"]
        pieces = [piece(kind, chars, name) for (kind, name), chars in sorted(merged.items(), key=lambda x: -x[1])[:PREFIX_PIECES]]
        report[project] = {"floor_tokens": int(percentile([i["contexts"][0] for i in infos], 0.5)), "sessions": len(infos), "pieces": pieces}
    return report


def usage_entries(path):
    seen = set()
    for entry in iter_entries(path):
        message = entry.get("message") or {}
        if entry.get("type") != "assistant" or not message.get("usage") or message.get("model") == SYNTHETIC_MODEL:
            yield entry, None
            continue
        request = entry.get("requestId") or entry.get("uuid")
        keys = {request, message.get("id")} - {None}
        if keys & seen:
            yield entry, None
            continue
        seen |= keys
        yield entry, message["usage"]


def recall_texts(message):
    content = message.get("content")
    if not isinstance(content, list):
        return []
    texts = (json.dumps(block.get("input") or {}, ensure_ascii=False) for block in content if isinstance(block, dict) and block.get("type") == "tool_use")
    return [text for text in texts if any(mark in text.replace("\\\\", "/") for mark in constants.LENS_READBACK_MARKS)]


def search_ids(message):
    content = message.get("content")
    blocks = content if isinstance(content, list) else []
    return {b.get("id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use" and str(b.get("name", "")).endswith("search")}


def search_results(message, ids):
    """Texts of the results of earlier search calls that name a saved file."""
    content = message.get("content")
    blocks = content if isinstance(content, list) else []
    texts = (json.dumps(b.get("content") or "", ensure_ascii=False) for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in ids)
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
    cutoff = window_start(days * 2)
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


def avoidable_miss(miss):
    """Compaction rewrites the prefix on purpose and an idle cache expires on its own; every other miss has a cause to fix."""
    return miss["cause"] != "compaction" and not miss["cause"].startswith("idle")


def cache_report(days, per_day, misses, paid):
    """Overview 03: hit rate against the target, avoidable misses against the window before, causes by cost."""
    start = window_start(days)  # whole local days, as every other figure of the window
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
    had_prior = any(day < local_day(start) and v["input"] for day, v in per_day.items())
    # The extra cost covers every miss outside compaction, idle expiry included: those tokens were paid for either way.
    extra = extra_cost(sum(m["tokens"] for m in current if m["cause"] != "compaction"))
    return {"hit_rate": round(read / total, 4) if total else None, "hit_target": constants.LENS_HIT_TARGET,
            "hit_days": [{"day": day, "rate": round(v["read"] / v["input"], 4)} for day, v in sorted(window.items()) if v["input"]],
            "misses": len(current), "avoidable": sum(1 for m in current if avoidable_miss(m)),
            "idle": sum(1 for m in current if m["cause"].startswith("idle")),
            "prior_avoidable": sum(1 for m in prior if avoidable_miss(m)) if had_prior else None,
            "tokens": sum(m["tokens"] for m in current), "extra": extra, "extra_share": round(extra / paid, 4) if paid else None,
            "write_price": constants.CACHE_WRITE_PRICE, "read_price": constants.CACHE_READ_PRICE, "causes": rows}


def read_back(record, t, sessions):
    """True when the session's later tool calls name the file a cap saved: its text came back into the context."""
    name = Path(record.get("saved_path") or "").name
    recalls = sessions.get(record.get("session_id", ""), {}).get("recalls", [])
    return bool(name) and any(when > t and name in text for when, text in recalls)


def recall_rows(events, sessions):
    rows = defaultdict(lambda: {"cuts": 0, "recalled": 0})
    for t, record in events:
        if record.get("event") != "cap" or not record.get("saved_path"):
            continue
        row = rows[record.get("kind") or "other"]
        row["cuts"] += 1
        if read_back(record, t, sessions):
            row["recalled"] += 1
    return sorted(({"kind": kind, **row} for kind, row in rows.items()), key=lambda row: -row["cuts"])


class Sessions(dict):
    """Session id to its main-session figures, plus when each subagent's own steps ran and when each session's subagents started."""

    def __init__(self):
        super().__init__()
        self.agent_steps = {}
        self.agent_starts = defaultdict(list)
        self.usage = defaultdict(lambda: defaultdict(Counter))  # day -> model -> read, write, input, output tokens of main and subagent steps
        self.day_steps = Counter()  # main-session steps a day


USAGE_KEYS = {"read": "cache_read_input_tokens", "write": "cache_creation_input_tokens", "input": "input_tokens", "output": "output_tokens"}


def add_usage(sessions, day, message, usage):
    counter = sessions.usage[day][message.get("model") or "unknown"]
    for key, field in USAGE_KEYS.items():
        counter[key] += usage.get(field, 0) or 0


def model_price(model):
    """The price row of a model id, matched by its longest known prefix; None for a model not in the table."""
    best = max((prefix for prefix in constants.MODEL_PRICES if model == prefix or model.startswith(prefix + "-")), key=len, default=None)
    return constants.MODEL_PRICES[best] if best else None


def cost_report(usage):
    """What the window's usage would cost at public API rates, per model and per day, and what the cache saved.

    The saving is measured: every cache-read token is priced at its model's read rate against the input rate
    it would have cost fresh. It is not the estimate the dashboard once showed for tokens kept out of context."""
    models, per_day = defaultdict(Counter), defaultdict(lambda: Counter())
    unpriced = Counter()
    for day, by_model in usage.items():
        for model, tokens in by_model.items():
            price = model_price(model)
            if price is None:
                unpriced.update(tokens)
                models[model].update(tokens)
                continue
            in_price, out_price, write_price, read_price = price
            cost = (tokens["input"] * in_price + tokens["write"] * write_price + tokens["read"] * read_price + tokens["output"] * out_price) / 1e6
            saved = tokens["read"] * (in_price - read_price) / 1e6
            models[model].update(tokens)
            models[model]["cost"] += cost
            models[model]["saved"] += saved
            per_day[day]["cost"] += cost
            per_day[day]["saved"] += saved
    rows = [{"model": model, "priced": model_price(model) is not None, **{k: int(v) if k in USAGE_KEYS else round(v, 2) for k, v in tokens.items()}}
            for model, tokens in models.items()]
    rows.sort(key=lambda r: (-r.get("cost", 0), -r["read"]))
    cost = sum(r.get("cost", 0) for r in rows)
    saved = sum(r.get("saved", 0) for r in rows)
    return {"cost": round(cost, 2), "saved": round(saved, 2), "saved_share": round(saved / (cost + saved), 4) if cost + saved else None,
            "models": rows, "unpriced_tokens": sum(unpriced.values()),
            "per_day": {day: {k: round(v, 2) for k, v in row.items()} for day, row in sorted(per_day.items())},
            "note": "public API rates per model; shown on the dashboard only when the account is billed through the API"}


def scan_sessions(days):
    """Steps, prompts and the re-read bill of the window's whole local days: a session resumed today counts only today's."""
    main, subs = transcript_files(days)
    first_day = local_day(window_start(days))
    sessions = Sessions()
    bill = Counter()
    main_bill, prompts = Counter(), Counter()
    for path in main:
        info = {"session": Path(path).stem, "project": project_of(path), "steps": [], "contexts": [], "prompts": [], "reread": 0, "recalls": [], "new": None, "pieces": [], "partial": False, "first_day": None, "agents": [], "synthetic": 0, "synthetic_kinds": Counter()}
        asked, searches = set(), set()
        for entry, usage in usage_entries(path):
            t = epoch_iso(entry["timestamp"])
            message = entry.get("message") or {}
            if entry.get("type") == "assistant":
                info["recalls"].extend((t, text) for text in recall_texts(message))
                searches |= search_ids(message)
                blocks = message.get("content")
                if isinstance(blocks, list) and any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in AGENT_TOOLS for b in blocks):
                    info["agents"].append(t)
            elif entry.get("type") == "user" and searches:
                info["recalls"].extend((t, text) for text in search_results(message, searches))
            day = local_day(t)
            if info["new"] is None:
                info["pieces"].extend(prefix_pieces(entry))
            if day < first_day:
                info["partial"] = True
            elif info["first_day"] is None:
                info["first_day"] = day
            if day >= first_day and message.get("model") == SYNTHETIC_MODEL:
                info["synthetic"] += 1
                info["synthetic_kinds"][synthetic_kind(entry, message)] += 1
            if usage:
                if info["new"] is None:
                    info["new"] = day >= first_day  # a session that began before the window was resumed, not started
                if day < first_day:
                    continue
                info["steps"].append(t)
                info["contexts"].append(sum(usage.get(k, 0) for k in CONTEXT_KEYS))
                info["reread"] += usage.get("cache_read_input_tokens", 0)
                bill[day] += usage.get("cache_read_input_tokens", 0)
                main_bill[day] += usage.get("cache_read_input_tokens", 0)
                sessions.day_steps[day] += 1
                add_usage(sessions, day, message, usage)
            elif entry.get("type") == "user" and is_prompt(entry.get("message") or {}) and first_ask(entry, asked) and day >= first_day:
                info["prompts"].append(t)
                prompts[day] += 1
        if info["steps"]:
            sessions[info["session"]] = info
    for path in subs:
        stem = Path(path).stem
        agent = stem[len("agent-"):] if stem.startswith("agent-") else stem
        owner = sessions.get(Path(path).parent.parent.name)
        for entry, usage in usage_entries(path):
            if owner is not None and entry.get("type") == "assistant":
                owner["recalls"].extend((epoch_iso(entry["timestamp"]), text) for text in recall_texts(entry.get("message") or {}))
            if usage:
                t = epoch_iso(entry["timestamp"])
                if local_day(t) < first_day:
                    continue
                bill[local_day(t)] += usage.get("cache_read_input_tokens", 0)
                add_usage(sessions, local_day(t), entry.get("message") or {}, usage)
                sessions.agent_steps.setdefault(agent, []).append(t)
        if agent in sessions.agent_steps:
            sessions.agent_steps[agent].sort()
            sessions.agent_starts[Path(path).parent.parent.name].append(sessions.agent_steps[agent][0])
    return sessions, bill, main_bill, prompts


def count_after(times, t):
    return len(times) - bisect_right(times, t)


def steps_until_next_prompt(steps, prompts, t):
    index = bisect_right(prompts, t)
    end = prompts[index] if index < len(prompts) else float("inf")
    return bisect_right(steps, end) - bisect_right(steps, t)


def read_events(days):
    cutoff = window_start(days)
    events = []
    try:
        for line in open(constants.EVENT_LOG):
            try:
                record = json.loads(line)
                t = epoch_local(record["ts"])
            except (ValueError, KeyError, TypeError):
                continue
            if t >= cutoff:
                events.append((t, record))
    except OSError:
        pass
    return events


def problem_rows(events):
    found = {}
    for t, record in events:
        if record.get("event") != "error":
            continue
        kind = str(record.get("kind") or "unknown")
        row = found.setdefault(kind, {"kind": kind, "explanation": PROBLEM_KINDS.get(kind, "Kiasi worked around a failure of this kind."), "count": 0})
        row["count"] += 1
        detail = record.get("error") or record.get("path") or record.get("settings") or record.get("project") or record.get("hook") or ""
        row.update({"ts": record.get("ts") or epoch_iso(t), "session": str(record.get("session_id") or "")[:8],
                    "message": detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False),
                    "path": str(record.get("path") or "")})
    return sorted(found.values(), key=lambda row: (-row["count"], row["kind"]))


def tokens(chars):
    return int(chars // constants.CHARS_PER_TOKEN)


def action(record, kind, label, kept_out=0, later=0, saved=0, formula=""):
    return {"ts": record["ts"], "session": record.get("session_id") or record.get("agent_id") or "", "kind": kind, "label": label,
            "kept_out": int(kept_out), "later_steps": int(later), "saved": int(saved), "formula": formula, "record": record}


def follow_ups(events, agent_starts=None):
    """Per session, when its prompts, Agent calls (and the ones logged as allowed), continues at the pause question,
    and subagent transcripts (agent_starts, from the transcripts) were logged, and which skipped reads were repeated."""
    out = defaultdict(lambda: {"prompts": [], "agents": [], "allowed": [], "renewals": [], "starts": [], "skips": [], "retries": []})
    for t, record in events:
        session = record.get("session_id", "")
        if record.get("event") in ("prompt", "agent"):
            out[session][f"{record['event']}s"].append(t)
            if record.get("event") == "agent" and record.get("decision") == "allow":
                out[session]["allowed"].append(t)
        elif record.get("event") == "turn_resume" and record.get("asked"):
            out[session]["renewals"].append(t)
        elif record.get("event") == "read_skipped":
            out[session]["skips"].append((t, record.get("path")))
        elif record.get("event") == "read_retry":
            out[session]["retries"].append((t, record.get("path")))
    for session, times in (agent_starts or {}).items():
        out[session]["starts"] = times
    return out


def delegation_followed(t, logged):
    """True when an Agent call is known to have run after this prompt and before its next one. The Agent event is logged
    before the outcome (and an "ask" may be refused), so it counts only when logged as allowed or when a subagent
    transcript started in between."""
    next_prompt = min((when for when in logged.get("prompts", []) if when > t), default=float("inf"))
    inside = lambda times: any(t <= when < next_prompt for when in times)  # noqa: E731
    return inside(logged.get("allowed", [])) or (inside(logged.get("agents", [])) and inside(logged.get("starts", [])))


def skip_repeated(t, record, logged):
    """True when the session repeated this skipped read (before skipping it again) and it went through: nothing was saved."""
    mine = (logged or {}).get(record.get("session_id", "")) or {}
    path = record.get("path")
    next_skip = min((when for when, other in mine.get("skips", []) if other == path and when > t), default=float("inf"))
    return any(other == path and t < when < next_skip for when, other in mine.get("retries", []))


def classify(t, record, sessions, logged=None):
    session = record.get("session_id", "")
    info = sessions.get(session, {"steps": [], "prompts": []})
    event = record.get("event")
    later = count_after(info["steps"], t)
    if event == "cap":
        kept = tokens(record.get("chars", 0) - record.get("shown_chars", 0))
        back = read_back(record, t, sessions)
        return action(record, "cap", f"{record.get('tool_name')} {record.get('kind')}: {record.get('label', '')}", kept, later, 0 if back else kept * later,
                      "read back later in the session: nothing saved" if back else f"{kept:,} tokens kept out × {later} later steps in the session")
    if event == "read_skipped":
        kept = tokens(record.get("chars", 0))
        agent = record.get("agent_id")
        # A subagent's skip saves its own later steps; the parent's steps say nothing about it, and an unknown agent is not credited.
        later = count_after(getattr(sessions, "agent_steps", {}).get(agent, []), t) if agent else later
        repeated = skip_repeated(t, record, logged)
        return action(record, "read_skipped", f"unchanged re-read of {record.get('path', '')} skipped" + (", then repeated and let through" if repeated else ""), kept, later, 0 if repeated else kept * later,
                      "repeated and let through: nothing saved" if repeated else f"{kept:,} tokens not re-sent × {later} later steps in the {'subagent' if agent else 'session'}")
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
            # The instruction alone saves nothing: the estimate is credited only when Claude went on to delegate.
            if not delegation_followed(t, (logged or {}).get(session) or {"prompts": [], "agents": []}):
                return action(record, "reread_check", f"delegation instructed at {record.get('context_tokens', 0) // 1000}k context, no Agent call before the next prompt")
            saved = max(0, check.get("here", 0) - check.get("delegated", 0))
            return action(record, "delegated", f"delegated as instructed at {record.get('context_tokens', 0) // 1000}k context", 0, check.get("steps", 0), saved,
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
    if event in ("turn_warn", "turn_stop", "turn_over") and record.get("subagent"):
        return None  # a subagent's budget is not the turn budget, and its pause is not followed by the next prompt
    if event == "turn_warn":
        return action(record, "turn_warn", f"warned at {record.get('steps')} steps, {record.get('reread', 0) / 1e6:.1f}M re-read")
    if event == "turn_stop" and record.get("first"):
        # A continue at the pause question starts a fresh budget, as the next prompt does.
        renewals = ((logged or {}).get(session) or {}).get("renewals", [])
        after = steps_until_next_prompt(info["steps"], sorted([*info["prompts"], *renewals]), t)
        return action(record, "turn_stop", f"paused at {record.get('steps')} steps, {after} more before the next prompt", 0, after, 0,
                      f"steps between the pause and the next prompt: {after} (complies under {constants.LENS_COMPLY_STEPS})")
    if event == "turn_over":
        after = steps_until_next_prompt(info["steps"], info["prompts"], t)
        return action(record, "turn_over", f"reached the budget at {record.get('steps')} steps in warn mode, {after} more before the next prompt", 0, after, 0,
                      f"steps between reaching the budget and the next prompt: {after}")
    if event == "turn_resume":
        where = " in a new session" if record.get("paused_session") not in (None, "", session) else ""
        if record.get("mode") == "resume":
            source = "from its checklist" if record.get("checklist") else "from its task and edited files, no checklist written"
            how = "at the pause question" if record.get("asked") else "with continue"
            return action(record, "turn_resume", f"turn paused at {record.get('steps')} steps resumed {how}{where}, {source}")
        return action(record, "turn_moved_on", f"the first prompt after a pause at {record.get('steps')} steps was not continue{where}; Claude was pointed to the saved work")
    if event == "loop":
        return action(record, "loop", f"{record.get('label', '')} failed {record.get('count')} times in one turn, Claude told to stop retrying")
    if event == "state" and (record.get("task") or record.get("files") or record.get("failures") or record.get("outputs") or record.get("checklists")):
        return action(record, "state", f"state re-injected after compaction: {record.get('files', 0)} files, {record.get('failures', 0)} failing, {record.get('outputs', 0)} saved outputs, {record.get('checklists', 0)} checklists")
    if event == "agent" and record.get("decision") == "ask":  # before model_set: an asked call carries both
        return action(record, "review_asked", f"repeat review by {record.get('agent_type')} turned into a question")
    if event == "agent" and record.get("model_set"):
        return action(record, "agent_model", f"{record.get('agent_type')} set to {record.get('model_set')}")
    if event == "agent" and record.get("brief_suffix"):
        return action(record, "brief_suffix", f"{record.get('agent_type')} brief extended with the budget and the answer format")
    if event == "agent" and record.get("long_prompt"):
        return action(record, "long_prompt", f"{record.get('agent_type')} prompt of {record.get('prompt_chars', 0):,} chars flagged")
    if event == "turn_choice":
        return action(record, "turn_choice", f"{record.get('choice')} chosen at the pause question after {record.get('steps')} steps")
    if event == "quiet":
        return action(record, "quiet", "plugin output quieted")
    if event == "desktop_notify":
        return action(record, "desktop_notify", f"desktop notification: {record.get('title', '')}")
    if event == "read_nudge":
        return action(record, "read_nudge", f"nudge to read {record.get('path', '')} by section")
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
    steps, contexts, prompts = info["steps"], info["contexts"], sorted(info["prompts"])
    rows = []
    for index, start in enumerate(prompts):
        end = prompts[index + 1] if index + 1 < len(prompts) else float("inf")
        first, last = bisect_right(steps, start), bisect_right(steps, end)
        rows.append((last - first, sum(contexts[first:last])))
    return rows


def step_buckets():
    """LENS_STEP_BUCKETS cut at the configured turn budget: the last band starts at TURN_STOP_STEPS, not at a fixed 60."""
    stop = constants.TURN_STOP_STEPS
    buckets = [(low, min(high or stop - 1, stop - 1)) for low, high in constants.LENS_STEP_BUCKETS if low < stop]
    return buckets + [(stop, None)]


def step_histogram(sessions):
    buckets = step_buckets()
    hist = [{"label": bucket_label(low, high), "low": low, "prompts": 0, "bill": 0} for low, high in buckets]
    for info in sessions.values():
        for count, bill in prompt_steps(info):
            if count:
                row = hist[bucket_index(buckets, count)]
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
            "session": session, "short": session[:8], "project": info["project"], "day": local_day(info["steps"][0]), "partial": info["partial"], "first_day": info["first_day"], "start": local_stamp(info["steps"][0]),
            "prompts": len(info["prompts"]), "steps": len(info["steps"]), "mean_steps": round(sum(steps_per_prompt) / max(1, len(steps_per_prompt)), 1),
            "long_turns": sum(1 for c in steps_per_prompt if c >= constants.TURN_STOP_STEPS),
            "startup": info["contexts"][0],
            "mean_context": mean_context, "median_context": int(percentile(info["contexts"], 0.5)), "p90_context": int(percentile(info["contexts"], 0.9)), "synthetic_skipped": info["synthetic"], "synthetic_kinds": dict(info["synthetic_kinds"]),
            "peak": max(info["contexts"]), "runaway": max(info["contexts"]) > RUNAWAY_TOKENS, "bill": info["reread"],
            "findings": postmortem(info, per_session.get(session, []), steps_per_prompt, mean_context),
            "compactions": max(sum(1 for a in per_session.get(session, []) if a["kind"] == "compaction"),
                               sum(1 for a in per_session.get(session, []) if a["kind"] in ("pruned", "summary"))),
            "actions": len(per_session.get(session, [])), "saved": sum(a["saved"] for a in per_session.get(session, [])), "kept_out": sum(a["kept_out"] for a in per_session.get(session, [])),
            "series": series_points(info, marks),
            "prompt_rows": prompt_rows(info),
        })
    records.sort(key=lambda r: -r["bill"])
    return records[: constants.LENS_MAX_SESSIONS]


def step_weighted(rows, key):
    """A step-weighted mean of the days' median or p90 context (an approximation of the period's own); None unless every day has it."""
    turns = sum(r.get("turns", 0) for r in rows)
    if not turns or any(key not in (r.get("context") or {}) for r in rows if r.get("turns")):
        return None
    return int(sum(r["context"][key] * r.get("turns", 0) for r in rows) / turns)


def period_metrics(rows):
    turns = main_turns = sum(r.get("turns", 0) for r in rows)  # main-session steps and their re-read only, as the headline and the bands
    reread = sum((r.get("main") or {}).get("cache_read_input_tokens", 0) for r in rows)
    return {
        "days": len(rows),
        "turns": turns,
        "reread": reread,
        "reread_per_turn": int(reread / turns) if turns else None,
        "reread_per_day": int(reread / len(rows)) if rows else None,
        "steps_per_day": int(turns / len(rows)) if rows else None,
        "mean_context": int(sum(r.get("mean_context", 0) * r.get("turns", 0) for r in rows) / main_turns) if main_turns else None,
        "median_context": step_weighted(rows, "median"), "p90_context": step_weighted(rows, "p90"),
        "high_share": round(sum(r.get("high_share", 0) * r.get("turns", 0) for r in rows) / main_turns, 3) if main_turns else None,
    }


def billing_plan():
    """"api" when Claude Code is billed per token, "subscription" for a Pro, Max, Team or Enterprise seat, else "unknown".

    From the key variables Claude Code honours and the account record it keeps in ~/.claude.json; no token is read."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "api"
    try:
        config = json.loads(constants.CLAUDE_CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return "unknown"
    if config.get("primaryApiKey"):
        return "api"
    billing = (config.get("oauthAccount") or {}).get("billingType") or ""
    return "subscription" if "subscription" in billing or config.get("hasAvailableSubscription") else "unknown"


def pace_report(today_tokens):
    """Re-read tokens a day over the 7 full days before today against the 7 before those, from the day history budget.py keeps.

    A day with no work counts as zero: the pace is what a weekly limit sees, not the mean of busy days. None until a full day is known."""
    tokens = {}
    for row in history_rows():
        tokens[row["day"]] = sum((row.get(side) or {}).get("cache_read_input_tokens", 0) for side in ("main", "sub"))
    day = lambda n: local_day(time.time() - n * 86400)
    last = [tokens.get(day(n), 0) for n in range(1, 8)]
    prior = [tokens.get(day(n), 0) for n in range(8, 15)]
    if not any(day(n) in tokens for n in range(1, 8)):
        return None
    has_prior = any(day(n) in tokens for n in range(8, 15))
    return {"rate": int(sum(last) / 7), "week": sum(last), "prior_rate": int(sum(prior) / 7) if has_prior else None,
            "prior_week": sum(prior) if has_prior else None, "today": today_tokens,
            "series": [{"day": day(n), "tokens": tokens.get(day(n), 0)} for n in range(14, 0, -1)],
            "note": "the 7 full days before today against the 7 before those; days without work count as zero"}


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
    with file_lock(constants.SAVINGS_FILE):
        return _merge_savings(actions, days)


def _merge_savings(actions, days):
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
    write_atomic(constants.SAVINGS_FILE, json.dumps({"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "per_day": kept}, indent=1))
    return {"first_day": kept[0]["day"] if kept else None, "days": len(kept),
            **{key: sum(r.get(key, 0) for r in kept) for key in ("saved", "kept_out", "actions", "caps")}}


def day_series(rows):
    """One point per history day, for the before-and-after charts of the Overview."""
    points = []
    for r in rows:
        m = period_metrics([r])
        points.append({"day": r["day"], "reread": m["reread"], "steps": m["turns"]})
    return points


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
    for key in ("reread_per_turn", "turns", "mean_context", "median_context", "p90_context", "high_share"):
        after[key] = per_turn[key]
    b = period_metrics(before)
    # A handful of steps on either side gives a ratio that the next session overturns.
    enough = min(b["turns"], after["turns"]) >= constants.LENS_FACTOR_MIN_STEPS
    factor = round(b["reread_per_turn"] / after["reread_per_turn"], 1) if enough and b["reread_per_turn"] and after["reread_per_turn"] else None
    return {"install_day": day, "before": b if before else None, "after": after if after_all else None, "factor": factor,
            "factor_min_steps": constants.LENS_FACTOR_MIN_STEPS,
            "first_day": rows[0]["day"], "history_days": len(rows), "series": day_series(rows)}


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
    managed = [(name, path, stat) for name, folder, _ in cleanup.managed_folders() for path, stat in cleanup.own_files(name, folder)]
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


def figure(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1000)}k"


def mean_of(values):
    return sum(values) / len(values) if values else 0


def outcome(fired, followed, effect, note, **extra):
    return {"fired": fired, "followed": followed, "rate": round(followed / fired, 3) if fired else None, "effect": effect, "note": note, **extra}


def turn_paid(info, t, end):
    """Context summed over the main session's steps after t and up to end: what the turn paid to re-read."""
    lo, hi = bisect_right(info["steps"], t), bisect_right(info["steps"], end)
    return sum(info["contexts"][lo:hi])


def reread_outcome(rows):
    """rows: (followed, brief, paid or None, here) per delegate instruction."""
    def side(picked):
        paid = [p for _, _, p, _ in picked if p is not None]
        return {"turns": len(paid), "paid": int(mean_of(paid)), "predicted": int(mean_of([h for _, _, p, h in picked if p is not None]))}
    done, other = [r for r in rows if r[0]], [r for r in rows if not r[0]]
    a, b = side(done), side(other)
    effect = f"delegated turns paid {figure(a['paid'])} vs {figure(a['predicted'])} predicted here ({a['turns']}); the others paid {figure(b['paid'])} vs {figure(b['predicted'])} ({b['turns']})"
    split = {}
    for key, flag in (("brief", True), ("no_brief", False)):
        part = [r for r in rows if bool(r[1]) is flag]
        d = side([r for r in part if r[0]])
        split[key] = {"fired": len(part), "followed": sum(1 for r in part if r[0]), "paid": d["paid"], "predicted": d["predicted"], "turns": d["turns"]}
    return outcome(len(rows), len(done), effect, "an Agent call in the main transcript before the next prompt; paid is the context summed over that turn's steps, per turn", delegated=a, kept=b, split=split)


def rule_outcomes(events, sessions, logged=None):
    """Per instruction the hooks gave Claude, whether it was followed and what followed, judged from the log and the transcripts."""
    logged = logged if logged is not None else follow_ups(events)
    by = defaultdict(lambda: defaultdict(list))
    for t, record in events:
        by[record.get("session_id", "")][record.get("event")].append((t, record))
    window = constants.LENS_HANDOFF_WINDOW_MINUTES * 60
    out = {}
    reread = []
    warns, handoffs, nudges, switches = [], [], [], []
    for t, record in events:
        if record.get("event") != "prompt":
            continue
        sid = record.get("session_id", "")
        info = sessions.get(sid)
        check = record.get("reread_check") or {}
        if check.get("mode") == "delegate":
            nexts = [w for w, _ in by[sid]["prompt"] if w > t]
            end = min(nexts, default=float("inf"))
            agents = (info or {}).get("agents", [])
            done = info is not None and any(t <= a < end for a in agents)
            reread.append((done, check.get("brief"), turn_paid(info, t, end) if info else None, check.get("here", 0)))
        if record.get("handoff_notice"):
            handoffs.append((t, sid, record))
        if record.get("nudge"):
            nudges.append((t, sid, record))
        if record.get("task_switch"):
            switches.append((t, sid, record))
    if reread:
        out["reread"] = reread_outcome(reread)
    for t, record in events:
        if record.get("event") == "turn_warn" and not record.get("subagent") and sessions.get(record.get("session_id", "")):
            info = sessions[record["session_id"]]
            warns.append(steps_until_next_prompt(info["steps"], info["prompts"], t))
    if warns:
        ok = sum(1 for a in warns if a < constants.LENS_COMPLY_STEPS)
        out["turn"] = outcome(len(warns), ok, f"mean {mean_of(warns):.1f} steps after the warning", f"the turn ended within {constants.LENS_COMPLY_STEPS} steps after the warning, the rule used for pauses")
    restores = [(t, record.get("session_id", "")) for t, record in events if record.get("event") == "session_start" and record.get("taskfile")]
    if handoffs:
        done, first, notice = 0, [], []
        for t, sid, record in handoffs:
            project = (sessions.get(sid) or {}).get("project")
            for w, other in restores:
                info = sessions.get(other)
                if t < w <= t + window and other != sid and info and project and info["project"] == project:
                    done += 1
                    first.append(info["contexts"][0])
                    notice.append(record.get("context_tokens", 0))
                    break
        effect = f"restored sessions started at a median {figure(percentile(first, 0.5))} against {figure(percentile(notice, 0.5))} at the notice" if first else "no restored session yet"
        out["handoff"] = outcome(len(handoffs), done, effect, f"a session_start that restored the task file in the same project within {constants.LENS_HANDOFF_WINDOW_MINUTES} minutes")

    def moved_on(t, sid):
        later = [w for w, _ in by[sid]["prompt"] if w > t]
        cutoff = later[constants.LENS_NUDGE_PROMPTS - 1] if len(later) >= constants.LENS_NUDGE_PROMPTS else float("inf")
        if any(t < w <= cutoff for w, _ in by[sid]["compact"]):
            return True
        project = (sessions.get(sid) or {}).get("project")
        return any(other != sid and info["new"] and info["project"] == project and t < info["steps"][0] <= cutoff for other, info in sessions.items())

    for key, group, what in (("nudge", nudges, "compact or clear nudge"), ("task_switch", switches, "task-switch notice")):
        if not group:
            continue
        flags = [(moved_on(t, sid), record.get("context_tokens", 0)) for t, sid, record in group]
        yes = [c for f, c in flags if f]
        no = [c for f, c in flags if not f]
        out[key] = outcome(len(flags), len(yes), (f"followed at a median {figure(percentile(yes, 0.5))} context, ignored at {figure(percentile(no, 0.5)) if no else 'none ignored'}" if yes else f"none followed, ignored at a median {figure(percentile(no, 0.5))} context"),
                           f"a compaction or a new session in the project within {constants.LENS_NUDGE_PROMPTS} prompts of the {what}")
    skips = [(t, r) for t, r in events if r.get("event") == "read_skipped"]
    if skips:
        held = [r for t, r in skips if not skip_repeated(t, r, logged)]
        cut = sum(tokens(r.get("chars", 0)) for r in held)
        out["reads"] = outcome(len(skips), len(held), f"{figure(cut)} tokens not re-sent; {len(skips) - len(held)} repeated and let through", "a skipped read counts as followed unless the same path was read again")
    nudged = [(t, r) for t, r in events if r.get("event") == "read_nudge"]
    if nudged:
        yes = sum(1 for t, r in nudged if any(w > t for w, _ in by[r.get("session_id", "")]["read_retry"]))
        out["read_nudge"] = outcome(len(nudged), yes, f"{yes} followed by a repeated read of a skipped file", "a read_retry later in the same session")
    routed = [(t, r) for t, r in events if r.get("event") == "route_retry"]
    sent = [(t, r) for t, r in events if r.get("event") == "routed"]
    if sent:
        used = set()
        for t, r in routed:
            for i, (w, o) in enumerate(sent):
                if i not in used and w < t and o.get("session_id") == r.get("session_id") and o.get("tool_name") == r.get("tool_name"):
                    used.add(i)
                    break
        out["route"] = outcome(len(sent), len(sent) - len(used), f"{len(used)} let through on a repeat", "a routed call counts as followed unless the same tool was repeated and let through")
    return out


def experiment_report(events, sessions):
    """The holdout comparison: sessions with the rule on against off, for the rule with the most sessions in the window."""
    side = defaultdict(dict)
    for t, record in events:
        hold = record.get("holdout")
        if record.get("event") == "session_start" and isinstance(hold, dict) and hold.get("rule") and record.get("session_id") in sessions:
            side[hold["rule"]][record["session_id"]] = bool(hold.get("off"))
    if not side:
        return None
    rule = max(side, key=lambda r: len(side[r]))

    def figures(ids):
        infos = [sessions[i] for i in ids]
        prompts = sum(len(i["prompts"]) for i in infos)
        steps = sum(len(i["steps"]) for i in infos)
        contexts = [c for i in infos for c in i["contexts"]]
        return {"sessions": len(infos), "prompts": prompts, "median_context": int(percentile(contexts, 0.5)) if contexts else None,
                "reread_per_prompt": int(sum(i["reread"] for i in infos) / prompts) if prompts else None, "steps_per_prompt": round(steps / prompts, 1) if prompts else None}
    on, off = figures([i for i, o in side[rule].items() if not o]), figures([i for i, o in side[rule].items() if o])
    need = constants.LENS_EXPERIMENT_MIN_SESSIONS
    return {"rule": rule, "on": on, "off": off, "enough": on["sessions"] >= need and off["sessions"] >= need, "min_sessions": need}


def build(days):
    reset_skips()
    sessions, bill, main_bill, prompts = scan_sessions(days)
    events = read_events(days * 2)
    cache_days, misses_all = cache_scan(days)
    attribute_misses(misses_all, events)
    window = window_start(days)
    events = [(t, record) for t, record in events if t >= window]
    actions = []
    per_day = defaultdict(Counter)
    kind_counts = defaultdict(Counter)  # per day and kind, untruncated (the actions list is cut at LENS_MAX_ACTIONS)
    by_kind = defaultdict(lambda: {"count": 0, "saved": 0, "kept_out": 0})
    checks, pastes, budget_rows = [], [], []
    logged = follow_ups(events, sessions.agent_starts)
    for t, record in events:
        item = classify(t, record, sessions, logged)
        if not item:
            continue
        actions.append(item)
        by_kind[item["kind"]]["count"] += 1
        by_kind[item["kind"]]["saved"] += item["saved"]
        by_kind[item["kind"]]["kept_out"] += item["kept_out"]
        per_day[item["ts"][:10]][item["kind"]] += item["saved"]
        kind_counts[item["ts"][:10]][item["kind"]] += 1
        if item["kind"] in ("reread_check", "delegated"):
            check = record.get("reread_check") or {}
            checks.append({"ts": item["ts"], "session": item["session"][:8], "context": record.get("context_tokens", 0), "steps": check.get("steps"),
                           "here": check.get("here", 0), "delegated": check.get("delegated", 0),
                           "mode": "delegate, no Agent call" if item["kind"] == "reread_check" and check.get("mode") == "delegate" else check.get("mode", "shown")})
        if item["kind"] in ("paste_saved", "paste_refused"):
            pastes.append({"ts": item["ts"], "session": item["session"][:8], "chars": record.get("prompt_chars", 0), "blocked": item["kind"] == "paste_refused",
                           "path": record.get("paste_saved") or record.get("paste_blocked") or ""})
        if item["kind"] in ("turn_warn", "turn_stop", "turn_over", "turn_resume", "turn_moved_on"):
            follow_up = item["kind"] in ("turn_resume", "turn_moved_on")
            budget_rows.append({"ts": item["ts"], "session": item["session"][:8], "kind": item["kind"], "steps": record.get("steps"),
                                "reread": None if follow_up else record.get("reread", 0),
                                "after": item["later_steps"] if item["kind"] in ("turn_stop", "turn_over") else None,
                                "note": ("checklist written" if record.get("checklist") else "no checklist written") if follow_up else ""})
    first_day = local_day(time.time() - days * 86400)
    outcomes = rule_outcomes(events, sessions, logged)
    experiment = experiment_report(events, sessions)
    days_seen = sorted(day for day in set(bill) | set(per_day) if day >= first_day)
    stops = [a for a in actions if a["kind"] == "turn_stop"]
    complied = sum(1 for a in stops if a["later_steps"] < constants.LENS_COMPLY_STEPS)
    session_rows = session_records(sessions, actions)
    total_prompts = sum(len(s["prompts"]) for s in sessions.values())
    total_steps = sum(len(s["steps"]) for s in sessions.values())
    startups = defaultdict(list)
    for info in sessions.values():
        if info["new"]:  # a resumed session's first request in the window is no startup
            startups[local_day(info["steps"][0])].append(info["contexts"][0])
    all_startups = [value for values in startups.values() for value in values]
    all_contexts = [c for info in sessions.values() for c in info["contexts"]]
    misses, miss_tokens = defaultdict(Counter), Counter()
    for miss in misses_all:
        misses[local_day(miss["t"])][miss["cause"]] += 1
        miss_tokens[local_day(miss["t"])] += miss["tokens"]
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    all_time = merge_savings(actions, days)
    cost = cost_report(sessions.usage)
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "days": days,
        "totals": {"saved": sum(a["saved"] for a in actions), "actions": len(actions), "kept_out": sum(a["kept_out"] for a in actions), "paid": sum(bill.values()),
                   "pruned": by_kind["pruned"]["count"], "summaries": by_kind["summary"]["count"], "compactions": by_kind["compaction"]["count"],
                   "stops": len(stops), "stops_complied": complied, "resumed": by_kind["turn_resume"]["count"], "skipped": by_kind["turn_moved_on"]["count"],
                   "over_budget": by_kind["turn_over"]["count"], "mean_steps_after_stop": round(sum(a["later_steps"] for a in stops) / max(1, len(stops)), 1),
                   "sessions": len(sessions), "prompts": total_prompts, "steps": total_steps, "mean_steps": round(total_steps / max(1, total_prompts), 1),
                   "reread_per_prompt": int(sum(main_bill.values()) / total_prompts) if total_prompts else None,
                   "context_mean": int(sum(all_contexts) / len(all_contexts)) if all_contexts else None,
                   "context_median": int(percentile(all_contexts, 0.5)) if all_contexts else None, "context_p90": int(percentile(all_contexts, 0.9)) if all_contexts else None,
                   "runaway_sessions": sum(1 for s in sessions.values() if max(s["contexts"]) > RUNAWAY_TOKENS),
                   "synthetic_skipped": sum(s["synthetic"] for s in sessions.values()),
                   "synthetic_kinds": dict(sum((s["synthetic_kinds"] for s in sessions.values()), Counter())),
                   "startup_mean": int(sum(all_startups) / len(all_startups)) if all_startups else None,
                   "turn_choices": dict(Counter(a["record"].get("choice") for a in actions if a["kind"] == "turn_choice")),
                   "reads_skipped": by_kind["read_skipped"]["count"], "reads_retried": by_kind["read_retry"]["count"]},
        "by_kind": dict(by_kind),
        "outcomes": outcomes,
        **({"experiment": experiment} if experiment else {}),
        "since": since_install(),
        "all_time": all_time,
        "cost": cost,
        "pace": pace_report(bill.get(local_day(time.time()), 0)),
        "per_day": [{"day": day, "paid": bill.get(day, 0), "prompts": prompts.get(day, 0), "reread_per_prompt": int(main_bill.get(day, 0) / prompts[day]) if prompts.get(day) else None,
                     "steps": sessions.day_steps.get(day, 0), "steps_per_prompt": round(sessions.day_steps.get(day, 0) / prompts[day], 1) if prompts.get(day) else None,
                     "sessions": len(startups.get(day, [])), "startup": int(sum(startups[day]) / len(startups[day])) if startups.get(day) else None,
                     "kinds": dict(kind_counts.get(day, {})),
                     "cache_misses": dict(misses.get(day, {})), "cache_miss_tokens": miss_tokens.get(day, 0),
                     "hit_rate": round(cache_days[day]["read"] / cache_days[day]["input"], 4) if cache_days.get(day, {}).get("input") else None,
                     "cost_usd": cost["per_day"].get(day, {}).get("cost"), "cache_saved_usd": cost["per_day"].get(day, {}).get("saved"),
                     **{k: v for k, v in per_day[day].items() if v}} for day in days_seen],
        "sessions": session_rows,
        "window": window_in_effect(),
        "prefix": prefix_report(sessions),
        "steps_hist": step_histogram(sessions),
        "caps": cap_summary(actions),
        "recall": recall_rows(events, sessions),
        "cache": cache_report(days, cache_days, misses_all, sum(bill.values())),
        "storage": storage([r for info in sessions.values() for r in info["recalls"]]),
        "compactions": compaction_rows(events),
        "problems": problem_rows(events),
        "checks": list(reversed(checks)),
        "pastes": list(reversed(pastes)),
        "budget_rows": list(reversed(budget_rows)),
        "actions": list(reversed(actions))[: constants.LENS_MAX_ACTIONS],
        "settings": {"plan": billing_plan(), "warn_tokens": constants.CONTEXT_WARN_TOKENS, "hard_tokens": constants.CONTEXT_HARD_TOKENS, "turn_warn_steps": constants.turn_warn_steps(),
                     "turn_stop_steps": constants.TURN_STOP_STEPS, "turn_budget_mode": constants.TURN_BUDGET_MODE, "comply_steps": constants.LENS_COMPLY_STEPS, "saving_kinds": SAVING_KINDS,
                     "holdout": constants.HOLDOUT, "holdout_rules": list(constants.HOLDOUT_RULES), "experiment_min_sessions": constants.LENS_EXPERIMENT_MIN_SESSIONS,
                     "delegation_credit": "only when an Agent call is logged as allowed or a subagent transcript starts after it: the log records no outcome"},
    }
    report["skipped"] = dict(SKIPPED)
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    constants.LENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = constants.LENS_FILE.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(report, ensure_ascii=False).encode(), compresslevel=6, mtime=0))
    os.replace(tmp, constants.LENS_FILE)
    (constants.LOG_DIR / "lens.json").unlink(missing_ok=True)  # the uncompressed copy older versions wrote
    return report


def print_report(report):
    t = report["totals"]
    print(f"last {report['days']} days: {t['actions']} kiasi actions, {t['paid'] / 1e6:.0f}M re-read tokens paid, "
          f"{t['kept_out'] // 1000}k tokens cut, {t['pruned']} compactions pruned / {t['summaries']} summarised, {t['stops']} turns paused "
          f"({t['resumed']} resumed, {t['stops_complied']} complied, mean {t['mean_steps_after_stop']} steps after), {t['sessions']} sessions, {t['mean_steps']} steps per prompt")
    for kind, row in sorted(report["by_kind"].items(), key=lambda x: -x[1]["kept_out"]):
        print(f"  {kind:14} {row['count']:4}  cut {row['kept_out'] / 1e3:8.0f}k")


if __name__ == "__main__":
    print_report(build(int(sys.argv[1]) if len(sys.argv) > 1 else constants.BUDGET_DAYS))
