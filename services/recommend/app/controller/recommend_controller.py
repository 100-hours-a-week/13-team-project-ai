from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.vector_store import search_similar_meetings

logger = logging.getLogger(__name__)

# ================================================================
# 카테고리 상수
# ================================================================
CAT_ID_TO_NAME: Dict[int, str] = {
    1: "한식", 2: "일식", 3: "중식", 4: "양식", 5: "아시안",
    6: "분식", 7: "고기", 8: "해산물", 9: "기타",
}
CAT_NAME_TO_ID: Dict[str, int] = {v: k for k, v in CAT_ID_TO_NAME.items()}
N_CATS = 9

TIER_RATIO_DEFAULT: Dict[str, float] = {
    "tier1": 0.45, "tier2": 0.40, "tier3": 0.15, "tier4": 0.00,
}
SIM_TOPK               = 20
SIM_MIN_COS            = 0.25
PREF_ALPHA             = 2.0
INFER_HARD_DISLIKE_THR = 2


# ================================================================
# 유틸
# ================================================================

def safe_float(x, default=0.0):
    try:
        v = float(x)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return default


def safe_int(x, default=0):
    try:
        if isinstance(x, bool):
            return int(x)
        v = float(x)
        return default if (math.isnan(v) or math.isinf(v)) else int(v)
    except Exception:
        return default


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6_371_000.0
    lat1, lng1, lat2, lng2 = map(np.radians, [lat1, lng1, lat2, lng2])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = np.sin(dlat/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlng/2)**2
    return R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def group_zscore(arr):
    mu, std = arr.mean(), arr.std()
    return (arr - mu) / (std + 1e-8)


def group_minmax(arr):
    mn, mx = arr.min(), arr.max()
    return np.zeros_like(arr) if mx - mn < 1e-8 else (arr - mn) / (mx - mn)


def pref_to_vec(pref_dict: Dict[str, float]) -> np.ndarray:
    vec = np.zeros(N_CATS, np.float32)
    for name, votes in pref_dict.items():
        cid = CAT_NAME_TO_ID.get(name)
        if cid:
            vec[cid - 1] = float(votes)
    return vec


def dow_from_start_time(s: str) -> Tuple[int, bool]:
    try:
        dt  = datetime.fromisoformat(str(s))
        dow = dt.weekday()
        return dow, dow >= 5
    except Exception:
        return 0, False


# ================================================================
# Tier 분류 — 학습 label 기준과 동일
# ================================================================

def vote_tier(lk_v: float, dk_v: float) -> int:
    if lk_v > 0 and dk_v == 0:
        return 1
    elif lk_v == 0 and dk_v == 0:
        return 2
    elif lk_v > 0 and dk_v > 0:
        return 3
    else:
        return 4


def get_restaurant_tier(rid: int,
                         likes_raw: Dict,
                         dislikes_raw: Dict,
                         item_df: pd.DataFrame) -> int:
    if rid not in item_df.index:
        return 2
    cat_id   = safe_int(item_df.loc[rid].get('category_id', 0))
    cat_name = CAT_ID_TO_NAME.get(cat_id, '기타')
    return vote_tier(
        float(likes_raw.get(cat_name, 0.0)),
        float(dislikes_raw.get(cat_name, 0.0)),
    )


# ================================================================
# 후보 필터
# ================================================================

