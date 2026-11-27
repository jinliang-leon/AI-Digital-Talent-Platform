import json
from dataclasses import replace
from secrets import token_urlsafe
from types import SimpleNamespace
from uuid import uuid4

import httpx

from backend.app.ai_resume.worker import run_once
from backend.app.modules.chat import RecruitmentAction
from backend.app.modules.interview_ai import GuideOutput
from backend.app.modules.knowledge import GroundedAnswer


def test_index_search_and_application_evidence(client, headers):
    hr = headers["hr"]
    api = "/api/v1"
    job = client.post(api + "/jobs", headers=hr, json={
        "title": "SYNTHETIC Listing role", "department": "Operations", "headcount": 1,
        "responsibilities": "US marketplace", "required_skills": "Listing, FBA", "synthetic": True,
    }).json()
    batch = client.post(api + "/resume-batches", headers=hr, json={"synthetic": True}).json()["id"]
    quote = "Amazon US Listing 改版并完成 A/B 测试"
    upload = client.post(f"{api}/resume-batches/{batch}/files?filename=integration.txt",
                         headers={**hr, "Content-Type": "application/octet-stream"},
                         content=f"SYNTHETIC\n{quote}".encode()).json()
    state = client.app.state
    assert run_once(state.engine, state.storage, state.settings)
    uri = api + "/resume-uploads/" + upload["id"]
    draft = client.get(uri + "/draft", headers=hr).json()
    assert client.post(api + "/talent-search", headers=hr, json={"query": "Listing"}).json()["items"] == []
    final = client.post(uri + "/confirm", headers=hr, json={
        "expected_version": draft["version"], "candidate": {"name": "SYNTHETIC applicant"},
        "evidence": [{"section": "project", "block_id": "txt-2", "quote": quote}],
    }).json()
    assert run_once(state.engine, state.storage, state.settings)
    search = client.post(api + "/talent-search", headers=hr, json={"query": "Listing"}).json()
    assert search["items"][0]["evidence"][0]["quote"] == quote
    scoped = {"query": "Listing", "job_id": job["id"]}
    assert client.post(api + "/talent-search", headers=hr, json=scoped).json()["items"] == []
    assert client.post(api + "/talent-search", headers=headers["interviewer"], json=scoped).status_code == 403
    app = client.post(api + "/applications", headers=hr, json={
        "job_id": job["id"], "candidate_id": final["candidate_id"],
    }).json()
    assert client.post(api + "/talent-search", headers=hr, json=scoped).json()["items"][0]["candidate_id"] == final["candidate_id"]
    response = client.post(f"{api}/applications/{app['id']}/analyses", headers=hr, json={})
    assert response.status_code == 201, response.text
    row = response.json()
    assert [item["status"] for item in row["findings"]] == ["evidence_found", "not_found"]
    assert row["job_version"] == job["version"] and row["upload_id"] == upload["id"]
    review = f"{api}/analyses/{row['id']}/review"
    assert client.post(review, headers=hr, json={"note": "确认引文"}).status_code == 200
    assert client.post(review, headers=hr, json={"note": "重复提交"}).status_code == 409
    assert client.get(f"{api}/applications/{app['id']}/interview-guides",
                      headers=headers["interviewer"]).status_code == 404


def test_knowledge_access_citations_and_versions(client, headers, monkeypatch):
    from backend.app.modules import knowledge

    hr = headers["hr"]
    api = "/api/v1"
    base = client.post(api + "/knowledge-bases", headers=hr, json={"title": "SYNTHETIC 面试制度"}).json()
    path = f"{api}/knowledge-bases/{base['id']}"
    text = "面试反馈应在 24 小时内记录。"
    doc = client.post(path + "/documents", headers=hr, json={
        "title": "SYNTHETIC 制度", "text": text, "model_authorized": True,
    }).json()
    assert client.get(path + "/documents", headers=headers["interviewer"]).status_code == 404
    answer = {"query": "面试反馈"}

    def fake_answer(settings, trace, name, schema, system, data):
        assert schema is GroundedAnswer and data["sources"][0]["quote"] == text
        reference = data["sources"][0]["reference_id"]
        return schema.model_validate({"citations": [reference]})

    monkeypatch.setattr(knowledge, "invoke", fake_answer)
    result = client.post(path + "/answer", headers=hr, json=answer)
    assert result.status_code == 200, result.text
    assert result.json()["citations"][0]["quote"] == text

    def invented(*args):
        return GroundedAnswer.model_validate({"citations": [999]})

    monkeypatch.setattr(knowledge, "invoke", invented)
    assert client.post(path + "/answer", headers=hr, json=answer).status_code == 502
    revised = client.put(api + "/knowledge-documents/" + doc["id"], headers=hr, json={
        "title": doc["title"], "text": "面试反馈应在 48 小时内记录。", "expected_version": doc["version"],
    })
    assert revised.status_code == 200, revised.text
    response = client.post(path + "/search", headers=hr, json=answer).json()
    assert "48 小时" in response["items"][0]["quote"]
    assert client.request("DELETE", api + "/knowledge-documents/" + doc["id"], headers=hr,
                          json={"expected_version": revised.json()["version"]}).status_code == 200
    assert client.post(path + "/search", headers=hr, json=answer).json()["items"] == []
    attachment = client.post(path + "/files", headers={**hr, "Content-Type": "application/octet-stream"},
                             params={"title": "附加制度", "filename": "policy.txt"}, content=text.encode())
    assert attachment.status_code == 201, attachment.text
    assert client.post(path + "/search", headers=hr, json=answer).json()["items"][0]["quote"] == text


