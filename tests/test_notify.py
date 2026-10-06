import json
import os
import shutil
import subprocess
import sys

from helpers import SCRIPTS, KiasiTestCase
from core import constants  # noqa: E402
from core import notify  # noqa: E402
from core import turn  # noqa: E402


class TestDesktopCommand(KiasiTestCase):
    def patch(self, target, name, value):
        self.addCleanup(setattr, target, name, getattr(target, name))
        setattr(target, name, value)

    def command(self, platform, installed=True):
        self.patch(sys, "platform", platform)
        self.patch(shutil, "which", lambda name: f"/bin/{name}" if installed else None)
        return notify.desktop_command("Kiasi paused", 'Reply "continue"; $(rm) `x`')

    def test_linux_uses_notify_send(self):
        self.assertEqual(self.command("linux"), ["/bin/notify-send", "-a", "Kiasi", "--", "Kiasi paused", 'Reply "continue"; $(rm) `x`'])

    def test_macos_passes_the_text_as_arguments_not_as_script(self):
        command = self.command("darwin")
        self.assertEqual((command[0], command[-2:]), ("/bin/osascript", ["Kiasi paused", 'Reply "continue"; $(rm) `x`']))
        script = command[1:-2]
        self.assertEqual(script[0::2], ["-e"] * len(notify.MACOS_SCRIPT))
        self.assertEqual(tuple(script[1::2]), notify.MACOS_SCRIPT)
        self.assertNotIn("continue", " ".join(script))

    def test_windows_reads_the_text_from_the_environment(self):
        command = self.command("win32")
        self.assertEqual(command[:4], ["/bin/powershell", "-NoProfile", "-NonInteractive", "-Command"])
        script = command[4]
        self.assertEqual(len(command), 5)
        for part in (f"$env:{constants.NOTIFY_TITLE_ENV}", f"$env:{constants.NOTIFY_BODY_ENV}", notify.WINDOWS_APP_ID, ".Show($toast)"):
            self.assertIn(part, script)
        self.assertNotIn("continue", script)
        self.assertIn("SetAttribute('activationType', 'protocol')", script, "a click must not open a PowerShell window")
        self.assertNotIn('"', script, "a double quote would need escaping on the Windows command line")
        self.assertNotIn("\n", script)

    def test_a_system_without_the_command_gets_none(self):
        for platform in ("linux", "darwin", "win32"):
            self.assertIsNone(self.command(platform, installed=False))


class TestNotifyDesktop(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.started = []
        self.addCleanup(setattr, subprocess, "Popen", subprocess.Popen)
        subprocess.Popen = lambda command, **kwargs: self.started.append((command, kwargs))
        self.addCleanup(setattr, notify, "desktop_command", notify.desktop_command)
        notify.desktop_command = lambda title, body: ["notifier", title, body]
        entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
        self.addCleanup(lambda: os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None) if entrypoint is None else os.environ.__setitem__("CLAUDE_CODE_ENTRYPOINT", entrypoint))

    def mode(self, mode, entrypoint):
        self.addCleanup(setattr, constants, "PAUSE_NOTIFICATION", constants.PAUSE_NOTIFICATION)
        constants.PAUSE_NOTIFICATION = mode
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        return notify.notify_desktop("title", "body")

    def test_auto_notifies_in_vs_code_and_the_desktop_app_only(self):
        sent = {entrypoint: self.mode("auto", entrypoint) for entrypoint in ("claude-vscode", "claude-desktop", "cli", "sdk-cli", "")}
        self.assertEqual(sent, {"claude-vscode": True, "claude-desktop": True, "cli": False, "sdk-cli": False, "": False},
                         "the terminal raises its own notification, and a headless run has nobody to notify")
        self.assertEqual(len(self.started), 2)

    def test_always_and_off_ignore_where_the_session_runs(self):
        self.assertTrue(self.mode("always", "cli"))
        self.assertFalse(self.mode("off", "claude-vscode"))

    def test_the_command_is_started_detached_with_the_text_in_its_environment(self):
        self.assertTrue(self.mode("always", "cli"))
        command, kwargs = self.started[0]
        self.assertEqual(command, ["notifier", "title", "body"])
        self.assertEqual((kwargs["env"][constants.NOTIFY_TITLE_ENV], kwargs["env"][constants.NOTIFY_BODY_ENV]), ("title", "body"))
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL, "a hook's stdout is its answer to Claude Code")
        self.assertTrue(kwargs.get("start_new_session") or kwargs.get("creationflags"), "the hook does not wait for it")

    def test_a_missing_or_failing_command_never_fails_the_hook(self):
        def fail(command, **kwargs):
            raise OSError("no such file")
        subprocess.Popen = fail
        self.assertFalse(self.mode("always", "cli"))
        notify.desktop_command = lambda title, body: None
        self.assertFalse(self.mode("always", "cli"))


