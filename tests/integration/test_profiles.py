from uuid import uuid4
import pytest
from sqlalchemy import select

from app.cli import seed_roles
from app.config import Settings
from app.db import make_session_factory
from app.errors import AppError
from app.models import User, Role, Talent, ResumeDocument, SourceChunk, Evidence, TalentNote, TalentRevision
from app.services.auth import hash_password
from app.services.profiles import update_profile, add_note

pytestmark = pytest.mark.integration


@pytest.fixture
def profile():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    with factory.begin() as session:
        seed_roles(session)
        actor = User(
            username="档案测试_" + uuid4().hex[:10],
            password_hash=hash_password("Profile-test-password"),
            roles=[session.scalar(select(Role).where(Role.code == "hr"))],
        )
        session.add(actor)
        session.flush()
        talent = Talent(
            name="张三",
            created_by=actor.id,
            facts={
                "name": "张三",
                "skills": ["Python"],
                "experience_months": None,
                "projects": [],
                "experiences": [],
                "education": [],
            },
            index_status="READY",
        )
        session.add(talent)
        session.flush()
        doc = ResumeDocument(
            talent_id=talent.id,
            original_name="虚构简历.docx",
            object_key="test/" + uuid4().hex,
            sha256=uuid4().hex + uuid4().hex,
            mime_type="test",
            size_bytes=1,
            status="PARSED",
        )
        session.add(doc)
        session.flush()
        talent.active_document_id = doc.id
        chunk = SourceChunk(
            document_id=doc.id,
            document_revision=1,
            chunk_no=0,
            text="姓名：张三。常用名：张小三。技能：Python、FastAPI。",
            paragraph_no=1,
            char_start=0,
            char_end=38,
        )
        session.add(chunk)
        session.flush()
        session.add(
            Evidence(
                talent_id=talent.id,
                document_id=doc.id,
                document_revision=1,
                talent_revision=1,
                field_path="name",
                chunk_id=chunk.id,
                quote="姓名：张三",
            )
        )
        session.add(
            Evidence(
                talent_id=talent.id,
                document_id=doc.id,
                document_revision=1,
                talent_revision=1,
                field_path="skills",
                chunk_id=chunk.id,
                quote="技能：Python",
            )
        )
        session.add(
            TalentRevision(
                talent_id=talent.id,
                revision=1,
                document_id=doc.id,
                factual_snapshot=talent.facts,
                evidence_snapshot=[],
                reason="测试基线",
            )
        )
        result = (factory, settings, actor.id, talent.id, chunk.id)
    yield result
    engine.dispose()


def test_edit_requires_source_and_preserves_revision_history(profile):
    factory, settings, actor, talent_id, chunk_id = profile
    with factory.begin() as session:
        with pytest.raises(AppError) as error:
            update_profile(
                session,
                talent_id,
                1,
                {"name": "完全编造"},
                {"name": [{"chunk_id": chunk_id, "quote": "姓名：张三"}]},
                actor,
            )
        assert error.value.code == "EVIDENCE_REQUIRED"
    with factory.begin() as session:
        item = update_profile(
            session,
            talent_id,
            1,
            {"name": "张小三"},
            {"name": [{"chunk_id": chunk_id, "quote": "常用名：张小三"}]},
            actor,
        )
        assert item.revision == 2 and item.name == "张小三"
        assert item.index_status == "PENDING"
    with factory.begin() as session:
        with pytest.raises(AppError) as error:
            update_profile(
                session,
                talent_id,
                1,
                {"name": "张三"},
                {"name": [{"chunk_id": chunk_id, "quote": "姓名：张三"}]},
                actor,
            )
        assert error.value.status == 409
        snapshots = list(
            session.scalars(
                select(TalentRevision)
                .where(TalentRevision.talent_id == talent_id)
                .order_by(TalentRevision.revision)
            )
        )
        assert [s.factual_snapshot["name"] for s in snapshots] == ["张三", "张小三"]


def test_note_is_separate_and_cannot_change_factual_revision(profile):
    factory, settings, actor, talent_id, chunk_id = profile
    with factory.begin() as session:
        note = add_note(session, talent_id, "这是未证实的 HR 备注，不能作为技能证据", actor)
        assert note.content.startswith("这是")
        talent = session.get(Talent, talent_id)
        assert talent.revision == 1
        assert "备注" not in str(talent.facts)
        assert session.scalar(select(TalentNote).where(TalentNote.talent_id == talent_id)) is not None


@pytest.mark.parametrize("state", ["PENDING", "FAILED"])
def test_edit_without_current_parse_snapshot_is_rejected(profile, state):
    factory, settings, actor, talent_id, chunk_id = profile
    with factory.begin() as session:
        talent = session.get(Talent, talent_id)
        talent.revision = 2
        talent.index_status = state
    with factory.begin() as session:
        with pytest.raises(AppError) as error:
            update_profile(
                session,
                talent_id,
                2,
                {"name": "张小三"},
                {"name": [{"chunk_id": chunk_id, "quote": "常用名：张小三"}]},
                actor,
            )
        assert error.value.status == 409
        assert session.get(Talent, talent_id).revision == 2


def test_array_correction_reuses_unchanged_grounded_values_across_chunks(profile):
    factory, settings, actor, talent_id, chunk_id = profile
    with factory.begin() as session:
        first = session.get(SourceChunk, chunk_id)
        second = SourceChunk(
            document_id=first.document_id,
            document_revision=1,
            chunk_no=1,
            text="技能：FastAPI",
            paragraph_no=2,
            char_start=38,
            char_end=49,
        )
        session.add(second)
        session.flush()
        second_id = second.id
    with factory.begin() as session:
        item = update_profile(
            session,
            talent_id,
            1,
            {"skills": ["Python", "FastAPI"]},
            {"skills": [{"chunk_id": second_id, "quote": "技能：FastAPI"}]},
            actor,
        )
        assert item.facts["skills"] == ["Python", "FastAPI"]
        evidence = list(
            session.scalars(
                select(Evidence).where(
                    Evidence.talent_id == talent_id,
                    Evidence.talent_revision == item.revision,
                    Evidence.field_path == "skills",
                )
            )
        )
        assert {e.chunk_id for e in evidence} == {chunk_id, second_id}
