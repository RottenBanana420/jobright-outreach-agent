"""Schema for the applications JSON file that Claude writes.

Hand-rolled on purpose: the file is small and every error must name the bad field.
Fields outside the schema are dropped, so Jobright's internal ranking and profile
data never reaches the store or the later steps.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from outreach.errors import UserError

_STRING_FIELDS = ["jobright_url", "location", "employment_type", "work_model", "seniority", "salary", "original_url"]
_LIST_FIELDS = ["responsibilities", "required", "preferred", "core_skills"]


def _fail(path: str, problem: str) -> UserError:
    return UserError(f"{path}: {problem}")


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(path, "must be a non-empty string")
    return value.strip()


def _score(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise _fail(path, f"must be an integer from 0 to 100, got {value!r}")
    return value


def _application(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail(path, "must be an object")
    app: dict[str, Any] = {}
    for name in ("jobright_id", "company", "title", "status"):
        app[name] = _text(raw.get(name), f"{path}.{name}")
    applied_on = _text(raw.get("applied_on"), f"{path}.applied_on")
    try:
        date.fromisoformat(applied_on)
    except ValueError:
        raise _fail(f"{path}.applied_on", f"must be a date like 2026-11-12, got {applied_on!r}") from None
    app["applied_on"] = applied_on

    for name in ("job_closed", "agent_applied"):
        value = raw.get(name, False if name == "agent_applied" else None)
        if not isinstance(value, bool):
            raise _fail(f"{path}.{name}", f"must be true or false, got {value!r}")
        app[name] = value

    # A closed job's card hides its score, so only open jobs must have one.
    score = raw.get("match_score")
    app["match_score"] = None if score is None and app["job_closed"] else _score(score, f"{path}.match_score")
    hidden = raw.get("hidden_score")
    app["hidden_score"] = None if hidden is None else _score(hidden, f"{path}.hidden_score")

    for name in _STRING_FIELDS:
        value = raw.get(name)
        if value is not None and not isinstance(value, str):
            raise _fail(f"{path}.{name}", f"must be a string or null, got {value!r}")
        app[name] = value
    for name in _LIST_FIELDS:
        items = raw.get(name, [])
        if not isinstance(items, list):
            raise _fail(f"{path}.{name}", "must be a list of strings")
        for i, item in enumerate(items):
            if not isinstance(item, str):
                raise _fail(f"{path}.{name}[{i}]", f"must be a string, got {item!r}")
        app[name] = items
    return app


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise UserError(f"{path.name}: file not found in {path.parent}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise UserError(f"{path.name}: not valid JSON ({e})") from None


def load_applications(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("applications"), list):
        raise _fail("applications", "must be a list at the top level of the file")
    apps = [_application(item, f"applications[{i}]") for i, item in enumerate(raw["applications"])]
    seen: set[str] = set()
    for i, app in enumerate(apps):
        if app["jobright_id"] in seen:
            raise _fail(f"applications[{i}].jobright_id", f"duplicate id {app['jobright_id']!r}")
        seen.add(app["jobright_id"])
    return apps


GROUPS = ("beyond_network", "school", "previous_company")


def _optional_text(value: Any, path: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _fail(path, f"must be a string or null, got {value!r}")
    return value.strip()


def _candidate(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail(path, "must be an object")
    group = raw.get("group")
    if group not in GROUPS:
        raise _fail(f"{path}.group", f"must be one of {', '.join(GROUPS)}, got {group!r}")
    return {
        "name": _text(raw.get("name"), f"{path}.name"),
        "title": _optional_text(raw.get("title"), f"{path}.title"),
        # A blank company is allowed in the file; the picker leaves such Contacts out.
        "company": _optional_text(raw.get("company"), f"{path}.company"),
        "linkedin_url": _text(raw.get("linkedin_url"), f"{path}.linkedin_url"),
        "group": group,
        "connection": _optional_text(raw.get("connection"), f"{path}.connection"),
    }


def load_contact_candidates(path: Path) -> list[dict[str, Any]]:
    """Read `contact_candidates.json`: one entry per Company Group, each with its Contacts."""
    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("companies"), list):
        raise _fail("companies", "must be a list at the top level of the file")
    companies: list[dict[str, Any]] = []
    for i, item in enumerate(raw["companies"]):
        where = f"companies[{i}]"
        if not isinstance(item, dict):
            raise _fail(where, "must be an object")
        if not isinstance(item.get("contacts"), list):
            raise _fail(f"{where}.contacts", "must be a list")
        companies.append({
            "company": _text(item.get("company"), f"{where}.company"),
            "contacts": [_candidate(c, f"{where}.contacts[{j}]") for j, c in enumerate(item["contacts"])],
        })
    return companies


def _address(value: Any, path: str) -> str:
    address = _text(value, path).lower()
    local, _, domain = address.partition("@")
    if not local or "." not in domain or any(c.isspace() or c in "@<>,;" for c in local + domain):
        raise _fail(path, f"must be an email address, got {address!r}")
    return address


def _contact_id(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(path, f"must be a Contact id from picked_contacts.json, got {value!r}")
    return value


def load_revealed_addresses(path: Path) -> list[dict[str, Any]]:
    """Read `revealed_addresses.json`: one address per picked Contact, by Contact id."""
    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("addresses"), list):
        raise _fail("addresses", "must be a list at the top level of the file")
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, item in enumerate(raw["addresses"]):
        where = f"addresses[{i}]"
        if not isinstance(item, dict):
            raise _fail(where, "must be an object")
        contact_id = _contact_id(item.get("contact_id"), f"{where}.contact_id")
        if contact_id in seen:
            raise _fail(f"{where}.contact_id", f"duplicate Contact id {contact_id}")
        seen.add(contact_id)
        out.append({"contact_id": contact_id, "address": _address(item.get("address"), f"{where}.address")})
    return out


MAX_BODY_CHARS = 10_000
MAX_SUBJECT_CHARS = 200


def subject_problem(subject: str) -> str | None:
    """Why this cannot be an Outreach Email subject (one non-empty line, bounded), or None."""
    if not subject.strip() or "\n" in subject or "\r" in subject or len(subject) > MAX_SUBJECT_CHARS:
        return f"must be one non-empty line of at most {MAX_SUBJECT_CHARS} characters"
    return None


def body_problem(body: str) -> str | None:
    """Why this cannot be an Outreach Email body (non-empty, bounded), or None."""
    if not body.strip() or len(body) > MAX_BODY_CHARS:
        return f"must be non-empty and at most {MAX_BODY_CHARS} characters"
    return None


def _recipient_list(value: Any, path: str, required: bool) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        raise _fail(path, "must be a list with at least one address")
    return [_address(item, f"{path}[{i}]") for i, item in enumerate(value)]


def load_written_emails(path: Path) -> list[dict[str, Any]]:
    """Read `written_emails.json`: one Outreach Email per Contact id.

    `recipients` combines to, cc and bcc so the linter can reject more than one. The text is
    untrusted: it is stored and shown, never interpreted.
    """
    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("emails"), list):
        raise _fail("emails", "must be a list at the top level of the file")
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, item in enumerate(raw["emails"]):
        where = f"emails[{i}]"
        if not isinstance(item, dict):
            raise _fail(where, "must be an object")
        contact_id = _contact_id(item.get("contact_id"), f"{where}.contact_id")
        if contact_id in seen:
            raise _fail(f"{where}.contact_id", f"duplicate Contact id {contact_id}: one Outreach Email per Contact")
        seen.add(contact_id)
        to = _recipient_list(item.get("to"), f"{where}.to", required=True)
        extra = _recipient_list(item.get("cc"), f"{where}.cc", False) + _recipient_list(
            item.get("bcc"), f"{where}.bcc", False)
        subject = _text(item.get("subject"), f"{where}.subject")
        if problem := subject_problem(subject):
            raise _fail(f"{where}.subject", problem)
        body = _text(item.get("body"), f"{where}.body")
        if problem := body_problem(body):
            raise _fail(f"{where}.body", problem)
        out.append({"contact_id": contact_id, "recipients": to + extra, "subject": subject, "body": body})
    return out


def load_bounces(path: Path) -> list[dict[str, str]]:
    """Read `bounces.json`: each bounce has an address, and optionally a date and a note."""
    raw = _read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("bounces"), list):
        raise _fail("bounces", "must be a list at the top level of the file")
    bounces: list[dict[str, str]] = []
    for i, item in enumerate(raw["bounces"]):
        where = f"bounces[{i}]"
        if not isinstance(item, dict):
            raise _fail(where, "must be an object")
        address = _address(item.get("address"), f"{where}.address")
        bounced_at = _optional_text(item.get("bounced_at"), f"{where}.bounced_at")
        if bounced_at:
            try:
                date.fromisoformat(bounced_at)
            except ValueError:
                raise _fail(f"{where}.bounced_at", f"must be a date like 2026-11-12, got {bounced_at!r}") from None
        note = _optional_text(item.get("note"), f"{where}.note")
        bounces.append({"address": address, "bounced_at": bounced_at, "note": note})
    return bounces
