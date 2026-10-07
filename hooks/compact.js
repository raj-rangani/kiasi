import { pruneTranscript } from './prune.js';
import { interpreterOrder, withInterpreter, looksMissing, rememberInterpreter } from './python.js';
import { CHARS_PER_TOKEN, KIASI_SCRIPT, LOG_EVENT_NAME, TOAST_MS, ARCHIVE_PATH_EVENT_NAME, ARCHIVE_EVENT_NAME } from './constants.js';

const tokens = (chars) => Math.round(chars / CHARS_PER_TOKEN);

async function runKiasi($, eventName, record) {
  const payload = JSON.stringify({ hook_event_name: eventName, ...record });
  try {
    return await runPython($, ['python3', `${$.plugin.root}/scripts/${KIASI_SCRIPT}`], { stdin: payload });
  } catch (error) {
    $.ui.log(`kiasi ${eventName} failed: ${error.message}`, { to: 'debug' });
    return null;
  }
}

async function archivePath($, session, agentId) {
  const result = await runKiasi($, ARCHIVE_PATH_EVENT_NAME, { session_id: session, agent_id: agentId || null });
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
    let result = pruneTranscript(e.messages, await archivePath($, session, e.agentId));
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
    if (result.archive?.items.length) {
      const wrote = await runKiasi($, ARCHIVE_EVENT_NAME, { path: result.archive.path, items: result.archive.items });
      let written = false;
      try {
        written = JSON.parse(wrote?.stdout || '{}').written === true;
      } catch (error) {
        written = false;
      }
      // Markers must never name a file that was not written: prune again with no archive, so none is named.
      if (!written) {
        result = pruneTranscript(e.messages, null);
        record.archived = 0;
        record.tokens_after = tokens(result.after);
        record.level = result.level;
        record.mode = result.kept ? 'prune' : 'summary';
      }
    }
    await runKiasi($, LOG_EVENT_NAME, record);
    if (!result.kept) return next(e);
    if (e.trigger !== 'precompute') {
      $.ui.toast(`kiasi pruned the transcript: ${tokens(result.before)} to ${tokens(result.after)} tokens, level ${result.level}`, { timeoutMs: TOAST_MS });
    }
    return { messages: result.messages };
  });
}

// argv[0] is a placeholder: tries python3, python and py -3 until one exists, then sticks with it.
async function runPython($, argv, opts) {
  let last;
  for (const candidate of interpreterOrder()) {
    last = await $.process.run(withInterpreter(argv, candidate), opts);
    if (!looksMissing(last)) {
      rememberInterpreter(candidate);
      return last;
    }
  }
  return last;
}
