"""岗位版本、双向匹配与持久化快照。"""

from sqlalchemy import select

from app.adapters.vector import VectorStore
from app.adapters.graph import GraphStore
from app.errors import AppError
from app.models import Job, Talent, Task, IndexOutbox, Evidence, SourceChunk, MatchResult, AuditLog
from app.services.cache import query_vector
from app.services.indexing import factual_text
from app.services.matching import match_pair, ALGORITHM_VERSION
from app.services.profiles import get_talent, evidence_view


def job_view(job):
    return {
        "id": job.id,
        "title": job.title,
        "raw_jd": job.raw_jd,
        "requirements": job.requirements,
        "hard_conditions": job.hard_conditions,
        "hard_confirmed": job.hard_confirmed,
        "revision": job.revision,
        "index_status": job.index_status,
        "status": job.status,
    }


def get_job(session, job_id, lock=False):
    statement = select(Job).where(Job.id == job_id)
    if lock:
        statement = statement.with_for_update()
    item = session.scalar(statement)
    if not item:
        raise AppError("NOT_FOUND", "岗位不存在", 404)
    return item


def save_job(session, body, actor_id, job_id=None):
    item = get_job(session, job_id, lock=True) if job_id else Job(created_by=actor_id)
    if job_id:
        item.revision += 1
    item.title = body.title
    item.raw_jd = body.raw_jd
    item.hard_conditions = body.hard_conditions.model_dump()
    item.hard_confirmed = body.hard_confirmed
    item.index_status = "PENDING"
    item.requirements = {}
    if not job_id:
        session.add(item)
    session.flush()
    task = Task(task_type="job", entity_id=item.id, revision=item.revision)
    session.add(task)
    session.flush()
    session.add(
        IndexOutbox(
            entity_type="job", entity_id=item.id, revision=item.revision, operation="UPSERT", task_id=task.id
        )
    )
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="job.update" if job_id else "job.create",
            entity_type="job",
            entity_id=item.id,
        )
    )
    return item, task


def _evidence(session, talent):
    rows = session.execute(
        select(Evidence, SourceChunk)
        .join(SourceChunk, SourceChunk.id == Evidence.chunk_id)
        .where(Evidence.talent_id == talent.id, Evidence.talent_revision == talent.revision)
    )
    return [evidence_view(row, chunk) for row, chunk in rows]


def _pair(session, job, talent, semantic, graph, settings):
    factual = talent.facts | {"skills": graph.skills("talent", talent.id, talent.revision)}
    job_data = job_view(job)
    job_data["requirements"] = job.requirements | {"skills": graph.skills("job", job.id, job.revision)}
    result = match_pair(job_data, factual, semantic)
    evidence = _evidence(session, talent)
    snapshot = session.scalar(
        select(MatchResult).where(
            MatchResult.job_id == job.id,
            MatchResult.job_revision == job.revision,
            MatchResult.talent_id == talent.id,
            MatchResult.talent_revision == talent.revision,
            MatchResult.algorithm_version == ALGORITHM_VERSION,
        )
    )
    if snapshot is None:
        session.add(
            MatchResult(
                job_id=job.id,
                job_revision=job.revision,
                talent_id=talent.id,
                talent_revision=talent.revision,
                algorithm_version=ALGORITHM_VERSION,
                embedding_model=settings.embedding_model,
                score_json=result,
                evidence_json=evidence,
            )
        )
    return result | {
        "talent_id": talent.id,
        "job_id": job.id,
        "talent_revision": talent.revision,
        "job_revision": job.revision,
        "evidence": evidence,
    }


def matches(session, entity_id, direction, settings, cache=None):
    if direction == "job":
        source = get_job(session, entity_id)
        if source.status != "ACTIVE" or source.index_status != "READY":
            raise AppError("NOT_READY", "岗位尚未完成索引，请查看处理任务", 409)
        text = source.title + "\n" + source.raw_jd
        kind = "profile"
        candidates = list(
            session.scalars(
                select(Talent).where(Talent.deleted_at.is_(None), Talent.index_status == "READY").limit(100)
            )
        )
    else:
        source = get_talent(session, entity_id)
        if source.index_status != "READY":
            raise AppError("NOT_READY", "人才档案尚未完成索引", 409)
        text = factual_text(source.facts)
        kind = "job"
        candidates = list(
            session.scalars(select(Job).where(Job.status == "ACTIVE", Job.index_status == "READY").limit(100))
        )
    source_revision = source.revision
    source_type = Job if direction == "job" else Talent
    session.commit()
    vector = query_vector(text, settings, cache)
    session.expire_all()
    source = session.get(source_type, entity_id)
    if (
        not source
        or source.revision != source_revision
        or source.index_status != "READY"
        or (direction == "talent" and source.deleted_at)
        or (direction == "job" and source.status != "ACTIVE")
    ):
        raise AppError("DATA_CHANGED", "匹配期间档案已更新，请重新匹配", 409)
    store = VectorStore(settings)
    graph = GraphStore(settings)
    result = {
        "eligible": [],
        "needs_review": [],
        "excluded_count": 0,
        "pending_count": 0,
        "algorithm_version": ALGORITHM_VERSION,
    }
    try:
        hits = store.search(
            kind,
            vector,
            128,
            current_versions=[
                (c.id, c.revision)
                for c in candidates
                if c.index_status == "READY" and (direction != "job" or c.deleted_at is None)
            ],
        )
        score_by_version = {}
        for hit in hits:
            key = (hit["entity_id"], int(hit["revision"]))
            score_by_version[key] = max(score_by_version.get(key, 0), hit["score"])
        for candidate in candidates:
            if (
                candidate.index_status != "READY"
                or (direction == "job" and candidate.deleted_at)
                or (direction == "talent" and candidate.status != "ACTIVE")
            ):
                continue
            # 缺少当前版本代表向量不能当作语义0分继续排序。
            semantic = score_by_version.get((candidate.id, candidate.revision))
            if semantic is None:
                result["pending_count"] += 1
                continue
            job, talent = (source, candidate) if direction == "job" else (candidate, source)
            item = _pair(session, job, talent, semantic, graph, settings)
            if item["hard_conditions"]["state"] == "FAIL":
                result["excluded_count"] += 1
                continue
            item["id"] = candidate.id
            item["name"] = candidate.name if direction == "job" else candidate.title
            group = "needs_review" if item["hard_conditions"]["state"] == "UNKNOWN" else "eligible"
            result[group].append(item)
        for group in ("eligible", "needs_review"):
            result[group].sort(key=lambda i: (-i["score"], i["id"]))
        session.commit()
        session.expire_all()
        current = session.get(source_type, entity_id)
        if not current or current.revision != source_revision or current.index_status != "READY":
            raise AppError("DATA_CHANGED", "匹配期间档案已更新，请重新匹配", 409)
        for item in result["eligible"] + result["needs_review"]:
            talent = session.get(Talent, item["talent_id"])
            job = session.get(Job, item["job_id"])
            if (
                not talent
                or talent.deleted_at
                or talent.revision != item["talent_revision"]
                or talent.index_status != "READY"
                or not job
                or job.revision != item["job_revision"]
                or job.status != "ACTIVE"
                or job.index_status != "READY"
            ):
                raise AppError("DATA_CHANGED", "匹配期间档案已更新，请重新匹配", 409)
        return result
    finally:
        store.close()
        graph.close()
