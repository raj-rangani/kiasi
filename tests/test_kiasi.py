"""Tests for the kiasi hook handlers, run with:
    python3 -m unittest discover -s tests
Each test points constants.DATA_DIR (and the derived *_DIR constants) at a
fresh temp directory so nothing touches a real data dir.
"""
import importlib
import json
import os
import time
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import constants  # noqa: E402
import kiasi  # noqa: E402


class KiasiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kiasi-test-"))
        self._patch_data_dir(self.tmp)
        kiasi.ensure_dirs()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_data_dir(self, data_dir):
        constants.DATA_DIR = data_dir
        constants.LOG_DIR = data_dir
        constants.EVENT_LOG = data_dir / "kiasi.jsonl"
        constants.SESSION_DIR = data_dir / "sessions"
        constants.PASTE_DIR = data_dir / "pastes"
        constants.OUTPUT_DIR = data_dir / "outputs"
        constants.NOTES_DIR = data_dir / "notes"
        constants.CHECKPOINT_DIR = data_dir / "checkpoints"
        constants.BUDGET_FILE = data_dir / "budget.json"
        constants.HOME_DIR = data_dir / "home"
        constants.DATA_DIR_POINTER = constants.HOME_DIR / "data-dir"
        constants.LEGACY_HOME_DIR = data_dir / "legacy-home"


class TestOutputCap(KiasiTestCase):
    def test_cap_cuts_and_saves(self):
        big_text = "line of output\n" * 2000  # well over CAP_OUTSIDE_READ_CHARS
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_use_id": "toolu_cap_1",
            "session_id": "sess-cap",
            "transcript_path": str(self.tmp / "missing-transcript.jsonl"),
            "tool_input": {"file_path": "/home/user/.claude/projects/whatever/some.jsonl"},
            "tool_response": big_text,
        }
        result = kiasi.handle_tool_output(payload)
        self.assertIsNotNone(result, "an over-cap outside read should return capped output")
        context = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("[kiasi kept", context)
        saved = list(constants.OUTPUT_DIR.glob("*toolu_cap_1*"))
        self.assertTrue(saved, "the full output should be saved under OUTPUT_DIR")
        self.assertEqual(saved[0].read_text(), big_text)


class TestPasteRefusal(KiasiTestCase):
    def test_over_limit_paste_is_refused_and_saved(self):
        prompt = "x" * (constants.PASTE_BLOCK_CHARS + 10)
        payload = {
            "prompt": prompt,
            "session_id": "sess-paste",
            "transcript_path": str(self.tmp / "missing-transcript.jsonl"),
        }
        result = kiasi.handle_prompt(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")
        self.assertIn("saved at", result["reason"])
        saved_files = list(constants.PASTE_DIR.glob("*.txt"))
        self.assertTrue(saved_files, "the refused paste should be saved to PASTE_DIR")
        self.assertEqual(saved_files[0].read_text(), prompt)

    def test_under_limit_paste_is_not_refused(self):
        payload = {
            "prompt": "a short prompt",
            "session_id": "sess-paste-2",
            "transcript_path": str(self.tmp / "missing-transcript.jsonl"),
        }
        result = kiasi.handle_prompt(payload)
        self.assertIsNone(result)


class TestTurnGuard(KiasiTestCase):
    def test_stops_after_budget(self):
        session_id = "sess-turn"
        transcript_path = str(self.tmp / "missing-transcript.jsonl")
        blocked_at = None
        for step in range(1, constants.TURN_STOP_STEPS + 5):
            payload = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "session_id": session_id,
                "transcript_path": transcript_path,
            }
            result = kiasi.turn_guard(payload, 1000)
            if result and result.get("decision") == "block":
                blocked_at = step
                break
        self.assertIsNotNone(blocked_at, "turn_guard should block once the step budget is exhausted")
        self.assertGreaterEqual(blocked_at, constants.TURN_STOP_STEPS)


class TestLegacyMigration(KiasiTestCase):
    def test_moves_old_data_dir_and_links_it(self):
        old = self.tmp / "plugins" / "ankush-inline"
        new = self.tmp / "plugins" / "kiasi-inline"
        (old / "outputs").mkdir(parents=True)
        (old / "outputs" / "saved.txt").write_text("full text")
        new.mkdir()
        self.assertTrue(kiasi.migrate_legacy_dir(old, new))
        self.assertTrue(old.is_symlink())
        self.assertEqual((old / "outputs" / "saved.txt").read_text(), "full text")

    def test_merges_old_event_log_before_new_events(self):
        (self.tmp / "ankush.jsonl").write_text('{"event": "old"}')
        constants.EVENT_LOG.write_text('{"event": "new"}\n')
        self.assertTrue(kiasi.merge_legacy_log(self.tmp))
        self.assertEqual(constants.EVENT_LOG.read_text(), '{"event": "old"}\n{"event": "new"}\n')
        self.assertFalse((self.tmp / "ankush.jsonl").exists())
        self.assertFalse(kiasi.merge_legacy_log(self.tmp))

    def test_leaves_non_empty_new_dir_alone(self):
        old = self.tmp / "old"
        new = self.tmp / "new"
        old.mkdir()
        new.mkdir()
        (new / "kiasi.jsonl").write_text("{}\n")
        self.assertFalse(kiasi.migrate_legacy_dir(old, new))
        self.assertTrue(old.is_dir() and not old.is_symlink())


