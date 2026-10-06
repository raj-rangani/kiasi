import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import KiasiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import autostart  # noqa: E402
from core import constants, procs  # noqa: E402


class AutostartTests(KiasiTestCase):
    def test_launcher_runs_dashboard_from_recorded_plugin_root(self):
        autostart.write_launcher()
        self.assertTrue(constants.DASHBOARD_LAUNCHER.is_file())
        self.assertEqual(constants.PLUGIN_ROOT_POINTER.read_text().strip(), str(constants.PLUGIN_ROOT))
        result = subprocess.run([sys.executable, str(constants.DASHBOARD_LAUNCHER), "--help"], capture_output=True, text=True, timeout=30,
                                env={**os.environ, "CLAUDE_PLUGIN_DATA": str(constants.DATA_DIR)})
        self.assertIn("dashboard.py", result.stdout + result.stderr)

    def test_launcher_reports_missing_plugin(self):
        autostart.write_launcher()
        constants.PLUGIN_ROOT_POINTER.write_text("/nowhere/kiasi\n")
        result = subprocess.run([sys.executable, str(constants.DASHBOARD_LAUNCHER)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("plugin not found", result.stderr)

    def test_entry_texts_name_the_launcher(self):
        for text in (autostart.systemd_text(), autostart.xdg_text(), autostart.startup_text()):
            self.assertIn(str(constants.DASHBOARD_LAUNCHER), text)
            self.assertIn(str(constants.DASHBOARD_PORT), text)
        self.assertIn("WantedBy=default.target", autostart.systemd_text())
        self.assertIn("Terminal=false", autostart.xdg_text())
        self.assertIn('shell.Run', autostart.startup_text())
        with mock.patch.object(autostart.sys, "platform", "darwin"):
            self.assertIn("RunAtLoad", autostart.launch_agent_text())

    def test_entry_picks_mechanism_per_platform(self):
        with mock.patch.object(autostart.sys, "platform", "win32"), mock.patch.dict(os.environ, {"APPDATA": str(constants.HOME_DIR / "appdata")}):
            path, _text, kind = autostart.entry()
            self.assertEqual(kind, "Startup folder")
            self.assertTrue(str(path).endswith("kiasi-dashboard.vbs"))
        with mock.patch.object(autostart.sys, "platform", "linux"), mock.patch.object(autostart, "user_systemd_available", return_value=False):
            _path, _text, kind = autostart.entry()
            self.assertEqual(kind, "XDG autostart")

    def test_status_without_entry_and_unknown_mode(self):
        with mock.patch.object(autostart, "entry", return_value=(constants.HOME_DIR / "none.service", "", "systemd user service")):
            self.assertIn("autostart is off", autostart.status())
        self.assertEqual(autostart.main(["autostart.py", "bogus"]), 2)

    def test_detached_kwargs_per_platform(self):
        with mock.patch.object(procs.sys, "platform", "linux"):
            self.assertTrue(procs.detached_kwargs()["start_new_session"])
        with mock.patch.object(procs.sys, "platform", "win32"), mock.patch.object(procs.subprocess, "DETACHED_PROCESS", 8, create=True), \
             mock.patch.object(procs.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
             mock.patch.object(procs.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
            kwargs = procs.detached_kwargs()
            self.assertNotIn("start_new_session", kwargs)
            self.assertEqual(kwargs["creationflags"], 512 | 0x08000000,
                             "with no console at all (DETACHED_PROCESS) every console program the child starts opens a window")


if __name__ == "__main__":
    unittest.main()
