from hashlib import sha256
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError

from app.adapters.storage import ObjectStore
from app.controllers.deps import db, permission
from app.errors import AppError
from app.models import (
    ResumeDocument,
    Talent,
    Task,
    IndexOutbox,
    utcnow,
    Evidence,
    SourceChunk,
    TalentNote,
    TalentRevision,
    Tag,
    talent_tags,
    AuditLog,
    Job,
)
from app.schemas import ProfileUpdateInput, NoteInput, TagInput, TagsInput, ReparseInput
from app.services.profiles import get_talent, talent_view, evidence_view, update_profile, add_note
from app.services.documents import validate_upload
from app.services.tasks import dispatch_task

router = APIRouter(tags=["简历与人才"])


def task_view(task):
    return {
        "id": task.id,
        "state": task.state,
        "stage": task.stage,
        "attempts": task.attempts,
        "error_code": task.error_code,
        "message": task.safe_error_message,
        "entity_id": task.entity_id,
        "task_type": task.task_type,
        "revision": task.revision,
        "created_at": task.created_at.isoformat(),
    }


def upload_result(session, document):
    task = session.scalar(
        select(Task)
        .where(Task.entity_id == document.talent_id, Task.task_type == "resume")
        .order_by(Task.created_at.desc())
    )
    return {
        "document_id": document.id,
        "talent_id": document.talent_id,
        "task_id": task.id,
        "message": "文件已接收，等待处理",
    }


@router.post("/resumes", status_code=202)
def upload(
    request: Request,
    file: UploadFile = File(...),
    talent_id: str | None = Form(None),
    session=Depends(db),
    actor=Depends(permission("talents:write")),
):
    settings = request.app.state.settings
    content = file.file.read(settings.max_upload_bytes + 1)
    mime = validate_upload(content, file.filename or "", settings.max_upload_bytes)
    digest = sha256(content).hexdigest()
    existing = session.scalar(select(ResumeDocument).where(ResumeDocument.sha256 == digest))
    if existing:
        owner = session.get(Talent, existing.talent_id)
        if owner.deleted_at:
            raise AppError("DUPLICATE_DELETED", "该文件属于已删除档案，请使用新文件或恢复档案", 409)
        if talent_id and talent_id != existing.talent_id:
            raise AppError("DUPLICATE_FILE", "该文件已关联另一人才", 409)
        return upload_result(session, existing)
    if talent_id:
        talent = session.scalar(select(Talent).where(Talent.id == talent_id).with_for_update())
        if not talent or talent.deleted_at:
            raise AppError("NOT_FOUND", "人才不存在", 404)
        talent.revision += 1
        talent.index_status = "PENDING"
    else:
        talent = Talent(created_by=actor.id)
        session.add(talent)
        session.flush()
    filename = PurePath((file.filename or "").replace("\\", "/")).name[:250]
    key = f"resumes/{uuid4().hex}{PurePath(filename).suffix.lower()}"
    store = ObjectStore(settings)
    store.put(key, content, mime)
    document = ResumeDocument(
        talent_id=talent.id,
        original_name=filename,
        object_key=key,
        sha256=digest,
        mime_type=mime,
        size_bytes=len(content),
        revision=talent.revision,
    )
    session.add(document)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        try:
            store.remove(key)
        except AppError:
            pass
        existing = session.scalar(select(ResumeDocument).where(ResumeDocument.sha256 == digest))
        if not existing:
            raise AppError("UPLOAD_CONFLICT", "上传发生冲突，请重试", 409)
        return upload_result(session, existing)
    talent.active_document_id = document.id
    task = Task(task_type="resume", entity_id=talent.id, revision=talent.revision)
    session.add(task)
    session.flush()
    session.add(
        IndexOutbox(
            entity_type="talent",
            entity_id=talent.id,
            revision=talent.revision,
            operation="PARSE",
            task_id=task.id,
        )
    )
    session.commit()
    dispatch_task(task.id, settings)
    return {
        "document_id": document.id,
        "talent_id": talent.id,
        "task_id": task.id,
        "message": "文件已接收，等待处理",
    }


@router.get("/tasks/{task_id}")
def task_status(task_id: str, session=Depends(db), actor=Depends(permission("talents:read"))):
    task = session.get(Task, task_id)
    if not task:
        raise AppError("NOT_FOUND", "处理任务不存在", 404)
    return task_view(task)


@router.get("/tasks")
def tasks(session=Depends(db), actor=Depends(permission("talents:read"))):
    items = list(session.scalars(select(Task).order_by(Task.created_at.desc()).limit(100)))
    ids = {t.entity_id for t in items}
    names = {t.id: t.name for t in session.scalars(select(Talent).where(Talent.id.in_(ids)))}
    names.update({j.id: j.title for j in session.scalars(select(Job).where(Job.id.in_(ids)))})
    return {"items": [task_view(t) | {"entity_name": names.get(t.entity_id, "档案处理")} for t in items]}


