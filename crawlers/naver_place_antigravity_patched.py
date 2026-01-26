import csv
import json
import re
import time
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from db_manager import DBManager


# =========================
# Logging & Utils
# =========================
def log_skipped_category(place_id: str, name: str, category: str, reason: str):
    """
    Logs category and reason of skipped entries to skipped_categories.csv
    """
    file_path = "skipped_categories.csv"
    file_exists = os.path.isfile(file_path)
    with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["place_id", "name", "category", "reason", "timestamp"])
        writer.writerow([place_id, name, category, reason, time.strftime("%Y-%m-%d %H:%M:%S")])


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("naver_antigravity")


# =========================
# Utils
# =========================
def safe_text(el) -> str:
    try:
        return (el.text or "").strip()
    except Exception:
        return ""


def only_digits(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)", text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def click_js(driver, el) -> bool:
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False


def try_find(driver, by, sel, timeout=2):
    try:
        return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, sel)))
    except TimeoutException:
        return None


def try_find_all(driver, by, sel, timeout=2):
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, sel)))
        return driver.find_elements(by, sel)
    except TimeoutException:
        return []


def click_any(driver, candidates: List[Tuple[str, str]], timeout=2) -> bool:
    """
    candidates: [(By.CSS_SELECTOR, "...."), (By.XPATH, "...."), ...]
    """
    for by, sel in candidates:
        el = try_find(driver, by, sel, timeout=timeout)
        if el and click_js(driver, el):
            return True
    return False


def switch_to_entry_iframe(driver, timeout=12) -> bool:
    """
    Naver Map place entry content is usually inside entryIframe.
    """
    try:
        driver.switch_to.default_content()
        iframe = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "entryIframe"))
        )
        driver.switch_to.frame(iframe)
        return True
    except TimeoutException:
        return False


# =========================
# Anti-bot / Reliability helpers
# =========================
ERROR_TEXTS = [
    "일시적으로 정보를 불러올 수 없습니다",
    "잠시 후 다시",
    "요청이 처리되지",
]

def human_sleep(a: float = 0.8, b: float = 1.6):
    """Random-ish sleep to avoid ultra-regular bot patterns."""
    import random
    time.sleep(random.uniform(a, b))

def page_has_temp_error(driver) -> bool:
    """Detect Naver soft-block / temporary error screens by text."""
    try:
        src = driver.page_source or ""
        return any(t in src for t in ERROR_TEXTS)
    except Exception:
        return False


def is_entry_page_loaded(driver) -> bool:
    """Return True if the place 'entry' page looks 정상(loaded).
    We prefer POSITIVE signals (tabs/title elements) to avoid false TEMP BLOCK.
    """
    # We are usually inside entryIframe already.
    positive_selectors = [
        (By.TAG_NAME, "h1"),                 # place title (often present)
        (By.CSS_SELECTOR, 'button[role="tab"]'),  # tabs: 정보/메뉴/리뷰
        (By.CSS_SELECTOR, 'a[href^="tel:"]'),      # tel link (sometimes)
    ]
    for by, sel in positive_selectors:
        try:
            el = driver.find_element(by, sel)
            # sometimes h1 exists but empty; still a good signal
            if el is not None:
                return True
        except Exception:
            continue
    return False


def is_real_temp_block(driver) -> bool:
    """Return True only when we likely see ONLY the error screen (soft-block)."""
    try:
        src = driver.page_source or ""
    except Exception:
        return False

    # If 정상 요소가 하나라도 있으면, 에러 텍스트가 남아있어도 정상으로 취급
    try:
        if is_entry_page_loaded(driver):
            return False
    except Exception:
        pass

    return any(t in src for t in ERROR_TEXTS)

def add_stealth_scripts(driver):
    """Minimize easy automation fingerprints (best-effort)."""
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"}
        )
    except Exception:
        pass





