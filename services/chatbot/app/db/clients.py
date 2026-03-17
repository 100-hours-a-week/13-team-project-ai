# app/db/clients.py
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sentence_transformers import SentenceTransformer
from app.core.config import settings

# ── 전역 클라이언트 선언 ──
qdrant:           AsyncQdrantClient    = None
db_engine                              = None
AsyncSessionLocal                      = None
embedding_model:  SentenceTransformer  = None


async def init_clients():
    """
    앱 시작 시 1회 호출
    모든 클라이언트/모델을 전역 싱글턴으로 초기화
    """
    global qdrant, db_engine, AsyncSessionLocal, embedding_model
    from app.common.reranker import load_reranker

    # Qdrant
    qdrant = AsyncQdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY if settings.QDRANT_API_KEY else None
    )
    print("✅ Qdrant 클라이언트 초기화")

    # DB
    db_engine = create_async_engine(settings.DATABASE_URL)
    AsyncSessionLocal = sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    db_type = settings.DATABASE_URL.split(':')[0]
    print(f"✅ {db_type} 클라이언트 초기화")

    # KR-SBERT 임베딩 모델 (CPU, 768차원)
    embedding_model = SentenceTransformer(
        settings.EMBEDDING_MODEL_NAME,
        device=settings.EMBEDDING_DEVICE
    )
    print(f"✅ KR-SBERT 로딩 완료 (차원: {settings.EMBEDDING_DIM})")

    # Reranker (BAAI/bge-reranker-v2-m3) — 없으면 graceful skip
    load_reranker()


async def close_clients():
    """앱 종료 시 1회 호출 — 연결 정리"""
    global qdrant, db_engine

    if qdrant:
        await qdrant.close()
        print("Qdrant 연결 종료")

    if db_engine:
        await db_engine.dispose()
        print("PostgreSQL 연결 종료")