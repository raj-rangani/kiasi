# Kiasi

*kiasi (Swahili): measure, moderation. Kwa kiasi, in moderation.*

Kiasi is a Claude Code plugin that keeps a session's context small and its
weekly usage limit long. Every turn re-sends the whole conversation, so the
real cost is context size times number of turns; Kiasi keeps both down with
deterministic thresholds, no model calls, reading only the hook payloads and
the transcript file each hook is already given. It makes no network requests
and sends nothing anywhere.

## What it does

- **Output caps** — web results, transcript/log/`/tmp` reads and bulk command
  output over a size threshold are cut, with the full text always saved to
  disk and a `[kiasi kept ...]` / `[kiasi trimmed ...]` marker naming the path.
- **Paste manager** — prompts over 4,000 chars are saved to disk so later
  turns can refer to the path instead of quoting them; prompts over 40,000
  chars are refused outright (saved, dropped from the conversation, and you're
  asked to resend the instruction with the path).
- **Turn budget** — a turn is warned at 30 tool calls / 4M re-read tokens and
  stopped at 60 / 8M, instructing Claude to checkpoint the remaining work and
  either end the turn or hand it to one subagent.
- **Subagent policy** — sets a default model per subagent type and appends a
  step budget to every dispatched brief.
- **Session notes and recall** — a note is written at Stop and at compaction
  and injected at the next SessionStart for that project, with a one-line
  budget summary.
- **Context rules as SessionStart context** — the working rules (one task per
  session, no polling, read files by section, etc.) are injected as
  `additionalContext` from `rules.md`, nothing to paste into your `CLAUDE.md`.
- **(Experimental) deterministic compaction pruner** — a function hook that
  replaces the model-written compaction summary with a rule-based prune of the
  transcript (verbatim prompts and assistant text, old tool results cut to a
  head, errors kept longer), falling back to the built-in summarizer if the
  prune can't reach the target size.
- **(Experimental) `mcp__kiasi__search`** — full-text search
  over everything Kiasi has saved: outputs, pastes, notes, checkpoints. It reads
  the files directly (no index to store; about 0.1 s for a few MB).
- **Local dashboard** — five pages (Overview, Sessions, Rules, Actions,
  Budget) showing what was cut, delegated, refused or pruned and the re-read
  tokens avoided, served by a small stdlib HTTP server with no other
  dependency.

## Install

From a marketplace repository:
```
/plugin marketplace add <owner>/<repo>
/plugin install kiasi@<marketplace>
```

