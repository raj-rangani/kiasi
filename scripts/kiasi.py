import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import constants


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


def tail_entries(transcript_path):
    if not transcript_path:
        return []
    path = Path(transcript_path)
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - constants.TRANSCRIPT_TAIL_BYTES))
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    entries = []
    for line in raw.splitlines()[1:] if size > constants.TRANSCRIPT_TAIL_BYTES else raw.splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def current_context_tokens(entries):
    for entry in reversed(entries):
        if entry.get("type") != "assistant":
            continue
        usage = (entry.get("message") or {}).get("usage") or {}
        if usage:
            return (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
    return 0


def entry_epoch(entry):
    stamp = entry.get("timestamp") or ""
    try:
        return time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone if stamp else 0
    except ValueError:
        return 0


def is_system_prompt(text):
    return not text.strip() or bool(re.match(constants.SYSTEM_PROMPT_PATTERN, text))


def is_task_prompt(text):
    return not is_system_prompt(text) and len(text.strip()) >= constants.TASK_MIN_CHARS


def user_texts(entries):
    out = []
    for entry in entries:
        if entry.get("type") != "user":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            out.append((entry, content))
        else:
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    out.append((entry, block.get("text", "")))
    return out


def last_task_prompt(entries):
    for entry, text in reversed(user_texts(entries)):
        if is_task_prompt(text):
            return text
    return ""


def last_prompt_epoch(entries):
    for entry, text in reversed(user_texts(entries)):
        if not is_system_prompt(text):
            return entry_epoch(entry)
    return 0


def tool_uses(entries):
    out = []
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        for block in (entry.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out.append(block)
    return out


def edited_files(entries):
    files = []
    for block in tool_uses(entries):
        if block.get("name") in ("Edit", "Write", "NotebookEdit"):
            path = (block.get("input") or {}).get("file_path")
            if path and path not in files:
                files.append(path)
    return files[-constants.COMPACT_EDITED_FILES:]


def failing_commands(entries):
    uses = {b.get("id"): b for b in tool_uses(entries)}
    out = []
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for block in (entry.get("message") or {}).get("content") or [] if isinstance((entry.get("message") or {}).get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                use = uses.get(block.get("tool_use_id")) or {}
                label = (use.get("input") or {}).get("command") or (use.get("input") or {}).get("file_path") or use.get("name") or "tool"
                out.append(str(label).splitlines()[0][:120])
    return out[-constants.COMPACT_FAILING_COMMANDS:]


def fmt_k(tokens):
    return f"{tokens // 1000}k"


def fmt_m(tokens):
    return f"{tokens / 1_000_000:.1f}M"


def is_short_reply(text):
    stripped = text.strip()
    return bool(re.match(constants.QUESTION_PATTERN, stripped, re.I | re.S) or re.match(constants.ACK_PATTERN, stripped, re.I | re.S))


def prompt_text(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content or []):
        return ""
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def steps_per_prompt(entries):
    counts = []
    steps = 0
    started = False
    for entry in entries:
        if entry.get("type") == "assistant":
            steps += started
            continue
        if entry.get("type") != "user" or is_system_prompt(prompt_text(entry)):
            continue
        if started:
            counts.append(steps)
        started = True
        steps = 0
    if started:
        counts.append(steps)
    return counts[-constants.REREAD_RECENT_PROMPTS:]


def reread_check(prompt, tokens, entries, state):
    if tokens < constants.REREAD_ASK_TOKENS or not is_task_prompt(prompt) or is_short_reply(prompt):
        return None
    if tokens - state.get("reread_asked_at", 0) < constants.REREAD_ASK_STEP_TOKENS:
        return None
    counts = steps_per_prompt(entries)
    mean_steps = max(constants.REREAD_MIN_STEPS, round(sum(counts) / len(counts))) if counts else constants.REREAD_MIN_STEPS
    if counts and sum(counts) / len(counts) < constants.REREAD_MIN_STEPS:
        return None
    here = tokens * mean_steps
    delegated = tokens * constants.REREAD_MAIN_TURNS_WHEN_DELEGATED + mean_steps * constants.REREAD_SUBAGENT_MEAN_TOKENS
    mode = "delegate" if delegated <= here * constants.REREAD_DELEGATE_RATIO else "here"
    if mode == "delegate":
        rule = (
            f"(3) otherwise delegate without asking: write a self-contained brief (goal, files by absolute path, done condition, constraints), "
            f"make one Agent call with subagent_type general-purpose, never fork, and relay its summary. Do not ask the user whether to delegate."
        )
        verdict = "Claude delegates unless the work needs this conversation."
    else:
        rule = f"(3) otherwise do it here within the turn budget of {constants.TURN_STOP_STEPS} tool calls, batching commands and running each test suite once."
        verdict = "Delegation would not pay; Claude works here within the turn budget."
    context = (
        f"kiasi re-read check: the context is {fmt_k(tokens)} tokens and every step of this turn re-reads all of it. "
        f"Recent prompts in this session took about {mean_steps} steps each, so doing this work here costs about {fmt_m(here)} re-read tokens; "
        f"one subagent with a fresh context would cost about {fmt_m(delegated)}. Decide before your first tool call: "
        f"(1) if the prompt is answerable from what is already in the conversation, answer with no tool calls; "
        f"(2) if the work needs back-and-forth with the user or the exact state of this conversation, do it here; " + rule
    )
    message = f"kiasi: context {fmt_k(tokens)}, about {mean_steps} steps per prompt lately: roughly {fmt_m(here)} tokens re-read if done here, {fmt_m(delegated)} in a subagent. {verdict}"
    return {"context": context, "message": message, "steps": mean_steps, "here": here, "delegated": delegated, "mode": mode}


def transcript_key(transcript_path):
    return Path(transcript_path or "main").stem


def save_paste(session_id, state, prompt):
    ensure_dirs()
    index = len(state["pastes"]) + 1
    path = constants.PASTE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', session_id)}-{index}.txt"
    path.write_text(prompt)
    state["pastes"].append({"path": str(path), "chars": len(prompt), "head": prompt[: constants.PASTE_HEAD_CHARS].replace("\n", " ")})
    return path


def config_fingerprint(cwd):
    """(mtime, size) of every file in each CONFIG_FINGERPRINT group; stat only, so it is cheap on every prompt."""
    names = {"cwd": cwd or ".", "plugin": str(constants.PLUGIN_ROOT)}
    prints = {}
    for group, patterns in constants.CONFIG_FINGERPRINT.items():
        stats = []
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.expanduser(pattern.format(**names)))):
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                stats.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        prints[group] = "|".join(stats)
    return prints


def config_changes(state, cwd):
    """Groups whose files changed since this session's previous prompt; the first prompt only records them."""
    prints = config_fingerprint(cwd)
    before = state.get("config")
    state["config"] = prints
    if not before:
        return []
    return [group for group in constants.CONFIG_FINGERPRINT if before.get(group) != prints.get(group)]


def handle_prompt(payload):
    prompt = payload.get("prompt") or ""
    session_id = payload.get("session_id", "")
    if is_system_prompt(prompt):
        return None
    state = load_session(session_id)
    entries = tail_entries(payload.get("transcript_path"))
    tokens = current_context_tokens(entries)
    gap_min = (time.time() - last_prompt_epoch(entries)) / 60 if entries else 0
    context_lines = []
    messages = []
    record = {"event": "prompt", "session_id": session_id, "context_tokens": tokens, "prompt_chars": len(prompt), "gap_minutes": round(gap_min)}
    if len(prompt) >= constants.PASTE_BLOCK_CHARS:
        path = save_paste(session_id, state, prompt)
        save_session(session_id, state)
        log_event({**record, "paste_blocked": str(path)})
        return {"decision": "block", "reason": f"kiasi: this prompt is {len(prompt)} chars, about {fmt_k(len(prompt) // constants.CHARS_PER_TOKEN)} tokens, and would be re-read on every step of every later turn. "
                                                f"It is saved at {path}. Send the instruction again with that path in place of the text; Claude will read it by section."}
    state["prompts"] = state.get("prompts", 0) + 1
    changed = config_changes(state, payload.get("cwd"))
    if changed:
        record["config_changed"] = changed
    state.setdefault("turns", {})[transcript_key(payload.get("transcript_path"))] = {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state["prompts"]}
    if len(prompt) >= constants.PASTE_MIN_CHARS:
        path = save_paste(session_id, state, prompt)
        context_lines.append(f"The pasted content in this prompt ({len(prompt)} chars) is also saved at {path}. After this turn refer to it by that path instead of quoting it, and expect it to be dropped from the conversation summary.")
        record["paste_saved"] = str(path)
    nudge = None
    if tokens >= constants.CONTEXT_HARD_TOKENS and tokens - state.get("nudged_at", 0) >= constants.CONTEXT_NUDGE_STEP_TOKENS:
        nudge = f"Context is at {fmt_k(tokens)} tokens; every turn re-reads all of it. Run /compact now, or /clear if this prompt starts a new task."
    elif tokens >= constants.CONTEXT_WARN_TOKENS and gap_min >= constants.NEW_TASK_GAP_MINUTES and tokens - state.get("nudged_at", 0) >= constants.CONTEXT_NUDGE_STEP_TOKENS:
        nudge = f"You were away {int(gap_min)} min and the context is at {fmt_k(tokens)} tokens. If this is a new task, /clear first; the last session note is saved for the next session."
    if nudge:
        messages.append(nudge)
        state["nudged_at"] = tokens
        record["nudge"] = nudge
    check = reread_check(prompt, tokens, entries, state)
    if check:
        context_lines.append(check["context"])
        messages.append(check["message"])
        state["reread_asked_at"] = tokens
        record["reread_check"] = {"steps": check["steps"], "here": check["here"], "delegated": check["delegated"], "mode": check["mode"]}
    save_session(session_id, state)
    log_event(record)
    if not context_lines and not messages:
        return None
    output = {"suppressOutput": True}
    if context_lines:
        output["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": "\n".join(context_lines)}
    if messages:
        output["systemMessage"] = " ".join(messages)
    return output


def handle_agent(payload):
    tool_input = payload.get("tool_input") or {}
    agent_type = tool_input.get("subagent_type") or "general-purpose"
    prompt = tool_input.get("prompt") or ""
    record = {"event": "agent", "session_id": payload.get("session_id", ""), "agent_type": agent_type, "prompt_chars": len(prompt), "requested_model": tool_input.get("model")}
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    specific = output["hookSpecificOutput"]
    if re.search(constants.AGENT_REPEAT_REVIEW_PATTERN, prompt) or re.search(constants.AGENT_REPEAT_REVIEW_PATTERN, tool_input.get("description") or ""):
        specific["permissionDecision"] = "ask"
        specific["permissionDecisionReason"] = "kiasi: this looks like a repeat review round; each subagent re-reads its whole context on every turn. Confirm it is needed."
        record["decision"] = "ask"
    if agent_type != "fork":
        updated = dict(tool_input)
        if not tool_input.get("model"):
            updated["model"] = constants.AGENT_DEFAULT_MODEL.get(agent_type, constants.AGENT_FALLBACK_MODEL)
            record["model_set"] = updated["model"]
        suffix = constants.SUBAGENT_BRIEF_SUFFIX.format(steps=constants.SUBAGENT_STEP_LIMIT)
        if suffix not in prompt:
            updated["prompt"] = prompt.rstrip() + "\n\n" + suffix
            record["brief_suffix"] = True
        specific["updatedInput"] = updated
    if len(prompt) > constants.AGENT_PROMPT_MAX_CHARS:
        specific["additionalContext"] = f"kiasi: this subagent prompt is {len(prompt)} chars; long prompts are re-read on every subagent turn. Prefer a short brief plus file paths."
        record["long_prompt"] = True
    log_event(record)
    return output if len(specific) > 1 else None


def response_text(tool_name, response):
    if isinstance(response, str):
        return response
    if isinstance(response, list):
        if all(isinstance(block, dict) and block.get("type") == "text" for block in response):
            return "\n".join(block.get("text", "") for block in response)
        return ""
    if not isinstance(response, dict):
        return json.dumps(response, ensure_ascii=False)
    if tool_name == "Bash":
        return "\n".join(part for part in (response.get("stdout", ""), response.get("stderr", "")) if part)
    if tool_name == "Read":
        return "" if response.get("type") == "image" else (response.get("file") or {}).get("content", "")
    for key in ("content", "result"):
        if isinstance(response.get(key), str):
            return response[key]
    return json.dumps(response, ensure_ascii=False)


def leading_command(command):
    text = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", command.strip())
    text = re.sub(r"^\s*\w+=\S+\s+", "", text)
    words = text.split()
    if not words:
        return ""
    if len(words) > 1 and words[0] == "git":
        return f"git {words[1]}"
    return words[0].rsplit("/", 1)[-1]


def is_bulk_command(command):
    return leading_command(command) not in constants.SELECTOR_COMMANDS and any(re.search(p, command) for p in constants.BULK_PATTERNS)


def error_lines(lines):
    pattern = re.compile(constants.ERROR_LINE_PATTERN)
    return [i for i, line in enumerate(lines) if pattern.search(line)]


def save_output(tool_use_id, text):
    ensure_dirs()
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool_use_id or f"unknown-{int(time.time())}")
    path = constants.OUTPUT_DIR / f"{safe}.txt"
    path.write_text(text)
    return str(path)


