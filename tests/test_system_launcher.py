"""The real launcher's process handling, checked on harmless `sleep` processes (nothing of Claude or the Mac)."""
from __future__ import annotations

import subprocess
import time

from outreach.run import SystemLauncher


def test_terminate_ends_a_process_and_everything_it_started() -> None:
    # A shell that starts a child, like Claude Code starting its helpers.
    parent = subprocess.Popen(["sh", "-c", "sleep 60 & wait"], start_new_session=True)
    launcher = SystemLauncher()
    child = 0
    for _ in range(100):
        out = subprocess.run(["pgrep", "-P", str(parent.pid)], capture_output=True, text=True).stdout.split()
        if out:
            child = int(out[0])
            break
        time.sleep(0.05)
    assert child, "child did not start"

    launcher.terminate(parent.pid)

    parent.wait(timeout=5)
    for _ in range(100):
        if not launcher.is_alive(child):
            break
        time.sleep(0.05)
    assert not launcher.is_alive(child)


def test_terminate_ignores_a_process_that_is_already_gone() -> None:
    gone = subprocess.Popen(["true"])
    gone.wait()

    SystemLauncher().terminate(gone.pid)  # no error
