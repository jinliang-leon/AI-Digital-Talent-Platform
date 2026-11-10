from io import BytesIO
from uuid import uuid4
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import seed_roles
from app.config import Settings
from app.db import make_session_factory
from app.main import create_app
from app.models import User, Role, Task
from app.services.auth import hash_password, issue_token
from app.worker import process_task

pytestmark = pytest.mark.integration


class Model:
    def extract_resume(self, chunks):
        return {
            "name": "双向验证人才",
            "skills": ["Python"],
            "experience_months": 24,
            "projects": ["API服务"],
            "evidence": [
                {
                    "field": "name",
                    "value": "双向验证人才",
                    "quote": "姓名：双向验证人才",
                    "chunk_id": chunks[0]["id"],
                },
                {"field": "skills", "value": "Python", "quote": "技能：Python", "chunk_id": chunks[0]["id"]},
                {
                    "field": "experience_months",
                    "value": 24,
                    "quote": "相关经验：24个月",
                    "chunk_id": chunks[0]["id"],
                },
                {
                    "field": "projects",
                    "value": "API服务",
                    "quote": "项目：API服务",
                    "chunk_id": chunks[0]["id"],
                },
            ],
        }

    def extract_job(self, text):
        return {"skills": ["Python"], "experience_months": 24, "projects": ["API服务"]}


def test_bidirectional_scores_use_same_vector_pair_and_deleted_talent_is_filtered():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        user = User(
            username="双向验证_" + uuid4().hex[:10],
            password_hash=hash_password("Pair-match-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(user)
        session.flush()
        token = issue_token(user, settings.jwt_secret.get_secret_value())
    document = Document()
    document.add_paragraph(
        "姓名：双向验证人才。技能：Python。相关经验：24个月。项目：API服务。" + uuid4().hex
    )
    stream = BytesIO()
    document.save(stream)
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        uploaded = client.post(
            "/api/v1/resumes", headers=headers, files={"file": ("双向验证.docx", stream.getvalue())}
        ).json()
        process_task(uploaded["task_id"], settings, Model())
        job = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "title": "Python工程师",
                "raw_jd": "招聘Python工程师，相关经验24个月，负责API服务开发与维护。",
                "hard_conditions": {"min_experience_months": 24},
                "hard_confirmed": True,
            },
        )
        assert job.status_code == 202
        job = job.json()
        process_task(job["task_id"], settings, Model())
        with factory() as session:
            assert session.get(Task, job["task_id"]).state == "READY"
        forward = client.post("/api/v1/jobs/" + job["id"] + "/matches", headers=headers)
        assert forward.status_code == 200, forward.text
        first = next(i for i in forward.json()["eligible"] if i["talent_id"] == uploaded["talent_id"])
        reverse = client.post("/api/v1/talents/" + uploaded["talent_id"] + "/job-matches", headers=headers)
        assert reverse.status_code == 200, reverse.text
        second = next(i for i in reverse.json()["eligible"] if i["job_id"] == job["id"])
        assert first["score"] == second["score"]
        assert first["evidence"]
        assert client.delete("/api/v1/talents/" + uploaded["talent_id"], headers=headers).status_code == 200
        after = client.post("/api/v1/jobs/" + job["id"] + "/matches", headers=headers).json()
        assert not any(
            i["talent_id"] == uploaded["talent_id"] for i in after["eligible"] + after["needs_review"]
        )
        rag = client.post(
            "/api/v1/search",
            headers=headers,
            json={"question": "谁有Python项目经历？", "filters": {"talent_id": uploaded["talent_id"]}},
        )
        assert rag.status_code == 200 and rag.json()["answered"] is False
    engine.dispose()


def test_source_changed_during_embedding_is_not_ranked(monkeypatch):
    from app.models import Job
    from app.services.jobs import matches
    from app.errors import AppError

    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        user = User(username="并发匹配_" + uuid4().hex, password_hash="unused")
        session.add(user)
        session.flush()
        job = Job(title="并发测试岗位", raw_jd="测试原文", created_by=user.id, index_status="READY")
        session.add(job)
        session.flush()
        job_id = job.id

    def change_source(*args):
        with factory.begin() as other:
            item = other.get(Job, job_id)
            item.revision += 1
            item.index_status = "PENDING"
        return [0.1] * 1024

    monkeypatch.setattr("app.services.jobs.query_vector", change_source)
    with factory() as session:
        with pytest.raises(AppError, match="已更新"):
            matches(session, job_id, "job", settings)
    engine.dispose()
