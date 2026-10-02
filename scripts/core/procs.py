import subprocess
import sys


def detached_kwargs():
    """Popen keyword arguments that start a child in its own session or console-free process group, so it outlives the hook that started it."""
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}