# =========================
# Categorization & Normalization
# =========================
def check_if_restaurant(category: str) -> bool:
    """
    1st Filter: Discard if it's a non-restaurant category.
    """
    if not category: 
        return True # Don't skip yet, maybe LLM can decide based on name
    c = category.strip()
    
    # 1st Filter Keywords (Non-restaurant)
    non_res_kws = [
        "카페", "디저트", "베이커리", "커피", "빵집", "케이크", "샌드위치", "도넛", "빙수",
        "골프", "의류", "패션", "네일", "미용", "학원", "편의점", "마트", "정육점",
        "사무실", "부동산", "병원", "약국", "서점", "꽃집", "주차장",
        "웨어", "스포츠용품", "백화점", "쇼핑", "수선", "의복", "영화관",
        "브런치", "베이글", "반찬가게"
    ]
    if any(x in c for x in non_res_kws):
        return False
    return True

def categorize_restaurant(naver_category: str) -> tuple:
    """
    2nd Classification: Rule-based 9 categories mapping.
    Returns (category_name, is_definite)
    """
    if not naver_category:
        return "기타", False
        
    c = naver_category.strip()
    
    # 1. Definite Mapping (Strict)
    # 한식
    if any(x in c for x in ["백반", "가정식", "해장국", "설렁탕", "곰탕", "갈비탕", "김치찌개", "된장찌개", "부대찌개", "한정식", "국밥", "냉면", "삼계탕", "백숙", "오리", "찌개", "막국수", "칼국수", "국수", "면요리", "두부", "전골", "순두부", "샤브샤브", "순대국", "순댓국", "보리밥", "닭요리", "찜닭"]): 
        return "한식", True
    # 일식
    if any(x in c for x in ["초밥", "스시", "카츠", "돈가스", "돈까스", "텐동", "오마카세", "라멘", "우동", "소바", "이자카야", "라면", "덮밥", "오뎅", "야키토리", "오니기리"]): 
        return "일식", True
    # 중식
    if any(x in c for x in ["짜장", "짬뽕", "탕수육", "마라탕", "마라샹궈", "훠궈", "중식당"]): 
        return "중식", True
    # 양식
    if any(x in c for x in ["파스타", "피자", "리조또", "스테이크", "이탈리아", "버거", "스페인", "파에야", "타파스",
    "멕시코", "퀘사디아", "나초", "남미"]): 
        return "양식", True
    # 아시안
    if any(x in c for x in ["쌀국수", "팟타이", "커리", "카레", "케밥", "타코", "부리또", "아시아", "태국", "베트남", "인도음식"]): 
        return "아시안", True
    # 분식
    if any(x in c for x in ["떡볶이", "순대", "튀김", "김밥"]): 
        return "분식", True
    # 고기
    if any(x in c for x in ["삼겹살", "목살", "갈비", "한우", "곱창", "막창", "대창", "양꼬치", "구이", "양갈비", "족발", "보쌈", "정육식당"]): 
        return "고기", True
    # 해산물
    if any(x in c for x in ["사시미", "조개구이", "킹크랩", "횟집", "생선회", "매운탕", "오징어", "낙지", "쭈꾸미", "주꾸미", "코다리", "명태", "아구", "찜", "게장", "전복", "문어", "장어", "게요리", "조개요리", "생선구이", "굴요리"]): 
        return "해산물", True
    # 기타 (Definite)
    if any(x in c for x in ["치킨", "호프", "맥주", "술집", "포장마차", "와인바", "뷔페", "요리주점", "BAR", "바(BAR)"]): 
        return "기타", True

    # 2. General Mapping (Fallback)
    if "한식" in c: return "한식", True
    if "일식" in c: return "일식", True
    if "중식" in c: return "중식", True
    if "양식" in c: return "양식", True
    if "아시아" in c: return "아시안", True
    if "분식" in c: return "분식", True
    if "고기" in c: return "고기", True
    if "육류" in c: return "고기", True
    if "해물" in c: return "해산물", True
    if "해산물" in c: return "해산물" , True
    
    return "기타", False # Ambiguous case -> fallback to LLM



