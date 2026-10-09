"""Audit the startup floor and write the script that trims it.

Reads the last N days of transcripts for what each project actually used (skills, MCP servers, agents) and what every session was offered,
then prints the findings and writes DATA_DIR/floor-fix.sh with the settings moves. The script is for the developer to read and run;
this audit changes nothing."""
import json
import os
import re
import shlex
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import constants  # noqa: E402
from reports import lens  # noqa: E402
from reports.budget import project_of, transcript_files  # noqa: E402

HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
SETTINGS = HOME / "settings.json"
CLAUDE_JSON = HOME.parent / ".claude.json" if HOME.name == ".claude" else HOME / ".claude.json"
AGENTS_DIR = HOME / "agents"
USER_SKILLS = HOME / "skills"
PLUGIN_CACHE = HOME / "plugins" / "cache"
FIX_FILE = constants.DATA_DIR / "floor-fix.sh"
ONCE = Path(__file__).resolve().parent.parent / "once.py"
BUILTIN_COMMANDS = {"effort", "model", "compact", "clear", "help", "config", "cost", "status", "login", "logout", "init", "memory", "review", "doctor", "mcp", "agents", "hooks", "permissions", "resume", "bug", "exit", "quit", "fast", "context", "loop", "rewind", "export", "vim", "terminal-setup", "skill-doctor", "code-review", "ultrareview", "plugin", "add-dir", "release-notes", "upgrade", "privacy-settings", "stats", "usage", "artifacts"}
MIN_SESSIONS = 3
REPEAT_SHARE = 0.6  # a hook whose most common text is this share of its fires says the same thing every prompt


def tail(name):
    return name.split(":")[-1]


def tokens(chars):
    return int(chars / 4)


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def skill_keys():
    """Every override key Claude Code would accept for a user or plugin skill: bare for ~/.claude/skills, plugin:skill for plugins."""
    keys = {}
    for path in USER_SKILLS.glob("*/SKILL.md"):
        keys[path.parent.name] = path.parent.name
    for path in PLUGIN_CACHE.glob("*/*/*/skills/*/SKILL.md"):
        plugin, skill = path.parts[-5], path.parent.name
        keys[f"{plugin}:{skill}"] = skill
    return keys


def agent_names():
    out = {}
    for path in AGENTS_DIR.glob("*.md"):
        m = re.search(r"^name:\s*(.+)$", path.read_text(errors="replace"), re.M)
        out[(m.group(1).strip() if m else path.stem)] = path
    return out


def hook_commands(settings):
    out = []
    for event, matchers in (settings.get("hooks") or {}).items():
        for matcher in matchers or []:
            for h in matcher.get("hooks") or []:
                if h.get("type", "command") == "command" and h.get("command"):
                    out.append((event, h["command"]))
    return out


def hook_output_text(a):
    raw = a.get("stdout") or ""
    try:
        parsed = json.loads(raw)
        return str(((parsed or {}).get("hookSpecificOutput") or {}).get("additionalContext") or raw)
    except ValueError:
        return raw


