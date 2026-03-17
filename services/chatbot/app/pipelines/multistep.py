# app/pipelines/multistep.py
import re
import json
from app.common.retriever import (
    qdrant_facility_filter, qdrant_name_search, qdrant_menu_search, 
    filter_open_now, _SERVICE_REGIONS
)
from app.common.reranker import rerank_place_ids
from app.llm.vllm_client import chat
from app.core.config import settings
from langsmith import traceable

# hyde.py 등에서 참조하는 허용 지역 목록
ALLOWED_REGIONS = _SERVICE_REGIONS

from app.common.parser import validate_conditions, rule_based_parse
from app.llm.prompts import get_condition_extraction_prompt

@traceable(run_type="chain", name="Extract Conditions (LLM)")
async def _extract_conditions_with_llm(query: str, current_time: str) -> dict:
    """LLM을 사용하여 쿼리에서 검색 조건 추출"""
    prompt = get_condition_extraction_prompt(query, current_time)
    try:
        raw     = await chat([{"role": "user", "content": prompt}], max_tokens=200)
        cleaned = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
        result  = json.loads(cleaned)
        return validate_conditions(result)
    except Exception:
        return rule_based_parse(query)

from app.common.details import get_details, format_details, generate_answer

@traceable(run_type="chain", name="Multistep Pipeline")
async def multistep_pipeline(query: str, current_time: str) -> str:
    """RAG 파이프라인 집합체"""
    # 생략된 기존 로직은 내부에서 context_summary 등을 만들어서 generate_answer에 전달하도록 수정
    # 실제 구현은 아래와 같이 generate_answer를 호출하는 식으로 단순화
    # 1. 조건 추출
    conditions = await _extract_conditions_with_llm(query, current_time)
    
    # [방어 로직] LLM이 이름을 놓쳤을 경우 규칙 기반으로 보완
    if not conditions.get("name_query"):
        rule_based = rule_based_parse(query)
        if rule_based.get("name_query"):
            conditions["name_query"] = rule_based["name_query"]
            conditions["category"] = None
            conditions["menu_query"] = None

    # 지역 제한 체크
    region = conditions.get("region")
    if region and not any(r in region for r in _SERVICE_REGIONS):
        return f"현재 {region} 지역은 제공하지 않아요. 판교나 수내동 근처 정보를 물어봐 주세요!"

    # 2. 특정 식당 모드
    name_q = conditions.get("name_query")
    if name_q:
        place_ids = await qdrant_name_search(name_q)
        if place_ids:
            details = await get_details(place_ids[:3])
            return await generate_answer(query, details)
        else:
            if not any([conditions.get("category"), conditions.get("menu_query"), conditions.get("parking")]):
                return f"죄송해요, '{name_q}'라는 식당은 저희 DB에 아직 등록되어 있지 않아요."

    # 3. 추천 질문 모드
    facility_ids = await qdrant_facility_filter(conditions)
    candidates = set(facility_ids)

    menu_q = conditions.get("menu_query")
    if menu_q and menu_q != "메뉴" and candidates:
        menu_ids = await qdrant_menu_search(menu_q, region=conditions.get("region"))
        if menu_ids: candidates &= set(menu_ids)

    if conditions.get("open_now") and candidates:
        open_ids = await filter_open_now(list(candidates), current_time)
        candidates = set(open_ids)

    if not candidates:
        return "조건에 맞는 식당을 찾기가 어렵네요. 조건을 조금 변경해서 다시 물어봐 주시셨어요?"

    # 4. 재정렬 및 최종 답변
    top_ids = await _rerank_candidates(query, list(candidates)[:50])
    final_limit = min(conditions.get("requested_count") or 3, 3)
    details = await get_details(top_ids[:final_limit])
    
    return await generate_answer(query, details)


@traceable(run_type="tool", name="Rerank Candidates")
async def _rerank_candidates(query: str, place_ids: list[int]) -> list[int]:
    """
    place_id 리스트를 profile text 기준으로 rerank
    """
    from app.common.reranker import is_available
    if not is_available() or not place_ids:
        return place_ids

    import app.db.clients as clients
    from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
    from app.core.config import settings

    results, _ = await clients.qdrant.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="doc_type", match=MatchValue(value="profile")),
            FieldCondition(key="place_id", match=MatchAny(any=place_ids)),
        ]),
        limit=len(place_ids),
        with_payload=True,
        with_vectors=False,
    )

    class _FakePoint:
        def __init__(self, payload): self.payload = payload

    fake_points = [_FakePoint(r.payload) for r in results]
    return await rerank_place_ids(query, fake_points, top_k=5)