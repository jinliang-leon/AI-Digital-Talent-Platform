"""中文简历全文按字节超过 MySQL TEXT 上限时仍可保存。"""

from uuid import uuid4
import pytest
from app.config import Settings
from app.db import make_session_factory
from app.models import Talent, ResumeDocument, User

pytestmark = pytest.mark.integration


def test_chinese_extracted_text_can_exceed_64_kib():
    factory, engine = make_session_factory(Settings())
    document_id = None
    try:
        with factory.begin() as session:
            user = User(username="容量_" + uuid4().hex, password_hash="unused-test-hash")
            session.add(user)
            session.flush()
            talent = Talent(name="全文容量测试", created_by=user.id)
            session.add(talent)
            session.flush()
            document = ResumeDocument(
                talent_id=talent.id,
                original_name="容量测试.pdf",
                object_key=uuid4().hex,
                sha256=uuid4().hex + uuid4().hex,
                mime_type="application/pdf",
                size_bytes=100,
                revision=1,
                extracted_text="中文简历事实。" * 6000,
            )
            session.add(document)
            session.flush()
            document_id = document.id
        with factory() as session:
            assert len(session.get(ResumeDocument, document_id).extracted_text) == 42000
    finally:
        engine.dispose()
