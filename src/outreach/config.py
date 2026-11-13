"""Config: one TOML file. A missing file means defaults."""
from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from outreach.errors import UserError

DEFAULT_PATH = Path("outreach.toml")


@dataclass(frozen=True)
class Config:
    contacts_per_company: int = 4
    shortlist_size: int = 10
    cooldown_days: int = 30
    timezone: str = "America/Los_Angeles"
    resume_dir: str = "resumes"
    sender: str = ""
    send_pause_seconds: float = 30.0
    model: str = "sonnet"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_MINIMUMS = {"contacts_per_company": 1, "shortlist_size": 1, "cooldown_days": 0}


def _check(name: str, value: Any, file: str) -> Any:
    def bad(problem: str) -> UserError:
        return UserError(f"{file}: {name}: {problem}, got {value!r}")

    if name in _MINIMUMS:
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad("must be a whole number")
        if value < _MINIMUMS[name]:
            raise bad(f"must be at least {_MINIMUMS[name]}")
    elif name == "send_pause_seconds":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise bad("must be a number")
        if value < 0:
            raise bad("must not be negative")
        return float(value)
    else:
        if not isinstance(value, str):
            raise bad("must be a string")
        if name in ("timezone", "model") and not value.strip():
            raise bad("must not be empty")
        if name == "timezone":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise bad("is not a known timezone name (like America/Los_Angeles)") from None
    return value


def load_config(path: Path | None) -> Config:
    path = path or DEFAULT_PATH
    if not path.exists():
        return Config()
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise UserError(f"{path.name}: not valid TOML ({e})") from None
    known = {f.name for f in fields(Config)}
    for name in raw:
        if name not in known:
            raise UserError(f"{path.name}: {name}: unknown setting (known: {', '.join(sorted(known))})")
    return Config(**{name: _check(name, value, path.name) for name, value in raw.items()})
