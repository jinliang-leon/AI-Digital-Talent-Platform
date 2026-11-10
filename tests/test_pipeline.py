from app.services.pipeline import extract_with_graph


class EvidenceModel:
    def extract_resume(self, chunks):
        return {
            "name": "李四",
            "skills": ["Python"],
            "experience_months": None,
            "evidence": [
                {"field": "name", "value": "李四", "quote": "姓名：李四", "chunk_id": "c1"},
                {"field": "skills", "value": "Python", "quote": "技能：Python", "chunk_id": "c1"},
            ],
        }


def test_graph_keeps_only_grounded_facts_and_builds_summary():
    result = extract_with_graph([{"id": "c1", "text": "姓名：李四。技能：Python。"}], EvidenceModel())
    assert result["facts"]["name"] == "李四"
    assert result["facts"]["skills"] == ["Python"]
    assert "Python" in result["summary"]
    assert len(result["evidence"]) == 2
