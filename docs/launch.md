# Launch copy

The text for each channel, ready to paste. Numbers come from [BENCHMARK.md](../BENCHMARK.md) and the overview on the machine Kiasi was built on, in the week to 17 October 2026. Swap in your own card before posting, and never post a number you cannot reproduce from a transcript.

Lead with the measurement, not a multiple. The 2.1× drop in context per step on the author's machine comes almost entirely from the 200k compaction window on a 1M-context model, a setting anyone can make without Kiasi; the cuts themselves keep out about 5% of what is re-sent. Never present the 2.1× as the cuts' effect, and name the setting whenever the 2.1× appears.

## Order

1. Reply in weekly-limit threads first, with the free fix, a card and one line. Collect ten testers' cards on the results page (`#results`, through the "Share your month" issue form).
2. Submit to the directories. They work while you sleep.
3. Show HN, Tuesday to Thursday, 7 to 9 am US Eastern, after the results page has ten entries.

## Show HN

**Title** (the limit is 80 characters, and "Show HN:" is part of it):

```
Show HN: Kiasi – Where your Claude Code weekly limit goes, measured locally
```

Alternatives if the first reads as vague:

```
Show HN: Kiasi – See what each Claude Code step re-sends, and cut the noise
Show HN: Kiasi – Claude Code plugin that cuts the context you re-send every step
```

**First comment** (post it within a minute of the submission, as the author):

```
Author here. Claude Code re-sends the whole conversation on every step, so a
20,000-char build log is paid for again on every later step until the session
compacts. I wanted to know how much of my weekly limit that was, so Kiasi starts
with measurement: a local dashboard reads your own transcripts and shows what each
step re-sends and where it came from. On my machine last week, 42% was the context
every session starts with (system prompt, tool definitions, CLAUDE.md), 22% file
reads, 17% shell output.

The biggest lever it found is a setting, not the plugin. On a 1M-context model,
CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000 took the context re-sent per step from 232k
to 108k tokens. Kiasi's setup writes it for you, but you can set it yourself today.

The plugin's own cuts are smaller, and I would rather say so: about 5% of what I
re-sent in a normal week. They matter most on noisy output. A failing test run
through its run tool reaches Claude as 2,082 chars (the failing test and its error)
instead of a 10,000-char head and tail; an unchanged file read a second time comes
back as a 245-char pointer instead of 29,000 chars. Everything it cuts is saved in
full and searchable. The benchmark replays these through the same handlers against
what Claude Code alone sends, including the three cases where Claude Code alone
sends less.

The trade-off: the turn budget pauses Claude after 60 steps and asks you to
continue. It is a setting.

No model calls, no network, MIT. All of this comes from one machine, mine, and I
am not the person who hits the limit every week. If you are, the dashboard makes a
card you can post, and I would like to see it.
```

What to do in the thread: answer every question in the first two hours, concede the fair ones in the first sentence, and link the specific file when someone asks how a number is computed. When someone says "so just set the env var", agree: that is the point of the second paragraph. Do not reply to arguments about whether Anthropic should fix this upstream.

## Reply for weekly-limit threads

For r/ClaudeAI, r/ClaudeCode, the Claude Code GitHub discussions and X threads where someone has just hit the weekly limit. One reply per thread, never a top-level post, and only where the thread is about the limit. The reply gives the free fix first; the plugin is the second half.

```
Every step re-sends the whole conversation, so the biggest fix on a 1M-context
model is usually one setting: CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000 in the env
block of ~/.claude/settings.json. It halved what each step re-sent for me. After
that, it helps to see what fills the context. I wrote a plugin that breaks it down
from your own transcripts and cuts noisy tool output, which kept out about another
5% for me. My card is below; if you try it, I would like to see yours.

[card image]  github.com/raj-rangani/kiasi
```

Rules: post the card image, not a screenshot of the README. If the subreddit removes self-promotion, post the setting alone and leave the plugin out.

## Awesome-list pull requests

For `hesreallyhim/awesome-claude-code` and `ccplugins/awesome-claude-code-plugins`. Follow each list's entry format and alphabetical position; this is the body.

**PR title**

```
Add Kiasi: measures what each Claude Code step re-sends and cuts the noisy part
```

**Entry**

```
- [Kiasi](https://github.com/raj-rangani/kiasi) - A local dashboard that measures, from your own transcripts, what each step re-sends and where it comes from, plus fixed rules that keep noisy tool output, re-reads and big pastes out of the context, with the cut text saved in full and searchable. MIT, no model calls.
```

**PR description**

```
Kiasi is a Claude Code plugin that measures what each step re-sends, from the
user's own transcripts with the days before install as the baseline, and keeps
noisy tool output, re-reads and pastes out of the context. It differs from the
output-filtering tools already listed in two ways: the cut text is saved in full and
searchable rather than dropped, and the numbers come from the user's own sessions.
Its benchmark compares each cut with what Claude Code alone sends, including the
cases where Claude Code alone sends less. MIT licensed, no network or model calls.
```

## Directory submissions

- **Anthropic community plugins**: the form linked from the Claude Code plugin docs. Use the one-line description from `.claude-plugin/plugin.json` and the dashboard screenshot.
- **claudepluginhub**: claim the auto-generated listing, replace the description with the plugin.json one, add the screenshot.

## The article

Title: "Where a Claude Code week actually goes". Structure: your budget data by kind of context (startup floor, tool output, file re-reads, reminders), one chart, the single biggest piece and what it cost, three things anyone can change without a plugin (the compaction window first), then Kiasi in one paragraph at the end. It is the link in the Show HN first comment if it is ready, and stands alone if it is not.