# =========================
# Parsers
# =========================
def parse_address_layer(text: str) -> Dict[str, Optional[str]]:
    """
    Extract road/jibun/zip from address layer text (heuristic).
    """
    text = text.replace("복사", "").replace("안내", "")
    
    road = None
    jibun = None
    zipcd = None

    m_zip = re.search(r"우편번호[:\s]*(\d{5})", text)
    if not m_zip:
        # fallback just find 5 digits
        m_zip = re.search(r"\b(\d{5})\b", text)
    
    if m_zip:
        zipcd = m_zip.group(1)

    # label-based
    m_road = re.search(r"도로명\s*([^\n]+)", text)
    if m_road:
        road = m_road.group(1).strip()

    m_jibun = re.search(r"지번\s*([^\n]+)", text)
    if m_jibun:
        jibun = m_jibun.group(1).strip()

    # fallback: line heuristics
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not road:
        for ln in lines:
            if re.search(r"(로|길)\d*", ln) and not re.search(r"도로명|지번|우편번호", ln):
                road = ln
                break
    if not jibun:
        for ln in lines:
            # typical jibun has "동/가/리" + digits
            if re.search(r"(동|가|리)", ln) and re.search(r"\d", ln) and not re.search(r"도로명|지번|우편번호", ln):
                jibun = ln
                break

    # Raw cleanup
    clean_lines = []
    keywords = ["도로명", "지번", "우편번호", "주소"]
    for ln in lines:
        if any(k in ln for k in keywords) or re.search(r"(로|길)\d*", ln) or re.search(r"(동|가|리)\s*\d*", ln):
             if "레이어 닫기" in ln or "복사" == ln:
                 continue
             clean_lines.append(ln)
    cleaned_raw = "\n".join(clean_lines) if clean_lines else text.strip()

    return {"road_address": road, "jibun_address": jibun, "zipcd": zipcd}



# =========================
# Output schema
# =========================
@dataclass
class PlaceRecord:
    place_id: str

    name: Optional[str] = None
    category: Optional[str] = None

    visitor_reviews: Optional[int] = None
    blog_reviews: Optional[int] = None

    road_address: Optional[str] = None
    jibun_address: Optional[str] = None
    zipcd: Optional[str] = None
    
    # Coordinates
    lat: Optional[str] = None
    lng: Optional[str] = None

    phone: Optional[str] = None
    images: Optional[str] = None  # JSON list of up to 3 image URLs


