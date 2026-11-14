"""Config: one TOML file. A missing file means defaults."""
from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from outreach.errors import UserError

DEFAULT_PATH = Path("outreach.toml")


# Resume Variant -> file name inside `resume_dir` (docs/design.md). `[resume_files]` overrides any of them.
DEFAULT_RESUME_FILES = {
    "forward_deployed": "Kusai_Hajuri_Resume_Forward_Deployed_Engineer.pdf",
    "ml": "Kusai_Hajuri_Resume_ML_Engineer.pdf",
    "ai": "Kusai_Hajuri_Resume_AI_Engineer.pdf",
    "data": "Kusai_Hajuri_Resume_Data_Engineer.pdf",
    "software": "Kusai_Hajuri_Resume_Software_Engineer.pdf",
}


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
    humanizer_check: str = ""  # optional path to the work-humanizer check script; empty means skip
    gmail_credentials: str = "credentials.json"  # the Google Cloud OAuth client (Desktop app); gitignored
    gmail_token: str = "data/gmail_token.json"  # written by `outreach gmail-auth`; gitignored
    test_recipient: str = ""  # live-test mode: every email goes to a plus-address of this one address; empty = off
    resume_files: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_RESUME_FILES))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_MINIMUMS = {"contacts_per_company": 1, "shortlist_size": 1, "cooldown_days": 0}


def _is_plain_address(value: str) -> bool:
    local, at, domain = value.partition("@")
    return bool(local and at and "." in domain and not any(c in value for c in "+ ,;<>\t") and "@" not in domain)


def test_address(base: str, contact_id: int) -> str:
    """The address a live-test email goes to: a plus-address of `base`, different for every Contact."""
    local, _, domain = base.partition("@")
    return f"{local}+outreachtest{contact_id}@{domain}".lower()


def is_test_address(address: str) -> bool:
    """True for an address `test_address` made, so sending to it must not start a Contact's cooldown."""
    return "+outreachtest" in address.lower().partition("@")[0]


def _check(name: str, value: Any, file: str) -> Any:
    def bad(problem: str) -> UserError:
        return UserError(f"{file}: {name}: {problem}, got {value!r}")

    if name in _MINIMUMS:
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad("must be a whole number")
        if value < _MINIMUMS[name]:
            raise bad(f"must be at least {_MINIMUMS[name]}")
    elif name == "resume_files":
        if not isinstance(value, dict):
            raise bad("must be a table of variant = file name")
        for variant, file_name in value.items():
            if variant not in DEFAULT_RESUME_FILES:
                raise UserError(f"{file}: resume_files.{variant}: unknown Resume Variant "
                                f"(known: {', '.join(DEFAULT_RESUME_FILES)})")
            if not isinstance(file_name, str) or not file_name.strip():
                raise UserError(f"{file}: resume_files.{variant}: must be a file name, got {file_name!r}")
        return {**DEFAULT_RESUME_FILES, **value}
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
        if name == "test_recipient" and value and not _is_plain_address(value):
            raise bad("must be one plain address like you@gmail.com (no '+', spaces or commas)")
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
