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
from app.models import User, Role
from app.services.auth import hash_password, issue_token

pytestmark = pytest.mark.integration


def test_upload_is_idempotent_and_job_state_is_visible():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        user = User(
            username="上传测试_" + uuid4().hex[:10],
            password_hash=hash_password("Upload-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(user)
        session.flush()
        token = issue_token(user, settings.jwt_secret.get_secret_value())
    document = Document()
    document.add_paragraph("虚构测试简历 " + uuid4().hex)
    stream = BytesIO()
    document.save(stream)
    content = stream.getvalue()
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        first = client.post("/api/v1/resumes", headers=headers, files={"file": ("测试简历.docx", content)})
        assert first.status_code == 202
        second = client.post("/api/v1/resumes", headers=headers, files={"file": ("重复简历.docx", content)})
        assert second.status_code == 202
        assert first.json()["document_id"] == second.json()["document_id"]
        assert first.json()["task_id"] == second.json()["task_id"]
        status = client.get("/api/v1/tasks/" + first.json()["task_id"], headers=headers)
        assert status.status_code == 200
        assert status.json()["state"] in {"QUEUED", "RUNNING", "READY", "FAILED"}
        current_url = "/api/v1/resumes/" + first.json()["document_id"] + "/download"
        assert client.get(current_url, headers=headers, follow_redirects=False).status_code == 307
        document.add_paragraph("替换后的新版本 " + uuid4().hex)
        replacement = BytesIO()
        document.save(replacement)
        updated = client.post(
            "/api/v1/resumes",
            headers=headers,
            data={"talent_id": first.json()["talent_id"]},
            files={"file": ("替换简历.docx", replacement.getvalue())},
        )
        assert updated.status_code == 202
        assert client.get(current_url, headers=headers, follow_redirects=False).status_code == 409
        assert (
            client.get(
                "/api/v1/resumes/" + updated.json()["document_id"] + "/download",
                headers=headers,
                follow_redirects=False,
            ).status_code
            == 307
        )
    engine.dispose()
