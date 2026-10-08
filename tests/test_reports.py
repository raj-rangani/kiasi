import importlib
import json
import os
import time
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import prompt  # noqa: E402

NOW = int(os.environ.get("KIASI_TEST_NOW", time.time()))


class PromptRows(unittest.TestCase):
    def test_prompt_rows(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location("lens", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "reports" / "lens.py")
        lens = importlib.util.module_from_spec(spec); spec.loader.exec_module(lens)
        info = {"steps": [100, 110, 120, 130, 140], "contexts": [10, 20, 30, 40, 50], "prompts": [125, 99]}
        rows = lens.prompt_rows(info)
        self.assertEqual([r["n"] for r in rows], [1, 2])
        self.assertEqual((rows[0]["step"], rows[0]["steps"], rows[0]["context"], rows[0]["cost"]), (0, 3, 10, 60))
        self.assertEqual((rows[1]["step"], rows[1]["steps"], rows[1]["cost"]), (3, 2, 90))


class TestLensSignals(KiasiTestCase):
    def test_subagent_budget_events_stay_out_of_the_turn_figures(self):
        from reports import lens
        stop = {"ts": "2026-10-06T10:00:00", "event": "turn_stop", "session_id": "s1", "steps": 40, "first": True, "subagent": True}
        self.assertIsNone(lens.classify(0, stop, {}))
        self.assertIsNone(lens.classify(0, {**stop, "event": "turn_warn", "steps": 20}, {}))
        self.assertEqual(lens.classify(0, {**stop, "steps": 60, "subagent": False}, {})["kind"], "turn_stop")

    def test_pauses_follow_ups_and_warn_mode_overruns_are_classified(self):
        from reports import lens
        stop = {"ts": "2026-10-06T10:00:00", "event": "turn_stop", "session_id": "s1", "steps": 60, "first": True, "subagent": False}
        self.assertTrue(lens.classify(0, stop, {})["label"].startswith("paused at 60 steps"))
        over = {**stop, "event": "turn_over"}
        self.assertEqual(lens.classify(0, over, {})["kind"], "turn_over")
        self.assertIsNone(lens.classify(0, {**over, "subagent": True}, {}), "a subagent's overrun is not the turn's")
        resume = {"ts": "2026-10-06T11:00:00", "event": "turn_resume", "session_id": "s2", "mode": "resume", "checklist": True, "paused_session": "s1", "steps": 60}
        resumed = lens.classify(0, resume, {})
        self.assertEqual(resumed["kind"], "turn_resume")
        self.assertIn("in a new session", resumed["label"])
        self.assertNotIn("in a new session", lens.classify(0, {**resume, "session_id": "s1"}, {})["label"])
        self.assertEqual(lens.classify(0, {**resume, "mode": "pointer"}, {})["kind"], "turn_moved_on")

    def test_miss_cause_and_recall(self):
        from reports import lens
        self.assertEqual(lens.miss_cause(10, True, False), "compaction")
        self.assertEqual(lens.miss_cause(4000, False, False), "idle over 1h")
        self.assertEqual(lens.miss_cause(10, False, False), "other")
        events = [(100, {"event": "cap", "session_id": "s", "kind": "bulk", "saved_path": "/d/outputs/a.txt"}),
                  (100, {"event": "cap", "session_id": "s", "kind": "bulk", "saved_path": "/d/outputs/b.txt"})]
        sessions = {"s": {"recalls": [(150, '{"file_path": "/d/outputs/a.txt"}'), (50, '{"file_path": "/d/outputs/b.txt"}')]}}
        self.assertEqual(lens.recall_rows(events, sessions), [{"kind": "bulk", "cuts": 2, "recalled": 1}])
        misses = [{"t": 200, "prev": 100, "tokens": 30000, "cause": "other", "session": "s"},
                  {"t": 400, "prev": 300, "tokens": 30000, "cause": "other", "session": "s"},
                  {"t": 600, "prev": 500, "tokens": 30000, "cause": "other", "session": "s"}]
        lens.attribute_misses(misses, [(150, {"event": "plugin_compact", "session_id": "s"}),
                                       (350, {"event": "prompt", "session_id": "s", "config_changed": ["plugins", "settings"]}),
                                       (550, {"event": "prompt", "session_id": "other-session", "config_changed": ["settings"]})])
        self.assertEqual([m["cause"] for m in misses], ["compaction", "plugins changed", "other"])

    def test_read_back_by_a_windows_path_counts(self):
        from reports import lens
        saved = r"C:\Users\sachin\.claude\plugins\data\kiasi-kiasi\outputs\run-20261006-1.txt"
        texts = lens.recall_texts({"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": saved}}]})
        self.assertEqual(len(texts), 1, "a Read of a saved output by its Windows path is a read-back")
        events = [(100, {"event": "cap", "session_id": "s", "kind": "bulk", "saved_path": saved})]
        with mock.patch.object(lens, "Path", PureWindowsPath):
            self.assertEqual(lens.recall_rows(events, {"s": {"recalls": [(150, texts[0])]}}), [{"kind": "bulk", "cuts": 1, "recalled": 1}])

    def test_cache_report_ranks_by_cost_and_compares_windows(self):
        from reports import lens
        # A fixed clock: the report and the day keys below must agree on the day, which they cannot across midnight.
        now = time.mktime((2026, 10, 7, 12, 0, 0, 0, 0, -1))
        day = lambda t: lens.local_day(t)
        per_day = {day(now): {"read": 900, "input": 1000}, day(now - 9 * 86400): {"read": 1, "input": 10}}
        misses = [{"t": now - 60, "prev": now - 90, "tokens": 30000, "cause": "other", "session": "s", "project": "p"},
                  {"t": now - 120, "prev": now - 150, "tokens": 90000, "cause": "idle over 1h", "session": "s", "project": "p"},
                  {"t": now - 180, "prev": now - 200, "tokens": 500000, "cause": "compaction", "session": "s", "project": "p"},
                  {"t": now - 9 * 86400, "prev": now - 9 * 86400 - 5, "tokens": 30000, "cause": "other", "session": "s", "project": "p"}]
        with mock.patch.object(lens.time, "time", return_value=now):
            report = lens.cache_report(7, per_day, misses, 10_000_000)
        self.assertEqual(report["hit_rate"], 0.9)
        self.assertEqual((report["misses"], report["avoidable"], report["idle"], report["prior_avoidable"]), (3, 1, 1, 1),
                         "a cache that expired while idle is a miss, but not an avoidable one")
        self.assertEqual([row["cause"] for row in report["causes"]], ["idle over 1h", "other", "compaction"], "by cost, compaction last")
        self.assertEqual(report["extra"], lens.extra_cost(120000), "the idle miss was paid for all the same")

    def test_a_delegation_is_credited_only_when_an_agent_call_followed(self):
        from reports import lens
        prompt = {"event": "prompt", "ts": "2026-10-06T10:00:00", "session_id": "s", "context_tokens": 150_000,
                  "reread_check": {"mode": "delegate", "steps": 10, "here": 900_000, "delegated": 300_000}}
        agent, later_prompt = {"event": "agent", "session_id": "s", "decision": "allow"}, {"event": "prompt", "session_id": "s"}
        followed = lens.classify(100, prompt, {}, lens.follow_ups([(100, prompt), (130, agent), (200, later_prompt)]))
        self.assertEqual((followed["kind"], followed["saved"]), ("delegated", 600_000))
        for why, rest in (("no Agent call", []), ("the Agent call came after the next prompt", [(120, later_prompt), (130, agent)]),
                          ("the Agent call was another session's", [(130, {**agent, "session_id": "other"})])):
            shown = lens.classify(100, prompt, {}, lens.follow_ups([(100, prompt), *rest]))
            self.assertEqual((shown["kind"], shown["saved"]), ("reread_check", 0), why)
            self.assertIn("no Agent call", shown["label"])

    def test_config_change_is_logged_from_the_second_prompt(self):
        cwd = self.tmp / "proj"
        cwd.mkdir()
        state = {}
        self.assertEqual(prompt.config_changes(state, str(cwd)), [])
        self.assertEqual(prompt.config_changes(state, str(cwd)), [])
        (cwd / "CLAUDE.md").write_text("rules")
        self.assertEqual(prompt.config_changes(state, str(cwd)), ["CLAUDE.md"])


