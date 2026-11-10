"""通过真实API导入冻结样例，并用真实模型和索引同步处理。可重复执行。"""

import json
from pathlib import Path
import time

from dotenv import dotenv_values
import httpx

from app.config import get_settings
from app.db import make_session_factory
from app.models import Task, Talent
from app.worker import process_task
from app.services.tasks import recover_tasks


def main(only_jobs=False):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "样例数据/验收标注.json").read_text(encoding="utf-8"))
    cfg = dotenv_values(root / ".env")
    settings = get_settings()
    factory, engine = make_session_factory(settings)
    recover_tasks(settings)
    report = {"resumes": [], "jobs": [], "complete": False}
    target = root / ("运行数据/样例岗位导入结果.json" if only_jobs else "运行数据/样例导入结果.json")
    target.parent.mkdir(parents=True, exist_ok=True)

    def save():
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8088", trust_env=False, timeout=30) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": cfg.get("INITIAL_ADMIN_USERNAME", "admin"),
                "password": cfg["INITIAL_ADMIN_PASSWORD"],
            },
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        # 只归档本代理早期连通性测试产生、尚未解析的明确测试档案，不触碰用户资料。
        for item in client.get("/api/v1/talents?page_size=100").json()["items"]:
            if item["id"] == "cac87acc-3773-4c70-964d-fa8ee2b63385" and item["name"] == "待解析人才":
                client.delete("/api/v1/talents/" + item["id"]).raise_for_status()
        for sample in [] if only_jobs else manifest["resumes"]:
            start = time.monotonic()
            path = root / "样例数据/简历" / sample["filename"]
            with path.open("rb") as f:
                response = client.post(
                    "/api/v1/resumes", files={"file": (sample["filename"], f, "application/pdf")}
                )
            response.raise_for_status()
            record = response.json()
            status = client.get("/api/v1/tasks/" + record["task_id"]).json()
            if status["state"] == "FAILED":
                client.post("/api/v1/tasks/" + record["task_id"] + "/retry").raise_for_status()
            elif status["state"] == "READY":
                current = client.get("/api/v1/talents/" + record["talent_id"]).json()
                facts = current["facts"]
                # 冻结样例有明确的技能/经验；仅修复早期格式缺失，不修改标注或历史快照。
                if (sample["facts"]["skills"] and not facts.get("skills")) or (
                    sample["facts"]["experience_months"] is not None
                    and facts.get("experience_months") is None
                ):
                    response = client.post(
                        "/api/v1/talents/" + record["talent_id"] + "/reparse",
                        json={"base_revision": current["revision"]},
                    )
                    response.raise_for_status()
                    record.update(response.json())
            process_task(record["task_id"], settings=settings)
            with factory() as session:
                task = session.get(Task, record["task_id"])
                talent = session.get(Talent, record["talent_id"])
                row = {
                    "name": sample["name"],
                    "filename": sample["filename"],
                    **record,
                    "state": task.state,
                    "error_code": task.error_code,
                    "message": task.safe_error_message,
                    "seconds": round(time.monotonic() - start, 2),
                    "processing_seconds": round((task.finished_at - task.started_at).total_seconds(), 2)
                    if task.finished_at and task.started_at
                    else None,
                    "facts": talent.facts,
                }
            report["resumes"].append(row)
            save()
            print(
                json.dumps(
                    {
                        "resume": sample["name"],
                        "state": row["state"],
                        "seconds": row["seconds"],
                        "error_code": row["error_code"],
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
            if row["state"] != "READY":
                print("该份模型/索引失败已记录；继续其余独立样例，不自动无限重试。", flush=True)
        existing = client.get("/api/v1/jobs?page_size=100").json()["items"]
        for sample in manifest["jobs"]:
            start = time.monotonic()
            prior = next(
                (j for j in existing if j["title"] == sample["title"] and j["raw_jd"] == sample["raw_jd"]),
                None,
            )
            if prior and prior["index_status"] == "READY":
                row = prior | {"state": "READY", "seconds": 0}
            else:
                body = {k: sample[k] for k in ("title", "raw_jd", "hard_conditions", "hard_confirmed")}
                response = client.post("/api/v1/jobs", json=body)
                response.raise_for_status()
                record = response.json()
                process_task(record["task_id"], settings=settings)
                with factory() as session:
                    task = session.get(Task, record["task_id"])
                    row = record | {
                        "state": task.state,
                        "error_code": task.error_code,
                        "message": task.safe_error_message,
                        "seconds": round(time.monotonic() - start, 2),
                    }
            report["jobs"].append(row)
            save()
            print(
                json.dumps(
                    {"job": sample["title"], "state": row["state"], "seconds": row["seconds"]},
                    ensure_ascii=True,
                ),
                flush=True,
            )
    report["complete"] = all(r["state"] == "READY" for r in report["resumes"] + report["jobs"])
    save()
    engine.dispose()
    print("真实模型及本地索引导入完成。", flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="导入虚构演示数据")
    parser.add_argument("--only-jobs", action="store_true", help="只导入五个演示岗位")
    main(only_jobs=parser.parse_args().only_jobs)
