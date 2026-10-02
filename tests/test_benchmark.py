

from helpers import KiasiTestCase


class TestBenchmark(KiasiTestCase):
    def test_every_scenario_cuts_at_least_half(self):
        import benchmark
        rows = benchmark.scenarios(benchmark.point_at_tmp())
        self.assertEqual(len(rows), 6)
        for name, _rule, chars_in, chars_shown in rows:
            self.assertLess(chars_shown, chars_in / 2, f"{name} should cut at least half")
