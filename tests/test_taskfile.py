import json
import os
import time

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import taskfile  # noqa: E402


class TestTaskFile(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.cwd = str(self.tmp)

    def test_write_merges_and_keeps_sections_it_was_not_given(self):
        path = taskfile.write(self.cwd, "sess-aaaa1111", goal="Add the exporter", decisions=["use csv"], files=["/r/a.py"], next_step="write the test")
        self.assertEqual(path, taskfile.path_for(self.cwd, "sess-aaaa1111"))
        taskfile.write(self.cwd, "sess-aaaa1111", files=["/r/b.py", "/r/a.py"], checklist=["[x] wire it", "[x] ran the suite, verified", "- [ ] docs"])
        text = path.read_text()
        for heading in ("# Task", "## Goal", "## Decisions", "## Checklist", "## Files touched", "## Next step"):
            self.assertIn(heading, text)
        self.assertIn("sess-aaaa1111", text.splitlines()[2])
        self.assertIn("Add the exporter", text)
        self.assertIn("- use csv", text)
        self.assertIn("write the test", text)
        self.assertEqual(text.count("/r/a.py"), 1)
        self.assertIn("- [ ] wire it", text, "a box is checked only when the item says it was verified")
        self.assertIn("- [x] ran the suite, verified", text)
        self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_latest_and_start_block(self):
        self.assertIsNone(taskfile.latest(self.cwd))
        self.assertEqual(taskfile.start_block(self.cwd), "")
        old = taskfile.write(self.cwd, "sess-old00000", goal="old task")
        os.utime(old, (time.time() - 100, time.time() - 100))
        new = taskfile.write(self.cwd, "sess-new00000", goal="g" * 3000)
        self.assertEqual(taskfile.latest(self.cwd), new)
        block = taskfile.start_block(self.cwd)
        self.assertIn(str(new), block)
        self.assertIn("Continue from here. Re-verify before marking anything done.", block)
        self.assertLess(len(block), constants.TASKFILE_START_CHARS + 400)

    def test_a_resumed_task_reuses_the_paused_tasks_file(self):
        first = taskfile.write(self.cwd, "sess-first0000", goal="the task")
        self.assertEqual(taskfile.adopt(self.cwd, "sess-second000", "sess-first0000"), first)
        self.assertEqual(taskfile.path_for(self.cwd, "sess-second000"), first)
        self.assertEqual(taskfile.write(self.cwd, "sess-second000", next_step="go on"), first)
        self.assertIn("go on", first.read_text())
