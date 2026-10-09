import gzip
import json
import time

from helpers import KiasiTestCase, constants
from core import digest


def reading(used, resets_in_days, run_out_in_days=None, burn=None, group="weekly", now=None):
    now = now or time.time()
    item = {"key": "seven_day", "label": "Weekly", "group": group, "used": used, "resets_at": now + resets_in_days * 86400,
            "burn_per_day": burn, "run_out_at": now + run_out_in_days * 86400 if run_out_in_days else None}
    return {"limits": [item], "updated": now}


class TestRunway(KiasiTestCase):
    def test_runs_out_before_the_reset(self):
        r = digest.runway(reading(60, 3, run_out_in_days=1.5, burn=30))
        self.assertEqual(r["state"], "runs_out")
        self.assertIn("runs out", digest.runway_sentence(r))
        self.assertIn("before the reset", digest.runway_sentence(r))

    def test_clear_with_spare(self):
        r = digest.runway(reading(20, 2, burn=10))
        self.assertEqual(r["state"], "clear")
        self.assertEqual(r["spare"], 60, "20 used plus 10 a day for 2 days leaves 60")
        self.assertIn("to spare", digest.runway_sentence(r))

    def test_pace_without_a_forecast(self):
        r = digest.runway(reading(50, 3.5))
        self.assertEqual(r["state"], "pace")
        self.assertEqual(r["gap"], 0, "half the limit at half the week is on pace")
        self.assertIn("on pace", digest.runway_sentence(r))

    def test_no_weekly_window_or_an_expired_one_gives_nothing(self):
        self.assertIsNone(digest.runway(reading(50, 1, group="five_hour")))
        self.assertIsNone(digest.runway(reading(50, -1)))
        self.assertIsNone(digest.runway(None))
        self.assertEqual(digest.runway_sentence(None), "")


class TestBoughtBack(KiasiTestCase):
    def test_days_at_the_daily_rate(self):
        self.assertEqual(digest.bought_back(372_401_655, 69_051_930), 5.4)
        self.assertIsNone(digest.bought_back(1000, 0))
        self.assertIsNone(digest.bought_back(None, 1000))
        self.assertEqual(digest.bought_back_text(5.4), "about 5.4 days")
        self.assertEqual(digest.bought_back_text(0.05), "about 1.2 hours")


class TestWeeklyDigest(KiasiTestCase):
    def lens(self, days=7, **extra):
        report = {"per_day": [{"day": f"2026-10-0{i + 1}"} for i in range(days)],
                  "totals": {"kept_out": 6_900_000, "saved": 373_000_000}, "since": {"factor": 2.1},
                  "pace": {"rate": 69_000_000, "prior_rate": 106_000_000},
                  "spikes": {"rows": [{"day": "2026-10-01", "reread": 12_900_000, "factor": 6.9}]},
                  "attribution": {"kinds": [{"kind": "floor", "share": 0.46}]}, **extra}
        with gzip.open(constants.LENS_FILE, "wt", encoding="utf-8") as fh:
            json.dump(report, fh)
        return report

    def test_text_covers_factor_saving_pace_spike_fix_and_runway(self):
        text = digest.digest_text(self.lens(), reading(60, 3, run_out_in_days=1.5, burn=30))
        self.assertIn("2.1× less context", text)
        self.assertIn("about 5.4 days of your weekly limit", text)
        self.assertIn("35% less than the week before", text)
        self.assertIn("1 spike session", text)
        self.assertIn("Next fix: the startup floor, 46%", text)
        self.assertIn("runs out", text)

    def test_under_a_week_of_days_stays_quiet(self):
        self.assertEqual(digest.digest_text(self.lens(days=3)), "")
        self.assertEqual(digest.digest_text(None), "")

    def test_shown_once_a_week(self):
        self.lens()
        first = digest.weekly_digest(None)
        self.assertIn("Kiasi kept", first)
        self.assertEqual(digest.weekly_digest(None), "", "shown again only after DIGEST_DAYS")
        self.assertTrue(digest.due(time.time() + constants.DIGEST_DAYS * 86400 + 1))

    def test_no_report_does_not_mark_the_week(self):
        self.assertEqual(digest.weekly_digest(None), "")
        self.assertFalse(constants.DIGEST_FILE.exists())
