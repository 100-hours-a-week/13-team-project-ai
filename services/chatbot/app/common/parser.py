import re
import json
from app.core.config import settings
from app.llm.vllm_client import chat
from app.llm.prompts import get_condition_extraction_prompt

# ──────────────────────────────────────────
# 1. 상수 및 정규식 정의
# ──────────────────────────────────────────

# 서비스 지역
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
PRONOUNS = {"거기", "그곳", "이곳", "여기", "그게", "그집", "세곳", "세 곳", "모두", "전부", "둘 다", "셋 다"}
FORBIDDEN_WORDS = {
    "영업", "운영", "주차", "추천", "지금", "오늘", "내일", "있는", "가기", "좋은", "의", 
    "맛집", "곳", "기능", "있어", "여부", "예약", "룸", "단체", "메뉴", "정보", 
    "위치", "전화번호", "시간", "번호", "한식당", "중식당", "일식당", "양식당", "고깃집", "고기집",
    "모두", "전부", "세곳", "둘 다", "셋 다", "전체", "군데", "첫 번째", "두 번째", "세 번째", "첫번째", "두번째", "세번째", "가운데", "안내"
}

# 식당 이름 추출 패턴
RESTAURANT_BRANCH_PATTERN = re.compile(
    r"([가-힣0-9a-zA-Z ]{1,15}(?:판교점|수내점|삼평점|백현점|분당점|본점|식당|레스토랑|바|카페))"
)
# 키워드 앞에 나오는 명칭을 캡처하되, 키워드가 명칭의 일부로 흡수되는 것을 방지하기 위해 
# 키워드 목록을 긴 것부터 배치하고, 캡처된 이름의 끝 부분을 사후 처리(strip)함.
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
            if isinstance(v, str):
                name = v.strip().strip("[]").strip()
                # LLM이 추출한 이름에 대해서도 금지어 포함 여부 재검증
                checked_names = extract_restaurant_names(name)
                result[k] = checked_names[0] if checked_names else name
                
                # 재검증 후에도 마지막에 운영/영업 등이 붙어있는지 한 번 더 확인
                for fw in ["운영", "영업", "시간", "정보"]:
                    if result[k].endswith(fw):
                        result[k] = result[k][:-len(fw)].strip()
            else:
                result[k] = None
        elif k == "category" and v:
            v_clean = v.replace(" ", "")
            result[k] = CAT_MAP.get(v_clean, v_clean)
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
    name_candidates = extract_restaurant_names(query)
    if name_candidates:
        result["name_query"] = name_candidates[0]
    for r in REGIONS:
        if r in query:
            result["region"] = r
            break
    if "주차" in query: result["parking"] = True
    if "룸" in query: result["room"] = True
    return result

def extract_restaurant_names(query: str) -> list[str]:
    """쿼리에서 모든 식당 이름 후보 추출"""
    query = re.sub(r'[\r\n\t]+', ' ', query).strip()
    names = []
    
    # 0.5. 추천 목록 패턴 (1. 식당명)
    for m_list in re.finditer(r"\d\.\s*([가-힣a-zA-Z0-9 ]{1,15})(?:\s|\n|$)", query):
        lname = m_list.group(1).strip()
        if lname and lname not in names and lname not in FORBIDDEN_WORDS:
            names.append(lname)

    # 1. 지점명 포함
    for m in RESTAURANT_BRANCH_PATTERN.finditer(query):
        candidate = m.group(1).strip()
        if candidate not in names and candidate not in PRONOUNS:
            names.append(candidate)

    # 2. 정보 키워드 패턴 (greedy matching 방어 포함)
    for m_info in RESTAURANT_INFO_PATTERN.finditer(query):
        raw_name = m_info.group(1).strip()
        
        # [핵심 수정] 추출된 이름의 끝부분이 금지어(운영, 영업, 시간 등)에 포함되면 반복해서 제거
        # 예: "스시 하루쿠 운영시간" -> "스시 하루쿠 운영" (Group 1) -> "스시 하루쿠" (Stripped)
        temp_name = raw_name
        while True:
            parts = temp_name.split()
            if not parts: break
            if parts[-1] in FORBIDDEN_WORDS:
                temp_name = " ".join(parts[:-1]).strip()
            else:
                break
        raw_name = temp_name
        
        if raw_name and raw_name not in names and raw_name not in PRONOUNS:
            names.append(raw_name)

    return names

async def extract_accumulated_conditions(query: str, current_time: str, history: list) -> dict:
    """LLM을 사용하여 대화 내역으로부터 누적 검색 조건 추출"""
    prompt = get_condition_extraction_prompt(query, current_time, history)
    try:
        raw = await chat([{"role": "user", "content": prompt}], max_tokens=200)
        cleaned = re.sub(r"```[a-z]*", "", raw).strip().strip("`").strip()
        result = json.loads(cleaned)
        return validate_conditions(result)
    except Exception:
        return rule_based_parse(query)
