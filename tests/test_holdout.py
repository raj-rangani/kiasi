import json
import os
from unittest import mock

from helpers import KiasiTestCase
from core import constants, events, holdout, caps, prompt, reads, session, turn  # noqa: E402


def ids(rule_off):
    return [f"h-{n}" for n in range(40) if holdout.holdout_off(f"h-{n}", constants.HOLDOUT or "x", None) is rule_off]


class HoldoutCase(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.before = constants.HOLDOUT
        self.flag = os.environ.get(constants.ROUTE_ENV_FLAG)
        os.environ[constants.ROUTE_ENV_FLAG] = "1"

    def tearDown(self):
        constants.HOLDOUT = self.before
        if self.flag is None:
            os.environ.pop(constants.ROUTE_ENV_FLAG, None)
        else:
            os.environ[constants.ROUTE_ENV_FLAG] = self.flag
        super().tearDown()

    def pair(self, rule):
        """An off and an on session id for rule, each started so its state carries the holdout."""
        constants.HOLDOUT = rule
        off, on = ids(True)[0], ids(False)[0]
        for sid in (off, on):
            session.handle_session_start({"session_id": sid, "source": "startup", "cwd": str(self.tmp)})
        return off, on


class TestHashing(HoldoutCase):
    def test_deterministic_and_splits_ids(self):
        constants.HOLDOUT = "output_cap"
        results = [holdout.holdout_off(f"s{n}", "output_cap") for n in range(40)]
        self.assertEqual(results, [holdout.holdout_off(f"s{n}", "output_cap") for n in range(40)])
        self.assertTrue(any(results) and not all(results))
        self.assertFalse(holdout.holdout_off("s1", "sandbox"), "only the configured rule is held out")
        constants.HOLDOUT = ""
        self.assertFalse(any(holdout.holdout_off(f"s{n}", "output_cap") for n in range(40)))

    def test_project_file_sets_it_and_a_bad_value_is_ignored(self):
        constants.HOLDOUT = ""
        (self.tmp / ".kiasi.json").write_text(json.dumps({"holdout": "sandbox"}))
        constants.apply_project(str(self.tmp))
        self.assertEqual(constants.HOLDOUT, "sandbox")
        (self.tmp / ".kiasi.json").write_text(json.dumps({"holdout": "nonsense"}))
        constants.apply_project(str(self.tmp))
        self.assertEqual(constants.HOLDOUT, "sandbox")

    def test_session_start_record_carries_the_holdout(self):
        off, on = self.pair("output_cap")
        records = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        starts = {r["session_id"]: r["holdout"] for r in records if r["event"] == "session_start"}
        self.assertEqual(starts[off], {"rule": "output_cap", "off": True})
        self.assertEqual(starts[on], {"rule": "output_cap", "off": False})
        self.assertEqual(events.load_session(off)["holdout"], starts[off])
        constants.HOLDOUT = ""
        session.handle_session_start({"session_id": "plain", "source": "startup", "cwd": str(self.tmp)})
        self.assertNotIn("holdout", events.load_session("plain"))
        self.assertNotIn("holdout", [json.loads(l) for l in constants.EVENT_LOG.read_text().splitlines() if '"plain"' in l][-1])


class TestGates(HoldoutCase):
    def prompt_run(self, sid, tokens):
        path = self.tmp / f"{sid}.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}) + "\n")
        result = prompt.handle_prompt({"prompt": "continue with the exporter work", "session_id": sid, "transcript_path": str(path), "cwd": str(self.tmp)})
        return (result or {}).get("systemMessage", "")

    def test_reread_check(self):
        off, on = self.pair("reread_check")
        with mock.patch.object(prompt, "reread_check", return_value=None) as inner:
            self.prompt_run(off, 50_000)
            self.assertFalse(inner.called)
            self.prompt_run(on, 50_000)
            self.assertTrue(inner.called)

    def test_turn_budget(self):
        off, on = self.pair("turn_budget")
        for sid, paused in ((off, False), (on, True)):
            payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": sid, "transcript_path": str(self.tmp / "none.jsonl")}
            results = [turn.turn_guard(payload, 100_000) for _ in range(constants.TURN_STOP_STEPS)]
            self.assertEqual(any(results), paused)

    def test_context_notices(self):
        off, on = self.pair("context_notices")
        self.assertNotIn("/kiasi:handoff", self.prompt_run(off, 152_000))
        self.assertIn("/kiasi:handoff", self.prompt_run(on, 152_000))

    def test_output_cap(self):
        off, on = self.pair("output_cap")
        for sid, capped in ((off, False), (on, True)):
            payload = {"hook_event_name": "PostToolUse", "session_id": sid, "tool_name": "Bash", "tool_use_id": f"t-{sid}",
                       "tool_input": {"command": "cat big.txt"}, "tool_response": {"stdout": "line of output\n" * 2000}}
            self.assertEqual(caps.handle_tool_output(payload) is not None, capped)

    def test_sandbox(self):
        off, on = self.pair("sandbox")
        for sid, routed in ((off, False), (on, True)):
            payload = {"hook_event_name": "PreToolUse", "session_id": sid, "transcript_path": "/tmp/t.jsonl", "tool_name": "Bash", "tool_input": {"command": "npm test"}}
            self.assertEqual(reads.handle_pre_tool(payload) is not None, routed)
