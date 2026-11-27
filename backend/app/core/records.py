from fastapi import HTTPException
from sqlalchemy import update

from ..models import Audit


def record(db, user, action, resource_type, resource_id, summary=""):
    db.add(Audit(actor_id=user.id, action=action, resource_type=resource_type,
                 resource_id=resource_id, summary=summary))


def get_or_404(db, model, identifier):
    obj = db.get(model, identifier)
    if obj is None:
        raise HTTPException(404, "记录不存在或不可访问")
    return obj


def update_version(db, obj, expected, **changes):
    result = db.execute(update(type(obj)).where(type(obj).id == obj.id, type(obj).version == expected)
                        .values(**changes, version=expected + 1))
    if result.rowcount != 1:
        raise HTTPException(409, "记录已更新，请刷新后重试")
    db.refresh(obj)
