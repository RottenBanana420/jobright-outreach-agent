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

from outreach.config import DEFAULT_PATH as CONFIG_FILE
from outreach.config import Config, load_config
from outreach.contacts import pick_contacts
from outreach.drafts import sync_run_drafts
from outreach.emails import prepare_emails
from outreach.errors import UserError
from outreach.gmail import Gmail
from outreach.menu import SETUP, Ask, Item, Menu, build_menu, menu_loop
from outreach.resume import resume_path, variant_for
from outreach.run import Launcher, SystemLauncher, mark_reviewed, start_run, stop_run
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


def main(
    argv: list[str] | None = None, launcher: Launcher | None = None, *, gmail: Gmail | None = None,
    ask: Ask | None = None,
) -> int:
    """The `outreach` command. With no command in a terminal it opens the menu; `ask` replaces the prompt in tests."""
    parser = argparse.ArgumentParser(prog="outreach")
    sub = parser.add_subparsers(dest="command")
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
    start_cmd = sub.add_parser("start", help="start a Run: keep the Mac awake, open Claude Code")
    start_cmd.add_argument("--contacts", type=int, help="override contacts_per_company for this Run")
    start_cmd.add_argument("--dry-run", action="store_true", help="no Gmail drafts and nothing can be sent")
    start_cmd.add_argument("--data-dir", type=Path, default=Path("data"), help="folder holding one folder per Run date")
    start_cmd.add_argument("--config", type=Path)
    start_cmd.add_argument("--db", type=Path)
    start_cmd.add_argument("--now", help="override the current time (ISO 8601), e.g. to replay a past day")
    stop_cmd = sub.add_parser("stop", help="cancel the Run in progress")
    stop_cmd.add_argument("--db", type=Path)
    review_cmd = sub.add_parser("review", help="serve the review page of a Run whose emails are already prepared")
    review_cmd.add_argument("run_dir", type=Path)
    review_cmd.add_argument("--port", type=int, default=0, help="review page port on 127.0.0.1 (default: free)")
    review_cmd.add_argument("--config", type=Path)
    review_cmd.add_argument("--db", type=Path)
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
        if args.command is None:
            if ask is None and not (sys.stdin.isatty() and sys.stdout.isatty()):
                parser.print_help()
                return 2
            return _menu(launcher or SystemLauncher(), gmail, ask)
        if args.command == "config":
            print(json.dumps(load_config(args.config).as_dict(), indent=2))
            return 0
        if args.command == "gmail-auth":
            return _gmail_auth(args)
        if args.command == "start":
            return _start(args, launcher or SystemLauncher())
        if args.command == "stop":
            return stop_run(args.db or DEFAULT_DB, launcher or SystemLauncher())
        if args.command == "pick-contacts":
            return _pick_contacts(args)
        if args.command == "record-bounces":
            return _record_bounces(args)
        if args.command == "prepare":
            return _prepare(args, launcher or SystemLauncher(), gmail)
        if args.command == "send":
            return _send(args, gmail)
        if args.command == "review":
            return _review(args, launcher or SystemLauncher(), gmail)
        return _shortlist(args)
    except (OSError, sqlite3.Error) as e:
        print(f"outreach: error: cannot read or write files: {e}", file=sys.stderr)
        return 1
    except UserError as e:
        print(f"outreach: error: {e}", file=sys.stderr)
        return 2


SETUP_WIZARD = Path("scripts/setup-wizard.sh")


def _menu(launcher: Launcher, gmail: Gmail | None, ask: Ask | None) -> int:
    """`outreach` with no command: the arrow-key menu, shown again after each action until Quit."""
    from outreach.menu import ask_questionary

    def build() -> Menu:
        config = load_config(None)
        return build_menu(
            DEFAULT_DB, config, config_file=CONFIG_FILE, data_dir=Path("data"),
            now=datetime.now(ZoneInfo(config.timezone)), is_alive=launcher.is_alive,
        )

    def act(item: Item) -> None:
        if item.key == SETUP:
            launcher.run_wizard(SETUP_WIZARD)
        else:
            main(list(item.argv), launcher, gmail=gmail)

    return menu_loop(build, ask or ask_questionary, act)


def _now(args: argparse.Namespace, zone: ZoneInfo) -> datetime:
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(zone)
    return now if now.tzinfo else now.replace(tzinfo=zone)


def _require_run(store: Store, run_dir: Path) -> sqlite3.Row:
    run = store.find_run(run_dir)
    if run is None:
        raise UserError(f"{run_dir}: no Shortlist recorded for this Run; run `outreach shortlist` first")
    return run


def _start(args: argparse.Namespace, launcher: Launcher) -> int:
    config = load_config(args.config)
    options = {"db": _path(args.db), "config": _path(args.config), "now": args.now}
    return start_run(
        args.db or DEFAULT_DB, config, launcher, data_dir=args.data_dir, now=_now(args, ZoneInfo(config.timezone)),
        contacts=args.contacts, dry_run=args.dry_run, options=options,
    )


def _path(path: Path | None) -> str | None:
    return str(path) if path else None


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
        if not shortlisted:
            mark_reviewed(store, run_id, "no Today's Applications: nothing to review")
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
            variant = variant_for(store.group_applications(group["id"])[0]["title"])
            out_groups.append({
                "company": group["name"],
                "rank": group["rank"],
                "picked_of_cap": f"{len(picks.picked)} of {cap}",
                "resume_variant": variant,
                "resume_file": str(resume_path(config, variant)),
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


def _prepare(args: argparse.Namespace, launcher: Launcher, gmail: Gmail | None) -> int:
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
    if config.test_recipient:
        print(f"LIVE TEST MODE: every email goes to a plus-address of {config.test_recipient}, "
              "not to the Contact's real address")
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
    return _serve_review(db_path, config, run_id, args.port, launcher, gmail)


def _review(args: argparse.Namespace, launcher: Launcher, gmail: Gmail | None) -> int:
    config = load_config(args.config)
    db_path = args.db or DEFAULT_DB
    with closing(Store(db_path)) as store:
        run = _require_run(store, args.run_dir)
        if not store.run_has_emails(run["id"]):
            raise UserError(f"{args.run_dir}: no Outreach Emails prepared for this Run; run `outreach prepare` first")
        run_id = int(run["id"])
    return _serve_review(db_path, config, run_id, args.port, launcher, gmail)


def _serve_review(
    db_path: Path, config: Config, run_id: int, port: int, launcher: Launcher, gmail: Gmail | None,
) -> int:
    from outreach.review import serve

    with closing(Store(db_path)) as store:
        waiting = sum(1 for e in store.run_emails(run_id) if e["status"] == "written")
        dry_run = bool(store.get_run(run_id)["dry_run"])
    if not dry_run:
        gmail = gmail or _real_gmail(config)  # edits on the page update the Gmail drafts

    def ready(url: str) -> None:
        launcher.open_url(url)
        launcher.notify("Outreach review ready", f"{waiting} email(s) to review at {url}")

    confirmed = serve(db_path, config, run_id, port=port, gmail=gmail, on_ready=ready)
    code = _send_confirmed(db_path, config, run_id, gmail) if confirmed and not dry_run else 0
    with closing(Store(db_path)) as store, store.transaction():
        mark_reviewed(store, run_id)
    return code


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
