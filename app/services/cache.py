"""仅缓存可重算的查询向量，结果仍要重新经过 SQL 版本与权限过滤。"""

from hashlib import sha256
import json
from app.adapters.embedding import LocalEmbeddings, validate_vectors


def query_vector(text, settings, cache=None):
    key = (
        settings.project_namespace
        + ":embedding:"
        + sha256(
            (settings.embedding_model + ":" + str(settings.embedding_dimension) + ":" + text).encode()
        ).hexdigest()
    )
    if cache is not None:
        try:
            value = cache.get(key)
            if value:
                return validate_vectors([json.loads(value)], 1, settings.embedding_dimension)[0]
        except Exception:
            pass
    vector = LocalEmbeddings(settings).embed_query(text)
    if cache is not None:
        try:
            cache.setex(key, 600, json.dumps(vector))
        except Exception:
            pass
    return vector
