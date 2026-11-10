import pytest
from app.errors import AppError
from app.adapters.llm import parse_json_object


def test_json_fence_is_supported_but_non_object_is_rejected():
    assert parse_json_object('```json\n{"skills":["Python"]}\n```')["skills"] == ["Python"]
    with pytest.raises(AppError):
        parse_json_object('["not an object"]')
    with pytest.raises(AppError):
        parse_json_object("我不知道")


def test_resume_schema_repairs_combined_skills_and_unit_bearing_number(monkeypatch):
    import json
    from types import SimpleNamespace
    from pydantic import SecretStr
    from langchain_core.runnables import RunnableLambda
    from app.adapters.llm import LanguageModel

    # 真实 Spark 响应曾把多项技能合并，并给整数证据加单位；必须修复后才进入事实核验。
    invalid = {
        "name": None,
        "skills": ["Python", "MySQL"],
        "experience_months": 12,
        "projects": [],
        "experiences": [],
        "education": [],
        "evidence": [
            {"field": "skills", "value": "Python、MySQL", "quote": "技能：Python、MySQL", "chunk_id": "c"},
            {"field": "experience_months", "value": "12个月", "quote": "经验：12个月", "chunk_id": "c"},
        ],
    }
    valid = {
        **invalid,
        "evidence": [
            {"field": "skills", "value": "Python", "quote": "技能：Python、MySQL", "chunk_id": "c"},
            {"field": "skills", "value": "MySQL", "quote": "技能：Python、MySQL", "chunk_id": "c"},
            {"field": "experience_months", "value": 12, "quote": "经验：12个月", "chunk_id": "c"},
        ],
    }
    outputs = iter([invalid, valid])
    calls = []

    def respond(prompt):
        calls.append(prompt)
        return json.dumps(next(outputs))

    monkeypatch.setattr("app.adapters.llm.ChatOpenAI", lambda **kw: RunnableLambda(respond))
    settings = SimpleNamespace(
        llm_base_url="http://localhost/v1", llm_api_key=SecretStr("test"), llm_model="test"
    )
    result = LanguageModel(settings).extract_resume(
        [{"id": "c", "text": "技能：Python、MySQL。经验：12个月"}]
    )
    assert result == valid
    assert len(calls) == 2


def test_resume_repeated_schema_failure_is_visible(monkeypatch):
    from types import SimpleNamespace
    from pydantic import SecretStr
    from langchain_core.runnables import RunnableLambda
    from app.adapters.llm import LanguageModel

    monkeypatch.setattr(
        "app.adapters.llm.ChatOpenAI", lambda **kw: RunnableLambda(lambda p: '{"skills":"Python"}')
    )
    settings = SimpleNamespace(
        llm_base_url="http://localhost/v1", llm_api_key=SecretStr("test"), llm_model="test"
    )
    with pytest.raises(AppError, match="结构化"):
        LanguageModel(settings).extract_resume([{"id": "c", "text": "Python"}])


def test_resume_fabricated_quote_is_repaired_before_it_can_look_successful(monkeypatch):
    import json
    from types import SimpleNamespace
    from pydantic import SecretStr
    from langchain_core.runnables import RunnableLambda
    from app.adapters.llm import LanguageModel

    raw = {
        "name": None,
        "skills": [],
        "experience_months": None,
        "projects": [],
        "experiences": ["负责接口与优化。教育经历"],
        "education": [],
        "evidence": [
            {
                "field": "experiences",
                "value": "负责接口与优化。教育经历",
                "quote": "负责接口与优化。教育经历",
                "chunk_id": "c",
            }
        ],
    }
    correct = {
        **raw,
        "experiences": ["负责接口与缓存优化。"],
        "evidence": [
            {
                "field": "experiences",
                "value": "负责接口与缓存优化。",
                "quote": "负责接口与缓存优化。",
                "chunk_id": "c",
            }
        ],
    }
    outputs = iter([raw, correct])
    monkeypatch.setattr(
        "app.adapters.llm.ChatOpenAI", lambda **kw: RunnableLambda(lambda p: json.dumps(next(outputs)))
    )
    settings = SimpleNamespace(
        llm_base_url="http://localhost/v1", llm_api_key=SecretStr("test"), llm_model="test"
    )
    assert (
        LanguageModel(settings).extract_resume([{"id": "c", "text": "负责接口与缓存优化。\n教育经历"}])
        == correct
    )


def test_sdk_receives_explicit_timeout_and_configured_thinking_mode(monkeypatch):
    from app.config import Settings
    from app.adapters.llm import LanguageModel
    from langchain_core.runnables import RunnableLambda

    captured = []

    def build(**kwargs):
        captured.append({k: kwargs.get(k) for k in ("timeout", "max_retries", "extra_body")})
        return RunnableLambda(lambda p: '{"ok":true}')

    monkeypatch.setattr("app.adapters.llm.ChatOpenAI", build)
    LanguageModel(Settings(llm_api_key="test-placeholder", llm_timeout_seconds=45)).json_call("返回JSON", {})
    assert captured[0].get("timeout") == 45
    assert captured[0]["max_retries"] == 0
    assert captured[0].get("extra_body") == {"thinking": {"type": "disabled"}}


def test_short_model_source_id_round_trips_to_actual_chunk_without_inventing_sources(monkeypatch):
    import json
    from app.config import Settings
    from app.adapters.llm import LanguageModel
    from langchain_core.runnables import RunnableLambda

    raw = {
        "name": None,
        "skills": ["Python"],
        "experience_months": None,
        "projects": [],
        "experiences": [],
        "education": [],
        "evidence": [{"field": "skills", "value": "Python", "quote": "Python", "chunk_id": "C1"}],
    }
    monkeypatch.setattr("app.adapters.llm.ChatOpenAI", lambda **kw: RunnableLambda(lambda p: json.dumps(raw)))
    actual_id = "19ca6388-f328-42ad-b19c-07341969cd23"
    result = LanguageModel(Settings(llm_api_key="test-placeholder")).extract_resume(
        [{"id": actual_id, "text": "技能：Python"}]
    )
    assert result["evidence"][0]["chunk_id"] == actual_id
