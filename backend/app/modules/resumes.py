"""Resume batch intake, review and candidate confirmation."""
from dataclasses import asdict
from hashlib import sha256
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from urllib.parse import quote

from ..ai_resume.documents import DocumentError, TextBlock
from ..ai_resume.structured import MAX_ITEMS, ModelEvidence, redact_working_copy, verify_evidence
from ..ai_resume.worker import enqueue
from ..ai_resume.worker import read_source
from ..core.ai_client import ModelError
from ..core.records import get_or_404, record, update_version
from ..core.schema import Input, Version
from ..core.security import Actor, DB, can_read_raw
from ..models import Candidate, ResumeFile, utcnow
from ..resume_models import AsyncTask, ResumeBatch, ResumeUpload
from .candidates import CandidateInput, save_contact
from .files import read_upload, validate_file

router = APIRouter()
logger = logging.getLogger(__name__)


class BatchInput(Input):
    synthetic: bool = False
    model_authorized: bool = False


class Review(Version):
    evidence: list[ModelEvidence] = Field(default_factory=list, max_length=MAX_ITEMS)


class Confirmation(Review):
    candidate_id: str | None = Field(None, max_length=36)
    candidate: CandidateInput | None = None
    note: str = Field("", max_length=800)


def require_reviewer(actor):
    if not can_read_raw(actor):
        raise HTTPException(403, "需要 HR 敏感简历权限")


def task_view(task):
    return {field: getattr(task, field) for field in ("id", "upload_id", "kind", "status", "attempts", "error", "trace_id")}


def upload_view(upload, tasks):
    return {**{field: getattr(upload, field) for field in (
        "id", "candidate_id", "file_id", "filename", "size_bytes", "status", "error", "version", "duplicate_id"
    )}, "tasks": [task_view(task) for task in tasks]}


@router.get("/resume-batches")
def list_batches(db: DB, actor: Actor, offset: int = Query(0, ge=0)):
    require_reviewer(actor)
    batches = db.scalars(select(ResumeBatch).order_by(ResumeBatch.created_at.desc()).offset(offset).limit(30))
    return [{"id": batch.id, "created_at": batch.created_at, "upload_count": batch.upload_count} for batch in batches]


@router.post("/resume-batches", status_code=201)
def create_batch(body: BatchInput, db: DB, actor: Actor, request: Request):
    require_reviewer(actor)
    if body.model_authorized and not request.app.state.settings.external_ai_enabled:
        raise HTTPException(422, "当前环境尚未启用外部模型处理")
    batch = ResumeBatch(created_by=actor.id, **body.model_dump())
    db.add(batch)
    db.flush()
    record(db, actor, "resume.batch_create", "resume_batch", batch.id)
    db.commit()
    return {"id": batch.id}


@router.get("/resume-batches/{identifier}")
def batch_detail(identifier: str, db: DB, actor: Actor):
    require_reviewer(actor)
    batch = get_or_404(db, ResumeBatch, identifier)
    uploads = db.scalars(select(ResumeUpload).where(ResumeUpload.batch_id == batch.id)
                         .order_by(ResumeUpload.created_at)).all()
    tasks = db.scalars(select(AsyncTask).where(AsyncTask.upload_id.in_([item.id for item in uploads]))).all()
    by_upload = {item.id: [] for item in uploads}
    for task in tasks:
        by_upload[task.upload_id].append(task)
    return {"id": batch.id, "uploads": [upload_view(item, by_upload[item.id]) for item in uploads]}


