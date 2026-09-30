import { test, expect } from 'claude-code/testing';
import type { SessionCompactInput } from 'claude-code';
import { pruneTranscript, charsOf } from './prune.js';
import { searchArgv } from './search.js';
import { RESULT_HEAD_CHARS, ERROR_KEEP_CHARS, SEARCH_MAX_LIMIT } from './constants.js';

const big = (seed: string, size: number) => seed.repeat(Math.ceil(size / seed.length)).slice(0, size);

function fixture(rounds: number) {
  const messages: any[] = [{ role: 'user', text: 'Fix the login bug in auth.py', toolUses: [], handle: 'h0' }];
  for (let i = 0; i < rounds; i++) {
    const id = `toolu_${i}`;
    const isError = i % 5 === 4;
    messages.push({ role: 'assistant', text: `Reading file ${i}.`, toolUses: [{ tool_use_id: id, tool: 'Read', input: { file_path: `/repo/file${i}.py` }, text: big(`line ${i} `, 6000), ...(isError ? { isError: true } : {}) }], handle: `a${i}` });
    messages.push({ role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: id, text: big(`line ${i} `, 6000), isError }], handle: `u${i}` });
  }
  messages.push({ role: 'assistant', text: 'Done: edited auth.py, added verify_token.', toolUses: [], handle: 'last' });
  return messages;
}

test('prunes old tool results, keeps prompts, errors and the recent messages', () => {
  const messages = fixture(30);
  const result = pruneTranscript(messages);
  expect(result.kept).toBe(true);
  expect(result.messages!.length).toBe(messages.length);
  expect(result.messages![0].text).toBe('Fix the login bug in auth.py');
  expect(result.messages![0].handle).toBeUndefined();
  expect(result.messages![2].toolResults![0].text.length).toBeLessThan(RESULT_HEAD_CHARS + 60);
  expect(result.messages![2].toolResults![0].text).toContain('[kiasi pruned');
  expect(result.messages![10].toolResults![0].isError).toBe(true);
  expect(result.messages![10].toolResults![0].text.length).toBeGreaterThan(ERROR_KEEP_CHARS - 1);
  const last = result.messages![result.messages!.length - 1];
  expect(last.handle).toBe('last');
  expect(charsOf(result.messages!)).toBeLessThan(result.before / 2);
});

test('falls back to the summary when pruning cannot reach the target', () => {
  const messages = [
    { role: 'user', text: big('prompt ', 3000), toolUses: [], handle: 'h0' },
    { role: 'assistant', text: big('answer ', 3000), toolUses: [], handle: 'h1' },
  ];
  const result = pruneTranscript(messages);
  expect(result.kept).toBe(false);
  expect(result.messages).toBeNull();
});

test('replaces edit content with a size marker and keeps the path', () => {
  const messages: any[] = [{ role: 'user', text: 'go', toolUses: [], handle: 'h0' }];
  for (let i = 0; i < 12; i++) {
    messages.push({ role: 'assistant', text: 'Writing.', toolUses: [{ tool_use_id: `w${i}`, tool: 'Write', input: { file_path: `/repo/f${i}.py`, content: big('code\n', 9000) }, text: 'ok' }], handle: `a${i}` });
    messages.push({ role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: `w${i}`, text: 'File written', isError: false }], handle: `u${i}` });
  }
  const result = pruneTranscript(messages);
  expect(result.kept).toBe(true);
  expect(result.messages![1].toolUses[0].input.file_path).toBe('/repo/f0.py');
  expect(result.messages![1].toolUses[0].input.content).toBe('[9000 chars]');
});

test('the hook defers to the engine when the event carries no message list', async ($) => {
  await expect($.session.compact({} as SessionCompactInput)).rejects.toThrow();
});

test('the search tool builds the script call from the flat tool arguments', () => {
  expect(searchArgv('/root/plugin', { query: 'toolu_01 login  error', limit: 99 })).toEqual(['python3', '/root/plugin/scripts/search.py', 'toolu_01', 'login', 'error', '-n', String(SEARCH_MAX_LIMIT)]);
  expect(searchArgv('/root/plugin', { query: 'x' })).toEqual(['python3', '/root/plugin/scripts/search.py', 'x', '-n', '8']);
  expect(searchArgv('/root/plugin', { query: '  ' })).toBeNull();
});

test('the search tool refuses an empty query', async ($) => {
  const out: any = await $.tool.call({ tool: 'mcp__kiasi__search', query: '   ' });
  expect(out.deny).toBe('search needs a query');
});

test('a fourth level shortens long prompts and assistant text when the first three cannot halve the transcript', () => {
  const messages: any[] = [];
  for (let i = 0; i < 20; i++) {
    messages.push({ role: 'user', text: big(`prompt ${i} `, 3000), toolUses: [], handle: `u${i}` });
    messages.push({ role: 'assistant', text: big(`answer ${i} `, 500), toolUses: [], handle: `a${i}` });
  }
  const result = pruneTranscript(messages);
  expect(result.kept).toBe(true);
  expect(result.level).toBe(3);
  expect(result.messages![0].text).toContain('[kiasi pruned');
  expect(result.messages![result.messages!.length - 2].text.length).toBe(3000);
  expect(charsOf(result.messages!)).toBeLessThan(result.before / 2);
});

test('large cuts name their saved original and earlier markers are left whole', () => {
  const old = `short head\n[kiasi pruned 9000 chars at compaction; full text is #1 in /d/outputs/compact-a.txt]`;
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'assistant', text: 'ok', toolUses: [{ tool_use_id: 't1', tool: 'Bash', input: { command: 'ls' }, text: big('row ', 40000) }] },
    { role: 'user', text: old, toolUses: [] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const result = pruneTranscript(messages, '/d/outputs/compact-b.txt');
  expect(result.kept).toBe(true);
  expect(result.archive!.items.length).toBe(1);
  expect(result.messages[1].toolUses[0].text).toContain('full text is #1 in /d/outputs/compact-b.txt');
  expect(result.messages[2].text).toBe(old);
});
