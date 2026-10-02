from pathlib import Path

from helpers import KiasiTestCase
from core import constants  # noqa: E402


class TestSearch(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import search
        self.search = search
        constants.SEARCH_DIRS = (constants.OUTPUT_DIR, constants.PASTE_DIR, constants.NOTES_DIR, constants.CHECKPOINT_DIR)
        (constants.OUTPUT_DIR / "toolu_a.txt").write_text("FAIL tests/login.test.ts\nTimeout waiting for the login form\n" + "noise " * 200)
        (constants.OUTPUT_DIR / "toolu_b.txt").write_text("all tests passed, build ok\n" + "noise " * 200)
        (constants.NOTES_DIR / "proj.jsonl").write_text('{"task": "fix the login redirect"}\n')

    def paths(self, *words):
        return [Path(path).name for path, *_ in self.search.search(list(words), 8)[0]]

    def test_all_words_must_appear_and_endings_match(self):
        self.assertEqual(self.paths("login", "timeout"), ["toolu_a.txt"])
        self.assertEqual(sorted(self.paths("test")), ["toolu_a.txt", "toolu_b.txt"], "'test' also finds 'tests'")

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
