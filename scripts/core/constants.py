import getpass
import os
import tempfile
from pathlib import Path

# PLUGIN_ROOT is the installed plugin's code directory (read-only, changes on update).
# Use it only for bundled files such as rules.md, never for state.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]

# DATA_DIR is where all state (events log, sessions, pastes, outputs, notes,
# checkpoints, reports, sync status, search index) is written. Claude Code
# exports CLAUDE_PLUGIN_DATA to hook processes. The SessionStart hook records
# that path in DATA_DIR_POINTER so scripts run by hand (dashboard, sync,
# search) read and write the same directory; before the first session, or
# if the pointer is stale, they fall back to ~/.claude/kiasi.
PLUGIN_NAME = "kiasi"
HOME_DIR = Path.home() / ".claude" / PLUGIN_NAME
DATA_DIR_POINTER = HOME_DIR / "data-dir"
PLUGIN_ROOT_POINTER = HOME_DIR / "plugin-root"
DASHBOARD_LAUNCHER = HOME_DIR / "dashboard-launcher.py"
AUTOSTART_NAME = "kiasi-dashboard"

# The plugin was called Ankush until 2026-09-29. The SessionStart hook moves
# the old data and home directories to the new names and leaves a link at the
# old path, so markers already written into transcripts still resolve.
LEGACY_NAME = "ankush"
LEGACY_HOME_DIR = Path.home() / ".claude" / LEGACY_NAME
LEGACY_EVENT_LOG_NAME = "ankush.jsonl"


def resolve_data_dir():
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    if env:
        return Path(env)
    for pointer in (DATA_DIR_POINTER, LEGACY_HOME_DIR / "data-dir"):
        try:
            pointed = Path(pointer.read_text().strip())
        except OSError:
            continue
        if pointed.is_dir():
            return pointed
    return HOME_DIR


DATA_DIR = resolve_data_dir()

ROOT = PLUGIN_ROOT  # kept for readability at call sites that mean "plugin code"
LOG_DIR = DATA_DIR
EVENT_LOG = LOG_DIR / "kiasi.jsonl"
SESSION_DIR = LOG_DIR / "sessions"
# A hook holds its session's lock file in SESSION_DIR while it runs; it waits this long for it, then goes ahead without it.
SESSION_LOCK_WAIT_SECONDS = 3
PASTE_DIR = LOG_DIR / "pastes"
OUTPUT_DIR = LOG_DIR / "outputs"
NOTES_DIR = LOG_DIR / "notes"
CHECKPOINT_DIR = LOG_DIR / "checkpoints"
# Where a checklist goes when there is no writable project folder: Claude Code accepts a Write under the system temp folder.
def _user_token():
    try:
        name = getpass.getuser()
    except Exception:
        name = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "user"


# Per user: the system temp folder is shared, so the folder is named for its owner and made private (0o700).
TEMP_CHECKLIST_DIR = Path(tempfile.gettempdir()) / f"kiasi-{_user_token()}" / "checkpoints"
# Turn checklists go in the project, in a folder git ignores: Claude Code refuses Claude's writes anywhere under
# ~/.claude, this data folder included, as edits to a sensitive file. TEMP_CHECKLIST_DIR is for a session without one.
PROJECT_CHECKLIST_DIR = Path(".kiasi") / "checkpoints"
BUDGET_FILE = LOG_DIR / "budget.json"
# Every per-day row budget.py ever built, merged on each build so a day outlives the report window and
# Claude Code's own transcript retention; the Overview before-and-after block is built from it.
HISTORY_FILE = LOG_DIR / "history.json"
# What Kiasi avoided per day, merged on each lens.py build the same way; the Overview's all-time avoided figure is its sum.
SAVINGS_FILE = LOG_DIR / "savings.json"
TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"

TRANSCRIPT_TAIL_BYTES = 400_000
CHARS_PER_TOKEN = 4

CONTEXT_WARN_TOKENS = 200_000
CONTEXT_HARD_TOKENS = 300_000
CONTEXT_NUDGE_STEP_TOKENS = 50_000
NEW_TASK_GAP_MINUTES = 45
TASK_MIN_CHARS = 25
SYSTEM_PROMPT_PATTERN = r"^\s*<(task-notification|system-reminder|local-command-|command-name|pasted_content|agent-message)"

REREAD_ASK_TOKENS = 150_000
REREAD_ASK_STEP_TOKENS = 50_000
REREAD_RECENT_PROMPTS = 5
REREAD_MIN_STEPS = 6
REREAD_SUBAGENT_MEAN_TOKENS = 90_000
REREAD_MAIN_TURNS_WHEN_DELEGATED = 2
REREAD_DELEGATE_RATIO = 0.6
QUESTION_PATTERN = r"^(what|why|how|is|are|does|do|can|could|should|would|which|where|when|who)\b.{0,200}\??$"
ACK_PATTERN = r"^(yes|no|ok|okay|continue|retry|proceed|go ahead|do it|next|done|thanks)\b.{0,40}$"

PASTE_MIN_CHARS = 4_000
PASTE_HEAD_CHARS = 300
PASTE_BLOCK_CHARS = 40_000

