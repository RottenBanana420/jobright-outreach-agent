from __future__ import annotations

import json

import pytest

from conftest import Env, make_app


def test_ranks_todays_applications_by_match_score(env: Env) -> None:
    env.write_apps([make_app("Acme", "Backend Engineer", 70), make_app("Globex", "SWE", 90)])

    result = env.shortlist()

    assert result.code == 0, result.err
    groups = env.shortlist_json()["groups"]
    assert [g["company"] for g in groups] == ["Globex", "Acme"]


def test_keeps_only_applied_status_with_todays_date(env: Env) -> None:
    env.write_apps([
        make_app("Acme", "Backend Engineer", 70),
        make_app("Saved Co", "SWE", 99, status="Saved"),
        make_app("Viewed Co", "SWE", 98, status="Viewed"),
        make_app("Old Co", "SWE", 97, applied_on="2026-11-11"),
    ])

    result = env.shortlist()

    assert result.code == 0, result.err
    assert [g["company"] for g in env.shortlist_json()["groups"]] == ["Acme"]


LATE_UTC = "2026-11-13T03:00:00+00:00"  # 19:00 on 2026-11-12 in Los Angeles


def test_today_is_the_date_in_the_default_timezone(env: Env) -> None:
    env.write_apps([make_app("Acme", "Backend Engineer", 70)])

    env.shortlist(now=LATE_UTC)

    assert [g["company"] for g in env.shortlist_json()["groups"]] == ["Acme"]


def test_today_uses_the_configured_timezone(env: Env) -> None:
    env.write_config('timezone = "Pacific/Auckland"\n')  # already 2026-11-13 there
    env.write_apps([make_app("Acme", "Backend Engineer", 70)])

    result = env.shortlist(now=LATE_UTC)

    assert result.code == 0, result.err
    assert env.shortlist_json()["groups"] == []


def test_closed_jobs_rank_below_every_open_job(env: Env) -> None:
    env.write_apps([
        make_app("Closed Hi", "SWE", None, job_closed=True, hidden_score=95),
        make_app("Open Lo", "SWE", 40),
        make_app("Closed Mid", "SWE", None, job_closed=True, hidden_score=75),
        make_app("Open Hi", "SWE", 85),
    ])

    env.shortlist()

    companies = [g["company"] for g in env.shortlist_json()["groups"]]
    assert companies == ["Open Hi", "Open Lo", "Closed Hi", "Closed Mid"]


def test_two_applications_at_one_company_form_one_group_naming_both_roles(env: Env) -> None:
    env.write_apps([
        make_app("Acme, Inc.", "Backend Engineer", 70),
        make_app("Globex", "SWE", 90),
        make_app("ACME", "ML Engineer", 95),
        make_app("Initech", "SWE", 80),
    ])

    env.shortlist()

    groups = env.shortlist_json()["groups"]
    assert [g["company"] for g in groups] == ["ACME", "Globex", "Initech"]
    acme = groups[0]
    assert acme["roles"] == ["ML Engineer", "Backend Engineer"]
    assert [a["title"] for a in acme["applications"]] == ["ML Engineer", "Backend Engineer"]
    assert acme["rank"] == 1


def test_shortlist_is_cut_at_the_default_size_of_ten(env: Env) -> None:
    env.write_apps([make_app(f"Company {i:02d}", "SWE", 50 + i) for i in range(12)])

    env.shortlist()

    groups = env.shortlist_json()["groups"]
    assert len(groups) == 10
    assert env.shortlist_json()["total_groups"] == 12
    assert groups[0]["company"] == "Company 11"
    assert groups[-1]["company"] == "Company 02"


def test_shortlist_size_comes_from_config(env: Env) -> None:
    env.write_config("shortlist_size = 2\n")
    env.write_apps([make_app(f"Company {i}", "SWE", 50 + i) for i in range(5)])

    env.shortlist()

    assert [g["company"] for g in env.shortlist_json()["groups"]] == ["Company 4", "Company 3"]


def test_fewer_applications_than_the_shortlist_size_is_not_an_error(env: Env) -> None:
    env.write_apps([make_app("Acme", "SWE", 70)])

    result = env.shortlist()

    assert result.code == 0, result.err
    assert len(env.shortlist_json()["groups"]) == 1


def test_a_day_with_no_applications_writes_an_empty_shortlist(env: Env) -> None:
    env.write_apps([])

    result = env.shortlist()

    assert result.code == 0, result.err
    assert env.shortlist_json()["groups"] == []


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda a: a.pop("company"), "applications[1].company"),
        (lambda a: a.update(company="  "), "applications[1].company"),
        (lambda a: a.pop("title"), "applications[1].title"),
        (lambda a: a.pop("jobright_id"), "applications[1].jobright_id"),
        (lambda a: a.update(applied_on="yesterday"), "applications[1].applied_on"),
        (lambda a: a.pop("status"), "applications[1].status"),
        (lambda a: a.update(match_score="high"), "applications[1].match_score"),
        (lambda a: a.update(match_score=140), "applications[1].match_score"),
        (lambda a: a.update(match_score=None), "applications[1].match_score"),  # open job needs one
        (lambda a: a.update(job_closed="no"), "applications[1].job_closed"),
        (lambda a: a.update(responsibilities="Build things"), "applications[1].responsibilities"),
        (lambda a: a.update(core_skills=[1, 2]), "applications[1].core_skills[0]"),
    ],
)
def test_malformed_application_is_rejected_naming_the_field(
    env: Env, mutate: object, field: str
) -> None:
    bad = make_app("Globex", "SWE", 90)
    mutate(bad)  # type: ignore[operator]
    env.write_apps([make_app("Acme", "SWE", 70), bad])

    result = env.shortlist()

    assert result.code != 0
    assert field in result.err
    assert not (env.run_dir / "shortlist.json").exists()


def test_a_file_that_is_not_json_is_rejected(env: Env) -> None:
    (env.run_dir / "applications.json").write_text("{not json")

    result = env.shortlist()

    assert result.code != 0
    assert "applications.json" in result.err


def test_missing_applications_file_is_rejected(env: Env) -> None:
    result = env.shortlist()

    assert result.code != 0
    assert "applications.json" in result.err


def test_top_level_applications_list_is_required(env: Env) -> None:
    (env.run_dir / "applications.json").write_text(json.dumps({"run_date": "2026-11-12"}))

    result = env.shortlist()

    assert result.code != 0
    assert "applications" in result.err


def test_fields_outside_the_job_schema_are_discarded(env: Env) -> None:
    env.write_apps([make_app("Acme", "SWE", 70, internal_rank=5, summary="Wrong company text")])

    env.shortlist()

    saved = env.shortlist_json()["groups"][0]["applications"][0]
    assert "internal_rank" not in saved
    assert "summary" not in saved
    assert saved["responsibilities"] == ["Build things"]


def test_a_now_without_offset_is_read_in_the_configured_timezone(env: Env) -> None:
    env.write_config('timezone = "Pacific/Auckland"\n')
    env.write_apps([make_app("Acme", "SWE", 70)])  # applied_on 2026-11-12

    env.shortlist(now="2026-11-12T23:30:00")

    assert [g["company"] for g in env.shortlist_json()["groups"]] == ["Acme"]
