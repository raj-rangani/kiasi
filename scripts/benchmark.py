#!/usr/bin/env python3
"""Kiasi benchmark: replay shape-realistic tool outputs through the real pipeline.

Every number is measured, not estimated: the inputs are generated here,
deterministically, and fed through the same handlers that run in a session
(caps.handle_tool_output for the caps and the Bash clean, sandbox.run and
sandbox.distill for the out-of-context tools). Nothing touches a real data
dir; everything lands in a temp folder.

    python3 scripts/benchmark.py              # print the table
    python3 scripts/benchmark.py --write      # rewrite BENCHMARK.md
    python3 scripts/benchmark.py --json PATH  # write the data the website renders

Two baselines per row: the raw output, and what Claude Code alone would put
in context under its own documented output limits. Savings are reported
against the second. Claude Code re-sends the whole conversation every step,
so chars that enter the context cost on every later step of the session; the
last column assumes LATER_STEPS of them.
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import constants

LATER_STEPS = 10
GENERATED_ON = "2026-10-11"
COMMAND = "python3 scripts/benchmark.py"
CHARS_PER_TOKEN = constants.CHARS_PER_TOKEN
DOCS = "https://code.claude.com/docs/en"

BASH_INLINE_CHARS = 30_000
BASH_FAILURE_INLINE_CHARS = 10_000
BASH_PREVIEW_CHARS = 2_000
MCP_SPILL_CHARS = 50_000
MCP_TOKEN_LIMIT = 25_000
MCP_WARNING_TOKENS = 10_000
READ_TOKEN_LIMIT = 25_000

TOOL_RESULT_PATH = "/home/user/.claude/projects/-home-user-app/0b7c1c7e-5d1e-4c1a-9f2b-3a6d2f8e4c10/tool-results/toolu_01A2B3C4D5E6F7G8H9J1K2L3.txt"
PATH_MESSAGE = "Output too large. Full output saved to {path}. Read or search that file for the part you need."
PATH_MESSAGE_CHARS = len(PATH_MESSAGE.format(path=TOOL_RESULT_PATH))

LIMITS = [
    {"tool": "Bash", "value": BASH_INLINE_CHARS, "unit": "chars", "setting": "BASH_MAX_OUTPUT_LENGTH",
     "what": "A successful command (exit 0, or exit 1 for grep, rg, find, diff, test, git diff, git grep) is returned inline up to about this size.",
     "source": f"{DOCS}/tools-reference"},
    {"tool": "Bash", "value": BASH_PREVIEW_CHARS, "unit": "chars", "setting": None,
     "what": "Past that, a successful command arrives as a file path plus a preview of the first characters.",
     "source": f"{DOCS}/tools-reference"},
    {"tool": "Bash", "value": BASH_FAILURE_INLINE_CHARS, "unit": "chars", "setting": None,
     "what": "A failed command is returned inline up to about this size; past that, a head-and-tail excerpt of this size with no file path.",
     "source": f"{DOCS}/tools-reference"},
    {"tool": "MCP", "value": MCP_SPILL_CHARS, "unit": "chars", "setting": None,
     "what": "A text result over this size from a tool without anthropic/maxResultSizeChars is saved to a file and replaced by a message naming the path.",
     "source": f"{DOCS}/mcp"},
    {"tool": "MCP", "value": MCP_TOKEN_LIMIT, "unit": "tokens", "setting": "MAX_MCP_OUTPUT_TOKENS",
     "what": "The token limit past which an MCP result is saved to a file and replaced by a path message.",
     "source": f"{DOCS}/mcp"},
    {"tool": "MCP warning", "value": MCP_WARNING_TOKENS, "unit": "tokens", "setting": "MAX_MCP_OUTPUT_TOKENS",
     "what": "Claude Code shows a warning above this size; the output is still passed through.",
     "source": f"{DOCS}/mcp"},
    {"tool": "Read", "value": READ_TOKEN_LIMIT, "unit": "tokens", "setting": "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS",
     "what": "One Read call returns the whole file up to this size; past it, the first page with a PARTIAL notice.",
     "source": f"{DOCS}/tools-reference"},
]

NOTE_NPM = ("Exit 0, so Claude Code alone returns a file path and the first 2,000 chars; Kiasi cleans the log and "
            "keeps a head, the error lines and a tail, so it sends more here.")
NOTE_MCP_SPILL = ("Over 50,000 chars Claude Code alone saves the result to a file and sends only a path message, "
                  "so it sends less than Kiasi's capped head.")
NOTE_READ_OUTSIDE = ("The file is under Claude Code's 25,000-token Read limit, so Claude Code alone sends all of it; "
                     "Kiasi caps session transcripts and other files under ~/.claude.")
NOTE_TEST = ("A failing test run exits 1, which is a failure for Claude Code: past 10,000 chars it sends a blind head and "
             "tail with no path. Kiasi's digest keeps the failing lines and a path.")
NOTE_DISTILL = ("Without Kiasi, Claude would usually run a one-liner through Bash that prints just the count; "
                "the baseline is that tiny output, so there is no saving against it.")
NOTE_REREAD = ("Claude Code alone re-sends the whole unchanged file on every re-read in the same conversation; "
               "Kiasi answers with a pointer to the copy already in context.")
NOTE_MID_BASH = ("Under 30,000 chars, so Claude Code alone sends all of it, colour codes and progress redraws included; "
                 "Kiasi strips the noise.")
NOTE_MID_MCP = ("Under 50,000 chars, so Claude Code alone sends all of it; Kiasi keeps a head and a path "
                "to the full result.")

def point_at_tmp():
    tmp = Path(tempfile.mkdtemp(prefix="kiasi-bench-"))
    constants.DATA_DIR = tmp
    constants.LOG_DIR = tmp
    constants.EVENT_LOG = tmp / "kiasi.jsonl"
    constants.SESSION_DIR = tmp / "sessions"
    constants.PASTE_DIR = tmp / "pastes"
    constants.OUTPUT_DIR = tmp / "outputs"
    constants.NOTES_DIR = tmp / "notes"
    constants.CHECKPOINT_DIR = tmp / "checkpoints"
    constants.BUDGET_FILE = tmp / "budget.json"
    return tmp


# --- deterministic, shape-realistic inputs ---------------------------------

ROLES = ("button", "link", "textbox", "img", "listitem", "heading", "cell")


def playwright_snapshot(target=56_000):
    lines, i = ["- Page snapshot", "- document [ref=e1]:"], 2
    while sum(len(line) + 1 for line in lines) < target:
        role = ROLES[i % len(ROLES)]
        depth = 1 + (i % 5)
        lines.append(f"{'  ' * depth}- {role} \"Item {i} — product tile with price and badge\" [ref=e{i}]")
        i += 1
    return "\n".join(lines)


def github_issue_list(count=170):
    issues = [{"number": 4000 + i, "state": "open" if i % 3 else "closed",
               "title": f"Intermittent timeout in worker pool when queue depth exceeds limit ({i})",
               "labels": [{"name": "bug"}, {"name": f"area/{ROLES[i % len(ROLES)]}"}],
               "user": {"login": f"dev{i % 23}"}, "comments": i % 17,
               "body": f"Seen on CI run {i}. Steps: enqueue 500 jobs, kill a worker, observe the drain stall. " * 4}
              for i in range(count)]
    return json.dumps(issues, indent=1)


def npm_install_log(packages=420):
    lines = []
    for i in range(packages):
        lines.append(f"\x1b[90mnpm\x1b[0m \x1b[32mhttp fetch\x1b[0m GET 200 https://registry.npmjs.org/pkg-{i} {30 + i % 400}ms")
        if i % 7 == 0:
            lines.append(f"[{'#' * (i % 20)}{'.' * (20 - i % 20)}] \\ reify:pkg-{i}: timing\r" * 3)
    lines.append("\nadded 1423 packages, and audited 1424 packages in 31s\n\n73 packages are looking for funding")
    return "\n".join(lines)


def session_transcript(turns=60):
    lines = []
    for i in range(turns):
        lines.append(json.dumps({"type": "assistant" if i % 2 else "user", "timestamp": f"2026-10-01T10:{i:02d}:00Z",
                                 "message": {"role": "assistant" if i % 2 else "user",
                                             "content": f"Working through step {i}: ran the suite, 3 failures left in the parser module. " * 8}}))
    return "\n".join(lines)


def pytest_log(tests=900):
    lines = [f"tests/test_module_{i % 40}.py::test_case_{i} PASSED{' ' * 20}[{i * 100 // tests:3d}%]" for i in range(tests)]
    lines[500] = ("tests/test_parser.py::test_unicode FAILED\n"
                  "    def test_unicode():\n>       assert parse(b'\\xff') == []\nE       UnicodeDecodeError: invalid start byte")
    lines.append(f"\n==== 1 failed, {tests - 1} passed in 42.17s ====")
    return "\n".join(lines)


def data_json(items=1500):
    return json.dumps({"items": [{"id": i, "sku": f"SKU-{i:06d}", "price": round(3 + i * 0.07, 2),
                                  "stock": i % 90, "name": f"Product {i} long descriptive name for realism"}
                                 for i in range(items)]})


def source_file(target=29_000):
    lines, i = [], 0
    while sum(len(line) + 1 for line in lines) < target:
        lines.append(f"    def handle_{i}(self, request): return self.registry[{i}].dispatch(request, retries={i % 5})")
        i += 1
    return "\n".join(lines)


def build_log(target=20_000):
    lines, i = ["\x1b[1mnpm run build\x1b[0m"], 0
    while sum(len(line) + 1 for line in lines) < target:
        lines.append(f"\x1b[36mtransforming\x1b[0m src/components/widget_{i}.tsx \x1b[2m({i % 90} kB)\x1b[0m")
        if i % 4 == 0:
            lines.append(f"[{'=' * (i % 20)}{' ' * (20 - i % 20)}] bundling chunk-{i}\r" * 3)
        i += 1
    lines.append("\x1b[32mbuilt in 12.4s\x1b[0m")
    return "\n".join(lines)


def linear_issue_list(count=75):
    return json.dumps([{"id": f"ENG-{i}", "title": f"Checkout form loses state after payment retry ({i})",
                        "state": "In Progress" if i % 2 else "Todo", "assignee": f"dev{i % 11}",
                        "description": f"Reproduced on staging build {i}; the form resets after the second retry. " * 5}
                       for i in range(count)], indent=1)


# --- scenarios: every one goes through the real handler --------------------

def claude_code_alone(tool, chars_in, exit_status):
    if tool == "Bash":
        if exit_status == 0:
            if chars_in <= BASH_INLINE_CHARS:
                return chars_in, "the whole output"
            return BASH_PREVIEW_CHARS + PATH_MESSAGE_CHARS, "a file path and the first 2,000 chars"
        if chars_in <= BASH_FAILURE_INLINE_CHARS:
            return chars_in, "the whole output"
        return BASH_FAILURE_INLINE_CHARS, "a 10,000-char head and tail, no file path"
    if tool == "MCP":
        if chars_in > MCP_SPILL_CHARS or chars_in > MCP_TOKEN_LIMIT * CHARS_PER_TOKEN:
            return PATH_MESSAGE_CHARS, "a file path"
        return chars_in, "the whole output"
    if chars_in > READ_TOKEN_LIMIT * CHARS_PER_TOKEN:
        return READ_TOKEN_LIMIT * CHARS_PER_TOKEN, "the first 25,000 tokens and a PARTIAL notice"
    return chars_in, "the whole output"


def row(name, rule, tool, exit_status, chars_in, chars_kiasi, kiasi_gets, note, claude_override=None):
    chars_claude_code, claude_gets = claude_override or claude_code_alone(tool, chars_in, exit_status)
    return {"name": name, "rule": rule, "tool": tool, "exit_status": exit_status, "chars_in": chars_in,
            "chars_claude_code": chars_claude_code, "chars_kiasi": chars_kiasi,
            "claude_code_gets": claude_gets, "kiasi_gets": kiasi_gets, "note": note}


def entered(result, fallback):
    if result is None:
        return fallback
    return len(result["hookSpecificOutput"]["updatedToolOutput"])


def mcp_payload(tool, text):
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_use_id": f"toolu_bench_{tool[-8:]}",
            "session_id": "bench", "tool_input": {}, "tool_response": {"content": text}}


def bash_payload(tool_use_id, command, text, exit_code):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": tool_use_id, "session_id": "bench",
            "tool_input": {"command": command}, "tool_response": {"stdout": text, "stderr": "", "exitCode": exit_code}}


def read_payload(tool_use_id, path, text, response_text):
    return {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_use_id": tool_use_id, "session_id": "bench",
            "transcript_path": str(Path(path).parent / "none.jsonl"), "tool_input": {"file_path": path},
            "tool_response": response_text}


def scenarios(tmp):
    from core import caps, reads
    import sandbox

    rows = []

    text = playwright_snapshot()
    rows.append(row("Playwright page snapshot via MCP", "MCP output cap", "MCP", 0, len(text),
                    entered(caps.handle_tool_output(mcp_payload("mcp__playwright__browser_snapshot", text)), len(text)),
                    "the first 6,000 chars and a file path", NOTE_MCP_SPILL))

    text = github_issue_list()
    rows.append(row("GitHub issue list via MCP", "MCP output cap", "MCP", 0, len(text),
                    entered(caps.handle_tool_output(mcp_payload("mcp__github__list_issues", text)), len(text)),
                    "the first 6,000 chars and a file path", NOTE_MCP_SPILL))

    text = npm_install_log()
    rows.append(row("npm install log (ANSI, progress bars)", "Bash clean + cap", "Bash", 0, len(text),
                    entered(caps.handle_tool_output(bash_payload("toolu_bench_npm", "npm install", text, 0)), len(text)),
                    "a cleaned head, the error lines and a tail, and a file path", NOTE_NPM))

    text = session_transcript()
    session_path = "/home/user/.claude/projects/demo/session.jsonl"
    rows.append(row("Session transcript Read (.jsonl)", "outside-read cap", "Read", 0, len(text),
                    entered(caps.handle_tool_output(read_payload("toolu_bench_jsonl", session_path, text, text)), len(text)),
                    "the first 8,000 chars and a file path", NOTE_READ_OUTSIDE))

    log = tmp / "pytest.log"
    log.write_text(pytest_log())
    buf = io.StringIO()
    with redirect_stdout(buf):
        sandbox.run(SimpleNamespace(command=f"cat {log}", timeout=30, session="bench"))
    rows.append(row("Test run through mcp__kiasi__run", "sandbox digest", "Bash", 1, log.stat().st_size, len(buf.getvalue()),
                    "a digest with the failing lines and a file path", NOTE_TEST))

    data = tmp / "data.json"
    data.write_text(data_json())
    buf = io.StringIO()
    with redirect_stdout(buf):
        sandbox.distill(SimpleNamespace(
            language="python", code="import json,sys;print(len(json.load(open(sys.argv[1]))['items']))",
            file=[str(data)], timeout=30, session="bench"))
    rows.append(row("Count items in a big JSON via mcp__kiasi__distill", "think in code", "Bash", 0, data.stat().st_size,
                    len(buf.getvalue()), "the count", NOTE_DISTILL,
                    claude_override=(len(buf.getvalue()), "the count from a one-liner")))

    source = tmp / "src" / "registry.py"
    source.parent.mkdir()
    source.write_text(source_file())
    first = read_payload("toolu_bench_src1", str(source), "", {"type": "text", "file": {"content": source.read_text()}})
    reads.track_reads(first, False)
    again = dict(first, tool_use_id="toolu_bench_src2")
    denied = reads.handle_read_check(again)
    rows.append(row("Unchanged source file read a second time", "Read skip", "Read", 0, len(source.read_text()),
                    len(denied["hookSpecificOutput"]["permissionDecisionReason"]),
                    "a one-line pointer to the copy already in context", NOTE_REREAD))

    text = build_log()
    rows.append(row("Mid-size build log (ANSI, progress redraws)", "Bash clean", "Bash", 0, len(text),
                    entered(caps.handle_tool_output(bash_payload("toolu_bench_build", "npm run build", text, 0)), len(text)),
                    "the log without colour codes and repeated lines, and a file path", NOTE_MID_BASH))

    text = linear_issue_list()
    rows.append(row("Mid-size issue list via MCP", "MCP output cap", "MCP", 0, len(text),
                    entered(caps.handle_tool_output(mcp_payload("mcp__linear__list_issues", text)), len(text)),
                    "the first 6,000 chars and a file path", NOTE_MID_MCP))

    return rows


def percent(whole, part):
    return (200 * (whole - part) + whole) // (2 * max(1, whole))


def versus(claude_code, kiasi):
    if kiasi == claude_code:
        return "same"
    ratio = max(claude_code, kiasi) / max(1, min(claude_code, kiasi))
    size = f"{ratio:,.0f}" if ratio >= 100 else f"{ratio:.1f}"
    return f"{size}× {'less' if kiasi < claude_code else 'more'}"


def tokens_kept_off(item, steps=LATER_STEPS):
    return (item["chars_claude_code"] - item["chars_kiasi"]) * steps // CHARS_PER_TOKEN


def table(rows):
    out = ["| scenario | rule | chars in | Claude Code alone | chars entered | reduction | Kiasi vs Claude Code alone "
           f"| tokens kept off {LATER_STEPS} later steps |",
           "|---|---|---:|---:|---:|---:|---:|---:|"]
    for item in rows:
        out.append(f"| {item['name']} | {item['rule']} | {item['chars_in']:,} | {item['chars_claude_code']:,} "
                   f"| {item['chars_kiasi']:,} | {percent(item['chars_in'], item['chars_kiasi'])}% "
                   f"| {versus(item['chars_claude_code'], item['chars_kiasi'])} | {tokens_kept_off(item):,} |")
    return "\n".join(out)


def limits_table():
    out = ["| path | default | setting | source |", "|---|---|---|---|"]
    for limit in LIMITS:
        setting = f"`{limit['setting']}`" if limit["setting"] else "fixed"
        out.append(f"| {limit['tool']}: {limit['what']} | {limit['value']:,} {limit['unit']} | {setting} | {limit['source']} |")
    return "\n".join(out)


def payload(rows):
    return {"generated": GENERATED_ON, "command": COMMAND, "later_steps": LATER_STEPS,
            "chars_per_token": CHARS_PER_TOKEN, "limits": LIMITS, "scenarios": rows}


def report(rows):
    return f"""# Kiasi benchmark

