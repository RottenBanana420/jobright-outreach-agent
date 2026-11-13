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


def load_applications(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise UserError(f"{path.name}: file not found in {path.parent}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise UserError(f"{path.name}: not valid JSON ({e})") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("applications"), list):
        raise _fail("applications", "must be a list at the top level of the file")
    apps = [_application(item, f"applications[{i}]") for i, item in enumerate(raw["applications"])]
    seen: set[str] = set()
    for i, app in enumerate(apps):
        if app["jobright_id"] in seen:
            raise _fail(f"applications[{i}].jobright_id", f"duplicate id {app['jobright_id']!r}")
        seen.add(app["jobright_id"])
    return apps
