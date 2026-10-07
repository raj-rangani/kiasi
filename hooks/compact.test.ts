import { test, expect } from 'claude-code/testing';
import type { SessionCompactInput } from 'claude-code';
import { pruneTranscript, charsOf } from './prune.js';
import { searchArgv } from './search.js';
import { KIASI_MARKER_PREFIXES, RESULT_HEAD_CHARS, ERROR_KEEP_CHARS, SEARCH_MAX_LIMIT } from './constants.js';

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

// Steps of a three-call parallel batch in the order Claude Code wrote them: Ca is call a, Ra is its result.
function batch(steps: string): any[] {
  return steps.split(' ').map(([kind, id]) => (kind === 'C'
    ? { role: 'assistant', text: id === 'a' ? 'Running three checks.' : '', toolUses: [{ tool_use_id: `toolu_${id}`, tool: 'Bash', input: { command: `sleep 3 && cat ${id}.txt` }, text: big(`${id} out `, 6000) }], handle: `call_${id}` }
    : { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: `toolu_${id}`, text: big(`${id} out `, 6000), isError: false }], handle: `result_${id}` }));
}

const shape = (message: any) => `${message.role[0]}:${[...message.toolUses, ...(message.toolResults || [])].map((item: any) => item.tool_use_id.slice(6)).join(' ')}`;

test('a pruned parallel batch keeps every call ahead of its result, whatever order the results were written in', () => {
  const layouts: Record<string, string[]> = {
    'Ca Cb Cc Rc Ra Rb': ['a:a b c', 'u:c', 'u:a', 'u:b'],
    'Ca Cb Ra Cc Rb Rc': ['a:a b c', 'u:a', 'u:b', 'u:c'],
    'Ca Ra Cb Rb Cc Rc': ['a:a', 'u:a', 'a:b', 'u:b', 'a:c', 'u:c'],
  };
  for (const [steps, expected] of Object.entries(layouts)) {
    const [prompt, ...rounds] = fixture(10);
    const result = pruneTranscript([prompt, ...batch(steps), ...rounds]);
    expect(result.kept).toBe(true);
    expect(result.messages!.slice(1, 1 + expected.length).map(shape)).toEqual(expected);
    expect(result.messages![1].handle).toBeUndefined();
    expect(result.messages![1].text).toBe('Running three checks.');
    expect(result.messages![1].toolUses[0].text).toContain('[kiasi pruned');
  }
});

test('the recent messages kept as they are never start inside a parallel batch', () => {
  for (const [steps, tail] of [['Ca Cb Cc Rc Ra Rb', 3], ['Ca Cb Ra Cc Rb Rc', 5]] as const) {
    const then = Array.from({ length: tail }, (_, i) => ({ role: i % 2 ? 'user' : 'assistant', text: `then ${i}`, toolUses: [], handle: `then_${i}` }));
    const messages = [...fixture(30).slice(0, -1), ...batch(steps), ...then];
    const result = pruneTranscript(messages);
    expect(result.kept).toBe(true);
    expect(result.level).toBe(0);
    expect(result.messages!.slice(-6 - tail)).toEqual(messages.slice(-6 - tail));
    expect(result.messages![result.messages!.length - 7 - tail].handle).toBeUndefined();
  }
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

function writes(from: number, count: number) {
  return Array.from({ length: count }, (_, k) => from + k).flatMap((i) => [
    { role: 'assistant', text: 'Writing.', toolUses: [{ tool_use_id: `w${i}`, tool: 'Write', input: { file_path: `/repo/f${i}.py`, content: big('code\n', 9000) }, text: 'ok' }], handle: `a${i}` },
    { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: `w${i}`, text: 'File written', isError: false }], handle: `u${i}` },
  ]);
}

test('replaces edit content with a size marker and keeps the path', () => {
  const result = pruneTranscript([{ role: 'user', text: 'go', toolUses: [], handle: 'h0' }, ...writes(0, 12)]);
  expect(result.kept).toBe(true);
  expect(result.messages![1].toolUses[0].input.file_path).toBe('/repo/f0.py');
  expect(result.messages![1].toolUses[0].input.content).toBe('[9000 chars]');
});

test('a second compaction keeps the sizes the first one wrote', () => {
  const first = pruneTranscript([{ role: 'user', text: 'go', toolUses: [], handle: 'h0' }, ...writes(0, 12)]);
  const second = pruneTranscript([...first.messages!, ...writes(12, 12)]);
  expect(second.kept).toBe(true);
  expect(second.messages![1].toolUses[0].input.content).toBe('[9000 chars]');
});

test('the hook defers to the engine when the event carries no message list', async ($) => {
  await expect($.session.compact({} as SessionCompactInput)).rejects.toThrow();
});