For local development or before publishing, load it directly by directory:
```
claude --plugin-dir /path/to/kiasi
```
(or set `CLAUDE_CODE_PLUGIN_DIRS` in `~/.claude/settings.json`'s `env` block).

## Two env settings worth setting

A plugin cannot set the user's env, so these two go in the `env` block of
`~/.claude/settings.json` by hand. Without them Kiasi still works, just less
effectively; if either is missing, SessionStart adds one warning line with the
exact snippet to add:

```json
{
  "env": {
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
    "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"
  }
}
```

- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` caps the context window so
  compaction happens well before the re-read bill grows large. This is the
  single biggest lever; on the machine this was built on it took mean context
  from roughly 250k to 57k tokens.
- `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` turns on the experimental compaction
  pruner and the search tool (see below). The six classic hooks (caps, turn
  budget, paste manager, notes, rules injection) work without it.

## Data directory

All state (event log, saved pastes/outputs/notes/checkpoints, session
records, gzipped report JSON, sync status) is written to
`${CLAUDE_PLUGIN_DATA}`, a path Claude Code exports to every hook process
(normally under `~/.claude/plugins/data/`). Scripts run by hand (the
dashboard, `sync.py`, `search.py`) aren't hook processes and don't see that
variable, so they fall back to `~/.claude/kiasi/`; export `CLAUDE_PLUGIN_DATA`
yourself first if you want a by-hand run to read the hooks' own directory.
Nothing is ever written under `${CLAUDE_PLUGIN_ROOT}` (the installed plugin
code), since that path changes on every update.

### Cleanup

A saved output, checkpoint, paste or session record only matters to the
session that made it, so `scripts/cleanup.py` moves such a file, gzipped, to `trash/`
once the file and its session have both been unused for 7 days (pastes: 14).
Trash is deleted 7 days later. It runs in the background at session start and
on sync, at most once a day, and it:

- touches only file names Kiasi itself writes, never through a symlink;
- never touches the running session or anything used in the last 24 hours;
- removes a notes file only when its newest entry is over 30 days old, and
  never rewrites the event log;
- reports only, moving nothing, for the first 7 days (the dashboard shows what
  it would move);
- empties trash oldest first above 50 MB, then only warns;
- logs every move, delete and restore to `cleanup.jsonl`.

`python3 scripts/cleanup.py --dry-run` lists what would move, and `--restore`
puts trashed files back. Set `KIASI_CLEANUP` to `report`, `trash` or `off` to
override the default `auto`. The limits are the `CLEANUP_*` constants in
`scripts/constants.py`.

## Dashboard

To keep the dashboard up permanently on Linux, a systemd user unit works: `ExecStart=/usr/bin/python3 <plugin root>/scripts/dashboard.py 8787`, `Restart=always`, enabled with `systemctl --user enable --now`. Then bookmark http://127.0.0.1:8787/ (one page; tabs are `/#overview`, `/#sessions`, `/#rules`, `/#budget`).


Run `/kiasi:dashboard` to start `scripts/dashboard.py` (Python stdlib only:
`http.server` + a background thread) and get its URL. It serves the report
pages and their assets at `/`, the JSON reports at `/reports/<file>`, accepts
`POST /sync` to rebuild them immediately, and rebuilds on its own every 30
minutes. Run `/kiasi:sync` to rebuild without opening the dashboard.

The 5-hour and weekly plan limits in the dashboard header come from the
Claude Code status line, which is the only place Claude Code exposes them.
Run `/kiasi:limits setup` once to install the Kiasi status line: it copies
`scripts/statusline.py` to `~/.claude/kiasi/`, points `statusLine` in
`~/.claude/settings.json` at it (backing the file up once to
`settings.json.kiasi-bak`), and keeps any status line you already had
running, with the Kiasi part appended. `/kiasi:limits remove` puts your
previous status line back; `/kiasi:limits` alone reports the status. Run
`setup` again after a plugin update to refresh the copy.

## What it reads, and what it doesn't send anywhere

Kiasi reads every transcript file under `~/.claude/projects` (your own
conversation history, main sessions and subagents) to compute context size,
steps per prompt and the budget/lens reports. It writes only to the data
directory above and makes no network calls of any kind.

## Experimental: function hooks

The compaction pruner and `mcp__kiasi__search` (`hooks/kiasi.js` and
friends) use Claude Code's function-hook API, early access (2.1.282+) and not
yet part of the documented manifest reference. It may be held for reviewer
approval in the plugin directory, and may break on an engine update — watch
the debug log for "hooks module ... loaded" after upgrading. The plugin is
fully useful without it: the six classic hooks run regardless of
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS`.

## Configuration (userConfig / env)

Four values are `userConfig` in `.claude-plugin/plugin.json` (set via
`/config`) and can also be set directly as env vars read by
`scripts/constants.py`:

| userConfig key | env var | default |
|---|---|---|
| `output_cap_chars` | `CLAUDE_PLUGIN_OPTION_OUTPUT_CAP_CHARS` | 12000 |
| `turn_call_budget` | `CLAUDE_PLUGIN_OPTION_TURN_CALL_BUDGET` | 60 |
| `paste_refusal_chars` | `CLAUDE_PLUGIN_OPTION_PASTE_REFUSAL_CHARS` | 40000 |
| `compaction_window_text` | `CLAUDE_PLUGIN_OPTION_COMPACTION_WINDOW_TEXT` | "200000" |

## Requirements and tests

Python 3.8+ (stdlib only). Node 18+ only for
the experimental module. Linux/macOS; Claude Code 2.1.283+.

```
python3 -m unittest discover -s tests   # hook handlers, synthetic payloads
claude plugin test .                     # hooks/compact.test.ts, real engine
claude plugin validate .                 # manifest + hooks.json
npx -p typescript tsc --noEmit -p .      # type-check the hooks module
```

## Uninstall

`/plugin uninstall kiasi` (or remove the marketplace/`--plugin-dir` entry).
By default Claude Code deletes `${CLAUDE_PLUGIN_DATA}` when the plugin is
uninstalled from its last install location; pass `--keep-data` to keep it.
The `~/.claude/kiasi/` fallback directory (used only when scripts are run by
hand outside a hook) is never touched automatically — remove it yourself if
you want it gone.
