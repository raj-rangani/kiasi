import json
import re
import time
import uuid
from pathlib import Path

from core import constants
from core.holdout import held_off
from core.events import ensure_dirs, load_session, log_event, save_session


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


def handle_tool_output(payload):
    if constants.HOLDOUT and held_off(load_session(payload.get("session_id", "")), "output_cap"):
        return None
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


def handle_archive_path(payload):
    ensure_dirs()
    agent = re.sub(r"[^A-Za-z0-9]", "", payload.get("agent_id") or "")[:8]
    tag = f"-{agent}" if agent else ""
    name = f"compact-{(payload.get('session_id') or 'unknown')[:8]}{tag}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.txt"
    return {"path": str(constants.OUTPUT_DIR / name)}


def handle_archive(payload):
    path = Path(payload.get("path") or "")
    if path.parent.resolve() != constants.OUTPUT_DIR.resolve():
        return {"written": False}
    ensure_dirs()
    try:
        with open(path, "x") as handle:  # never replaces an earlier archive
            handle.write("\n\n".join(f"=== #{index} ===\n{text}" for index, text in enumerate(payload.get("items") or [], 1)))
    except OSError:
        return {"written": False}
    return {"written": True}
