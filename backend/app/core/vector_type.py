"""Native PostgreSQL vector storage and a portable JSON representation for local development."""
import json

from sqlalchemy import JSON, cast
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions=None):
        self.dimensions = dimensions

    def get_col_spec(self, **kw):
        return f"VECTOR({self.dimensions})" if self.dimensions else "VECTOR"

    def bind_expression(self, bindvalue):
        return cast(bindvalue, self)

    def bind_processor(self, dialect):
        return lambda value: json.dumps(value, separators=(",", ":"), allow_nan=False) if value is not None else None

    def result_processor(self, dialect, coltype):
        return lambda value: json.loads(value) if isinstance(value, (str, bytes)) else value


ischema_names.setdefault("vector", Vector)
vector_storage = JSON().with_variant(Vector(), "postgresql")