class TestPostmortem(KiasiTestCase):
    def setUp(self):
        super().setUp()
        from reports import lens
        self.lens = lens

    def test_findings_are_ranked_by_what_they_cost(self):
        info = {"contexts": [40_000, 42_000, 90_000, 91_000, 92_000], "steps": [1, 2, 3, 4, 5], "prompts": [0]}
        acts = [{"kind": "compaction", "label": "", "record": {"context_tokens": 150_000}}]
        findings = self.lens.postmortem(info, acts, [5], 71_000)
        labels = [f["label"] for f in findings]
        self.assertIn("started at 40k", labels[0], "startup × 5 steps = 200k is the biggest cost")
        self.assertIn("compacted 1 time", labels[1])
        self.assertIn("step 3 added 48k", labels[2])
        self.assertTrue(all(f["fix"] for f in findings))

    def test_a_lean_session_has_no_findings(self):
        info = {"contexts": [8_000, 9_000], "steps": [1, 2], "prompts": [0]}
        self.assertEqual(self.lens.postmortem(info, [], [2], 8_500), [])

    def test_repeated_bash_caps_get_the_sandbox_advisory(self):
        info = {"contexts": [8_000, 9_000], "steps": [1, 2], "prompts": [0]}
        acts = [{"kind": "cap", "label": "", "record": {"kind": "bash"}} for _ in range(3)]
        findings = self.lens.postmortem(info, acts, [2], 8_500)
        self.assertEqual(len(findings), 1)
        self.assertIn("3 long command outputs were capped", findings[0]["label"])
        self.assertIn("mcp__kiasi__run", findings[0]["fix"])


