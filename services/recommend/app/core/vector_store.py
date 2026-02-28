"""
app/core/vector_store.py — 벡터DB 클라이언트 싱글턴.

컬렉션: meetup_requests_v2 (18차원)
벡터 구성:
  [0~8]  likes(9)   L2 정규화
  [9~17] dislikes(9) L2 정규화
  → 전체 18차원 L2 정규화

페이로드:
  request_id        : str
  headcount         : int
  restaurant_pairs  : dict[str, int]  {"rid": label}
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[object] = None


def get_vector_client():
    global _client
    if _client is None:
        _client = _init_client()
    return _client


def _init_client():
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        raise ImportError("qdrant-client 가 설치되지 않았습니다.")

    if settings.QDRANT_URL:
        client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            timeout=settings.QDRANT_TIMEOUT,
        )
        logger.info(f"Qdrant 원격 연결: {settings.QDRANT_URL}")
    else:
        client = QdrantClient(":memory:")
        logger.warning("Qdrant 인메모리 모드")

    return client


class SimilarMeetingResult:
    __slots__ = ("request_id", "headcount", "restaurant_pairs", "score")

    def __init__(self, request_id: str, headcount: int,
                 restaurant_pairs: dict, score: float):
        self.request_id       = request_id
        self.headcount        = headcount
        self.restaurant_pairs = restaurant_pairs
        self.score            = score


def search_similar_meetings(
    pref_vector: list,
    headcount: int,
    top_k: int,
    min_score: float,
    collection: str,
    likes: Dict[str, float] = None,
    dislikes: Dict[str, float] = None,
    dow: int = 0,
    start_hour: int = 19,
) -> List[SimilarMeetingResult]:
    client = get_vector_client()

    logger.info(
        f"[CF] 검색 시작 | collection={collection} | top_k={top_k} | "
        f"likes={likes} | headcount={headcount}"
    )

    try:
        from qdrant_client.models import SearchParams
        try:
            # 신버전 API (v1.7+) — exact search, 전체 반환
            response = client.query_points(
                collection_name=collection,
                query=pref_vector,
                limit=10000,
                with_payload=True,
                search_params=SearchParams(exact=True),
            )
            hits = response.points
        except AttributeError:
            # 구버전 API fallback
            hits = client.search(
                collection_name=collection,
                query_vector=pref_vector,
                limit=10000,
                with_payload=True,
                search_params=SearchParams(exact=True),
            )
        logger.info(f"[CF] Qdrant 응답 | hits={len(hits)}개")
    except Exception as e:
        logger.error(f"[CF] Qdrant 검색 실패: {e}")
        return []

    # headcount 재가중 후 min_score 이상인 모임 전체 수집 (원본과 동일)
    results: List[SimilarMeetingResult] = []
    for hit in hits:
        payload  = hit.payload or {}
        hc       = int(payload.get("headcount", headcount))
        hc_sim   = max(0.0, 1.0 - abs(hc - headcount) / 3.0)
        combined = hit.score * 0.7 + hc_sim * 0.3

        if combined <= min_score:
            continue

        raw_pairs = payload.get("restaurant_pairs", {})
        pairs     = {int(k): int(v) for k, v in raw_pairs.items()}
        if not pairs:
            continue

        results.append(SimilarMeetingResult(
            request_id       = str(payload.get("request_id", hit.id)),
            headcount        = hc,
            restaurant_pairs = pairs,
            score            = combined,
        ))

    # combined 기준 상위 top_k만 (원본과 동일)
    results.sort(key=lambda r: r.score, reverse=True)
    results = results[:top_k]
    logger.info(f"[CF] 최종 결과 | {len(results)}개 (min_score={min_score})")
    return results