# What the turn and subagent budgets do: pause (refuse further calls and save the rest to resume), warn (say so, refuse
# nothing) or off (only count the calls, for the reports). turn_budget_mode sets it; any other word means pause.
TURN_BUDGET_MODES = ("pause", "warn", "off")
TURN_BUDGET_MODE = "pause"
TURN_STOP_STEPS = 60
TURN_STOP_TOKENS = 8_000_000
# The warning comes TURN_WARN_MARGIN calls, or a fifth of the tokens, before the pause: late enough to leave the turn its
# working room, early enough to finish the item in progress and save the rest. turn_warn_steps and turn_warn_tokens in
# .kiasi.json set them.
TURN_WARN_MARGIN = 10
TURN_WARN_STEPS = None
TURN_WARN_TOKENS = None
TURN_REMIND_STEPS = 5
# Calls refused after a pause before the turn is ended outright, so a model that ignores the refusals stops re-reading.
TURN_DENY_BACKSTOP = 3
# Refusals this close together count as one response when the transcript names none: parallel calls arrive at once.
REFUSAL_RESPONSE_SECONDS = 3
# A desktop notification at a pause: auto (only in the apps below, whose chat raises none; the terminal gets
# pause_alert's), always or off. pause_notification sets it; any other word leaves it as it is.
PAUSE_NOTIFICATION_MODES = ("auto", "always", "off")
PAUSE_NOTIFICATION = "auto"
# CLAUDE_CODE_ENTRYPOINT in the VS Code extension and in the desktop app.
PAUSE_NOTIFICATION_APPS = ("claude-vscode", "claude-desktop")
# At a pause in the developer's own turn Claude asks how to go on, with the three options below: auto (only where a
# question can be shown: CLAUDE_CODE_ENTRYPOINT in the terminal, the VS Code extension and the desktop app), always or
# off. pause_question sets it; any other word leaves it as it is.
PAUSE_QUESTION_MODES = ("auto", "always", "off")
PAUSE_QUESTION = "auto"
PAUSE_QUESTION_APPS = ("cli", "claude-vscode", "claude-desktop")
PAUSE_QUESTION_HEADER = "Kiasi pause"
# The option labels are fixed: pause_answer reads the developer's choice by them.
PAUSE_CHOICES = {"continue": "Continue here", "subagent": "Hand to a subagent", "stop": "Stop here"}
NOTIFY_TITLE_ENV = "KIASI_NOTIFY_TITLE"
NOTIFY_BODY_ENV = "KIASI_NOTIFY_BODY"
TURN_EXEMPT_TOOLS = {"Agent", "Write", "AskUserQuestion", "TodoWrite", "TaskCreate", "TaskUpdate"}
# At a pause these pass only for the turn's own checklist, so it is brought up to date instead of written blind.
TURN_CHECKLIST_TOOLS = {"Read", "Edit", "MultiEdit"}
# The first prompt after a pause resumes it from the checklist when it is a plain "continue" (or resume, go on, go
# ahead, carry on, keep going, proceed); any other prompt is only told where the checklist is.
RESUME_PATTERN = r"^((ok|okay|yes|sure|please)[\s,.!]+)*(continue|resume|go on|go ahead|carry on|keep going|proceed)\b.{0,80}$"
RESUME_CHECKLIST_CHARS = 2000
RESUME_TASK_CHARS = 300
# A subagent's own turn budget: stated in its brief, warned TURN_WARN_MARGIN calls before the limit, paused at it.
SUBAGENT_STEP_LIMIT = 40
# The brief's budget sentence, left out when the budget is off.
SUBAGENT_BRIEF_BUDGET = "Kiasi budget: finish within {steps} steps; the tool calls of one response are one step, so parallel calls count once. "
SUBAGENT_BRIEF_SUFFIX = (
    "Batch shell commands, run each test suite once per round, never poll with sleep. "
    "When done or blocked, reply with at most 300 words: what changed (paths), what was verified, what remains."
)
BUDGET_FRESH_HOURS = 48

# Delegation brief: the ready Agent prompt handed to Claude when the re-read check or a pause points at a subagent.
DELEGATION_GOAL_CHARS = 300
DELEGATION_PROMPT_CHARS = 2000
DELEGATION_BRIEF = (
    "Task file: {path}. Read it first and keep it updated. Goal: {goal}. Working directory: {cwd}. "
    "Budget: {steps} steps; batch commands; run each test suite once. Do not commit. "
    "Report in under 300 words: what changed, what was verified, what is left."
)
DELEGATION_BRIEF_LEAD = "Ready brief, paste as the Agent prompt (subagent_type general-purpose, never fork):\n"

AGENT_TOOL = "Agent"
READ_TOOL = "Read"
BASH_TOOL = "Bash"
WEB_FETCH_TOOL = "WebFetch"
PRE_TOOL_HOOKED = (AGENT_TOOL, READ_TOOL, BASH_TOOL, WEB_FETCH_TOOL)
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
READ_SKIP_MIN_CHARS = 2000
READ_SKIP_REASON = ("kiasi: {path}{span} is unchanged since you read it earlier in this conversation, so its text is already in your context. "
                    "Use that copy. If it is no longer in your context, repeat the same Read and it will go through.")
