"""
app/schemas/recommendation.py
─────────────────────────────
v1 recommend_schema.py 클래스 명세 기준 + v2 추론 파이프라인 검증 로직 통합.

v1 → v2 변경 요약
  - 클래스명: Location / Meeting / Swipe          (v1 그대로 유지)
  - Response:  RecommendedRestaurant              (v1 그대로 유지)
  - radius_m:  int (v1) → float 허용 후 int 변환  (하위호환)
  - like/dislike: Dict[str, int] (v1) 허용         (float도 수용)
  - start_time: datetime (v1) — timezone 필수 검증 추가
  - exclude:   신규 추가 (meat / bar)
  - Response.restaurants: rank, store_id, name?, category_mapped?,
                           distance_m, final_score
  - created_at: datetime (v1 방식 유지)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


KST = timezone(timedelta(hours=9))


# ================================================================
# Request
# ================================================================

class Location(BaseModel):
    lat:      float = Field(..., ge=-90,  le=90,  description="위도")
    lng:      float = Field(..., ge=-180, le=180, description="경도")
    radius_m: float = Field(..., gt=0,            description="반경 (m)")
    # v1은 int였으나 float도 수용 (백엔드가 int로 보내도 자동 변환)


class Preferences(BaseModel):
    like:    Dict[str, float] = Field(default_factory=dict,
                                      description="선호 카테고리 {이름: 투표수}")
    dislike: Dict[str, float] = Field(default_factory=dict,
                                      description="비선호 카테고리 {이름: 투표수}")
    # v1은 Dict[str, int] — float도 수용하므로 하위호환


class Meeting(BaseModel):
    start_time: datetime = Field(..., description="모임 시작 시각 (timezone 필수)")
    headcount:  int      = Field(..., ge=1, le=100, description="참석 인원")

    @field_validator("start_time")
    @classmethod
    def must_have_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("start_time must include timezone info (e.g. +09:00 or Z)")
        return v


class Swipe(BaseModel):
    card_limit: int = Field(..., ge=1, le=200, description="카드 장 수")


class Exclude(BaseModel):
    """v2 신규. 백엔드가 exclude 필드를 보내지 않으면 기본값(False) 사용."""
    meat: bool = Field(False, description="고기류 전체 제외")
    bar:  bool = Field(False, description="술집 제외")


class RecommendRequest(BaseModel):
    user_id:     int         = Field(..., description="유저 ID")
    request_id:  str         = Field(..., description="요청 ID")
    location:    Location
    preferences: Preferences = Field(default_factory=Preferences)
    meeting:     Meeting
    swipe:       Swipe
    exclude:     Exclude     = Field(default_factory=Exclude,
                                     description="v2 신규 — 없으면 기본값(전부 False)")


# ================================================================
# Response
# ================================================================

class RecommendedRestaurant(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,   # v1: DB ORM 객체 직접 변환 지원
        populate_by_name=True,  # alias와 실제 이름 모두 허용
    )

    rank:             int            = Field(..., description="추천 순위")
    # store_id == id (v1 호환 alias)
    store_id:         str            = Field(..., description="식당 ID")
    distance_m:       int            = Field(..., description="거리 (m)")
    final_score:      float          = Field(..., description="LightGBM 추천 점수")


class RecommendResponse(BaseModel):
    request_id:  str                         = Field(..., description="요청 ID")
    user_id:     int                         = Field(..., description="유저 ID")
    top_n:       int                         = Field(..., description="card_limit 기준 장 수")
    restaurants: List[RecommendedRestaurant] = Field(..., description="추천 식당 리스트")
    created_at:  datetime                    = Field(
        default_factory=lambda: datetime.now(KST),
        description="응답 생성 시각 (KST)",
    )


# ================================================================
# Error
# ================================================================

class ErrorResponse(BaseModel):
    code:   str = Field(..., description="에러 코드")
    detail: str = Field(..., description="에러 상세 메시지")
