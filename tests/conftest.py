"""Test helpers: drive the `outreach` CLI end to end against synthetic fixture data."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from outreach.cli import main

# 2026-11-13 02:00 UTC is still 2026-11-12 in America/Los_Angeles.
NOW = "2026-11-12T12:00:00-08:00"
TODAY = "2026-11-12"


def make_app(company: str, title: str, score: int | None = 80, **over: Any) -> dict[str, Any]:
    """A synthetic Application as Claude would write it (no real names or data)."""
    app: dict[str, Any] = {
        "jobright_id": f"id-{company}-{title}".replace(" ", "_"),
        "jobright_url": "https://example.test/jobs/1",
        "company": company,
        "title": title,
        "applied_on": TODAY,
        "status": "Applied",
        "agent_applied": False,
        "job_closed": False,
        "match_score": score,
        "responsibilities": ["Build things"],
        "required": ["A degree"],
        "preferred": [],
        "core_skills": ["Python"],
    }
    app.update(over)
    return app


def make_contact(name: str, title: str = "Software Engineer", company: str = "Acme",
                 group: str = "beyond_network", **over: Any) -> dict[str, Any]:
    """A synthetic Contact candidate as Claude would write it (no address, no real person)."""
    slug = name.lower().replace(" ", "-")
    contact: dict[str, Any] = {
        "name": name,
        "title": title,
        "company": company,
        "linkedin_url": f"https://www.linkedin.com/in/{slug}",
        "group": group,
    }
    contact.update(over)
    return contact


@dataclass
class Result:
    code: int
    out: str
    err: str


@dataclass
class Env:
    root: Path
    run_dir: Path
    db: Path
    config: Path
    capsys: pytest.CaptureFixture[str]

    def write_apps(self, apps: list[dict[str, Any]], **top: Any) -> None:
        doc = {"run_date": TODAY, "timezone": "America/Los_Angeles", "applications": apps, **top}
        (self.run_dir / "applications.json").write_text(json.dumps(doc))

    def write_candidates(self, by_company: dict[str, list[dict[str, Any]]]) -> None:
        doc = {"companies": [{"company": c, "contacts": cs} for c, cs in by_company.items()]}
        (self.run_dir / "contact_candidates.json").write_text(json.dumps(doc))

    def write_bounces(self, bounces: list[dict[str, Any]]) -> None:
        (self.run_dir / "bounces.json").write_text(json.dumps({"bounces": bounces}))

    def write_config(self, toml: str) -> None:
        self.config.write_text(toml)

    def shortlist(self, *extra: str, now: str = NOW) -> Result:
        argv = [
            "shortlist", str(self.run_dir),
            "--config", str(self.config), "--db", str(self.db), "--now", now, *extra,
        ]
        try:
            code = main(argv)
        except SystemExit as e:  # argparse errors
            code = int(e.code or 0)
        cap = self.capsys.readouterr()
        return Result(code, cap.out, cap.err)

    def _run(self, argv: list[str]) -> Result:
        try:
            code = main(argv)
        except SystemExit as e:  # argparse errors
            code = int(e.code or 0)
        cap = self.capsys.readouterr()
        return Result(code, cap.out, cap.err)

    def pick_contacts(self, *extra: str, now: str = NOW) -> Result:
        return self._run([
            "pick-contacts", str(self.run_dir),
            "--config", str(self.config), "--db", str(self.db), "--now", now, *extra,
        ])

    def record_bounces(self, *extra: str) -> Result:
        return self._run(["record-bounces", str(self.run_dir), "--db", str(self.db), *extra])

    def picked_json(self) -> dict[str, Any]:
        return json.loads((self.run_dir / "picked_contacts.json").read_text())  # type: ignore[no-any-return]

    def picked_names(self, company: str) -> list[str]:
        """Names of the picked Contacts for one company, in pick order."""
        [group] = [g for g in self.picked_json()["groups"] if g["company"] == company]
        return [c["name"] for c in group["contacts"]]

    def shortlist_json(self) -> dict[str, Any]:
        return json.loads((self.run_dir / "shortlist.json").read_text())  # type: ignore[no-any-return]

    def query(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()


@pytest.fixture
def env(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Env:
    run_dir = tmp_path / "data" / TODAY
    run_dir.mkdir(parents=True)
    return Env(tmp_path, run_dir, tmp_path / "outreach.db", tmp_path / "outreach.toml", capsys)


AppFactory = Callable[..., dict[str, Any]]
