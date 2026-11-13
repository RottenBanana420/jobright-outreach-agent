"""`outreach start` / `stop`: the Run's lifecycle, driven through the CLI with a fake process launcher."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from conftest import NOW, TODAY, Env, Result, prepared_inputs, write_for_all
from fakes import FakeLauncher
from outreach.cli import main


def _cli(env: Env, launcher: FakeLauncher, command: str, *extra: str) -> Result:
    argv = [command, "--db", str(env.db), *extra]
    if command == "start":
        argv += ["--data-dir", str(env.root / "data"), "--config", str(env.config), "--now", NOW]
    try:
        code = main(argv, launcher=launcher)
    except SystemExit as e:  # argparse errors
        code = int(e.code or 0)
    cap = env.capsys.readouterr()
    return Result(code, cap.out, cap.err)


def test_start_keeps_the_mac_awake_for_the_run_and_releases_it_when_claude_ends(env: Env) -> None:
    launcher = FakeLauncher()

    result = _cli(env, launcher, "start")

    assert result.code == 0
    assert launcher.awake_while_claude_ran == [True]
    assert launcher.awake == set()
    [run] = env.query("SELECT run_dir, run_date, status FROM runs")
    assert run["run_date"] == TODAY
    assert run["run_dir"].endswith(f"data/{TODAY}")
    assert run["status"] == "ended"


def _state(env: Env) -> dict[str, Any]:
    return json.loads((env.run_dir / "run_state.json").read_text())  # type: ignore[no-any-return]


def test_start_hands_claude_the_run_command_the_run_folder_and_this_runs_options(env: Env) -> None:
    env.write_config('model = "haiku"\n')
    launcher = FakeLauncher()

    result = _cli(env, launcher, "start", "--contacts", "2", "--dry-run")

    assert result.code == 0
    assert launcher.prompts == [f"/outreach-run {env.run_dir}"]
    assert launcher.models == ["haiku"]
    state = _state(env)
    assert state["run_dir"] == str(env.run_dir)
    assert state["dry_run"] is True
    assert state["contacts"] == 2
    assert state["resumed"] is False
    assert state["steps_done"] == []
    assert state["db"] == str(env.db)
    assert env.query("SELECT dry_run FROM runs")[0]["dry_run"] == 1


def test_start_refuses_a_contact_cap_below_one_before_launching_anything(env: Env) -> None:
    launcher = FakeLauncher()

    result = _cli(env, launcher, "start", "--contacts", "0")

    assert result.code == 2
    assert "--contacts" in result.err
    assert launcher.prompts == []
    assert launcher.awake == set()


def test_a_failed_claude_marks_the_run_failed_and_still_releases_the_mac(env: Env) -> None:
    launcher = FakeLauncher()
    launcher.claude_exit = 3

    result = _cli(env, launcher, "start")

    assert result.code == 1
    assert "start" in result.err  # tells the user how to resume
    assert launcher.awake == set()
    assert env.query("SELECT status FROM runs")[0]["status"] == "failed"
    assert env.query("SELECT owner_pid FROM runs")[0]["owner_pid"] is None


def test_stop_ends_claude_and_caffeinate_and_marks_the_run_stopped(env: Env) -> None:
    launcher = FakeLauncher()
    stopped: list[Result] = []
    launcher.claude_hook = lambda: stopped.append(_cli(env, launcher, "stop"))

    result = _cli(env, launcher, "start")

    [stop] = stopped
    assert stop.code == 0
    assert "stopped" in stop.out
    assert len(launcher.terminated) >= 2  # Claude and caffeinate
    assert launcher.awake == set()
    assert result.code == 0  # start returns normally once its Claude is gone
    assert env.query("SELECT status FROM runs")[0]["status"] == "stopped"
    kinds = [(e["from_status"], e["to_status"]) for e in env.query("SELECT * FROM events WHERE entity = 'runs'")]
    assert ("running", "stopped") in kinds


def test_stop_without_a_run_in_progress_says_so(env: Env) -> None:
    result = _cli(env, FakeLauncher(), "stop")

    assert result.code == 2
    assert "no Run is in progress" in result.err


def test_a_second_start_while_a_run_is_in_progress_is_refused(env: Env) -> None:
    launcher = FakeLauncher()
    second: list[Result] = []
    launcher.claude_hook = lambda: second.append(_cli(env, launcher, "start"))

    assert _cli(env, launcher, "start").code == 0

    assert second[0].code == 2
    assert "already in progress" in second[0].err
    assert len(launcher.prompts) == 1


def test_a_stopped_run_resumes_in_the_same_run_and_is_told_which_steps_are_done(env: Env) -> None:
    ids = prepared_inputs(env, contacts=2)  # shortlist and pick-contacts have run
    launcher = FakeLauncher()
    launcher.claude_hook = lambda: _cli(env, launcher, "stop")
    assert _cli(env, launcher, "start").code == 0
    run_before = env.query("SELECT id FROM runs")[0]["id"]

    launcher.claude_hook = None
    result = _cli(env, launcher, "start")

    assert result.code == 0
    assert env.query("SELECT id FROM runs")[0]["id"] == run_before
    state = _state(env)
    assert state["resumed"] is True
    assert state["steps_done"] == ["applications", "shortlist", "contact_candidates", "picked_contacts"]
    assert len(launcher.prompts) == 2
    assert env.query("SELECT status FROM runs")[0]["status"] == "ended"
    assert ids  # contacts picked before the stop are still there
    assert len(env.query("SELECT id FROM contacts")) == 2


def test_a_resumed_run_that_already_has_its_emails_is_told_not_to_write_them_again(env: Env) -> None:
    ids = prepared_inputs(env, contacts=2)
    write_for_all(env, ids)
    assert env.prepare().code == 0
    emails_before = [tuple(r) for r in env.query("SELECT id, body, content_hash, status FROM outreach_emails")]

    assert _cli(env, FakeLauncher(), "start").code == 0

    assert "prepared" in _state(env)["steps_done"]
    assert [tuple(r) for r in env.query("SELECT id, body, content_hash, status FROM outreach_emails")] == emails_before


def test_a_run_whose_start_process_died_is_marked_failed_and_the_next_start_resumes_it(env: Env) -> None:
    launcher = FakeLauncher()
    assert _cli(env, launcher, "start").code == 0
    con = sqlite3.connect(env.db)  # as if `outreach start` was killed without cleaning up
    con.execute("UPDATE runs SET status = 'running', owner_pid = 999999, caffeinate_pid = 4242, claude_pid = 4243")
    con.commit()
    con.close()

    result = _cli(env, launcher, "start")

    assert result.code == 0
    assert 4242 in launcher.terminated  # the orphaned caffeinate is released
    assert _state(env)["resumed"] is True
    statuses = [e["to_status"] for e in env.query("SELECT * FROM events WHERE entity = 'runs' ORDER BY id")]
    assert "failed" in statuses
    assert statuses[-1] == "ended"