def head_cut(text, head_chars, saved_path, label):
    return text[:head_chars] + f"\n[kiasi kept the first {head_chars} of {len(text)} chars of this {label}; full text saved at {saved_path}, read it with offset if you need more]"


def bulk_cut(text, saved_path):
    lines = text.splitlines()
    head, tail = constants.BULK_HEAD_LINES, constants.BULK_TAIL_LINES
    if len(lines) <= head + tail:
        return None
    middle_errors = [i for i in error_lines(lines) if head <= i < len(lines) - tail][: constants.BULK_ERROR_LINES]
    kept = lines[:head] + [f"[kiasi trimmed {len(lines) - head - tail} of {len(lines)} lines here; full output saved at {saved_path}]"]
    kept += [f"{i + 1}: {lines[i]}" for i in middle_errors] + lines[-tail:]
    return "\n".join(kept)


def failure_cut(text, saved_path):
    lines = text.splitlines()
    head, tail = constants.FAIL_HEAD_LINES, constants.BULK_TAIL_LINES
    if len(lines) <= head + tail:
        return None
    wanted = set()
    failure = re.compile(constants.FAIL_LINE_PATTERN)
    middle_lines = lines[head:len(lines) - tail]
    hits = set(error_lines(middle_lines)) | {i for i, line in enumerate(middle_lines) if failure.search(line)}
    for i in sorted(hits):
        wanted.update(range(head + i, min(head + i + constants.FAIL_CONTEXT_LINES + 1, len(lines) - tail)))
    middle = sorted(wanted)[: constants.FAIL_MAX_LINES]
    kept = lines[:head] + [f"[kiasi kept {len(middle)} failure lines of {len(lines)}; full output saved at {saved_path}]"]
    previous = None
    for i in middle:
        if previous is not None and i != previous + 1:
            kept.append("...")
        kept.append(f"{i + 1}: {lines[i]}")
        previous = i
    return "\n".join(kept + lines[-tail:])


