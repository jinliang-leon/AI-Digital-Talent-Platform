from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ..core.privacy import clean_model_text
from ..core.records import get_or_404, record, update_version
from ..core.schema import Input, Reason, Version
from ..core.security import Actor, DB, require_role
from ..models import Application, ApplicationHistory, Candidate, InterviewFeedback, Job, User

router = APIRouter(prefix="/applications")
TERMINAL = frozenset({"hired", "rejected", "withdrawn"})
TRANSITIONS = {
    "collected": {"pending_review"},
    "pending_review": {"pending_interview", "rejected", "withdrawn", "reserve"},
    "pending_interview": {"interviewing", "pending_review", "withdrawn"},
    "interviewing": {"withdrawn"},
    "pending_decision": {"hired", "rejected", "withdrawn", "reserve", "pending_interview"},
    "reserve": {"pending_review"},
}
Target = Literal["pending_review", "pending_interview", "interviewing", "hired", "rejected", "withdrawn", "reserve"]
Identifier = str


class ApplicationInput(Input):
    job_id: Identifier = Field(min_length=36, max_length=36)
    candidate_id: Identifier = Field(min_length=36, max_length=36)


class Assignment(Version):
    interviewer_id: Identifier | None = Field(None, min_length=36, max_length=36)


class Transition(Version):
    status: Target
    reason: Reason


class Reopen(Version):
    reason: Reason


class Feedback(Version):
    notes: str = Field(min_length=1, max_length=8000)


def transition_allowed(current, target, role, closed=False):
    if role == "interviewer":
        return current == "pending_interview" and target == "interviewing"
    if role != "hr" or current in TERMINAL:
        return False
    if closed and current != "reserve" and target in {"rejected", "withdrawn", "reserve"}:
        return True
    return target in TRANSITIONS.get(current, ())


def scope(stmt, actor):
    require_role(actor, "hr", "interviewer")
    if actor.role == "interviewer":
        stmt = stmt.where(Application.interviewer_id == actor.id)
    return stmt


def visible(db, actor, identifier):
    application = db.scalar(scope(select(Application).where(Application.id == identifier), actor))
    if application is None:
        raise HTTPException(404, "应聘记录不存在或不可访问")
    return application


def view(application, job_title, candidate_name, interviewer_name=None):
    return {"id": application.id, "job_id": application.job_id, "job_title": job_title,
            "candidate_id": application.candidate_id, "candidate_name": clean_model_text(candidate_name) or "候选人",
            "interviewer_id": application.interviewer_id, "interviewer_name": interviewer_name,
            "status": application.status, "version": application.version, "updated_at": application.updated_at}


def detail(db, application):
    interviewer = db.get(User, application.interviewer_id) if application.interviewer_id else None
    return view(application, db.get(Job, application.job_id).title, db.get(Candidate, application.candidate_id).name,
                interviewer.name if interviewer else None)


def history(db, actor, application, previous, reason, action):
    db.add(ApplicationHistory(application_id=application.id, actor_id=actor.id, previous_status=previous,
                              status=application.status, reason=reason, application_version=application.version))
    record(db, actor, action, "application", application.id, f"{previous or 'new'} -> {application.status}")


@router.get("")
def list_applications(db: DB, actor: Actor, job_id: str | None = None, candidate_id: str | None = None,
                      offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    stmt = scope(select(Application, Job.title, Candidate.name, User.name)
                 .join(Job, Job.id == Application.job_id).join(Candidate, Candidate.id == Application.candidate_id)
                 .outerjoin(User, User.id == Application.interviewer_id), actor)
    if job_id:
        stmt = stmt.where(Application.job_id == job_id)
    if candidate_id:
        stmt = stmt.where(Application.candidate_id == candidate_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Application.updated_at.desc(), Application.id).offset(offset).limit(limit))
    return {"items": [view(*row) for row in rows], "total": total}


@router.get("/{identifier}")
def get_application(identifier: str, db: DB, actor: Actor):
    return detail(db, visible(db, actor, identifier))


