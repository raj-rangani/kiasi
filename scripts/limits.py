#!/usr/bin/env python3
"""Set up, remove or check the Kiasi status line, the only source of plan limits.

setup copies statusline.py to ~/.claude/kiasi/, records the status line the
user had before in statusline-chain.json (it keeps running, with the Kiasi part
appended), and points statusLine in ~/.claude/settings.json at the copy.
remove puts the previous status line back. Run setup again after a plugin
update to refresh the copy.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import constants  # noqa: E402


class SettingsError(Exception):
    pass


def installed_script():
    return constants.HOME_DIR / constants.STATUSLINE_SCRIPT_NAME


def chain_file():
    return constants.HOME_DIR / constants.STATUSLINE_CHAIN_NAME


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SettingsError(f"could not read {path}: {exc}") from exc


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def is_ours(status_line):
    command = (status_line or {}).get("command") or ""
    return f"{constants.HOME_DIR.name}/{constants.STATUSLINE_SCRIPT_NAME}" in command


def backup_settings(settings_file):
    backup = settings_file.with_name(settings_file.name + constants.SETTINGS_BACKUP_SUFFIX)
    if settings_file.is_file() and not backup.exists():
        shutil.copy2(settings_file, backup)
    return backup


def setup():
    settings_file = constants.CLAUDE_SETTINGS_FILE
    settings = read_json(settings_file)
    current = settings.get("statusLine")
    previous = read_json(chain_file()).get("previous") if is_ours(current) else current
    installed_script().parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(constants.PLUGIN_ROOT / "scripts" / constants.STATUSLINE_SCRIPT_NAME, installed_script())
    write_json(chain_file(), {"previous": previous, "data_dir": str(constants.DATA_DIR)})
    backup = backup_settings(settings_file)
    settings["statusLine"] = {**(current if is_ours(current) else {}), "type": "command",
                              "command": f'python3 "{installed_script()}"'}
    write_json(settings_file, settings)
    kept = f"; your previous status line still runs first: {previous.get('command')}" if previous else ""
    return f"Kiasi status line set up in {settings_file} (backup at {backup}){kept}. It shows from the next status line update."


def remove():
    settings_file = constants.CLAUDE_SETTINGS_FILE
    settings = read_json(settings_file)
    if not is_ours(settings.get("statusLine")):
        return f"The status line in {settings_file} is not Kiasi's; nothing changed."
    previous = read_json(chain_file()).get("previous")
    if previous:
        settings["statusLine"] = previous
    else:
        settings.pop("statusLine", None)
    write_json(settings_file, settings)
    for path in (chain_file(), installed_script()):
        if path.exists():
            path.unlink()
    restored = f"restored {previous.get('command')}" if previous else "no status line is set now"
    return f"Kiasi status line removed from {settings_file}; {restored}."


def status():
    settings = read_json(constants.CLAUDE_SETTINGS_FILE)
    if not is_ours(settings.get("statusLine")):
        return "Kiasi status line: not set up. Run /kiasi:limits setup."
    previous = (read_json(chain_file()).get("previous") or {}).get("command")
    updated = read_json(constants.DATA_DIR / constants.RATE_LIMITS_NAME).get("updated")
    seen = (f"limits last saved {time.strftime('%Y-%m-%d %H:%M', time.localtime(updated))}" if updated
            else "no limits saved yet (they appear on Pro and Max plans after the next status line update)")
    chained = f", runs after {previous}" if previous else ""
    return f"Kiasi status line: set up{chained}; {seen}."


ACTIONS = {"setup": setup, "remove": remove, "status": status}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Set up, remove or check the Kiasi status line.")
    parser.add_argument("action", nargs="?", default="status", choices=sorted(ACTIONS))
    args = parser.parse_args(argv)
    try:
        print(ACTIONS[args.action]())
    except (SettingsError, OSError) as exc:
        print(f"kiasi limits {args.action} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
