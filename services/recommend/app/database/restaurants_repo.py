from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text


def fetch_bbox_rows(
    db: Session,
    bbox: Dict[str, float],
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    sql = """
        SELECT
            id,
            name,
            lat,
            lng,
            category_mapped,
            category_original,
            COALESCE(review_count_visitor, 0) AS review_count_visitor,
            COALESCE(review_count_blog, 0) AS review_count_blog
        FROM restaurants
        WHERE lat BETWEEN :min_lat AND :max_lat
          AND lng BETWEEN :min_lng AND :max_lng
          AND lat IS NOT NULL
          AND lng IS NOT NULL
          AND id IS NOT NULL
    """

    params = {
        "min_lat": bbox["min_lat"],
        "max_lat": bbox["max_lat"],
        "min_lng": bbox["min_lng"],
        "max_lng": bbox["max_lng"],
    }

    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = int(limit)

    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]