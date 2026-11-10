"""使用真实 MySQL 和 Redis 验证角色、失效与事务边界。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import make_session_factory
from app.main import create_app
from app.models import User, Role
from app.cli import seed_roles
from app.services.auth import hash_password

pytestmark = pytest.mark.integration


def test_real_login_privileges_and_role_change():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    name = "测试_" + uuid4().hex[:12]
    with factory.begin() as session:
        seed_roles(session)
        role = session.scalar(select(Role).where(Role.code == "hr"))
        user = User(username=name, password_hash=hash_password("Integration-test-password"), roles=[role])
        session.add(user)
        session.flush()
        user_id = user.id
    try:
        with TestClient(create_app(settings)) as client:
            response = client.post(
                "/api/v1/auth/login", json={"username": name, "password": "Integration-test-password"}
            )
            assert response.status_code == 200
            token = response.json()["access_token"]
            headers = {"Authorization": "Bearer " + token}
            assert client.get("/api/v1/auth/me", headers=headers).json()["username"] == name
            assert client.get("/api/v1/users", headers=headers).status_code == 403
            with factory.begin() as session:
                account = session.get(User, user_id)
                account.auth_version += 1
            assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    finally:
        with factory.begin() as session:
            user = session.get(User, user_id)
            if user:
                user.roles.clear()
                session.flush()
                session.delete(user)
        engine.dispose()