def checkpoint_path(key, turn):
    return constants.CHECKPOINT_DIR / f"{key[:8]}-{turn.get('index', 0)}.md"


def turn_guard(payload, tokens):
    tool_name = payload.get("tool_name", "")
    session_id = payload.get("session_id", "")
    key = transcript_key(payload.get("transcript_path"))
    state = load_session(session_id)
    turn = state.setdefault("turns", {}).setdefault(key, {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state.get("prompts", 0)})
    turn["steps"] += 1
    turn["reread"] += tokens
    state["turn_budget"] = {"warn": constants.TURN_WARN_STEPS, "stop": constants.TURN_STOP_STEPS}
    result = None
    over_stop = turn["steps"] >= constants.TURN_STOP_STEPS or turn["reread"] >= constants.TURN_STOP_TOKENS
    over_warn = turn["steps"] >= constants.TURN_WARN_STEPS or turn["reread"] >= constants.TURN_WARN_TOKENS
    checkpoint = checkpoint_path(key, turn)
    if over_stop and tool_name not in constants.TURN_EXEMPT_TOOLS and (not turn["stopped"] or (turn["steps"] - turn["stopped"]) % constants.TURN_REMIND_STEPS == 0):
        first = not turn["stopped"]
        turn["stopped"] = turn["stopped"] or turn["steps"]
        reason = (
            f"kiasi turn budget exhausted: {turn['steps']} tool calls and {fmt_m(turn['reread'])} tokens re-read in this turn at a context of {fmt_k(tokens)}. "
            f"Make no further Read, Bash, Edit or search calls. Write what remains as a checklist to {checkpoint} (Write is allowed), then either make one Agent call with subagent_type general-purpose "
            f"whose brief is that checklist path plus the done condition, or end the turn reporting what is done, what is verified and what remains."
        ) if first else f"kiasi: turn budget still exhausted ({turn['steps']} tool calls, {fmt_m(turn['reread'])} tokens). Write the checklist to {checkpoint} and stop or delegate."
        result = {"decision": "block", "reason": reason}
        log_event({"event": "turn_stop", "session_id": session_id, "transcript": key, "steps": turn["steps"], "reread": turn["reread"], "context_tokens": tokens, "first": first})
    elif over_warn and not turn["warned"]:
        turn["warned"] = True
        context = (
            f"kiasi turn budget: this turn has made {turn['steps']} tool calls and re-read {fmt_m(turn['reread'])} tokens at a context of {fmt_k(tokens)}. "
            f"The turn is stopped at {constants.TURN_STOP_STEPS} tool calls or {fmt_m(constants.TURN_STOP_TOKENS)} tokens. Before that: finish the item in progress, "
            f"write the remaining items as a checklist to {checkpoint}, then end the turn with that path or hand the checklist to one general-purpose subagent."
        )
        result = {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "additionalContext": context}}
        log_event({"event": "turn_warn", "session_id": session_id, "transcript": key, "steps": turn["steps"], "reread": turn["reread"], "context_tokens": tokens})
    save_session(session_id, state)
    return result


