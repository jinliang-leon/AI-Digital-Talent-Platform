"""生成可重复、无真实个人信息的演示简历及冻结验收标注。"""

from functools import partial
from html import escape
import json
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable


def build_dataset():
    groups = [
        (
            "Python后端",
            ["Python", "FastAPI", "MySQL", "Redis", "Docker"],
            "招聘API服务",
            "负责后端接口开发、数据库查询与缓存优化。",
        ),
        (
            "数据分析",
            ["Python", "SQL", "Pandas", "Power BI", "Excel"],
            "用户行为分析报表",
            "负责用户行为分析、数据清洗与业务报表。",
        ),
        (
            "前端开发",
            ["JavaScript", "TypeScript", "Vue", "HTML", "CSS"],
            "电商平台前端",
            "负责电商商品列表、购物车及用户界面开发。",
        ),
        (
            "运维开发",
            ["Linux", "Docker", "Kubernetes", "Prometheus", "Shell"],
            "容器集群监控",
            "负责容器集群部署、监控告警和运维脚本。",
        ),
        (
            "Java后端",
            ["Java", "Spring Boot", "MySQL", "Redis", "Maven"],
            "订单服务系统",
            "负责订单服务、接口联调和数据库性能优化。",
        ),
    ]
    resumes = []
    jobs = []
    questions = []
    for group_no, (role, skills, project, description) in enumerate(groups):
        names = []
        positive = []
        for offset, months in enumerate([12, 24, 36, 48, None, 60]):
            number = group_no * 6 + offset + 1
            name = f"虚构人才{number:02d}"
            names.append(name)
            candidate_skills = skills[:] if offset else [skills[0], skills[2], skills[-1]]
            facts = {
                "name": name,
                "skills": candidate_skills,
                "experience_months": months,
                "projects": [project],
                "experiences": [description],
                "education": ["计算机相关专业（虚构）"],
            }
            filename = f"{number:02d}_{name}_{role}.pdf"
            resumes.append({"name": name, "role": role, "filename": filename, "facts": facts})
            if months is not None and months >= 36 and offset:
                positive.append(name)
        title = role + "工程师"
        raw = (
            f"岗位名称：{title}。技能要求："
            + "、".join(skills)
            + f"。相关经验要求：36个月。项目要求：{project}。工作内容：{description}"
        )
        jobs.append(
            {
                "title": title,
                "raw_jd": raw,
                "hard_conditions": {
                    "required_skills": skills[:2],
                    "min_experience_months": 36,
                    "required_projects": [],
                },
                "hard_confirmed": True,
                "relevant_names": positive,
            }
        )
        questions.append({"question": f"谁做过{project}项目？", "answerable": True, "relevant_names": names})
        semantic = {
            "Python后端": "谁有服务端接口开发和数据库缓存优化经历？",
            "数据分析": "谁能从用户数据中提炼结论并制作可视化报表？",
            "前端开发": "谁做过线上商店的页面和购物车交互？",
            "运维开发": "谁有容器部署、系统监控和自动化运维经验？",
            "Java后端": "谁参与过订单后端接口联调与性能优化？",
        }[role]
        questions.append({"question": semantic, "answerable": True, "relevant_names": names})
    for question in [
        "谁有量子纠错芯片流片经验？",
        "谁有深空激光通信硬件设计经历？",
        "谁做过自动驾驶激光雷达芯片量产？",
        "谁参与过海底光缆铺设项目？",
        "谁有心脏起搏器临床试验经验？",
    ]:
        questions.append({"question": question, "answerable": False, "relevant_names": []})
    return {
        "schema_version": 1,
        "description": "全部为虚构演示数据，不对应真实个人。标注在模型验收前冻结。",
        "resumes": resumes,
        "jobs": jobs,
        "questions": questions,
    }


