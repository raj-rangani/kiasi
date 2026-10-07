import {
  LEVELS, ERROR_KEEP_CHARS, USER_TEXT_KEEP_CHARS, USER_TEXT_HEAD_CHARS, INJECTED_HEAD_CHARS, INJECTED_PATTERN, HANDBACK_PATTERNS, HANDBACK_SCAN_CHARS,
  INPUT_FIELD_KEEP_CHARS, CONTENT_FIELDS, CHARS_PER_TOKEN, MAX_KEEP_TOKENS, MAX_KEEP_RATIO, MARKER_PREFIX, SIZE_MARKER_PATTERN, ARCHIVE_MIN_CHARS, PRUNE_KEEP_EXCHANGES, PRUNE_OLD_PROMPT_CHARS, PRUNE_OUTCOME_CHARS, PRUNE_REDUCE_MIN_CHARS, EXCHANGE_MARKER_PREFIX, KIASI_MARKER_PREFIXES, POINTER_LINES_KEPT,
} from './constants.js';

const marker = (dropped, ref) => (ref
  ? `${MARKER_PREFIX} ${dropped} chars at compaction; full text is #${ref.index} in ${ref.path}]`
  : `${MARKER_PREFIX} ${dropped} chars at compaction]`);

// Content an earlier compaction replaced is already a size marker; measuring it again would give the marker's own length.
const sizeMarker = (value) => (SIZE_MARKER_PATTERN.test(value) ? value : `[${value.length} chars]`);

function archived(archive, text, dropped) {
  if (!archive || dropped < ARCHIVE_MIN_CHARS) return null;
  archive.items.push(text);
  return { index: archive.items.length, path: archive.path };
}

const isMarker = (line) => KIASI_MARKER_PREFIXES.some((prefix) => line.startsWith(prefix));

function alreadyCut(text) {
  const end = text.trimEnd();
  return isMarker(end.slice(end.lastIndexOf('\n') + 1));
}

// Saved-at lines in the dropped tail are the only path to the full output, so they survive the cut.
function pointersIn(tail) {
  const lines = tail.split('\n').map((line) => line.trim()).filter((line) => isMarker(line) && line.includes('saved at '));
  return [...new Set(lines)].slice(0, POINTER_LINES_KEPT);
}

function head(text, keep, archive) {
  if (text.length <= keep || alreadyCut(text)) return text;
  const dropped = text.length - keep;
  const pointers = pointersIn(text.slice(keep));
  return `${text.slice(0, keep)}\n${marker(dropped, archived(archive, text, dropped))}${pointers.map((line) => `\n${line}`).join('')}`;
}

function charsOfMessage(message) {
  const uses = (message.toolUses || []).reduce((sum, use) => sum + JSON.stringify(use.input || {}).length + (use.text || '').length, 0);
  const results = (message.toolResults || []).reduce((sum, result) => sum + (result.text || '').length, 0);
  return (message.text || '').length + uses + results;
}

export function charsOf(messages) {
  return messages.reduce((sum, message) => sum + charsOfMessage(message), 0);
}

// Strings nested in arrays and objects (a MultiEdit's edits) shrink like top-level ones; the field name decides content or not.
function pruneValue(key, value, level) {
  if (typeof value === 'string') return CONTENT_FIELDS.includes(key) ? sizeMarker(value) : head(value, INPUT_FIELD_KEEP_CHARS, level.archive);
  if (Array.isArray(value)) return value.map((item) => pruneValue(key, item, level));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, pruneValue(name, item, level)]));
  return value;
}

function pruneInput(input, level) {
  return Object.fromEntries(Object.entries(input || {}).map(([key, value]) => [key, pruneValue(key, value, level)]));
}

function pruneResult(text, isError, level) {
  return head(text || '', isError ? ERROR_KEEP_CHARS : level.resultChars, level.archive);
}

const isHandback = (text) => {
  const start = text.trim().slice(0, HANDBACK_SCAN_CHARS);
  return HANDBACK_PATTERNS.some((pattern) => pattern.test(start));
};

