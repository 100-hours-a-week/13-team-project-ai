from pydantic import BaseModel, Field
from typing import List, Optional

class ReceiptItem(BaseModel):
    """영수증 개별 품목 정보"""
    name: str = Field(..., description="품목명")
    unit_price: float = Field(default=0, ge=0, description="단가")
    quantity: float = Field(default=0, ge=0, description="수량")
    amount: float = Field(default=0, ge=0, description="총 금액 (단가 * 수량)")

class ReceiptRequest(BaseModel):
    image_url: str = Field(..., description="S3 Pre-signed GET URL")
    request_id: str = Field(..., description="요청 식별자 (UUID 등)")

class ReceiptResult(BaseModel):
    """OCR 분석 결과 본체"""
    items: List[ReceiptItem] = Field(default_factory=list, description="품목 리스트")
    total_amount: float = Field(default=0, ge=0, description="총 합계")
    discount_amount: float = Field(default=0, ge=0, description="할인 금액")
    paid_amount: float = Field(default=0, ge=0, description="실제 결제 금액")
    created_at: str = Field(..., description="영수증 발행 일시 (ISO8601 형식)")

class ErrorDetail(BaseModel):
    """에러 상세 정보"""
    code: str = Field(..., description="에러 코드 (예: INVALID_IMAGE_TYPE)")
    message: str = Field(..., description="사용자용 에러 메시지")

class ReceiptResponse(BaseModel):
    """최종 API 응답 형식 (성공/실패 통합)"""
    request_id: str = Field(..., description="요청 식별자")
    # 성공 시에는 result가 채워지고, 실패 시에는 error가 채워집니다.
    result: Optional[ReceiptResult] = Field(default=None, description="분석 성공 결과")
    error: Optional[ErrorDetail] = Field(default=None, description="분석 실패 상세")