def reader_key(payload):
    return payload.get("agent_id") or transcript_key(payload.get("transcript_path"))


def read_key(tool_input):
    return f"{tool_input.get('file_path', '')}|{tool_input.get('offset') or ''}|{tool_input.get('limit') or ''}"


def file_stamp(path):
    try:
        stat = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return [stat.st_mtime_ns, stat.st_size]


def forget_reads(session_id):
    state = load_session(session_id)
    if state.get("reads"):
        state["reads"] = {}
        save_session(session_id, state)


def read_nudge(payload, tool_input, session_id, state):
    path = tool_input.get("file_path") or ""
    if tool_input.get("offset") or tool_input.get("limit") or os.path.splitext(path)[1].lower() in constants.READ_NUDGE_SKIP_SUFFIXES:
        return None
    if path.startswith(str(constants.DATA_DIR)):
        return None
    stamp = file_stamp(path)
    if not stamp or stamp[1] < constants.READ_NUDGE_CHARS:
        return None
    nudges = state.setdefault("read_nudges", {}).setdefault(reader_key(payload), [])
    if path in nudges:
        nudges.remove(path)
        save_session(session_id, state)
        return None
    nudges.append(path)
    save_session(session_id, state)
    log_event({"event": "read_nudge", "session_id": session_id, "path": path, "chars": stamp[1], "agent_id": payload.get("agent_id")})
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": constants.READ_NUDGE_REASON.format(path=path, chars=stamp[1])}}


