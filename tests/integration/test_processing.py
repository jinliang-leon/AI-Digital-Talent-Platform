from uuid import uuid4
from io import BytesIO
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import seed_roles
from app.config import Settings
from app.db import make_session_factory
from app.main import create_app
from app.models import User, Role, Task, Talent, Evidence
from app.services.auth import hash_password, issue_token
from app.worker import process_task
from app.errors import AppError

pytestmark = pytest.mark.integration


class GroundedModel:
    def extract_resume(self, chunks):
        c = chunks[0]
        return {
            "name": "虚构人才甲",
            "skills": ["Python"],
            "experience_months": 24,
            "projects": ["API服务"],
            "evidence": [
                {"field": "name", "value": "虚构人才甲", "quote": "姓名：虚构人才甲", "chunk_id": c["id"]},
                {"field": "skills", "value": "Python", "quote": "技能：Python", "chunk_id": c["id"]},
                {"field": "experience_months", "value": 24, "quote": "相关经验：24个月", "chunk_id": c["id"]},
                {"field": "projects", "value": "API服务", "quote": "项目：API服务", "chunk_id": c["id"]},
            ],
        }


def test_background_pipeline_persists_evidence_and_is_idempotent():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        user = User(
            username="处理测试_" + uuid4().hex[:10],
            password_hash=hash_password("Processing-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(user)
        session.flush()
        token = issue_token(user, settings.jwt_secret.get_secret_value())
    doc = Document()
    doc.add_paragraph("姓名：虚构人才甲。技能：Python。相关经验：24个月。项目：API服务。编号：" + uuid4().hex)
    stream = BytesIO()
    doc.save(stream)
    with TestClient(create_app(settings)) as client:
        result = client.post(
            "/api/v1/resumes",
            headers={"Authorization": "Bearer " + token},
            files={"file": ("处理测试.docx", stream.getvalue())},
        ).json()
    # 此处替换的是慢速云模型与外部索引操作，MySQL/MinIO/文本解析/LangGraph 都是真实组件。
    process_task(result["task_id"], settings=settings, model=GroundedModel(), indexer=lambda *args: None)
    with factory() as session:
        task = session.get(Task, result["task_id"])
        talent = session.get(Talent, result["talent_id"])
        assert task.state == "READY"
        assert talent.facts["skills"] == ["Python"] and talent.index_status == "READY"
        assert len(list(session.scalars(select(Evidence).where(Evidence.talent_id == talent.id)))) == 4
        revision = talent.revision
    process_task(result["task_id"], settings=settings, model=GroundedModel(), indexer=lambda *args: None)
    with factory() as session:
        assert session.get(Talent, result["talent_id"]).revision == revision
        assert session.get(Task, result["task_id"]).attempts == 1
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        url = "/api/v1/talents/" + result["talent_id"]
        assert (
            client.post(url + "/notes", headers=headers, json={"content": "保留的HR备注"}).status_code == 201
        )
        reparsed = client.post(url + "/reparse", headers=headers, json={"base_revision": revision})
        assert reparsed.status_code == 202, reparsed.text
        assert (
            client.post(url + "/reparse", headers=headers, json={"base_revision": revision}).status_code
            == 409
        )
        process_task(
            reparsed.json()["task_id"], settings=settings, model=GroundedModel(), indexer=lambda *args: None
        )
        detail = client.get(url, headers=headers).json()
        assert detail["revision"] == revision + 1 and detail["facts"]["skills"] == ["Python"]
        assert client.get(url + "/notes", headers=headers).json()["items"][0]["content"] == "保留的HR备注"
        assert len(client.get(url + "/revisions", headers=headers).json()["items"]) == 2
    engine.dispose()


def test_index_failure_is_visible_and_retry_reuses_saved_facts():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        user = User(
            username="恢复测试_" + uuid4().hex[:10],
            password_hash=hash_password("Recovery-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(user)
        session.flush()
        token = issue_token(user, settings.jwt_secret.get_secret_value())
    doc = Document()
    doc.add_paragraph("姓名：虚构人才甲。技能：Python。相关经验：24个月。项目：API服务。" + uuid4().hex)
    stream = BytesIO()
    doc.save(stream)
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        result = client.post(
            "/api/v1/resumes", headers=headers, files={"file": ("恢复测试.docx", stream.getvalue())}
        ).json()

        def unavailable(*args):
            raise AppError("VECTOR_UNAVAILABLE", "向量服务不可用", 503)

        process_task(result["task_id"], settings=settings, model=GroundedModel(), indexer=unavailable)
        response = client.get("/api/v1/tasks/" + result["task_id"], headers=headers).json()
        assert response["state"] == "FAILED" and response["error_code"] == "VECTOR_UNAVAILABLE"
        assert (
            client.post("/api/v1/tasks/" + result["task_id"] + "/retry", headers=headers).status_code == 202
        )

        class NoSecondExtraction:
            def extract_resume(self, chunks):
                raise AssertionError("不应再次调用已保存的事实提取")

        process_task(
            result["task_id"], settings=settings, model=NoSecondExtraction(), indexer=lambda *args: None
        )
        assert client.get("/api/v1/tasks/" + result["task_id"], headers=headers).json()["state"] == "READY"
    with factory() as session:
        assert session.get(Task, result["task_id"]).attempts == 2
    engine.dispose()


def test_actual_bge_milvus_and_neo4j_index_is_queryable():
    from app.adapters.embedding import LocalEmbeddings
    from app.adapters.vector import VectorStore
    from app.adapters.graph import GraphStore

    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        user = User(
            username="索引测试_" + uuid4().hex[:10],
            password_hash=hash_password("Index-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(user)
        session.flush()
        token = issue_token(user, settings.jwt_secret.get_secret_value())
    doc = Document()
    doc.add_paragraph("姓名：虚构人才甲。技能：Python。相关经验：24个月。项目：API服务。" + uuid4().hex)
    stream = BytesIO()
    doc.save(stream)
    with TestClient(create_app(settings)) as client:
        result = client.post(
            "/api/v1/resumes",
            headers={"Authorization": "Bearer " + token},
            files={"file": ("真实索引测试.docx", stream.getvalue())},
        ).json()
    process_task(result["task_id"], settings=settings, model=GroundedModel())
    with factory() as session:
        task = session.get(Task, result["task_id"])
        assert task.state == "READY", (task.error_code, task.safe_error_message)
        revision = session.get(Talent, result["talent_id"]).revision
    vector = LocalEmbeddings(settings).embed_query("Python API开发经验")
    store = VectorStore(settings)
    graph = GraphStore(settings)
    try:
        hits = store.search("talent", vector, 100)
        assert any(h["entity_id"] == result["talent_id"] for h in hits)
        assert "python" in graph.skills("talent", result["talent_id"], revision)
    finally:
        store.close()
        graph.close()
        engine.dispose()
