"""可重建索引：事实字段白名单、当前版本、所有外部写入成功后才就绪。"""

from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select

from app.adapters.embedding import LocalEmbeddings
from app.adapters.vector import VectorStore
from app.adapters.graph import GraphStore
from app.errors import AppError
from app.models import Talent, Job, SourceChunk, Evidence
from app.services.extraction import normalize


def factual_text(facts: dict) -> str:
    parts = []
    for field in ("name", "skills", "experience_months", "projects", "experiences", "education"):
        value = facts.get(field)
        if value is not None and value != []:
            parts.append(
                field + ": " + ("；".join(map(str, value)) if isinstance(value, list) else str(value))
            )
    return "\n".join(parts)


def sync_entity(session, kind, entity_id, revision, settings):
    entity = session.get(Talent if kind == "talent" else Job, entity_id)
    if not entity or entity.revision != revision or (kind == "talent" and entity.deleted_at):
        raise AppError("STALE_TASK", "档案或岗位版本已改变，当前任务已过期", 409)
    embeddings = LocalEmbeddings(settings)
    vectors = VectorStore(settings)
    graph = GraphStore(settings)
    try:
        if kind == "talent":
            chunks = list(
                session.scalars(
                    select(SourceChunk).where(SourceChunk.document_id == entity.active_document_id)
                )
            )
            evidence = list(
                session.scalars(
                    select(Evidence).where(
                        Evidence.talent_id == entity.id, Evidence.talent_revision == revision
                    )
                )
            )
            # 原文切片保留检索证据；独立 HR 备注不在此查询路径中。
            values = embeddings.embed_documents([c.text for c in chunks])
            records = [
                {
                    "id": c.id,
                    "vector": v,
                    "entity_id": entity.id,
                    "revision": revision,
                    "document_id": c.document_id,
                    "document_revision": c.document_revision,
                    "chunk_id": c.id,
                    "embedding_model": settings.embedding_model,
                }
                for c, v in zip(chunks, values)
            ]
            skills = [
                {
                    "name": normalize(skill),
                    "chunk_id": next(
                        (
                            e.chunk_id
                            for e in evidence
                            if e.field_path == "skills" and normalize(skill) in normalize(e.quote)
                        ),
                        None,
                    ),
                }
                for skill in entity.facts.get("skills", [])
            ]
            skills = [s for s in skills if s["chunk_id"]]
            # 画像代表向量用于双向同一对向量评分；原文切片向量仍专门用于RAG。
            profile_vector = embeddings.embed_query(factual_text(entity.facts))
            profile_record = {
                "id": str(
                    uuid5(NAMESPACE_URL, f"{settings.project_namespace}:profile:{entity.id}:{revision}")
                ),
                "vector": profile_vector,
                "entity_id": entity.id,
                "revision": revision,
                "document_id": entity.active_document_id,
                "document_revision": chunks[0].document_revision if chunks else 0,
                "chunk_id": "",
                "embedding_model": settings.embedding_model,
            }
            vectors.upsert("profile", [profile_record])
        else:
            text = entity.title + "\n" + entity.raw_jd
            vector = embeddings.embed_query(text)
            records = [
                {
                    "id": str(
                        uuid5(NAMESPACE_URL, f"{settings.project_namespace}:job:{entity.id}:{revision}")
                    ),
                    "vector": vector,
                    "entity_id": entity.id,
                    "revision": revision,
                    "document_id": "",
                    "document_revision": revision,
                    "chunk_id": "",
                    "embedding_model": settings.embedding_model,
                }
            ]
            names = entity.requirements.get("skills", [])
            if entity.hard_confirmed:
                names = names + entity.hard_conditions.get("required_skills", [])
            skills = [{"name": normalize(s), "chunk_id": ""} for s in dict.fromkeys(names)]
        if not records:
            raise AppError("EMPTY_INDEX", "没有可建立索引的原文证据")
        vectors.upsert(kind, records)
        graph.sync(kind, entity.id, revision, skills)
    finally:
        vectors.close()
        graph.close()
