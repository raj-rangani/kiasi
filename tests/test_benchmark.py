import json
from pathlib import Path

from helpers import KiasiTestCase

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_COUNT = 9


class TestBenchmark(KiasiTestCase):
    def rows(self):
        import benchmark
        return benchmark, benchmark.scenarios(benchmark.point_at_tmp())

    def test_kiasi_never_sends_more_than_the_raw_output(self):
        _, rows = self.rows()
        self.assertEqual(len(rows), SCENARIO_COUNT)
        for item in rows:
            self.assertLessEqual(item["chars_kiasi"], item["chars_in"], item["name"])
            self.assertLessEqual(item["chars_claude_code"], item["chars_in"], item["name"])

    def test_every_row_says_what_each_side_gets(self):
        _, rows = self.rows()
        for item in rows:
            self.assertTrue(item["claude_code_gets"] and item["kiasi_gets"], item["name"])

    def test_website_data_matches_the_script(self):
        benchmark, rows = self.rows()
        published = json.loads((ROOT / "docs" / "benchmark.json").read_text())
        self.assertEqual(published, json.loads(json.dumps(benchmark.payload(rows))))

    def test_benchmark_md_table_matches_the_script(self):
        benchmark, rows = self.rows()
        self.assertIn(benchmark.table(rows), (ROOT / "BENCHMARK.md").read_text())
