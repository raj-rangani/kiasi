import { interpreterOrder, withInterpreter, looksMissing, rememberInterpreter } from './python.js';
import {
  SEARCH_SCRIPT, SEARCH_TOOL_FULL_NAME, SEARCH_DEFAULT_LIMIT, SEARCH_MAX_LIMIT, SEARCH_TIMEOUT_MS,
} from './constants.js';

function limitOf(input) {
  const asked = Number(input.limit) || SEARCH_DEFAULT_LIMIT;
  return String(Math.min(SEARCH_MAX_LIMIT, Math.max(1, Math.floor(asked))));
}

export function searchArgv(root, input) {
  // A quoted phrase stays one term, quotes included; search.py reads it as an in-order phrase.
  const words = String(input.query || '').match(/"[^"]*"|\S+/g) || [];
  if (!words.length) return null;
  return ['python3', `${root}/scripts/${SEARCH_SCRIPT}`, '-n', limitOf(input), '--', ...words];
}

async function runSearch($, input) {
  const argv = searchArgv($.plugin.root, input);
  if (!argv) return { deny: 'search needs a query' };
  const { exitCode, stdout, stderr } = await runPython($, argv, { timeoutMs: SEARCH_TIMEOUT_MS });
  if (exitCode !== 0) return { result: `search failed (exit ${exitCode}): ${stderr.trim()}`, isError: true };
  return { result: stdout.trim(), isReadOnly: true };
}

export function registerSearch(on) {
  on('tool.call', { tool: SEARCH_TOOL_FULL_NAME }, ($, e) => runSearch($, e));
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
