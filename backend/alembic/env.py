from alembic import context

from backend.app.core.config import Settings
from backend.app.db import Base, make_engine
from backend.app import assistant_models, models, resume_models  # Load table metadata.


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run():
    connection = context.config.attributes.get("connection")
    if connection is not None:
        migrate(connection)
        return
    url = Settings.load().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()


run()
