from __future__ import annotations

import json

import pytest

from conftest import Env
from outreach.cli import main


def show(env: Env) -> tuple[int, dict[str, object], str]:
    code = main(["config", "--config", str(env.config)])
    cap = env.capsys.readouterr()
    return code, (json.loads(cap.out) if cap.out else {}), cap.err


def test_missing_config_file_means_defaults(env: Env) -> None:
    code, settings, _ = show(env)

    assert code == 0
    assert settings["contacts_per_company"] == 4
    assert settings["shortlist_size"] == 10
    assert settings["cooldown_days"] == 30
    assert settings["timezone"] == "America/Los_Angeles"
    assert settings["model"] == "sonnet"


def test_settings_in_the_file_override_defaults(env: Env) -> None:
    env.write_config(
        'contacts_per_company = 1\nshortlist_size = 5\nsender = "me@example.test"\n'
        'resume_dir = "/tmp/resumes"\nsend_pause_seconds = 12.5\nmodel = "opus"\n'
    )

    code, settings, _ = show(env)

    assert code == 0
    assert settings["contacts_per_company"] == 1
    assert settings["shortlist_size"] == 5
    assert settings["sender"] == "me@example.test"
    assert settings["resume_dir"] == "/tmp/resumes"
    assert settings["send_pause_seconds"] == 12.5
    assert settings["model"] == "opus"
    assert settings["cooldown_days"] == 30  # untouched


@pytest.mark.parametrize(
    ("toml", "setting"),
    [
        ("contacts_per_company = 0\n", "contacts_per_company"),
        ('contacts_per_company = "4"\n', "contacts_per_company"),
        ("shortlist_size = 0\n", "shortlist_size"),
        ("cooldown_days = -1\n", "cooldown_days"),
        ('timezone = "Mars/Olympus"\n', "timezone"),
        ("send_pause_seconds = -3\n", "send_pause_seconds"),
        ("shortlist_sise = 5\n", "shortlist_sise"),  # typo'd key
        ('test_recipient = "me"\n', "test_recipient"),
        ('test_recipient = "me+x@gmail.test"\n', "test_recipient"),  # the plus part is added by the program
        ('test_recipient = "me@gmail.test, you@gmail.test"\n', "test_recipient"),
        ("contacts_per_company = [\n", "outreach.toml"),  # malformed TOML
    ],
)
def test_bad_config_is_rejected_naming_the_setting(env: Env, toml: str, setting: str) -> None:
    env.write_config(toml)

    code, _, err = show(env)

    assert code != 0
    assert setting in err


def test_bad_config_stops_the_shortlist_command_too(env: Env) -> None:
    env.write_config("shortlist_size = 0\n")
    env.write_apps([])

    result = env.shortlist()

    assert result.code != 0
    assert "shortlist_size" in result.err
    assert not (env.run_dir / "shortlist.json").exists()


def test_resume_files_default_to_the_design_table_and_can_be_overridden(env: Env) -> None:
    code, settings, _ = show(env)
    assert code == 0
    assert settings["resume_files"]["ml"] == "Kusai_Hajuri_Resume_ML_Engineer.pdf"  # type: ignore[index]

    env.write_config('[resume_files]\nml = "mine.pdf"\n')
    _, settings, _ = show(env)
    files = settings["resume_files"]
    assert files["ml"] == "mine.pdf"  # type: ignore[index]
    assert files["ai"] == "Kusai_Hajuri_Resume_AI_Engineer.pdf"  # type: ignore[index]


def test_an_unknown_resume_variant_in_the_config_is_named(env: Env) -> None:
    env.write_config('[resume_files]\nrust = "x.pdf"\n')
    code, _, err = show(env)
    assert code == 2
    assert "resume_files.rust" in err

