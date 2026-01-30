from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional


# =========================
# Weights / Params
# =========================

@dataclass
class V1Weights:
    w_cat: float = 0.63
    w_dist: float = 0.22
    w_review: float = 0.15


@dataclass(frozen=True)
class V1Params:
    gamma: float = 1.2
    eps: float = 1e-6

    laplace_alpha: float = 1.0
    dislike_penalty: float = 0.15
    dislike_power: float = 1.0

    diversify: bool = True
    pool_mult: int = 3
    temperature: float = 0.8
    seed: Optional[int] = None


# =========================
# Utils
# =========================

def normalize_minmax(values: List[float], eps: float = 1e-6) -> List[float]:
    if not values:
        return []

    vmin = min(values)
    vmax = max(values)
    denom = (vmax - vmin) + eps

    return [(v - vmin) / denom for v in values]


# =========================
# Distance Score (m 기준)
# =========================

def compute_s_dist(dist_m: float, R_m: float, gamma: float) -> float:
    """
    dist_m, R_m : meters
    """
    if R_m <= 0:
        return 0.0

    x = dist_m / R_m
    x = max(0.0, min(1.0, x))

    return 1.0 - (x ** gamma)


# =========================
# Category Score
# =========================

def build_p_input(
    like_dislike: Dict[str, Tuple[int, int]],
    alpha: float,
) -> Dict[str, float]:

    cats = list(like_dislike.keys())
    sum_likes = sum(int(v[0]) for v in like_dislike.values())

    denom = float(sum_likes + alpha * len(cats))

    if denom <= 0:
        return {c: 1.0 / max(1, len(cats)) for c in cats}

    return {
        c: (int(like_dislike[c][0]) + alpha) / denom
        for c in cats
    }


def compute_s_cat(
    category: Optional[str],
    P_input: Dict[str, float],
    like_dislike: Dict[str, Tuple[int, int]],
    dislike_penalty: float,
    dislike_power: float,
    N_people: int,
) -> Tuple[float, int]:

    cat = category or ""
    base = float(P_input.get(cat, 0.0))

    _, D = like_dislike.get(cat, (0, 0))

    d_ratio = 0.0 if N_people <= 0 else float(D) / float(N_people)

    penalty = dislike_penalty * (d_ratio ** max(1e-6, dislike_power))

    s = base - penalty
    s = max(0.0, min(1.0, s))

    return s, int(D)


# =========================
# Sampling Utils
# =========================

def softmax_probs(xs: List[float], temperature: float) -> List[float]:
    if not xs:
        return []

    t = max(temperature, 1e-6)
    m = max(xs)

    exps = [math.exp((x - m) / t) for x in xs]
    s = sum(exps)

    if s <= 0:
        return [1.0 / len(xs)] * len(xs)

    return [e / s for e in exps]


def sample_without_replacement(
    items: List[Any],
    probs: List[float],
    k: int,
    rng: random.Random,
) -> List[Any]:

    if k <= 0 or not items:
        return []

    k = min(k, len(items))

    picked = []
    remain_items = list(items)
    remain_probs = list(probs)

    for _ in range(k):

        if not remain_items:
            break

        total = sum(remain_probs)

        if total <= 0:
            idx = rng.randrange(len(remain_items))

        else:
            r = rng.random() * total
            s = 0.0
            idx = 0

            for i, p in enumerate(remain_probs):
                s += p
                if s >= r:
                    idx = i
                    break

        picked.append(remain_items.pop(idx))
        remain_probs.pop(idx)

    return picked


# =========================
# Main Ranking (m 기준)
# =========================

def rank_v2(
    out_candidates: List[Dict[str, Any]],
    like_dislike: Dict[str, Tuple[int, int]],
    N_people: int,
    R_m: float,                      # ← meters
    top_n: int = 10,
    weights: V1Weights = V1Weights(),
    params: V1Params = V1Params(),
) -> List[Dict[str, Any]]:

    if not out_candidates:
        return []

    P_input = build_p_input(like_dislike, alpha=params.laplace_alpha)

    reviews = [float(r.get("review_score", 0.0)) for r in out_candidates]
    s_reviews = normalize_minmax(reviews, eps=params.eps)

    scored: List[Dict[str, Any]] = []

    for r, s_rev in zip(out_candidates, s_reviews):

        cat = r.get("category")

        # meters
        dist_m = float(r.get("dist_m", 0.0))

        s_cat, dislike_count = compute_s_cat(
            category=cat,
            P_input=P_input,
            like_dislike=like_dislike,
            dislike_penalty=params.dislike_penalty,
            dislike_power=params.dislike_power,
            N_people=N_people,
        )

        s_dist = compute_s_dist(dist_m, R_m, params.gamma)

        final_score = (
            weights.w_cat * float(s_cat)
            + weights.w_dist * float(s_dist)
            + weights.w_review * float(s_rev)
        )

        final_score = math.floor(final_score * 100) / 100

        scored.append({
            "place_id": r.get("place_id"),
            "name": r.get("name"),
            "category": cat,

            # meters
            "dist_m": dist_m,

            "final_score": float(final_score),
            "dislike_count": dislike_count,
            "s_cat": float(s_cat),
            "s_dist": float(s_dist),
            "s_review": float(s_rev),
        })

    # 1차 정렬
    scored.sort(
        key=lambda x: (
            -x["final_score"],
            x["dislike_count"],
            x["dist_m"],
            -x["s_review"],
        )
    )

    # Diversify
    if params.diversify:

        rng = (
            random.Random(params.seed)
            if params.seed is not None
            else random.Random()
        )

        M = min(
            len(scored),
            max(top_n, top_n * max(1, params.pool_mult)),
        )

        pool = scored[:M]

        probs = softmax_probs(
            [p["final_score"] for p in pool],
            temperature=params.temperature,
        )

        picked = sample_without_replacement(
            pool,
            probs,
            k=top_n,
            rng=rng,
        )

        picked.sort(
            key=lambda x: (
                -x["final_score"],
                x["dislike_count"],
                x["dist_m"],
                -x["s_review"],
            )
        )

        return picked

    return scored[:top_n]
