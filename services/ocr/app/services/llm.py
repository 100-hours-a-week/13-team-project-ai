import os
import base64
import cv2
import numpy as np
import json
import httpx
from io import BytesIO
from PIL import Image
from app.schemas.schemas import ReceiptResult
from datetime import datetime
import pytz
from langsmith import traceable
from fastapi.concurrency import run_in_threadpool  


class LLMService:
    def __init__(self):
        self.base_url = os.getenv("VLLM_BASE_URL")
        self.model = os.getenv("VLLM_MODEL") 
        self.timeout = float(os.getenv("VLLM_TIMEOUT", 60.0))

    def _preprocess_image(self, img: Image.Image) -> str:
        """이미지 3배 확대 및 선명도 향상 (CPU 집중 작업)"""
        # 1. PIL Image를 OpenCV(NumPy) 형식으로 변환
        img_cv = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    
        # 2. 이미지 3배 확대
        h, w = img_cv.shape[:2]
        img_cv = cv2.resize(img_cv, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    
        # 3. 선명도 향상
        gaussian = cv2.GaussianBlur(img_cv, (0, 0), 2.0)
        img_cv = cv2.addWeighted(img_cv, 1.5, gaussian, -0.5, 0)
    
        # 4. 다시 PIL Image로 변환
        img_res = Image.fromarray(cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB))
    
        # 5. JPEG 압축 및 Base64 인코딩
        buf = BytesIO()
        img_res.save(buf, format="JPEG", quality=90, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        return f"data:image/jpeg;base64,{b64}"


    @traceable(name="analyze_receipt", run_type="llm")
    async def analyze_receipt(self, img: Image.Image, prompt: str) -> dict:
        """vLLM 서버 비동기 호출 및 결과 파싱"""
        
        img_url = await run_in_threadpool(self._preprocess_image, img)
        
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

        # httpx를 사용하여 비동기로 vLLM에 요청
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            res_json = response.json()

        content = res_json["choices"][0]["message"]["content"].strip()
        
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("모델이 유효한 JSON을 생성하지 못했습니다.")

        raw_data = json.loads(content[start:end + 1])

        if "result" in raw_data and raw_data["result"] is not None:
            res_body = raw_data["result"]
            
            # 1. 기본값 세팅
            items = res_body.get("items") or []
            extracted_total = res_body.get("total_amount") or 0
            extracted_paid = res_body.get("paid_amount") or 0
            extracted_discount = res_body.get("discount_amount") or 0

            # 2. 1차 검증: Total - Discount = Paid 가 맞는지 확인
            if (extracted_total - extracted_discount != extracted_paid) or (extracted_total == 0):
                calculated_item_sum = sum(item.get("amount", 0) for item in items)
                res_body["total_amount"] = calculated_item_sum
                res_body["discount_amount"] = max(0, calculated_item_sum - extracted_paid)
                res_body["paid_amount"] = extracted_paid
            else:
                res_body["total_amount"] = extracted_total
                res_body["discount_amount"] = extracted_discount
                res_body["paid_amount"] = extracted_paid
            
            # 3. 시간 보정 및 Pydantic 검증
            kst = pytz.timezone('Asia/Seoul')
            res_body["created_at"] = datetime.now(kst).isoformat()
            
            validated = ReceiptResult(**res_body)
            return {"result": validated.model_dump()}
        
        return raw_data