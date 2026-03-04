import os
import base64
import cv2
import numpy as np
import json
import requests
from io import BytesIO
from PIL import Image
from app.schemas.schemas import ReceiptResult # 스키마 임포트
from datetime import datetime
import pytz

class LLMService:
    def __init__(self):
        self.base_url = os.getenv("VLLM_BASE_URL")
        self.model = os.getenv("VLLM_MODEL") 
        self.timeout = float(os.getenv("VLLM_TIMEOUT"))

    def _preprocess_image(self, img: Image.Image) -> str:
        """이미지 3배 확대 및 선명도 향상 (vLLM 최적화)"""
        
        # 1. PIL Image를 OpenCV(NumPy) 형식으로 변환
        img_cv = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    
        # 2. 이미지 3배 확대 (INTER_CUBIC)
        h, w = img_cv.shape[:2]
        img_cv = cv2.resize(img_cv, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    
        # 3. 선명도 향상 (Unsharp Masking 기법)
        gaussian = cv2.GaussianBlur(img_cv, (0, 0), 2.0)
        img_cv = cv2.addWeighted(img_cv, 1.5, gaussian, -0.5, 0)
    
        # 4. 다시 PIL Image로 변환 (JPEG 압축을 위해)
        img_res = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
    
        # 5. JPEG 압축 및 Base64 인코딩
        buf = BytesIO()
        img_res.save(buf, format="JPEG", quality=90, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        return f"data:image/jpeg;base64,{b64}"

    def analyze_receipt(self, img: Image.Image, prompt: str) -> dict:
        """vLLM 서버 호출 및 결과 파싱"""
        img_url = self._preprocess_image(img)
        
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": img_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 600,
            "temperature": 0.0,
        }

        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=self.timeout
        )
        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"].strip()
        
        # JSON 블록 추출
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("모델이 유효한 JSON을 생성하지 못했습니다.")

        raw_data = json.loads(content[start:end + 1])

        
        if "result" in raw_data and raw_data["result"] is not None:
            res_body = raw_data["result"]
            
            # 1. discount_amount 계산 로직 (total - paid)
            total = res_body.get("total_amount") or 0
            paid = res_body.get("paid_amount") or 0
            
            # 할인 금액이 음수가 나오지 않도록 처리 
            res_body["discount_amount"] = max(0, total - paid)
                
            # 2. created_at 보정: 서버 현재 시간 사용
            if "created_at" not in res_body or not res_body["created_at"]:
                kst = pytz.timezone('Asia/Seoul')
                res_body["created_at"] = datetime.now(kst).isoformat()

            # 3. Pydantic 검증 및 반환
            validated = ReceiptResult(**res_body)
            return {"result": validated.model_dump()}
        
        return raw_data


