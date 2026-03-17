# app/common/retriever.py
import re
import asyncio
from datetime import datetime
from qdrant_client.models import (
    Filter, FieldCondition, MatchValue, MatchAny, Range, MatchText,
    Prefetch, FusionQuery, Fusion,
)
from app.core.config import settings
import app.db.clients as clients
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree


# ──────────────────────────────────────────
# 임베딩
# ──────────────────────────────────────────

def embed_query(text: str) -> list[float]:
    """
    KR-SBERT 쿼리 임베딩 (768차원)
    주의: prefix 없이 인코딩 — e5-large 와 달리 'query: ' prefix 붙이면 안 됨
    """
    return clients.embedding_model.encode(
        text,
        normalize_embeddings=True
    ).tolist()


# ──────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────

def _region_filter(region: str) -> Filter:
    """지역명 → road_address / jibun_address 텍스트 매칭 Filter"""
    # region이 리스트로 들어오는 경우 방어 (LLM 추출 오류)
    if isinstance(region, list):
        region = region[0] if region else ""
    
    # '판교역', '수내역' 등 역 키워드 제거 후 검색
    clean_region = re.sub(r"역$", "", str(region)).strip()
    return Filter(
        should=[
            FieldCondition(key="road_address",  match=MatchText(text=clean_region)),
            FieldCondition(key="jibun_address", match=MatchText(text=clean_region)),
        ]
    )


# 서비스 지역 키워드 (판교/수내동)
_SERVICE_REGIONS = ["백현동", "수내동", "삼평동", "판교", "수내", "삼평", "백현", "판교역"]


def _default_region_filter() -> Filter:
    """
    region 미지정 시 서비스 지역 기본 필터
    ALLOWED_REGIONS 전체를 OR 조건으로 묶어서 서비스 외 지역 차단
    """
    should = []
    for r in _SERVICE_REGIONS:
        should.append(FieldCondition(key="road_address",  match =MatchText(text=r)))
        should.append(FieldCondition(key="jibun_address", match=MatchText(text=r)))
    return Filter(should=should)


# ──────────────────────────────────────────
# 1. 시설 조건 필터
# ──────────────────────────────────────────

@traceable(run_type="retriever", name="Qdrant Facility Filter")
async def qdrant_facility_filter(conditions: dict) -> list[int]:
    """
    profile doc payload 필터 검색 → place_id 리스트 반환

    처리하는 조건:
        category     → category 정확 매칭
        region       → road_address / jibun_address 텍스트 매칭
        parking      → facility.parking.available = True
        room         → facility.room.available = True
        capacity_min → facility.group.available = True
                       (실서버 확인 결과 group_seat.max_pax 필드 없음)

    조건이 없으면 전체 profile 문서 반환 (최대 500개)
    """
    must = [
        FieldCondition(key="doc_type", match=MatchValue(value="profile"))
    ]

    if conditions.get("category"):
        must.append(
            FieldCondition(key="category", match=MatchValue(value=conditions["category"]))
        )

    if conditions.get("region"):
        must.append(_region_filter(conditions["region"]))

    if conditions.get("parking"):
        must.append(
            FieldCondition(key="facility.parking.available", match=MatchValue(value=True))
        )

    if conditions.get("room"):
        must.append(
            FieldCondition(key="facility.room.available", match=MatchValue(value=True))
        )

    if conditions.get("capacity_min"):
        must.append(
            FieldCondition(key="facility.group.available", match=MatchValue(value=True))
        )

    results, _ = await clients.qdrant.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=must),
        limit=50,
        with_payload=True,
        with_vectors=False
    )
    results_ids = [r.payload["place_id"] for r in results]
    # LangSmith 결과 개수 기록
    run_tree = get_current_run_tree()
    if run_tree:
        run_tree.metadata["result_count"] = len(results_ids)
        if not results_ids:
            run_tree.add_tags(["retrieval_fail"])
    return results_ids


