from types import SimpleNamespace
import pytest
from app.errors import AppError
from app.services.search import validate_answer, answer_with_graph, is_current_hit

SOURCES = [
    {
        "chunk_id": "c1",
        "talent_id": "t1",
        "name": "虚构人才甲",
        "text": "曾开发Python招聘API服务。",
        "document_id": "d1",
        "document_revision": 1,
        "talent_revision": 2,
        "page_no": 1,
        "paragraph_no": None,
    }
]


def test_fabricated_or_misattributed_citation_is_rejected():
    with pytest.raises(AppError):
        validate_answer(
            {
                "answered": True,
                "answer": "候选人有相关经历",
                "citations": [{"chunk_id": "invented", "quote": "Python"}],
            },
            SOURCES,
        )
    with pytest.raises(AppError):
        validate_answer(
            {
                "answered": True,
                "answer": "候选人会Java",
                "citations": [{"chunk_id": "c1", "quote": "会Java"}],
            },
            SOURCES,
        )


def test_valid_citation_preserves_source_location():
    result = validate_answer(
        {
            "answered": True,
            "answer": "有Python项目经验",
            "citations": [{"chunk_id": "c1", "quote": "开发Python招聘API服务"}],
        },
        SOURCES,
    )
    assert result["citations"][0]["page_no"] == 1
    assert result["citations"][0]["document_id"] == "d1"
    assert result["talent_ids"] == ["t1"]


def test_no_evidence_returns_unknown_without_invoking_model():
    class MustNotCall:
        def json_call(self, *args, **kwargs):
            raise AssertionError("无证据不应让模型编造")

    result = answer_with_graph("谁做过没有记载的项目？", [], MustNotCall())
    assert result["answered"] is False
    assert result["citations"] == []


def test_old_deleted_or_incomplete_hit_cannot_be_used():
    hit = {"entity_id": "t1", "revision": 2, "document_id": "d1", "document_revision": 1, "chunk_id": "c1"}
    chunk = SimpleNamespace(id="c1", document_id="d1", document_revision=1)
    talent = SimpleNamespace(
        id="t1", revision=2, deleted_at=None, index_status="READY", active_document_id="d1"
    )
    assert is_current_hit(hit, chunk, talent)
    talent.revision = 3
    assert not is_current_hit(hit, chunk, talent)
    talent.revision = 2
    talent.deleted_at = "deleted"
    assert not is_current_hit(hit, chunk, talent)


def test_rag_invalid_citation_gets_one_controlled_repair_and_short_ids_resolve(monkeypatch):
    import json
    from app.adapters.llm import LanguageModel
    from app.config import Settings
    from langchain_core.runnables import RunnableLambda

    replies = iter(
        [
            {
                "answered": True,
                "answer": "有Python项目经历",
                "citations": [{"chunk_id": "S999", "quote": "Python"}],
            },
            {
                "answered": True,
                "answer": "有Python项目经历",
                "citations": [{"chunk_id": "S1", "quote": "Python招聘API服务"}],
            },
        ]
    )
    monkeypatch.setattr(
        "app.adapters.llm.ChatOpenAI", lambda **kw: RunnableLambda(lambda p: json.dumps(next(replies)))
    )
    result = answer_with_graph(
        "谁有Python项目经历？", SOURCES, LanguageModel(Settings(llm_api_key="test-placeholder"))
    )
    assert result["answered"] is True and result["citations"][0]["chunk_id"] == "c1"


def test_rag_budget_allows_reasoning_and_complete_grounded_json(monkeypatch):
    import json
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from app.adapters.llm import LanguageModel
    from app.config import Settings

    complete = {
        "answered": True,
        "answer": "有Python项目经历",
        "citations": [{"chunk_id": "S1", "quote": "Python招聘API服务"}],
    }

    def provider(**kwargs):
        # 实际通道的1600上限耗尽后只有部分正文，finish_reason=length。
        if kwargs["max_tokens"] < 3200:
            return RunnableLambda(
                lambda p: AIMessage(
                    content='{"answered":true,"answer":"有Python项目经历",',
                    response_metadata={"finish_reason": "length"},
                )
            )
        return RunnableLambda(
            lambda p: AIMessage(content=json.dumps(complete), response_metadata={"finish_reason": "stop"})
        )

    monkeypatch.setattr("app.adapters.llm.ChatOpenAI", provider)
    result = answer_with_graph(
        "谁有Python项目经历？", SOURCES, LanguageModel(Settings(llm_api_key="test-placeholder"))
    )
    assert result["answered"] and result["citations"][0]["chunk_id"] == "c1"
