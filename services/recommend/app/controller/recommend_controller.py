import math
from typing import List

def calculate_recommendations(db_conn, req):
    cur = db_conn.cursor()
    
    likes = req.preferences.like
    dislikes = req.preferences.dislike
    
    query = """
    SELECT 
        id, 
        name,
        lat,
        lng,
        (6371000 * acos(
            cos(radians(%s)) * cos(radians(lat)) * cos(radians(lng) - radians(%s)) + 
            sin(radians(%s)) * sin(radians(lat))
        )) AS distance_m,
        category_mapped,
        review_count_visitor,
        review_count_blog
    FROM public.restaurants
    WHERE lat IS NOT NULL AND lng IS NOT NULL
      AND (6371000 * acos(
            cos(radians(%s)) * cos(radians(lat)) * cos(radians(lng) - radians(%s)) + 
            sin(radians(%s)) * sin(radians(lat))
        )) <= %s
    ORDER BY distance_m
    """
    
    params = (
        req.location.lat, req.location.lng, req.location.lat,
        req.location.lat, req.location.lng, req.location.lat,
        req.location.radius_m
    )
    
    cur.execute(query, params)
    rows = cur.fetchall()
    
    scored_list = []
    for row in rows:
        score = 0.0
        
        # 1. 리뷰 기반 신뢰도 점수 (로그 스케일)
        visitor_rev = row['review_count_visitor'] or 0
        blog_rev = row['review_count_blog'] or 0
        score += math.log10(visitor_rev + (blog_rev * 1.2) + 1) * 0.7

        # 2. 선호도 가중치 적용
        category_text = row['category_mapped'] or ""
        
        for cat, val in likes.items():
            if cat in category_text:
                score += (val * 1.0)
        
        for cat, val in dislikes.items():
            if cat in category_text:
                score -= (val * 1.5)

        # 3. 거리 패널티
        score -= (row['distance_m'] / 100) * 0.05
                
        # 응답 스키마(RecommendedRestaurant) 필드명에 맞춤
        scored_list.append({
            "id": row['id'],
            "name": row['name'],
            "category_mapped": category_text, 
            "distance_m": int(row['distance_m']),
            "final_score": round(max(0, score), 2)
        })
    
    # 최종 점수순 정렬
    scored_list.sort(key=lambda x: x['final_score'], reverse=True)
    
    # JSON 데이터에 있던 swipe.card_limit을 추천 개수로 활용
    limit = req.swipe.card_limit if hasattr(req, 'swipe') else 10
    limit = limit * 2
    return scored_list[:limit]
