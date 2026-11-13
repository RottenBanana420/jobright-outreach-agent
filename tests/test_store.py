"""The Store is checked through what `outreach shortlist` leaves in SQLite."""
from __future__ import annotations

from conftest import TODAY, Env, make_app


def seed(env: Env) -> None:
    env.write_apps([
        make_app("Acme", "Backend Engineer", 70),
        make_app("Acme", "ML Engineer", 95),
        make_app("Globex", "SWE", 90),
        make_app("Initech", "SWE", 60),
        make_app("Old Co", "SWE", 99, applied_on="2026-11-11"),
    ])


def test_records_the_run_applications_and_company_groups(env: Env) -> None:
    env.write_config("shortlist_size = 2\n")
    seed(env)

    assert env.shortlist().code == 0

    [run] = env.query("SELECT * FROM runs")
    assert run["run_date"] == TODAY
    assert run["status"] == "shortlisted"
    groups = env.query("SELECT name, rank, status FROM company_groups ORDER BY rank")
    assert [(g["name"], g["rank"], g["status"]) for g in groups] == [
        ("Acme", 1, "shortlisted"),
        ("Globex", 2, "shortlisted"),
        ("Initech", 3, "cut"),
    ]
    apps = env.query(
        "SELECT a.title, a.match_score, g.name AS grp FROM applications a "
        "JOIN company_groups g ON g.id = a.group_id ORDER BY a.title, a.match_score"
    )
    assert [(a["title"], a["match_score"], a["grp"]) for a in apps] == [
        ("Backend Engineer", 70, "Acme"),
        ("ML Engineer", 95, "Acme"),
        ("SWE", 60, "Initech"),
        ("SWE", 90, "Globex"),
    ]  # Old Co (not applied today) is not recorded


def test_running_shortlist_again_does_not_duplicate_anything(env: Env) -> None:
    seed(env)

    env.shortlist()
    env.shortlist()

    assert len(env.query("SELECT * FROM runs")) == 1
    assert len(env.query("SELECT * FROM company_groups")) == 3
    assert len(env.query("SELECT * FROM applications")) == 4


def test_every_status_is_backed_by_an_audit_event(env: Env) -> None:
    seed(env)

    env.shortlist()

    [run] = env.query("SELECT id, status FROM runs")
    history = env.query(
        "SELECT from_status, to_status FROM events WHERE entity = 'runs' AND entity_id = ? ORDER BY id",
        run["id"],
    )
    assert [(h["from_status"], h["to_status"]) for h in history] == [
        (None, "created"),
        ("created", "shortlisted"),
    ]
    for g in env.query("SELECT id, status FROM company_groups"):
        [event] = env.query(
            "SELECT from_status, to_status FROM events WHERE entity = 'company_groups' AND entity_id = ?",
            g["id"],
        )
        assert (event["from_status"], event["to_status"]) == (None, g["status"])


def test_a_rejected_file_leaves_no_trace_in_the_database(env: Env) -> None:
    env.write_apps([make_app("Acme", "SWE", 70, match_score="high")])

    assert env.shortlist().code != 0

    assert not env.db.exists()


def test_the_same_jobright_id_twice_is_rejected(env: Env) -> None:
    env.write_apps([make_app("Acme", "SWE", 70), make_app("Acme", "SWE", 70)])

    result = env.shortlist()

    assert result.code != 0
    assert "applications[1].jobright_id" in result.err


def test_rerunning_after_scores_change_updates_rows_in_place(env: Env) -> None:
    env.write_config("shortlist_size = 1\n")
    env.write_apps([make_app("Acme", "SWE", 90), make_app("Globex", "SWE", 70)])
    env.shortlist()
    ids = {g["name"]: g["id"] for g in env.query("SELECT id, name FROM company_groups")}

    env.write_apps([make_app("Acme", "SWE", 50), make_app("Globex", "SWE", 70)])
    env.shortlist()

    after = env.query("SELECT id, name, rank, status FROM company_groups ORDER BY rank")
    assert [(g["name"], g["status"]) for g in after] == [("Globex", "shortlisted"), ("Acme", "cut")]
    assert {g["name"]: g["id"] for g in after} == ids  # same rows, so anything hung off them survives
    acme_history = env.query(
        "SELECT to_status FROM events WHERE entity = 'company_groups' AND entity_id = ? ORDER BY id",
        ids["Acme"],
    )
    assert [e["to_status"] for e in acme_history] == ["shortlisted", "cut"]


def test_an_application_no_longer_applied_today_is_dropped_on_rerun(env: Env) -> None:
    env.write_apps([make_app("Acme", "SWE", 90), make_app("Globex", "SWE", 70)])
    env.shortlist()

    env.write_apps([make_app("Acme", "SWE", 90)])
    env.shortlist()

    assert [g["name"] for g in env.query("SELECT name FROM company_groups")] == ["Acme"]
    assert len(env.query("SELECT * FROM applications")) == 1
