"""`picked_contacts.json` tells Claude which Resume Variant to read before it writes a Company Group's emails."""
from __future__ import annotations

from conftest import VARIANT_FILES, Env, prepared_inputs


def test_each_picked_group_names_the_resume_variant_of_its_best_role(env: Env) -> None:
    prepared_inputs(env, roles=[("Backend Engineer", 70), ("Machine Learning Engineer", 90)])

    [group] = env.picked_json()["groups"]

    assert group["resume_variant"] == "ml"
    assert group["resume_file"] == str(env.root / "resumes" / VARIANT_FILES["ml"])


def test_a_group_with_an_ordinary_role_gets_the_software_resume(env: Env) -> None:
    prepared_inputs(env, roles=[("Backend Engineer", 90)])

    [group] = env.picked_json()["groups"]

    assert group["resume_variant"] == "software"