def scan(days):
    """Per project: cwd, sessions, what was used and how big each offered piece was in the latest session; hooks by command."""
    main, _ = transcript_files(days)
    projects = defaultdict(lambda: {"cwd": None, "sessions": 0, "skills": Counter(), "mcp": Counter(), "agents": Counter(),
                                    "offered_skills": {}, "offered_mcp": {}, "offered_mcp_instr": {}, "offered_agents": {}, "latest": 0.0})
    hooks = defaultdict(lambda: {"fires": 0, "sessions": set(), "texts": Counter(), "chars": 0, "commands": Counter()})
    for path in main:
        p = projects[project_of(path)]
        p["sessions"] += 1
        started = os.path.getmtime(path)
        latest = started > p["latest"]
        if latest:
            p["latest"] = started
        session = Path(path).stem
        for entry in lens.iter_entries(path):
            if p["cwd"] is None and entry.get("cwd"):
                p["cwd"] = entry["cwd"]
            message = entry.get("message") or {}
            content = message.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
            if entry.get("type") == "assistant":
                for b in blocks:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = str(b.get("name", "")), b.get("input") or {}
                    if name == "Skill" and inp.get("skill"):
                        p["skills"][tail(str(inp["skill"]))] += 1
                    elif name.startswith("mcp__"):
                        p["mcp"][name.split("__")[1]] += 1
                    elif name in lens.AGENT_TOOLS and inp.get("subagent_type"):
                        p["agents"][str(inp["subagent_type"])] += 1
            elif entry.get("type") == "user":
                for b in blocks:
                    text = b.get("text") or "" if isinstance(b, dict) else ""
                    for m in re.finditer(r"<command-name>/?([\w:-]+)</command-name>", text):
                        if m.group(1) not in BUILTIN_COMMANDS:
                            p["skills"][tail(m.group(1))] += 1
            elif entry.get("type") == "attachment":
                a = entry.get("attachment") or {}
                kind = a.get("type")
                if kind == "hook_success" and a.get("command") and a.get("hookEvent") == "UserPromptSubmit":
                    text = hook_output_text(a)
                    if text.strip():
                        hooks[lens.hook_label({"hookName": "UserPromptSubmit", "content": text})]["commands"][a["command"]] += 1
                elif kind in lens.HOOK_TYPES and a.get("hookEvent") == "UserPromptSubmit":
                    text = lens.hook_text(a)
                    h = hooks[lens.hook_label(a)]
                    h["fires"] += 1
                    h["sessions"].add(session)
                    h["texts"][text.strip()[:400]] += 1
                    h["chars"] += len(text)
                elif latest and kind == "skill_listing":
                    for line in str(a.get("content") or "").splitlines():
                        m = re.match(r"-\s*([\w:-]+):", line)
                        if m:
                            p["offered_skills"][tail(m.group(1))] = len(line)
                elif latest and kind == "deferred_tools_delta":
                    for d in a.get("surfacedDefinitions") or []:
                        name = str((d or {}).get("name", ""))
                        if name.startswith("mcp__"):
                            server = name.split("__")[1]
                            p["offered_mcp"][server] = p["offered_mcp"].get(server, 0) + len(str(d.get("listing") or ""))
                    for line in a.get("addedLines") or []:
                        if str(line).startswith("mcp__"):
                            server = str(line).split("__")[1]
                            p["offered_mcp"][server] = p["offered_mcp"].get(server, 0) + len(str(line))
                elif latest and kind == "mcp_instructions_delta":
                    for block in a.get("addedBlocks") or []:
                        m = re.match(r"##\s*(.+)", str(block))
                        if m:
                            p["offered_mcp_instr"][m.group(1).strip()] = len(str(block))
                elif latest and kind == "agent_listing_delta":
                    for line in a.get("addedLines") or []:
                        m = re.match(r"-\s*([\w-]+):", str(line))
                        if m:
                            p["offered_agents"][m.group(1)] = len(str(line))
    return projects, hooks


def used_in(projects, field, name):
    return [key for key, p in projects.items() if p[field].get(name)]


