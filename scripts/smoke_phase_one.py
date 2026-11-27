"""Exercise the running frontend proxy, backend, and storage with synthetic records."""
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
    parser.add_argument("--require-compose", action="store_true")
    args = parser.parse_args()
    password = configuration().get("TALENT_DEMO_PASSWORD")
    if not password:
        parser.error("TALENT_DEMO_PASSWORD is missing")
    tag = uuid4().hex[:8]

    with httpx.Client(base_url=args.url, timeout=15, trust_env=False) as client:
        def call(method, path, expected=200, auth=None, **kwargs):
            headers = kwargs.pop("headers", {})
            if auth:
                headers["Authorization"] = "Bearer " + auth
            res = client.request(method, "/api/v1" + path, headers=headers, **kwargs)
            assert res.status_code == expected, (method, path, res.status_code)
            return res.json() if expected not in (204, 403, 404, 409) else None

        def login(name):
            return call("POST", "/auth/login", json={"username": name, "password": password})["access_token"]

        index = client.get("/")
        assert index.status_code == 200 and "/app.js" in index.text
        assert client.get("/app.js").status_code == 200
        assert client.get("/styles.css").status_code == 200
        checks = client.get("/health/ready").json()
        assert checks["checks"]["database"] == checks["checks"]["migration"] == "ok"
        if args.require_compose:
            assert checks["status"] == "ok" and all(checks["checks"][key] == "ok"
                                                     for key in ("pgvector", "redis", "minio")), checks
        else:
            assert checks["status"] in {"local_only", "ok"}
        hr, interviewer, admin = (login(actor) for actor in ("hr", "interviewer", "admin"))
        reviewer = call("GET", "/auth/me", auth=interviewer)
        job_body = {"title": f"SYNTHETIC 亚马逊运营验收 {tag}", "department": "运营部", "headcount": 1,
                    "responsibilities": "跟进服装类目 Listing", "required_skills": "Amazon US", "synthetic": True}
        posting = call("POST", "/jobs", 201, hr, json=job_body)
        job_path = "/jobs/" + posting["id"]
        posting = call("PATCH", job_path, auth=hr, json={**job_body, "headcount": 2, "expected_version": posting["version"]})
        other_job = call("POST", "/jobs", 201, hr, json={**job_body, "title": f"SYNTHETIC 广告运营验收 {tag}"})
        candidate = call("POST", "/candidates", 201, hr, json={"name": f"SYNTHETIC 虚构运营 {tag}", "synthetic": True})
        candidate_path = "/candidates/" + candidate["id"]
        assert call("GET", candidate_path, auth=hr)["id"] == candidate["id"]
        call("GET", candidate_path, 403, admin)
        call("GET", candidate_path, 404, interviewer)
        app = call("POST", "/applications", 201, hr, json={"job_id": posting["id"], "candidate_id": candidate["id"]})
        separate = call("POST", "/applications", 201, hr, json={"job_id": other_job["id"], "candidate_id": candidate["id"]})
        path = "/applications/" + app["id"]
        app = call("POST", path + "/assign", auth=hr, json={
            "expected_version": app["version"], "interviewer_id": reviewer["id"]})
        assert call("GET", path, auth=interviewer)["id"] == app["id"]
        data = "SYNTHETIC 招聘验收简历。不含真实个人资料。".encode()
        file = call("POST", candidate_path + "/files", 201, hr, params={"filename": "synthetic-resume.txt"},
                    headers={"Content-Type": "application/octet-stream"}, content=data)
        download = "/api/v1/files/" + file["id"] + "/download"
        assert client.get(download, headers={"Authorization": "Bearer " + hr}).content == data
        assert client.get(download, headers={"Authorization": "Bearer " + interviewer}).status_code == 404
        for target, actor in (("pending_review", hr), ("pending_interview", hr), ("interviewing", interviewer)):
            app = call("POST", path + "/transitions", auth=actor,
                       json={"expected_version": app["version"], "status": target, "reason": "SYNTHETIC 联调验收"})
        app = call("POST", path + "/feedback", 201, interviewer,
                   json={"expected_version": app["version"], "notes": "模拟面试已完成"})
        app = call("POST", path + "/transitions", auth=hr, json={
            "expected_version": app["version"], "status": "hired", "reason": "SYNTHETIC 人工结论"})
        app = call("POST", path + "/reopen", auth=hr, json={"expected_version": app["version"], "reason": "SYNTHETIC 重开"})
        assert app["status"] == "pending_review"
        assert call("GET", "/applications/" + separate["id"], auth=hr)["status"] == "collected"
        assert call("POST", job_path + "/close", auth=hr,
                    json={"expected_version": posting["version"], "reason": "SYNTHETIC 关闭岗位"})["status"] == "closed"
        additional = call("POST", "/candidates", 201, hr, json={"name": f"SYNTHETIC 第二候选人 {tag}", "synthetic": True})
        call("POST", "/applications", 409, hr, json={"job_id": posting["id"], "candidate_id": additional["id"]})
        assert call("GET", path, auth=hr)["status"] == "pending_review"
        assert any(event["action"] == "application.reopen" and event["resource_id"] == app["id"]
                   for event in call("GET", "/audit-logs", auth=admin))
        for actor in (hr, interviewer, admin):
            call("POST", "/auth/logout", 204, actor)
    print(f"PASS frontend proxy, auth/RBAC, jobs, candidates, multi-job applications, file round trip, feedback, audit ({checks['profile']})")


if __name__ == "__main__":
    main()