def _pdf(path, record):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    body = ParagraphStyle(
        "正文",
        fontName="STSong-Light",
        fontSize=11,
        leading=21,
        textColor=colors.HexColor("#263c50"),
        wordWrap="CJK",
    )
    heading = ParagraphStyle(
        "章节",
        parent=body,
        fontSize=13,
        leading=23,
        textColor=colors.HexColor("#245fd4"),
        spaceBefore=17,
        spaceAfter=7,
    )
    title = ParagraphStyle("姓名", parent=body, fontSize=25, leading=35, spaceAfter=8)
    muted = ParagraphStyle("说明", parent=body, fontSize=9, leading=16, textColor=colors.HexColor("#708496"))
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=24 * mm,
        leftMargin=24 * mm,
        topMargin=29 * mm,
        bottomMargin=24 * mm,
    )
    facts = record["facts"]
    story = [
        Paragraph(escape(record["name"]), title),
        Paragraph(escape(record["role"]) + " - 虚构演示简历", body),
        Spacer(1, 8 * mm),
        Paragraph(
            "本资料仅用于AI数字化人才平台的功能测试与课程演示，全部信息均为虚构，不对应真实个人。", muted
        ),
        Spacer(1, 5 * mm),
        HRFlowable(width="100%", color=colors.HexColor("#dce5ee")),
        Paragraph("基本信息与技能", heading),
        Paragraph("姓名：" + facts["name"], body),
        Paragraph("技能：" + "、".join(facts["skills"]), body),
        Paragraph(
            "相关经验："
            + ("未提供" if facts["experience_months"] is None else str(facts["experience_months"]) + "个月"),
            body,
        ),
        Paragraph("项目经历", heading),
        Paragraph("项目：" + escape(facts["projects"][0]), body),
        Paragraph("项目说明：" + escape(facts["experiences"][0]), body),
        Paragraph("工作经历", heading),
        Paragraph("工作经历：" + escape(facts["experiences"][0]), body),
        Paragraph("教育经历", heading),
        Paragraph("教育经历：" + escape(facts["education"][0]), body),
    ]

    def header(pdf, document):
        pdf.setTitle(record["name"] + " - 虚构演示简历")
        pdf.setAuthor("AI数字化人才平台")
        pdf.setFont("STSong-Light", 9)
        pdf.setFillColor(colors.HexColor("#526d83"))
        pdf.drawString(24 * mm, 277 * mm, "AI数字化人才平台 / 人才档案")
        pdf.drawRightString(186 * mm, 277 * mm, "演示样本 " + record["filename"][:2])
        pdf.setStrokeColor(colors.HexColor("#e1e8ef"))
        pdf.line(24 * mm, 272 * mm, 186 * mm, 272 * mm)
        pdf.setFont("STSong-Light", 8)
        pdf.setFillColor(colors.HexColor("#8093a2"))
        pdf.drawString(24 * mm, 15 * mm, "虚构信息 - 仅用于测试与课程演示")
        pdf.drawRightString(186 * mm, 15 * mm, f"第 {document.page} 页")

    doc.build(story, onFirstPage=header, onLaterPages=header, canvasmaker=partial(canvas.Canvas, invariant=1))


def generate(destination: Path):
    dataset = build_dataset()
    folder = destination / "简历"
    folder.mkdir(parents=True, exist_ok=True)
    for record in dataset["resumes"]:
        path = folder / record["filename"]
        _pdf(path, record)
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if record["name"] not in text or not all(skill in text for skill in record["facts"]["skills"]):
            raise RuntimeError("生成的简历回读不完整：" + record["filename"])
    (destination / "验收标注.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "岗位JD.json").write_text(
        json.dumps(dataset["jobs"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "样例说明.md").write_text(
        "# 虚构演示样例\n\n30份中文PDF简历、5个岗位JD、15个检索问题。全部为虚构资料。\n\n简历包含明确经验不足与经验未提供两类情形，用于分别验证排除和需复核分组。\n标注在真实模型验收前冻结；生成工具不依据模型结果调整标注。\n",
        encoding="utf-8",
    )
    print("已生成并回读：30份简历、5个岗位JD、15个冻结问题。")


if __name__ == "__main__":
    generate(Path(__file__).resolve().parents[1] / "样例数据")
