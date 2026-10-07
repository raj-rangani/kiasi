import os
import re

from core import constants
from core.caps import response_text
from core.events import load_session, log_event, save_session
from core.holdout import held_off
from core.transcript import caller_key, response_id, tail_entries


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
        budget = "" if constants.TURN_BUDGET_MODE == "off" else constants.SUBAGENT_BRIEF_BUDGET.format(steps=constants.SUBAGENT_STEP_LIMIT)
        suffix = budget + constants.SUBAGENT_BRIEF_SUFFIX
        if suffix not in prompt:
            updated["prompt"] = prompt.rstrip() + "\n\n" + suffix
            record["brief_suffix"] = True
        specific["updatedInput"] = updated
    if len(prompt) > constants.AGENT_PROMPT_MAX_CHARS:
        specific["additionalContext"] = f"kiasi: this subagent prompt is {len(prompt)} chars; long prompts are re-read on every subagent turn. Prefer a short brief plus file paths."
        record["long_prompt"] = True
    log_event(record)
    return output if len(specific) > 1 else None


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
    nudges = state.setdefault("read_nudges", {}).setdefault(caller_key(payload), [])
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
    reads = state.setdefault("reads", {}).setdefault(caller_key(payload), {})
    key = read_key(tool_input)
    entry = reads.get(key)
    if not entry:
        return read_nudge(payload, tool_input, session_id, state)
    path = tool_input.get("file_path", "")
    if file_stamp(path) != entry.get("stamp"):
        reads.pop(key)
        save_session(session_id, state)
        return None
    here = response_id(payload)
    # A repeat goes through only in a later response: identical reads in one response are all refused, as they are one step.
    if entry.get("skipped") and not (here and entry.get("skipped_in") == here):
        reads.pop(key)
        save_session(session_id, state)
        log_event({"event": "read_retry", "session_id": session_id, "path": path, "chars": entry.get("chars", 0)})
        return None
    entry["skipped"] = True
    entry["skipped_in"] = here
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
        reads.setdefault(caller_key(payload), {})[read_key(tool_input)] = {"stamp": file_stamp(path), "chars": len(text)}
    save_session(session_id, state)


def sandbox_tools_on():
    return os.environ.get(constants.ROUTE_ENV_FLAG) == "1"


def is_scan_command(command):
    if re.search(constants.ROUTE_LIMITED_PATTERN, command):
        return False
    return any(re.search(p, command) for p in constants.ROUTE_BASH_PATTERNS)


def route_once(payload, target, key, label, reason):
    session_id = payload.get("session_id", "")
    state = load_session(session_id)
    if held_off(state, "sandbox"):
        return None
    routed = state.setdefault("routed", {}).setdefault(caller_key(payload), [])
    record = {"session_id": session_id, "tool_name": payload.get("tool_name"), "target": target, "label": label, "agent_id": payload.get("agent_id")}
    if key in routed:
        routed.remove(key)
        save_session(session_id, state)
        log_event({"event": "route_retry", **record})
        return None
    routed.append(key)
    save_session(session_id, state)
    log_event({"event": "routed", **record})
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


def route_bash(payload):
    tool_input = payload.get("tool_input") or {}
    command = (tool_input.get("command") or "").strip()
    if not sandbox_tools_on() or not command or tool_input.get("run_in_background") or not is_scan_command(command):
        return None
    label = command[: constants.ROUTE_LABEL_CHARS]
    return route_once(payload, constants.RUN_TOOL_NAME, command, label, constants.ROUTE_BASH_REASON.format(command=label))


def route_web(payload):
    tool_input = payload.get("tool_input") or {}
    url = (tool_input.get("url") or "").strip()
    if not sandbox_tools_on() or not url:
        return None
    label = url[: constants.ROUTE_LABEL_CHARS]
    return route_once(payload, constants.FETCH_TOOL_NAME, url, label, constants.ROUTE_WEB_REASON.format(url=label))


def handle_pre_tool(payload):
    tool_name = payload.get("tool_name")
    if tool_name == constants.READ_TOOL:
        return handle_read_check(payload)
    if tool_name == constants.BASH_TOOL:
        return route_bash(payload)
    if tool_name == constants.WEB_FETCH_TOOL:
        return route_web(payload)
    return handle_agent(payload)
