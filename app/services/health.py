"""只读依赖探测；不生成模型文本，不在响应中公开地址、凭据或异常正文。"""

from concurrent.futures import ThreadPoolExecutor
import httpx
from sqlalchemy import text
from app.adapters.storage import ObjectStore
from app.adapters.vector import VectorStore
from app.adapters.graph import GraphStore


def integration_status(settings, sessions, cache):
    def mysql():
        with sessions() as session:
            session.execute(text("SELECT 1")).scalar_one()

    def storage():
        if not ObjectStore(settings).client.bucket_exists(settings.minio_bucket):
            raise RuntimeError("bucket missing")

    def graph():
        store = GraphStore(settings)
        try:
            store.driver.verify_connectivity()
        finally:
            store.close()

    def vector():
        store = VectorStore(settings)
        try:
            store.client.list_collections(timeout=5)
        finally:
            store.close()

    def embedding():
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(settings.embedding_base_url.rstrip("/") + "/api/tags")
            response.raise_for_status()
            if settings.embedding_model not in {m.get("name") for m in response.json().get("models", [])}:
                raise RuntimeError("model missing")

    def model():
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(
                settings.llm_base_url.rstrip("/") + "/models",
                headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
            )
            response.raise_for_status()
            if settings.llm_model not in {m.get("id") for m in response.json().get("data", [])}:
                raise RuntimeError("model missing")

    checks = [
        ("MySQL", mysql),
        ("Redis", cache.ping),
        ("MinIO", storage),
        ("Neo4j", graph),
        ("Milvus", vector),
        ("本地 BGE-M3", embedding),
        ("Spark-X2.6 网关", model),
    ]

    def probe(item):
        name, operation = item
        try:
            operation()
            message = "连接正常"
            if name in {"本地 BGE-M3", "Spark-X2.6 网关"}:
                message = "连接正常，模型已列出；实际推理以处理结果为准"
            return {"name": name, "ok": True, "message": message}
        except Exception:
            return {"name": name, "ok": False, "message": "连接不可用或所需资源未就绪，请检查本机服务"}

    with ThreadPoolExecutor(max_workers=7) as pool:
        return list(pool.map(probe, checks))
