from fastapi import APIRouter, HTTPException
from datetime import datetime

from app.controller.recommend_controller import calculate_recommendations
from app.schemas.recommend_schema import RecommendRequest, RecommendResponse
from app.database.connection import get_connection 

router = APIRouter()

@router.post("/recommendations", response_model=RecommendResponse)
async def create_recommendations(request: RecommendRequest):
    # DB 연결 
    try:
        conn = get_connection()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB Connection Error: {str(e)}")

    try:
        # 추천 로직 실행
        results = calculate_recommendations(conn, request)
        
        # 응답 데이터 구성
        response_data = {
            "request_id": request.request_id,
            "user_id": request.user_id,
            "top_n": len(results),
            "restaurants": [
                {
                    "rank": res.get("rank") or (i + 1),
                    "id": res["id"],  
                    "name": res["name"],           # <--- 추가
                    "category_mapped": res["category_mapped"],
                    "distance_m": res["distance_m"],
                    "final_score": res["final_score"]
                } for i, res in enumerate(results)
            ],
            "created_at": datetime.now()
        }
        return response_data
        
    except Exception as e:
        # 로직 실행 중 에러 발생 시 처리
        raise HTTPException(status_code=500, detail=f"Recommendation Error: {str(e)}")
    finally:
        # 연결 반환/종료
        conn.close()