class TestSessionStart(KiasiTestCase):
    def test_injects_rules(self):
        payload = {"session_id": "sess-start", "cwd": str(self.tmp), "source": "startup"}
        result = kiasi.handle_session_start(payload)
        self.assertIsNotNone(result)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Context rules", text)
        self.assertIn("Compact instructions", text)


if __name__ == "__main__":
    unittest.main()


class PromptRows(unittest.TestCase):
    def test_prompt_rows(self):
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location("lens", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lens.py")
        lens = importlib.util.module_from_spec(spec); spec.loader.exec_module(lens)
        info = {"steps": [100, 110, 120, 130, 140], "contexts": [10, 20, 30, 40, 50], "prompts": [125, 99]}
        rows = lens.prompt_rows(info)
        self.assertEqual([r["n"] for r in rows], [1, 2])
        self.assertEqual((rows[0]["step"], rows[0]["steps"], rows[0]["context"], rows[0]["cost"]), (0, 3, 10, 60))
        self.assertEqual((rows[1]["step"], rows[1]["steps"], rows[1]["cost"]), (3, 2, 90))


class TestPluginCompactSession(KiasiTestCase):
    def test_logs_session_id_and_lens_keeps_it(self):
        import json
        import lens
        kiasi.handle_plugin_compact({"hook_event_name": "PluginCompact", "session_id": "abcdef12-3456", "trigger": "auto", "mode": "prune",
                                     "level": 1, "messages": 40, "tokens_before": 70000, "tokens_after": 30000})
        record = json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])
        self.assertEqual(record["session_id"], "abcdef12-3456")
        events = [(lens.epoch_local(record["ts"]), record)]
        self.assertEqual(lens.compaction_rows(events)[0]["session"], "abcdef12")

    def test_join_skips_a_compaction_from_another_session(self):
        import lens
        ts = "2026-09-29T12:00:00"
        t = lens.epoch_local(ts)
        compact = {"ts": ts, "event": "compact", "session_id": "aaaaaaaa-1", "trigger": "auto", "context_tokens": 160000}
        plugin = {"ts": ts, "event": "plugin_compact", "session_id": "bbbbbbbb-2", "mode": "prune", "level": 0, "tokens_before": 70000, "tokens_after": 20000}
        rows = lens.compaction_rows([(t, compact), (t, plugin)])
        self.assertEqual(sorted(r["session"] for r in rows), ["aaaaaaaa", "bbbbbbbb"])
        self.assertEqual(next(r for r in rows if r["session"] == "aaaaaaaa")["mode"], "")


class TestFailureCut(KiasiTestCase):
    def test_keeps_failures_with_context(self):
        lines = [f"PASS test {i}" for i in range(300)]
        lines[150] = "FAIL tests/user.test.ts > rejects bad email"
        lines[151] = "  expected 400, got 200"
        shown = kiasi.failure_cut("\n".join(lines), "/tmp/x")
        self.assertIn("rejects bad email", shown)
        self.assertIn("expected 400, got 200", shown)
        self.assertNotIn("PASS test 100", shown)
        self.assertIn("PASS test 299", shown)
        self.assertLess(len(shown), 3000)

    def test_quiet_event_logged(self):
        import json
        kiasi.handle_plugin_quiet({"hook_event_name": "PluginQuiet", "session_id": "s1", "before": "npm install", "after": "npm install --loglevel=warn", "applied": ["npm"]})
        record = json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])
        self.assertEqual(record["event"], "quiet")
        self.assertEqual(record["applied"], ["npm"])


