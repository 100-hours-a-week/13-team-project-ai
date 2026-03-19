# app/api/chat.py
import asyncio
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from app.core.classifier import classify_query
from app.pipelines.multistep import multistep_pipeline
from app.pipelines.hyde import hyde_pipeline
from app.pipelines.multiturn import multiturn_pipeline
from app.db.history_store import load_history, save_turn
from app.pipelines.graph import app_graph
from app.core.config import settings

import logging
import time
from langsmith import trace as ls_trace

router = APIRouter()
logger = logging.getLogger("chatbot")

# 동시 처리 수 제한을 위한 세마포어
chat_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)


# ──────────────────────────────────────────
# 스키마
# ──────────────────────────────────────────

class ChatRequest(BaseModel):
    user_id: str
    message: str

    @field_validator("user_id")
    @classmethod
    def user_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("user_id는 빈 문자열일 수 없습니다.")
        return v.strip()

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("message는 빈 문자열일 수 없습니다.")
        return v.strip()


class ChatResponse(BaseModel):
    answer:   str
    pipeline: str
    user_id:  str


# ──────────────────────────────────────────
# 파이프라인 실행 (LangSmith trace context 안에서 실행)
# ──────────────────────────────────────────

from app.pipelines.graph import app_graph

async def _run_pipeline(pipeline_type: str, message: str, history: list, current_time: str, user_id: str) -> str:
    """LangGraph 워크플로우를 통한 파이프라인 실행"""
    async with chat_semaphore:
        async with asyncio.timeout(settings.AGENT_TIMEOUT_SEC):
            # 그래프 초기 상태 설정
            initial_state = {
                "user_id": user_id,
                "message": message,
                "history": history,
                "current_time": current_time,
                "pipeline": pipeline_type, # 분류 정보 초기값 (노드에서 재확인 가능)
                "answer": None
            }
            
            # 그래프 실행
            result = await app_graph.ainvoke(initial_state)
            return result.get("answer") or "응답을 생성하지 못했어요."


# ──────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────

@router.post("/RAGchat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    통합 채팅 엔드포인트 (동시 요청 처리 및 LangSmith 트레이싱)
    """
    start_time = time.time()
    current_time = datetime.now().isoformat()

    logger.info(f"[Request] user_id={req.user_id}, message='{req.message[:50]}...'")

    # ── 대화 기록 로드 / 파이프라인 분류 ──
    try:
        history = await load_history(req.user_id)
        pipeline_type = classify_query(req.message, history)
    except Exception as e:
        logger.error(f"[History/Classify Error] user_id={req.user_id}: {e}")
        pipeline_type = "multistep"
        history = []

    # ── 파이프라인 실행 (LangSmith trace context manager 사용) ──
    answer = ""
    try:
        with ls_trace(
            name="Main RAG Chat",
            run_type="chain",
            project_name=settings.LANGCHAIN_PROJECT,
            metadata={
                "user_id": req.user_id,
                "pipeline_type": pipeline_type,
                "message": req.message[:200],
            },
            tags=[f"pipeline:{pipeline_type}"],
        ):
            answer = await _run_pipeline(pipeline_type, req.message, history, current_time, req.user_id)

    except asyncio.TimeoutError:
        answer = "응답 시간이 초과됐어요. 다시 시도해주세요."
        logger.error(f"[Timeout] user_id={req.user_id}, pipeline={pipeline_type}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        answer = "일시적인 오류가 발생했어요. 다시 시도해주세요."
        logger.error(f"[Pipeline Error] user_id={req.user_id}, pipeline={pipeline_type}, error={e}")

    # ── 대화 기록 저장 ──
    try:
        await save_turn(req.user_id, req.message, answer)
    except Exception as e:
        logger.warning(f"[History Save Error] user_id={req.user_id}: {e}")

    duration = time.time() - start_time
    logger.info(f"[Response] user_id={req.user_id}, pipeline={pipeline_type}, duration={duration:.2f}s")

    return JSONResponse({
        "answer":   answer,
        "pipeline": pipeline_type,
        "user_id":  req.user_id,
    })


@router.delete("/RAGchat/{user_id}")
async def clear_history(user_id: str):
    """
    대화 기록 초기화

    대화를 새로 시작하거나 세션을 리셋할 때 사용
    이후 요청은 history=[] 가 되어 멀티턴 대신 multistep/hyde 로 라우팅됨
    """
    from app.db.sqlite import delete_history
    await delete_history(user_id)
    return {"message": f"{user_id} 대화 기록이 초기화됐어요."}