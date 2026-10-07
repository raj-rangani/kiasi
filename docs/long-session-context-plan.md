# Long sessions: where the context goes and what Kiasi can do next

Date: 2026-10-06. Companion to `turn-budget-pause-research.md`.

## Summary

- **Fixed: compaction dropped the results of parallel tool calls.** In the session that produced this document, 22 of 52 tool results disappeared from the model's context after Kiasi's compactions. Each was replaced by `[Tool result missing due to internal error]`. Every lost result came from a batch of parallel calls; single calls and the last call of each batch survived. Across this machine's last 7 days it hit 46 of 61 compactions and lost 488 results. Cause and fix (2026-10-06) are in finding 1.
- A typical request carries **118k tokens** of context (median). **About 46k of that is a fixed floor** that exists before the conversation starts: Claude Code's system prompt and tools (~30k) plus ~15k from this machine's setup (skills list, CLAUDE.md and memory, MCP tool names and instructions, agent list, hook text). The floor alone is about **17% of total cost**.
- Compaction only gets a session back to **~73k**. With a floor that high, compacting earlier does not pay: compacting at 150k costs 1–6% *more* than it saves.
- What does pay: **clear-and-resume between tasks** (handing off at 150k into a fresh ~48k session ≈ 6.6%), **cap runaway contexts at 200k** (~4%), and **lower the floor** (~0.4% per 1k tokens removed). These add up, because a lower floor makes every reset land lower.
- Kiasi's own report undercounts context. It counts `<synthetic>` messages (zero usage) as turns, so it shows a mean context of 28k instead of ~120k, and 0% high-context turns. Both the dashboard and the SessionStart budget line use these numbers.

## Data and method

- Sample: this machine; the 59 main conversations active in the last 7 days, with their full history; 6,073 model requests. Models: Opus 5.5, Fable 5.1 and Fable 5. The context numbers exclude subagent transcripts.
- Cost is in input-price units, using API price ratios: cache read 0.1, 1-hour cache write 2, 5-minute cache write 1.25, uncached input 1, output 5. Subscription limits weigh tokens in a similar way, but not identically.
- Context per request = input + cache write + cache read tokens, taken from each response's usage.
- Simulation: replay each session's context growth. When the simulated context passes a trigger, reset it to a floor and charge the cost of the reset: the floor is rewritten at 2x, and a summary-style compaction also pays for reading the context and 8k output tokens. Rework after a reset (re-reading files that were dropped) is not modelled, so the savings are upper bounds.
- Scripts, in this session's scratchpad (temporary): `/tmp/claude-1000/-var-www-html/9332ebb1-7b51-49f9-940d-9e34e18c8f47/scratchpad/` — `cache_audit.py`, `context_sources.py`, `context_floor.py` and `context_floor_lean.py` (the 55k and 48k floors). Their raw outputs were later lost to the bug in finding 1; the numbers were copied into this document's draft before that happened. Re-run the scripts to check them.

## Where the cost goes

| Part | Share of cost | Notes |
|---|---|---|
| Re-reading cached context | 47% | about 37% of this is the fixed floor |
| Writing to cache | 30% | 96% of writes use the 1-hour TTL (2x price) |
| Output (including thinking) | 23% | |

Cache writes by cause:
- new content each step: 59%
- first request after a compaction: 20%
- first request after more than 1 hour idle: 10%
- first request of a session: 6%
- rewrite with no idle (e.g. a model or effort switch): 5%

Context per request: mean 128k, median 118k, p90 164k, p99 435k, max 521k.

| Context size at the request | Share of re-read tokens |
|---|---|
| under 100k | 19% |
| 100–150k | 44% |
| 150–200k | 20% |
| 200–300k | 5% |
| 300–500k | 10% |
| 500k and over | 1% |

## Findings

### 1. Compaction drops the results of parallel tool calls

When Claude runs several tools in one response, Kiasi's prune-mode compaction keeps the result of only the last one. Claude Code fills each lost result with `[Tool result missing due to internal error]`, so after the compaction the model can no longer see those outputs.

Seen in this session (`9332ebb1`), across two compactions:
- 52 tool calls ran before the second compaction. 32 of them were in 9 parallel batches.
- 22 results were lost, all of them non-last results of a parallel batch. Of the 23 such results, only one survived (the first of three fetches in one batch).
- No single call, and no last call of a batch, lost its result.
- The lost results included three research-agent launches and the raw output of most of the analysis behind this document.

