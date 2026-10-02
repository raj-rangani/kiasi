import json
import os
import unittest

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import reads  # noqa: E402


class TestRouting(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.before = os.environ.get(constants.ROUTE_ENV_FLAG)
        os.environ[constants.ROUTE_ENV_FLAG] = "1"

    def tearDown(self):
        if self.before is None:
            os.environ.pop(constants.ROUTE_ENV_FLAG, None)
        else:
            os.environ[constants.ROUTE_ENV_FLAG] = self.before
        super().tearDown()

    def payload(self, tool, **tool_input):
        return {"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": "/tmp/t.jsonl", "tool_name": tool, "tool_input": tool_input}

    def test_scan_only_bash_is_denied_once_with_the_run_call(self):
        first = reads.handle_pre_tool(self.payload("Bash", command="npm test"))
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mcp__kiasi__run", first["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(reads.handle_pre_tool(self.payload("Bash", command="npm test")), "the repeat goes through")
        self.assertIsNotNone(reads.handle_pre_tool(self.payload("Bash", command="npm test")), "and the next fresh call is routed again")

    def test_limited_selector_and_background_commands_stay_raw(self):
        for command in ("git log -5", "git log --oneline", "npm test 2>&1 | tail -20", "curl -s https://x.test -o page.html",
                        "pytest -q > out.txt", "sed -n 1,40p a.py", "grep -n foo a.py", "ls -la"):
            self.assertIsNone(reads.handle_pre_tool(self.payload("Bash", command=command)), command)
        self.assertIsNone(reads.handle_pre_tool(self.payload("Bash", command="npm run build", run_in_background=True)))

    def test_scan_commands_are_recognised(self):
        for command in ("cd app && npm run build", "python3 -m unittest discover -s tests", "pip install -r requirements.txt",
                        "curl -s https://api.test/items", "git log", "docker logs web", "tail -n 500 app.log", "npx tsc --noEmit"):
            self.assertTrue(reads.is_scan_command(command), command)

    def test_routing_is_off_without_the_function_hooks_flag(self):
        os.environ.pop(constants.ROUTE_ENV_FLAG, None)
        self.assertIsNone(reads.handle_pre_tool(self.payload("Bash", command="npm test")))
        self.assertIsNone(reads.handle_pre_tool(self.payload("WebFetch", url="https://docs.test/page", prompt="what")))

    def test_webfetch_is_denied_once_with_the_fetch_call(self):
        first = reads.handle_pre_tool(self.payload("WebFetch", url="https://docs.test/page", prompt="what"))
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mcp__kiasi__fetch", first["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(reads.handle_pre_tool(self.payload("WebFetch", url="https://docs.test/page", prompt="what")))

    def test_routing_and_the_let_through_are_logged(self):
        reads.handle_pre_tool(self.payload("Bash", command="npm test"))
        reads.handle_pre_tool(self.payload("Bash", command="npm test"))
        events = [json.loads(line)["event"] for line in constants.EVENT_LOG.read_text().splitlines()]
        self.assertEqual(events, ["routed", "route_retry"])

    def test_the_hook_entry_point_handles_bash(self):
        self.assertIn("Bash", constants.PRE_TOOL_HOOKED)
        self.assertIn("WebFetch", constants.PRE_TOOL_HOOKED)