class TestCleanOutput(KiasiTestCase):
    def bash(self, command, text):
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_clean", "session_id": "s-clean",
                "tool_input": {"command": command}, "tool_response": {"stdout": text, "stderr": ""}}

    def test_strips_colour_progress_and_repeats(self):
        text = "\x1b[32mok\x1b[0m\n10%\r50%\r100%\nsame\nsame\nsame\nsame\n\n\nend"
        self.assertEqual(kiasi.clean_output(text), "ok\n100%\nsame\n[kiasi collapsed 3 repeats of the line above]\n\n\nend")

    def test_cleans_bulk_output_and_saves_original(self):
        text = "\n".join(f"\x1b[33mwarn\x1b[0m building module {i}" for i in range(150))
        result = kiasi.handle_tool_output(self.bash("npm run build", text))
        shown = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertNotIn("\x1b", shown)
        self.assertIn("original output saved at", shown)
        record = json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])
        self.assertEqual(record["kind"], "clean")
        self.assertEqual(Path(record["saved_path"]).read_text(), text)

    def test_never_touches_selector_commands(self):
        text = "\n".join(["    }"] * 900)
        self.assertIsNone(kiasi.handle_tool_output(self.bash("cat src/app.ts", text)))
        self.assertIsNone(kiasi.handle_tool_output(self.bash("sed -n 1,900p src/app.ts", text)))

    def test_plain_non_bulk_output_is_left_alone(self):
        self.assertIsNone(kiasi.handle_tool_output(self.bash("python3 report.py", "row\n" * 900)))


