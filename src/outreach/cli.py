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

from outreach.config import Config, load_config
from outreach.contacts import pick_contacts
from outreach.drafts import sync_run_drafts
from outreach.emails import prepare_emails
from outreach.errors import UserError
from outreach.gmail import Gmail
from outreach.schema import (
    load_applications,
    load_bounces,
    load_contact_candidates,
    load_revealed_addresses,
    load_written_emails,
)
from outreach.send import send_run
from outreach.shortlist import build_groups, company_key
from outreach.store import DEFAULT_PATH as DEFAULT_DB
from outreach.store import Store


def main(argv: list[str] | None = None, *, gmail: Gmail | None = None) -> int:
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
    prepare_cmd = sub.add_parser("prepare", help="lint the written emails, choose resumes and start the review page")
    prepare_cmd.add_argument("run_dir", type=Path)
    prepare_cmd.add_argument("--dry-run", action="store_true", help="no Gmail drafts and nothing can be sent")
    prepare_cmd.add_argument("--port", type=int, default=0, help="review page port on 127.0.0.1 (default: free)")
    prepare_cmd.add_argument("--no-serve", action="store_true", help="prepare the emails and exit without the page")
    prepare_cmd.add_argument("--config", type=Path)
    prepare_cmd.add_argument("--db", type=Path)
    prepare_cmd.add_argument("--now", help="override the current time (ISO 8601)")
    send_cmd = sub.add_parser("send", help="send the confirmed Outreach Emails (also resumes an interrupted send)")
    send_cmd.add_argument("run_dir", type=Path)
    send_cmd.add_argument("--config", type=Path)
    send_cmd.add_argument("--db", type=Path)
    auth_cmd = sub.add_parser("gmail-auth", help="sign in to Gmail once in the browser (saves a token)")
    auth_cmd.add_argument("--config", type=Path)
    config_cmd = sub.add_parser("config", help="print the effective settings")
    config_cmd.add_argument("--config", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "config":
            print(json.dumps(load_config(args.config).as_dict(), indent=2))
            return 0
        if args.command == "gmail-auth":
            return _gmail_auth(args)
        if args.command == "pick-contacts":
            return _pick_contacts(args)
        if args.command == "record-bounces":
            return _record_bounces(args)
        if args.command == "prepare":
            return _prepare(args, gmail)
        if args.command == "send":
            return _send(args, gmail)
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


def _require_run(store: Store, run_dir: Path) -> sqlite3.Row:
    run = store.find_run(run_dir)
    if run is None:
        raise UserError(f"{run_dir}: no Shortlist recorded for this Run; run `outreach shortlist` first")
    return run


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
        run = _require_run(store, args.run_dir)
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


def _real_gmail(config: Config) -> Gmail:
    from outreach.gmail_api import RealGmail

    gmail: Gmail = RealGmail(config)
    return gmail


def _prepare(args: argparse.Namespace, gmail: Gmail | None) -> int:
    config = load_config(args.config)
    addresses = load_revealed_addresses(args.run_dir / "revealed_addresses.json")
    emails = load_written_emails(args.run_dir / "written_emails.json")
    db_path = args.db or DEFAULT_DB
    with closing(Store(db_path)) as store, store.transaction():
        run = _require_run(store, args.run_dir)
        store.set_run_dry_run(run["id"], args.dry_run)
        done = prepare_emails(store, config, run["id"], addresses, emails)
        run_id = int(run["id"])
    mode = " (dry run: no Gmail drafts, nothing can be sent)" if args.dry_run else ""
    print(f"prepared {done.emails} Outreach Email(s) for {done.groups} Company Group(s){mode}")
    if done.blocked:
        print(f"{done.blocked} email(s) blocked by a bad resume; see the review page")
    for line in done.skipped:
        print(f"not prepared: {line}")
    if done.humanizer_skipped:
        print("work-humanizer check skipped (humanizer_check is not set or not available)")
    if not args.dry_run:
        gmail = gmail or _real_gmail(config)
        with closing(Store(db_path)) as store:
            made = sync_run_drafts(store, gmail, config, run_id)
        print(f"created or updated {made} Gmail draft(s)")
    if args.no_serve:
        return 0
    from outreach.review import serve

    confirmed = serve(db_path, config, run_id, port=args.port, gmail=gmail)
    return _send_confirmed(db_path, config, run_id, gmail) if confirmed and not args.dry_run else 0


def _send(args: argparse.Namespace, gmail: Gmail | None) -> int:
    config = load_config(args.config)
    db_path = args.db or DEFAULT_DB
    with closing(Store(db_path)) as store:
        run_id = int(_require_run(store, args.run_dir)["id"])
    return _send_confirmed(db_path, config, run_id, gmail)


def _send_confirmed(db_path: Path, config: Config, run_id: int, gmail: Gmail | None) -> int:
    """Send what the user confirmed and say what went and what is left. Exit code 3: not everything went out."""
    with closing(Store(db_path)) as store:
        report = send_run(store, gmail or _real_gmail(config), config, run_id)
    print(f"sent {len(report.sent)}")
    for line in report.sent:
        print(f"  sent: {line}")
    for line in report.problems:
        print(f"  {line}")
    if report.left:
        detail = f" ({report.not_attempted} not attempted)" if report.not_attempted else ""
        print(f"{report.left} left{detail}; fix the cause and rerun `outreach send <run-dir>` to carry on")
    if report.stopped:
        print(f"outreach: error: send stopped: {report.stopped}", file=sys.stderr)
    return 0 if report.complete else 3


def _gmail_auth(args: argparse.Namespace) -> int:
    from outreach.gmail_api import authorize

    config = load_config(args.config)
    address = authorize(config)
    print(f"Gmail is connected as {address}; token saved to {config.gmail_token}")
    if config.sender and address.lower() != config.sender.strip().lower():
        print(f"warning: the config's sender is {config.sender}, which is not this account", file=sys.stderr)
    return 0
