import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import settings
from app.core.model_loader import load_model
from app.core.vector_store import get_vector_client
from app.api.v2.recommend_router_v2 import router as rec_router   

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) LightGBM 모델 로드
    logger.info("모델 로드 중...")
    try:
        load_model()
        logger.info("모델 로드 완료")
    except FileNotFoundError as e:
        logger.error(f"모델 파일 없음 — 서버 시작 불가: {e}")
        raise

    # 2) Qdrant 연결 확인
    logger.info(
        f"Qdrant 연결 확인 중... "
        f"({'원격: ' + settings.QDRANT_URL if settings.QDRANT_URL else '인메모리'})"
    )
    try:
        client = get_vector_client()
        collections = [c.name for c in client.get_collections().collections]
        if settings.QDRANT_COLLECTION in collections:
            info = client.get_collection(settings.QDRANT_COLLECTION)
            logger.info(
                f"Qdrant 연결 완료 | "
                f"컬렉션={settings.QDRANT_COLLECTION} | "
                f"벡터 수={info.points_count}"
            )
        else:
            logger.warning(
                f"Qdrant 컬렉션 '{settings.QDRANT_COLLECTION}' 없음 — "
                f"CF 피처가 0으로 대체됩니다."
            )
    except Exception as e:
        logger.warning(f"Qdrant 연결 실패 — CF 피처 비활성화: {e}")

    yield
    logger.info("서버 종료")


# ── FastAPI 앱 ────────────────────────────────────────────────────
app = FastAPI(
    title="모임 식당 추천 API",
    version="2.0.0",
    description="TwoTower + LightGBM LambdaRank 기반 모임 식당 추천 서비스",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(app)


# ── 전역 예외 핸들러 ─────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"처리되지 않은 예외 | path={request.url.path}")
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# ── 라우터 ───────────────────────────────────────────────────────
app.include_router(rec_router, prefix="/api/v2", tags=["recommendations"])


# ── 헬스체크 ─────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health():
    """서버 상태 및 Qdrant 연결 확인."""
    qdrant_ok = False
    try:
        client = get_vector_client()
        client.get_collections()
        qdrant_ok = True
    except Exception:
        pass

    return {
        "status": "ok",
        "qdrant": "connected" if qdrant_ok else "unavailable (CF fallback active)",
    }