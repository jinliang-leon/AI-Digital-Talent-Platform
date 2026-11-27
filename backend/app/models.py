from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Identified:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Identified, Timestamped, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','hr','interviewer')", name="ck_user_role"),)

    username: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    view_contacts: Mapped[bool] = mapped_column(Boolean, default=False)
    reopen_applications: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginSession(Identified, Base):
    __tablename__ = "login_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Identified, Timestamped, Base):
    __tablename__ = "job_positions"
    __table_args__ = (CheckConstraint("headcount > 0", name="ck_job_headcount"),)

    title: Mapped[str] = mapped_column(String(160))
    department: Mapped[str] = mapped_column(String(100))
    headcount: Mapped[int] = mapped_column(Integer)
    responsibilities: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[str] = mapped_column(Text)
    preferred_skills: Mapped[str] = mapped_column(Text, default="")
    experience: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Candidate(Identified, Timestamped, Base):
    __tablename__ = "candidates"

    name: Mapped[str] = mapped_column(String(100), index=True)
    headline: Mapped[str] = mapped_column(String(160), default="")
    skills: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class CandidateContact(Base):
    __tablename__ = "candidate_contacts"

    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), primary_key=True)
    encrypted: Mapped[str] = mapped_column(Text)


class Application(Identified, Timestamped, Base):
    __tablename__ = "job_applications"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_job_candidate"),)

    job_id: Mapped[str] = mapped_column(ForeignKey("job_positions.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    interviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="collected", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ApplicationHistory(Identified, Base):
    __tablename__ = "application_status_logs"

    application_id: Mapped[str] = mapped_column(ForeignKey("job_applications.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    previous_status: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    application_version: Mapped[int] = mapped_column(Integer)


class InterviewFeedback(Identified, Base):
    __tablename__ = "interview_feedback"
    __table_args__ = (UniqueConstraint("application_id", "application_version", name="uq_feedback_round"),)

    application_id: Mapped[str] = mapped_column(ForeignKey("job_applications.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    application_version: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResumeFile(Identified, Base):
    __tablename__ = "resume_files"

    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    interviewer_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Audit(Identified, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
