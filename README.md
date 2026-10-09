<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
  <img src="docs/logo-light.svg" alt="Kiasi" width="150">
</picture>

Your weekly limit is spent re-reading. Every step of Claude Code re-sends the whole conversation, so one noisy test log is paid for again on every step after it, for the rest of the session. One `npm install` log is 42,800 characters. One Playwright snapshot through MCP is 56,000. Twenty GitHub issues are 105,000. Each one rides along on every step that follows.

Kiasi is a Claude Code plugin that keeps that noise out: fixed rules, on your machine, no model calls, no network. The full text of everything it cuts is saved and searchable, so nothing is lost. Install it and work as usual. The next day a local dashboard shows your own before and after, built from your own transcripts: the context each step re-sends, and the days of weekly limit Kiasi bought back.

[**Website**](https://raj-rangani.github.io/kiasi/) · [Benchmark](BENCHMARK.md) · [Settings](docs/settings.md) · [Changelog](CHANGELOG.md) · MIT

## Quick start

In the Claude Code terminal:

```
/plugin marketplace add raj-rangani/kiasi
/plugin install kiasi@kiasi
```

Add two settings to the `env` block of `~/.claude/settings.json`. Kiasi reminds you at session start if either is missing.

```json
"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
```

The first compacts at 200k instead of the full window and is the biggest single lever, with or without Kiasi. The second turns on the experimental pruner and search.

Restart Claude Code and run `/kiasi:limits setup` once. It adds your 5-hour and weekly limits and what Kiasi kept off them to the status line, after whatever you already had, and it is the only place the dashboard reads your plan limits from. The dashboard starts itself with every session at `http://127.0.0.1:8787/`.

In the VS Code extension, where `/plugin` does not exist, open this link (GitHub shows it as plain text, so copy it into the browser's address bar). It opens the Claude Code panel on Kiasi, adds the marketplace if needed and asks for a scope:

```
vscode://anthropic.claude-code/install-plugin?plugin=kiasi&marketplace=raj-rangani/kiasi
```

Or type `/plugins` in the prompt box, add `raj-rangani/kiasi` in the Marketplaces tab, then install Kiasi from the Plugins tab. From any shell, `claude plugin marketplace add raj-rangani/kiasi` and `claude plugin install kiasi@kiasi` do the same without a dialog.

## What it cuts

- **Noisy tool output.** Over 12,000 chars it is cut to head and tail, progress bars and repeated lines are stripped, and the full text is saved to disk and named in the cut. File reads, range commands and diffs are never cut.
- **Re-reads and huge pastes.** A file already in context and unchanged comes back as a pointer. A paste over 4,000 chars is saved to disk, over 40,000 it is refused and you resend the path.
- **Runaway turns.** A warning 10 steps before the budget at 60, then a pause with a checklist in the project's `.kiasi/` folder; reply "continue" to resume. The same call failing 3 times tells Claude to rethink instead of retry.
- **Compaction loss** (experimental). Your prompts and Claude's replies stay word for word while old tool output is pruned; the task, edited files and failing commands are re-injected.

## Measured, not estimated

Every number Kiasi shows is measured. The benchmark replays shape-realistic tool outputs through the same handlers that run in a session, and the dashboard measures your own sessions the same way, against the days before you installed.

| Scenario | Chars in | Chars entered | Saved over 10 later steps |
|---|---:|---:|---:|
| GitHub issue list via MCP | 105,223 | 6,181 | 247,605 tokens |
| npm install --loglevel=warn --no-fund --no-audit log | 42,800 | 3,569 | 98,077 tokens |
| Test run through `mcp__kiasi__run` | 64,578 | 2,082 | 156,240 tokens |

The full table and how to rerun it are in [BENCHMARK.md](BENCHMARK.md). On the machine Kiasi was built on, 16 days before against 7 days after: context re-sent per step 201k → 31k tokens, 6.5× less; steps over 200k context 52% → 0%. That is one machine. Yours is the number that matters, and it is on your dashboard the day after you install.

<img src="docs/overview.webp" alt="Kiasi dashboard overview" width="720">

## Compared with the alternatives

| | Kiasi | [RTK](https://github.com/rtk-ai/rtk) | [Context Mode](https://github.com/mksglu/claude-context-mode) | `CLAUDE_CODE_AUTO_COMPACT_WINDOW` alone |
|---|---|---|---|---|
| What it covers | Bash, MCP and outside-file output, re-reads, pastes, turns, compaction | Bash commands it rewrites | MCP, Bash, Read and WebFetch routed through its server | Compaction point only |
| Where the cut text goes | Saved in full, searchable | Filtered, not kept | Stored in a local database | n/a |
| Before and after from your own transcripts | Yes, baseline from the days before install | No, estimates in the README | No, per-tool stats from install | No |
| Model calls, network | None | None | None | None |
| License | MIT | MIT | Elastic-2.0 | n/a |

Kiasi tells you to set the compaction window too, because it is the biggest single lever. The comparison is what Kiasi adds on top of it, and the `holdout` setting lets you measure that on your own sessions.

## The rules

Each rule is a Claude Code hook with a fixed threshold you can change. Nothing is decided by a model.

| Leak | Rule | Hook |
|---|---|---|
| One install log rides along on every step after it | Output cap and cleaning | `PostToolUse` |
| A 60-step turn chasing one failing test | Turn budget and loop check | `PostToolUse`, `PostToolUseFailure`, `PostToolBatch` |
| The same file, read again, unchanged | Re-read skip | `PreToolUse` |
| A whole log pasted to ask one question | Paste manager | `UserPromptSubmit` |
| Compaction forgets what you were doing | Pruner and state | `session.compact` (experimental) |
| No idea where the week went | Local dashboard | `SessionStart` |
| The week runs out before the reset | Runway, session receipt, weekly digest | `SessionEnd`, `SessionStart` |

Cuts leave a `[kiasi kept …]` or `[kiasi trimmed …]` marker naming the saved file. The trade-off is the turn budget: a long autonomous turn is paused at 60 steps and asks how to go on. `turn_budget_mode` can make it only warn, or turn it off. Every threshold is in [docs/settings.md](docs/settings.md).

## Dashboard

The days before you installed Kiasi are the baseline, built from your own transcripts. Every cut, skip and stop is a row with the file it saved and what it kept out. A per-day history outlives Claude Code's transcript retention, so the comparison does not expire.

| Command | What it does |
|---|---|
| `/kiasi:dashboard` | Start the dashboard if it is not up and print its URL |
| `/kiasi:dashboard autostart on` | Run the dashboard from login, with no Claude Code session needed (systemd user service or XDG autostart on Linux, LaunchAgent on macOS, Startup folder on Windows); `off` removes it, `status` shows it |
| `/kiasi:sync` | Rebuild the reports now |
| `/kiasi:limits setup` | Install the Kiasi status line, the only source of your plan limits |
| `/kiasi:limits remove` | Put your previous status line back |

### Runway, receipt and digest

The Overview leads with the runway: whether the weekly limit lasts to its reset at the current burn, from the status line readings. Under it, the days of that limit Kiasi bought back: the re-reads it avoided, divided by what you send a day. "Share your month" opens the last 30 days on one card, to copy as text or save as an image.

Outside the dashboard, two notices and nothing else:

- **Session receipt.** When a session ends, a desktop notification with what it sent, what it would have sent without Kiasi, and the costliest output cut. The next session in the same project sees it as one line at start.
- **Weekly digest.** Once a week at session start: the factor since the install, what was kept out and the days it bought back, the week against the week before, any spike session, the next fix, and the runway.

Both come from the same event log and report the dashboard reads; nothing is sent anywhere.

## Privacy

- Nothing is lost. Every cut output and refused paste is saved in full and found again with `mcp__kiasi__search` or `scripts/search.py`.
- No network calls. Kiasi reads your local transcripts and writes to its own folder under `~/.claude/plugins/data/`.
- No prompt text is logged. Reports hold counts and sizes, never what you typed.
- The dashboard listens on `127.0.0.1` only and refuses other host names.
- Saved files are moved to `trash/` after 7 unused days (pastes 14) and deleted 7 days later. The first week is report-only, `cleanup.py --restore` undoes a move, and `KIASI_CLEANUP=off` disables it. Turn checklists in a project's `.kiasi/` folder are cleaned the same way, and a restore puts them back there.

## Updating

Claude Code does not update plugins on its own. A new Kiasi version becomes visible when the version in the repo changes, and you pick it up with:

```
/plugin marketplace update kiasi
```

or `claude plugin update kiasi@kiasi` from a shell, then restart Claude Code. To update automatically at session start, open `/plugin`, pick the Kiasi marketplace under Marketplaces and choose Enable auto-update. If you use the status line, run `/kiasi:limits setup` again after an update that changed it; the release notes say when. Releases and their notes are at https://github.com/raj-rangani/kiasi/releases.

## Requirements

- Claude Code 2.1.283+
- Python 3.8+, standard library only
- Linux (tested), macOS (expected to work) or Windows with Git Bash, which Claude Code needs anyway (hooks run through `scripts/run.sh`, which picks `py -3`, `python` or `python3`; the dashboard and cleanup detach without POSIX calls; not yet tested on a Windows machine, reports welcome)
- Node 18+ only for the experimental function hooks

Settings and thresholds: [docs/settings.md](docs/settings.md). Tests and layout: [docs/development.md](docs/development.md).

## Uninstall

Run `/kiasi:limits remove` if you set up the status line, then `/plugin uninstall kiasi`. Your data folder stays until you delete it.

## License

MIT. Not affiliated with Anthropic.