@router.get("/stats")
def statistics(session=Depends(db), actor=Depends(permission("talents:read"))):
    return {
        "talents": session.scalar(
            select(func.count()).select_from(Talent).where(Talent.deleted_at.is_(None))
        ),
        "ready_talents": session.scalar(
            select(func.count())
            .select_from(Talent)
            .where(Talent.deleted_at.is_(None), Talent.index_status == "READY")
        ),
        "jobs": session.scalar(select(func.count()).select_from(Job).where(Job.status == "ACTIVE")),
        "pending_tasks": session.scalar(
            select(func.count()).select_from(Task).where(Task.state.in_(["QUEUED", "RUNNING"]))
        ),
    }


@router.post("/tasks/{task_id}/retry", status_code=202)
def retry_task(
    task_id: str, request: Request, session=Depends(db), actor=Depends(permission("talents:write"))
):
    task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if not task:
        raise AppError("NOT_FOUND", "处理任务不存在", 404)
    if task.state != "FAILED":
        raise AppError("INVALID_TASK_STATE", "只能重试失败的任务", 409)
    talent = session.get(Talent, task.entity_id) if task.task_type in {"resume", "talent_index"} else None
    if talent and (talent.deleted_at or talent.revision != task.revision):
        raise AppError("STALE_TASK", "档案已更新或删除，不能重试旧任务", 409)
    task.state = "QUEUED"
    task.stage = "等待重试"
    task.error_code = None
    task.safe_error_message = None
    box = session.scalar(select(IndexOutbox).where(IndexOutbox.task_id == task_id))
    if box:
        box.state = "PENDING"
    session.commit()
    dispatch_task(task_id, request.app.state.settings)
    return task_view(task)


@router.get("/resumes/{document_id}/download")
def download(
    document_id: str, request: Request, session=Depends(db), actor=Depends(permission("talents:read"))
):
    document = session.get(ResumeDocument, document_id)
    if not document:
        raise AppError("NOT_FOUND", "原文件不存在", 404)
    talent = session.get(Talent, document.talent_id)
    if not talent or talent.deleted_at:
        raise AppError("NOT_FOUND", "档案不存在", 404)
    if talent.active_document_id != document.id:
        raise AppError("STALE_DOCUMENT", "该简历已被替换，请从当前档案下载最新原文", 409)
    return RedirectResponse(
        ObjectStore(request.app.state.settings).download_url(document.object_key), status_code=307
    )


@router.get("/resumes/{document_id}/chunks")
def source_chunks(document_id: str, session=Depends(db), actor=Depends(permission("talents:read"))):
    document = session.get(ResumeDocument, document_id)
    if not document:
        raise AppError("NOT_FOUND", "原文件不存在", 404)
    get_talent(session, document.talent_id)
    rows = session.scalars(
        select(SourceChunk)
        .where(SourceChunk.document_id == document_id, SourceChunk.document_revision == document.revision)
        .order_by(SourceChunk.chunk_no)
    )
    return {
        "items": [
            {"id": c.id, "text": c.text, "page_no": c.page_no, "paragraph_no": c.paragraph_no} for c in rows
        ]
    }


