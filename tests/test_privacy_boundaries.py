"""Contract checks use fictional values and isolated fixtures."""
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.ai_resume.worker import run_once
from backend.app.assistant_models import ChatMessage, ChatSession, KnowledgeRevision
from backend.app.core import embedding, tracing
from backend.app.core.ai_client import ModelError
from backend.app.core.privacy import clean_model_text, mask_private
from backend.app.models import Candidate
from backend.app.modules import chat, interview_ai, knowledge
from backend.app.resume_models import AsyncTask, ResumeBatch, ResumeChunk, ResumeUpload

EMAIL = "fixture@example.invalid"
MOBILE = "10000000000"
ID = "00000000000000000X"
US_PHONE = "+1 (202) 555-0147"
ZH_ADDRESS = "测试省虚构市示例区合成路12号3栋504室"
EN_ADDRESS = "99999 Fiction Road Apt 9"
PROFESSIONAL = "面试反馈应在 24 小时内记录，use the Listing checklist。"
PRIVATE = (EMAIL, MOBILE, ID, US_PHONE, ZH_ADDRESS, EN_ADDRESS)
MIXED = "\n".join((PROFESSIONAL, f"邮箱：{EMAIL}", f"mobile: {MOBILE}", f"身份证号：{ID}",
                   f"Phone: {US_PHONE}", ZH_ADDRESS, EN_ADDRESS))


def private_absent(value):
    wire = json.dumps(value, ensure_ascii=False)
    assert all(item not in wire for item in PRIVATE)


@pytest.mark.parametrize("value", PRIVATE)
def test_mask_keeps_source_offset_and_professional_text(value):
    text = PROFESSIONAL + "\n" + value + "\n" + PROFESSIONAL
    masked = mask_private(text)
    assert len(masked) == len(text)
    assert [i for i, char in enumerate(masked) if char == "\n"] == [i for i, char in enumerate(text) if char == "\n"]
    assert masked == PROFESSIONAL + "\n" + " " * len(value) + "\n" + PROFESSIONAL


def test_chat_persist_read_memory_tool_parameters_and_results(client, headers, monkeypatch, caplog):
    hr = headers["hr"]
    sessions = "/api/v1/chat/sessions"
    scope = client.post(sessions, headers=hr, json={"title": "招聘工作\n" + EMAIL}).json()
    assert scope["title"] == "招聘工作"
    uri = f"{sessions}/{scope['id']}"
    state = client.app.state
    expected_queries = [PROFESSIONAL, "继续查 Listing 证据"]
    prompts = []

    def route(settings, trace, name, schema, instructions, data, *, tool):
        assert name == "chat.route" and tool
        private_absent(data)
        assert data["query"] == expected_queries[len(prompts)]
        assert data["recent_user_queries"] == ([PROFESSIONAL] if prompts else [])
        prompts.append(data)
        # A model argument can include sensitive data. The tool input still needs filtering.
        return schema(intent="talent_search", rewritten_query="Listing\n" + EMAIL)

    def search(db, settings, body, trace):
        assert body.query == "Listing" and not body.use_external_ai
        return {"query": body.query, "job_id": None, "mode": "hybrid_local", "items": [{
            "candidate_id": "synthetic-candidate", "name": "虚构全名",
            "resume_upload_id": "synthetic-upload", "routes": ["keyword"], "unneeded_contact": MOBILE,
            "evidence": [{"quote": "Listing", "source": "line:1"}, {"quote": EMAIL, "source": "line:2"}],
        }]}

    monkeypatch.setattr(chat, "invoke", route)
    monkeypatch.setattr(chat.talent_search, "search", search)
    first = client.post(uri + "/stream", headers=hr, json={"client_id": "first", "query": MIXED})
    assert first.status_code == 200 and "event: done" in first.text, first.text
    too_long = client.post(uri + "/stream", headers=hr, json={"client_id": "long", "query": "a" * 801})
    assert too_long.status_code == 422
    private_absent(first.text)
    assert "虚构全名" not in first.text and "Listing" in first.text
    with Session(state.engine) as db:
        session = db.get(ChatSession, scope["id"])
        turn = db.scalar(select(ChatMessage).where(ChatMessage.session_id == session.id))
        assert session.title == "招聘工作" and turn.query == PROFESSIONAL
        private_absent(turn.result)
        assert "虚构全名" not in json.dumps(turn.result, ensure_ascii=False)
        assert turn.result["data"]["items"][0]["evidence"] == [{"quote": "Listing", "source": "line:1"}]
        # Test a legacy record in this disposable database. The API does not rewrite it on read.
        session.title += "\n" + EMAIL
        turn.query = MIXED
        db.commit()

    result = client.post(uri + "/stream", headers=hr,
                         json={"client_id": "second", "query": expected_queries[1]})
    assert "event: done" in result.text and len(prompts) == 2
    assert client.get(uri + "/messages", headers=headers["hr_standard"]).status_code == 404
    listed = client.get(sessions, headers=hr).json()
    private_absent(listed)
    restored = client.get(uri + "/messages", headers=hr).json()
    assert [row["query"] for row in restored] == expected_queries
    private_absent(restored)
    private_absent(caplog.text)
    with Session(state.engine) as db:
        assert db.scalar(select(ChatMessage.query).where(ChatMessage.session_id == scope["id"],
                                                        ChatMessage.client_id == "first")) == MIXED