def stage1_filter(item_df: pd.DataFrame,
                   u_lat: float, u_lng: float, u_rad: float,
                   likes_raw: Dict, dislikes_raw: Dict,
                   excl_meat: bool, excl_bar: bool,
                   radius_expand: float,
                   hard_dislike_thr: int,
                   ) -> Tuple[List[int], np.ndarray]:
    all_rids = list(item_df.index)
    lats  = item_df['lat'].fillna(0).to_numpy(np.float64)
    lngs  = item_df['lng'].fillna(0).to_numpy(np.float64)
    dists = haversine_m(u_lat, u_lng, lats, lngs).astype(np.float32)

    kept_rids, kept_dist = [], []
    for rid, dist in zip(all_rids, dists):
        if float(dist) > u_rad * radius_expand:
            continue

        cat_id   = safe_int(item_df.loc[rid].get('category_id', 0))
        cat_name = CAT_ID_TO_NAME.get(cat_id, '기타')
        lk_v     = float(likes_raw.get(cat_name, 0.0))
        dk_v     = float(dislikes_raw.get(cat_name, 0.0))

        if dk_v >= hard_dislike_thr and lk_v == 0:
            continue
        if excl_meat and cat_id == 7:
            continue
        if excl_bar and cat_id == 9:
            continue

        kept_rids.append(rid)
        kept_dist.append(float(dist))

    return kept_rids, np.array(kept_dist, np.float32)


# ================================================================
# 피처 벡터 빌드
# ================================================================

def _build_feature_vector(rid: int,
                            ann_norm: float,
                            dist: float, u_rad: float,
                            like_vec: np.ndarray,
                            dislike_vec: np.ndarray,
                            likes_raw: Dict, dislikes_raw: Dict,
                            headcount: int, dow: int,
                            sim_feats: Dict,
                            item_df: pd.DataFrame,
                            feat_idx: Dict[str, int],
                            item_feat_cols: List[str],
                            n_feat: int,
                            pref_alpha: float) -> np.ndarray:
    x   = np.zeros(n_feat, np.float32)
    ir  = 1.0 if dist <= u_rad else 0.0
    dr  = dist / max(u_rad, 1.0)

    def fi(name): return feat_idx.get(name, -1)

    x[fi('ann_score_norm')] = ann_norm
    x[fi('dist_m')]         = dist
    x[fi('dist_norm')]      = 0.0      # batch 정규화 후 채움
    x[fi('dist_ratio')]     = dr
    x[fi('in_radius')]      = ir
    x[fi('log_dist')]       = math.log1p(dist)

    irow     = item_df.loc[rid] if rid in item_df.index else pd.Series(dtype=float)
    cat_id   = safe_int(irow.get('category_id', 0))
    cat_id   = cat_id if 1 <= cat_id <= N_CATS else 0
    cat_name = CAT_ID_TO_NAME.get(cat_id, '기타')
    cid0     = max(cat_id - 1, 0)

    lk_n = float(like_vec[cid0])    if cid0 < len(like_vec)    else 0.0
    dk_n = float(dislike_vec[cid0]) if cid0 < len(dislike_vec) else 0.0
    lk_v = float(likes_raw.get(cat_name, 0.0))
    dk_v = float(dislikes_raw.get(cat_name, 0.0))

    x[fi('like_norm')]       = lk_n
    x[fi('dislike_norm')]    = dk_n
    x[fi('net_norm')]        = lk_n - dk_n
    x[fi('pref_score_norm')] = lk_n - pref_alpha * dk_n
    x[fi('like_votes')]      = lk_v
    x[fi('dislike_votes')]   = dk_v
    x[fi('pref_score_raw')]  = lk_v - pref_alpha * dk_v
    x[fi('conflict_ratio')]  = min(dk_v / (lk_v + 1e-8), 5.0)
    x[fi('is_conflict')]     = 1.0 if (lk_v > 0 and dk_v > 0) else 0.0
    x[fi('pure_like')]       = 1.0 if (lk_v > 0 and dk_v == 0) else 0.0
    x[fi('pure_dislike')]    = 1.0 if (dk_v > 0 and lk_v == 0) else 0.0
    x[fi('has_any_dislike')] = 1.0 if dk_v > 0 else 0.0
    x[fi('dislike_mild')]    = 1.0 if 0 < dk_v <= 1 else 0.0
    x[fi('dislike_strong')]  = 1.0 if dk_v >= 2 else 0.0

    x[fi('pure_like_x_in_radius')] = x[fi('pure_like')] * ir
    x[fi('dislike_v_x_in_radius')] = dk_v * ir
    x[fi('pref_x_ann')]            = x[fi('pref_score_norm')] * ann_norm

    tier = vote_tier(lk_v, dk_v)
    for t in range(1, 5):
        idx = fi(f'tier_{t}')
        if idx >= 0:
            x[idx] = 1.0 if tier == t else 0.0

    has_group = has_room = avg_price = dinner_r = 0.0
    if len(irow):
        for fc in item_feat_cols:
            idx = fi(fc)
            if idx >= 0:
                x[idx] = safe_float(irow.get(fc, 0.0))
        x[fi('category_id')] = float(cat_id)
        x[fi('price_level')] = safe_float(irow.get('price_level', 0.0))
        has_group  = safe_float(irow.get('has_group',  0))
        has_room   = safe_float(irow.get('has_room',   0))
        avg_price  = safe_float(irow.get('avg_price_all', 0))
        dinner_r   = safe_float(irow.get('time_band_dinner_ratio', 0.5), 0.5)

    sf = sim_feats.get(rid, {})
    x[fi('sim_pos_rate')]  = float(sf.get('sim_pos_rate',  0.0))
    x[fi('sim_count')]     = float(sf.get('sim_count',     0.0))
    x[fi('sim_pos_count')] = float(sf.get('sim_pos_count', 0.0))

    x[fi('headcount_group_match')] = float(headcount >= 4) * has_group
    x[fi('headcount_room_match')]  = float(headcount >= 6) * has_room
    x[fi('time_band_match')]       = dinner_r if dow >= 4 else (1.0 - dinner_r)
    x[fi('price_in_budget')]       = max(0.0, 1.0 - (avg_price * headcount) / 100_000.0)

    return x


