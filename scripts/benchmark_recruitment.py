"""Measure the local list and retrieval endpoints with 300 synthetic indexed resumes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from statistics import median, quantiles
import sys
from time import perf_counter
from uuid import uuid4

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.core.config import ROOT, Settings
from backend.app.core.embedding import lexical_vector
from backend.app.core.migrations import config_for
from backend.app.core.security import passwords
from backend.app.db import make_engine
from backend.app.main import create_app
from backend.app.models import Candidate, User
from backend.app.resume_models import AsyncTask, ResumeBatch, ResumeChunk, ResumeUpload


def main():
    directory = ROOT / ".runtime" / ("benchmark-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    settings = replace(Settings.load(), profile="local", database_url="sqlite:///" + str(directory / "bench.db"),
                       storage_path=directory / "objects", external_ai_enabled=False, langfuse_url="")
    engine = make_engine(settings.database_url)
    config = config_for()
    count = 300
    password = uuid4().hex + uuid4().hex
    with engine.connect() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    with Session(engine) as db:
        actor = User(username="benchmark_hr", name="SYNTHETIC", role="hr",
                     password_hash=passwords.hash(password))
        db.add(actor)
        db.flush()
        batch = ResumeBatch(created_by=actor.id, synthetic=True, model_authorized=False, upload_count=0)
        db.add(batch)
        candidates = [Candidate(name=f"SYNTHETIC {index:03}", synthetic=True) for index in range(count)]
        db.add_all(candidates)
        db.flush()
        uploads = [ResumeUpload(batch_id=batch.id, candidate_id=candidate.id, filename="synthetic.txt",
                                media_type="text/plain", size_bytes=40, status="confirmed") for candidate in candidates]
        db.add_all(uploads)
        db.flush()
        for index, upload in enumerate(uploads):
            text = (f"Amazon US Listing 优化和广告实验 {index}" if index % 9 == 0
                    else f"仓库管理和供应商交接 Inventory {index}")
            db.add(AsyncTask(upload_id=upload.id, kind="index", status="succeeded", trace_id=uuid4().hex))
            db.add(ResumeChunk(upload_id=upload.id, ordinal=0, text=text, evidence={"quote": text, "source": "line:2"},
                               embedding=lexical_vector(text), embedding_revision="lexical-hash-256-v1"))
        db.commit()
    engine.dispose()

    with TestClient(create_app(settings)) as client:
        auth = client.post("/api/v1/auth/login", json={"username": "benchmark_hr", "password": password})
        auth.raise_for_status()
        headers = {"Authorization": "Bearer " + auth.json()["access_token"]}

        def run(endpoint, index):
            started = perf_counter()
            if endpoint == "list":
                result = client.get("/api/v1/candidates?limit=20", headers=headers)
                assert result.json()["total"] == count
            else:
                result = client.post("/api/v1/talent-search", headers=headers, json={"query": "Listing 广告"})
                assert len(result.json()["items"]) == 10
            result.raise_for_status()
            return (perf_counter() - started) * 1000

        list_p95 = 0.0
        for endpoint in ("list", "search"):
            run(endpoint, 0)
            with ThreadPoolExecutor(max_workers=10) as pool:
                samples = list(pool.map(lambda index: run(endpoint, index), range(60)))
            p95 = quantiles(samples, n=20, method="inclusive")[18]
            print(f"{endpoint} resumes={count} concurrency=10 n={len(samples)} "
                  f"p50_ms={median(samples):.1f} p95_ms={p95:.1f}")
            if endpoint == "list":
                list_p95 = p95
        return int(list_p95 > 500)


if __name__ == "__main__":
    raise SystemExit(main())
