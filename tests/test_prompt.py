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
