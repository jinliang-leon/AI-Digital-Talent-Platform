"""首版项目表；只作用于本项目专用数据库。"""

from alembic import op
from pathlib import Path

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # 首版发布前冻结；后续模型变化不得改变已发布迁移的执行结果。
    snapshot = Path(__file__).with_name("0001_表结构快照.sql").read_text(encoding="utf-8")
    for statement in snapshot.split(";"):
        if statement.strip():
            op.execute(statement.strip())


def downgrade():
    raise RuntimeError("回退涉及数据删除，请先备份并采用显式人工回滚方案")
