"""Validate the live RAG, interview and assistant endpoints against a real model."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.core.config import configuration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    settings = configuration()
    if not settings.get("TALENT_DEMO_PASSWORD") or settings.get("TALENT_EXTERNAL_AI_ENABLED") != "true":
        parser.error("An enabled model provider and demo login are required")
    marker = uuid4().hex[:8]
    with httpx.Client(base_url=args.url + "/api/v1/", timeout=125, trust_env=False) as client:
        def post(path, data):
            response = client.post(path, json=data)
            if not response.is_success:
                raise RuntimeError(f"{path} returned HTTP {response.status_code}: {response.json().get('message')}")
            return response

        login = post("auth/login", {"username": "hr", "password": settings["TALENT_DEMO_PASSWORD"]}).json()
        client.headers["Authorization"] = "Bearer " + login["access_token"]

        base = post("knowledge-bases", {"title": "SYNTHETIC interview policy " + marker}).json()
        kb = "knowledge-bases/" + base["id"]
        statement = "Interview feedback must be submitted within 24 hours."
        post(kb + "/documents", {"title": "SYNTHETIC policy", "text": statement, "model_authorized": True})
        answer = post(kb + "/answer", {"query": "When must interview feedback be submitted?"}).json()
        assert answer["status"] == "answered" and any("24 hours" in item["quote"] for item in answer["citations"])
        print("PASS real model RAG and grounded citations", flush=True)

        job = post("jobs", {"title": "SYNTHETIC Listing " + marker, "department": "Operations", "headcount": 1,
                            "responsibilities": "Amazon US listings", "required_skills": "listing",
                            "synthetic": True}).json()
        results = post("talent-search", {"query": "listings Amazon apparel"}).json()["items"]
        target = next(item for item in results if item["name"].startswith("SYNTHETIC Resume "))
        app = post("applications", {"job_id": job["id"], "candidate_id": target["candidate_id"]}).json()
        analysis = post("applications/" + app["id"] + "/analyses",
                        {"upload_id": target["resume_upload_id"]}).json()
        evidence = "analyses/" + analysis["id"]
        post(evidence + "/review", {"note": "SYNTHETIC verify sourcing"})
        guide = post(evidence + "/interview-guides", {"count": 1, "difficulty": "基础"}).json()
        assert len(guide["questions"]) == 1 and guide["questions"][0]["question"]
        print("PASS real model interview guide", flush=True)

        session = post("chat/sessions", {}).json()
        chat = "chat/sessions/" + session["id"]
        turn = {"client_id": marker, "job_id": job["id"], "query": "Show me the recruitment progress for the selected job."}
        events = post(chat + "/stream", turn).text
        assert "event: done" in events and '"intent": "job_progress"' in events, events
        history = client.get(chat + "/messages").json()
        assert history[-1]["status"] == "completed" and history[-1]["result"]["data"]["counts"] == {"collected": 1}
        print("PASS real model tool calling, scoped job progress, SSE and recovery", flush=True)


if __name__ == "__main__":
    main()
