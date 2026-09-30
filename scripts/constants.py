import os
from pathlib import Path

# PLUGIN_ROOT is the installed plugin's code directory (read-only, changes on update).
# Use it only for bundled files such as rules.md, never for state.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# DATA_DIR is where all state (events log, sessions, pastes, outputs, notes,
# checkpoints, reports, sync status, search index) is written. Claude Code
# exports CLAUDE_PLUGIN_DATA to hook processes. The SessionStart hook records
# that path in DATA_DIR_POINTER so scripts run by hand (dashboard, sync,
# search) read and write the same directory; before the first session, or
# if the pointer is stale, they fall back to ~/.claude/kiasi.
PLUGIN_NAME = "kiasi"
HOME_DIR = Path.home() / ".claude" / PLUGIN_NAME
DATA_DIR_POINTER = HOME_DIR / "data-dir"

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
PASTE_DIR = LOG_DIR / "pastes"
OUTPUT_DIR = LOG_DIR / "outputs"
NOTES_DIR = LOG_DIR / "notes"
CHECKPOINT_DIR = LOG_DIR / "checkpoints"
BUDGET_FILE = LOG_DIR / "budget.json"
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

TURN_WARN_STEPS = 30
TURN_STOP_STEPS = 60
TURN_WARN_TOKENS = 4_000_000
TURN_STOP_TOKENS = 8_000_000
TURN_REMIND_STEPS = 5
TURN_EXEMPT_TOOLS = {"Agent", "Write", "AskUserQuestion", "TodoWrite", "TaskCreate", "TaskUpdate"}
SUBAGENT_STEP_LIMIT = 40
SUBAGENT_BRIEF_SUFFIX = (
    "Kiasi budget: finish within {steps} tool calls. Batch shell commands, run each test suite once per round, never poll with sleep. "
    "When done or blocked, reply with at most 300 words: what changed (paths), what was verified, what remains."
)
BUDGET_FRESH_HOURS = 48

AGENT_TOOL = "Agent"
READ_TOOL = "Read"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
READ_SKIP_MIN_CHARS = 2000
READ_SKIP_REASON = ("kiasi: {path}{span} is unchanged since you read it earlier in this conversation, so its text is already in your context. "
                    "Use that copy. If it is no longer in your context, repeat the same Read and it will go through.")
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
OUTSIDE_READ_PATTERN = r"(/\.claude/projects/|^/tmp/|\.(log|jsonl)$)"
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
NOTE_MIN_INTERVAL_MINUTES = 10
NOTE_MESSAGE_HEAD_CHARS = 400

BUDGET_DAYS = 7
BUDGET_TOP_SESSIONS = 8
BUDGET_TOP_OUTPUTS = 8
BUDGET_BIG_OUTPUT_CHARS = 20_000
BUDGET_HIGH_CONTEXT_TOKENS = 200_000

SEARCH_DB = LOG_DIR / "search.db"
SEARCH_DIRS = (OUTPUT_DIR, PASTE_DIR, NOTES_DIR, CHECKPOINT_DIR)
SEARCH_SUFFIXES = {".txt", ".md", ".jsonl", ".json"}
SEARCH_MAX_FILE_CHARS = 1_000_000
SEARCH_RESULTS = 8
SEARCH_SNIPPET_TOKENS = 40
SEARCH_WORD_ENDINGS = r"(?:s|es|ed|ing|er)?"
SEARCH_AVG_CHARS = 20_000

LENS_FILE = LOG_DIR / "lens.json.gz"  # gzipped; the dashboard server sends it as is with Content-Encoding: gzip
LENS_MAX_ACTIONS = 2000
LENS_MAX_SESSIONS = 60
LENS_SERIES_POINTS = 240
LENS_COMPACT_JOIN_SECONDS = 180
LENS_STEP_BUCKETS = ((1, 1), (2, 5), (6, 10), (11, 20), (21, 29), (30, 59), (60, None))
LENS_CUT_BUCKETS = ((0, 2000), (2000, 5000), (5000, 10000), (10000, 20000), (20000, None))
LENS_COMPLY_STEPS = 10
LENS_CACHE_MISS_TOKENS = 20000
LENS_CACHE_IDLE_SHORT = 300
LENS_CACHE_IDLE_LONG = 3600
LENS_HIT_TARGET = 0.7  # share of input read from cache; 70%+ is the usual bar for a stable prompt prefix
# Anthropic prompt-cache prices relative to base input (5-minute cache): a write costs 1.25x, a read 0.1x.
# A miss writes what would have been read, so its extra cost in re-read tokens is tokens * (write - read) / read.
CACHE_WRITE_PRICE = 1.25
CACHE_READ_PRICE = 0.1
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
    "outputs": r"(toolu_[A-Za-z0-9_-]+|compact-[0-9a-f]{8}-\d{8}-\d{6})\.txt",
    "checkpoints": r"[0-9a-f]{8}-\d+\.md",
    "pastes": r"[0-9a-f-]{36}-\d+\.txt",
    "sessions": r"[0-9a-f-]{36}\.json",
    "notes": r"[A-Za-z0-9-]+\.jsonl",
}

SYNC_REQUEST = LOG_DIR / "sync.request"
SYNC_STATUS = LOG_DIR / "sync.json"
SYNC_SCRIPTS = ("budget.py", "lens.py")

# SessionStart: rules.md shipped inside the plugin, injected as additionalContext.
RULES_FILE = PLUGIN_ROOT / "rules.md"
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

CAP_OUTSIDE_READ_CHARS = _env_int("CLAUDE_PLUGIN_OPTION_OUTPUT_CAP_CHARS", CAP_OUTSIDE_READ_CHARS)
SUBAGENT_STEP_LIMIT = _env_int("CLAUDE_PLUGIN_OPTION_TURN_CALL_BUDGET", SUBAGENT_STEP_LIMIT)
TURN_STOP_STEPS = _env_int("CLAUDE_PLUGIN_OPTION_TURN_CALL_BUDGET", TURN_STOP_STEPS)
PASTE_BLOCK_CHARS = _env_int("CLAUDE_PLUGIN_OPTION_PASTE_REFUSAL_CHARS", PASTE_BLOCK_CHARS)
COMPACTION_WINDOW_TEXT = os.environ.get("CLAUDE_PLUGIN_OPTION_COMPACTION_WINDOW_TEXT", "200000")
SYNC_TIMEOUT_SECONDS = 120

# The dashboard answers only requests addressed to the loopback names it listens on,
# so a web page cannot reach it through DNS rebinding, and POST /sync only from its own pages.
DASHBOARD_HOSTS = ("127.0.0.1", "localhost")

# Plan limits come only from the status line: Claude Code hands it rate_limits
# and the Kiasi wrapper (statusline.py) saves them for the dashboard.
RATE_LIMITS_NAME = "rate-limits.json"
LEGACY_LIMITS_NAME = "limits.json"
LIMIT_WARN_PERCENT = 75
LIMIT_CRITICAL_PERCENT = 90
STATUSLINE_CHAIN_NAME = "statusline-chain.json"
STATUSLINE_SCRIPT_NAME = "statusline.py"
CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
SETTINGS_BACKUP_SUFFIX = ".kiasi-bak"
