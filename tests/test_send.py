"""Sending: approved emails go out one at a time through Gmail, and every failure leaves a safe, resumable state."""
from __future__ import annotations

import re
import socket
import time
import urllib.request
from threading import Thread

import pytest

from conftest import NOW, Env, prepared_inputs, write_for_all
from outreach.cli import main
from outreach.gmail import GmailAuthError, GmailError, GmailQuotaError, GmailUncertain
from test_gmail_drafts import attachments
from test_review import Page, email_rows, prepared, serving, statuses


def confirmed(env: Env, contacts: int = 2, approve: tuple[int, ...] | None = None) -> None:
    """A prepared Run whose chosen emails (default all) are approved and whose totals were confirmed."""
    prepared(env, contacts=contacts)
    chosen = approve if approve is not None else tuple(range(1, contacts + 1))
    with serving(env) as page:
        for email_id in chosen:
            assert page.post(f"/emails/{email_id}/approve").status in (200, 303)
        reply = page.post("/confirm-send", {"emails": str(len(chosen)), "recipients": str(len(chosen))})
        assert reply.status == 200, reply.body


def sent_to(env: Env) -> list[str]:
    return sorted(str(m["To"]) for m in env.gmail.sent_messages())


def test_only_approved_emails_are_sent_and_each_result_is_recorded(env: Env) -> None:
    confirmed(env, contacts=3, approve=(1, 3))

    result = env.send()

    assert result.code == 0, result.err
    assert sent_to(env) == ["eng.0@acme.test", "eng.2@acme.test"]
    rows = email_rows(env)
    assert [r["status"] for r in rows] == ["sent", "written", "sent"]
    assert rows[0]["gmail_message_id"] and rows[0]["gmail_message_id"] != rows[2]["gmail_message_id"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT.*", rows[0]["sent_at"])
    assert [m["Message-ID"] for m in env.gmail.sent_messages()] == [rows[0]["message_id"], rows[2]["message_id"]]
    assert env.query("SELECT 1 FROM events WHERE entity = 'outreach_emails' AND to_status = 'sent'")
    assert "sent 2" in result.out


def test_sent_emails_get_the_outreach_label_and_start_the_contact_cooldown(env: Env) -> None:
    confirmed(env, contacts=1)

    assert env.send().code == 0

    [message] = env.gmail.sent_messages()
    assert env.gmail.labels_of(message) == {"Outreach"}
    assert env.query("SELECT emailed_at FROM contacts")[0]["emailed_at"] is not None


def test_sent_mail_has_one_recipient_and_the_resume(env: Env) -> None:
    confirmed(env, contacts=1)

    assert env.send().code == 0

    [message] = env.gmail.sent_messages()
    assert message["To"] == "eng.0@acme.test" and message["Cc"] is None and message["Bcc"] is None
    assert attachments(message)[0][0] == "Resume_Software_Engineer.pdf"


def test_send_refuses_a_dry_run(env: Env) -> None:
    prepared(env, contacts=1, dry_run=True)
    with serving(env) as page:
        page.post("/emails/1/approve")
        page.post("/confirm-send", {"emails": "1", "recipients": "1"})

    result = env.send()

    assert result.code == 2
    assert "dry run" in result.err.lower()
    assert env.gmail.sent == {}
    assert "send_draft" not in [op for op, _ in env.gmail.calls]


def test_send_refuses_when_nothing_was_confirmed(env: Env) -> None:
    prepared(env, contacts=2)
    with serving(env) as page:
        page.post("/emails/1/approve")  # approved but the final confirmation never happened

    result = env.send()

    assert result.code == 2
    assert "nothing to send" in result.err
    assert env.gmail.sent == {}
    assert statuses(env) == ["approved", "written"]


def test_a_second_send_has_nothing_left_and_sends_nothing_again(env: Env) -> None:
    confirmed(env, contacts=1)
    assert env.send().code == 0

    again = env.send()

    assert again.code == 2
    assert len(env.gmail.sent) == 1


def test_sends_are_one_at_a_time_with_the_configured_pause(env: Env) -> None:
    confirmed(env, contacts=3)
    env.write_config(env.config.read_text().replace("send_pause_seconds = 0", "send_pause_seconds = 0.15"))

    assert env.send().code == 0

    times = env.gmail.send_times
    assert len(times) == 3
    assert min(b - a for a, b in zip(times, times[1:], strict=False)) >= 0.14


def test_a_timeout_after_gmail_accepted_does_not_send_twice(env: Env) -> None:
    confirmed(env, contacts=2)
    env.gmail.fail("send_draft", GmailUncertain("read timed out"), call=1, accepted=True)

    result = env.send()

    assert result.code == 0, result.err
    assert sent_to(env) == ["eng.0@acme.test", "eng.1@acme.test"]
    assert len(env.gmail.calls_to("send_draft")) == 2  # one per email: no retry of the accepted one
    assert statuses(env) == ["sent", "sent"]
    assert len(env.gmail.labels) == 2  # the one found in Sent is labelled too


def test_a_timeout_before_gmail_accepted_is_retried_once(env: Env) -> None:
    confirmed(env, contacts=1)
    env.gmail.fail("send_draft", GmailUncertain("connection reset"), call=1)

    result = env.send()

    assert result.code == 0, result.err
    assert len(env.gmail.sent) == 1
    assert len(env.gmail.calls_to("send_draft")) == 2
    assert len(env.gmail.calls_to("find_sent")) == 1  # Sent was searched before the retry
    assert statuses(env) == ["sent"]


def test_an_email_still_uncertain_after_one_retry_stops_the_batch_and_a_rerun_checks_sent_first(env: Env) -> None:
    confirmed(env, contacts=3)
    env.gmail.fail("send_draft", GmailUncertain("timed out"), call=1)
    env.gmail.fail("send_draft", GmailUncertain("timed out"), call=2)
    env.gmail.fail("find_sent", GmailUncertain("search timed out"), call=2)  # second search; the first finds nothing

    first = env.send()

    assert first.code == 3
    assert statuses(env) == ["uncertain", "ready_to_send", "ready_to_send"]
    assert "uncertain" in first.out and "2 not attempted" in first.out
    assert env.gmail.sent == {}

    second = env.send()

    assert second.code == 0, second.err
    assert sent_to(env) == [f"eng.{i}@acme.test" for i in range(3)]
    assert statuses(env) == ["sent", "sent", "sent"]


def test_a_rerun_finds_an_uncertain_email_already_in_sent_and_does_not_send_it_again(env: Env) -> None:
    confirmed(env, contacts=2)
    env.gmail.fail("send_draft", GmailUncertain("timed out"), call=1, accepted=True)
    env.gmail.fail("find_sent", GmailUncertain("search timed out"), call=1)  # cannot look: stays uncertain

    first = env.send()

    assert first.code == 3
    assert statuses(env) == ["uncertain", "ready_to_send"]

    second = env.send()

    assert second.code == 0, second.err
    assert sent_to(env) == ["eng.0@acme.test", "eng.1@acme.test"]
    assert env.gmail.calls_to("send_draft").count("draft1") == 1


@pytest.mark.parametrize("error", [GmailAuthError("Gmail authorization has expired"), GmailQuotaError("daily limit")])
def test_an_auth_or_quota_error_stops_the_batch_and_a_rerun_sends_only_what_is_left(
    env: Env, error: GmailError,
) -> None:
    confirmed(env, contacts=3)
    env.gmail.fail("send_draft", error, call=2)

    first = env.send()

    assert first.code == 3
    assert str(error) in first.err
    assert sent_to(env) == ["eng.0@acme.test"]
    assert statuses(env) == ["sent", "failed", "ready_to_send"]
    assert "sent 1" in first.out and "2 left" in first.out

    second = env.send()

    assert second.code == 0, second.err
    assert sent_to(env) == [f"eng.{i}@acme.test" for i in range(3)]
    assert env.gmail.calls_to("send_draft").count("draft1") == 1  # the first email was not sent again


def test_a_refused_email_is_reported_and_the_batch_carries_on(env: Env) -> None:
    confirmed(env, contacts=3)
    env.gmail.fail("send_draft", GmailError("Invalid To header"), call=1)

    result = env.send()

    assert result.code == 3
    assert statuses(env) == ["failed", "sent", "sent"]
    assert "Invalid To header" in result.out


def test_a_label_failure_does_not_undo_the_send_and_a_rerun_labels_without_resending(env: Env) -> None:
    confirmed(env, contacts=1)
    env.gmail.fail("add_label", GmailError("label service down"))

    first = env.send()

    assert first.code == 3
    assert statuses(env) == ["sent"]
    assert "label" in first.out
    [message] = env.gmail.sent_messages()
    assert env.gmail.labels_of(message) == set()

    second = env.send()

    assert second.code == 0, second.err
    assert env.gmail.labels_of(message) == {"Outreach"}
    assert len(env.gmail.sent) == 1


def test_the_draft_is_refreshed_from_the_store_just_before_sending(env: Env) -> None:
    """A resume switch that failed half way left one draft with the new attachment; the store says otherwise."""
    prepared(env, contacts=2)
    env.gmail.fail("update_draft", GmailAuthError("expired"), call=2)
    with serving(env) as page:
        assert page.post("/groups/1/resume", {"variant": "ml"}).status == 502
        assert page.post("/emails/1/approve").status in (200, 303)
        assert page.post("/emails/2/approve").status in (200, 303)
        assert page.post("/confirm-send", {"emails": "2", "recipients": "2"}).status == 200

    assert env.send().code == 0

    assert {attachments(m)[0][0] for m in env.gmail.sent_messages()} == {"Resume_Software_Engineer.pdf"}


def test_preparing_again_after_sending_does_not_touch_sent_emails(env: Env) -> None:
    confirmed(env, contacts=1)
    assert env.send().code == 0
    drafts_before = len(env.gmail.calls_to("create_draft"))

    result = env.prepare()

    assert result.code == 0, result.err
    assert statuses(env) == ["sent"]
    assert len(env.gmail.calls_to("create_draft")) == drafts_before
    assert "already" in result.out


def test_confirming_in_prepare_sends_straight_away(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    outcome: list[int] = []
    argv = ["prepare", str(env.run_dir), "--port", str(port), "--config", str(env.config),
            "--db", str(env.db), "--now", NOW]
    thread = Thread(target=lambda: outcome.append(main(argv, gmail=env.gmail)), daemon=True)
    thread.start()
    base, html = f"http://127.0.0.1:{port}", ""
    for _ in range(200):
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as r:
                html = r.read().decode()
            break
        except OSError:
            time.sleep(0.05)
    token = re.search(r'name="token" value="([^"]+)"', html)
    assert token, "review page did not come up"
    page = Page(base, token.group(1), f"127.0.0.1:{port}")
    assert page.post("/emails/1/approve").status == 200
    assert page.post("/confirm-send", {"emails": "1", "recipients": "1"}).status == 200

    thread.join(timeout=10)
    assert outcome == [0]
    assert statuses(env) == ["sent"]
    assert sent_to(env) == ["eng.0@acme.test"]
