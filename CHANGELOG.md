# Changelog

### Unreleased

- Website at https://raj-rangani.github.io/kiasi/ (`docs/index.html`, served by GitHub Pages from `/docs`): rules, dashboard tour, install steps, privacy and the full reference.
- README trimmed to the pitch, quick start and a link to the website, with the new Kiasi logo (`docs/logo-light.svg`, `docs/logo-dark.svg`).
- README rewritten for first-time visitors: quick start, a table of every rule, the commands, and dashboard screenshots (`docs/`, fictional sample data).

## 0.2.0 - 2026-09-30

- Dashboard server answers only requests whose Host is 127.0.0.1 or localhost (a page on another site cannot reach it through DNS rebinding), and runs `POST /sync` only when the Origin or Referer is the dashboard itself.
- Tests for the report maths: one step per request id in budget.py and lens.py, subagent billing, the day window, compaction detection, typed prompts vs tool results, weighted period means and the before-and-after factor; lens and budget are checked to agree.
- `/kiasi:limits setup|remove|status` installs the Kiasi status line (the only source of the plan limits on the dashboard), keeps any status line you had running before it, backs up `settings.json` once, and restores the previous status line on remove. The dashboard's empty limits hint points to it.
- The type-check runs from the repository root with `allowJs`, so it checks the hooks module against the JS it imports; two type errors in `hooks/compact.test.ts` fixed. It runs locally only: `.claude-plugin/types/` (the early-access declarations Claude Code writes when it loads the plugin) is no longer committed, so CI runs the Python tests alone.
- Removed `scripts/ankush.py`, an unused copy of `scripts/kiasi.py` left from the rename; stale systemd wording removed from the Sync button.
- `SHIPPING.md` (internal notes) is no longer part of the published repository.


- Loop check: a new `PostToolUseFailure` hook counts failed tool calls toward the turn budget (they were not counted before) and tells Claude to stop retrying when the same call fails 3 times in a turn, or the same command fails 5 times with different arguments. Logged as `loop`.
- State after compaction: when a session restarts from a compaction, Kiasi re-injects the task, edited files, commands still failing at their last attempt, saved outputs and checklists, read from the transcript. Logged as `state`.
- Output cleaning: build, install, test, search and other bulk Bash output, and any output with colour codes or progress bars, has colour codes and `\r` redraws removed and runs of 3+ identical lines collapsed before any cap. File-reading commands (`cat`, `sed`, `head`, `git diff`, …) are never touched. The original is saved; a cleaning-only cut is a cap of kind `clean`.
- Dashboard: figures that come from a formula carry an "est." tag with the formula as tooltip; the Rules tab has a Loop check card.

- Renamed from Ankush to Kiasi (Swahili: measure, moderation). Commands are now `/kiasi:dashboard` and `/kiasi:sync`, the search tool is `mcp__kiasi__search`, and markers read `[kiasi kept …]`, `[kiasi trimmed …]`, `[kiasi pruned …]`. On first run the old data directory is moved to the new name and a link is left at the old path, so markers already in past transcripts still open their saved files.

- The detail panel is now a proper modal drawer: the page behind it cannot scroll (with no sideways shift), it is inert to keyboard and screen readers, focus moves to the panel and returns to the opener on close, Tab cycles inside the panel, Escape and the backdrop close it, and reduced-motion preferences are honoured.
- Previous and next buttons (and the arrow keys) step through rules or sessions without closing the panel; a sticky row of section chips jumps within the panel and tracks scrolling.

- Compact detail panel: narrower, smaller type and tighter spacing so a rule or session detail fits with less scrolling.
- Thin, quiet scrollbars everywhere, a stable scrollbar gutter, and no body scroll lock when the panel opens, so the page never shifts sideways.

- Rule and session details open in a slide-over panel from the right (a full-screen sheet on phones) instead of expanding inline below the cards. Close with the button, Escape, the backdrop, or a tab change; the hash still deep-links to an open panel.

