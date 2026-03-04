from fastapi import APIRouter, HTTPException, Request
from PIL import Image
from io import BytesIO
import requests
from app.core.prompts import RECEIPT_OCR_PROMPT 
from app.schemas.schemas import ReceiptRequest, ReceiptResponse # 요청 스키마 추가 가정

router = APIRouter()

@router.post("/receipt", response_model=ReceiptResponse)
async def receipt_ocr(
    request: Request, 
    body: ReceiptRequest
):
    try:
        # 1. Pre-signed URL로부터 이미지 다운로드
        try:
            # 타임아웃을 설정하여 무한 대기를 방지합니다.
            response = requests.get(body.image_url, timeout=15)
            response.raise_for_status() # 에러 시 예외 발생
            image_data = response.content
        except requests.exceptions.RequestException as e:
            raise ValueError(f"이미지 다운로드 실패: {str(e)}")

        # 2. 이미지를 PIL 객체로 변환 및 RGB 통일
        try:
            img = Image.open(BytesIO(image_data)).convert("RGB")
        except Exception:
            raise ValueError("유효하지 않은 이미지 파일입니다.")
        
        # 3. LLM 서비스 호출
        llm_service = request.app.state.llm
        ocr_data = llm_service.analyze_receipt(img, RECEIPT_OCR_PROMPT)
        
        return {
            "request_id": body.request_id,
            **ocr_data
        }
        
    except ValueError as e:
        # 다운로드 실패, 이미지 파싱 실패 등
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # vLLM 서버 에러 등 기타 시스템 에러
        raise HTTPException(status_code=422, detail=f"처리 중 오류 발생: {str(e)}")