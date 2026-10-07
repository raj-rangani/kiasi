export const CHARS_PER_TOKEN = 4;
export const MAX_KEEP_TOKENS = 60000;
export const MAX_KEEP_RATIO = 0.5;

export const RECENT_MESSAGES = 8;
export const RECENT_MESSAGES_MIN = 4;
export const RESULT_HEAD_CHARS = 300;
export const RESULT_LINE_CHARS = 80;
export const ERROR_KEEP_CHARS = 2000;
export const ASSISTANT_TEXT_HEAD_CHARS = 600;
export const ASSISTANT_TEXT_LAST_CHARS = 300;
export const RECENT_MESSAGES_LAST = 2;

export const USER_TEXT_KEEP_CHARS = 4000;
export const USER_TEXT_HEAD_CHARS = 600;
export const USER_TEXT_LAST_KEEP_CHARS = 1500;
export const INJECTED_HEAD_CHARS = 200;
export const INJECTED_PATTERN = /^\s*<(task-notification|system-reminder|local-command-|command-name|pasted_content|agent-message)/;

export const HANDBACK_SCAN_CHARS = 300;
export const HANDBACK_PATTERNS = [
  /^<task-notification/,
  /^<agent-message/,
  /^Another Claude session sent a message:\s*<agent-message/,
  /^<system-reminder>[\s\S]*?<task-notification/,
];

export const INPUT_FIELD_KEEP_CHARS = 500;
export const CONTENT_FIELDS = ['content', 'new_string', 'old_string', 'new_source'];

export const LEVELS = [
  { recent: RECENT_MESSAGES, resultChars: RESULT_HEAD_CHARS, assistantChars: Infinity },
  { recent: RECENT_MESSAGES_MIN, resultChars: RESULT_LINE_CHARS, assistantChars: Infinity },
  { recent: RECENT_MESSAGES_MIN, resultChars: RESULT_LINE_CHARS, assistantChars: ASSISTANT_TEXT_HEAD_CHARS },
  { recent: RECENT_MESSAGES_LAST, resultChars: RESULT_LINE_CHARS, assistantChars: ASSISTANT_TEXT_LAST_CHARS, userKeepChars: USER_TEXT_LAST_KEEP_CHARS },
];

export const PRUNE_KEEP_EXCHANGES = 3;
export const PRUNE_OLD_PROMPT_CHARS = 600;
export const PRUNE_OUTCOME_CHARS = 200;
export const PRUNE_REDUCE_MIN_CHARS = 200000;
export const EXCHANGE_MARKER_PREFIX = '[kiasi reduced exchange';

export const KIASI_SCRIPT = 'kiasi.py';
export const LOG_EVENT_NAME = 'PluginCompact';
export const TOAST_MS = 6000;

export const SEARCH_SCRIPT = 'search.py';
export const SEARCH_TOOL = 'search';
export const SEARCH_TOOL_FULL_NAME = 'mcp__kiasi__search';
export const SEARCH_DEFAULT_LIMIT = 8;
export const SEARCH_MAX_LIMIT = 20;
export const SEARCH_TIMEOUT_MS = 20000;
export const SEARCH_DESCRIPTION = 'Full-text search over the context kiasi\'s saved tool outputs, pasted prompts, session notes and turn checkpoints. Returns the file path, a score, the hit line numbers and a snippet per match; read the named file by section (offset and limit around the named lines) afterwards instead of re-reading whole files. Every word must appear (common endings like -s, -ed, -ing also match); OR, NOT, "quoted phrases" and a trailing * work.';
export const SEARCH_INPUT_SCHEMA = {
  type: 'object',
  properties: {
    query: { type: 'string', description: 'Words to find, e.g. a tool-use id, a file name, an error message or a task phrase.' },
    limit: { type: 'integer', minimum: 1, maximum: SEARCH_MAX_LIMIT, description: `How many files to list (default ${SEARCH_DEFAULT_LIMIT}).` },
  },
  required: ['query'],
};

