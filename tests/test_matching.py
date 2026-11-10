from app.services.matching import evaluate_hard_conditions, match_pair, rank_matches


def job(**overrides):
    return {
        "requirements": {"skills": ["Python", "FastAPI"], "experience_months": 24, "projects": []},
        "hard_confirmed": True,
        "hard_conditions": {},
        **overrides,
    }


def test_known_failure_excludes_but_missing_information_needs_review():
    spec = job(hard_conditions={"required_skills": ["Python"], "min_experience_months": 24})
    assert evaluate_hard_conditions(spec, {"skills": ["Python"], "experience_months": 12})["state"] == "FAIL"
    assert (
        evaluate_hard_conditions(spec, {"skills": ["Python"], "experience_months": None})["state"]
        == "UNKNOWN"
    )
    # 没写Java不是证明不会Java，不能按关键词缺失直接淘汰。
    assert (
        evaluate_hard_conditions(job(hard_conditions={"required_skills": ["Java"]}), {"skills": ["Python"]})[
            "state"
        ]
        == "UNKNOWN"
    )


def test_unconfirmed_extracted_hard_conditions_do_not_filter_people():
    result = evaluate_hard_conditions(
        job(hard_confirmed=False, hard_conditions={"min_experience_months": 60}),
        {"skills": ["Python"], "experience_months": 12},
    )
    assert result["state"] == "PASS"


def test_score_uses_hand_computed_weights_and_ignores_sensitive_fields():
    # 技能1/2 ->25分，经历12/24 ->15分，语义0.8 ->16分，总56。
    profile = {"skills": ["Python"], "experience_months": 12, "projects": [], "gender": "女", "age": 47}
    result = match_pair(job(), profile, 0.8)
    assert result["score"] == 56.0
    assert match_pair(job(), profile | {"gender": "男", "age": 20}, 0.8)["score"] == 56.0


def test_unspecified_criterion_removed_but_unknown_specified_criterion_scores_zero():
    spec = job(requirements={"skills": ["Python"], "experience_months": 24, "projects": []})
    assert match_pair(spec, {"skills": ["Python"], "experience_months": None}, 1)["score"] == 70.0
    no_experience_requirement = job(requirements={"skills": ["Python"], "projects": []})
    assert match_pair(no_experience_requirement, {"skills": ["Python"]}, 1)["score"] == 100.0


def test_rank_groups_unknown_separately_and_counts_excluded():
    spec = job(hard_conditions={"min_experience_months": 24})
    candidates = [
        {"id": "ok", "facts": {"skills": ["Python", "FastAPI"], "experience_months": 36}, "semantic": 0.8},
        {
            "id": "review",
            "facts": {"skills": ["Python", "FastAPI"], "experience_months": None},
            "semantic": 0.99,
        },
        {"id": "no", "facts": {"skills": ["Python", "FastAPI"], "experience_months": 12}, "semantic": 0.99},
    ]
    result = rank_matches(spec, candidates)
    assert [r["id"] for r in result["eligible"]] == ["ok"]
    assert [r["id"] for r in result["needs_review"]] == ["review"]
    assert result["excluded_count"] == 1


def test_score_is_bounded_for_extreme_similarity():
    assert 0 <= match_pair(job(), {"skills": [], "experience_months": 0}, 2)["score"] <= 100


def test_aliases_do_not_duplicate_skill_weights():
    spec = job(
        requirements={"skills": ["python", "FastAPI"]}, hard_conditions={"required_skills": ["Python3"]}
    )
    result = match_pair(spec, {"skills": ["Python"]}, 1)
    assert result["components"][0]["value"] == 0.5
    assert result["matched_skills"] == ["python"]


def test_zero_experience_requirement_accepts_unknown_experience():
    assert evaluate_hard_conditions(job(hard_conditions={"min_experience_months": 0}), {})["state"] == "PASS"
