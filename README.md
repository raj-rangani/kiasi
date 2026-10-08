<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
  <img src="docs/logo-light.svg" alt="Kiasi" width="150">
</picture>

Kiasi is a Claude Code plugin that makes your weekly limit last longer. Every step of Claude Code re-sends the whole conversation, so one noisy tool output is paid for again on every step after it. Kiasi keeps that noise out with fixed rules, on your machine, with no model calls and no network.

On the machine it was built on, context re-sent per step went from 201k to 31k tokens, 6.5× less, and steps over 200k context went from 52% to 0%. Your own before-and-after is on the local dashboard the day after you install.

[**Website**](https://raj-rangani.github.io/kiasi/) · [Changelog](CHANGELOG.md) · MIT

## Key features

- **Output cap and cleaning**: tool output over 12,000 chars is cut, progress bars and repeated lines are stripped, the full text is saved to disk and named in the cut.
- **Turn budget and loop check**: warned 10 steps before the pause at 60 (the tool calls of one response are one step, so parallel calls count once; later calls are refused and Claude saves a checklist in the project's `.kiasi/` folder, which git ignores; reply "continue", here or after `/clear`, and Claude resumes from it; `turn_budget_mode` can make the budget only warn, or turn it off); the same call failing 3 times tells Claude to rethink instead of retry.
- **Re-read skip**: a file already in context and unchanged comes back as a pointer, not the text again.
- **Paste manager**: a paste over 4,000 chars is saved to disk; over 40,000 it is refused and you resend with the path.
- **Pruner and state** (experimental): at compaction your prompts and Claude's replies stay word for word while old tool output is pruned; the task, edited files and failing commands are re-injected.
- **Local dashboard**: every action Kiasi took, the tokens it kept off your limit, your 5-hour and weekly limits, rebuilt every 30 minutes at `http://127.0.0.1:8787/`.

## Quick start

In the Claude Code terminal:

```
/plugin marketplace add raj-rangani/kiasi
/plugin install kiasi@kiasi
```

In the VS Code extension, where `/plugin` does not exist, open this link (GitHub shows it as plain text, so copy it into the browser's address bar). It opens the Claude Code panel on Kiasi, adds the marketplace if needed and asks for a scope:

```
vscode://anthropic.claude-code/install-plugin?plugin=kiasi&marketplace=raj-rangani/kiasi
```

Or type `/plugins` in the prompt box, add `raj-rangani/kiasi` in the Marketplaces tab, then install Kiasi from the Plugins tab. From any shell, `claude plugin marketplace add raj-rangani/kiasi` and `claude plugin install kiasi@kiasi` do the same without a dialog.

Add two settings to the `env` block of `~/.claude/settings.json`. Kiasi reminds you at session start if either is missing.

```json
"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
```

The first compacts at 200k instead of the full window and is the biggest single lever. The second turns on the experimental pruner and search.

Restart Claude Code and work as usual. Run `/kiasi:limits setup` once so the dashboard can show your plan limits. The dashboard starts itself with every session.

## Updating

Claude Code does not update plugins on its own. A new Kiasi version becomes visible when the version in the repo changes, and you pick it up with:

```
/plugin marketplace update kiasi
```

or `claude plugin update kiasi@kiasi` from a shell, then restart Claude Code. To update automatically at session start, open `/plugin`, pick the Kiasi marketplace under Marketplaces and choose Enable auto-update. If you use the status line, run `/kiasi:limits setup` again after an update that changed it; the release notes say when. Releases and their notes are at https://github.com/raj-rangani/kiasi/releases.

## How it works

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

Cuts keep the head and tail and leave a `[kiasi kept …]` or `[kiasi trimmed …]` marker naming the saved file. File reads, range commands and diffs are never cut, so code quality does not depend on the cap.

## Dashboard

<img src="docs/overview.webp" alt="Kiasi dashboard overview" width="720">

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

- No network calls. Kiasi reads your local transcripts and writes to its own folder under `~/.claude/plugins/data/`.
- No prompt text is logged. Reports hold counts and sizes, never what you typed.
- The dashboard listens on `127.0.0.1` only and refuses other host names.
- Nothing is lost. Every cut output and refused paste is saved in full and found again with `mcp__kiasi__search` or `scripts/search.py`.
- Saved files are moved to `trash/` after 7 unused days (pastes 14) and deleted 7 days later. The first week is report-only, `cleanup.py --restore` undoes a move, and `KIASI_CLEANUP=off` disables it. Turn checklists in a project's `.kiasi/` folder are cleaned the same way, and a restore puts them back there.

## Settings

Set these with `/config` or as env vars. Every other threshold is a named constant in `scripts/core/constants.py` or `hooks/constants.js`.

| Setting | Env var | Default |
|---|---|---|
| `output_cap_chars` | `CLAUDE_PLUGIN_OPTION_OUTPUT_CAP_CHARS` | 12000 |
| `turn_budget_mode` | `CLAUDE_PLUGIN_OPTION_TURN_BUDGET_MODE` | pause (or warn, off) |
| `pause_notification` | `CLAUDE_PLUGIN_OPTION_PAUSE_NOTIFICATION` | auto (or always, off) |
| `pause_question` | `CLAUDE_PLUGIN_OPTION_PAUSE_QUESTION` | auto (or always, off) |
| `turn_call_budget` | `CLAUDE_PLUGIN_OPTION_TURN_CALL_BUDGET` | 60 |
| `turn_token_budget` | `CLAUDE_PLUGIN_OPTION_TURN_TOKEN_BUDGET` | 8000000 |
| `subagent_call_budget` | `CLAUDE_PLUGIN_OPTION_SUBAGENT_CALL_BUDGET` | 40 |
| `holdout` | `CLAUDE_PLUGIN_OPTION_HOLDOUT` | empty (or reread_check, turn_budget, context_notices, output_cap, sandbox) |
| `paste_refusal_chars` | `CLAUDE_PLUGIN_OPTION_PASTE_REFUSAL_CHARS` | 40000 |
| `compaction_window_text` | `CLAUDE_PLUGIN_OPTION_COMPACTION_WINDOW_TEXT` | "200000" |

Set `holdout` to a rule name to switch it off in half of your sessions; the Rules tab then compares the two halves.

A `.kiasi.json` in the project root can set any of these except `compaction_window_text` for that project, plus `turn_warn_steps` and `turn_warn_tokens` (by default the warning comes 10 calls, or a fifth of the tokens, before the budget).

`pause_question` is what your own turn does when its budget runs low. At the warning, and again at the pause if the turn gets that far, Claude writes the checklist and asks how to go on, with three options: continue here with a fresh budget, hand the rest to a subagent, or stop. Continue renews the budget in place; the other two leave the turn paused, so replying continue later still resumes it. With `auto` the question is asked in the terminal, the VS Code extension and the desktop app; a headless run (`claude -p`) and a subagent are paused without it. With `off` the turn ends with the pause notice.

`pause_notification` is a desktop notification when a turn is paused, through `notify-send` on Linux, `osascript` on macOS and a PowerShell toast on Windows. With `auto` it shows only in the VS Code extension and the desktop app, where the pause is otherwise one grey line in the chat; the terminal already raises its own notification and bell.

`KIASI_DASHBOARD=off` stops the session-start autostart of the dashboard.

Two Claude Code settings outside Kiasi are worth setting too:

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` under `env` in team settings: it caps runaway contexts, and the dashboard flags sessions that went past it.
- `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` on API keys: the default 5-minute TTL rewrites the whole context after a short break. The status line's `cache warm` countdown follows this value.

## Requirements

- Claude Code 2.1.283+
- Python 3.8+, standard library only
- Linux (tested), macOS (expected to work) or Windows with Git Bash, which Claude Code needs anyway (hooks run through `scripts/run.sh`, which picks `py -3`, `python` or `python3`; the dashboard and cleanup detach without POSIX calls; not yet tested on a Windows machine, reports welcome)
- Node 18+ only for the experimental function hooks

## Development

```
python3 -m unittest discover -s tests   # hook handlers, reports, dashboard
claude plugin test .                     # hooks/*.test.ts on the real engine
claude plugin validate .                 # manifest, marketplace and hooks.json
```

`scripts/kiasi.py` is the hook entry point and dispatches to `scripts/core/`. Reports live in `scripts/reports/`, the dashboard in `dashboard/`, and `scripts/README.md` says which hook calls what.

## Uninstall

Run `/kiasi:limits remove` if you set up the status line, then `/plugin uninstall kiasi`. Your data folder stays until you delete it.

## License

MIT. Not affiliated with Anthropic.
