"""Tests for the report fixes of the second audit: atomic history, window and local-day bucketing, delegation evidence, unreadable input."""
import calendar
import json
import os
import threading
import time
import unittest
from unittest import mock

from test_reports import ReportTestCase
from core import constants  # noqa: E402


def local(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


class TestAuditReports(ReportTestCase):
    def test_write_atomic_retries_a_replace_a_reader_blocks(self):
        target = self.tmp / "out.json"
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise PermissionError("held open")
            return real(src, dst)
        with mock.patch.object(self.budget.os, "replace", flaky), mock.patch.object(self.budget.time, "sleep"):
            self.budget.write_atomic(target, "hi")
        self.assertEqual((target.read_text(), len(calls)), ("hi", 3))

    def test_write_atomic_raises_after_the_last_retry(self):
        target = self.tmp / "out.json"
        with mock.patch.object(self.budget.os, "replace", side_effect=PermissionError("held")) as replace, mock.patch.object(self.budget.time, "sleep"):
            with self.assertRaises(PermissionError):
                self.budget.write_atomic(target, "hi")
        self.assertEqual(replace.call_count, self.budget.REPLACE_TRIES)

    def test_history_is_replaced_atomically_so_a_concurrent_build_never_reads_half_of_it(self):
        self.write(self.project / "s1.jsonl", [self.prompt(2), self.step("r1", 3, 1000)])
        self.budget.build(7)
        torn, stop = [], threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    json.loads(constants.HISTORY_FILE.read_text())
                except (ValueError, OSError) as exc:
                    torn.append(exc)
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for _ in range(40):
                self.budget.build(7)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(torn, [])
        self.assertEqual([p.name for p in self.tmp.glob("*.tmp")], [])

    def test_a_session_resumed_today_counts_only_the_steps_inside_the_window(self):
        old = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * 86400))
        self.write(self.project / "s1.jsonl", [{**self.prompt(1), "timestamp": f"{old}T06:00:01Z"}, self.step("old", 2, 9999, day=old), self.prompt(3), self.step("r1", 4, 1000)])
        sessions, bill, _, prompts = self.lens.scan_sessions(7)
        info = sessions["s1"]
        self.assertEqual((len(info["steps"]), info["reread"], sum(bill.values())), (1, 1000, 1000))
        self.assertEqual((sum(prompts.values()), info["new"]), (1, False), "a resumed session is no new session")
        report = self.lens.build(7)
        self.assertIsNone(report["per_day"][0]["startup"])
        self.assertIsNone(report["totals"]["startup_mean"])

    def test_both_reports_bucket_by_local_day(self):
        self.addCleanup(lambda: (os.environ.pop("TZ", None), time.tzset()))
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        yesterday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
        self.write(self.project / "s1.jsonl", [{**self.prompt(1), "timestamp": f"{yesterday}T03:00:01Z"}, {**self.step("r1", 2, 1000), "timestamp": f"{yesterday}T03:00:02Z"}])
        expected = local(calendar.timegm(time.strptime(f"{yesterday}T03:00:02", "%Y-%m-%dT%H:%M:%S")))
        self.assertEqual([r["day"] for r in self.budget.build(7)["per_day"]], [expected])
        self.assertEqual([r["day"] for r in self.lens.build(7)["per_day"]], [expected])

    def test_an_old_history_is_rekeyed_once_and_days_without_transcripts_keep_their_row(self):
        gone = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 40 * 86400))
        ghost = time.strftime("%Y-%m-%d", time.gmtime(time.time()))
        row = lambda day, turns: {"day": day, "turns": turns, "sub_turns": 0, "mean_context": 1, "high_share": 0.0, "main": {}, "sub": {}}  # noqa: E731
        constants.HISTORY_FILE.write_text(json.dumps({"counting": 2, "per_day": [row(gone, 77), row(ghost, 500)]}))
        self.write(self.project / "s1.jsonl", [self.prompt(2), self.step("r1", 3, 1000)])
        self.budget.build(7)
        stored = json.loads(constants.HISTORY_FILE.read_text())
        self.assertEqual(stored["counting"], self.budget.HISTORY_COUNTING)
        by_day = {r["day"]: r["turns"] for r in stored["per_day"]}
        self.assertEqual(by_day.get(gone), 77, "no transcript is left for that day: its row stays")
        self.assertNotIn(ghost, by_day, "a UTC-keyed row inside the recounted range is replaced by the recount")
        self.assertEqual(by_day[self.day], 1)

    def test_delegation_needs_evidence_the_agent_call_ran(self):
        lens = self.lens
        prompt = {"event": "prompt", "ts": "2026-10-06T10:00:00", "session_id": "s", "context_tokens": 150_000,
                  "reread_check": {"mode": "delegate", "steps": 10, "here": 900_000, "delegated": 300_000}}
        ask = {"event": "agent", "session_id": "s", "decision": "ask"}
        later = {"event": "prompt", "session_id": "s"}
        asked = lens.classify(100, prompt, {}, lens.follow_ups([(100, prompt), (130, ask), (200, later)]))
        self.assertEqual((asked["kind"], asked["saved"]), ("reread_check", 0), "an agent call logged before its outcome proves nothing")
        started = lens.classify(100, prompt, {}, lens.follow_ups([(100, prompt), (130, ask), (200, later)], {"s": [140]}))
        self.assertEqual(started["kind"], "delegated", "a subagent transcript began after the call")
        allowed = lens.classify(100, prompt, {}, lens.follow_ups([(100, prompt), (130, {**ask, "decision": "allow"}), (200, later)]))
        self.assertEqual(allowed["kind"], "delegated")

    def test_an_asked_review_is_questioned_even_with_a_model_set(self):
        from core import events
        record = {"event": "agent", "session_id": "s1", "decision": "ask", "model_set": "sonnet", "agent_type": "reviewer"}
        events.log_event(record)
        self.assertEqual(self.budget.kiasi_actions(7)["counts"], {"repeat reviews questioned": 1})
        self.assertEqual(self.lens.classify(0, {**record, "ts": "2026-10-06T10:00:00"}, {})["kind"], "review_asked")

    def test_a_skipped_read_is_credited_to_its_own_agent_and_debited_by_a_repeat(self):
        lens = self.lens
        sessions = lens.Sessions()
        sessions["s"] = {"steps": [1, 2, 3, 4, 5], "prompts": [], "recalls": []}
        sessions.agent_steps["a1"] = [10, 11]
        skip = {"event": "read_skipped", "ts": "2026-10-06T10:00:00", "session_id": "s", "path": "/f", "chars": 4000, "agent_id": "a1"}
        self.assertEqual(lens.classify(0, skip, sessions)["later_steps"], 2)
        self.assertEqual(lens.classify(0, {**skip, "agent_id": "unknown"}, sessions)["saved"], 0, "an unknown agent is not credited")
        retry = {"event": "read_retry", "session_id": "s", "path": "/f"}
        logged = lens.follow_ups([(0, skip), (5, retry)])
        self.assertEqual(lens.classify(0, skip, sessions, logged)["saved"], 0)
        cap = {"event": "cap", "ts": "2026-10-06T10:00:00", "session_id": "s", "chars": 8000, "shown_chars": 0, "saved_path": "/x/out-1.txt"}
        self.assertGreater(lens.classify(0, cap, sessions)["saved"], 0)
        sessions["s"]["recalls"] = [(2, '{"file_path": "/x/out-1.txt"}')]
        self.assertEqual(lens.classify(0, cap, sessions)["saved"], 0, "the saved file was read back")

    def test_unreadable_input_is_skipped_and_counted_not_fatal(self):
        bad_stamp = {**self.step("bad", 5, 100), "timestamp": "not a time"}
        path = self.project / "s1.jsonl"
        self.write(path, [self.prompt(2), self.step("r1", 3, 1000), bad_stamp])
        with open(path, "a") as fh:
            fh.write("[1, 2]\n5\n{broken\n")
        ghost = str(self.project / "gone.jsonl")
        real = self.budget.glob.glob
        with mock.patch.object(self.budget.glob, "glob", lambda pattern: real(pattern) + ([ghost] if pattern.endswith("/*/*.jsonl") else [])):
            report = self.budget.build(7)
            lens_report = self.lens.build(7)
        self.assertEqual(report["totals"]["turns"], 1)
        self.assertEqual(report["skipped"], {"files": 1, "lines": 3, "timestamps": 1})
        self.assertEqual(lens_report["totals"]["steps"], 1)
        self.assertEqual(lens_report["skipped"]["lines"], 3)
        self.assertEqual(list(self.budget.read_lines(self.tmp / "missing.jsonl")), [])

    def test_the_last_step_band_follows_the_turn_budget_and_turn_choices_are_counted(self):
        with mock.patch.object(constants, "TURN_STOP_STEPS", 30):
            self.assertEqual(self.lens.step_buckets()[-2:], [(21, 29), (30, None)])
            self.assertEqual(self.lens.step_histogram({})[-1]["label"], "30+")
        record = {"event": "turn_choice", "session_id": "s", "choice": "stop", "steps": 60, "ts": "2026-10-06T10:00:00"}
        self.assertEqual(self.lens.classify(0, record, {})["kind"], "turn_choice")


if __name__ == "__main__":
    unittest.main()