@router.get("/talents")
def talent_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=100),
    tag_id: str | None = None,
    session=Depends(db),
    actor=Depends(permission("talents:read")),
):
    criteria = [Talent.deleted_at.is_(None)]
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        criteria.append(
            Talent.name.like("%" + escaped + "%", escape="\\")
            | Talent.summary.like("%" + escaped + "%", escape="\\")
        )
    if tag_id:
        criteria.append(Talent.id.in_(select(talent_tags.c.talent_id).where(talent_tags.c.tag_id == tag_id)))
    total = session.scalar(select(func.count()).select_from(Talent).where(*criteria))
    records = session.scalars(
        select(Talent)
        .where(*criteria)
        .order_by(Talent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {"items": [talent_view(t) for t in records], "total": total, "page": page, "page_size": page_size}


@router.get("/talents/{talent_id}")
def talent_detail(talent_id: str, session=Depends(db), actor=Depends(permission("talents:read"))):
    result = talent_view(get_talent(session, talent_id))
    result["tags"] = [
        {"id": t.id, "name": t.name}
        for t in session.scalars(select(Tag).join(talent_tags).where(talent_tags.c.talent_id == talent_id))
    ]
    return result


@router.patch("/talents/{talent_id}")
def correct_talent(
    talent_id: str,
    body: ProfileUpdateInput,
    request: Request,
    session=Depends(db),
    actor=Depends(permission("talents:write")),
):
    item = update_profile(
        session, talent_id, body.base_revision, body.field_changes, body.evidence_refs, actor.id
    )
    task = session.scalar(
        select(Task).where(
            Task.entity_id == talent_id, Task.revision == item.revision, Task.task_type == "talent_index"
        )
    )
    session.commit()
    dispatch_task(task.id, request.app.state.settings)
    return talent_view(item) | {"task_id": task.id}


@router.get("/talents/{talent_id}/evidence")
def evidence(talent_id: str, session=Depends(db), actor=Depends(permission("talents:read"))):
    talent = get_talent(session, talent_id)
    rows = session.execute(
        select(Evidence, SourceChunk)
        .join(SourceChunk, SourceChunk.id == Evidence.chunk_id)
        .where(Evidence.talent_id == talent_id, Evidence.talent_revision == talent.revision)
    )
    return {"items": [evidence_view(row, chunk) for row, chunk in rows]}


@router.post("/talents/{talent_id}/reparse", status_code=202)
def reparse(
    talent_id: str,
    body: ReparseInput,
    request: Request,
    session=Depends(db),
    actor=Depends(permission("talents:write")),
):
    talent = get_talent(session, talent_id, lock=True)
    if talent.revision != body.base_revision:
        raise AppError("REVISION_CONFLICT", "档案已更新，请刷新后再重新解析", 409)
    if talent.index_status in {"PENDING", "PARTIAL"}:
        raise AppError("TASK_IN_PROGRESS", "当前档案正在处理，请等待任务完成", 409)
    if not talent.active_document_id:
        raise AppError("NOT_FOUND", "没有可重新解析的简历", 404)
    talent.revision += 1
    talent.index_status = "PENDING"
    task = Task(task_type="resume", entity_id=talent.id, revision=talent.revision)
    session.add(task)
    session.flush()
    session.add(
        IndexOutbox(
            entity_type="talent",
            entity_id=talent.id,
            revision=talent.revision,
            operation="PARSE",
            task_id=task.id,
        )
    )
    session.add(
        AuditLog(actor_id=actor.id, action="talent.reparse", entity_type="talent", entity_id=talent.id)
    )
    session.commit()
    dispatch_task(task.id, request.app.state.settings)
    return {
        "talent_id": talent.id,
        "task_id": task.id,
        "revision": talent.revision,
        "message": "重新解析已排队，原有版本和HR备注保留",
    }


@router.get("/talents/{talent_id}/revisions")
def revisions(talent_id: str, session=Depends(db), actor=Depends(permission("talents:read"))):
    get_talent(session, talent_id)
    return {
        "items": [
            {
                "revision": r.revision,
                "facts": r.factual_snapshot,
                "evidence": r.evidence_snapshot,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in session.scalars(
                select(TalentRevision)
                .where(TalentRevision.talent_id == talent_id)
                .order_by(TalentRevision.revision.desc())
                .limit(100)
            )
        ]
    }


@router.get("/talents/{talent_id}/notes")
def notes(talent_id: str, session=Depends(db), actor=Depends(permission("talents:read"))):
    get_talent(session, talent_id)
    return {
        "items": [
            {"id": n.id, "content": n.content, "created_at": n.created_at.isoformat()}
            for n in session.scalars(
                select(TalentNote)
                .where(TalentNote.talent_id == talent_id)
                .order_by(TalentNote.created_at.desc())
                .limit(100)
            )
        ],
        "indexed": False,
        "message": "HR备注不参与匹配和智能检索",
    }


@router.post("/talents/{talent_id}/notes", status_code=201)
def new_note(
    talent_id: str, body: NoteInput, session=Depends(db), actor=Depends(permission("talents:write"))
):
    note = add_note(session, talent_id, body.content, actor.id)
    return {"id": note.id, "content": note.content, "indexed": False}


@router.delete("/talents/{talent_id}")
def soft_delete(talent_id: str, session=Depends(db), actor=Depends(permission("talents:write"))):
    talent = get_talent(session, talent_id, lock=True)
    talent.deleted_at = utcnow()
    talent.revision += 1
    talent.index_status = "DELETED"
    session.add(
        AuditLog(actor_id=actor.id, action="talent.soft_delete", entity_type="talent", entity_id=talent.id)
    )
    return {"message": "档案已移出人才库，原文件与审计记录保留"}


@router.get("/tags")
def tags(session=Depends(db), actor=Depends(permission("talents:read"))):
    return {"items": [{"id": t.id, "name": t.name} for t in session.scalars(select(Tag).order_by(Tag.name))]}


@router.post("/tags", status_code=201)
def create_tag(body: TagInput, session=Depends(db), actor=Depends(permission("talents:write"))):
    tag = session.scalar(select(Tag).where(Tag.name == body.name.strip()))
    if not tag:
        tag = Tag(name=body.name.strip())
        session.add(tag)
        session.flush()
    return {"id": tag.id, "name": tag.name}


@router.put("/talents/{talent_id}/tags")
def set_tags(
    talent_id: str, body: TagsInput, session=Depends(db), actor=Depends(permission("talents:write"))
):
    get_talent(session, talent_id)
    ids = set(body.tag_ids)
    found = set(session.scalars(select(Tag.id).where(Tag.id.in_(ids)))) if ids else set()
    if found != ids:
        raise AppError("NOT_FOUND", "部分标签不存在", 404)
    session.execute(delete(talent_tags).where(talent_tags.c.talent_id == talent_id))
    for tag_id in ids:
        session.execute(talent_tags.insert().values(talent_id=talent_id, tag_id=tag_id))
    return {"tag_ids": sorted(ids), "message": "标签仅用于管理，不自动成为技能证据"}
