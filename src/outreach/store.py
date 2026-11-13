"""Store: SQLite holds Runs, Applications and Company Groups, plus an append-only event log.

Every status lives in a `status` column. A status changes only through `Store.set_status`,
which writes the audit event in the same transaction; the first status of a new row is
logged by the insert that creates it.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""

_STATUS_TABLES = {"runs", "company_groups"}
_NOT_DETAILS = {"jobright_id", "company", "title", "applied_on", "job_closed", "agent_applied", "match_score", "hidden_score"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        self._con.executescript(_SCHEMA)

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

    def _delete_missing(self, table: str, column: str, run_id: int, keep: list[str]) -> None:
        marks = ", ".join("?" for _ in keep)
        self._con.execute(
            f"DELETE FROM {table} WHERE run_id = ? AND {column} NOT IN ({marks})", (run_id, *keep)
        )
