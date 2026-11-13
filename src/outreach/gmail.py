"""Gmail adapter: the only external seam (ADR 0001).

`Gmail` is the interface the rest of the code uses. The real implementation (`outreach.gmail_api`) calls the
official Gmail API; tests use an in-memory fake with scriptable failures. Both build the message with
`build_mime`, so what a test inspects is the message the real adapter would upload.

Error classes tell the sender what it may do next:

- `GmailAuthError`, `GmailQuotaError`: Gmail refused the call, so nothing was sent. Stop the batch.
- `GmailUncertain`: a timeout or network drop. Gmail may or may not have acted; look in Sent before retrying.
- `GmailError`: any other refusal. Nothing was sent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from outreach.errors import UserError

LABEL = "Outreach"


class GmailError(UserError):
    """Gmail refused a call. Nothing was sent."""


class GmailAuthError(GmailError):
    """Gmail authorization is missing, expired or revoked."""


class GmailQuotaError(GmailError):
    """A Gmail rate or daily sending limit was hit."""


class GmailUncertain(GmailError):  # noqa: N818
    """A timeout or network error: Gmail may have acted on the call anyway."""


@dataclass(frozen=True)
class DraftContent:
    """One Outreach Email as a Gmail draft: one recipient, one attachment, no CC or BCC."""

    sender: str
    to: str
    subject: str
    body: str
    attachment: Path
    message_id: str


@dataclass(frozen=True)
class SentMessage:
    gmail_id: str


class Gmail(Protocol):
    def create_draft(self, content: DraftContent) -> str:
        """Create a draft and return its id."""

    def update_draft(self, draft_id: str, content: DraftContent) -> None:
        """Replace a draft's message (subject, body, attachment)."""

    def send_draft(self, draft_id: str) -> SentMessage:
        """Send a draft. Returning means Gmail accepted it, not that it was delivered."""

    def find_sent(self, message_id: str) -> SentMessage | None:
        """The message in Sent with this Message-ID header, if any."""

    def add_label(self, gmail_id: str, label: str) -> None:
        """Label a message, creating the label when it does not exist yet."""


def new_message_id(sender: str) -> str:
    """A unique Message-ID for one Outreach Email, on the sender's own domain."""
    domain = sender.rpartition("@")[2] or "outreach.invalid"
    return f"<{uuid.uuid4().hex}@{domain}>"


def build_mime(content: DraftContent) -> bytes:
    """The RFC 822 message for a draft. Headers cannot hold a newline, so text cannot add a Cc or Bcc."""
    message = EmailMessage()
    message["From"] = content.sender
    message["To"] = content.to
    message["Subject"] = content.subject
    message["Message-ID"] = content.message_id
    message.set_content(content.body)
    message.add_attachment(
        content.attachment.read_bytes(), maintype="application", subtype="pdf", filename=content.attachment.name,
    )
    return message.as_bytes()