def test_chat_function_transport_sse_and_memory(client, headers, monkeypatch):
    from backend.app.core import ai_runtime

    hr = headers["hr"]
    path = "/api/v1/chat/sessions"
    session = client.post(path, headers=hr, json={}).json()
    uri = path + "/" + session["id"]
    assert client.get(uri + "/messages", headers=headers["hr_standard"]).status_code == 404
    state = client.app.state
    requests = []

    def respond(request):
        data = json.loads(request.content)
        assert data["tool_choice"]["function"]["name"] == "RecruitmentAction"
        prompt = json.loads(data["messages"][1]["content"])
        assert "private@example.invalid" not in json.dumps(prompt)
        requests.append(prompt)
        plan = RecruitmentAction(intent="talent_search", rewritten_query="Listing")
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
            "type": "function", "function": {"name": "RecruitmentAction", "arguments": plan.model_dump_json()}
        }]}}]})

    mock = httpx.MockTransport(respond)
    client_factory = httpx.Client

    class Transport:
        Client = staticmethod(lambda: client_factory(transport=mock))

    monkeypatch.setattr(ai_runtime, "httpx", Transport)
    monkeypatch.setattr(state, "settings", replace(
        state.settings, external_ai_enabled=True, model_url="https://model.example.invalid/v1",
        model_key=token_urlsafe(24), chat_model="test-model",
    ))
    turn = {"client_id": uuid4().hex, "query": "帮我找 Listing 经验\n邮箱 private@example.invalid"}
    result = client.post(uri + "/stream", headers=hr, json=turn)
    assert result.status_code == 200, result.text
    assert result.headers["content-type"].startswith("text/event-stream")
    assert all("event: " + event in result.text for event in ["meta", "result", "done"])
    assert client.post(uri + "/stream", headers=hr, json=turn).status_code == 409
    next_turn = {"client_id": uuid4().hex, "query": "再查美国站"}
    assert "event: done" in client.post(uri + "/stream", headers=hr, json=next_turn).text
    assert "Listing" in requests[1]["recent_user_queries"][0]
    saved = client.get(uri + "/messages", headers=hr).json()
    assert len(saved) == 2 and all(row["status"] == "completed" for row in saved)


def test_langfuse_export_contains_only_span_metadata(monkeypatch):
    from backend.app.core import tracing

    calls = []

    def capture(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(tracing.httpx, "post", capture)
    settings = SimpleNamespace(langfuse_url="https://trace.example.invalid",
                               langfuse_public_key=token_urlsafe(24), langfuse_secret_key=token_urlsafe(24))
    trace = tracing.Trace(settings, "3" * 32)
    with trace.step("chat.tool", intent="knowledge_answer", document="person@example.invalid"):
        with trace.step("resume.structured_extract", model="example", prompt_tokens=32,
                        query="person@example.invalid"):
            pass
    assert trace.flush()
    url, data = calls[0]
    assert url.endswith("/api/public/otel/v1/traces")
    assert data["headers"]["x-langfuse-ingestion-version"] == "4"
    spans = data["json"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[2]["parentSpanId"] == spans[0]["spanId"]
    assert spans[1]["parentSpanId"] == spans[2]["spanId"]
    assert {item["key"] for item in spans[1]["attributes"]} == {"talent.model", "talent.prompt_tokens"}
    assert "person@example.invalid" not in json.dumps(data["json"])

    def reject_spans(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"partialSuccess": {"rejectedSpans": "1"}})

    monkeypatch.setattr(tracing.httpx, "post", reject_spans)
    assert not trace.flush()
