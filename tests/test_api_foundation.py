"""覆盖实际 HTTP 边界，不使用 SQLite 代替 MySQL。"""

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


def test_live_endpoint_and_cookie_csrf_boundary():
    app = create_app(Settings(jwt_secret="test-only-32-character-secret-value"))
    with TestClient(app) as client:
        assert client.get("/health/live").json()["status"] == "ok"
        client.cookies.set("access_token", "untrusted-cookie")
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_FAILED"


def test_anonymous_admin_call_is_rejected_before_database_access():
    with TestClient(create_app(Settings(jwt_secret="test-only-32-character-secret-value"))) as client:
        response = client.get("/api/v1/users")
        assert response.status_code == 401


def test_validation_response_does_not_echo_password():
    with TestClient(create_app(Settings(jwt_secret="test-only-32-character-secret-value"))) as client:
        response = client.post(
            "/api/v1/auth/login", json={"username": "", "password": "sensitive-test-password"}
        )
        assert response.status_code == 422
        assert "sensitive-test-password" not in response.text


def test_unrelated_authorization_header_cannot_bypass_cookie_csrf():
    with TestClient(create_app(Settings(jwt_secret="test-only-32-character-secret-value"))) as client:
        client.cookies.set("access_token", "untrusted-cookie")
        response = client.post("/api/v1/auth/logout", headers={"Authorization": "Basic irrelevant"})
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_FAILED"


def test_readiness_fails_closed_without_exposing_configuration(monkeypatch):
    monkeypatch.setattr(
        "app.main.integration_status",
        lambda *a: [{"name": "MySQL", "ok": False, "message": "连接不可用"}],
        raising=False,
    )
    with TestClient(create_app(Settings())) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}
        assert client.get("/api/v1/admin/integrations/status").status_code == 401


def test_resume_body_is_bounded_before_multipart_or_database():
    with TestClient(create_app(Settings(max_upload_bytes=32))) as client:
        response = client.post(
            "/api/v1/resumes",
            content=b"x" * 70000,
            headers={"content-type": "multipart/form-data; boundary=test"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "FILE_TOO_LARGE"
        # 没有 Content-Length 的分块上传同样受限。
        response = client.post(
            "/api/v1/resumes",
            content=iter([b"x" * 35000, b"x" * 35000]),
            headers={"content-type": "multipart/form-data; boundary=test"},
        )
        assert response.status_code == 413