READ_NUDGE_CHARS = 30_000
READ_NUDGE_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ipynb"}
READ_NUDGE_REASON = ("kiasi: {path} is {chars} chars and this Read has no offset or limit. Read only the section you need "
                     "(offset and limit around the lines you want, from a search hit or a grep), or use mcp__kiasi__distill to derive "
                     "the answer without reading it. If you truly need the whole file, repeat the same Read and it will go through.")
AGENT_DEFAULT_MODEL = {
    "Explore": "haiku",
    "Plan": "sonnet",
    "general-purpose": "sonnet",
    "code-reviewer": "sonnet",
    "typescript-reviewer": "sonnet",
    "adonisjs-reviewer": "sonnet",
    "security-reviewer": "sonnet",
    "silent-failure-hunter": "sonnet",
    "performance-optimizer": "sonnet",
    "tdd-guide": "sonnet",
    "claude-code-guide": "haiku",
}
AGENT_FALLBACK_MODEL = "sonnet"
AGENT_PROMPT_MAX_CHARS = 6_000
AGENT_REPEAT_REVIEW_PATTERN = r"(?i)\b(round\s*\d+|confirmation\s+review|re-?review|review\s+again|final\s+check|one\s+more\s+pass)\b"

WEB_TOOLS = {"WebFetch", "WebSearch"}
CAP_WEB_CHARS = 8_000
CAP_WEB_HEAD_CHARS = 6_000
CAP_OUTSIDE_READ_CHARS = 12_000
CAP_OUTSIDE_READ_HEAD_CHARS = 8_000
OUTSIDE_READ_PATTERN = r"([\\/]\.claude[\\/]projects[\\/]|^/tmp/|[\\/]AppData[\\/]Local[\\/]Temp[\\/]|\.(log|jsonl)$)"
CAP_MCP_CHARS = 8_000
CAP_MCP_HEAD_CHARS = 6_000
MCP_CAP_EXEMPT_PREFIX = "mcp__kiasi__"
CAP_BASH_CHARS = 12_000
CAP_BASH_HEAD_CHARS = 6_000
CAP_BULK_CHARS = 6_000
BULK_HEAD_LINES = 40
BULK_TAIL_LINES = 20
BULK_ERROR_LINES = 30
TEST_COMMAND_PATTERN = r"\b(pytest|jest|vitest|mocha|phpunit|pest|go\s+test|cargo\s+test|unittest|bun\s+test|(npm|pnpm|yarn)\s+(run\s+)?test|node\s+ace\s+test|tsc|eslint|ruff|mypy|phpstan)\b"
FAIL_LINE_PATTERN = r"(?i)(^\s*(fail|failed|not ok|✗|✕|×)\b|\bassert\w*|\bexpected\b.*\b(got|received|to\s+(be|equal))\b|\b\d+\s+(failed|failing)\b)"
FAIL_HEAD_LINES = 5
FAIL_CONTEXT_LINES = 8
FAIL_MAX_LINES = 160
ERROR_LINE_PATTERN = r"(?im)^(.*\b(traceback|\w*error|exception|fatal|failed|fail:|panic|exit code [1-9]|command not found|permission denied)\b.*)$"
CLEAN_MIN_CHARS = 2_000
CLEAN_MIN_SAVED_CHARS = 400
CLEAN_REPEAT_MIN = 3
ANSI_PATTERN = r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07"
LOOP_SAME_FAILS = 3
LOOP_LEAD_FAILS = 5
LOOP_LABEL_CHARS = 120
LOOP_REASON = ("kiasi loop check: {label} has failed {count} times in this turn. Stop retrying variations. Read the last error in full, "
               "check the assumption behind the command (path, cwd, version, permissions), and if the cause is still unclear, ask the user instead of trying again.")
STATE_MAX_FILES = 12
STATE_MAX_FAILURES = 5
STATE_MAX_OUTPUTS = 5
STATE_MAX_CHECKLISTS = 3
STATE_TASK_CHARS = 400
SELECTOR_COMMANDS = {"sed", "head", "tail", "cat", "awk", "less", "more", "git show", "git diff", "git blame"}
BULK_PATTERNS = [
    r"\b(pnpm|npm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck|check)\b",
    r"\bnode\s+ace\s+test\b",
    r"\b(pytest|jest|vitest|mocha|japa|phpunit|go\s+test|cargo\s+test)\b",
    r"\b(pnpm|npm|yarn|bun|pip|pip3|apt|apt-get|brew|composer)\s+(i|install|add|ci|update|upgrade)\b",
    r"\b(tsc|eslint|prettier|ruff|flake8|mypy|pylint|phpstan)\b",
    r"\b(docker|docker-compose|kubectl|terraform)\b",
    r"\b(webpack|vite|esbuild|rollup|next)\s+build\b",
    r"\bls\b[^|]*\s-[a-zA-Z]*R",
    r"\bfind\s",
    r"\btree\b",
    r"\bgit\s+log\b",
    r"\bgrep\s+-[a-zA-Z]*r",
    r"\brg\s",
    r"\bcurl\s",
]

