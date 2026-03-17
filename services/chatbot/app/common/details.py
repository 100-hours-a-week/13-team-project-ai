# app/common/details.py
import asyncio
import re
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.core.config import settings
import app.db.clients as clients


# ──────────────────────────────────────────
# 1. 상세정보 조회
# ──────────────────────────────────────────

async def get_details(place_ids: list[int]) -> list[dict]:
    """
    place_id 리스트 → 상세정보 리스트 반환

    - asyncio.gather 로 병렬 조회
    - 조회 실패(None) 는 결과에서 제외
    - 최대 5개만 처리 (호출 측에서 슬라이싱 권장)
    """
    if not place_ids:
        return []

    results = await asyncio.gather(*[_fetch_one(pid) for pid in place_ids])
    print(f"[details] 조회 결과: {len(results)}개")
    return [r for r in results if r is not None]


async def _fetch_one(place_id: int) -> dict | None:
    """
    단일 식당 상세정보 조회

    Qdrant 에서 profile + review_evidence + menu + hours 네 doc 을 병렬로 조회하고 합칩니다.
    profile 없으면 None 반환 (나머지는 없어도 허용)
    """
    profile_r, evidence_r, menu_r, hours_r = await asyncio.gather(
        _scroll_one(place_id, "profile"),
        _scroll_one(place_id, "review_evidence"),
        _scroll_one(place_id, "menu"),   # menu doc 은 1개에 전체 목록 포함
        _scroll_one(place_id, "hours"),  # hours doc 은 1개에 전체 영업시간 포함
    )

    if not profile_r:
        return None

    p  = profile_r[0].payload
    ev = evidence_r[0].payload if evidence_r else {}
    h_text = hours_r[0].payload.get("text", "정보 없음") if hours_r else "정보 없음"

    # menu doc text 필드에서 메뉴명 파싱 (대표 메뉴 우선순위 적용)
    menu_names = []
    if menu_r:
        text = menu_r[0].payload.get("text", "")
        all_raw_names = re.findall(r"메뉴명:\s*([^|]+?)\s*\|", text)
        rep_menus = []
        normal_menus = []
        seen_m = set()

        for n in all_raw_names:
            cleaned = _strip_emoji(n).strip()
            if not cleaned or cleaned in seen_m:
                continue
            
            # 한식/아시안 식당 등 한글 메뉴 위주로 필터링
            has_korean = any("가" <= c <= "힣" for c in cleaned)
            if not has_korean:
                continue

            # '대표' 표기 여부 확인
            if "대표" in cleaned:
                rep_menus.append(cleaned)
            else:
                normal_menus.append(cleaned)
            seen_m.add(cleaned)

        # 대표 메뉴 먼저, 그 다음 일반 메뉴 순으로 최대 5개 구성
        menu_names = (rep_menus + normal_menus)[:5]
        
        # 만약 한글 필터링으로 검색 결과가 너무 적으면 원본에서 보충
        if len(menu_names) < 3 and all_raw_names:
            for n in all_raw_names:
                c = _strip_emoji(n).strip()
                if c and c not in seen_m:
                    menu_names.append(c)
                    seen_m.add(c)
                if len(menu_names) >= 5:
                    break

    negative_ratio = _extract_negative_ratio(ev)

    return {
        "place_id":          place_id,
        "name":              p.get("name"),
        "category":          p.get("category"),
        "road_address":      p.get("road_address"),
        "lat":               p.get("lat"),
        "lng":               p.get("lng"),
        "facility":          p.get("facility", {}),
        "review_count":      p.get("review_count_visitor", 0),
        "derived":           ev.get("derived", []),
        "negative_evidence": ev.get("negative_evidence", []),
        "negative_ratio":    negative_ratio,
        "menu_names":        menu_names,
        "business_hours":    h_text,
    }


async def _scroll_one(place_id: int, doc_type: str):
    """단일 place_id + doc_type 으로 Qdrant scroll (limit=1)"""
    results, _ = await clients.qdrant.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="doc_type",
                           match=MatchValue(value=doc_type)),
            FieldCondition(key="place_id",
                           match=MatchValue(value=place_id)),
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False
    )
    return results


def _strip_emoji(text: str) -> str:
    """메뉴명 등에서 이모티콘/특수문자 제거"""
    return re.sub(r'[^\w\s가-힣()·,./&%-]', '', text).strip()


def _extract_negative_ratio(ev: dict) -> float:
    """
    review_evidence payload 에서 부정 비율 추출

    computed_ratios 구조 예시:
        {"negative_ratio": 0.08}
    """
    computed = ev.get("computed_ratios", {})
    if not computed:
        return 0.0

    # 구조 1: {"negative": 0.08}
    if "negative" in computed:
        return float(computed["negative"])

    # 구조 2: {"negative_ratio": 0.08}
    if "negative_ratio" in computed:
        return float(computed["negative_ratio"])

    return 0.0


# ──────────────────────────────────────────
# 2. 프롬프트 포맷
# ──────────────────────────────────────────

