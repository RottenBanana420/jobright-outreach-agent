from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from outreach.config import load_config
from outreach.contacts import pick_contacts
from outreach.errors import UserError
from outreach.schema import load_applications, load_bounces, load_contact_candidates
from outreach.shortlist import build_groups, company_key
from outreach.store import DEFAULT_PATH as DEFAULT_DB
from outreach.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="outreach")
    sub = parser.add_subparsers(dest="command", required=True)
    shortlist_cmd = sub.add_parser("shortlist", help="rank Today's Applications into a Shortlist")
    shortlist_cmd.add_argument("run_dir", type=Path)
    shortlist_cmd.add_argument("--config", type=Path)
    shortlist_cmd.add_argument("--db", type=Path)
    shortlist_cmd.add_argument("--now", help="override the current time (ISO 8601), e.g. to replay a past day")
    pick_cmd = sub.add_parser("pick-contacts", help="pick Contacts for each Shortlisted Company Group")
    pick_cmd.add_argument("run_dir", type=Path)
    pick_cmd.add_argument("--contacts", type=int, help="override contacts_per_company for this Run")
    pick_cmd.add_argument("--config", type=Path)
    pick_cmd.add_argument("--db", type=Path)
    pick_cmd.add_argument("--now", help="override the current time (ISO 8601), e.g. to replay a past day")
    bounces_cmd = sub.add_parser("record-bounces", help="store bounced addresses so they are never used again")
    bounces_cmd.add_argument("run_dir", type=Path)
    bounces_cmd.add_argument("--db", type=Path)
    config_cmd = sub.add_parser("config", help="print the effective settings")
    config_cmd.add_argument("--config", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "config":
            print(json.dumps(load_config(args.config).as_dict(), indent=2))
            return 0
        if args.command == "pick-contacts":
            return _pick_contacts(args)
        if args.command == "record-bounces":
            return _record_bounces(args)
        return _shortlist(args)
    except (OSError, sqlite3.Error) as e:
        print(f"outreach: error: cannot read or write files: {e}", file=sys.stderr)
        return 1
    except UserError as e:
        print(f"outreach: error: {e}", file=sys.stderr)
        return 2


def _now(args: argparse.Namespace, zone: ZoneInfo) -> datetime:
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(zone)
    return now if now.tzinfo else now.replace(tzinfo=zone)


def _shortlist(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apps = load_applications(args.run_dir / "applications.json")
    zone = ZoneInfo(config.timezone)
    today = _now(args, zone).astimezone(zone).date().isoformat()
    kept = [a for a in apps if a["status"] == "Applied" and a["applied_on"] == today]
    groups = build_groups(kept, config.shortlist_size)
    shortlisted = [g for g in groups if g["shortlisted"]]
    with closing(Store(args.db or DEFAULT_DB)) as store, store.transaction():
        run_id = store.get_or_create_run(args.run_dir, today)
        store.record_shortlist(run_id, groups)
    out = {"run_date": today, "total_groups": len(groups), "groups": shortlisted}
    (args.run_dir / "shortlist.json").write_text(json.dumps(out, indent=2))
    return 0


def _pick_contacts(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    cap = config.contacts_per_company if args.contacts is None else args.contacts
    if cap < 1:
        raise UserError(f"--contacts: must be at least 1, got {cap}")
    entries = load_contact_candidates(args.run_dir / "contact_candidates.json")
    zone = ZoneInfo(config.timezone)
    now = _now(args, zone)
    with closing(Store(args.db or DEFAULT_DB)) as store, store.transaction():
        run = store.find_run(args.run_dir)
        if run is None:
            raise UserError(f"{args.run_dir}: no Shortlist recorded for this Run; run `outreach shortlist` first")
        groups = store.shortlisted_groups(run["id"])
        by_key = {g["key"]: g for g in groups}
        candidates: dict[str, list[dict[str, Any]]] = {}
        for i, entry in enumerate(entries):
            key = company_key(entry["company"])
            if key not in by_key:
                raise UserError(f"companies[{i}].company: {entry['company']!r} is not a Shortlisted Company Group")
            if key in candidates:
                raise UserError(f"companies[{i}].company: {entry['company']!r} is listed twice")
            candidates[key] = entry["contacts"]
        last_emailed = store.last_emailed(run["id"])
        bounced = store.bounced_people()
        out_groups = []
        for group in groups:
            picks = pick_contacts(
                group["key"], candidates.get(group["key"], []), cap,
                now=now, cooldown_days=config.cooldown_days, last_emailed=last_emailed, bounced=bounced,
            )
            store.record_picks(run["id"], group["id"], cap, picks.picked, picks.flagged)
            rows = store.picked_contacts(group["id"])
            out_groups.append({
                "company": group["name"],
                "rank": group["rank"],
                "picked_of_cap": f"{len(picks.picked)} of {cap}",
                "contacts": [_contact_out(r) for r in rows if r["status"] == "picked"],
                "review": [_contact_out(r) for r in rows if r["status"] == "flagged"],
            })
    out = {"run_date": run["run_date"], "contacts_per_company": cap, "groups": out_groups}
    (args.run_dir / "picked_contacts.json").write_text(json.dumps(out, indent=2))
    return 0


def _contact_out(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "title": row["title"],
        "category": row["category"],
        "linkedin_url": row["linkedin_url"],
        "groups": json.loads(row["jobright_groups"]),
        "connection": row["connection"],
        "cooldown_last_emailed": row["cooldown_last_emailed"],
    }


def _record_bounces(args: argparse.Namespace) -> int:
    bounces = load_bounces(args.run_dir / "bounces.json")
    with closing(Store(args.db or DEFAULT_DB)) as store, store.transaction():
        store.add_bounces(bounces, datetime.now(UTC).date().isoformat())
    print(f"recorded {len(bounces)} bounced address(es)")
    return 0
