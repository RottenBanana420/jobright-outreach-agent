"""A Run's lifecycle: `outreach start` and `outreach stop`.

`start` keeps the Mac awake (`caffeinate`), opens Claude Code in visible mode with the project Run command
(`.claude/commands/outreach-run.md`) and waits for it. `stop` ends that process tree from another terminal.
Every process is launched through a `Launcher`, so tests can inject a fake and nothing real is spawned.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from outreach.config import Config
from outreach.errors import UserError
from outreach.store import Store

COMMAND_FILE = Path(".claude/commands/outreach-run.md")
# Once the review is finished, Claude Code gets this long to print its final report before `start` ends it.
REPORT_GRACE_SECONDS = 30.0
POLL_SECONDS = 2.0
# Files Claude writes into the Run folder, in order. A resumed Run skips the steps whose file already exists.
STEP_FILES = (
    "bounces", "applications", "shortlist", "contact_candidates", "picked_contacts",
    "revealed_addresses", "written_emails",
)


class Launcher(Protocol):
    def start_caffeinate(self) -> int: ...
    def run_claude(
        self, prompt: str, model: str, on_started: Callable[[int], None], finished: Callable[[], bool],
    ) -> int: ...
    def terminate(self, pid: int) -> None: ...
    def is_alive(self, pid: int) -> bool: ...
    def notify(self, title: str, message: str) -> None: ...
    def open_url(self, url: str) -> None: ...
    def run_wizard(self, script: Path) -> int: ...


class SystemLauncher:
    """The real thing: `caffeinate`, `claude`, `osascript` and `open` on this Mac."""

    def start_caffeinate(self) -> int:
        # -w ties it to this process, so it also exits if `outreach start` dies without cleaning up.
        proc = subprocess.Popen(
            ["caffeinate", "-dis", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return proc.pid

    def run_claude(
        self, prompt: str, model: str, on_started: Callable[[int], None], finished: Callable[[], bool],
    ) -> int:
        """Run Claude Code until it exits. Once `finished()` turns true, end it after `REPORT_GRACE_SECONDS`."""
        if not COMMAND_FILE.exists():
            raise UserError(f"{COMMAND_FILE}: not found; run `outreach start` from the project folder")
        try:
            proc = subprocess.Popen(["claude", "--chrome", "--model", model, prompt])
        except FileNotFoundError:
            raise UserError("claude: command not found; install Claude Code and make sure it is on PATH") from None
        on_started(proc.pid)
        deadline: float | None = None
        while True:
            try:
                return proc.wait(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            except KeyboardInterrupt:
                continue  # Ctrl-C goes to Claude Code as well; it decides when to exit
            if deadline is None:
                if finished():
                    deadline = time.monotonic() + REPORT_GRACE_SECONDS
            elif time.monotonic() >= deadline:
                self.terminate(proc.pid)
                code = proc.wait()
                _reset_terminal()
                return code

    def terminate(self, pid: int) -> None:
        for target in reversed([pid, *_descendants(pid)]):
            try:
                os.kill(target, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def notify(self, title: str, message: str) -> None:
        # The text goes in as arguments, never into the script, so it cannot change what the script does.
        script = ["on run argv", "display notification (item 1 of argv) with title (item 2 of argv)", "end run"]
        try:
            subprocess.run(
                ["osascript", *[a for line in script for a in ("-e", line)], message, title],
                stdin=subprocess.DEVNULL, capture_output=True, check=False,
            )
        except OSError:
            pass  # a missing osascript (not a Mac) must not stop the review page from coming up

    def open_url(self, url: str) -> None:
        try:
            subprocess.run(["open", url], stdin=subprocess.DEVNULL, capture_output=True, check=False)
        except OSError:
            pass  # no `open` (not a Mac): the URL is still printed and in the notification

    def run_wizard(self, script: Path) -> int:
        """Run a bash wizard (the setup wizard) in this terminal and wait for it."""
        if not script.exists():
            raise UserError(f"{script}: not found; run `outreach` from the project folder")
        return subprocess.run(["bash", str(script)], check=False).returncode


def _reset_terminal() -> None:
    """Claude Code ended by a signal can leave the terminal in raw mode; put it back. Best effort."""
    if sys.stdin.isatty():
        subprocess.run(["stty", "sane"], check=False)


def _descendants(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False).stdout
    found: list[int] = []
    for line in out.split():
        child = int(line)
        found += [child, *_descendants(child)]
    return found


def start_run(
    db_path: Path, config: Config, launcher: Launcher, *, data_dir: Path, now: datetime, contacts: int | None,
    dry_run: bool, options: dict[str, str | None],
) -> int:
    """Run `outreach start`: set up the Run, keep the Mac awake and hand over to Claude Code until it exits."""
    if contacts is not None and contacts < 1:
        raise UserError(f"--contacts: must be at least 1, got {contacts}")
    today = now.date().isoformat()
    run_dir = data_dir / today
    run_dir.mkdir(parents=True, exist_ok=True)
    with closing(Store(db_path)) as store, store.transaction():
        _clear_dead_runs(store, launcher)
        for live in store.runs_with_processes():
            raise UserError(f"a Run is already in progress ({live['run_dir']}); end it with `outreach stop`")
        run_id = store.get_or_create_run(run_dir, today)
        run = store.get_run(run_id)
        resumed = run["status"] != "created"
        steps = [s for s in STEP_FILES if (run_dir / f"{s}.json").exists()]
        if store.run_has_emails(run_id):
            steps.append("prepared")
        state: dict[str, Any] = {
            "run_dir": str(run_dir), "run_date": today, "dry_run": dry_run, "contacts": contacts,
            "resumed": resumed, "steps_done": steps, **options,
        }
        (run_dir / "run_state.json").write_text(json.dumps(state, indent=2))
        store.set_run_dry_run(run_id, dry_run)
        store.set_status("runs", run_id, "running", "resumed" if resumed else "started")
        store.set_run_processes(run_id, os.getpid(), None, None)
    caffeinate = launcher.start_caffeinate()
    code = 1
    try:
        # `outreach stop` can land at any point after the Run was marked running. Each pid is recorded only if the
        # Run was not stopped meanwhile, and a process that could not be recorded is ended on the spot.
        if _remember(db_path, run_id, caffeinate=caffeinate):
            code = launcher.run_claude(
                f"/outreach-run {run_dir}", config.model, lambda pid: _remember_claude(db_path, run_id, launcher, pid),
                lambda: _status(db_path, run_id) == "reviewed",
            )
    finally:
        launcher.terminate(caffeinate)
        with closing(Store(db_path)) as store, store.transaction():
            store.set_run_processes(run_id, None, None, None)
            status = store.get_run(run_id)["status"]
            if status == "reviewed":
                code = 0  # `start` ended Claude Code itself because the review was done
                store.set_status("runs", run_id, "ended", "review finished; Claude Code ended by outreach start")
            elif status != "stopped":
                store.set_status("runs", run_id, "ended" if code == 0 else "failed", f"Claude Code exited with {code}")
    if code != 0 and _status(db_path, run_id) != "stopped":
        print(f"outreach: Claude Code exited with code {code}; run `outreach start` again to resume", file=sys.stderr)
        return 1
    return 0


def _status(db_path: Path, run_id: int) -> str:
    with closing(Store(db_path)) as store:
        return str(store.get_run(run_id)["status"])


def mark_reviewed(store: Store, run_id: int, note: str = "review page closed") -> None:
    """The review page closed and what was confirmed is sent, or there was nothing to review: a live Run can end
    (see `start_run`)."""
    run = store.get_run(run_id)
    if run["owner_pid"] is not None and run["status"] not in ("stopped", "reviewed"):
        store.set_status("runs", run_id, "reviewed", note)


def _remember(db_path: Path, run_id: int, *, caffeinate: int | None = None, claude: int | None = None) -> bool:
    """Save a process id of the Run. False when `outreach stop` already ended the Run: save nothing then."""
    with closing(Store(db_path)) as store, store.transaction():
        run = store.get_run(run_id)
        if run["status"] == "stopped":
            return False
        store.set_run_processes(
            run_id, run["owner_pid"], caffeinate if caffeinate is not None else run["caffeinate_pid"],
            claude if claude is not None else run["claude_pid"],
        )
    return True


def _remember_claude(db_path: Path, run_id: int, launcher: Launcher, pid: int) -> None:
    if not _remember(db_path, run_id, claude=pid):
        launcher.terminate(pid)  # the stop came before Claude was recorded, so `stop` could not end it


def _clear_dead_runs(store: Store, launcher: Launcher) -> None:
    """A Run whose `outreach start` process is gone crashed; mark it failed so it can be resumed."""
    for run in store.runs_with_processes():
        if not launcher.is_alive(run["owner_pid"]):
            if run["caffeinate_pid"] is not None:
                launcher.terminate(run["caffeinate_pid"])
            store.set_run_processes(run["id"], None, None, None)
            store.set_status("runs", run["id"], "failed", "outreach start ended without finishing the Run")


def stop_run(db_path: Path, launcher: Launcher) -> int:
    """Run `outreach stop`: end the Run's processes and mark it stopped."""
    with closing(Store(db_path)) as store, store.transaction():
        _clear_dead_runs(store, launcher)
        live = store.runs_with_processes()
        if not live:
            raise UserError("no Run is in progress")
        for run in live:
            for pid in (run["claude_pid"], run["caffeinate_pid"]):
                if pid is not None:
                    launcher.terminate(pid)
            store.set_run_processes(run["id"], None, None, None)
            store.set_status("runs", run["id"], "stopped", "outreach stop")
            print(f"stopped the Run in {run['run_dir']}; `outreach start` resumes it")
    return 0