class TestPauseNotification(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.addCleanup(setattr, turn, "notify_desktop", turn.notify_desktop)
        turn.notify_desktop = lambda title, body: self.sent.append((title, body)) or True
        for name, value in (("TURN_STOP_STEPS", 6), ("SUBAGENT_STEP_LIMIT", 6), ("TURN_BUDGET_MODE", "pause")):
            self.addCleanup(setattr, constants, name, getattr(constants, name))
            setattr(constants, name, value)
        self.project = self.tmp / "shop-api"
        self.project.mkdir()

    def post(self, session, **extra):
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": session, "cwd": str(self.project),
                "transcript_path": str(self.tmp / f"{session}.jsonl"), **extra}

    def events(self, name):
        return [row for row in map(json.loads, constants.EVENT_LOG.read_text().splitlines()) if row.get("event") == name]

    def test_a_pause_notifies_once_and_names_the_project(self):
        for _ in range(constants.TURN_STOP_STEPS + constants.TURN_REMIND_STEPS):
            turn.turn_guard(self.post("s-note"), 1000)
        self.assertEqual(self.sent, [("Kiasi paused this turn at 6 steps",
                                      'In shop-api. Reply "continue" to resume, or run /clear and then reply "continue", which is cheaper.')])
        self.assertEqual([(row["session_id"], row["title"]) for row in self.events("desktop_notify")], [("s-note", "Kiasi paused this turn at 6 steps")])

    def test_ending_the_turn_after_refused_calls_notifies_once_more(self):
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(self.post("s-end"), 1000)
        pre = self.post("s-end", hook_event_name="PreToolUse", tool_name="Edit")
        for _ in range(constants.TURN_DENY_BACKSTOP + 2):
            turn.paused_call(pre)
        self.assertEqual([title for title, _ in self.sent], ["Kiasi paused this turn at 6 steps", "Kiasi ended this turn, paused at 6 steps"])

    def test_a_subagent_pause_and_warn_mode_notify_nobody(self):
        for _ in range(constants.SUBAGENT_STEP_LIMIT):
            turn.turn_guard(self.post("s-sub", agent_id="a5e0c3d9b71f2a846"), 1000)
        for _ in range(constants.TURN_DENY_BACKSTOP + 1):
            turn.paused_call(self.post("s-sub", agent_id="a5e0c3d9b71f2a846", hook_event_name="PreToolUse"))
        constants.TURN_BUDGET_MODE = "warn"
        for _ in range(constants.TURN_STOP_STEPS + 2):
            turn.turn_guard(self.post("s-warnonly"), 1000)
        self.assertEqual(self.sent, [], "the developer's turn is still running, or nothing stopped")

    def test_nothing_is_logged_when_no_notification_was_sent(self):
        turn.notify_desktop = lambda title, body: False
        for _ in range(constants.TURN_STOP_STEPS):
            turn.turn_guard(self.post("s-quiet"), 1000)
        self.assertEqual(self.events("desktop_notify"), [])


class TestPauseNotificationSetting(KiasiTestCase):
    def test_kiasi_json_and_the_option_set_where_it_shows(self):
        self.addCleanup(setattr, constants, "PAUSE_NOTIFICATION", constants.PAUSE_NOTIFICATION)
        (self.tmp / constants.PROJECT_FILE_NAME).write_text(json.dumps({"pause_notification": "Always"}))
        constants.apply_project(self.tmp)
        self.assertEqual(constants.PAUSE_NOTIFICATION, "always")
        (self.tmp / constants.PROJECT_FILE_NAME).write_text(json.dumps({"pause_notification": "loudly"}))
        constants.apply_project(self.tmp)
        self.assertEqual(constants.PAUSE_NOTIFICATION, "always", "an unknown word changes nothing")

        def option(value):
            env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(self.tmp), "CLAUDE_PLUGIN_OPTION_PAUSE_NOTIFICATION": value}
            script = "from core import constants; print(constants.PAUSE_NOTIFICATION)"
            return subprocess.run([sys.executable, "-c", script], cwd=SCRIPTS, env=env, capture_output=True, text=True, check=True).stdout.strip()

        self.assertEqual([option(" Off "), option("sometimes"), option("")], ["off", "auto", "auto"])
