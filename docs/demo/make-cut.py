#!/usr/bin/env python3
"""Run the real Bash output cap on stdin and write what Claude would see to cut.txt next to this file.

    npm ls --all 2>&1 | python3 docs/demo/make-cut.py

The handler saves the full output under the Kiasi data dir like a real session would; the demo session id
is "demo" so the stray file and event are easy to remove afterwards."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
from core import caps  # noqa: E402

text = sys.stdin.read()
result = caps.handle_tool_output({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_01Rk7VqM",
                                  "session_id": "demo", "tool_input": {"command": "npm ls --all"},
                                  "tool_response": {"stdout": text, "stderr": ""}})
shown = result["hookSpecificOutput"]["updatedToolOutput"] if result else text
pathlib.Path(__file__).with_name("cut.txt").write_text(shown)
print(f"{len(text):,} chars in, {len(shown):,} shown; written to docs/demo/cut.txt")
