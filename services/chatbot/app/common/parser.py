import re
import json
from app.core.config import settings

# ──────────────────────────────────────────
# 1. 상수 및 정규식 정의
# ──────────────────────────────────────────

# 서비스 지역 (retriever.py 등과 동기화 필요 시 여기서 중앙 관리도 가능)
REGIONS = ["백현동", "수내동", "삼평동", "판교", "수내", "삼평", "백현", "분당"]

# DB 카테고리 매핑
CAT_MAP = {
    "한식당": "한식", "중식당": "중식", "일식당": "일식", "양식당": "양식",
    "고깃집": "고기", "고기집": "고기", "횟집": "해산물", "카페": "기타"
}

# 메뉴 키워드 → 카테고리 매핑
MENU_CATEGORY_MAP = {
    "삼겹살": "고기", "갈비": "고기", "곱창": "고기", "양꼬치": "고기",
    "매운탕": "해산물", "회": "해산물", "초밥": "일식",
    "파스타": "양식", "스테이크": "양식",
    "마라탕": "아시안", "쌀국수": "아시안", "치킨": "기타", "피자": "양식"
}

# 지칭어 및 일반 단어 제외
PRONOUNS = {"거기", "그곳", "이곳", "여기", "그게", "그집"}
FORBIDDEN_WORDS = {
    "영업", "운영", "주차", "추천", "지금", "오늘", "내일", "있는", "가능한", "의", 
    "맛집", "곳", "기능", "있어", "여부", "예약", "룸", "단체", "메뉴", "정보", 
    "위치", "전화번호", "시간", "번호"
}

# 식당 이름 추출 패턴
# 1. 지점명이 포함된 경우 (예: "연경 수내점")
RESTAURANT_BRANCH_PATTERN = re.compile(
    r"([가-힣0-9a-zA-Z ]{1,15}(?:판교점|수내점|삼평점|백현점|분당점|본점|식당|레스토랑|바|카페))"
)
# 2. 상태/정보 키워드가 뒤따르는 경우 (예: "서현궁 영업중")
RESTAURANT_INFO_PATTERN = re.compile(
    r"([가-힣a-zA-Z0-9 ]{1,15})\s*(?:의\s*)?(?:운영시간|영업시간|영업중|영업여부|영업하나|영업|운영|오픈|문\s*열었|메뉴|정보|위치|전화번호|시간|번호|예약|룸|주차|단체)"
)

# ──────────────────────────────────────────
# 2. 파싱 유틸리티
# ──────────────────────────────────────────

def validate_conditions(raw: dict) -> dict:
    """JSON 파싱된 결과의 타입과 기본값 보정"""
    defaults = {
        "region": None,
        "category": None,
        "radius_m": settings.DEFAULT_RADIUS_M,
        "capacity_min": None,
        "parking": False,
        "room": False,
        "open_now": False,
        "menu_query": None,
        "name_query": None,
        "requested_count": None,
    }
    result = {}
    for k, default in defaults.items():
        v = raw.get(k, default)
        if k in ("parking", "room", "open_now"):
            result[k] = bool(v) if not isinstance(v, bool) else v
        elif k == "radius_m":
            result[k] = int(v) if v else settings.DEFAULT_RADIUS_M
        elif k == "capacity_min":
            result[k] = int(v) if v else None
        elif k == "name_query" and v:
            result[k] = v.strip().strip("[]").strip()
        elif k == "category" and v:
            v_clean = v.replace(" ", "")
            result[k] = CAT_MAP.get(v_clean, v_clean)
            if result[k] == "기타" and "카페" in v_clean and not result.get("menu_query"):
                result["menu_query"] = "카페"
        else:
            result[k] = v if v else None
    return result


def rule_based_parse(query: str) -> dict:
    """LLM 실패 시 또는 간단한 키워드 매칭을 위한 규칙 기반 파싱"""
    result = {
        "region": None, "category": None, "radius_m": settings.DEFAULT_RADIUS_M,
        "capacity_min": None, "parking": False, "room": False,
        "open_now": False, "menu_query": None, "name_query": None,
        "requested_count": None,
    }
    
    # 1. 특정 식당 이름 추출
    name_candidates = extract_restaurant_names(query)
    if name_candidates:
        result["name_query"] = name_candidates[0]

    # 2. 지역 추출
    for r in REGIONS:
        if r in query:
            result["region"] = r
            break
    
    # 3. 인원
    m = re.search(r"(\d+)\s*(명|인)", query)
    if m:
        result["capacity_min"] = int(m.group(1))
    
    # 4. 시설
    if "주차" in query: result["parking"] = True
    if "룸"   in query: result["room"]    = True
    if any(k in query for k in ["지금", "현재", "오픈", "영업중", "열었"]):
        result["open_now"] = True
    
    # 5. 추천 갯수
    count_match = re.search(r"(\d+)\s*(?:곳|군데|개|번)", query)
    if count_match:
        result["requested_count"] = int(count_match.group(1))
    
    # 6. 메뉴 및 카테고리
    for menu, cat in MENU_CATEGORY_MAP.items():
        if menu in query:
            result["menu_query"] = menu
            result["category"]   = cat
            break
            
    if not result["category"]:
        mapping = {
            "고기": ["고기", "고기집"], "일식": ["일식", "일식당"],
            "양식": ["양식", "양식당"], "중식": ["중식", "중식당"],
            "분식": ["분식"], "해산물": ["해산물", "횟집"],
            "한식": ["한식", "한식당"], "아시안": ["아시안"], "기타": ["카페"]
        }
        for cat, keywords in mapping.items():
            if any(k in query for k in keywords):
                result["category"] = cat
                break
                
    return result


def extract_restaurant_names(query: str) -> list[str]:
    """쿼리에서 식당 이름 후보 추출"""
    names = []
    
    # 1. 지점명이 포함된 경우
    m = RESTAURANT_BRANCH_PATTERN.search(query)
    if m:
        candidate = m.group(1).strip()
        if candidate not in PRONOUNS and not any(word in candidate for word in ["있는", "가능한", "좋은", "맛있는", "추천"]):
            names.append(candidate)
            # 지점명 제거한 베이스 이름도 후보로 추가
            base = re.sub(r"\s*(?:판교점|수내점|삼평점|백현점|분당점|본점|식당|레스토랑|바|카페)$", "", candidate).strip()
            if base and base not in names and base not in PRONOUNS and base not in FORBIDDEN_WORDS:
                names.append(base)

    # 2. 상태/정보 키워드 패턴
    m_info = RESTAURANT_INFO_PATTERN.search(query)
    if m_info:
        raw_name = m_info.group(1).strip()
        clean_name = raw_name
        # 지역명 제거
        for r in REGIONS:
            clean_name = re.sub(rf"^{r}\s*", "", clean_name).strip()
            
        # 금칙어 제거
        clean_name = re.sub(rf"\s*(?:{'|'.join(FORBIDDEN_WORDS)})$", "", clean_name).strip()
        # 조사 제거
        clean_name = re.sub(rf"(?:은|는|이|가|을|를)$", "", clean_name).strip()

        if clean_name and clean_name not in PRONOUNS and clean_name not in names:
            names.append(clean_name)
            
    return names
