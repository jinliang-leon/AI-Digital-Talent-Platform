"""模型数据只能在原文核验后成为事实；不接收备注与权限字段。"""

import re
import unicodedata

FIELDS = {"name", "skills", "experience_months", "projects", "experiences", "education"}
LIST_FIELDS = {"skills", "projects", "experiences", "education"}


def normalize(text):
    return "".join(unicodedata.normalize("NFKC", str(text)).lower().split())


def months_in_quote(quote: str) -> set[int]:
    months = {int(n) for n in re.findall(r"(\d+)\s*(?:个)?月", quote)}
    for n in re.findall(r"(\d+(?:\.\d+)?)\s*年", quote):
        months.add(round(float(n) * 12))
    return months


def validate_facts(raw: dict, chunks: list[dict]) -> tuple[dict, list[dict]]:
    text_by_id = {c["id"]: c["text"] for c in chunks}
    facts = {
        "name": None,
        "skills": [],
        "experience_months": None,
        "projects": [],
        "experiences": [],
        "education": [],
    }
    valid = []
    for item in raw.get("evidence", [])[:200]:
        if not isinstance(item, dict):
            continue
        field, value, quote, chunk_id = (item.get(k) for k in ("field", "value", "quote", "chunk_id"))
        if field not in FIELDS or not isinstance(quote, str) or not 1 <= len(quote) <= 1500:
            continue
        if chunk_id not in text_by_id or quote not in text_by_id[chunk_id]:
            continue
        if field == "experience_months":
            if type(value) is not int or not 0 <= value <= 720 or value not in months_in_quote(quote):
                continue
        elif (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 2000
            or normalize(value) not in normalize(quote)
        ):
            continue
        proposed = raw.get(field)
        if field in LIST_FIELDS:
            if not isinstance(proposed, list) or value not in proposed:
                continue
            if value not in facts[field]:
                facts[field].append(value)
        else:
            if proposed != value:
                continue
            facts[field] = value
        valid.append({"field": field, "value": value, "quote": quote, "chunk_id": chunk_id})
    return facts, valid


def profile_summary(facts: dict) -> str:
    parts = []
    if facts.get("skills"):
        parts.append("已核验技能：" + "、".join(facts["skills"]))
    if facts.get("experience_months") is not None:
        parts.append(f"相关经验：{facts['experience_months']}个月")
    if facts.get("projects"):
        parts.append("项目：" + "；".join(facts["projects"][:3]))
    return "。".join(parts) or "当前未提取到足够的已核验信息，请核对原文。"
