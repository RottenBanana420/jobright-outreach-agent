"""Contact picker: candidates plus history in, picked Contacts plus a shortfall out."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from outreach.shortlist import company_key

# Checked in this order, so "Lead Engineer" is a Hiring Leader and "Recruiting Manager" a Recruiter.
_CATEGORY_KEYWORDS = [
    ("Recruiter", ("recruit", "talent")),
    ("Hiring Leader", ("manager", "head", "director", "lead")),
    ("Engineer", ("engineer", "developer", "architect")),
]
OTHER = "Other Recruiting"


def categorize(title: str) -> str:
    """Contact Category from the title: keywords match the start of a word."""
    words = "".join(c if c.isalnum() else " " for c in title.casefold()).split()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(w.startswith(k) for w in words for k in keywords):
            return category
    return OTHER


def linkedin_key(url: str) -> str:
    """Identity of a person: the profile URL without scheme, "www.", query, fragment, case or trailing slash."""
    rest = url.strip().casefold().split("#")[0].split("?")[0]
    rest = rest.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return rest.rstrip("/")


@dataclass
class Picks:
    picked: list[dict[str, Any]]
    flagged: list[dict[str, Any]]
    eligible: int


_CATEGORY_ORDER = [name for name, _ in _CATEGORY_KEYWORDS] + [OTHER]
_CONNECTIONS = ("school", "previous_company")


def pick_contacts(
    group_key: str,
    candidates: list[dict[str, Any]],
    cap: int,
    *,
    now: datetime,
    cooldown_days: int,
    last_emailed: dict[str, datetime],
    bounced: set[str],
) -> Picks:
    """Eligible, deduped Contacts in priority order, cut at `cap`.

    Eligible means the listed company is the Company Group (blank or different never is).
    The same person (LinkedIn URL) in several Jobright groups is one Contact with all its groups.
    A person whose address bounced is dropped. A person emailed within `cooldown_days` (at any
    company) is flagged for review instead of picked, and does not use up a slot of the cap.
    Priority: school and previous-company Contacts first, then by Contact Category; ties keep file order.
    """
    people: dict[str, dict[str, Any]] = {}
    for c in candidates:
        if not c["company"] or company_key(c["company"]) != group_key:
            continue
        key = linkedin_key(c["linkedin_url"])
        person = people.get(key)
        if person is None:
            people[key] = {**c, "key": key, "category": categorize(c["title"]), "groups": [c["group"]]}
        else:
            if c["group"] not in person["groups"]:
                person["groups"].append(c["group"])
            person["connection"] = person["connection"] or c["connection"]
    ordered = sorted(
        people.values(),
        key=lambda p: (not any(g in _CONNECTIONS for g in p["groups"]), _CATEGORY_ORDER.index(p["category"])),
    )
    ordered = [p for p in ordered if p["key"] not in bounced]
    picked: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    for person in ordered:
        last = last_emailed.get(person["key"])
        if last is not None and now - last < timedelta(days=cooldown_days):
            flagged.append({**person, "cooldown_last_emailed": last.isoformat()})
        else:
            picked.append(person)
    return Picks(picked=picked[:cap], flagged=flagged, eligible=len(picked))