class ReportTestCase(KiasiTestCase):
    """Synthetic transcripts under a temp TRANSCRIPT_ROOT; timestamps sit at local noon
    yesterday, so the local day both reports count by is the same in any zone.
    KIASI_TEST_NOW pins the clock (seconds since the epoch)."""

    def setUp(self):
        super().setUp()
        from reports import budget
        from reports import lens
        self.budget, self.lens = budget, lens
        constants.TRANSCRIPT_ROOT = self.tmp / "projects"
        self.project = constants.TRANSCRIPT_ROOT / "-home-user-app"
        self.project.mkdir(parents=True)
        if "KIASI_TEST_NOW" in os.environ:
            patcher = mock.patch("time.time", return_value=NOW)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.day = time.strftime("%Y-%m-%d", time.localtime(NOW - 86400))

    def stamp(self, second, day=None):
        noon = time.mktime(time.strptime(f"{day or self.day} 12:00:{second:02d}", "%Y-%m-%d %H:%M:%S"))  # local noon: the same local day in every zone
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(noon))

    def at(self, clock, day=None):
        """The UTC stamp of a local clock time (HH:MM:SS) on a local day, default yesterday."""
        local_time = time.mktime(time.strptime(f"{day or self.day} {clock}", "%Y-%m-%d %H:%M:%S"))
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(local_time))

    def step(self, request, second, read, create=90, day=None):
        return {"type": "assistant", "requestId": request, "timestamp": self.stamp(second, day),
                "message": {"content": [{"type": "text", "text": "ok"}],
                            "usage": {"input_tokens": 10, "cache_creation_input_tokens": create,
                                      "cache_read_input_tokens": read, "output_tokens": 5}}}

    def prompt(self, second, text="do it"):
        return {"type": "user", "timestamp": self.stamp(second), "message": {"role": "user", "content": text}}

    def tool_result(self, second):
        return {"type": "user", "timestamp": self.stamp(second),
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "done"}]}}

    def write(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


class TestBudgetReport(ReportTestCase):
    def test_one_step_per_request_even_when_split_into_blocks(self):
        """A response is one transcript line per content block; each line repeats the usage."""
        self.write(self.project / "s1.jsonl", [
            self.prompt(0), self.step("r1", 1, 1000), self.step("r1", 2, 1000), self.step("r1", 3, 1000),
            self.tool_result(4), self.step("r2", 5, 3000)])
        report = self.budget.build(7)
        [day] = report["per_day"]
        self.assertEqual(day["turns"], 2)
        self.assertEqual(day["main"]["cache_read_input_tokens"], 4000)
        self.assertEqual(day["mean_context"], (1100 + 3100) // 2)
        [session] = report["sessions"]
        self.assertEqual((session["turns"], session["peak"], session["steps_per_prompt"]), (2, 3100, 2.0))

    def test_subagent_steps_are_billed_apart_from_main(self):
        self.write(self.project / "s1.jsonl", [self.prompt(0), self.step("r1", 1, 1000)])
        self.write(self.project / "s1" / "subagents" / "agent-a.jsonl", [self.step("a1", 2, 500), self.step("a2", 3, 700)])
        [day] = self.budget.build(7)["per_day"]
        self.assertEqual((day["turns"], day["sub_turns"]), (1, 2))
        self.assertEqual((day["main"]["cache_read_input_tokens"], day["sub"]["cache_read_input_tokens"]), (1000, 1200))

    def test_steps_older_than_the_window_are_dropped_by_day_not_file_age(self):
        old = time.strftime("%Y-%m-%d", time.localtime(NOW - 10 * 86400))
        self.write(self.project / "s1.jsonl", [self.step("old", 1, 9999, day=old), self.prompt(2), self.step("r1", 3, 1000)])
        days = [row["day"] for row in self.budget.build(7)["per_day"]]
        self.assertEqual(days, [self.day])

    def test_subagent_stops_are_counted_apart_from_turn_stops(self):
        from core import events
        for subagent in (False, True, True):
            events.log_event({"event": "turn_stop", "session_id": "s1", "steps": 40, "first": True, "subagent": subagent})
        counts = self.budget.kiasi_actions(7)["counts"]
        self.assertEqual((counts.get("turns paused"), counts.get("subagents paused")), (1, 2))

    def test_resumes_skips_and_warn_mode_overruns_are_counted(self):
        from core import events
        for record in ({"event": "turn_resume", "mode": "resume"}, {"event": "turn_resume", "mode": "pointer"},
                       {"event": "turn_over", "steps": 60, "subagent": False}, {"event": "turn_over", "steps": 40, "subagent": True}):
            events.log_event({"session_id": "s1", **record})
        counts = self.budget.kiasi_actions(7)["counts"]
        self.assertEqual([counts.get(k) for k in ("pauses resumed", "pauses skipped", "turns over budget", "subagents over budget")], [1, 1, 1, 1])
        self.assertEqual(self.budget.build(7)["settings"]["turn_budget_mode"], constants.TURN_BUDGET_MODE)

    def test_compaction_is_a_drop_to_under_half_from_over_150k(self):
        self.write(self.project / "s1.jsonl", [
            self.prompt(0), self.step("r1", 1, 160_000), self.step("r2", 2, 170_000),
            self.step("r3", 3, 30_000), self.step("r4", 4, 100_000)])
        [session] = self.budget.build(7)["sessions"]
        self.assertEqual(session["compactions"], 1)


class TestHistory(ReportTestCase):
    def days_ago(self, n):
        return time.strftime("%Y-%m-%d", time.localtime(NOW - n * 86400))

    def row(self, day, turns, read):
        return {"day": day, "turns": turns, "sub_turns": 0, "mean_context": read, "high_share": 0.0,
                "main": {"cache_read_input_tokens": read * turns}, "sub": {}}

    def test_days_outside_the_window_survive_a_rebuild(self):
        constants.HISTORY_FILE.write_text(json.dumps({"per_day": [self.row(self.days_ago(20), 50, 200_000), self.row(self.day, 50, 200_000)]}))
        self.write(self.project / "s1.jsonl", [self.prompt(2), self.step("r1", 3, 1000)])
        report = self.budget.build(7)
        kept = json.loads(constants.HISTORY_FILE.read_text())["per_day"]
        self.assertEqual([(r["day"], r["turns"]) for r in kept], [(self.days_ago(20), 50), (self.day, 1)])
        self.assertEqual(report["history_days"], 2)

    def test_a_history_from_before_the_replay_fix_is_recounted_once(self):
        old = self.days_ago(20)
        self.write(self.project / "s1.jsonl", [self.step("r1", 1, 1000, day=old)])
        constants.HISTORY_FILE.write_text(json.dumps({"per_day": [self.row(old, 100, 200_000)]}))
        self.budget.build(7)
        stored = json.loads(constants.HISTORY_FILE.read_text())
        self.assertEqual((stored["counting"], stored["per_day"][0]["turns"]), (self.budget.HISTORY_COUNTING, 1))
        constants.HISTORY_FILE.write_text(json.dumps({"counting": self.budget.HISTORY_COUNTING, "per_day": [self.row(old, 100, 200_000)]}))
        self.budget.build(7)
        self.assertEqual(json.loads(constants.HISTORY_FILE.read_text())["per_day"][0]["turns"], 100, "a current history keeps the fuller stored row")

    def test_baseline_older_than_the_window_still_compares(self):
        constants.EVENT_LOG.write_text(json.dumps({"ts": f"{self.days_ago(5)}T10:00:00", "event": "cap"}) + "\n")
        constants.HISTORY_FILE.write_text(json.dumps({"per_day": [self.row(self.days_ago(20), 100, 200_000)]}))
        self.write(self.project / "s1.jsonl", [self.prompt(2), self.step("r1", 3, 50_000)])
        self.budget.build(7)
        with mock.patch.object(constants, "LENS_FACTOR_MIN_STEPS", 1):
            since = self.lens.since_install()
        self.assertEqual((since["install_day"], since["first_day"], since["history_days"]), (self.days_ago(5), self.days_ago(20), 2))
        self.assertEqual((since["before"]["days"], since["before"]["reread_per_turn"]), (1, 200_000))
        self.assertEqual((since["after"]["days"], since["after"]["reread_per_turn"]), (1, 50_000))
        self.assertEqual(since["factor"], 4.0)

    def test_savings_older_than_the_window_stay_in_the_all_time_total(self):
        constants.SAVINGS_FILE.write_text(json.dumps({"per_day": [{"day": self.days_ago(20), "saved": 500, "kept_out": 100, "actions": 3, "caps": 3}]}))
        constants.EVENT_LOG.write_text(json.dumps({"ts": f"{self.days_ago(2)}T10:00:00", "event": "cap"}) + "\n")
        self.write(self.project / "s1.jsonl", [self.prompt(2), self.step("r1", 3, 50_000)])
        all_time = self.lens.build(7)["all_time"]
        self.assertEqual((all_time["first_day"], all_time["days"], all_time["saved"], all_time["caps"]), (self.days_ago(20), 2, 500, 4))

    def test_actions_cover_the_whole_first_day_of_the_window(self):
        first_day = self.lens.local_day(self.lens.time.time() - 7 * 86400)
        constants.EVENT_LOG.write_text(json.dumps({"ts": f"{first_day}T00:00:30", "event": "cap"}) + "\n")
        self.write(self.project / "s1.jsonl", [self.prompt(2), self.step("r1", 3, 50_000)])
        self.assertEqual([a["ts"][:10] for a in self.lens.build(7)["actions"]], [first_day])


class TestEmptyReport(ReportTestCase):
    """The dashboard keys its page-level empty state off these fields."""

    def test_reports_without_transcripts_have_no_days_and_no_sessions(self):
        lens, budget = self.lens.build(7), self.budget.build(7)
        self.assertEqual((lens["per_day"], lens["sessions"], lens["totals"]["sessions"]), ([], [], 0))
        self.assertEqual((budget["per_day"], budget["sessions"]), ([], []))
        self.assertEqual((lens["days"], budget["days"]), (7, 7))

    def test_one_session_fills_both_reports(self):
        self.write(self.project / "s1.jsonl", [self.prompt(0), self.step("r1", 1, 1000)])
        self.assertEqual(len(self.lens.build(7)["per_day"]), 1)
        self.assertEqual(len(self.budget.build(7)["per_day"]), 1)


class TestLensReport(ReportTestCase):
    def test_synthetic_and_repeated_message_ids_count_once(self):
        def with_id(entry, mid):
            entry["message"]["id"] = mid
            return entry
        synthetic = self.step("s1", 2, 0)
        synthetic["message"]["model"] = "<synthetic>"
        self.write(self.project / "s1.jsonl", [
            self.prompt(0), with_id(self.step("a", 1, 1000), "m1"), synthetic,
            with_id(self.step("b", 3, 1000), "m1"), with_id(self.step("c", 4, 5000), "m2")])
        info = self.lens.scan_sessions(7)[0]["s1"]
        self.assertEqual(info["contexts"], [1100, 5100])

    def test_median_p90_and_the_runaway_flag_past_200k(self):
        steps = [self.prompt(0)] + [self.step(f"r{i}", i + 1, read) for i, read in enumerate([50000, 100000, 150000, 210000])]
        self.write(self.project / "s1.jsonl", steps)
        os.environ.pop(self.lens.WINDOW_ENV, None)
        report = self.lens.build(7)
        [session] = report["sessions"]
        self.assertTrue(session["runaway"])
        self.assertEqual((session["median_context"], session["p90_context"], session["peak"]), (100100, 210100, 210100))
        self.assertEqual((report["totals"]["runaway_sessions"], report["totals"]["context_median"]), (1, 100100))
        self.assertEqual(report["window"], {"env": self.lens.WINDOW_ENV, "tokens": None, "effective": 200000, "recommend": True})
        os.environ[self.lens.WINDOW_ENV] = "200000"
        try:
            self.assertFalse(self.lens.window_in_effect()["recommend"])
        finally:
            del os.environ[self.lens.WINDOW_ENV]

    def test_prefix_audit_sizes_the_first_attachments_per_project(self):
        reminder = {"type": "user", "timestamp": self.stamp(0), "message": {"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>Contents of /repo/CLAUDE.md:\n" + "x" * 400 + "</system-reminder>"},
            {"type": "text", "text": "<system-reminder>The following deferred tools are now available: mcp__db__query\n" + "y" * 200 + "</system-reminder>"},
            {"type": "text", "text": "<system-reminder>something unknown</system-reminder>"}]}}
        skills = {"type": "attachment", "timestamp": self.stamp(0), "attachment": {"type": "skill_listing", "content": "z" * 300, "skillCount": 3}}
        self.write(self.project / "s1.jsonl", [reminder, skills, self.prompt(1), self.step("r1", 2, 40000)])
        [prefix] = self.lens.build(7)["prefix"].values()
        self.assertEqual(prefix["floor_tokens"], 40100)
        kinds = {p["kind"]: p["chars"] for p in prefix["pieces"]}
        self.assertGreater(kinds["CLAUDE.md files"], 400)
        self.assertGreater(kinds["MCP tool schemas"], 200)
        self.assertGreater(kinds["plugins and skills"], 300)
        self.assertIn("other", kinds)

    def test_synthetic_entries_are_counted_per_session_and_in_the_totals(self):
        synthetic = {**self.step("x", 1, 0), "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "old"}], "usage": {}}}
        self.write(self.project / "s1.jsonl", [self.prompt(0), synthetic, {**synthetic, "requestId": "y"}, self.step("r1", 2, 1000)])
        report = self.lens.build(7)
        self.assertEqual((report["sessions"][0]["synthetic_skipped"], report["totals"]["synthetic_skipped"]), (2, 2))

    def test_synthetic_entries_are_classified_by_kind(self):
        def synth(uuid, text, **extra):
            return {**self.step(uuid, 1, 0), "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}], "usage": {}}, **extra}
        entries = [self.prompt(0), synth("a", "an earlier answer"), synth("b", "[Request interrupted by user]"), synth("c", "API Error: The response stopped arriving."),
                   synth("d", "No response requested."), synth("e", "boom", isApiErrorMessage=True), self.step("r1", 2, 1000)]
        self.write(self.project / "s1.jsonl", entries)
        report = self.lens.build(7)
        want = {"replayed": 1, "interrupted": 1, "api_error": 2, "no_response": 1}
        self.assertEqual(report["sessions"][0]["synthetic_kinds"], want)
        self.assertEqual(report["totals"]["synthetic_kinds"], want)
        self.assertEqual(report["totals"]["synthetic_skipped"], 5)

    def test_the_since_table_carries_median_only_when_every_day_has_it(self):
        rows = [{"day": "d1", "turns": 10, "mean_context": 100, "context": {"median": 80, "p90": 200}, "main": {}},
                {"day": "d2", "turns": 30, "mean_context": 100, "context": {"median": 40, "p90": 100}, "main": {}}]
        metrics = self.lens.period_metrics(rows)
        self.assertEqual((metrics["median_context"], metrics["p90_context"]), (50, 125))
        del rows[1]["context"]
        self.assertIsNone(self.lens.period_metrics(rows)["median_context"])

    def test_sessions_count_steps_once_and_only_typed_prompts(self):
        self.write(self.project / "s1.jsonl", [
            self.prompt(0), self.step("r1", 1, 1000), self.step("r1", 2, 1000),
            self.tool_result(3), self.step("r2", 4, 3000), self.prompt(5, "next"), self.step("r3", 6, 4000)])
        sessions, bill, main_bill, prompts = self.lens.scan_sessions(7)
        info = sessions["s1"]
        self.assertEqual((len(info["steps"]), info["contexts"], info["reread"]), (3, [1100, 3100, 4100], 8000))
        self.assertEqual(len(info["prompts"]), 2, "a tool result is not a prompt")
        self.assertEqual(sum(bill.values()), 8000)
        self.assertEqual(sum(prompts.values()), 2)
        self.assertEqual(self.lens.steps_until_next_prompt(info["steps"], info["prompts"], info["prompts"][0]), 2)
        self.assertEqual(self.lens.steps_until_next_prompt(info["steps"], info["prompts"], info["prompts"][1]), 1)

    def cap_rows(self, entries, sub=None):
        self.write(self.project / "s1.jsonl", entries)
        if sub:
            self.write(self.project / "s1" / "subagents" / "agent-a.jsonl", sub)
        sessions = self.lens.scan_sessions(7)[0]
        saved = "/d/outputs/run-1.txt"
        events = [(self.lens.epoch_iso(self.stamp(1)), {"event": "cap", "session_id": "s1", "kind": "bulk", "saved_path": saved})]
        return self.lens.recall_rows(events, sessions)

    def use(self, second, name, tool_id, **inp):
        entry = self.step(f"u{second}", second, 100)
        entry["message"]["content"] = [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}]
        return entry

    def test_a_subagent_read_of_the_saved_path_is_a_read_back(self):
        base = [self.prompt(0), self.step("r1", 1, 1000)]
        read = [self.use(5, "Read", "a1", file_path="/d/outputs/run-1.txt")]
        self.assertEqual(self.cap_rows(base, read), [{"kind": "bulk", "cuts": 1, "recalled": 1}])

    def test_a_search_result_naming_the_saved_path_is_a_read_back(self):
        result = {"type": "user", "timestamp": self.stamp(6), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "q1", "content": [{"type": "text", "text": "hit in /d/outputs/run-1.txt"}]}]}}
        entries = [self.prompt(0), self.step("r1", 1, 1000), self.use(5, "mcp__kiasi__search", "q1", query="x"), result]
        self.assertEqual(self.cap_rows(entries), [{"kind": "bulk", "cuts": 1, "recalled": 1}])

    def test_a_cap_nobody_read_back_keeps_its_credit(self):
        self.assertEqual(self.cap_rows([self.prompt(0), self.step("r1", 1, 1000), self.tool_result(5)]), [{"kind": "bulk", "cuts": 1, "recalled": 0}])

    def test_a_session_with_entries_before_the_window_is_partial(self):
        old = time.strftime("%Y-%m-%d", time.localtime(NOW - 10 * 86400))
        self.write(self.project / "s1.jsonl", [self.step("old", 1, 9999, day=old), self.prompt(2), self.step("r1", 3, 1000)])
        self.write(self.project / "s2.jsonl", [self.prompt(2), self.step("r2", 3, 1000)])
        sessions = self.lens.scan_sessions(7)[0]
        self.assertTrue(sessions["s1"]["partial"])
        self.assertEqual(sessions["s1"]["first_day"], self.lens.local_day(self.lens.epoch_iso(self.stamp(3))))
        self.assertFalse(sessions["s2"]["partial"])

    def test_messages_replayed_by_a_pruned_compaction_are_not_counted_again(self):
        """The kept prompt comes back under its promptId and the kept answer as a zero-usage synthetic entry."""
        asked = dict(self.prompt(0), promptId="p1")
        replayed_prompt = dict(self.prompt(5), promptId="p1")
        replayed_step = self.step("x1", 5, 0, create=0)
        replayed_step["message"]["model"] = "<synthetic>"
        replayed_step["message"]["usage"]["input_tokens"] = 0
        self.write(self.project / "s1.jsonl", [
            asked, self.step("r1", 1, 1000), self.tool_result(2), self.step("r2", 3, 3000),
            replayed_prompt, replayed_step, self.step("r3", 6, 500), dict(self.prompt(7, "next"), promptId="p2"), self.step("r4", 8, 700)])
        sessions, bill, _, prompts = self.lens.scan_sessions(7)
        info = sessions["s1"]
        self.assertEqual(info["contexts"], [1100, 3100, 600, 800], "the synthetic entry is not a step")
        self.assertEqual((len(info["prompts"]), sum(prompts.values())), (2, 2))
        self.assertEqual([count for count, _ in self.lens.prompt_steps(info)], [3, 1])
        [day] = self.budget.build(7)["per_day"]
        self.assertEqual((day["turns"], day["mean_context"]), (4, (1100 + 3100 + 600 + 800) // 4))
        [session] = self.budget.build(7)["sessions"]
        self.assertEqual(session["steps_per_prompt"], 2.0)

    def test_prompts_out_of_time_order_never_give_a_negative_step_count(self):
        info = {"steps": [1, 2, 3, 4], "contexts": [10, 10, 10, 10], "prompts": [2.5, 0.5]}
        self.assertEqual(self.lens.prompt_steps(info), [(2, 20), (2, 20)])

    def test_a_compaction_summary_is_not_a_prompt(self):
        summary = dict(self.prompt(2, "This session is being continued"), isCompactSummary=True)
        self.write(self.project / "s1.jsonl", [self.prompt(0), self.step("r1", 1, 1000), summary, self.step("r2", 3, 500)])
        sessions, _, _, prompts = self.lens.scan_sessions(7)
        self.assertEqual((len(sessions["s1"]["prompts"]), sum(prompts.values())), (1, 1))
        self.assertEqual(self.budget.build(7)["totals"]["prompts"], 1)

    def test_a_continue_at_the_pause_question_ends_the_steps_counted_after_the_pause(self):
        asked = {"ts": "2026-10-06T11:00:05", "event": "turn_resume", "session_id": "s1", "mode": "resume", "asked": True, "checklist": True, "paused_session": "s1", "steps": 60}
        logged = self.lens.follow_ups([(5.0, asked), (9.0, {**asked, "asked": False})])
        self.assertEqual(logged["s1"]["renewals"], [5.0], "a continue prompt is already a prompt")
        sessions = {"s1": {"steps": [1.0, 3.0, 4.0, 6.0, 7.0, 8.0], "prompts": [0.5]}}
        pause = {"ts": "2026-10-06T11:00:02", "event": "turn_stop", "session_id": "s1", "steps": 60, "first": True, "subagent": False}
        self.assertEqual(self.lens.classify(2.0, pause, sessions, logged)["later_steps"], 2, "the checklist and the question, not the renewed work")
        self.assertEqual(self.lens.classify(2.0, pause, sessions)["later_steps"], 5)
        self.assertIn("resumed at the pause question", json.dumps(self.lens.classify(5.0, asked, sessions, logged)))

    def test_the_report_counts_resumes_against_pauses(self):
        from core import events
        pause = {"event": "turn_stop", "session_id": "s1", "steps": 60, "first": True, "subagent": False}
        for record in (pause, pause, {"event": "turn_resume", "session_id": "s1", "mode": "resume", "checklist": True, "paused_session": "s1", "steps": 60}):
            events.log_event(record)
        report = self.lens.build(7)
        self.assertEqual((report["totals"]["stops"], report["totals"]["resumed"], report["totals"]["skipped"]), (2, 1, 0))
        self.assertEqual([row["kind"] for row in report["budget_rows"]], ["turn_resume", "turn_stop", "turn_stop"])
        self.assertEqual((report["budget_rows"][0]["note"], report["budget_rows"][0]["reread"]), ("checklist written", None))
        self.assertEqual(report["settings"]["turn_budget_mode"], constants.TURN_BUDGET_MODE)

    def test_error_events_become_problems_by_kind(self):
        from core import events
        self.assertEqual(self.lens.build(7)["problems"], [])
        events.log_error("pause_record", session_id="abcdef123456", error="disk full")
        events.log_error("pause_record", session_id="abcdef123456", error="read-only")
        events.log_error("bad_setting", project="/p", settings=["turn_stop_steps"])
        events.log_event({"event": "error"})
        problems = {row["kind"]: row for row in self.lens.build(7)["problems"]}
        self.assertEqual((problems["pause_record"]["count"], problems["bad_setting"]["count"], problems["unknown"]["count"]), (2, 1, 1))
        self.assertEqual((problems["pause_record"]["message"], problems["pause_record"]["session"]), ("read-only", "abcdef12"))
        self.assertIn("nothing to resume", problems["pause_record"]["explanation"])
        self.assertIn("turn_stop_steps", problems["bad_setting"]["message"])

    def test_lens_and_budget_agree_on_steps_and_reread(self):
        self.write(self.project / "s1.jsonl", [self.prompt(0), self.step("r1", 1, 1000), self.step("r1", 2, 1000), self.step("r2", 3, 2500)])
        self.write(self.project / "s1" / "subagents" / "agent-a.jsonl", [self.step("a1", 4, 600)])
        sessions, bill, _, _ = self.lens.scan_sessions(7)
        [day] = self.budget.build(7)["per_day"]
        self.assertEqual(len(sessions["s1"]["steps"]), day["turns"])
        self.assertEqual(sum(bill.values()), day["main"]["cache_read_input_tokens"] + day["sub"]["cache_read_input_tokens"])

    def test_period_metrics_weights_means_by_main_turns(self):
        rows = [{"turns": 10, "sub_turns": 0, "mean_context": 1000, "high_share": 0.0, "main": {"cache_read_input_tokens": 50_000}},
                {"turns": 30, "sub_turns": 10, "mean_context": 3000, "high_share": 0.5, "main": {"cache_read_input_tokens": 150_000},
                 "sub": {"cache_read_input_tokens": 20_000}}]
        m = self.lens.period_metrics(rows)
        self.assertEqual((m["turns"], m["reread"], m["reread_per_turn"], m["reread_per_day"]), (40, 200_000, 5000, 100_000), "main-session steps and re-read only")
        self.assertEqual((m["mean_context"], m["high_share"]), (2500, 0.375))
        self.assertIsNone(self.lens.period_metrics([])["reread_per_turn"])

    def test_since_install_splits_on_install_day_and_computes_factor(self):
        day = lambda n: time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))
        row = lambda d, turns, read: {"day": d, "turns": turns, "sub_turns": 0, "mean_context": 0, "high_share": 0,
                                      "main": {"cache_read_input_tokens": read}}
        constants.BUDGET_FILE.write_text(json.dumps({"per_day": [
            row(day(5), 10, 400_000), row(day(4), 10, 400_000),   # before: 40k per turn
            row(day(3), 10, 999_999),                             # install day: left out of both
            row(day(2), 10, 100_000), row(day(1), 10, 100_000),   # after: 10k per turn
            row(day(0), 10, 100_000)]}))                          # today: per turn only
        constants.EVENT_LOG.write_text(json.dumps({"ts": day(3) + "T09:00:00", "event": "cap"}) + "\n")
        with mock.patch.object(constants, "LENS_FACTOR_MIN_STEPS", 20):
            result = self.lens.since_install()
        self.assertEqual(result["install_day"], day(3))
        self.assertEqual((result["before"]["days"], result["before"]["reread_per_turn"]), (2, 40_000))
        self.assertEqual((result["after"]["days"], result["after"]["reread_per_turn"], result["after"]["turns"]), (2, 10_000, 30))
        self.assertEqual(result["factor"], 4.0)
        self.assertEqual((result["before"]["steps_per_day"], result["after"]["steps_per_day"]), (10, 10))
        self.assertEqual([(p["day"], p["reread"], p["steps"]) for p in result["series"]][:1], [(day(5), 400_000, 10)])
        self.assertEqual(len(result["series"]), 6)
        few = self.lens.since_install()
        self.assertEqual((few["factor"], few["factor_min_steps"]), (None, constants.LENS_FACTOR_MIN_STEPS), "20 steps before the install are too few for a ratio")

    def test_since_install_has_no_per_day_figure_before_a_full_day(self):
        day = lambda n: time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))
        row = lambda d, read: {"day": d, "turns": 10, "sub_turns": 0, "mean_context": 0, "high_share": 0, "main": {"cache_read_input_tokens": read}}
        constants.BUDGET_FILE.write_text(json.dumps({"per_day": [row(day(3), 400_000), row(day(1), 999_999), row(day(0), 100_000)]}))
        constants.EVENT_LOG.write_text(json.dumps({"ts": day(1) + "T09:00:00", "event": "cap"}) + "\n")
        result = self.lens.since_install()
        self.assertEqual((result["after"]["days"], result["after"]["turns"]), (0, 10))
        self.assertIsNone(result["after"]["reread_per_day"], "with no full day since the install, 0 per day reads as a 100% drop")

    def test_since_install_without_a_log_or_report(self):
        self.assertEqual(self.lens.since_install(), {"install_day": None, "before": None, "after": None})


