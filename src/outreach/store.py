"""Store: SQLite holds Runs, Applications, Company Groups, Contacts and bounces, plus an append-only event log.

Every status lives in a `status` column. A status changes only through `Store.set_status`,
which writes the audit event in the same transaction; the first status of a new row is
logged by the insert that creates it.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("data/outreach.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_dir TEXT NOT NULL UNIQUE,
    run_date TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    status TEXT NOT NULL,
    contact_cap INTEGER,
    contacts_picked INTEGER,
    UNIQUE (run_id, key)
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    group_id INTEGER NOT NULL REFERENCES company_groups(id),
    jobright_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    applied_on TEXT NOT NULL,
    job_closed INTEGER NOT NULL,
    agent_applied INTEGER NOT NULL,
    match_score INTEGER,
    hidden_score INTEGER,
    details TEXT NOT NULL,
    UNIQUE (run_id, jobright_id)
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    group_id INTEGER NOT NULL REFERENCES company_groups(id),
    linkedin_key TEXT NOT NULL,
    linkedin_url TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    listed_company TEXT NOT NULL,
    category TEXT NOT NULL,
    jobright_groups TEXT NOT NULL,
    connection TEXT NOT NULL,
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    cooldown_last_emailed TEXT,
    email TEXT,
    emailed_at TEXT,
    UNIQUE (group_id, linkedin_key)
);
CREATE TABLE IF NOT EXISTS bounces (
    address TEXT PRIMARY KEY,
    bounced_at TEXT NOT NULL,
    note TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outreach_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES company_groups(id),
    address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    resume_variant TEXT NOT NULL,
    resume_path TEXT NOT NULL,
    block_reason TEXT,
    findings TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (contact_id, group_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'status'
);
"""

