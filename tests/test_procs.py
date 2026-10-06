import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import SCRIPTS  # noqa: F401  puts scripts/ on the path
from core import procs  # noqa: E402


def running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # killed but not yet reaped still answers the signal
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class TestRunTree(unittest.TestCase):
    def test_returns_the_exit_code(self):
        self.assertEqual(procs.run_tree([sys.executable, "-c", "raise SystemExit(3)"], timeout=30), 3)

    @unittest.skipIf(sys.platform == "win32", "checks the child by signal")
    def test_the_timeout_stops_what_the_command_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            command = ("import subprocess, sys, time; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                       f"open({str(pid_file)!r}, 'w').write(str(child.pid)); time.sleep(60)")
            with self.assertRaises(subprocess.TimeoutExpired):
                procs.run_tree([sys.executable, "-c", command], timeout=2)
            child = int(pid_file.read_text())
            deadline = time.time() + 5
            while running(child) and time.time() < deadline:
                time.sleep(0.05)
            self.assertFalse(running(child), "subprocess.run's timeout left this process running")


if __name__ == "__main__":
    unittest.main()
