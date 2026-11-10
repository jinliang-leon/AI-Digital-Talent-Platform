from scripts.生成演示数据 import build_dataset


def test_demo_labels_are_frozen_and_all_candidates_are_fictional():
    dataset = build_dataset()
    assert len(dataset["resumes"]) == 30
    assert len(dataset["jobs"]) == 5
    assert len(dataset["questions"]) == 15
    names = {r["name"] for r in dataset["resumes"]}
    assert len(names) == 30 and all(n.startswith("虚构人才") for n in names)
    assert all(len(j["relevant_names"]) == 3 for j in dataset["jobs"])
    assert sum(not q["answerable"] for q in dataset["questions"]) == 5
    assert any(r["facts"]["experience_months"] is None for r in dataset["resumes"])


def test_evaluation_uses_top_k_denominator_and_literal_field_comparison():
    from scripts.真实模型验收 import precision_at, recall_at, field_equal

    assert precision_at(["甲", "乙", "丙"], {"甲", "丁"}, 3) == 1 / 3
    assert recall_at(["甲", "乙", "丙", "丁", "戊"], {"甲", "乙", "丙", "丁", "戊", "己"}, 5) == 5 / 6
    assert field_equal(["Python", "SQL"], ["sql", "Python"])
    assert not field_equal(["接口开发"], ["接口与缓存开发"])