COMPACT_FAILING_COMMANDS = 5
PLUGIN_QUIET_FIELDS = ("session_id", "before", "after", "applied")
PLUGIN_COMPACT_FIELDS = ("session_id", "trigger", "agent_id", "mode", "level", "messages", "tokens_before", "tokens_after", "archived")
COMPACT_EDITED_FILES = 20
NOTE_MAX_AGE_DAYS = 14
# save_session retries the rename this often, this long apart: on Windows a reader holding the state file blocks it.
SAVE_REPLACE_TRIES = 5
SAVE_REPLACE_DELAY = 0.02
SAVE_TMP_STALE_SECONDS = 60  # a temp file this old is a crashed write's, not a running hook's
# Call ids a turn keeps, to tell a PostToolBatch of a cleared turn from one of the current turn.
TURN_CALLS_KEPT = 200
NOTE_MIN_INTERVAL_MINUTES = 10
NOTE_MESSAGE_HEAD_CHARS = 400

BUDGET_DAYS = 7  # report window in days for sync, dashboard rebuilds and the hand-run report scripts
BUDGET_TOP_SESSIONS = 8
BUDGET_TOP_OUTPUTS = 8
BUDGET_BIG_OUTPUT_CHARS = 20_000
BUDGET_HIGH_CONTEXT_TOKENS = 200_000

# Sandbox tools (mcp__kiasi__run, mcp__kiasi__distill, mcp__kiasi__fetch → scripts/sandbox.py):
# the full output never enters the conversation; it is saved under outputs/ and only a digest returns.
SANDBOX_TIMEOUT_SECONDS = 120
SANDBOX_TIMEOUT_MAX_SECONDS = 600
RUN_HEAD_LINES = 15
RUN_TAIL_LINES = 10
RUN_ERROR_LINES = 20
RUN_LINE_CHARS = 200
DISTILL_RESULT_CHARS = 4_000
DISTILL_ERROR_CHARS = 2_000
FETCH_MAX_BYTES = 5_000_000
FETCH_HEAD_CHARS = 3_000
FETCH_FIND_LINES = 40
FETCH_USER_AGENT = "Mozilla/5.0 (compatible; kiasi-fetch)"
FETCH_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer",
                    "pre", "blockquote", "table", "ul", "ol", "dd", "dt", "hr", "nav", "main", "aside", "option", "title"}
FETCH_SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "template"}

# Sandbox routing (PreToolUse on Bash and WebFetch): while the sandbox tools are registered
# (CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1), a raw call whose output would only be scanned is
# denied once with the sandbox call to make instead; the identical call repeated goes through.
ROUTE_ENV_FLAG = "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"
RUN_TOOL_NAME = "mcp__kiasi__run"
FETCH_TOOL_NAME = "mcp__kiasi__fetch"
ROUTE_LABEL_CHARS = 200
ROUTE_BASH_PATTERNS = [
    r"\b(pnpm|npm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck|check)\b",
    r"\bnode\s+ace\s+test\b",
    r"\b(pytest|jest|vitest|mocha|japa|phpunit|go\s+test|cargo\s+(test|build)|python3?\s+-m\s+unittest)\b",
    r"\b(pnpm|npm|yarn|bun|pip|pip3|apt|apt-get|brew|composer)\s+(i|install|add|ci|update|upgrade)\b",
    r"\b(tsc|eslint|prettier|ruff|flake8|mypy|pylint|phpstan)\b",
    r"\b(docker|docker-compose|kubectl)\s+(logs|build|pull|compose\s+(up|build|logs|pull))\b",
    r"\b(webpack|vite|esbuild|rollup|next)\s+build\b",
    r"\b(curl|wget)\s",
    r"\bgit\s+log\b",
    r"\btail\s+(-n\s*|-)\d{3,}\b",
]
# A scan command that already limits its own output stays raw: piped into head/tail/wc/jq,
# a small numeric limit (git log -5), --oneline, or stdout sent to a file.
ROUTE_LIMITED_PATTERN = (r"\|\s*(head|tail|wc|jq|grep\s+-c)\b|(^|\s)(-n\s*|--max-count[= ]|-)\d{1,2}(\s|$)|--oneline"
                         r"|>\s*/dev/null|\s-o\s+\S+|\s>>?\s*\S+")
ROUTE_BASH_REASON = ("kiasi: the output of `{command}` would only be scanned. Run it through mcp__kiasi__run with the same command: "
                     "the full output is saved and searchable with mcp__kiasi__search, and only a digest (exit code, head, error lines, tail) "
                     "enters the conversation. If you need the exact full output here, repeat the same Bash call and it will go through.")
ROUTE_WEB_REASON = ("kiasi: fetch {url} with mcp__kiasi__fetch instead (url, plus `find` words to pull the matching lines): "
                    "the page text is saved and searchable with mcp__kiasi__search, and only its head and the hit lines enter the conversation. "
                    "If you need WebFetch's summarised answer, repeat the same WebFetch call and it will go through.")

SEARCH_DB = LOG_DIR / "search.db"
SEARCH_DIRS = (OUTPUT_DIR, PASTE_DIR, NOTES_DIR, CHECKPOINT_DIR, TEMP_CHECKLIST_DIR)
SEARCH_SUFFIXES = {".txt", ".md", ".jsonl", ".json"}
SEARCH_MAX_FILE_CHARS = 1_000_000
SEARCH_RESULTS = 8
SEARCH_SNIPPET_TOKENS = 40
SEARCH_WORD_ENDINGS = r"(?:s|es|ed|ing|er)?"
SEARCH_AVG_CHARS = 20_000
SEARCH_HIT_LINES = 5

