# Kiasi benchmark

Measured, not estimated: every row below is a shape-realistic tool output
generated deterministically by `scripts/benchmark.py` and replayed through
the **same handlers that run in a session** — the caps and the Bash clean in
`kiasi.py`, and the out-of-context `run`/`distill` tools in `sandbox.py`.
Reproduce it with:

    python3 scripts/benchmark.py

| scenario | rule | chars in | chars entered | reduction | tokens saved over 10 later steps |
|---|---|---:|---:|---:|---:|
| Playwright page snapshot via MCP | MCP output cap | 55,999 | 6,180 | 88% | 124,547 |
| GitHub issue list via MCP | MCP output cap | 105,223 | 6,181 | 94% | 247,605 |
| npm install log (ANSI, progress bars) | Bash clean + cap | 42,800 | 3,569 | 91% | 98,077 |
| Session transcript Read (.jsonl) | outside-read cap | 43,539 | 8,170 | 81% | 88,422 |
| test run through mcp__kiasi__run | sandbox digest | 64,578 | 2,082 | 96% | 156,240 |
| count items in a big JSON via mcp__kiasi__distill | think in code | 180,485 | 5 | 99% | 451,200 |

Two things other benchmarks leave out:

- **Chars entered is never zero and never a guess.** A capped output keeps a
  head plus a `[kiasi kept …]` pointer to the saved file, so the model can
  still recover any byte it actually needs (searchable with
  `mcp__kiasi__search`). The reduction shown is what remains after that.
- **The last column is the real cost axis.** Claude Code re-sends the whole
  conversation every step, so a cut pays out on every later step of the
  session. 10 later steps is an assumption here; the dashboard measures the
  true per-session figure and attributes savings with it.

Generated on 2026-10-01 by `scripts/benchmark.py` against the handlers in this
commit. Rerunning may shift byte counts slightly if the caps' constants
change; the table is rewritten with `--write`.
