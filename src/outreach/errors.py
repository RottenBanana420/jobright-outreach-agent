from __future__ import annotations


class UserError(Exception):
    """A problem the user can fix (bad file, bad config). Shown without a traceback."""