LENS_FILE = LOG_DIR / "lens.json.gz"  # gzipped; the dashboard server sends it as is with Content-Encoding: gzip
LENS_MAX_ACTIONS = 2000
LENS_MAX_SESSIONS = 60
LENS_SERIES_POINTS = 240
LENS_COMPACT_JOIN_SECONDS = 180
LENS_STEP_BUCKETS = ((1, 1), (2, 5), (6, 10), (11, 20), (21, 29), (30, 59), (60, None))
LENS_CUT_BUCKETS = ((0, 2000), (2000, 5000), (5000, 10000), (10000, 20000), (20000, None))
LENS_COMPLY_STEPS = 10
LENS_FACTOR_MIN_STEPS = 300  # steps needed before and after the install before the per-step factor is shown
LENS_CACHE_MISS_TOKENS = 20000
LENS_CACHE_IDLE_SHORT = 300
LENS_CACHE_IDLE_LONG = 3600
LENS_HIT_TARGET = 0.7  # share of input read from cache; 70%+ is the usual bar for a stable prompt prefix
# Anthropic prompt-cache prices relative to base input (5-minute cache): a write costs 1.25x, a read 0.1x.
# A miss writes what would have been read, so its extra cost in re-read tokens is tokens * (write - read) / read.
CACHE_WRITE_PRICE = 1.25
CACHE_READ_PRICE = 0.1
# Public Claude API prices in dollars per million tokens: input, output, cache write, cache read (reports/lens.py prices the
# re-read bill with them). Keyed by model id prefix; a dated id such as claude-haiku-4-5-20251001 matches its prefix.
# Cache reads are 0.1x input except where the price list says otherwise. From platform.claude.com/docs/en/about-claude/pricing.
MODEL_PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 12.5, 0.25), "claude-mythos-5-1": (10.0, 50.0, 12.5, 0.25),
    "claude-fable-5": (10.0, 50.0, 12.5, 1.0), "claude-mythos-5": (10.0, 50.0, 12.5, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.2), "claude-opus-5": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.5), "claude-opus-4-7": (5.0, 25.0, 6.25, 0.5), "claude-opus-4-6": (5.0, 25.0, 6.25, 0.5),
    "claude-opus-4-5": (5.0, 25.0, 6.25, 0.5), "claude-opus-4-1": (15.0, 75.0, 18.75, 1.5), "claude-opus-4": (15.0, 75.0, 18.75, 1.5),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.2), "claude-sonnet-5": (2.0, 10.0, 2.5, 0.2),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.3), "claude-sonnet-4-5": (3.0, 15.0, 3.75, 0.3), "claude-sonnet-4": (3.0, 15.0, 3.75, 0.3),
    "claude-haiku-5-5": (0.1, 0.5, 0.125, 0.01), "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.1), "claude-haiku-3-5": (0.8, 4.0, 1.0, 0.08),
}
# Files whose change rewrites the start of the prompt and so empties the cache. UserPromptSubmit stats them
# (mtime and size, no reads) and logs which group changed since the session's previous prompt.
CONFIG_FINGERPRINT = {
    "settings": ("~/.claude/settings.json", "{cwd}/.claude/settings.json", "{cwd}/.claude/settings.local.json"),
    "CLAUDE.md": ("~/.claude/CLAUDE.md", "{cwd}/CLAUDE.md", "{cwd}/.claude/CLAUDE.md", "{cwd}/CLAUDE.local.md"),
    "plugins": ("~/.claude/plugins/installed_plugins.json", "{plugin}/hooks/*", "{plugin}/.claude-plugin/plugin.json"),
    "skills": ("~/.claude/skills/*/SKILL.md", "{cwd}/.claude/skills/*/SKILL.md"),
    "MCP": ("{cwd}/.mcp.json",),
}
# Folders whose saved files count as read back when a tool call names them (Storage tab).
LENS_READBACK_MARKS = ("outputs/", "pastes/", "checkpoints/")
# Days the Storage tab's growth chart and cleanup calendar cover.
STORAGE_DAYS = 14
# Folders whose files are only worth keeping if something reads them back; notes and sessions are read by Kiasi itself.
STORAGE_READBACK_FOLDERS = ("outputs", "pastes", "checkpoints")

