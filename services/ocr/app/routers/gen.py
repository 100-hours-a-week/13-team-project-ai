import asyncio
import httpx
import logging
from fastapi import APIRouter, HTTPException, Request
from PIL import Image
from io import BytesIO
from app.core.prompts import RECEIPT_OCR_PROMPT 
from app.schemas.schemas import ReceiptRequest, ReceiptResponse # 

logger = logging.getLogger("uvicorn")
router = APIRouter()


@router.post("/receipt", response_model=ReceiptResponse)
async def receipt_ocr(request: Request, body: ReceiptRequest):
    logger.info(f" [OCR 시작] ID: {body.request_id}")

    img = None
    async with httpx.AsyncClient() as client:
        for i in range(3):
            try:
                res = await client.get(body.image_url, timeout=10.0)
                res.raise_for_status()
                img = Image.open(BytesIO(res.content)).convert("RGB")
                logger.info(f" 다운로드 성공 ")
                break 
            except Exception as e:
                if i == 2:
                    logger.error(f" 최종 실패: {e}")
                    raise HTTPException(400, f"이미지 로드 실패: {e}")
                
                logger.warning(f" 재시도 중... ")
                await asyncio.sleep(1.5)


    try:
        # LLM 서비스 호출 
        ocr_data = await request.app.state.llm.analyze_receipt(img, RECEIPT_OCR_PROMPT)
        
        # response_model 형식에 맞춰 리턴
        return {
            "request_id": body.request_id,
            **ocr_data
        }
    except Exception as e:
        logger.error(f" 분석 오류: {e}")
        raise HTTPException(422, "영수증 분석 중 내부 오류가 발생했습니다.")