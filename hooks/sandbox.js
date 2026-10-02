import {
  SANDBOX_SCRIPT, SANDBOX_DEFAULT_TIMEOUT_S, SANDBOX_MAX_TIMEOUT_S, SANDBOX_EXTRA_MS,
  RUN_TOOL_FULL_NAME, DISTILL_TOOL_FULL_NAME, FETCH_TOOL_FULL_NAME,
} from './constants.js';

export function timeoutOf(input) {
  const asked = Number(input.timeout_s) || SANDBOX_DEFAULT_TIMEOUT_S;
  return Math.min(SANDBOX_MAX_TIMEOUT_S, Math.max(1, Math.floor(asked)));
}

export function runArgv(root, input) {
  const command = String(input.command || '').trim();
  if (!command) return null;
  return ['python3', `${root}/scripts/${SANDBOX_SCRIPT}`, 'run', '--command', command, '--timeout', String(timeoutOf(input))];
}

export function distillArgv(root, input) {
  const code = String(input.code || '').trim();
  if (!code) return null;
  const language = input.language === 'node' ? 'node' : 'python';
  const files = Array.isArray(input.files) ? input.files.map(String).filter(Boolean) : [];
  return [
    'python3', `${root}/scripts/${SANDBOX_SCRIPT}`, 'distill',
    '--language', language, '--code', code, '--timeout', String(timeoutOf(input)),
    ...files.flatMap((file) => ['--file', file]),
  ];
}

export function fetchArgv(root, input) {
  const url = String(input.url || '').trim();
  if (!url) return null;
  const find = Array.isArray(input.find) ? input.find.map(String).filter(Boolean) : [];
  return [
    'python3', `${root}/scripts/${SANDBOX_SCRIPT}`, 'fetch',
    '--url', url, '--timeout', String(timeoutOf(input)),
    ...find.flatMap((word) => ['--find', word]),
  ];
}

async function runSandbox($, input, build, missing) {
  const argv = build($.plugin.root, input);
  if (!argv) return { deny: missing };
  const session = await $.session.id().catch(() => null);
  if (session) argv.push('--session', session);
  const timeoutMs = timeoutOf(input) * 1000 + SANDBOX_EXTRA_MS;
  const { exitCode, stdout, stderr } = await $.process.run(argv, { timeoutMs });
  if (exitCode !== 0 && !stdout.trim()) return { result: `sandbox failed (exit ${exitCode}): ${stderr.trim()}`, isError: true };
  return { result: stdout.trim(), isError: exitCode !== 0 };
}

export function registerSandbox(on) {
  on('tool.call', { tool: RUN_TOOL_FULL_NAME }, ($, e) => runSandbox($, e, runArgv, 'run needs a command'));
  on('tool.call', { tool: DISTILL_TOOL_FULL_NAME }, ($, e) => runSandbox($, e, distillArgv, 'distill needs code'));
  on('tool.call', { tool: FETCH_TOOL_FULL_NAME }, ($, e) => runSandbox($, e, fetchArgv, 'fetch needs a url'));
}
