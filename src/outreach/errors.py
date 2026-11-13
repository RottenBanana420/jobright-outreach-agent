from __future__ import annotations


class UserError(Exception):
    """A problem the user can fix (bad file, bad config). Shown without a traceback."""


class Conflict(UserError):
    """The request is fine but the current state forbids it (blocked email, stale totals, locked email)."""
