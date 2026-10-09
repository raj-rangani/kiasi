import json

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import events  # noqa: E402
from core import session  # noqa: E402


class TestLegacyMigration(KiasiTestCase):
    def test_moves_old_data_dir_and_links_it(self):
        old = self.tmp / "plugins" / "ankush-inline"
        new = self.tmp / "plugins" / "kiasi-inline"
        (old / "outputs").mkdir(parents=True)
        (old / "outputs" / "saved.txt").write_text("full text")
        new.mkdir()
        self.assertTrue(session.migrate_legacy_dir(old, new))
        self.assertTrue(old.is_symlink())
        self.assertEqual((old / "outputs" / "saved.txt").read_text(), "full text")

    def test_merges_old_event_log_before_new_events(self):
        (self.tmp / "ankush.jsonl").write_text('{"event": "old"}')
        constants.EVENT_LOG.write_text('{"event": "new"}\n')
        self.assertTrue(session.merge_legacy_log(self.tmp))
        self.assertEqual(constants.EVENT_LOG.read_text(), '{"event": "old"}\n{"event": "new"}\n')
        self.assertFalse((self.tmp / "ankush.jsonl").exists())
        self.assertFalse(session.merge_legacy_log(self.tmp))

    def test_leaves_non_empty_new_dir_alone(self):
        old = self.tmp / "old"
        new = self.tmp / "new"
        old.mkdir()
        new.mkdir()
        (new / "kiasi.jsonl").write_text("{}\n")
        self.assertFalse(session.migrate_legacy_dir(old, new))
        self.assertTrue(old.is_dir() and not old.is_symlink())


class TestSaveSession(KiasiTestCase):
    def test_replace_is_retried_after_permission_errors(self):
        import os
        from unittest import mock
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError("held open")
            return real(src, dst)
        with mock.patch.object(events.os, "replace", flaky), mock.patch.object(events.time, "sleep"):
            events.save_session("sess-retry", {"prompts": 7})
        self.assertEqual(len(calls), 3)
        self.assertEqual(events.load_session("sess-retry")["prompts"], 7)

    def test_a_crashed_writes_old_temp_file_is_removed(self):
        import os
        path = events.session_path("sess-tmp")
        old = path.with_name(path.name + ".999.tmp")
        old.write_text("{")
        os.utime(old, (1, 1))
        events.save_session("sess-tmp", {"prompts": 1})
        self.assertFalse(old.exists())


