from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..core.records import get_or_404, record
from ..core.schema import Input, Short
from ..core.security import Actor, DB, issue_token, passwords, require_role, verify
from ..models import Audit, LoginSession, User, utcnow

router = APIRouter()
Role = Literal["admin", "hr", "interviewer"]


class Credentials(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class UserInput(Credentials):
    name: Short
    password: str = Field(min_length=12, max_length=128)
    role: Role


class UserUpdate(Input):
    role: Role
    active: bool
    view_contacts: bool
    reopen_applications: bool


def user_view(user):
    return {key: getattr(user, key) for key in (
        "id", "username", "name", "role", "active", "view_contacts", "reopen_applications"
    )}


@router.post("/auth/login")
def login(body: Credentials, request: Request, db: DB):
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    if not verify(body.password, user.password_hash if user else None) or not user or not user.active:
        raise HTTPException(401, "账号或密码错误")
    token = issue_token(request, db, user)
    record(db, user, "auth.login", "user", user.id)
    db.commit()
    return {"access_token": token, "token_type": "bearer", "user": user_view(user)}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, db: DB, actor: Actor):
    request.state.login.revoked_at = utcnow()
    record(db, actor, "auth.logout", "user", actor.id)
    db.commit()
    return Response(status_code=204)


@router.get("/auth/me")
def me(actor: Actor):
    return user_view(actor)


@router.get("/users")
def users(db: DB, actor: Actor):
    require_role(actor, "admin")
    return [user_view(user) for user in db.scalars(select(User).order_by(User.username))]


@router.get("/interviewers")
def interviewers(db: DB, actor: Actor):
    require_role(actor, "hr")
    return [{"id": user.id, "name": user.name} for user in db.scalars(
        select(User).where(User.role == "interviewer", User.active.is_(True)).order_by(User.name)
    )]


@router.post("/users", status_code=201)
def create_user(body: UserInput, db: DB, actor: Actor):
    require_role(actor, "admin")
    username = body.username.strip().lower()
    if not 1 <= len(username) <= 80 or len(body.name) > 100:
        raise HTTPException(422, "请输入有效账号和姓名")
    user = User(username=username, name=body.name, password_hash=passwords.hash(body.password), role=body.role)
    db.add(user)
    try:
        db.flush()
        record(db, actor, "user.create", "user", user.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "账号已存在") from exc
    return user_view(user)


@router.patch("/users/{identifier}")
def change_user(identifier: str, body: UserUpdate, db: DB, actor: Actor):
    require_role(actor, "admin")
    if identifier == actor.id:
        raise HTTPException(409, "管理员不能通过此接口更改自己的角色或授权")
    if body.role != "hr" and (body.view_contacts or body.reopen_applications):
        raise HTTPException(422, "敏感授权仅适用于 HR")
    user = get_or_404(db, User, identifier)
    changes = body.model_dump()
    for key, value in changes.items():
        setattr(user, key, value)
    if not body.active:
        db.execute(update(LoginSession).where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))
    record(db, actor, "user.permissions", "user", user.id,
           ", ".join(f"{key}={value}" for key, value in changes.items()))
    db.commit()
    return user_view(user)


@router.get("/audit-logs")
def audit_logs(db: DB, actor: Actor):
    require_role(actor, "admin")
    rows = db.scalars(select(Audit).order_by(Audit.created_at.desc(), Audit.id).limit(100))
    return [{"id": row.id, "actor_id": row.actor_id, "action": row.action,
             "resource_type": row.resource_type, "resource_id": row.resource_id,
             "summary": row.summary, "created_at": row.created_at} for row in rows]
