from datetime import timedelta

from sqlalchemy.orm import Session

from backend.app.ai_resume.worker import claim, process, run_once
from backend.app.models import utcnow
from backend.app.resume_models import AsyncTask


def test_resume_workflow(client, headers):
    hr = headers["hr"]
    other = headers["hr_standard"]
    assert client.post("/api/v1/resume-batches", headers=other, json={}).status_code == 403
    batch = client.post("/api/v1/resume-batches", headers=hr, json={"synthetic": True}).json()["id"]
    base = "/api/v1/resume-batches/" + batch
    binary = {**hr, "Content-Type": "application/octet-stream"}
    phrase = "负责 Amazon US Listing 优化"
    content = ("SYNTHETIC\n邮箱：private@example.invalid\n" + phrase).encode()
    initial = client.post(base + "/files?filename=sample.txt", headers=binary, content=content)
    assert initial.status_code == 202, initial.text
    item = initial.json()
    assert item["status"] == "queued"
    duplicate = client.post(base + "/files?filename=copy.txt", headers=binary, content=content).json()
    assert duplicate["status"] == "duplicate" and duplicate["duplicate_id"] == item["id"]
    bad = client.post(base + "/files?filename=invalid.jpg", headers=binary, content=b"xyz").json()
    assert bad["status"] == "rejected"
    state = client.app.state
    assert run_once(state.engine, state.storage, state.settings)
    path = "/api/v1/resume-uploads/" + item["id"]
    assert client.get(path + "/draft", headers=other).status_code == 403
    draft = client.get(path + "/draft", headers=hr).json()
    assert draft["status"] == "review_pending"
    assert draft["blocks"][2]["location"] == "line:3"
    body = {"expected_version": draft["version"], "candidate": {"name": "SYNTHETIC-解析验收"},
            "evidence": [{"section": "employment", "block_id": "txt-3", "quote": phrase}]}
    bad_evidence = {**body, "evidence": [{"section": "employment", "block_id": "txt-2", "quote": "private@example.invalid"}]}
    assert client.post(path + "/confirm", headers=hr, json=bad_evidence).status_code == 422
    response = client.post(path + "/confirm", headers=hr, json=body)
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert client.post(path + "/confirm", headers=hr, json=body).status_code == 409
    assert run_once(state.engine, state.storage, state.settings)
    task = client.get("/api/v1/tasks/" + confirmed["tasks"][0]["id"], headers=hr).json()
    assert task["status"] == "succeeded"
    assert client.get("/api/v1/files/" + confirmed["file_id"] + "/download", headers=hr).content == content
    assert client.get("/api/v1/candidates/" + confirmed["candidate_id"], headers=hr).json()["synthetic"]


def test_resume_failure_retry_and_manual_recovery(client, headers):
    hr = headers["hr"]
    batch = client.post("/api/v1/resume-batches", headers=hr, json={"synthetic": True}).json()["id"]
    item = client.post("/api/v1/resume-batches/" + batch + "/files?filename=broken.pdf",
                       headers={**hr, "Content-Type": "application/octet-stream"},
                       content=b"%PDF-1.7 broken payload").json()
    state = client.app.state
    assert run_once(state.engine, state.storage, state.settings)
    task = item["tasks"][0]["id"]
    path = "/api/v1/resume-uploads/" + item["id"]
    assert client.post("/api/v1/tasks/" + task + "/retry", headers=hr).status_code == 202
    assert run_once(state.engine, state.storage, state.settings)
    draft = client.get(path + "/draft", headers=hr).json()
    assert draft["status"] == "parse_failed"
    body = {"expected_version": draft["version"], "candidate": {"name": "SYNTHETIC-人工补录"}}
    assert client.post(path + "/confirm", headers=hr, json=body).status_code == 422
    assert client.post(path + "/confirm", headers=hr, json={**body, "note": "与原件人工核对"}).status_code == 200
    assert client.post("/api/v1/tasks/" + task + "/retry", headers=hr).status_code == 409


def test_expired_worker_lease_can_be_reclaimed(client, headers):
    hr = headers["hr"]
    batch = client.post("/api/v1/resume-batches", headers=hr, json={"synthetic": True}).json()["id"]
    item = client.post("/api/v1/resume-batches/" + batch + "/files?filename=lease.txt",
                       headers={**hr, "Content-Type": "application/octet-stream"},
                       content=b"SYNTHETIC lease recovery test").json()
    state = client.app.state
    first_claim = claim(state.engine)
    assert first_claim[0] == item["tasks"][0]["id"]
    with Session(state.engine) as db, db.begin():
        db.get(AsyncTask, first_claim[0]).lease_until = utcnow() - timedelta(seconds=10)
    second_claim = claim(state.engine)
    assert second_claim[0] == first_claim[0] and second_claim[1] != first_claim[1]
    process(state.engine, state.storage, state.settings, *first_claim)
    task_url = "/api/v1/tasks/" + first_claim[0]
    assert client.get(task_url, headers=hr).json()["status"] == "running"
    process(state.engine, state.storage, state.settings, *second_claim)
    status = client.get(task_url, headers=hr).json()
    assert status["status"] == "succeeded" and status["attempts"] == 2
