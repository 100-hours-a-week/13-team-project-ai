from typing import Dict, Tuple
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.recommend_v2 import (
    MeetupRequest,
    RecoResponse,
    RecoItem,
    ErrorResponseWithCode,
    InternalServerErrorResponse,
    ValidationErrorResponse,
)
from app.controller.v2.candidates import generate_candidates, Params, CATEGORIES_9
from app.controller.v2.rank import rank_v2, V1Weights, V1Params

router = APIRouter(prefix="/recommendations", tags=["recommend-v2"])


def build_like_dislike(
    likes: Dict[str, int],
    dislikes: Dict[str, int],
) -> Dict[str, Tuple[int, int]]:
    """
    Build category -> (like, dislike) dict for all 9 categories.
    Missing categories are filled with 0.
    """
    like_dislike = {c: (0, 0) for c in CATEGORIES_9}

    for c, v in (likes or {}).items():
        if c in like_dislike:
            _, d0 = like_dislike[c]
            like_dislike[c] = (int(v), d0)

    for c, v in (dislikes or {}).items():
        if c in like_dislike:
            l0, _ = like_dislike[c]
            like_dislike[c] = (l0, int(v))

    return like_dislike


def raise_error(status_code: int, code: str, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code, "detail": detail})


def to_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


@router.post(
    "",
    response_model=RecoResponse,
    responses={
        404: {"model": ErrorResponseWithCode},
        422: {"model": ValidationErrorResponse},
        500: {"model": InternalServerErrorResponse},
    },
)
def recommend(payload: MeetupRequest, db: Session = Depends(get_db)) -> RecoResponse:
    # 1) Build like/dislike vector
    like_dislike = build_like_dislike(
        payload.preferences.like,
        payload.preferences.dislike,
    )

    # 2) Hard excludes
    excluded = set()
    if payload.exclude.meat:
        excluded.add("고기")
    if payload.exclude.bar:
        excluded.add("기타")

    # 3) Top-N (requested) and return count (2x)
    top_n = max(1, int(payload.swipe.card_limit))  # requested
    return_n = min(15 * 2, top_n * 2)              # actually return 2x, keep an upper bound if you want

    # 4) Candidate generation
    cand_params = Params()
    out_candidates, seed, fallback_meta = generate_candidates(
        db=db,
        meetup_lat=payload.location.lat,
        meetup_lng=payload.location.lng,
        R_m=payload.location.radius_m,
        K=return_n,  # ✅ 2x cards
        N_people=int(payload.meeting.headcount),
        like_dislike=like_dislike,
        params=cand_params,
        excluded_categories=excluded,
    )

    if not out_candidates:
        raise_error(404, "NO_CANDIDATES", "no candidates found within radius")

    # 5) Ranking
    ranked = rank_v2(
        out_candidates=out_candidates,
        like_dislike=like_dislike,
        N_people=int(payload.meeting.headcount),
        R_m=to_float(fallback_meta.get("final_radius_m"), to_float(payload.location.radius_m)),
        top_n=return_n,  # ✅ 2x cards
        weights=V1Weights(),
        params=V1Params(seed=seed),
    )

    if not ranked:
        raise_error(404, "NO_RECOMMENDATIONS", "no recommendations available")

    # 6) Build response items
    restaurants = []
    for i, r in enumerate(ranked, start=1):
        restaurants.append(
            RecoItem(
                rank=i,
                store_id=str(r.get("place_id")),
                distance_m=int(to_float(r.get("dist_m"), 0.0)),
                final_score=to_float(r.get("final_score"), 0.0),
            )
        )

    if not restaurants:
        raise_error(404, "NO_RECOMMENDATIONS", "no recommendations available")

    KST = timezone(timedelta(hours=9))

    # 7) Response: keep top_n as requested, return restaurants as 2x
    return RecoResponse(
        request_id=payload.request_id,
        user_id=int(payload.user_id),
        top_n=top_n,                         # requested
        restaurants=restaurants[:return_n],  # ✅ 2x
        created_at=datetime.now(tz=KST).isoformat(),
    )
