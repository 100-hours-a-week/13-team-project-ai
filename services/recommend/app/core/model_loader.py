"""app/core/model_loader.py — 모델 싱글턴 로더."""
import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

_model: Optional[object] = None
_meta:  Optional[dict]   = None


def load_model(
    model_path: Optional[Path] = None,
    meta_path:  Optional[Path] = None,
) -> Tuple[object, dict]:
    """
    model.pkl + model_meta.pkl 을 전역 싱글턴에 캐싱.
    이미 로드되어 있으면 재사용.
    """
    global _model, _meta
    if _model is not None and _meta is not None:
        return _model, _meta

    mp = model_path or settings.MODEL_PATH
    gp = meta_path  or settings.META_PATH

    if not mp.exists():
        raise FileNotFoundError(f"model.pkl 없음: {mp}")
    if not gp.exists():
        raise FileNotFoundError(f"model_meta.pkl 없음: {gp}")

    logger.info(f"모델 로드: {mp}")
    with open(mp, "rb") as f:
        _model = pickle.load(f)

    logger.info(f"메타 로드: {gp}")
    with open(gp, "rb") as f:
        _meta = pickle.load(f)

    logger.info(
        f"모델 로드 완료 | "
        f"features={len(_meta['feature_names'])} | "
        f"restaurants={len(_meta['item_df'])} | "
        f"train_requests={len(_meta['req_ids_train'])}"
    )
    return _model, _meta


def get_model() -> Tuple[object, dict]:
    """FastAPI Depends 용. 싱글턴 반환."""
    if _model is None or _meta is None:
        raise RuntimeError("모델이 로드되지 않았습니다. lifespan에서 load_model()을 확인하세요.")
    return _model, _meta
