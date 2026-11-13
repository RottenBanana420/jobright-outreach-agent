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


# Lines the real check prints for every text, flagged or not: totals, not findings.
_STATS_PREFIXES = ("words:",)


def _is_finding(line: str) -> bool:
    """The check prints a "label: hits" line for each thing it flags, then stats lines even for clean text.

    "sentences: ..." is a stats line unless it carries a "(uniform: ...)" note, and "words: N" never counts.
    """
    if line.startswith(_STATS_PREFIXES):
        return False
    if line.startswith("sentences:"):
        return "(uniform" in line
    return True


def run_humanizer(script: str, body: str) -> list[Finding] | None:
    """Run the optional work-humanizer check (its `check.py`); None means it is unavailable and was skipped.

    How the real script behaves: `python check.py` reads the text on stdin, always exits 0 and prints
    "label: hits" lines for what it flagged, followed by stats lines (`sentences: ...`, `words: N`) even
    for clean text. So only finding-shaped lines count (see `_is_finding`). A non-zero exit means the
    script crashed or could not run, so the check is skipped whatever it printed.
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
    if proc.returncode != 0:
        return None
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return [Finding("humanizer", f"work-humanizer: {ln}") for ln in lines if _is_finding(ln)]


def hard_findings(recipients: list[str]) -> list[Finding]:
    """Errors that stop an Outreach Email being prepared at all: it must have exactly one recipient."""
    if len(recipients) == 1:
        return []
    return [Finding(
        "recipients", f"{len(recipients)} recipients; exactly one is allowed, with no CC or BCC", hard=True,
    )]


def lint_email(
    *, subject: str, body: str, recipients: list[str], address: str, roles: list[str], company: str,
) -> list[Finding]:
    findings = hard_findings(recipients)
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
