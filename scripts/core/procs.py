import os
import signal
import subprocess
import sys


def detached_kwargs():
    """Popen keyword arguments that start a child in its own session, or on Windows its own process group with a hidden console that everything it starts inherits, so it outlives the hook that started it. With no console at all (DETACHED_PROCESS) each console program the child started, such as the dashboard's 30-minute sync, would open a new window."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def run_tree(argv, timeout, **kwargs):
    """Run a command and wait for it; at the timeout stop it together with everything it started, then raise TimeoutExpired.
    subprocess.run's timeout kills only the command itself, so a sync stopped that way left its report script running."""
    if sys.platform != "win32":
        kwargs["start_new_session"] = True  # its own process group, which kill_tree signals as a whole
    proc = subprocess.Popen(argv, **kwargs)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        raise


def kill_tree(proc):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    proc.wait()
