import os
import re
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import constants
from core.events import checklist_folder, make_checklist_folder, now_iso

SECTIONS = ("Goal", "Decisions", "Checklist", "Files touched", "Next step")
ITEM = re.compile(r"^\s*(?:[-*]\s*)?(?:\[([ xX])\]\s*)?(.*\S)\s*$")
EMPTY = "(none yet)"


def safe_id(session_id):
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")


def link_path(cwd, session_id):
    return checklist_folder(cwd) / f"{constants.TASKFILE_PREFIX}{safe_id(session_id)[:8]}.link"


def path_for(cwd, session_id):
    """One file per task: a task starts at the first prompt of a session, and a session that resumes a paused task reuses its file."""
    try:
        target = link_path(cwd, session_id).read_text().strip()
        if target:
            return Path(target)
    except OSError:
        pass
    return checklist_folder(cwd) / f"{constants.TASKFILE_PREFIX}{safe_id(session_id)[:8]}.md"


def adopt(cwd, session_id, from_session_id):
    """Make session_id continue the task file of from_session_id (a resumed pause)."""
    source = path_for(cwd, from_session_id)
    if from_session_id == session_id or not source.exists():
        return None
    try:
        link = link_path(cwd, session_id)
        make_checklist_folder(link)
        link.write_text(str(source))
    except OSError:
        return None
    return source


def parse(text):
    sections = {name: "" for name in SECTIONS}
    current = None
    for line in text.splitlines():
        match = re.match(r"^## (.+?)\s*$", line)
        if match:
            current = match.group(1) if match.group(1) in sections else None
        elif current is not None:
            sections[current] += line + "\n"
    return {name: ("" if body.strip() == EMPTY else body.strip("\n")) for name, body in sections.items()}


def bullets(value):
    if isinstance(value, str):
        value = value.splitlines()
    return [re.sub(r"^\s*[-*]\s*", "", str(item)).strip() for item in value if str(item).strip()]


def checklist_lines(items):
    """A box is checked only when the item says it was verified."""
    if isinstance(items, str):
        items = items.splitlines()
    lines = []
    for item in items:
        match = ITEM.match(str(item))
        if not match:
            continue
        done = (match.group(1) or " ").lower() == "x" and "verified" in match.group(2).lower()
        lines.append(f"- [{'x' if done else ' '}] {match.group(2)}")
    return "\n".join(lines)


def render(session_id, sections):
    parts = [f"# Task\n\nSession: {session_id} · Updated: {now_iso()}"]
    for name in SECTIONS:
        parts.append(f"## {name}\n\n{sections.get(name, '').strip() or EMPTY}")
    return "\n\n".join(parts) + "\n"


def write(cwd, session_id, goal=None, decisions=None, checklist=None, files=None, next_step=None, last_message=None):
    """Merge what is given into the session's task file and return its path; a section not given is kept as it was."""
    path = path_for(cwd, session_id)
    try:
        sections = parse(path.read_text())
    except OSError:
        sections = {name: "" for name in SECTIONS}
    if goal:
        sections["Goal"] = " ".join(goal.split())[: constants.TASKFILE_GOAL_CHARS]
    if decisions:
        sections["Decisions"] = "\n".join(f"- {line}" for line in bullets(decisions))
    if checklist:
        sections["Checklist"] = checklist_lines(checklist)
    if files:
        merged = list(dict.fromkeys([*bullets(sections["Files touched"]), *bullets(files)]))[-constants.TASKFILE_MAX_FILES:]
        sections["Files touched"] = "\n".join(f"- {line}" for line in merged)
    if next_step:
        sections["Next step"] = next_step.strip()
    elif last_message and not sections["Next step"]:
        sections["Next step"] = "Last message: " + " ".join(last_message.split())[: constants.NOTE_MESSAGE_HEAD_CHARS]
    make_checklist_folder(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(render(session_id, sections))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    return path


def latest(cwd):
    try:
        found = [(p.stat().st_mtime, p) for p in checklist_folder(cwd).glob(f"{constants.TASKFILE_PREFIX}*.md")]
    except OSError:
        return None
    return max(found)[1] if found else None


def start_block(cwd):
    path = latest(cwd)
    if not path:
        return ""
    try:
        text = path.read_text()
        age_days = (time.time() - path.stat().st_mtime) / 86400
    except OSError:
        return ""
    if age_days > constants.NOTE_MAX_AGE_DAYS:
        return ""
    cut = "\n[cut here: read the rest from the file]" if len(text) > constants.TASKFILE_START_CHARS else ""
    return f"Task file {path}:\n{text[: constants.TASKFILE_START_CHARS].rstrip()}{cut}\n{constants.TASKFILE_CONTINUE_LINE}"


if __name__ == "__main__":
    # `taskfile.py [session id]`: print the path the /kiasi:handoff skill writes the task file to.
    cwd = os.getcwd()
    constants.apply_project(cwd)
    print(path_for(cwd, sys.argv[1] if len(sys.argv) > 1 else ""))
