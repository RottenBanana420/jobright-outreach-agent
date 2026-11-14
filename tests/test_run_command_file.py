"""The Run command is not unit-tested, but the CLI calls it makes must exist."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from outreach.cli import main

COMMAND = Path(__file__).parent.parent / ".claude" / "commands" / "outreach-run.md"
SUBCOMMANDS = sorted(set(re.findall(r"uv run outreach ([a-z][a-z-]*)", COMMAND.read_text())))


def test_the_run_command_calls_the_cli() -> None:
    assert SUBCOMMANDS, "no `uv run outreach <subcommand>` found in the Run command"


@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_every_cli_call_in_the_run_command_is_a_real_subcommand(subcommand: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        main([subcommand, "--help"])

    assert stopped.value.code == 0
