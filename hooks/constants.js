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

export const INPUT_FIELD_KEEP_CHARS = 500;
export const CONTENT_FIELDS = ['content', 'new_string', 'old_string', 'new_source'];

export const LEVELS = [
  { recent: RECENT_MESSAGES, resultChars: RESULT_HEAD_CHARS, assistantChars: Infinity },
  { recent: RECENT_MESSAGES_MIN, resultChars: RESULT_LINE_CHARS, assistantChars: Infinity },
  { recent: RECENT_MESSAGES_MIN, resultChars: RESULT_LINE_CHARS, assistantChars: ASSISTANT_TEXT_HEAD_CHARS },
  { recent: RECENT_MESSAGES_LAST, resultChars: RESULT_LINE_CHARS, assistantChars: ASSISTANT_TEXT_LAST_CHARS, userKeepChars: USER_TEXT_LAST_KEEP_CHARS },
];

export const KIASI_SCRIPT = 'kiasi.py';
export const LOG_EVENT_NAME = 'PluginCompact';
export const TOAST_MS = 6000;

export const SEARCH_SCRIPT = 'search.py';
export const SEARCH_TOOL = 'search';
export const SEARCH_TOOL_FULL_NAME = 'mcp__kiasi__search';
export const SEARCH_DEFAULT_LIMIT = 8;
export const SEARCH_MAX_LIMIT = 20;
export const SEARCH_TIMEOUT_MS = 20000;
export const SEARCH_DESCRIPTION = 'Full-text search over the context kiasi\'s saved tool outputs, pasted prompts, session notes and turn checkpoints. Returns the file path, a score and a snippet per match; read the named file by section afterwards instead of re-reading whole files. Every word must appear (common endings like -s, -ed, -ing also match); OR, NOT, "quoted phrases" and a trailing * work.';
export const SEARCH_INPUT_SCHEMA = {
  type: 'object',
  properties: {
    query: { type: 'string', description: 'Words to find, e.g. a tool-use id, a file name, an error message or a task phrase.' },
    limit: { type: 'integer', minimum: 1, maximum: SEARCH_MAX_LIMIT, description: `How many files to list (default ${SEARCH_DEFAULT_LIMIT}).` },
  },
  required: ['query'],
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
export const ARCHIVE_MIN_CHARS = 2000;
export const ARCHIVE_PATH_EVENT_NAME = 'PluginArchivePath';
export const ARCHIVE_EVENT_NAME = 'PluginArchive';
