import os
import json
import logging
import requests

log = logging.getLogger("LLMClassifier")

class LLMClassifier:
    def __init__(self, api_url="http://localhost:11434/api/generate", model="qwen2.5:7b-instruct", cache_path="category_cache.json"):
        self.api_url = api_url
        self.model = model
        self.cache_path = cache_path
        self.cache = self._load_cache()
        self.allowed_categories = [
            "한식", "일식", "중식", "양식", "아시안", "분식", "고기", "해산물", "기타"
        ]

    def _load_cache(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_cache(self):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)

    def classify(self, place_id, name, category_original, address):
        """
        Returns (is_restaurant, category_9)
        """
        # 0. Check for valid cache entry first
        if place_id in self.cache:
            res = self.cache[place_id]
            cat = res.get("category_9", "기타")
            # Validate cache entry too
            if cat not in self.allowed_categories:
                cat = "기타"
            return res.get("is_restaurant", False), cat

        # 1. Call Local LLM (Ollama)
        prompt = f"""
입력된 장소 정보가 '식당'인지 판단하고, 아래 9개 카테고리 중 가장 적합한 하나를 선택해주세요.

[입력 정보]
장소명: {name}
네이버 카테고리: {category_original}
주소: {address}

[9개 카테고리 리스트]
1. 한식
2. 일식
3. 중식
4. 양식
5. 아시안
6. 분식
7. 고기
8. 해산물
9. 기타

[가이드라인]
- 카테고리나 장소명에 구체적인 음식 이름(예: 오징어, 보쌈, 찌개 등)이 포함되어 있으면 특별한 결격 사유가 없는 한 '식당'으로 간주합니다.
- 카페, 베이커리, 디저트, 주류 중심의 바(Bar) 등은 기존 룰에서 걸러지지 않았더라도 식당 목적이 아니면 false로 처리할 수 있습니다.
- '오징어요리', '낙지요리' 등 'OO요리'로 끝나는 카테고리는 100% 식당입니다.
- '생선구이'는 '해산물'로 분류합니다.
- '정육식당'은 '고기'로 분류합니다.
- '굴요리'는 '해산물'로 분류합니다.
- '기타'와 '기타(주점,치킨, 바 등)'는 모두 '기타'로 분류합니다.

[응답 형식 (JSON만 출력)]
{{
  "is_restaurant": true/false,
  "category_9": "위 리스트 중 정확히 하나만 선택"
}}
"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        
        try:
            response = requests.post(self.api_url, json=payload, timeout=20)
            if response.status_code == 200:
                result_text = response.json().get("response", "")
                result = json.loads(result_text)
                
                cat = result.get("category_9", "기타")
                # Strict Validation
                if cat not in self.allowed_categories:
                    log.warning(f"LLM returned invalid category '{cat}'. Defaulting to 기타.")
                    cat = "기타"
                
                result["category_9"] = cat
                
                # Update Cache
                self.cache[place_id] = result
                self._save_cache()
                
                return result.get("is_restaurant", False), cat
            else:
                log.error(f"Local LLM API error: {response.status_code} - {response.text}")
        except Exception as e:
            log.error(f"Local LLM classification failed for {name}: {e}")
            
        return False, "기타" # Fallback to Skip (Safety First)
