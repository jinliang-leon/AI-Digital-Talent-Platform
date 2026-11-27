"""Run a synthetic resume-to-search check through the running Web proxy."""
import argparse
from pathlib import Path
import sys
from time import monotonic, sleep
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.core.config import configuration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--real-model", action="store_true", help="Send only this synthetic fixture to the configured provider")
    args = parser.parse_args()
    values = configuration()
    password = values.get("TALENT_DEMO_PASSWORD")
    if not password:
        parser.error("Missing TALENT_DEMO_PASSWORD")
    if args.real_model and (values.get("TALENT_EXTERNAL_AI_ENABLED") != "true" or not all(
        values.get("TALENT_" + key) for key in ("MODEL_URL", "MODEL_API_KEY", "CHAT_MODEL", "EMBEDDING_MODEL")
    )):
        parser.error("Real-model acceptance requires the enabled model provider and Embedding credentials")

    tag = uuid4().hex[:10]
    anchor = "SyntheticListing" + tag
    quote = "Managed Amazon US apparel listing experiments and tracked outcomes. " + anchor
    with httpx.Client(base_url=args.url, timeout=16, trust_env=False) as web:
        def call(method, path, expected=200, **kwargs):
            response = web.request(method, "/api/v1" + path, **kwargs)
            if response.status_code != expected:
                raise RuntimeError(f"{method} {path} expected {expected}, received {response.status_code}")
            return response.json() if expected != 403 else None

        auth = call("POST", "/auth/login", json={"username": "hr", "password": password})
        web.headers["Authorization"] = "Bearer " + auth["access_token"]

        def wait_task(task):
            deadline = monotonic() + (110 if args.real_model else 20)
            while monotonic() < deadline:
                result = call("GET", "/tasks/" + task["id"])
                if result["status"] == "failed":
                    raise RuntimeError(f"Worker failed: {result['error']}, trace={result['trace_id']}")
                if result["status"] == "succeeded":
                    return result["trace_id"]
                sleep(.35)
            raise TimeoutError(f"Worker task {task['id']} did not finish")

        batch = call("POST", "/resume-batches", 201, json={"synthetic": True, "model_authorized": args.real_model})
        batch_path = "/resume-batches/" + batch["id"] + "/files"
        binary = {"Content-Type": "application/octet-stream"}

        def upload(name, content):
            return call("POST", batch_path, 202, params={"filename": name}, headers=binary, content=content)

        contents = ("SYNTHETIC\n" + quote).encode()
        entry = upload("synthetic-listing.txt", contents)
        assert upload("copy.txt", contents)["status"] == "duplicate"
        assert upload("bad.png", b"not an image")["status"] == "rejected"
        parse_trace = wait_task(entry["tasks"][0])
        path = "/resume-uploads/" + entry["id"]
        draft = call("GET", path + "/draft")
        assert draft["status"] == "review_pending" and draft["blocks"][1]["text"] == quote
        if args.real_model and not draft["evidence"]:
            raise RuntimeError("The model returned no verified resume evidence")
        confirmed = call("POST", path + "/confirm", json={
            "expected_version": draft["version"], "candidate": {"name": "SYNTHETIC Resume " + tag},
            "evidence": [{"section": "employment", "block_id": "txt-2", "quote": quote}],
        })
        index_trace = wait_task(confirmed["tasks"][0])
        result = call("POST", "/talent-search", json={"query": anchor, "use_external_ai": args.real_model})
        assert any(item["candidate_id"] == confirmed["candidate_id"] and item["evidence"][0]["quote"] == quote
                   for item in result["items"])
        if args.real_model and result["mode"] != "hybrid_semantic":
            raise RuntimeError(f"Model search degraded: {result['mode']}")
        result = web.get("/api/v1/files/" + confirmed["file_id"] + "/download")
        assert result.status_code == 200 and result.content == contents
        print(f"PASS upload, dedupe, rejection, worker, review, candidate, index, search and download; "
              f"parse trace={parse_trace}, index trace={index_trace}")


if __name__ == "__main__":
    main()