@traceable(run_type="retriever", name="Qdrant Name Search")
async def qdrant_name_search(name_query: str) -> list[int]:
    """
    profile doc 에서 식당 이름(name)으로 검색 → place_id 리스트 반환
    Full-Text MatchText 를 사용하여 '홍호아 판교점' 등을 검색
    """
    if not name_query:
        return []

    # '판교점', '수내점' 등 지점명 접미사 제거 시도 (검색 정확도 향상)
    clean_name = re.sub(r"\s*(?:판교|수내|삼평|백현)점$", "", name_query).strip()

    # 1. 원본 이름 + 접미사 제거 이름 둘 다 'name' 필드와 'text' 필드에서 검색
    must = [FieldCondition(key="doc_type", match=MatchValue(value="profile"))]
    should = [
        FieldCondition(key="name", match=MatchText(text=name_query)),
        FieldCondition(key="text", match=MatchText(text=name_query)),
        FieldCondition(key="name", match=MatchText(text=clean_name)),
    ]

    results, _ = await clients.qdrant.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=must, should=should),
        limit=10,
        with_payload=True,
        with_vectors=False
    )
    results_ids = [r.payload["place_id"] for r in results]
    # LangSmith 결과 개수 기록
    run_tree = get_current_run_tree()
    if run_tree:
        run_tree.metadata["result_count"] = len(results_ids)
        if not results_ids:
            run_tree.add_tags(["retrieval_fail"])
    return results_ids


# ──────────────────────────────────────────
# 2. 메뉴 Hybrid Search  ← 변경 (dense-only → Full-Text + Dense RRF)
# ──────────────────────────────────────────

@traceable(run_type="retriever", name="Qdrant Menu Search")
async def qdrant_menu_search(
    menu_query: str | None,
    region: str | None = None,
) -> list[int]:
    """
    menu doc Hybrid Search → place_id 리스트 반환

    Hybrid 전략 (Full-Text + Dense → RRF):
        Prefetch A — Full-Text 검색 (text 필드 payload index)
            정확한 메뉴명 키워드 매칭 ("삼겹살", "파스타" 등)
        Prefetch B — Dense 벡터 검색 (KR-SBERT 768차원)
            의미적 유사도 ("고기" → "삼겹살/갈비" 등 연관 메뉴)
        RRF(Reciprocal Rank Fusion) 로 두 결과 합산 재정렬

    dense-only 대비 개선:
        정확한 메뉴명 검색 정확도 향상 + 유사 표현 벡터 검색 유지

    region 이 주어지면 두 Prefetch 모두에 주소 필터 추가
    실패 시 dense-only fallback
    """
    if not menu_query:
        return []

    vec = embed_query(menu_query)

    must = [FieldCondition(key="doc_type", match=MatchValue(value="menu"))]
    if region:
        must.append(_region_filter(region))
    base_filter = Filter(must=must)

    try:
        response = await clients.qdrant.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            prefetch=[
                # A: Full-Text 검색 — 정확한 메뉴명 키워드 매칭
                Prefetch(
                    query=MatchText(text=menu_query),
                    filter=base_filter,
                    limit=100,
                ),
                # B: Dense 벡터 검색 — 의미적 유사도
                Prefetch(
                    query=vec,
                    filter=base_filter,
                    limit=100,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=50,
            with_payload=True,
        )
        points = response.points

    except Exception as e:
        # Full-Text index 미적용 환경 등 예외 시 dense-only fallback
        print(f"[hybrid menu] fallback to dense-only: {e}")
        response = await clients.qdrant.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=vec,
            query_filter=base_filter,
            limit=100,
            with_payload=True,
        )
        points = response.points

    seen, place_ids = set(), []
    for r in points:
        pid = r.payload["place_id"]
        if pid not in seen:
            seen.add(pid)
            place_ids.append(pid)

    # LangSmith 결과 개수 기록
    run_tree = get_current_run_tree()
    if run_tree:
        run_tree.metadata["result_count"] = len(place_ids)
        if not place_ids:
            run_tree.add_tags(["retrieval_fail"])
    return place_ids


# ──────────────────────────────────────────
# 3. 리뷰 Hybrid Search (HyDE용)  ← 변경 (dense-only → Dense + RRF)
# ──────────────────────────────────────────

