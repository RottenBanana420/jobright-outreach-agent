"""The arrow-key menu: `outreach` with no command (issue #22)."""
from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from conftest import NOW, TODAY, Env, make_app, resume_config, write_resumes
from fake_gmail import FakeGmail
from fakes import FakeLauncher
from outreach.cli import main
from outreach.config import load_config
from outreach.menu import (
    GMAIL,
    PRACTICE,
    QUIT,
    REVIEW,
    SEND,
    SETTINGS,
    SETUP,
    START,
    STOP,
    Menu,
    build_menu,
)
from outreach.run import mark_reviewed
from outreach.store import Store
from test_review import prepared
from test_send import confirmed


def test_with_no_command_and_no_terminal_the_help_is_printed_and_nothing_waits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([])
    out = capsys.readouterr().out
    assert code == 2
    assert "usage: outreach" in out
    assert "start" in out


# --- the menu model: what shows, and where the pointer starts --------------------------------------

def finish_setup(env: Env, signed_in: str | None = "2026-11-10T17:00:00+00:00") -> None:
    """Everything the setup wizard makes: config, OAuth client, Gmail token (signed in 3 days ago) and resumes."""
    write_resumes(env.root / "resumes")
    if not env.config.exists():
        env.write_config(resume_config(env.root / "resumes"))
    credentials, token = env.root / "credentials.json", env.root / "gmail_token.json"
    credentials.write_text("{}")
    token.write_text(json.dumps({"refresh_token": "r", **({"signed_in_at": signed_in} if signed_in else {})}))
    env.write_config(f'gmail_credentials = "{credentials}"\ngmail_token = "{token}"\n' + env.config.read_text())


def menu(env: Env) -> Menu:
    return build_menu(
        env.db, load_config(env.config), config_file=env.config, data_dir=env.root / "data",
        now=datetime.fromisoformat(NOW), is_alive=FakeLauncher().is_alive,
    )


def test_with_no_run_today_one_enter_starts_it(env: Env) -> None:
    finish_setup(env)

    m = menu(env)

    assert m.title == "Outreach · Thu 12 Nov · today: no Run yet"
    assert m.default == START
    assert m.keys() == [START, PRACTICE, GMAIL, SETTINGS, QUIT]
    assert m.notes == ("Gmail: signed in, day 3 of 7",)


def test_emails_waiting_for_review_put_the_pointer_on_reopening_the_page(env: Env) -> None:
    prepared(env, contacts=2)
    finish_setup(env)

    m = menu(env)

    assert m.title.endswith("today: waiting for your review: 2 emails")
    assert m.default == REVIEW
    assert m.keys() == [START, PRACTICE, REVIEW, GMAIL, SETTINGS, QUIT]
    assert m.item(REVIEW).argv == ("review", str(env.run_dir))


def test_an_interrupted_send_puts_the_pointer_on_finishing_it(env: Env) -> None:
    confirmed(env, contacts=1)
    finish_setup(env)

    m = menu(env)

    assert m.title.endswith("today: send interrupted: 1 email to finish")
    assert m.default == SEND
    assert m.keys() == [START, PRACTICE, SEND, GMAIL, SETTINGS, QUIT]


def test_when_everything_is_sent_the_pointer_rests_on_quit(env: Env) -> None:
    confirmed(env, contacts=1)
    assert env.send().code == 0
    finish_setup(env)

    m = menu(env)

    assert m.title.endswith("today: all sent: 1 email")
    assert m.default == QUIT
    assert REVIEW not in m.keys() and SEND not in m.keys()


@pytest.mark.parametrize(("signed_in", "note"), [
    ("2026-11-07T17:00:00+00:00", "Gmail: day 6 of 7; renew the sign-in soon"),
    ("2026-11-06T17:00:00+00:00", "Gmail: day 7 of 7; renew the sign-in today"),
    ("2026-11-01T17:00:00+00:00", "Gmail: the sign-in has run out; renew it"),
])
def test_from_day_6_of_the_gmail_sign_in_the_pointer_is_on_renewing_it(env: Env, signed_in: str, note: str) -> None:
    finish_setup(env, signed_in=signed_in)

    m = menu(env)

    assert m.default == GMAIL
    assert m.notes == (note,)


