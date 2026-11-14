"""Gmail drafts for Outreach Emails: create them, keep them equal to what the Store holds.

The Store is the truth. A draft is a copy that the user can also see in Gmail, and the sender refreshes it
once more right before sending, so what was approved is what goes out.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from outreach.config import Config
from outreach.emails import LOCKED
from outreach.errors import UserError
from outreach.gmail import DraftContent, Gmail, GmailNotFound, new_message_id
from outreach.store import Store


def require_sender(config: Config) -> str:
    if not config.sender.strip():
        raise UserError("sender: set your Gmail address as `sender` in outreach.toml before creating drafts")
    return config.sender.strip()


def draft_content(config: Config, row: sqlite3.Row, message_id: str) -> DraftContent:
    return DraftContent(
        sender=require_sender(config), to=row["address"], subject=row["subject"], body=row["body"],
        attachment=Path(row["resume_path"]), message_id=message_id,
    )


def sync_draft(store: Store, gmail: Gmail, config: Config, email_id: int) -> bool:
    """Create the email's Gmail draft, or bring the existing one up to date. Returns whether Gmail was called.

    An email with a blocked resume has nothing to attach, and a locked email is on its way out: both are left alone.
    """
    row = _email(store, email_id)
    if row["status"] in LOCKED or row["block_reason"]:
        return False
    refresh_draft(store, gmail, config, email_id)
    return True


def _email(store: Store, email_id: int) -> sqlite3.Row:
    row = store.get_email(email_id)
    if row is None:
        raise LookupError(f"Outreach Email {email_id} does not exist")
    return row


def refresh_draft(store: Store, gmail: Gmail, config: Config, email_id: int, *, recreate: bool = True) -> str:
    """Make the Gmail draft equal to the Store, whatever the email's status (the sender uses this too).

    Gmail is called outside any database transaction; the draft id is saved in its own as soon as Gmail returns
    it. When the draft no longer exists in Gmail (the user deleted it) a new one is made, with a new Message-ID.
    The sender passes `recreate=False` once an earlier send attempt may have consumed the draft: a missing
    draft then raises `GmailNotFound` instead of being made again, because making it again could send twice.
    """
    row = _email(store, email_id)
    if row["draft_id"]:
        draft_id = str(row["draft_id"])
        try:
            gmail.update_draft(draft_id, draft_content(config, row, row["message_id"]))
            return draft_id
        except GmailNotFound:
            if not recreate:
                raise
    message_id = new_message_id(require_sender(config))
    draft_id = gmail.create_draft(draft_content(config, row, message_id))
    with store.transaction():
        store.set_draft(email_id, draft_id, message_id)
    return draft_id


def sync_run_drafts(store: Store, gmail: Gmail, config: Config, run_id: int) -> int:
    """Draft every email of the Run that can have one. Each draft id is saved as soon as Gmail returns it,
    so a failure part way leaves the earlier drafts recorded and a second `prepare` carries on."""
    require_sender(config)
    made = 0
    for row in store.run_emails(run_id):
        made += sync_draft(store, gmail, config, row["id"])
    return made
