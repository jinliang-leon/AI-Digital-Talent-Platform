"""保护令牌、角色和账号状态的实际行为；不连接外部数据库。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.services.auth import issue_token, verify_token, check_permission, hash_password, verify_password

SECRET = "test-only-secret-with-at-least-32-characters"


def principal(**overrides):
    data = dict(id="user-1", username="测试HR", is_active=True, auth_version=1, roles=["hr"])
    return SimpleNamespace(**(data | overrides))


def test_tampered_token_is_rejected():
    token = issue_token(principal(), SECRET)
    with pytest.raises(AppError) as exc:
        verify_token(token + "x", SECRET, lambda _: principal(), lambda _: False)
    assert exc.value.status == 401


def test_expired_token_is_rejected():
    token = issue_token(principal(), SECRET, now=datetime.now(timezone.utc) - timedelta(hours=2))
    with pytest.raises(AppError):
        verify_token(token, SECRET, lambda _: principal(), lambda _: False)


@pytest.mark.parametrize("changed", [dict(is_active=False), dict(auth_version=2)])
def test_disabled_or_changed_account_cannot_reuse_token(changed):
    token = issue_token(principal(), SECRET)
    with pytest.raises(AppError) as exc:
        verify_token(token, SECRET, lambda _: principal(**changed), lambda _: False)
    assert exc.value.status == 401


def test_revoked_token_is_rejected():
    token = issue_token(principal(), SECRET)
    with pytest.raises(AppError):
        verify_token(token, SECRET, lambda _: principal(), lambda _: True)


def test_hr_cannot_manage_accounts_and_admin_needs_hr_for_business():
    with pytest.raises(AppError) as exc:
        check_permission(principal(), "users:manage")
    assert exc.value.status == 403
    check_permission(principal(roles=["admin"]), "users:manage")
    with pytest.raises(AppError):
        check_permission(principal(roles=["admin"]), "talents:write")


def test_password_hash_is_salted_and_wrong_password_fails():
    first = hash_password("课程演示密码-123")
    assert first != hash_password("课程演示密码-123")
    assert verify_password("课程演示密码-123", first)
    assert not verify_password("错误密码", first)
