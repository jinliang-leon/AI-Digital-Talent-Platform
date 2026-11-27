"""Run DeepEval on responses from the live synthetic-only RAG acceptance fixture."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.core.config import configuration


FIXTURE = Path(__file__).resolve().parents[1] / "datasets" / "synthetic" / "evaluation.json"


def without_fixture_label(value):
    return value.removeprefix("SYNTHETIC：").strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--judge-ollama-url", help="Ollama base URL, e.g. http://127.0.0.1:11434")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    config = configuration()
    if not config.get("TALENT_DEMO_PASSWORD") or config.get("TALENT_EXTERNAL_AI_ENABLED") != "true":
        parser.error("The demo login and real model provider must be configured")
    try:
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except ImportError:
        parser.error("Install requirements-eval.txt first")

    model = args.judge_model
    if args.judge_ollama_url:
        from deepeval.models import OllamaModel

        model = OllamaModel(model=args.judge_model, base_url=args.judge_ollama_url, temperature=0,
                            generation_kwargs={"num_predict": 384})

    results = []
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with httpx.Client(base_url=args.url + "/api/v1/", timeout=125, trust_env=False) as web:
        def post(path, data):
            response = web.post(path, json=data)
            if not response.is_success:
                raise RuntimeError(f"{path}: HTTP {response.status_code}")
            return response.json()

        auth = post("auth/login", {"username": "hr", "password": config["TALENT_DEMO_PASSWORD"]})
        web.headers["Authorization"] = "Bearer " + auth["access_token"]
        for ordinal, fixture in enumerate(dataset["rag"], start=1):
            question, gold = fixture["question"], fixture["source"]
            scope = post("knowledge-bases", {"title": "SYNTHETIC DeepEval " + uuid4().hex[:8]})
            path = "knowledge-bases/" + scope["id"]
            post(path + "/documents", {"title": "SYNTHETIC evaluation", "text": gold, "model_authorized": True})
            post(path + "/documents", {"title": "SYNTHETIC distractor",
                                       "text": "SYNTHETIC：访客到达前台后应登记。",
                                       "model_authorized": True})
            sources = post(path + "/search", {"query": question})["items"]
            context = [source["quote"] for source in sources]
            result = post(path + "/answer", {"query": question})
            quotes = [citation["quote"] for citation in result["citations"]]
            grounded = bool(quotes) and gold in context and all(quote in gold for quote in quotes)
            results.append({"case": ordinal, "metric": "grounded_evidence", "passed": grounded})
            if not grounded:
                print(f"FAIL fixture={ordinal} prerequisite=grounded_evidence", flush=True)
                continue
            case = LLMTestCase(input=question, actual_output="\n".join(map(without_fixture_label, quotes)),
                               expected_output=without_fixture_label(gold),
                               retrieval_context=list(map(without_fixture_label, context)))
            for metric_class in (FaithfulnessMetric, AnswerRelevancyMetric):
                metric = metric_class(model=model, threshold=.8, async_mode=False)
                try:
                    metric.measure(case)
                except Exception as exc:
                    print(f"ERROR fixture={ordinal} metric={metric_class.__name__} type={type(exc).__name__}", flush=True)
                    results.append({"case": ordinal, "metric": metric_class.__name__, "passed": False,
                                    "error_type": type(exc).__name__})
                    continue
                successful = metric.is_successful()
                print(f"{'PASS' if successful else 'FAIL'} fixture={ordinal} "
                      f"metric={metric_class.__name__} score={metric.score}", flush=True)
                results.append({"case": ordinal, "metric": metric_class.__name__, "score": metric.score,
                                "passed": successful, "reason": metric.reason})
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"dataset": dataset["dataset"], "judge": args.judge_model,
                                           "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(not all(result["passed"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
