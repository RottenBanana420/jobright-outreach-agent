"""Gmail drafts: `outreach prepare` creates them, the review page keeps them in step. Fake Gmail only."""
from __future__ import annotations

from email.message import EmailMessage

from conftest import NOW, TINY_PDF, Env, make_body, make_email, prepared_inputs, write_for_all
from outreach.cli import main
from outreach.gmail import GmailAuthError, GmailUncertain
from test_review import email_rows, prepared, serving


def attachments(message: EmailMessage) -> list[tuple[str | None, bytes]]:
    return [(a.get_filename(), a.get_content()) for a in message.iter_attachments()]


def prepare_two(env: Env, *extra: str) -> dict[str, int]:
    ids = prepared_inputs(env, contacts=2)
    write_for_all(env, ids)
    result = env.prepare(*extra)
    assert result.code == 0, result.err
    return ids


def test_prepare_creates_one_draft_per_email_with_one_recipient_and_the_resume(env: Env) -> None:
    prepare_two(env)

    drafts = [env.gmail.draft(d) for d in env.gmail.drafts]
    assert sorted(str(m["To"]) for m in drafts) == ["eng.0@acme.test", "eng.1@acme.test"]
    for m in drafts:
        assert m["From"] == "me@example.test"
        assert m["Cc"] is None and m["Bcc"] is None
        assert attachments(m) == [("Resume_Software_Engineer.pdf", TINY_PDF)]
        assert m["Subject"] == "Applied for Backend Engineer"
        assert "I recently applied" in m.get_body(("plain",)).get_content()  # type: ignore[union-attr]


def test_each_draft_carries_its_own_message_id_recorded_in_the_store(env: Env) -> None:
    prepare_two(env)

    rows = env.query("SELECT draft_id, message_id FROM outreach_emails ORDER BY id")
    ids = [env.gmail.draft(r["draft_id"])["Message-ID"] for r in rows]
    assert ids == [r["message_id"] for r in rows]
    assert len(set(ids)) == 2
    assert all(i.startswith("<") and i.endswith("@example.test>") for i in ids)


def test_dry_run_creates_no_drafts_and_never_touches_gmail(env: Env) -> None:
    prepare_two(env, "--dry-run")

    assert env.gmail.calls == []
    assert env.gmail.drafts == {}
    assert all(r["draft_id"] is None for r in env.query("SELECT draft_id FROM outreach_emails"))


