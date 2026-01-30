from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Literal, Annotated
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


class Meta(BaseModel):
    seed: Optional[int] = None
    limit_bbox: Optional[int] = None


class MeetupRequest(BaseModel):
    user_id: int = Field(..., ge=1)
    request_id: str
    meeting: Meeting
    location: Location
    swipe: Swipe
    filters: Filters = Field(default_factory=Filters)
    preferences: Preferences

    seed: Optional[int] = None
    limit_bbox: Optional[int] = None
    meta: Optional[Meta] = None


# =========================
# Response Item
# =========================

class RecoItem(BaseModel):

    rank: int = Field(..., ge=1)

    store_id: Any
    distance_m: float = Field(..., ge=0, description="거리 (meter)")
    final_score: float = Field(..., ge=0.0, le=1.0)


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
    created_at: datetime
