from hashlib import sha256
from io import BytesIO
import logging
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from ..core.records import get_or_404, record
from ..core.security import Actor, DB, can_read_raw, require_role
from ..models import Candidate, ResumeFile
from .candidates import visible_candidate

router = APIRouter()
logger = logging.getLogger(__name__)
LIMIT = 20 * 1024 * 1024


def validate_file(filename, data):
    extension = PurePosixPath(filename).suffix.lower()
    if extension == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if extension == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if extension in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if extension == ".txt":
        try:
            text = data.decode("utf-8-sig")
            if text.strip() and not any(ord(char) < 32 and char not in "\r\n\t" for char in text):
                return "text/plain"
        except UnicodeDecodeError:
            pass
    if extension == ".docx":
        try:
            with ZipFile(BytesIO(data)) as archive:
                if ({"[Content_Types].xml", "word/document.xml"} <= set(archive.namelist())
                    and len(archive.infolist()) <= 5000
                    and sum(item.file_size for item in archive.infolist()) <= 80 * 1024 * 1024
                    and not any(item.flag_bits & 1 for item in archive.infolist())):
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except BadZipFile:
            pass
    raise HTTPException(415, "文件内容与支持的简历格式不匹配")


def file_view(file):
    return {"id": file.id, "filename": file.filename, "media_type": file.media_type,
            "size_bytes": file.size_bytes, "interviewer_visible": file.interviewer_visible,
            "created_at": file.created_at}


@router.get("/candidates/{identifier}/files")
def files(identifier: str, db: DB, actor: Actor):
    visible_candidate(db, actor, identifier)
    stmt = select(ResumeFile).where(ResumeFile.candidate_id == identifier)
    if not can_read_raw(actor):
        stmt = stmt.where(ResumeFile.interviewer_visible.is_(True))
    return [file_view(file) for file in db.scalars(stmt.order_by(ResumeFile.created_at.desc()))]


def save_file(db, actor, storage, identifier, filename, data, redacted):
    get_or_404(db, Candidate, identifier)
    media_type = validate_file(filename, data)
    file = ResumeFile(candidate_id=identifier, filename=filename, object_key=uuid4().hex, media_type=media_type,
                      sha256=sha256(data).hexdigest(), size_bytes=len(data), uploaded_by=actor.id,
                      interviewer_visible=redacted)
    try:
        storage.put(file.object_key, data, media_type)
    except Exception as exc:
        logger.warning("resume storage write failed: %s", type(exc).__name__)
        raise HTTPException(503, "文件存储暂不可用") from exc
    try:
        db.add(file)
        db.flush()
        record(db, actor, "file.upload_redacted" if redacted else "file.upload", "file", file.id)
        db.commit()
    except Exception:
        db.rollback()
        try:
            storage.remove(file.object_key)
        except Exception as exc:
            logger.error("resume cleanup failed: object=%s error=%s", file.object_key, type(exc).__name__)
        raise
    return file_view(file)


@router.post("/candidates/{identifier}/files", status_code=201, openapi_extra={
    "requestBody": {"required": True, "content": {"application/octet-stream": {
        "schema": {"type": "string", "format": "binary"}
    }}}
})
async def upload(identifier: str, request: Request, db: DB, actor: Actor,
                 filename: str = Query(min_length=1, max_length=180), interviewer_visible: bool = False):
    require_role(actor, "hr")
    if interviewer_visible and not can_read_raw(actor):
        raise HTTPException(403, "发布脱敏简历需要候选人敏感资料授权")
    return await run_in_threadpool(save_file, db, actor, request.app.state.storage, identifier,
                                   filename, await read_upload(request, filename), interviewer_visible)


async def read_upload(request, filename):
    if any(char in filename for char in "/\\:") or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "文件名无效")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise HTTPException(415, "请使用 application/octet-stream 上传简历原件")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > LIMIT:
            raise HTTPException(413, "文件不能超过 20 MiB")
        data.extend(chunk)
    return bytes(data)


@router.get("/files/{identifier}/download")
def download(identifier: str, request: Request, db: DB, actor: Actor):
    require_role(actor, "hr", "interviewer")
    file = get_or_404(db, ResumeFile, identifier)
    visible_candidate(db, actor, file.candidate_id)
    if not file.interviewer_visible and not can_read_raw(actor):
        raise HTTPException(404, "文件不存在或不可访问")
    try:
        contents = request.app.state.storage.read(file.object_key)
    except Exception as exc:
        logger.warning("resume storage read failed: %s", type(exc).__name__)
        raise HTTPException(503, "文件存储暂不可用") from exc
    record(db, actor, "file.download", "file", file.id)
    db.commit()
    return Response(contents, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file.filename, safe=""),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"
    })
