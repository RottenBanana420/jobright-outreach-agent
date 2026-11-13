"""Shortlister: Today's Applications in, ordered Company Groups out."""
from __future__ import annotations

from typing import Any


def _rank_key(app: dict[str, Any]) -> tuple[bool, int]:
    if app["job_closed"]:
        return (True, -(app.get("hidden_score") or -1))
    return (False, -app["match_score"])


def rank_applications(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open jobs by Match Score descending, then closed jobs by hidden score. Stable."""
    return sorted(apps, key=_rank_key)


_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company"}


def company_key(name: str) -> str:
    """Normalized company name: case, punctuation and legal suffixes don't split a group."""
    words = "".join(c if c.isalnum() else " " for c in name.casefold()).split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def build_groups(apps: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Merge ranked Applications into Company Groups; each takes its best member's rank.

    Groups ranked beyond `size` are marked cut (`shortlisted` is False).
    """
    groups: dict[str, dict[str, Any]] = {}
    for app in rank_applications(apps):
        key = company_key(app["company"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "rank": len(groups) + 1,
                "key": key,
                "company": app["company"],
                "best_match_score": app["match_score"],
                "applications": [],
            }
        group["applications"].append(app)
    for group in groups.values():
        group["roles"] = [a["title"] for a in group["applications"]]
        group["shortlisted"] = group["rank"] <= size
    return list(groups.values())