class TestLoopCheck(KiasiTestCase):
    def fail(self, command):
        return kiasi.handle_tool_failure({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "session_id": "s-loop",
                                          "transcript_path": "/tmp/loop.jsonl", "tool_input": {"command": command}, "error": "exit 1"})

    def test_nudges_once_at_third_identical_failure(self):
        self.assertIsNone(self.fail("pnpm build"))
        self.assertIsNone(self.fail("pnpm build"))
        third = self.fail("pnpm build")
        self.assertIn("failed 3 times", third["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(third["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
        self.assertIsNone(self.fail("pnpm build"))

    def test_nudges_on_variations_of_one_command(self):
        results = [self.fail(f"pnpm build --filter app{i}") for i in range(5)]
        self.assertTrue(all(r is None for r in results[:4]))
        self.assertIn("with different arguments", results[4]["hookSpecificOutput"]["additionalContext"])

    def test_failures_count_toward_turn_budget_and_reset_per_prompt(self):
        for _ in range(3):
            self.fail("make")
        state = kiasi.load_session("s-loop")
        self.assertEqual(state["turns"]["loop"]["steps"], 3)
        kiasi.handle_prompt({"prompt": "a new task for the loop test", "session_id": "s-loop", "transcript_path": "/tmp/loop.jsonl"})
        self.assertIsNone(self.fail("make"))

    def test_interrupts_are_ignored(self):
        payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "session_id": "s-loop", "is_interrupt": True, "tool_input": {"command": "x"}}
        self.assertIsNone(kiasi.handle_tool_failure(payload))


class TestStateBlock(KiasiTestCase):
    def transcript(self):
        path = self.tmp / "abcdef12-0000.jsonl"
        rows = [
            {"type": "user", "message": {"content": "add the discount validation to the order service"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/repo/order.ts"}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pnpm test"}},
                {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "pnpm lint"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "t2", "content": "1 failed", "is_error": True},
                {"type": "tool_result", "tool_use_id": "t3", "content": "bad", "is_error": True}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "pnpm lint"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t4", "content": "clean"}]}},
            {"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued from a previous conversation"}},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return path

    def test_injected_only_after_compaction(self):
        path = self.transcript()
        kiasi.log_event({"event": "cap", "session_id": "abcdef12-0000", "saved_path": "/data/outputs/t9.txt"})
        (constants.CHECKPOINT_DIR / "abcdef12-4.md").write_text("- [ ] finish")
        payload = {"session_id": "abcdef12-0000", "cwd": str(self.tmp), "transcript_path": str(path)}
        text = kiasi.handle_session_start({**payload, "source": "compact"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Task: add the discount validation", text)
        self.assertIn("Files edited: /repo/order.ts", text)
        self.assertIn("Still failing at the last attempt: Bash pnpm test", text)
        self.assertNotIn("pnpm lint", text)
        self.assertIn("/data/outputs/t9.txt", text)
        self.assertIn("abcdef12-4.md", text)
        startup = kiasi.handle_session_start({**payload, "source": "startup"})["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("kiasi state after compaction", startup)


class TestReadSkip(KiasiTestCase):
    def payload(self, event, path, **extra):
        return {"hook_event_name": event, "session_id": "s-read", "transcript_path": "/tmp/main.jsonl", "tool_name": "Read", "tool_input": {"file_path": str(path)}, **extra}

    def read_done(self, path):
        response = {"type": "text", "file": {"content": "x" * 5000}}
        kiasi.track_reads(self.payload("PostToolUse", path, tool_response=response), None)

    def test_skips_unchanged_then_lets_retry_through(self):
        path = constants.DATA_DIR / "big.txt"
        path.write_text("x" * 5000)
        self.read_done(path)
        denied = kiasi.handle_pre_tool(self.payload("PreToolUse", path))
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(kiasi.handle_pre_tool(self.payload("PreToolUse", path)))

    def test_change_edit_and_compaction_clear_the_entry(self):
        path = constants.DATA_DIR / "big.txt"
        path.write_text("x" * 5000)
        self.read_done(path)
        path.write_text("y" * 5001)
        self.assertIsNone(kiasi.handle_pre_tool(self.payload("PreToolUse", path)))
        self.read_done(path)
        kiasi.track_reads({"session_id": "s-read", "tool_name": "Edit", "tool_input": {"file_path": str(path)}}, None)
        self.assertIsNone(kiasi.handle_pre_tool(self.payload("PreToolUse", path)))
        self.read_done(path)
        kiasi.forget_reads("s-read")
        self.assertIsNone(kiasi.handle_pre_tool(self.payload("PreToolUse", path)))

    def test_small_reads_are_not_tracked(self):
        path = constants.DATA_DIR / "small.txt"
        path.write_text("x")
        kiasi.track_reads(self.payload("PostToolUse", path, tool_response={"type": "text", "file": {"content": "x"}}), None)
        self.assertIsNone(kiasi.handle_pre_tool(self.payload("PreToolUse", path)))


class TestArchive(KiasiTestCase):
    def test_writes_numbered_sections_inside_outputs_only(self):
        path = kiasi.handle_archive_path({"session_id": "abcdef123456"})["path"]
        kiasi.handle_archive({"path": path, "items": ["first", "second"]})
        text = open(path).read()
        self.assertIn("=== #2 ===\nsecond", text)
        outside = constants.DATA_DIR / "elsewhere.txt"
        kiasi.handle_archive({"path": str(outside), "items": ["x"]})
        self.assertFalse(outside.exists())


class TestLensSignals(KiasiTestCase):
    def test_miss_cause_and_recall(self):
        import lens
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

    def test_cache_report_ranks_by_cost_and_compares_windows(self):
        import lens
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
        self.assertEqual(kiasi.config_changes(state, str(cwd)), [])
        self.assertEqual(kiasi.config_changes(state, str(cwd)), [])
        (cwd / "CLAUDE.md").write_text("rules")
        self.assertEqual(kiasi.config_changes(state, str(cwd)), ["CLAUDE.md"])


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


class TestSandbox(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import sandbox
        self.sandbox = sandbox

    def run_cli(self, *argv):
        import contextlib
        import io
        out = io.StringIO()
        before = sys.argv
        sys.argv = ["sandbox.py", *argv]
        try:
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
                self.sandbox.main()
        finally:
            sys.argv = before
        return caught.exception.code or 0, out.getvalue()

    def test_run_saves_full_output_and_returns_a_digest(self):
        command = "seq 1 100 && echo 'Error: broke here' && seq 101 200"
        code, shown = self.run_cli("run", "--command", command)
        self.assertEqual(code, 0)
        saved = list(constants.OUTPUT_DIR.glob("run-*.txt"))
        self.assertEqual(len(saved), 1)
        self.assertIn("101", saved[0].read_text(), "full output is on disk")
        self.assertIn("exit 0", shown)
        self.assertIn("[kiasi trimmed", shown)
        self.assertIn("Error: broke here", shown, "error lines from the trimmed middle are kept")
        self.assertNotIn("\n101\n", shown, "the middle stays out of the conversation")

    def test_distill_returns_only_what_the_script_prints(self):
        big = constants.OUTPUT_DIR / "big.txt"
        big.write_text("x\n" * 5000)
        code, shown = self.run_cli("distill", "--code", "import sys; print(sum(1 for _ in open(sys.argv[1])))", "--file", str(big))
        self.assertEqual(code, 0)
        self.assertEqual(shown.strip(), "5000")

    def test_distill_overflow_is_saved_and_named(self):
        code, shown = self.run_cli("distill", "--code", "print('y' * 10000)")
        self.assertEqual(code, 0)
        self.assertIn("[kiasi kept the first", shown)
        saved = list(constants.OUTPUT_DIR.glob("distill-*.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(saved[0].read_text().strip()), 10000)

    def test_distill_failure_reports_exit_and_stderr(self):
        code, shown = self.run_cli("distill", "--code", "raise SystemExit('boom')")
        self.assertEqual(code, 1)
        self.assertIn("script failed (exit 1)", shown)
        self.assertIn("boom", shown)

    def test_run_logs_its_saving_as_a_cap_event(self):
        self.run_cli("run", "--command", "seq 1 500")
        events = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        caps = [e for e in events if e.get("event") == "cap" and e.get("kind") == "sandbox"]
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["tool_name"], "mcp__kiasi__run")
        self.assertGreater(caps[0]["chars"], caps[0]["shown_chars"])

    def test_distill_counts_the_avoided_read(self):
        big = constants.OUTPUT_DIR / "big.txt"
        big.write_text("x" * 50_000)
        self.run_cli("distill", "--code", "print('ok')", "--file", str(big))
        events = [json.loads(line) for line in constants.EVENT_LOG.read_text().splitlines()]
        caps = [e for e in events if e.get("event") == "cap" and e.get("kind") == "sandbox"]
        self.assertEqual(caps[0]["chars"], 50_000, "the saving is the Read the script replaced")
        self.assertLess(caps[0]["shown_chars"], 10)

    def test_sandbox_files_match_the_cleanup_pattern(self):
        import re as re_module
        pattern = re_module.compile(constants.CLEANUP_PATTERNS["outputs"])
        self.assertTrue(pattern.fullmatch("run-20261001-120000-123.txt"))
        self.assertTrue(pattern.fullmatch("distill-20261001-120000-123.txt"))
        self.assertTrue(pattern.fullmatch("toolu_abc123.txt"))


class TestBashCap(KiasiTestCase):
    def payload(self, text, tool_use_id, stderr=""):
        response = {"stdout": text}
        if stderr:
            response["stderr"] = stderr
        return {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": tool_use_id,
                "tool_name": "Bash", "tool_input": {"command": "./scan.sh"}, "tool_response": response}

    def test_long_plain_output_gets_the_trimmed_digest(self):
        text = "\n".join(f"line {i}" for i in range(2000))
        out = kiasi.handle_tool_output(self.payload(text, "toolu_bash1"))
        shown = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("[kiasi trimmed", shown)
        self.assertIn("line 1999", shown, "the tail survives")
        self.assertTrue((constants.OUTPUT_DIR / "toolu_bash1.txt").exists())

    def test_failed_output_keeps_failure_lines(self):
        text = "\n".join(["ok step passed"] * 900 + ["FAIL: test_x", "AssertionError: 1 != 2"] + ["ok step passed"] * 900)
        out = kiasi.handle_tool_output(self.payload(text, "toolu_bash2", stderr="exit 1"))
        shown = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("AssertionError", shown)

    def test_single_line_monster_falls_back_to_head_cut(self):
        out = kiasi.handle_tool_output(self.payload("x" * 15_000, "toolu_bash3"))
        self.assertIn("[kiasi kept the first", out["hookSpecificOutput"]["updatedToolOutput"])

    def test_output_under_the_cap_passes(self):
        self.assertIsNone(kiasi.handle_tool_output(self.payload("y" * 11_000, "toolu_bash4")))


class TestMcpCap(KiasiTestCase):
    def test_big_mcp_output_is_capped_and_saved(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_mcp1",
                   "tool_name": "mcp__playwright__browser_snapshot", "tool_input": {},
                   "tool_response": {"content": "snap " * 4000}}
        result = kiasi.handle_tool_output(payload)
        shown = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("[kiasi kept the first 6000", shown)
        self.assertTrue((constants.OUTPUT_DIR / "toolu_mcp1.txt").exists())

    def test_mcp_list_of_text_blocks_is_capped(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_lt",
                   "tool_name": "mcp__playwright__browser_snapshot", "tool_input": {},
                   "tool_response": [{"type": "text", "text": "A" * 9000}]}
        result = kiasi.handle_tool_output(payload)
        self.assertIn("[kiasi kept", result["hookSpecificOutput"]["updatedToolOutput"])

    def test_mcp_image_blocks_pass_untouched(self):
        payload = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_img",
                   "tool_name": "mcp__chrome__computer", "tool_input": {},
                   "tool_response": [{"type": "image", "data": "B" * 20_000}]}
        self.assertIsNone(kiasi.handle_tool_output(payload))

    def test_kiasi_own_tools_and_small_outputs_pass(self):
        own = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_own",
               "tool_name": "mcp__kiasi__search", "tool_input": {}, "tool_response": {"content": "hit " * 4000}}
        small = {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "toolu_small",
                 "tool_name": "mcp__github__list_issues", "tool_input": {}, "tool_response": {"content": "ok"}}
        self.assertIsNone(kiasi.handle_tool_output(own))
        self.assertIsNone(kiasi.handle_tool_output(small))


class TestReadNudge(KiasiTestCase):
    def setUp(self):
        super().setUp()
        # project files must live outside DATA_DIR, which the nudge exempts
        self.proj = Path(tempfile.mkdtemp(prefix="kiasi-proj-"))

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)
        super().tearDown()

    def payload(self, path, **tool_input):
        return {"hook_event_name": "PreToolUse", "session_id": "s1", "transcript_path": "/tmp/t.jsonl",
                "tool_name": "Read", "tool_input": {"file_path": str(path), **tool_input}}

    def test_whole_read_of_a_big_file_is_denied_once(self):
        big = self.proj / "big.py"
        big.write_text("x = 1\n" * 10_000)
        first = kiasi.handle_read_check(self.payload(big))
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mcp__kiasi__distill", first["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(kiasi.handle_read_check(self.payload(big)), "the repeat goes through")

    def test_sectioned_reads_small_files_and_images_pass(self):
        big = self.proj / "big.py"
        big.write_text("x = 1\n" * 10_000)
        image = self.proj / "shot.png"
        image.write_bytes(b"p" * 40_000)
        small = self.proj / "small.py"
        small.write_text("x = 1\n")
        self.assertIsNone(kiasi.handle_read_check(self.payload(big, offset=10, limit=50)))
        self.assertIsNone(kiasi.handle_read_check(self.payload(image)))
        self.assertIsNone(kiasi.handle_read_check(self.payload(small)))

    def test_nudge_exempts_kiasi_data_dir(self):
        saved = constants.OUTPUT_DIR / "toolu_big.txt"
        saved.write_text("x" * 40_000)
        self.assertIsNone(kiasi.handle_read_check(self.payload(saved)),
                          "reads of kiasi's own saved files are never denied")


class TestLimitsForecast(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import usage_limits
        self.usage_limits = usage_limits

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
        item = self.usage_limits.get_limits()["limits"][0]
        self.assertLess(item["run_out_at"], resets, "40 points in a day burns out in ~3 days")
        self.assertEqual(item["severity"], "warning")
        self.assertGreater(item["burn_per_day"], 15)

    def test_short_or_flat_history_stays_quiet(self):
        now = int(time.time())
        resets = now + 5 * 86400
        self.write_reading(40, resets)
        self.write_history([{"ts": now - 600, "used": 39, "resets_at": resets},
                            {"ts": now - 60, "used": 40, "resets_at": resets}])
        item = self.usage_limits.get_limits()["limits"][0]
        self.assertNotIn("run_out_at", item, "under four hours of history is noise")


class TestProjectOverrides(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.before = {name: getattr(constants, name) for name in constants.PROJECT_KEYS.values()}

    def tearDown(self):
        for name, value in self.before.items():
            setattr(constants, name, value)
        super().tearDown()

    def test_kiasi_json_tunes_caps_and_budgets(self):
        (self.tmp / constants.PROJECT_FILE_NAME).write_text(json.dumps(
            {"output_cap_chars": 20_000, "turn_call_budget": 80, "mystery_knob": 7, "mcp_cap_chars": -1}))
        applied = constants.apply_project(self.tmp)
        self.assertEqual(constants.CAP_OUTSIDE_READ_CHARS, 20_000)
        self.assertEqual(constants.TURN_STOP_STEPS, 80)
        self.assertEqual(constants.CAP_MCP_CHARS, self.before["CAP_MCP_CHARS"], "non-positive values are ignored")
        self.assertEqual(set(applied), {"CAP_OUTSIDE_READ_CHARS", "TURN_STOP_STEPS"})

    def test_missing_or_broken_file_changes_nothing(self):
        self.assertEqual(constants.apply_project(self.tmp), {})
        (self.tmp / constants.PROJECT_FILE_NAME).write_text("{not json")
        self.assertEqual(constants.apply_project(self.tmp), {})
        self.assertEqual(constants.TURN_STOP_STEPS, self.before["TURN_STOP_STEPS"])


class TestBenchmark(KiasiTestCase):
    def test_every_scenario_cuts_at_least_half(self):
        import benchmark
        rows = benchmark.scenarios(benchmark.point_at_tmp())
        self.assertEqual(len(rows), 6)
        for name, _rule, chars_in, chars_shown in rows:
            self.assertLess(chars_shown, chars_in / 2, f"{name} should cut at least half")


class TestPostmortem(KiasiTestCase):
    def setUp(self):
        super().setUp()
        import lens
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


class TestCleanup(KiasiTestCase):
    SID = "aaaaaaaa-1111-2222-3333-444444444444"
    LIVE = "bbbbbbbb-1111-2222-3333-444444444444"

    def setUp(self):
        super().setUp()
        import cleanup
        self.cleanup = cleanup
        constants.CLEANUP_STATE = self.tmp / "cleanup.json"
        constants.CLEANUP_MANIFEST = self.tmp / "cleanup.jsonl"
        constants.CLEANUP_LOCK = self.tmp / "cleanup.lock"
        constants.TRASH_DIR = self.tmp / "trash"
        constants.SEARCH_DB = self.tmp / "search.db"
        constants.TRANSCRIPT_ROOT = self.tmp / "projects"
        constants.CLEANUP_MODE = "auto"
        self.old = time.time() - 30 * 86400

    def put(self, path, age=None, text="x" * 100):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if age:
            os.utime(path, (age, age))
        return path

    def test_only_idle_sessions_and_own_names_are_candidates(self):
        idle = self.put(constants.OUTPUT_DIR / f"compact-{self.SID[:8]}-20260901-101010.txt", self.old)
        live = self.put(constants.OUTPUT_DIR / f"compact-{self.LIVE[:8]}-20260901-101010.txt", self.old)
        stranger = self.put(constants.OUTPUT_DIR / "notes-by-hand.txt", self.old)
        current = self.put(constants.CHECKPOINT_DIR / f"{self.SID[:8]}-3.md", self.old)
        self.put(constants.TRANSCRIPT_ROOT / "p" / f"{self.SID}.jsonl", self.old)
        self.put(constants.TRANSCRIPT_ROOT / "p" / f"{self.LIVE}.jsonl")  # touched now
        found = {row[1] for row in self.cleanup.candidates(time.time(), "")}
        self.assertIn(idle, found)
        self.assertIn(current, found)
        self.assertNotIn(live, found, "a session active today keeps its files")
        self.assertNotIn(stranger, found, "files Kiasi did not name are never touched")
        found = {row[1] for row in self.cleanup.candidates(time.time(), self.SID)}
        self.assertNotIn(idle, found, "the running session keeps its files")

    def test_symlinks_are_skipped(self):
        target = self.put(self.tmp / "elsewhere.txt", self.old)
        link = constants.OUTPUT_DIR / "toolu_link.txt"
        link.symlink_to(target)
        self.assertNotIn(link, {row[1] for row in self.cleanup.candidates(time.time(), "")})

    def test_auto_mode_reports_first_then_trashes_and_restores(self):
        out = self.put(constants.OUTPUT_DIR / "toolu_old.txt", self.old)
        result, _ = self.cleanup.run("", False)
        self.assertEqual(result["mode"], "report")
        self.assertTrue(out.exists(), "report mode moves nothing")
        self.assertEqual(result["pending"][0]["path"], str(out))
        constants.CLEANUP_STATE.write_text(json.dumps({"first_run": "2026-01-01T00:00:00"}))
        result, _ = self.cleanup.run("", False)
        self.assertEqual((result["mode"], result["moved"]), ("trash", 1))
        self.assertFalse(out.exists())
        packed = constants.TRASH_DIR / "outputs" / "toolu_old.txt.gz"
        self.assertTrue(packed.exists(), "trash keeps files gzipped")
        self.assertEqual(self.cleanup.restore({"toolu_old.txt"}), 1)
        self.assertEqual(out.read_text(), "x" * 100, "restore unpacks the original text")
        self.assertFalse(packed.exists())
        actions = [json.loads(l)["action"] for l in constants.CLEANUP_MANIFEST.read_text().splitlines()]
        self.assertEqual(actions, ["trash", "restore"])

    def test_trash_is_emptied_after_its_days(self):
        constants.CLEANUP_MODE = "trash"
        stale = self.put(constants.TRASH_DIR / "outputs" / "toolu_gone.txt", self.old)
        fresh = self.put(constants.TRASH_DIR / "outputs" / "toolu_new.txt")
        result, _ = self.cleanup.run("", False)
        self.assertEqual(result["purged"], 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_old_search_index_is_removed_once(self):
        self.put(constants.SEARCH_DB)
        self.cleanup.run("", False)
        self.assertFalse(constants.SEARCH_DB.exists())
        self.cleanup.run("", True)
        actions = [json.loads(l)["reason"] for l in constants.CLEANUP_MANIFEST.read_text().splitlines()]
        self.assertEqual(actions, ["search index no longer used"])

    def test_notes_go_only_when_newest_entry_is_old(self):
        old_note = json.dumps({"ts": "2026-01-01T00:00:00", "task": "t"}) + "\n"
        stale = self.put(constants.NOTES_DIR / "proj-a.jsonl", self.old - 60 * 86400, old_note)
        fresh = self.put(constants.NOTES_DIR / "proj-b.jsonl", self.old - 60 * 86400, old_note + json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
        found = {row[1] for row in self.cleanup.candidates(time.time(), "")}
        self.assertIn(stale, found)
        self.assertNotIn(fresh, found)

    def test_schedule_dates_each_file_by_its_last_use(self):
        used = time.time() - 2 * 86400
        out = self.put(constants.OUTPUT_DIR / f"compact-{self.SID[:8]}-20260901-101010.txt", used)
        rows = {row[1]: row for row in self.cleanup.schedule("")}
        self.assertAlmostEqual(rows[out][4], used + constants.CLEANUP_IDLE_DAYS * 86400, delta=2)
        self.assertNotIn(out, {row[1] for row in self.cleanup.schedule(self.SID)}, "the running session is left out")

    def test_storage_growth_folders_and_read_back(self):
        import lens
        now = time.time()
        read = self.put(constants.OUTPUT_DIR / "toolu_read.txt", now - 86400)
        self.put(constants.OUTPUT_DIR / "toolu_never.txt", now - 86400, "y" * 300)
        self.put(constants.SESSION_DIR / f"{self.SID}.json", now - 86400)
        managed = [(n, p, p.stat()) for n, (f, _) in self.cleanup.folders().items() for p, _ in self.cleanup.own_files(n, f)]
        growth = lens.storage_growth(managed, now)
        self.assertEqual(len(growth), constants.STORAGE_DAYS)
        self.assertEqual(growth[-2]["added"], 100 + 300 + 100)
        self.assertEqual(growth[-1]["total"], 500)
        cleaned, moves = lens.storage_folders(self.cleanup, [(now, json.dumps({"file_path": str(read)}))], now, now + 30 * 86400)
        self.assertEqual((cleaned["outputs"]["read"], cleaned["outputs"]["unread"], cleaned["outputs"]["unread_bytes"]), (1, 1, 300))
        self.assertEqual(cleaned["outputs"]["next_due"], lens.local_day(now - 86400 + constants.CLEANUP_IDLE_DAYS * 86400))
        self.assertTrue(cleaned["sessions"]["auto"])
        self.assertEqual((moves["count"], moves["bytes"]), (3, 500), "everything due before report-only ends moves then")

    def test_off_mode_does_nothing(self):
        constants.CLEANUP_MODE = "off"
        self.put(constants.OUTPUT_DIR / "toolu_old.txt", self.old)
        self.assertEqual(self.cleanup.resolve_mode({}, time.time()), "off")
        result, _ = self.cleanup.run("", False)
        self.assertEqual(result["moved"], 0)


class TestLimits(KiasiTestCase):
    MINE = {"type": "command", "command": "bash ~/my-line.sh", "padding": 1}

    def setUp(self):
        super().setUp()
        import limits
        self.limits = limits
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


class TestDashboardOrigin(unittest.TestCase):
    """A live dashboard on a free port; the sync itself is stubbed so no subprocess runs."""

    def setUp(self):
        import http.client
        import threading
        import dashboard
        self.http = http.client
        self.dashboard = dashboard
        self.syncs = []
        self._real_sync = dashboard.run_sync_background
        dashboard.run_sync_background = lambda: self.syncs.append(1) or True
        self.server = dashboard.ThreadingServer(("127.0.0.1", 0), dashboard.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dashboard.run_sync_background = self._real_sync

    def request(self, method, path, headers):
        conn = self.http.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest(method, path, skip_host=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders()
        status = conn.getresponse().status
        conn.close()
        return status

    def own(self, host="127.0.0.1"):
        return f"{host}:{self.port}"

    def test_sync_from_own_page_runs(self):
        for headers in ({"Origin": f"http://{self.own()}"}, {"Referer": f"http://{self.own()}/#rules"},
                        {"Origin": f"http://{self.own('localhost')}"}):
            host = headers.get("Origin", headers.get("Referer", ""))[7:].split("/")[0]
            self.assertEqual(self.request("POST", "/sync", {"Host": host, **headers}), 200, headers)
        self.assertEqual(len(self.syncs), 3)

    def test_sync_from_other_origin_or_none_is_refused(self):
        for headers in ({}, {"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Origin": f"http://{self.own()}.evil.example"}, {"Referer": "http://evil.example/"},
                        {"Referer": f"http://{self.own()}.evil.example/"}):
            self.assertEqual(self.request("POST", "/sync", {"Host": self.own(), **headers}), 403, headers)
        self.assertEqual(self.syncs, [])

    def test_foreign_host_is_refused_even_for_reads(self):
        """DNS rebinding: a page on evil.example resolved to 127.0.0.1 sends Host: evil.example."""
        rebound = f"evil.example:{self.port}"
        self.assertEqual(self.request("GET", "/", {"Host": rebound}), 403)
        self.assertEqual(self.request("POST", "/sync", {"Host": rebound, "Origin": f"http://{rebound}"}), 403)
        self.assertEqual(self.request("GET", "/", {}), 403, "no Host header at all")
        self.assertEqual(self.request("GET", "/", {"Host": self.own()}), 200)
        self.assertEqual(self.syncs, [])


class ReportTestCase(KiasiTestCase):
    """Synthetic transcripts under a temp TRANSCRIPT_ROOT; timestamps sit at 06:00 UTC
    yesterday so the UTC day (budget.py) and the local day (lens.py) agree in any zone
    from UTC-6 to UTC+14."""

    def setUp(self):
        super().setUp()
        import budget
        import lens
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

    def test_compaction_is_a_drop_to_under_half_from_over_150k(self):
        self.write(self.project / "s1.jsonl", [
            self.prompt(0), self.step("r1", 1, 160_000), self.step("r2", 2, 170_000),
            self.step("r3", 3, 30_000), self.step("r4", 4, 100_000)])
        [session] = self.budget.build(7)["sessions"]
        self.assertEqual(session["compactions"], 1)


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

    def test_since_install_without_a_log_or_report(self):
        self.assertEqual(self.lens.since_install(), {"install_day": None, "before": None, "after": None})