# 영어 키워드 → 한국어 매핑
_KO_MAP = {
    "taste": "맛", "price": "가격", "service": "서비스",
    "waiting": "대기시간", "parking": "주차", "cleanliness": "청결", "clean": "청결",
    "quantity": "양", "location": "위치", "noise": "소음", "billing": "결제",
    "menu": "메뉴", "interior": "인테리어", "staff": "직원",
}

# 부정 키워드 → 자연스러운 문장 변환 매핑
_NEG_PHRASE_MAP = {
    "taste":       "맛에 대한 아쉬운 평가",
    "price":       "가격이 다소 비싸다는 의견",
    "service":     "서비스가 아쉽다는 불만",
    "waiting":     "대기시간이 길다는 평가",
    "parking":     "주차 공간이 부족하다는 의견",
    "cleanliness": "청결도가 아쉽다는 언급",
    "clean":       "매장 청결 상태에 대한 아쉬운 평가",
    "quantity":    "음식 양이 적다는 의견",
    "location":    "찾아가기 어렵다는 정보",
    "noise":       "매장이 다소 소란스럽다는 평가",
    "menu":        "메뉴 구성이 아쉽다는 의견",
    "interior":    "인테리어가 다소 노후되었다는 언급",
    "staff":       "직원의 응대가 아쉽다는 의견",
    "billing":     "계산 과정에 아쉽다는 의견",
    "bill":        "계산 과정에 아쉽다는 의견",
    "value":       "가성비가 아쉽다는 의견",
    "hygiene":     "위생 상태에 아쉽다는 의견",
    "atmosphere":  "분위기에 아쉽다는 의견",
    "portion":     "음식 양이 적다는 의견",
}


def _to_korean(keywords: list[str]) -> list[str]:
    return [_KO_MAP.get(k.lower(), k) for k in keywords]


def _neg_to_phrase(keywords: list[str]) -> str:
    if not keywords:
        return "특별한 부정적인 언급은 없어요."

    phrases = []
    for k in keywords:
        k_lower = k.lower()
        if k_lower in _NEG_PHRASE_MAP:
            phrases.append(_NEG_PHRASE_MAP[k_lower])
        else:
            phrases.append(f"{k}에 대한 아쉬운 평가")

    return ", ".join(phrases[:3]) + "가 있어요."


def format_details(details: list[dict]) -> str:
    """
    상세정보 리스트 → vLLM 프롬프트 주입용 텍스트 (문서별 정보 구분)
    """
    if not details:
        return ""

    lines = []
    for d in details:
        block = [f"### {d['name']} ###"]
        
        # profile 문서 기반 (편의시설, 기본 정보)
        facility = d.get("facility", {})
        f_info = []
        if facility.get("parking", {}).get("available"):
            f_info.append(f"주차: {facility['parking'].get('desc', '가능')}")
        if facility.get("room", {}).get("available"):
            f_info.append(f"룸: {facility['room'].get('desc', '가능')}")
        if facility.get("reservation", {}).get("available"):
            f_info.append(f"예약: {facility['reservation'].get('desc', '가능')}")
        if facility.get("group", {}).get("available"):
            f_info.append(f"단체석: {facility['group'].get('desc', '가능')}")
        
        block.append("[profile 문서]")
        block.append(f"카테고리: {d.get('category', '-')}")
        block.append(f"주소: {d.get('road_address', '-')}")
        
        # 편의시설 개별 항목으로 추가
        if f_info:
            for item in f_info:
                block.append(f"- {item}")
        else:
            block.append("- 편의시설 정보 없음")
        
        # menu 문서 기반
        menu_names = d.get("menu_names", [])
        block.append("\n[menu 문서]")
        block.append(f"주요 메뉴: {', '.join(menu_names) if menu_names else '메뉴 정보 없음'}")
        
        # hours 문서 기반
        block.append("\n[hours 문서]")
        block.append(f"영업시간:\n{d.get('business_hours', '정보 없음')}")
        
        # review_evidence 문서 기반
        derived = d.get("derived", [])
        block.append("\n[review_evidence 문서]")
        block.append(f"특징 요약: {', '.join(derived) if derived else '특징 정보 없음'}")
        if d.get("negative_ratio") > 0:
            block.append(f"부정 리뷰 비율: {d.get('negative_ratio')*100:.1f}%")
        
        lines.append("\n".join(block))

    return "\n\n".join(lines)


# ──────────────────────────────────────────
# 3. 답변 생성
# ──────────────────────────────────────────

async def generate_answer(query: str, details: list[dict], history: list[dict] = None) -> str:
    """
    상세정보 + 질문 → vLLM → 자연어 추천 답변
    """
    from app.llm.vllm_client import chat
    from app.llm.prompts import get_answer_generation_messages

    if not details:
        return "조건에 맞는 식당을 찾지 못했어요. 조건을 조금 완화해보세요."

    # 최대 3개만 사용하도록 제한
    details = details[:3]
    details_text = format_details(details)
    
    # 히스토리 요약 구성 (있는 경우)
    history_summary = ""
    if history:
        for h in history[-4:]:
            role_label = "질문" if h["role"] == "user" else "나의 이전 답변"
            history_summary += f"{role_label}: {h['content'][:150]}\n"

    messages = get_answer_generation_messages(query, details_text, history_summary or None)
    return await chat(messages, max_tokens=300)