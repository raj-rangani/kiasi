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


class TestSessionStart(KiasiTestCase):
    def test_injects_rules(self):
        payload = {"session_id": "sess-start", "cwd": str(self.tmp), "source": "startup"}
        result = session.handle_session_start(payload)
        self.assertIsNotNone(result)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Context rules", text)
        self.assertIn("Compact instructions", text)


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
