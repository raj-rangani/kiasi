import json
import sys

from core import constants
from core.caps import handle_archive, handle_archive_path
from core.events import log_error, session_lock
from core.prompt import handle_prompt
from core.session import handle_plugin_compact, handle_plugin_quiet, handle_pre_compact, handle_session_start, handle_stop
from core.turn import handle_post_tool, handle_pre_tool_use, handle_tool_batch, handle_tool_failure


HANDLERS = {
    "UserPromptSubmit": handle_prompt,
    "PreToolUse": handle_pre_tool_use,
    "PostToolUse": handle_post_tool,
    "PostToolUseFailure": handle_tool_failure,
    "PostToolBatch": handle_tool_batch,
    "PreCompact": handle_pre_compact,
    "PluginArchivePath": handle_archive_path,
    "PluginArchive": handle_archive,
    "PluginCompact": handle_plugin_compact,
    "PluginQuiet": handle_plugin_quiet,
    "Stop": handle_stop,
    "SessionStart": handle_session_start,
}


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    constants.apply_project(payload.get("cwd") or "")
    handler = HANDLERS.get(payload.get("hook_event_name", ""))
    if not handler:
        return
    with session_lock(payload.get("session_id", "")) as locked:
        if not locked:
            log_error("lock_timeout", session_id=payload.get("session_id", ""), hook=payload.get("hook_event_name", ""))
        output = handler(payload)
    if output:
        print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"kiasi fail-open: {exc}\n")
    sys.exit(0)
