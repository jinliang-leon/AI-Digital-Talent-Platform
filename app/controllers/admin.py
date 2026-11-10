from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.controllers.deps import db, permission
from app.controllers.auth import user_view
from app.errors import AppError
from app.models import User, Role, AuditLog
from app.schemas import CreateUserInput, UpdateUserInput
from app.services.auth import hash_password

router = APIRouter(tags=["账号管理"])


@router.get("/admin/integrations/status")
def integrations(request: Request, actor=Depends(permission("users:manage"))):
    from app.services.health import integration_status

    return {
        "items": integration_status(
            request.app.state.settings, request.app.state.sessions, request.app.state.redis
        )
    }


def resolve_roles(session, codes):
    if not codes or not set(codes).issubset({"admin", "hr"}):
        raise AppError("INVALID_ROLES", "角色只能为管理员或 HR")
    roles = list(session.scalars(select(Role).where(Role.code.in_(set(codes)))))
    if len(roles) != len(set(codes)):
        raise AppError("NOT_INITIALIZED", "请先运行初始化命令", 503)
    return roles


@router.get("/users")
def users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session=Depends(db),
    actor=Depends(permission("users:manage")),
):
    records = session.scalars(
        select(User).order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {"items": [user_view(u) for u in records], "page": page, "page_size": page_size}


@router.post("/users", status_code=201)
def create_user(body: CreateUserInput, session=Depends(db), actor=Depends(permission("users:manage"))):
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        roles=resolve_roles(session, body.roles),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        raise AppError("USERNAME_EXISTS", "该用户名已存在", 409) from exc
    session.add(AuditLog(actor_id=actor.id, action="user.create", entity_type="user", entity_id=user.id))
    return user_view(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str, body: UpdateUserInput, session=Depends(db), actor=Depends(permission("users:manage"))
):
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user:
        raise AppError("NOT_FOUND", "账号不存在", 404)
    if user.id == actor.id and (
        body.is_active is False or (body.roles is not None and "admin" not in body.roles)
    ):
        raise AppError("SELF_LOCKOUT", "不能停用自己或移除自己的管理员角色")
    if body.roles is not None:
        user.roles = resolve_roles(session, body.roles)
    if body.is_active is not None:
        user.is_active = body.is_active
    user.auth_version += 1
    session.add(AuditLog(actor_id=actor.id, action="user.update", entity_type="user", entity_id=user.id))
    return user_view(user)
