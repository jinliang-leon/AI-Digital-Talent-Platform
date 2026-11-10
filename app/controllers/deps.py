"""服务端权限检查；Cookie 状态变更另由中间件检查 CSRF。"""

from fastapi import Depends, Request
from redis.exceptions import RedisError
from app.errors import AppError
from app.models import User
from app.services.auth import verify_token, check_permission


def db(request: Request):
    with request.app.state.sessions() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def current_user(request: Request, session=Depends(db)):
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.cookies.get("access_token")
    if not token:
        raise AppError("UNAUTHENTICATED", "请先登录", 401)
    settings = request.app.state.settings
    cache = request.app.state.redis
    try:
        return verify_token(
            token,
            settings.jwt_secret.get_secret_value(),
            lambda user_id: session.get(User, user_id),
            lambda jti: cache.exists(f"{settings.project_namespace}:revoked:{jti}"),
        )
    except RedisError as exc:
        raise AppError("AUTH_UNAVAILABLE", "登录状态服务暂不可用，请稍后重试", 503) from exc


def permission(code: str):
    def guard(user=Depends(current_user)):
        check_permission(user, code)
        return user

    return guard