# =========================
# Crawler (Anti-gravity Plan Executor)
# =========================
class AntiGravityNaverPlaceCrawler:
    def __init__(self, headless=True, timeout=12):
        self.timeout = timeout
        self._headless = headless
        self.driver = self._new_driver(headless=headless)
        self.wait = WebDriverWait(self.driver, timeout)

    def _new_driver(self, headless: bool):
        options = webdriver.ChromeOptions()

        # ⚠️ Naver Map is very sensitive to headless automation.
        # If you can, keep headless=False for stability.
        if headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1380,900")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        options.add_argument(
            "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.set_page_load_timeout(30)

        # Best-effort stealth: remove navigator.webdriver
        add_stealth_scripts(driver)
        return driver

    def _restart_driver(self):
        try:
            self.driver.quit()
        except Exception:
            pass
        human_sleep(1.5, 2.5)
        self.driver = self._new_driver(headless=self._headless)
        self.wait = WebDriverWait(self.driver, self.timeout)

    def _safe_get(self, url: str, tries: int = 4) -> bool:
        """Open URL with backoff, detecting Naver's temporary/soft-block pages."""
        import random
        for i in range(tries):
            try:
                self.driver.get(url)
                human_sleep(2.2, 3.4)
            except Exception as e:
                log.warning(f"[GET WARN] {e}")

            # small human-like actions
            try:
                self.driver.execute_script("window.scrollTo(0, 300);")
                human_sleep(0.4, 0.9)
                self.driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass

            if not page_has_temp_error(self.driver):
                return True

            sleep_s = min((2 ** i) + random.uniform(0.6, 1.6), 20)
            log.warning(f"[TEMP BLOCK] {url} (try {i+1}/{tries}) -> sleep {sleep_s:.1f}s")
            time.sleep(sleep_s)

            # sometimes refresh keeps the soft-block; hard re-get helps
            try:
                self.driver.get("https://map.naver.com/p")
                human_sleep(1.2, 2.0)
            except Exception:
                pass

        return False

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def search_and_get_place_id(self, name: str, address: str) -> Optional[str]:
        """
        Search for ONLY the name on Naver Map and return the first result's ID.
        """
        search_query = name
        
        url = f"https://map.naver.com/p/search/{search_query}"
        log.info(f"[SEARCH] Searching for: {search_query}")
        
        try:
            if not self._safe_get(url, tries=4):
                return None
            
            # Step A: Check for searchIframe (Multi-result case)
            self.driver.switch_to.default_content()
            try:
                search_iframe = WebDriverWait(self.driver, 7).until(
                    EC.presence_of_element_located((By.ID, "searchIframe"))
                )
                if search_iframe:
                    self.driver.switch_to.frame(search_iframe)
                    
                    # 1. Try to find ID from data-id first
                    items = self.driver.find_elements(By.CSS_SELECTOR, "li[data-id], [data-id]")
                    if items:
                        data_id = items[0].get_attribute("data-id")
                        if data_id and data_id.isdigit():
                            # We found the ID, but we also need to click it to open entryIframe for details
                            try:
                                click_target = items[0].find_element(By.CSS_SELECTOR, ".place_bluelink, .typo_title")
                                click_js(self.driver, click_target)
                                time.sleep(1.5)
                            except:
                                pass
                            log.info(f"[SEARCH] Found ID {data_id} in list for {name}")
                            return data_id
                    
                    # 2. Fallback: Click the first .place_bluelink if data-id failed or list is different
                    first_link = try_find(self.driver, By.CSS_SELECTOR, ".place_bluelink")
                    if first_link:
                        click_js(self.driver, first_link)
                        time.sleep(2.0)
                        # After clicking, we might need to get the ID from the URL or entryIframe
            except TimeoutException:
                pass

            # Step B: Check for entryIframe (Direct match or after clicking result)
            self.driver.switch_to.default_content()
            try:
                entry_iframe = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "entryIframe"))
                )
                if entry_iframe:
                    current_url = self.driver.current_url
                    match = re.search(r"/place/(\d+)", current_url)
                    if match:
                        rescode = match.group(1)
                        log.info(f"[SEARCH] Found ID {rescode} via entryIframe")
                        return rescode
            except TimeoutException:
                pass

        except Exception as e:
            log.error(f"[SEARCH ERR] {name}: {e}")

        log.warning(f"[SEARCH FAIL] No ID found for {name}")
        return None

    # ---- step helpers ----
    def _read_name(self) -> Optional[str]:
        # Candidate selectors for name (changes often)
        candidates = [
            (By.CSS_SELECTOR, ".GHAhO"),    # 2024 new verified
            (By.CSS_SELECTOR, ".bh9OH"),    # 2024 new
            (By.CSS_SELECTOR, ".GHAoO"),    # 2024 common
            (By.CSS_SELECTOR, ".FcngH"),
            (By.CSS_SELECTOR, "span.Fc1rA"),
            (By.CSS_SELECTOR, "h1"),
        ]
        for by, sel in candidates:
            el = try_find(self.driver, by, sel, timeout=2)
            if el:
                t = safe_text(el)
                if t:
                    return t
        return None

    def _read_category(self) -> Optional[str]:
        # Category is usually near name; use broad scan fallback
        # 1) try a few common spots
        candidates = [
            (By.CSS_SELECTOR, "span.DJJvD"),
            (By.CSS_SELECTOR, "span.lnJFt"),
            (By.XPATH, "//*[contains(@class,'DJJvD') or contains(@class,'lnJFt')]"),
        ]
        for by, sel in candidates:
            el = try_find(self.driver, by, sel, timeout=2)
            if el:
                t = safe_text(el)
                if t and "별점" not in t:
                    return t

        # 2) fallback: scan small spans near top, choose something that looks like category
        spans = self.driver.find_elements(By.CSS_SELECTOR, "span")
        for el in spans[:250]:
            t = safe_text(el)
            # heuristic: category often ends with "집", "식당", "카페", or is short-ish
            if 1 <= len(t) <= 25 and any(k in t for k in ["음식점", "카페", "술집", "식당", "레스토랑", "베이커리"]):
                return t
        return None

    def _read_reviews(self) -> Tuple[Optional[int], Optional[int]]:
        visitor = None
        blog = None

        # 1) Direct selector for visitor reviews (robust)
        try:
             # usually a link with href containing /review/visitor
             v_el = self.driver.find_element(By.CSS_SELECTOR, 'a[href*="/review/visitor"]')
             if v_el:
                 visitor = only_digits(safe_text(v_el))
        except Exception:
             pass

        # 2) Scan for blog reviews or fallback visitor
        elems = self.driver.find_elements(By.CSS_SELECTOR, "a, span")
        for el in elems[:600]:
            t = safe_text(el)
            if not t:
                continue
            if visitor is None and "방문자 리뷰" in t:
                visitor = only_digits(t)
            if blog is None and "블로그 리뷰" in t:
                blog = only_digits(t)
            if visitor is not None and blog is not None:
                break
        return visitor, blog

    def _click_and_read_address_layer(self) -> Dict[str, Optional[str]]:
        # Prioritize known selectors with retry
        target_selectors = [".PkgBl", ".LDgIH", "a.PkgBl"]
        start_time = time.time()
        opened = False
        
        while time.time() - start_time < 3:
            for sel in target_selectors:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if btn:
                        click_js(self.driver, btn)
                        opened = True
                        break
                except Exception:
                    pass
            if opened:
                break
            time.sleep(0.3)

        if not opened:
            # Fallback
            opened = click_any(
                self.driver,
                candidates=[
                    (By.XPATH, "//*[contains(text(),'주소')]/ancestor::a[1]"),
                    (By.CSS_SELECTOR, "div.O8qbU a"),
                    # New: check for hidden text
                    (By.XPATH, "//*[contains(@class, 'place_blind') and contains(text(), '주소')]/ancestor::a"),
                ],
                timeout=1
            )

        if not opened:
            return {"road_address": None, "jibun_address": None, "zipcd": None, "address_raw": None}

        time.sleep(1.0) # wait for layer

        # Find the text block
        # subagent found div.nQ7Lh often contains the details
        blocks = self.driver.find_elements(By.CSS_SELECTOR, "div.nQ7Lh, div.c3L0b, div, span")
        best = None
        best_score = -1
        
        for b in blocks[:800]:
            it = safe_text(b)
            if len(it) < 5 or len(it) > 300:
                continue
            
            score = 0
            if "도로명" in it: score += 5
            if "지번" in it: score += 5
            if "우편번호" in it: score += 5
            if "복사" in it: score += 1
            
            if score > best_score:
                best = it
                best_score = score

        if not best:
            return {"road_address": None, "jibun_address": None, "zipcd": None, "address_raw": None}

        parsed = parse_address_layer(best)
        return parsed

    def _read_images(self) -> List[str]:
        # Extract images from top gallery
        # Selector found: .QX0J7 img  or  .CB8hP img
        # We want to skip the 1st, then take next 3.
        
        candidates = [
            (By.CSS_SELECTOR, ".QX0J7 img"),
            (By.CSS_SELECTOR, ".CB8hP img"),
            (By.CSS_SELECTOR, ".K00vp img"),
        ]
        
        found_urls = []
        for by, sel in candidates:
            try:
                imgs = self.driver.find_elements(by, sel)
                if imgs:
                    for img in imgs:
                        src = img.get_attribute("src")
                        if src and "http" in src:
                            # Strip Naver Proxy if present to get original URL
                            if "search.pstatic.net" in src and "src=" in src:
                                try:
                                    from urllib.parse import urlparse, parse_qs, unquote
                                    parsed = urlparse(src)
                                    src = unquote(parse_qs(parsed.query).get("src", [src])[0])
                                except:
                                    pass
                            found_urls.append(src)
                    if found_urls:
                        break
            except Exception:
                pass
        
        if not found_urls:
            return []
            
        # Unique them
        unique_urls = []
        seen = set()
        for u in found_urls:
            if u not in seen:
                unique_urls.append(u)
                seen.add(u)
        
        # Skip first, take next 3
        if len(unique_urls) > 1:
            target_urls = unique_urls[1:4] # indices 1, 2, 3
        else:
            target_urls = []
            
        return target_urls



    def _extract_coordinates(self, place_id: str) -> Dict[str, Optional[str]]:
        # Usually in entryIframe context
        # Try finding coordinates in window.__APOLLO_STATE__
        try:
            # We iterate keys because sometimes the ID format in the key might differ slightly
            script = """
            const state = window.__APOLLO_STATE__;
            if (!state) return null;
            
            // 1. Try exact match
            const key1 = 'PlaceDetailBase:' + arguments[0];
            if (state[key1] && state[key1].coordinate) {
                return state[key1].coordinate;
            }
            
            // 2. Iterate to find PlaceDetailBase
            for (let k in state) {
                if (k.startsWith('PlaceDetailBase:') && k.includes(arguments[0])) {
                   if (state[k].coordinate) return state[k].coordinate;
                   // sometimes x, y are directly on the object
                   if (state[k].x && state[k].y) return {x: state[k].x, y: state[k].y};
                }
            }
            return null;
            """
            res = self.driver.execute_script(script, place_id)
            if res and ('x' in res or 'lng' in res) and ('y' in res or 'lat' in res):
                # Naver: x=lng, y=lat
                x = res.get('x') or res.get('lng')
                y = res.get('y') or res.get('lat')
                return {"lat": str(y), "lng": str(x)}
        except Exception:
            pass

        try:
            # __INITIAL_STATE__ fallback
            script = "return window.__INITIAL_STATE__?.place?.summary;"
            res = self.driver.execute_script(script)
            if res and 'x' in res and 'y' in res:
                return {"lat": str(res['y']), "lng": str(res['x'])}
        except Exception:
            pass

        return {"lat": None, "lng": None}

    def _read_phone(self) -> Optional[str]:
        # Phone number often appears as 0xx-xxxx-xxxx or 0xx xxxx xxxx
        # Try obvious candidates first, then regex scan in top area.
        candidates = [
            (By.XPATH, "//*[contains(text(),'전화')]/following::*[1]"),
            (By.CSS_SELECTOR, "span.xlx7Q"),
        ]
        for by, sel in candidates:
            el = try_find(self.driver, by, sel, timeout=2)
            if el:
                t = safe_text(el)
                if t and re.search(r"\d", t):
                    # normalize spaces and remove artifacts
                    t = re.sub(r"\s+", " ", t)
                    return t.replace("안내", "").replace("복사", "").strip()

        # fallback scan
        elems = self.driver.find_elements(By.CSS_SELECTOR, "a, span, div")
        for el in elems[:800]:
            t = safe_text(el)
            if re.search(r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b", t):
                m = re.search(r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b", t)
                if m:
                    return m.group(0).replace(" ", "-")
        return None

    # _click_and_read_business_block removed

    # ---- main plan ----
    def crawl_one(self, place_id: str) -> PlaceRecord:
        record = PlaceRecord(place_id=place_id)
        url = f"https://map.naver.com/p/entry/place/{place_id}"
        # 1) entry (with retry against soft-block / temp error)
        log.info(f"[OPEN] {place_id}")

        if not self._safe_get(url, tries=5):
            log.warning(f"[FAIL] temp-block persists: {place_id}")
            return record

        # 2) entryIframe (retry a few times; iframe sometimes appears late)
        ok = False
        for attempt in range(3):
            if not switch_to_entry_iframe(self.driver, timeout=self.timeout):
                human_sleep(0.8, 1.4)
                # hard re-enter sometimes helps
                self._safe_get(url, tries=1)
                continue

            human_sleep(1.0, 1.8)  # Wait for content to render

            # 판단: '정상 요소'가 보이면 성공 (오탐 방지)
            if is_entry_page_loaded(self.driver):
                ok = True
                break

            # 진짜 soft-block(에러 화면)일 때만 TEMP BLOCK 처리
            if is_real_temp_block(self.driver):
                log.warning(f"[TEMP BLOCK] entry page after iframe: {place_id} (attempt {attempt+1}/3)")
                self.driver.switch_to.default_content()
                human_sleep(1.0, 2.0)
                self._safe_get(url, tries=1)
                continue

            # 그 외: 로딩 지연/DOM 변형 가능 -> 조금 더 기다렸다가 재시도
            human_sleep(0.8, 1.6)

            ok = True
            break

        if not ok:
            # last resort: restart driver once for this place_id
            log.warning(f"[RESTART DRIVER] entry iframe failed repeatedly: {place_id}")
            self._restart_driver()
            if not self._safe_get(url, tries=2) or not switch_to_entry_iframe(self.driver, timeout=self.timeout):
                log.warning(f"[FAIL] entryIframe not found after restart: {place_id}")
                return record

            human_sleep(1.0, 1.8)

        # 3) read name/category/reviews
        try:
            record.name = self._read_name()
        except Exception:
            pass

        try:
            record.category = self._read_category()
        except Exception:
            pass

        try:
            v, b = self._read_reviews()
            record.visitor_reviews = v
            record.blog_reviews = b
        except Exception:
            pass

        # 4) address click -> read layer
        try:
            addr = self._click_and_read_address_layer()
            record.road_address = addr.get("road_address")
            record.jibun_address = addr.get("jibun_address")
            record.zipcd = addr.get("zipcd")
            # address_raw removed
        except Exception:
            pass
            
        # 5) Coordinates
        try:
            coords = self._extract_coordinates(place_id)
            record.lat = coords.get("lat")
            record.lng = coords.get("lng")
        except Exception:
            pass

        # 6) phone
        try:
            record.phone = self._read_phone()
        except Exception:
            pass

        # business hours removed

        # 7) images
        try:
            imgs = self._read_images()
            if imgs:
                record.images = json.dumps(imgs, ensure_ascii=False)
        except Exception:
            pass

        return record


# =========================
# IO
# =========================


def read_google_restaurants(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


from llm_classifier import LLMClassifier

import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1, help="Start index (1-based)")
    parser.add_argument("--end", type=int, default=None, help="End index (inclusive)")
    args = parser.parse_args()

    google_restaurants = read_google_restaurants("google_restaurants.csv")
    total_count = len(google_restaurants)
    
    start_idx = max(1, args.start)
    end_idx = args.end if args.end is not None else total_count
    
    # Slice the list
    # e.g. start=1, end=10 -> google_restaurants[0:10]
    target_list = google_restaurants[start_idx-1 : end_idx]
    
    log.info(f"Target Range: {start_idx} ~ {end_idx} (Total {len(target_list)} / {total_count})")

    crawler = AntiGravityNaverPlaceCrawler(headless=False, timeout=12)
    db = DBManager(host="localhost", user="root", password="dami08036!!", database="restaurant_db")
    
    # 0. Get existing IDs from DB to skip them
    existing_ids = set()
    try:
        tmp_cursor = db.conn.cursor()
        tmp_cursor.execute("SELECT id FROM restaurants")
        rows = tmp_cursor.fetchall()
        existing_ids = {str(row[0]) for row in rows}
        tmp_cursor.close()
        log.info(f"Found {len(existing_ids)} existing restaurants in DB. Will skip them.")
    except Exception as e:
        log.warning(f"Could not load existing IDs: {e}")

    # 1. Load Naver ID Cache (google_naver_ids.csv)
    id_cache = {} # {google_name: naver_id}
    cache_file = "google_naver_ids.csv"
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    id_cache[row["google_name"]] = row["naver_id"]
            log.info(f"Loaded {len(id_cache)} IDs from cache file.")
        except Exception as e:
            log.warning(f"Could not load cache file: {e}")

    def save_to_cache(google_name, naver_id):
        file_exists = os.path.exists(cache_file)
        with open(cache_file, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["google_name", "naver_id"])
            if not file_exists:
                writer.writeheader()
            writer.writerow({"google_name": google_name, "naver_id": naver_id})

    # Local LLM Classifier (Ollama)
    llm = LLMClassifier(api_url="http://localhost:11434/api/generate", model="qwen2.5:7b-instruct")

    out_csv = "naver_place_antigravity.csv"
    fieldnames = list(asdict(PlaceRecord(place_id="x")).keys())

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        w.writeheader()

        try:
            for i, res in enumerate(target_list, start=start_idx):
                name = res["name"]
                address = res["address"]
                log.info(f"[{i}/{total_count}] Processing: {name}")

                # ---- Rate limiting (anti soft-block) ----
                # 1) 최소 2초 텀 (식당 접근 간)
                if i > start_idx:
                    time.sleep(2)

                # 2) 50개마다 60초 휴식
                if (i - start_idx) > 0 and (i - start_idx) % 50 == 0:
                    log.info("[COOLDOWN] processed 50 items -> sleep 60s")
                    time.sleep(60)

                # 1. Check ID Cache or Search
                pid = id_cache.get(name)
                if pid:
                    log.info(f"[CACHE] Found ID {pid} for {name}")
                else:
                    pid = crawler.search_and_get_place_id(name, address)
                    if pid:
                        save_to_cache(name, pid)
                        id_cache[name] = pid
                
                if not pid:
                    continue
                
                # Check if already in DB
                if pid in existing_ids:
                    log.info(f"[SKIP] {pid} already exists in database.")
                    continue

                try:
                    # 2. Detail Crawl
                    rec = crawler.crawl_one(pid)
                    w.writerow(asdict(rec))
                    
                    # --- 3-Step Filtering & Classification ---
                    
                    # 1. First Filter (Non-restaurant Check)
                    if not check_if_restaurant(rec.category):
                        log.info(f"[SKIP 1st] {pid} | Category '{rec.category}' is non-restaurant")
                        log_skipped_category(pid, rec.name, rec.category, "1st Filter (Non-restaurant)")
                        continue
                    
                    # 2. Keyword Mapping (Definite Check)
                    category_9, is_definite = categorize_restaurant(rec.category)
                    is_restaurant = True
                    
                    # 3. LLM Correction (Ambiguous Check)
                    if not is_definite:
                        log.info(f"[LLM REFINING] {pid} | Category '{rec.category}' is ambiguous")
                        is_restaurant, category_9 = llm.classify(
                            pid, rec.name, rec.category, rec.road_address
                        )
                        
                    if not is_restaurant:
                        log.info(f"[SKIP 3rd] {pid} | LLM excluded as non-restaurant")
                        log_skipped_category(pid, rec.name, rec.category, "3rd Filter (LLM)")
                        continue
                        
                    # --- DB Saving ---
                    
                    # Extract image URLs (up to 3)
                    img_list = []
                    if rec.images:
                        try: img_list = json.loads(rec.images)
                        except: pass
                    
                    db_data = {
                        "id": int(rec.place_id),
                        "name": rec.name,
                        "road_address": rec.road_address or "",
                        "jibun_address": rec.jibun_address or "",
                        "zipcd": rec.zipcd or "",
                        "lat": rec.lat,
                        "lng": rec.lng,
                        "phone": rec.phone,
                        "category_original": rec.category or "",
                        "category_mapped": category_9,
                        "review_count_visitor": rec.visitor_reviews,
                        "review_count_blog": rec.blog_reviews,
                        "image_url1": img_list[0] if len(img_list) > 0 else None,
                        "image_url2": img_list[1] if len(img_list) > 1 else None,
                        "image_url3": img_list[2] if len(img_list) > 2 else None,
                    }
                    
                    if not db_data["name"] or not db_data["road_address"] or not db_data["lat"]:
                         missing = []
                         if not db_data["name"]: missing.append("name")
                         if not db_data["road_address"]: missing.append("road_address")
                         if not db_data["lat"]: missing.append("lat/lng")
                         log.warning(f"[FAIL DB] {pid} | Missing mandatory fields: {', '.join(missing)}")
                    else:
                         db.upsert_restaurant(db_data)
                         log.info(f"[DB OK] {pid} | Final Category: '{category_9}'")

                    log.info(
                        f"[OK {i}/{len(google_restaurants)}] {pid} | name={rec.name} | category={category_9}"
                    )
                except Exception as e:
                    log.exception(f"[ERR] {pid}: {e}")
                    w.writerow(asdict(PlaceRecord(place_id=pid)))
        finally:
            crawler.close()
            db.close()

    log.info(f"Saved -> {out_csv}")


if __name__ == "__main__":
    main()
