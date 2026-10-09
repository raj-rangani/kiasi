import json
import sys
import time
import types
from unittest import mock

from helpers import KiasiTestCase
from core import constants  # noqa: E402


class TestLimitsForecast(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import limits_data
        self.limits_data = limits_data

    def write_reading(self, used, resets_at):
        (constants.DATA_DIR / constants.RATE_LIMITS_NAME).write_text(json.dumps(
            {"updated": int(time.time()),
             "rate_limits": {"seven_day": {"used_percentage": used, "resets_at": resets_at}}}))

    def write_history(self, points):
        lines = [json.dumps({"key": "seven_day", **p}) for p in points]
        (constants.DATA_DIR / constants.LIMITS_HISTORY_NAME).write_text("\n".join(lines) + "\n")

    def test_steady_burn_projects_a_run_out_before_the_reset(self):
        now = int(time.time())
        resets = now + 5 * 86400
        self.write_reading(40, resets)
        self.write_history([{"ts": now - 86400, "used": 20, "resets_at": resets},
                            {"ts": now - 3600, "used": 40, "resets_at": resets}])
        item = self.limits_data.get_limits()["limits"][0]
        self.assertLess(item["run_out_at"], resets, "40 points in a day burns out in ~3 days")
        self.assertEqual(item["severity"], "warning")
        self.assertGreater(item["burn_per_day"], 15)

    def test_the_runway_carries_the_window_readings_thinned(self):
        now = int(time.time())
        resets = now + 5 * 86400
        self.write_reading(40, resets)
        points = [{"ts": now - 2 * 86400 + i * 600, "used": min(40, i // 8), "resets_at": resets} for i in range(288)]
        points.insert(0, {"ts": resets - 8 * 86400, "used": 90, "resets_at": resets - 7 * 86400})  # last window: left out
        self.write_history(points)
        runway = self.limits_data.get_limits()["runway"]
        self.assertLessEqual(len(runway["points"]), constants.RUNWAY_POINTS_MAX)
        self.assertEqual(runway["points"][0], [now - 2 * 86400, 0], "the first reading of the window is kept")
        self.assertEqual(runway["points"][-1], [points[-1]["ts"], points[-1]["used"]], "and so is the last")
        self.assertTrue(all(a[0] < b[0] for a, b in zip(runway["points"], runway["points"][1:])), "in time order")

    def test_short_or_flat_history_stays_quiet(self):
        now = int(time.time())
        resets = now + 5 * 86400
        self.write_reading(40, resets)
        self.write_history([{"ts": now - 600, "used": 39, "resets_at": resets},
                            {"ts": now - 60, "used": 40, "resets_at": resets}])
        item = self.limits_data.get_limits()["limits"][0]
        self.assertNotIn("run_out_at", item, "under four hours of history is noise")


class TestStatuslineHistory(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import statusline
        self.statusline = statusline

    def payload(self, used):
        return {"rate_limits": {"seven_day": {"used_percentage": used, "resets_at": 1791388800}}}

    def test_only_changed_readings_are_appended(self):
        self.statusline.save_rate_limits(self.payload(10), self.tmp)
        self.statusline.save_rate_limits(self.payload(10), self.tmp)
        self.statusline.save_rate_limits(self.payload(11), self.tmp)
        lines = (self.tmp / self.statusline.HISTORY_NAME).read_text().splitlines()
        self.assertEqual([json.loads(line)["used"] for line in lines], [10, 11])


class TestLimits(KiasiTestCase):
    MINE = {"type": "command", "command": "bash ~/my-line.sh", "padding": 1}

    def setUp(self):
        super().setUp()
        import statusline_install
        self.limits = statusline_install
        constants.CLAUDE_SETTINGS_FILE = self.tmp / "settings.json"

    def settings(self):
        return json.loads(constants.CLAUDE_SETTINGS_FILE.read_text())

    def test_setup_chains_previous_line_and_backs_up_once(self):
        constants.CLAUDE_SETTINGS_FILE.write_text(json.dumps({"statusLine": self.MINE, "model": "x"}))
        self.limits.setup()
        self.limits.setup()
        line = self.settings()["statusLine"]
        self.assertTrue(self.limits.is_ours(line))
        self.assertEqual(self.settings()["model"], "x")
        self.assertTrue((constants.HOME_DIR / constants.STATUSLINE_SCRIPT_NAME).is_file())
        chain = json.loads((constants.HOME_DIR / constants.STATUSLINE_CHAIN_NAME).read_text())
        self.assertEqual(chain["previous"], self.MINE, "a second setup keeps the user's line, not ours")
        backup = json.loads((self.tmp / ("settings.json" + constants.SETTINGS_BACKUP_SUFFIX)).read_text())
        self.assertEqual(backup["statusLine"], self.MINE)

    def test_remove_restores_previous_line(self):
        constants.CLAUDE_SETTINGS_FILE.write_text(json.dumps({"statusLine": self.MINE}))
        self.limits.setup()
        self.limits.remove()
        self.assertEqual(self.settings()["statusLine"], self.MINE)
        self.assertFalse((constants.HOME_DIR / constants.STATUSLINE_CHAIN_NAME).exists())

    def test_remove_without_previous_line_drops_the_key(self):
        self.limits.setup()
        self.limits.remove()
        self.assertNotIn("statusLine", self.settings())

    def test_remove_leaves_someone_elses_line_alone(self):
        constants.CLAUDE_SETTINGS_FILE.write_text(json.dumps({"statusLine": self.MINE}))
        self.assertIn("not Kiasi's", self.limits.remove())
        self.assertEqual(self.settings()["statusLine"], self.MINE)

    def test_invalid_settings_json_is_not_overwritten(self):
        constants.CLAUDE_SETTINGS_FILE.write_text("{broken")
        self.assertEqual(self.limits.main(["setup"]), 1)
        self.assertEqual(constants.CLAUDE_SETTINGS_FILE.read_text(), "{broken")


class LimitsLockTest(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import statusline
        self.statusline = statusline

    def test_the_lock_is_taken_and_released(self):
        with self.statusline.limits_lock(self.tmp):
            self.assertTrue((self.tmp / ".limits.lock").exists())
        with self.statusline.limits_lock(self.tmp):
            pass

    def test_windows_locks_a_byte_with_msvcrt_when_fcntl_is_missing(self):
        calls = []
        fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda fd, mode, size: calls.append((mode, size)))
        with mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": fake}):
            self.assertIs(self.statusline.lock_backend(), fake)
            with self.statusline.limits_lock(self.tmp):
                self.assertEqual(calls, [(2, 1)])
        self.assertEqual(calls, [(2, 1), (0, 1)])

    def test_it_runs_unlocked_with_no_lock_module(self):
        with mock.patch.dict(sys.modules, {"fcntl": None, "msvcrt": None}):
            self.assertIsNone(self.statusline.lock_backend())
            with self.statusline.limits_lock(self.tmp):
                pass


class TestClaudeConfigFallback(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import limits_data
        self.limits_data = limits_data

    def test_claude_code_cached_windows_feed_the_limits_when_no_status_line_wrote(self):
        constants.CLAUDE_CONFIG_FILE.write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": int(time.time() * 1000) - 60_000,
            "utilization": {"five_hour": {"utilization": 12, "resets_at": "2026-10-07T09:20:00+00:00"},
                            "seven_day": {"utilization": 40, "resets_at": "2026-10-12T09:20:00+00:00"},
                            "seven_day_opus": {"utilization": None}}}}))
        reading = self.limits_data.get_limits()
        self.assertEqual((reading["source"], reading["setup"]), ("claude_code", "ready"))
        self.assertEqual([(item["key"], item["used"]) for item in reading["limits"]], [("five_hour", 12), ("seven_day", 40)])

    def test_a_newer_status_line_reading_wins_over_the_cached_one(self):
        constants.CLAUDE_CONFIG_FILE.write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": int(time.time() * 1000) - 3_600_000, "utilization": {"seven_day": {"utilization": 40, "resets_at": None}}}}))
        (constants.DATA_DIR / constants.RATE_LIMITS_NAME).write_text(json.dumps(
            {"updated": int(time.time()), "rate_limits": {"seven_day": {"used_percentage": 55, "resets_at": None}}}))
        reading = self.limits_data.get_limits()
        self.assertEqual(reading["source"], "statusline")
        self.assertEqual([item["used"] for item in reading["limits"]], [55])