// A subagent's hand-back report is the only copy of its findings: kept whole, or archived, never reduced to a stub.
function pruneHandback(text, keep, archive) {
  if (text.length <= keep || alreadyCut(text)) return text;
  const dropped = text.length - keep;
  if (archived(archive, text, dropped)) return head(text, USER_TEXT_HEAD_CHARS, archive);
  return `${text.slice(0, keep)}\n[kiasi trimmed ${dropped} chars of a subagent report; it was not archived]`;
}

function pruneUserText(text, level) {
  const keep = level.userKeepChars ?? USER_TEXT_KEEP_CHARS;
  if (isHandback(text)) return pruneHandback(text, keep, level.archive);
  if (INJECTED_PATTERN.test(text)) return head(text, INJECTED_HEAD_CHARS, level.archive);
  if (text.length > keep) return head(text, USER_TEXT_HEAD_CHARS, level.archive);
  return text;
}

function rebuildUser(message, level) {
  const rebuilt = { role: 'user', text: pruneUserText(message.text || '', level), toolUses: [] };
  if (message.toolResults) {
    rebuilt.toolResults = message.toolResults.map((result) => ({
      tool_use_id: result.tool_use_id,
      text: pruneResult(result.text, result.isError, level),
      isError: Boolean(result.isError),
    }));
  }
  return rebuilt;
}

function rebuildToolUse(use, level) {
  const rebuilt = { tool_use_id: use.tool_use_id, tool: use.tool, input: pruneInput(use.input, level) };
  if (use.text !== undefined) rebuilt.text = pruneResult(use.text, use.isError, level);
  if (use.isError) rebuilt.isError = true;
  return rebuilt;
}

function rebuildAssistant(message, level) {
  return {
    role: 'assistant',
    text: head(message.text || '', level.assistantChars, level.archive),
    toolUses: (message.toolUses || []).map((use) => rebuildToolUse(use, level)),
  };
}

// Claude Code stores a parallel batch as one assistant message per call and writes each result when its call ends,
// sometimes before the batch's later calls. Rebuilt messages get fresh ids, so unless a batch is rebuilt as one
// message ahead of all its results, every call whose result does not come next loses it when the session loads.
function mergeBatches(messages) {
  const ahead = new Set(messages.flatMap((message) => (message.toolResults || []).map((result) => result.tool_use_id)));
  const pending = new Set();
  const merged = [];
  let calls = null;
  for (const message of messages) {
    for (const result of message.toolResults || []) {
      ahead.delete(result.tool_use_id);
      pending.delete(result.tool_use_id);
    }
    if (message.role === 'assistant' && calls && (merged[merged.length - 1] === calls || pending.size)) {
      calls.text = [calls.text, message.text].filter(Boolean).join('\n\n');
      calls.toolUses = [...calls.toolUses, ...message.toolUses];
    } else {
      merged.push(message);
      if (message.role === 'assistant') calls = message;
    }
    for (const use of message.toolUses) if (ahead.has(use.tool_use_id)) pending.add(use.tool_use_id);
  }
  return merged;
}

// The recent messages kept as they are must not start inside a run of assistant messages or between a call and its result.
function batchStart(messages, index) {
  const callAt = new Map();
  messages.forEach((message, at) => (message.toolUses || []).forEach((use) => callAt.set(use.tool_use_id, at)));
  const earliest = Array(messages.length + 1).fill(Infinity);
  for (let at = messages.length - 1; at >= 0; at -= 1) {
    earliest[at] = Math.min(earliest[at + 1], ...(messages[at].toolResults || []).map((result) => callAt.get(result.tool_use_id) ?? Infinity));
  }
  let start = index;
  while (start > 0 && (earliest[start] < start || (messages[start].role === 'assistant' && messages[start - 1].role === 'assistant'))) start -= 1;
  return start;
}

// An exchange starts at a real prompt: not a tool-result message, an injected notice or a subagent hand-back.
function isPrompt(message) {
  const text = (message.text || '').trim();
  return message.role === 'user' && !(message.toolResults || []).length && Boolean(text) && !INJECTED_PATTERN.test(text) && !isHandback(text);
}

