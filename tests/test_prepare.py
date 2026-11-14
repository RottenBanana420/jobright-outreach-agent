"""`outreach prepare`, driven through the CLI with synthetic revealed addresses and written emails."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

import pytest

from conftest import (
    VARIANT_FILES,
    Env,
    make_app,
    make_body,
    make_contact,
    make_email,
    make_pdf,
    prepared_inputs,
    resume_config,
    write_for_all,
    write_resumes,
)


def emails(env: Env) -> list[sqlite3.Row]:
    return env.query("SELECT * FROM outreach_emails ORDER BY id")


def test_prepare_attaches_each_email_to_its_contact_and_company_group(env: Env) -> None:
    ids = prepared_inputs(env)
    write_for_all(env, ids)

    result = env.prepare()

    assert result.code == 0, result.err
    rows = env.query(
        "SELECT e.status, e.address, e.subject, c.name, g.name AS company FROM outreach_emails e"
        " JOIN contacts c ON c.id = e.contact_id JOIN company_groups g ON g.id = e.group_id ORDER BY c.position"
    )
    assert [(r["name"], r["company"], r["status"], r["address"]) for r in rows] == [
        ("Eng 0", "Acme", "written", "eng.0@acme.test"),
        ("Eng 1", "Acme", "written", "eng.1@acme.test"),
    ]
    assert rows[0]["subject"] == "Applied for Backend Engineer"
    assert "2 Outreach Email(s)" in result.out


def test_the_revealed_address_is_stored_on_the_contact(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)

    assert env.prepare().code == 0

    [row] = env.query("SELECT email FROM contacts")
    assert row["email"] == "eng.0@acme.test"


def test_content_hash_covers_recipient_subject_and_body(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids, subject="Hello", body=make_body())

    assert env.prepare().code == 0

    [row] = emails(env)
    expected = hashlib.sha256(f"eng.0@acme.test\nHello\n{make_body()}".encode()).hexdigest()
    assert row["content_hash"] == expected


def test_the_database_allows_one_outreach_email_per_contact_and_company_group(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    assert env.prepare().code == 0
    [row] = emails(env)

    con = sqlite3.connect(env.db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO outreach_emails (run_id, contact_id, group_id, address, subject, body, content_hash,"
            " resume_variant, resume_path, status, findings, created_at)"
            " VALUES (?, ?, ?, 'x@acme.test', 's', 'b', 'h', 'software', '', 'written', '[]', '')",
            (row["run_id"], row["contact_id"], row["group_id"]),
        )
    con.close()


def test_preparing_twice_does_not_duplicate_emails(env: Env) -> None:
    ids = prepared_inputs(env)
    write_for_all(env, ids)
    assert env.prepare().code == 0
    assert env.prepare().code == 0

    assert len(emails(env)) == 2


def test_status_changes_are_logged_as_events(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    assert env.prepare().code == 0

    [row] = emails(env)
    history = env.query("SELECT from_status, to_status FROM events WHERE entity = 'outreach_emails' AND entity_id = ?",
                        row["id"])
    assert [(e["from_status"], e["to_status"]) for e in history] == [(None, "written")]


# --- Resume Variant ---------------------------------------------------------------------------


@pytest.mark.parametrize(("title", "variant"), [
    ("Forward Deployed Engineer", "forward_deployed"),
    ("forward deployed software engineer", "forward_deployed"),
    ("Machine Learning Engineer", "ml"),
    ("ML Platform Engineer", "ml"),
    ("Data Scientist", "ml"),
    ("AI/ML Engineer", "ml"),
    ("Applied AI Engineer", "ai"),
    ("LLM Engineer", "ai"),
    ("GenAI Developer", "ai"),
    ("Data Engineer", "data"),
    ("Analytics Engineer", "data"),
    ("Data Platform Engineer", "data"),
    ("Backend Engineer", "software"),
    ("HTML Developer", "software"),  # "ML" inside a word is not a match
    ("Said Engineer", "software"),  # nor "AI"
    ("Forward Deployed Data Engineer", "forward_deployed"),  # first match wins
    ("Forward-Deployed Engineer", "forward_deployed"),
    ("Forward_Deployed Engineer", "forward_deployed"),
    ("Machine-Learning Engineer", "ml"),
    ("MLOps Engineer", "ml"),
    ("ML-Ops Engineer", "ml"),
    ("Gen-AI Engineer", "ai"),
    ("Applied-AI Engineer", "ai"),
    ("Data-Platform Engineer", "data"),
    ("Machine-Learning Forward-Deployed Engineer", "forward_deployed"),
    ("HTML-Developer", "software"),
])
def test_resume_variant_follows_the_first_match_wins_table(env: Env, title: str, variant: str) -> None:
    ids = prepared_inputs(env, roles=[(title, 90)], contacts=1)
    write_for_all(env, ids, role=title)

    assert env.prepare().code == 0

    [row] = emails(env)
    assert row["resume_variant"] == variant
    assert row["resume_path"] == str(env.root / "resumes" / VARIANT_FILES[variant])
    assert row["block_reason"] is None


def test_a_multi_role_group_uses_its_highest_match_score_role(env: Env) -> None:
    ids = prepared_inputs(env, roles=[("Backend Engineer", 70), ("Machine Learning Engineer", 95)], contacts=1)
    write_for_all(env, ids, body=make_body("Backend Engineer") + " Also Machine Learning Engineer.")

    assert env.prepare().code == 0

    [row] = emails(env)
    assert row["resume_variant"] == "ml"


def test_an_open_role_beats_a_closed_one_for_the_resume_choice(env: Env) -> None:
    write_resumes(env.root / "resumes")
    env.write_config(resume_config(env.root / "resumes"))
    env.write_apps([
        make_app("Acme", "Machine Learning Engineer", None, job_closed=True, hidden_score=99),
        make_app("Acme", "Backend Engineer", 60),
    ])
    assert env.shortlist().code == 0
    env.write_candidates({"Acme": [make_contact("Eng 0")]})
    assert env.pick_contacts().code == 0
    ids = env.contact_ids()
    write_for_all(env, ids)

    assert env.prepare().code == 0

    [row] = emails(env)
    assert row["resume_variant"] == "software"


def test_a_resume_file_can_be_remapped_in_the_config(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    resumes = env.root / "resumes"
    (resumes / "mine.pdf").write_bytes((resumes / "Resume_Software_Engineer.pdf").read_bytes())
    env.write_config(resume_config(env.root / "resumes").replace("Resume_Software_Engineer.pdf", "mine.pdf"))
    write_for_all(env, ids)

    assert env.prepare().code == 0

    [row] = emails(env)
    assert row["resume_path"].endswith("mine.pdf")


@pytest.mark.parametrize(("setup", "message"), [
    ("missing", "does not exist"),
    ("not_pdf", "not a PDF"),
    ("truncated", "not a readable PDF"),
    ("junk_between_markers", "not a readable PDF"),  # a valid header and %%EOF, nothing PDF-like between
    ("startxref_past_the_end", "not a readable PDF"),
    ("startxref_at_junk", "not a readable PDF"),
    ("cut_in_the_middle", "not a readable PDF"),
    ("huge", "25 MB"),
])
def test_a_bad_resume_blocks_the_email_with_a_clear_message(env: Env, setup: str, message: str) -> None:
    ids = prepared_inputs(env, contacts=1)
    path = env.root / "resumes" / VARIANT_FILES["software"]
    if setup == "missing":
        path.unlink()
    elif setup == "not_pdf":
        path.write_bytes(b"just some text")
    elif setup == "truncated":
        path.write_bytes(b"%PDF-1.4\n1 0 obj\n")
    elif setup == "junk_between_markers":
        path.write_bytes(b"%PDF-1.4\n" + b"not really a pdf at all\n" * 20 + b"%%EOF\n")
    elif setup == "startxref_past_the_end":
        path.write_bytes(make_pdf(startxref=999_999))
    elif setup == "startxref_at_junk":
        path.write_bytes(make_pdf(startxref=12))  # inside the first object's text, not an xref table or object
    elif setup == "cut_in_the_middle":
        path.write_bytes(make_pdf()[:-60])  # the tail with the xref pointer and marker is gone
    else:
        path.write_bytes(b"%PDF-1.4\n" + b"x" * (25 * 1024 * 1024) + b"\n%%EOF\n")
    write_for_all(env, ids)

    result = env.prepare()

    assert result.code == 0, result.err
    [row] = emails(env)
    assert row["status"] == "written"
    assert message in row["block_reason"]
    assert str(path.name) in row["block_reason"]
    assert "blocked" in result.out


# --- Email linter -------------------------------------------------------------------------------


def findings(env: Env) -> list[str]:
    [row] = emails(env)
    return [f["code"] for f in json.loads(row["findings"])]


def one_email(env: Env, **over: Any) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids, **over)


def test_a_good_email_has_no_findings(env: Env) -> None:
    one_email(env)
    assert env.prepare().code == 0
    assert findings(env) == []


@pytest.mark.parametrize("words", [79, 141])
def test_word_count_outside_80_to_140_is_a_finding(env: Env, words: int) -> None:
    one_email(env, body=make_body(words=words))
    assert env.prepare().code == 0
    assert findings(env) == ["word_count"]


@pytest.mark.parametrize("words", [80, 140])
def test_word_count_at_the_limits_is_fine(env: Env, words: int) -> None:
    one_email(env, body=make_body(words=words))
    assert env.prepare().code == 0
    assert findings(env) == []


def test_a_missing_role_title_is_a_finding(env: Env) -> None:
    one_email(env, body=make_body("Platform Engineer"))
    assert env.prepare().code == 0
    assert findings(env) == ["role_title"]


def test_role_title_must_match_exactly_but_not_by_case(env: Env) -> None:
    one_email(env, body=make_body("backend engineer"))
    assert env.prepare().code == 0
    assert findings(env) == []


def test_every_role_of_the_group_must_be_named(env: Env) -> None:
    ids = prepared_inputs(env, roles=[("Backend Engineer", 90), ("Data Engineer", 80)], contacts=1)
    write_for_all(env, ids)  # names only Backend Engineer
    assert env.prepare().code == 0
    [row] = emails(env)
    [finding] = json.loads(row["findings"])
    assert finding["code"] == "role_title"
    assert "Data Engineer" in finding["message"]


def test_not_saying_you_applied_is_a_finding(env: Env) -> None:
    body = make_body().replace("applied for", "am interested in")
    one_email(env, body=body)
    assert env.prepare().code == 0
    assert findings(env) == ["applied"]


def test_an_address_on_another_domain_is_a_finding(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    env.write_revealed([{"contact_id": ids["Eng 0"], "address": "eng.0@globex.test"}])
    env.write_emails([make_email(ids["Eng 0"], "eng.0@globex.test")])
    assert env.prepare().code == 0
    assert findings(env) == ["domain"]


@pytest.mark.parametrize("domain", ["acme.com", "mail.acme.io", "acme-inc.co", "acmecorp.test"])
def test_company_domains_that_contain_the_company_name_are_fine(env: Env, domain: str) -> None:
    ids = prepared_inputs(env, contacts=1)
    env.write_revealed([{"contact_id": ids["Eng 0"], "address": f"eng.0@{domain}"}])
    env.write_emails([make_email(ids["Eng 0"], f"eng.0@{domain}")])
    assert env.prepare().code == 0
    assert findings(env) == []


def test_findings_do_not_stop_the_email_being_prepared(env: Env) -> None:
    one_email(env, body="Too short.")
    assert env.prepare().code == 0
    [row] = emails(env)
    assert row["status"] == "written"
    assert set(findings(env)) == {"word_count", "role_title", "applied"}


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
def test_more_than_one_recipient_is_a_hard_error(env: Env, field: str) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    doc = json.loads((env.run_dir / "written_emails.json").read_text())
    extra = ["boss@acme.test"]
    doc["emails"][0][field] = doc["emails"][0]["to"] + extra if field == "to" else extra
    (env.run_dir / "written_emails.json").write_text(json.dumps(doc))

    result = env.prepare()

    assert result.code == 2
    assert "emails[0]" in result.err
    assert "recipient" in result.err
    assert emails(env) == []


def test_a_recipient_other_than_the_revealed_address_is_a_hard_error(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    env.write_revealed([{"contact_id": ids["Eng 0"], "address": "eng.0@acme.test"}])
    env.write_emails([make_email(ids["Eng 0"], "someone.else@acme.test")])

    result = env.prepare()

    assert result.code == 2
    assert "emails[0].to" in result.err
    assert emails(env) == []


def test_one_hard_error_stores_nothing_for_the_other_emails(env: Env) -> None:
    ids = prepared_inputs(env)
    write_for_all(env, ids)
    doc = json.loads((env.run_dir / "written_emails.json").read_text())
    doc["emails"][1]["to"].append("boss@acme.test")
    (env.run_dir / "written_emails.json").write_text(json.dumps(doc))

    assert env.prepare().code == 2

    assert emails(env) == []


# --- Input files --------------------------------------------------------------------------------


def test_a_missing_input_file_is_named(env: Env) -> None:
    prepared_inputs(env)
    result = env.prepare()
    assert result.code == 2
    assert "revealed_addresses.json" in result.err


@pytest.mark.parametrize(("mutate", "field"), [
    (lambda d: d["addresses"][0].pop("address"), "addresses[0].address"),
    (lambda d: d["addresses"][0].update(address="not an address"), "addresses[0].address"),
    (lambda d: d["addresses"][0].update(contact_id="12"), "addresses[0].contact_id"),
    (lambda d: d["addresses"].append(dict(d["addresses"][0])), "addresses[1].contact_id"),
])
def test_bad_revealed_addresses_name_the_field(env: Env, mutate, field: str) -> None:  # type: ignore[no-untyped-def]
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    path = env.run_dir / "revealed_addresses.json"
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc))

    result = env.prepare()

    assert result.code == 2
    assert field in result.err


@pytest.mark.parametrize(("mutate", "field"), [
    (lambda d: d["emails"][0].pop("subject"), "emails[0].subject"),
    (lambda d: d["emails"][0].update(subject="Hi\nBcc: x@y.test"), "emails[0].subject"),
    (lambda d: d["emails"][0].update(body="  "), "emails[0].body"),
    (lambda d: d["emails"][0].update(to="eng.0@acme.test"), "emails[0].to"),
    (lambda d: d["emails"][0].update(to=[]), "emails[0].to"),
    (lambda d: d["emails"][0].update(to=["nope"]), "emails[0].to[0]"),
    (lambda d: d["emails"][0].update(contact_id=True), "emails[0].contact_id"),
    (lambda d: d["emails"].append(dict(d["emails"][0])), "emails[1].contact_id"),
])
def test_bad_written_emails_name_the_field(env: Env, mutate, field: str) -> None:  # type: ignore[no-untyped-def]
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    path = env.run_dir / "written_emails.json"
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc))

    result = env.prepare()

    assert result.code == 2
    assert field in result.err
    assert emails(env) == []


def test_an_email_for_an_unknown_contact_is_rejected(env: Env) -> None:
    prepared_inputs(env, contacts=1)
    env.write_revealed([{"contact_id": 9999, "address": "x@acme.test"}])
    env.write_emails([make_email(9999, "x@acme.test")])

    result = env.prepare()

    assert result.code == 2
    assert "addresses[0].contact_id" in result.err


def test_an_email_for_a_contact_without_an_address_is_rejected(env: Env) -> None:
    ids = prepared_inputs(env, contacts=2)
    env.write_revealed([{"contact_id": ids["Eng 0"], "address": "eng.0@acme.test"}])
    env.write_emails([make_email(ids["Eng 0"], "eng.0@acme.test"), make_email(ids["Eng 1"], "eng.1@acme.test")])

    result = env.prepare()

    assert result.code == 2
    assert "emails[1].contact_id" in result.err
    assert "address" in result.err


def test_an_email_for_a_cooldown_flagged_contact_is_rejected(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    con = sqlite3.connect(env.db)
    con.execute("UPDATE contacts SET status = 'flagged'")
    con.commit()
    con.close()
    write_for_all(env, ids)

    result = env.prepare()

    assert result.code == 2
    assert "emails[0].contact_id" in result.err
    assert "not a picked Contact" in result.err


# --- Bounces and shared addresses -------------------------------------------------------------


def test_a_bounced_address_gets_no_email_and_is_reported(env: Env) -> None:
    ids = prepared_inputs(env)
    write_for_all(env, ids)
    env.write_bounces([{"address": "Eng.0@acme.test"}])
    assert env.record_bounces().code == 0

    result = env.prepare()

    assert result.code == 0, result.err
    assert [r["address"] for r in emails(env)] == ["eng.1@acme.test"]
    assert "Eng 0" in result.out
    assert "bounced" in result.out
    [row] = env.query("SELECT status FROM contacts WHERE name = 'Eng 0'")
    assert row["status"] == "bounced"


def test_the_same_address_for_two_contacts_gets_one_email(env: Env) -> None:
    ids = prepared_inputs(env)
    env.write_revealed([
        {"contact_id": ids["Eng 0"], "address": "shared@acme.test"},
        {"contact_id": ids["Eng 1"], "address": "Shared@acme.test"},
    ])
    env.write_emails([make_email(ids["Eng 0"], "shared@acme.test"), make_email(ids["Eng 1"], "shared@acme.test")])

    result = env.prepare()

    assert result.code == 0, result.err
    assert [r["address"] for r in emails(env)] == ["shared@acme.test"]
    assert "same address" in result.out
    [row] = env.query("SELECT status FROM contacts WHERE name = 'Eng 1'")
    assert row["status"] == "duplicate_address"


def test_a_bounced_contact_is_picked_again_when_the_address_is_no_longer_bounced(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    env.write_bounces([{"address": "eng.0@acme.test"}])
    assert env.record_bounces().code == 0
    assert env.prepare().code == 0
    con = sqlite3.connect(env.db)
    con.execute("DELETE FROM bounces")
    con.commit()
    con.close()

    assert env.prepare().code == 0

    assert len(emails(env)) == 1
    [row] = env.query("SELECT status FROM contacts")
    assert row["status"] == "picked"


# --- work-humanizer check hook ---------------------------------------------------------------


def test_prepare_skips_the_humanizer_check_when_it_is_not_configured(env: Env) -> None:
    one_email(env)
    result = env.prepare()
    assert result.code == 0
    assert "humanizer check skipped" in result.out
    assert findings(env) == []


def test_prepare_skips_the_humanizer_check_when_the_script_is_missing(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1, extra_config=f'humanizer_check = "{env.root / "nope" / "check.py"}"')
    write_for_all(env, ids)
    result = env.prepare()
    assert result.code == 0
    assert "humanizer check skipped" in result.out


# Models the real check (.claude/skills/outreach-humanizer/check.py): body on stdin, exit code 0 always, and
# stdout holds "label: hits" lines for what it flagged plus stats lines ("sentences: ...", "words: N") even
# for clean text.
FAKE_CHECK = (
    "import sys\n"
    "body = sys.stdin.read()\n"
    "if 'Thanks' in body:\n"
    "    print('filler hedges: [\\'arguably\\']')\n"
    "    print('exclamation marks: 2')\n"
    "if 'Thanks' in body and 'Thanks Thanks' in body:\n"
    "    print('sentences: 9, mean 8.0 words, 80% within 5 words of the median (uniform: vary)')\n"
    "else:\n"
    "    print('sentences: 9, mean 8.0 words, 20% within 5 words of the median')\n"
    "print(f'words: {len(body.split())}')\n"
)


def humanizer_env(env: Env, source: str, body: str | None = None) -> None:
    script = env.root / "check.py"
    script.write_text(source)
    ids = prepared_inputs(env, contacts=1, extra_config=f'humanizer_check = "{script}"')
    write_for_all(env, ids, **({} if body is None else {"body": body}))


def test_humanizer_check_findings_are_added_to_the_lint(env: Env) -> None:
    humanizer_env(env, FAKE_CHECK)

    result = env.prepare()

    assert result.code == 0, result.err
    [row] = emails(env)
    messages = [f["message"] for f in json.loads(row["findings"])]
    assert messages == [
        "work-humanizer: filler hedges: ['arguably']",
        "work-humanizer: exclamation marks: 2",
        "work-humanizer: sentences: 9, mean 8.0 words, 80% within 5 words of the median (uniform: vary)",
    ]
    assert "humanizer check skipped" not in result.out


def test_clean_text_with_only_stats_lines_has_no_humanizer_findings(env: Env) -> None:
    clean = make_body().replace("Thanks", "Cheers")
    humanizer_env(env, FAKE_CHECK, body=clean)

    result = env.prepare()

    assert result.code == 0, result.err
    assert findings(env) == []
    assert "humanizer check skipped" not in result.out


def test_a_crashing_humanizer_script_is_skipped_not_fatal(env: Env) -> None:
    script = env.root / "check.py"
    script.write_text("raise SystemExit(3)\n")
    ids = prepared_inputs(env, contacts=1, extra_config=f'humanizer_check = "{script}"')
    write_for_all(env, ids)
    result = env.prepare()
    assert result.code == 0
    assert findings(env) == []


def test_a_database_with_the_old_drafted_status_is_upgraded_to_written(env: Env) -> None:
    ids = prepared_inputs(env, contacts=1)
    write_for_all(env, ids)
    assert env.prepare().code == 0
    con = sqlite3.connect(env.db)
    con.execute("UPDATE outreach_emails SET status = 'drafted'")
    con.execute("UPDATE events SET to_status = 'drafted' WHERE entity = 'outreach_emails' AND to_status = 'written'")
    con.commit()
    con.close()

    assert env.prepare().code == 0  # opening the database upgrades it

    [row] = emails(env)
    assert row["status"] == "written"
    assert env.query("SELECT 1 FROM events WHERE to_status = 'drafted' OR from_status = 'drafted'") == []


def test_a_humanizer_script_that_exits_non_zero_is_skipped_even_with_output(env: Env) -> None:
    humanizer_env(env, "import sys\nprint('Traceback-looking output')\nsys.exit(1)\n")
    result = env.prepare()
    assert result.code == 0
    assert findings(env) == []
    assert "humanizer check skipped" in result.out
