

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
