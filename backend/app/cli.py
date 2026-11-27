import argparse
from getpass import getpass

from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.config import ROOT, Settings, configuration
from .core.migrations import config_for
from .core.security import passwords
from .core.storage import LocalStorage, MinioStorage
from .db import make_engine
from .models import Application, ApplicationHistory, Candidate, Job, User

SYNTHETIC_JOBS = [
    ("亚马逊美国站运营", "亚马逊运营部", "维护美国站服装类目 Listing，推动转化和库存协同。", "Amazon US、Listing 优化、类目运营"),
    ("亚马逊广告投放专员", "增长营销部", "管理 Sponsored Products 广告，复盘周度转化和预算。", "ACOS、ROAS、广告结构、搜索词分析"),
    ("FBA 供应链计划专员", "供应链部", "跟踪服装 SKU 断货风险、海运时效与 FBA 补货。", "FBA、需求预测、库存周转"),
    ("跨境服装设计师", "商品企划部", "结合美国市场需求设计服装款式和尺码方案。", "服装制版、Tech Pack"),
    ("美国站客服专员", "客户运营部", "处理英文售后、尺码和退货问题。", "英文书面沟通、亚马逊售后"),
]
SYNTHETIC_CANDIDATES = [
    ("虚构候选人·林沐", "亚马逊美国站服装运营", "Listing 优化、变体管理、Amazon US"),
    ("虚构候选人·顾禾", "广告投放与增长", "Sponsored Products、ACOS、ROAS"),
    ("虚构候选人·周晴", "跨境库存与采购计划", "FBA、SKU 库存计划、海运跟踪"),
    ("虚构候选人·沈杉", "跨境服装产品设计", "Tech Pack、服装制版"),
    ("虚构候选人·何远", "英文电商客服", "美国市场、售后、邮件沟通"),
]


def seed(db, password):
    accounts = {}
    for username, name, role, sensitive in [
        ("admin", "系统管理员", "admin", False), ("hr", "招聘专员", "hr", True),
        ("interviewer", "面试官", "interviewer", False), ("hr_standard", "普通招聘专员", "hr", False),
    ]:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, name=name, role=role, password_hash=passwords.hash(password),
                        view_contacts=sensitive, reopen_applications=sensitive)
            db.add(user)
        accounts[username] = user
    db.flush()
    jobs = []
    for title, department, responsibilities, required_skills in SYNTHETIC_JOBS:
        job = db.scalar(select(Job).where(Job.title == title, Job.synthetic.is_(True)))
        if job is None:
            job = Job(title=title, department=department, headcount=2, responsibilities=responsibilities,
                      required_skills=required_skills, synthetic=True, owner_id=accounts["hr"].id)
            db.add(job)
        jobs.append(job)
    candidates = []
    for name, headline, skills in SYNTHETIC_CANDIDATES:
        candidate = db.scalar(select(Candidate).where(Candidate.name == name, Candidate.synthetic.is_(True)))
        if candidate is None:
            candidate = Candidate(name=name, headline=headline, skills=skills, synthetic=True)
            db.add(candidate)
        candidates.append(candidate)
    db.flush()
    for i, j in [(0, 0), (0, 1), (1, 1), (2, 2)]:
        if jobs[j].status != "open":
            continue
        existing = db.scalar(select(Application.id).where(
            Application.candidate_id == candidates[i].id, Application.job_id == jobs[j].id))
        if existing is None:
            application = Application(candidate_id=candidates[i].id, job_id=jobs[j].id,
                                      interviewer_id=accounts["interviewer"].id if (i, j) == (0, 0) else None)
            db.add(application)
            db.flush()
            db.add(ApplicationHistory(application_id=application.id, actor_id=accounts["hr"].id,
                                      status=application.status, reason="初始化模拟数据", application_version=1))
    db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["migrate", "seed", "init-storage", "create-admin"])
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    config = Settings.load()
    if config.profile == "local":
        (ROOT / ".runtime").mkdir(exist_ok=True)
    if args.action == "migrate":
        command.upgrade(config_for(), "head")
    elif args.action == "init-storage":
        if config.profile == "compose":
            MinioStorage(config).ensure_bucket()
        else:
            LocalStorage(config.storage_path)
    else:
        engine = make_engine(config.database_url)
        with Session(engine) as db:
            if args.action == "seed":
                password = configuration().get("TALENT_DEMO_PASSWORD", "")
                if len(password) < 12:
                    parser.error("Set TALENT_DEMO_PASSWORD to at least 12 characters.")
                seed(db, password)
            else:
                password = getpass("Admin password (12+ characters): ")
                username = args.username.strip().lower()
                if not 1 <= len(username) <= 80 or len(password) < 12:
                    parser.error("Invalid username or password length.")
                if db.scalar(select(User.id).where(User.username == username)):
                    parser.error("This user already exists.")
                db.add(User(username=username, name="系统管理员", role="admin", password_hash=passwords.hash(password)))
                db.commit()
        engine.dispose()


if __name__ == "__main__":
    main()
