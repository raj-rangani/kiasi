import { pruneTranscript } from './prune.js';
import { CHARS_PER_TOKEN, KIASI_SCRIPT, LOG_EVENT_NAME, TOAST_MS, ARCHIVE_PATH_EVENT_NAME, ARCHIVE_EVENT_NAME } from './constants.js';

const tokens = (chars) => Math.round(chars / CHARS_PER_TOKEN);

async function runKiasi($, eventName, record) {
  const payload = JSON.stringify({ hook_event_name: eventName, ...record });
  try {
    return await $.process.run(['python3', `${$.plugin.root}/scripts/${KIASI_SCRIPT}`], { stdin: payload });
  } catch (error) {
    $.ui.log(`kiasi ${eventName} failed: ${error.message}`, { to: 'debug' });
    return null;
  }
}

async function archivePath($, session) {
  const result = await runKiasi($, ARCHIVE_PATH_EVENT_NAME, { session_id: session });
  try {
    return JSON.parse(result?.stdout || '{}').path || null;
  } catch (error) {
    return null;
  }
}

async function sessionId($) {
  try {
    return await $.session.id();
  } catch (error) {
    return null;
  }
}

export function registerCompact(on) {
  on('session.compact', async ($, e, next) => {
    if (!Array.isArray(e.messages)) return next(e);
    const session = await sessionId($);
    const result = pruneTranscript(e.messages, await archivePath($, session));
    const record = {
      session_id: session,
      trigger: e.trigger,
      agent_id: e.agentId || null,
      mode: result.kept ? 'prune' : 'summary',
      level: result.level,
      messages: e.messages.length,
      tokens_before: tokens(result.before),
      tokens_after: tokens(result.after),
      archived: result.archive ? result.archive.items.length : 0,
    };
    if (result.archive?.items.length) await runKiasi($, ARCHIVE_EVENT_NAME, { path: result.archive.path, items: result.archive.items });
    await runKiasi($, LOG_EVENT_NAME, record);
    if (!result.kept) return next(e);
    if (e.trigger !== 'precompute') {
      $.ui.toast(`kiasi pruned the transcript: ${tokens(result.before)} to ${tokens(result.after)} tokens, level ${result.level}`, { timeoutMs: TOAST_MS });
    }
    return { messages: result.messages };
  });
}