- Rule cards open a detail sheet: fired per day (bars, with the tokens avoided that day above each), the evidence table for that rule (tools capped and cut sizes; every compaction the pruner handled; every warning and stop with compliance; every re-read check; every paste), and the rule's log with a filter and the raw record behind each row. Deep link `/#rules/<key>`.
- Session detail is fuller: facts, seven headline numbers, a taller trajectory chart, a bill split by prompt band (under the warning / warned / stopped), a prompts table (start time, context at start, steps, cost bar, warned or stopped) folded past 20 rows, and every Kiasi action in the session with its raw record on click. lens.py now writes `prompt_rows` per session (prompt text is never logged).

- Sessions, Rules and Actions rethought. Sessions drops its stat row and shows the eight costliest sessions plus a compact table (steps, mean context, compactions, avoided, bill). Rules is now one card per rule (output cap, compaction pruner, turn budget, re-read check, paste manager, notes and recall) with fired count, tokens avoided, a one-line fact and a log link that opens that rule's events with the raw record behind each row; the steps-per-prompt chart stays. The Actions tab is gone; `/#actions` opens Rules.

- Budget fix: budget.py counted every transcript line of a model response (one per content block) as a step, so its steps, re-read totals and per-day bill were about double. It now keeps one entry per request id like lens.py, and filters steps by day rather than by transcript file age, so Budget and Overview agree.
- Dashboard renamed to plain "Kiasi". Overview drops the per-mechanism day chart and the latest-actions list, and hides bookkeeping rows in the mechanism table. Sessions hides the detail sheet until a session is picked and folds sessions under 5 steps behind a link. Rules folds compactions from before the plugin was loaded behind a link. Actions hides notes, recalls and subagent model rows until "bookkeeping rows" is ticked. Budget now has three sections: re-read per day, by project (full width, with steps and share) and biggest single outputs (full width, session column, paths shortened with ellipsis); the session table and the actions card it duplicated are gone.

- The dashboard is one page. `dashboard/index.html` holds all five views (Overview, Sessions, Rules, Actions, Budget); tabs switch by URL hash (`/#sessions`, `/#sessions/<id>`) without a reload, both reports load once and every view renders from the same data, and Sync refreshes all views together. The old `<view>.html` files redirect to their tab. View scripts register through `registerView(name, render, source)` in `assets/lens-common.js`.

- Overview opens with a before-and-after block: context re-sent per step, mean context, turns over 200 k and re-read per day, for the days before Kiasi was switched on against the days after, with the implied factor by which the weekly limit lasts longer. Built from budget.json's per-day rows and the first logged event.
- The daily bill chart marks the install day.
- Glossary for "re-read" and "avoided" in the Overview tagline; friendlier empty state before the first report.
- Hand-run scripts follow the data-dir pointer the SessionStart hook writes, so the dashboard and the hooks share one directory.

## 0.1.0 - 2026-09-28

First shippable release, split out of the context-governor prototype.

- Six classic hooks (UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SessionStart) ship as `hooks/hooks.json`, running `scripts/kiasi.py` at `${CLAUDE_PLUGIN_ROOT}`.
- All state (events log, sessions, pastes, outputs, notes, checkpoints, reports, sync status, search index) moved out of the plugin root to `${CLAUDE_PLUGIN_DATA}` (falls back to `~/.claude/kiasi/` when unset).
- SessionStart injects the context rules as `additionalContext` (`rules.md`) instead of requiring them in the user's `CLAUDE.md`, plus a warning when `CLAUDE_CODE_AUTO_COMPACT_WINDOW` or `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS` is unset.
- Experimental function-hook module (`hooks/kiasi.js`) provides a deterministic compaction pruner and the `mcp__kiasi__search` tool, gated behind `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`.
- Local dashboard (`scripts/dashboard.py`, stdlib only) replaces the old Apache + PHP + systemd sync path: serves the report pages, `/reports/<file>` JSON, and `POST /sync`.
- `commands/dashboard.md` and `commands/sync.md` slash commands.
- Python (`tests/test_kiasi.py`) and TypeScript (`hooks/compact.test.ts`) test suites; GitHub Actions CI.
- Renamed throughout: `governor` to `kiasi`, `[governor kept/trimmed]` to `[kiasi kept/trimmed]`, `mcp__context-governor__search` to `mcp__kiasi__search`, `governor.jsonl` to `kiasi.jsonl`.
