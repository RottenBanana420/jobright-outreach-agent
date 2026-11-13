"""`outreach pick-contacts`, driven through the CLI with synthetic candidates."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from datetime import date
from typing import Any

from conftest import Env, make_app, make_contact
from outreach.store import Store


def shortlisted(env: Env, *companies: str) -> None:
    env.write_apps([make_app(c, "Backend Engineer", 90 - i) for i, c in enumerate(companies)])
    assert env.shortlist().code == 0


def engineers(n: int, company: str = "Acme") -> list[dict[str, Any]]:
    return [make_contact(f"Eng {i}", company=company) for i in range(n)]


def test_picks_up_to_the_configured_cap_per_company(env: Env) -> None:
    env.write_config("contacts_per_company = 3\n")
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": engineers(5)})

    result = env.pick_contacts()

    assert result.code == 0, result.err
    assert env.picked_names("Acme") == ["Eng 0", "Eng 1", "Eng 2"]


def test_contacts_flag_overrides_the_configured_cap_for_one_run(env: Env) -> None:
    env.write_config("contacts_per_company = 3\n")
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": engineers(5)})

    result = env.pick_contacts("--contacts", "2")

    assert result.code == 0, result.err
    assert env.picked_names("Acme") == ["Eng 0", "Eng 1"]


def test_cap_is_a_ceiling_and_the_shortfall_is_recorded(env: Env) -> None:
    env.write_config("contacts_per_company = 10\n")
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": engineers(7)})

    assert env.pick_contacts().code == 0

    [group] = env.picked_json()["groups"]
    assert len(group["contacts"]) == 7
    assert group["picked_of_cap"] == "7 of 10"
    [row] = env.query("SELECT contacts_picked, contact_cap FROM company_groups WHERE name = 'Acme'")
    assert (row["contacts_picked"], row["contact_cap"]) == (7, 10)


def test_picked_contacts_are_stored_with_category_and_group(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [
        make_contact("Rae Recruiter", "Technical Recruiter", group="school", connection="Example University"),
    ]})

    assert env.pick_contacts().code == 0

    [row] = env.query("SELECT * FROM contacts")
    assert (row["name"], row["title"], row["category"]) == ("Rae Recruiter", "Technical Recruiter", "Recruiter")
    assert row["status"] == "picked"
    assert row["linkedin_url"] == "https://www.linkedin.com/in/rae-recruiter"
    assert row["connection"] == "Example University"
    assert env.picked_json()["groups"][0]["contacts"][0]["groups"] == ["school"]


def earlier_run(env: Env, day: str, company: str, contacts: list[dict[str, Any]]) -> Env:
    """Shortlist and pick on an earlier day, in its own Run folder, sharing the database and config."""
    other = replace(env, run_dir=env.root / "data" / day)
    other.run_dir.mkdir(parents=True)
    other.write_apps([make_app(company, "Backend Engineer", 90, applied_on=day)])
    now = f"{day}T12:00:00-08:00"
    assert other.shortlist(now=now).code == 0
    other.write_candidates({company: contacts})
    assert other.pick_contacts(now=now).code == 0
    return other


def store_call(env: Env, action: Callable[[Store], None]) -> None:
    """Stands in for later steps (addresses, sends) that write to the Store."""
    with closing(Store(env.db)) as store, store.transaction():
        action(store)


def contact_id(env: Env, name: str) -> int:
    [row] = env.query("SELECT id FROM contacts WHERE name = ?", name)
    return int(row["id"])


def test_contact_emailed_within_the_cooldown_at_any_company_is_flagged_not_picked(env: Env) -> None:
    earlier_run(env, "2026-11-02", "Globex", [make_contact("Cal Cooldown", company="Globex")])
    store_call(env, lambda s: s.mark_emailed(contact_id(env, "Cal Cooldown"), "2026-11-02T13:00:00-08:00"))
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [
        make_contact("Cal Cooldown", "Technical Recruiter"),
        make_contact("Eng 1"),
    ]})

    assert env.pick_contacts().code == 0

    [group] = env.picked_json()["groups"]
    assert [c["name"] for c in group["contacts"]] == ["Eng 1"]
    assert [(c["name"], c["cooldown_last_emailed"]) for c in group["review"]] == [
        ("Cal Cooldown", "2026-11-02T13:00:00-08:00")
    ]
    [row] = env.query("SELECT status FROM contacts WHERE name = 'Cal Cooldown' AND listed_company = 'Acme'")
    assert row["status"] == "flagged"


def test_contact_emailed_before_the_cooldown_window_is_picked_again(env: Env) -> None:
    env.write_config("cooldown_days = 30\n")
    earlier_run(env, "2026-10-01", "Globex", [make_contact("Cal Cooldown", company="Globex")])
    store_call(env, lambda s: s.mark_emailed(contact_id(env, "Cal Cooldown"), "2026-10-01T13:00:00-07:00"))
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [make_contact("Cal Cooldown")]})

    assert env.pick_contacts().code == 0

    assert env.picked_names("Acme") == ["Cal Cooldown"]


def test_a_bounced_address_is_never_picked_again(env: Env) -> None:
    earlier_run(env, "2026-11-02", "Globex", [make_contact("Bo Bounce", company="Globex")])
    store_call(env, lambda s: s.set_contact_email(contact_id(env, "Bo Bounce"), "Bo.Bounce@globex.example"))
    env.write_bounces([{"address": "bo.bounce@GLOBEX.example", "bounced_at": "2026-11-03"}])
    assert env.record_bounces().code == 0
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [make_contact("Bo Bounce"), make_contact("Eng 1")]})

    assert env.pick_contacts().code == 0

    assert env.picked_names("Acme") == ["Eng 1"]
    assert env.picked_json()["groups"][0]["review"] == []


def test_record_bounces_stores_addresses_and_can_be_repeated(env: Env) -> None:
    env.write_bounces([
        {"address": "A@one.example", "bounced_at": "2026-11-03", "note": "mailbox full"},
        {"address": "b@two.example"},
    ])

    assert env.record_bounces().code == 0
    assert env.record_bounces().code == 0

    rows = env.query("SELECT address, bounced_at, note FROM bounces ORDER BY address")
    assert [(r["address"], r["note"]) for r in rows] == [("a@one.example", "mailbox full"), ("b@two.example", "")]
    assert rows[0]["bounced_at"] == "2026-11-03"
    assert date.fromisoformat(rows[1]["bounced_at"])  # no date given: defaults to when it was recorded


def test_picking_again_updates_in_place_and_keeps_contact_ids(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": engineers(3)})
    assert env.pick_contacts().code == 0
    first = {r["name"]: r["id"] for r in env.query("SELECT id, name FROM contacts")}

    env.write_candidates({"Acme": engineers(2)})
    assert env.pick_contacts().code == 0

    second = {r["name"]: r["id"] for r in env.query("SELECT id, name FROM contacts")}
    assert second == {"Eng 0": first["Eng 0"], "Eng 1": first["Eng 1"]}
    assert env.picked_names("Acme") == ["Eng 0", "Eng 1"]
    events = env.query("SELECT to_status FROM events WHERE entity = 'contacts' AND entity_id = ?", first["Eng 0"])
    assert [e["to_status"] for e in events] == ["picked"]


def test_a_shortlisted_group_without_candidates_gets_no_contacts(env: Env) -> None:
    shortlisted(env, "Acme", "Globex")
    env.write_candidates({"Acme": engineers(2)})

    assert env.pick_contacts().code == 0

    groups = {g["company"]: g for g in env.picked_json()["groups"]}
    assert groups["Globex"]["contacts"] == []
    assert groups["Globex"]["picked_of_cap"] == "0 of 4"


def test_candidates_for_a_company_outside_the_shortlist_are_rejected(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": engineers(1), "Globex": engineers(1, "Globex")})

    result = env.pick_contacts()

    assert result.code == 2
    assert "companies[1].company" in result.err
    assert "Globex" in result.err


def test_a_candidate_with_an_unknown_group_names_the_field(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [make_contact("Eng 0"), make_contact("Eng 1", group="friends")]})

    result = env.pick_contacts()

    assert result.code == 2
    assert "companies[0].contacts[1].group" in result.err
    assert not (env.run_dir / "picked_contacts.json").exists()


def test_a_candidate_without_a_linkedin_url_names_the_field(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [make_contact("Eng 0", linkedin_url="")]})

    result = env.pick_contacts()

    assert result.code == 2
    assert "companies[0].contacts[0].linkedin_url" in result.err


def test_picking_before_the_shortlist_is_an_error(env: Env) -> None:
    env.write_candidates({"Acme": engineers(1)})

    result = env.pick_contacts()

    assert result.code == 2
    assert "outreach shortlist" in result.err


def test_missing_candidates_file_is_an_error(env: Env) -> None:
    shortlisted(env, "Acme")

    result = env.pick_contacts()

    assert result.code == 2
    assert "contact_candidates.json" in result.err


def test_contacts_flag_below_one_is_an_error(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": engineers(1)})

    result = env.pick_contacts("--contacts", "0")

    assert result.code == 2
    assert "--contacts" in result.err


def test_a_bad_bounce_address_names_the_field(env: Env) -> None:
    env.write_bounces([{"address": "a@one.example"}, {"address": "not-an-address"}])

    result = env.record_bounces()

    assert result.code == 2
    assert "bounces[1].address" in result.err


def test_blank_or_different_company_contacts_are_never_picked(env: Env) -> None:
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [
        make_contact("Blank Co", company=""),
        make_contact("Other Co", company="Globex"),
        make_contact("Same Co", company="ACME, Inc."),
        make_contact("Exact Co", company="Acme"),
    ]})

    assert env.pick_contacts().code == 0

    assert env.picked_names("Acme") == ["Same Co", "Exact Co"]
    assert env.picked_json()["groups"][0]["picked_of_cap"] == "2 of 4"


def test_same_person_in_two_groups_counts_once(env: Env) -> None:
    env.write_config("contacts_per_company = 3\n")
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [
        make_contact("Sam Shared", group="school", connection="Example University"),
        make_contact("Eng 1"),
        make_contact("Eng 2"),
        make_contact("Sam Shared", group="previous_company", linkedin_url="http://linkedin.com/in/Sam-Shared/?x=1"),
        make_contact("Eng 3"),
    ]})

    assert env.pick_contacts().code == 0

    [group] = env.picked_json()["groups"]
    assert [c["name"] for c in group["contacts"]] == ["Sam Shared", "Eng 1", "Eng 2"]
    assert group["contacts"][0]["groups"] == ["school", "previous_company"]
    assert len(env.query("SELECT * FROM contacts")) == 3


def test_connections_first_then_recruiter_hiring_leader_engineer_other(env: Env) -> None:
    env.write_config("contacts_per_company = 10\n")
    shortlisted(env, "Acme")
    env.write_candidates({"Acme": [
        make_contact("Olga Other", "Designer"),
        make_contact("Eli Engineer", "Senior Software Engineer"),
        make_contact("Hana Hiring", "Engineering Manager"),
        make_contact("Rae Recruiter", "Talent Acquisition Partner"),
        make_contact("Sid School", "Designer", group="school", connection="Example University"),
        make_contact("Pat Previous", "Software Engineer", group="previous_company", connection="Initech"),
        make_contact("Lee Lead", "Lead Engineer"),
    ]})

    assert env.pick_contacts().code == 0

    assert env.picked_names("Acme") == [
        "Pat Previous", "Sid School", "Rae Recruiter", "Hana Hiring", "Lee Lead", "Eli Engineer", "Olga Other",
    ]
    categories = {c["name"]: c["category"] for c in env.picked_json()["groups"][0]["contacts"]}
    assert categories["Lee Lead"] == "Hiring Leader"
    assert categories["Olga Other"] == "Other Recruiting"
