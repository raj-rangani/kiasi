const LENS_FILE = '/reports/lens.json';
const REFRESH_MS = 30000;
const CHART_HEIGHT = 300;
const BAR_RADIUS = 4;
const MINI_HEIGHT = 110;
const PAD = { top: 24, right: 88, bottom: 44, left: 64 };
const MINI_PAD = { top: 8, right: 8, bottom: 8, left: 8 };
const BILL_PAD = { ...PAD, right: PAD.left };
const DAY_LINE = { width: 224, height: 32, pad: 5, dot: 1.8, peakDot: 3 };
const NARROW_STEP_PX = 48;
const MAX_ROWS = 400;
const OVERVIEW_ROWS = 12;
const STARTUP_HINT_RATIO = 1.1;
const MISS_CAUSE_HELP = {
  compaction: 'the transcript was rebuilt, so this one is expected',
  'idle over 1h': 'the cache expired during a long pause',
  'idle over 5 min': 'the cache expired during a pause (5-minute cache)',
  'model switch': 'each model has its own cache',
  other: 'something early in the prompt changed that Kiasi did not see: a reload, the tool list, or a change made before it started recording',
  'settings changed': 'a settings file changed between two steps',
  'CLAUDE.md changed': 'a CLAUDE.md file changed between two steps',
  'plugins changed': 'a plugin was installed, updated or reloaded',
  'skills changed': 'a skill was added or edited',
  'MCP changed': 'the project MCP servers changed',
};
const MISS_CAUSE_FIX = {
  compaction: 'expected, nothing to do',
  'idle over 1h': 'start a new session or /clear after a long break instead of carrying on',
  'idle over 5 min': 'short pauses while the 5-minute cache was in use; mostly unavoidable',
  'model switch': 'pick the model at the start of a session and keep it',
  other: 'avoid editing CLAUDE.md, settings, skills or plugins while a session is running',
  'settings changed': 'change settings between sessions, not during one',
  'CLAUDE.md changed': 'edit CLAUDE.md between sessions, not during one',
  'plugins changed': 'update or reload plugins between sessions',
  'skills changed': 'edit skills between sessions',
  'MCP changed': 'change MCP servers between sessions',
};
const MISS_SPARK = { width: 96, height: 22 };
const TREND = { width: 360, height: 120, maxDays: 60 };
const CACHE_CAUSE_MIN_SHARE = 0.1;
const RECALL_LOW = 0.05;
const RECALL_HIGH = 0.3;
const MULTIPLES = 12;
const BOOKKEEPING_KINDS = ['note', 'recall', 'state', 'agent_model', 'compaction', 'review_asked'];
const SMALL_SESSION_STEPS = 5;
const SESSION_ROWS = 10;
const KIND_LABELS = {
  cap: 'output cap', paste_refused: 'paste refused', delegated: 'delegation', pruned: 'compaction pruned', summary: 'compaction summarised',
  compaction: 'compaction', reread_check: 're-read check', paste_saved: 'paste saved', nudge: 'context nudge', turn_warn: 'turn warning',
  turn_stop: 'turn paused', turn_over: 'turn over budget', turn_resume: 'pause resumed', turn_moved_on: 'pause skipped', read_skipped: 're-read skipped', read_retry: 're-read let through', agent_model: 'subagent model', review_asked: 'review round asked', note: 'session note', recall: 'note recalled', loop: 'loop stopped', state: 'state re-injected',
  routed: 'routed to sandbox', route_retry: 'routing let through',
};
const RULE_STRIP_DAYS = 8;