def test_without_a_sender_prepare_says_so_and_calls_no_gmail(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    env.write_config(env.config.read_text().replace('sender = "me@example.test"', ""))
    write_for_all(env, ids)

    result = env.prepare()

    assert result.code == 2
    assert "sender" in result.err
    assert env.gmail.calls == []


def drafted(env: Env) -> int:
    return len([r for r in env.query("SELECT draft_id FROM outreach_emails") if r["draft_id"]])


def test_expired_gmail_auth_stops_prepare_with_a_clear_message_and_a_rerun_finishes(env: Env) -> None:
    ids = prepared_inputs(env, contacts=3)
    write_for_all(env, ids)
    env.gmail.fail("create_draft", GmailAuthError("Gmail authorization has expired; run `outreach gmail-auth`"), call=2)

    first = env.prepare()

    assert first.code == 2
    assert "Gmail authorization has expired" in first.err
    assert len(env.gmail.drafts) == 1  # the first draft was made and is remembered
    assert drafted(env) == 1

    second = env.prepare()

    assert second.code == 0, second.err
    assert len(env.gmail.drafts) == 3
    assert drafted(env) == 3


def test_preparing_again_updates_the_drafts_in_place(env: Env) -> None:
    ids = prepare_two(env)
    first = {r["id"]: r["draft_id"] for r in env.query("SELECT id, draft_id FROM outreach_emails")}
    env.write_emails([make_email(i, f"{n.lower().replace(' ', '.')}@acme.test", subject="A new subject")
                      for n, i in ids.items()])

    assert env.prepare().code == 0

    assert {r["id"]: r["draft_id"] for r in env.query("SELECT id, draft_id FROM outreach_emails")} == first
    assert len(env.gmail.drafts) == 2
    assert {str(env.gmail.draft(d)["Subject"]) for d in env.gmail.drafts} == {"A new subject"}


def test_a_draft_timeout_is_reported_and_the_rerun_makes_the_draft(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    env.gmail.fail("create_draft", GmailUncertain("timed out"), accepted=True)

    assert env.prepare().code == 2
    assert drafted(env) == 0

    assert env.prepare().code == 0
    assert drafted(env) == 1


# --- the review page keeps the drafts in step -----------------------------------------------------


def test_editing_on_the_review_page_updates_the_gmail_draft(env: Env) -> None:
    prepared(env, contacts=1)
    before = env.gmail.only_draft()["Message-ID"]

    with serving(env) as page:
        body = make_body("Backend Engineer") + " Edited."
        reply = page.post("/emails/1/edit", {"subject": "Hello again", "body": body})

    assert reply.status in (200, 303)
    draft = env.gmail.only_draft()
    assert draft["Subject"] == "Hello again"
    assert draft.get_body(("plain",)).get_content().strip().endswith("Edited.")  # type: ignore[union-attr]
    assert draft["Message-ID"] == before
    assert attachments(draft) == [("Resume_Software_Engineer.pdf", TINY_PDF)]
    assert len(env.gmail.drafts) == 1


def test_switching_the_resume_replaces_the_attachment_on_every_draft_of_the_group(env: Env) -> None:
    prepared(env, contacts=2)

    with serving(env) as page:
        assert page.post("/groups/1/resume", {"variant": "ml"}).status in (200, 303)

    names = [attachments(env.gmail.draft(d)) for d in env.gmail.drafts]
    assert len(names) == 2
    assert all(n == [("Resume_ML_Engineer.pdf", TINY_PDF)] for n in names)


def test_a_gmail_failure_on_edit_saves_nothing_and_says_so(env: Env) -> None:
    prepared(env, contacts=1)
    env.gmail.fail("update_draft", GmailAuthError("Gmail authorization has expired"))

    with serving(env) as page:
        reply = page.post("/emails/1/edit", {"subject": "Lost edit", "body": make_body("Backend Engineer")})

    assert reply.status == 502
    assert "Gmail authorization has expired" in reply.body
    assert email_rows(env)[0]["subject"] == "Applied for Backend Engineer"
    assert env.gmail.only_draft()["Subject"] == "Applied for Backend Engineer"


def test_dry_run_edits_never_touch_gmail(env: Env) -> None:
    prepared(env, contacts=1, dry_run=True)

    with serving(env) as page:
        edit = page.post("/emails/1/edit", {"subject": "Hi", "body": make_body("Backend Engineer")})
        assert edit.status in (200, 303)
        assert page.post("/groups/1/resume", {"variant": "ml"}).status in (200, 303)

    assert env.gmail.calls == []


def test_an_email_with_a_bad_resume_gets_its_draft_once_the_resume_is_switched(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    (env.root / "resumes" / "Resume_Software_Engineer.pdf").unlink()
    write_for_all(env, ids)
    assert env.prepare().code == 0
    assert env.gmail.drafts == {}

    with serving(env) as page:
        assert page.post("/groups/1/resume", {"variant": "ai"}).status in (200, 303)

    assert attachments(env.gmail.only_draft()) == [("Resume_AI_Engineer.pdf", TINY_PDF)]


def test_the_review_page_says_how_many_gmail_drafts_exist(env: Env) -> None:
    prepared(env, contacts=2)
    with serving(env) as page:
        assert "Gmail drafts: 2 of 2" in page.get().body


def test_the_review_page_says_a_dry_run_has_no_drafts(env: Env) -> None:
    prepared(env, contacts=1, dry_run=True)
    with serving(env) as page:
        assert "Gmail drafts: none (dry run)" in page.get().body


def test_with_no_saved_gmail_sign_in_prepare_says_how_to_sign_in(env: Env) -> None:
    """The real adapter is used when no fake is injected; with no token file it stops before any network call."""
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    env.write_config(f'gmail_token = "{env.root / "none.json"}"\n' + env.config.read_text())
    argv = ["prepare", str(env.run_dir), "--no-serve", "--config", str(env.config), "--db", str(env.db), "--now", NOW]

    code = main(argv)

    assert code == 2
    assert "outreach gmail-auth" in env.capsys.readouterr().err


def test_gmail_auth_without_the_google_client_file_says_what_is_missing(env: Env) -> None:
    env.write_config(f'gmail_credentials = "{env.root / "missing.json"}"\n')

    code = main(["gmail-auth", "--config", str(env.config)])

    assert code == 2
    assert "missing.json" in env.capsys.readouterr().err
