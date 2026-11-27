"""Durable SQL queue shared by the local and Compose worker processes."""
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
import logging
from time import sleep
from uuid import uuid4

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..core.ai_client import ModelError, OpenAICompatible
from ..core.config import Settings
from ..core.embedding import embed
from ..core.storage import LocalStorage, MinioStorage
from ..core.tracing import Trace
from ..db import make_engine
from ..models import ResumeFile, utcnow
from ..resume_models import AsyncTask, ResumeBatch, ResumeChunk, ResumeUpload
from .documents import DocumentError, extract_document
from .structured import PROMPT_VERSION, extract_professional_facts

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def enqueue(db, upload, kind, trace_id):
    task = AsyncTask(upload_id=upload.id, kind=kind, trace_id=trace_id)
    db.add(task)
    return task


def claim(engine):
    with Session(engine) as db, db.begin():
        now = utcnow()
        ready = or_(
            and_(AsyncTask.status == "queued", AsyncTask.available_at <= now),
            and_(AsyncTask.status == "running", AsyncTask.lease_until <= now),
        )
        row = db.execute(select(AsyncTask.id, AsyncTask.attempts).where(ready)
                         .order_by(AsyncTask.available_at, AsyncTask.id).limit(1)).first()
        if row is None:
            return None
        token = uuid4().hex
        result = db.execute(update(AsyncTask).where(AsyncTask.id == row.id, ready,
                             AsyncTask.attempts == row.attempts).values(status="running",
                             lease_token=token, lease_until=now + timedelta(minutes=16),
                             attempts=row.attempts + 1))
        return (row.id, token) if result.rowcount == 1 else None


def read_source(storage, key, digest, size):
    try:
        raw = storage.read(key)
    except Exception as exc:
        raise ModelError("STORAGE_READ_FAILED", retryable=True) from exc
    if len(raw) != size or sha256(raw).hexdigest() != digest:
        raise DocumentError("FILE_INTEGRITY_MISMATCH")
    return raw


def process(engine, storage, settings, task_id, token):
    with Session(engine) as db:
        task = db.get(AsyncTask, task_id)
        if not task or task.status != "running" or task.lease_token != token:
            return
        upload = db.get(ResumeUpload, task.upload_id)
        batch = db.get(ResumeBatch, upload.batch_id)
        file = db.get(ResumeFile, upload.file_id) if upload.file_id else None
        key = file.object_key if file else upload.id.replace("-", "")
        authorized = settings.external_ai_enabled and batch.model_authorized
        db.expunge_all()

    trace = Trace(settings, task.trace_id)
    blocks, evidence, vectors = [], [], []
    error, retryable = None, False
    try:
        if task.attempts > MAX_ATTEMPTS:
            raise DocumentError("TASK_INTERRUPTED")
        if task.kind == "parse":
            with trace.step("resume.document_extract", bytes=upload.size_bytes) as metadata:
                raw = read_source(storage, key, upload.digest, upload.size_bytes)
                blocks = extract_document(upload.filename, raw)
                metadata["blocks"] = len(blocks)
            if authorized:
                with trace.step("resume.structured_extract", model=settings.chat_model,
                                prompt_version=PROMPT_VERSION) as metadata:
                    with httpx.Client() as client:
                        model = OpenAICompatible(client, settings.model_url, settings.model_key, settings.chat_model,
                                                 timeout=settings.model_timeout)
                        result = extract_professional_facts(blocks, model, external_processing_authorized=True)
                    evidence = [asdict(item) for item in result.evidence]
                    metadata.update(prompt_tokens=result.usage.prompt_tokens,
                                    completion_tokens=result.usage.completion_tokens)
        elif task.kind == "index" and upload.evidence:
            with trace.step("resume.index", chunks=len(upload.evidence)) as metadata:
                revision, values = embed(settings, [item["quote"] for item in upload.evidence], authorized=authorized)
                metadata["embedding_revision"] = revision
                vectors = [
                    ResumeChunk(upload_id=upload.id, ordinal=number, text=item["quote"],
                                evidence=item, embedding=vector, embedding_revision=revision)
                    for number, (item, vector) in enumerate(zip(upload.evidence, values, strict=True))
                ]
    except (DocumentError, ModelError) as exc:
        error, retryable = exc.code, getattr(exc, "retryable", False)
    except Exception as exc:
        logger.error("worker error trace=%s type=%s", task.trace_id, type(exc).__name__)
        error, retryable = "TASK_INTERNAL_ERROR", True

    try:
        with trace.step("resume.task_write", task_kind=task.kind, attempt=task.attempts) as metadata, \
                Session(engine) as db, db.begin():
            retry = error and retryable and task.attempts < MAX_ATTEMPTS
            status = "queued" if retry else ("failed" if error else "succeeded")
            metadata.update(task_status=status, error_code=error)
            result = db.execute(update(AsyncTask).where(AsyncTask.id == task_id,
                                AsyncTask.lease_token == token, AsyncTask.status == "running").values(
                status=status, error=error,
                lease_token=None, lease_until=None,
                available_at=utcnow() + timedelta(seconds=2 ** task.attempts) if retry else utcnow()))
            if result.rowcount != 1:
                metadata["task_status"] = "lease_replaced"
                return
            if task.kind == "parse":
                changes = {"blocks": [asdict(block) for block in blocks]} if blocks else {}
                if not retry:
                    changes.update(status="parse_failed" if error else "review_pending",
                                   error=error, evidence=evidence)
                if changes:
                    db.execute(update(ResumeUpload).where(ResumeUpload.id == upload.id,
                               ResumeUpload.status == "queued").values(**changes, version=ResumeUpload.version + 1))
            elif not error:
                db.add_all(vectors)
    finally:
        trace.flush()


def run_once(engine, storage, settings):
    acquired = claim(engine)
    if not acquired:
        return False
    process(engine, storage, settings, *acquired)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    settings = Settings.load()
    engine = make_engine(settings.database_url)
    storage = LocalStorage(settings.storage_path) if settings.profile == "local" else MinioStorage(settings)
    try:
        while True:
            try:
                if not run_once(engine, storage, settings):
                    sleep(0.75)
            except Exception as exc:
                logger.error("worker iteration failed: %s", type(exc).__name__)
                sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
