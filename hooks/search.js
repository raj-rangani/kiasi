import {
  SEARCH_SCRIPT, SEARCH_TOOL_FULL_NAME, SEARCH_DEFAULT_LIMIT, SEARCH_MAX_LIMIT, SEARCH_TIMEOUT_MS,
} from './constants.js';

function limitOf(input) {
  const asked = Number(input.limit) || SEARCH_DEFAULT_LIMIT;
  return String(Math.min(SEARCH_MAX_LIMIT, Math.max(1, Math.floor(asked))));
}

export function searchArgv(root, input) {
  const words = String(input.query || '').split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  return ['python3', `${root}/scripts/${SEARCH_SCRIPT}`, ...words, '-n', limitOf(input)];
}

async function runSearch($, input) {
  const argv = searchArgv($.plugin.root, input);
  if (!argv) return { deny: 'search needs a query' };
  const { exitCode, stdout, stderr } = await $.process.run(argv, { timeoutMs: SEARCH_TIMEOUT_MS });
  if (exitCode !== 0) return { result: `search failed (exit ${exitCode}): ${stderr.trim()}`, isError: true };
  return { result: stdout.trim(), isReadOnly: true };
}

export function registerSearch(on) {
  on('tool.call', { tool: SEARCH_TOOL_FULL_NAME }, ($, e) => runSearch($, e));
}
