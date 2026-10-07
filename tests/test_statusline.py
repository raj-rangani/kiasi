import os
import sys
import re
import time
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from helpers import KiasiTestCase  # noqa: E402


def plain(text):
    return re.sub(r"\033\[[0-9;]*m", "", text)


class TestContextMeter(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import statusline
        self.sl = statusline
        self.now = 1_800_000_000.0

    def payload(self, tokens):
        return {"context_window": {"current_usage": {"input_tokens": tokens}}}

    def ago(self, seconds):
        return datetime.fromtimestamp(self.now - seconds, timezone.utc).isoformat()

    def line(self, tokens, session, standalone=True):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            return self.sl.kiasi_line(self.payload(tokens), session, standalone, self.tmp, self.now)

    def test_full_meter_reads_the_session_state(self):
        session = {"context_floor": 46000, "last_call_at": self.ago(180),
                   "turn_budget": {"stop": 60, "warn": 50}, "turns": {"main": {"steps": 12, "reread": 1_400_000}}}
        line = self.line(118000, session)
        self.assertIn("ctx", line)
        self.assertIn("118k (floor 46k)", line)
        self.assertIn("steps 12/60", line)
        self.assertIn("turn 1.4M", line)
        self.assertIn("cache warm 57m", line)
        self.assertIn("idle 3m", line)

    def test_missing_state_falls_back_to_the_default_floor_and_skips_the_cache(self):
        line = self.line(118000, {})
        self.assertIn("118k (floor 46k)", line)
        self.assertNotIn("cache", line)

    def test_cache_turns_cold_past_the_ttl_and_follows_the_env_var(self):
        session = {"last_call_at": self.ago(400)}
        self.assertIn("cache warm", self.line(1000, session))
        with mock.patch.dict(os.environ, {self.sl.CACHE_TTL_ENV: "5m"}):
            self.assertIn("cache cold", self.line(1000, session))
            self.assertEqual(self.sl.cache_ttl_seconds(), 300)
        with mock.patch.dict(os.environ, {self.sl.CACHE_TTL_ENV: "1h"}):
            self.assertEqual(self.sl.cache_ttl_seconds(), 3600)

    def test_ctx_tone_turns_warn_then_bad(self):
        for tokens, tone in ((100000, "ok"), (150000, "warn"), (200000, "bad")):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_COLOR", None)
                line = self.sl.kiasi_line(self.payload(tokens), {}, False, self.tmp, self.now)
            self.assertIn(self.sl.COLORS[tone] + self.sl.fmt_tokens(tokens), line)

    def test_appended_line_leaves_out_idle(self):
        line = self.line(118000, {"last_call_at": self.ago(180)}, standalone=False)
        self.assertIn("cache warm", line)
        self.assertNotIn("idle", line)
