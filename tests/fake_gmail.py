"""The in-memory Gmail adapter: the only fake in the test suite.

It keeps drafts, a Sent folder and labels, and records every call. Failures are scripted per operation:

    gmail.fail("send_draft", GmailUncertain("timeout"), call=1, accepted=True)

makes the first `send_draft` call raise after Gmail accepted the message (the timeout-after-accept case).
"""
from __future__ import annotations

import email
import email.policy
import time
from dataclasses import dataclass, field
from email.message import EmailMessage

from outreach.gmail import DraftContent, GmailError, SentMessage, build_mime


@dataclass
class _Failure:
    error: GmailError
    call: int
    accepted: bool


@dataclass
class FakeGmail:
    drafts: dict[str, bytes] = field(default_factory=dict)
    sent: dict[str, bytes] = field(default_factory=dict)  # Gmail message id -> message
    labels: dict[str, set[str]] = field(default_factory=dict)  # Gmail message id -> label names
    calls: list[tuple[str, str]] = field(default_factory=list)  # (operation, draft id or message id)
    send_times: list[float] = field(default_factory=list)
    _failures: dict[str, list[_Failure]] = field(default_factory=dict)
    _count: dict[str, int] = field(default_factory=dict)
    _next: int = 0

    # --- scripting and inspection -----------------------------------------------------------

    def fail(self, op: str, error: GmailError, call: int = 1, accepted: bool = False) -> None:
        """Make the `call`-th (1-based) call to `op` raise `error`. `accepted`: do the work first."""
        self._failures.setdefault(op, []).append(_Failure(error, call, accepted))

    def calls_to(self, op: str) -> list[str]:
        return [target for name, target in self.calls if name == op]

    def draft(self, draft_id: str) -> EmailMessage:
        return parse(self.drafts[draft_id])

    def only_draft(self) -> EmailMessage:
        [raw] = self.drafts.values()
        return parse(raw)

    def sent_messages(self) -> list[EmailMessage]:
        return [parse(raw) for raw in self.sent.values()]

    def labels_of(self, message: EmailMessage) -> set[str]:
        [gmail_id] = [i for i, raw in self.sent.items() if parse(raw)["Message-ID"] == message["Message-ID"]]
        return self.labels.get(gmail_id, set())

    # --- the adapter ------------------------------------------------------------------------

    def _enter(self, op: str, target: str) -> _Failure | None:
        self.calls.append((op, target))
        self._count[op] = self._count.get(op, 0) + 1
        for failure in self._failures.get(op, []):
            if failure.call == self._count[op]:
                if not failure.accepted:
                    raise failure.error
                return failure
        return None

    def _id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}{self._next}"

    def create_draft(self, content: DraftContent) -> str:
        failure = self._enter("create_draft", content.to)
        draft_id = self._id("draft")
        self.drafts[draft_id] = build_mime(content)
        if failure:
            raise failure.error
        return draft_id

    def update_draft(self, draft_id: str, content: DraftContent) -> None:
        failure = self._enter("update_draft", draft_id)
        if draft_id not in self.drafts:
            raise GmailError(f"draft {draft_id} not found")
        self.drafts[draft_id] = build_mime(content)
        if failure:
            raise failure.error

    def send_draft(self, draft_id: str) -> SentMessage:
        failure = self._enter("send_draft", draft_id)
        if draft_id not in self.drafts:
            raise GmailError(f"draft {draft_id} not found")
        gmail_id = self._id("msg")
        self.sent[gmail_id] = self.drafts.pop(draft_id)
        self.send_times.append(time.monotonic())
        if failure:
            raise failure.error
        return SentMessage(gmail_id)

    def find_sent(self, message_id: str) -> SentMessage | None:
        self._enter("find_sent", message_id)
        for gmail_id, raw in self.sent.items():
            if parse(raw)["Message-ID"] == message_id:
                return SentMessage(gmail_id)
        return None

    def add_label(self, gmail_id: str, label: str) -> None:
        self._enter("add_label", gmail_id)
        self.labels.setdefault(gmail_id, set()).add(label)


def parse(raw: bytes) -> EmailMessage:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    return message
