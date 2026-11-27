from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import func, select

from ..core.records import get_or_404, record, update_version
from ..core.schema import Input, Reason, Short, Text, Version
from ..core.security import Actor, DB, require_role
from ..models import Application, Job

router = APIRouter(prefix="/jobs")


class JobInput(Input):
    title: Short
    department: Short = Field(max_length=100)
    headcount: int = Field(ge=1, le=1000)
    responsibilities: Reason
    required_skills: Reason
    preferred_skills: Text = ""
    experience: Text = ""
    synthetic: bool = False


class JobUpdate(JobInput, Version):
    pass


class Close(Version):
    reason: Reason


def job_view(job):
    return {key: getattr(job, key) for key in (
        "id", "title", "department", "headcount", "responsibilities", "required_skills", "preferred_skills",
        "experience", "status", "version", "owner_id", "synthetic", "created_at", "updated_at"
    )}


def job_scope(stmt, actor):
    require_role(actor, "hr", "interviewer")
    if actor.role == "interviewer":
        stmt = stmt.where(Job.id.in_(select(Application.job_id).where(Application.interviewer_id == actor.id)))
    return stmt


@router.get("")
def list_jobs(db: DB, actor: Actor, q: str = Query("", max_length=100), offset: int = Query(0, ge=0),
              limit: int = Query(20, ge=1, le=100)):
    stmt = job_scope(select(Job), actor)
    if q.strip():
        stmt = stmt.where(Job.title.contains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Job.created_at.desc(), Job.id).offset(offset).limit(limit))
    return {"items": [job_view(job) for job in rows], "total": total}


@router.get("/{identifier}")
def get_job(identifier: str, db: DB, actor: Actor):
    job = db.scalar(job_scope(select(Job).where(Job.id == identifier), actor))
    if not job:
        raise HTTPException(404, "岗位不存在或不可访问")
    return job_view(job)


@router.post("", status_code=201)
def create_job(body: JobInput, db: DB, actor: Actor):
    require_role(actor, "hr")
    job = Job(**body.model_dump(), owner_id=actor.id)
    db.add(job)
    db.flush()
    record(db, actor, "job.create", "job", job.id)
    db.commit()
    return job_view(job)


@router.patch("/{identifier}")
def edit_job(identifier: str, body: JobUpdate, db: DB, actor: Actor):
    require_role(actor, "hr")
    job = get_or_404(db, Job, identifier)
    update_version(db, job, body.expected_version, **body.model_dump(exclude={"expected_version"}))
    record(db, actor, "job.edit", "job", job.id)
    db.commit()
    return job_view(job)


@router.post("/{identifier}/close")
def close_job(identifier: str, body: Close, db: DB, actor: Actor):
    require_role(actor, "hr")
    job = get_or_404(db, Job, identifier)
    if job.status == "closed":
        raise HTTPException(409, "岗位已关闭")
    update_version(db, job, body.expected_version, status="closed")
    record(db, actor, "job.close", "job", job.id, body.reason)
    db.commit()
    return job_view(job)
