import { KIASI_SCRIPT, QUIET_EVENT_NAME, QUIET_RULES } from './constants.js';
import { interpreterOrder, withInterpreter, looksMissing, rememberInterpreter } from './python.js';

// True when index sits inside a single or double quoted stretch, so `git commit -m "fix npm install --loglevel=warn --no-fund --no-audit"` is left alone.
function insideQuotes(text, index) {
  let quote = null;
  for (let i = 0; i < index; i += 1) {
    const char = text[i];
    if (char === '\\' && quote !== "'") i += 1;
    else if (quote) quote = char === quote ? null : quote;
    else if (char === '"' || char === "'") quote = char;
  }
  return quote !== null;
}

function firstOutsideQuotes(text, pattern) {
  const all = new RegExp(pattern.source, pattern.flags.includes('g') ? pattern.flags : `${pattern.flags}g`);
  for (const found of text.matchAll(all)) if (!insideQuotes(text, found.index)) return found;
  return null;
}

export function quietCommand(command) {
  let quieted = command;
  const applied = [];
  for (const rule of QUIET_RULES) {
    const found = firstOutsideQuotes(quieted, rule.match);
    if (!found || rule.skip.test(quieted)) continue;
    const end = found.index + found[0].length;
    quieted = `${quieted.slice(0, end)} ${rule.flags}${quieted.slice(end)}`;
    applied.push(rule.name);
  }
  return { command: quieted, applied };
}

async function logQuiet($, before, after, applied) {
  try {
    const sessionId = await $.session.id().catch(() => null);
    const payload = JSON.stringify({ hook_event_name: QUIET_EVENT_NAME, session_id: sessionId, before, after, applied });
    await runPython($, ['python3', `${$.plugin.root}/scripts/${KIASI_SCRIPT}`], { stdin: payload });
  } catch (error) {
    $.ui.log(`kiasi quiet log failed: ${error.message}`, { to: 'debug' });
  }
}

export function registerQuiet(on) {
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const { command, applied } = quietCommand(String(e.command || ''));
    if (!applied.length) return next(e);
    await logQuiet($, e.command, command, applied);
    return next({ ...e, command });
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
