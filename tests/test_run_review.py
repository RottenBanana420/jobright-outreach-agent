"""The review page's part in a Run: the "ready" notification, and `outreach review` for a resumed Run."""
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
