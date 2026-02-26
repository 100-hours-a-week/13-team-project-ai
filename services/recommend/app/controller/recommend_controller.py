import math
from typing import List, Dict, Any
from psycopg2.extras import RealDictCursor

def calculate_recommendations(db_conn, req) -> List[Dict[str, Any]]:
    # ✅ dict row로 받기
    cur = db_conn.cursor(cursor_factory=RealDictCursor)

    likes = req.preferences.like or {}
    dislikes = req.preferences.dislike or {}

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

        visitor_rev = row.get("review_count_visitor") or 0
        blog_rev = row.get("review_count_blog") or 0
        score += math.log10(visitor_rev + (blog_rev * 1.2) + 1) * 0.7

        category_text = row.get("category_mapped") or ""

        # ✅ 정확히 일치하는 카테고리면 == 로 매칭하는 게 안전
        # (현재는 "한식" in "한식/분식" 같은 문자열 포함을 기대하는 듯)
        for cat, val in likes.items():
            if cat in category_text:
                score += (int(val) * 1.0)

        for cat, val in dislikes.items():
            if cat in category_text:
                score -= (int(val) * 1.5)

        # distance_m이 NULL/NaN일 수 있어 방어
        dist = row.get("distance_m")
        dist = float(dist) if dist is not None else 0.0
        score -= (dist / 100) * 0.05

        scored_list.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "category_mapped": category_text,
            "distance_m": int(dist),
            "final_score": round(max(0.0, score), 2),
        })

    scored_list.sort(key=lambda x: x["final_score"], reverse=True)

    limit = int(getattr(req.swipe, "card_limit", 10))
    limit = min(max(limit, 2), 15)  # 2~15 안전 클램프
    limit = limit * 2               # 기존 로직 유지
    return scored_list[:limit]