def handle_read_check(payload):
    tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    reads = state.setdefault("reads", {}).setdefault(reader_key(payload), {})
    key = read_key(tool_input)
    entry = reads.get(key)
    if not entry:
        return read_nudge(payload, tool_input, session_id, state)
    path = tool_input.get("file_path", "")
    if file_stamp(path) != entry.get("stamp"):
        reads.pop(key)
        save_session(session_id, state)
        return None
    if entry.get("skipped"):
        reads.pop(key)
        save_session(session_id, state)
        log_event({"event": "read_retry", "session_id": session_id, "path": path, "chars": entry.get("chars", 0)})
        return None
    entry["skipped"] = True
    save_session(session_id, state)
    log_event({"event": "read_skipped", "session_id": session_id, "path": path, "chars": entry.get("chars", 0), "agent_id": payload.get("agent_id")})
    span = f" (offset {tool_input.get('offset') or 0}, limit {tool_input.get('limit')})" if tool_input.get("offset") or tool_input.get("limit") else ""
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": constants.READ_SKIP_REASON.format(path=path, span=span)}}


def track_reads(payload, capped):
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not path or (tool_name != constants.READ_TOOL and tool_name not in constants.EDIT_TOOLS):
        return
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    reads = state.setdefault("reads", {})
    if tool_name in constants.EDIT_TOOLS:
        for entries in reads.values():
            for key in [key for key in entries if key.startswith(f"{path}|")]:
                entries.pop(key)
    else:
        text = response_text(tool_name, payload.get("tool_response"))
        if capped or len(text) < constants.READ_SKIP_MIN_CHARS:
            return
        reads.setdefault(reader_key(payload), {})[read_key(tool_input)] = {"stamp": file_stamp(path), "chars": len(text)}
    save_session(session_id, state)


def handle_pre_tool(payload):
    if payload.get("tool_name") == constants.READ_TOOL:
        return handle_read_check(payload)
    return handle_agent(payload)


def handle_post_tool(payload):
    entries = tail_entries(payload.get("transcript_path"))
    guard = turn_guard(payload, current_context_tokens(entries))
    capped = handle_tool_output(payload)
    track_reads(payload, capped)
    if not guard:
        return capped
    if capped:
        guard.setdefault("hookSpecificOutput", {}).update(capped["hookSpecificOutput"])
    return guard


def clean_output(text):
    ansi = re.compile(constants.ANSI_PATTERN)
    lines = [ansi.sub("", line.rstrip("\r")).rsplit("\r", 1)[-1] for line in text.split("\n")]
    kept = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        run = j - i + 1
        if run >= constants.CLEAN_REPEAT_MIN and lines[i].strip():
            kept += [lines[i], f"[kiasi collapsed {run - 1} repeats of the line above]"]
        else:
            kept += lines[i:j + 1]
        i = j + 1
    return "\n".join(kept)


def cleanable(tool_name, command, text):
    if tool_name != "Bash" or len(text) <= constants.CLEAN_MIN_CHARS or leading_command(command) in constants.SELECTOR_COMMANDS:
        return False
    return is_bulk_command(command) or "\x1b" in text or "\r" in text.replace("\r\n", "\n")


def failure_label(tool_name, tool_input):
    target = tool_input.get("command") or tool_input.get("file_path") or tool_input.get("pattern") or tool_input.get("url") or ""
    return f"{tool_name} {' '.join(str(target).split())}".strip()[: constants.LOOP_LABEL_CHARS]


def loop_check(payload):
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    turn = state.setdefault("turns", {}).setdefault(transcript_key(payload.get("transcript_path")), {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state.get("prompts", 0)})
    fails = turn.setdefault("fails", {})
    nudged = turn.setdefault("loop_nudged", [])
    label = failure_label(tool_name, tool_input)
    lead = f"{tool_name} {leading_command(tool_input.get('command') or '')}" if tool_name == "Bash" else label
    fails[label] = fails.get(label, 0) + 1
    if lead != label:
        fails[f"lead:{lead}"] = fails.get(f"lead:{lead}", 0) + 1
    hit = None
    if fails[label] >= constants.LOOP_SAME_FAILS and label not in nudged:
        hit = (label, label, fails[label])
    elif lead != label and fails[f"lead:{lead}"] >= constants.LOOP_LEAD_FAILS and f"lead:{lead}" not in nudged:
        hit = (f"lead:{lead}", f"{lead} (with different arguments)", fails[f"lead:{lead}"])
    if hit:
        nudged.append(hit[0])
        log_event({"event": "loop", "session_id": session_id, "label": hit[1], "count": hit[2]})
    save_session(session_id, state)
    return constants.LOOP_REASON.format(label=hit[1], count=hit[2]) if hit else None


