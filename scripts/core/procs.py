import subprocess
import sys


def detached_kwargs():
    """Popen keyword arguments that start a child in its own session, or on Windows its own process group with a hidden console that everything it starts inherits, so it outlives the hook that started it. With no console at all (DETACHED_PROCESS) each console program the child started, such as the dashboard's 30-minute sync, would open a new window."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}
