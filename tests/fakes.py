"""A fake process launcher: records what `outreach start` / `stop` / `prepare` would have run, spawns nothing."""
from __future__ import annotations

import os
from collections.abc import Callable


class FakeLauncher:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.models: list[str] = []
        self.notifications: list[tuple[str, str]] = []
        self.terminated: list[int] = []
        self.awake: set[int] = set()  # caffeinate "processes" currently running
        self.awake_while_claude_ran: list[bool] = []
        self.claude_exit = 0
        self.claude_hook: Callable[[], object] | None = None  # runs "inside" Claude, e.g. to call `outreach stop`
        self.caffeinate_hook: Callable[[], object] | None = None  # runs right after caffeinate started
        self.spawn_hook: Callable[[], object] | None = None  # runs after Claude was spawned, before `on_started`
        self._live: set[int] = set()
        self._next_pid = 5000

    def _pid(self) -> int:
        self._next_pid += 1
        return self._next_pid

    def start_caffeinate(self) -> int:
        pid = self._pid()
        self.awake.add(pid)
        self._live.add(pid)
        if self.caffeinate_hook:
            self.caffeinate_hook()
        return pid

    def run_claude(self, prompt: str, model: str, on_started: Callable[[int], None]) -> int:
        pid = self._pid()
        self._live.add(pid)
        if self.spawn_hook:
            self.spawn_hook()
        on_started(pid)
        self.prompts.append(prompt)
        self.models.append(model)
        self.awake_while_claude_ran.append(bool(self.awake))
        if self.claude_hook:
            self.claude_hook()
        self._live.discard(pid)
        return -15 if pid in self.terminated else self.claude_exit

    def terminate(self, pid: int) -> None:
        self.terminated.append(pid)
        self.awake.discard(pid)
        self._live.discard(pid)

    def is_alive(self, pid: int) -> bool:
        return pid == os.getpid() or pid in self._live

    def notify(self, title: str, message: str) -> None:
        self.notifications.append((title, message))