def handle_tool_failure(payload):
    if payload.get("is_interrupt"):
        return None
    guard = turn_guard(payload, current_context_tokens(tail_entries(payload.get("transcript_path"))))
    nudge = loop_check(payload)
    if not nudge:
        return guard
    output = guard or {}
    specific = output.setdefault("hookSpecificOutput", {"hookEventName": payload.get("hook_event_name", "PostToolUseFailure")})
    specific["additionalContext"] = "\n".join(part for part in (specific.get("additionalContext"), nudge) if part)
    return output


def handle_tool_output(payload):
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    text = response_text(tool_name, payload.get("tool_response"))
    body = clean_output(text) if cleanable(tool_name, command, text) else text
    cleaned = len(text) - len(body) >= constants.CLEAN_MIN_SAVED_CHARS
    shown = None
    kind = None
    if tool_name in constants.WEB_TOOLS and len(text) > constants.CAP_WEB_CHARS:
        kind = "web"
    elif tool_name == "Read" and len(text) > constants.CAP_OUTSIDE_READ_CHARS and re.search(constants.OUTSIDE_READ_PATTERN, tool_input.get("file_path") or ""):
        kind = "outside-read"
    elif tool_name == "Bash" and len(body) > constants.CAP_BULK_CHARS and is_bulk_command(command):
        kind = "test" if re.search(constants.TEST_COMMAND_PATTERN, command) else "bulk"
    elif tool_name == "Bash" and len(body) > constants.CAP_BASH_CHARS:
        kind = "bash"
    elif tool_name.startswith("mcp__") and not tool_name.startswith(constants.MCP_CAP_EXEMPT_PREFIX) and len(text) > constants.CAP_MCP_CHARS:
        kind = "mcp"
    if not kind and not cleaned:
        return None
    saved = save_output(payload.get("tool_use_id"), text)
    if kind == "web":
        shown = head_cut(text, constants.CAP_WEB_HEAD_CHARS, saved, "web result")
    elif kind == "outside-read":
        shown = head_cut(text, constants.CAP_OUTSIDE_READ_HEAD_CHARS, saved, "file")
    elif kind == "mcp":
        shown = head_cut(text, constants.CAP_MCP_HEAD_CHARS, saved, "tool result")
    elif kind == "test":
        shown = failure_cut(body, saved)
    elif kind == "bulk":
        shown = bulk_cut(body, saved)
    elif kind == "bash":
        resp = payload.get("tool_response")
        failed = isinstance(resp, dict) and (bool(resp.get("stderr")) or resp.get("exitCode") not in (None, 0))
        shown = (failure_cut(body, saved) if failed else bulk_cut(body, saved)) \
            or head_cut(body, constants.CAP_BASH_HEAD_CHARS, saved, "command output")
    if shown is None and cleaned:
        kind, shown = "clean", body + f"\n[kiasi removed colour codes and repeated lines; original output saved at {saved}]"
    if shown is None:
        return None
    log_event({"event": "cap", "session_id": payload.get("session_id", ""), "tool_name": tool_name, "kind": kind, "chars": len(text), "shown_chars": len(shown), "saved_path": saved,
               "label": str(tool_input.get("command") or tool_input.get("file_path") or tool_input.get("url") or tool_input.get("query") or "")[:120]})
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    state["kept_tokens"] = state.get("kept_tokens", 0) + (len(text) - len(shown)) // constants.CHARS_PER_TOKEN
    save_session(session_id, state)
    return {"hookSpecificOutput": {"hookEventName": payload.get("hook_event_name", "PostToolUse"), "updatedToolOutput": shown}}


def handle_pre_compact(payload):
    session_id = payload.get("session_id", "")
    entries = tail_entries(payload.get("transcript_path"))
    state = load_session(session_id)
    task = last_task_prompt(entries)
    files = edited_files(entries)
    failures = failing_commands(entries)
    write_note(session_id, payload.get("cwd") or "", task, files, "", state, force=True)
    forget_reads(session_id)
    log_event({"event": "compact", "session_id": session_id, "trigger": payload.get("trigger"), "context_tokens": current_context_tokens(entries), "files": len(files), "failures": len(failures), "pastes": len(state.get("pastes", []))})
    return None


def handle_plugin_compact(payload):
    log_event({"event": "plugin_compact", **{key: payload.get(key) for key in constants.PLUGIN_COMPACT_FIELDS}})
    forget_reads(payload.get("session_id") or "")
    return None


def handle_archive_path(payload):
    ensure_dirs()
    name = f"compact-{(payload.get('session_id') or 'unknown')[:8]}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    return {"path": str(constants.OUTPUT_DIR / name)}


def handle_archive(payload):
    path = Path(payload.get("path") or "")
    if path.parent.resolve() != constants.OUTPUT_DIR.resolve():
        return None
    ensure_dirs()
    path.write_text("\n\n".join(f"=== #{index} ===\n{text}" for index, text in enumerate(payload.get("items") or [], 1)))
    return None


def handle_plugin_quiet(payload):
    log_event({"event": "quiet", **{key: payload.get(key) for key in constants.PLUGIN_QUIET_FIELDS}})
    return None


def project_slug(cwd):
    return re.sub(r"[^A-Za-z0-9]+", "-", cwd or "unknown").strip("-") or "unknown"