class TestCostReport(ReportTestCase):
    def test_model_price_matches_the_longest_prefix_and_dated_ids(self):
        lens = self.lens
        self.assertEqual(lens.model_price("claude-haiku-4-5-20251001"), constants.MODEL_PRICES["claude-haiku-4-5"])
        self.assertEqual(lens.model_price("claude-opus-5-5"), constants.MODEL_PRICES["claude-opus-5-5"], "opus-5-5 is not priced as opus-5")
        self.assertEqual(lens.model_price("claude-opus-5"), constants.MODEL_PRICES["claude-opus-5"])
        self.assertEqual(lens.model_price("claude-opus-5-5-20260401"), constants.MODEL_PRICES["claude-opus-5-5"], "a dated suffix matches")
        self.assertIsNone(lens.model_price("claude-opus-55"), "a prefix match needs a dash boundary")
        self.assertIsNone(lens.model_price("claude-opus-6"), "an unknown family is unpriced, not guessed")
        self.assertIsNone(lens.model_price("unknown"))

    def test_cost_report_prices_each_model_and_measures_the_cache_saving(self):
        def step(request, second, model, read, create=0, inp=0, out=0):
            entry = self.step(request, second, read, create)
            entry["message"]["model"] = model
            entry["message"]["usage"].update({"input_tokens": inp, "output_tokens": out})
            return entry
        # 1M cache reads on opus-5-5 ($0.20 read, $4 input) and 1M on fable-5-1 ($0.25 read, $10 input), plus a priced write and output
        self.write(self.project / "s1.jsonl", [self.prompt(0), step("a", 1, "claude-opus-5-5", 1_000_000, create=100_000, inp=50_000, out=10_000),
                                               step("b", 2, "claude-fable-5-1", 1_000_000), step("c", 3, "made-up-model", 500_000)])
        report = self.lens.build(7)
        cost = report["cost"]
        by_model = {row["model"]: row for row in cost["models"]}
        self.assertAlmostEqual(by_model["claude-opus-5-5"]["cost"], 0.2 + 0.5 + 0.2 + 0.2, places=2)
        self.assertAlmostEqual(by_model["claude-opus-5-5"]["saved"], 3.8, places=2, msg="1M reads at $0.20 against $4 fresh")
        self.assertAlmostEqual(by_model["claude-fable-5-1"]["saved"], 9.75, places=2)
        self.assertEqual((by_model["made-up-model"]["priced"], by_model["made-up-model"].get("cost")), (False, None))
        self.assertEqual(cost["unpriced_tokens"], 500_000)
        self.assertAlmostEqual(cost["cost"], 1.1 + 0.25, places=2)
        self.assertAlmostEqual(cost["saved"], 13.55, places=2)
        self.assertEqual(cost["models"][0]["model"], "claude-opus-5-5", "ranked by cost")
        [day] = report["per_day"]
        self.assertEqual((day["cost_usd"], day["cache_saved_usd"]), (1.35, 13.55))
        self.assertEqual(report["settings"]["holdout_rules"], list(constants.HOLDOUT_RULES))

    def test_billing_plan_comes_from_the_key_variables_and_the_account_record(self):
        write = lambda d: constants.CLAUDE_CONFIG_FILE.write_text(json.dumps(d))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}):
            self.assertEqual(self.lens.billing_plan(), "unknown", "no config file")
            write({"oauthAccount": {"billingType": "stripe_subscription"}})
            self.assertEqual(self.lens.billing_plan(), "subscription")
            write({"hasAvailableSubscription": True})
            self.assertEqual(self.lens.billing_plan(), "subscription")
            write({"primaryApiKey": "sk-ant-x", "oauthAccount": {"billingType": "stripe_subscription"}})
            self.assertEqual(self.lens.billing_plan(), "api", "a stored key means per-token billing")
            write({"oauthAccount": {}})
            self.assertEqual(self.lens.billing_plan(), "unknown")
            self.assertEqual(self.lens.build(7)["settings"]["plan"], "unknown")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-x"}):
            self.assertEqual(self.lens.billing_plan(), "api")

    def test_subagent_steps_are_priced_too(self):
        self.write(self.project / "s1.jsonl", [self.prompt(0), self.step("a", 1, 1000)])
        sub = self.step("x", 2, 2_000_000)
        sub["message"]["model"] = "claude-haiku-5-5"
        self.write(self.project / "s1" / "subagents" / "agent-1.jsonl", [sub])
        cost = self.lens.cost_report(self.lens.scan_sessions(7)[0].usage)
        self.assertAlmostEqual(cost["saved"], 2 * (0.1 - 0.01), places=3)
        self.assertEqual(cost["unpriced_tokens"], 1000 + 10 + 90 + 5, "the main step carries no model in this fixture")


