"""The sender: confirmed Outreach Emails go out through Gmail, one at a time, and every outcome is recorded.

Safety rules, all enforced here and not on the review page:

- It refuses to run for a dry run, and when nothing was confirmed. Only `ready_to_send` emails are sent, and
  `ready_to_send` is reachable only from Approval, through the final confirmation (`emails.confirm_send`).
- "sent" means Gmail accepted the message. Before any retry of an email that may have gone out, Sent is
  searched for its Message-ID; found means sent, and nothing is sent twice.
- An auth or quota error stops the batch. Whatever was not sent stays confirmed, and a rerun carries on.
- Each status is saved before and after the Gmail call, so a crash leaves `sending`, which a rerun treats as
  uncertain.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NoReturn

from outreach.config import Config
from outreach.drafts import refresh_draft
from outreach.errors import UserError
from outreach.gmail import LABEL, Gmail, GmailAuthError, GmailError, GmailQuotaError, GmailUncertain
from outreach.store import Store


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # one line per email that did not go out, or a label that failed
    not_attempted: int = 0
    stopped: str | None = None  # why the batch stopped early

    @property
    def complete(self) -> bool:
        return not self.problems and not self.not_attempted and self.stopped is None

    @property
    def left(self) -> int:
        return self.not_attempted + sum(1 for p in self.problems if not p.startswith("label:"))


class _Stop(Exception):  # noqa: N818
    """End the batch now; the reason is already in the report."""


def _label(row: sqlite3.Row) -> str:
    return f"{row['contact_name']} <{row['address']}> ({row['company']})"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def send_run(
    store: Store, gmail: Gmail, config: Config, run_id: int, sleep: Callable[[float], None] = time.sleep,
) -> SendReport:
    if store.get_run(run_id)["dry_run"]:
        raise UserError("this Run is a dry run: nothing can be sent (run `outreach prepare` without --dry-run)")
    todo = store.unfinished_emails(run_id)
    if not todo:
        raise UserError("nothing to send: no Outreach Email is confirmed and waiting (approve emails and confirm "
                        "the totals on the review page first)")
    report = SendReport()
    attempted = False
    for index, row in enumerate(todo):
        try:
            if row["status"] == "sent":
                _apply_label(store, gmail, row, report)
                continue
            if attempted and config.send_pause_seconds > 0:
                sleep(config.send_pause_seconds)
            attempted = _send_one(store, gmail, config, row, report) or attempted
        except _Stop:
            report.not_attempted = sum(1 for r in todo[index + 1:] if r["status"] != "sent")
            break
    return report


def _set(store: Store, email_id: int, status: str, note: str) -> None:
    with store.transaction():
        store.set_status("outreach_emails", email_id, status, note)


def _stop(report: SendReport, why: object) -> NoReturn:
    report.stopped = str(why)
    raise _Stop


def _fail(store: Store, row: sqlite3.Row, report: SendReport, error: GmailError) -> None:
    """Gmail refused, so nothing went out. Rerunning tries the email again (after looking in Sent)."""
    _set(store, row["id"], "failed", str(error))
    report.problems.append(f"failed: {_label(row)}: {error}")


def _mark_sent(store: Store, gmail: Gmail, row: sqlite3.Row, gmail_id: str, report: SendReport) -> None:
    with store.transaction():
        store.record_sent(row["id"], gmail_id, _now())
        store.set_status("outreach_emails", row["id"], "sent", f"Gmail accepted message {gmail_id}")
        store.mark_emailed(row["contact_id"], _now())
    report.sent.append(_label(row))
    _apply_label(store, gmail, row, report, gmail_id)


def _apply_label(
    store: Store, gmail: Gmail, row: sqlite3.Row, report: SendReport, gmail_id: str | None = None,
) -> None:
    try:
        gmail.add_label(gmail_id or row["gmail_message_id"], LABEL)
    except (GmailAuthError, GmailQuotaError) as e:
        report.problems.append(f"label: {_label(row)} was sent but not labelled {LABEL!r}: {e}")
        _stop(report, e)
    except GmailError as e:
        report.problems.append(f"label: {_label(row)} was sent but not labelled {LABEL!r}: {e}")
        return
    with store.transaction():
        store.set_labelled(row["id"])


def _send_one(store: Store, gmail: Gmail, config: Config, row: sqlite3.Row, report: SendReport) -> bool:
    """Send one email. Returns whether a send was attempted (a pause is due before the next one)."""
    if row["status"] != "ready_to_send":
        # An earlier attempt may have gone out: look in Sent before anything else.
        try:
            found = gmail.find_sent(row["message_id"])
        except GmailUncertain as e:
            _set(store, row["id"], "uncertain", f"could not check Sent: {e}")
            report.problems.append(f"uncertain: {_label(row)}: could not check Sent ({e})")
            _stop(report, e)
        if found:
            _mark_sent(store, gmail, row, found.gmail_id, report)
            return False
    _set(store, row["id"], "sending", "sending now")
    try:
        with store.transaction():
            draft_id = refresh_draft(store, gmail, config, row["id"])
    except (GmailUncertain, GmailAuthError, GmailQuotaError) as e:  # nothing was sent, but Gmail is not reachable
        _fail(store, row, report, e)
        _stop(report, e)
    except GmailError as e:
        _fail(store, row, report, e)
        return True
    try:
        message = gmail.send_draft(draft_id)
    except (GmailAuthError, GmailQuotaError) as e:
        _fail(store, row, report, e)
        _stop(report, e)
    except GmailUncertain as e:
        _set(store, row["id"], "uncertain", str(e))
        _settle_uncertain(store, gmail, row, draft_id, report)
    except GmailError as e:
        _fail(store, row, report, e)
    else:
        _mark_sent(store, gmail, row, message.gmail_id, report)
    return True


def _settle_uncertain(store: Store, gmail: Gmail, row: sqlite3.Row, draft_id: str, report: SendReport) -> None:
    """A timeout or network error: search Sent, and only if the message is not there retry once."""

    def look() -> str | None:
        try:
            found = gmail.find_sent(row["message_id"])
        except GmailUncertain as e:
            _give_up(store, row, report, f"could not check Sent: {e}")
        return found.gmail_id if found else None

    if gmail_id := look():
        _mark_sent(store, gmail, row, gmail_id, report)
        return
    try:
        message = gmail.send_draft(draft_id)
    except (GmailAuthError, GmailQuotaError) as e:
        _fail(store, row, report, e)
        _stop(report, e)
    except GmailUncertain as e:
        if gmail_id := look():
            _mark_sent(store, gmail, row, gmail_id, report)
            return
        _give_up(store, row, report, f"no answer from Gmail and not in Sent: {e}")
    except GmailError as e:
        _fail(store, row, report, e)
    else:
        _mark_sent(store, gmail, row, message.gmail_id, report)


def _give_up(store: Store, row: sqlite3.Row, report: SendReport, why: str) -> NoReturn:
    _set(store, row["id"], "uncertain", why)
    report.problems.append(f"uncertain: {_label(row)}: {why}")
    _stop(report, why)
