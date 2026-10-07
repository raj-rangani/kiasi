import os
from pathlib import Path
from unittest import mock

from helpers import KiasiTestCase
from core import constants  # noqa: E402


class TestSearch(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import search
        self.search = search
        constants.SEARCH_DIRS = (constants.OUTPUT_DIR, constants.PASTE_DIR, constants.NOTES_DIR, constants.CHECKPOINT_DIR)
        # search.py also reads the checklists of the project it runs in; pin it so a real one never leaks in.
        environ = mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.tmp / "project")})
        environ.start()
        self.addCleanup(environ.stop)
        (constants.OUTPUT_DIR / "toolu_a.txt").write_text("FAIL tests/login.test.ts\nTimeout waiting for the login form\n" + "noise " * 200)
        (constants.OUTPUT_DIR / "toolu_b.txt").write_text("all tests passed, build ok\n" + "noise " * 200)
        (constants.NOTES_DIR / "proj.jsonl").write_text('{"task": "fix the login redirect"}\n')

    def paths(self, *words):
        return [Path(path).name for path, *_ in self.search.search(list(words), 8)[0]]

    def test_checklists_in_the_project_are_searched(self):
        checklist = self.tmp / "project" / ".kiasi" / "checkpoints" / "abcdef12-1.md"
        checklist.parent.mkdir(parents=True)
        checklist.write_text("- [ ] migrate the invoice exporter\n")
        self.assertEqual(self.paths("invoice", "exporter"), ["abcdef12-1.md"])

    def test_a_bare_double_dash_is_a_searchable_word(self):
        (constants.OUTPUT_DIR / "toolu_d.txt").write_text("usage: tool -- args\n" + "noise " * 200)
        self.assertEqual(self.paths("--"), ["toolu_d.txt"])

    def test_double_dash_after_the_separator_reaches_main_as_a_word(self):
        (constants.OUTPUT_DIR / "toolu_d.txt").write_text("usage: tool -- args\n" + "noise " * 200)
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with mock.patch("sys.argv", ["search.py", "-n", "8", "--", "--"]), redirect_stdout(out):
            self.search.main()
        self.assertIn("toolu_d.txt", out.getvalue())

    def test_all_words_must_appear_and_endings_match(self):
        self.assertEqual(self.paths("login", "timeout"), ["toolu_a.txt"])
        self.assertEqual(sorted(self.paths("test")), ["toolu_a.txt", "toolu_b.txt"], "'test' also finds 'tests'")

    def test_quoted_phrase_matches_in_order_only(self):
        (constants.OUTPUT_DIR / "toolu_c.txt").write_text("waiting for timeout here\n" + "noise " * 200)
        self.assertEqual(self.paths('"Timeout waiting"'), ["toolu_a.txt"])
        self.assertEqual(self.paths("timeout", "waiting"), ["toolu_a.txt", "toolu_c.txt"])

    def test_leading_dash_word_is_a_word(self):
        with mock.patch("sys.argv", ["search.py", "-bash:", "-n", "3"]), mock.patch("builtins.print"):
            self.search.main()

    def test_or_not_phrase_and_prefix(self):
        self.assertEqual(sorted(self.paths("redirect", "OR", "passed")), ["proj.jsonl", "toolu_b.txt"])
        self.assertEqual(self.paths("login", "NOT", "FAIL"), ["proj.jsonl"])
        self.assertEqual(self.paths('"login', 'form"'), ["toolu_a.txt"], "split phrase from the search tool")
        self.assertEqual(self.paths("redir*"), ["proj.jsonl"])

    def test_snippet_marks_hits_and_rare_words_rank_first(self):
        rows, total = self.search.search(["login"], 8)
        self.assertEqual(total, 3)
        self.assertIn("[login]", rows[0][1])
        self.assertEqual(self.search.search(["nothing-here"], 8), ([], 3))

    def test_rows_carry_hit_line_numbers(self):
        rows, _ = self.search.search(["timeout"], 8)
        self.assertEqual(rows[0][3], [2], "'Timeout waiting' is on line 2 of toolu_a.txt")
