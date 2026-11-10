from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, func

from app.controllers.deps import db, permission
from app.models import Job
from app.schemas import JobInput
from app.services.jobs import job_view, get_job, save_job, matches
from app.services.tasks import dispatch_task

router = APIRouter(tags=["岗位与双向匹配"])


@router.get("/jobs")
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session=Depends(db),
    actor=Depends(permission("jobs:read")),
):
    records = session.scalars(
        select(Job).order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [job_view(j) for j in records],
        "total": session.scalar(select(func.count()).select_from(Job)),
        "page": page,
        "page_size": page_size,
    }


@router.post("/jobs", status_code=202)
def create_job(
    body: JobInput, request: Request, session=Depends(db), actor=Depends(permission("jobs:write"))
):
    job, task = save_job(session, body, actor.id)
    session.commit()
    dispatch_task(task.id, request.app.state.settings)
    return job_view(job) | {"task_id": task.id}


@router.get("/jobs/{job_id}")
def detail(job_id: str, session=Depends(db), actor=Depends(permission("jobs:read"))):
    return job_view(get_job(session, job_id))


@router.patch("/jobs/{job_id}")
def edit_job(
    job_id: str,
    body: JobInput,
    request: Request,
    session=Depends(db),
    actor=Depends(permission("jobs:write")),
):
    job, task = save_job(session, body, actor.id, job_id)
    session.commit()
    dispatch_task(task.id, request.app.state.settings)
    return job_view(job) | {"task_id": task.id}


@router.post("/jobs/{job_id}/matches")
def candidates(job_id: str, request: Request, session=Depends(db), actor=Depends(permission("jobs:read"))):
    return matches(session, job_id, "job", request.app.state.settings, request.app.state.redis)


@router.post("/talents/{talent_id}/job-matches")
def positions(
    talent_id: str, request: Request, session=Depends(db), actor=Depends(permission("talents:read"))
):
    return matches(session, talent_id, "talent", request.app.state.settings, request.app.state.redis)
