"""版本化、可手算的双向匹配规则，不使用人口属性或自由生成总分。"""

import math
from app.errors import AppError
from app.services.extraction import normalize

ALGORITHM_VERSION = "v1-50-30-20-bgem3"
ALIASES = {
    "python3": "python",
    "python3.x": "python",
    "node.js": "nodejs",
    "vue.js": "vue",
    "vue3": "vue",
    "react.js": "react",
    "mysql数据库": "mysql",
    "postgres": "postgresql",
}


def skill_name(value):
    key = normalize(value)
    return ALIASES.get(key, key)


def evaluate_hard_conditions(job: dict, profile: dict) -> dict:
    conditions = job.get("hard_conditions", {}) if job.get("hard_confirmed") else {}
    items = []
    skills = {skill_name(s) for s in profile.get("skills", [])}
    for skill in conditions.get("required_skills", []):
        state = "PASS" if skill_name(skill) in skills else "UNKNOWN"
        items.append(
            {
                "condition": "必需技能：" + skill,
                "state": state,
                "message": "有原文依据" if state == "PASS" else "简历未提供足够证据，需人工复核",
            }
        )
    minimum = conditions.get("min_experience_months")
    if minimum is not None:
        actual = profile.get("experience_months")
        state = (
            "PASS"
            if minimum == 0
            else ("UNKNOWN" if actual is None else ("PASS" if actual >= minimum else "FAIL"))
        )
        items.append(
            {
                "condition": f"相关经验至少{minimum}个月",
                "state": state,
                "message": "相关经验未提供" if actual is None else f"已核验相关经验：{actual}个月",
            }
        )
    for project in conditions.get("required_projects", []):
        present = any(normalize(project) in normalize(p) for p in profile.get("projects", []))
        items.append(
            {
                "condition": "必需项目：" + project,
                "state": "PASS" if present else "UNKNOWN",
                "message": "有原文依据" if present else "缺少对应项目证据，需复核",
            }
        )
    state = (
        "FAIL"
        if any(i["state"] == "FAIL" for i in items)
        else ("UNKNOWN" if any(i["state"] == "UNKNOWN" for i in items) else "PASS")
    )
    return {
        "state": state,
        "items": items,
        "unknown_conditions": [i["condition"] for i in items if i["state"] == "UNKNOWN"],
    }


def match_pair(job: dict, profile: dict, semantic: float) -> dict:
    if not math.isfinite(semantic):
        raise AppError("INVALID_SIMILARITY", "向量相关分无效", 502)
    requirements = job.get("requirements", {})
    hard = job.get("hard_conditions", {}) if job.get("hard_confirmed") else {}
    unique = {}
    for skill in requirements.get("skills", []) + hard.get("required_skills", []):
        unique.setdefault(skill_name(skill), skill)
    required = list(unique.values())
    skills = {skill_name(s) for s in profile.get("skills", [])}
    matched = [s for s in required if skill_name(s) in skills]
    gaps = [s for s in required if skill_name(s) not in skills]
    components = []
    if required:
        components.append({"name": "技能覆盖", "weight": 0.5, "value": len(matched) / len(required)})
    coverage = []
    unknown = []
    minimum = hard.get("min_experience_months")
    if minimum is None:
        minimum = requirements.get("experience_months")
    if minimum is not None and minimum > 0:
        months = profile.get("experience_months")
        coverage.append(min(months / minimum, 1) if months is not None else 0)
        if months is None:
            unknown.append("相关经验月数未提供")
    projects = list(dict.fromkeys(requirements.get("projects", []) + hard.get("required_projects", [])))
    if projects:
        covered = sum(
            any(normalize(r) in normalize(p) for p in profile.get("projects", [])) for r in projects
        )
        coverage.append(covered / len(projects))
        gaps.extend(
            "项目：" + r
            for r in projects
            if not any(normalize(r) in normalize(p) for p in profile.get("projects", []))
        )
    if coverage:
        components.append({"name": "经历与项目", "weight": 0.3, "value": sum(coverage) / len(coverage)})
    components.append({"name": "语义相关", "weight": 0.2, "value": max(0, min(1, semantic))})
    total_weight = sum(c["weight"] for c in components)
    for item in components:
        item["effective_weight"] = round(item["weight"] / total_weight, 6)
        item["points"] = round(100 * item["weight"] * item["value"] / total_weight, 2)
        item["value"] = round(item["value"], 6)
    decision = evaluate_hard_conditions(job, profile)
    return {
        "score": round(sum(c["points"] for c in components), 2),
        "components": components,
        "matched_skills": matched,
        "gaps": gaps,
        "unknown_information": unknown,
        "hard_conditions": decision,
        "algorithm_version": ALGORITHM_VERSION,
        "disclaimer": "岗位相关辅助排序，不代表录用概率；请结合原文人工判断",
    }


def rank_matches(job: dict, candidates: list[dict]) -> dict:
    result = {"eligible": [], "needs_review": [], "excluded_count": 0}
    for candidate in candidates:
        match = match_pair(job, candidate["facts"], candidate.get("semantic", 0))
        if match["hard_conditions"]["state"] == "FAIL":
            result["excluded_count"] += 1
            continue
        item = {k: v for k, v in candidate.items() if k not in {"facts", "semantic"}} | match
        group = "needs_review" if match["hard_conditions"]["state"] == "UNKNOWN" else "eligible"
        result[group].append(item)
    for group in ("eligible", "needs_review"):
        result[group].sort(key=lambda x: (-x["score"], x.get("id", "")))
    return result
