"""冻结标注上的真实 API 验收；不修改候选事实、标注或阈值。"""

import json
import time
from pathlib import Path
from statistics import mean
from dotenv import dotenv_values
import httpx
from app.services.extraction import normalize


def precision_at(predicted, relevant, k):
    return len(set(predicted[:k]) & set(relevant)) / k


def recall_at(predicted, relevant, k):
    return len(set(predicted[:k]) & set(relevant)) / len(relevant) if relevant else None


def field_equal(actual, expected):
    if isinstance(expected, list):
        return isinstance(actual, list) and {normalize(v) for v in actual} == {normalize(v) for v in expected}
    if isinstance(expected, str):
        return isinstance(actual, str) and normalize(actual) == normalize(expected)
    return actual == expected


def main(port=8088):
    root = Path(__file__).resolve().parents[1]
    labels = json.loads((root / "样例数据/验收标注.json").read_text(encoding="utf-8"))
    cfg = dotenv_values(root / ".env")
    result = {
        "complete": False,
        "method": {
            "field_comparison": "字段逐项精确匹配；字符串仅NFKC/大小写/空白规范化，数组比较集合，不改措辞或标点",
            "precision": "每个JD的eligible组前3名，分母固定3，五个JD取平均",
            "recall": "十个可回答问题取实际召回人才前5名／冻结相关人数；无答案五题单独统计拒答",
            "thresholds": {"field_accuracy": 0.9, "precision_at_3": 0.8, "recall_at_5": 0.8},
        },
        "parsing": [],
        "matching": [],
        "questions": [],
    }
    destination = root / "文档/05_测试验收/真实模型验收结果.json"

    def save():
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=130) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": cfg["INITIAL_ADMIN_USERNAME"], "password": cfg["INITIAL_ADMIN_PASSWORD"]},
        )
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        talents = client.get("/api/v1/talents?page_size=100").json()["items"]
        by_name = {t["name"]: t for t in talents}
        by_id = {t["id"]: t for t in talents}
        import_path = root / "运行数据/样例导入结果.json"
        imported = json.loads(import_path.read_text(encoding="utf-8")) if import_path.exists() else {}
        by_file = {r["filename"]: r["talent_id"] for r in imported.get("resumes", [])}
        correct = 0
        total = 0
        for expected in labels["resumes"]:
            actual = by_id.get(by_file.get(expected["filename"])) or by_name.get(expected["name"])
            facts = actual["facts"] if actual else {}
            comparisons = {
                k: field_equal(facts.get(k), v) if actual else False for k, v in expected["facts"].items()
            }
            correct += sum(comparisons.values())
            total += len(comparisons)
            result["parsing"].append(
                {
                    "name": expected["name"],
                    "state": actual["index_status"] if actual else "MISSING",
                    "correct_fields": comparisons,
                    "actual_facts": facts,
                }
            )
        result["field_accuracy"] = correct / total
        result["field_counts"] = {"correct": correct, "total": total}
        save()
        jobs = client.get("/api/v1/jobs?page_size=100").json()["items"]
        for expected in labels["jobs"]:
            job = next(
                (j for j in jobs if j["title"] == expected["title"] and j["raw_jd"] == expected["raw_jd"]),
                None,
            )
            row = {"title": expected["title"], "precision_at_3": 0, "bidirectional_equal": False}
            if job:
                start = time.monotonic()
                response = client.post("/api/v1/jobs/" + job["id"] + "/matches")
                row["seconds"] = round(time.monotonic() - start, 2)
                row["status"] = response.status_code
                if response.is_success:
                    data = response.json()
                    top = data["eligible"][:3]
                    row.update(
                        {
                            "top_names": [i["name"] for i in top],
                            "eligible_count": len(data["eligible"]),
                            "needs_review_count": len(data["needs_review"]),
                            "excluded_count": data["excluded_count"],
                        }
                    )
                    row["precision_at_3"] = precision_at(row["top_names"], expected["relevant_names"], 3)
                    if top:
                        reverse = client.post("/api/v1/talents/" + top[0]["talent_id"] + "/job-matches")
                        if reverse.is_success:
                            pair = next(
                                (i for i in reverse.json()["eligible"] if i["job_id"] == job["id"]), None
                            )
                            row["bidirectional_equal"] = pair is not None and pair["score"] == top[0]["score"]
                else:
                    row["error_code"] = response.json().get("code")
            else:
                row["error_code"] = "JOB_MISSING"
            result["matching"].append(row)
            save()
            print(json.dumps({"job": row}, ensure_ascii=True), flush=True)
        source_cache = {}
        for expected in labels["questions"]:
            start = time.monotonic()
            row = {"question": expected["question"], "answerable": expected["answerable"]}
            try:
                response = client.post(
                    "/api/v1/search", json={"question": expected["question"], "filters": {}}
                )
                row["status"] = response.status_code
                if response.is_success:
                    data = response.json()
                    retrieved = [t["name"] for t in data.get("retrieved_talents", [])][:5]
                    row.update(
                        {
                            "answered": data["answered"],
                            "answer": data["answer"],
                            "retrieved_names": retrieved,
                            "citations": data["citations"],
                            "recall_at_5": recall_at(retrieved, expected["relevant_names"], 5),
                        }
                    )
                    valid = True
                    for cite in data["citations"]:
                        did = cite["document_id"]
                        if did not in source_cache:
                            response_chunks = client.get("/api/v1/resumes/" + did + "/chunks")
                            response_chunks.raise_for_status()
                            source_cache[did] = {c["id"]: c["text"] for c in response_chunks.json()["items"]}
                        valid = valid and cite["quote"] in source_cache[did].get(cite["chunk_id"], "")
                    row["citations_locatable"] = valid
                else:
                    row.update(
                        {
                            "error_code": response.json().get("code"),
                            "recall_at_5": 0 if expected["answerable"] else None,
                        }
                    )
            except httpx.HTTPError as exc:
                row.update(
                    {"error_code": type(exc).__name__, "recall_at_5": 0 if expected["answerable"] else None}
                )
            row["seconds"] = round(time.monotonic() - start, 2)
            result["questions"].append(row)
            save()
            print(
                json.dumps(
                    {
                        "question": row["question"],
                        "status": row.get("status"),
                        "seconds": row["seconds"],
                        "answered": row.get("answered"),
                        "error_code": row.get("error_code"),
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
    answerable = [q for q in result["questions"] if q["answerable"]]
    unknown = [q for q in result["questions"] if not q["answerable"]]
    result["summary"] = {
        "ready_resumes": sum(r["state"] == "READY" for r in result["parsing"]),
        "field_accuracy": result["field_accuracy"],
        "precision_at_3": mean(r["precision_at_3"] for r in result["matching"]),
        "recall_at_5": mean(q.get("recall_at_5", 0) or 0 for q in answerable),
        "unknown_refusal_rate": sum(q.get("answered") is False and not q.get("error_code") for q in unknown)
        / len(unknown),
        "all_citations_locatable": all(
            q.get("citations_locatable", False) for q in result["questions"] if q.get("answered")
        ),
        "rag_latency_max_seconds": max(q["seconds"] for q in result["questions"]),
    }
    result["complete"] = True
    save()
    print(json.dumps(result["summary"], ensure_ascii=True), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="冻结标注上的真实模型验收")
    parser.add_argument("--port", type=int, choices=[8088, 8089], default=8088)
    main(port=parser.parse_args().port)
