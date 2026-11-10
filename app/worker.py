"""后台任务：短事务领取、外部调用、版本核验提交、幂等恢复。"""

import logging
import time
from uuid import uuid5, NAMESPACE_URL

from rq import Worker
from sqlalchemy import select

from app.adapters.llm import LanguageModel
from app.adapters.storage import ObjectStore
from app.config import get_settings
from app.db import make_session_factory
from app.errors import AppError
from app.models import (
    Task,
    Talent,
    Job,
    ResumeDocument,
    SourceChunk,
    Evidence,
    TalentRevision,
    IndexOutbox,
    utcnow,
)
from app.services.documents import extract_document, split_chunks
from app.services.pipeline import extract_with_graph
from app.services.indexing import sync_entity
from app.services.tasks import queue_connection, recover_tasks
from app.services.extraction import normalize, months_in_quote

log = logging.getLogger(__name__)


def _progress(factory, task_id, stage):
    with factory.begin() as session:
        task = session.get(Task, task_id)
        task.stage = stage
        task.heartbeat_at = utcnow()


def _check_talent(session, task):
    talent = session.scalar(select(Talent).where(Talent.id == task.entity_id).with_for_update())
    if not talent or talent.deleted_at or talent.revision != task.revision:
        raise AppError("STALE_TASK", "人才档案已更新或删除，此任务不再适用", 409)
    return talent


def _resume(factory, task, settings, model):
    with factory() as session:
        talent = _check_talent(session, task)
        document = session.get(ResumeDocument, talent.active_document_id)
        if not document:
            raise AppError("NOT_FOUND", "关联简历不存在", 404)
        document_id, document_revision = document.id, document.revision
        key, filename = document.object_key, document.original_name
        snapshot = session.scalar(
            select(TalentRevision).where(
                TalentRevision.talent_id == talent.id, TalentRevision.revision == task.revision
            )
        )
        if snapshot:
            return
    _progress(factory, task.id, "提取简历文字")
    data = ObjectStore(settings).get(key, settings.max_upload_bytes)
    pages = extract_document(data, filename)
    pieces = split_chunks(pages)
    chunk_data = [
        {
            "id": str(uuid5(NAMESPACE_URL, f"{document_id}:{document_revision}:{c.chunk_no}")),
            "text": c.text,
            "page_no": c.page_no,
            "paragraph_no": c.paragraph_no,
            "chunk_no": c.chunk_no,
            "char_start": c.char_start,
            "char_end": c.char_end,
        }
        for c in pieces
    ]
    _progress(factory, task.id, "结构化解析与证据核验")
    result = extract_with_graph(chunk_data, model)
    with factory.begin() as session:
        talent = _check_talent(session, task)
        if talent.active_document_id != document_id:
            raise AppError("STALE_TASK", "简历版本已变更", 409)
        for c in chunk_data:
            if session.get(SourceChunk, c["id"]) is None:
                session.add(SourceChunk(**c, document_id=document_id, document_revision=document_revision))
        session.flush()
        evidence_data = []
        for e in result["evidence"]:
            row = Evidence(
                talent_id=talent.id,
                document_id=document_id,
                document_revision=document_revision,
                talent_revision=task.revision,
                field_path=e["field"],
                chunk_id=e["chunk_id"],
                quote=e["quote"],
            )
            session.add(row)
            evidence_data.append(
                {
                    "field_path": e["field"],
                    "value": e["value"],
                    "chunk_id": e["chunk_id"],
                    "quote": e["quote"],
                    "origin": "model_extracted",
                }
            )
        talent.facts = result["facts"]
        talent.name = result["facts"].get("name") or "未提供姓名"
        talent.summary = result["summary"]
        talent.index_status = "PARTIAL"
        document = session.get(ResumeDocument, document_id)
        document.extracted_text = "\n".join(p.text for p in pages)
        document.status = "PARSED"
        session.add(
            TalentRevision(
                talent_id=talent.id,
                revision=task.revision,
                document_id=document_id,
                factual_snapshot=result["facts"],
                evidence_snapshot=evidence_data,
                reason="简历解析",
            )
        )