def save_upload(db, actor, request, identifier, filename, data):
    storage = request.app.state.storage
    batch_lock = db.execute(update(ResumeBatch).where(ResumeBatch.id == identifier, ResumeBatch.upload_count < 50)
                            .values(upload_count=ResumeBatch.upload_count + 1))
    if batch_lock.rowcount != 1:
        get_or_404(db, ResumeBatch, identifier)
        raise HTTPException(409, "批次最多上传 50 份文件")
    try:
        media_type = validate_file(filename, data)
    except HTTPException:
        upload = ResumeUpload(batch_id=identifier, filename=filename, media_type="", size_bytes=len(data),
                              status="rejected", error="UNSUPPORTED_OR_INVALID_FILE")
        db.add(upload)
        db.flush()
        db.commit()
        return upload_view(upload, [])

    digest = sha256(data).hexdigest()
    upload = ResumeUpload(batch_id=identifier, filename=filename, digest=digest, media_type=media_type,
                          size_bytes=len(data), status="queued")
    try:
        with db.begin_nested():
            db.add(upload)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(ResumeUpload.id).where(ResumeUpload.digest == digest))
        if not existing:
            raise
        upload = ResumeUpload(batch_id=identifier, filename=filename, media_type=media_type,
                              size_bytes=len(data), status="duplicate", duplicate_id=existing)
        db.add(upload)
        db.flush()
        db.commit()
        return upload_view(upload, [])

    key = upload.id.replace("-", "")
    try:
        storage.put(key, data, media_type)
    except Exception as exc:
        logger.warning("intake storage write failed: %s", type(exc).__name__)
        raise HTTPException(503, "文件存储暂不可用") from exc
    try:
        task = enqueue(db, upload, "parse", request.state.trace_id)
        record(db, actor, "resume.upload", "resume_upload", upload.id)
        db.commit()
    except Exception:
        db.rollback()
        try:
            storage.remove(key)
        except Exception as exc:
            logger.error("intake cleanup failed: %s", type(exc).__name__)
        raise
    return upload_view(upload, [task])


@router.post("/resume-batches/{identifier}/files", status_code=202)
async def upload_batch_file(identifier: str, request: Request, db: DB, actor: Actor,
                            filename: str = Query(min_length=1, max_length=180)):
    require_reviewer(actor)
    data = await read_upload(request, filename)
    return await run_in_threadpool(save_upload, db, actor, request, identifier, filename, data)


@router.post("/files/{identifier}/parse", status_code=202)
def parse_attached_file(identifier: str, body: BatchInput, db: DB, actor: Actor, request: Request):
    require_reviewer(actor)
    file = get_or_404(db, ResumeFile, identifier)
    if file.interviewer_visible:
        raise HTTPException(422, "请选择简历原件")
    if body.model_authorized and not request.app.state.settings.external_ai_enabled:
        raise HTTPException(422, "当前环境尚未启用外部模型处理")
    try:
        with db.begin_nested():
            batch = ResumeBatch(created_by=actor.id, synthetic=body.synthetic,
                                model_authorized=body.model_authorized, upload_count=1)
            db.add(batch)
            db.flush()
            upload = ResumeUpload(batch_id=batch.id, candidate_id=file.candidate_id, file_id=file.id,
                                  filename=file.filename, digest=file.sha256, media_type=file.media_type,
                                  size_bytes=file.size_bytes, status="queued")
            db.add(upload)
            db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "已存在同内容的解析记录，请在简历解析页处理") from exc
    task = enqueue(db, upload, "parse", request.state.trace_id)
    record(db, actor, "resume.enqueue_existing", "resume_upload", upload.id)
    db.commit()
    return upload_view(upload, [task])


@router.get("/resume-uploads/{identifier}/original")
def original(identifier: str, db: DB, actor: Actor, request: Request):
    require_reviewer(actor)
    upload = get_or_404(db, ResumeUpload, identifier)
    if not upload.digest:
        raise HTTPException(404, "原件不可用")
    file = db.get(ResumeFile, upload.file_id) if upload.file_id else None
    key = file.object_key if file else upload.id.replace("-", "")
    try:
        contents = read_source(request.app.state.storage, key, upload.digest, upload.size_bytes)
    except (ModelError, DocumentError) as exc:
        raise HTTPException(503, exc.code) from exc
    record(db, actor, "resume.original_read", "resume_upload", upload.id)
    db.commit()
    return Response(contents, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(upload.filename, safe=""),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
    })


def verified_review(upload, body):
    blocks = tuple(TextBlock(**block) for block in upload.blocks)
    try:
        evidence = verify_evidence({"items": [item.model_dump() for item in body.evidence]},
                                   redact_working_copy(blocks))
    except DocumentError as exc:
        raise HTTPException(422, exc.code) from exc
    previous = {(item["section"], item["block_id"], item["quote"]): item.get("origin", "model")
                for item in upload.evidence}
    return [{**asdict(item), "origin": previous.get((item.section, item.block_id, item.quote), "human")}
            for item in evidence]