# Retention (cleanup.py). A saved file only matters to the session that made it, so it goes once
# both the file and its session have been unused for CLEANUP_IDLE_DAYS. Files are moved to TRASH_DIR
# first and deleted CLEANUP_TRASH_DAYS later; `cleanup.py --restore` brings them back. For the first
# CLEANUP_REPORT_DAYS nothing moves: the run only reports what it would do.
# KIASI_CLEANUP: auto (default: report, then trash) | report | trash | off.
CLEANUP_MODE = os.environ.get("KIASI_CLEANUP", "auto").strip().lower()
CLEANUP_STATE = LOG_DIR / "cleanup.json"
CLEANUP_MANIFEST = LOG_DIR / "cleanup.jsonl"
CLEANUP_LOCK = LOG_DIR / "cleanup.lock"
TRASH_DIR = LOG_DIR / "trash"
CLEANUP_INTERVAL_HOURS = 24
CLEANUP_REPORT_DAYS = 7
CLEANUP_IDLE_DAYS = 7
CLEANUP_PASTE_IDLE_DAYS = 14
CLEANUP_RECENT_HOURS = 24
CLEANUP_TRASH_DAYS = 7
CLEANUP_NOTE_DAYS = 30
CLEANUP_MAX_BYTES = 50_000_000
CLEANUP_TIMEOUT_SECONDS = 120
CLEANUP_GZIP_LEVEL = 6
CLEANUP_REPORT_FILES = 50
CLEANUP_EVENT_NAME = "cleanup"
STORAGE_LIST_ROWS = 200
STORAGE_HISTORY_ROWS = 100
# Only files whose names Kiasi itself writes are ever touched; anything else in these folders stays.
CLEANUP_PATTERNS = {
    "outputs": r"(toolu_[A-Za-z0-9_-]+|compact-([0-9a-f]{8}|unknown)(-[A-Za-z0-9]+)?-\d{8}-\d{6}(-[0-9a-f]{6})?|unknown-\d{8}-\d{6}(-[0-9a-f]{6})?|(run|distill|fetch)-\d{8}-\d{6}-\d+)\.txt",
    "checkpoints": r"[0-9a-f]{8}-\d+(-\d+)?\.md",
    "pastes": r"[0-9a-f-]{36}-\d+\.txt",
    "sessions": r"[0-9a-f-]{36}\.(json|lock)",
    "notes": r"[A-Za-z0-9-]+\.jsonl",
    "paused": r"[A-Za-z0-9-]+\.paused(\.[A-Za-z0-9_-]+)?\.json",
}

SYNC_REQUEST = LOG_DIR / "sync.request"
SYNC_STATUS = LOG_DIR / "sync.json"
SYNC_SCRIPTS = ("reports/budget.py", "reports/lens.py")

# SessionStart: rules.md shipped inside the plugin, injected as additionalContext.
RULES_FILE = PLUGIN_ROOT / "rules.md"
# rules.md states the default budgets. rules_text renders its lines that start with these prefixes from the templates
# below for the budget settings, so rules.md must stay the pause template with the defaults filled in.
TURN_RULE_PREFIX = "- Every turn has a budget of"
SUBAGENT_RULE_PREFIX = "- Each subagent has its own budget of"
TURN_RULES = {
    "pause": ("- Every turn has a budget of {steps} steps (the tool calls of one response are one step, so parallel calls count once) or {tokens} re-read tokens. At the warning, about {margin} steps before the pause, "
              "finish the item in progress, update the task file at the path kiasi names (goal, decisions, verified checklist, files, next step), then ask the developer the question kiasi gives "
              "(continue here, hand to a subagent, or stop) and do what they choose; never choose for them. At the pause every call except Agent, that question and the task file's own Write, Read and Edit is refused: bring the task file up to date and ask it. "
              "When kiasi gives no question, end the turn or hand the task file to one general-purpose subagent; at a pause, end with its notice, so the developer knows how to resume."),
    "warn": ("- Every turn has a budget of {steps} steps (the tool calls of one response are one step, so parallel calls count once) or {tokens} re-read tokens, which kiasi reports but does not enforce. At the warning, "
             "about {margin} steps before the budget, finish the item in progress, update the task file at the path kiasi names (goal, decisions, verified checklist, files, next step), "
             "and end the turn or hand the task file to one general-purpose subagent."),
}
SUBAGENT_RULES = {
    "pause": ("- Each subagent has its own budget of {subagent_steps} steps, stated in its brief and enforced like the turn budget: at the pause "
              "it updates its task file and replies. Scope review subagents to the diff, never the whole repo."),
    "warn": ("- Each subagent has its own budget of {subagent_steps} steps, stated in its brief and reported like the turn budget. "
             "Scope review subagents to the diff, never the whole repo."),
    "off": "- Scope review subagents to the diff, never the whole repo.",
}
REQUIRED_ENV = {
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
    "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1",
}

# userConfig / env knobs a user may override (see plugin.json userConfig and the README).
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_choice(name, default, choices):
    value = os.environ.get(name, "").strip().lower()
    return value if value in choices else default

