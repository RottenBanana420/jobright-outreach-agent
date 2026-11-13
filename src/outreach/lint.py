"""Email linter: an Outreach Email in, findings out.

Findings show on the review page and do not block Approval, except `recipients`, a hard error.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from outreach.shortlist import company_key

MIN_WORDS = 80
MAX_WORDS = 140
_APPLIED = re.compile(r"\bapplied\b|\bmy applications?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    hard: bool = False


def _domain_matches(address: str, company: str) -> bool:
    """True when some label of the address domain (not the TLD) contains, or is part of, the company name."""
    labels = address.rpartition("@")[2].lower().split(".")[:-1]
    compact = "".join(company_key(company).split())
    for label in labels:
        word = "".join(c for c in label if c.isalnum())
        if word and compact and (compact in word or (len(word) >= 3 and word in compact)):
            return True
    return False


def run_humanizer(script: str, body: str) -> list[Finding] | None:
    """Run the optional work-humanizer check; None means it is unavailable and was skipped.

    Contract (ours, since the check is external): `python <script>` gets the body on stdin and prints
    one finding per line on stdout. A non-zero exit with output still counts; a crash with no output
    or a script that cannot start means skipped.
    """
    if not script or not Path(script).expanduser().is_file():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(script).expanduser())],
            input=body, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines and proc.returncode != 0:
        return None
    return [Finding("humanizer", f"work-humanizer: {ln}") for ln in lines]


def lint_email(
    *, subject: str, body: str, recipients: list[str], address: str, roles: list[str], company: str,
) -> list[Finding]:
    findings: list[Finding] = []
    if len(recipients) != 1:
        findings.append(Finding(
            "recipients", f"{len(recipients)} recipients; exactly one is allowed, with no CC or BCC", hard=True,
        ))
    words = len(body.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        findings.append(Finding("word_count", f"{words} words; aim for {MIN_WORDS} to {MAX_WORDS}"))
    for role in roles:
        if role.casefold() not in body.casefold():
            findings.append(Finding("role_title", f"the exact role title {role!r} is not in the body"))
    if not _APPLIED.search(body):
        findings.append(Finding("applied", "the body does not say you already applied"))
    if not _domain_matches(address, company):
        findings.append(Finding("domain", f"address domain {address.rpartition('@')[2]!r} differs from {company!r}"))
    return findings
