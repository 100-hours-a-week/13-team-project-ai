import re

from app.common.parser import RESTAURANT_INFO_PATTERN, PRONOUNS

def _is_specific_restaurant_query(query: str) -> bool:
    """'특정 식당명 + 특정 요청' 패턴 감지 → multistep 유도"""
    # 문맥 의존적인 질문은 특정 식당 문의로 보지 않음
    if any(k in query for k in ["이 중", "이중에", "여기서", "둘 중", "셋 중", "세곳", "비교", "거기", "그곳"]):
        return False
        
    match = RESTAURANT_INFO_PATTERN.search(query)
    if match:
        name = match.group(1).strip()
        # "거기", "그곳" 등 지칭어거나 "운영시간" 등 금지어면 특정 식당 검색이 아닌 멀티턴으로 유도해야 함
        from app.common.parser import FORBIDDEN_WORDS
        if name in PRONOUNS or name in FORBIDDEN_WORDS:
            return False
        return True
    return False

def classify_query(query: str, history: list = None) -> str:
    """
    쿼리를 3가지 파이프라인 중 하나로 분류:
    1. multiturn: 대화 기록이 있고, 지칭어(거기, 그곳, 아까 등)가 있거나 조건 추가/변경인 경우
    2. hyde: 분위기, 주관적 표현, 모호한 질문인 경우 (리뷰 검색 필요)
    3. multistep: 명확한 지역, 메뉴, 편의시설 조건이 있는 경우 (필터링 중심)
    """
    history = history or []
    history_len = len(history)

    # 0. 특정 식당 직접 문의 (어떤 파이프라인보다 우선)
    if _is_specific_restaurant_query(query):
        return "multistep"

    # 1. 주관적 키워드 판별
    subjective_keywords = [
        "분위기", "감성", "조용한", "깔끔한", "친절한", "데이트", "소개팅", "회식하기 좋은",
        "부모님", "품격", "고급", "맛있는", "유명하게", "가기 좋은", "추천해", "어디가 좋아"
    ]
    is_subjective = any(k in query for k in subjective_keywords)

    # 2. 멀티턴 판단 (기록이 있을 때만)
    if history_len > 0:
        # 명시적 비교/지칭 키워드가 있으면 가장 먼저 멀티턴으로 연결
        comparative_context = [
            "이 중", "이중에", "여기서", "둘 중", "셋 중", "세곳", "세 곳", "네곳", "네 곳",
            "비교", "거기", "그곳", "아까", "다시", "더", "말고", "다른", "방금", "중에서",
            "모두", "전부", "셋 다", "둘 다", "전부 다", "첫 번째", "두 번째", "세 번째"
        ]
        if any(k in query for k in comparative_context) or bool(re.search(r"\d+\s*(명|인)", query)):
            return "multiturn"

        # 주관적인 질문이지만 강력한 지칭어가 없는 경우는 HyDE를 통해 리뷰 검색 유도 (더 풍부한 결과)
        if is_subjective:
            return "hyde"

        # 그 외 연결어 ("추천", "알려줘" 등) 및 질문형 종결어는 멀티턴 유지
        context_keywords = [
            "추천", "보여줘", "알려줘", "어때", "들어가", "가능해", "돼?", "있는", 
            "있어", "있니", "있나요", "어때요", "정보", "예약", "전화", "위치"
        ]
        if any(k in query for k in context_keywords):
            return "multiturn"
            
        # 시설 관련 키워드가 포함되었으나 지역정보가 없는 경우도 대개 멀티턴 (이전 식당에 대한 질문)
        facility_keywords = ["주차", "룸", "단체", "화장실", "발렛", "아기의자"]
        if any(k in query for k in facility_keywords) and not bool(re.search(r"[가-힣]{2,}(동|역|구|시)", query)) and not _is_specific_restaurant_query(query):
            return "multiturn"

    # 3. HyDE 판단 (단일 턴 주관적 쿼리)
    if is_subjective:
        return "hyde"

    # 4. 기본값: multistep
    return "multistep"