@router.post("", status_code=201)
def create_application(body: ApplicationInput, db: DB, actor: Actor):
    require_role(actor, "hr")
    get_or_404(db, Candidate, body.candidate_id)
    get_or_404(db, Job, body.job_id)
    # Take the job's write lock before inserting, and recheck its state in the same statement.
    open_job = db.execute(update(Job).where(Job.id == body.job_id, Job.status == "open")
                          .values(version=Job.version, updated_at=Job.updated_at))
    if open_job.rowcount != 1:
        raise HTTPException(409, "关闭的岗位不能新增应聘")
    application = Application(**body.model_dump())
    db.add(application)
    try:
        db.flush()
        history(db, actor, application, None, "创建应聘记录", "application.create")
        result = detail(db, application)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "候选人已关联该岗位") from exc
    return result


@router.post("/{identifier}/assign")
def assign(identifier: str, body: Assignment, db: DB, actor: Actor):
    require_role(actor, "hr")
    application = visible(db, actor, identifier)
    if application.status in TERMINAL:
        raise HTTPException(409, "已结案的应聘不能重新分配")
    if body.interviewer_id:
        interviewer = get_or_404(db, User, body.interviewer_id)
        if interviewer.role != "interviewer" or not interviewer.active:
            raise HTTPException(422, "请选择有效的面试官")
    update_version(db, application, body.expected_version, interviewer_id=body.interviewer_id)
    record(db, actor, "application.assign", "application", application.id, body.interviewer_id or "unassigned")
    result = detail(db, application)
    db.commit()
    return result


@router.post("/{identifier}/transitions")
def transition(identifier: str, body: Transition, db: DB, actor: Actor):
    application = visible(db, actor, identifier)
    job = db.get(Job, application.job_id)
    previous = application.status
    if not transition_allowed(previous, body.status, actor.role, job.status == "closed"):
        raise HTTPException(409, "当前状态无法执行此流转")
    if body.status == "interviewing" and not application.interviewer_id:
        raise HTTPException(409, "请先指派面试官")
    update_version(db, application, body.expected_version, status=body.status)
    history(db, actor, application, previous, body.reason, "application.transition")
    result = detail(db, application)
    db.commit()
    return result


@router.post("/{identifier}/reopen")
def reopen(identifier: str, body: Reopen, db: DB, actor: Actor):
    require_role(actor, "hr")
    if not actor.reopen_applications:
        raise HTTPException(403, "没有重开应聘的权限")
    application = visible(db, actor, identifier)
    previous = application.status
    if previous not in TERMINAL:
        raise HTTPException(409, "仅可重新开启已结案的应聘")
    update_version(db, application, body.expected_version, status="pending_review", interviewer_id=None)
    history(db, actor, application, previous, body.reason, "application.reopen")
    result = detail(db, application)
    db.commit()
    return result


@router.post("/{identifier}/feedback", status_code=201)
def submit_feedback(identifier: str, body: Feedback, db: DB, actor: Actor):
    require_role(actor, "interviewer")
    application = visible(db, actor, identifier)
    if application.status != "interviewing" or not body.notes.strip():
        raise HTTPException(409, "请开始面试并填写反馈")
    update_version(db, application, body.expected_version, status="pending_decision")
    db.add(InterviewFeedback(application_id=application.id, actor_id=actor.id, notes=body.notes.strip(),
                             application_version=application.version))
    history(db, actor, application, "interviewing", "已提交人工面试反馈", "application.feedback")
    result = detail(db, application)
    db.commit()
    return result


@router.get("/{identifier}/status-history")
def status_history(identifier: str, db: DB, actor: Actor):
    application = visible(db, actor, identifier)
    items = db.execute(select(ApplicationHistory, User.name).join(User, User.id == ApplicationHistory.actor_id)
                       .where(ApplicationHistory.application_id == application.id)
                       .order_by(ApplicationHistory.application_version))
    return [{"id": row.id, "actor_id": row.actor_id, "actor_name": name, "previous_status": row.previous_status,
             "status": row.status, "reason": row.reason, "created_at": row.created_at} for row, name in items]


@router.get("/{identifier}/feedback")
def feedback_history(identifier: str, db: DB, actor: Actor):
    application = visible(db, actor, identifier)
    query = select(InterviewFeedback, User.name).join(User, User.id == InterviewFeedback.actor_id).where(
        InterviewFeedback.application_id == application.id)
    if actor.role == "interviewer":
        query = query.where(InterviewFeedback.actor_id == actor.id)
    return [{"id": row.id, "actor_name": name, "notes": row.notes, "created_at": row.created_at}
            for row, name in db.execute(query.order_by(InterviewFeedback.application_version))]