function isReduced(exchange) {
  const last = exchange[exchange.length - 1];
  return last.role === 'assistant' && !last.toolUses.length && (last.text || '').trimEnd().split('\n').pop().startsWith(EXCHANGE_MARKER_PREFIX);
}

const isHandbackMessage = (message) => message.role === 'user' && isHandback(message.text || '');

function flatten(messages) {
  return messages.map((message) => {
    const uses = (message.toolUses || []).map((use) => `[${use.tool}] ${JSON.stringify(use.input || {})}${use.text ? `\n${use.text}` : ''}`);
    const results = (message.toolResults || []).map((result) => result.text || '');
    return [`[${message.role}] ${message.text || ''}`, ...uses, ...results].join('\n');
  }).join('\n');
}

function outcomeOf(exchange) {
  const last = [...exchange].reverse().find((message) => message.role === 'assistant' && (message.text || '').trim());
  return last ? last.text.trim().split('\n')[0].slice(0, PRUNE_OUTCOME_CHARS) : '';
}

// Prompt, a one-line outcome and a pointer; hand-backs stay whole or archived with their own pointer, the rest is in the archive.
function reduceExchange(exchange, number, level) {
  const [prompt, ...rest] = exchange;
  level.archive.items.push(`--- exchange ${number} ---\n${flatten([prompt, ...rest.filter((message) => !isHandbackMessage(message))])}`);
  const pointer = `${EXCHANGE_MARKER_PREFIX} ${number} (${flatten(exchange).length} chars) at compaction; full text is #${level.archive.items.length} in ${level.archive.path}]`;
  const handbacks = rest.filter(isHandbackMessage)
    .map((message) => ({ role: 'user', text: pruneHandback(message.text, level.userKeepChars ?? USER_TEXT_KEEP_CHARS, level.archive), toolUses: [] }));
  const text = prompt.text || '';
  return [
    { role: 'user', text: text.length > PRUNE_OLD_PROMPT_CHARS ? `${text.slice(0, PRUNE_OLD_PROMPT_CHARS)} ...` : text, toolUses: [] },
    ...handbacks,
    { role: 'assistant', text: [outcomeOf(exchange), pointer].filter(Boolean).join('\n'), toolUses: [] },
  ];
}

const rebuildAll = (messages, level) => mergeBatches(messages.map((message) => (message.role === 'user' ? rebuildUser(message, level) : rebuildAssistant(message, level))));

function pruneRecent(messages, level) {
  const cut = batchStart(messages, Math.max(0, messages.length - level.recent));
  return [...rebuildAll(messages.slice(0, cut), level), ...messages.slice(cut)];
}

// Older exchanges shrink to a pointer when there is an archive to point at; the last few are only tool-pruned.
function applyLevel(messages, level, total) {
  const starts = messages.flatMap((message, at) => (isPrompt(message) ? [at] : []));
  if (!level.archive || total < PRUNE_REDUCE_MIN_CHARS || starts.length <= PRUNE_KEEP_EXCHANGES) return pruneRecent(messages, level);
  const old = [rebuildAll(messages.slice(0, starts[0]), level)];
  starts.slice(0, -PRUNE_KEEP_EXCHANGES).forEach((from, index) => {
    const exchange = messages.slice(from, starts[index + 1]);
    old.push(isReduced(exchange) || charsOf(exchange) < ARCHIVE_MIN_CHARS ? rebuildAll(exchange, level) : reduceExchange(exchange, index + 1, level));
  });
  return [...old.flat(), ...pruneRecent(messages.slice(starts[starts.length - PRUNE_KEEP_EXCHANGES]), level)];
}

export function pruneTranscript(messages, archivePath) {
  const before = charsOf(messages);
  const limit = Math.min(MAX_KEEP_TOKENS * CHARS_PER_TOKEN, before * MAX_KEEP_RATIO);
  let after = before;
  for (const [index, level] of LEVELS.entries()) {
    const archive = archivePath ? { path: archivePath, items: [] } : null;
    const pruned = applyLevel(messages, { ...level, archive }, before);
    after = charsOf(pruned);
    if (after <= limit) return { kept: true, messages: pruned, before, after, level: index, archive };
  }
  return { kept: false, messages: null, before, after, level: LEVELS.length, archive: null };
}
