# Upstream report: `session.compact` hooks and parallel tool batches

Reported to Anthropic with `/feedback` on 2026-10-06, receipt `d15b4c11-b128-422e-acea-c0b0cd68ac5b`. This is the full write-up, which can also go to a GitHub issue on `anthropics/claude-code`. Nothing here depends on Kiasi: each case is a plugin of about ten lines.

## Summary

Tested on Claude Code 2.1.291 with function hooks on. A `session.compact` hook that drops handles or changes messages runs into three problems:

1. **A message returned without its handle gets a new message id.** Claude Code stores a parallel tool batch as one assistant entry per call, all sharing one message id, and joins them into one message when the session loads. A hook that returns the batch without handles gives each call its own id, so the calls load as separate assistant messages. Every call except the last then loses its result, which is replaced with `[Tool result missing due to internal error]`. Neither the user nor the hook sees an error.
2. **Keeping the handle does not keep the id of a changed message.** A changed assistant message is written as a new entry with a new message id and no `requestId`, even when its handle is kept. A hook therefore cannot edit one call of a batch, for example to shorten its input, and keep the batch together. Changing a result message while keeping its handle works.
3. **A result kept unchanged after its call changed points back into the old history.** It is written after the compact boundary, but its `parentUuid` and `sourceToolAssistantUUID` still name the pre-compaction call entry. A resumed session follows that chain into the old history and never reaches the boundary, so it loads the uncompacted messages and the compaction is silently undone.

## Environment

- Claude Code 2.1.291, native build, Linux x86-64.
- `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`. Each test plugin is loaded through `CLAUDE_CODE_PLUGIN_DIRS`.

## What the hook receives

A batch of three parallel Bash calls whose results arrive after all three calls are written reaches the hook as six messages. That arrival order is the usual one for slow tools: Bash, web search and fetch, subagents.

```
assistant  handle=<entry uuid>  toolUses=[a]
assistant  handle=<entry uuid>  toolUses=[b]
assistant  handle=<entry uuid>  toolUses=[c]
user       handle=<entry uuid>  toolResults=[a]
user       handle=<entry uuid>  toolResults=[b]
user       handle=<entry uuid>  toolResults=[c]
```

Each transcript entry is its own message, and its handle is the entry's uuid. Nothing in the messages says that the three calls belong to one batch.

## Tests

Every test runs the same session. The model makes three parallel `sleep 3 && cat <file>` calls, then a fourth call on its own, then four short filler turns. Then come `/compact` and a resumed turn that asks the model to quote the first line of each result. Each file starts with a random marker.

| Hook returns | Batch's message ids after compaction | Chain the resumed session loads | Model quoted |
|---|---|---|---|
| every message, handle removed | 3 new ids | starts at the boundary | a, b: `[Tool result missing due to internal error]`; c, d: correct |
| every message with its handle, results edited | original id kept | starts at the boundary | all four correct; the edits were written |
| every message with its handle, calls edited | 3 new ids, no `requestId` | starts at line 2 of the old history: 25 pre-compaction entries, boundary not on it | all four correct, because the old history was loaded |

The chain is followed by `parentUuid` from the last entry of the transcript.

## Minimal repro, finding 1

`.claude-plugin/plugin.json`
```json
{"name": "compact-handle-repro", "version": "0.0.1", "description": "Minimal session.compact repro."}
```

`hooks/hooks.json`
```json
{"hooks": {}, "modules": ["./repro.js"]}
```

`hooks/repro.js`
```js
export function register(on) {
  on('session.compact', async ($, e, next) => {
    if (!Array.isArray(e.messages)) return next(e);
    return { messages: e.messages.map(({ handle, ...message }) => message) };
  });
}
```

Steps, in a folder holding `a.txt`, `b.txt`, `c.txt` and `d.txt`, each with a distinct first line:

1. `export CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 CLAUDE_CODE_PLUGIN_DIRS=/path/to/compact-handle-repro`
2. `claude -p --model haiku --allowedTools 'Bash(sleep:*)' 'Bash(cat:*)' --output-format json "In ONE response, call the Bash tool three times in parallel, with exactly these commands: 'sleep 3 && cat a.txt', 'sleep 3 && cat b.txt' and 'sleep 3 && cat c.txt'. After those three results come back, call Bash once more, on its own, with exactly: 'cat d.txt'. Then reply with exactly: OK"`
3. Four times: `claude -p --resume <session id> "Reply with exactly: filler <n>"`
4. `claude -p --resume <session id> "/compact"`
5. `claude -p --resume <session id> "Do not use any tools. Quote verbatim the first line of each of the four tool results as you see it now."`

## Repro, findings 2 and 3

Same steps, with this return line in `hooks/repro.js`:

```js
return { messages: e.messages.map((message) => (message.toolUses?.length ? { ...message, toolUses: message.toolUses.map((use) => ({ ...use, input: { ...use.input, description: 'edited by hook' } })) } : message)) };
```

After step 4, the three call entries written after the boundary have three different message ids. The result entries after the boundary have `parentUuid` set to the call entries before it.

## Expected

- A hook that returns the messages it was given, minus their handles, gets back an equivalent transcript.
- A changed message whose handle is kept keeps its message id and `requestId`.
- Every entry written after the boundary chains back to the boundary.

## Suggested fixes

1. Keep the message id and `requestId` of a changed assistant message whose handle is kept. Or put a batch id on hook messages and honour it on returned messages.
2. When a kept entry's parent was changed, point its `parentUuid` and `sourceToolAssistantUUID` at the new entry, as already happens when the parent is unchanged.
3. Check the returned list before writing it: every tool use must be directly followed by its result. If the check fails, fail the hook and fall back to the default compaction instead of inserting placeholders.
4. Document what a handle keeps and what it does not.

## Impact

On one machine over the last 7 days, before the workaround, a pruning compaction hook lost results in 46 of 61 compactions, 488 results in all. By tool: web search 229, Bash 71, fetch 63, Edit 61, Read 43, Write 11. The model re-ran 27 of the lost calls with identical input, and the 72 lost Edit and Write results read as failed edits.

## Workaround

Rebuild each batch as one assistant message holding all its calls, ahead of all its results, and keep the batch open while any call still has a result to come. Claude Code writes that merged message as one entry. Start the part kept unchanged at a message that has no call still waiting for its result, so a kept result never outlives its call. With both rules, the same test keeps every result and the resumed chain starts at the boundary.

## Related: read records after a hook compaction

Mentioned as the cause in a separate `/feedback` report on 2026-10-06, receipt `4c23646f-f8fe-4126-9298-729df97e3c89`.

When a `session.compact` hook replaces the messages, Claude Code clears its record of which files the session has read or written and restores none. Its own summary compaction clears the same record but then re-attaches the most recent files. Write and Edit refuse to overwrite a file with no record ("File has not been read yet") unless it is a file Claude Code may read without asking, which covers the working directories. So after a hook compaction, a file outside them that the session wrote a minute earlier cannot be overwritten until it is read again.

Found when Kiasi's turn checklists, kept under `~/.claude/plugins/data/`, could not be updated after a compaction: 2 refusals in 7 days, while 21 overwrites of project files after the same kind of compaction went through. Suggested fix: after a hook compaction, rebuild the record from the returned messages, as a resumed session already does.
