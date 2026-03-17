import asyncio
from app.llm.vllm_client import chat
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from app.common.retriever import embed_query, search_by_hypothesis, _SERVICE_REGIONS
from app.common.details import get_details, generate_answer
from app.common.reranker import rerank_place_ids
from app.pipelines.multistep import multistep_pipeline
from app.common.parser import rule_based_parse


# ──────────────────────────────────────────
# 의도 감지 키워드
# ──────────────────────────────────────────

_FAMILY_KEYWORDS  = ["부모님", "어머니", "아버지", "부모", "어르신", "가족", "어른"]
_DATE_KEYWORDS    = ["데이트", "연인", "커플", "기념일", "애인", "남자친구", "여자친구"]
_DINNER_KEYWORDS  = ["저녁", "디너", "야간", "밤"]
_LUNCH_KEYWORDS   = ["점심", "런치", "낮"]


def _detect_intent(query: str) -> dict:
    """
    쿼리에서 방문 목적/동행 의도를 감지

    반환:
        {
            "with_parent":  bool,  # 부모님/어르신 동행
            "is_date":      bool,  # 데이트/기념일
            "prefer_dinner": bool, # 저녁 선호
            "prefer_lunch":  bool, # 점심 선호
        }
    """
    return {
        "with_parent":   any(k in query for k in _FAMILY_KEYWORDS),
        "is_date":       any(k in query for k in _DATE_KEYWORDS),
        "prefer_dinner": any(k in query for k in _DINNER_KEYWORDS),
        "prefer_lunch":  any(k in query for k in _LUNCH_KEYWORDS),
    }


def deduplicate_by_place(results: list) -> list[int]:
    """검색 결과에서 중복된 place_id 제거 (순서 유지)"""
    seen, unique_ids = set(), []
    for r in results:
        pid = r.payload["place_id"]
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)
    return unique_ids


def generate_hypothetical(query: str) -> str:
    """
    사용자 질문에서 키워드를 추출해 가상의 식당 묘사를 템플릿으로 즉시 생성 (LLM 불필요)
    LLM 호출을 1번으로 줄여 응답 속도를 크게 개선.
    """
    # 목적 키워드 추출
    purpose_map = {
        "부모님": "부모님과 함께 식사하기 좋고 조용하며 룸이 있는",
        "어머니": "어머님과 함께 식사하기 좋고 조용하며 룸이 있는",
        "아버지": "아버님과 함께 식사하기 좋고 조용하며 룸이 있는",
        "가족": "가족 모임에 적합하고 넓은 좌석과 조용한 분위기의",
        "데이트": "연인과 데이트하기 좋고 분위기가 좋으며 조명이 아늑한",
        "기념일": "기념일에 어울리는 특별하고 분위기 있는",
        "회식": "직장 회식에 적합하고 단체석이 있는",
        "혼밥": "혼자 방문하기 편안하고 1인석이 있는",
        "점심": "점심 시간에 빠르게 식사할 수 있는",
        "저녁": "저녁 식사를 즐기기에 좋은 분위기의",
    }
    category_map = {
        "한식": "한식을 제공하는", "일식": "일식을 제공하는",
        "중식": "중식을 제공하는", "양식": "양식을 제공하는",
        "고기": "고기구이 전문점인", "해산물": "해산물 요리 전문점인",
        "카페": "카페 분위기의", "술": "술과 안주를 제공하는",
    }
    purpose_desc = next(
        (v for k, v in purpose_map.items() if k in query), "편안하고 맛있는"
    )
    category_desc = next(
        (v for k, v in category_map.items() if k in query), ""
    )
    region_hint   = "판교 또는 수내 지역의"
    return (
        f"{region_hint} {category_desc} {purpose_desc} 식당입니다. "
        f"리뷰가 많고 맛과 서비스에 대한 긍정적인 평가가 많으며, "
        f"청결하고 직원이 친절한 음식점입니다."
    )


async def _get_category_ids(category: str, region: str | None) -> list[int]:
    """
    PostgreSQL profile 테이블에서 카테고리 + 지역 기준 place_id 조회
    """
    import app.db.clients as clients
    from sqlalchemy import text
    from app.common.retriever import _SERVICE_REGIONS

    region_clause = ""
    params = {"category": category}
    if region:
        # 서비스 지역 내 키워드면 해당 키워드로 검색, 아니면 null 처리
        clean_region = re.sub(r"역$", "", region).strip()
        region_clause = "AND (road_address LIKE :region OR jibun_address LIKE :region)"
        params["region"] = f"%{clean_region}%"
    else:
        # 지역 미지정 시 서비스 지역 전체에서 조회
        clauses = []
        for i, r in enumerate(_SERVICE_REGIONS):
            key = f"r{i}"
            clauses.append(f"road_address LIKE :{key} OR jibun_address LIKE :{key}")
            params[key] = f"%{r}%"
        region_clause = f"AND ({' OR '.join(clauses)})"

    sql = text(f"""
        SELECT id FROM restaurants
        WHERE category_mapped = :category
        {region_clause}
    """)

    async with clients.AsyncSessionLocal() as session:
        result = await session.execute(sql, params)
        return [row[0] for row in result.fetchall()]


async def _noop() -> None:
    """의도 없을 때 asyncio.gather 자리채우기용 noop"""
    return None


