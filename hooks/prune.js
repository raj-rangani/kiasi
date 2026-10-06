import {
  LEVELS, ERROR_KEEP_CHARS, USER_TEXT_KEEP_CHARS, USER_TEXT_HEAD_CHARS, INJECTED_HEAD_CHARS, INJECTED_PATTERN,
  INPUT_FIELD_KEEP_CHARS, CONTENT_FIELDS, CHARS_PER_TOKEN, MAX_KEEP_TOKENS, MAX_KEEP_RATIO, MARKER_PREFIX, ARCHIVE_MIN_CHARS,
} from './constants.js';

const marker = (dropped, ref) => (ref
  ? `${MARKER_PREFIX} ${dropped} chars at compaction; full text is #${ref.index} in ${ref.path}]`
  : `${MARKER_PREFIX} ${dropped} chars at compaction]`);

function archived(archive, text, dropped) {
  if (!archive || dropped < ARCHIVE_MIN_CHARS) return null;
  archive.items.push(text);
  return { index: archive.items.length, path: archive.path };
}

function head(text, keep, archive) {
  if (text.length <= keep || text.includes(MARKER_PREFIX)) return text;
  const dropped = text.length - keep;
  return `${text.slice(0, keep)}\n${marker(dropped, archived(archive, text, dropped))}`;
}

function charsOfMessage(message) {
  const uses = (message.toolUses || []).reduce((sum, use) => sum + JSON.stringify(use.input || {}).length + (use.text || '').length, 0);
  const results = (message.toolResults || []).reduce((sum, result) => sum + (result.text || '').length, 0);
  return (message.text || '').length + uses + results;
}

export function charsOf(messages) {
  return messages.reduce((sum, message) => sum + charsOfMessage(message), 0);
}

function pruneInput(input, level) {
  const pruned = {};
  for (const [key, value] of Object.entries(input || {})) {
    if (typeof value !== 'string') pruned[key] = value;
    else if (CONTENT_FIELDS.includes(key)) pruned[key] = `[${value.length} chars]`;
    else pruned[key] = head(value, INPUT_FIELD_KEEP_CHARS, level.archive);
  }
  return pruned;
}

function pruneResult(text, isError, level) {
  return head(text || '', isError ? ERROR_KEEP_CHARS : level.resultChars, level.archive);
}

function pruneUserText(text, level) {
  if (INJECTED_PATTERN.test(text)) return head(text, INJECTED_HEAD_CHARS);
  if (text.length > (level.userKeepChars ?? USER_TEXT_KEEP_CHARS)) return head(text, USER_TEXT_HEAD_CHARS, level.archive);
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

function applyLevel(messages, level) {
  const cut = batchStart(messages, Math.max(0, messages.length - level.recent));
  const rebuilt = messages.slice(0, cut).map((message) => (message.role === 'user' ? rebuildUser(message, level) : rebuildAssistant(message, level)));
  return [...mergeBatches(rebuilt), ...messages.slice(cut)];
}

export function pruneTranscript(messages, archivePath) {
  const before = charsOf(messages);
  const limit = Math.min(MAX_KEEP_TOKENS * CHARS_PER_TOKEN, before * MAX_KEEP_RATIO);
  let after = before;
  for (const [index, level] of LEVELS.entries()) {
    const archive = archivePath ? { path: archivePath, items: [] } : null;
    const pruned = applyLevel(messages, { ...level, archive });
    after = charsOf(pruned);
    if (after <= limit) return { kept: true, messages: pruned, before, after, level: index, archive };
  }
  return { kept: false, messages: null, before, after, level: LEVELS.length, archive: null };
}
