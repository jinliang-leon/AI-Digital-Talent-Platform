"""Run DeepEval on responses from the live synthetic-only RAG acceptance fixture."""
import argparse
from pathlib import Path
import sys
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.core.config import configuration


FIXTURES = [
    ("面试反馈多久填写？", "SYNTHETIC：面试反馈必须在面试结束后 24 小时内填写。"),
    ("复面由谁安排？", "SYNTHETIC：复面由招聘专员确认面试官与候选人的时间后安排。"),
    ("岗位关闭后如何处理新应聘？", "SYNTHETIC：岗位关闭后，系统停止接受该岗位的新应聘。"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--judge-model", required=True)
    args = parser.parse_args()
    config = configuration()
    if not config.get("TALENT_DEMO_PASSWORD") or config.get("TALENT_EXTERNAL_AI_ENABLED") != "true":
        parser.error("The demo login and real model provider must be configured")
    try:
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except ImportError:
        parser.error("Install requirements-eval.txt first")

    failed = 0
    with httpx.Client(base_url=args.url + "/api/v1/", timeout=45, trust_env=False) as web:
        def post(path, data):
            response = web.post(path, json=data)
            if not response.is_success:
                raise RuntimeError(f"{path}: HTTP {response.status_code}")
            return response.json()

        auth = post("auth/login", {"username": "hr", "password": config["TALENT_DEMO_PASSWORD"]})
        web.headers["Authorization"] = "Bearer " + auth["access_token"]
        for ordinal, (question, gold) in enumerate(FIXTURES, start=1):
            scope = post("knowledge-bases", {"title": "SYNTHETIC DeepEval " + uuid4().hex[:8]})
            path = "knowledge-bases/" + scope["id"]
            post(path + "/documents", {"title": "SYNTHETIC evaluation", "text": gold, "model_authorized": True})
            result = post(path + "/answer", {"query": question})
            quotes = [citation["quote"] for citation in result["citations"]]
            if not quotes or any(quote not in gold for quote in quotes):
                print(f"FAIL fixture={ordinal} prerequisite=grounded_evidence")
                failed += 1
                continue
            case = LLMTestCase(input=question, actual_output="\n".join(quotes), expected_output=gold,
                               retrieval_context=[gold])
            for metric_class in (FaithfulnessMetric, AnswerRelevancyMetric):
                metric = metric_class(model=args.judge_model, threshold=.8, async_mode=False)
                metric.measure(case)
                successful = metric.is_successful()
                print(f"{'PASS' if successful else 'FAIL'} fixture={ordinal} "
                      f"metric={metric_class.__name__} score={metric.score}")
                failed += not successful
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
