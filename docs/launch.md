# Launch copy

The text for each channel, ready to paste. Numbers come from [BENCHMARK.md](../BENCHMARK.md) and the overview on the machine Kiasi was built on. Swap in your own card before posting, and never post a number you cannot reproduce from a transcript.

## Order

1. Reply in weekly-limit threads first, with a card and one line. Collect ten testers' cards into `docs/results.md`.
2. Submit to the directories. They work while you sleep.
3. Show HN, Tuesday to Thursday, 7 to 9 am US Eastern, after the results page has ten entries and the README has the GIF.

## Show HN

**Title** (the limit is 80 characters, and "Show HN:" is part of it):

```
Show HN: Kiasi – Claude Code plugin that cuts the context you re-send every step
```

Alternatives if the first reads as vague:

```
Show HN: Kiasi – I measured where my Claude Code weekly limit went, then cut it 6.5×
Show HN: Kiasi – Keep tool output out of Claude Code's context, measured per session
```

**First comment** (post it within a minute of the submission, as the author):

```
Author here. Claude Code re-sends the whole context on every step, so a 42,800-char
npm log or a 105,000-char list of GitHub issues is paid for again on every later step
of the session. Kiasi is a plugin that keeps that text out of the context in the first
place: outputs over 12,000 chars are cut to head and tail with the full text saved to
disk and named in the cut, re-reads of unchanged files come back as a pointer, big
pastes go to disk, and runaway turns get a budget and a pause.

Everything it shows is measured, not estimated. The dashboard reads your own
transcripts, takes the days before you installed as the baseline, and shows context
re-sent per step before and after. On my machine: 201k → 31k tokens per step, and
steps over 200k context went from 52% to 0%. The benchmark in the repo replays
realistic outputs through the same handlers so you can check the handler numbers
without installing.

The trade-off: the turn budget pauses Claude after 60 steps and asks you to continue.
It is the feature people turn off first and the one that caught my worst runaway
sessions. It is a setting.

No model calls, no network, MIT. The two settings it needs are written by one command.
Happy to answer anything about how the measurement works; that is the part I would
want to be skeptical of too.
```

What to do in the thread: answer every question in the first two hours, concede the fair ones in the first sentence, and link the specific file when someone asks how a number is computed. Do not reply to arguments about whether Anthropic should fix this upstream.

## Reply for weekly-limit threads

For r/ClaudeAI, r/ClaudeCode, the Claude Code GitHub discussions and X threads where someone has just hit the weekly limit. One reply per thread, never a top-level post, and only where the thread is about the limit.

```
Same here until I measured it. Most of my weekly limit was context re-sent on every
step: one big tool output early in a session gets paid for on every step after it.
I wrote a plugin that cuts those outputs to head and tail (full text saved to disk),
returns re-reads as pointers, and shows before/after from your own transcripts.
My card after a week is below. If you try it, I would like to see yours; the number
I trust least is the one from only my machine.

[card image]  github.com/raj-rangani/kiasi
```

Rules: post the card image, not a screenshot of the README. If the subreddit removes self-promotion, reply without the link and let them ask.

## Awesome-list pull requests

For `hesreallyhim/awesome-claude-code` and `ccplugins/awesome-claude-code-plugins`. Follow each list's entry format and alphabetical position; this is the body.

**PR title**

```
Add Kiasi: cuts the context Claude Code re-sends every step, measured per session
```

**Entry**

```
- [Kiasi](https://github.com/raj-rangani/kiasi) - Keeps noisy tool output, re-reads and big pastes out of the context, with the cut text saved in full and searchable. A dashboard measures context per step before and after install from your own transcripts. MIT, no model calls.
```

**PR description**

```
Kiasi is a Claude Code plugin that keeps tool output, re-reads and pastes out of the
context so the same tokens are not re-sent on every later step. It differs from the
output-filtering tools already listed in two ways: the cut text is saved in full and
searchable rather than dropped, and the dashboard measures before and after from the
user's own transcripts, with the days before install as the baseline. Benchmark and
method are in the repo. MIT licensed, no network or model calls.
```

## Directory submissions

- **Anthropic community plugins**: the form linked from the Claude Code plugin docs. Use the one-line description from `.claude-plugin/plugin.json` and the dashboard screenshot.
- **claudepluginhub**: claim the auto-generated listing, replace the description with the plugin.json one, add the screenshot.

## The article

Title: "Where a Claude Code week actually goes". Structure: your budget data by kind of context (startup floor, tool output, file re-reads, reminders), one chart, the single biggest piece and what it cost, three things anyone can change without a plugin, then Kiasi in one paragraph at the end. It is the link in the Show HN first comment if it is ready, and stands alone if it is not.
