import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.common.details import get_details, generate_answer
from app.common.parser import (
    extract_accumulated_conditions,
    extract_restaurant_names,
    FORBIDDEN_WORDS,
    PRONOUNS
)
from app.common.retriever import qdrant_name_search, get_id_by_name

async def multiturn_pipeline(user_message: str, history: List[Dict[str, str]], current_time: str) -> str:
    """
    멀티턴 대화 맥락을 유지하여 사용자 질문에 답변하는 파이프라인.
    이전 추천 목록이나 언급된 식당을 기반으로 지시어(대치어)를 해소하고 정보를 제공함.
    """
    # ── 1.1: 검색 조건 추출 (LLM 활용) ──
    conditions = await extract_accumulated_conditions(user_message, current_time, history)
    
    # ── 1.2: 이전 대화 맥락 분석 ──
    context_names = []
    is_referencing_previous = any(kw in user_message for kw in PRONOUNS)

    # 마지막 AI 답변에서 식당 목록 추출
    last_recommendations_found = []
    has_multi_ref = any(kw in user_message for kw in ["모두", "전부", "세곳", "둘 다"])
    
    for h in reversed(history[-6:]):
        if h["role"] == "assistant":
            found = extract_restaurant_names(h["content"])
            if found:
                for f in found:
                    cleaned_f = f.strip()
                    base_f = re.sub(r"\s*(?:판교점|수내점|삼평점|백현점|분당점|본점|식당|레스토랑|바|카페)$", "", cleaned_f).strip()
                    if base_f and base_f not in FORBIDDEN_WORDS:
                        context_names.append(base_f)
                last_recommendations_found = found
                if not has_multi_ref or len(found) > 1:
                    break

    # ── 1.3: 새로운 질문 여부 판단 ──
    # 정보를 묻는 키워드가 있으면 새로운 '추천' 요청이 아니라 기존 맥락에 대한 '정보' 요청으로 간주
    is_info_query = any(kw in user_message for kw in ["주차", "룸", "영업", "운영", "정보", "시간", "메뉴", "위치", "어디", "번호", "전화"])
    is_new_recommendation_request = (
        any(kw in user_message for kw in ["추천", "찾아줘", "다른", "새로운", "말고"]) 
        and not is_info_query
    )

    # [방어] 특정 속성 질의 시 이전의 menu_query가 방해되지 않도록 제거
    if any(kw in user_message for kw in ["영업", "운영", "주차", "룸", "시간"]):
        conditions["menu_query"] = None
    if conditions.get("menu_query") and (len(conditions["menu_query"]) > 15 or "," in conditions["menu_query"]):
        conditions["menu_query"] = None

    # 맥락 초기화 조건: 새로운 추천 요청이거나, 맥락 지시어가 없으면서 새로운 식당명이 언급되지도 않았는데 정보 요청도 아닌 경우
    if is_new_recommendation_request:
        context_names = []
    elif not is_referencing_previous and not extract_restaurant_names(user_message):
        # 정보 요청 키워드조차 없으면 맥락 끊김으로 간주 (단순 대화 등)
        if not is_info_query:
            context_names = []

    # ── 1.4: 식당명 최종 결정 ──
    direct_names = extract_restaurant_names(user_message)
    potential_names = direct_names if direct_names else context_names

    if not potential_names:
        return "어떤 식당에 대해 알고 싶으신지 식당 이름을 다시 말씀해 주시겠어요?"

    # ── 1.5: Qdrant 검색 (ID 확보) ──
    all_ids = []
    target_region = conditions.get("region")
    
    if len(potential_names) == 1:
        exact_id = await get_id_by_name(potential_names[0])
        if exact_id: all_ids = [exact_id]
        
    if not all_ids:
        for name in potential_names:
            ids = await qdrant_name_search(name, region=target_region)
            if ids: all_ids.append(ids[0])

    unique_ids = list(dict.fromkeys(all_ids))
    
    # ── 1.6: 정보 조회 및 답변 생성 ──
    if unique_ids:
        try:
            details = await get_details(unique_ids[:5])
            filtered_details = []
            for d in details:
                mat = True
                if not has_multi_ref:
                    if conditions.get("room") and not d.get("facility", {}).get("room", {}).get("available"):
                        mat = False
                    if conditions.get("parking") and not d.get("facility", {}).get("parking", {}).get("available"):
                        mat = False
                    if conditions.get("capacity_min") and not d.get("facility", {}).get("group", {}).get("available"):
                        mat = False
                if mat: filtered_details.append(d)

            if not filtered_details:
                return "요청하신 정보에 해당하는 식당이 없어요."

            return await generate_answer(user_message, filtered_details, history)
            
        except Exception as e:
            import logging
            logging.error(f"Multiturn details error: {str(e)}")
            return "식당 정보를 불러오는 중 오류가 발생했습니다."
            
    return "요청하신 식당을 찾을 수 없습니다."