class TestPaceReport(ReportTestCase):
    def test_pace_compares_the_last_seven_full_days_with_the_seven_before(self):
        day = lambda n: time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))
        row = lambda d, main, sub=0: {"day": d, "main": {"cache_read_input_tokens": main}, "sub": {"cache_read_input_tokens": sub}}
        constants.BUDGET_FILE.write_text(json.dumps({"per_day": [row(day(10), 700_000), row(day(3), 350_000, 350_000), row(day(1), 700_000), row(day(0), 50_000)]}))
        pace = self.lens.pace_report(50_000)
        self.assertEqual((pace["rate"], pace["week"], pace["prior_rate"], pace["prior_week"], pace["today"]), (200_000, 1_400_000, 100_000, 700_000, 50_000))
        self.assertEqual(len(pace["series"]), 14)
        self.assertEqual(pace["series"][-1], {"day": day(1), "tokens": 700_000}, "today is partial and left out of the series")
        constants.BUDGET_FILE.write_text(json.dumps({"per_day": [row(day(2), 100)]}))
        self.assertIsNone(self.lens.pace_report(0)["prior_week"], "no row in the week before means no comparison")
        constants.BUDGET_FILE.write_text(json.dumps({"per_day": [row(day(0), 100)]}))
        self.assertIsNone(self.lens.pace_report(100), "today alone is not a full day")


    def test_per_day_rows_carry_main_session_steps_per_prompt(self):
        self.write(self.project / "s1.jsonl", [self.prompt(0), self.step("a", 1, 1000), self.step("b", 2, 1000), self.prompt(3), self.step("c", 4, 1000)])
        self.write(self.project / "s1" / "subagents" / "agent-1.jsonl", [self.step("x", 5, 1000)])
        [day] = self.lens.build(7)["per_day"]
        self.assertEqual((day["prompts"], day["steps"], day["steps_per_prompt"]), (2, 3, 1.5), "subagent steps are not main-session steps")


