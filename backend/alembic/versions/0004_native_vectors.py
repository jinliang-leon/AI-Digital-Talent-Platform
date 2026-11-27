"""Store PostgreSQL resume embeddings as native pgvector vectors."""
import sqlalchemy as sa
from alembic import op

from backend.app.core.vector_type import Vector

revision = "0004_vectors"
down_revision = "0003_assistant"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.alter_column("resume_chunks", "embedding", existing_type=sa.JSON(),
                        type_=Vector(), postgresql_using="embedding::text::vector")


def downgrade():
    if op.get_context().dialect.name == "postgresql":
        op.alter_column("resume_chunks", "embedding", existing_type=Vector(),
                        type_=sa.JSON(), postgresql_using="embedding::text::json")
