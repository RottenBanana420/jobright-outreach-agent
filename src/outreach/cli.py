from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from outreach.config import load_config
from outreach.errors import UserError
from outreach.schema import load_applications
from outreach.store import DEFAULT_PATH as DEFAULT_DB
from outreach.store import Store
from outreach.shortlist import build_groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="outreach")
    sub = parser.add_subparsers(dest="command", required=True)
    shortlist_cmd = sub.add_parser("shortlist", help="rank Today's Applications into a Shortlist")
    shortlist_cmd.add_argument("run_dir", type=Path)
    shortlist_cmd.add_argument("--config", type=Path)
    shortlist_cmd.add_argument("--db", type=Path)
    shortlist_cmd.add_argument("--now", help="override the current time (ISO 8601), e.g. to replay a past day")
    config_cmd = sub.add_parser("config", help="print the effective settings")
    config_cmd.add_argument("--config", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "config":
            print(json.dumps(load_config(args.config).as_dict(), indent=2))
            return 0
        return _shortlist(args)
    except (OSError, sqlite3.Error) as e:
        print(f"outreach: error: cannot read or write files: {e}", file=sys.stderr)
        return 1
    except UserError as e:
        print(f"outreach: error: {e}", file=sys.stderr)
        return 2


def _shortlist(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apps = load_applications(args.run_dir / "applications.json")
    zone = ZoneInfo(config.timezone)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(zone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    today = now.astimezone(zone).date().isoformat()
    kept = [a for a in apps if a["status"] == "Applied" and a["applied_on"] == today]
    groups = build_groups(kept, config.shortlist_size)
    shortlisted = [g for g in groups if g["shortlisted"]]
    with closing(Store(args.db or DEFAULT_DB)) as store, store.transaction():
        run_id = store.get_or_create_run(args.run_dir, today)
        store.record_shortlist(run_id, groups)
    (args.run_dir / "shortlist.json").write_text(json.dumps({"run_date": today, "total_groups": len(groups), "groups": shortlisted}, indent=2))
    return 0
