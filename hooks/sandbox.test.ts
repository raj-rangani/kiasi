import { test, expect } from 'claude-code/testing';
import { runArgv, distillArgv, fetchArgv, timeoutOf, registerSandbox } from './sandbox.js';
import {
  RUN_TOOL_FULL_NAME, DISTILL_TOOL_FULL_NAME, FETCH_TOOL_FULL_NAME,
  RUN_INPUT_SCHEMA, DISTILL_INPUT_SCHEMA, FETCH_INPUT_SCHEMA,
} from './constants.js';

test('run builds the argv and needs a command', () => {
  expect(runArgv('/root/plugin', { command: 'npm test' })).toEqual(
    ['python3', '/root/plugin/scripts/sandbox.py', 'run', '--command=npm test', '--timeout', '120'],
  );
  expect(runArgv('/root/plugin', { command: '   ' })).toBe(null);
});

test('distill builds the argv, repeats --file and needs code', () => {
  expect(distillArgv('/root/plugin', { code: 'print(1)', files: ['a.txt', 'b.txt'] })).toEqual(
    ['python3', '/root/plugin/scripts/sandbox.py', 'distill', '--language', 'python', '--code=print(1)', '--timeout', '120', '--file', 'a.txt', '--file', 'b.txt'],
  );
  expect(distillArgv('/root/plugin', { code: 'console.log(1)', language: 'node' })).toContain('node');
  expect(distillArgv('/root/plugin', { code: '' })).toBe(null);
});

test('fetch builds the argv, repeats --find and needs a url', () => {
  expect(fetchArgv('/root/plugin', { url: 'https://docs.test/page', find: ['needle', 'rate limit'] })).toEqual(
    ['python3', '/root/plugin/scripts/sandbox.py', 'fetch', '--url=https://docs.test/page', '--timeout', '120', '--find=needle', '--find=rate limit'],
  );
  expect(fetchArgv('/root/plugin', { url: '' })).toBe(null);
});

test('timeout is clamped to one second up to the maximum', () => {
  expect(timeoutOf({})).toBe(120);
  expect(timeoutOf({ timeout_s: 0 })).toBe(120);
  expect(timeoutOf({ timeout_s: -5 })).toBe(1);
  expect(timeoutOf({ timeout_s: 9999 })).toBe(590);
});

// The same check Claude Code applies to $.process.run: init.timeoutMs is a whole number of ms, 1 to 600000.
const host = {
  plugin: { root: '/root/plugin' },
  session: { id: async () => 'aaaaaaaa-1111-2222-3333-444444444444' },
  process: {
    run: async (_argv: string[], init: any) => {
      const ms = init.timeoutMs;
      if (!(Number.isInteger(ms) && ms > 0 && ms <= 600000)) throw new Error(`process.run: init.timeoutMs is a whole number of ms, 1 to 600000 (got ${ms})`);
      return { exitCode: 0, stdout: 'ok', stderr: '' };
    },
  },
};

test('the longest timeout the schemas allow, or more, passes the host check on process.run', async () => {
  const handlers: Record<string, any> = {};
  registerSandbox((_event: string, filter: any, handler: any) => { handlers[filter.tool] = handler; });
  const tools: [string, any, object][] = [
    [RUN_TOOL_FULL_NAME, RUN_INPUT_SCHEMA, { command: 'npm test' }],
    [DISTILL_TOOL_FULL_NAME, DISTILL_INPUT_SCHEMA, { code: 'print(1)' }],
    [FETCH_TOOL_FULL_NAME, FETCH_INPUT_SCHEMA, { url: 'https://docs.test/page' }],
  ];
  for (const [tool, schema, input] of tools) {
    const longest = schema.properties.timeout_s.maximum;
    for (const timeout_s of [longest, longest + 1000]) {
      expect(await handlers[tool](host, { ...input, timeout_s })).toEqual({ result: 'ok', isError: false });
    }
  }
});
