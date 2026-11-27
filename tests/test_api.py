from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import CandidateContact
from conftest import PASSWORD, authenticate

API = "/api/v1"


def result(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if status != 204 else None


def job(client, headers, title):
    return result(client.post(API + "/jobs", headers=headers, json={
        "title": title, "department": "跨境运营部", "headcount": 2,
        "responsibilities": "负责美国站服装类目", "required_skills": "Listing 优化", "synthetic": True,
    }), 201)


def candidate(client, headers, name, **fields):
    return result(client.post(API + "/candidates", headers=headers, json={
        "name": name, "headline": "美国站运营", "skills": "Listing、广告投放", "synthetic": True, **fields,
    }), 201)


def application(client, headers, job_id, candidate_id):
    return result(client.post(API + "/applications", headers=headers, json={
        "job_id": job_id, "candidate_id": candidate_id,
    }), 201)


def transition(client, headers, app, status, reason="集成测试人工流转"):
    return result(client.post(API + f"/applications/{app['id']}/transitions", headers=headers,
                              json={"expected_version": app["version"], "status": status, "reason": reason}))


def test_migration_ready_and_jwt_revocation(client):
    health = result(client.get("/health/ready"))
    assert health["status"] == "local_only"
    assert health["checks"]["migration"] == "ok"
    assert all(health["checks"][name] == "not_configured" for name in ["pgvector", "redis", "minio"])
    assert client.get(API + "/auth/me").status_code == 401
    assert client.post(API + "/auth/login", json={"username": "hr", "password": "incorrect"}).status_code == 401
    headers = authenticate(client, "hr")
    assert result(client.get(API + "/auth/me", headers=headers))["role"] == "hr"
    result(client.post(API + "/auth/logout", headers=headers), 204)
    assert client.get(API + "/auth/me", headers=headers).status_code == 401


def test_admin_permission_update_and_revocation(client, headers):
    admin = headers["admin"]
    body = {"username": "api-permission-hr", "name": "权限验收 HR", "role": "hr", "password": PASSWORD}
    person = result(client.post(API + "/users", headers=admin, json=body), 201)
    path = API + "/users/" + person["id"]
    employee = authenticate(client, person["username"])
    assert not result(client.get(API + "/auth/me", headers=employee))["view_contacts"]
    grant = {"role": "hr", "view_contacts": True, "reopen_applications": True, "active": True}
    result(client.patch(path, headers=admin, json=grant))
    assert result(client.get(API + "/auth/me", headers=employee))["reopen_applications"]
    result(client.patch(path, headers=admin, json={**grant, "active": False}))
    assert client.get(API + "/auth/me", headers=employee).status_code == 401
    assert client.get(API + "/jobs", headers=admin).status_code == 403
    assert client.get(API + "/candidates", headers=admin).status_code == 403
    assert client.get(API + "/audit-logs", headers=employee).status_code == 401
    result(client.patch(path, headers=admin, json=grant))
    assert client.get(API + "/auth/me", headers=employee).status_code == 401
    assert client.get(API + "/audit-logs", headers=headers["hr"]).status_code == 403


def test_candidate_contact_permissions_and_encryption(client, headers):
    email = "synthetic@example.invalid"
    person = candidate(client, headers["hr"], "Contact test", contact={"email": email, "phone": ""})
    path = API + "/candidates/" + person["id"]
    public = result(client.get(path, headers=headers["hr"]))
    assert "contact" not in public and email not in str(public)
    assert result(client.get(path + "/contact", headers=headers["hr"]))["contact"]["email"] == email
    with Session(client.app.state.engine) as db:
        assert email not in db.scalar(select(CandidateContact.encrypted).where(CandidateContact.candidate_id == person["id"]))
    for role in ["admin", "hr_standard", "interviewer"]:
        assert client.get(path + "/contact", headers=headers[role]).status_code == 403
    assert client.get(path, headers=headers["interviewer"]).status_code == 404
    assert client.post(API + "/candidates", headers=headers["hr_standard"], json={
        "name": "Should rollback", "contact": {"email": email},
    }).status_code == 403
    assert result(client.get(API + "/candidates?q=Should+rollback", headers=headers["hr"]))["total"] == 0


def test_multi_job_closure_state_feedback_and_reopen(client, headers):
    hr, interviewer = headers["hr"], headers["interviewer"]
    first = job(client, hr, "亚马逊美国站运营验收岗")
    second = job(client, hr, "美国站广告运营验收岗")
    third = job(client, hr, "独立供应链验收岗")
    person = candidate(client, hr, "虚构应聘人·云桥")
    spare = candidate(client, hr, "虚构应聘人·鱼青")
    app = application(client, hr, first["id"], person["id"])
    other = application(client, hr, second["id"], person["id"])
    closing_existing = application(client, hr, first["id"], spare["id"])
    path = API + f"/applications/{app['id']}"
    assert client.post(API + "/applications", headers=hr, json={
        "job_id": first["id"], "candidate_id": person["id"],
    }).status_code == 409
    updated_fields = {name: first[name] for name in [
        "title", "department", "headcount", "responsibilities", "required_skills", "preferred_skills", "experience", "synthetic",
    ]}
    job_path = API + f"/jobs/{first['id']}"
    edited = result(client.patch(job_path, headers=hr, json={**updated_fields, "headcount": 3, "expected_version": first["version"]}))
    assert edited["headcount"] == 3
    assert client.get(API + f"/jobs/{third['id']}", headers=interviewer).status_code == 404
    assert client.get(path, headers=interviewer).status_code == 404
    assert client.post(path + "/transitions", headers=hr, json={
        "status": "hired", "expected_version": app["version"], "reason": "试图跨过流程",
    }).status_code == 409
    interviewer_id = result(client.get(API + "/auth/me", headers=interviewer))["id"]
    app = result(client.post(path + "/assign", headers=hr, json={
        "interviewer_id": interviewer_id, "expected_version": app["version"],
    }))
    assert client.get(path, headers=interviewer).status_code == 200
    assert client.get(path, headers=headers["other_interviewer"]).status_code == 404
    assert result(client.get(job_path, headers=interviewer))["id"] == first["id"]
    assert client.get(API + "/candidates", headers=interviewer).status_code == 403
    closed = result(client.post(job_path + "/close", headers=hr, json={
        "expected_version": edited["version"], "reason": "暂停新增人选",
    }))
    assert closed["status"] == "closed"
    assert client.post(API + "/applications", headers=hr, json={
        "job_id": first["id"], "candidate_id": candidate(client, hr, "SYNTHETIC new applicant")["id"],
    }).status_code == 409
    assert result(client.get(path, headers=hr))["status"] == "collected"
    assert transition(client, hr, closing_existing, "reserve")["status"] == "reserve"
    old_version = app["version"]
    app = transition(client, hr, app, "pending_review")
    assert client.post(path + "/transitions", headers=hr, json={
        "status": "pending_interview", "expected_version": old_version, "reason": "并发旧版本",
    }).status_code == 409
    app = transition(client, hr, app, "pending_interview")
    app = transition(client, interviewer, app, "interviewing")
    app = result(client.post(path + "/feedback", headers=interviewer, json={
        "expected_version": app["version"], "notes": "人工面试记录，核实 Listing 变体经验",
    }), 201)
    assert app["status"] == "pending_decision"
    assert result(client.get(path + "/feedback", headers=hr))[0]["notes"].startswith("人工")
    app = transition(client, hr, app, "hired")
    assert client.post(path + "/reopen", headers=headers["hr_standard"], json={
        "expected_version": app["version"], "reason": "无权重开",
    }).status_code == 403
    assert client.post(path + "/reopen", headers=hr, json={
        "expected_version": app["version"], "reason": " ",
    }).status_code == 422
    reopened = result(client.post(path + "/reopen", headers=hr, json={
        "expected_version": app["version"], "reason": "人工复核需重新开启",
    }))
    assert reopened["status"] == "pending_review" and reopened["interviewer_id"] is None
    assert client.get(path, headers=interviewer).status_code == 404
    assert result(client.get(API + f"/applications/{other['id']}", headers=hr))["status"] == "collected"
    log = result(client.get(path + "/status-history", headers=hr))
    assert log[-1]["previous_status"] == "hired" and log[-1]["reason"] == "人工复核需重新开启"
    logs = result(client.get(API + "/audit-logs", headers=headers["admin"]))
    assert any(entry["action"] == "application.reopen" and entry["resource_id"] == app["id"] for entry in logs)
    assert result(client.get(API + "/dashboard", headers=hr))["pending_review"] >= 1


def test_file_roundtrip_and_interviewer_scope(client, headers):
    hr, reviewer = headers["hr"], headers["interviewer"]
    person = candidate(client, hr, "虚构附件测试")
    posting = job(client, hr, "文件验收岗位")
    app = application(client, hr, posting["id"], person["id"])
    reviewer_id = result(client.get(API + "/auth/me", headers=reviewer))["id"]
    result(client.post(API + f"/applications/{app['id']}/assign", headers=hr, json={
        "expected_version": app["version"], "interviewer_id": reviewer_id,
    }))
    route = API + f"/candidates/{person['id']}/files"
    raw_data = "仅供验收使用的简历原件".encode()
    redacted_data = "已人工移除个人联系信息的简历".encode()
    content_type = {"Content-Type": "application/octet-stream"}
    private = result(client.post(route, params={"filename": "简历.txt"}, headers={**hr, **content_type},
                                 content=raw_data), 201)
    redacted = result(client.post(route, params={"filename": "脱敏.txt", "interviewer_visible": True},
                                  headers={**hr, **content_type}, content=redacted_data), 201)
    assert client.post(route, params={"filename": "false.png"}, headers={**hr, **content_type},
                       content=raw_data).status_code == 415
    assert client.post(route, params={"filename": "big.txt"}, headers={**hr, **content_type},
                       content=b"x" * (20 * 1024 * 1024 + 1)).status_code == 413
    assert client.post(route, params={"filename": "no.txt", "interviewer_visible": True},
                       headers={**headers["hr_standard"], **content_type}, content=redacted_data).status_code == 403
    assert [item["id"] for item in result(client.get(route, headers=reviewer))] == [redacted["id"]]
    download = lambda file_id, role: client.get(API + f"/files/{file_id}/download", headers=headers[role])
    response = download(private["id"], "hr")
    assert response.content == raw_data and response.headers["content-disposition"].startswith("attachment")
    assert download(private["id"], "interviewer").status_code == 404
    assert download(redacted["id"], "other_interviewer").status_code == 404
    assert download(redacted["id"], "admin").status_code == 403
    assert download(redacted["id"], "interviewer").content == redacted_data
