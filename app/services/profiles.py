"""档案修正必须有原文证据；人工备注不进入事实、评分和 RAG。"""

from pydantic import ValidationError
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    Talent,
    ResumeDocument,
    SourceChunk,
    Evidence,
    TalentRevision,
    TalentNote,
    Task,
    IndexOutbox,
    AuditLog,
)
from app.schemas import ProfileFacts
from app.services.extraction import validate_facts, profile_summary, FIELDS, LIST_FIELDS


def get_talent(session, talent_id, lock=False):
    query = select(Talent).where(Talent.id == talent_id, Talent.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    talent = session.scalar(query)
    if not talent:
        raise AppError("NOT_FOUND", "人才档案不存在", 404)
    return talent


def talent_view(talent):
    return {
        "id": talent.id,
        "name": talent.name,
        "summary": talent.summary,
        "facts": talent.facts,
        "revision": talent.revision,
        "index_status": talent.index_status,
        "document_id": talent.active_document_id,
        "created_at": talent.created_at.isoformat(),
    }


def evidence_view(row, chunk=None):
    result = {
        "id": row.id,
        "field_path": row.field_path,
        "document_id": row.document_id,
        "document_revision": row.document_revision,
        "talent_revision": row.talent_revision,
        "chunk_id": row.chunk_id,
        "quote": row.quote,
        "origin": row.origin,
    }
    if chunk:
        result.update(page_no=chunk.page_no, paragraph_no=chunk.paragraph_no)
    return result


def update_profile(session, talent_id, base_revision, changes, evidence_refs, actor_id):
    talent = get_talent(session, talent_id, lock=True)
    if talent.revision != base_revision:
        raise AppError("REVISION_CONFLICT", "档案已被修改，请刷新后再提交", 409)
    snapshot = session.scalar(
        select(TalentRevision).where(
            TalentRevision.talent_id == talent_id,
            TalentRevision.revision == base_revision,
            TalentRevision.document_id == talent.active_document_id,
        )
    )
    if snapshot is None:
        raise AppError("FACTS_NOT_READY", "当前简历尚未完成解析，请先完成或重试解析任务", 409)
    if not changes or not set(changes).issubset(FIELDS):
        raise AppError("INVALID_FIELDS", "只能修改声明的简历事实字段")
    try:
        merged = ProfileFacts.model_validate(talent.facts | changes).model_dump()
    except ValidationError as exc:
        raise AppError("INVALID_FACTS", "档案字段格式无效") from exc
    chunks = list(
        session.scalars(select(SourceChunk).where(SourceChunk.document_id == talent.active_document_id))
    )
    data = [{"id": c.id, "text": c.text} for c in chunks]
    proposed = {k: merged[k] for k in changes}
    old = list(
        session.scalars(
            select(Evidence).where(Evidence.talent_id == talent.id, Evidence.talent_revision == base_revision)
        )
    )
    raw_evidence = []
    for field in changes:
        refs = evidence_refs.get(field, [])
        if not isinstance(refs, list) or len(refs) > 50:
            raise AppError("INVALID_EVIDENCE", "证据引用数量无效")
        values = merged[field] if field in LIST_FIELDS else [merged[field]]
        if not values or values == [None]:
            continue
        for value in values:
            value_refs = list(refs)
            if field in LIST_FIELDS and value in talent.facts.get(field, []):
                value_refs.extend(
                    {"chunk_id": e.chunk_id, "quote": e.quote} for e in old if e.field_path == field
                )
            for ref in value_refs:
                raw_evidence.append(
                    {
                        "field": field,
                        "value": value,
                        "chunk_id": ref.get("chunk_id"),
                        "quote": ref.get("quote"),
                    }
                )
    verified, valid = validate_facts(proposed | {"evidence": raw_evidence}, data)
    for field in changes:
        if verified[field] != merged[field]:
            raise AppError("EVIDENCE_REQUIRED", "修改内容缺少有效原文依据，请将额外信息写入 HR 备注")
    talent.revision += 1
    talent.facts = merged
    talent.name = merged["name"] or "未提供姓名"
    talent.summary = profile_summary(merged)
    talent.index_status = "PENDING"
    document = session.get(ResumeDocument, talent.active_document_id)
    records = []
    for row in old:
        if row.field_path not in changes:
            records.append(
                {
                    "field_path": row.field_path,
                    "chunk_id": row.chunk_id,
                    "quote": row.quote,
                    "origin": row.origin,
                    "verified_by": row.verified_by,
                }
            )
    records.extend(
        {
            "field_path": e["field"],
            "chunk_id": e["chunk_id"],
            "quote": e["quote"],
            "origin": "human_corrected",
            "verified_by": actor_id,
        }
        for e in valid
    )
    for record in records:
        session.add(
            Evidence(
                talent_id=talent.id,
                document_id=document.id,
                document_revision=document.revision,
                talent_revision=talent.revision,
                **record,
            )
        )
    session.add(
        TalentRevision(
            talent_id=talent.id,
            revision=talent.revision,
            document_id=document.id,
            factual_snapshot=merged,
            evidence_snapshot=records,
            actor_id=actor_id,
            reason="HR原文核对修正",
        )
    )
    task = Task(task_type="talent_index", entity_id=talent.id, revision=talent.revision)
    session.add(task)
    session.flush()
    session.add(
        IndexOutbox(
            entity_type="talent",
            entity_id=talent.id,
            revision=talent.revision,
            operation="UPSERT",
            task_id=task.id,
        )
    )
    session.add(
        AuditLog(actor_id=actor_id, action="talent.correct", entity_type="talent", entity_id=talent.id)
    )
    return talent


def add_note(session, talent_id, text, actor_id):
    get_talent(session, talent_id)
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        raise AppError("INVALID_NOTE", "备注内容须为1至5000个字符")
    note = TalentNote(talent_id=talent_id, author_id=actor_id, content=text.strip())
    session.add(note)
    session.flush()
    session.add(AuditLog(actor_id=actor_id, action="talent.note", entity_type="talent", entity_id=talent_id))
    return note