Confirmed:
- The transcript on disk (`~/.claude/projects/-var-www-html/9332ebb1-7b51-49f9-940d-9e34e18c8f47.jsonl`) still holds all 52 results, each linked into the `parentUuid` chain. The results are lost in the compaction round trip, not when they are recorded.
- `hooks/compact.js` hands Claude Code's `session.compact` messages to `pruneTranscript` and returns its output.
- `pruneTranscript` returns the most recent messages unchanged and rebuilds older ones as new objects (`rebuildUser` at `hooks/prune.js:52`, `rebuildAssistant` at `hooks/prune.js:71`). The rebuilt objects keep every tool use and result but drop the message's `handle`. That is intended: `hooks/compact.test.ts:27` asserts it, and line 33 checks that the kept last message keeps its handle.
- Before the fix, every test fixture with tool calls had one call per message, so parallel batches were never tested.

Cause, reproduced on 2026-10-06:
- Claude Code writes a parallel batch as one assistant entry per call, all sharing the response's message id, and writes each result when its call ends. When a session loads, consecutive assistant entries with one message id become one message, and so do consecutive user entries.
- The pruner rebuilt each entry as its own message, and Claude Code gives every rebuilt message a fresh id. A batch of N calls therefore loaded as N separate assistant messages, and only the last was followed by the results. The first N−1 calls got `[Tool result missing due to internal error]` and their real results were dropped.
- It bites when results land after later calls of the batch, which is what slow tools (Bash, web search and fetch, agents) do. Fast reads are written call, result, call, result, which survives the rebuild. A batch that starts fast and then slows down keeps its first and last results, which is why one batch here kept two.
- Of 445 parallel batches on this machine in the last 7 days, 284 wrote all calls and then all results, 116 wrote each call followed by its result, and 45 mixed the two. In 15 of those, a result landed between two calls while an earlier call's result was still to come, so merging adjacent calls alone would not have been enough.

Cost on this machine, last 7 days:
- 46 of 61 compactions lost results: 488 results in 14 sessions and one subagent, across 5 projects.
- By tool: web search 229, Bash 71, Kiasi fetch 63, Edit 61, Read 43, Write 11. The 72 lost Edit and Write results now read as errors.
- 27 lost calls were run again with identical input. A lost result also takes with it the pointer to Kiasi's saved copy of the full output.

Fix, in `hooks/prune.js`:
- Each batch is rebuilt as one assistant message holding all its calls, ahead of all its results. A batch stays open while any of its calls still has a result to come. Claude Code writes the merged message as one entry with all the calls.
- The recent messages kept unchanged never start inside a run of assistant messages or between a call and its result.
- `hooks/compact.test.ts` covers the three layouts and both cut positions. `tests/e2e_parallel_compact.py` replays the repro against a real session: it passes on the fix, and on the old code it fails because calls a and b lose their results.

Why it matters: losing results defeats the purpose of compaction. Afterwards, Claude either re-runs the lost calls, filling the context again, or carries on from results it can no longer see. Parallel calls are how an efficient session works (62% of the calls in this one), so this is not an edge case.

### 2. Every request starts from a ~46k floor

The first request of a session has a median context of 46k. The smallest context seen in a session has a median of 44k. About 30k of the floor is Claude Code's own system prompt and tool definitions. The rest is loaded at session start (average per session, approximate):

