from fastapi import APIRouter, Depends, Request
from app.controllers.deps import db, permission
from app.schemas import SearchInput
from app.services.search import search

router = APIRouter(tags=["智能检索"])


@router.post("/search")
def search_talents(
    body: SearchInput, request: Request, session=Depends(db), actor=Depends(permission("search:read"))
):
    return search(session, body.question, body.filters, request.app.state.settings, request.app.state.redis)
