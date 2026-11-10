"""连接池与请求事务边界，不在导入时连接数据库。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url


def make_session_factory(settings):
    url = make_url(settings.database_url.get_secret_value())
    if settings.database_host_override:
        url = url.set(host=settings.database_host_override)
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800, connect_args={"connect_timeout": 5})
    return sessionmaker(engine, expire_on_commit=False), engine
