from datetime import timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..db import session
from ..models import LoginSession, User, utcnow

DB = Annotated[Session, Depends(session)]
bearer = HTTPBearer(auto_error=False)
passwords = PasswordHasher()
dummy_hash = passwords.hash("dummy-hash-for-timing")


def verify(password, digest):
    try:
        return passwords.verify(digest or dummy_hash, password) and digest is not None
    except (VerificationError, InvalidHash):
        return False


def authenticate(request: Request, db: DB, credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    unauthorized = HTTPException(401, "请重新登录", headers={"WWW-Authenticate": "Bearer"})
    if credential is None:
        raise unauthorized
    try:
        claims = jwt.decode(credential.credentials, request.app.state.settings.jwt_secret,
                            algorithms=["HS256"], issuer="talent-hub", audience="talent-hub-web",
                            options={"require": ["exp", "iat", "sub", "jti"]})
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    login = db.get(LoginSession, claims["jti"])
    user = db.get(User, claims["sub"])
    if not login or login.user_id != claims["sub"] or login.revoked_at is not None or not user or not user.active:
        raise unauthorized
    request.state.login = login
    return user


Actor = Annotated[User, Depends(authenticate)]


def issue_token(request, db, user):
    now = utcnow()
    login = LoginSession(user_id=user.id, expires_at=now + timedelta(hours=2))
    db.add(login)
    db.flush()
    token = jwt.encode({"sub": user.id, "jti": login.id, "iat": now, "exp": login.expires_at,
                        "iss": "talent-hub", "aud": "talent-hub-web"},
                       request.app.state.settings.jwt_secret, algorithm="HS256")
    return token


def require_role(user, *roles):
    if user.role not in roles:
        raise HTTPException(403, "没有此操作权限")


def can_read_raw(user):
    return user.role == "hr" and user.view_contacts