@traceable(run_type="retriever", name="HyDE Search by Hypothesis")
async def search_by_hypothesis(
    vec: list[float],
    keyword: str | None = None,
    region: str | None = None,
    allowed_ids: list[int] | None = None,
    top_k: int = 30,
) -> list:
    """
    HyDE 가상 답변 임베딩으로 review + review_evidence 동시 검색 → ScoredPoint 리스트

    Prefetch 3개 → RRF 합산:
        A. review doc Dense       — 실제 리뷰 원문 감성 텍스트 (부모님, 분위기 등 직접 표현)
        B. review_evidence Dense  — 전체 리뷰 집계 요약 (구조화된 정보)
        C. review Full-Text       — 키워드 exact match (부모님, 데이트, 분위기 등)

    score_threshold 제거 — threshold로 자르지 않고 top_k 기준으로만 반환
    allowed_ids: 의도 기반 사전 필터 place_id — 지정 시 해당 집합 내에서만 검색
    """
    base_must = []
    if region:
        base_must.append(_region_filter(region))
    else:
        base_must.append(_default_region_filter())
    if allowed_ids:
        base_must.append(FieldCondition(
            key="place_id",
            match=MatchAny(any=allowed_ids),
        ))

    review_filter   = Filter(must=[
        FieldCondition(key="doc_type", match=MatchValue(value="review")),
        *base_must,
    ])
    evidence_filter = Filter(must=[
        FieldCondition(key="doc_type", match=MatchValue(value="review_evidence")),
        *base_must,
    ])

    prefetches = [
        # A: review doc Dense — 감성 원문
        Prefetch(query=vec, filter=review_filter,   limit=50),
        # B: review_evidence Dense — 집계 요약
        Prefetch(query=vec, filter=evidence_filter, limit=50),
    ]
    # C: review Full-Text — filter에 MatchText 추가 (Prefetch.query는 벡터만 허용)
    if keyword:
        ft_filter = Filter(must=[
            FieldCondition(key="doc_type", match=MatchValue(value="review")),
            FieldCondition(key="text",     match=MatchText(text=keyword)),
            *base_must,
        ])
        prefetches.append(
            Prefetch(query=vec, filter=ft_filter, limit=50)
        )

    try:
        response = await clients.qdrant.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            prefetch=prefetches,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
        points = response.points
        # LangSmith 결과 개수 기록
        run_tree = get_current_run_tree()
        if run_tree:
            run_tree.metadata["result_count"] = len(points)
            if not points:
                run_tree.add_tags(["retrieval_fail"])
        return points

    except Exception as e:
        print(f"[hybrid review] fallback to dense-only: {e}")
        response = await clients.qdrant.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=vec,
            query_filter=review_filter,
            limit=top_k,
            with_payload=True,
        )
        return response.points


# ──────────────────────────────────────────
# 4. 영업시간 필터
# ──────────────────────────────────────────

async def filter_open_now(
    place_ids: list[int],
    current_time: str
) -> list[int]:
    """
    hours doc 파싱 → 현재 영업 중인 place_id 만 반환

    - asyncio.Semaphore(10) 으로 동시 요청 제한 (ResponseHandlingException 방지)
    - hours doc 없는 식당은 결과에서 제외
    - place_ids 가 빈 리스트면 즉시 빈 리스트 반환
    """
    if not place_ids:
        return []

    now = datetime.fromisoformat(current_time)
    sem = asyncio.Semaphore(10)

    async def check(pid: int) -> int | None:
        async with sem:
            results, _ = await clients.qdrant.scroll(
                collection_name=settings.QDRANT_COLLECTION,
                scroll_filter=Filter(must=[
                    FieldCondition(key="doc_type",  match=MatchValue(value="hours")),
                    FieldCondition(key="place_id",  match=MatchValue(value=pid)),
                ]),
                limit=1,
                with_payload=True,
                with_vectors=False
            )
            if results and is_open(results[0].payload.get("text", ""), now):
                return pid
            return None

    checked = await asyncio.gather(*[check(pid) for pid in place_ids])
    return [pid for pid in checked if pid is not None]


# ──────────────────────────────────────────
# 5. 영업시간 파싱 (순수 함수)
# ──────────────────────────────────────────

def is_open(hours_text: str, now: datetime) -> bool:
    """
    hours text 에서 오늘 요일의 영업시간을 파싱하여 현재 시각 영업 여부 반환

    지원 포맷:
        "월요일: 11:00 - 22:00"
        "월: 11:00 ~ 22:00"
        "월요일: 11:00 - 22:00\\n브레이크타임: 15:00 - 17:00"

    브레이크타임 중이면 False 반환
    hours_text 가 비어있거나 오늘 요일 정보 없으면 False 반환
    """
    if not hours_text:
        return False

    day_map = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    today   = day_map[now.weekday()]

    m = re.search(
        rf"(?<![가-힣]){today}[요일]*\s*[:\s]\s*(\d{{1,2}}:\d{{2}})\s*[-~]\s*(\d{{1,2}}:\d{{2}})",
        hours_text
    )
    if not m:
        return False

    try:
        open_t  = datetime.strptime(m.group(1), "%H:%M").time()
        close_t = datetime.strptime(m.group(2), "%H:%M").time()
    except ValueError:
        return False

    if not (open_t <= now.time() <= close_t):
        return False

    bm = re.search(
        r"브레이크타임\s*[:\s]\s*(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})",
        hours_text
    )
    if bm:
        try:
            bs = datetime.strptime(bm.group(1), "%H:%M").time()
            be = datetime.strptime(bm.group(2), "%H:%M").time()
            if bs <= now.time() <= be:
                return False
        except ValueError:
            pass

    return True