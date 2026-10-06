import importlib
import json
import time
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import prompt  # noqa: E402


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
        now = time.time()
        day = lambda t: lens.local_day(t)
        per_day = {day(now): {"read": 900, "input": 1000}, day(now - 9 * 86400): {"read": 1, "input": 10}}
        misses = [{"t": now - 60, "prev": now - 90, "tokens": 30000, "cause": "other", "session": "s", "project": "p"},
                  {"t": now - 120, "prev": now - 150, "tokens": 90000, "cause": "idle over 1h", "session": "s", "project": "p"},
                  {"t": now - 180, "prev": now - 200, "tokens": 500000, "cause": "compaction", "session": "s", "project": "p"},
                  {"t": now - 9 * 86400, "prev": now - 9 * 86400 - 5, "tokens": 30000, "cause": "other", "session": "s", "project": "p"}]
        report = lens.cache_report(7, per_day, misses, 10_000_000)
        self.assertEqual(report["hit_rate"], 0.9)
        self.assertEqual((report["misses"], report["avoidable"], report["prior_avoidable"]), (3, 2, 1))
        self.assertEqual([row["cause"] for row in report["causes"]], ["idle over 1h", "other", "compaction"], "by cost, compaction last")
        self.assertEqual(report["extra"], lens.extra_cost(120000))

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
    """Synthetic transcripts under a temp TRANSCRIPT_ROOT; timestamps sit at 06:00 UTC
    yesterday so the UTC day (budget.py) and the local day (lens.py) agree in any zone
    from UTC-6 to UTC+14."""

    def setUp(self):
        super().setUp()
        from reports import budget
        from reports import lens
        self.budget, self.lens = budget, lens
        constants.TRANSCRIPT_ROOT = self.tmp / "projects"
        self.project = constants.TRANSCRIPT_ROOT / "-home-user-app"
        self.project.mkdir(parents=True)
        self.day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))

    def stamp(self, second, day=None):
        return f"{day or self.day}T06:00:{second:02d}Z"

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
        old = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * 86400))
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
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() - n * 86400))

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
        self.assertEqual((m["turns"], m["reread"], m["reread_per_turn"], m["reread_per_day"]), (50, 220_000, 4400, 110_000))
        self.assertEqual((m["mean_context"], m["high_share"]), (2500, 0.375))
        self.assertEqual(self.lens.period_metrics([])["reread_per_turn"], 0)

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
        result = self.lens.since_install()
        self.assertEqual(result["install_day"], day(3))
        self.assertEqual((result["before"]["days"], result["before"]["reread_per_turn"]), (2, 40_000))
        self.assertEqual((result["after"]["days"], result["after"]["reread_per_turn"], result["after"]["turns"]), (2, 10_000, 30))
        self.assertEqual(result["factor"], 4.0)
        self.assertEqual((result["before"]["steps_per_day"], result["after"]["steps_per_day"]), (10, 10))
        self.assertEqual([(p["day"], p["reread"], p["steps"]) for p in result["series"]][:1], [(day(5), 400_000, 10)])
        self.assertEqual(len(result["series"]), 6)

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