# ================================================================
# predict()
# ================================================================

def predict(user_request: Dict, model, meta: Dict) -> pd.DataFrame:
    # ── 입력 파싱 (형식 A: API / 형식 B: flat) ───────────────
    if 'location' in user_request or 'meeting' in user_request:
        loc      = user_request.get('location',    {})
        meeting  = user_request.get('meeting',     {})
        swipe    = user_request.get('swipe',       {})
        prefs    = user_request.get('preferences', {})
        exclude  = user_request.get('exclude',     {})
        dow, _   = dow_from_start_time(meeting.get('start_time', ''))
        req = {
            'lat':          float(loc.get('lat',       0.0)),
            'lng':          float(loc.get('lng',       0.0)),
            'radius_m':     float(loc.get('radius_m',  1000.0)),
            'headcount':    int(meeting.get('headcount', 4)),
            'dow':          dow,
            'card_limit':   int(swipe.get('card_limit', 10)),
            'likes_raw':    prefs.get('like',    {}),
            'dislikes_raw': prefs.get('dislike', {}),
            'exclude_meat': bool(exclude.get('meat', False)),
            'exclude_bar':  bool(exclude.get('bar',  False)),
        }
    else:
        req = user_request

    u_lat        = float(req['lat'])
    u_lng        = float(req['lng'])
    u_rad        = float(req['radius_m'])
    head         = int(req.get('headcount', 4))
    dow          = int(req.get('dow', 0))
    card_limit   = int(req.get('card_limit', 10))
    likes_raw    = req.get('likes_raw',    req.get('likes',    {}))
    dislikes_raw = req.get('dislikes_raw', req.get('dislikes', {}))
    excl_meat    = bool(req.get('exclude_meat', False))
    excl_bar     = bool(req.get('exclude_bar',  False))

    item_df          = meta['item_df']
    feat_names       = meta['feature_names']
    item_feat_cols   = meta['item_feat_cols']
    feat_idx         = {n: i for i, n in enumerate(feat_names)}
    n_feat           = len(feat_names)
    radius_expand    = meta.get('RADIUS_EXPAND',    1.5)
    # [특수 케이스] 1~2인 모임인 경우 반경 확장을 2.0배로 상향
    if head <= 2:
        radius_expand = max(radius_expand, 2.0)
        logger.info(f"[Relaxation] Headcount={head} -> radius_expand up to {radius_expand}")

    _                = meta.get('HARD_DISLIKE_THR', 2)
    tier_ratio       = meta.get('TIER_RATIO', TIER_RATIO_DEFAULT)
    pref_alpha       = meta.get('PREF_ALPHA', PREF_ALPHA)

    like_vec    = pref_to_vec(likes_raw)
    dislike_vec = pref_to_vec(dislikes_raw)

    # ── Stage 1: 후보 필터 ───────────────────────────────────
    filt_rids, filt_dist = stage1_filter(
        item_df, u_lat, u_lng, u_rad,
        likes_raw, dislikes_raw,
        excl_meat, excl_bar,
        radius_expand, hard_dislike_thr=INFER_HARD_DISLIKE_THR,
    )

    if not filt_rids:
        logger.warning("후보 식당 0개")
        return pd.DataFrame()

    # ── 유사 모임 CF (Qdrant) — 원본 로직과 동일 ─────────────
    pref_q = np.concatenate([like_vec, dislike_vec])
    pref_q /= (np.linalg.norm(pref_q) + 1e-8)

    sim_feats: Dict[int, Dict] = {}
    try:
        similar_meetings = search_similar_meetings(
            pref_vector = pref_q.tolist(),
            headcount   = head,
            top_k       = SIM_TOPK,
            min_score   = SIM_MIN_COS,
            collection  = settings.QDRANT_COLLECTION,
            likes       = likes_raw,
            dislikes    = dislikes_raw,
        )

        for mtg in similar_meetings:
            for rid, lbl in mtg.restaurant_pairs.items():
                if rid not in sim_feats:
                    sim_feats[rid] = {'_lbls': []}
                sim_feats[rid]['_lbls'].append(lbl)

        for rid in sim_feats:
            lbls = sim_feats[rid].pop('_lbls')
            arr  = np.array(lbls, np.float32)
            sim_feats[rid] = {
                'sim_pos_rate':  float(arr.mean()),
                'sim_count':     float(len(arr)),
                'sim_pos_count': float((arr > 0).sum()),
            }

        logger.info(f"[CF] 집계완료 | similar={len(similar_meetings)} | sim_feats={len(sim_feats)}")
    except Exception as e:
        logger.warning(f"CF 실패 (폴백) — {e}")

    # ── 피처 행렬 구성 ────────────────────────────────────────
    ann_scores = np.zeros(len(filt_rids), np.float32)
    X_list = []
    for rid, dist, ann_raw in zip(filt_rids, filt_dist, ann_scores):
        x = _build_feature_vector(
            rid, ann_raw, dist, u_rad,
            like_vec, dislike_vec, likes_raw, dislikes_raw,
            head, dow, sim_feats,
            item_df, feat_idx, item_feat_cols, n_feat, pref_alpha,
        )
        X_list.append(x)

    X = np.stack(X_list).astype(np.float32)
    X[:, feat_idx.get('ann_score_norm', 0)] = group_zscore(ann_scores)
    X[:, feat_idx.get('dist_norm',      1)] = group_minmax(filt_dist)

    scores = model.predict(X)

    # ── 결과 DataFrame 구성 ───────────────────────────────────
    TIER_LABEL = {1: '순수선호', 2: '중립', 3: '충돌', 4: '비선호'}
    rows = []
    for rid, dist, score in zip(filt_rids, filt_dist, scores):
        irow     = item_df.loc[rid] if rid in item_df.index else pd.Series(dtype=float)
        cat_id   = safe_int(irow.get('category_id', 0))
        cat_name = CAT_ID_TO_NAME.get(cat_id, '기타')
        tier     = get_restaurant_tier(rid, likes_raw, dislikes_raw, item_df)
        rows.append({
            'restaurant_id':   rid,
            'score':           float(score),
            'dist_m':          float(dist),
            'in_radius':       float(dist) <= u_rad,
            'category_id':     cat_id,
            'category':        cat_name,
            'tier':            tier,
            'tier_label':      TIER_LABEL[tier],
            'price_level':     safe_int(irow.get('price_level', 0)),
            'avg_price':       safe_float(irow.get('avg_price_all', 0)),
            'has_group':       safe_int(irow.get('has_group', 0)),
            'has_room':        safe_int(irow.get('has_room', 0)),
            'has_reservation': safe_int(irow.get('has_reservation', 0)),
        })

    df_all = (pd.DataFrame(rows)
              .sort_values('score', ascending=False)
              .reset_index(drop=True))

    top_n = card_limit * 2

    # ── 기타 카테고리 상한 ────────────────────────────────────
    ETC_CAT_ID  = 9
    etc_liked   = float(likes_raw.get('기타', 0)) > 0
    etc_max     = top_n if etc_liked else max(1, round(top_n * 0.20))
    etc_used    = 0

    t_dfs = {t: df_all[df_all['tier'] == t] for t in range(1, 5)}

    def _slot_fill_etc(pool_df: pd.DataFrame, needed: int, used_ids: set) -> tuple:
        nonlocal etc_used
        avail   = pool_df[~pool_df['restaurant_id'].isin(used_ids)]
        picked_rows = []
        for _, row in avail.iterrows():
            if len(picked_rows) >= needed:
                break
            if row['category_id'] == ETC_CAT_ID:
                if etc_used >= etc_max:
                    continue
                etc_used += 1
            picked_rows.append(row)
        picked   = pd.DataFrame(picked_rows)
        new_used = used_ids | set(picked['restaurant_id']) if len(picked) else used_ids
        return picked, new_used

    def _calc_slots(n: int, t3_pool: int) -> tuple:
        s1 = round(n * 0.50)
        s3 = min(round(n * tier_ratio.get('tier3', 0.15)), t3_pool)
        s4 = 0
        s2 = n - s1 - s3
        return s1, max(s2, 0), s3, s4

    used: set = set()
    result_parts = []

    for phase_n in [card_limit, card_limit]:
        t3_remaining = len(t_dfs[3][~t_dfs[3]['restaurant_id'].isin(used)])
        s1, s2, s3, s4 = _calc_slots(phase_n, t3_remaining)

        # [특수 케이스] 1~2인 모임이거나 선호 카테고리가 매우 많을 때, Tier 1 비중 우대
        if head <= 2 or len(likes_raw) >= 5:
            s1 = phase_n
            s2 = s3 = s4 = 0

        phase_picks = {}
        for t, slot in [(1, s1), (2, s2), (3, s3), (4, s4)]:
            phase_picks[t], used = _slot_fill_etc(t_dfs[t], slot, used)

        phase_df = pd.concat(list(phase_picks.values()), ignore_index=True)

        if len(phase_df) < phase_n:
            supplement, used = _slot_fill_etc(
                df_all,
                phase_n - len(phase_df), used,
            )
            phase_df = pd.concat([phase_df, supplement], ignore_index=True)

        phase_df = (phase_df
                    .sort_values('score', ascending=False)
                    .head(phase_n)
                    .reset_index(drop=True))
        result_parts.append(phase_df)

    df_top = pd.concat(result_parts, ignore_index=True)
    df_top.insert(0, 'rank', range(1, len(df_top) + 1))

    logger.info(
        f"추론 완료 | candidates={len(df_all)} | "
        f"returned={len(df_top)} | radius={u_rad:.0f}m | "
        f"etc={etc_used}/{etc_max}"
    )
    return df_top
