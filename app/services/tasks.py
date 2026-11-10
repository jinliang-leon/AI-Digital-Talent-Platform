"""RQ 投递与 MySQL outbox 恢复；不能因 Redis 入队失败丢失上传任务。"""

from datetime import timedelta

from redis import Redis
from rq import Queue
from rq.job import Job as QueueJob
from sqlalchemy import select, update

from app.config import get_settings
from app.db import make_session_factory
from app.models import Task, IndexOutbox, utcnow


def queue_connection(settings):
    return Redis.from_url(settings.redis_url.get_secret_value(), socket_connect_timeout=3, socket_timeout=3)


def dispatch_task(task_id: str, settings=None) -> bool:
    settings = settings or get_settings()
    factory, engine = make_session_factory(settings)
    try:
        with factory() as session:
            task = session.get(Task, task_id)
            if not task or task.state != "QUEUED":
                return False
            attempts = task.attempts
        cache = queue_connection(settings)
        try:
            queue = Queue(settings.project_namespace, connection=cache)
            job_id = f"{settings.project_namespace}-{task_id}-{attempts}"
            existing = QueueJob.fetch(job_id, connection=cache) if cache.exists(f"rq:job:{job_id}") else None
            if existing is None:
                queue.enqueue(
                    "app.worker.process_task",
                    task_id,
                    job_id=job_id,
                    job_timeout=settings.task_timeout,
                    result_ttl=3600,
                    failure_ttl=86400,
                )
            with factory.begin() as session:
                session.execute(
                    update(IndexOutbox)
                    .where(IndexOutbox.task_id == task_id, IndexOutbox.state == "PENDING")
                    .values(state="SENT")
                )
            return True
        finally:
            cache.close()
    except Exception:
        return False
    finally:
        engine.dispose()


def recover_tasks(settings=None) -> int:
    settings = settings or get_settings()
    factory, engine = make_session_factory(settings)
    try:
        with factory.begin() as session:
            expired = utcnow() - timedelta(seconds=settings.worker_stale_seconds)
            stale = session.scalars(
                select(Task).where(Task.state == "RUNNING", Task.heartbeat_at < expired).with_for_update()
            )
            for task in stale:
                task.state = "FAILED"
                task.error_code = "WORKER_TIMEOUT"
                task.safe_error_message = "处理进程中断或超时，可手动重试"
                task.finished_at = utcnow()
            queued = list(session.scalars(select(Task.id).where(Task.state == "QUEUED")))
        return sum(dispatch_task(task_id, settings) for task_id in queued)
    finally:
        engine.dispose()