def test_a_token_without_a_sign_in_date_shows_signed_in_without_a_countdown(env: Env) -> None:
    finish_setup(env, signed_in=None)

    m = menu(env)

    assert m.notes == ("Gmail: signed in",)
    assert m.default == START


def test_once_the_google_app_is_in_production_the_sign_in_has_no_countdown(env: Env) -> None:
    finish_setup(env, signed_in="2026-11-01T17:00:00+00:00")
    env.write_config('gmail_publishing_status = "production"\n' + env.config.read_text())

    m = menu(env)

    assert m.notes == ("Gmail: signed in",)
    assert m.default == START


def test_incomplete_setup_shows_finish_setup_with_what_is_missing_and_points_at_it(env: Env) -> None:
    write_resumes(env.root / "resumes", skip=("ml",))
    env.write_config(f'gmail_credentials = "{env.root / "none.json"}"\ngmail_token = "{env.root / "none2.json"}"\n'
                     + resume_config(env.root / "resumes"))

    m = menu(env)

    assert m.default == SETUP
    [setup] = [i for i in m.upkeep if i.key == SETUP]
    assert setup.label == "Finish setup (missing: none.json, Gmail sign-in, resume files)"
    assert m.notes == ()


def test_a_missing_config_file_is_part_of_incomplete_setup(env: Env) -> None:
    m = menu(env)

    assert m.default == SETUP
    assert "outreach.toml" in m.upkeep[0].label


def test_a_run_live_in_another_window_offers_stop_but_one_enter_does_not_stop_it(env: Env) -> None:
    prepared(env, contacts=1)
    finish_setup(env)
    with closing(Store(env.db)) as store, store.transaction():
        run = store.find_run(env.run_dir)
        assert run is not None
        store.set_run_processes(run["id"], os.getpid(), None, None)

    m = menu(env)

    assert m.title.endswith("today: a Run is in progress in another window")
    assert m.keys() == [START, PRACTICE, STOP, GMAIL, SETTINGS, QUIT]
    assert m.default == QUIT


def test_addresses_recorded_undelivered_today_are_reported_without_saying_bounced(env: Env) -> None:
    finish_setup(env)
    env.write_bounces([{"address": "gone@acme.test", "bounced_at": "2026-11-11", "note": "no such user"}])
    assert env.record_bounces().code == 0

    m = menu(env)

    assert m.notes[-1] == "1 email undelivered; that address won't be used again"
    assert "bounce" not in " ".join([m.title, *m.notes, *(i.label for i in m.daily + m.upkeep)]).lower()


# --- the loop, through `outreach` with no command ---------------------------------------------------

def test_after_start_returns_the_menu_shows_again_with_the_new_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    shown: list[Menu] = []
    answers = iter([START, QUIT])

    def ask(m: Menu) -> str | None:
        shown.append(m)
        return next(answers)

    launcher = FakeLauncher()
    code = main([], launcher, gmail=FakeGmail(), ask=ask)

    assert code == 0
    assert launcher.prompts and launcher.prompts[0].startswith("/outreach-run data/")
    assert shown[0].title.endswith("today: no Run yet")
    assert shown[1].title.endswith("today: started, not finished; Start picks up where it left off")


def test_ctrl_c_at_the_prompt_quits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    launcher = FakeLauncher()

    assert main([], launcher, ask=lambda m: None) == 0
    assert launcher.prompts == []


# --- the Questionary layer, fed keys through a pipe instead of a terminal ---------------------------

def answer_with(keys: str, m: Menu) -> str | None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from outreach.menu import ask_questionary

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text(keys)
        return ask_questionary(m)


