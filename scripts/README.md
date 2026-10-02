# Kiasi scripts

Python 3.10 or newer, standard library only. No install step.

## Layout

- `kiasi.py`: the hook entry point. Reads the hook payload from stdin, dispatches on `hook_event_name` and prints the JSON result.
- `core/`: the hook logic, imported by `kiasi.py`. `constants.py` (every threshold and path), `events.py` (event log, session state), `transcript.py` (transcript reading), `caps.py` (output caps, cleaning, archive), `prompt.py`, `turn.py`, `reads.py`, `session.py`, `launch.py`.
- `reports/`: `budget.py`, `lens.py` and `sync.py`, which rebuild the dashboard reports.
- `dashboard.py`, `search.py`, `sandbox.py`, `cleanup.py`, `benchmark.py`, `statusline.py`, `statusline_install.py`, `limits_data.py`: standalone tools, each run as `python3 scripts/<name>.py`.

## Which caller runs which script

| Caller | Script |
| --- | --- |
| `hooks/hooks.json` (UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure, PreCompact, Stop, SessionStart) | `kiasi.py` |
| `hooks/compact.js`, `hooks/quiet.js` (function hooks) | `kiasi.py` |
| `hooks/search.js` (`mcp__kiasi__search`) | `search.py` |
| `hooks/sandbox.js` (`mcp__kiasi__run`, `distill`, `fetch`) | `sandbox.py` |
| SessionStart (through `core/launch.py`) | `dashboard.py --ensure`, `cleanup.py --if-due` |
| `/kiasi:dashboard` | `dashboard.py --ensure` |
| `/kiasi:sync`, the dashboard every 30 minutes, `POST /sync` | `reports/sync.py`, which runs `reports/budget.py`, `reports/lens.py` and `cleanup.py` |
| `/kiasi:limits` | `statusline_install.py`, which installs `statusline.py` |
| the dashboard's `/limits` route | `limits_data.py` |

## What writes what in the data directory

The data directory is `${CLAUDE_PLUGIN_DATA}`, or the one recorded in `~/.claude/kiasi/data-dir`.

- `kiasi.py` (via `core/`): `kiasi.jsonl`, `sessions/`, `pastes/`, `outputs/`, `notes/`, `checkpoints/`
- `reports/budget.py`: `budget.json`
- `reports/lens.py`: `lens.json.gz`
- `reports/sync.py`: `sync.json`
- `dashboard.py`: `dashboard.json`, `dashboard.log`
- `cleanup.py`: `cleanup.json`, `cleanup.jsonl`, `cleanup.lock`, `trash/`
- `statusline.py` and `statusline_install.py`: `~/.claude/kiasi/` (the installed status line, chain file and pointer), plus the rate-limit readings the dashboard shows

## Tests

From the plugin root:

    python3 -m unittest discover -s tests
    claude plugin test .

Python tests patch the `constants` module object, so core modules import it as `from core import constants`.
