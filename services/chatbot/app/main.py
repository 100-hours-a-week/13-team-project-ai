# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from app.api.chat import router as chat_router
from app.api.history import router as history_router
from app.db.clients import init_clients, close_clients
from app.db.history_store import init_db
import app.db.clients as clients
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 수명 주기 관리"""
    # startup (SQLite 버전은 순서 상관 없음)
    await init_db()
    await init_clients()
    yield
    # shutdown
    await close_clients()


app = FastAPI(
    title="Restaurant RAG API",
    description="멀티스텝 + HyDE + 멀티턴 식당 추천 RAG 서버 (user_id 기반 SQLite 멀티턴)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # TODO: 운영 배포 전 도메인 제한
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app)

app.include_router(chat_router)
app.include_router(history_router)


@app.get("/health")
async def health():
    """
    서버 상태 확인

    Response:
        {
            "status": "ok",
            "clients": {
                "qdrant":    true,
                "db":        true,
                "embedding": true
            }
        }
    """
    return {
        "status": "ok",
        "clients": {
            "qdrant":    clients.qdrant    is not None,
            "db":        clients.db_engine is not None,
            "embedding": clients.embedding_model is not None,
        },
    }