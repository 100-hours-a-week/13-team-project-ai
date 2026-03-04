from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── 서버 ──────────────────────────────────────────────────
    HOST:      str = "127.0.0.1"
    PORT:      int = 8000
    LOG_LEVEL: str = "info"

    # ── 모델 파일 경로 ────────────────────────────────────────
    MODEL_DIR: Path = Path("models")

    @property
    def MODEL_PATH(self) -> Path:
        return self.MODEL_DIR / "model.pkl"

    @property
    def META_PATH(self) -> Path:
        return self.MODEL_DIR / "model_meta.pkl"

    # ── Qdrant 벡터DB ─────────────────────────────────────────
    QDRANT_URL:        Optional[str] = None   # None → 인메모리
    QDRANT_API_KEY:    Optional[str] = None
    QDRANT_TIMEOUT:    float         = 5.0
    QDRANT_COLLECTION: str           = "meetup_requests"

    # ── 추론 파라미터 (model_meta에 없을 때 폴백) ─────────────
    INFER_HARD_DISLIKE_THR: int   = 2
    RADIUS_EXPAND:          float = 1.5
    MAX_RADIUS_M:           float = 10_000.0
    RADIUS_EXPAND_MAX_ITER: int   = 7
    ETC_CAT_MAX_RATIO:      float = 0.20

    # ── CF 파라미터 ───────────────────────────────────────────
    SIM_TOPK:    int   = 20
    SIM_MIN_COS: float = 0.25


settings = Settings()
