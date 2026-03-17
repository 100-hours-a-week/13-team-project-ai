# app/api/history.py
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/history", tags=["history"])


class HistoryResponse(BaseModel):
    messages: list[dict]
    next_cursor: int | None


@router.get("/{user_id}", response_model=HistoryResponse, summary="대화 기록 조회 (커서 페이징)")
async def get_history(
    user_id: str,
    limit: int   = Query(default=20, ge=1, le=100, description="한 번에 가져올 메시지 수"),
    before_id: int | None = Query(default=None, ge=1, description="이 id 이전 메시지 조회 (커서)"),
):
    """
    채팅 진입 시 이전 대화 기록을 커서 단위로 조회합니다.

    ### 사용법
    1. **첫 진입** — `before_id` 없이 호출 → 최신 메시지 N개 반환
    2. **더 보기** — 응답의 `next_cursor`를 `before_id`로 전달 → 이전 메시지 N개 반환
    3. **마지막 페이지** — `next_cursor`가 `null`이면 더 이상 이전 메시지 없음
    """
    from app.db.sqlite import load_history_cursor
    return await load_history_cursor(user_id, limit=limit, before_id=before_id)