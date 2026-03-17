# app/common/reranker.py
"""
CrossEncoder reranker — BAAI/bge-reranker-v2-m3

역할:
    Hybrid Search 결과 (top-N place_id 리스트) 를
    질문-문서 교차 인코딩으로 재정렬하여 top-K 만 반환

위치:
    retriever (hybrid search) → reranker → get_details()

설치 (실서버):
    pip install sentence-transformers

모델 다운로드 (최초 1회, ~1.1GB):
    python3 -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ──────────────────────────────────────────
# 모델 싱글턴
# ──────────────────────────────────────────

_reranker = None   # CrossEncoder 인스턴스 (앱 시작 시 1회 로딩)


def load_reranker(model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
    """
    앱 시작 시 clients.init_clients() 에서 호출
    이미 로딩된 경우 재로딩 스킵
    """
    global _reranker
    if _reranker is not None:
        return

    try:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(model_name, max_length=512)
        print(f"✅ Reranker 로딩 완료 ({model_name})")
    except Exception as e:
        print(f"⚠️  Reranker 로딩 실패 (reranker 비활성화): {e}")
        _reranker = None


def is_available() -> bool:
    return _reranker is not None


# ──────────────────────────────────────────
# Rerank 핵심 로직
# ──────────────────────────────────────────

async def rerank(
    query: str,
    candidates: list[dict],
    *,
    text_key: str = "text",
    top_k: int = 5,
) -> list[dict]:
    """
    질문-문서 교차 인코딩으로 후보 재정렬

    Args:
        query      : 사용자 원본 질문
        candidates : [{"place_id": int, "text": str, ...}, ...]
                     Qdrant ScoredPoint.payload 리스트
        text_key   : 점수 계산에 사용할 텍스트 필드명
        top_k      : 반환할 상위 개수

    Returns:
        score 기준 내림차순 정렬된 payload 리스트 (최대 top_k개)
        reranker 비활성화 시 원본 순서 그대로 반환 (입력 상위 top_k)

    참고:
        CrossEncoder.predict() 는 CPU bound — 
        asyncio.get_event_loop().run_in_executor 로 비동기 처리
    """
    if not candidates:
        return []

    if not is_available():
        # reranker 없으면 hybrid search 순서 그대로 사용
        return candidates[:top_k]

    pairs = [(query, c.get(text_key, "")) for c in candidates]

    loop   = asyncio.get_event_loop()
    scores = await loop.run_in_executor(
        None,                        # default ThreadPoolExecutor
        _reranker.predict,
        pairs,
    )

    ranked = sorted(
        zip(scores, candidates),
        key=lambda x: x[0],
        reverse=True,
    )

    return [c for _, c in ranked[:top_k]]


# ──────────────────────────────────────────
# place_id 기준 rerank (파이프라인 편의 함수)
# ──────────────────────────────────────────

async def rerank_place_ids(
    query: str,
    scored_points: list,     # Qdrant ScoredPoint 리스트
    *,
    text_key: str = "text",
    top_k: int = 5,
) -> list[int]:
    """
    Qdrant ScoredPoint 리스트를 재정렬하고 place_id 리스트 반환

    멀티스텝 / HyDE 파이프라인에서 직접 사용:

        # multistep
        place_ids = await rerank_place_ids(query, menu_scored_points)

        # HyDE
        place_ids = await rerank_place_ids(query, review_results)

    reranker 비활성화 시 원본 순서 유지하며 중복 제거 후 top_k 반환
    """
    if not scored_points:
        return []

    payloads = [p.payload for p in scored_points]

    if not is_available():
        seen, ids = set(), []
        for p in payloads:
            pid = p.get("place_id")
            if pid and pid not in seen:
                seen.add(pid)
                ids.append(pid)
        return ids[:top_k]

    reranked = await rerank(query, payloads, text_key=text_key, top_k=top_k * 3)

    # place_id 중복 제거 (같은 식당 여러 doc 가능)
    seen, ids = set(), []
    for p in reranked:
        pid = p.get("place_id")
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
        if len(ids) >= top_k:
            break

    return ids