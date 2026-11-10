"""中文全文使用 LONGTEXT，避免 TEXT 的 64KiB 字节限制。"""

from alembic import op
from sqlalchemy.dialects.mysql import LONGTEXT, TEXT

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "resume_documents", "extracted_text", type_=LONGTEXT(), existing_type=TEXT(), existing_nullable=False
    )


def downgrade():
    raise RuntimeError("降级可能截断简历全文；请备份后采用显式人工回滚方案")
