# Kiasi settings

Every threshold Kiasi uses, how to change it, and how to trim the startup floor. The [README](../README.md) has the short version.

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

## Trimming the startup floor

The floor is what a session holds before its first reply (CLAUDE.md files, the skill, tool and agent listings, hook output, memory) and every request re-sends it. The Overview's "Startup floor" fix opens a panel with the pieces of each project's latest session, biggest first, and the floor per day, so a settings change shows as a step. `/kiasi:floor` audits the last 30 days of sessions and writes a fix script (never run by Kiasi) that moves what only some projects use out of the global scope: unused skills to `name-only`, single-project skills and agents into that project, user-scope MCP servers to project scope.

A `UserPromptSubmit` hook that attaches the same text on every prompt adds a copy per prompt for the rest of the session. `scripts/once.py` runs any such hook once a session: in `settings.json` replace its command with `sh <kiasi>/scripts/run.sh once.py -- <the command>`. The audit writes that line for every hook it saw repeating.

Two Claude Code settings outside Kiasi are worth setting too:

`/kiasi:limits setup` writes both of these to the `env` block of `~/.claude/settings.json`. Teams that manage settings centrally set them there instead:

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` under `env` in team settings: it caps runaway contexts, and the dashboard flags sessions that went past it.
- `CLAUDE_CODE_PROMPT_CACHE_TTL=1h` on API keys: the default 5-minute TTL rewrites the whole context after a short break. The status line's `cache warm` countdown follows this value.
