"""固定算法的 JWT、实时账号状态与角色权限。"""

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from typing import Callable

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from app.errors import AppError

PASSWORDS = PasswordHasher()
ROLE_PERMISSIONS = {
    "admin": {"users:manage", "integrations:read"},
    "hr": {"talents:read", "talents:write", "jobs:read", "jobs:write", "search:read"},
}


def issue_token(user, secret: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user.id,
            "ver": user.auth_version,
            "jti": token_urlsafe(24),
            "iat": now,
            "exp": now + timedelta(minutes=60),
            "iss": "ai-talent",
            "aud": "talent-platform",
        },
        secret,
        algorithm="HS256",
    )


def decode_token(token: str, secret: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer="ai-talent",
            audience="talent-platform",
            options={"require": ["sub", "ver", "jti", "exp"]},
        )
        if not isinstance(claims["sub"], str) or not isinstance(claims["jti"], str):
            raise ValueError("invalid claims")
        if type(claims["ver"]) is not int:
            raise ValueError("invalid version")
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise AppError("INVALID_TOKEN", "登录已失效，请重新登录", 401) from exc


def verify_token(token: str, secret: str, load_user: Callable, is_revoked: Callable):
    claims = decode_token(token, secret)
    user = load_user(claims["sub"])
    if not user or not user.is_active or user.auth_version != claims["ver"] or is_revoked(claims["jti"]):
        raise AppError("INVALID_TOKEN", "登录已失效，请重新登录", 401)
    return user


def role_codes(user) -> set[str]:
    return {r if isinstance(r, str) else r.code for r in user.roles}


def check_permission(user, permission: str) -> None:
    if not any(permission in ROLE_PERMISSIONS.get(role, set()) for role in role_codes(user)):
        raise AppError("FORBIDDEN", "当前账号没有此操作权限", 403)


def hash_password(password: str) -> str:
    if not 10 <= len(password) <= 128:
        raise AppError("PASSWORD_POLICY", "密码长度须为 10 至 128 个字符")
    return PASSWORDS.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if len(password) > 128:
        return False
    try:
        return PASSWORDS.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False
