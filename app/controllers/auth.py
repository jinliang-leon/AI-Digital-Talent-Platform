from hashlib import sha256
from secrets import token_urlsafe
from time import time

from fastapi import APIRouter, Depends, Request, Response
from redis.exceptions import RedisError
from sqlalchemy import select

from app.controllers.deps import db, current_user
from app.errors import AppError
from app.models import User
from app.schemas import LoginInput
from app.services.auth import issue_token, decode_token, verify_password, role_codes

router = APIRouter(prefix="/auth", tags=["登录与账号"])


def user_view(user):
    return {
        "id": user.id,
        "username": user.username,
        "roles": sorted(role_codes(user)),
        "is_active": user.is_active,
    }


@router.post("/login")
def login(body: LoginInput, request: Request, response: Response, session=Depends(db)):
    settings, cache = request.app.state.settings, request.app.state.redis
    identity = sha256(f"{request.client.host}:{body.username}".encode()).hexdigest()
    key = f"{settings.project_namespace}:login-attempts:{identity}"
    try:
        attempts = cache.incr(key)
        if attempts == 1:
            cache.expire(key, 300)
        if attempts > 10:
            raise AppError("LOGIN_THROTTLED", "登录尝试过于频繁，请五分钟后重试", 429)
        user = session.scalar(select(User).where(User.username == body.username))
        if not user or not user.is_active or not verify_password(body.password, user.password_hash):
            raise AppError("LOGIN_FAILED", "用户名或密码不正确", 401)
        cache.delete(key)
    except RedisError as exc:
        raise AppError("AUTH_UNAVAILABLE", "登录服务暂不可用", 503) from exc
    token = issue_token(user, settings.jwt_secret.get_secret_value())
    secure = settings.app_env == "production"
    response.set_cookie("access_token", token, httponly=True, secure=secure, samesite="lax", max_age=3600)
    csrf = token_urlsafe(32)
    response.set_cookie("csrf_token", csrf, secure=secure, samesite="strict", max_age=3600)
    return {"user": user_view(user), "access_token": token, "token_type": "bearer", "csrf_token": csrf}


@router.get("/me")
def me(user=Depends(current_user)):
    return user_view(user)


@router.post("/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    token = request.headers.get("authorization", "")[7:] or request.cookies.get("access_token", "")
    claims = decode_token(token, request.app.state.settings.jwt_secret.get_secret_value())
    ttl = max(1, int(claims["exp"] - time()))
    try:
        request.app.state.redis.setex(
            f"{request.app.state.settings.project_namespace}:revoked:{claims['jti']}", ttl, "1"
        )
    except RedisError as exc:
        raise AppError("AUTH_UNAVAILABLE", "暂时无法退出，请稍后重试", 503) from exc
    response.delete_cookie("access_token")
    response.delete_cookie("csrf_token")
    return {"message": "已退出登录"}