def write_note(session_id, cwd, task, files, last_message, state, force=False):
    if not task:
        return False
    if not force and time.time() - state.get("last_note_ts", 0) < constants.NOTE_MIN_INTERVAL_MINUTES * 60:
        return False
    ensure_dirs()
    note = {"ts": now_iso(), "session_id": session_id, "cwd": cwd, "task": task[:300], "files": files, "last_message": last_message[: constants.NOTE_MESSAGE_HEAD_CHARS]}
    with open(constants.NOTES_DIR / f"{project_slug(cwd)}.jsonl", "a") as fh:
        fh.write(json.dumps(note, ensure_ascii=False) + "\n")
    state["last_note_ts"] = time.time()
    save_session(session_id, state)
    return True


def handle_stop(payload):
    session_id = payload.get("session_id", "")
    entries = tail_entries(payload.get("transcript_path"))
    state = load_session(session_id)
    written = write_note(session_id, payload.get("cwd") or "", last_task_prompt(entries), edited_files(entries), payload.get("last_assistant_message") or "", state)
    turn = state.get("turns", {}).get(transcript_key(payload.get("transcript_path")), {})
    log_event({"event": "stop", "session_id": session_id, "context_tokens": current_context_tokens(entries), "note_written": written, "steps": turn.get("steps", 0), "reread": turn.get("reread", 0), "stopped": bool(turn.get("stopped"))})
    return None


def budget_line():
    try:
        report = json.loads(constants.BUDGET_FILE.read_text())
        built = time.mktime(time.strptime(report["generated"], "%Y-%m-%dT%H:%M:%S"))
    except (OSError, ValueError, KeyError):
        return ""
    if time.time() - built > constants.BUDGET_FRESH_HOURS * 3600:
        return ""
    t = report["totals"]
    reread = t["main"].get("cache_read_input_tokens", 0) + t["sub"].get("cache_read_input_tokens", 0)
    return f"Budget for the last {report['days']} days: {fmt_m(reread)} tokens re-read, mean context {fmt_k(t['mean_context'])}, {t.get('mean_steps', 0)} steps per prompt, {t.get('long_turns', 0)} turns over the step budget."