@pytest.mark.parametrize(("keys", "chosen"), [("\r", REVIEW), ("1\r", START), ("\x1b[B\r", GMAIL), ("q", QUIT)])
def test_enter_takes_the_highlighted_item_a_number_picks_another_and_q_quits(
    env: Env, keys: str, chosen: str,
) -> None:
    prepared(env, contacts=1)
    finish_setup(env)

    assert answer_with(keys, menu(env)) == chosen


def test_a_run_that_found_no_applications_today_is_done(env: Env) -> None:
    finish_setup(env)
    env.write_apps([])
    with closing(Store(env.db)) as store, store.transaction():
        run_id = store.get_or_create_run(env.run_dir, TODAY)
        store.set_run_processes(run_id, os.getpid(), None, None)  # as during `outreach start`
    assert env.shortlist().code == 0
    with closing(Store(env.db)) as store, store.transaction():
        store.set_run_processes(run_id, None, None, None)
        store.set_status("runs", run_id, "ended", "review finished")

    m = menu(env)

    assert m.title.endswith("today: done, no applications to follow up")
    assert m.default == QUIT


def test_a_run_that_saved_an_empty_application_list_is_done(env: Env) -> None:
    finish_setup(env)
    env.write_apps([])  # a Run from before `shortlist` ended it: the list was saved, then Claude Code exited
    with closing(Store(env.db)) as store, store.transaction():
        run_id = store.get_or_create_run(env.run_dir, TODAY)
        store.set_status("runs", run_id, "ended", "Claude Code exited with 0")

    m = menu(env)

    assert m.title.endswith("today: done, no applications to follow up")
    assert m.default == QUIT


def test_a_run_that_stopped_on_a_problem_says_what_to_fix_and_points_at_start(env: Env) -> None:
    finish_setup(env)
    with closing(Store(env.db)) as store, store.transaction():
        run_id = store.get_or_create_run(env.run_dir, TODAY)
        store.set_run_processes(run_id, os.getpid(), None, None)  # as during `outreach start`
    assert main(["halt", str(env.run_dir), "--problem", "Signed out of Jobright: sign in to Jobright in Chrome",
                 "--db", str(env.db)]) == 0
    with closing(Store(env.db)) as store, store.transaction():
        store.set_run_processes(run_id, None, None, None)  # as `outreach start` leaves it

    m = menu(env)

    assert m.title.endswith(
        "today: stopped: Signed out of Jobright: sign in to Jobright in Chrome; Start picks up where it left off")
    assert m.default == START


def test_a_run_with_nothing_to_email_is_done(env: Env) -> None:
    finish_setup(env)
    env.write_apps([make_app("Acme", "Backend Engineer", 90)])
    with closing(Store(env.db)) as store, store.transaction():
        run_id = store.get_or_create_run(env.run_dir, TODAY)
        store.set_run_processes(run_id, os.getpid(), None, None)
        mark_reviewed(store, run_id, "no Outreach Emails to review")
        store.set_run_processes(run_id, None, None, None)
        store.set_status("runs", run_id, "ended", "review finished")

    m = menu(env)

    assert m.title.endswith("today: done, nothing to email today")
    assert m.default == QUIT


def test_a_practice_run_whose_review_page_closed_is_done_not_waiting(env: Env) -> None:
    prepared(env, contacts=1, dry_run=True)
    finish_setup(env)
    with closing(Store(env.db)) as store, store.transaction():
        run = store.find_run(env.run_dir)
        assert run is not None
        store.set_run_processes(run["id"], os.getpid(), None, None)  # as during `outreach start`
        mark_reviewed(store, run["id"])
        store.set_run_processes(run["id"], None, None, None)
        store.set_status("runs", run["id"], "ended", "review finished")

    m = menu(env)

    assert m.title.endswith("today: done, nothing sent (practice run)")
    assert REVIEW not in m.keys()
    assert m.default == QUIT


def test_finish_setup_runs_the_setup_wizard_then_shows_the_menu_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    answers = iter([SETUP, QUIT])
    launcher = FakeLauncher()

    assert main([], launcher, ask=lambda m: next(answers)) == 0
    assert launcher.wizards == [Path("scripts/setup-wizard.sh")]
