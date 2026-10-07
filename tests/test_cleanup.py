import json
import os
import time

from helpers import KiasiTestCase
from core import constants  # noqa: E402


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
        numbered = self.put(constants.CHECKPOINT_DIR / f"{self.SID[:8]}-3-2.md", self.old)
        lock = self.put(constants.SESSION_DIR / f"{self.SID}.lock", self.old, text="")
        self.put(constants.TRANSCRIPT_ROOT / "p" / f"{self.SID}.jsonl", self.old)
        self.put(constants.TRANSCRIPT_ROOT / "p" / f"{self.LIVE}.jsonl")  # touched now
        found = {row[1] for row in self.cleanup.candidates(time.time(), "")}
        self.assertIn(idle, found)
        self.assertIn(current, found)
        self.assertIn(numbered, found, "a numbered checklist from the same turn is Kiasi's too")
        self.assertIn(lock, found, "a session's lock file goes with its state")
        self.assertNotIn(live, found, "a session active today keeps its files")
        self.assertNotIn(stranger, found, "files Kiasi did not name are never touched")
        found = {row[1] for row in self.cleanup.candidates(time.time(), self.SID)}
        self.assertNotIn(idle, found, "the running session keeps its files")

    def test_a_live_pause_keeps_its_session_state_and_checklist(self):
        state = self.put(constants.SESSION_DIR / f"{self.SID}.json", self.old)
        checklist = self.put(constants.CHECKPOINT_DIR / f"{self.SID[:8]}-3.md", self.old)
        record = {"session_id": self.SID, "checkpoint": str(checklist), "at": self.cleanup.stamp(time.time() - 10 * 86400)}
        self.put(constants.NOTES_DIR / "proj.paused.json", text=json.dumps(record))
        found = {row[1] for row in self.cleanup.candidates(time.time(), "")}
        self.assertNotIn(state, found, "a resume still needs the state")
        self.assertNotIn(checklist, found)
        self.put(constants.NOTES_DIR / "proj.paused.json", text=json.dumps({**record, "at": self.cleanup.stamp(time.time() - 20 * 86400)}))
        found = {row[1] for row in self.cleanup.candidates(time.time(), "")}
        self.assertIn(state, found, "an expired record keeps nothing")
        self.assertIn(checklist, found)

    def test_unknown_session_outputs_are_cleaned_too(self):
        names = ("compact-unknown-20260901-101010-abc123.txt", "unknown-20260901-101010.txt", "compact-unknown-agent1-20260901-101010-abc123.txt")
        files = [self.put(constants.OUTPUT_DIR / name, self.old) for name in names]
        found = {row[1] for row in self.cleanup.candidates(time.time(), "")}
        self.assertTrue(set(files) <= found)

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
        from reports import lens
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

    def test_project_checklists_named_by_turn_events_are_cleaned_and_restored(self):
        constants.CLEANUP_MODE = "trash"
        folder = self.tmp / "app" / ".kiasi" / "checkpoints"
        idle = self.put(folder / f"{self.SID[:8]}-3.md", self.old)
        stranger = self.put(folder / "plan.md", self.old)
        unnamed = self.put(self.tmp / "other" / ".kiasi" / "checkpoints" / f"{self.SID[:8]}-4.md", self.old)
        constants.EVENT_LOG.write_text(json.dumps({"event": "turn_warn", "session_id": self.SID, "checkpoint": str(idle)}) + "\n")
        self.assertIn(("checkpoints", folder, constants.CLEANUP_IDLE_DAYS), self.cleanup.managed_folders(), "the storage tab counts them too")
        self.assertNotIn(idle, {row[1] for row in self.cleanup.candidates(time.time(), self.SID)}, "the running session keeps its checklists")
        result, _ = self.cleanup.run("", False)
        self.assertEqual(result["moved"], 1)
        self.assertFalse(idle.exists())
        self.assertTrue(stranger.exists(), "files Kiasi did not name are never touched")
        self.assertTrue(unnamed.exists(), "only folders a turn event named are cleaned")
        self.assertEqual(self.cleanup.restore({idle.name}), 1)
        self.assertEqual(idle.read_text(), "x" * 100, "a restore puts the checklist back in its project")

    def test_checklist_paths_outside_a_project_kiasi_folder_or_behind_a_symlink_are_ignored(self):
        elsewhere = self.put(self.tmp / "elsewhere" / "checkpoints" / f"{self.SID[:8]}-3.md", self.old)
        project = self.tmp / "app"
        project.mkdir()
        (project / ".kiasi").symlink_to(self.tmp / "elsewhere")
        linked = project / ".kiasi" / "checkpoints" / elsewhere.name
        loose = self.put(self.tmp / "docs" / f"{self.SID[:8]}-5.md", self.old)
        constants.EVENT_LOG.write_text("".join(json.dumps({"event": "turn_stop", "checkpoint": str(p)}) + "\n" for p in (linked, loose, elsewhere)))
        self.assertEqual(self.cleanup.checklist_folders(), [])
        self.assertFalse({elsewhere, linked, loose} & {row[1] for row in self.cleanup.candidates(time.time(), "")})

    def test_off_mode_does_nothing(self):
        constants.CLEANUP_MODE = "off"
        self.put(constants.OUTPUT_DIR / "toolu_old.txt", self.old)
        self.assertEqual(self.cleanup.resolve_mode({}, time.time()), "off")
        result, _ = self.cleanup.run("", False)
        self.assertEqual(result["moved"], 0)
