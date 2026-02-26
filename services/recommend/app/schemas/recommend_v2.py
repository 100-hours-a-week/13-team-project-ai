from datetime import datetime
from typing import Any, Dict, List, Literal, Annotated
from pydantic import BaseModel, Field, field_validator


# =========================
# Types
# =========================

Category9 = Literal[
    "한식", "일식", "중식", "양식",
    "아시안", "분식", "고기", "해산물", "기타"
]


# =========================
# Request
# =========================

CategoryScore = Annotated[int, Field(ge=0)]


class Meeting(BaseModel):
    start_time: datetime = Field(..., description="약속 시작 시간 (ISO8601, timezone 포함)")
    headcount: int = Field(..., ge=1, description="모임 인원수")

    @field_validator("start_time")
    @classmethod
    def ensure_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("start_time must include timezone")
        return v


class Location(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_m: float = Field(..., ge=100, le=20000, description="검색 반경 (meter)")


class Swipe(BaseModel):
    card_limit: int = Field(..., ge=1, le=15, description="스와이프 카드 개수 (최대 15)")


class Filters(BaseModel):
    exclude_meat: bool = Field(False, description="고깃집 제외 (True → '고기' 제외)")
    exclude_bar: bool = Field(False, description="술집 제외 (True → '기타' 제외)")


class Preferences(BaseModel):
    like: Dict[Category9, CategoryScore] = Field(default_factory=dict)
    dislike: Dict[Category9, CategoryScore] = Field(default_factory=dict)


class Exclude(BaseModel):
    meat: bool = Field(default=False, description="고깃집 제외 (True → '고기' 제외)")
    bar: bool = Field(default=False, description="술집 제외 (True → '기타' 제외)")
 

class MeetupRequest(BaseModel):

    user_id: int = Field(..., ge=1, description="유저 ID (>=1)")
    request_id: str = Field(..., min_length=1, description="요청 ID")

    meeting: Meeting
    location: Location
    swipe: Swipe

    preferences: Preferences = Field(default_factory=Preferences)

    exclude: Exclude = Field(default_factory=lambda: Exclude())




# =========================
# Response Item
# =========================

class RecoItem(BaseModel):

    rank: int = Field(..., ge=1)
    store_id: str
    distance_m: int = Field(..., ge=0)
    final_score: float


# =========================
# Fallback Info
# =========================

class FallbackInfo(BaseModel):

    used: bool
    type: str

    # meters
    requested_radius_m: float
    final_radius_m: float

    # radius expansion steps (meters)
    steps_m: List[float]

    hard_banned: List[str] = []
    soft_banned: List[str] = []


# =========================
# Response
# =========================


class RecoResponse(BaseModel):
    request_id: str
    user_id: int
    top_n: int
    restaurants: List[RecoItem]
    created_at: str  # ISO8601 string

# =========================
# Error Response (V2)
# =========================

class ErrorDetail(BaseModel):
    loc: List[Any]
    msg: str
    type: str


class ErrorResponse(BaseModel):
    detail: str


class ErrorResponseWithCode(BaseModel):
    code: str
    detail: str


class InternalServerErrorResponse(BaseModel):
    detail: str = Field("Internal Server Error", description="서버 내부 오류")


class ValidationErrorResponse(BaseModel):
    detail: List[ErrorDetail]
