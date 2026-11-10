from uuid import uuid4
import pytest
from app.config import Settings
from app.adapters.vector import VectorStore
from app.adapters.graph import GraphStore
from app.errors import AppError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["profile", "job", "talent"])
def test_current_version_filter_precedes_top_k_over_historical_vectors(kind):
    store = VectorStore(Settings())
    entity_id = str(uuid4())
    query = [1.0] + [0.0] * 1023
    current = [0.0, 1.0] + [0.0] * 1022
    common = {"entity_id": entity_id, "document_id": "test", "document_revision": 1, "chunk_id": "test"}
    records = [{"id": f"{entity_id}-{n}", "revision": n, "vector": query, **common} for n in range(1, 131)]
    records.append({"id": f"{entity_id}-131", "revision": 131, "vector": current, **common})
    try:
        store.upsert(kind, records)
        hits = store.search(kind, query, 128, current_versions=[(entity_id, 131)])
        assert len(hits) == 1 and hits[0]["revision"] == 131
    finally:
        store.close()


def test_missing_current_graph_node_differs_from_grounded_empty_skills():
    graph = GraphStore(Settings())
    entity_id = str(uuid4())
    try:
        graph.sync("talent", entity_id, 1, [])
        assert graph.skills("talent", entity_id, 1) == []
        with pytest.raises(AppError) as error:
            graph.skills("talent", entity_id, 2)
        assert error.value.code == "GRAPH_INDEX_NOT_READY"
    finally:
        graph.close()
