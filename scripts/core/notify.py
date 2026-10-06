import os
import shutil
import subprocess
import sys

from core import constants
from core.procs import detached_kwargs

# A toast through the WinRT API of Windows PowerShell 5.1, which every Windows 10 and 11 has. The text comes from the
# environment, so nothing in it is read as script. A toast shows only for a registered app id; PowerShell's own is used.
WINDOWS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
WINDOWS_TOAST = "; ".join((
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null",
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null",
    "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)",
    "$raw = [xml]$template.GetXml()",
    # A click on a toast starts its app by default, here a PowerShell window. A protocol toast with nothing to open only closes.
    "$raw.DocumentElement.SetAttribute('activationType', 'protocol')",
    "$raw.DocumentElement.SetAttribute('launch', '')",
    f"($raw.toast.visual.binding.text | Where-Object {{ $_.id -eq '1' }}).AppendChild($raw.CreateTextNode($env:{constants.NOTIFY_TITLE_ENV})) > $null",
    f"($raw.toast.visual.binding.text | Where-Object {{ $_.id -eq '2' }}).AppendChild($raw.CreateTextNode($env:{constants.NOTIFY_BODY_ENV})) > $null",
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument",
    "$xml.LoadXml($raw.OuterXml)",
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)",
    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{WINDOWS_APP_ID}').Show($toast)",
))
# The text arrives as arguments, so nothing in it is read as AppleScript.
MACOS_SCRIPT = ("on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run")


def desktop_command(title, body):
    """The command that shows a desktop notification on this system, or None when the system has no such command."""
    if sys.platform == "win32":
        powershell = shutil.which("powershell")
        return [powershell, "-NoProfile", "-NonInteractive", "-Command", WINDOWS_TOAST] if powershell else None
    if sys.platform == "darwin":
        osascript = shutil.which("osascript")
        return [osascript, *(part for line in MACOS_SCRIPT for part in ("-e", line)), title, body] if osascript else None
    notify_send = shutil.which("notify-send")
    return [notify_send, "-a", "Kiasi", "--", title, body] if notify_send else None


def wanted():
    """auto notifies only in the apps that raise no notification of their own; the terminal gets one from pause_alert."""
    if constants.PAUSE_NOTIFICATION == "auto":
        return os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") in constants.PAUSE_NOTIFICATION_APPS
    return constants.PAUSE_NOTIFICATION == "always"


def notify_desktop(title, body):
    """Show a desktop notification without waiting for it. True when its command was started; a hook never fails over it."""
    command = desktop_command(title, body) if wanted() else None
    if not command:
        return False
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={**os.environ, constants.NOTIFY_TITLE_ENV: title, constants.NOTIFY_BODY_ENV: body}, **detached_kwargs())
    except OSError:
        return False
    return True
