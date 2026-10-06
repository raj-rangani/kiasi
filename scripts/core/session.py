import json
import os
import re
import sys
import time

from core import constants
from core.caps import failure_label
from core.events import checklist_folder, ensure_dirs, load_session, log_event, now_iso, project_slug, save_session
from core.launch import ensure_dashboard, launch_cleanup
from core.reads import forget_reads
from core.transcript import current_context_tokens, edited_files, failing_commands, fmt_k, fmt_m, last_task_prompt, tail_entries, tool_uses, transcript_key
from core.turn import pause_elsewhere, pause_reminder


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


def handle_plugin_quiet(payload):
    log_event({"event": "quiet", **{key: payload.get(key) for key in constants.PLUGIN_QUIET_FIELDS}})
    return None


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
    return pause_reminder(payload)


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
        text = constants.RULES_FILE.read_text().strip()
    except OSError:
        return ""
    lines = (budget_rule(line) for line in text.splitlines())
    return "\n".join(line for line in lines if line is not None)


def budget_rule(line):
    """rules.md states the default budgets: fit its two budget lines to the settings, and drop the turn's when the budget is off."""
    if line.startswith(constants.TURN_RULE_PREFIX):
        template = constants.TURN_RULES.get(constants.TURN_BUDGET_MODE)
    elif line.startswith(constants.SUBAGENT_RULE_PREFIX):
        template = constants.SUBAGENT_RULES.get(constants.TURN_BUDGET_MODE)
    else:
        return line
    if template is None:
        return None
    return template.format(steps=constants.TURN_STOP_STEPS, tokens=f"{round(constants.TURN_STOP_TOKENS / 1_000_000, 1):g}M",
                           margin=constants.TURN_STOP_STEPS - constants.turn_warn_steps(), subagent_steps=constants.SUBAGENT_STEP_LIMIT)


def record_plugin_root():
    """Keep HOME_DIR/plugin-root pointing at the installed plugin so the login-time dashboard launcher finds the current version."""
    try:
        if constants.PLUGIN_ROOT_POINTER.read_text().strip() == str(constants.PLUGIN_ROOT):
            return
    except OSError:
        pass
    try:
        constants.PLUGIN_ROOT_POINTER.parent.mkdir(parents=True, exist_ok=True)
        constants.PLUGIN_ROOT_POINTER.write_text(str(constants.PLUGIN_ROOT) + "\n")
    except OSError:
        pass


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
    pattern = f"{transcript_key(payload.get('transcript_path'))[:8]}-*.md"
    found = {path for folder in (checklist_folder(payload.get("cwd")), constants.CHECKPOINT_DIR) for path in folder.glob(pattern)}
    checklists = sorted(found, key=lambda p: p.stat().st_mtime)[-constants.STATE_MAX_CHECKLISTS:]
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
    record_plugin_root()
    launch_cleanup(payload.get("session_id", ""))
    dashboard_line = ensure_dashboard(payload.get("session_id", ""))
    note = last_note(payload.get("cwd") or "")
    paused = pause_elsewhere(payload)
    log_event({"event": "session_start", "session_id": payload.get("session_id", ""), "source": payload.get("source"), "note": bool(note)})
    lines = [rules_text(), env_warning(), budget_line(), dashboard_line, state_block(payload) if payload.get("source") == "compact" else ""]
    if note and note.get("session_id") != payload.get("session_id"):
        files = ", ".join(note.get("files") or []) or "none recorded"
        lines.append(f"Last session note for this project ({note['ts'][:16]}): task was \"{note['task']}\". Files edited: {files}. It ended with: {note.get('last_message', '')[:200]}")
    if paused:
        lines.append(f"The last session in this project was paused by kiasi at {paused['steps']} steps; its remaining work is in "
                     f"{paused['checkpoint']}. If the developer says continue, read that file and resume from its first open item.")
    text = "\n".join(line for line in lines if line)
    if not text:
        return None
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    if paused:
        output["systemMessage"] = f"Kiasi: the last session paused with work left in {paused['checkpoint']}. Reply continue to resume it."
    return output