@traceable(run_type="tool", name="HyDE Intent Prefilter")
async def _intent_prefilter(
    intent: dict,
    region: str | None,
    category: str | None,
) -> list[int] | None:
    """
    의도 기반 필터 → place_id 리스트

    1단계 (RDB): category 있으면 PostgreSQL에서 카테고리 place_id 조회
    2단계 (Qdrant): review_evidence에서 mood + negative_ratio 필터
    3단계: 교집합 반환

    필터 결과 5개 미만이면 None 반환
    """
    import app.db.clients as clients
    from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, Range
    from app.core.config import settings

    need_quality = intent.get("with_parent") or intent.get("is_date")
    need_dinner  = intent.get("prefer_dinner")

    # 1단계: 카테고리 필터 (RDB)
    category_ids: set[int] | None = None
    if category:
        ids = await _get_category_ids(category, region)
        if ids:
            category_ids = set(ids)
            print(f"[HyDE] category prefilter ({category}): {len(category_ids)}개")

    # 품격/부모님/데이트/저녁 의도 없고 카테고리도 없으면 스킵
    if not need_quality and not need_dinner and category_ids is None:
        return None

    # 2단계: Qdrant mood + negative_ratio 필터
    must = [FieldCondition(key="doc_type", match=MatchValue(value="review_evidence"))]

    if region:
        from app.common.retriever import _region_filter
        must.append(_region_filter(region))
    else:
        from app.common.retriever import _default_region_filter
        must.append(_default_region_filter())

    if need_quality:
        must.extend([
            # mood 필터: 약간 완화 (gte 30 -> 15)
            FieldCondition(key="tag_counts.mood",
                           range=Range(gte=15)),
            FieldCondition(key="computed_ratios.negative_ratio",
                           range=Range(lt=0.15)),
        ])

    if need_dinner:
        must.append(FieldCondition(
            key="computed_ratios.time_band_dinner_ratio",
            range=Range(gte=0.3)
        ))

    # category_ids 있으면 Qdrant 필터에도 추가 (교집합 효과)
    if category_ids:
        must.append(FieldCondition(
            key="place_id",
            match=MatchAny(any=list(category_ids))
        ))

    results, _ = await clients.qdrant.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=must),
        limit=200,
        with_payload=True,
        with_vectors=False,
    )

    if not results:
        # mood 필터 제거하고 카테고리만으로 재시도
        if category_ids:
            print("[HyDE] mood 필터 결과 없음 → 카테고리 필터만 사용")
            return list(category_ids) if len(category_ids) >= 5 else None
        return None

    place_ids = list({r.payload["place_id"] for r in results})
    print(f"[HyDE] intent prefilter ({len(place_ids)}개) - quality={need_quality}, dinner={need_dinner}")
    return place_ids if len(place_ids) >= 3 else None


@traceable(run_type="chain", name="HyDE Pipeline")
async def hyde_pipeline(
    query: str,
    current_time: str,
) -> str:
    """
    HyDE (Hypothetical Document Embeddings) 파이프라인

    흐름:
        1. 지역/의도 추출
        2. 의도 기반 사전 필터 (부모님/데이트 등 → payload Range 필터)
        3. 가상 답변 생성 (LLM)
        4. 가상 답변 임베딩 & review_evidence 벡터 검색
           (사전 필터 결과 있으면 해당 place_id 내에서만 검색)
        5. Rerank → 상위 5개
        6. 상세정보 & 답변 생성
    """
    # 1. 지역/카테고리/의도 추출
    parsed   = rule_based_parse(query)
    region   = parsed.get("region")
    category = parsed.get("category")
    intent   = _detect_intent(query)

    if region and not any(r in region for r in _SERVICE_REGIONS):
        return (
            f"죄송합니다. 현재 '{region}' 지역은 서비스를 준비 중이에요. "
            "지금은 판교(삼평동, 백현동 등)와 수내동 지역에 대해서만 맛집 정보를 알려드릴 수 있습니다!"
        )

    # 2. 가상 답변 생성 (규칙 기반, 즉시 반환)
    hypothetical = generate_hypothetical(query)
    print(f"[HyDE] 가상 답변: {hypothetical[:50]}...")

    # 3. 의도 기반 사전 필터 (비동기)
    prefiltered_ids = None
    if any(intent.values()):
        prefiltered_ids = await _intent_prefilter(intent, region, category)

    # 4. 임베딩 & 검색
    vec     = embed_query(hypothetical)
    results = await search_by_hypothesis(
        vec,
        keyword=query,
        region=region,
        allowed_ids=prefiltered_ids,
    )

    # 4-1. Fallback: prefilter 제거하고 재검색
    if len(results) < 5 and prefiltered_ids is not None:
        print(f"[HyDE] 결과 {len(results)}개 → prefilter 제거 후 재검색")
        results = await search_by_hypothesis(
            vec, keyword=query, region=region
        )

    # 4-3. Fallback: Multistep
    if not results:
        print("[HyDE] 최종 결과 없음 → Multistep fallback")
        run_tree = get_current_run_tree()
        if run_tree:
            run_tree.add_tags(["hyde_fallback"])
        return await multistep_pipeline(query, current_time)

    # 5. 상위 선택 (벡터 스코어순, reranker 스킵)
    # 요청 개수 반영 (최대 3개 제한)
    final_limit = min(parsed.get("requested_count") or 3, 3)
    top_ids = deduplicate_by_place(results)[:final_limit]
    print(f"[HyDE] 상위: {len(top_ids)}개 (요청 제한 적용)")

    # 6. 상세정보 & 답변
    try:
        details = await get_details(top_ids)
        if not details:
            print("[HyDE] 상세정보 조회 실패 (empty details)")
            return await multistep_pipeline(query, current_time)
        return await generate_answer(query, details)
    except Exception as e:
        print(f"[HyDE] Error in generate_answer: {e}")
        return await multistep_pipeline(query, current_time)