class TestRuleOutcomes(ReportTestCase):
    def session(self, name, prompts, steps, agents=(), read=100_000):
        """A transcript with typed prompts at the given seconds, steps (one request each) and Agent calls."""
        entries = [self.prompt(s) for s in prompts] + [self.step(f"{name}-{s}", s, read) for s in steps]
        for s in agents:
            entry = self.step(f"{name}-agent-{s}", s, read)
            entry["message"]["content"] = [{"type": "tool_use", "id": f"call-{s}", "name": "Agent", "input": {}}]
            entries.append(entry)
        self.write(self.project / f"{name}.jsonl", entries)

    def outcomes(self, events, sessions=True):
        found = self.lens.scan_sessions(7)[0] if sessions else {}
        timed = [(self.lens.epoch_iso(self.stamp(second)), {"session_id": "", **record}) for second, record in events]
        return self.lens.rule_outcomes(timed, found)

    def prompts(self, sid, seconds):
        return [(s, {"event": "prompt", "session_id": sid}) for s in seconds]

    def test_reread_is_attributed_to_the_floor_then_to_what_sat_in_the_context(self):
        path = constants.TRANSCRIPT_ROOT / "-proj" / "attr.jsonl"
        self.write(path, [self.prompt(0), self.step("attr-1", 1, 0), self.tool_result(2), self.step("attr-2", 3, 300)])
        kinds = {row["kind"]: row["tokens"] for row in self.lens.build(7)["attribution"]["kinds"]}
        self.assertEqual(kinds["floor"], 100, "the first request's context is the floor, paid first on every later step")
        self.assertEqual(sum(kinds.values()), 300, "the whole re-read of the later step is accounted for")
        self.assertGreater(kinds["other tool results"], kinds["assistant text"], "the rest is split by the size of what each kind left in the context")

    def test_spikes_are_sessions_far_above_the_median_reread_per_prompt(self):
        self.session("a", [1, 5], [2, 6])
        self.session("b", [1, 5], [2, 6])
        self.session("c", [1, 5], [2, 6], read=3_000_000)
        spikes = self.lens.build(7)["spikes"]
        self.assertEqual([row["session"] for row in spikes["rows"]], ["c"])
        self.assertEqual((spikes["rows"][0]["factor"], spikes["median_per_prompt"]), (30.0, 100_000))

    def test_delegation_followed_and_paid_against_the_prediction_split_by_brief(self):
        self.session("a", [1], [2, 3], agents=[4])
        self.session("b", [20], [21, 22])
        check = lambda brief, here: {"mode": "delegate", "steps": 3, "here": here, "delegated": 100_000, "brief": brief}  # noqa: E731
        events = [(1, {"event": "prompt", "session_id": "a", "reread_check": check(True, 900_000)}), (10, {"event": "prompt", "session_id": "a"}),
                  (20, {"event": "prompt", "session_id": "b", "reread_check": check(False, 600_000)})]
        out = self.outcomes(events)["reread"]
        self.assertEqual((out["fired"], out["followed"], out["rate"]), (2, 1, 0.5))
        self.assertEqual(out["effect"], "delegated turns paid 300k vs 900k predicted here (1); the others paid 200k vs 600k (1)")
        self.assertEqual((out["split"]["brief"]["followed"], out["split"]["no_brief"]["followed"], out["split"]["no_brief"]["fired"]), (1, 0, 1))

    def test_a_warning_is_followed_when_the_turn_ends_within_the_comply_steps(self):
        self.session("w1", [1, 30], [2, 4, 5])
        self.session("w2", [1], range(2, 15))
        events = [(3, {"event": "turn_warn", "session_id": "w1", "steps": 40}), (3, {"event": "turn_warn", "session_id": "w2", "steps": 40}),
                  (3, {"event": "turn_warn", "session_id": "w2", "subagent": True})]
        out = self.outcomes(events)["turn"]
        self.assertEqual((out["fired"], out["followed"], out["effect"]), (2, 1, "mean 6.5 steps after the warning"))

    def test_a_handoff_notice_is_followed_by_a_restored_session_in_the_project(self):
        self.session("h1", [1], [2, 11], read=160_000)
        self.session("h2", [20], [21], read=49_900)
        self.session("h3", [40], [41], read=150_000)
        events = [(10, {"event": "prompt", "session_id": "h1", "handoff_notice": "x", "context_tokens": 160_000}),
                  (19, {"event": "session_start", "session_id": "h2", "taskfile": True}),
                  (40, {"event": "prompt", "session_id": "h3", "handoff_notice": "x", "context_tokens": 150_000}),
                  (45, {"event": "session_start", "session_id": "h3", "taskfile": False})]
        out = self.outcomes(events)["handoff"]
        self.assertEqual((out["fired"], out["followed"]), (2, 1))
        self.assertEqual(out["effect"], "restored sessions started at a median 50k against 160k at the notice")

    def test_a_nudge_is_followed_by_a_compaction_within_three_prompts(self):
        self.session("n1", [1], [1, 2])
        self.session("n2", [40], [41, 42])
        events = [(1, {"event": "prompt", "session_id": "n1", "nudge": "x", "context_tokens": 160_000}), *self.prompts("n1", [2, 3, 4]), (3, {"event": "compact", "session_id": "n1"}),
                  (40, {"event": "prompt", "session_id": "n2", "nudge": "x", "context_tokens": 120_000}), *self.prompts("n2", [41, 42, 43, 44])]
        out = self.outcomes(events)["nudge"]
        self.assertEqual((out["fired"], out["followed"], out["effect"]), (2, 1, "followed at a median 160k context, ignored at 120k"))

    def test_a_task_switch_notice_is_followed_by_a_new_session_in_the_project(self):
        self.session("t1", [1], [1, 2])
        self.session("t2", [3], [3, 4])
        self.session("t3", [40], [41, 42])
        events = [(1, {"event": "prompt", "session_id": "t1", "task_switch": True, "context_tokens": 90_000}), *self.prompts("t1", [2, 3, 4]),
                  (40, {"event": "prompt", "session_id": "t3", "task_switch": True, "context_tokens": 80_000}), *self.prompts("t3", [41, 42, 43])]
        out = self.outcomes(events)["task_switch"]
        self.assertEqual((out["fired"], out["followed"]), (2, 1))

    def test_skips_nudges_and_routing_report_what_held(self):
        at = lambda second, event, **rest: (second, {"event": event, "session_id": "r", **rest})  # noqa: E731
        events = [at(1, "read_skipped", path="a", chars=8000), at(2, "read_skipped", path="b", chars=8000), at(3, "read_retry", path="a"),
                  at(1, "read_nudge", path="a"), at(2, "read_nudge_repeat", path="a"), at(5, "read_nudge", path="b"),
                  at(1, "routed", tool_name="Bash"), at(2, "routed", tool_name="Bash"), at(4, "route_retry", tool_name="Bash")]
        out = self.outcomes(events, sessions=False)
        self.assertEqual((out["reads"]["fired"], out["reads"]["followed"]), (2, 1))
        self.assertEqual(out["reads"]["effect"], "2k tokens not re-sent; 1 repeated and let through")
        self.assertEqual((out["read_nudge"]["fired"], out["read_nudge"]["followed"], out["read_nudge"]["effect"]), (2, 1, "1 read in full anyway"))
        self.assertEqual((out["route"]["fired"], out["route"]["followed"]), (2, 1))
        self.assertNotIn("reread", out)

    def test_the_holdout_compares_sessions_with_the_rule_on_and_off(self):
        def start(name, off):
            return (0, {"event": "session_start", "session_id": name, "holdout": {"rule": "reread_check", "off": off}})
        events = []
        for i in range(10):
            self.session(f"on{i}", [1], [2, 3], read=1000)
            events.append(start(f"on{i}", False))
        for i in range(9):
            self.session(f"off{i}", [1], [2, 3, 4], read=1000)
            events.append(start(f"off{i}", True))
        found = self.lens.scan_sessions(7)[0]
        timed = [(self.lens.epoch_iso(self.stamp(s)), r) for s, r in events]
        report = self.lens.experiment_report(timed, found)
        self.assertEqual((report["rule"], report["enough"]), ("reread_check", False))
        self.assertEqual(report["on"], {"sessions": 10, "prompts": 10, "median_context": 1100, "reread_per_prompt": 2000, "steps_per_prompt": 2.0})
        self.assertEqual((report["off"]["reread_per_prompt"], report["off"]["steps_per_prompt"]), (3000, 3.0))
        self.session("off9", [1], [2, 3, 4], read=1000)
        timed.append((self.lens.epoch_iso(self.stamp(0)), start("off9", True)[1]))
        self.assertTrue(self.lens.experiment_report(timed, self.lens.scan_sessions(7)[0])["enough"])
        self.assertIsNone(self.lens.experiment_report([], found))
