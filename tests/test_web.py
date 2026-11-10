from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import seed_roles
from app.config import Settings
from app.db import make_session_factory
from app.main import create_app
from app.models import User, Role
from app.services.auth import hash_password, issue_token


def test_login_page_is_chinese_and_private_page_redirects():
    with TestClient(create_app(Settings())) as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert 'lang="zh-CN"' in response.text and "登录" in response.text
        assert client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.integration
def test_hr_can_view_workspace_but_cannot_view_admin_page():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        user = User(
            username="页面测试_" + uuid4().hex[:10],
            password_hash=hash_password("Web-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(user)
        session.flush()
        token = issue_token(user, settings.jwt_secret.get_secret_value())
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        response = client.get("/talents", headers=headers)
        assert response.status_code == 200 and "人才库" in response.text
        assert (
            "password_hash" not in response.text
            and settings.jwt_secret.get_secret_value() not in response.text
        )
        assert client.get("/admin", headers=headers).status_code == 403
    engine.dispose()
