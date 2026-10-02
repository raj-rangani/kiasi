#!/usr/bin/env python3
"""Keep the Kiasi dashboard running from login, with no Claude Code session needed.

Usage: python3 autostart.py install | remove | status

install writes a small launcher to ~/.claude/kiasi and registers it with the
login-time starter of this system: a systemd user service on Linux (an XDG
autostart entry when there is no user systemd), a LaunchAgent on macOS, a
script in the Startup folder on Windows. The launcher reads plugin-root and
data-dir from ~/.claude/kiasi, so it keeps working after the plugin updates.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import constants  # noqa: E402
from core.procs import detached_kwargs  # noqa: E402

LAUNCHER_TEXT = '''import os
import subprocess
import sys
from pathlib import Path

home = Path(__file__).resolve().parent


def read(name):
    try:
        return home.joinpath(name).read_text().strip()
    except OSError:
        return ""


root = read("plugin-root")
script = Path(root) / "scripts" / "dashboard.py"
if not script.is_file():
    sys.stderr.write(f"kiasi: plugin not found at {root!r}; start one Claude Code session so the path is recorded\\n")
    sys.exit(1)
env = {**os.environ, "CLAUDE_PLUGIN_DATA": read("data-dir") or str(home)}
sys.exit(subprocess.call([sys.executable, str(script)] + sys.argv[1:], env=env))
'''


def python_executable():
    if sys.platform == "win32":
        quiet = Path(sys.executable).with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return sys.executable


def write_launcher():
    constants.HOME_DIR.mkdir(parents=True, exist_ok=True)
    constants.DASHBOARD_LAUNCHER.write_text(LAUNCHER_TEXT)
    constants.PLUGIN_ROOT_POINTER.write_text(str(constants.PLUGIN_ROOT) + "\n")
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        constants.DATA_DIR_POINTER.write_text(str(constants.DATA_DIR) + "\n")


def command():
    return [python_executable(), str(constants.DASHBOARD_LAUNCHER), str(constants.DASHBOARD_PORT)]


def quoted():
    return " ".join(f'"{part}"' for part in command())


def systemd_unit():
    return constants.HOME_DIR.parent.parent / ".config" / "systemd" / "user" / f"{constants.AUTOSTART_NAME}.service"


def systemd_text():
    return ("[Unit]\nDescription=Kiasi dashboard (Claude Code plugin)\n\n[Service]\n"
            f"ExecStart={quoted()}\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n")


def xdg_entry():
    return constants.HOME_DIR.parent.parent / ".config" / "autostart" / f"{constants.AUTOSTART_NAME}.desktop"


def xdg_text():
    return ("[Desktop Entry]\nType=Application\nName=Kiasi dashboard\nComment=Local Claude Code usage dashboard on 127.0.0.1\n"
            f"Exec={quoted()}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")


def launch_agent():
    return Path.home() / "Library" / "LaunchAgents" / f"com.{constants.AUTOSTART_NAME}.plist"


def launch_agent_text():
    import plistlib
    return plistlib.dumps({
        "Label": f"com.{constants.AUTOSTART_NAME}", "ProgramArguments": command(), "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False}, "StandardOutPath": str(constants.DASHBOARD_LOG),
        "StandardErrorPath": str(constants.DASHBOARD_LOG)}).decode()


def startup_script():
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{constants.AUTOSTART_NAME}.vbs"


def startup_text():
    args = " ".join(f'""{part}""' for part in command())
    return f'Set shell = CreateObject("WScript.Shell")\nshell.Run "{args}", 0, False\n'


def entry():
    """(path, text, kind) for this system; kind names the mechanism."""
    if sys.platform == "darwin":
        return launch_agent(), launch_agent_text(), "LaunchAgent"
    if sys.platform == "win32":
        return startup_script(), startup_text(), "Startup folder"
    if user_systemd_available():
        return systemd_unit(), systemd_text(), "systemd user service"
    return xdg_entry(), xdg_text(), "XDG autostart"


def user_systemd_available():
    if not shutil.which("systemctl"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=5).returncode in (0, 1)
    except (OSError, subprocess.SubprocessError):
        return False


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=20)


def launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)


def stop_session_started_dashboard():
    """Stop a dashboard that a session started, so the login-time one owns the port."""
    import dashboard
    dashboard.stop_running()


def start_now(path, kind):
    if kind == "systemd user service":
        systemctl("daemon-reload")
        result = systemctl("enable", "--now", constants.AUTOSTART_NAME)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "systemctl enable failed")
        return
    if kind == "LaunchAgent":
        launchctl("bootout", f"gui/{os.getuid()}", str(path))
        result = launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        if result.returncode != 0:
            result = launchctl("load", "-w", str(path))
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "launchctl load failed")
        return
    subprocess.Popen(command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached_kwargs())


def install():
    import dashboard
    write_launcher()
    path, text, kind = entry()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    stop_session_started_dashboard()
    start_now(path, kind)
    url = dashboard.wait_for_url()
    where = url or f"not answering yet, see {constants.DASHBOARD_LOG}"
    return f"Kiasi dashboard registered as a {kind} ({path}); it starts at every login and is {where}"


def remove():
    path, _text, kind = entry()
    if kind == "systemd user service":
        systemctl("disable", "--now", constants.AUTOSTART_NAME)
        systemctl("daemon-reload")
    elif kind == "LaunchAgent":
        launchctl("bootout", f"gui/{os.getuid()}", str(path))
    removed = path.exists()
    if removed:
        path.unlink()
    if kind in ("XDG autostart", "Startup folder"):
        import dashboard
        dashboard.stop_running()
    return f"{kind} removed ({path})" if removed else f"nothing to remove: no {kind} at {path}"


def status():
    import dashboard
    path, _text, kind = entry()
    url = dashboard.running_url()
    running = f"running on {url}" if url else "not running"
    if path.exists():
        return f"Kiasi dashboard autostart is on ({kind}, {path}); dashboard {running}"
    return f"Kiasi dashboard autostart is off; the session-start hook starts the dashboard instead; dashboard {running}"


def main(argv):
    mode = (argv[1] if len(argv) > 1 else "status").strip().lower()
    actions = {"install": install, "on": install, "remove": remove, "off": remove, "status": status}
    if mode not in actions:
        sys.stderr.write("usage: autostart.py install | remove | status\n")
        return 2
    try:
        print(actions[mode]())
    except Exception as exc:  # noqa: BLE001  one line for the user, never a traceback
        print(f"autostart {mode} failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
