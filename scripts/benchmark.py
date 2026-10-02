#!/usr/bin/env python3
"""Kiasi benchmark: replay shape-realistic tool outputs through the real pipeline.

Every number is measured, not estimated: the inputs are generated here,
deterministically, and fed through the same handlers that run in a session
(caps.handle_tool_output for the caps and the Bash clean, sandbox.run and
sandbox.distill for the out-of-context tools). Nothing touches a real data
dir; everything lands in a temp folder.

    python3 scripts/benchmark.py            # print the table
    python3 scripts/benchmark.py --write    # rewrite BENCHMARK.md

The honest multiplier: Claude Code re-sends the whole conversation every
step, so chars that enter the context cost on every later step of the
session. The table's last column assumes 10 later steps; the dashboard
measures the real figure per session.
"""
import io
import json
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import constants

LATER_STEPS = 10


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


# --- scenarios: every one goes through the real handler --------------------

def entered(result, fallback):
    if result is None:
        return fallback
    return len(result["hookSpecificOutput"]["updatedToolOutput"])


def mcp_payload(tool, text):
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_use_id": f"toolu_bench_{tool[-8:]}",
            "session_id": "bench", "tool_input": {}, "tool_response": {"content": text}}


def scenarios(tmp):
    from core import caps
    import sandbox

    rows = []

    text = playwright_snapshot()
    rows.append(("Playwright page snapshot via MCP", "MCP output cap", len(text),
                 entered(caps.handle_tool_output(mcp_payload("mcp__playwright__browser_snapshot", text)), len(text))))

    text = github_issue_list()
    rows.append(("GitHub issue list via MCP", "MCP output cap", len(text),
                 entered(caps.handle_tool_output(mcp_payload("mcp__github__list_issues", text)), len(text))))

    text = npm_install_log()
    shown = entered(caps.handle_tool_output({
        "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_bench_npm",
        "session_id": "bench", "tool_input": {"command": "npm install"},
        "tool_response": {"stdout": text, "stderr": ""}}), len(text))
    rows.append(("npm install log (ANSI, progress bars)", "Bash clean + cap", len(text), shown))

    text = session_transcript()
    rows.append(("Session transcript Read (.jsonl)", "outside-read cap", len(text),
                 entered(caps.handle_tool_output({
                     "hook_event_name": "PostToolUse", "tool_name": "Read", "tool_use_id": "toolu_bench_jsonl",
                     "session_id": "bench", "transcript_path": str(tmp / "none.jsonl"),
                     "tool_input": {"file_path": "/home/user/.claude/projects/demo/session.jsonl"},
                     "tool_response": text}), len(text))))

    log = tmp / "pytest.log"
    log.write_text(pytest_log())
    buf = io.StringIO()
    with redirect_stdout(buf):
        sandbox.run(SimpleNamespace(command=f"cat {log}", timeout=30, session="bench"))
    rows.append(("test run through mcp__kiasi__run", "sandbox digest", log.stat().st_size, len(buf.getvalue())))

    data = tmp / "data.json"
    data.write_text(data_json())
    buf = io.StringIO()
    with redirect_stdout(buf):
        sandbox.distill(SimpleNamespace(
            language="python", code="import json,sys;print(len(json.load(open(sys.argv[1]))['items']))",
            file=[str(data)], timeout=30, session="bench"))
    rows.append(("count items in a big JSON via mcp__kiasi__distill", "think in code", data.stat().st_size, len(buf.getvalue())))

    return rows


def table(rows):
    out = ["| scenario | rule | chars in | chars entered | reduction | tokens saved over 10 later steps |",
           "|---|---|---:|---:|---:|---:|"]
    for name, rule, chars_in, chars_shown in rows:
        cut = max(0, chars_in - chars_shown)
        out.append(f"| {name} | {rule} | {chars_in:,} | {chars_shown:,} | {cut * 100 // max(1, chars_in)}% "
                   f"| {cut * LATER_STEPS // constants.CHARS_PER_TOKEN:,} |")
    return "\n".join(out)


def report(rows):
    stamp = time.strftime("%Y-%m-%d")
    return f"""# Kiasi benchmark

Measured, not estimated: every row below is a shape-realistic tool output
generated deterministically by `scripts/benchmark.py` and replayed through
the **same handlers that run in a session** — the caps and the Bash clean in
`core/caps.py`, and the out-of-context `run`/`distill` tools in `sandbox.py`.
Reproduce it with:

    python3 scripts/benchmark.py

{table(rows)}

Two things other benchmarks leave out:

- **Chars entered is never zero and never a guess.** A capped output keeps a
  head plus a `[kiasi kept …]` pointer to the saved file, so the model can
  still recover any byte it actually needs (searchable with
  `mcp__kiasi__search`). The reduction shown is what remains after that.
- **The last column is the real cost axis.** Claude Code re-sends the whole
  conversation every step, so a cut pays out on every later step of the
  session. 10 later steps is an assumption here; the dashboard measures the
  true per-session figure and attributes savings with it.

Generated on {stamp} by `scripts/benchmark.py` against the handlers in this
commit. Rerunning may shift byte counts slightly if the caps' constants
change; the table is rewritten with `--write`.
"""


def main():
    tmp = point_at_tmp()
    rows = scenarios(tmp)
    if "--write" in sys.argv:
        (Path(__file__).resolve().parent.parent / "BENCHMARK.md").write_text(report(rows))
        print(f"BENCHMARK.md written; fixtures and outputs under {tmp}")
    else:
        print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
