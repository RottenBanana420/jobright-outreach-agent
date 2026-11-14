"""The review page's part in a Run: the "ready" notification, opening the page, ending the Run once the review is
done, and `outreach review` for a resumed Run."""
from __future__ import annotations

import re
import sqlite3
import time
import urllib.request
from threading import Thread

import pytest

from conftest import NOW, Env, prepared_inputs, write_for_all
from fakes import FakeLauncher
from outreach.cli import main
from test_review import Page


def _wait_for_page(launcher: FakeLauncher) -> Page:
    """Wait for the "review page is ready" notification, then open the page it points at."""
    for _ in range(200):
        if launcher.notifications:
            break
        time.sleep(0.05)
    assert launcher.notifications, "no notification"
    url = re.search(r"http://127\.0\.0\.1:\d+", launcher.notifications[0][1])
    assert url, launcher.notifications
    with urllib.request.urlopen(url.group(0) + "/", timeout=5) as r:
        token = re.search(r'name="token" value="([^"]+)"', r.read().decode())
    assert token
    return Page(url.group(0), token.group(1), url.group(0).removeprefix("http://"))


def _serve(argv: list[str], launcher: FakeLauncher) -> tuple[Thread, list[int]]:
    outcome: list[int] = []
    thread = Thread(target=lambda: outcome.append(main(argv, launcher=launcher)), daemon=True)
    thread.start()
    return thread, outcome


def test_prepare_posts_a_mac_notification_when_the_review_page_is_ready(env: Env) -> None:
    write_for_all(env, prepared_inputs(env, contacts=1))
    launcher = FakeLauncher()
    argv = ["prepare", str(env.run_dir), "--dry-run", "--config", str(env.config), "--db", str(env.db), "--now", NOW]
    thread, outcome = _serve(argv, launcher)

    page = _wait_for_page(launcher)

    title, message = launcher.notifications[0]
    assert "review" in title.lower()
    assert "1 email" in message
    assert page.post("/emails/1/approve").status == 200
    assert page.post("/confirm-send", {"emails": "1", "recipients": "1"}).status == 200
    thread.join(timeout=5)
    assert outcome == [0]


def test_prepare_opens_the_review_page_in_the_browser(env: Env) -> None:
    write_for_all(env, prepared_inputs(env, contacts=1))
    launcher = FakeLauncher()
    argv = ["prepare", str(env.run_dir), "--dry-run", "--config", str(env.config), "--db", str(env.db), "--now", NOW]
    thread, outcome = _serve(argv, launcher)

    page = _wait_for_page(launcher)

    assert launcher.opened == [page.base + "/"]
    assert page.post("/emails/1/approve").status == 200
    assert page.post("/confirm-send", {"emails": "1", "recipients": "1"}).status == 200
    thread.join(timeout=5)
    assert outcome == [0]


def _review_in_claude(env: Env, launcher: FakeLauncher) -> None:
    """What Claude does at the end of a Run: run `prepare`, and the user confirms on the review page."""
    argv = ["prepare", str(env.run_dir), "--dry-run", "--config", str(env.config), "--db", str(env.db), "--now", NOW]
    thread, outcome = _serve(argv, launcher)
    page = _wait_for_page(launcher)
    assert page.post("/emails/1/approve").status == 200
    assert page.post("/confirm-send", {"emails": "1", "recipients": "1"}).status == 200
    thread.join(timeout=5)
    assert outcome == [0]


def _start(env: Env, launcher: FakeLauncher) -> int:
    return main(["start", "--dry-run", "--db", str(env.db), "--data-dir", str(env.root / "data"),
                 "--config", str(env.config), "--now", NOW], launcher=launcher)


def test_a_run_ends_by_itself_once_the_review_is_done(env: Env) -> None:
    write_for_all(env, prepared_inputs(env, contacts=1))
    launcher = FakeLauncher()
    launcher.claude_hook = lambda: _review_in_claude(env, launcher)

    assert _start(env, launcher) == 0

    assert launcher.ended_after_review == [True]
    statuses = [e["to_status"] for e in env.query("SELECT to_status FROM events WHERE entity = 'runs' ORDER BY id")]
    assert statuses[-2:] == ["reviewed", "ended"]


def test_a_resumed_run_is_not_ended_because_an_earlier_review_was_done(env: Env) -> None:
    write_for_all(env, prepared_inputs(env, contacts=1))
    launcher = FakeLauncher()
    launcher.claude_hook = lambda: _review_in_claude(env, launcher)
    assert _start(env, launcher) == 0

    launcher.claude_hook = None
    assert _start(env, launcher) == 0

    assert launcher.ended_after_review == [True, False]
    assert env.query("SELECT status FROM runs")[0]["status"] == "ended"


def test_a_review_outside_a_live_run_does_not_mark_the_run(env: Env) -> None:
    write_for_all(env, prepared_inputs(env, contacts=1))

    _review_in_claude(env, FakeLauncher())  # `outreach prepare` typed by hand, no `outreach start`

    assert env.query("SELECT status FROM runs")[0]["status"] != "reviewed"


def test_review_serves_an_already_prepared_run_without_writing_its_emails_again(env: Env) -> None:
    write_for_all(env, prepared_inputs(env, contacts=2))
    assert env.prepare("--dry-run").code == 0
    con = sqlite3.connect(env.db)  # the user edited an email on an earlier visit to the page
    con.execute("UPDATE outreach_emails SET subject = 'Edited by the user' WHERE id = 1")
    con.commit()
    con.close()
    launcher = FakeLauncher()
    argv = ["review", str(env.run_dir), "--config", str(env.config), "--db", str(env.db)]
    thread, outcome = _serve(argv, launcher)

    page = _wait_for_page(launcher)

    assert "Edited by the user" in page.get().body
    assert page.post("/emails/2/approve").status == 200
    assert page.post("/confirm-send", {"emails": "1", "recipients": "1"}).status == 200
    thread.join(timeout=5)
    assert outcome == [0]
    assert env.query("SELECT subject FROM outreach_emails WHERE id = 1")[0]["subject"] == "Edited by the user"


def test_review_needs_a_prepared_run(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    prepared_inputs(env, contacts=1)  # shortlisted and picked, but no emails prepared

    code = main(["review", str(env.run_dir), "--config", str(env.config), "--db", str(env.db)])

    assert code == 2
    assert "prepare" in capsys.readouterr().err