def main(argv):
    days = int(argv[0]) if argv[:1] and argv[0].isdigit() else 30
    settings = load_json(SETTINGS, {})
    claude_json = load_json(CLAUDE_JSON, {})
    projects, hooks = scan(days)
    real = {k: p for k, p in projects.items() if p["cwd"] and not re.match(r"^-tmp(-|$)", k) and p["sessions"] >= 1}
    overrides = settings.get("skillOverrides") or {}
    keys = skill_keys()
    lines, script = [], ["#!/bin/bash", "# Written by /kiasi:floor on %s from the last %d days of sessions. Read it, then run: bash %s" % (time.strftime("%Y-%m-%d %H:%M"), days, FIX_FILE),
                        "set -e", "STAMP=$(date +%Y%m%d-%H%M%S)", "back() { [ -f \"$1\" ] && cp \"$1\" \"$1.bak-$STAMP\" && echo \"backup $1.bak-$STAMP\"; true; }", ""]
    lines.append(f"Startup floor audit, last {days} days, {len(real)} projects with sessions")
    for key, p in sorted(real.items(), key=lambda kv: -kv[1]["sessions"]):
        lines.append(f"  {p['cwd']}: {p['sessions']} sessions, used {len(p['skills'])} skills, {len(p['mcp'])} MCP servers, {len(p['agents'])} agents")

    # skills: never used anywhere -> name-only; used in one project -> off globally, on there
    name_only, per_project_on = {}, defaultdict(dict)
    offered = Counter()
    for p in real.values():
        for skill in p["offered_skills"]:
            offered[skill] += 1
    for key, skill in sorted(keys.items()):
        if key in overrides or skill in (tail(k) for k in overrides):
            continue
        users = used_in(real, "skills", skill)
        size = max((p["offered_skills"].get(skill, 0) for p in real.values()), default=0)
        if not users and offered.get(skill):
            name_only[key] = size
        elif len(users) == 1 and len(real) > 1 and offered.get(skill):
            name_only[key] = ("off", size)
            per_project_on[real[users[0]]["cwd"]][key] = "on"
    if name_only:
        lines.append("")
        lines.append("Skills (the listing is part of every session's floor)")
        for key, v in sorted(name_only.items(), key=lambda kv: -(kv[1][1] if isinstance(kv[1], tuple) else kv[1])):
            if isinstance(v, tuple):
                where = next(cwd for cwd, d in per_project_on.items() if key in d)
                lines.append(f"  {key}: used only in {where}; off globally, on there (about {tokens(v[1])} tokens off every other project's floor)")
            else:
                lines.append(f"  {key}: offered but never invoked; name-only keeps it callable (about {tokens(v)} tokens off the floor)")
        updates = {k: (v[0] if isinstance(v, tuple) else "name-only") for k, v in name_only.items()}
        script += ["# Skills", f"back {shlex.quote(str(SETTINGS))}",
                   "python3 - <<'PY'", "import json", f"p={str(SETTINGS)!r}", "s=json.load(open(p))", f"s.setdefault('skillOverrides',{{}}).update({json.dumps(updates)})",
                   "json.dump(s,open(p,'w'),indent=2)", "PY"]
        for cwd, d in per_project_on.items():
            proj = Path(cwd) / ".claude" / "settings.json"
            script += [f"mkdir -p {shlex.quote(str(proj.parent))}", f"back {shlex.quote(str(proj))}",
                       "python3 - <<'PY'", "import json,os", f"p={str(proj)!r}", "s=json.load(open(p)) if os.path.exists(p) else {}",
                       f"s.setdefault('skillOverrides',{{}}).update({json.dumps(d)})", "json.dump(s,open(p,'w'),indent=2)", "PY"]
        script.append("")

    # MCP servers at user scope used by some projects only
    user_mcp = claude_json.get("mcpServers") or {}
    moves = []
    for name, conf in user_mcp.items():
        users = used_in(real, "mcp", name)
        if users and len(users) < len(real):
            size = max((p["offered_mcp"].get(name, 0) + p["offered_mcp_instr"].get(name, 0) for p in real.values()), default=0)
            moves.append((name, conf, [real[u]["cwd"] for u in users], size))
    if moves:
        lines.append("")
        lines.append("MCP servers (user scope loads their tool schemas and instructions into every project)")
        script.append("# MCP servers")
        for name, conf, cwds, size in sorted(moves, key=lambda m: -m[3]):
            lines.append(f"  {name}: used in {len(cwds)} of {len(real)} projects; move to project scope there (about {tokens(size)} tokens off the others)")
            script.append(f"claude mcp remove {shlex.quote(name)} -s user || true")
            for cwd in cwds:
                if conf.get("type") in ("http", "sse") and conf.get("url"):
                    args = f"--transport {conf['type']} {shlex.quote(name)} {shlex.quote(conf['url'])}"
                    args += "".join(f" --header {shlex.quote(f'{k}: {v}')}" for k, v in (conf.get("headers") or {}).items())
                else:
                    args = "".join(f"-e {shlex.quote(f'{k}={v}')} " for k, v in (conf.get("env") or {}).items())
                    args += f"{shlex.quote(name)} -- {shlex.quote(str(conf.get('command', '')))} " + " ".join(shlex.quote(str(a)) for a in conf.get("args") or [])
                script.append(f"(cd {shlex.quote(cwd)} && claude mcp add --scope project {args})")
        script.append("")

    # global agents used by one project only
    agent_moves = []
    for name, path in agent_names().items():
        users = used_in(real, "agents", name)
        if len(users) == 1 and len(real) > 1:
            size = max((p["offered_agents"].get(name, 0) for p in real.values()), default=0)
            agent_moves.append((name, path, real[users[0]]["cwd"], size))
    if agent_moves:
        lines.append("")
        lines.append("Agents (~/.claude/agents is listed in every session)")
        script.append("# Agents")
        for name, path, cwd, size in agent_moves:
            lines.append(f"  {name}: used only in {cwd}; move it there (about {tokens(size)} tokens off the others)")
            script += [f"mkdir -p {shlex.quote(cwd + '/.claude/agents')}", f"mv {shlex.quote(str(path))} {shlex.quote(cwd + '/.claude/agents/')}"]
        script.append("")

    # hooks that say the same thing on every prompt
    repeats = []
    user_hooks = [c for e, c in hook_commands(settings) if e == "UserPromptSubmit" and "once.py" not in c]
    for label, h in hooks.items():
        if h["fires"] < MIN_SESSIONS or not h["texts"]:
            continue
        top_text, top_count = h["texts"].most_common(1)[0]
        if top_text and top_count / h["fires"] >= REPEAT_SHARE and top_count >= 2 * len(h["sessions"]):
            command = next((c for c, _ in h["commands"].most_common() if c in user_hooks), None)
            repeats.append((label, command, h, top_count))
    if repeats:
        lines.append("")
        lines.append("Hooks (a UserPromptSubmit hook's text stays in the context for the rest of the session, so repeating it adds a copy per prompt)")
        wraps = []
        for label, command, h, top_count in repeats:
            per = h["chars"] / h["fires"]
            others = ", ".join(user_hooks) or "none found"
            fix = f"its command in settings.json is {command}; the script wraps it" if command else f"wrap its command with once.py (the UserPromptSubmit commands in settings.json: {others})"
            lines.append(f"  {label}: {h['fires']} fires in {len(h['sessions'])} sessions, the same text {top_count} times, about {tokens(per)} tokens each; {fix}")
            if command:
                wraps.append((command, f"sh {shlex.quote(str(ONCE.parent / 'run.sh'))} once.py -- {command}"))
        if wraps:
            script += ["# Hooks", f"back {shlex.quote(str(SETTINGS))}", "python3 - <<'PY'", "import json", f"p={str(SETTINGS)!r}", "s=json.load(open(p))"]
            for command, wrapped in wraps:
                script.append(f"for m in s.get('hooks',{{}}).get('UserPromptSubmit',[]):\n    for h in m.get('hooks',[]):\n        if h.get('command')=={command!r}: h['command']={wrapped!r}")
            script += ["json.dump(s,open(p,'w'),indent=2)", "PY", ""]

    if len(script) <= 6:
        lines.append("")
        lines.append("Nothing to move: every skill, MCP server and agent offered was used in every project, and no hook repeats itself.")
    else:
        script.append("echo 'done: start a new session and open the Startup floor panel on the dashboard'")
        FIX_FILE.parent.mkdir(parents=True, exist_ok=True)
        FIX_FILE.write_text("\n".join(script) + "\n")
        lines.append("")
        lines.append(f"Fix script written (nothing run): {FIX_FILE}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