_STATUS_TABLES = {"runs", "company_groups", "contacts", "outreach_emails"}
_NOT_DETAILS = {
    "jobright_id", "company", "title", "applied_on",
    "job_closed", "agent_applied", "match_score", "hidden_score",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        self._con.executescript(_SCHEMA)
        self._add_missing_columns("company_groups", {"contact_cap": "INTEGER", "contacts_picked": "INTEGER"})
        self._add_missing_columns("runs", {
            "dry_run": "INTEGER NOT NULL DEFAULT 0",
            "owner_pid": "INTEGER", "caffeinate_pid": "INTEGER", "claude_pid": "INTEGER",
        })
        self._add_missing_columns("events", {"kind": "TEXT NOT NULL DEFAULT 'status'"})
        self._rename_status("outreach_emails", "drafted", "written")

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        """Bring a database made by an earlier version up to date."""
        have = {row["name"] for row in self._con.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                self._con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    def _rename_status(self, entity: str, old: str, new: str) -> None:
        """Rename a status in place, in the rows and in their audit events (earlier versions used `old`)."""
        self._con.execute(f"UPDATE {entity} SET status = ? WHERE status = ?", (new, old))
        self._con.execute("UPDATE events SET to_status = ? WHERE entity = ? AND to_status = ?", (new, entity, old))
        self._con.execute("UPDATE events SET from_status = ? WHERE entity = ? AND from_status = ?", (new, entity, old))
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._con:
            yield

    def _log(self, entity: str, entity_id: int, from_status: str | None, to_status: str, note: str = "") -> None:
        self._con.execute(
            "INSERT INTO events (at, entity, entity_id, from_status, to_status, note) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), entity, entity_id, from_status, to_status, note),
        )

    def set_status(self, entity: str, entity_id: int, to_status: str, note: str = "") -> None:
        """The one place a status changes. Also appends the audit event."""
        if entity not in _STATUS_TABLES:
            raise ValueError(f"{entity} has no status")
        row = self._con.execute(f"SELECT status FROM {entity} WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            raise LookupError(f"{entity} {entity_id} does not exist")
        if row["status"] == to_status:
            return
        self._con.execute(f"UPDATE {entity} SET status = ? WHERE id = ?", (to_status, entity_id))
        self._log(entity, entity_id, row["status"], to_status, note)

    def log_edit(self, entity: str, entity_id: int, what: str) -> None:
        """Append an audit event for a change that is not a status change (an edit, a resume switch)."""
        row = self._con.execute(f"SELECT status FROM {entity} WHERE id = ?", (entity_id,)).fetchone()
        status = row["status"] if row is not None else ""
        self._con.execute(
            "INSERT INTO events (at, entity, entity_id, from_status, to_status, note, kind)"
            " VALUES (?, ?, ?, ?, ?, ?, 'edit')",
            (_now(), entity, entity_id, status, status, what),
        )

    def get_or_create_run(self, run_dir: Path, run_date: str) -> int:
        key = str(run_dir.resolve())
        row = self._con.execute("SELECT id FROM runs WHERE run_dir = ?", (key,)).fetchone()
        if row is not None:
            return int(row["id"])
        cur = self._con.execute(
            "INSERT INTO runs (run_dir, run_date, status, created_at) VALUES (?, ?, 'created', ?)",
            (key, run_date, _now()),
        )
        run_id = int(cur.lastrowid or 0)
        self._log("runs", run_id, None, "created")
        return run_id

    def record_shortlist(self, run_id: int, groups: list[dict[str, Any]]) -> None:
        """Upsert this Run's Company Groups and Applications, then mark the Run shortlisted.

        Rows are updated in place, never recreated, so later steps can hang state off them
        and a re-run is safe. Rows that are no longer in the file are removed.
        """
        keep_apps: list[str] = []
        keep_groups: list[str] = []
        for group in groups:
            status = "shortlisted" if group["shortlisted"] else "cut"
            row = self._con.execute(
                "SELECT id FROM company_groups WHERE run_id = ? AND key = ?", (run_id, group["key"])
            ).fetchone()
            if row is None:
                cur = self._con.execute(
                    "INSERT INTO company_groups (run_id, key, name, rank, status) VALUES (?, ?, ?, ?, ?)",
                    (run_id, group["key"], group["company"], group["rank"], status),
                )
                group_id = int(cur.lastrowid or 0)
                self._log("company_groups", group_id, None, status)
            else:
                group_id = int(row["id"])
                self._con.execute(
                    "UPDATE company_groups SET name = ?, rank = ? WHERE id = ?",
                    (group["company"], group["rank"], group_id),
                )
                self.set_status("company_groups", group_id, status)
            keep_groups.append(group["key"])
            for app in group["applications"]:
                details = json.dumps({k: v for k, v in app.items() if k not in _NOT_DETAILS})
                self._con.execute(
                    "INSERT INTO applications (run_id, group_id, jobright_id, company, title, applied_on,"
                    " job_closed, agent_applied, match_score, hidden_score, details)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (run_id, jobright_id) DO UPDATE SET group_id = excluded.group_id,"
                    " company = excluded.company, title = excluded.title, applied_on = excluded.applied_on,"
                    " job_closed = excluded.job_closed, agent_applied = excluded.agent_applied,"
                    " match_score = excluded.match_score, hidden_score = excluded.hidden_score,"
                    " details = excluded.details",
                    (run_id, group_id, app["jobright_id"], app["company"], app["title"], app["applied_on"],
                     app["job_closed"], app["agent_applied"], app["match_score"], app["hidden_score"], details),
                )
                keep_apps.append(app["jobright_id"])
        self._delete_missing("applications", "jobright_id", run_id, keep_apps)
        self._delete_missing("company_groups", "key", run_id, keep_groups)
        self.set_status("runs", run_id, "shortlisted")

    def find_run(self, run_dir: Path) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._con.execute(
            "SELECT * FROM runs WHERE run_dir = ?", (str(run_dir.resolve()),)
        ).fetchone()
        return row

    def shortlisted_groups(self, run_id: int) -> list[sqlite3.Row]:
        return self._con.execute(
            "SELECT * FROM company_groups WHERE run_id = ? AND status != 'cut' ORDER BY rank",
            (run_id,),
        ).fetchall()

    def record_picks(
        self, run_id: int, group_id: int, cap: int,
        picked: list[dict[str, Any]], flagged: list[dict[str, Any]],
    ) -> None:
        """Replace a Company Group's Contacts with this pick; the group remembers "N of M".

        Rows are updated in place, never recreated, so later steps (addresses, Outreach Emails)
        can hang state off them and a re-run is safe. Contacts no longer in the pick are removed, except
        those with an Outreach Email, which are kept with status `dropped` so the email is not lost.
        """
        keep: list[str] = []
        ordered = [(c, "picked") for c in picked] + [(c, "flagged") for c in flagged]
        for position, (c, status) in enumerate(ordered):
            fields = (
                c["linkedin_url"], c["name"], c["title"], c["company"], c["category"],
                json.dumps(c["groups"]), c["connection"], position, c.get("cooldown_last_emailed"),
            )
            row = self._con.execute(
                "SELECT id FROM contacts WHERE group_id = ? AND linkedin_key = ?", (group_id, c["key"])
            ).fetchone()
            if row is None:
                cur = self._con.execute(
                    "INSERT INTO contacts (run_id, group_id, linkedin_key, linkedin_url, name, title,"
                    " listed_company, category, jobright_groups, connection, position,"
                    " cooldown_last_emailed, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, group_id, c["key"], *fields, status),
                )
                self._log("contacts", int(cur.lastrowid or 0), None, status)
            else:
                self._con.execute(
                    "UPDATE contacts SET linkedin_url = ?, name = ?, title = ?, listed_company = ?,"
                    " category = ?, jobright_groups = ?, connection = ?, position = ?,"
                    " cooldown_last_emailed = ? WHERE id = ?",
                    (*fields, row["id"]),
                )
                self.set_status("contacts", int(row["id"]), status)
            keep.append(c["key"])
        marks = ", ".join("?" for _ in keep)
        gone = self._con.execute(
            f"SELECT id, status, EXISTS (SELECT 1 FROM outreach_emails WHERE contact_id = contacts.id) AS has_email"
            f" FROM contacts WHERE group_id = ? AND linkedin_key NOT IN ({marks})", (group_id, *keep),
        ).fetchall()
        for row in gone:
            if row["has_email"]:
                # Deleting would cascade to the Outreach Email, losing a reviewed or approved email.
                self.set_status("contacts", int(row["id"]), "dropped", "no longer in the contact candidates")
            else:
                self._con.execute("DELETE FROM contacts WHERE id = ?", (row["id"],))
        self._con.execute(
            "UPDATE company_groups SET contact_cap = ?, contacts_picked = ? WHERE id = ?",
            (cap, len(picked), group_id),
        )

    def picked_contacts(self, group_id: int) -> list[sqlite3.Row]:
        return self._con.execute(
            "SELECT * FROM contacts WHERE group_id = ? ORDER BY position", (group_id,)
        ).fetchall()

    def flagged_contacts(self, run_id: int) -> list[sqlite3.Row]:
        """Contacts of the Run that get no email and need the user's eye: cooldown, bounced, shared address."""
        return self._con.execute(
            "SELECT * FROM contacts WHERE run_id = ? AND status IN ('flagged', 'bounced', 'duplicate_address')"
            " ORDER BY group_id, position", (run_id,),
        ).fetchall()

    def set_contact_email(self, contact_id: int, address: str) -> None:
        """Attach the revealed (Unverified) address to a Contact."""
        self._con.execute("UPDATE contacts SET email = ? WHERE id = ?", (address.lower(), contact_id))

    def mark_emailed(self, contact_id: int, at: str) -> None:
        """Record that this Contact was emailed (ISO 8601 time); it starts their cooldown."""
        self._con.execute("UPDATE contacts SET emailed_at = ? WHERE id = ?", (at, contact_id))

    def last_emailed(self, run_id: int) -> dict[str, datetime]:
        """When each person (by LinkedIn key) was last emailed in any other Run, at any company."""
        rows = self._con.execute(
            "SELECT linkedin_key, emailed_at FROM contacts WHERE emailed_at IS NOT NULL AND run_id != ?",
            (run_id,),
        ).fetchall()
        latest: dict[str, datetime] = {}
        for row in rows:
            at = datetime.fromisoformat(row["emailed_at"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if row["linkedin_key"] not in latest or at > latest[row["linkedin_key"]]:
                latest[row["linkedin_key"]] = at
        return latest

    def add_bounces(self, bounces: list[dict[str, str]], today: str) -> None:
        for b in bounces:
            self._con.execute(
                "INSERT INTO bounces (address, bounced_at, note, recorded_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (address) DO UPDATE SET bounced_at = excluded.bounced_at, note = excluded.note",
                (b["address"], b["bounced_at"] or today, b["note"], _now()),
            )

    def is_bounced(self, address: str) -> bool:
        return self._con.execute("SELECT 1 FROM bounces WHERE address = ?", (address.lower(),)).fetchone() is not None

    def bounced_people(self) -> set[str]:
        """LinkedIn keys of people whose known address has bounced."""
        rows = self._con.execute(
            "SELECT DISTINCT c.linkedin_key FROM contacts c JOIN bounces b ON b.address = lower(c.email)"
        ).fetchall()
        return {row["linkedin_key"] for row in rows}

    def _delete_missing(self, table: str, column: str, run_id: int, keep: list[str]) -> None:
        marks = ", ".join("?" for _ in keep)
        self._con.execute(
            f"DELETE FROM {table} WHERE run_id = ? AND {column} NOT IN ({marks})", (run_id, *keep)
        )

    # --- Outreach Emails (ticket #4) ---------------------------------------------------------

    def set_run_dry_run(self, run_id: int, dry_run: bool) -> None:
        self._con.execute("UPDATE runs SET dry_run = ? WHERE id = ?", (int(dry_run), run_id))

    def get_run(self, run_id: int) -> sqlite3.Row:
        row: sqlite3.Row | None = self._con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise LookupError(f"run {run_id} does not exist")
        return row

    def group_applications(self, group_id: int) -> list[sqlite3.Row]:
        """A Company Group's Applications, best first (open by Match Score, then closed by hidden score)."""
        return self._con.execute(
            "SELECT * FROM applications WHERE group_id = ?"
            " ORDER BY job_closed, match_score DESC, COALESCE(hidden_score, -1) DESC, id",
            (group_id,),
        ).fetchall()

    def run_contact(self, run_id: int, contact_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._con.execute(
            "SELECT c.*, g.name AS company FROM contacts c JOIN company_groups g ON g.id = c.group_id"
            " WHERE c.id = ? AND c.run_id = ?", (contact_id, run_id),
        ).fetchone()
        return row

    def upsert_email(
        self, *, run_id: int, contact_id: int, group_id: int, address: str, subject: str, body: str,
        content_hash: str, resume_variant: str, resume_path: str, block_reason: str | None, findings: str,
    ) -> int:
        """Create the Outreach Email for (Contact, Company Group), or replace its content in place."""
        row = self._con.execute(
            "SELECT id, status FROM outreach_emails WHERE contact_id = ? AND group_id = ?", (contact_id, group_id)
        ).fetchone()
        if row is None:
            cur = self._con.execute(
                "INSERT INTO outreach_emails (run_id, contact_id, group_id, address, subject, body, content_hash,"
                " resume_variant, resume_path, block_reason, findings, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'written', ?)",
                (run_id, contact_id, group_id, address, subject, body, content_hash, resume_variant, resume_path,
                 block_reason, findings, _now()),
            )
            email_id = int(cur.lastrowid or 0)
            self._log("outreach_emails", email_id, None, "written")
            return email_id
        email_id = int(row["id"])
        self._con.execute(
            "UPDATE outreach_emails SET address = ?, subject = ?, body = ?, content_hash = ?, resume_variant = ?,"
            " resume_path = ?, block_reason = ?, findings = ? WHERE id = ?",
            (address, subject, body, content_hash, resume_variant, resume_path, block_reason, findings, email_id),
        )
        self.log_edit("outreach_emails", email_id, "re-prepared from the written emails file")
        self.set_status("outreach_emails", email_id, "written", "re-prepared")
        return email_id

    def get_email(self, email_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._con.execute(
            "SELECT * FROM outreach_emails WHERE id = ?", (email_id,)
        ).fetchone()
        return row

    def run_emails(self, run_id: int) -> list[sqlite3.Row]:
        """Every Outreach Email of the Run with its Contact and Company Group, in review order."""
        return self._con.execute(
            "SELECT e.*, c.name AS contact_name, c.title AS contact_title, c.category, c.jobright_groups,"
            " c.connection, c.linkedin_url, g.name AS company, g.rank, g.contact_cap, g.contacts_picked"
            " FROM outreach_emails e JOIN contacts c ON c.id = e.contact_id"
            " JOIN company_groups g ON g.id = e.group_id WHERE e.run_id = ? ORDER BY g.rank, c.position",
            (run_id,),
        ).fetchall()

    def update_email_content(self, email_id: int, subject: str, body: str, content_hash: str, findings: str) -> None:
        self._con.execute(
            "UPDATE outreach_emails SET subject = ?, body = ?, content_hash = ?, findings = ? WHERE id = ?",
            (subject, body, content_hash, findings, email_id),
        )

    def update_group_resume(self, group_id: int, variant: str, path: str, block_reason: str | None) -> None:
        """Point every Outreach Email of a Company Group at one Resume Variant."""
        self._con.execute(
            "UPDATE outreach_emails SET resume_variant = ?, resume_path = ?, block_reason = ? WHERE group_id = ?",
            (variant, path, block_reason, group_id),
        )

    def get_group(self, group_id: int) -> sqlite3.Row:
        row: sqlite3.Row | None = self._con.execute(
            "SELECT * FROM company_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Company Group {group_id} does not exist")
        return row

    def run_emails_for_group(self, group_id: int) -> list[sqlite3.Row]:
        return self._con.execute("SELECT * FROM outreach_emails WHERE group_id = ?", (group_id,)).fetchall()

    def set_email_block(self, email_id: int, reason: str | None) -> None:
        self._con.execute("UPDATE outreach_emails SET block_reason = ? WHERE id = ?", (reason, email_id))

    # --- Run lifecycle: `outreach start` / `stop` (ticket #6) -----------------------------------

    def set_run_processes(self, run_id: int, owner_pid: int | None, caffeinate_pid: int | None,
                          claude_pid: int | None) -> None:
        """Remember the processes of a live Run: `outreach start` itself, its `caffeinate` and its Claude Code."""
        self._con.execute(
            "UPDATE runs SET owner_pid = ?, caffeinate_pid = ?, claude_pid = ? WHERE id = ?",
            (owner_pid, caffeinate_pid, claude_pid, run_id),
        )

    def runs_with_processes(self) -> list[sqlite3.Row]:
        return self._con.execute("SELECT * FROM runs WHERE owner_pid IS NOT NULL ORDER BY id").fetchall()

    def run_has_emails(self, run_id: int) -> bool:
        return self._con.execute(
            "SELECT 1 FROM outreach_emails WHERE run_id = ? LIMIT 1", (run_id,)
        ).fetchone() is not None
