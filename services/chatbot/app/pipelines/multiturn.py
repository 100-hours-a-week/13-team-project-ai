import json
import re
from app.llm.vllm_client import chat
from langsmith import traceable
from app.common.retriever import qdrant_facility_filter, filter_open_now, _SERVICE_REGIONS
from app.core.config import settings

from app.common.parser import validate_conditions, rule_based_parse, extract_restaurant_names
from app.llm.prompts import get_condition_extraction_prompt, get_answer_generation_prompt
from app.common.details import get_details, format_details, generate_answer

@traceable(run_type="chain", name="Extract Multi-turn Conditions")
async def extract_accumulated_conditions(
    history: list[dict],
    new_message: str,
    current_time: str,
) -> dict:
    history_text = "\n".join(f"{h['role']}: {h['content'][:150]}" for h in history[-6:])
    prompt = get_condition_extraction_prompt(new_message, current_time, history_text)
    
    raw = await chat([{"role": "user", "content": prompt}], max_tokens=200)
    try:
        cleaned = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
        result  = json.loads(cleaned)
        return validate_conditions(result)
    except Exception:
        return rule_based_parse(new_message)


@traceable(run_type="chain", name="Multiturn Pipeline")
async def multiturn_pipeline(user_message: str, history: list[dict], current_time: str) -> str:
    # ── 1: 누적 조건 추출 ──
    conditions = await extract_accumulated_conditions(history, user_message, current_time)
    
    # [방어 로직] 쿼리에 직접 식당 이름이 언급된 경우, 유연하게 대응
    name_candidates = extract_restaurant_names(user_message)
    if name_candidates and not conditions.get("name_query"):
        conditions["name_query"] = name_candidates[0]
        conditions["category"] = None
        conditions["menu_query"] = None

    # ── 1.5: 식당명 직접 검색 ──
    from app.common.retriever import qdrant_name_search
    
    # 1. LLM 추출 이름 우선
    name_query = conditions.get("name_query")
    # 2. 추출 실패 시 쿼리에서 직접 추출 시도 (Fallback)
    if not name_query:
        candidates_raw = extract_restaurant_names(user_message)
        name_query = candidates_raw[0] if candidates_raw else None

    if name_query:
        name_ids = await qdrant_name_search(name_query)
        
        if not name_ids:
            return f"죄송해요, '{name_query}'에 대한 정보는 등록되어 있지 않아요."

        details = await get_details(name_ids[:5])
        return await generate_answer(user_message, details, history)

    # ── Step 2: 시설 필터 검색 ──
    facility_ids = await qdrant_facility_filter(conditions)
    candidates   = set(facility_ids)

    # ── Step 2.5: 메뉴 필터 검색 ──
    menu_q = conditions.get("menu_query")
    if menu_q and menu_q != "메뉴" and candidates:
        from app.common.retriever import qdrant_menu_search
        menu_ids = await qdrant_menu_search(menu_q, region=conditions.get("region"))
        if menu_ids: candidates &= set(menu_ids)

    # ── Step 3: 영업시간 필터 ──
    if conditions.get("open_now") and candidates:
        open_ids = await filter_open_now(list(candidates), current_time)
        if open_ids: candidates = set(open_ids)

    if not candidates:
        return "조건에 맞는 식당을 찾기가 어렵네요. 다시 물어봐주시겠어요?"

    # ── Step 4 & 5: 상세정보 조회 및 답변 생성 ──
    details = await get_details(list(candidates)[:5])
    return await generate_answer(user_message, details, history)