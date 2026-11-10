"""集成测试始终使用独立 MySQL 库、Redis 分区与索引命名空间。"""

import pytest
from dotenv import dotenv_values


@pytest.fixture(autouse=True)
def isolate_integration(request, monkeypatch):
    if request.node.get_closest_marker("integration") is None:
        yield
        return
    cfg = dotenv_values(".env")
    target = cfg.get("TEST_DATABASE_URL")
    if not target:
        pytest.fail("真实集成测试需要本地 .env 中的 TEST_DATABASE_URL，不能回退到演示数据库")
    monkeypatch.setenv("DATABASE_URL", target)
    monkeypatch.setenv("PROJECT_NAMESPACE", "ai_talent_test")
    monkeypatch.setenv("MINIO_BUCKET", "ai-talent-tests")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/10")
    from app.config import get_settings, Settings
    from app.db import make_session_factory
    from app.models import Base

    get_settings.cache_clear()
    factory, engine = make_session_factory(Settings())
    Base.metadata.create_all(engine)
    engine.dispose()
    yield
    get_settings.cache_clear()
