import { test, expect } from 'claude-code/testing';
import { runArgv, distillArgv, timeoutOf } from './sandbox.js';

test('run builds the argv and needs a command', () => {
  expect(runArgv('/root/plugin', { command: 'npm test' })).toEqual(
    ['python3', '/root/plugin/scripts/sandbox.py', 'run', '--command', 'npm test', '--timeout', '120'],
  );
  expect(runArgv('/root/plugin', { command: '   ' })).toBe(null);
});

test('distill builds the argv, repeats --file and needs code', () => {
  expect(distillArgv('/root/plugin', { code: 'print(1)', files: ['a.txt', 'b.txt'] })).toEqual(
    ['python3', '/root/plugin/scripts/sandbox.py', 'distill', '--language', 'python', '--code', 'print(1)', '--timeout', '120', '--file', 'a.txt', '--file', 'b.txt'],
  );
  expect(distillArgv('/root/plugin', { code: 'console.log(1)', language: 'node' })).toContain('node');
  expect(distillArgv('/root/plugin', { code: '' })).toBe(null);
});

test('timeout is clamped to one second up to the maximum', () => {
  expect(timeoutOf({})).toBe(120);
  expect(timeoutOf({ timeout_s: 0 })).toBe(120);
  expect(timeoutOf({ timeout_s: -5 })).toBe(1);
  expect(timeoutOf({ timeout_s: 9999 })).toBe(600);
});
