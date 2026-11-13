"""Outreach Email workflow: prepare them from Claude's files, then edit, switch resume, decide and confirm.

Both `outreach prepare` and the review server call these functions, so the rules (what blocks Approval,
what locks an email, what the audit log records) live in one place.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from outreach.config import Config
from outreach.errors import Conflict, UserError
from outreach.lint import Finding, hard_findings, lint_email, run_humanizer
from outreach.resume import check_resume, resume_path, variant_for
from outreach.schema import body_problem, subject_problem
from outreach.store import Store

# Once an email is marked ready to send (or later) the review page can no longer change it.
LOCKED = {"ready_to_send", "sending", "sent", "failed", "uncertain"}
DECISIONS = {"approved", "rejected", "skipped"}
_CONTACT_OK = {"picked", "bounced", "duplicate_address"}


def content_hash(address: str, subject: str, body: str) -> str:
    return hashlib.sha256(f"{address}\n{subject}\n{body}".encode()).hexdigest()


@dataclass
class Prepared:
    emails: int = 0
    groups: int = 0
    blocked: int = 0
    skipped: list[str] = field(default_factory=list)
    humanizer_skipped: bool = False


def _findings_json(findings: list[Finding]) -> str:
    return json.dumps([{"code": f.code, "message": f.message} for f in findings if not f.hard])


def _lint(config: Config, *, subject: str, body: str, address: str, roles: list[str],
          company: str) -> tuple[list[Finding], bool]:
    """All findings for one email, and whether the humanizer check was skipped."""
    findings = lint_email(subject=subject, body=body, recipients=[address], address=address,
                          roles=roles, company=company)
    extra = run_humanizer(config.humanizer_check, body)
    return findings + (extra or []), extra is None


def prepare_emails(
    store: Store, config: Config, run_id: int, addresses: list[dict[str, Any]], emails: list[dict[str, Any]],
) -> Prepared:
    by_contact: dict[int, str] = {}
    for i, a in enumerate(addresses):
        if store.run_contact(run_id, a["contact_id"]) is None:
            raise UserError(f"addresses[{i}].contact_id: no Contact {a['contact_id']} in this Run "
                            "(use the ids in picked_contacts.json)")
        by_contact[a["contact_id"]] = a["address"]

    # Validate everything before writing anything: one bad email stops the whole prepare.
    plan: list[tuple[sqlite3.Row, str, dict[str, Any]]] = []
    for i, e in enumerate(emails):
        contact = store.run_contact(run_id, e["contact_id"])
        if contact is None:
            raise UserError(f"emails[{i}].contact_id: no Contact {e['contact_id']} in this Run")
        if contact["status"] not in _CONTACT_OK:
            raise UserError(f"emails[{i}].contact_id: Contact {e['contact_id']} ({contact['name']}) is "
                            f"{contact['status']}, not a picked Contact; flagged Contacts get no email")
        address = by_contact.get(e["contact_id"]) or contact["email"]
        if not address:
            raise UserError(f"emails[{i}].contact_id: Contact {e['contact_id']} ({contact['name']}) has no "
                            "revealed address; add it to revealed_addresses.json")
        if hard := hard_findings(e["recipients"]):
            raise UserError(f"emails[{i}]: {hard[0].message}")
        if e["recipients"][0] != address:
            raise UserError(f"emails[{i}].to: {e['recipients'][0]!r} is not the revealed address {address!r} "
                            f"for {contact['name']}")
        plan.append((contact, address, e))

    result = Prepared()
    claimed: dict[str, str] = {}
    groups: set[int] = set()
    for contact, address, e in plan:
        label = f"{contact['name']} ({contact['company']})"
        existing = store.email_for(contact["id"], contact["group_id"])
        if existing is not None and existing["status"] in LOCKED:
            result.skipped.append(f"{label}: already {existing['status']}, left as it is")
            continue
        store.set_contact_email(contact["id"], address)
        if store.is_bounced(address):
            store.set_status("contacts", contact["id"], "bounced", "address bounced before")
            result.skipped.append(f"{label}: {address} bounced before, no email")
            continue
        if address in claimed:
            store.set_status("contacts", contact["id"], "duplicate_address", f"same address as {claimed[address]}")
            result.skipped.append(f"{label}: same address as {claimed[address]}, no second email")
            continue
        claimed[address] = contact["name"]
        store.set_status("contacts", contact["id"], "picked", "address usable")

        apps = store.group_applications(contact["group_id"])
        variant = variant_for(apps[0]["title"])
        path = resume_path(config, variant)
        block = check_resume(path)
        findings, hook_skipped = _lint(config, subject=e["subject"], body=e["body"], address=address,
                                       roles=[a["title"] for a in apps], company=contact["company"])
        result.humanizer_skipped = result.humanizer_skipped or hook_skipped
        store.upsert_email(
            run_id=run_id, contact_id=contact["id"], group_id=contact["group_id"], address=address,
            subject=e["subject"], body=e["body"], content_hash=content_hash(address, e["subject"], e["body"]),
            resume_variant=variant, resume_path=str(path), block_reason=block, findings=_findings_json(findings),
        )
        result.emails += 1
        result.blocked += block is not None
        groups.add(contact["group_id"])
    result.groups = len(groups)
    return result


def _editable(store: Store, email_id: int) -> sqlite3.Row:
    row = store.get_email(email_id)
    if row is None:
        raise LookupError(f"Outreach Email {email_id} does not exist")
    if row["status"] in LOCKED:
        raise Conflict(f"this email is {row['status']} and can no longer be changed")
    return row


def _reopen(store: Store, row: sqlite3.Row, why: str) -> None:
    """An approved email that changed must be approved again: what is approved is what gets sent."""
    if row["status"] == "approved":
        store.set_status("outreach_emails", row["id"], "written", why)


def edit_email(store: Store, config: Config, email_id: int, subject: str, body: str) -> None:
    row = _editable(store, email_id)
    subject, body = subject.strip(), body.strip()
    if problem := subject_problem(subject):
        raise UserError(f"subject: {problem}")
    if problem := body_problem(body):
        raise UserError(f"body: {problem}")
    group = store.get_group(row["group_id"])
    roles = [a["title"] for a in store.group_applications(row["group_id"])]
    findings, _ = _lint(config, subject=subject, body=body, address=row["address"], roles=roles, company=group["name"])
    store.update_email_content(email_id, subject, body, content_hash(row["address"], subject, body),
                               _findings_json(findings))
    store.log_edit("outreach_emails", email_id, "edited subject/body on the review page")
    _reopen(store, row, "edited after approval")


def switch_resume(store: Store, config: Config, group_id: int, variant: str) -> None:
    if variant not in config.resume_files:
        raise UserError(f"resume: unknown Resume Variant {variant!r} (known: {', '.join(config.resume_files)})")
    rows = store.run_emails_for_group(group_id)
    if not rows:
        raise LookupError(f"Company Group {group_id} has no Outreach Emails")
    for row in rows:
        _editable(store, row["id"])
    path = resume_path(config, variant)
    store.update_group_resume(group_id, variant, str(path), check_resume(path))
    for row in rows:
        store.log_edit("outreach_emails", row["id"], f"resume switched to {variant} ({path.name})")
        _reopen(store, row, "resume switched after approval")


def decide(store: Store, email_id: int, status: str) -> None:
    if status not in DECISIONS:
        raise UserError(f"status: must be one of {', '.join(sorted(DECISIONS))}")
    row = _editable(store, email_id)
    if status == "approved":
        _check_approvable(store, row)
    store.set_status("outreach_emails", email_id, status, "decided on the review page")


def _check_approvable(store: Store, row: sqlite3.Row) -> None:
    block = check_resume(Path(row["resume_path"]))
    if block != row["block_reason"]:
        store.set_email_block(row["id"], block)
    if block:
        raise Conflict(f"blocked: {block}. Switch the Resume Variant or fix the file first")


def bulk_approve(store: Store, run_id: int) -> tuple[int, int]:
    """Approve every written email whose resume is fine. Returns (approved, left).

    Lint findings are warnings and do not block Approval, so they do not stop a bulk approve; a blocked
    resume does. Emails in any other status (approved, rejected, skipped) are not touched.
    """
    approved = left = 0
    for row in store.run_emails(run_id):
        if row["status"] != "written":
            continue
        try:
            _check_approvable(store, row)
        except Conflict:
            left += 1
            continue
        store.set_status("outreach_emails", row["id"], "approved", "bulk approve")
        approved += 1
    return approved, left


@dataclass
class Totals:
    emails: int
    recipients: int


def totals(store: Store, run_id: int) -> Totals:
    approved = [r for r in store.run_emails(run_id) if r["status"] == "approved"]
    return Totals(len(approved), len({r["address"] for r in approved}))


def confirm_send(store: Store, run_id: int, emails: int, recipients: int) -> Totals:
    """Mark the approved emails ready to send, once the user echoes back the totals the page showed.

    Dry run: the totals are checked and nothing is marked. Rejected, skipped and unreviewed emails
    are never marked.
    """
    shown = totals(store, run_id)
    if shown.emails == 0:
        raise Conflict("nothing is approved, so there is nothing to send")
    if (emails, recipients) != (shown.emails, shown.recipients):
        raise Conflict(f"totals do not match: {shown.emails} email(s) to {shown.recipients} recipient(s) are "
                       f"approved now, you confirmed {emails} and {recipients}. Reload the page and check again")
    approved = [r for r in store.run_emails(run_id) if r["status"] == "approved"]
    for row in approved:
        _check_approvable(store, row)
    if store.get_run(run_id)["dry_run"]:
        return shown
    for row in approved:
        store.set_status("outreach_emails", row["id"], "ready_to_send", "send confirmed on the review page")
    return shown