export const SANDBOX_SCRIPT = 'sandbox.py';
export const SANDBOX_DEFAULT_TIMEOUT_S = 120;
export const SANDBOX_EXTRA_MS = 10000;
export const PROCESS_RUN_MAX_TIMEOUT_MS = 600000;
export const SANDBOX_MAX_TIMEOUT_S = Math.floor((PROCESS_RUN_MAX_TIMEOUT_MS - SANDBOX_EXTRA_MS) / 1000);
export const RUN_TOOL = 'run';
export const RUN_TOOL_FULL_NAME = 'mcp__kiasi__run';
export const RUN_DESCRIPTION = 'Run a shell command out of context: the full output is saved to kiasi\'s outputs folder (searchable with mcp__kiasi__search) and only a digest — exit code, head, error lines, tail, saved path — enters the conversation. Use it for commands whose output you would only scan: builds, test runs, long logs, curl. Use plain Bash when you need the exact full output or the command changes files you will edit next.';
export const RUN_INPUT_SCHEMA = {
  type: 'object',
  properties: {
    command: { type: 'string', description: 'The shell command, run with bash -lc from the project directory.' },
    timeout_s: { type: 'integer', minimum: 1, maximum: SANDBOX_MAX_TIMEOUT_S, description: `Seconds before the command is stopped (default ${SANDBOX_DEFAULT_TIMEOUT_S}).` },
  },
  required: ['command'],
};
export const DISTILL_TOOL = 'distill';
export const DISTILL_TOOL_FULL_NAME = 'mcp__kiasi__distill';
export const DISTILL_DESCRIPTION = 'Derive an answer from files without reading them into context: runs a short python3 or node script, with the file paths as script arguments (sys.argv[1:] / process.argv.slice(2)), and only what it prints enters the conversation, capped at 4000 chars (the overflow is saved and the result names the file). Use it to count, filter, parse or aggregate over big files, including files kiasi saved. It cannot edit files; use Write or Edit for that.';
export const DISTILL_INPUT_SCHEMA = {
  type: 'object',
  properties: {
    code: { type: 'string', description: 'The script body. Print only the derived answer, not the data.' },
    language: { type: 'string', enum: ['python', 'node'], description: 'python (default) runs python3 -c, node runs node -e.' },
    files: { type: 'array', items: { type: 'string' }, description: 'File paths passed to the script as arguments.' },
    timeout_s: { type: 'integer', minimum: 1, maximum: SANDBOX_MAX_TIMEOUT_S, description: `Seconds before the script is stopped (default ${SANDBOX_DEFAULT_TIMEOUT_S}).` },
  },
  required: ['code'],
};
export const FETCH_TOOL = 'fetch';
export const FETCH_TOOL_FULL_NAME = 'mcp__kiasi__fetch';
export const FETCH_DESCRIPTION = 'Fetch a web page out of context: the page is downloaded, HTML is stripped to text, the whole text is saved to kiasi\'s outputs folder (searchable with mcp__kiasi__search) and only the first 3000 chars plus the lines matching the `find` words enter the conversation. Use it instead of WebFetch to read documentation, API responses or long pages; use WebFetch only when you want a summarised answer to a question.';
export const FETCH_INPUT_SCHEMA = {
  type: 'object',
  properties: {
    url: { type: 'string', description: 'The http(s) URL to fetch.' },
    find: { type: 'array', items: { type: 'string' }, description: 'Words or phrases; every line of the page containing one of them is returned with its line number (up to 40).' },
    timeout_s: { type: 'integer', minimum: 1, maximum: SANDBOX_MAX_TIMEOUT_S, description: `Seconds before the download is stopped (default ${SANDBOX_DEFAULT_TIMEOUT_S}).` },
  },
  required: ['url'],
};

export const QUIET_EVENT_NAME = 'PluginQuiet';
export const QUIET_RULES = [
  { name: 'npm', match: /\bnpm\s+(install|i|ci)\b/, skip: /--loglevel|--silent|\s-s\b|--quiet/, flags: '--loglevel=warn --no-fund --no-audit' },
  { name: 'pnpm', match: /\bpnpm\s+(install|i|add)\b/, skip: /--reporter|--loglevel|--silent/, flags: '--reporter=append-only --loglevel=warn' },
  { name: 'yarn', match: /\byarn\s+(install|add)\b/, skip: /--silent|--json/, flags: '--silent' },
  { name: 'composer', match: /\bcomposer\s+(install|update|require)\b/, skip: /--no-progress|\s-q\b|--quiet/, flags: '--no-progress' },
  { name: 'pip', match: /\bpip3?\s+install\b/, skip: /\s-q\b|--quiet/, flags: '-q' },
];


export const MARKER_PREFIX = '[kiasi pruned';
export const SIZE_MARKER_PATTERN = /^\[\d+ chars\]$/;
export const ARCHIVE_MIN_CHARS = 2000;
export const ARCHIVE_PATH_EVENT_NAME = 'PluginArchivePath';
export const ARCHIVE_EVENT_NAME = 'PluginArchive';
