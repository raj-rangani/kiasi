import json
import shutil
import tempfile
from pathlib import Path

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import reads  # noqa: E402


class TestReadSkip(KiasiTestCase):
    def payload(self, event, path, **extra):
        return {"hook_event_name": event, "session_id": "s-read", "transcript_path": "/tmp/main.jsonl", "tool_name": "Read", "tool_input": {"file_path": str(path)}, **extra}

    def read_done(self, path):
        response = {"type": "text", "file": {"content": "x" * 5000}}
        reads.track_reads(self.payload("PostToolUse", path, tool_response=response), None)

    def test_skips_unchanged_then_lets_retry_through(self):
        path = constants.DATA_DIR / "big.txt"
        path.write_text("x" * 5000)
        self.read_done(path)
        denied = reads.handle_pre_tool(self.payload("PreToolUse", path))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(reads.handle_pre_tool(self.payload("PreToolUse", path)))

    def test_identical_reads_of_one_response_are_all_refused(self):
        path = constants.DATA_DIR / "big.txt"
        path.write_text("x" * 5000)
        self.read_done(path)
        transcript = constants.DATA_DIR / "main.jsonl"

        def respond(message_id):
            transcript.write_text(json.dumps({"type": "assistant", "message": {"id": message_id, "content": []}}) + "\n")

        def read():
            return reads.handle_pre_tool(self.payload("PreToolUse", path, transcript_path=str(transcript)))

        respond("msg_1")
        self.assertEqual(read()["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(read()["hookSpecificOutput"]["permissionDecision"], "deny", "the second identical call of the same response")
        respond("msg_2")
        self.assertIsNone(read(), "the repeat of a later response goes through")

    def test_change_edit_and_compaction_clear_the_entry(self):
        path = constants.DATA_DIR / "big.txt"
        path.write_text("x" * 5000)
        self.read_done(path)
        path.write_text("y" * 5001)
        self.assertIsNone(reads.handle_pre_tool(self.payload("PreToolUse", path)))
        self.read_done(path)
        reads.track_reads({"session_id": "s-read", "tool_name": "Edit", "tool_input": {"file_path": str(path)}}, None)
        self.assertIsNone(reads.handle_pre_tool(self.payload("PreToolUse", path)))
        self.read_done(path)
        reads.forget_reads("s-read")
        self.assertIsNone(reads.handle_pre_tool(self.payload("PreToolUse", path)))

    def test_small_reads_are_not_tracked(self):
        path = constants.DATA_DIR / "small.txt"
        path.write_text("x")
        reads.track_reads(self.payload("PostToolUse", path, tool_response={"type": "text", "file": {"content": "x"}}), None)
        self.assertIsNone(reads.handle_pre_tool(self.payload("PreToolUse", path)))


class TestReadNudge(KiasiTestCase):
    def setUp(self):
        super().setUp()
        # project files must live outside DATA_DIR, which the nudge exempts
        self.proj = Path(tempfile.mkdtemp(prefix="kiasi-proj-"))

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)
        super().tearDown()

    def payload(self, path, **tool_input):
        return {"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": "/tmp/t.jsonl",
                "tool_name": "Read", "tool_input": {"file_path": str(path), **tool_input}}

    def test_whole_read_of_a_big_file_is_denied_once(self):
        big = self.proj / "big.py"
        big.write_text("x = 1\n" * 10_000)
        first = reads.handle_read_check(self.payload(big))
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mcp__kiasi__distill", first["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(reads.handle_read_check(self.payload(big)), "the repeat goes through")
        logged = [json.loads(line)["event"] for line in constants.EVENT_LOG.read_text().splitlines()]
        self.assertEqual(logged[-2:], ["read_nudge", "read_nudge_repeat"], "the repeat is logged so the report can count the nudges that were ignored")

    def test_sectioned_reads_small_files_and_images_pass(self):
        big = self.proj / "big.py"
        big.write_text("x = 1\n" * 10_000)
        image = self.proj / "shot.png"
        image.write_bytes(b"p" * 40_000)
        small = self.proj / "small.py"
        small.write_text("x = 1\n")
        self.assertIsNone(reads.handle_read_check(self.payload(big, offset=10, limit=50)))
        self.assertIsNone(reads.handle_read_check(self.payload(image)))
        self.assertIsNone(reads.handle_read_check(self.payload(small)))

    def test_nudge_exempts_kiasi_data_dir(self):
        saved = constants.OUTPUT_DIR / "toolu_big.txt"
        saved.write_text("x" * 40_000)
        self.assertIsNone(reads.handle_read_check(self.payload(saved)),
                          "reads of kiasi's own saved files are never denied")
