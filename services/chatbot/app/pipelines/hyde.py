import asyncio
from app.llm.vllm_client import chat
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from app.common.retriever import embed_query, search_by_hypothesis, _SERVICE_REGIONS, get_category_ids
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
        "부모님": "부모님과 함께 식사하기 좋고 조용한",
        "어머니": "어머님과 함께 식사하기 좋고 조용한",
        "아버지": "아버님과 함께 식사하기 좋고 조용한",
        "가족": "가족 모임에 적합하고 넓은 좌석과 조용한 분위기의",
        "데이트": "연인과 데이트하기 좋고 분위기가 좋은",
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


async def _noop() -> None:
    """의도 없을 때 asyncio.gather 자리채우기용 noop"""
    return None
    return None


@traceable(run_type="tool", name="HyDE Intent Prefilter")
async def _intent_prefilter(
    intent: dict,
    region: str | None,
    category: str | None,
) -> list[int] | None:
    """
    의도/카테고리 기반 필터 → place_id 리스트

    카테고리 있으면 Postgres에서 해당 카테고리 place_id 1차 조회 (하드 필터)
    부모님/데이트/저녁 의도 있으면 Qdrant mood/시간대 필터 추가 적용 (소프트 필터)
    카테고리만 있고 의도 없으면 category_ids 바로 반환
    """
    import app.db.clients as clients
    from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, Range
    from app.core.config import settings

    need_quality = intent.get("with_parent") or intent.get("is_date")
    need_dinner  = intent.get("prefer_dinner")

    # 1단계: 카테고리 필터 (Postgres RDB — 하드 필터)
    category_ids: list[int] | None = None
    if category:
        ids = await get_category_ids(category, region)
        if ids:
            category_ids = ids
            print(f"[HyDE] category prefilter ({category}): {len(category_ids)}개")
        else:
            print(f"[HyDE] category prefilter ({category}): 결과 없음")
            return []  # 카테고리 지정했는데 해당 식당이 없으면 빈 리스트 반환

    # 의도(품격/저녁) 없고 카테고리도 없으면 필터 불필요
    if not need_quality and not need_dinner:
        return category_ids  # 카테고리만 있으면 바로 반환 (None이면 필터 없음)

    # 2단계: Qdrant mood + 시간대 필터 (카테고리 내에서 추가 적용)
    must = [FieldCondition(key="doc_type", match=MatchValue(value="review_evidence"))]

    if region:
        from app.common.retriever import _region_filter
        must.append(_region_filter(region))
    else:
        from app.common.retriever import _default_region_filter
        must.append(_default_region_filter())

    # 카테고리 필터를 하드 필터로 Qdrant에도 적용
    if category_ids:
        must.append(FieldCondition(
            key="place_id",
            match=MatchAny(any=category_ids)
        ))

    if need_quality:
        must.extend([
            FieldCondition(key="tag_counts.mood", range=Range(gte=15)),
            FieldCondition(key="computed_ratios.negative_ratio", range=Range(lt=0.15)),
        ])

    if need_dinner:
        must.append(FieldCondition(
            key="computed_ratios.time_band_dinner_ratio",
            range=Range(gte=0.3)
        ))

    results, _ = await clients.qdrant.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=must),
        limit=200,
        with_payload=True,
        with_vectors=False,
    )

    if not results:
        # mood/시간대 필터 결과 없음 → 카테고리 필터만 유지
        if category_ids:
            print("[HyDE] mood 필터 결과 없음 → 카테고리 필터만 사용")
            return category_ids
        return None

    place_ids = list({r.payload["place_id"] for r in results})
    print(f"[HyDE] intent prefilter ({len(place_ids)}개) - quality={need_quality}, dinner={need_dinner}")
    return place_ids


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

    try:
        async with asyncio.timeout(30):
            # 3. 의도/카테고리 기반 사전 필터 (비동기)
            prefiltered_ids = None
            if any(intent.values()) or category:
                prefiltered_ids = await _intent_prefilter(intent, region, category)

            # 4. 임베딩 & 검색
            vec     = embed_query(hypothetical)
            results = await search_by_hypothesis(
                vec,
                keyword=query,
                region=region,
                allowed_ids=prefiltered_ids,
            )
            
            # (생략된 Fallback 로직 등은 try 블록 안에 포함되어야 함)
            # 여기서는 편의상 results 가 부족할 때의 로직까지 30초 내에 끝내도록 감싸는 것임
    except asyncio.TimeoutError:
        print("[HyDE] 30초 타임아웃 발생 -> Multistep fallback")
        return await multistep_pipeline(query, current_time)


    # 4-1. Fallback: prefilter 결과 부족 시 mood 필터 없이 재검색
    # 단, 카테고리가 지정된 경우 allowed_ids(카테고리 필터)는 절대 해제하지 않음
    if len(results) < 5 and prefiltered_ids is not None and not category:
        print(f"[HyDE] 결과 {len(results)}개 → prefilter 제거 후 재검색 (카테고리 없음)")
        results = await search_by_hypothesis(
            vec, keyword=query, region=region
        )
    elif len(results) < 3 and prefiltered_ids is not None and category:
        # 카테고리는 있지만 리뷰 데이터 부족 → profile 직접 조회로 보완
        print(f"[HyDE] 카테고리 리뷰 부족({len(results)}개) → profile 직접 보완")
        from app.common.retriever import qdrant_facility_filter
        extra_ids = await qdrant_facility_filter({"category": category, "region": region})
        if extra_ids:
            # 기존 results에 없는 ID만 추가로 details 조회
            existing_ids = {r.payload.get("place_id") for r in results}
            supplement = [pid for pid in extra_ids if pid not in existing_ids]
            prefiltered_ids = list(set(prefiltered_ids or []) | set(extra_ids))
            print(f"[HyDE] profile 보완 ID {len(prefiltered_ids)}개")

    # 4-3. Fallback: Multistep
    if not results:
        print("[HyDE] 최종 결과 없음 → Multistep fallback")
        run_tree = get_current_run_tree()
        if run_tree:
            run_tree.add_tags(["hyde_fallback"])
        return await multistep_pipeline(query, current_time)

    # 5. 상위 선택: 벡터 스코어 기반 중복 제거
    final_limit = min(parsed.get("requested_count") or 3, 3)
    top_ids = deduplicate_by_place(results)

    # 5-1. 카테고리가 있는데 top_ids에 카테고리 외 ID가 섞일 수 있음
    #       → prefiltered_ids(카테고리 ID)로 한번 더 교집합 필터
    if prefiltered_ids:
        filtered_top = [pid for pid in top_ids if pid in set(prefiltered_ids)]
        if filtered_top:
            top_ids = filtered_top
            print(f"[HyDE] 카테고리 교집합 필터 후: {len(top_ids)}개")
        else:
            # 벡터 검색 결과가 카테고리에 없는 경우 → category_ids 직접 사용
            print("[HyDE] 벡터 결과가 카테고리 밖 → category_ids 직접 사용")
            top_ids = prefiltered_ids

    top_ids = top_ids[:final_limit]

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