| Loaded at session start | ≈ tokens |
|---|---|
| Skills list | 4.7k |
| CLAUDE.md and memory | 3.3k |
| Deferred MCP tool names | 1.8k |
| Agent types list | 1.6k |
| MCP server instructions | 1.3k |
| Hook context (includes Kiasi's rules block) | 1.2k |
| Hook output | 1.1k |

Every request re-reads all of this. At ~6,000 requests a week, removing 1k tokens from the floor saves ~6M re-read tokens a week, about 0.4% of total cost.

### 3. Compaction lands at ~73k, and the floor does not creep up

Median context right after a compaction:

| Compaction | Median context after |
|---|---|
| 1st | 74k |
| 2nd | 85k |
| 3rd and later | 70k |

Across all 113 compactions the mean is 73k, so compaction keeps about 27k of conversation on top of the 46k floor. Because the floor is so high, each compaction frees only ~90k of room (until the next one near 167k). In return, it rewrites ~73k at 2x.

### 4. Runaway contexts on 1M-token windows

Most sessions compact near 167k, but some ran to 300–521k (p99 435k). 16% of all re-read tokens were read at contexts above 200k. Claude Code has a documented setting that caps this: `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100,000 to 1,000,000 tokens).

### 5. Idle returns and compactions rewrite everything

- **First request after a compaction:** 20% of all cache writes.
- **First request after more than an hour idle:** 10% of all cache writes. On average each one rewrites ~104k tokens.
- **Claude Code's built-in fix:** after a long break it offers to "resume from a summary", but its docs mention this only for Pro and Max plans.
- **API keys:** the cache TTL defaults to 5 minutes, so any pause longer than 5 minutes means a full rewrite.

### 6. What fills the conversation

Conversation content, not counting attachments (approximate shares):

| Source | Share |
|---|---|
| Bash output | ~1/3 |
| The Bash commands themselves (long inline commands, heredoc scripts) | ~1/5 |
| Read output | ~1/10 |
| Claude's text | ~1/10 |
| Write inputs | ~7% |

Kiasi's output caps, re-read skip and its `run`, `distill` and `fetch` tools already target the largest of these.

Transcripts also store `prompt_snapshot` attachments, which make up 59% of attachment characters. They are a record of the prompt, not extra context, so the conclusions above leave them out.

### 7. Kiasi's budget report undercounts context

`scripts/reports/budget.py` counts every assistant entry that has a `usage` field as a turn. But Claude Code also writes `<synthetic>` assistant messages with zero usage. In one session, 1,752 of the 2,154 counted turns were synthetic; only 402 were real requests. As a result:
- the mean context shows as 28k, while the real median is 118k
- steps per prompt shows 11.3, which is inflated
- 0% of turns show as 200k or more

The SessionStart hook injects these numbers into every session.

Duplicates add to the error: in this session, the last assistant message before each of the two compactions was written to the transcript twice (same message id and tool-use ids). Counts have to dedupe by message id.

## What the simulation says

The table shows the net cost change compared with today. In each cell:
- **First number:** prune-style reset (Kiasi's pruner or `/clear`; no summary call).
- **Second number:** summary-style reset (`/compact`).

| Reset lands at | Trigger 150k | Trigger 200k | Trigger 250k |
|---|---|---|---|
| 73k (today's compaction) | +1.1% / +5.6% | −3.8% / −3.3% | −3.4% / −3.0% |
| 55k (leaner pruner, smaller floor) | −4.5% / −0.7% | −4.4% / −4.0% | −4.0% / −3.6% |
| 48k (`/clear` + task file) | −6.6% / −3.2% | −4.7% / −4.2% | −4.1% / −3.8% |

With a 150k trigger, re-read tokens drop by 21–26%; with a 200k trigger, by about 10%. In short, a lower trigger only pays off when the reset lands low.

## Proposals

Ordered by value for the effort.

### 1. Fix correctness first

**Compaction loss (finding 1).** Done on 2026-10-06:
1. Reproduced with three slow parallel Bash calls, then `/compact`, then a question asking for all four outputs. The first two calls of the batch came back as `[Tool result missing due to internal error]`.
2. Fixed in `hooks/prune.js` rather than with the stopgap, so batches are still pruned.
3. Regression tests in `hooks/compact.test.ts`, plus `tests/e2e_parallel_compact.py` to rerun after a Claude Code update.
4. Reported upstream with `/feedback` on 2026-10-06, receipt `d15b4c11-b128-422e-acea-c0b0cd68ac5b`. The full write-up is `docs/upstream-compact-hook-report.md`. Testing it with minimal hooks found two more traps, both avoided by the fix. A changed call gets a new message id even when its handle is kept. A result kept unchanged after its call changed links back into the pre-compaction history, which undoes the compaction when the session is resumed.

**Report numbers (finding 7).**
- In `budget.py` (and any shared code in `lens.py`):
  - skip `model == "<synthetic>"`
  - dedupe by message id
  - show median and p90 context next to the mean
- Find out what writes ~1,750 synthetic messages in one session.
- Apply the turn-budget counter fixes from `turn-budget-pause-research.md`: count per agent, and lock state updates.

### 2. Make clear-and-resume the normal way to continue a long task
- **One task file per task.** It holds:
  - the goal
  - decisions made
  - a checklist, where an item is checked only after it was verified
  - the files touched
  - the next step
- **One writer format.** The turn-budget pause, the compaction state, the stop note and a new `/kiasi:handoff` skill all write this same file.
- **Restore on start.** On SessionStart (startup, clear or resume), inject the project's latest task file with "continue from here; re-verify before marking anything done".
- **Notice at ~150k context:** "Context 152k (floor 46k). Run /kiasi:handoff, then /clear: this task continues from ~48k." Later, also detect when the developer switches tasks.
- **Expected:** about −6.6% cost. It also fixes "the chat forgot what we did", because the state lives in a file rather than in the context.

### 3. Cap runaway contexts at 200k
- Recommend `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` under `env` in team settings.
- The dashboard shows the window in effect and flags sessions that went past 200k.
- **Expected:** about −4% on its own.

### 4. Lower the floor
- **Prefix audit on the dashboard.** For each project:
  - show the size of each piece loaded at session start (the table in finding 2), measured from the session's first attachments
  - show what can be turned off for that project (plugins, MCP servers, agents)
  - suggest moving long CLAUDE.md sections into skills
- **Shrink Kiasi's own SessionStart block** to a few lines, and move the full rules into a skill that loads on demand.
- **Leaner pruner** (after the fix in proposal 1).
  - Keep the last few exchanges word for word.
  - Reduce older ones to the prompt, a one-line outcome and an archive pointer.
  - Let the task file carry the state.
- **Expected:** about −0.4% of cost per 1k tokens removed. It also makes proposal 2 and compaction go further.

### 5. Live context meter in the status line
- A Kiasi status line script, e.g. `ctx 118k (floor 46k) · turn 1.4M · cache warm 1h · idle 3m`. Claude Code passes status line scripts the context and prompt-cache data.
- Developers add one line to their settings, or a Kiasi setup command adds it.
- This is what makes proposals 2 and 3 visible to developers while they work.

### 6. Cache-aware notices
- **Idle return:** when the cache has expired and the context is over ~100k, suggest a handoff before the next prompt rewrites everything.
- **TTL check:** on API keys the default TTL is 5 minutes. If the dashboard sees many gaps of 5–60 minutes, suggest `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` (or `ENABLE_PROMPT_CACHING_1H=1`). This machine already uses 1 hour.
- **Mid-session switches:** explain the rewrites caused by changing `/model` or `/effort` mid-session. The prompt caching docs say an effort change invalidates the cached messages.

### 7. Effort and thinking (measure first)
- Output is 23% of cost.
- On Opus 4.5+ and Sonnet 4.6+, thinking blocks stay in context, so higher effort also grows the context.
- Show the effort level in the status line, and measure output per prompt by effort before recommending a default.

## Status (2026-10-07)

| Proposal | State |
|---|---|
| 1 Parallel-batch pruning | done 2026-10-06; report numbers (synthetic skip, message-id dedupe, median and p90) done |
| 2 Clear-and-resume | task file, four writers, `/kiasi:handoff`, restore on start, 150k notice, task-switch notice, pause wording done |
| 3 Cap at 200k | README recommendation, window in effect and runaway flag on the dashboard done |
| 4 Lower the floor | prefix audit sheet, 7-line SessionStart block, leaner pruner done; MCP schema sizes are from the deferred-tool listing, not the schemas |
| 5 Context meter | done in the status line |
| 6 Cache-aware notices | idle-return notice, TTL check and model/effort switch counts done |
| 7 Effort | measured per effort level on the Budget tab; the comparison line appears once two levels have 50 prompts each; no recommendation beyond that until a week of data |

Open items are tracked in the pull request that landed this.

## Decisions needed

1. Where should the task file live: in the repo (`.kiasi/task.md`, visible and editable), or in Kiasi's data folder (private, per session)?
2. Should Kiasi write `CLAUDE_CODE_AUTO_COMPACT_WINDOW` into settings itself (after asking), or only recommend it?
3. Should the handoff notice default to 150k?

## Sources

- Claude Code docs, Manage costs effectively (sections "Why usage climbs in a long session" and "Manage context proactively"): https://code.claude.com/docs/en/costs
- Claude Code docs, Environment variables (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, `CLAUDE_CODE_PROMPT_CACHE_TTL`, `ENABLE_PROMPT_CACHING_1H`, `FORCE_PROMPT_CACHING_5M`, `DISABLE_AUTO_COMPACT`): https://code.claude.com/docs/en/env-vars
- Claude API docs, Prompt caching (prices, what invalidates the cache, thinking blocks): https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic engineering, Effective harnesses for long-running agents: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- `docs/turn-budget-pause-research.md` (turn budget findings)
- Finding 1: this session's transcript, `hooks/compact.js`, `hooks/prune.js`, `hooks/compact.test.ts`
