import json
import os
import sys
import time
import unittest

from helpers import KiasiTestCase
from core import constants  # noqa: E402
from core import session  # noqa: E402


class TestDashboardAutostart(KiasiTestCase):
    """The probe tells a live Kiasi dashboard from a closed port; ensure() starts one only when none answers."""

    def setUp(self):
        super().setUp()
        import socket
        import threading
        import dashboard
        self.dashboard = dashboard
        self.server = dashboard.ThreadingServer(("127.0.0.1", 0), dashboard.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.closed_port = probe.getsockname()[1]
        probe.close()
        self._real = (constants.DASHBOARD_PORT, constants.DASHBOARD_START_WAIT_SECONDS, dashboard.start_detached, dashboard.ensure)
        constants.DASHBOARD_PORT = self.closed_port
        constants.DASHBOARD_START_WAIT_SECONDS = 0.3
        self.starts = []
        dashboard.start_detached = lambda: self.starts.append(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        constants.DASHBOARD_PORT, constants.DASHBOARD_START_WAIT_SECONDS, self.dashboard.start_detached, self.dashboard.ensure = self._real
        super().tearDown()

    def test_probe_recognises_a_kiasi_dashboard_and_nothing_else(self):
        self.assertTrue(self.dashboard.answers(self.port))
        self.assertFalse(self.dashboard.answers(self.closed_port))

    def test_running_url_prefers_the_recorded_port(self):
        self.assertIsNone(self.dashboard.running_url())
        self.dashboard.write_state(self.port)
        self.assertEqual(self.dashboard.running_url(), f"http://127.0.0.1:{self.port}/")
        state = json.loads(constants.DASHBOARD_STATE.read_text())
        self.assertEqual(state["port"], self.port)
        self.assertEqual(state["pid"], os.getpid())

    def test_ensure_starts_nothing_when_one_answers(self):
        self.dashboard.write_state(self.port)
        self.assertEqual(self.dashboard.ensure(), (f"http://127.0.0.1:{self.port}/", False))
        self.assertEqual(self.starts, [])

    def test_ensure_replaces_a_windows_dashboard_an_older_build_started(self):
        stops = []
        self.addCleanup(setattr, self.dashboard, "stop_running", self.dashboard.stop_running)
        self.addCleanup(setattr, sys, "platform", sys.platform)
        self.dashboard.stop_running = lambda: stops.append(1) or True
        self.dashboard.write_state(self.port)
        url = f"http://127.0.0.1:{self.port}/"
        recorded = json.loads(constants.DASHBOARD_STATE.read_text())
        self.assertEqual(recorded["build"], constants.DASHBOARD_BUILD)
        sys.platform = "win32"
        self.assertEqual(self.dashboard.ensure(), (url, False), "started by this build: left running")
        constants.DASHBOARD_STATE.write_text(json.dumps({key: value for key, value in recorded.items() if key != "build"}))
        sys.platform = "linux"
        self.assertEqual(self.dashboard.ensure(), (url, False), "only Windows opens the windows")
        self.assertEqual((stops, self.starts), ([], []))
        sys.platform = "win32"
        self.assertEqual(self.dashboard.ensure(), (url, True))
        self.assertEqual((stops, self.starts), ([1], [1]))

    def test_ensure_starts_one_and_reports_when_it_does_not_come_up(self):
        self.assertEqual(self.dashboard.ensure(), (None, True))
        self.assertEqual(self.starts, [1])

    def test_ensure_waits_for_the_started_server(self):
        self.dashboard.start_detached = lambda: self.dashboard.write_state(self.port)
        self.assertEqual(self.dashboard.ensure(), (f"http://127.0.0.1:{self.port}/", True))

    def test_stale_reports(self):
        real = constants.LENS_FILE
        constants.LENS_FILE = self.tmp / "lens.json.gz"
        try:
            self.assertTrue(self.dashboard.reports_stale())
            constants.LENS_FILE.write_bytes(b"")
            self.assertFalse(self.dashboard.reports_stale())
            old = time.time() - constants.DASHBOARD_REBUILD_SECONDS - 60
            os.utime(constants.LENS_FILE, (old, old))
            self.assertTrue(self.dashboard.reports_stale())
        finally:
            constants.LENS_FILE = real

    def test_session_start_reports_a_newly_started_dashboard(self):
        constants.DASHBOARD_AUTOSTART = True
        self.dashboard.ensure = lambda: ("http://127.0.0.1:8787/", True)
        text = session.handle_session_start({"session_id": "sess-dash", "cwd": str(self.tmp), "source": "startup"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Kiasi dashboard started: http://127.0.0.1:8787/", text)
        events = [json.loads(l) for l in constants.EVENT_LOG.read_text().splitlines()]
        self.assertTrue(any(e["event"] == "dashboard_start" and e["ok"] for e in events))

    def test_session_start_is_quiet_when_it_was_already_running(self):
        constants.DASHBOARD_AUTOSTART = True
        self.dashboard.ensure = lambda: ("http://127.0.0.1:8787/", False)
        text = session.handle_session_start({"session_id": "sess-dash2", "cwd": str(self.tmp), "source": "startup"})["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("dashboard started", text)


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