@router.get("/resume-uploads/{identifier}/draft")
def draft(identifier: str, db: DB, actor: Actor):
    require_reviewer(actor)
    upload = get_or_404(db, ResumeUpload, identifier)
    tasks = db.scalars(select(AsyncTask).where(AsyncTask.upload_id == upload.id))
    result = upload_view(upload, tasks)
    result.update(blocks=upload.blocks, evidence=upload.evidence, review=upload.review)
    return result


def reviewable(upload):
    if upload.status not in {"review_pending", "parse_failed"}:
        raise HTTPException(409, "当前状态不能审核")


@router.patch("/resume-uploads/{identifier}/draft")
def edit_draft(identifier: str, body: Review, db: DB, actor: Actor):
    require_reviewer(actor)
    upload = get_or_404(db, ResumeUpload, identifier)
    reviewable(upload)
    update_version(db, upload, body.expected_version, evidence=verified_review(upload, body))
    record(db, actor, "resume.draft_edit", "resume_upload", upload.id)
    db.commit()
    return {"version": upload.version}


@router.post("/resume-uploads/{identifier}/confirm")
def confirm(identifier: str, body: Confirmation, db: DB, actor: Actor, request: Request):
    require_reviewer(actor)
    upload = get_or_404(db, ResumeUpload, identifier)
    reviewable(upload)
    if body.candidate_id and body.candidate or not upload.candidate_id and not (body.candidate_id or body.candidate):
        raise HTTPException(422, "请选择已有候选人或创建候选人")
    if upload.candidate_id and (body.candidate or body.candidate_id not in {None, upload.candidate_id}):
        raise HTTPException(409, "原件已关联其他候选人")
    if upload.status == "parse_failed" and not body.note.strip():
        raise HTTPException(422, "解析失败时请说明人工处理原因")
    evidence = verified_review(upload, body)
    update_version(db, upload, body.expected_version, status="confirmed", evidence=evidence,
                   reviewed_by=actor.id, review={"note": body.note.strip(), "source": "human",
                                                  "at": utcnow().isoformat()})
    batch = db.get(ResumeBatch, upload.batch_id)
    if body.candidate:
        candidate = Candidate(**body.candidate.model_dump(exclude={"contact", "synthetic"}), synthetic=batch.synthetic)
        db.add(candidate)
        db.flush()
        save_contact(db, request, actor, candidate.id, body.candidate.contact)
        record(db, actor, "candidate.create", "candidate", candidate.id)
    else:
        candidate = get_or_404(db, Candidate, upload.candidate_id or body.candidate_id)
    if not upload.file_id:
        file = ResumeFile(candidate_id=candidate.id, filename=upload.filename, object_key=upload.id.replace("-", ""),
                          media_type=upload.media_type, sha256=upload.digest, size_bytes=upload.size_bytes,
                          uploaded_by=batch.created_by, interviewer_visible=False)
        db.add(file)
        db.flush()
        upload.file_id = file.id
    upload.candidate_id = candidate.id
    task = enqueue(db, upload, "index", request.state.trace_id) if evidence else None
    record(db, actor, "resume.confirm", "resume_upload", upload.id)
    db.commit()
    return upload_view(upload, [task] if task else [])


@router.get("/tasks/{identifier}")
def get_task(identifier: str, db: DB, actor: Actor):
    require_reviewer(actor)
    return task_view(get_or_404(db, AsyncTask, identifier))


@router.post("/tasks/{identifier}/retry", status_code=202)
def retry_task(identifier: str, db: DB, actor: Actor, request: Request):
    require_reviewer(actor)
    task = get_or_404(db, AsyncTask, identifier)
    upload = db.get(ResumeUpload, task.upload_id)
    status = "parse_failed" if task.kind == "parse" else "confirmed"
    if upload.status != status:
        raise HTTPException(409, "当前状态不能重试")
    # Use the same upload revision lock as review and confirmation.
    update_version(db, upload, upload.version, status="queued" if task.kind == "parse" else status, error=None)
    result = db.execute(update(AsyncTask).where(AsyncTask.id == identifier, AsyncTask.status == "failed")
                        .values(status="queued", attempts=0, error=None, available_at=utcnow(),
                                trace_id=request.state.trace_id))
    if result.rowcount != 1:
        raise HTTPException(409, "仅失败任务可以重试")
    record(db, actor, "resume.task_retry", "task", task.id)
    db.commit()
    return task_view(task)
