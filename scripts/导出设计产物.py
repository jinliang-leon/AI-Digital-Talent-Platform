"""从当前代码导出接口、MySQL DDL 与数据字典，避免分册和实现脱节。"""

import json
from pathlib import Path
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import mysql
from app.main import create_app
from app.models import Base


def main():
    destination = Path(__file__).resolve().parents[1] / "文档/02_设计"
    destination.mkdir(parents=True, exist_ok=True)
    app = create_app()
    (destination / "接口定义.openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    statements = []
    sections = [
        "# 数据字典（实现快照）",
        "",
        "由 `python -m scripts.导出设计产物` 从 SQLAlchemy 模型导出；MySQL 8、utf8mb4、UTC、UUID。",
        "正式升级使用 Alembic；SQL 文件用于查阅和新库建表参考，不重复应用于已有表。",
        "",
        "Python 端默认值由应用写入，未标为数据库 DEFAULT。facts、requirements、快照采用 JSON；简历全文使用 LONGTEXT。",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=mysql.dialect())).strip())
        sections.extend(
            [f"## {table.name}", "", "| 字段 | 类型 | 可空 | 主键／外键 |", "| --- | --- | --- | --- |"]
        )
        for column in table.columns:
            constraints = []
            if column.primary_key:
                constraints.append("主键")
            constraints.extend("→ " + str(foreign.column) for foreign in column.foreign_keys)
            sections.append(
                f"| {column.name} | {column.type.compile(dialect=mysql.dialect())} | {'是' if column.nullable else '否'} | {'；'.join(constraints)} |"
            )
        sections.append("")
        for constraint in table.constraints:
            if constraint.__class__.__name__ == "UniqueConstraint":
                sections.append("唯一约束：" + ", ".join(c.name for c in constraint.columns) + "。")
        for index in sorted(table.indexes, key=lambda i: i.name):
            statements.append(str(CreateIndex(index).compile(dialect=mysql.dialect())).strip())
            sections.append("索引：" + index.name + " (" + ", ".join(c.name for c in index.columns) + ")。")
        sections.append("")
    (destination / "数据库建表.sql").write_text(
        "-- 当前模型快照；已有数据库使用 Alembic 迁移。\nSET NAMES utf8mb4;\n\n"
        + ";\n\n".join(statements)
        + ";\n",
        encoding="utf-8",
    )
    (destination / "数据字典.md").write_text("\n".join(sections), encoding="utf-8")
    print("已导出 OpenAPI、MySQL DDL 和数据字典；表数：" + str(len(Base.metadata.tables)))


if __name__ == "__main__":
    main()