def test_rag_projections_permissions_and_grounded_model_output(client, headers, monkeypatch):
    hr = headers["hr"]
    base = client.post("/api/v1/knowledge-bases", headers=hr, json={"title": "SYNTHETIC policy"}).json()
    uri = f"/api/v1/knowledge-bases/{base['id']}"
    # The contact field straddles the stored 800-character boundary.
    beginning = PROFESSIONAL + "\n" + " " * (798 - len(PROFESSIONAL) - 1)
    doc = client.post(uri + "/documents", headers=hr, json={
        "title": "SYNTHETIC policy\n" + EMAIL, "text": beginning + EMAIL, "model_authorized": True,
    })
    assert doc.status_code == 201 and doc.json()["chunks"] == 2, doc.text
    denied = client.post(uri + "/documents", headers=hr, json={
        "title": "Unapproved", "text": "Listing 面试反馈：私有合成文本", "model_authorized": False,
    }).json()
    query = {"query": "面试反馈 Listing"}
    assert client.post(uri + "/search", headers=headers["interviewer"], json=query).status_code == 404
    result = client.post(uri + "/search", headers=hr, json=query).json()
    assert PROFESSIONAL in [item["quote"] for item in result["items"]]
    private_absent(result)
    with Session(client.app.state.engine) as db:
        revision = db.scalar(select(KnowledgeRevision).where(KnowledgeRevision.document_id == doc.json()["id"]))
        assert EMAIL in "".join(chunk["text"] for chunk in revision.chunks)
        assert revision.chunks[1]["vector"] == [0.0] * 256

    def answer(settings, trace, name, schema, instructions, data):
        assert name == "knowledge.answer"
        private_absent(data)
        sources = data["sources"]
        assert len(sources) == 1 and sources[0]["quote"] == PROFESSIONAL
        assert set(sources[0]) == {"reference_id", "quote"}
        return schema(citations=[sources[0]["reference_id"]])

    monkeypatch.setattr(knowledge, "invoke", answer)
    response = client.post(uri + "/answer", headers=hr, json=query)
    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["quote"] == PROFESSIONAL
    private_absent(response.json())
    assert denied["id"] != doc.json()["id"]

    def invalid_answer(settings, trace, name, schema, instructions, data):
        return schema(citations=[len(data["sources"]) + 1])

    monkeypatch.setattr(knowledge, "invoke", invalid_answer)
    response = client.post(uri + "/answer", headers=hr, json=query)
    assert response.status_code == 502
    assert response.json()["message"] == "RAG_CITATION_INVALID"
    private_absent(response.json())


def test_index_rejects_private_legacy_evidence_without_provider_or_vector(client, headers, monkeypatch):
    state = client.app.state
    with Session(state.engine) as db:
        candidate = Candidate(name="SYNTHETIC privacy index", synthetic=True)
        from backend.app.models import User
        actor = db.scalar(select(User).where(User.username == "hr"))
        batch = ResumeBatch(created_by=actor.id, synthetic=True, model_authorized=False)
        db.add_all([candidate, batch])
        db.flush()
        upload = ResumeUpload(batch_id=batch.id, candidate_id=candidate.id, filename="synthetic-privacy.txt",
                              media_type="text/plain", size_bytes=1, status="confirmed",
                              evidence=[{"quote": ZH_ADDRESS}])
        db.add(upload)
        db.flush()
        task = AsyncTask(upload_id=upload.id, kind="index", trace_id=uuid4().hex)
        db.add(task)
        db.flush()
        upload_id, task_id = upload.id, task.id
        db.commit()

    def reject_provider(*args, **kwargs):
        pytest.fail("A private quote must not be submitted")

    monkeypatch.setattr(embedding.httpx, "post", reject_provider)
    assert run_once(state.engine, state.storage, state.settings)
    with Session(state.engine) as db:
        task = db.get(AsyncTask, task_id)
        assert task.status == "failed" and task.error == "INDEX_PRIVATE_EVIDENCE"
        assert db.scalar(select(ResumeChunk).where(ResumeChunk.upload_id == upload_id)) is None

    settings = replace(state.settings, embedding_model="test-embedding")
    assert embedding.embed(settings, ["Listing"], authorized=True)[0] == "lexical-hash-256-v1"
    with pytest.raises(ModelError, match="EMBEDDING_PRIVATE_INPUT"):
        embedding.embed(replace(settings, external_ai_enabled=True), [EMAIL], authorized=True)


def test_generated_interview_fields_and_trace_values_are_validated(monkeypatch, caplog):
    question = interview_ai.Question(question="Describe a Listing experiment.",
                                     focus=MOBILE, reference_answer="Compare before and after.",
                                     followup="Explain the evidence.", evidence_block_ids=[])
    assert not interview_ai.valid_questions([question], {})

    sent = []
    def capture(url, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(tracing.httpx, "post", capture)
    from types import SimpleNamespace
    settings = SimpleNamespace(langfuse_url="https://trace.example.invalid",
                               langfuse_public_key="synthetic-public", langfuse_secret_key="synthetic-secret")
    trace = tracing.Trace(settings, uuid4().hex)
    with trace.step(EMAIL, query=MIXED, model=MOBILE, intent=ID, error_code="secret-placeholder"):
        with trace.step("chat.tool", intent="knowledge_answer", prompt_tokens=23):
            pass
    assert trace.flush()
    private_absent(sent)
    wire = json.dumps(sent)
    assert "secret-placeholder" not in wire and "talent.step" in wire and "talent.prompt_tokens" in wire
    private_absent(caplog.text)
