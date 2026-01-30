from typing import Dict, Tuple
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.recommend_v2 import MeetupRequest, RecoResponse, RecoItem
from app.controller.v2.candidates import generate_candidates, Params, CATEGORIES_9
from app.controller.v2.rank import rank_v2, V1Weights, V1Params

router = APIRouter(prefix="/api/v2/recommendations", tags=["recommend-v2"])


def build_like_dislike(likes: Dict[str, int], dislikes: Dict[str, int]) -> Dict[str, Tuple[int, int]]:
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


@router.post("", response_model=RecoResponse)
def recommend(payload: MeetupRequest, db: Session = Depends(get_db)) -> RecoResponse:
    like_dislike = build_like_dislike(
        payload.preferences.like,
        payload.preferences.dislike,
    )

    excluded = set()
    if payload.filters.exclude_meat:
        excluded.add("고기")
    if payload.filters.exclude_bar:
        excluded.add("기타")

    cand_params = Params(seed=payload.seed)
    out_candidates, seed, fallback_meta = generate_candidates(
        db=db,
        meetup_lat=payload.location.lat,
        meetup_lng=payload.location.lng,
        R_m=payload.location.radius_m,
        K=max(1, int(payload.swipe.card_limit)),
        N_people=int(payload.meeting.headcount),
        like_dislike=like_dislike,
        params=cand_params,
        limit_bbox=payload.limit_bbox,
        excluded_categories=excluded,
    )

    ranked = rank_v2(
        out_candidates=out_candidates,
        like_dislike=like_dislike,
        N_people=int(payload.meeting.headcount),
        R_m=float(fallback_meta["final_radius_m"]),
        top_n=max(1, int(payload.swipe.card_limit)),
        weights=V1Weights(),
        params=V1Params(seed=seed),
    )

    recos = []
    for i, r in enumerate(ranked, start=1):
        recos.append(
            RecoItem(
                rank=i,
                store_id=r["place_id"],
                distance_m=round(float(r.get("dist_m", 0.0)), 2),
                final_score=float(r.get("final_score", 0.0)),
            )
        )

    return RecoResponse(
        request_id=payload.request_id,
        user_id=int(payload.user_id),
        top_n=len(recos),
        restaurants=recos,
        created_at=payload.meeting.start_time,
    )
