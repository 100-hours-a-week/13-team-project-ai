from fastapi import APIRouter, Depends, HTTPException
from typing import Tuple

from app.core.model_loader import get_model
from app.controller.recommend_controller import predict
from app.schemas.recommend_schema import (
    RecommendRequest,
    RecommendResponse,
    RecommendedRestaurant,
    ErrorResponse,
)

router = APIRouter()


@router.post(
    "/recommendations",
    response_model=RecommendResponse,
    responses={
        400: {"model": ErrorResponse, "description": "잘못된 요청"},
        404: {"model": ErrorResponse, "description": "후보 없음"},
        500: {"description": "서버 내부 오류"},
    },
)
async def create_recommendations(
    request: RecommendRequest,
    model_meta: Tuple = Depends(get_model),
):
    model, meta = model_meta

    # 추론 실행
    try:
        df_top = predict(request.model_dump(), model, meta)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recommendation Error: {str(e)}")

    # 후보 없음
    if df_top is None or df_top.empty:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_CANDIDATES", "detail": "no candidates found within radius"},
        )

    # 응답 구성
    restaurants = [
        RecommendedRestaurant(
            rank        = int(row["rank"]),
            store_id    = str(int(row["restaurant_id"])),
            distance_m  = round(float(row["dist_m"])),
            final_score = round(float(row["score"]), 4),
        )
        for _, row in df_top.iterrows()
    ]

    return RecommendResponse(
        request_id  = request.request_id,
        user_id     = request.user_id,
        top_n       = request.swipe.card_limit,
        restaurants = restaurants,
    )