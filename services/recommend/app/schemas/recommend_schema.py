from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional
from datetime import datetime

# --- Request Schemas ---
class Location(BaseModel):
    lat: float
    lng: float
    radius_m: int

class Preferences(BaseModel):
    like: Dict[str, int]
    dislike: Dict[str, int]

class Meeting(BaseModel):
    start_time: datetime  
    headcount: int

class Swipe(BaseModel):  # 추가된 부분
    card_limit: int

class RecommendRequest(BaseModel):
    user_id: int
    request_id: str
    location: Location
    preferences: Preferences
    meeting: Meeting
    swipe: Swipe      

# --- Response Schemas ---
class RecommendedRestaurant(BaseModel):
    id: int
    name: str
    category_mapped: Optional[str] = None
    distance_m: int
    final_score: float

    # DB 객체를 바로 Pydantic으로 변환하기 위한 설정 
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class RecommendResponse(BaseModel):
    request_id: str
    user_id: int
    top_n: int
    restaurants: List[RecommendedRestaurant]
    created_at: datetime = Field(default_factory=datetime.now)