const KIND_HELP = {
  cap: 'a tool result over its cap was cut; the full text is saved to disk',
  paste_refused: 'a prompt over 40 k chars was saved to disk and dropped from the conversation',
  delegated: 'the re-read numbers said a subagent was cheaper, so Claude was told to delegate',
  pruned: 'the plugin replaced the summariser with a deterministic prune of the transcript',
  summary: 'the prune could not get under the ceiling, so the built-in summary ran',
  compaction: 'a compaction happened (PreCompact hook)',
  read_skipped: 'a Read of a file range already in context and unchanged on disk was answered with a pointer to the earlier copy',
  read_retry: 'Claude repeated a skipped Read, so it went through; a high count means the skip fires when the copy is gone',
  reread_check: 'here-versus-subagent estimates were shown and left to Claude',
  paste_saved: 'a prompt over 4 k chars was saved so later turns can refer to the path',
  nudge: 'a message asked to compact or clear',
  turn_warn: 'the turn passed the warning step or re-read budget',
  turn_stop: 'the turn reached its budget and was paused: further calls were refused and the remaining work was saved to resume',
  turn_over: 'the turn reached its budget in warn mode; Kiasi said so and refused nothing',
  turn_resume: 'the developer replied "continue" after a pause and Claude got the saved work back',
  turn_moved_on: 'the first prompt after a pause was not "continue", so Claude was only told where the saved work is',
  agent_model: 'a subagent got its model set by type',
  review_asked: 'a repeated review round became a permission prompt',
  note: 'a session note was written for recall',
  recall: 'the last note for the project was injected at session start',
  loop: 'the same failing command or file edit failed repeatedly in one turn, so Claude was told to stop retrying and check its assumption',
  state: 'after a compaction the task, edited files, still-failing commands, saved outputs and checklists were re-injected from the transcript',
  routed: 'a Bash command whose output would only be scanned, or a WebFetch, was refused once with the mcp__kiasi__run or mcp__kiasi__fetch call to make instead',
  route_retry: 'Claude repeated the same raw call after the routing, so it went through; a high count means the sandbox digest was not enough',
};
const MARK_LABELS = { compaction: 'compaction', pruned: 'pruned compaction', check: 're-read check', stop: 'turn stopped' };
const VIEWS = [
  ['overview', 'Overview', 'What your sessions paid, how that changed since Kiasi was switched on, and what Kiasi did. <b>Re-read</b> is the whole conversation sent again on every step.'],
  ['sessions', 'Sessions', 'One chart per session: context at every step, prompts as ticks, compactions as rings. Click a session for its full trajectory and every Kiasi action inside it.'],
  ['rules', 'Rules', 'Each rule of Kiasi: how often it fired, how many tokens it cut, and its log. The chart shows where the steps go, which is what the turn budget shapes. Cache misses and the cut outputs read back follow the rules.'],
  ['budget', 'Budget', 'Where the weekly limit goes: every turn re-reads the whole conversation, so the cost is context size times turns.'],
  ['storage', 'Storage', 'What Kiasi keeps on disk and for how long. A saved file follows its session: once both are unused for a week it goes to trash, and the trash is emptied a week later.']
];
// Page-level empty states, one per view. `missing` is shown when no report exists yet,
// `empty` when the report holds no session in range; {days} is the report window.
const EMPTY_STATES = {
  overview: {
    missing: ['Your first report is one click away', 'Kiasi reads your Claude Code transcripts and shows what it kept off the weekly limit. Nothing has been built yet.'],
    empty: ['No Claude Code sessions in the last {days} days', 'The savings, the daily bill and the rule cards fill in from the next session with Kiasi on. The report rebuilds every 30 minutes.'],
  },
  sessions: {
    missing: ['No sessions to chart yet', 'One chart per session appears here, context at every step, once Kiasi has read a Claude Code session. Nothing has been built yet.'],
    empty: ['No Claude Code sessions in the last {days} days', 'One chart per session appears here from the next session with Kiasi on. The report rebuilds every 30 minutes.'],
  },
  rules: {
    missing: ['Rules have not fired yet', 'Each rule\'s count, savings and log fill in as you work in Claude Code with Kiasi on. Nothing has been built yet.'],
    empty: ['No rule fired in the last {days} days', 'Each rule\'s count, savings and log fill in from the next session with Kiasi on. The report rebuilds every 30 minutes.'],
  },
  budget: {
    missing: ['No re-read bill yet', 'The per-day bill and the costliest tool results appear here after the first session. Plan limits above stay live. Nothing has been built yet.'],
    empty: ['No Claude Code sessions in the last {days} days', 'The per-day bill and the costliest tool results appear here from the next session with Kiasi on. Plan limits above stay live.'],
  },
  storage: {
    missing: ['No storage scan yet', 'What Kiasi keeps on disk, and for how long, is measured when the report is built. Nothing has been built yet.'],
    empty: ['No storage scan in this report', 'The scan runs with the next report build, every 30 minutes.'],
  },
};
const EMPTY_ACTIONS = { missing: 'Build the report', empty: 'Rebuild now' };
const EMPTY_BUILDING = 'Reading the transcripts, usually under a minute.';
const EMPTY_LOADING = 'Loading the report';
const EMPTY_LINK = { text: 'What each tab shows', href: 'https://raj-rangani.github.io/kiasi/#dashboard' };
// Growth below this many bytes a day reads as flat.
const STORE_FLAT_BYTES = 10e3;
// Days of growth averaged for the rate under the bar.
const STORE_RATE_DAYS = 7;
// Never-read saved files below this size get no recommendation.
const STORE_REC_MIN_BYTES = 100e3;
// Storage categories in bar order: key, label, 24px line-icon SVG body, data-folder names (empty = everything unlisted).
const STORE_CATS = [
  ['outputs', 'Cut outputs', '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12"/>', ['outputs']],
  ['checkpoints', 'Checkpoints', '<path d="M10 6h10M10 12h10M10 18h10"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/>', ['checkpoints']],
  ['pastes', 'Pastes', '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>', ['pastes']],
  ['notes', 'Notes', '<path d="M4 4h16v10l-6 6H4z"/><path d="M14 20v-6h6M8 9h8M8 13h4"/>', ['notes']],
  ['sessions', 'Session counters', '<path d="M3.34 19a10 10 0 1 1 17.32 0"/><path d="m12 14 4-4"/>', ['sessions']],
  ['log', 'Event log', '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>', ['kiasi.jsonl', 'cleanup.jsonl']],
  ['trash', 'Trash', '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>', ['trash']],
  ['caches', 'Caches', '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>', []],
];
const STORE_CAT_HELP = {
  outputs: 'Full text of cut tool results and compaction originals',
  checkpoints: 'Handover checklists written at the turn budget',
  pastes: 'Large pasted prompts saved to disk',
  notes: 'Last task per project, shown at session start',
  sessions: 'Per-session counters for the hooks',
  log: 'Kept · every chart is built from it',
  caches: 'Dashboard data and status files · rebuilt on every sync',
};
const STORAGE_ACTIONS = { trash: 'moved to trash', delete: 'deleted', restore: 'restored' };
const BUDGET_FILE = '/reports/budget.json';
const LIMITS_FILE = '/limits';
const LIMIT_WARN = 75;
const LIMIT_BAD = 90;
const LIMIT_PACE_SLACK = 5;
const LIMITS_STALE_MS = 30 * 60 * 1000;
const LIMITS_HINTS = {
  none: 'Plan limits show here once the Kiasi status line is set up: run /kiasi:limits setup in Claude Code. It hands the status line the 5-hour and weekly use on Pro and Max plans.',
  waiting: 'Plan limits show after the next reply in a Claude Code session.',
};
const SYNC_URL = '/sync';
const SYNC_STATUS_FILE = '/reports/sync.json';
const SYNC_POLL_MS = 1000;
const SYNC_TIMEOUT_MS = 60000;
const SYNC_EVERY_TEXT = 'auto every 30 min';
const VIEW_ALIASES = { actions: 'rules' };
const TOP_MULTIPLES = 8;
const LOG_ROWS = 60;
const RULES = [
  { key: 'cap', name: 'Output cap', short: 'Oversized tool output cut; the full text is kept on disk', kinds: ['cap'], what: 'A tool result over its cap is cut and the full text saved to disk; the conversation keeps a marker with the path.' },
  { key: 'route', name: 'Sandbox routing', short: 'Scan-only Bash and WebFetch pointed at the sandbox tools', kinds: ['routed', 'route_retry'], what: 'While the sandbox tools are registered, a Bash command whose output would only be scanned (tests, builds, installs, curl, git log) or a WebFetch is refused once with the mcp__kiasi__run or mcp__kiasi__fetch call to make instead; repeating the same call lets it through.' },
  { key: 'pruner', name: 'Compaction pruner', short: 'Transcript pruned at compaction instead of summarised', kinds: ['pruned', 'summary'], what: 'At compaction the plugin prunes the transcript deterministically instead of calling the summariser; it falls back only if the prune cannot get under the ceiling.' },
  { key: 'turn', name: 'Turn budget', short: 'Long prompts warned, then paused with the rest saved', kinds: ['turn_warn', 'turn_stop', 'turn_over', 'turn_resume', 'turn_moved_on'], what: 'A prompt that runs too many steps is warned, then paused with the remaining work written down; "continue" resumes it.' },
  { key: 'reread', name: 'Re-read check', short: 'Cost here against a subagent, shown to Claude', kinds: ['reread_check', 'delegated'], what: 'Shows what the rest of the turn will cost here against in a subagent, and lets Claude delegate.' },
  { key: 'reads', name: 'Re-read skip', short: 'Unchanged file range not read twice', kinds: ['read_skipped', 'read_retry'], what: 'A Read of a file range already in context and unchanged on disk gets a pointer to the earlier copy instead of the text; repeating the Read lets it through.' },
  { key: 'loop', name: 'Loop check', short: 'Repeated failing call told to stop retrying', kinds: ['loop'], what: 'A command or edit that fails three times in one turn, or one command failing five times with different arguments, gets a note to stop retrying and check the assumption. Failed calls also count toward the turn budget.' },
  { key: 'paste', name: 'Paste manager', short: 'Large pastes saved to disk; very large ones refused', kinds: ['paste_saved', 'paste_refused'], what: 'Large pasted prompts are saved to disk so later turns refer to the path; very large ones are refused.' },
  { key: 'notes', name: 'Notes and recall', short: 'Bookkeeping: session notes, recall, state after compaction', kinds: ['note', 'recall', 'state', 'agent_model', 'compaction', 'nudge', 'review_asked'], what: 'Bookkeeping: a note at every stop and compaction, recalled at the next start; working state re-injected after each compaction; subagent models set by type.', muted: true },
];
const DETAIL_HEIGHT = 340;
const PROMPT_ROWS = 20;
const RULE_CHART_HEIGHT = 200;
const STEPS_CHART_HEIGHT = 240;
const PANEL_MS = 260;
const JUMP_LABELS = { 'Which tools get capped': 'Tools', 'How much each cut kept out': 'Cut sizes', 'Every compaction the pruner handled': 'Compactions', 'Every warning, pause and resume': 'Warnings', 'Every check shown': 'Checks', 'Every paste handled': 'Pastes' };
const OFFENDER_ROWS = 5;
const OFFENDER_LABEL_CHARS = 48;

const EST_TAG = 'est.';
const EST_TIPS = {
  avoided: 'Estimate: tokens kept out of context, times the assistant steps that came after in the session.',
  cap: 'Estimate: (characters before the cut − characters shown) ÷ 4, times the assistant steps that came after.',
  pruner: 'Estimate: the context size before the compaction (tokens_before) that the pruned transcript did not have to rebuild.',
  reread: 'Estimate: re-read tokens if this ran here, minus re-read tokens if it were delegated to a subagent.',
  extra: 'Estimate: tokens written again after each miss, priced against a cache read.',
};
const EST_NOTE = 'Figures marked est. come from a formula; everything else is read from your transcripts.';