test('the search tool builds the script call from the flat tool arguments', () => {
  expect(searchArgv('/root/plugin', { query: 'toolu_01 login  error', limit: 99 })).toEqual(['python3', '/root/plugin/scripts/search.py', '-n', String(SEARCH_MAX_LIMIT), '--', 'toolu_01', 'login', 'error']);
  expect(searchArgv('/root/plugin', { query: 'x' })).toEqual(['python3', '/root/plugin/scripts/search.py', '-n', '8', '--', 'x']);
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

test('a text that only mentions the marker is cut, and one an earlier compaction cut is left whole', () => {
  const mention = `${big('row ', 20000)}\nexport const MARKER_PREFIX = '[kiasi pruned';\n${big('row ', 20000)}`;
  const earlier = `${big('row ', 300)}\n[kiasi pruned 9000 chars at compaction]\n`;
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'assistant', text: 'ok', toolUses: [
      { tool_use_id: 't1', tool: 'Bash', input: { command: 'cat hooks/constants.js' }, text: mention },
      { tool_use_id: 't2', tool: 'Bash', input: { command: 'ls' }, text: earlier },
    ] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const result = pruneTranscript(messages);
  expect(result.kept).toBe(true);
  expect(result.messages![1].toolUses[0].text.length).toBeLessThan(mention.length / 2);
  expect(result.messages![1].toolUses[1].text).toBe(earlier);
});

test('a subagent hand-back report is archived, not cut to a stub', () => {
  const report = `<task-notification>\n${big('finding ', 3000)}\n</task-notification>`;
  const injected = `<system-reminder>\n${big('rule ', 5000)}\n</system-reminder>`;
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'user', text: report, toolUses: [] },
    { role: 'user', text: injected, toolUses: [] },
    { role: 'assistant', text: 'ok', toolUses: [{ tool_use_id: 't1', tool: 'Bash', input: { command: 'ls' }, text: big('row ', 40000) }] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const result = pruneTranscript(messages, '/d/outputs/compact-c.txt');
  expect(result.kept).toBe(true);
  expect(result.messages![1].text).toBe(report);
  expect(result.messages![2].text).toContain('full text is #');
  expect(result.archive!.items).toContain(injected);
});

const handbacks: Record<string, (body: string) => string> = {
  'another session': (body) => `Another Claude session sent a message:\n<agent-message from="worker">\n${body.replace(/^/gm, '  ')}\n</agent-message>`,
  'bare agent-message': (body) => `<agent-message from="worker">\n${body}\n</agent-message>`,
  'system-reminder wrapped': (body) => `<system-reminder>\n[SYSTEM NOTIFICATION - NOT USER INPUT]\n<task-notification>\n${body}\n</task-notification>\n</system-reminder>`,
};

for (const [name, wrap] of Object.entries(handbacks)) {
  test(`a ${name} hand-back is archived, or trimmed with a note when there is no archive`, () => {
    const report = wrap(big('finding ', 9000));
    const messages = [
      { role: 'user', text: 'task', toolUses: [] },
      { role: 'user', text: report, toolUses: [] },
      ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
    ];
    const withArchive = pruneTranscript(messages, '/d/outputs/compact-c.txt');
    expect(withArchive.messages![1].text).toContain('full text is #');
    expect(withArchive.archive!.items).toContain(report);
    const without = pruneTranscript(messages, null).messages![1].text;
    expect(without.startsWith(report.slice(0, 4000))).toBe(true);
    expect(without.split('\n').pop()).toBe(`[kiasi trimmed ${report.length - 4000} chars of a subagent report; it was not archived]`);
  });

  test(`a short ${name} hand-back is untouched`, () => {
    const report = wrap(big('finding ', 1000));
    const messages = [
      { role: 'user', text: 'task', toolUses: [] },
      { role: 'user', text: report, toolUses: [] },
      { role: 'assistant', text: 'ok', toolUses: [{ tool_use_id: 't1', tool: 'Bash', input: { command: 'ls' }, text: big('row ', 40000) }] },
      ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
    ];
    expect(pruneTranscript(messages, '/d/outputs/compact-c.txt').messages![1].text).toBe(report);
  });
}

test('a long plain user message that is not a hand-back is still cut to its head', () => {
  const plain = big('word ', 9000);
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'user', text: plain, toolUses: [] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const text = pruneTranscript(messages, null).messages![1].text;
  expect(text.length).toBeLessThan(700);
  expect(text).not.toContain('subagent report');
});

test('every Kiasi last-line marker is left whole, and a cut text keeps its saved-at pointer', () => {
  const digest = `${big('row ', 300)}\n[kiasi kept the first 1200 of 9000 chars of this output; full text saved at /d/outputs/x.txt, read it with offset if you need more]`;
  const colour = `${big('row ', 300)}\n[kiasi removed colour codes from this output]`;
  const pointer = '[kiasi trimmed 5 of 90 lines here; full output saved at /d/outputs/y.txt]';
  const tailed = `${big('row ', 3000)}\n${pointer}\n${big('tail ', 1000)}`;
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'assistant', text: 'ok', toolUses: [
      { tool_use_id: 't1', tool: 'Bash', input: { command: 'a' }, text: digest },
      { tool_use_id: 't2', tool: 'Bash', input: { command: 'b' }, text: colour },
      { tool_use_id: 't3', tool: 'Bash', input: { command: 'c' }, text: tailed },
    ] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const result = pruneTranscript(messages);
  const uses = result.messages![1].toolUses;
  expect(uses[0].text).toBe(digest);
  expect(uses[1].text).toBe(colour);
  expect(uses[2].text.endsWith(pointer)).toBe(true);
});

test('strings inside a MultiEdit edits array are shrunk too', () => {
  const edits = [{ old_string: big('a', 5000), new_string: big('b', 5000) }];
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'assistant', text: 'ok', toolUses: [{ tool_use_id: 't1', tool: 'MultiEdit', input: { file_path: '/f.py', edits }, text: 'done' }] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const result = pruneTranscript(messages);
  const shrunk = result.messages![1].toolUses[0].input.edits[0];
  expect(shrunk.old_string).toBe('[5000 chars]');
  expect(shrunk.new_string).toBe('[5000 chars]');
});

test('a quoted phrase stays one search term', () => {
  const argv = searchArgv('/r', { query: 'login "timeout waiting" -bash:' })!;
  expect(argv.slice(argv.indexOf('--') + 1)).toEqual(['login', '"timeout waiting"', '-bash:']);
});

test('a text whose last line only starts "[kiasi " is cut, and every real marker form is left whole', () => {
  const cutOf = (last: string) => {
    const text = `${big('row ', 3000)}\n${last}`;
    const messages = [
      { role: 'user', text: 'task', toolUses: [] },
      { role: 'assistant', text: 'ok', toolUses: [{ tool_use_id: 't1', tool: 'Bash', input: { command: 'a' }, text }, { tool_use_id: 't2', tool: 'Bash', input: { command: 'b' }, text: big('pad ', 30000) }] },
      ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
    ];
    return { text, out: pruneTranscript(messages).messages![1].toolUses[0].text };
  };
  const notes = cutOf('[kiasi 0.4.0] notes');
  expect(notes.out).not.toBe(notes.text);
  expect(notes.out).toContain('[kiasi pruned');
  const forms = [
    '[kiasi pruned 9000 chars at compaction]',
    '[kiasi kept the first 1200 of 9000 chars of this output; full text saved at /d/x.txt]',
    '[kiasi kept 4 failure lines of 90; full output saved at /d/x.txt]',
    '[kiasi trimmed 5 of 90 lines here; full output saved at /d/x.txt]',
    '[kiasi collapsed 3 repeats of the line above]',
    '[kiasi removed colour codes and repeated lines; original output saved at /d/x.txt]',
  ];
  expect(forms.length).toBe(KIASI_MARKER_PREFIXES.length + 1);
  for (const form of forms) {
    const { text, out } = cutOf(form);
    expect(out).toBe(text);
  }
});

test('a cut text keeps every saved-at pointer line of its dropped tail, in order and without repeats', () => {
  const one = '[kiasi trimmed 5 of 90 lines here; full output saved at /d/outputs/a.txt]';
  const two = '[kiasi kept 4 failure lines of 90; full output saved at /d/outputs/b.txt]';
  const text = `${big('row ', 3000)}\n${one}\n${big('mid ', 100)}\n${two}\n${one}\n${big('tail ', 1000)}`;
  const messages = [
    { role: 'user', text: 'task', toolUses: [] },
    { role: 'assistant', text: 'ok', toolUses: [{ tool_use_id: 't1', tool: 'Bash', input: { command: 'a' }, text }] },
    ...Array.from({ length: 10 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `m${i}`, toolUses: [] })),
  ];
  const out = pruneTranscript(messages).messages![1].toolUses[0].text as string;
  expect(out.endsWith(`\n${one}\n${two}`)).toBe(true);
});

test('a bare -- in the query is passed as a word after the separator', () => {
  const argv = searchArgv('/r', { query: '--' })!;
  expect(argv.slice(argv.indexOf('--') + 1)).toEqual(['--']);
});
