import { test, expect } from 'claude-code/testing';
import { pruneTranscript } from './prune.js';
import { PRUNE_KEEP_EXCHANGES, PRUNE_OLD_PROMPT_CHARS, PRUNE_OUTCOME_CHARS } from './constants.js';

const big = (seed: string, size: number) => seed.repeat(Math.ceil(size / seed.length)).slice(0, size);
const ARCHIVE = '/d/outputs/compact-x.txt';

function exchange(n: number, extra: any[] = [], prompt = `Task number ${n}`) {
  const id = `toolu_${n}`;
  return [
    { role: 'user', text: prompt, toolUses: [] },
    { role: 'assistant', text: `Looking at ${n}.`, toolUses: [{ tool_use_id: id, tool: 'Read', input: { file_path: `/repo/f${n}.py` }, text: big(`row ${n} `, 30000) }] },
    { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: id, text: big(`row ${n} `, 30000), isError: false }] },
    ...extra,
    { role: 'assistant', text: `Outcome ${n}: done.\nA second line that is dropped.`, toolUses: [] },
  ];
}

const conversation = (count: number, extras: Record<number, any[]> = {}) => Array.from({ length: count }, (_, i) => exchange(i + 1, extras[i + 1] || [])).flat();
const prompts = (messages: any[]) => messages.filter((m) => m.role === 'user' && m.text.startsWith('Task number')).length;

test('the last exchanges stay word for word and older ones shrink to prompt, outcome and pointer', () => {
  const messages = conversation(6);
  const result = pruneTranscript(messages, ARCHIVE);
  expect(result.kept).toBe(true);
  const out = result.messages!;
  expect(out.slice(0, 6).map((m: any) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant', 'user', 'assistant']);
  expect(out[0].text).toBe('Task number 1');
  expect(out[1].text.split('\n')[0]).toBe('Outcome 1: done.');
  expect(out[1].text).toContain(`[kiasi reduced exchange 1`);
  expect(out[1].text).toContain(`in ${ARCHIVE}]`);
  expect(out[1].toolUses).toEqual([]);
  const recent = out.slice(-(PRUNE_KEEP_EXCHANGES * 4));
  expect(prompts(recent)).toBe(PRUNE_KEEP_EXCHANGES);
  expect(recent.filter((m: any) => m.role === 'assistant' && m.text.startsWith('Looking')).length).toBe(PRUNE_KEEP_EXCHANGES);
  expect(recent[recent.length - 1].text).toBe('Outcome 6: done.\nA second line that is dropped.');
  expect(result.archive!.items[0]).toContain('--- exchange 1 ---');
  expect(result.archive!.items.filter((item: string) => item.startsWith('--- exchange')).length).toBe(3);
});

test('a long old prompt and outcome are cut to their limits', () => {
  const messages = [...exchange(1, [], big('p', 5000)), ...conversation(5).map((m: any) => m)];
  messages[4 - 1 + 1] = { role: 'assistant', text: big('o', 900), toolUses: [] };
  const result = pruneTranscript(messages, ARCHIVE);
  expect(result.messages![0].text.length).toBe(PRUNE_OLD_PROMPT_CHARS + 4);
  expect(result.messages![1].text.split('\n')[0].length).toBeLessThanOrEqual(PRUNE_OUTCOME_CHARS);
});

test('a hand-back inside an old exchange stays whole or is archived with its own pointer', () => {
  const small = `<task-notification>\n${big('finding ', 1500)}\n</task-notification>`;
  const large = `<task-notification>\n${big('detail ', 6000)}\n</task-notification>`;
  const result = pruneTranscript(conversation(6, { 1: [{ role: 'user', text: small, toolUses: [] }], 2: [{ role: 'user', text: large, toolUses: [] }] }), ARCHIVE);
  const out = result.messages!;
  expect(out.map((m: any) => m.text)).toContain(small);
  const trimmed = out.find((m: any) => m.text.startsWith('<task-notification>\ndetail'));
  expect(trimmed.text).toContain('full text is #');
  expect(result.archive!.items).toContain(large);
  expect(result.archive!.items.some((item: string) => item.startsWith('--- exchange 2 ---') && !item.includes('detail '))).toBe(true);
});

test('pruning the pruned output changes nothing already reduced', () => {
  const first = pruneTranscript(conversation(7), ARCHIVE);
  const reduced = first.messages!.slice(0, 4 * 2);
  const later = [...first.messages!, ...conversation(3).map((m: any) => (m.text.startsWith('Task number') ? { ...m, text: `Later ${m.text}` } : m))];
  const second = pruneTranscript(later, '/d/outputs/compact-y.txt');
  expect(second.kept).toBe(true);
  expect(second.messages!.slice(0, reduced.length)).toEqual(reduced);
  expect(second.archive!.items.every((item: string) => !item.includes('Task number 1\n'))).toBe(true);
});

test('three exchanges or fewer are only tool-pruned, never reduced', () => {
  const result = pruneTranscript(conversation(PRUNE_KEEP_EXCHANGES), ARCHIVE);
  expect(prompts(result.messages!)).toBe(PRUNE_KEEP_EXCHANGES);
  expect(result.messages!.some((m: any) => (m.text || '').includes('[kiasi reduced exchange'))).toBe(false);
  expect(result.messages!.length).toBe(conversation(PRUNE_KEEP_EXCHANGES).length);
});

test('without an archive no exchange is reduced, so no pointer names a missing file', () => {
  const result = pruneTranscript(conversation(6), null as any);
  expect(result.messages!.some((m: any) => (m.text || '').includes('[kiasi reduced exchange'))).toBe(false);
});