CAP_OUTSIDE_READ_CHARS = _env_int("CLAUDE_PLUGIN_OPTION_OUTPUT_CAP_CHARS", CAP_OUTSIDE_READ_CHARS)
SUBAGENT_STEP_LIMIT = _env_int("CLAUDE_PLUGIN_OPTION_SUBAGENT_CALL_BUDGET", SUBAGENT_STEP_LIMIT)
TURN_STOP_STEPS = _env_int("CLAUDE_PLUGIN_OPTION_TURN_CALL_BUDGET", TURN_STOP_STEPS)
TURN_STOP_TOKENS = _env_int("CLAUDE_PLUGIN_OPTION_TURN_TOKEN_BUDGET", TURN_STOP_TOKENS)
TURN_BUDGET_MODE = _env_choice("CLAUDE_PLUGIN_OPTION_TURN_BUDGET_MODE", TURN_BUDGET_MODE, TURN_BUDGET_MODES)
PAUSE_NOTIFICATION = _env_choice("CLAUDE_PLUGIN_OPTION_PAUSE_NOTIFICATION", PAUSE_NOTIFICATION, PAUSE_NOTIFICATION_MODES)
PAUSE_QUESTION = _env_choice("CLAUDE_PLUGIN_OPTION_PAUSE_QUESTION", PAUSE_QUESTION, PAUSE_QUESTION_MODES)
PASTE_BLOCK_CHARS = _env_int("CLAUDE_PLUGIN_OPTION_PASTE_REFUSAL_CHARS", PASTE_BLOCK_CHARS)
# Holdout experiment: the named rule is switched off in sessions whose id hashes odd (core/holdout.py).
HOLDOUT_RULES = ("reread_check", "turn_budget", "context_notices", "output_cap", "sandbox")
HOLDOUT = _env_choice("CLAUDE_PLUGIN_OPTION_HOLDOUT", "", ("",) + HOLDOUT_RULES)
COMPACTION_WINDOW_TEXT = os.environ.get("CLAUDE_PLUGIN_OPTION_COMPACTION_WINDOW_TEXT", "200000")

# Per-project overrides: a .kiasi.json at the project root (the session's cwd) tunes
# caps and budgets for that codebase. It wins over the env knobs above, since it is
# the more specific setting; unknown keys and non-positive values are ignored.
PROJECT_FILE_NAME = ".kiasi.json"
PROJECT_KEYS = {
    "output_cap_chars": "CAP_OUTSIDE_READ_CHARS",
    "mcp_cap_chars": "CAP_MCP_CHARS",
    "bash_cap_chars": "CAP_BASH_CHARS",
    "web_cap_chars": "CAP_WEB_CHARS",
    "read_nudge_chars": "READ_NUDGE_CHARS",
    "paste_refusal_chars": "PASTE_BLOCK_CHARS",
    "turn_call_budget": "TURN_STOP_STEPS",
    "turn_warn_steps": "TURN_WARN_STEPS",
    "turn_token_budget": "TURN_STOP_TOKENS",
    "turn_warn_tokens": "TURN_WARN_TOKENS",
    "subagent_call_budget": "SUBAGENT_STEP_LIMIT",
}
# Settings that take one of a few words rather than a number; any other value is ignored.
PROJECT_CHOICES = {"turn_budget_mode": ("TURN_BUDGET_MODE", TURN_BUDGET_MODES), "pause_notification": ("PAUSE_NOTIFICATION", PAUSE_NOTIFICATION_MODES),
                   "pause_question": ("PAUSE_QUESTION", PAUSE_QUESTION_MODES),
                   "holdout": ("HOLDOUT", HOLDOUT_RULES)}


