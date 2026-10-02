import json
import re
import time
from pathlib import Path

from core import constants


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


def transcript_key(transcript_path):
    return Path(transcript_path or "main").stem
