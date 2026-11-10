"""Milvus 仅保存项目索引，不保存 HR 备注或主业务事实。"""

import json
from pymilvus import DataType, MilvusClient
from app.errors import AppError


class VectorStore:
    def __init__(self, settings):
        self.settings = settings
        try:
            self.client = MilvusClient(
                uri=settings.milvus_uri, token=settings.milvus_token.get_secret_value(), timeout=8
            )
        except Exception as exc:
            raise AppError("VECTOR_UNAVAILABLE", "Milvus 向量库暂不可用", 503) from exc

    def collection(self, kind: str) -> str:
        if kind not in {"talent", "job", "profile"}:
            raise ValueError("invalid vector entity")
        return f"{self.settings.project_namespace}_{kind}_bgem3_v1"

    def ensure(self, kind):
        name = self.collection(kind)
        if not self.client.has_collection(name):
            self.client.create_collection(
                collection_name=name,
                dimension=self.settings.embedding_dimension,
                primary_field_name="id",
                id_type=DataType.VARCHAR,
                max_length=64,
                auto_id=False,
                vector_field_name="vector",
                metric_type="COSINE",
                enable_dynamic_field=True,
            )
        schema = self.client.describe_collection(name)
        vector = next((f for f in schema["fields"] if f["name"] == "vector"), None)
        if not vector or int(vector.get("params", {}).get("dim", 0)) != self.settings.embedding_dimension:
            raise AppError("VECTOR_SCHEMA_MISMATCH", "现有向量集合维度与模型不一致，请新建集合重建", 503)
        return name

    def upsert(self, kind, records):
        if not records:
            return
        try:
            self.client.upsert(collection_name=self.ensure(kind), data=records)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("VECTOR_UNAVAILABLE", "向量索引写入失败", 503) from exc

    def search(self, kind, vector, limit=50, current_versions=None):
        if current_versions is not None and not current_versions:
            return []
        try:
            name = self.ensure(kind)
            expression = ""
            if current_versions is not None:
                expression = " or ".join(
                    f"(entity_id == {json.dumps(entity_id)} and revision == {int(revision)})"
                    for entity_id, revision in current_versions
                )
            results = self.client.search(
                collection_name=name,
                data=[vector],
                limit=min(limit, 128),
                filter=expression,
                output_fields=["entity_id", "revision", "document_id", "document_revision", "chunk_id"],
                search_params={"metric_type": "COSINE", "params": {}},
                consistency_level="Strong",
            )
            return [{"id": h["id"], "score": float(h["distance"]), **h["entity"]} for h in results[0]]
        except AppError:
            raise
        except Exception as exc:
            raise AppError("VECTOR_UNAVAILABLE", "向量检索暂不可用", 503) from exc

    def close(self):
        self.client.close()
