import json
import re
import sys
from pathlib import Path

from helpers import KiasiTestCase
from core import constants  # noqa: E402


class TestSandbox(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import sandbox
        self.sandbox = sandbox

    def run_cli(self, *argv):
        import contextlib
        import io
        out = io.StringIO()
        before = sys.argv
        sys.argv = ["sandbox.py", *argv]
        try:
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
                self.sandbox.main()
        finally:
            sys.argv = before
        return caught.exception.code or 0, out.getvalue()

    def test_a_failed_run_is_logged_without_saved_tokens(self):
        from reports import lens
        code, _ = self.run_cli("run", "--command", "seq 1 500; exit 3")
        self.assertEqual(code, 1)
        events = [json.loads(l) for l in constants.EVENT_LOG.read_text().splitlines()]
        self.assertTrue(events[-1]["failed"])
        self.assertEqual(events[-1]["chars"], events[-1]["shown_chars"])
        self.assertEqual(lens.tokens(events[-1]["chars"] - events[-1]["shown_chars"]), 0)

    def test_run_saves_full_output_and_returns_a_digest(self):
        command = "seq 1 100 && echo 'Error: broke here' && seq 101 200"
        code, shown = self.run_cli("run", "--command", command)
        self.assertEqual(code, 0)
        saved = list(constants.OUTPUT_DIR.glob("run-*.txt"))
        self.assertEqual(len(saved), 1)
        self.assertIn("101", saved[0].read_text(), "full output is on disk")
        self.assertIn("exit 0", shown)
        self.assertIn("[kiasi trimmed", shown)
        self.assertIn("Error: broke here", shown, "error lines from the trimmed middle are kept")
        self.assertNotIn("\n101\n", shown, "the middle stays out of the conversation")

    def test_distill_returns_only_what_the_script_prints(self):
        big = constants.OUTPUT_DIR / "big.txt"
        big.write_text("x\n" * 5000)
        code, shown = self.run_cli("distill", "--code", "import sys; print(sum(1 for _ in open(sys.argv[1])))", "--file", str(big))
        self.assertEqual(code, 0)
        self.assertEqual(shown.strip(), "5000")

    def test_distill_overflow_is_saved_and_named(self):
        code, shown = self.run_cli("distill", "--code", "print('y' * 10000)")
        self.assertEqual(code, 0)
        self.assertIn("[kiasi kept the first", shown)
        saved = list(constants.OUTPUT_DIR.glob("distill-*.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(saved[0].read_text().strip()), 10000)

    def test_distill_failure_reports_exit_and_stderr(self):
        code, shown = self.run_cli("distill", "--code", "raise SystemExit('boom')")
        self.assertEqual(code, 1)
        self.assertIn("script failed (exit 1)", shown)
        self.assertIn("boom", shown)

    def test_run_logs_its_saving_as_a_cap_event(self):
        self.run_cli("run", "--command", "seq 1 500")
        events = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        caps = [e for e in events if e.get("event") == "cap" and e.get("kind") == "sandbox"]
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["tool_name"], "mcp__kiasi__run")
        self.assertGreater(caps[0]["chars"], caps[0]["shown_chars"])

    def test_distill_counts_the_avoided_read(self):
        big = constants.OUTPUT_DIR / "big.txt"
        big.write_text("x" * 50_000)
        self.run_cli("distill", "--code", "print('ok')", "--file", str(big))
        events = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        caps = [e for e in events if e.get("event") == "cap" and e.get("kind") == "sandbox"]
        self.assertEqual(caps[0]["chars"], 50_000, "the saving is the Read the script replaced")
        self.assertLess(caps[0]["shown_chars"], 10)

    def test_fetch_saves_the_page_text_and_returns_head_and_hits(self):
        import re as re_module
        page = self.tmp / "page.html"
        page.write_text("<html><head><title>Kiasi docs</title><style>p{}</style></head><body><h1>Hello &amp; welcome</h1>"
                        "<script>var secret = 1;</script>" + "".join(f"<p>filler line {i}</p>" for i in range(400)) +
                        "<p>the needle is here</p></body></html>")
        code, out = self.run_cli("fetch", "--url", page.as_uri(), "--find", "needle")
        self.assertEqual(code, 0)
        self.assertIn("Kiasi docs", out)
        self.assertIn("Hello & welcome", out)
        self.assertNotIn("secret", out)
        self.assertIn("the needle is here", out)
        self.assertNotIn("filler line 399", out, "only the head enters the conversation")
        saved = Path(re_module.search(r"saved at (\S+),", out).group(1))
        self.assertIn("filler line 399", saved.read_text())
        events = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        caps = [e for e in events if e.get("event") == "cap" and e.get("tool_name") == "mcp__kiasi__fetch"]
        self.assertEqual(len(caps), 1)
        self.assertGreater(caps[0]["chars"], caps[0]["shown_chars"])

    def test_fetch_failure_reports_the_error(self):
        code, out = self.run_cli("fetch", "--url", (self.tmp / "missing.html").as_uri())
        self.assertEqual(code, 1)
        self.assertIn("fetch failed", out)

    def test_sandbox_files_match_the_cleanup_pattern(self):
        import re as re_module
        pattern = re_module.compile(constants.CLEANUP_PATTERNS["outputs"])
        self.assertTrue(pattern.fullmatch("run-20261001-120000-123.txt"))
        self.assertTrue(pattern.fullmatch("distill-20261001-120000-123.txt"))
        self.assertTrue(pattern.fullmatch("fetch-20261001-120000-123.txt"))
        self.assertTrue(pattern.fullmatch("toolu_abc123.txt"))