def _job(factory, task, model):
    with factory() as session:
        job = session.get(Job, task.entity_id)
        if not job or job.revision != task.revision:
            raise AppError("STALE_TASK", "岗位版本已改变", 409)
        raw_jd = job.raw_jd
    _progress(factory, task.id, "提取岗位要求")
    raw = model.extract_job(raw_jd)
    skills = [s for s in raw.get("skills", []) if isinstance(s, str) and normalize(s) in normalize(raw_jd)]
    projects = [
        s for s in raw.get("projects", []) if isinstance(s, str) and normalize(s) in normalize(raw_jd)
    ]
    months = raw.get("experience_months")
    if type(months) is not int or months not in months_in_quote(raw_jd):
        months = None
    with factory.begin() as session:
        job = session.scalar(select(Job).where(Job.id == task.entity_id).with_for_update())
        if job.revision != task.revision:
            raise AppError("STALE_TASK", "岗位版本已改变", 409)
        job.requirements = {"skills": skills[:30], "projects": projects[:20], "experience_months": months}
        job.index_status = "PARTIAL"


def process_task(task_id: str, settings=None, model=None, indexer=None):
    settings = settings or get_settings()
    factory, engine = make_session_factory(settings)
    model = model or LanguageModel(settings)
    indexer = indexer or sync_entity
    task = None
    try:
        with factory.begin() as session:
            task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if not task or task.state != "QUEUED":
                return
            task.state = "RUNNING"
            task.attempts += 1
            task.started_at = utcnow()
            task.heartbeat_at = utcnow()
            task.stage = "开始处理"
            task.error_code = None
            task.safe_error_message = None
        if task.task_type == "resume":
            _resume(factory, task, settings, model)
        elif task.task_type == "job":
            _job(factory, task, model)
        elif task.task_type != "talent_index":
            raise AppError("INVALID_TASK", "不支持的处理任务类型")
        _progress(factory, task_id, "同步向量与技能图谱")
        kind = "job" if task.task_type == "job" else "talent"
        with factory() as session:
            indexer(session, kind, task.entity_id, task.revision, settings)
        with factory.begin() as session:
            current = session.get(Task, task_id)
            entity = session.scalar(
                select(Job if kind == "job" else Talent)
                .where((Job.id if kind == "job" else Talent.id) == task.entity_id)
                .with_for_update()
            )
            if not entity or entity.revision != task.revision or (kind == "talent" and entity.deleted_at):
                raise AppError("STALE_TASK", "处理完成前数据已更新，请查看最新任务", 409)
            entity.index_status = "READY"
            current.state = "READY"
            current.stage = "已完成"
            current.finished_at = utcnow()
            current.heartbeat_at = utcnow()
            box = session.scalar(select(IndexOutbox).where(IndexOutbox.task_id == task_id))
            if box:
                box.state = "DONE"
    except Exception as exc:
        code = exc.code if isinstance(exc, AppError) else "PROCESSING_FAILED"
        message = exc.message if isinstance(exc, AppError) else "后台处理失败，请检查服务状态后重试"
        log.warning("task_failed task_id=%s error_type=%s code=%s", task_id, type(exc).__name__, code)
        with factory.begin() as session:
            current = session.get(Task, task_id)
            if current:
                current.state = "FAILED"
                current.error_code = code
                current.safe_error_message = message[:255]
                current.finished_at = utcnow()
                kind = "job" if current.task_type == "job" else "talent"
                entity = session.get(Job if kind == "job" else Talent, current.entity_id)
                if entity and entity.revision == current.revision:
                    entity.index_status = "FAILED"
                box = session.scalar(select(IndexOutbox).where(IndexOutbox.task_id == task_id))
                if box:
                    box.state = "FAILED"
                    box.attempts += 1
    finally:
        engine.dispose()


def main():
    settings = get_settings()
    connection = queue_connection(settings)
    # 一个 worker 串行处理；每轮空闲后由 SQL 恢复未投递任务与失联状态。
    try:
        while True:
            recover_tasks(settings)
            worker = Worker([settings.project_namespace], connection=connection)
            worker.work(burst=True, logging_level="WARNING")
            time.sleep(2)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
