import time


from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import prompt as prompt_handlers  # noqa: E402


class TestPasteRefusal(KiasiTestCase):
    def test_over_limit_paste_is_refused_and_saved(self):
        prompt = "x" * (constants.PASTE_BLOCK_CHARS + 10)
        payload = {
            "prompt": prompt,
            "session_id": "sess-paste",
            "transcript_path": str(self.tmp / "missing-transcript.jsonl"),
        }
        result = prompt_handlers.handle_prompt(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")
        self.assertIn("saved at", result["reason"])
        saved_files = list(constants.PASTE_DIR.glob("*.txt"))
        self.assertTrue(saved_files, "the refused paste should be saved to PASTE_DIR")
        self.assertEqual(saved_files[0].read_text(), prompt)

    def test_under_limit_paste_is_not_refused(self):
        payload = {
            "prompt": "a short prompt",
            "session_id": "sess-paste-2",
            "transcript_path": str(self.tmp / "missing-transcript.jsonl"),
        }
        result = prompt_handlers.handle_prompt(payload)
        self.assertIsNone(result)


class TestHandoffNotices(KiasiTestCase):
    def run_prompt(self, session_id, tokens, state=None):
        from core import events
        import json
        path = self.tmp / f"{session_id}.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}) + "\n")
        if state:
            events.save_session(session_id, {**events.load_session(session_id), **state})
        result = prompt_handlers.handle_prompt({"prompt": "continue with the exporter work", "session_id": session_id, "transcript_path": str(path), "cwd": str(self.tmp)})
        return (result or {}).get("systemMessage", "")

    def test_notice_at_150k_once_per_step_using_the_measured_floor(self):
        message = self.run_prompt("s-150", 152_000, {"context_floor": 44_000})
        self.assertIn("Context 152k (floor 44k). Run /kiasi:handoff, then /clear: this task continues from about 46k.", message)
        self.assertNotIn("/kiasi:handoff", self.run_prompt("s-150", 160_000))
        self.assertIn("floor 46k", self.run_prompt("s-default", 151_000))
        self.assertNotIn("/kiasi:handoff", self.run_prompt("s-low", 120_000))

    def test_idle_return_notice_once_and_the_150k_one_wins(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 90 * 60))
        message = self.run_prompt("s-idle", 120_000, {"last_call_at": old, "context_tokens": 120_000, "context_floor": 46_000})
        self.assertIn("Cache cold after 90 min idle at 120k context: the next prompt rewrites all of it. /kiasi:handoff then /clear starts from about 48k.", message)
        self.assertNotIn("Cache cold", self.run_prompt("s-idle", 120_000))
        both = self.run_prompt("s-both", 160_000, {"last_call_at": old, "context_tokens": 160_000})
        self.assertIn("Run /kiasi:handoff, then /clear", both)
        self.assertNotIn("Cache cold", both)
        recent = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 10 * 60))
        self.assertNotIn("Cache cold", self.run_prompt("s-recent", 120_000, {"last_call_at": recent, "context_tokens": 120_000}))

    def test_the_ttl_follows_the_env_variable(self):
        import os
        self.assertEqual(prompt_handlers.cache_ttl_minutes(), 60)
        os.environ[constants.CACHE_TTL_ENV] = "5m"
        try:
            self.assertEqual(prompt_handlers.cache_ttl_minutes(), 5)
            os.environ[constants.CACHE_TTL_ENV] = "weird"
            self.assertEqual(prompt_handlers.cache_ttl_minutes(), 60)
        finally:
            del os.environ[constants.CACHE_TTL_ENV]


class TestTaskSwitch(KiasiTestCase):
    OLD = staticmethod(lambda: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 90 * 60)))

    def run_prompt(self, session_id, prompt, tokens=60_000, state=None, goal="Build the CSV parser for the import screen"):
        from core import events, taskfile
        import json
        if goal:
            taskfile.write(str(self.tmp), session_id, goal=goal, checklist="- [ ] tests for the parser")
        path = self.tmp / f"{session_id}.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}) + "\n")
        events.save_session(session_id, {**events.load_session(session_id), "last_call_at": self.OLD(), **(state or {})})
        result = prompt_handlers.handle_prompt({"prompt": prompt, "session_id": session_id, "transcript_path": str(path), "cwd": str(self.tmp)})
        return (result or {}).get("systemMessage", "")

    def test_a_different_task_after_a_gap_is_noticed_once(self):
        message = self.run_prompt("s-switch", "Redesign the invoice email template with the new branding colours")
        self.assertIn("This looks like a different task from the one in", message)
        self.assertIn("(Build the CSV parser for the import screen)", message)
        self.assertIn("/kiasi:handoff then /clear keeps them apart; context is 60k.", message)
        self.assertNotIn("different task", self.run_prompt("s-switch", "Redesign the invoice email template with the new branding colours", goal=None))

    def test_the_same_task_is_not_noticed(self):
        self.assertNotIn("different task", self.run_prompt("s-same", "Add the missing parser tests for the import screen and the CSV edge cases"))

    def test_a_short_reply_a_slash_command_or_no_gap_is_not_noticed(self):
        for sid, text in (("s-short", "yes go ahead"), ("s-slash", "/kiasi:handoff and then write the invoice email template"), ("s-word", "continue")):
            self.assertNotIn("different task", self.run_prompt(sid, text))
        self.assertNotIn("different task", self.run_prompt("s-fresh", "Redesign the invoice email template with the new branding colours", state={"last_call_at": time.strftime("%Y-%m-%dT%H:%M:%S")}))

    def test_the_150k_notice_wins_over_a_switch(self):
        message = self.run_prompt("s-big", "Redesign the invoice email template with the new branding colours", tokens=160_000)
        self.assertIn("Run /kiasi:handoff, then /clear", message)
        self.assertNotIn("different task", message)
