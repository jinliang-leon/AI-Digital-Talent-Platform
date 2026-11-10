"""Ollama BGE-M3 适配，拒绝错误维度、非有限值和零向量。"""

import math
import httpx
from langchain_core.embeddings import Embeddings
from app.errors import AppError


def validate_vectors(vectors, expected_count: int, dimension: int = 1024):
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise AppError("INVALID_EMBEDDING", "向量服务返回数量与输入不一致", 502)
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise AppError("INVALID_EMBEDDING", "向量维度与集合配置不一致", 502)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
            raise AppError("INVALID_EMBEDDING", "向量包含无效数值", 502)
        if sum(x * x for x in vector) <= 0:
            raise AppError("INVALID_EMBEDDING", "向量服务返回了零向量", 502)
    return vectors


class LocalEmbeddings(Embeddings):
    def __init__(self, settings):
        self.settings = settings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = []
        try:
            with httpx.Client(trust_env=False, timeout=90) as client:
                for start in range(0, len(texts), 8):
                    batch = texts[start : start + 8]
                    response = client.post(
                        self.settings.embedding_base_url.rstrip("/") + "/api/embed",
                        json={"model": self.settings.embedding_model, "input": batch, "truncate": False},
                    )
                    response.raise_for_status()
                    result.extend(
                        validate_vectors(
                            response.json().get("embeddings"), len(batch), self.settings.embedding_dimension
                        )
                    )
        except AppError:
            raise
        except Exception as exc:
            raise AppError("EMBEDDING_UNAVAILABLE", "本地 BGE-M3 向量服务暂不可用", 503) from exc
        return result

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