def warn_steps(stop_steps):
    """The call count of the warning for a budget of stop_steps: TURN_WARN_MARGIN calls before it, never before half way."""
    return max(stop_steps - TURN_WARN_MARGIN, stop_steps // 2)


def turn_warn_steps():
    """The main turn's warning: turn_warn_steps from .kiasi.json when set, else TURN_WARN_MARGIN calls before the pause."""
    return TURN_WARN_STEPS or warn_steps(TURN_STOP_STEPS)


def turn_warn_tokens():
    """The warning's token count: turn_warn_tokens from .kiasi.json when set, else a fifth of the tokens before the pause."""
    return TURN_WARN_TOKENS or TURN_STOP_TOKENS * 4 // 5


def apply_project(cwd):
    """Overlay .kiasi.json from the project root onto this module. Fail-open."""
    applied, bad = {}, []
    try:
        import json as _json
        raw = _json.loads((Path(cwd) / PROJECT_FILE_NAME).read_text(encoding="utf-8"))
        for key, name in PROJECT_KEYS.items():
            value = raw.get(key)
            try:
                if isinstance(value, (int, float)) and int(value) > 0:
                    globals()[name] = int(value)
                    applied[name] = int(value)
            except (OverflowError, ValueError):  # 1e999 or nan: this key keeps its default
                bad.append(key)
        for key, (name, choices) in PROJECT_CHOICES.items():
            value = str(raw.get(key, "")).strip().lower()
            if value in choices:
                globals()[name] = applied[name] = value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if bad:
        report_bad_settings(cwd, bad)
    return applied


def report_bad_settings(cwd, keys):
    """Log each setting of a project that could not be used once, so that a default kept silently can be found. Never raises."""
    try:
        import json as _json
        from core.events import log_error
        seen_file = LOG_DIR / "bad-settings.json"
        try:
            seen = _json.loads(seen_file.read_text())
        except (OSError, ValueError):
            seen = []
        fresh = [f"{cwd}:{key}" for key in keys if f"{cwd}:{key}" not in seen]
        if fresh:
            seen_file.parent.mkdir(parents=True, exist_ok=True)
            seen_file.write_text(_json.dumps(seen[-200:] + fresh))
            log_error("bad_setting", project=cwd, settings=keys)
    except Exception:  # noqa: BLE001  a setting that cannot be reported must not stop the hook
        pass
SYNC_TIMEOUT_SECONDS = 120

# The dashboard answers only requests addressed to the loopback names it listens on,
# so a web page cannot reach it through DNS rebinding, and POST /sync only from its own pages.
DASHBOARD_HOSTS = ("127.0.0.1", "localhost")

# The SessionStart hook makes sure a dashboard server is up (KIASI_DASHBOARD=off
# disables that), started detached so it outlives the Claude Code session that
# started it. dashboard.py records where it listens in DASHBOARD_STATE and
# writes its errors to DASHBOARD_LOG; the hook probes GET /limits on the recorded
# port, then the default port, and starts a server only when neither answers.
DASHBOARD_PORT = 8787
DASHBOARD_PORT_TRIES = 20
DASHBOARD_AUTOSTART = os.environ.get("KIASI_DASHBOARD", "on").strip().lower() not in ("off", "0", "no", "false")
DASHBOARD_STATE = LOG_DIR / "dashboard.json"
DASHBOARD_LOG = LOG_DIR / "dashboard.log"
DASHBOARD_LOG_MAX_BYTES = 200_000
DASHBOARD_SERVER = "kiasi-dashboard"
DASHBOARD_PROBE_SECONDS = 1.0
DASHBOARD_START_WAIT_SECONDS = 3.0
DASHBOARD_REBUILD_SECONDS = 30 * 60
# Recorded in DASHBOARD_STATE and raised when a dashboard that is already running must be replaced after an update. On
# Windows one started before build 2 has no console, so every report rebuild it starts opens an empty window.
DASHBOARD_BUILD = 2

# Plan limits come only from the status line: Claude Code hands it rate_limits
# and the Kiasi wrapper (statusline.py) saves them for the dashboard.
RATE_LIMITS_NAME = "rate-limits.json"
LEGACY_LIMITS_NAME = "limits.json"
LIMIT_WARN_PERCENT = 75
LIMIT_CRITICAL_PERCENT = 90

# Burn-rate forecast: the statusline wrapper appends every changed limit reading to
# limits-history.jsonl; the dashboard projects a run-out time from the points of the
# current window. Under FORECAST_MIN_SPAN_SECONDS of history the forecast stays quiet —
# a projection from less than four hours of a week is noise, not a pace.
LIMITS_HISTORY_NAME = "limits-history.jsonl"
FORECAST_MIN_POINTS = 2
FORECAST_MIN_SPAN_SECONDS = 4 * 3600
LIMIT_GROUP_SPAN = {"session": 5 * 3600, "weekly": 7 * 86400}

# Session postmortem: deterministic findings ranked by token cost, shown in the
# dashboard's session detail. A jump is one step growing the context this much.
PM_JUMP_TOKENS = 25_000
PM_STARTUP_TOKENS = 30_000
PM_BASH_CAPS = 3
PM_MAX_FINDINGS = 5
STATUSLINE_CHAIN_NAME = "statusline-chain.json"
STATUSLINE_SCRIPT_NAME = "statusline.py"
CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
SETTINGS_BACKUP_SUFFIX = ".kiasi-bak"

# Task file and context notices
TASK_SWITCH_OVERLAP = 0.15
TASK_SWITCH_MIN_CHARS = 40
TASK_SWITCH_MIN_WORD = 4
TASK_SWITCH_STOPWORDS = frozenset("""this that with from have then there their about would should could which what when where into over also just
more some than them these those your will been were does done make made need like want please again still very only other""".split())
TASKFILE_PREFIX = "task-"
TASKFILE_START_CHARS = 2000
TASKFILE_GOAL_CHARS = 300
TASKFILE_MAX_FILES = 40
TASKFILE_CONTINUE_LINE = "Continue from here. Re-verify before marking anything done."
CONTEXT_HANDOFF_TOKENS = 150_000
CONTEXT_FLOOR_DEFAULT = 46_000
CONTEXT_IDLE_HANDOFF_TOKENS = 100_000
CACHE_TTL_MINUTES = 60
CACHE_TTL_ENV = "CLAUDE_CODE_PROMPT_CACHE_TTL"
CACHE_TTL_VALUES = {"1h": 60, "5m": 5}
# The SessionStart block: the full text is rules.md, loaded on demand by /kiasi:rules.
SESSION_RULE_LINES = (
    "- One task per session. `/kiasi:handoff` saves the task to its task file under `.kiasi/checkpoints/`, `/clear` starts fresh, and the next session restores it.",
    "- Commands, web pages and big files you only scan go through the `mcp__kiasi__run`, `fetch` and `distill` tools when available; a raw scan-only Bash or WebFetch call is refused once with the call to make.",
    "- A line starting with `[kiasi kept` or `[kiasi trimmed` names a saved file with the full text; read it by path if the cut part matters.",
    "- At /compact keep verbatim the current task, every file path edited, new names and failed commands with their errors; reduce other outputs to one line; end with the next steps.",
    "Load /kiasi:rules for the full rules.",
)
TASKFILE_START_SOURCES = ("startup", "clear", "resume")

# Rule outcomes
LENS_HANDOFF_WINDOW_MINUTES = 60  # a handoff notice is followed by a restored session in the project within this
LENS_NUDGE_PROMPTS = 3  # a compact or clear nudge is followed by a compaction or a new session within this many prompts
LENS_EXPERIMENT_MIN_SESSIONS = 10  # sessions needed on each side of a holdout before the comparison counts
