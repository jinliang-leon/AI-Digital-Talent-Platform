import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, select

from ..core.records import get_or_404, record, update_version
from ..core.schema import Input, Short, Text, Version
from ..core.security import Actor, DB, can_read_raw, require_role
from ..models import Application, Candidate, CandidateContact

router = APIRouter(prefix="/candidates")


class Contact(Input):
    email: str = Field("", max_length=254)
    phone: str = Field("", max_length=40)


class CandidateInput(Input):
    name: Short = Field(max_length=100)
    headline: str = Field("", max_length=160)
    skills: Text = ""
    synthetic: bool = False
    contact: Contact | None = None


class CandidateUpdate(CandidateInput, Version):
    pass


def candidate_view(candidate):
    return {key: getattr(candidate, key) for key in (
        "id", "name", "headline", "skills", "version", "synthetic", "created_at", "updated_at"
    )}


def visible_candidate(db, actor, identifier):
    require_role(actor, "hr", "interviewer")
    if actor.role == "interviewer":
        assigned = db.scalar(select(Application.id).where(
            Application.candidate_id == identifier,
            Application.interviewer_id == actor.id,
        ).limit(1))
        if not assigned:
            raise HTTPException(404, "候选人不存在或不可访问")
    return get_or_404(db, Candidate, identifier)


def save_contact(db, request, actor, identifier, payload):
    if payload is None:
        return
    if not can_read_raw(actor):
        raise HTTPException(403, "没有维护候选人联系方式的权限")
    values = {"email": payload.email.strip(), "phone": payload.phone.strip()}
    encrypted = request.app.state.fernet.encrypt(json.dumps(values).encode()).decode()
    db.merge(CandidateContact(candidate_id=identifier, encrypted=encrypted))
    record(db, actor, "candidate.contact_write", "candidate", identifier)


@router.get("")
def list_candidates(db: DB, actor: Actor, q: str = Query("", max_length=100), offset: int = Query(0, ge=0),
                    limit: int = Query(20, ge=1, le=100)):
    require_role(actor, "hr")
    stmt = select(Candidate)
    if q.strip():
        stmt = stmt.where(Candidate.name.contains(q.strip(), autoescape=True)
                          | Candidate.skills.contains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Candidate.created_at.desc(), Candidate.id).offset(offset).limit(limit))
    return {"items": [candidate_view(candidate) for candidate in rows], "total": total}


@router.get("/{identifier}")
def get_candidate(identifier: str, db: DB, actor: Actor):
    return candidate_view(visible_candidate(db, actor, identifier))


@router.get("/{identifier}/contact")
def get_contact(identifier: str, request: Request, db: DB, actor: Actor):
    if not can_read_raw(actor):
        raise HTTPException(403, "没有查看候选人联系方式的权限")
    get_or_404(db, Candidate, identifier)
    contact = db.get(CandidateContact, identifier)
    result = json.loads(request.app.state.fernet.decrypt(contact.encrypted.encode())) if contact else None
    record(db, actor, "candidate.contact_read", "candidate", identifier)
    db.commit()
    return {"contact": result}


@router.post("", status_code=201)
def create_candidate(body: CandidateInput, request: Request, db: DB, actor: Actor):
    require_role(actor, "hr")
    candidate = Candidate(**body.model_dump(exclude={"contact"}))
    db.add(candidate)
    db.flush()
    save_contact(db, request, actor, candidate.id, body.contact)
    record(db, actor, "candidate.create", "candidate", candidate.id)
    db.commit()
    return candidate_view(candidate)


@router.patch("/{identifier}")
def edit_candidate(identifier: str, body: CandidateUpdate, request: Request, db: DB, actor: Actor):
    require_role(actor, "hr")
    candidate = get_or_404(db, Candidate, identifier)
    update_version(db, candidate, body.expected_version, **body.model_dump(exclude={"expected_version", "contact"}))
    save_contact(db, request, actor, candidate.id, body.contact)
    record(db, actor, "candidate.edit", "candidate", candidate.id)
    db.commit()
    return candidate_view(candidate)
