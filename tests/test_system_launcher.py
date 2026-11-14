"""The real launcher's process handling, checked on harmless `sleep` processes (nothing of Claude or the Mac)."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from outreach import run
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


@pytest.mark.real_launcher
def test_notify_without_osascript_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")  # no osascript, as on a Linux CI runner

    SystemLauncher().notify("title", "message")


@pytest.mark.real_launcher
def test_claude_is_ended_after_the_grace_period_once_the_review_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stand-in `claude` that would sit at its prompt for a minute, like Claude Code after its final report.
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexec sleep 60\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(run, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(run, "REPORT_GRACE_SECONDS", 0.3)
    checks: list[float] = []

    def finished() -> bool:
        checks.append(time.monotonic())
        return len(checks) >= 3  # the review is done on the third check

    started = time.monotonic()
    code = SystemLauncher().run_claude("/outreach-run x", "model", lambda pid: None, finished)

    assert code != 0  # ended by a signal, not by itself
    assert time.monotonic() - checks[2] >= 0.3  # the grace period was given
    assert time.monotonic() - started < 10