def last_note(cwd):
    path = constants.NOTES_DIR / f"{project_slug(cwd)}.jsonl"
    try:
        lines = [l for l in path.read_text().splitlines() if l.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            note = json.loads(line)
        except ValueError:
            continue
        age_days = (time.time() - time.mktime(time.strptime(note["ts"], "%Y-%m-%dT%H:%M:%S"))) / 86400
        return note if age_days <= constants.NOTE_MAX_AGE_DAYS else None
    return None


def env_warning():
    missing = {k: v for k, v in constants.REQUIRED_ENV.items() if os.environ.get(k) != v}
    if not missing:
        return ""
    snippet = ", ".join(f'"{k}": "{v}"' for k, v in missing.items())
    return (f"kiasi: {', '.join(missing)} not set. Add to the env block of ~/.claude/settings.json for the full effect: "
            f'{{ "env": {{ {snippet} }} }}')


def rules_text():
    try:
        return constants.RULES_FILE.read_text().strip()
    except OSError:
        return ""


def record_data_dir():
    if not os.environ.get("CLAUDE_PLUGIN_DATA"):
        return
    try:
        current = constants.DATA_DIR_POINTER.read_text().strip()
    except OSError:
        current = ""
    if current == str(constants.DATA_DIR):
        return
    constants.DATA_DIR_POINTER.parent.mkdir(parents=True, exist_ok=True)
    constants.DATA_DIR_POINTER.write_text(str(constants.DATA_DIR) + "\n")


def migrate_legacy_dir(old, new):
    if old.is_symlink() or not old.is_dir():
        return False
    if new.is_symlink() or (new.is_dir() and any(new.iterdir())):
        return False
    if new.is_dir():
        new.rmdir()
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    old.symlink_to(new, target_is_directory=True)
    return True


def merge_legacy_log(directory):
    # Events logged under the old name come first; anything the renamed hooks
    # already wrote is appended after them.
    legacy = directory / constants.LEGACY_EVENT_LOG_NAME
    if not legacy.is_file():
        return False
    current = directory / constants.EVENT_LOG.name
    merged = legacy.read_bytes()
    if merged and not merged.endswith(b"\n"):
        merged += b"\n"
    if current.is_file():
        merged += current.read_bytes()
    staging = current.with_name(current.name + ".merging")
    staging.write_bytes(merged)
    staging.replace(current)
    legacy.unlink()
    return True


def migrate_legacy_data():
    data = constants.DATA_DIR
    pairs = [
        (data.with_name(data.name.replace(constants.PLUGIN_NAME, constants.LEGACY_NAME, 1)), data),
        (constants.LEGACY_HOME_DIR, constants.HOME_DIR),
    ]
    for old, new in pairs:
        if old == new:
            continue
        try:
            migrate_legacy_dir(old, new)
        except OSError as exc:
            sys.stderr.write(f"kiasi: could not move {old} to {new}: {exc}\n")
    try:
        merge_legacy_log(data)
    except OSError as exc:
        sys.stderr.write(f"kiasi: could not merge the old event log: {exc}\n")


def launch_cleanup(session_id):
    """Start cleanup.py detached so session start never waits on it; it runs at most once a day."""
    if constants.CLEANUP_MODE == "off":
        return
    try:
        import subprocess
        subprocess.Popen([sys.executable, str(constants.PLUGIN_ROOT / "scripts" / "cleanup.py"), "--if-due", "--quiet", "--session", session_id],
                         env={**os.environ, "CLAUDE_PLUGIN_DATA": str(constants.DATA_DIR)},  # same data folder as this hook, never another
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        sys.stderr.write(f"kiasi: cleanup not started: {exc}\n")


def open_failures(entries):
    uses = {b.get("id"): b for b in tool_uses(entries)}
    last = {}
    for entry in entries:
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") != "user" or not isinstance(content, list):
            continue
        for block in content:
            use = uses.get(block.get("tool_use_id")) if isinstance(block, dict) and block.get("type") == "tool_result" else None
            if use:
                label = failure_label(use.get("name", ""), use.get("input") or {})
                last.pop(label, None)
                last[label] = bool(block.get("is_error"))
    return [label for label, failed in last.items() if failed][-constants.STATE_MAX_FAILURES:]


def session_events(session_id, event):
    try:
        lines = constants.EVENT_LOG.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        if session_id not in line or f'"{event}"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("event") == event and record.get("session_id") == session_id:
            out.append(record)
    return out


def state_block(payload):
    session_id = payload.get("session_id", "")
    entries = [e for e in tail_entries(payload.get("transcript_path")) if not e.get("isCompactSummary")]
    task = " ".join(last_task_prompt(entries).split())
    files = edited_files(entries)[-constants.STATE_MAX_FILES:]
    failures = open_failures(entries)
    outputs = list(dict.fromkeys(r.get("saved_path") for r in session_events(session_id, "cap") if r.get("saved_path")))[-constants.STATE_MAX_OUTPUTS:]
    checklists = sorted(constants.CHECKPOINT_DIR.glob(f"{transcript_key(payload.get('transcript_path'))[:8]}-*.md"), key=lambda p: p.stat().st_mtime)[-constants.STATE_MAX_CHECKLISTS:]
    log_event({"event": "state", "session_id": session_id, "task": bool(task), "files": len(files), "failures": len(failures), "outputs": len(outputs), "checklists": len(checklists)})
    lines = []
    if task:
        lines.append(f"Task: {task[: constants.STATE_TASK_CHARS]}")
    if files:
        lines.append(f"Files edited: {', '.join(files)}")
    if failures:
        lines.append(f"Still failing at the last attempt: {'; '.join(failures)}")
    if outputs:
        lines.append(f"Full outputs saved earlier (read by path if needed): {', '.join(outputs)}")
    if checklists:
        lines.append(f"Checklists written this session: {', '.join(str(p) for p in checklists)}")
    if not lines:
        return ""
    return "kiasi state after compaction (read from the transcript, not summarised):\n" + "\n".join(f"- {line}" for line in lines)


def handle_session_start(payload):
    migrate_legacy_data()
    record_data_dir()
    launch_cleanup(payload.get("session_id", ""))
    note = last_note(payload.get("cwd") or "")
    log_event({"event": "session_start", "session_id": payload.get("session_id", ""), "source": payload.get("source"), "note": bool(note)})
    lines = [rules_text(), env_warning(), budget_line(), state_block(payload) if payload.get("source") == "compact" else ""]
    if note and note.get("session_id") != payload.get("session_id"):
        files = ", ".join(note.get("files") or []) or "none recorded"
        lines.append(f"Last session note for this project ({note['ts'][:16]}): task was \"{note['task']}\". Files edited: {files}. It ended with: {note.get('last_message', '')[:200]}")
    text = "\n".join(line for line in lines if line)
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


HANDLERS = {
    "UserPromptSubmit": handle_prompt,
    "PreToolUse": handle_pre_tool,
    "PostToolUse": handle_post_tool,
    "PostToolUseFailure": handle_tool_failure,
    "PreCompact": handle_pre_compact,
    "PluginArchivePath": handle_archive_path,
    "PluginArchive": handle_archive,
    "PluginCompact": handle_plugin_compact,
    "PluginQuiet": handle_plugin_quiet,
    "Stop": handle_stop,
    "SessionStart": handle_session_start,
}


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    constants.apply_project(payload.get("cwd") or "")
    handler = HANDLERS.get(payload.get("hook_event_name", ""))
    if not handler:
        return
    if payload.get("hook_event_name") == "PreToolUse" and payload.get("tool_name") not in (constants.AGENT_TOOL, constants.READ_TOOL):
        return
    output = handler(payload)
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"kiasi fail-open: {exc}\n")
    sys.exit(0)
