"""MySQL 持久化模型；事实、备注、外部索引同步分别记录。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import LONGTEXT


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    Column("role_id", String(36), ForeignKey("roles.id"), primary_key=True),
)
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", String(36), ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", String(36), ForeignKey("permissions.id"), primary_key=True),
)


class Permission(Record, Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(80), unique=True)


class Role(Record, Base):
    __tablename__ = "roles"
    code: Mapped[str] = mapped_column(String(32), unique=True)
    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, lazy="selectin")


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin")


class Talent(Record, Base):
    __tablename__ = "talents"
    name: Mapped[str] = mapped_column(String(120), default="待解析人才")
    summary: Mapped[str] = mapped_column(Text, default="")
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    active_document_id: Mapped[str | None] = mapped_column(String(36))
    index_status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class ResumeDocument(Record, Base):
    __tablename__ = "resume_documents"
    talent_id: Mapped[str] = mapped_column(ForeignKey("talents.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    extracted_text: Mapped[str] = mapped_column(LONGTEXT, default="")


class SourceChunk(Record, Base):
    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("document_id", "document_revision", "chunk_no"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("resume_documents.id"), index=True)
    document_revision: Mapped[int] = mapped_column(Integer)
    chunk_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer)
    paragraph_no: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)


class Evidence(Record, Base):
    __tablename__ = "extraction_evidence"
    talent_id: Mapped[str] = mapped_column(ForeignKey("talents.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("resume_documents.id"))
    document_revision: Mapped[int] = mapped_column(Integer)
    talent_revision: Mapped[int] = mapped_column(Integer)
    field_path: Mapped[str] = mapped_column(String(100))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("source_chunks.id"))
    quote: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(30), default="model_extracted")
    verified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class TalentRevision(Record, Base):
    __tablename__ = "talent_revisions"
    __table_args__ = (UniqueConstraint("talent_id", "revision"),)
    talent_id: Mapped[str] = mapped_column(ForeignKey("talents.id"))
    revision: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[str | None] = mapped_column(String(36))
    factual_snapshot: Mapped[dict] = mapped_column(JSON)
    evidence_snapshot: Mapped[list] = mapped_column(JSON)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(100))


class TalentNote(Record, Base):
    __tablename__ = "talent_notes"
    talent_id: Mapped[str] = mapped_column(ForeignKey("talents.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)


talent_tags = Table(
    "talent_tags",
    Base.metadata,
    Column("talent_id", String(36), ForeignKey("talents.id"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("tags.id"), primary_key=True),
)


class Tag(Record, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(60), unique=True)


class Job(Record, Base):
    __tablename__ = "jobs"
    title: Mapped[str] = mapped_column(String(150))
    raw_jd: Mapped[str] = mapped_column(Text)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    hard_conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    hard_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    index_status: Mapped[str] = mapped_column(String(24), default="PENDING")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Task(Record, Base):
    __tablename__ = "processing_tasks"
    task_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    stage: Mapped[str] = mapped_column(String(80), default="等待处理")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    safe_error_message: Mapped[str | None] = mapped_column(String(255))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class IndexOutbox(Record, Base):
    __tablename__ = "index_outbox"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "revision", "operation"),)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(24), default="UPSERT")
    task_id: Mapped[str] = mapped_column(ForeignKey("processing_tasks.id"))
    state: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MatchResult(Record, Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("job_id", "job_revision", "talent_id", "talent_revision", "algorithm_version"),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    job_revision: Mapped[int] = mapped_column(Integer)
    talent_id: Mapped[str] = mapped_column(ForeignKey("talents.id"))
    talent_revision: Mapped[int] = mapped_column(Integer)
    algorithm_version: Mapped[str] = mapped_column(String(60))
    embedding_model: Mapped[str] = mapped_column(String(100))
    score_json: Mapped[dict] = mapped_column(JSON)
    evidence_json: Mapped[list] = mapped_column(JSON)


class AuditLog(Record, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(80), default="success")
