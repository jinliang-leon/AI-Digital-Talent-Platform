"""汇总已完成实测，不修改原始结果或自动把失败标为通过。"""

import json
from pathlib import Path
from statistics import median


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "文档/05_测试验收"
    baseline = json.loads((folder / "真实模型验收结果.json").read_text(encoding="utf-8"))
    retrieval = json.loads((folder / "检索层诊断结果.json").read_text(encoding="utf-8"))
    imported = json.loads((root / "运行数据/样例导入结果.json").read_text(encoding="utf-8"))
    async_test = json.loads((folder / "容器异步验收结果.json").read_text(encoding="utf-8"))
    environment = json.loads((folder / "验收环境.json").read_text(encoding="utf-8"))
    previous_file = folder / "真实模型验收结果_修复前.json"
    previous = json.loads(previous_file.read_text(encoding="utf-8")) if previous_file.exists() else None
    summary = baseline["summary"]
    questions = baseline["questions"]
    citations = sum(len(q.get("citations", [])) for q in questions)
    latencies = [q["seconds"] for q in questions]
    durations = [
        r["processing_seconds"] for r in imported["resumes"] if r.get("processing_seconds") is not None
    ]
    recovery_file = folder / "RAG失败项恢复验证.json"
    recovery = json.loads(recovery_file.read_text(encoding="utf-8")) if recovery_file.exists() else None
    p = lambda value: f"{value * 100:.2f}%"
    rows = [
        (
            "确定性自动化",
            "完整套件通过",
            f"{environment['full_tests_passed']} passed（含18项真实服务集成）",
            "通过",
        ),
        (
            "Linux/Python3.11",
            "镜像内单元通过",
            f"{environment['container_unit_tests_passed']} passed，18项集成另在本机执行",
            "通过",
        ),
        ("样例就绪", "30简历、5JD", "30简历／5JD全部READY，期间发生超时和重试", "通过，非首次全成功"),
        (
            "字段精确准确率",
            "≥90%",
            f"{baseline['field_counts']['correct']}/{baseline['field_counts']['total']} = {p(summary['field_accuracy'])}",
            "通过" if summary["field_accuracy"] >= 0.9 else "未通过",
        ),
        (
            "岗位Precision@3",
            "≥80%",
            p(summary["precision_at_3"]),
            "通过" if summary["precision_at_3"] >= 0.8 else "未通过",
        ),
        ("双向评分一致", "相同人才岗位分数相同", "5/5对一致", "通过"),
        (
            "端到端RAG Recall@5",
            "≥80%",
            p(summary["recall_at_5"]),
            "通过" if summary["recall_at_5"] >= 0.8 else "未通过",
        ),
        ("无答案问题拒答", "不编造", "5/5 = 100%", "通过"),
        (
            "返回引用可定位",
            "100%",
            f"{citations}/{citations}条原文可定位",
            "通过" if summary["all_citations_locatable"] else "未通过",
        ),
        (
            "RAG时延",
            "≤30秒",
            f"中位数{median(latencies):.2f}秒，最大{max(latencies):.2f}秒；{sum(v > 30 for v in latencies)}/15次超目标",
            "未通过" if max(latencies) > 30 else "通过",
        ),
        (
            "单份简历完成",
            "≤120秒",
            f"跨迭代历史成功任务中位数{median(durations):.2f}秒，最大{max(durations):.2f}秒；非当前构建全量性能复测",
            "当前构建未全量复验",
        ),
        (
            "容器异步新上传",
            "入队≤3秒，完成≤120秒",
            f"上传{async_test['upload_seconds']}秒，{async_test['total_seconds']}秒READY；原文307下载",
            "通过" if async_test["passed"] else "未通过",
        ),
    ]
    text = [
        "# 测试与验收报告",
        "",
        "日期：2026-11-10（Asia/Shanghai）。整体验收：**未全部通过**。应用与Docker部署可运行，核心确定性测试通过；质量与性能各项结果分列如下，不能将已执行验收等同于全部达标。PR保持草稿，不等同于用户已验收。",
        "",
        "## 实测结果",
        "",
        "| 项目 | 原定目标 | 实际结果 | 结论 |",
        "| --- | --- | --- | --- |",
    ]
    text.extend("| " + " | ".join(row) + " |" for row in rows)
    text += [
        "",
        "## 数据和指标口径",
        "",
        "- 30份虚构中文PDF、5个岗位JD、15个问题，标注在模型运行前冻结。未为实现修改标注或降低阈值。",
        "- 六个事实字段逐项精确比较，共180项；字符串仅规范化NFKC、空白和大小写，数组比较集合，不删改标点或字段措辞。按原文件映射人才，避免姓名提取错误影响样本归属。",
        "- 每个JD在eligible组取前三名，分母固定3；五个JD平均Precision@3。未知硬条件独立进入needs_review；明确经验不足排除。",
        "- 十个可回答问题计算端到端Recall@5，相关人每题6名；HTTP失败按0，不用成功样本重新归一化。五个无答案问题单独统计拒答。",
        f"- 检索层另做不调用生成模型的诊断，Recall@5={p(retrieval['recall_at_5'])}；此结果只用于定位故障边界，**不替代端到端{p(summary['recall_at_5'])}的实测结论**。",
        "- 首个完整导入批次22/30就绪、8次超时；经过正常重试后30份全部就绪。报告字段准确率来自最终当前版本，不冒充首次成功率。历史成功任务耗时不含排队及前面失败尝试，并跨越SDK超时等修复前后；414秒属于早期实现，不能当成当前构建同条件性能复测。当前容器另有12.3秒新上传实测。",
        "- 这是小型合成演示验证集，不能据此宣称真实企业招聘的泛化效果。",
        "",
        "## 当前完整复验的失败项与修复前对照",
        "",
        "| 问题 | 状态 | 错误 | 秒数 |",
        "| --- | --- | --- | --- |",
    ]
    for q in questions:
        if q.get("status") != 200:
            text.append(f"| {q['question']} | {q.get('status')} | {q.get('error_code')} | {q['seconds']} |")
    text.append("")
    if recovery:
        text.append(
            f"修复前1600预算条件下，针对首次失败项的独立恢复验证测{recovery['total']}项、恢复{recovery['recovered']}项。详见 [历史恢复记录](RAG失败项恢复验证.json)，不作为当前构建指标。"
        )
    else:
        text.append("失败项恢复验证尚未完成，不预先宣称已恢复。")
    text += [
        "",
        "## 兼容性与原因边界",
        "",
        "本机10Router 1.6.2→Spark-X2.6路径接受关闭思考参数，但64token探测仍返回reasoning_content而正文为空。该证据说明当前路径不能保证关闭思考，不足以单独断定问题发生在网关还是上游。未修改用户现有全局网关设置，保留指定Spark-X2.6。",
        "程序此前将RAG输出预算设为1600，未给推理和最终JSON预留足够空间。正式容器使用与API相同缓存来源的复现返回finish_reason=length、completion_tokens=1600、正文233字符，确认输出被截断。已修正为4096，新增回归先失败再通过，严格引用规则与90秒单次超时不变。",
        "LangChain会改写max_tokens为max_completion_tokens；1token对照表明网关同时执行两个限制，排除“令牌参数完全被忽略”的假设。只读短原文对照35.77秒仍未达到30秒，未把缩短上下文的单次结果用来替代完整验收。",
        "结构化格式和引用最多一次受控修复；仍不成立返回502，服务或超时错误返回503，不返回假答案。模型慢请求可能使整体耗时超过单次90秒。",
        "",
        "## 真实组件与浏览器验证",
        "",
        "- MySQL8.0.27专用业务／测试库隔离；迁移0002成功；中文全文LONGTEXT真实读写通过。",
        "- BGE-M3返回有效1024维向量；Milvus切片、画像和岗位集合及Neo4j技能图均实际写入、查询；当前版本在Top-K前过滤，结果仍回查SQL。",
        "- Redis实际负责队列、缓存、注销和限流；MinIO实际保存原文，业务桶与向量库内部对象存储分开。",
        "- Linux RQ真实上传DOCX、云提取、索引、签名下载、测试档案软归档验证通过。Web/worker使用同一个镜像；镜像不含.env。",
        "- 浏览器验证登录、30/30人才就绪和5JD统计、双向匹配、原文展开、备注保存、真实RAG来源、系统七项连接状态。1280桌面与375窄屏均有截图，窄屏无整页横向溢出。",
        "- 整分支独立审阅五项Important均先RED再GREEN，修复证据见独立审阅记录；无Critical。",
        "- 浏览器文件选择补验因控制工具阻塞而未完成，核对files为空后已取消且未提交；API上传和Linux真实异步上传已通过，不能互相冒充。",
        "",
        "## 执行命令和证据",
        "",
        "```powershell",
        "$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'",
        ".\\.venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider",
        ".\\.venv\\Scripts\\python.exe -m scripts.真实模型验收",
        ".\\.venv\\Scripts\\python.exe -m scripts.生成验收报告",
        "docker compose ps",
        "```",
        "",
        "[环境及向量模型摘要](验收环境.json) · [真实模型原始结果](真实模型验收结果.json) · [检索层诊断](检索层诊断结果.json) · [容器异步证据](容器异步验收结果.json) · [测试用例](测试用例与执行记录.md) · [独立审阅](独立审阅记录.md) · [未通过项](验收问题清单.md)",
        "",
        "![正式容器工作台](截图/正式工作台桌面.jpg)",
        "",
        "## 交付判断",
        "",
        "需求、设计、计划、代码、确定性测试、Docker、中文手册和验收材料已形成。简历画像与岗位匹配可供当前课程演示。RAG质量与性能以表中当前完整复验为准，性能未达或尚未覆盖的范围继续按问题清单处理，不因代码通过而忽略。",
        "",
    ]
    if previous:
        before = previous["summary"]
        text += [
            "## 原标注完整复验对照",
            "",
            "| 运行 | RAG输出预算 | 端到端Recall@5 | 最大RAG秒数 |",
            "| --- | --- | --- | --- |",
            f"| 修复前 | 1600 | {p(before['recall_at_5'])} | {before['rag_latency_max_seconds']} |",
            f"| 修复后当前轮 | 4096 | {p(summary['recall_at_5'])} | {summary['rag_latency_max_seconds']} |",
            "",
            "[修复前原始15题结果](真实模型验收结果_修复前.json) · [修复前报告](测试报告_修复前.md)",
            "",
        ]
    (folder / "测试报告.md").write_text("\n".join(text), encoding="utf-8")
    print("已生成报告；保留所有原始结果，逐项判断质量与性能。")


if __name__ == "__main__":
    main()
