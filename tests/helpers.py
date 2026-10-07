"""Tests for the kiasi hook handlers, run with:
    python3 -m unittest discover -s tests
Each test points constants.DATA_DIR (and the derived *_DIR constants) at a
fresh temp directory so nothing touches a real data dir."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from core import constants  # noqa: E402
from core.events import ensure_dirs  # noqa: E402


class KiasiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kiasi-test-"))
        self._patch_data_dir(self.tmp)
        ensure_dirs()

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
        constants.TEMP_CHECKLIST_DIR = data_dir / "tmp-checkpoints"
        constants.BUDGET_FILE = data_dir / "budget.json"
        constants.HISTORY_FILE = data_dir / "history.json"
        constants.SAVINGS_FILE = data_dir / "savings.json"
        constants.LENS_FILE = data_dir / "lens.json.gz"
        constants.HOME_DIR = data_dir / "home"
        constants.DATA_DIR_POINTER = constants.HOME_DIR / "data-dir"
        constants.PLUGIN_ROOT_POINTER = constants.HOME_DIR / "plugin-root"
        constants.DASHBOARD_LAUNCHER = constants.HOME_DIR / "dashboard-launcher.py"
        constants.LEGACY_HOME_DIR = data_dir / "legacy-home"
        constants.DASHBOARD_STATE = data_dir / "dashboard.json"
        constants.DASHBOARD_LOG = data_dir / "dashboard.log"
        constants.DASHBOARD_AUTOSTART = False  # tests never start a real server from a hook
        constants.PAUSE_NOTIFICATION = "off"  # nor raise a real desktop notification
        constants.PAUSE_QUESTION = "off"  # the pause question is asked only in the tests about it
