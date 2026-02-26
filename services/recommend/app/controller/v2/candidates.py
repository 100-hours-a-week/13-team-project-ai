from __future__ import annotations

import math
import random
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional, Set
from collections import defaultdict

from sqlalchemy.orm import Session
from app.database.restaurants_repo import fetch_bbox_rows

log = logging.getLogger(__name__)

CATEGORIES_9 = ["한식", "일식", "중식", "양식", "아시안", "분식", "고기", "해산물", "기타"]
Q_DIST = [0.35, 0.4, 0.25]


@dataclass
class Params:
    eps: float = 0.10
    alpha_top3: float = 0.75
    T_cat: float = 2.0
    M_inbin_max: int = 50
    lam0: float = 1.5
    lam1: float = 2.0
    seed: Optional[int] = None
    rand_min_percentile: float = 0.30
    rand_min_abs: float = 0.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Returns distance in meters (m).
    """
    R = 6_371_000.0  # meters
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_bounding_box(lat: float, lon: float, R_m: float) -> Dict[str, float]:
    """
    R_m is meters.
    Bounding box delta 계산은 근사적으로 km가 편해서 내부에서만 km로 변환.
    """
    R_km = float(R_m) / 1000.0
    lat_delta = R_km / 111.0
    lng_delta = R_km / max(111.0 * math.cos(math.radians(lat)), 1e-6)
    return {
        "min_lat": lat - lat_delta,
        "max_lat": lat + lat_delta,
        "min_lng": lon - lng_delta,
        "max_lng": lon + lng_delta,
    }


def softmax(xs: List[float], T: float) -> List[float]:
    if not xs:
        return []
    t = max(T, 1e-9)
    m = max(xs)
    exps = [math.exp((x - m) / t) for x in xs]
    s = sum(exps)
    if s <= 0:
        return [1.0 / len(xs)] * len(xs)
    return [e / s for e in exps]


def review_score(r: Dict[str, Any]) -> float:
    v = r.get("review_count_visitor", None)
    b = r.get("review_count_blog", None)
    v2 = int(v or 0)
    b2 = int(b or 0)
    return math.log1p(v2) + 0.5 * math.log1p(b2)


def decide_top_m(n: int, m_base: int = 200) -> int:
    if n <= 0:
        return 0
    return min(n, max(m_base, int(n * 0.25)))


def assign_bin(dist_m: float, R_m: float) -> int:
    if R_m <= 0:
        return 0
    x = max(0.0, min(1.0, dist_m / R_m))
    if x <= Q_DIST[0]:
        return 0
    if x <= (Q_DIST[0] + Q_DIST[1]):
        return 1
    return 2


def build_candidate_pool(
    db: Session,
    meetup_lat: float,
    meetup_lng: float,
    R_m: float,
    limit_bbox: Optional[int] = None,
) -> List[Dict[str, Any]]:
    bbox = get_bounding_box(meetup_lat, meetup_lng, R_m)
    rows = fetch_bbox_rows(db, bbox, limit=limit_bbox)

    seen: Set[Any] = set()
    pool: List[Dict[str, Any]] = []

    for r in rows:
        pid = r.get("id")
        lat = r.get("lat")
        lng = r.get("lng")
        if pid is None or lat is None or lng is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)

        d_m = float(haversine(meetup_lat, meetup_lng, float(lat), float(lng)))
        if d_m > R_m:
            continue

        cat = (r.get("category_mapped") or "").strip()
        if not cat or cat not in CATEGORIES_9:
            cat = "기타"

        rr = dict(r)
        rr["place_id"] = pid
        rr["name"] = (r.get("name") or "").strip() or None
        rr["dist_m"] = float(d_m)
        rr["category"] = cat
        rr["visitor_reviews"] = int(rr.get("review_count_visitor") or 0)
        rr["blog_reviews"] = int(rr.get("review_count_blog") or 0)
        rr["review_score"] = float(review_score(rr))
        pool.append(rr)

    return pool


def assign_bins(pool: List[Dict[str, Any]], R_m: float) -> None:
    for r in pool:
        r["bin"] = assign_bin(float(r.get("dist_m", 0.0)), R_m)


def split(K: int, eps: float) -> Tuple[int, int]:
    K_random = int(round(K * eps))
    K_random = max(0, min(K, K_random))
    K_main = K - K_random
    return K_main, K_random


def top3_categories(
    N_people: int,
    like_dislike: Dict[str, Tuple[int, int]],
    params: Params,
) -> Tuple[List[str], Dict[str, float], Dict[str, float]]:
    scores: Dict[str, float] = {}
    total_like = sum(int(v[0]) for v in like_dislike.values())

    for c, (L, D) in like_dislike.items():
        l = int(L)
        if total_like <= 0:
            scores[c] = 1.0 / len(CATEGORIES_9)
        else:
            scores[c] = (l + params.alpha_top3) / (total_like + params.alpha_top3 * len(CATEGORIES_9))

    top3 = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:3]
    top3 = [c for c in top3 if scores[c] > 0]

    sum_p = sum(scores[c] for c in top3) or 1.0
    P3 = {c: scores[c] / sum_p for c in top3}

    return top3, scores, P3


def category_quota(K_main: int, top3: List[str], P3: Dict[str, float], params: Params):
    quota_top3: Dict[str, int] = {}
    if top3:
        K_top3 = int(round(K_main * params.alpha_top3))
        remain = K_top3
        for i, c in enumerate(top3):
            if i == len(top3) - 1:
                q = remain
            else:
                q = int(round(K_top3 * P3.get(c, 0.0)))
                q = max(0, min(remain, q))
            quota_top3[c] = q
            remain -= q
        if remain > 0:
            best = top3[0]
            quota_top3[best] += remain
    else:
        K_top3 = 0

    K_other = K_main - sum(quota_top3.values())
    quota_other = K_other
    return quota_top3, quota_other, K_top3, K_other


def _cap_bin(pool: List[Dict[str, Any]], M_inbin_max: int) -> List[Dict[str, Any]]:
    bins = defaultdict(list)
    for r in pool:
        bins[int(r.get("bin", 0))].append(r)

    out = []
    for b in sorted(bins.keys()):
        rs = bins[b]
        if len(rs) > M_inbin_max:
            rs = rs[:M_inbin_max]
        out.extend(rs)
    return out


def sampling(
    pool: List[Dict[str, Any]],
    K_main: int,
    K_random: int,
    top3: List[str],
    quota_top3: Dict[str, int],
    quota_other: int,
    params: Params,
    banned_categories: Set[str],
    rng: random.Random,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pool2 = _cap_bin(pool, params.M_inbin_max)

    main: List[Dict[str, Any]] = []
    used: Set[Any] = set()

    by_cat = defaultdict(list)
    for r in pool2:
        by_cat[r["category"]].append(r)

    for c in top3:
        q = int(quota_top3.get(c, 0))
        if q <= 0:
            continue
        cand = by_cat.get(c, [])
        cand = [x for x in cand if x["place_id"] not in used]
        cand.sort(key=lambda x: (x["dist_m"], -x["review_score"]))
        pick = cand[:q]
        for x in pick:
            used.add(x["place_id"])
        main.extend(pick)

    if len(main) < K_main:
        rest = [r for r in pool2 if r["place_id"] not in used and r["category"] not in banned_categories]
        rest.sort(key=lambda x: (x["dist_m"], -x["review_score"]))
        need = K_main - len(main)
        main.extend(rest[:need])
        for x in rest[:need]:
            used.add(x["place_id"])

    rand: List[Dict[str, Any]] = []
    if K_random > 0:
        rest = [r for r in pool2 if r["place_id"] not in used and r["category"] not in banned_categories]
        if rest:
            scores = [float(r["review_score"]) for r in rest]
            scores_sorted = sorted(scores)
            idx = int(max(0, min(len(scores_sorted) - 1, round(len(scores_sorted) * params.rand_min_percentile))))
            thr = max(params.rand_min_abs, scores_sorted[idx])

            rand_pool = [r for r in rest if float(r["review_score"]) >= thr]
            if not rand_pool:
                rand_pool = rest

            # m 단위 dist_m 사용
            ds = [float(r["dist_m"]) for r in rand_pool]
            probs = softmax([-d for d in ds], T=params.T_cat)

            picks = []
            for _ in range(min(K_random, len(rand_pool))):
                r = rng.choices(rand_pool, weights=probs, k=1)[0]
                if r["place_id"] in used:
                    continue
                used.add(r["place_id"])
                picks.append(r)
            rand.extend(picks)

    return main, rand


def final_candidates(
    main: List[Dict[str, Any]],
    rand: List[Dict[str, Any]],
    K: int,
    pool: List[Dict[str, Any]],
    banned_categories: Set[str],
) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for r in main + rand:
        pid = r["place_id"]
        if pid in seen:
            continue
        seen.add(pid)
        out.append(r)
        if len(out) >= K:
            return out

    rest = [r for r in pool if r["place_id"] not in seen and r["category"] not in banned_categories]
    rest.sort(key=lambda x: (x["dist_m"], -x["review_score"]))
    for r in rest:
        out.append(r)
        if len(out) >= K:
            break
    return out


def _run_once(
    db: Session,
    meetup_lat: float,
    meetup_lng: float,
    R_m: float,
    K: int,
    N_people: int,
    like_dislike: Dict[str, Tuple[int, int]],
    params: Params,
    limit_bbox: Optional[int],
    banned_categories: Set[str],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    pool = build_candidate_pool(db, meetup_lat, meetup_lng, R_m, limit_bbox=limit_bbox)
    pool = [r for r in pool if r["category"] not in banned_categories]
    log.info(f"[POOL@{R_m:.1f}m] size={len(pool)}")

    if not pool:
        return []

    assign_bins(pool, R_m)

    K_main, K_random = split(K, params.eps)
    top3, _, P3 = top3_categories(N_people, like_dislike, params)
    top3 = [c for c in top3 if c not in banned_categories]

    quota_top3, quota_other, _, _ = category_quota(K_main, top3, P3, params)
    main, rand = sampling(pool, K_main, K_random, top3, quota_top3, quota_other, params, banned_categories, rng)
    cand = final_candidates(main, rand, K, pool, banned_categories)

    cand.sort(key=lambda x: (float(x.get("dist_m", 0.0)), -float(x.get("review_score", 0.0))))
    return cand


def generate_candidates(
    db: Session,
    meetup_lat: float,
    meetup_lng: float,
    R_m: float,
    K: int,
    N_people: int,
    like_dislike: Dict[str, Tuple[int, int]],
    params: Params = Params(),
    limit_bbox: Optional[int] = None,
    excluded_categories: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
    """
    return: (out_candidates, seed, fallback_meta)

    out_candidates fields:
      - place_id, name, category, dist_m, bin, review_score, visitor_reviews, blog_reviews
    """
    seed = params.seed if params.seed is not None else random.randrange(1, 10**9)
    rng = random.Random(seed)

    hard_banned = set(excluded_categories or set())
    soft_banned = set()

    cand: List[Dict[str, Any]] = []
    used_type = "none"

    # m 단위로 반경 확장
    steps = [float(R_m), float(R_m) * 1.5, float(R_m) * 2.0, float(R_m) * 3.0]
    final_radius_m = float(R_m)

    # ---------- Level 1: 반경 확장 ----------
    for i, r in enumerate(steps):
        final_radius_m = r
        cand = _run_once(
            db=db,
            meetup_lat=meetup_lat,
            meetup_lng=meetup_lng,
            R_m=r,
            K=K,
            N_people=N_people,
            like_dislike=like_dislike,
            params=params,
            limit_bbox=limit_bbox,
            banned_categories=hard_banned,
            rng=rng,
        )
        if cand:
            used_type = "none" if i == 0 else "radius_expand"
            break

    # ---------- Level 2: 조건 완화(soft 제외 해제) ----------
    if not cand and soft_banned:
        log.info("[FALLBACK] relax exclusions (soft off)")
        cand = _run_once(
            db=db,
            meetup_lat=meetup_lat,
            meetup_lng=meetup_lng,
            R_m=final_radius_m,
            K=K,
            N_people=N_people,
            like_dislike=like_dislike,
            params=params,
            limit_bbox=limit_bbox,
            banned_categories=hard_banned,  # 현재는 hard/soft 동일로 유지
            rng=rng,
        )
        if cand:
            used_type = "relaxed"

    out: List[Dict[str, Any]] = []
    for r in cand:
        out.append({
            "place_id": r.get("place_id"),
            "name": r.get("name"),
            "category": r.get("category"),
            "dist_m": float(r.get("dist_m", 0.0)),
            "bin": int(r.get("bin", -1)),
            "review_score": float(r.get("review_score", 0.0)),
            "visitor_reviews": int(r.get("visitor_reviews", 0)),
            "blog_reviews": int(r.get("blog_reviews", 0)),
        })

    fallback_meta = {
        "used": (used_type != "none"),
        "type": used_type,
        "requested_radius_m": float(R_m),
        "final_radius_m": float(final_radius_m),
        "steps_m": steps,
        "hard_banned": sorted(list(hard_banned)),
        "soft_banned": sorted(list(soft_banned)),
    }
    return out, seed, fallback_meta
