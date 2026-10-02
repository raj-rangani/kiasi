

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import events  # noqa: E402
from core import prompt  # noqa: E402
from core import turn  # noqa: E402


class TestTurnGuard(KiasiTestCase):
    def test_stops_after_budget(self):
        session_id = "sess-turn"
        transcript_path = str(self.tmp / "missing-transcript.jsonl")
        blocked_at = None
        for step in range(1, constants.TURN_STOP_STEPS + 5):
            payload = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "session_id": session_id,
                "transcript_path": transcript_path,
            }
            result = turn.turn_guard(payload, 1000)
            if result and result.get("decision") == "block":
                blocked_at = step
                break
        self.assertIsNotNone(blocked_at, "turn_guard should block once the step budget is exhausted")
        self.assertGreaterEqual(blocked_at, constants.TURN_STOP_STEPS)


class TestLoopCheck(KiasiTestCase):
    def fail(self, command):
        return turn.handle_tool_failure({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "session_id": "s-loop",
                                          "transcript_path": "/tmp/loop.jsonl", "tool_input": {"command": command}, "error": "exit 1"})

    def test_nudges_once_at_third_identical_failure(self):
        self.assertIsNone(self.fail("pnpm build"))
        self.assertIsNone(self.fail("pnpm build"))
        third = self.fail("pnpm build")
        self.assertIn("failed 3 times", third["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(third["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
        self.assertIsNone(self.fail("pnpm build"))

    def test_nudges_on_variations_of_one_command(self):
        results = [self.fail(f"pnpm build --filter app{i}") for i in range(5)]
        self.assertTrue(all(r is None for r in results[:4]))
        self.assertIn("with different arguments", results[4]["hookSpecificOutput"]["additionalContext"])

    def test_failures_count_toward_turn_budget_and_reset_per_prompt(self):
        for _ in range(3):
            self.fail("make")
        state = events.load_session("s-loop")
        self.assertEqual(state["turns"]["loop"]["steps"], 3)
        prompt.handle_prompt({"prompt": "a new task for the loop test", "session_id": "s-loop", "transcript_path": "/tmp/loop.jsonl"})
        self.assertIsNone(self.fail("make"))

    def test_interrupts_are_ignored(self):
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "session_id": "s-loop", "is_interrupt": True, "tool_input": {"command": "x"}}
        self.assertIsNone(turn.handle_tool_failure(payload))
