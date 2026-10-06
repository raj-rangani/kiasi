

import json

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

    def test_subagent_calls_have_their_own_turn(self):
        main = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s-agents", "transcript_path": str(self.tmp / "s-agents.jsonl")}
        results = [turn.turn_guard(main, 1000) for _ in range(constants.TURN_STOP_STEPS)]
        self.assertEqual(results[-1]["decision"], "block")
        subagent = {**main, "agent_id": "a1fd597a875bd1f14"}
        handed_off = [turn.turn_guard(subagent, 1000) for _ in range(constants.TURN_REMIND_STEPS)]
        self.assertFalse([r for r in handed_off if r and r.get("decision") == "block"],
                         "the subagent a stopped turn hands its checklist to must be able to work")
        turns = events.load_session("s-agents")["turns"]
        self.assertEqual(turns["s-agents"]["steps"], constants.TURN_STOP_STEPS)
        self.assertEqual(turns["a1fd597a875bd1f14"]["steps"], constants.TURN_REMIND_STEPS)
        prompt.handle_prompt({"prompt": "the next task after the hand-off", "session_id": "s-agents", "transcript_path": main["transcript_path"]})
        self.assertNotIn("a1fd597a875bd1f14", events.load_session("s-agents")["turns"])

    def test_subagent_calls_reread_their_own_context(self):
        main_path = self.tmp / "s-ctx.jsonl"
        agent_path = self.tmp / "s-ctx" / "subagents" / "agent-a4bb4aca3bed22433.jsonl"
        agent_path.parent.mkdir(parents=True)
        for path, tokens in ((main_path, 150_000), (agent_path, 20_000)):
            path.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10}}}) + "\n")
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "make"}, "session_id": "s-ctx",
                   "transcript_path": str(main_path), "agent_id": "a4bb4aca3bed22433"}
        turn.handle_tool_failure(payload)
        self.assertEqual(events.load_session("s-ctx")["turns"]["a4bb4aca3bed22433"]["reread"], 20_000)

    def test_checkpoint_path_never_names_an_existing_file(self):
        constants.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        record = {"index": 7}
        names = []
        for _ in range(3):
            path = turn.checkpoint_path("0123abcd-transcript", record)
            names.append(path.name)
            path.write_text("- [ ] left\n")
        self.assertEqual(names, ["0123abcd-7.md", "0123abcd-7-2.md", "0123abcd-7-3.md"])

    def test_stop_names_a_new_file_once_the_warned_checklist_exists(self):
        constants.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "sess-checklist",
                   "transcript_path": str(self.tmp / "checklist.jsonl")}
        warning = stop = ""
        for _ in range(constants.TURN_STOP_STEPS):
            result = turn.turn_guard(payload, 1000) or {}
            if "hookSpecificOutput" in result:
                warning = result["hookSpecificOutput"]["additionalContext"]
                (constants.CHECKPOINT_DIR / "checklis-0.md").write_text("- [ ] left\n")
            stop = result.get("reason", stop)
        self.assertIn(str(constants.CHECKPOINT_DIR / "checklis-0.md"), warning)
        self.assertIn(str(constants.CHECKPOINT_DIR / "checklis-0-2.md"), stop)


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
