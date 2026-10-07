import glob
import os
import re
import time

from core import constants, taskfile
from core.events import ensure_dirs, load_session, log_event, save_session
from core.transcript import current_context_tokens, fmt_k, is_system_prompt, last_prompt_epoch, tail_entries, transcript_key
from core.holdout import held_off
from core.turn import reread_check, resume_context


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


def cache_ttl_minutes():
    return constants.CACHE_TTL_VALUES.get(os.environ.get(constants.CACHE_TTL_ENV, ""), constants.CACHE_TTL_MINUTES)


def last_call_gap_minutes(state):
    try:
        return (time.time() - time.mktime(time.strptime(state["last_call_at"], "%Y-%m-%dT%H:%M:%S"))) / 60
    except (KeyError, ValueError, TypeError):
        return 0


def handoff_notice(state, tokens):
    """One notice, the 150k one winning over the idle-return one: a handoff then /clear restarts from about the floor."""
    floor = state.get("context_floor") or constants.CONTEXT_FLOOR_DEFAULT
    if tokens >= constants.CONTEXT_HANDOFF_TOKENS and tokens - state.get("handoff_nudged_at", 0) >= constants.CONTEXT_NUDGE_STEP_TOKENS:
        state["handoff_nudged_at"] = tokens
        return f"Context {fmt_k(tokens)} (floor {fmt_k(floor)}). Run /kiasi:handoff, then /clear: this task continues from about {fmt_k(floor + 2000)}."
    gap = last_call_gap_minutes(state)
    held = state.get("context_tokens") or tokens
    if gap > cache_ttl_minutes() and held > constants.CONTEXT_IDLE_HANDOFF_TOKENS and state.get("idle_nudged_for") != state.get("last_call_at"):
        state["idle_nudged_for"] = state.get("last_call_at")
        return (f"Cache cold after {int(gap)} min idle at {fmt_k(held)} context: the next prompt rewrites all of it. "
                f"/kiasi:handoff then /clear starts from about {fmt_k(floor + 2000)}.")
    return None


def words(text):
    return {w for w in re.findall(r"[a-z]{%d,}" % constants.TASK_SWITCH_MIN_WORD, text.lower()) if w not in constants.TASK_SWITCH_STOPWORDS}


def task_switch_notice(state, cwd, session_id, prompt, tokens, gap_min):
    """Once per session: a long gap and a prompt that shares little with the task file's goal and checklist."""
    text = prompt.strip()
    if gap_min < constants.NEW_TASK_GAP_MINUTES or state.get("switch_noticed") or len(text) <= constants.TASK_SWITCH_MIN_CHARS or text.startswith("/") or len(text.split()) < 2:
        return None
    path = taskfile.path_for(cwd, session_id)
    try:
        saved = taskfile.parse(path.read_text())
    except OSError:
        return None
    goal = saved.get("Goal", "")
    task_words = words(f"{goal} {saved.get('Checklist', '')}")
    mine = words(text)
    if not goal or not task_words or not mine or len(mine & task_words) / len(mine) >= constants.TASK_SWITCH_OVERLAP:
        return None
    state["switch_noticed"] = True
    return (f"This looks like a different task from the one in {path} ({goal.splitlines()[0][:80]}). "
            f"If so, /kiasi:handoff then /clear keeps them apart; context is {fmt_k(tokens)}.")


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
    main = transcript_key(payload.get("transcript_path"))
    turns = state.get("turns") or {}
    # The calls of the turn this prompt ends may still report in a PostToolBatch: they are not steps of the new turn. A subagent
    # started by this or the previous prompt may still be running and keeps its own count; older ones are done and their turns go.
    state["cleared_calls"] = [*state.get("cleared_calls", []), *(turns.get(main) or {}).get("calls", [])][-constants.TURN_CALLS_KEPT:]
    state["turns"] = {**{key: turn for key, turn in turns.items() if key != main and turn.get("index", 0) >= state["prompts"] - 1},
                      main: {"steps": 0, "reread": 0, "warned": False, "stopped": False, "index": state["prompts"]}}
    resume = resume_context(state, prompt, payload.get("cwd"))
    if resume:
        context_lines.append(resume["context"])
        if "message" in resume:
            messages.append(resume["message"])
        record["resume"] = resume["mode"]
        if resume["mode"] == "resume" and resume["log"].get("paused_session"):
            taskfile.adopt(payload.get("cwd"), session_id, resume["log"]["paused_session"])
        log_event({"event": "turn_resume", "session_id": session_id, "mode": resume["mode"], **resume["log"]})
    if len(prompt) >= constants.PASTE_MIN_CHARS:
        path = save_paste(session_id, state, prompt)
        context_lines.append(f"The pasted content in this prompt ({len(prompt)} chars) is also saved at {path}. After this turn refer to it by that path instead of quoting it, and expect it to be dropped from the conversation summary.")
        record["paste_saved"] = str(path)
    nudge = None
    notices_off = held_off(state, "context_notices")
    if notices_off:
        pass
    elif tokens >= constants.CONTEXT_HARD_TOKENS and tokens - state.get("nudged_at", 0) >= constants.CONTEXT_NUDGE_STEP_TOKENS:
        nudge = f"Context is at {fmt_k(tokens)} tokens; every turn re-reads all of it. Run /compact now, or /clear if this prompt starts a new task."
    elif tokens >= constants.CONTEXT_WARN_TOKENS and gap_min >= constants.NEW_TASK_GAP_MINUTES and tokens - state.get("nudged_at", 0) >= constants.CONTEXT_NUDGE_STEP_TOKENS:
        nudge = f"You were away {int(gap_min)} min and the context is at {fmt_k(tokens)} tokens. If this is a new task, /clear first; the last session note is saved for the next session."
    if nudge:
        messages.append(nudge)
        state["nudged_at"] = tokens
        record["nudge"] = nudge
    handoff = None if notices_off else handoff_notice(state, tokens)
    if handoff:
        messages.append(handoff)
        record["handoff_notice"] = handoff
    elif not nudge and not notices_off:
        switch = task_switch_notice(state, payload.get("cwd"), session_id, prompt, tokens, last_call_gap_minutes(state))
        if switch:
            messages.append(switch)
            record["task_switch"] = True
    check = None if held_off(state, "reread_check") else reread_check(
        prompt, tokens, entries, state, cwd=payload.get("cwd"), session_id=session_id
    )
    if check:
        context_lines.append(check["context"])
        messages.append(check["message"])
        state["reread_asked_at"] = tokens
        record["reread_check"] = {"steps": check["steps"], "here": check["here"], "delegated": check["delegated"], "mode": check["mode"], "brief": check.get("brief", False)}
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