class TestChecklistFolder(KiasiTestCase):
    def test_temp_folder_is_per_user_and_private(self):
        import stat
        from unittest import mock
        with mock.patch.object(constants.getpass, "getuser", return_value="dana"):
            self.assertEqual(constants._user_token(), "dana")
        with mock.patch.object(constants.getpass, "getuser", side_effect=KeyError):
            self.assertTrue(constants._user_token())
        constants.TEMP_CHECKLIST_DIR = self.tmp / "kiasi-u" / "checkpoints"
        events.make_checklist_folder(constants.TEMP_CHECKLIST_DIR / "x.md")
        if hasattr(__import__("os"), "getuid"):
            self.assertEqual(stat.S_IMODE(constants.TEMP_CHECKLIST_DIR.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(constants.TEMP_CHECKLIST_DIR.parent.stat().st_mode), 0o700)


class TestSessionStart(KiasiTestCase):
    def test_env_reminder_names_the_one_command_that_fixes_it(self):
        import os
        from unittest import mock
        from core import session
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000"}, clear=False):
            os.environ.pop("CLAUDE_CODE_ENABLE_FUNCTION_HOOKS", None)
            text = session.env_warning()
        self.assertIn("/kiasi:limits setup", text)
        self.assertIn("CLAUDE_CODE_ENABLE_FUNCTION_HOOKS", text)
        self.assertNotIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW not", text, "only the missing setting is named")
        with mock.patch.dict(os.environ, constants.REQUIRED_ENV):
            self.assertEqual(session.env_warning(), "")

    def test_injects_rules(self):
        payload = {"session_id": "sess-start", "cwd": str(self.tmp), "source": "startup"}
        result = session.handle_session_start(payload)
        self.assertIsNotNone(result)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Context rules", text)
        self.assertIn("Load /kiasi:rules for the full rules.", text)
        self.assertLessEqual(text.count("\n"), 12 + 3)


class TestPluginCompactSession(KiasiTestCase):
    def test_logs_session_id_and_lens_keeps_it(self):
        import json
        from reports import lens
        session.handle_plugin_compact({"hook_event_name": "PluginCompact", "session_id": "abcdef12-3456", "trigger": "auto", "mode": "prune",
                                     "level": 1, "messages": 40, "tokens_before": 70000, "tokens_after": 30000})
        record = json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])
        self.assertEqual(record["session_id"], "abcdef12-3456")
        events = [(lens.epoch_local(record["ts"]), record)]
        self.assertEqual(lens.compaction_rows(events)[0]["session"], "abcdef12")

    def test_join_skips_a_compaction_from_another_session(self):
        from reports import lens
        ts = "2026-09-29T12:00:00"
        t = lens.epoch_local(ts)
        compact = {"ts": ts, "event": "compact", "session_id": "aaaaaaaa-1", "trigger": "auto", "context_tokens": 160000}
        plugin = {"ts": ts, "event": "plugin_compact", "session_id": "bbbbbbbb-2", "mode": "prune", "level": 0, "tokens_before": 70000, "tokens_after": 20000}
        rows = lens.compaction_rows([(t, compact), (t, plugin)])
        self.assertEqual(sorted(r["session"] for r in rows), ["aaaaaaaa", "bbbbbbbb"])
        self.assertEqual(next(r for r in rows if r["session"] == "aaaaaaaa")["mode"], "")


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

    def test_checklists_in_the_project_are_listed_after_compaction(self):
        path = self.transcript()
        checklist = self.tmp / ".kiasi" / "checkpoints" / "abcdef12-7.md"
        checklist.parent.mkdir(parents=True)
        checklist.write_text("- [ ] ship the exporter")
        payload = {"session_id": "abcdef12-0000", "cwd": str(self.tmp), "transcript_path": str(path), "source": "compact"}
        self.assertIn(str(checklist), session.handle_session_start(payload)["hookSpecificOutput"]["additionalContext"])

    def test_the_latest_task_file_is_restored_on_start_and_a_note_is_the_fallback(self):
        from core import taskfile
        payload = {"session_id": "sess-new", "cwd": str(self.tmp), "source": "startup"}
        session.write_note("sess-old", str(self.tmp), "refactor the cart", ["/r/cart.py"], "stopped here", {}, force=True)
        path = taskfile.latest(str(self.tmp))
        self.assertIn("refactor the cart", path.read_text(), "the note writer feeds the task file")
        text = session.handle_session_start(payload)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(path), text)
        self.assertIn("Re-verify before marking anything done.", text)
        self.assertNotIn("Last session note", text)
        path.unlink()
        text = session.handle_session_start(payload)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Last session note for this project", text)
        taskfile.write(str(self.tmp), "sess-old", goal="again")
        for source in ("clear", "resume"):
            self.assertIn("Re-verify", session.handle_session_start({**payload, "source": source})["hookSpecificOutput"]["additionalContext"])

    def test_injected_only_after_compaction(self):
        path = self.transcript()
        events.log_event({"event": "cap", "session_id": "abcdef12-0000", "saved_path": "/data/outputs/t9.txt"})
        (constants.CHECKPOINT_DIR / "abcdef12-4.md").write_text("- [ ] finish")
        payload = {"session_id": "abcdef12-0000", "cwd": str(self.tmp), "transcript_path": str(path)}
        text = session.handle_session_start({**payload, "source": "compact"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Task: add the discount validation", text)
        self.assertIn("Files edited: /repo/order.ts", text)
        self.assertIn("Still failing at the last attempt: Bash pnpm test", text)
        self.assertNotIn("pnpm lint", text)
        self.assertIn("/data/outputs/t9.txt", text)
        self.assertIn("abcdef12-4.md", text)
        startup = session.handle_session_start({**payload, "source": "startup"})["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("kiasi state after compaction", startup)


class TestStatuslineRefresh(KiasiTestCase):
    def test_installed_copy_is_refreshed_only_when_set_up_and_stale(self):
        installed = constants.HOME_DIR / constants.STATUSLINE_SCRIPT_NAME
        source = constants.PLUGIN_ROOT / "scripts" / constants.STATUSLINE_SCRIPT_NAME
        constants.HOME_DIR.mkdir(parents=True, exist_ok=True)
        installed.write_text("old copy")
        self.assertFalse(session.refresh_statusline("s"), "without the chain file the status line was never set up; nothing is touched")
        self.assertEqual(installed.read_text(), "old copy")
        (constants.HOME_DIR / constants.STATUSLINE_CHAIN_NAME).write_text("{}")
        self.assertTrue(session.refresh_statusline("s"))
        self.assertEqual(installed.read_bytes(), source.read_bytes())
        self.assertEqual(json.loads(constants.EVENT_LOG.read_text().splitlines()[-1])["event"], "statusline_refreshed")
        self.assertFalse(session.refresh_statusline("s"), "an up-to-date copy is left alone")
        installed.unlink()
        self.assertFalse(session.refresh_statusline("s"), "a removed copy is not reinstalled behind the user's back")
