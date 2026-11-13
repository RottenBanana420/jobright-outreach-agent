"""Resume mapper: role title in, Resume Variant out, plus the checks before a file is attached."""
from __future__ import annotations

import re
from pathlib import Path

from outreach.config import Config

MAX_BYTES = 25 * 1024 * 1024

# docs/design.md, in order; the first rule that matches the title wins. Short words match whole words only
# ("ML" must not match "HTML").
_RULES: list[tuple[str, list[str]]] = [
    ("forward_deployed", [r"forward\s+deployed"]),
    ("ml", [r"machine\s+learning", r"\bml\b", r"data\s+scientist"]),
    ("ai", [r"\bai\b", r"\bllm\b", r"\bgenai\b", r"applied\s+ai"]),
    ("data", [r"data\s+engineer", r"data\s+platform", r"analytics\s+engineer"]),
]
DEFAULT_VARIANT = "software"


def variant_for(title: str) -> str:
    for variant, patterns in _RULES:
        if any(re.search(p, title, re.IGNORECASE) for p in patterns):
            return variant
    return DEFAULT_VARIANT


def resume_path(config: Config, variant: str) -> Path:
    return Path(config.resume_dir).expanduser() / config.resume_files[variant]


def check_resume(path: Path) -> str | None:
    """Why this file cannot be attached, or None if it can: exists, readable PDF, under 25 MB."""
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
    except OSError as e:
        return f"resume {name} cannot be read ({e.strerror or e})"
    if head != b"%PDF-":
        return f"resume {name} is not a PDF (no PDF header)"
    if b"%%EOF" not in tail:
        return f"resume {name} is not a readable PDF (truncated: no end-of-file marker)"
    return None
