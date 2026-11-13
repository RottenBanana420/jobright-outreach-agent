"""Resume mapper: role title in, Resume Variant out, plus the checks before a file is attached."""
from __future__ import annotations

import re
from pathlib import Path

from outreach.config import Config

MAX_BYTES = 25 * 1024 * 1024

# docs/design.md, in order; the first rule that matches the title wins. Short words match whole words only
# ("ML" must not match "HTML"). Titles are matched with hyphens and underscores read as spaces.
_RULES: list[tuple[str, list[str]]] = [
    ("forward_deployed", [r"forward\s+deployed"]),
    ("ml", [r"machine\s+learning", r"\bml\b", r"\bmlops\b", r"data\s+scientist"]),
    ("ai", [r"\bai\b", r"\bllm\b", r"\bgenai\b", r"applied\s+ai"]),
    ("data", [r"data\s+engineer", r"data\s+platform", r"analytics\s+engineer"]),
]
DEFAULT_VARIANT = "software"


def variant_for(title: str) -> str:
    spaced = re.sub(r"[-_]", " ", title)
    for variant, patterns in _RULES:
        if any(re.search(p, spaced, re.IGNORECASE) for p in patterns):
            return variant
    return DEFAULT_VARIANT


def resume_path(config: Config, variant: str) -> Path:
    return Path(config.resume_dir).expanduser() / config.resume_files[variant]


_STARTXREF = re.compile(rb"startxref\s+(\d+)\s*%%EOF\s*\Z")
_XREF_TARGET = re.compile(rb"\s*(xref\b|\d+\s+\d+\s+obj\b)")


def _unreadable(name: str, why: str) -> str:
    return f"resume {name} is not a readable PDF ({why})"


def check_resume(path: Path) -> str | None:
    """Why this file cannot be attached, or None if it can: exists, opens as a PDF, under 25 MB.

    "Opens as a PDF" is checked with the standard library only: the `%PDF-` header, then the end of the
    file must read `startxref <offset> %%EOF`, and that offset must land on the cross-reference table
    (`xref`) or on a cross-reference stream object (`N G obj`). That is what a viewer needs to open
    the file, and what a truncated download or a cut-off copy is missing.
    """
    name = path.name
    if not path.is_file():
        return f"resume {name} does not exist in {path.parent}"
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            return f"resume {name} is {size / 1024 / 1024:.1f} MB, over the 25 MB limit"
        with path.open("rb") as f:
            head = f.read(5)
            f.seek(max(0, size - 1024))
            tail = f.read()
            if head != b"%PDF-":
                return f"resume {name} is not a PDF (no PDF header)"
            if b"%%EOF" not in tail:
                return _unreadable(name, "truncated: no end-of-file marker")
            found = _STARTXREF.search(tail)
            if found is None:
                return _unreadable(name, "no startxref pointer before the end-of-file marker")
            offset = int(found.group(1))
            if offset >= size:
                return _unreadable(name, "the cross-reference pointer is past the end of the file")
            f.seek(offset)
            if _XREF_TARGET.match(f.read(64)) is None:
                return _unreadable(name, "the cross-reference pointer does not lead to a cross-reference table")
    except OSError as e:
        return f"resume {name} cannot be read ({e.strerror or e})"
    return None
