import glob
import os
import re
import time

from core import constants
from core.events import ensure_dirs, load_session, log_event, save_session
from core.transcript import current_context_tokens, fmt_k, is_system_prompt, last_prompt_epoch, tail_entries, transcript_key
from core.turn import reread_check


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