Measured, not estimated: every row below is a shape-realistic tool output
generated deterministically by `scripts/benchmark.py` and replayed through
the **same handlers that run in a session** — the caps and the Bash clean in
`core/caps.py`, and the out-of-context `run`/`distill` tools in `sandbox.py`.
Reproduce it with:

    {COMMAND}

{table(rows)}

## The baseline

Claude Code limits tool output itself, so counting the raw output as the
saving overstates it. Each row therefore has two baselines: the raw output
(`chars in`), and **Claude Code alone**, what the same output puts in context
under Claude Code's documented defaults, through the tool path the scenario
really uses without Kiasi. The headline is against Claude Code alone, as a
multiple, and on some rows Kiasi sends more.

{limits_table()}

How each row is modelled, with `{PATH_MESSAGE_CHARS}` chars for the path
message Claude Code inserts when it saves a result to a file (a realistic
message and path, not a quotation of Claude Code's own text):

- A successful Bash command (exit 0) is inline up to {BASH_INLINE_CHARS:,}
  chars; past that, a file path plus the first {BASH_PREVIEW_CHARS:,} chars.
- A failed Bash command is inline up to {BASH_FAILURE_INLINE_CHARS:,} chars;
  past that, a {BASH_FAILURE_INLINE_CHARS:,}-char head-and-tail excerpt with no
  file path. The test-run row exits 1, which is a failure.
- An MCP result over {MCP_SPILL_CHARS:,} chars (or {MCP_TOKEN_LIMIT:,} tokens)
  is saved to a file and replaced by a path message.
- A Read returns the whole file up to {READ_TOKEN_LIMIT:,} tokens; tokens are
  converted at {CHARS_PER_TOKEN} chars per token.
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
  session, in either direction. {LATER_STEPS} later steps is an assumption
  here; the dashboard measures the true per-session figure and attributes
  savings with it.

## What this does not measure

- The inputs are synthetic: shape-realistic, generated here, not recorded
  from real sessions.
- The number of later steps ({LATER_STEPS}) is an assumption, not an
  observation.
- There is no paired A/B on real tasks yet, so nothing here says whether the
  model finishes the same work with the shorter context.
- How cached re-reads count toward plan limits is not modelled.

Generated on {GENERATED_ON} by `scripts/benchmark.py` against the handlers in
this commit. Rerunning may shift byte counts slightly if the caps' constants
change; the table is rewritten with `--write`, and `--json PATH` writes the
data the website renders.
"""


def main():
    tmp = point_at_tmp()
    rows = scenarios(tmp)
    if "--json" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--json") + 1])
        target.write_text(json.dumps(payload(rows), indent=2, ensure_ascii=False) + "\n")
        print(f"{target} written")
    elif "--write" in sys.argv:
        (Path(__file__).resolve().parent.parent / "BENCHMARK.md").write_text(report(rows))
        print(f"BENCHMARK.md written; fixtures and outputs under {tmp}")
    else:
        print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
