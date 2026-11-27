from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .core.vector_type import vector_storage
from .db import Base
from .models import Identified, Timestamped, utcnow


class ResumeBatch(Identified, Timestamped, Base):
    __tablename__ = "resume_batches"

    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    synthetic: Mapped[bool]
    model_authorized: Mapped[bool]
    upload_count: Mapped[int] = mapped_column(default=0)


class ResumeUpload(Identified, Timestamped, Base):
    __tablename__ = "resume_uploads"

    batch_id: Mapped[str] = mapped_column(ForeignKey("resume_batches.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"), index=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("resume_files.id"), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    duplicate_id: Mapped[str | None] = mapped_column(ForeignKey("resume_uploads.id"))
    media_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int]
    status: Mapped[str] = mapped_column(String(24), index=True)
    error: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(default=1)
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class AsyncTask(Identified, Timestamped, Base):
    __tablename__ = "async_tasks"
    __table_args__ = (
        UniqueConstraint("upload_id", "kind", name="uq_task_upload_kind"),
        Index("ix_tasks_poll", "status", "available_at"),
    )

    upload_id: Mapped[str] = mapped_column(ForeignKey("resume_uploads.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(32))


class ResumeChunk(Base):
    __tablename__ = "resume_chunks"

    upload_id: Mapped[str] = mapped_column(ForeignKey("resume_uploads.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list] = mapped_column(vector_storage)
    embedding_revision: Mapped[str] = mapped_column(String(120))
