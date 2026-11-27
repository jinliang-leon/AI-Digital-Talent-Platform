from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Identified, Timestamped


class KnowledgeBase(Identified, Timestamped, Base):
    __tablename__ = "knowledge_bases"

    title: Mapped[str] = mapped_column(String(160))
    interviewer_visible: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class KnowledgeDocument(Identified, Timestamped, Base):
    __tablename__ = "knowledge_documents"

    base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    archived: Mapped[bool] = mapped_column(default=False)


class KnowledgeRevision(Identified, Timestamped, Base):
    __tablename__ = "knowledge_revisions"
    __table_args__ = (UniqueConstraint("document_id", "revision", name="uq_knowledge_revision"),)

    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"))
    revision: Mapped[int]
    title: Mapped[str] = mapped_column(String(160))
    model_authorized: Mapped[bool]
    chunks: Mapped[list] = mapped_column(JSON)


class EvidenceAnalysis(Identified, Timestamped, Base):
    __tablename__ = "evidence_analyses"

    application_id: Mapped[str] = mapped_column(ForeignKey("job_applications.id"), index=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("resume_uploads.id"))
    job_version: Mapped[int]
    findings: Mapped[list] = mapped_column(JSON)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)


class InterviewGuide(Identified, Timestamped, Base):
    __tablename__ = "interview_guides"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("evidence_analyses.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    trace_id: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    generated: Mapped[list] = mapped_column(JSON)
    questions: Mapped[list] = mapped_column(JSON)


class ChatSession(Identified, Timestamped, Base):
    __tablename__ = "chat_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))


class ChatMessage(Identified, Timestamped, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "client_id", name="uq_chat_client_message"),)

    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
