import json
from pathlib import Path

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import caps  # noqa: E402
from core import session  # noqa: E402


class TestOutputCap(KiasiTestCase):
    def test_cap_cuts_and_saves(self):
        big_text = "line of output\n" * 2000  # well over CAP_OUTSIDE_READ_CHARS
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_use_id": "toolu_cap_1",
            "session_id": "sess-cap",
            "transcript_path": str(self.tmp / "missing-transcript.jsonl"),
            "tool_input": {"file_path": "/home/user/.claude/projects/whatever/some.jsonl"},
            "tool_response": big_text,
        }
        result = caps.handle_tool_output(payload)
        self.assertIsNotNone(result, "an over-cap outside read should return capped output")
        context = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("[kiasi kept", context)
        saved = list(constants.OUTPUT_DIR.glob("*toolu_cap_1*"))
        self.assertTrue(saved, "the full output should be saved under OUTPUT_DIR")
        self.assertEqual(saved[0].read_text(), big_text)


class TestFailureCut(KiasiTestCase):
    def test_keeps_failures_with_context(self):
        lines = [f"PASS test {i}" for i in range(300)]
        lines[150] = "FAIL tests/user.test.ts > rejects bad email"
        lines[151] = "  expected 400, got 200"
        shown = caps.failure_cut("\n".join(lines), "/tmp/x")
        self.assertIn("rejects bad email", shown)
        self.assertIn("expected 400, got 200", shown)
        self.assertNotIn("PASS test 100", shown)
        self.assertIn("PASS test 299", shown)
        self.assertLess(len(shown), 3000)

    def test_quiet_event_logged(self):
        import json
        session.handle_plugin_quiet({"hook_event_name": "PluginQuiet", "session_id": "s1", "before": "npm install", "after": "npm install --loglevel=warn", "applied": ["npm"]})
        record = json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])
        self.assertEqual(record["event"], "quiet")
        self.assertEqual(record["applied"], ["npm"])


class TestCleanOutput(KiasiTestCase):
    def bash(self, command, text):
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_clean", "session_id": "s-clean",
                "tool_input": {"command": command}, "tool_response": {"stdout": text, "stderr": ""}}

    def test_strips_colour_progress_and_repeats(self):
        text = "\x1b[32mok\x1b[0m\n10%\r50%\r100%\nsame\nsame\nsame\nsame\n\n\nend"
        self.assertEqual(caps.clean_output(text), "ok\n100%\nsame\n[kiasi collapsed 3 repeats of the line above]\n\n\nend")

    def test_cleans_bulk_output_and_saves_original(self):
        text = "\n".join(f"\x1b[33mwarn\x1b[0m building module {i}" for i in range(150))
        result = caps.handle_tool_output(self.bash("npm run build", text))
        shown = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertNotIn("\x1b", shown)
        self.assertIn("original output saved at", shown)
        record = json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])
        self.assertEqual(record["kind"], "clean")
        self.assertEqual(Path(record["saved_path"]).read_text(), text)

    def test_never_touches_selector_commands(self):
        text = "\n".join(["    }"] * 900)
        self.assertIsNone(caps.handle_tool_output(self.bash("cat src/app.ts", text)))
        self.assertIsNone(caps.handle_tool_output(self.bash("sed -n 1,900p src/app.ts", text)))

    def test_plain_non_bulk_output_is_left_alone(self):
        self.assertIsNone(caps.handle_tool_output(self.bash("python3 report.py", "row\n" * 900)))


class TestArchive(KiasiTestCase):
    def test_writes_numbered_sections_inside_outputs_only(self):
        path = caps.handle_archive_path({"session_id": "abcdef123456"})["path"]
        caps.handle_archive({"path": path, "items": ["first", "second"]})
        text = open(path).read()
        self.assertIn("=== #2 ===\nsecond", text)
        outside = constants.DATA_DIR / "elsewhere.txt"
        caps.handle_archive({"path": str(outside), "items": ["x"]})
        self.assertFalse(outside.exists())


class TestBashCap(KiasiTestCase):
    def payload(self, text, tool_use_id, stderr=""):
        response = {"stdout": text}
        if stderr:
            response["stderr"] = stderr
        return {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": tool_use_id,
                "tool_name": "Bash", "tool_input": {"command": "./scan.sh"}, "tool_response": response}

    def test_long_plain_output_gets_the_trimmed_digest(self):
        text = "\n".join(f"line {i}" for i in range(2000))
        out = caps.handle_tool_output(self.payload(text, "toolu_bash1"))
        shown = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("[kiasi trimmed", shown)
        self.assertIn("line 1999", shown, "the tail survives")
        self.assertTrue((constants.OUTPUT_DIR / "toolu_bash1.txt").exists())

    def test_failed_output_keeps_failure_lines(self):
        text = "\n".join(["ok step passed"] * 900 + ["FAIL: test_x", "AssertionError: 1 != 2"] + ["ok step passed"] * 900)
        out = caps.handle_tool_output(self.payload(text, "toolu_bash2", stderr="exit 1"))
        shown = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("AssertionError", shown)

    def test_single_line_monster_falls_back_to_head_cut(self):
        out = caps.handle_tool_output(self.payload("x" * 15_000, "toolu_bash3"))
        self.assertIn("[kiasi kept the first", out["hookSpecificOutput"]["updatedToolOutput"])

    def test_output_under_the_cap_passes(self):
        self.assertIsNone(caps.handle_tool_output(self.payload("y" * 11_000, "toolu_bash4")))


class TestMcpCap(KiasiTestCase):
    def test_big_mcp_output_is_capped_and_saved(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_mcp1",
                   "tool_name": "mcp__playwright__browser_snapshot", "tool_input": {},
                   "tool_response": {"content": "snap " * 4000}}
        result = caps.handle_tool_output(payload)
        shown = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("[kiasi kept the first 6000", shown)
        self.assertTrue((constants.OUTPUT_DIR / "toolu_mcp1.txt").exists())

    def test_mcp_list_of_text_blocks_is_capped(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_lt",
                   "tool_name": "mcp__playwright__browser_snapshot", "tool_input": {},
                   "tool_response": [{"type": "text", "text": "A" * 9000}]}
        result = caps.handle_tool_output(payload)
        self.assertIn("[kiasi kept", result["hookSpecificOutput"]["updatedToolOutput"])

    def test_mcp_image_blocks_pass_untouched(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_img",
                   "tool_name": "mcp__chrome__computer", "tool_input": {},
                   "tool_response": [{"type": "image", "data": "B" * 20_000}]}
        self.assertIsNone(caps.handle_tool_output(payload))

    def test_kiasi_own_tools_and_small_outputs_pass(self):
        own = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_own",
               "tool_name": "mcp__kiasi__search", "tool_input": {}, "tool_response": {"content": "hit " * 4000}}
        small = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_small",
                 "tool_name": "mcp__github__list_issues", "tool_input": {}, "tool_response": {"content": "ok"}}
        self.assertIsNone(caps.handle_tool_output(own))
        self.assertIsNone(caps.handle_tool_output(small))
