from secrets import token_urlsafe
from uuid import uuid4


import pytest
from alembic import command
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.cli import seed
from backend.app.core.config import ROOT, Settings
from backend.app.core.migrations import config_for
from backend.app.core.security import passwords
from backend.app.db import make_engine
from backend.app.main import create_app
from backend.app.models import User

PASSWORD = token_urlsafe(24)


@pytest.fixture(scope="module")
def client():
    directory = ROOT / ".runtime" / ("test-" + uuid4().hex)
    directory.mkdir(parents=True)
    url = "sqlite:///" + str(directory / "app.db")
    settings = Settings("local", url, token_urlsafe(48), Fernet.generate_key().decode(),
                        "", "", "", "", "unused", False, directory / "objects")
    engine = make_engine(url)
    config = config_for()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with Session(engine) as db:
        seed(db, PASSWORD)
        db.add(User(username="other_interviewer", name="未指派面试官", role="interviewer",
                    password_hash=passwords.hash(PASSWORD)))
        db.commit()
    engine.dispose()
    with TestClient(create_app(settings)) as instance:
        yield instance


def authenticate(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
def headers(client):
    return {role: authenticate(client, role) for role in
            ["hr", "admin", "hr_standard", "interviewer", "other_interviewer"]}
