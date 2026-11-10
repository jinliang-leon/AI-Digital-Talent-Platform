"""LangGraph 编排提取、原文核验及画像；MySQL 保存可恢复任务状态。"""

from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from app.errors import AppError
from app.services.extraction import validate_facts, profile_summary


class ResumeState(TypedDict, total=False):
    chunks: list[dict]
    raw: dict
    facts: dict
    evidence: list[dict]
    summary: str


def extract_with_graph(chunks: list[dict], model) -> ResumeState:
    if sum(len(c["text"]) for c in chunks) > 40000:
        raise AppError("MODEL_INPUT_TOO_LARGE", "简历文字超过当前模型处理上限，请拆分后上传")

    def extract(state):
        return {"raw": model.extract_resume(state["chunks"])}

    def verify(state):
        facts, evidence = validate_facts(state["raw"], state["chunks"])
        if not evidence:
            raise AppError("NO_GROUNDED_FACTS", "没有提取到可定位原文的事实，请人工检查简历")
        return {"facts": facts, "evidence": evidence}

    def summarize(state):
        return {"summary": profile_summary(state["facts"])}

    graph = StateGraph(ResumeState)
    graph.add_node("结构化提取", extract)
    graph.add_node("原文核验", verify)
    graph.add_node("人才画像", summarize)
    graph.add_edge(START, "结构化提取")
    graph.add_edge("结构化提取", "原文核验")
    graph.add_edge("原文核验", "人才画像")
    graph.add_edge("人才画像", END)
    return graph.compile().invoke({"chunks": chunks})
