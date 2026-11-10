"""Neo4j 技能关系，所有读写均限定项目与业务 ID。"""

from neo4j import GraphDatabase
from app.errors import AppError


class GraphStore:
    def __init__(self, settings):
        self.project = settings.project_namespace
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
            connection_timeout=5,
        )

    def ensure(self):
        try:
            with self.driver.session() as session:
                for label, key in [("Talent", "talent_id"), ("Job", "job_id"), ("Skill", "normalized_name")]:
                    name = f"{self.project}_{label.lower()}_unique"
                    session.run(
                        f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) "
                        f"REQUIRE (n.project_id,n.{key}) IS UNIQUE"
                    ).consume()
        except Exception as exc:
            raise AppError("GRAPH_UNAVAILABLE", "技能图谱暂不可用", 503) from exc

    def sync(self, kind, entity_id, revision, skills):
        if kind not in {"talent", "job"}:
            raise ValueError("invalid graph entity")
        label, key, relation = (
            ("Talent", "talent_id", "HAS_SKILL") if kind == "talent" else ("Job", "job_id", "REQUIRES_SKILL")
        )
        self.ensure()

        def operation(tx):
            row = tx.run(
                f"MERGE (n:{label} {{project_id:$p,{key}:$id}}) "
                "ON CREATE SET n.revision=-1 WITH n WHERE n.revision <= $rev "
                "SET n.revision=$rev RETURN n.revision AS revision",
                p=self.project,
                id=entity_id,
                rev=revision,
            ).single()
            if row is None:
                return
            tx.run(
                f"MATCH (n:{label} {{project_id:$p,{key}:$id}})-[r:{relation}]->() DELETE r",
                p=self.project,
                id=entity_id,
            ).consume()
            tx.run(
                f"MATCH (n:{label} {{project_id:$p,{key}:$id}}) UNWIND $skills AS item "
                "MERGE (s:Skill {project_id:$p,normalized_name:item.name}) "
                f"MERGE (n)-[r:{relation}]->(s) SET r.revision=$rev,r.chunk_id=item.chunk_id",
                p=self.project,
                id=entity_id,
                rev=revision,
                skills=skills,
            ).consume()

        try:
            with self.driver.session() as session:
                session.execute_write(operation)
        except Exception as exc:
            raise AppError("GRAPH_UNAVAILABLE", "技能图谱同步失败", 503) from exc

    def skills(self, kind, entity_id, revision):
        label, key, rel = (
            ("Talent", "talent_id", "HAS_SKILL") if kind == "talent" else ("Job", "job_id", "REQUIRES_SKILL")
        )
        try:
            with self.driver.session() as session:
                row = session.run(
                    f"MATCH (n:{label} {{project_id:$p,{key}:$id,revision:$rev}})"
                    f" OPTIONAL MATCH (n)-[r:{rel}]->(s:Skill {{project_id:$p}}) RETURN n.revision AS revision,collect(s.normalized_name) AS names",
                    p=self.project,
                    id=entity_id,
                    rev=revision,
                ).single()
                if row is None:
                    raise AppError(
                        "GRAPH_INDEX_NOT_READY", "当前版本技能图索引缺失，请重建项目索引后再匹配", 409
                    )
                return row["names"]
        except AppError:
            raise
        except Exception as exc:
            raise AppError("GRAPH_UNAVAILABLE", "技能图谱查询失败", 503) from exc

    def close(self):
        self.driver.close()
