# Kiasi benchmark

Measured, not estimated: every row below is a shape-realistic tool output
generated deterministically by `scripts/benchmark.py` and replayed through
the **same handlers that run in a session** — the caps and the Bash clean in
`core/caps.py`, and the out-of-context `run`/`distill` tools in `sandbox.py`.
Reproduce it with:

    python3 scripts/benchmark.py

| scenario | rule | chars in | Claude Code alone | chars entered | reduction | reduction vs Claude Code | tokens kept off 10 later steps |
|---|---|---:|---:|---:|---:|---:|---:|
| Playwright page snapshot via MCP | MCP output cap | 55,999 | 215 | 6,180 | 89% | -2774% | -14,913 |
| GitHub issue list via MCP | MCP output cap | 105,223 | 215 | 6,181 | 94% | -2775% | -14,915 |
| npm install log (ANSI, progress bars) | Bash clean + cap | 42,800 | 2,215 | 3,569 | 92% | -61% | -3,385 |
| Session transcript Read (.jsonl) | outside-read cap | 43,539 | 43,539 | 8,170 | 81% | 81% | 88,422 |
| Test run through mcp__kiasi__run | sandbox digest | 64,578 | 10,000 | 2,082 | 97% | 79% | 19,795 |
| Count items in a big JSON via mcp__kiasi__distill | think in code | 180,485 | 5 | 5 | 100% | 0% | 0 |
| Unchanged source file read a second time | Read skip | 29,029 | 29,029 | 245 | 99% | 99% | 71,960 |
| Mid-size build log (ANSI, progress redraws) | Bash clean | 20,034 | 20,034 | 2,929 | 85% | 85% | 42,762 |
| Mid-size issue list via MCP | MCP output cap | 38,522 | 38,522 | 6,180 | 84% | 84% | 80,855 |

## The baseline

Claude Code limits tool output itself, so counting the raw output as the
saving overstates it. Each row therefore has two baselines: the raw output
(`chars in`), and **Claude Code alone**, what the same output puts in context
under Claude Code's documented defaults, through the tool path the scenario
really uses without Kiasi. The headline reduction is against Claude Code
alone, and it can be negative.

| path | default | setting | source |
|---|---|---|---|
| Bash: A successful command (exit 0, or exit 1 for grep, rg, find, diff, test, git diff, git grep) is returned inline up to about this size. | 30,000 chars | `BASH_MAX_OUTPUT_LENGTH` | https://code.claude.com/docs/en/tools-reference |
| Bash: Past that, a successful command arrives as a file path plus a preview of the first characters. | 2,000 chars | fixed | https://code.claude.com/docs/en/tools-reference |
| Bash: A failed command is returned inline up to about this size; past that, a head-and-tail excerpt of this size with no file path. | 10,000 chars | fixed | https://code.claude.com/docs/en/tools-reference |
| MCP: A text result over this size from a tool without anthropic/maxResultSizeChars is saved to a file and replaced by a message naming the path. | 50,000 chars | fixed | https://code.claude.com/docs/en/mcp |
| MCP: The token limit past which an MCP result is saved to a file and replaced by a path message. | 25,000 tokens | `MAX_MCP_OUTPUT_TOKENS` | https://code.claude.com/docs/en/mcp |
| MCP warning: Claude Code shows a warning above this size; the output is still passed through. | 10,000 tokens | `MAX_MCP_OUTPUT_TOKENS` | https://code.claude.com/docs/en/mcp |
| Read: One Read call returns the whole file up to this size; past it, the first page with a PARTIAL notice. | 25,000 tokens | `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | https://code.claude.com/docs/en/tools-reference |

How each row is modelled, with `215` chars for the path
message Claude Code inserts when it saves a result to a file (a realistic
message and path, not a quotation of Claude Code's own text):

- A successful Bash command (exit 0) is inline up to 30,000
  chars; past that, a file path plus the first 2,000 chars.
- A failed Bash command is inline up to 10,000 chars;
  past that, a 10,000-char head-and-tail excerpt with no
  file path. The test-run row exits 1, which is a failure.
- An MCP result over 50,000 chars (or 25,000 tokens)
  is saved to a file and replaced by a path message.
- A Read returns the whole file up to 25,000 tokens; tokens are
  converted at 4 chars per token.
- The `distill` row compares against a Bash one-liner that prints only the
  count, which is what Claude would usually run, so it shows no saving.

For outputs far past Claude Code's limits, Claude Code alone already spills to
a file and sends less than Kiasi does, so Kiasi sends more on those rows
(Playwright, GitHub, npm install). Kiasi differs where it does something else
with the output: a failing run keeps the failing lines plus a path instead of
a blind head and tail, outputs under Claude Code's limits are still trimmed
and stripped of colour codes and redraws, session transcripts are capped, and
an unchanged file read a second time is not sent again.

- **Chars entered is never zero and never a guess.** A capped output keeps a
  head plus a `[kiasi kept …]` pointer to the saved file, so the model can
  still recover any byte it actually needs (searchable with
  `mcp__kiasi__search`).
- **The last column is the real cost axis.** Claude Code re-sends the whole
  conversation every step, so a difference pays out on every later step of the
  session, in either direction. 10 later steps is an assumption
  here; the dashboard measures the true per-session figure and attributes
  savings with it.

## What this does not measure

- The inputs are synthetic: shape-realistic, generated here, not recorded
  from real sessions.
- The number of later steps (10) is an assumption, not an
  observation.
- There is no paired A/B on real tasks yet, so nothing here says whether the
  model finishes the same work with the shorter context.
- How cached re-reads count toward plan limits is not modelled.

Generated on 2026-10-11 by `scripts/benchmark.py` against the handlers in
this commit. Rerunning may shift byte counts slightly if the caps' constants
change; the table is rewritten with `--write`, and `--json PATH` writes the
data the website renders.
