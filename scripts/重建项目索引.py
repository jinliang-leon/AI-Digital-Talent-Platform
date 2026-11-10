"""只重建当前项目的READY索引，不调用云模型，不修改事实。运行前暂停本项目worker。"""

from sqlalchemy import select
from app.config import Settings
from app.db import make_session_factory
from app.models import Talent, Job
from app.services.indexing import sync_entity


def main():
    settings = Settings()
    factory, engine = make_session_factory(settings)
    done = 0
    try:
        for kind, model in [("talent", Talent), ("job", Job)]:
            with factory() as session:
                rows = list(
                    session.execute(select(model.id, model.revision).where(model.index_status == "READY"))
                )
            for entity_id, revision in rows:
                with factory() as session:
                    entity = session.get(model, entity_id)
                    if kind == "talent" and entity.deleted_at:
                        continue
                    if kind == "job" and entity.status != "ACTIVE":
                        continue
                    sync_entity(session, kind, entity_id, revision, settings)
                done += 1
                print("已重建 " + kind + " " + entity_id, flush=True)
        print("当前版本索引重建完成：" + str(done) + " 项。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
