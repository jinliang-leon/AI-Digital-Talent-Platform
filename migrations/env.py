"""从未跟踪配置加载数据库连接，不在 ini 文件保存密码。"""

from alembic import context
from app.config import get_settings
from app.db import make_session_factory
from app.models import Base


def run():
    _, engine = make_session_factory(get_settings())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
