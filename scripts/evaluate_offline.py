"""Run reproducible retrieval, privacy and tool-contract gates on synthetic fixtures."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import httpx
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ai_resume.documents import DocumentError, extract_document
from backend.app.ai_resume.structured import extract_professional_facts, redact_working_copy, verify_evidence
from backend.app.core.ai_client import ModelError, OpenAICompatible
from backend.app.core.config import Settings
from backend.app.core.embedding import lexical_vector
from backend.app.core.retrieval import rank
from backend.app.modules.chat import RecruitmentAction

FIXTURE = ROOT / "datasets" / "synthetic" / "evaluation.json"
VECTOR_REVISION = "lexical-hash-256-v1"


@dataclass
class EvaluationRow:
    key: str
    text: str
    embedding: list[float]
    embedding_revision: str = VECTOR_REVISION


def evaluate(exact_match=None):
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows, results = [], []

    def record(group, name, actual, expected):
        passed = (actual == expected if exact_match is None else exact_match(f"{group}:{name}", actual, expected))
        results.append({"group": group, "case": name, "passed": passed})

    for sample in dataset["resumes"]:
        raw = extract_document("sample.txt", sample["text"].encode())
        blocks = redact_working_copy(raw)
        safe_text = "\n".join(block.text for block in blocks)
        hidden = sample["private_text"] + sample["blocked_text"]
        record("redaction", sample["id"], sum(value in safe_text for value in hidden), 0)

        evidence = []
        for quote in sample["gold"]:
            source = next(block for block in blocks if quote in block.text)
            evidence.append({"section": "employment", "block_id": source.id, "quote": quote})
        checked = verify_evidence({"items": evidence}, blocks)
        resolved = [next(block for block in raw if block.id == item.block_id).text[item.start:item.end]
                    for item in checked]
        record("evidence", sample["id"], resolved, sample["gold"])
        for block in raw:
            if any(value in block.text for value in hidden):
                try:
                    verify_evidence({"items": [{"section": "skill", "block_id": block.id, "quote": block.text}]}, raw)
                except DocumentError:
                    rejected = True
                else:
                    rejected = False
                record("unsafe-evidence", f"{sample['id']}:{block.id}", rejected, True)
        text = " ".join(sample["gold"])
        rows.append(EvaluationRow(sample["id"], text, lexical_vector(text)))

    for search in dataset["retrieval"]:
        query = search["query"]
        hits = rank(query, rows, {VECTOR_REVISION: lexical_vector(query)})
        record("retrieval@1", search["expected_first"], hits[0][0].key if hits else None, search["expected_first"])

    permitted = {"talent_search", "job_progress", "knowledge_answer", "interview_draft"}
    for tool in dataset["tools"]:
        try:
            action = RecruitmentAction(intent=tool["intent"], rewritten_query=tool["query"])
        except ValidationError:
            accepted = False
        else:
            accepted = action.intent == tool["intent"]
        record("tool-schema", tool["intent"], accepted, tool["intent"] in permitted)
    return {"dataset": dataset["dataset"], "mode": "deepeval-exact-match" if exact_match else "deterministic", "cases": results,
            "passed": sum(case["passed"] for case in results), "total": len(results)}


def score_with_deepeval(name, actual, expected):
    from deepeval.metrics import ExactMatchMetric
    from deepeval.test_case import LLMTestCase

    metric = ExactMatchMetric(threshold=1.0)
    metric.measure(LLMTestCase(input=name, actual_output=json.dumps(actual, ensure_ascii=False),
                               expected_output=json.dumps(expected, ensure_ascii=False)))
    return metric.is_successful()


def evaluate_live():
    dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
    settings = Settings.load()
    if not settings.external_ai_enabled:
        raise ValueError("Enable a model provider for the live evaluation")
    results = []
    with httpx.Client() as client:
        model = OpenAICompatible(client, settings.model_url, settings.model_key, settings.chat_model,
                                 timeout=settings.model_timeout)
        for sample in dataset["resumes"]:
            blocks = extract_document("sample.txt", sample["text"].encode())
            try:
                extracted = extract_professional_facts(blocks, model, external_processing_authorized=True)
            except (DocumentError, ModelError) as exc:
                results.append({"case": sample["id"], "passed": False, "error_code": exc.code})
                continue
            quotes = [item.quote for item in extracted.evidence]
            expected = sample["gold"]
            covered = sum(any(gold.rstrip("。.").casefold() in quote.casefold() for quote in quotes)
                          for gold in expected)
            exposure = sum(any(value in quote for quote in quotes)
                           for value in sample["private_text"] + sample["blocked_text"])
            results.append({"case": sample["id"], "passed": covered == len(expected) and exposure == 0,
                            "gold_evidence_found": covered, "gold_evidence_total": len(expected),
                            "forbidden_output_matches": exposure, "verified_evidence": len(quotes)})
    return {"dataset": dataset["dataset"], "mode": "live-extraction", "model": settings.chat_model,
            "cases": results, "passed": sum(case["passed"] for case in results), "total": len(results)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--deepeval", action="store_true")
    modes.add_argument("--live-extraction", action="store_true")
    args = parser.parse_args()
    result = evaluate_live() if args.live_extraction else evaluate(score_with_deepeval if args.deepeval else None)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return int(result["passed"] != result["total"])


if __name__ == "__main__":
    raise SystemExit(main())
